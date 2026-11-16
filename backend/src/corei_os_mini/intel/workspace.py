"""Market-intelligence workspace (Stage 2): watchlists, condition rules and the economic calendar.

Why it exists: operators need named groups of instruments (across brokers and accounts), conditions that tell them
when something happens on a feature ("RSI 14 on H1 crosses below 30", "regime becomes TREND_UP", "ATR% rank above
90"), and the scheduled events that move markets, so a screen can flag "event risk" before a release.
Purpose: storage and checks for those three, every change audited in the same transaction, plus rule evaluation
called by the engine after each closed bar.
Responsibilities: watchlist CRUD; rule CRUD, evaluate() with cooldown and one fire per bar, alert queueing;
calendar add/delete/CSV import, upcoming events and the per-instrument event-risk check.
Must never: place or change orders, or fire twice for the same bar.
Dependencies: config_store (database, audit), alerts (queueing), intel.analytics (currencies of a symbol).
Consumers: intel_api, intel.engine.
Architectural position: Stage 2 workspace; Stage 3 signals will read the same rules model.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator, model_validator

from .. import config_store as store
from .analytics import currencies_of

FIELDS = ('close', 'ema20', 'ema50', 'ema200', 'rsi14', 'atr14', 'atr_pct', 'atr_pct_rank100', 'bb_width_pct', 'adx14', 'plus_di', 'minus_di',
          'er20', 'rv20_pct', 'range_pos20', 'ret_1_pct', 'ret_20_pct', 'macd', 'macd_signal', 'macd_hist', 'stoch_k', 'stoch_d', 'zscore20',
          'regime', 'volatility')


# ---------- watchlists ----------

class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    items: list[dict] = Field(default_factory=list, max_length=2000)
    notes: str = Field(default='', max_length=500)

    @field_validator('items')
    @classmethod
    def _items(cls, v: list[dict]) -> list[dict]:
        out, seen = [], set()
        for x in v:
            key = (str(x.get('account_id') or ''), str(x.get('broker_symbol') or ''))
            if all(key) and key not in seen:
                seen.add(key)
                out.append({'account_id': key[0], 'broker_symbol': key[1]})
        return out


def watchlists() -> list[dict]:
    with store.transaction('read') as db:
        return [store.clean(r) for r in db.execute('SELECT * FROM intel_watchlists ORDER BY name').fetchall()]


def save_watchlist(actor: str, data: WatchlistIn, wid: UUID | None = None, version: int | None = None) -> dict:
    with store.transaction('write') as db:
        if wid is None:
            wid = uuid4()
            db.execute('INSERT INTO intel_watchlists(id, name, items, notes, created_by, updated_by) VALUES (%s,%s,%s,%s,%s,%s)',
                       (wid, data.name, Jsonb(data.items), data.notes, actor, actor))
            before = None
        else:
            before = db.execute('SELECT * FROM intel_watchlists WHERE id=%s FOR UPDATE', (wid,)).fetchone()
            if not before:
                raise HTTPException(404, 'Watchlist not found.')
            if version is not None and before['version'] != version:
                raise HTTPException(409, 'This watchlist was changed by someone else: reload and try again.')
            db.execute('UPDATE intel_watchlists SET name=%s, items=%s, notes=%s, version=version+1, updated_by=%s, updated_at=now() WHERE id=%s',
                       (data.name, Jsonb(data.items), data.notes, actor, wid))
        after = db.execute('SELECT * FROM intel_watchlists WHERE id=%s', (wid,)).fetchone()
        store.audit(db, actor, 'INTEL_WATCHLIST', 'intel_watchlist', wid, store.clean(before) if before else None, store.clean(after))
        return store.clean(after)


def delete_watchlist(actor: str, wid: UUID) -> None:
    with store.transaction('delete') as db:
        before = db.execute('DELETE FROM intel_watchlists WHERE id=%s RETURNING *', (wid,)).fetchone()
        if not before:
            raise HTTPException(404, 'Watchlist not found.')
        store.audit(db, actor, 'INTEL_WATCHLIST_DELETE', 'intel_watchlist', wid, store.clean(before), None)


# ---------- rules ----------

class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    timeframe: Literal['M1', 'M5', 'M15', 'H1', 'H4', 'D1'] = 'H1'
    scope: dict = Field(default_factory=dict)
    field: str
    op: Literal['>', '<', '>=', '<=', 'CROSSES_ABOVE', 'CROSSES_BELOW', 'BECOMES']
    value: str = Field(min_length=1, max_length=40)
    severity: Literal['INFO', 'WARNING', 'CRITICAL'] = 'INFO'
    cooldown_minutes: int = Field(default=60, ge=0, le=10080)
    notes: str = Field(default='', max_length=500)

    @field_validator('field')
    @classmethod
    def _field(cls, v: str) -> str:
        if v not in FIELDS:
            raise ValueError(f"Unknown feature. One of: {', '.join(FIELDS)}")
        return v

    @field_validator('scope')
    @classmethod
    def _scope(cls, v: dict) -> dict:
        if v.get('watchlist_id'):
            return {'watchlist_id': str(v['watchlist_id'])}
        if v.get('account_id') and v.get('broker_symbol'):
            return {'account_id': str(v['account_id']), 'broker_symbol': str(v['broker_symbol'])}
        return {}

    @model_validator(mode='after')
    def _value(self) -> 'RuleIn':
        categorical = self.field in ('regime', 'volatility')
        if categorical and self.op != 'BECOMES':
            raise ValueError('Regime and volatility rules use "becomes".')
        if not categorical:
            if self.op == 'BECOMES':
                raise ValueError('"Becomes" is for regime and volatility; use a comparison or a cross for numbers.')
            try:
                float(self.value)
            except ValueError:
                raise ValueError('The value must be a number for this feature.') from None
        else:
            self.value = self.value.upper()
        return self


def rules() -> list[dict]:
    with store.transaction('read') as db:
        rows = db.execute("""SELECT r.*, (SELECT count(*) FROM intel_rule_fires f WHERE f.rule_id=r.id) AS fires,
                                    (SELECT max(fired_at) FROM intel_rule_fires f WHERE f.rule_id=r.id) AS last_fired_at
                             FROM intel_rules r ORDER BY r.name""").fetchall()
        return [store.clean(r) for r in rows]


def save_rule(actor: str, data: RuleIn, rid: UUID | None = None, version: int | None = None) -> dict:
    cols = ('name', 'enabled', 'timeframe', 'scope', 'field', 'op', 'value', 'severity', 'cooldown_minutes', 'notes')
    vals = [Jsonb(data.scope) if c == 'scope' else getattr(data, c) for c in cols]
    with store.transaction('write') as db:
        if rid is None:
            rid = uuid4()
            db.execute(f"INSERT INTO intel_rules(id, {', '.join(cols)}, created_by, updated_by) VALUES (%s{', %s' * len(cols)}, %s, %s)",
                       (rid, *vals, actor, actor))
            before = None
        else:
            before = db.execute('SELECT * FROM intel_rules WHERE id=%s FOR UPDATE', (rid,)).fetchone()
            if not before:
                raise HTTPException(404, 'Rule not found.')
            if version is not None and before['version'] != version:
                raise HTTPException(409, 'This rule was changed by someone else: reload and try again.')
            db.execute(f"UPDATE intel_rules SET {', '.join(c + '=%s' for c in cols)}, version=version+1, updated_by=%s, updated_at=now() WHERE id=%s",
                       (*vals, actor, rid))
        after = db.execute('SELECT * FROM intel_rules WHERE id=%s', (rid,)).fetchone()
        store.audit(db, actor, 'INTEL_RULE', 'intel_rule', rid, store.clean(before) if before else None, store.clean(after))
        return store.clean(after)


def delete_rule(actor: str, rid: UUID) -> None:
    with store.transaction('delete') as db:
        before = db.execute('DELETE FROM intel_rules WHERE id=%s RETURNING *', (rid,)).fetchone()
        if not before:
            raise HTTPException(404, 'Rule not found.')
        store.audit(db, actor, 'INTEL_RULE_DELETE', 'intel_rule', rid, store.clean(before), None)


def fires(limit: int = 100) -> list[dict]:
    with store.transaction('read') as db:
        return [store.clean(r) for r in db.execute("""SELECT f.*, r.name AS rule_name, r.severity, a.code AS account_code FROM intel_rule_fires f
                                                      JOIN intel_rules r ON r.id=f.rule_id JOIN cfg_accounts a ON a.id=f.account_id
                                                      ORDER BY f.fired_at DESC LIMIT %s""", (limit,)).fetchall()]


def _hit(op: str, value: str, now, prev) -> bool:
    if op == 'BECOMES':
        return now == value and prev != value
    if now is None:
        return False
    x = float(value)
    if op == '>':
        return now > x
    if op == '<':
        return now < x
    if op == '>=':
        return now >= x
    if op == '<=':
        return now <= x
    if prev is None:
        return False
    return (prev <= x < now) if op == 'CROSSES_ABOVE' else (prev >= x > now)


def evaluate(db, account_id, symbol: str, timeframe: str, bar_time, now: dict, prev: dict | None) -> int:
    """Called by the engine for a newly closed bar: fire the matching rules once per bar, respecting cooldowns.
    `now` / `prev` hold the features plus 'regime' and 'volatility' of this bar and the one before."""
    active = db.execute('SELECT * FROM intel_rules WHERE enabled AND timeframe=%s', (timeframe,)).fetchall()
    if not active:
        return 0
    from .. import alerts
    fired = 0
    for r in active:
        scope = r['scope'] or {}
        if scope.get('account_id') and (scope['account_id'], scope.get('broker_symbol')) != (str(account_id), symbol):
            continue
        if scope.get('watchlist_id'):
            wl = db.execute('SELECT items FROM intel_watchlists WHERE id=%s', (scope['watchlist_id'],)).fetchone()
            if not wl or not any(i['account_id'] == str(account_id) and i['broker_symbol'] == symbol for i in wl['items']):
                continue
        cur, before = now.get(r['field']), (prev or {}).get(r['field'])
        if not _hit(r['op'], r['value'], cur, before):
            continue
        if r['cooldown_minutes']:
            recent = db.execute('SELECT 1 FROM intel_rule_fires WHERE rule_id=%s AND account_id=%s AND broker_symbol=%s '
                                'AND fired_at > now() - make_interval(mins => %s)', (r['id'], account_id, symbol, r['cooldown_minutes'])).fetchone()
            if recent:
                continue
        shown = cur if isinstance(cur, str) else round(cur, 6)
        message = f"{symbol} {timeframe}: {r['field']} {r['op'].lower().replace('_', ' ')} {r['value']} (now {shown})"
        row = db.execute('INSERT INTO intel_rule_fires(rule_id, account_id, broker_symbol, timeframe, bar_time, value, message) '
                         'VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id',
                         (r['id'], account_id, symbol, timeframe, bar_time, str(shown), message)).fetchone()
        if row:
            fired += 1
            alerts.enqueue(db, 'INTEL_RULE', r['severity'], f"Rule {r['name']}: {symbol} {timeframe}", message)
    return fired


# ---------- economic calendar ----------

class EventIn(BaseModel):
    at: datetime
    currency: str = Field(pattern=r'^[A-Z]{3}$|^ALL$')
    impact: Literal['LOW', 'MEDIUM', 'HIGH', 'HOLIDAY'] = 'MEDIUM'
    title: str = Field(min_length=1, max_length=200)
    forecast: str = Field(default='', max_length=40)
    previous: str = Field(default='', max_length=40)
    actual: str = Field(default='', max_length=40)

    @field_validator('at')
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v


def events(start: datetime, end: datetime) -> list[dict]:
    with store.transaction('read') as db:
        return [store.clean(r) for r in db.execute('SELECT * FROM intel_events WHERE at BETWEEN %s AND %s ORDER BY at', (start, end)).fetchall()]


def add_events(actor: str, items: list[EventIn], source: str = 'MANUAL') -> int:
    with store.transaction('write') as db:
        added = 0
        for e in items:
            row = db.execute("""INSERT INTO intel_events(id, at, currency, impact, title, forecast, previous, actual, source, created_by)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                ON CONFLICT (at, currency, title) DO UPDATE SET impact=EXCLUDED.impact, forecast=EXCLUDED.forecast,
                                    previous=EXCLUDED.previous, actual=EXCLUDED.actual RETURNING id""",
                             (uuid4(), e.at, e.currency, e.impact, e.title, e.forecast, e.previous, e.actual, source, actor)).fetchone()
            added += 1 if row else 0
        store.audit(db, actor, 'INTEL_EVENTS', 'intel_events', source.lower(), None, {'count': added, 'source': source})
        return added


def delete_event(actor: str, eid: UUID) -> None:
    with store.transaction('delete') as db:
        before = db.execute('DELETE FROM intel_events WHERE id=%s RETURNING *', (eid,)).fetchone()
        if not before:
            raise HTTPException(404, 'Event not found.')
        store.audit(db, actor, 'INTEL_EVENT_DELETE', 'intel_event', eid, store.clean(before), None)


def parse_csv(text: str) -> list[EventIn]:
    """CSV with a header: datetime_utc,currency,impact,title[,forecast,previous,actual]. Dates as 2026-11-20 13:30
    or ISO 8601 (UTC). Rows with errors are reported, not skipped silently."""
    reader = csv.DictReader(io.StringIO(text.strip()))
    need = {'datetime_utc', 'currency', 'impact', 'title'}
    if not reader.fieldnames or not need <= {f.strip().lower() for f in reader.fieldnames}:
        raise HTTPException(422, 'The first line must be: datetime_utc,currency,impact,title[,forecast,previous,actual]')
    out, errors = [], []
    for n, row in enumerate(reader, 2):
        row = {(k or '').strip().lower(): (v or '').strip() for k, v in row.items()}
        try:
            out.append(EventIn(at=datetime.fromisoformat(row['datetime_utc'].replace(' ', 'T')), currency=row['currency'].upper(),
                               impact=row['impact'].upper(), title=row['title'], forecast=row.get('forecast', ''),
                               previous=row.get('previous', ''), actual=row.get('actual', '')))
        except Exception as exc:  # noqa: BLE001 - every bad line is listed
            errors.append(f'line {n}: {exc}'[:200])
    if errors:
        raise HTTPException(422, 'Nothing was imported. ' + '; '.join(errors[:10]))
    if len(out) > 5000:
        raise HTTPException(413, 'At most 5000 events per file.')
    return out


def event_risk(symbol: str, base: str | None, quote: str | None, at: datetime, window_minutes: int, upcoming: list[dict]) -> list[dict]:
    """HIGH-impact events (and holidays) for the instrument's currencies within ±window of `at`."""
    cur = currencies_of(symbol, base, quote)
    if not cur:
        return []
    lo, hi = at - timedelta(minutes=window_minutes), at + timedelta(minutes=window_minutes)
    return [e for e in upcoming if e['impact'] in ('HIGH', 'HOLIDAY') and (e['currency'] in cur or e['currency'] == 'ALL')
            and lo <= (e['at'] if isinstance(e['at'], datetime) else datetime.fromisoformat(e['at'])) <= hi]
