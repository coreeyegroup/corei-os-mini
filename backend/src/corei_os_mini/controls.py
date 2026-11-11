"""Institutional controls around configuration (DEC-29..36).

Owns: four-eyes change requests (list, approve, reject, withdraw), switchable policies,
per-user preferences, facts written by adapters (connection status, account facts read
at login, symbol specifications), CSV import/export of accounts and the health summary.
Every write is audited in the same transaction.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
from typing import Any
from uuid import UUID

from fastapi import HTTPException
import psycopg
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from . import config_store as store
from . import credentials
from .config_models import AccountIn, ConnectionIn, CredentialEnvelope, Mt5AccountSetupIn

POLICIES = {
    'four_eyes_live_changes': ('bool', 'LIVE address additions, LIVE reactivation and LIVE credential replacement need a second person.'),
    'reauth_minutes': ('int', 'Minutes a re-entered password stays valid for sensitive actions (1–60).'),
    'expiry_warning_days': ('int', 'Days before an account or mandate expiry that warnings and alerts start (1–90).'),
    'infra_edit_controlled_tables': ('bool', 'Let administrators edit configuration tables (providers, connections, accounts, '
                                     'deployments…) directly in the Infrastructure table editor. Edits there skip four-eyes.'),
}
LIMITS = {'reauth_minutes': (1, 60), 'expiry_warning_days': (1, 90)}


# ---------- policies ----------

def list_policies() -> list[dict]:
    with store.transaction('read') as db:
        rows = db.execute('SELECT * FROM cfg_policies ORDER BY key').fetchall()
    return [{**store.clean(r), 'kind': POLICIES.get(r['key'], ('', ''))[0], 'description': POLICIES.get(r['key'], ('', ''))[1]} for r in rows]


def set_policy(actor: str, key: str, value: Any, version: int, reason: str) -> dict:
    if key not in POLICIES:
        raise HTTPException(404, 'Unknown policy.')
    kind = POLICIES[key][0]
    if kind == 'bool' and not isinstance(value, bool) or kind == 'int' and (isinstance(value, bool) or not isinstance(value, int)):
        raise HTTPException(422, f'{key} takes a {"yes/no" if kind == "bool" else "whole number"} value.')
    if key in LIMITS and not LIMITS[key][0] <= value <= LIMITS[key][1]:
        raise HTTPException(422, f'{key} must be between {LIMITS[key][0]} and {LIMITS[key][1]}.')
    with store.transaction('update') as db:
        before = db.execute('SELECT * FROM cfg_policies WHERE key=%s FOR UPDATE', (key,)).fetchone()
        if before['version'] != version:
            raise HTTPException(409, 'This policy was changed by someone else. Reload it first.')
        after = db.execute('UPDATE cfg_policies SET value=%s, version=version+1, updated_by=%s, updated_at=now() WHERE key=%s RETURNING *',
                           (Jsonb(value), actor, key)).fetchone()
        store.audit(db, actor, 'SET_POLICY', 'policy', key, before, after, reason)
        if key == 'four_eyes_live_changes' and value is False:
            from .alerts import enqueue
            enqueue(db, 'LIVE_CHANGE', 'CRITICAL', 'Four-eyes on LIVE changes turned OFF', f'By {actor}. Reason: {reason}')
        return store.clean(after)


# ---------- change requests ----------

def list_requests(status: str | None = None, limit: int = 100) -> list[dict]:
    with store.transaction('read') as db:
        rows = db.execute('SELECT id, entity, entity_id, action, summary, payload, base_version, reason, requested_by, requested_at, '
                          'status, decided_by, decided_at, decision_note FROM cfg_change_requests '
                          'WHERE %s::text IS NULL OR status=%s ORDER BY requested_at DESC LIMIT %s', (status, status, limit)).fetchall()
    return [store.clean(r) for r in rows]


def withdraw(actor: str, request_id: UUID) -> dict:
    with store.transaction('update') as db:
        row = db.execute('SELECT * FROM cfg_change_requests WHERE id=%s FOR UPDATE', (request_id,)).fetchone()
        if not row or row['status'] != 'PENDING':
            raise HTTPException(404, 'No pending change request with this id.')
        if row['requested_by'] != actor:
            raise HTTPException(403, 'Only the person who asked for a change can withdraw it.')
        db.execute("UPDATE cfg_change_requests SET status='WITHDRAWN', decided_by=%s, decided_at=now(), secret=NULL WHERE id=%s",
                   (actor, request_id))
        store.audit(db, actor, 'WITHDRAW_CHANGE', 'change request', request_id, None, {'status': 'WITHDRAWN'})
    return {'id': str(request_id), 'status': 'WITHDRAWN'}


def _secret(row: dict) -> dict:
    return credentials.decrypt(row['id'], bytes(row['secret'])) if row['secret'] is not None else {}


def decide(actor: str, request_id: UUID, approve: bool, note: str) -> dict:
    """Approve (apply) or reject a pending change. The approver must be a different person."""
    with store.transaction('update') as db:
        row = db.execute('SELECT * FROM cfg_change_requests WHERE id=%s FOR UPDATE', (request_id,)).fetchone()
        if not row or row['status'] != 'PENDING':
            raise HTTPException(404, 'No pending change request with this id.')
        if row['requested_by'] == actor:
            raise HTTPException(403, 'A change must be approved by a different person from the one who asked for it.')
        if not approve:
            db.execute("UPDATE cfg_change_requests SET status='REJECTED', decided_by=%s, decided_at=now(), decision_note=%s, secret=NULL WHERE id=%s",
                       (actor, note, request_id))
            store.audit(db, actor, 'REJECT_CHANGE', 'change request', request_id, None, {'note': note})
            return {'id': str(request_id), 'status': 'REJECTED'}
        secret = _secret(row)
    reason = f"Approved by {actor} (request {request_id} from {row['requested_by']}){': ' + note if note else ''}"
    try:
        result = _apply(row, secret, actor, reason)
        status, error = 'APPLIED', ''
    except HTTPException as exc:
        result, status, error = None, 'FAILED', str(exc.detail)[:400]
    with store.transaction('update') as db:
        db.execute('UPDATE cfg_change_requests SET status=%s, decided_by=%s, decided_at=now(), decision_note=%s, secret=NULL WHERE id=%s',
                   (status, actor, (note + (' · ' if note and error else '') + error)[:500], request_id))
        store.audit(db, actor, 'APPLY_CHANGE' if status == 'APPLIED' else 'APPLY_CHANGE_FAILED', 'change request', request_id, None,
                    {'status': status, 'error': error or None})
        from .alerts import enqueue
        enqueue(db, 'LIVE_CHANGE', 'WARNING' if status == 'APPLIED' else 'CRITICAL', f'LIVE change {status.lower()}: {row["action"].replace("_", " ").lower()}',
                f"{row['summary']}\nRequested by {row['requested_by']}, decided by {actor}.{' Error: ' + error if error else ''}")
    if status == 'FAILED':
        raise HTTPException(409, f'The change could not be applied and was marked failed: {error}')
    return {'id': str(request_id), 'status': status, 'result': result}


def _apply(row: dict, secret: dict, actor: str, reason: str) -> Any:
    payload, action = row['payload'], row['action']
    if action == 'CREATE_CONNECTION':
        return store.create(actor, 'connections', ConnectionIn.model_validate(payload['record']), approved=True, reason=reason)
    if action == 'UPDATE_CONNECTION':
        return store.update(actor, 'connections', row['entity_id'], ConnectionIn.model_validate(payload['record']),
                            row['base_version'], approved=True, reason=reason)
    if action.startswith('REPLACE_'):
        envelope = CredentialEnvelope.model_validate({'credential': {'kind': payload['kind'], **secret,
                                                                     **({'access_level': payload['access_level']} if payload['kind'] == 'MT5_LOGIN' else {})}})
        return store.set_credential(actor, payload['owner'], row['entity_id'], envelope.credential, approved=True, reason=reason)
    if action == 'ACCOUNT_SETUP':
        data = Mt5AccountSetupIn.model_validate({**payload['setup'], 'credential': None})
        if payload.get('credential'):
            data.credential = CredentialEnvelope.model_validate({'credential': {**payload['credential'], **secret}}).credential
        return store.setup_mt5_account(actor, data, approved=True, reason=reason)
    raise HTTPException(422, f'Unknown change action {action}.')


# ---------- preferences ----------

def get_prefs(username: str) -> dict:
    with store.transaction('read') as db:
        return {r['key']: r['value'] for r in db.execute('SELECT key, value FROM cfg_user_prefs WHERE username=%s', (username,)).fetchall()}


def set_pref(username: str, key: str, value: Any) -> None:
    with store.transaction('update') as db:
        if db.execute('SELECT count(*) AS n FROM cfg_user_prefs WHERE username=%s', (username,)).fetchone()['n'] >= 200 and \
                not db.execute('SELECT 1 FROM cfg_user_prefs WHERE username=%s AND key=%s', (username, key)).fetchone():
            raise HTTPException(422, 'Too many saved preferences.')
        db.execute('INSERT INTO cfg_user_prefs(username, key, value) VALUES (%s,%s,%s) ON CONFLICT (username, key) '
                   'DO UPDATE SET value=EXCLUDED.value, updated_at=now()', (username, key, Jsonb(value)))


# ---------- facts written by adapters ----------

def set_connection_status(connection_id: UUID, state: str, *, host: str | None = None, port: int | None = None,
                          logged_in: bool = False, heartbeat: bool = False, failover: bool = False,
                          server_time_offset_ms: int | None = None, error: str | None = None) -> None:
    """Adapters call this on every state change and heartbeat. Failures alert."""
    with store.transaction('update') as db:
        previous = db.execute('SELECT state FROM cfg_connection_status WHERE connection_id=%s', (connection_id,)).fetchone()
        db.execute('''INSERT INTO cfg_connection_status(connection_id, state, current_host, current_port, last_login_at,
                last_heartbeat_at, failovers_today, server_time_offset_ms, last_error, updated_at)
            VALUES (%(id)s, %(state)s, %(host)s, %(port)s, CASE WHEN %(login)s THEN now() END, CASE WHEN %(hb)s THEN now() END,
                    CASE WHEN %(fo)s THEN 1 ELSE 0 END, %(offset)s, %(error)s, now())
            ON CONFLICT (connection_id) DO UPDATE SET state=EXCLUDED.state,
                current_host=COALESCE(EXCLUDED.current_host, cfg_connection_status.current_host),
                current_port=COALESCE(EXCLUDED.current_port, cfg_connection_status.current_port),
                last_login_at=COALESCE(EXCLUDED.last_login_at, cfg_connection_status.last_login_at),
                last_heartbeat_at=COALESCE(EXCLUDED.last_heartbeat_at, cfg_connection_status.last_heartbeat_at),
                failovers_today=CASE WHEN cfg_connection_status.updated_at::date < current_date THEN EXCLUDED.failovers_today
                                     ELSE cfg_connection_status.failovers_today + EXCLUDED.failovers_today END,
                server_time_offset_ms=COALESCE(EXCLUDED.server_time_offset_ms, cfg_connection_status.server_time_offset_ms),
                last_error=EXCLUDED.last_error, updated_at=now()''',
                   {'id': connection_id, 'state': state, 'host': host, 'port': port, 'login': logged_in, 'hb': heartbeat,
                    'fo': failover, 'offset': server_time_offset_ms, 'error': (error or None) and error[:300]})
        if state == 'FAILED' and (not previous or previous['state'] != 'FAILED'):
            from .alerts import enqueue
            code = db.execute('SELECT code FROM cfg_connections WHERE id=%s', (connection_id,)).fetchone()
            enqueue(db, 'LOGIN_FAILED', 'CRITICAL', f"Connection {code['code'] if code else connection_id} failed", error or 'No detail')
        if failover:
            from .alerts import enqueue
            enqueue(db, 'ROUTE_FALLBACK', 'WARNING', 'Connection moved to another address', f'{connection_id} now on {host}:{port}')


def record_account_facts(account_id: UUID, *, margin_mode: str | None = None, broker_group: str | None = None,
                         leverage: int | None = None, stop_out_level: float | None = None,
                         max_open_orders: int | None = None, max_open_positions: int | None = None,
                         logged_in: bool = True) -> None:
    """Facts read from the broker at login override typed values and are marked BROKER."""
    with store.transaction('update') as db:
        before = db.execute('SELECT * FROM cfg_accounts WHERE id=%s FOR UPDATE', (account_id,)).fetchone()
        if not before:
            raise HTTPException(404, 'Account not found.')
        after = db.execute('''UPDATE cfg_accounts SET
                margin_mode=COALESCE(%s, margin_mode), margin_mode_source=CASE WHEN %s::text IS NULL THEN margin_mode_source ELSE 'BROKER' END,
                broker_group=COALESCE(%s, broker_group), leverage=COALESCE(%s, leverage), stop_out_level=COALESCE(%s, stop_out_level),
                max_open_orders=COALESCE(%s, max_open_orders), max_open_positions=COALESCE(%s, max_open_positions),
                broker_facts_read_at=now(), last_login_at=CASE WHEN %s THEN now() ELSE last_login_at END
            WHERE id=%s RETURNING *''', (margin_mode, margin_mode, broker_group, leverage, stop_out_level, max_open_orders,
                                         max_open_positions, logged_in, account_id)).fetchone()
        changed = {k for k in ('margin_mode', 'broker_group', 'leverage') if before[k] != after[k]}
        if changed:
            store.audit(db, 'broker', 'BROKER_FACTS', 'account', account_id, before, after, 'Read from the broker at login')


def record_symbol_spec(account_id: UUID, broker_symbol: str, spec: dict, source: str = 'BROKER', actor: str = 'broker') -> dict:
    with store.transaction('update') as db:
        if not db.execute('SELECT 1 FROM cfg_accounts WHERE id=%s', (account_id,)).fetchone():
            raise HTTPException(404, 'Account not found.')
        row = db.execute('INSERT INTO cfg_symbol_specs(account_id, broker_symbol, spec, source) VALUES (%s,%s,%s,%s) RETURNING *',
                         (account_id, broker_symbol, Jsonb(spec), source)).fetchone()
        if source == 'MANUAL':
            store.audit(db, actor, 'SET_SYMBOL_SPEC', 'account', account_id, None, {'broker_symbol': broker_symbol, 'spec': spec})
        return store.clean(row)


def latest_specs(account_id: UUID) -> list[dict]:
    with store.transaction('read') as db:
        rows = db.execute('SELECT DISTINCT ON (broker_symbol) broker_symbol, spec, source, read_at FROM cfg_symbol_specs '
                          'WHERE account_id=%s ORDER BY broker_symbol, read_at DESC', (account_id,)).fetchall()
    return [store.clean(r) for r in rows]


# ---------- CSV ----------

IMPORT_COLUMNS = ('connection_code', 'code', 'name', 'external_account_id', 'holder_name', 'base_currency', 'trading_permission',
                  'relationship', 'mandate_reference', 'mandate_expires_on', 'margin_mode', 'expires_on', 'groups')
EXPORT_COLUMNS = {
    'providers': ('code', 'name', 'kind', 'legal_entity', 'regulator', 'licence_number', 'jurisdiction', 'execution_model', 'status'),
    'connections': ('code', 'name', 'provider_code', 'platform', 'environment', 'purpose', 'adapter', 'server_utc_offset_minutes',
                    'dst_rule', 'max_orders_per_second', 'registered_egress_ip', 'status'),
    'accounts': ('code', 'name', 'provider_code', 'connection_code', 'environment', 'external_account_id', 'holder_name',
                 'base_currency', 'trading_permission', 'relationship', 'margin_mode', 'margin_mode_source', 'leverage',
                 'expires_on', 'status'),
}


def import_accounts(actor: str, text: str, dry_run: bool) -> dict:
    """All-or-nothing import. Passwords are never imported from files; add them per account."""
    if len(text) > 2_000_000:
        raise HTTPException(422, 'The file is larger than 2 MB.')
    reader = csv.DictReader(io.StringIO(text.lstrip('﻿')))
    if not reader.fieldnames or not {'connection_code', 'code', 'name', 'external_account_id', 'base_currency'} <= set(reader.fieldnames):
        raise HTTPException(422, 'The first line must name the columns: ' + ', '.join(IMPORT_COLUMNS))
    unknown = set(reader.fieldnames) - set(IMPORT_COLUMNS)
    if unknown:
        raise HTTPException(422, f"Unknown columns: {', '.join(sorted(unknown))}. Passwords cannot be imported.")
    rows = list(reader)
    if not 1 <= len(rows) <= 1000:
        raise HTTPException(422, 'Import between 1 and 1000 accounts at a time.')
    results, ok = [], True
    with store.transaction('create') as db:
        connections = {r['code']: r['id'] for r in db.execute('SELECT id, code FROM cfg_connections').fetchall()}
        groups = {r['code']: r['id'] for r in db.execute('SELECT id, code FROM cfg_account_groups').fetchall()}
        for number, raw in enumerate(rows, start=2):
            values = {k: (v or '').strip() for k, v in raw.items() if k in IMPORT_COLUMNS}
            line = {'line': number, 'code': (raw.get('code') or '').upper()}
            try:
                connection_id = connections.get(values.pop('connection_code', '').upper())
                if not connection_id:
                    raise HTTPException(422, 'Unknown connection_code.')
                group_codes = [g.strip().upper() for g in values.pop('groups', '').split('|') if g.strip()]
                missing = [g for g in group_codes if g not in groups]
                if missing:
                    raise HTTPException(422, f"Unknown group {', '.join(missing)}.")
                data = AccountIn.model_validate({**{k: v for k, v in values.items() if v != ''}, 'connection_id': connection_id,
                                                 'group_ids': [groups[g] for g in group_codes]})
            except ValidationError as exc:
                ok = False
                results.append({**line, 'status': 'ERROR', 'message': '; '.join(
                    f"{'.'.join(map(str, e['loc']))}: {e['msg'].removeprefix('Value error, ')}" for e in exc.errors())})
                continue
            except HTTPException as exc:
                ok = False
                results.append({**line, 'status': 'ERROR', 'message': str(exc.detail)})
                continue
            db.execute('SAVEPOINT import_row')
            try:
                record_id = store._insert(db, actor, 'accounts', data, 'CSV import')
                db.execute('RELEASE SAVEPOINT import_row')
                results.append({**line, 'status': 'OK', 'id': str(record_id)})
            except (HTTPException, psycopg.Error) as exc:
                db.execute('ROLLBACK TO SAVEPOINT import_row')
                ok = False
                results.append({**line, 'status': 'ERROR', 'message': str(exc.detail) if isinstance(exc, HTTPException) else _db_message(exc)})
        if dry_run or not ok:
            db.rollback()
    return {'dry_run': dry_run, 'applied': not dry_run and ok, 'ok': ok, 'rows': results,
            'counts': {'ok': sum(r['status'] == 'OK' for r in results), 'error': sum(r['status'] == 'ERROR' for r in results)}}


def _db_message(exc: Exception) -> str:
    constraint = getattr(getattr(exc, 'diag', None), 'constraint_name', None) or ''
    return store.UNIQUE_MESSAGES.get(constraint, 'Rejected by the database (duplicate or out-of-range value).')


def export_csv(entity: str) -> str:
    if entity not in EXPORT_COLUMNS:
        raise HTTPException(404, 'This list cannot be exported.')
    rows = store.list_records(entity, {}, '', include_archived=True)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(EXPORT_COLUMNS[entity])
    for r in rows:
        writer.writerow(['' if r.get(c) is None else _safe_cell(r.get(c)) for c in EXPORT_COLUMNS[entity]])
    return out.getvalue()


def _safe_cell(value: Any) -> str:
    text = str(value)
    # Spreadsheet formula injection guard.
    return "'" + text if text[:1] in ('=', '+', '-', '@') and not text.lstrip('-').replace('.', '', 1).isdigit() else text


# ---------- health ----------

def health() -> dict:
    """Everything needed to answer: can each account trade right now, and if not, why?"""
    from . import network
    accounts = store.list_records('accounts', {}, '')
    connections = store.list_records('connections', {}, '')
    with store.transaction('read') as db:
        pending = db.execute("SELECT count(*) AS n FROM cfg_change_requests WHERE status='PENDING'").fetchone()['n']
        controls = store.execution_controls(db)
        four_eyes = store.four_eyes_on(db)
        counts = {k: db.execute(f'SELECT count(*) AS n FROM {t}').fetchone()['n'] for k, t in (
            ('providers', 'cfg_providers'), ('connections', 'cfg_connections'), ('accounts', 'cfg_accounts'),
            ('instruments', 'cfg_instruments'), ('mappings', 'cfg_symbol_mappings'), ('strategies', 'cfg_strategies'),
            ('deployments', 'cfg_deployments'), ('alert_channels', 'cfg_alert_channels'))}
        credentials_saved = db.execute('SELECT count(DISTINCT account_id) AS n FROM cfg_credentials WHERE account_id IS NOT NULL').fetchone()['n']
        failed_alerts = db.execute("SELECT count(*) AS n FROM cfg_alert_outbox WHERE status='FAILED'").fetchone()['n']
    checklist = [
        {'step': 'Add a provider (broker)', 'done': counts['providers'] > 0, 'section': 'providers'},
        {'step': 'Add an account (MT5: login, password and server)', 'done': counts['accounts'] > 0, 'section': 'accounts'},
        {'step': 'Save the account passwords', 'done': counts['accounts'] > 0 and credentials_saved >= counts['accounts'], 'section': 'accounts'},
        {'step': 'Add instruments', 'done': counts['instruments'] > 0, 'section': 'instruments'},
        {'step': 'Map broker symbols to instruments', 'done': counts['mappings'] > 0, 'section': 'mappings'},
        {'step': 'Register a strategy and approve a version', 'done': counts['strategies'] > 0, 'section': 'strategies'},
        {'step': 'Deploy strategy × instrument × account', 'done': counts['deployments'] > 0, 'section': 'deployments'},
        {'step': 'Set up an alert channel', 'done': counts['alert_channels'] > 0, 'section': 'alerts'},
    ]
    return {'generated_at': datetime.now(timezone.utc).isoformat(), 'network': network.status(), 'controls': list(controls.values()),
            'four_eyes_live_changes': four_eyes, 'pending_approvals': pending, 'failed_alerts': failed_alerts,
            'checklist': checklist, 'connections': connections, 'accounts': accounts,
            'summary': {'accounts': len(accounts),
                        'data_ready': sum(not a['data_blockers'] for a in accounts),
                        'trading_ready': sum(not a['trading_blockers'] for a in accounts),
                        'with_warnings': sum(bool(a.get('warnings')) for a in accounts)}}
