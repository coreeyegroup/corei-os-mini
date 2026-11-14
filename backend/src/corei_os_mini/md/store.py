"""Database reads and writes for the collector and the market-data screens.

The collector writes in one transaction per second: ticks (COPY into the day's
partition), latest prices, bars, quality minutes, events and its status. Events of
WARNING/CRITICAL severity also queue alerts (MARKET_DATA or DATA_INTEGRITY).
"""
from __future__ import annotations


from datetime import date, datetime, timezone
from typing import Any, Iterable
from uuid import UUID

from psycopg.types.json import Jsonb

from .. import config_store as store

ALERT_KINDS = {'DISCONNECTED', 'FEED_SWITCH', 'FEED_RESTORED', 'STALE', 'GAP', 'DEVIATION', 'BACKLOG', 'ERROR'}
INTEGRITY_KINDS = {'VERIFY', 'BACKUP', 'DISK', 'ARCHIVE'}


def active_plans() -> list[dict]:
    with store.transaction('read') as db:
        return [dict(r) for r in db.execute('''SELECT p.*, a.code AS account_code, a.external_account_id, a.connection_id, a.status AS account_status,
                c.platform, c.environment, pr.holidays,
                COALESCE((SELECT (s.spec->>'digits')::int FROM cfg_symbol_specs s WHERE s.account_id=p.account_id AND s.broker_symbol=p.broker_symbol
                          ORDER BY s.read_at DESC LIMIT 1), 5) AS digits,
                EXISTS (SELECT 1 FROM cfg_deployments d WHERE d.instrument_id=p.instrument_id AND d.account_id=p.account_id AND d.status='ACTIVE') AS deployed
            FROM md_collection_plans p JOIN cfg_accounts a ON a.id=p.account_id JOIN cfg_connections c ON c.id=a.connection_id
            JOIN cfg_providers pr ON pr.id=c.provider_id
            WHERE p.status='ACTIVE' ''').fetchall()]


def paused_scopes() -> set[str]:
    with store.transaction('read') as db:
        return {r['scope'] for r in db.execute('SELECT scope FROM md_controls WHERE paused').fetchall()}


def policy(key: str, default: Any) -> Any:
    with store.transaction('read') as db:
        return store.policy(db, key, default)


def ensure_partitions(db, days: Iterable[date]) -> None:
    """One cheap call per day per batch, never cached: a cache went stale when a partition was dropped
    (archive, operator) or the schema changed, and the write then failed until restart."""
    for day in sorted(set(days)):
        db.execute('SELECT md_ensure_partition(%s)', (day,))


def write(batch: dict) -> None:
    """batch: ticks [tuple], latest {key: tuple}, bars [dict], quality [dict], events [dict], status {account_id: dict}."""
    ticks, latest, bars, quality, events, status = (batch.get(k) for k in ('ticks', 'latest', 'bars', 'quality', 'events', 'status'))
    if not any((ticks, latest, bars, quality, events, status)):
        return
    with store.transaction('write') as db:
        if ticks:
            ensure_partitions(db, (t[4].date() for t in ticks))
            with db.cursor() as cur, cur.copy('COPY md_ticks (account_id, broker_symbol, plan_id, source_time, received_at, seq, bid, ask, last, volume, backfilled) '
                                              'FROM STDIN') as copy:
                for row in ticks:
                    copy.write_row(row)
        if latest:
            with db.cursor() as cur:
                cur.executemany('''INSERT INTO md_latest(account_id, broker_symbol, bid, ask, last, source_time, received_at, seq)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (account_id, broker_symbol) DO UPDATE SET bid=EXCLUDED.bid, ask=EXCLUDED.ask,
                    last=EXCLUDED.last, source_time=EXCLUDED.source_time, received_at=EXCLUDED.received_at, seq=EXCLUDED.seq''', list(latest.values()))
            db.execute("SELECT pg_notify('md_latest', %s)", (str(len(latest)),))
        if bars:
            save_bars(db, bars)
        if quality:
            with db.cursor() as cur:
                cur.executemany('''INSERT INTO md_quality(account_id, broker_symbol, minute, ticks, latency_p50_ms, latency_p95_ms, max_gap_ms, spread_avg,
                        spread_max, frozen_ms, spikes, deviation_bp, score)
                    VALUES (%(account_id)s,%(broker_symbol)s,%(minute)s,%(ticks)s,%(latency_p50_ms)s,%(latency_p95_ms)s,%(max_gap_ms)s,%(spread_avg)s,
                        %(spread_max)s,%(frozen_ms)s,%(spikes)s,%(deviation_bp)s,%(score)s)
                    ON CONFLICT (account_id, broker_symbol, minute) DO UPDATE SET ticks=md_quality.ticks + EXCLUDED.ticks,
                        latency_p95_ms=GREATEST(md_quality.latency_p95_ms, EXCLUDED.latency_p95_ms), score=LEAST(md_quality.score, EXCLUDED.score)''', quality)
        for e in events or []:
            record_event(db, **e)
        for account_id, s in (status or {}).items():
            db.execute('''INSERT INTO md_collector_status(account_id, state, host, port, pid, subscribed, ticks_total, lag_ms, backlog,
                    server_offset_minutes, started_at, heartbeat_at, last_error)
                VALUES (%(account_id)s,%(state)s,%(host)s,%(port)s,%(pid)s,%(subscribed)s,%(ticks_total)s,%(lag_ms)s,%(backlog)s,
                    %(server_offset_minutes)s,%(started_at)s,now(),%(last_error)s)
                ON CONFLICT (account_id) DO UPDATE SET state=EXCLUDED.state, host=EXCLUDED.host, port=EXCLUDED.port, pid=EXCLUDED.pid,
                    subscribed=EXCLUDED.subscribed, ticks_total=EXCLUDED.ticks_total, lag_ms=EXCLUDED.lag_ms, backlog=EXCLUDED.backlog,
                    server_offset_minutes=EXCLUDED.server_offset_minutes, started_at=EXCLUDED.started_at, heartbeat_at=now(),
                    last_error=EXCLUDED.last_error''', {'account_id': account_id, **s})


def save_bars(db, bars: list[dict]) -> None:
    built = [b for b in bars if b.get('source', 'BUILT') == 'BUILT']
    broker = [b for b in bars if b.get('source') == 'BROKER']
    columns = 'account_id, broker_symbol, timeframe, bar_time, open, high, low, close, ticks, spread_avg, volume, source, complete'
    values = ('%(account_id)s,%(broker_symbol)s,%(timeframe)s,%(bar_time)s,%(open)s,%(high)s,%(low)s,%(close)s,%(ticks)s,'
              '%(spread_avg)s,%(volume)s,%(source)s,%(complete)s')
    with db.cursor() as cur:
        if built:
            # Our own bars replace our own partial bars, never a broker bar already there.
            cur.executemany(f'''INSERT INTO md_bars({columns}) VALUES ({values}) ON CONFLICT (account_id, broker_symbol, timeframe, bar_time)
                DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, close=EXCLUDED.close, ticks=EXCLUDED.ticks,
                spread_avg=EXCLUDED.spread_avg, volume=EXCLUDED.volume, complete=EXCLUDED.complete WHERE md_bars.source='BUILT' ''',
                            [{'source': 'BUILT', **b} for b in built])
        if broker:
            # Broker history fills gaps only; bars we built from live prices are kept.
            cur.executemany(f'INSERT INTO md_bars({columns}) VALUES ({values}) ON CONFLICT DO NOTHING', broker)


def record_event(db, kind: str, severity: str = 'INFO', detail: dict | None = None, account_id: Any = None,
                 broker_symbol: str | None = None, plan_id: Any = None, at: datetime | None = None) -> None:
    db.execute('INSERT INTO md_events(at, account_id, broker_symbol, plan_id, kind, severity, detail) VALUES (COALESCE(%s, now()),%s,%s,%s,%s,%s,%s)',
               (at, account_id, broker_symbol, plan_id, kind, severity, Jsonb(detail or {})))
    if severity in ('WARNING', 'CRITICAL') and (kind in ALERT_KINDS or kind in INTEGRITY_KINDS):
        from ..alerts import enqueue
        where = f' {broker_symbol}' if broker_symbol else ''
        enqueue(db, 'DATA_INTEGRITY' if kind in INTEGRITY_KINDS else 'MARKET_DATA', severity,
                f'Market data{where}: {kind.replace("_", " ").lower()}', ', '.join(f'{k}: {v}' for k, v in (detail or {}).items())[:1500])


def event(kind: str, severity: str = 'INFO', **kwargs) -> None:
    with store.transaction('write') as db:
        record_event(db, kind, severity, **kwargs)


# ---------- feed selection ----------

def feed_state() -> dict[str, dict]:
    with store.transaction('read') as db:
        return {str(r['instrument_id']): dict(r) for r in db.execute('SELECT * FROM md_feed_state').fetchall()}


def switch_feed(instrument_id: Any, plan_id: Any, kind: str, reason: str, detail: dict) -> None:
    with store.transaction('write') as db:
        db.execute('INSERT INTO md_feed_state(instrument_id, active_plan_id, switched_at, reason) VALUES (%s,%s,now(),%s) '
                   'ON CONFLICT (instrument_id) DO UPDATE SET active_plan_id=EXCLUDED.active_plan_id, switched_at=now(), reason=EXCLUDED.reason',
                   (instrument_id, plan_id, reason))
        record_event(db, kind, 'CRITICAL' if kind == 'FEED_SWITCH' else 'WARNING', {**detail, 'reason': reason}, plan_id=plan_id)


# ---------- disk guard ----------

def pause_for_disk(used_pct: float, limit: float) -> list[str]:
    with store.transaction('write') as db:
        rows = db.execute('''UPDATE md_collection_plans SET status='PAUSED', status_reason=%s, version=version+1, updated_by='collector', updated_at=now()
            WHERE status='ACTIVE' AND purpose IN ('RESEARCH','MONITORING') RETURNING id, code''',
                          (f'Paused automatically: disk {used_pct:.1f}% used (limit {limit}%)',)).fetchall()
        for r in rows:
            store.audit(db, 'collector', 'MD_DISK_PAUSE', 'md_plan', r['id'], None, {'code': r['code'], 'disk_used_pct': used_pct})
        if rows:
            record_event(db, 'DISK', 'CRITICAL', {'used_pct': round(used_pct, 1), 'limit_pct': limit, 'paused_plans': [r['code'] for r in rows]})
        return [r['code'] for r in rows]


# ---------- backfill jobs ----------

def open_jobs(plan_ids: list) -> list[dict]:
    if not plan_ids:
        return []
    with store.transaction('read') as db:
        return [dict(r) for r in db.execute("SELECT * FROM md_jobs WHERE plan_id = ANY(%s) AND status IN ('QUEUED','RUNNING') ORDER BY created_at",
                                            (plan_ids,)).fetchall()]


def job_status(job_id: Any) -> str | None:
    with store.transaction('read') as db:
        row = db.execute('SELECT status FROM md_jobs WHERE id=%s', (job_id,)).fetchone()
        return row['status'] if row else None


def job_progress(job_id: Any, status: str, done_until: datetime | None = None, bars: list[dict] | None = None,
                 error: str | None = None) -> None:
    with store.transaction('write') as db:
        if bars:
            save_bars(db, bars)
        row = db.execute('''UPDATE md_jobs SET status=%s, done_until=COALESCE(%s, done_until), bars_saved=bars_saved+%s, error=%s, updated_at=now()
            WHERE id=%s AND status IN ('QUEUED','RUNNING') RETURNING *''', (status, done_until, len(bars or []), error, job_id)).fetchone()
        if row and status in ('DONE', 'FAILED'):
            record_event(db, 'BACKFILL', 'WARNING' if status == 'FAILED' else 'INFO',
                         {'job': str(job_id), 'timeframe': row['timeframe'], 'bars_saved': row['bars_saved'], 'status': status,
                          **({'error': error} if error else {})}, plan_id=row['plan_id'])


def queue_gap_backfill(plan_id: Any, timeframe: str, start: datetime, end: datetime) -> None:
    """After an outage, fetch the missing bars from the broker (marked BROKER)."""
    from uuid import uuid4
    with store.transaction('write') as db:
        if not db.execute("SELECT 1 FROM md_jobs WHERE plan_id=%s AND timeframe=%s AND status IN ('QUEUED','RUNNING')", (plan_id, timeframe)).fetchone():
            db.execute('INSERT INTO md_jobs(id, plan_id, timeframe, range_from, range_to, reason, requested_by) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                       (uuid4(), plan_id, timeframe, start, end, 'Fill the outage from broker history', 'collector'))


# ---------- screens ----------

def latest(account_id: UUID | None = None) -> list[dict]:
    with store.transaction('read') as db:
        rows = db.execute('''SELECT l.*, a.code AS account_code, a.name AS account_name, extract(epoch FROM now() - l.received_at)::float8 AS age_seconds,
                (SELECT (s.spec->>'digits')::int FROM cfg_symbol_specs s WHERE s.account_id=l.account_id AND s.broker_symbol=l.broker_symbol
                          ORDER BY s.read_at DESC LIMIT 1) AS digits,
                (SELECT q.score FROM md_quality q WHERE q.account_id=l.account_id AND q.broker_symbol=l.broker_symbol ORDER BY q.minute DESC LIMIT 1) AS score,
                d.day_open, d.day_high, d.day_low, d.day_time
            FROM md_latest l JOIN cfg_accounts a ON a.id=l.account_id
            LEFT JOIN LATERAL (SELECT b.open AS day_open, b.high AS day_high, b.low AS day_low, b.bar_time AS day_time FROM md_bars b
                WHERE b.account_id=l.account_id AND b.broker_symbol=l.broker_symbol AND b.timeframe='D1' ORDER BY b.bar_time DESC LIMIT 1) d ON true''' + (' WHERE l.account_id=%s' if account_id else '') +
                          ' ORDER BY l.broker_symbol, a.code', (account_id,) if account_id else ()).fetchall()
    return [store.clean(r) for r in rows]


def ticks(account_id: UUID, broker_symbol: str, seconds: int = 300, limit: int = 3000) -> list[dict]:
    """The latest prices of one symbol from one account, oldest first (chart and time & sales)."""
    with store.transaction('read') as db:
        rows = db.execute('SELECT received_at, source_time, bid, ask, last, volume FROM md_ticks WHERE account_id=%s AND broker_symbol=%s '
                          'AND received_at > now() - make_interval(secs => %s) ORDER BY received_at DESC, seq DESC LIMIT %s',
                          (account_id, broker_symbol, seconds, limit)).fetchall()
    return [store.clean(r) for r in reversed(rows)]


def quality(account_id: UUID, broker_symbol: str, minutes: int = 60) -> list[dict]:
    with store.transaction('read') as db:
        rows = db.execute("SELECT * FROM md_quality WHERE account_id=%s AND broker_symbol=%s AND minute > now() - make_interval(mins => %s) "
                          'ORDER BY minute DESC', (account_id, broker_symbol, minutes)).fetchall()
    return [store.clean(r) for r in rows]


def quality_summary(hours: int = 24) -> list[dict]:
    """Per account × symbol over the last hours: average score, update rate, latency, spread,
    gaps, spikes and deviation — the broker comparison."""
    with store.transaction('read') as db:
        rows = db.execute('''SELECT q.account_id, a.code AS account_code, c.code AS connection_code, q.broker_symbol,
                p.instrument_id, i.symbol AS instrument_symbol, round(avg(q.score)::numeric, 1)::float8 AS score,
                sum(q.ticks)::bigint AS ticks, round(avg(q.latency_p50_ms)::numeric, 1)::float8 AS latency_p50_ms, round(max(q.latency_p95_ms)::numeric, 1)::float8 AS latency_p95_ms,
                round(avg(q.spread_avg)::numeric, 2)::float8 AS spread_avg, round(max(q.spread_max)::numeric, 2)::float8 AS spread_max,
                round((max(q.max_gap_ms) / 1000)::numeric, 1)::float8 AS max_gap_seconds, sum(q.spikes)::int AS spikes,
                round(avg(abs(q.deviation_bp))::numeric, 2)::float8 AS deviation_bp, count(*) FILTER (WHERE q.ticks = 0) AS empty_minutes
            FROM md_quality q JOIN cfg_accounts a ON a.id=q.account_id JOIN cfg_connections c ON c.id=a.connection_id
            LEFT JOIN md_collection_plans p ON p.account_id=q.account_id AND p.broker_symbol=q.broker_symbol AND p.status <> 'ARCHIVED'
            LEFT JOIN cfg_instruments i ON i.id=p.instrument_id
            WHERE q.minute > now() - make_interval(hours => %s)
            GROUP BY q.account_id, a.code, c.code, q.broker_symbol, p.instrument_id, i.symbol
            ORDER BY i.symbol NULLS LAST, q.broker_symbol, avg(q.score) DESC''', (hours,)).fetchall()
    return [store.clean(r) for r in rows]


def events(limit: int = 200, kind: str | None = None) -> list[dict]:
    with store.transaction('read') as db:
        rows = db.execute('SELECT e.*, a.code AS account_code FROM md_events e LEFT JOIN cfg_accounts a ON a.id=e.account_id'
                          + (' WHERE e.kind=%s' if kind else '') + ' ORDER BY e.id DESC LIMIT %s',
                          ((kind, limit) if kind else (limit,))).fetchall()
    return [store.clean(r) for r in rows]


def collectors() -> list[dict]:
    with store.transaction('read') as db:
        rows = db.execute('''SELECT s.*, a.code AS account_code, a.name AS account_name, extract(epoch FROM now() - s.heartbeat_at)::float8 AS heartbeat_age_seconds,
                (SELECT count(*) FROM md_collection_plans p WHERE p.account_id=s.account_id AND p.status='ACTIVE') AS active_plans
            FROM md_collector_status s JOIN cfg_accounts a ON a.id=s.account_id
            WHERE a.status <> 'ARCHIVED' AND (s.state <> 'STOPPED' OR s.heartbeat_at > now() - interval '1 day')
            ORDER BY a.code''').fetchall()
    from .explain import explain
    return [{**store.clean(r), 'problem': explain(r['last_error']) if r['state'] not in ('CONNECTED', 'STOPPED', 'PAUSED') else None} for r in rows]


def bars(account_id: UUID, broker_symbol: str, timeframe: str, limit: int = 500) -> list[dict]:
    with store.transaction('read') as db:
        rows = db.execute('SELECT * FROM md_bars WHERE account_id=%s AND broker_symbol=%s AND timeframe=%s ORDER BY bar_time DESC LIMIT %s',
                          (account_id, broker_symbol, timeframe, limit)).fetchall()
    return [store.clean(r) for r in rows]


def files(limit: int = 500) -> list[dict]:
    with store.transaction('read') as db:
        rows = db.execute('SELECT f.*, a.code AS account_code FROM md_files f LEFT JOIN cfg_accounts a ON a.id=f.account_id '
                          'ORDER BY f.day DESC, f.kind, f.broker_symbol LIMIT %s', (limit,)).fetchall()
    return [store.clean(r) for r in rows]


def backups(limit: int = 30) -> list[dict]:
    with store.transaction('read') as db:
        return [store.clean(r) for r in db.execute('SELECT * FROM md_backups ORDER BY id DESC LIMIT %s', (limit,)).fetchall()]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------- pre-send price check ----------

def price_check(account_id: UUID, broker_symbol: str) -> dict:
    """Is the live price for this account × symbol fit to trade on? Problems block an
    order; warnings inform. Used by the order check before every order."""
    with store.transaction('read') as db:
        last = db.execute('SELECT *, extract(epoch FROM now() - received_at) AS age FROM md_latest WHERE account_id=%s AND broker_symbol=%s',
                          (account_id, broker_symbol)).fetchone()
        plan = db.execute("SELECT * FROM md_collection_plans WHERE account_id=%s AND broker_symbol=%s AND status <> 'ARCHIVED'",
                          (account_id, broker_symbol)).fetchone()
        collector = db.execute('SELECT state FROM md_collector_status WHERE account_id=%s', (account_id,)).fetchone()
        digits = db.execute("SELECT (spec->>'digits')::int AS d FROM cfg_symbol_specs WHERE account_id=%s AND broker_symbol=%s ORDER BY read_at DESC LIMIT 1",
                            (account_id, broker_symbol)).fetchone()
        feed = db.execute('SELECT f.active_plan_id, p.code FROM md_feed_state f JOIN md_collection_plans p ON p.id=f.active_plan_id '
                          'WHERE f.instrument_id=%s', (plan['instrument_id'],)).fetchone() if plan and plan['instrument_id'] else None
    problems, warnings = [], []
    if not plan or plan['status'] != 'ACTIVE':
        warnings.append('No active collection plan for this symbol on this account, so the live price is not checked.')
    if last is None:
        (problems if plan and plan['status'] == 'ACTIVE' else warnings).append('No live price has been received for this symbol yet.')
        return {'price': None, 'problems': problems, 'warnings': warnings}
    stale = plan['stale_seconds'] if plan else 10
    if float(last['age']) > stale:
        problems.append(f"The live price is {float(last['age']):.1f} s old (limit {stale} s).")
    point = 10 ** -(digits['d'] if digits and digits['d'] is not None else 5)
    spread = (last['ask'] - last['bid']) / point if last['ask'] and last['bid'] else None
    if plan and plan['max_spread_points'] and spread is not None and spread > float(plan['max_spread_points']):
        problems.append(f"The spread is {spread:.1f} points (limit {float(plan['max_spread_points']):g}).")
    if plan and plan['status'] == 'ACTIVE' and (not collector or collector['state'] != 'CONNECTED'):
        problems.append(f"The market-data feed is {collector['state'] if collector else 'not running'}.")
    if feed and plan and feed['active_plan_id'] != plan['id']:
        warnings.append(f"This instrument is currently fed by {feed['code']}, not this plan.")
    return {'price': {'bid': last['bid'], 'ask': last['ask'], 'spread_points': round(spread, 1) if spread is not None else None,
                      'age_seconds': round(float(last['age']), 2), 'received_at': last['received_at'].isoformat()},
            'problems': problems, 'warnings': warnings}
