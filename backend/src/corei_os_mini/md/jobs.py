"""Nightly market-data jobs (systemd timer corei-os-mini-md-nightly, 02:30 IST):

archive  For every account × symbol × day older than its plan's hot_days: export the
         ticks to Parquet under a temporary name, record its SHA-256 fingerprint, check
         with DuckDB that it holds every row, rename it into place, and only then delete
         those rows from PostgreSQL (in the same transaction that records the file).
         Bars older than bars_hot_days the same way. Empty day partitions are dropped.
journals Fingerprint closed journal days; retire journals older than md_journal_days
         once their day is archived.
verify   Re-hash every file; a missing or changed file is a CRITICAL event and alert.
backup   pg_dump of the database (custom format) and a copy of the archive and journal
         files to COREI_BACKUP_DIR; old dumps beyond md_backup_keep are removed.

Run: python -m corei_os_mini.md.jobs nightly|archive|journals|verify|backup
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from .. import config_store as cfg
from ..config import get_settings
from . import store
from .collector import md_root

log = logging.getLogger('corei.md.jobs')
TICK_COLUMNS = {'account_id': 'UUID', 'broker_symbol': 'VARCHAR', 'plan_id': 'UUID', 'source_time': 'TIMESTAMPTZ', 'received_at': 'TIMESTAMPTZ',
                'seq': 'BIGINT', 'bid': 'DOUBLE', 'ask': 'DOUBLE', 'last': 'DOUBLE', 'volume': 'DOUBLE', 'backfilled': 'BOOLEAN'}
BAR_COLUMNS = {'account_id': 'UUID', 'broker_symbol': 'VARCHAR', 'timeframe': 'VARCHAR', 'bar_time': 'TIMESTAMPTZ', 'open': 'DOUBLE',
               'high': 'DOUBLE', 'low': 'DOUBLE', 'close': 'DOUBLE', 'ticks': 'INTEGER', 'spread_avg': 'DOUBLE', 'volume': 'DOUBLE',
               'source': 'VARCHAR', 'complete': 'BOOLEAN'}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def safe(name: str) -> str:
    return re.sub(r'[^A-Za-z0-9._-]', '_', name)[:80] or '_'


def today() -> date:
    return datetime.now(timezone.utc).date()


# ---------- archive ----------

def _plans(db) -> dict[tuple, dict]:
    rows = db.execute("SELECT account_id, broker_symbol, hot_days, bars_hot_days, archive FROM md_collection_plans "
                      "ORDER BY (status='ARCHIVED'), updated_at DESC").fetchall()
    out: dict[tuple, dict] = {}
    for r in rows:
        out.setdefault((str(r['account_id']), r['broker_symbol']), dict(r))
    return out


def _export(db, query: sql.Composable, params: tuple, columns: dict[str, str], target: Path) -> int:
    """Rows of the query to a Parquet file (written under a temporary name, then renamed).
    Returns the row count checked by reading the file back with DuckDB."""
    import duckdb
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        csv_path, parquet_tmp = Path(tmp) / 'rows.csv', Path(tmp) / 'rows.parquet'
        db.execute("SET LOCAL TIME ZONE 'UTC'")
        with open(csv_path, 'wb') as out, db.cursor() as cur:
            with cur.copy(sql.SQL('COPY ({}) TO STDOUT WITH (FORMAT csv, HEADER true)').format(query), params) as copy:
                for data in copy:
                    out.write(data)
        expected = db.execute(sql.SQL('SELECT count(*) AS n FROM ({}) q').format(query), params).fetchone()['n']
        con = duckdb.connect()
        types = ', '.join(f"'{k}': '{v}'" for k, v in columns.items())
        con.execute(f"COPY (SELECT * FROM read_csv('{csv_path}', header=true, columns={{{types}}})) TO '{parquet_tmp}' "
                    "(FORMAT parquet, COMPRESSION zstd)")
        written = con.execute(f"SELECT count(*) FROM read_parquet('{parquet_tmp}')").fetchone()[0]
        con.close()
        if written != expected:
            raise RuntimeError(f'Archive check failed for {target.name}: {written} rows in the file, {expected} in the database')
        os.chmod(parquet_tmp, 0o640)
        os.replace(parquet_tmp, target)
        fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return written


def _record_file(db, kind: str, day: date, account_id, symbol, path: Path, rows: int | None) -> None:
    db.execute('''INSERT INTO md_files(kind, day, account_id, broker_symbol, path, rows, bytes, sha256, verified_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now()) ON CONFLICT (path) DO UPDATE SET rows=EXCLUDED.rows, bytes=EXCLUDED.bytes,
        sha256=EXCLUDED.sha256, verified_at=now(), verify_status='OK' ''', (kind, day, account_id, symbol, str(path), rows, path.stat().st_size, sha256(path)))


def _target(root: Path, kind: str, day: date, account_code: str, symbol: str) -> Path:
    base = root / 'archive' / kind / f'day={day.isoformat()}' / f'account={safe(account_code)}'
    path = base / f'{safe(symbol)}.parquet'
    n = 2
    while path.exists():
        path = base / f'{safe(symbol)}.part{n}.parquet'
        n += 1
    return path


def archive(root: Path | None = None, now: date | None = None) -> dict:
    root, now = root or md_root(), now or today()
    summary = {'tick_files': 0, 'tick_rows': 0, 'bar_files': 0, 'bar_rows': 0, 'deleted_without_archive': 0, 'partitions_dropped': 0, 'errors': []}
    with cfg.transaction('read') as db:
        plans = _plans(db)
        codes = {str(r['id']): r['code'] for r in db.execute('SELECT id, code FROM cfg_accounts').fetchall()}
        partitions = [r['relname'] for r in db.execute(
            "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid=i.inhrelid JOIN pg_class p ON p.oid=i.inhparent "
            "JOIN pg_namespace n ON n.oid=p.relnamespace WHERE p.relname='md_ticks' AND n.nspname=current_schema() ORDER BY c.relname").fetchall()]
    for part in partitions:
        day = datetime.strptime(part[-8:], '%Y%m%d').date()
        if day >= now:
            continue
        with cfg.transaction('read') as db:
            pairs = db.execute(sql.SQL('SELECT DISTINCT account_id, broker_symbol FROM {}').format(sql.Identifier(part))).fetchall()
        for pair in pairs:
            account, symbol = str(pair['account_id']), pair['broker_symbol']
            plan = plans.get((account, symbol), {'hot_days': 14, 'archive': True})
            if day >= now - timedelta(days=plan['hot_days']):
                continue
            where = sql.SQL('FROM {} WHERE account_id=%s AND broker_symbol=%s').format(sql.Identifier(part))
            try:
                with cfg.transaction('write') as db:
                    if plan['archive']:
                        target = _target(root, 'ticks', day, codes.get(account, account[:8]), symbol)
                        query = sql.SQL('SELECT ' + ', '.join(TICK_COLUMNS) + ' ') + where + sql.SQL(' ORDER BY received_at, seq')
                        rows = _export(db, query, (pair['account_id'], symbol), TICK_COLUMNS, target)
                        _record_file(db, 'TICKS', day, pair['account_id'], symbol, target, rows)
                        summary['tick_files'] += 1
                        summary['tick_rows'] += rows
                    else:
                        summary['deleted_without_archive'] += db.execute(sql.SQL('SELECT count(*) AS n ') + where, (pair['account_id'], symbol)).fetchone()['n']
                    db.execute(sql.SQL('DELETE ') + where, (pair['account_id'], symbol))
            except Exception as exc:  # noqa: BLE001 - one pair must not stop the others
                log.exception('Archive failed for %s %s %s', part, account, symbol)
                summary['errors'].append(f'{day} {codes.get(account, account)} {symbol}: {exc}'[:300])
        with cfg.transaction('write') as db:
            if not db.execute(sql.SQL('SELECT 1 FROM {} LIMIT 1').format(sql.Identifier(part))).fetchone():
                db.execute(sql.SQL('DROP TABLE {}').format(sql.Identifier(part)))
                summary['partitions_dropped'] += 1
    # Bars, per account × symbol × day, beyond bars_hot_days.
    with cfg.transaction('read') as db:
        groups = db.execute("SELECT account_id, broker_symbol, (bar_time AT TIME ZONE 'UTC')::date AS day FROM md_bars "
                            "WHERE bar_time < %s GROUP BY 1, 2, 3 ORDER BY 3", (datetime.combine(now, datetime.min.time(), timezone.utc),)).fetchall()
    for g in groups:
        account, symbol, day = str(g['account_id']), g['broker_symbol'], g['day']
        plan = plans.get((account, symbol), {'bars_hot_days': 365, 'archive': True})
        if day >= now - timedelta(days=plan['bars_hot_days']):
            continue
        start = datetime.combine(day, datetime.min.time(), timezone.utc)
        where = sql.SQL('FROM md_bars WHERE account_id=%s AND broker_symbol=%s AND bar_time >= %s AND bar_time < %s')
        params = (g['account_id'], symbol, start, start + timedelta(days=1))
        try:
            with cfg.transaction('write') as db:
                if plan['archive']:
                    target = _target(root, 'bars', day, codes.get(account, account[:8]), symbol)
                    rows = _export(db, sql.SQL('SELECT ' + ', '.join(BAR_COLUMNS) + ' ') + where + sql.SQL(' ORDER BY timeframe, bar_time'),
                                   params, BAR_COLUMNS, target)
                    _record_file(db, 'BARS', day, g['account_id'], symbol, target, rows)
                    summary['bar_files'] += 1
                    summary['bar_rows'] += rows
                db.execute(sql.SQL('DELETE ') + where, params)
        except Exception as exc:  # noqa: BLE001
            log.exception('Bar archive failed')
            summary['errors'].append(f'bars {day} {symbol}: {exc}'[:300])
    store.event('ARCHIVE', 'CRITICAL' if summary['errors'] else 'INFO', detail=summary)
    return summary


# ---------- journals ----------

def journals(root: Path | None = None, now: date | None = None) -> dict:
    root, now = root or md_root(), now or today()
    keep = int(store.policy('md_journal_days', 30))
    summary = {'fingerprinted': 0, 'retired': 0}
    folder = root / 'journal'
    if not folder.is_dir():
        return summary
    with cfg.transaction('write') as db:
        known = {r['path']: r for r in db.execute("SELECT path, verify_status FROM md_files WHERE kind='JOURNAL'").fetchall()}
        oldest_hot = db.execute("SELECT min(received_at) AS t FROM md_ticks").fetchone()['t']
        for day_dir in sorted(p for p in folder.iterdir() if p.is_dir()):
            try:
                day = date.fromisoformat(day_dir.name)
            except ValueError:
                continue
            if day >= now:
                continue
            for f in sorted(day_dir.glob('*.jsonl.gz')):
                if str(f) not in known:
                    _record_file(db, 'JOURNAL', day, None, None, f, None)
                    summary['fingerprinted'] += 1
            archived = oldest_hot is None or oldest_hot.date() > day
            if day < now - timedelta(days=keep) and archived:
                for f in day_dir.glob('*.jsonl.gz'):
                    if sha256(f) != db.execute('SELECT sha256 FROM md_files WHERE path=%s', (str(f),)).fetchone()['sha256']:
                        continue  # a changed file is kept for the verify job to report
                    f.unlink()
                    db.execute("UPDATE md_files SET verify_status='RETIRED', verified_at=now() WHERE path=%s", (str(f),))
                    summary['retired'] += 1
                if not any(day_dir.iterdir()):
                    day_dir.rmdir()
    return summary


# ---------- verify ----------

def verify() -> dict:
    summary = {'checked': 0, 'ok': 0, 'mismatch': [], 'missing': []}
    with cfg.transaction('read') as db:
        files = db.execute("SELECT id, path, sha256 FROM md_files WHERE verify_status <> 'RETIRED' ORDER BY id").fetchall()
    for f in files:
        path = Path(f['path'])
        summary['checked'] += 1
        status = 'MISSING' if not path.is_file() else 'OK' if sha256(path) == f['sha256'] else 'MISMATCH'
        if status == 'OK':
            summary['ok'] += 1
        else:
            summary['missing' if status == 'MISSING' else 'mismatch'].append(f['path'])
        with cfg.transaction('write') as db:
            db.execute('UPDATE md_files SET verify_status=%s, verified_at=now() WHERE id=%s', (status, f['id']))
    bad = summary['mismatch'] or summary['missing']
    store.event('VERIFY', 'CRITICAL' if bad else 'INFO',
                detail={**summary, 'mismatch': summary['mismatch'][:20], 'missing': summary['missing'][:20]})
    return summary


# ---------- backup ----------

def _pg_dump(root: str = '/usr/lib/postgresql') -> str:
    """The newest installed pg_dump: it must be at least the server's version (an older one refuses with
    "server version mismatch"), and a newer one dumps older servers. COREI_PG_DUMP overrides."""
    configured = os.getenv('COREI_PG_DUMP')
    if configured and Path(configured).is_file():
        return configured
    versioned = sorted((p for p in Path(root).glob('*/bin/pg_dump') if p.parent.parent.name.isdigit()),
                       key=lambda p: int(p.parent.parent.name), reverse=True)
    for candidate in [*map(str, versioned), shutil.which('pg_dump')]:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError('pg_dump not found (set COREI_PG_DUMP)')


def _backup_row(kind: str) -> int:
    with cfg.transaction('write') as db:
        return db.execute('INSERT INTO md_backups(kind) VALUES (%s) RETURNING id', (kind,)).fetchone()['id']


def _backup_done(row_id: int, ok: bool, path: str | None = None, size: int | None = None, files: int | None = None, error: str | None = None) -> None:
    with cfg.transaction('write') as db:
        db.execute('UPDATE md_backups SET finished_at=now(), ok=%s, path=%s, bytes=%s, files=%s, error=%s WHERE id=%s',
                   (ok, path, size, files, (error or None) and error[:500], row_id))


def backup(root: Path | None = None) -> dict:
    root = root or md_root()
    target = os.getenv('COREI_BACKUP_DIR', '').strip()
    summary: dict = {'database': None, 'files': None}
    if not target:
        for kind in ('DATABASE', 'FILES'):
            _backup_done(_backup_row(kind), False, error='Backups are not configured: set COREI_BACKUP_DIR in /etc/corei-os-mini/workstation.env')
        store.event('BACKUP', 'WARNING', detail={'error': 'COREI_BACKUP_DIR is not set'})
        return summary
    dest = Path(target)
    # Database
    row = _backup_row('DATABASE')
    try:
        info = conninfo_to_dict(get_settings().database_url or '')
        password = info.pop('password', None)
        (dest / 'db').mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        final = dest / 'db' / f'corei-{stamp}.dump'
        tmp = final.with_suffix('.dump.tmp')
        env = {**os.environ, **({'PGPASSWORD': password} if password else {})}
        result = subprocess.run([_pg_dump(), '--format=custom', '--no-owner', f'--file={tmp}', make_conninfo(**info)],
                                capture_output=True, text=True, env=env, timeout=3600, check=False)
        if result.returncode:
            raise RuntimeError((result.stderr or 'pg_dump failed').strip()[:400])
        os.chmod(tmp, 0o600)
        os.replace(tmp, final)
        keep = int(store.policy('md_backup_keep', 14))
        for old in sorted((dest / 'db').glob('corei-*.dump'))[:-keep]:
            old.unlink()
        _backup_done(row, True, str(final), final.stat().st_size, 1)
        summary['database'] = {'path': str(final), 'bytes': final.stat().st_size, 'sha256': sha256(final)}
    except Exception as exc:  # noqa: BLE001
        _backup_done(row, False, error=str(exc))
        store.event('BACKUP', 'CRITICAL', detail={'kind': 'DATABASE', 'error': str(exc)[:300]})
        summary['database'] = {'error': str(exc)}
    # Files
    row = _backup_row('FILES')
    try:
        copied, total, changed = 0, 0, []
        with cfg.transaction('read') as db:
            fingerprints = {r['path']: r['sha256'] for r in db.execute("SELECT path, sha256 FROM md_files WHERE verify_status <> 'RETIRED'").fetchall()}
        for sub in ('archive', 'journal'):
            src_root = root / sub
            if not src_root.is_dir():
                continue
            for src in src_root.rglob('*'):
                if not src.is_file() or src.name.endswith('.tmp'):
                    continue
                rel = src.relative_to(root)
                out = dest / 'files' / rel
                total += 1
                expected = fingerprints.get(str(src))
                if expected and sha256(src) != expected:
                    changed.append(str(rel))   # never overwrite a good backup copy with a changed file
                    continue
                if out.is_file() and out.stat().st_size == src.stat().st_size and (not expected or sha256(out) == expected):
                    continue
                out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                tmp = out.with_suffix(out.suffix + '.tmp')
                shutil.copy2(src, tmp)
                if expected and sha256(tmp) != expected:
                    tmp.unlink()
                    raise RuntimeError(f'Copy of {rel} does not match its fingerprint (disk or copy error)')
                os.replace(tmp, out)
                copied += 1
        same_disk = os.stat(dest).st_dev == os.stat(root).st_dev if root.exists() else False
        _backup_done(row, True, str(dest / 'files'), None, total)
        summary['files'] = {'files': total, 'copied': copied, 'same_disk': same_disk, 'changed_not_copied': changed}
        if changed:
            store.event('BACKUP', 'CRITICAL', detail={'warning': 'Files that no longer match their fingerprint were not copied over their backup.',
                                                      'files': changed[:20]})
        if same_disk:
            store.event('BACKUP', 'WARNING', detail={'warning': 'The backup folder is on the same disk as the data; a disk failure loses both.'})
    except Exception as exc:  # noqa: BLE001
        _backup_done(row, False, error=str(exc))
        store.event('BACKUP', 'CRITICAL', detail={'kind': 'FILES', 'error': str(exc)[:300]})
        summary['files'] = {'error': str(exc)}
    if summary['database'] and 'error' not in summary['database'] and summary['files'] and 'error' not in summary['files']:
        store.event('BACKUP', 'INFO', detail={'database_bytes': summary['database']['bytes'], 'files': summary['files']['files'],
                                              'copied': summary['files']['copied']})
    return summary


def nightly() -> dict:
    out = {}
    for name, job in (('archive', archive), ('journals', journals), ('verify', verify), ('backup', backup)):
        try:
            out[name] = job()
        except Exception as exc:  # noqa: BLE001 - later jobs still run
            log.exception('%s failed', name)
            out[name] = {'error': str(exc)}
            store.event('ERROR', 'CRITICAL', detail={'job': name, 'error': str(exc)[:300]})
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    parser = argparse.ArgumentParser(description='COREI market-data jobs')
    parser.add_argument('job', choices=['nightly', 'archive', 'journals', 'verify', 'backup'])
    job = parser.parse_args().job
    result = {'nightly': nightly, 'archive': archive, 'journals': journals, 'verify': verify, 'backup': backup}[job]()
    import json
    print(json.dumps(result, default=str, indent=2))


if __name__ == '__main__':
    main()
