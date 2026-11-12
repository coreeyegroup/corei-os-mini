"""Collection plans (what to collect, from where, how to keep it), collection switches,
backfill jobs and the size estimate shown before a plan starts.

Rules:
  * One open plan per account × broker symbol.
  * A TRADING_FEED plan names its instrument; PRIMARY/BACKUP plans of one instrument
    form its feed set (the collector switches between them when quality rules say so).
  * Activating a LIVE trading feed is a four-eyes change request while both policies
    four_eyes_live_changes and md_four_eyes_trading_feeds are on.
  * The number of open plans per account is capped (policy md_max_symbols_per_account).
  * Every change is audited.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .. import config_store as store
from .bars import FRAMES

Code = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r'^[A-Z0-9][A-Z0-9_.-]{1,63}$')]
Symbol = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
Frame = Literal['S1', 'M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1']
COLUMNS = ('code', 'account_id', 'broker_symbol', 'instrument_id', 'purpose', 'feed_role', 'data_types', 'bar_timeframes', 'depth_levels',
           'schedule', 'window_start', 'window_end', 'window_days', 'conflate_ms', 'hot_days', 'bars_hot_days', 'archive',
           'backfill_from', 'backfill_per_minute', 'max_latency_ms', 'max_gap_seconds', 'max_spread_points', 'stale_seconds', 'on_breach')


class PlanIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: Code
    account_id: UUID
    broker_symbol: Symbol
    instrument_id: UUID | None = None
    purpose: Literal['TRADING_FEED', 'REFERENCE', 'RESEARCH', 'MONITORING'] = 'TRADING_FEED'
    feed_role: Literal['PRIMARY', 'BACKUP', 'REFERENCE'] = 'PRIMARY'
    data_types: Annotated[list[Literal['QUOTES', 'DEPTH', 'BARS']], Field(min_length=1)] = ['QUOTES', 'BARS']
    bar_timeframes: list[Frame] = ['M1']
    depth_levels: Annotated[int, Field(ge=0, le=50)] = 0
    schedule: Literal['ALWAYS', 'SESSIONS', 'WHILE_DEPLOYED', 'WINDOW'] = 'ALWAYS'
    window_start: time | None = None
    window_end: time | None = None
    window_days: list[Annotated[int, Field(ge=1, le=7)]] = [1, 2, 3, 4, 5]
    conflate_ms: Annotated[int, Field(ge=0, le=60000)] = 0
    hot_days: Annotated[int, Field(ge=1, le=365)] = 14
    bars_hot_days: Annotated[int, Field(ge=1, le=3650)] = 365
    archive: bool = True
    backfill_from: date | None = None
    backfill_per_minute: Annotated[int, Field(ge=1, le=600)] = 30
    max_latency_ms: Annotated[int, Field(ge=1, le=600000)] = 1500
    max_gap_seconds: Annotated[int, Field(ge=1, le=86400)] = 60
    max_spread_points: Annotated[float, Field(gt=0)] | None = None
    stale_seconds: Annotated[int, Field(ge=1, le=86400)] = 10
    on_breach: Literal['ALERT', 'SWITCH'] = 'ALERT'

    @model_validator(mode='after')
    def _rules(self):
        self.data_types = list(dict.fromkeys(self.data_types))
        self.bar_timeframes = sorted(set(self.bar_timeframes), key=lambda f: FRAMES[f])
        self.window_days = sorted(set(self.window_days))
        if 'BARS' in self.data_types and not self.bar_timeframes:
            raise ValueError('Choose at least one bar timeframe, or untick bars.')
        if 'DEPTH' in self.data_types and not self.depth_levels:
            raise ValueError('Choose how many order-book levels to keep.')
        if self.schedule == 'WINDOW' and (self.window_start is None or self.window_end is None):
            raise ValueError('A time window needs a start and an end (UTC).')
        if self.purpose == 'TRADING_FEED' and self.feed_role != 'REFERENCE' and self.instrument_id is None:
            raise ValueError('A trading feed must name its instrument (Setup › Instruments). To just collect this symbol, set purpose to Research.')
        if self.on_breach == 'SWITCH' and self.feed_role != 'PRIMARY':
            raise ValueError('Only a primary feed switches to its backup.')
        return self


class StatusIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['DRAFT', 'ACTIVE', 'PAUSED', 'ARCHIVED']
    version: int
    reason: Reason


class ControlIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    paused: bool
    version: int
    reason: Reason


class BackfillIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    plan_id: UUID
    timeframe: Literal['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'] = 'M1'
    range_from: datetime
    range_to: datetime
    reason: Reason

    @model_validator(mode='after')
    def _range(self):
        for k in ('range_from', 'range_to'):
            if getattr(self, k).tzinfo is None:
                setattr(self, k, getattr(self, k).replace(tzinfo=timezone.utc))
        if self.range_to <= self.range_from:
            raise ValueError('The end must be after the start.')
        if self.range_to > datetime.now(timezone.utc) + timedelta(minutes=1):
            raise ValueError('History cannot be fetched for the future.')
        return self


SELECT = '''SELECT p.*, a.code AS account_code, a.name AS account_name, a.external_account_id, c.code AS connection_code,
        c.environment, c.platform, i.symbol AS instrument_symbol,
        l.bid, l.ask, l.received_at AS last_price_at,
        (SELECT q.score FROM md_quality q WHERE q.account_id=p.account_id AND q.broker_symbol=p.broker_symbol AND q.ticks > 0 ORDER BY q.minute DESC LIMIT 1) AS score,
        s.last_error AS collector_error,
        s.state AS collector_state, s.heartbeat_at AS collector_heartbeat_at,
        f.active_plan_id = p.id AS active_feed,
        (SELECT count(*) FROM cfg_change_requests r WHERE r.entity='md_plans' AND r.entity_id=p.id AND r.status='PENDING') AS pending_changes
    FROM md_collection_plans p JOIN cfg_accounts a ON a.id=p.account_id JOIN cfg_connections c ON c.id=a.connection_id
    LEFT JOIN cfg_instruments i ON i.id=p.instrument_id
    LEFT JOIN md_latest l ON l.account_id=p.account_id AND l.broker_symbol=p.broker_symbol
    LEFT JOIN md_collector_status s ON s.account_id=p.account_id
    LEFT JOIN md_feed_state f ON f.instrument_id=p.instrument_id'''


def _row(r: dict) -> dict:
    out = store.clean(r)
    for k in ('window_start', 'window_end'):
        if r.get(k) is not None:
            out[k] = r[k].strftime('%H:%M')
    return out


def list_plans(account_id: UUID | None = None, include_archived: bool = False) -> list[dict]:
    clauses, params = [], []
    if not include_archived:
        clauses.append("p.status <> 'ARCHIVED'")
    if account_id:
        clauses.append('p.account_id=%s')
        params.append(account_id)
    where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
    with store.transaction('read') as db:
        rows = db.execute(SELECT + where + ' ORDER BY i.symbol NULLS LAST, p.feed_role, p.code', params).fetchall()
    return [_row(r) for r in rows]


def get_plan(db, plan_id: UUID) -> dict:
    row = db.execute(SELECT + ' WHERE p.id=%s', (plan_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Collection plan not found.')
    return row


def _check(db, data: PlanIn, plan_id: UUID | None) -> dict:
    account = db.execute('SELECT a.id, a.status, c.environment, c.platform FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id '
                         'WHERE a.id=%s', (data.account_id,)).fetchone()
    if not account:
        raise HTTPException(422, 'The account does not exist.')
    if account['status'] == 'ARCHIVED':
        raise HTTPException(409, 'The account is archived.')
    if data.instrument_id and not db.execute('SELECT 1 FROM cfg_instruments WHERE id=%s', (data.instrument_id,)).fetchone():
        raise HTTPException(422, 'The instrument does not exist.')
    if data.instrument_id and data.feed_role in ('PRIMARY', 'BACKUP') and db.execute(
            "SELECT 1 FROM md_collection_plans WHERE instrument_id=%s AND feed_role=%s AND purpose='TRADING_FEED' AND status <> 'ARCHIVED' "
            'AND id IS DISTINCT FROM %s', (data.instrument_id, data.feed_role, plan_id)).fetchone() and data.purpose == 'TRADING_FEED':
        raise HTTPException(409, f'This instrument already has a {data.feed_role.lower()} trading feed. Make this one the other role, or archive that plan.')
    return account


def _values(data: PlanIn) -> dict:
    return {k: getattr(data, k) for k in COLUMNS}


def create(actor: str, data: PlanIn) -> dict:
    with store.transaction('create') as db:
        _check(db, data, None)
        values = _values(data)
        plan_id = uuid4()
        cols = ', '.join(['id', *values, 'status', 'created_by', 'updated_by'])
        marks = ', '.join(['%s'] * (len(values) + 4))
        db.execute(f'INSERT INTO md_collection_plans ({cols}) VALUES ({marks})', (plan_id, *values.values(), 'DRAFT', actor, actor))
        row = get_plan(db, plan_id)
        store.audit(db, actor, 'CREATE', 'md_plan', plan_id, None, row)
        return _row(row)


def update(actor: str, plan_id: UUID, data: PlanIn, version: int) -> dict:
    with store.transaction('update') as db:
        before = db.execute('SELECT * FROM md_collection_plans WHERE id=%s FOR UPDATE', (plan_id,)).fetchone()
        if not before:
            raise HTTPException(404, 'Collection plan not found.')
        if before['version'] != version:
            raise HTTPException(409, 'This plan was changed by someone else. Reload it first.')
        if before['status'] == 'ARCHIVED':
            raise HTTPException(409, 'Archived plans are read-only.')
        if before['account_id'] != data.account_id or before['broker_symbol'] != data.broker_symbol:
            raise HTTPException(409, 'The account and symbol of a plan cannot change; archive it and create a new one.')
        _check(db, data, plan_id)
        values = _values(data)
        sets = ', '.join(f'{k}=%s' for k in values)
        db.execute(f'UPDATE md_collection_plans SET {sets}, version=version+1, updated_by=%s, updated_at=now() WHERE id=%s',
                   (*values.values(), actor, plan_id))
        after = get_plan(db, plan_id)
        store.audit(db, actor, 'UPDATE', 'md_plan', plan_id, before, after)
        return _row(after)


def set_status(actor: str, plan_id: UUID, data: StatusIn, approved: bool = False) -> dict:
    with store.transaction('update') as db:
        before = db.execute('SELECT p.*, c.environment FROM md_collection_plans p JOIN cfg_accounts a ON a.id=p.account_id '
                            'JOIN cfg_connections c ON c.id=a.connection_id WHERE p.id=%s FOR UPDATE OF p', (plan_id,)).fetchone()
        if not before:
            raise HTTPException(404, 'Collection plan not found.')
        if before['version'] != data.version:
            raise HTTPException(409, 'This plan was changed by someone else. Reload it first.')
        if before['status'] == data.status:
            return _row(get_plan(db, plan_id))
        if data.status == 'ACTIVE':
            cap = int(store.policy(db, 'md_max_symbols_per_account', 300))
            active = db.execute("SELECT count(*) AS n FROM md_collection_plans WHERE account_id=%s AND status='ACTIVE'", (before['account_id'],)).fetchone()['n']
            if active >= cap:
                raise HTTPException(409, f'This account already collects {active} symbols (limit {cap}, policy md_max_symbols_per_account).')
            if (not approved and before['purpose'] == 'TRADING_FEED' and before['environment'] == 'LIVE'
                    and store.four_eyes_on(db) and store.policy(db, 'md_four_eyes_trading_feeds', True)):
                return store.request_change(db, actor, 'md_plans', plan_id, 'ACTIVATE_MD_PLAN',
                                            f"Start LIVE trading feed {before['code']} ({before['broker_symbol']})",
                                            {'version': data.version, 'reason': data.reason}, base_version=data.version, reason=data.reason)
        db.execute('UPDATE md_collection_plans SET status=%s, status_reason=%s, version=version+1, updated_by=%s, updated_at=now() WHERE id=%s',
                   (data.status, data.reason, actor, plan_id))
        if data.status == 'ACTIVE' and before['instrument_id'] and before['feed_role'] == 'PRIMARY' and before['purpose'] == 'TRADING_FEED':
            db.execute('INSERT INTO md_feed_state(instrument_id, active_plan_id, reason) VALUES (%s,%s,%s) '
                       'ON CONFLICT (instrument_id) DO NOTHING', (before['instrument_id'], plan_id, 'Primary feed started'))
        after = get_plan(db, plan_id)
        store.audit(db, actor, 'SET_STATUS', 'md_plan', plan_id, before, after, data.reason)
        return _row(after)


# ---------- size estimate ----------

TICK_BYTES_DB = 120      # row + index, PostgreSQL
TICK_BYTES_FILE = 14     # compressed Parquet
DEFAULT_TICKS_PER_SECOND = 2.0


def _seconds_per_day(data: PlanIn) -> float:
    if data.schedule == 'WINDOW' and data.window_start and data.window_end:
        start = data.window_start.hour * 3600 + data.window_start.minute * 60
        end = data.window_end.hour * 3600 + data.window_end.minute * 60
        span = end - start if end > start else 86400 - start + end
        return span * len(data.window_days) / 7
    if data.schedule == 'SESSIONS':
        return 86400 * 5 / 7
    return 86400.0


def estimate(data: PlanIn) -> dict:
    """Rows and storage per day before a plan starts: from this symbol's measured update
    rate when it has been collected before, otherwise a typical active-market rate."""
    with store.transaction('read') as db:
        measured = db.execute('SELECT avg(ticks) AS per_minute, count(*) AS minutes FROM md_quality '
                              "WHERE account_id=%s AND broker_symbol=%s AND minute > now() - interval '7 days' AND ticks > 0",
                              (data.account_id, data.broker_symbol)).fetchone()
        cap = int(store.policy(db, 'md_max_symbols_per_account', 300))
        active = db.execute("SELECT count(*) AS n FROM md_collection_plans WHERE account_id=%s AND status='ACTIVE'", (data.account_id,)).fetchone()['n']
    was_measured = bool(measured and measured['minutes'] and measured['minutes'] >= 30)
    rate = float(measured['per_minute']) / 60 if was_measured else DEFAULT_TICKS_PER_SECOND
    if data.conflate_ms:
        rate = min(rate, 1000 / data.conflate_ms)
    seconds = _seconds_per_day(data)
    ticks = int(rate * seconds) if 'QUOTES' in data.data_types else 0
    depth = int(rate * seconds * data.depth_levels * 2) if 'DEPTH' in data.data_types else 0
    bars = sum(int(seconds // FRAMES[f]) + 1 for f in data.bar_timeframes) if 'BARS' in data.data_types else 0
    db_bytes = (ticks + depth) * TICK_BYTES_DB * data.hot_days + bars * 100 * data.bars_hot_days
    file_bytes_year = ((ticks + depth) * TICK_BYTES_FILE + bars * 30) * 365 if data.archive else 0
    return {'ticks_per_second': round(rate, 2), 'rate_source': 'MEASURED' if was_measured else 'TYPICAL',
            'rows_per_day': ticks + depth + bars, 'ticks_per_day': ticks, 'depth_rows_per_day': depth, 'bars_per_day': bars,
            'database_gb_steady': round(db_bytes / 1e9, 3), 'archive_gb_per_year': round(file_bytes_year / 1e9, 3),
            'account_active_symbols': active, 'account_symbol_limit': cap,
            'notes': [n for n in (
                'Order-book depth is stored only when the broker publishes it.' if 'DEPTH' in data.data_types else None,
                'Collected only while a deployment uses this instrument on this account.' if data.schedule == 'WHILE_DEPLOYED' else None,
                f'This account is at its limit of {cap} symbols.' if active >= cap else None) if n]}


# ---------- switches ----------

def controls() -> list[dict]:
    with store.transaction('read') as db:
        rows = db.execute('SELECT m.*, a.code AS account_code FROM md_controls m '
                          "LEFT JOIN cfg_accounts a ON m.scope = 'account:' || a.id::text ORDER BY m.scope='ALL' DESC, a.code").fetchall()
    return [store.clean(r) for r in rows]


def set_control(actor: str, scope: str, data: ControlIn) -> dict:
    if scope != 'ALL':
        try:
            account_id = UUID(scope.removeprefix('account:'))
        except ValueError:
            raise HTTPException(422, 'Scope is ALL or account:<id>.') from None
        scope = f'account:{account_id}'
    with store.transaction('update') as db:
        if scope != 'ALL' and not db.execute('SELECT 1 FROM cfg_accounts WHERE id=%s', (account_id,)).fetchone():
            raise HTTPException(404, 'Account not found.')
        before = db.execute('SELECT * FROM md_controls WHERE scope=%s FOR UPDATE', (scope,)).fetchone()
        if before and before['version'] != data.version:
            raise HTTPException(409, 'This switch was changed by someone else. Reload it first.')
        if not before and data.version != 0:
            raise HTTPException(409, 'This switch was changed by someone else. Reload it first.')
        after = db.execute('INSERT INTO md_controls(scope, paused, reason, updated_by) VALUES (%s,%s,%s,%s) '
                           'ON CONFLICT (scope) DO UPDATE SET paused=EXCLUDED.paused, reason=EXCLUDED.reason, version=md_controls.version+1, '
                           'updated_by=EXCLUDED.updated_by, updated_at=now() RETURNING *', (scope, data.paused, data.reason, actor)).fetchone()
        store.audit(db, actor, 'MD_PAUSE' if data.paused else 'MD_RESUME', 'md_control', scope, before, after, data.reason)
        if data.paused:
            from ..alerts import enqueue
            enqueue(db, 'MARKET_DATA', 'WARNING', 'Market data collection paused' + (' for everything' if scope == 'ALL' else ''),
                    f'{scope} paused by {actor}. Reason: {data.reason}')
        return store.clean(after)


# ---------- backfill jobs ----------

def create_job(actor: str, data: BackfillIn) -> dict:
    with store.transaction('create') as db:
        plan = db.execute('SELECT id, status, data_types FROM md_collection_plans WHERE id=%s', (data.plan_id,)).fetchone()
        if not plan:
            raise HTTPException(404, 'Collection plan not found.')
        if plan['status'] == 'ARCHIVED':
            raise HTTPException(409, 'The plan is archived.')
        if db.execute("SELECT 1 FROM md_jobs WHERE plan_id=%s AND timeframe=%s AND status IN ('QUEUED','RUNNING')", (data.plan_id, data.timeframe)).fetchone():
            raise HTTPException(409, 'A backfill for this plan and timeframe is already queued or running.')
        row = db.execute('INSERT INTO md_jobs(id, plan_id, timeframe, range_from, range_to, reason, requested_by) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                         (uuid4(), data.plan_id, data.timeframe, data.range_from, data.range_to, data.reason, actor)).fetchone()
        store.audit(db, actor, 'MD_BACKFILL', 'md_plan', data.plan_id, None, row, data.reason)
        return store.clean(row)


def cancel_job(actor: str, job_id: UUID) -> dict:
    with store.transaction('update') as db:
        row = db.execute("UPDATE md_jobs SET status='CANCELLED', updated_at=now() WHERE id=%s AND status IN ('QUEUED','RUNNING') RETURNING *",
                         (job_id,)).fetchone()
        if not row:
            raise HTTPException(409, 'Only a queued or running job can be cancelled.')
        store.audit(db, actor, 'MD_BACKFILL_CANCEL', 'md_plan', row['plan_id'], None, row)
        return store.clean(row)


def jobs(limit: int = 100) -> list[dict]:
    with store.transaction('read') as db:
        rows = db.execute('SELECT j.*, p.code AS plan_code, p.broker_symbol FROM md_jobs j JOIN md_collection_plans p ON p.id=j.plan_id '
                          'ORDER BY j.created_at DESC LIMIT %s', (limit,)).fetchall()
    return [store.clean(r) for r in rows]
