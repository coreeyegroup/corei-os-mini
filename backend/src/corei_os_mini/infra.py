"""Infrastructure console: health of everything COREI runs on, and a spreadsheet-style
view and editor for the PostgreSQL database.

Owns:
  * health(): PostgreSQL, the web service and Caddy, disk, memory, load, clock, egress IP,
    the MT5 reader, the credential key, the alert loop, migrations;
  * database(): server facts, table sizes and live sessions;
  * the table editor: list, filter, sort and page any table; edit, add and delete rows.

Safety (the database role owns every table, so the rules live here):
  * Secret columns (passwords, hashes, ciphertext, tokens, any bytea) are never returned
    and never editable; queries in the SQL console that name them are refused.
  * Logs, secrets, sessions and migration history are read-only, always.
  * Configuration tables that have their own controlled screens (four-eyes, readiness)
    are read-only unless the policy infra_edit_controlled_tables is turned on.
  * Every change needs ADMIN, a re-entered password and a reason; it is checked against
    the values the editor showed (so a row changed meanwhile is refused) and written to
    the append-only infra_data_edits and to the audit log.
  * The SQL console runs one statement in a READ ONLY transaction with a 5 s timeout.
Values travel as text and are cast by PostgreSQL to the column type, so numbers, dates,
JSON and arrays keep their exact form.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from fastapi import HTTPException
from psycopg import errors, sql
from psycopg.types.json import Jsonb

from . import config_store as store

SECRET_COLUMN = re.compile(r'(password|passwd|secret|ciphertext|token|hash|nonce|private_key|api_key|totp)', re.I)
READ_ONLY_TABLES = {
    'cfg_audit_log': 'Audit history is append-only.',
    'cfg_credential_use': 'The password-use log is append-only.',
    'infra_data_edits': 'The table-edit log is append-only.',
    'corei_schema_migrations': 'Migration history is maintained by the installer.',
    'cfg_credentials': 'Passwords are changed on the account screen (write-only, encrypted).',
    'cfg_sessions': 'Sign-in sessions are managed by the sign-in service.',
    'cfg_session_tokens': 'Broker tokens are managed by the broker login.',
    'cfg_oauth_states': 'Broker login state is managed by the broker login.',
    'cfg_change_requests': 'Change requests are decided on the Approvals screen.',
    'cfg_broker_reads': 'Broker reads are a record of what the broker said.',
    'cfg_latency_checks': 'Latency history is measured, not typed.',
    'cfg_alert_outbox': 'The alert outbox is managed by the alert service.',
    'cfg_users': 'Users are managed on the Users screen (roles, password rules).',
    'cfg_policies': 'Policies are changed on the Controls screen with a reason.',
    'cfg_execution_controls': 'Order sending is switched on the Controls screen with a reason.',
    'md_ticks': 'Prices as received are never edited.',
    'md_bars': 'Bars are built from prices or read from the broker.',
    'md_latest': 'Latest prices are written by the collector.',
    'md_quality': 'Quality is measured, not typed.',
    'md_events': 'Market-data events are a record of what happened.',
    'md_collector_status': 'Collector status is written by the collector.',
    'md_files': 'File fingerprints are written by the archive and checked nightly.',
    'md_backups': 'Backup records are written by the backup job.',
    'md_jobs': 'Backfill jobs are managed on the Market data screen.',
}
CONTROLLED_TABLES = {'cfg_providers', 'cfg_connections', 'cfg_accounts', 'cfg_symbol_mappings', 'cfg_instruments',
                     'cfg_strategies', 'cfg_strategy_versions', 'cfg_deployments', 'cfg_account_groups',
                     'cfg_account_group_members', 'cfg_alert_channels', 'cfg_connection_status', 'cfg_symbol_specs',
                     'md_collection_plans', 'md_controls', 'md_feed_state'}
MAX_PAGE = 500
_STARTED = time.time()


# ---------- health ----------

def _run(args: list[str], timeout: float = 3) -> str | None:
    if not shutil.which(args[0]):
        return None
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None


def _service(name: str) -> dict:
    state = _run(['systemctl', 'is-active', name])
    since = _run(['systemctl', 'show', name, '-p', 'ActiveEnterTimestamp', '--value'])
    return {'name': name, 'state': state or 'unknown', 'since': since or None,
            'status': 'OK' if state == 'active' else 'UNKNOWN' if state is None else 'FAIL'}


def _disk(path: str) -> dict | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    used = usage.used / usage.total * 100 if usage.total else 0
    return {'path': path, 'total_gb': round(usage.total / 1e9, 1), 'free_gb': round(usage.free / 1e9, 1), 'used_pct': round(used, 1),
            'status': 'FAIL' if used > 95 else 'WARN' if used > 85 else 'OK'}


def _memory() -> dict | None:
    try:
        info = {k: int(v.split()[0]) for k, v in (line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines() if ':' in line)}
    except (OSError, ValueError):
        return None
    total, available = info.get('MemTotal', 0), info.get('MemAvailable', 0)
    used = (total - available) / total * 100 if total else 0
    return {'total_gb': round(total / 1e6, 1), 'available_gb': round(available / 1e6, 1), 'used_pct': round(used, 1),
            'status': 'FAIL' if used > 95 else 'WARN' if used > 85 else 'OK'}


def health() -> dict:
    from . import alerts, network
    checks: list[dict] = []

    def add(group: str, name: str, status: str, detail: str, **extra: Any) -> None:
        checks.append({'group': group, 'name': name, 'status': status, 'detail': detail, **extra})

    # Database
    try:
        started = time.perf_counter()
        with store.transaction('read') as db:
            row = db.execute("SELECT current_setting('server_version') AS version, pg_database_size(current_database()) AS size, "
                             "now() - pg_postmaster_start_time() AS uptime, (SELECT count(*) FROM pg_stat_activity "
                             "WHERE datname=current_database()) AS sessions, current_setting('max_connections')::int AS max_sessions").fetchone()
            applied = db.execute("SELECT count(*) AS n, max(name) AS last FROM corei_schema_migrations").fetchone() \
                if db.execute("SELECT to_regclass('corei_schema_migrations') AS t").fetchone()['t'] else None
        ms = round((time.perf_counter() - started) * 1000, 1)
        add('Database', 'PostgreSQL', 'OK', f"{row['version']} · up {str(row['uptime']).split('.')[0]} · answered in {ms} ms")
        add('Database', 'Database size', 'OK', f"{row['size'] / 1e6:.1f} MB")
        load = row['sessions'] / row['max_sessions'] * 100
        add('Database', 'Sessions', 'WARN' if load > 80 else 'OK', f"{row['sessions']} of {row['max_sessions']}")
        files = sorted(p.name for p in (Path(__file__).resolve().parents[2] / 'sql').glob('[0-9][0-9][0-9]_*.sql'))
        if applied is not None:
            behind = files and applied['last'] and applied['last'] < files[-1]
            add('Database', 'Migrations', 'WARN' if behind else 'OK',
                f"{applied['n']} applied, latest {applied['last']}" + (f"; {files[-1]} not applied — run configure-workstation-database.sh" if behind else ''))
        else:
            add('Database', 'Migrations', 'WARN', 'No migration history table; apply migrations with configure-workstation-database.sh')
    except HTTPException as exc:
        add('Database', 'PostgreSQL', 'FAIL', str(exc.detail))

    # Services
    for name in ('corei-os-mini-workstation', 'caddy', 'postgresql'):
        s = _service(name)
        add('Services', name, s['status'], s['state'] + (f" since {s['since']}" if s['since'] else ''))
    add('Services', 'API process', 'OK', f"pid {os.getpid()} · up {int(time.time() - _STARTED)} s")
    beat, failed = alerts.heartbeat.get('at'), alerts.heartbeat.get('failed_at')
    if os.getenv('COREI_ALERTS', '').lower() == 'off':
        add('Services', 'Alert loop', 'UNKNOWN', 'Turned off (COREI_ALERTS=off)')
    else:
        age = time.time() - beat if beat else None
        add('Services', 'Alert loop', 'OK' if age is not None and age < 120 else 'WARN',
            f'last cycle {int(age)} s ago' if age is not None else 'no cycle yet' + (' (last cycle failed)' if failed else ''))

    # Host
    for path in ('/', '/var/lib/postgresql', '/opt/corei-os-mini'):
        d = _disk(path)
        if d and not any(c.get('device') == (d['total_gb'], d['free_gb']) for c in checks):
            add('Host', f'Disk {path}', d['status'], f"{d['free_gb']} GB free of {d['total_gb']} GB ({d['used_pct']}% used)",
                device=(d['total_gb'], d['free_gb']))
    m = _memory()
    if m:
        add('Host', 'Memory', m['status'], f"{m['available_gb']} GB available of {m['total_gb']} GB")
    try:
        one, five, _ = os.getloadavg()
        cpus = os.cpu_count() or 1
        add('Host', 'CPU load', 'WARN' if five > cpus * 1.5 else 'OK', f'{one:.2f} / {five:.2f} (1 / 5 min) on {cpus} CPUs')
    except OSError:
        pass
    synced = network.clock_synchronised()
    add('Host', 'Clock (NTP)', {True: 'OK', False: 'FAIL', None: 'UNKNOWN'}[synced],
        {True: 'synchronised', False: 'NOT synchronised — order timestamps and session windows will drift', None: 'cannot tell (no timedatectl)'}[synced])
    ip = network.egress_ip(cached_only=True)
    add('Host', 'Outbound IP', 'OK' if ip else 'UNKNOWN', ip or 'not measured yet (open Controls › Network)')

    # Market data
    try:
        _market_data_health(add)
    except HTTPException as exc:
        add('Market data', 'Market data', 'UNKNOWN', str(exc.detail))

    # Broker tools and secrets
    reader = Path(os.getenv('COREI_MT5_CATALOG_PARSER', '/opt/corei-os-mini/server-catalog/ServerCatalog'))
    add('Broker tools', 'MT5 reader', 'OK' if reader.is_file() else 'FAIL',
        str(reader) if reader.is_file() else 'not installed — run scripts/setup-broker-accounts.sh')
    key = Path(os.getenv('COREI_BROKER_KEY_FILE', '/etc/corei-os-mini/broker-credentials.key'))
    try:
        mode = key.stat().st_mode & 0o777
        add('Broker tools', 'Credential key', 'OK' if mode & 0o077 == 0 else 'WARN',
            f'present, permissions {oct(mode)[2:]}' + ('' if mode & 0o077 == 0 else ' — readable by others, run chmod 600'))
    except FileNotFoundError:
        add('Broker tools', 'Credential key', 'WARN', 'not created yet (created when the first password is saved)')
    except PermissionError:
        add('Broker tools', 'Credential key', 'OK', 'present (not readable by this check)')

    for c in checks:
        c.pop('device', None)
    worst = 'FAIL' if any(c['status'] == 'FAIL' for c in checks) else 'WARN' if any(c['status'] == 'WARN' for c in checks) else 'OK'
    return {'generated_at': datetime.now(timezone.utc).isoformat(), 'status': worst, 'checks': checks}


# ---------- database overview ----------

def database() -> dict:
    with store.transaction('read') as db:
        server = db.execute("SELECT current_database() AS database, current_user AS role, current_schema() AS schema, "
                            "current_setting('server_version') AS version, pg_size_pretty(pg_database_size(current_database())) AS size, "
                            "current_setting('TimeZone') AS timezone, pg_postmaster_start_time() AS started_at").fetchone()
        activity = db.execute("SELECT pid, usename AS role, application_name, client_addr::text AS client, state, "
                              "now() - query_start AS running_for, left(query, 200) AS query FROM pg_stat_activity "
                              "WHERE datname=current_database() ORDER BY query_start NULLS LAST").fetchall()
    return {'server': store.clean(server), 'sessions': [{**store.clean(a), 'running_for': str(a['running_for'] or '').split('.')[0]}
                                                         for a in activity], 'tables': tables()}


def tables() -> list[dict]:
    allow_controlled = _controlled_allowed()
    with store.transaction('read') as db:
        rows = db.execute("""SELECT c.relname AS name,
                CASE WHEN c.relkind='p' THEN (SELECT COALESCE(sum(GREATEST(k.reltuples, 0)), 0)::bigint FROM pg_inherits h JOIN pg_class k ON k.oid=h.inhrelid WHERE h.inhparent=c.oid)
                     ELSE c.reltuples::bigint END AS estimated_rows,
                CASE WHEN c.relkind='p' THEN (SELECT COALESCE(sum(pg_total_relation_size(h.inhrelid)), 0) FROM pg_inherits h WHERE h.inhparent=c.oid)
                     ELSE pg_total_relation_size(c.oid) END AS bytes,
                s.n_live_tup AS live_rows, s.last_autovacuum, s.last_autoanalyze,
                EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid=c.oid AND i.indisprimary) AS has_key
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_stat_user_tables s ON s.relid=c.oid
            WHERE n.nspname=current_schema() AND c.relkind IN ('r','p') AND NOT c.relispartition ORDER BY c.relname""").fetchall()
    out = []
    for r in rows:
        mode, why = _mode(r['name'], r['has_key'], allow_controlled)
        out.append({**store.clean(r), 'rows': max(r['live_rows'] or 0, r['estimated_rows'] or 0), 'mode': mode, 'why': why,
                    'group': 'Configuration' if r['name'].startswith('cfg_') else 'Infrastructure' if r['name'].startswith('infra_')
                    else 'Market data' if r['name'].startswith('md_') else 'Research and other'})
    return out


def _controlled_allowed() -> bool:
    with store.transaction('read') as db:
        return bool(store.policy(db, 'infra_edit_controlled_tables', False))


def _mode(table: str, has_key: bool, allow_controlled: bool) -> tuple[str, str]:
    if table in READ_ONLY_TABLES:
        return 'READ_ONLY', READ_ONLY_TABLES[table]
    if not has_key:
        return 'READ_ONLY', 'The table has no primary key, so a row cannot be addressed safely.'
    if table in CONTROLLED_TABLES and not allow_controlled:
        return 'READ_ONLY', 'Edited on its own screen with four-eyes and readiness checks. An administrator can allow direct edits with the policy infra_edit_controlled_tables.'
    return 'EDIT', 'Administrators can edit with a reason; every change is logged.'


# ---------- one table ----------

def _columns(db, table: str) -> list[dict]:
    rows = db.execute("""SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type, NOT a.attnotnull AS nullable,
            pg_get_expr(d.adbin, d.adrelid) AS default, a.attidentity <> '' OR a.attgenerated <> '' AS generated,
            COALESCE(a.attnum = ANY(i.indkey), false) AS key, t.typname = 'bytea' AS binary
        FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_type t ON t.oid=a.atttypid
        LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
        LEFT JOIN pg_index i ON i.indrelid=c.oid AND i.indisprimary
        WHERE n.nspname=current_schema() AND c.relname=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""", (table,)).fetchall()
    if not rows:
        raise HTTPException(404, 'Table not found.')
    return [{**dict(r), 'secret': bool(r['binary'] or SECRET_COLUMN.search(r['name']))} for r in rows]


def _select_list(columns: list[dict]) -> sql.Composable:
    parts = []
    for c in columns:
        ident = sql.Identifier(c['name'])
        if c['secret']:
            parts.append(sql.SQL("CASE WHEN {} IS NULL THEN NULL ELSE '••••••' END AS {}").format(ident, ident))
        else:
            parts.append(sql.SQL('{}::text AS {}').format(ident, ident))
    return sql.SQL(', ').join(parts)


def read_table(table: str, offset: int = 0, limit: int = 100, sort: str | None = None, descending: bool = False,
               filters: dict[str, str] | None = None, q: str = '') -> dict:
    limit = max(1, min(limit, MAX_PAGE))
    with store.transaction('read') as db:
        columns = _columns(db, table)
        names = {c['name']: c for c in columns}
        clauses, params = [], []
        for name, value in (filters or {}).items():
            if name not in names or names[name]['secret'] or value == '':
                continue
            if value in ('(empty)', '(null)'):
                clauses.append(sql.SQL('{} IS NULL').format(sql.Identifier(name)))
            else:
                clauses.append(sql.SQL('{}::text ILIKE %s').format(sql.Identifier(name)))
                params.append('%' + value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
        if q:
            visible = [sql.SQL('{}::text').format(sql.Identifier(c['name'])) for c in columns if not c['secret']]
            clauses.append(sql.SQL("concat_ws(' ', {}) ILIKE %s").format(sql.SQL(', ').join(visible)))
            params.append('%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
        where = sql.SQL(' WHERE ') + sql.SQL(' AND ').join(clauses) if clauses else sql.SQL('')
        keys = [c['name'] for c in columns if c['key']]
        if sort in names and not names[sort]['secret']:
            order = sql.SQL(' ORDER BY {} {} NULLS LAST').format(sql.Identifier(sort), sql.SQL('DESC' if descending else 'ASC'))
        elif keys:
            order = sql.SQL(' ORDER BY ') + sql.SQL(', ').join(sql.Identifier(k) for k in keys)
        else:
            order = sql.SQL('')
        estimated = False
        if not clauses:
            guess = db.execute("""SELECT CASE WHEN c.relkind='p' THEN (SELECT COALESCE(sum(GREATEST(k.reltuples, 0)), 0) FROM pg_inherits h
                    JOIN pg_class k ON k.oid=h.inhrelid WHERE h.inhparent=c.oid) ELSE GREATEST(c.reltuples, 0) END AS n
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND c.relname=%s""", (table,)).fetchone()
            estimated = bool(guess and guess['n'] > 200_000)
        total = int(guess['n']) if estimated else \
            db.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(table)) + where, params).fetchone()['n']
        rows = db.execute(sql.SQL('SELECT {} FROM {}').format(_select_list(columns), sql.Identifier(table)) + where + order
                          + sql.SQL(' LIMIT %s OFFSET %s'), [*params, limit, max(offset, 0)]).fetchall()
    mode, why = _mode(table, bool(keys), _controlled_allowed())
    return {'table': table, 'mode': mode, 'why': why, 'columns': [{k: v for k, v in c.items() if k != 'binary'} for c in columns],
            'key': keys, 'total': total, 'total_estimated': estimated, 'offset': offset, 'limit': limit, 'rows': [dict(r) for r in rows]}


def _editable(db, table: str) -> tuple[list[dict], list[str]]:
    columns = _columns(db, table)
    keys = [c['name'] for c in columns if c['key']]
    mode, why = _mode(table, bool(keys), bool(store.policy(db, 'infra_edit_controlled_tables', False)))
    if mode != 'EDIT':
        raise HTTPException(403, why)
    return columns, keys


def _row_where(keys: list[str], key: dict[str, Any]) -> tuple[sql.Composable, list]:
    if set(key) != set(keys):
        raise HTTPException(422, 'Identify the row by its full primary key: ' + ', '.join(keys))
    return (sql.SQL(' AND ').join(sql.SQL('{}::text = %s').format(sql.Identifier(k)) for k in keys),
            [None if key[k] is None else str(key[k]) for k in keys])


def _visible(row: dict | None, columns: list[dict]) -> dict | None:
    if row is None:
        return None
    secret = {c['name'] for c in columns if c['secret']}
    return {k: v for k, v in row.items() if k not in secret}


def _log(db, actor: str, table: str, action: str, key: dict, before: dict | None, after: dict | None, reason: str) -> None:
    db.execute('INSERT INTO infra_data_edits(actor, table_name, action, row_key, before, after, reason) VALUES (%s,%s,%s,%s,%s,%s,%s)',
               (actor, table, action, Jsonb(key), Jsonb(before) if before else None, Jsonb(after) if after else None, reason))
    store.audit(db, actor, 'INFRA_' + action, 'table:' + table, json.dumps(key, default=str)[:200], before, after, reason)


def _db_error(exc: Exception) -> HTTPException:
    diag = getattr(exc, 'diag', None)
    message = (getattr(diag, 'message_primary', None) or str(exc)).split('\n')[0]
    detail = getattr(diag, 'message_detail', None)
    return HTTPException(422, message + (f' ({detail})' if detail else ''))


@contextmanager
def _tx(operation: str) -> Iterator[Any]:
    """Like store.transaction, but an administrator sees PostgreSQL's own message
    (invalid value for the column type, constraint names) instead of a generic one."""
    with store.transaction(operation) as db:
        try:
            yield db
        except errors.QueryCanceled as exc:
            raise HTTPException(422, 'The query took longer than 5 seconds and was stopped.') from exc
        except errors.Error as exc:
            raise _db_error(exc) from exc


def update_row(actor: str, table: str, key: dict, changes: dict[str, str | None], original: dict[str, str | None], reason: str) -> dict:
    if not changes:
        raise HTTPException(422, 'Nothing to change.')
    try:
        with _tx('update') as db:
            columns, keys = _editable(db, table)
            names = {c['name']: c for c in columns}
            for name in changes:
                if name not in names:
                    raise HTTPException(422, f'Unknown column {name}.')
                if names[name]['secret'] or names[name]['generated']:
                    raise HTTPException(403, f'{name} cannot be edited here.')
            where, params = _row_where(keys, key)
            current = db.execute(sql.SQL('SELECT {} FROM {} WHERE ').format(_select_list(columns), sql.Identifier(table)) + where
                                 + sql.SQL(' FOR UPDATE'), params).fetchone()
            if not current:
                raise HTTPException(404, 'The row no longer exists. Reload the table.')
            stale = [n for n in changes if n in original and current[n] != original[n]]
            if stale:
                raise HTTPException(409, f"{', '.join(stale)} changed since you loaded it (now {current[stale[0]]!r}). Reload the table.")
            sets = [sql.SQL('{} = CAST(%s AS {})').format(sql.Identifier(n), sql.SQL(names[n]['type'])) for n in changes]
            values = list(changes.values())
            if 'version' in names and 'version' not in changes and names['version']['type'] == 'integer':
                sets.append(sql.SQL('version = version + 1'))
            if 'updated_at' in names and 'updated_at' not in changes:
                sets.append(sql.SQL('updated_at = now()'))
            db.execute(sql.SQL('UPDATE {} SET {} WHERE ').format(sql.Identifier(table), sql.SQL(', ').join(sets)) + where, [*values, *params])
            new_key = {k: changes.get(k, key[k]) for k in keys}
            where2, params2 = _row_where(keys, new_key)
            after = db.execute(sql.SQL('SELECT {} FROM {} WHERE ').format(_select_list(columns), sql.Identifier(table)) + where2, params2).fetchone()
            _log(db, actor, table, 'UPDATE', key, _visible(dict(current), columns), _visible(dict(after), columns), reason)
            return dict(after)
    except errors.Error as exc:
        raise _db_error(exc) from exc


def insert_row(actor: str, table: str, values: dict[str, str | None], reason: str) -> dict:
    try:
        with _tx('update') as db:
            columns, keys = _editable(db, table)
            names = {c['name']: c for c in columns}
            values = {k: v for k, v in values.items() if v not in ('', None)}  # blank = the column's default
            for name in values:
                if name not in names:
                    raise HTTPException(422, f'Unknown column {name}.')
                if names[name]['secret'] or names[name]['generated']:
                    raise HTTPException(403, f'{name} cannot be set here.')
            # A uuid primary key without a default gets one, as the screens do.
            for k in keys:
                if k not in values and names[k]['type'] == 'uuid' and not names[k]['default']:
                    values[k] = str(uuid4())
            if not values:
                raise HTTPException(422, 'Enter at least one value.')
            cols = sql.SQL(', ').join(sql.Identifier(n) for n in values)
            vals = sql.SQL(', ').join(sql.SQL('CAST(%s AS {})').format(sql.SQL(names[n]['type'])) for n in values)
            row = db.execute(sql.SQL('INSERT INTO {} ({}) VALUES ({}) RETURNING ').format(sql.Identifier(table), cols, vals)
                             + _select_list(columns), list(values.values())).fetchone()
            _log(db, actor, table, 'INSERT', {k: row[k] for k in keys}, None, _visible(dict(row), columns), reason)
            return dict(row)
    except errors.Error as exc:
        raise _db_error(exc) from exc


def delete_row(actor: str, table: str, key: dict, reason: str) -> None:
    try:
        with _tx('update') as db:
            columns, keys = _editable(db, table)
            where, params = _row_where(keys, key)
            before = db.execute(sql.SQL('SELECT {} FROM {} WHERE ').format(_select_list(columns), sql.Identifier(table)) + where
                                + sql.SQL(' FOR UPDATE'), params).fetchone()
            if not before:
                raise HTTPException(404, 'The row no longer exists. Reload the table.')
            db.execute(sql.SQL('DELETE FROM {} WHERE ').format(sql.Identifier(table)) + where, params)
            _log(db, actor, table, 'DELETE', key, _visible(dict(before), columns), None, reason)
    except errors.Error as exc:
        raise _db_error(exc) from exc


def edits(table: str | None = None, limit: int = 100) -> list[dict]:
    with store.transaction('read') as db:
        rows = db.execute('SELECT * FROM infra_data_edits' + (' WHERE table_name=%s' if table else '') + ' ORDER BY id DESC LIMIT %s',
                          (table, limit) if table else (limit,)).fetchall()
    return [store.clean(r) for r in rows]


# ---------- read-only SQL console ----------

def query(text: str, limit: int = 1000) -> dict:
    statement = text.strip().rstrip(';').strip()
    if not statement:
        raise HTTPException(422, 'Enter a query.')
    if ';' in statement:
        raise HTTPException(422, 'Run one statement at a time.')
    if not re.match(r'^(select|with|explain|table|values|show)\b', statement, re.I):
        raise HTTPException(422, 'The console is read-only: SELECT, WITH, EXPLAIN, TABLE, VALUES or SHOW. Edit rows in the table view.')
    if SECRET_COLUMN.search(statement) or re.search(r'\b(cfg_credentials|cfg_sessions|cfg_session_tokens|cfg_oauth_states)\b', statement, re.I):
        raise HTTPException(403, 'Queries that name secret columns or secret tables are refused here.')
    started = time.perf_counter()
    try:
        with _tx('read') as db:
            db.execute('SET TRANSACTION READ ONLY')
            db.execute("SET LOCAL statement_timeout = '5s'")
            cursor = db.execute(statement)
            columns = [d.name for d in cursor.description or []]
            rows = cursor.fetchmany(limit + 1) if cursor.description else []
    except errors.QueryCanceled as exc:
        raise HTTPException(422, 'The query took longer than 5 seconds and was stopped.') from exc
    except errors.Error as exc:
        raise _db_error(exc) from exc
    def cell(v: Any) -> Any:
        if isinstance(v, (bytes, bytearray, memoryview)):
            return '••••••'
        return v if v is None or isinstance(v, (bool, int, float, str)) else str(v)
    return {'columns': columns, 'rows': [{c: cell(r[c]) for c in columns} for r in rows[:limit]], 'truncated': len(rows) > limit,
            'ms': round((time.perf_counter() - started) * 1000, 1)}


def cancel_session(actor: str, pid: int, reason: str) -> dict:
    with store.transaction('update') as db:
        row = db.execute('SELECT pid, usename, left(query, 200) AS query FROM pg_stat_activity WHERE pid=%s AND datname=current_database()',
                         (pid,)).fetchone()
        if not row:
            raise HTTPException(404, 'That session is not on this database.')
        if pid == db.execute('SELECT pg_backend_pid() AS p').fetchone()['p']:
            raise HTTPException(409, 'That is this request.')
        ok = db.execute('SELECT pg_cancel_backend(%s) AS ok', (pid,)).fetchone()['ok']
        store.audit(db, actor, 'INFRA_CANCEL_QUERY', 'database', pid, None, {'query': row['query'], 'cancelled': ok}, reason)
    return {'cancelled': bool(ok)}


# ---------- market data health ----------

def _age(t) -> float | None:
    return (datetime.now(timezone.utc) - t).total_seconds() if t else None


def _market_data_health(add) -> None:
    from .md.collector import md_root
    with store.transaction('read') as db:
        if not db.execute("SELECT to_regclass('md_collection_plans') AS t").fetchone()['t']:
            add('Market data', 'Market data', 'WARN', 'Tables missing — run configure-workstation-database.sh')
            return
        active = db.execute("SELECT count(*) AS n FROM md_collection_plans WHERE status='ACTIVE'").fetchone()['n']
        paused_all = db.execute("SELECT paused FROM md_controls WHERE scope='ALL'").fetchone()
        collectors = db.execute('SELECT s.*, a.code FROM md_collector_status s JOIN cfg_accounts a ON a.id=s.account_id').fetchall()
        last_archive = db.execute("SELECT at, severity, detail FROM md_events WHERE kind='ARCHIVE' ORDER BY id DESC LIMIT 1").fetchone()
        last_verify = db.execute("SELECT at, severity, detail FROM md_events WHERE kind='VERIFY' ORDER BY id DESC LIMIT 1").fetchone()
        backups = {r['kind']: r for r in db.execute("SELECT DISTINCT ON (kind) * FROM md_backups ORDER BY kind, id DESC").fetchall()}
        growth = db.execute("SELECT COALESCE(sum(bytes), 0) AS b FROM md_files WHERE created_at > now() - interval '7 days' AND kind <> 'JOURNAL'").fetchone()['b']
    if paused_all and paused_all['paused']:
        add('Market data', 'Collection', 'WARN', 'Paused for everything (Market data › Controls)')
    else:
        add('Market data', 'Collection', 'OK', f'{active} active plan{"" if active == 1 else "s"}')
    if active and not collectors:
        add('Market data', 'Collector service', 'FAIL', 'No collector has reported — start corei-os-mini-collector')
    for c in collectors:
        age = _age(c['heartbeat_at'])
        status = 'OK' if c['state'] == 'CONNECTED' and age is not None and age < 30 else \
                 'UNKNOWN' if c['state'] in ('PAUSED', 'STOPPED', 'UNSUPPORTED') else 'FAIL' if age is None or age > 120 or c['state'] == 'FAILED' else 'WARN'
        lag = f", lag {c['lag_ms']:.0f} ms" if c['lag_ms'] is not None else ''
        add('Market data', f"Feed {c['code']}", status, f"{c['state'].lower()}{', ' + c['host'] if c['host'] else ''}{lag}, "
            f"{c['subscribed']} symbols, heartbeat {int(age)} s ago" if age is not None else c['state'].lower())
        if c['backlog'] and c['backlog'] > 10_000:
            add('Market data', f"Write backlog {c['code']}", 'WARN', f"{c['backlog']} prices waiting for the database (all are in the journal)")
    if last_archive:
        age = _age(last_archive['at'])
        add('Market data', 'Nightly archive', 'FAIL' if last_archive['severity'] == 'CRITICAL' else 'WARN' if age > 30 * 3600 else 'OK',
            f"{int(age // 3600)} h ago · {last_archive['detail'].get('tick_files', 0)} tick files, {last_archive['detail'].get('bar_files', 0)} bar files"
            + (f" · {len(last_archive['detail'].get('errors') or [])} errors" if last_archive['detail'].get('errors') else ''))
    else:
        add('Market data', 'Nightly archive', 'UNKNOWN', 'Not run yet (timer corei-os-mini-md-nightly)')
    if last_verify:
        d = last_verify['detail']
        add('Market data', 'File fingerprints', 'FAIL' if last_verify['severity'] == 'CRITICAL' else 'OK',
            f"{d.get('ok', 0)} of {d.get('checked', 0)} files match" + (f"; changed: {len(d.get('mismatch') or [])}, missing: {len(d.get('missing') or [])}"
                                                                         if last_verify['severity'] == 'CRITICAL' else ''))
    else:
        add('Market data', 'File fingerprints', 'UNKNOWN', 'Not checked yet')
    for kind, label in (('DATABASE', 'Database backup'), ('FILES', 'Files backup')):
        b = backups.get(kind)
        if not b:
            add('Backups', label, 'WARN', 'No backup yet — set COREI_BACKUP_DIR; the nightly job makes them')
            continue
        age = _age(b['finished_at'] or b['started_at'])
        add('Backups', label, 'FAIL' if not b['ok'] else 'WARN' if age > 30 * 3600 else 'OK',
            (f"{int(age // 3600)} h ago · {b['path'] or ''}" if b['ok'] else (b['error'] or 'failed')))
    try:
        usage = shutil.disk_usage(md_root() if md_root().exists() else '/')
        per_day = growth / 7 if growth else 0
        days = usage.free / per_day if per_day else None
        add('Market data', 'Disk forecast', 'WARN' if days is not None and days < 30 else 'OK',
            f"{usage.free / 1e9:.1f} GB free" + (f", about {int(days)} days at the last week's archive growth" if days else ', no archive growth yet'))
    except OSError:
        pass


# ---------- history (Parquet) ----------

def history_files(limit: int = 500) -> list[dict]:
    from .md import store as md
    return md.files(limit)


def history_query(text: str, limit: int = 1000) -> dict:
    """Read-only DuckDB over the archived Parquet files. Views: ticks, bars (with day and
    account columns from the folder names). DuckDB may read only the archive folder."""
    import threading
    import duckdb
    from .md.collector import md_root
    statement = text.strip().rstrip(';').strip()
    if not statement:
        raise HTTPException(422, 'Enter a query.')
    if ';' in statement:
        raise HTTPException(422, 'Run one statement at a time.')
    if not re.match(r'^(select|with|describe|summarize)\b', statement, re.I) or re.search(r'\b(copy|attach|install|load|export|import|pragma|set|create|call)\b', statement, re.I):
        raise HTTPException(422, 'The history console is read-only: SELECT, WITH, DESCRIBE or SUMMARIZE over the views ticks and bars.')
    root = md_root() / 'archive'
    con = duckdb.connect()
    try:
        con.execute("SET memory_limit='1GB'")
        con.execute('SET threads=2')
        con.execute(f"SET allowed_directories=['{root}/']")
        views = []
        for name in ('ticks', 'bars'):
            if any((root / name).rglob('*.parquet')) if (root / name).is_dir() else False:
                con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{root / name}/**/*.parquet', hive_partitioning=true, union_by_name=true)")
                views.append(name)
        con.execute('SET enable_external_access=false')
        con.execute('SET lock_configuration=true')
        if not views:
            raise HTTPException(422, 'No history has been archived yet. Prices move to history after their plan\'s days in the database.')
        timer = threading.Timer(10.0, con.interrupt)
        started = time.perf_counter()
        timer.start()
        try:
            cursor = con.execute(statement)
            columns = [d[0] for d in cursor.description or []]
            rows = cursor.fetchmany(limit + 1)
        except duckdb.InterruptException as exc:
            raise HTTPException(422, 'The query took longer than 10 seconds and was stopped.') from exc
        except duckdb.Error as exc:
            raise HTTPException(422, str(exc).split('\n')[0][:400]) from exc
        finally:
            timer.cancel()
    finally:
        con.close()

    def cell(v):
        return v if v is None or isinstance(v, (bool, int, float, str)) else str(v)
    return {'columns': columns, 'rows': [dict(zip(columns, map(cell, r))) for r in rows[:limit]], 'truncated': len(rows) > limit,
            'ms': round((time.perf_counter() - started) * 1000, 1), 'views': views}
