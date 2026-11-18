"""Execution algorithms (Stage 7 · Execution intelligence): a parent order worked over time as child orders.

Why it exists: a large or patient order should not hit the market at once. A desk works it — evenly over time (TWAP),
with the market's usual volume (VWAP), as a share of what trades (POV), or showing only a small clip (ICEBERG) — and
watches it fill against the price when it started.
Purpose: `create` (a parent with its schedule; from a ticket, or from a strategy decision waiting in the execution
queue), `tick` (every parent that is due sends its next child), `pause` / `resume` / `cancel`, `parents`, `detail`.
How a child is sized: each algorithm defines a target — how much should be done by now — and the child is
  target(now) − filled − working, rounded down to the instrument's step; nothing is sent below the smallest size.
    TWAP    target = Q × (slices due by now) ÷ slices, a slice every duration ÷ slices
    VWAP    target = Q × Σ weights of the slices due by now, weight = the instrument's average volume in that slice's
            time of day over the last `lookback_days` (equal weights when there is no history)
    POV     target = participation × market volume since the start (stored M1 bars of the instrument), at most Q;
            at the end time the rest is done only when `finish` is on
    ICEBERG target = filled + visible clip (the next clip goes when the previous one is filled)
  At most one child works at a time. A price cap stops children while the market is beyond it.
Must never: send a child past a refused or UNKNOWN child (the parent pauses and says why; a person resumes it);
bypass risk (every child goes through oms.place and its pre-trade check); send a child twice (its client order ID is
"algo:<parent>:<n>"); use floating point for a quantity or a price.
Dependencies: trading.oms, trading.risk, trading.common, config_store. Consumers: trading_api, trading.runtime and the
execution loop (tick), trading.handoff (a decision executed with an algorithm).
Architectural position: Stage 7, between the decision and the order engine (DEC-94).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .. import config_store as store
from . import oms, risk
from .common import D, deployments, instrument, quote, trading_symbol

ZERO = Decimal(0)


def _n(x: Decimal) -> str:
    """A decimal as people write it (1000, not 1E+3)."""
    return format(x.normalize(), 'f')

ALGOS = {
    'TWAP': {'label': 'TWAP — evenly over time', 'equation': 'childₖ = Q × k ÷ N − done, a slice every T ÷ N',
             'params': {'duration_minutes': 30, 'slices': 10}},
    'VWAP': {'label': 'VWAP — with the usual volume', 'equation': 'target = Q × Σ wᵢ (slices due), wᵢ = average volume at that time of day ÷ Σ',
             'params': {'duration_minutes': 60, 'slices': 12, 'lookback_days': 20}},
    'POV': {'label': 'POV — a share of what trades', 'equation': 'target = min(Q, participation × market volume since start)',
            'params': {'participation_pct': 10, 'duration_minutes': 120, 'interval_seconds': 60, 'finish': True}},
    'ICEBERG': {'label': 'Iceberg — one visible clip at a time', 'equation': 'target = filled + visible clip (next clip when the last fills)',
                'params': {'display_qty': None, 'duration_minutes': 240}},
}


class AlgoIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    algo: Literal['TWAP', 'VWAP', 'POV', 'ICEBERG']
    duration_minutes: int = Field(30, ge=1, le=24 * 60)
    slices: int = Field(10, ge=1, le=500)
    lookback_days: int = Field(20, ge=1, le=120)
    participation_pct: float = Field(10, gt=0, le=50)
    interval_seconds: int = Field(60, ge=5, le=3600)
    finish: bool = True
    display_qty: Decimal | None = Field(None, gt=0, max_digits=38, decimal_places=12)
    price_cap: Decimal | None = Field(None, gt=0, max_digits=38, decimal_places=12)
    child_type: Literal['MARKET', 'LIMIT'] = 'MARKET'


class ParentIn(AlgoIn):
    account_id: UUID
    instrument_id: UUID
    side: Literal['BUY', 'SELL']
    quantity: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    mode: Literal['PAPER', 'BROKER'] = 'PAPER'
    stop_loss: Decimal | None = Field(None, gt=0, max_digits=38, decimal_places=12)
    take_profit: Decimal | None = Field(None, gt=0, max_digits=38, decimal_places=12)
    ticket_key: str = Field(min_length=8, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    reason: str = Field('', max_length=500)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _event(db, pid, kind: str, actor: str, **detail) -> None:
    db.execute('INSERT INTO ex_parent_events(parent_id, kind, detail, actor) VALUES (%s,%s,%s,%s)',
               (str(pid), kind, json.dumps(detail, default=str), actor))


def _params(a: AlgoIn) -> dict:
    p = {'duration_minutes': a.duration_minutes, 'child_type': a.child_type}
    if a.algo in ('TWAP', 'VWAP'):
        p['slices'] = a.slices
    if a.algo == 'VWAP':
        p['lookback_days'] = a.lookback_days
    if a.algo == 'POV':
        p.update(participation_pct=a.participation_pct, interval_seconds=a.interval_seconds, finish=a.finish)
    if a.algo == 'ICEBERG':
        p['display_qty'] = str(a.display_qty) if a.display_qty else None
    return p


def _profile(db, account_id, symbol: str, start: datetime, slices: int, minutes: int, lookback_days: int) -> list[float]:
    """VWAP weights: each slice's share of the average volume traded in that time of day (stored M1 bars)."""
    width = minutes / slices
    rows = db.execute("SELECT bar_time, ticks, volume FROM md_bars WHERE account_id=%s AND broker_symbol=%s AND timeframe='M1' AND complete "
                      'AND bar_time >= %s AND bar_time < %s', (str(account_id), symbol, start - timedelta(days=lookback_days), start)).fetchall()
    if not rows:
        return [1.0 / slices] * slices
    real = any(float(r['volume'] or 0) for r in rows)
    sums = [0.0] * slices
    s0 = start.hour * 60 + start.minute
    for r in rows:
        t = r['bar_time']
        off = (t.hour * 60 + t.minute - s0) % 1440
        if off < minutes:
            sums[min(slices - 1, int(off // width))] += float((r['volume'] if real else r['ticks']) or 0)
    total = sum(sums)
    return [1.0 / slices] * slices if total <= 0 else [x / total for x in sums]


def schedule(p: dict, algo: str) -> list[dict]:
    """The planned slices (TWAP, VWAP) as offsets in minutes and cumulative shares — shown before and while it works."""
    if algo not in ('TWAP', 'VWAP'):
        return []
    n, m = int(p['slices']), float(p['duration_minutes'])
    w = p.get('weights') or [1.0 / n] * n
    out, cum = [], 0.0
    for k in range(n):
        cum += w[k]
        out.append({'slice': k + 1, 'at_minute': round(k * m / n, 2), 'share': round(w[k], 6), 'cumulative': round(min(1.0, cum), 6)})
    return out


def create(db, actor: str, data: AlgoIn, *, account_id, instrument_id, side: str, quantity: Decimal, mode: str, key: str, now: datetime,
           deployment_id=None, intent_id=None, stop_loss=None, take_profit=None, reason: str = '') -> dict:
    ins = instrument(db, instrument_id)
    if not ins:
        raise HTTPException(404, 'Instrument not found.')
    qty, problem = risk.lot(db, instrument_id, quantity)
    if problem:
        raise HTTPException(422, problem)
    existing = db.execute('SELECT * FROM ex_parent_orders WHERE client_key=%s', (key,)).fetchone()
    if existing:
        return {**store.clean(existing), 'duplicate': True}
    if data.algo == 'ICEBERG':
        if not data.display_qty:
            raise HTTPException(422, 'An iceberg needs the visible clip size (display quantity).')
        clip, problem = risk.lot(db, instrument_id, data.display_qty)
        if problem:
            raise HTTPException(422, f'Visible clip: {problem}')
        if clip >= qty:
            raise HTTPException(422, 'The visible clip must be smaller than the whole order.')
    if data.child_type == 'LIMIT' and not data.price_cap:
        raise HTTPException(422, 'Limit children need the price cap: they are sent as limits at the cap.')
    p = _params(data)
    q = quote(db, account_id, instrument_id)
    # Volume (VWAP history, POV participation) is read where the price comes from: the account's own symbol, else the source the quote uses.
    sym = trading_symbol(db, account_id, instrument_id)
    p['volume_from'] = {'account_id': str(account_id), 'symbol': sym} if sym else {'account_id': q and q['account_id'], 'symbol': q and q['broker_symbol']}
    if data.algo == 'VWAP':
        p['weights'] = _profile(db, p['volume_from']['account_id'], p['volume_from']['symbol'] or '', now, data.slices, data.duration_minutes, data.lookback_days)
    pid = uuid4()
    row = db.execute('INSERT INTO ex_parent_orders(id, client_key, algo, account_id, instrument_id, deployment_id, intent_id, side, quantity, arrival_bid, '
                     'arrival_ask, price_cap, mode, params, start_at, end_at, stop_loss, take_profit, created_by, reason) '
                     'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                     (pid, key, data.algo, str(account_id), str(instrument_id), str(deployment_id) if deployment_id else None,
                      str(intent_id) if intent_id else None, side, qty, q and q['bid'], q and q['ask'], data.price_cap, mode, json.dumps(p, default=str),
                      now, now + timedelta(minutes=data.duration_minutes), stop_loss, take_profit, actor, reason)).fetchone()
    _event(db, pid, 'CREATED', actor, algo=data.algo, quantity=str(qty), params=p, arrival={'bid': q and q['bid'], 'ask': q and q['ask']})
    store.audit(db, actor, 'CREATE_ALGO_ORDER', 'algo_order', pid, None, store.clean(row), reason or f'{data.algo} {side} {_n(qty)}')
    return store.clean(row)


def _target(db, p: dict, now: datetime) -> tuple[Decimal, str]:
    """How much should be done by now, and why — the equation with its numbers."""
    Q, prm, algo = D(p['quantity']), p['params'], p['algo']
    start, end = p['start_at'], p['end_at']
    if algo in ('TWAP', 'VWAP'):
        n, m = int(prm['slices']), float(prm['duration_minutes'])
        due = min(n, int(max(0.0, (now - start).total_seconds()) // (m * 60 / n)) + 1)
        share = Decimal(due) / n if algo == 'TWAP' else Decimal(str(round(sum((prm.get('weights') or [1 / n] * n)[:due]), 12)))
        if due >= n:
            share = Decimal(1)
        return Q * share, f'{algo}: {due} of {n} slices due → {_n(share)} × {_n(Q)} = {_n((Q * share))}'
    if algo == 'POV':
        if now >= end and prm.get('finish', True):
            return Q, f'POV: end time reached — the rest is done ({_n(Q)})'
        row = db.execute("SELECT coalesce(sum(CASE WHEN volume > 0 THEN volume ELSE ticks END), 0) AS v FROM md_bars WHERE account_id=%s AND "
                         "broker_symbol=%s AND timeframe='M1' AND bar_time >= %s AND bar_time < %s",
                         ((prm.get('volume_from') or {}).get('account_id') or str(p['account_id']), (prm.get('volume_from') or {}).get('symbol') or p['broker_symbol'],
                          start, now)).fetchone()
        vol = D(row['v']) or ZERO
        pct = Decimal(str(prm['participation_pct'])) / 100
        t = min(Q, pct * vol)
        return t, f'POV: {_n(pct)} × market volume {_n(vol)} since the start = {_n((pct * vol))} (at most {_n(Q)})'
    clip = D(prm['display_qty'])
    t = min(Q, D(p['filled_qty']) + clip)
    return t, f'ICEBERG: filled {_n(D(p["filled_qty"]))} + visible clip {_n(clip)} = {_n(t)}'


def _progress(db, p: dict) -> dict:
    kids = db.execute("SELECT state, quantity, filled_qty, avg_fill_price FROM oms_orders WHERE parent_id=%s", (str(p['id']),)).fetchall()
    filled = sum((D(k['filled_qty']) or ZERO for k in kids), ZERO)
    notional = sum(((D(k['filled_qty']) or ZERO) * (D(k['avg_fill_price']) or ZERO) for k in kids), ZERO)
    working = sum((D(k['quantity']) - (D(k['filled_qty']) or ZERO) for k in kids if k['state'] in oms.OPEN), ZERO)
    blocked = next((k['state'] for k in kids if k['state'] in ('UNKNOWN',)), None)
    return {'filled': filled, 'avg': (notional / filled) if filled else None, 'working': working, 'unknown': blocked,
            'open': any(k['state'] in oms.OPEN for k in kids)}


def _set(db, p: dict, actor: str, **fields) -> dict:
    sets = ', '.join([f'{k}=%s' for k in fields] + ['updated_at=now()', 'version=version+1'])
    return dict(db.execute(f'UPDATE ex_parent_orders SET {sets} WHERE id=%s RETURNING *', (*fields.values(), str(p['id']))).fetchone())


def step(db, p: dict, now: datetime, actor: str = 'algo') -> dict:
    """One look at one parent (its row locked by the caller): update progress, finish, pause, or send the next child."""
    p = {**p, 'broker_symbol': trading_symbol(db, p['account_id'], p['instrument_id']) or ''}
    pr = _progress(db, p)
    Q = D(p['quantity'])
    p = {**p, **_set(db, p, actor, filled_qty=pr['filled'], avg_price=pr['avg'], last_tick_at=now)}
    if pr['filled'] >= Q:
        _event(db, p['id'], 'COMPLETED', actor, filled=str(pr['filled']), avg=str(pr['avg']))
        return _set(db, p, actor, state='COMPLETED', last_note='done')
    if pr['unknown']:
        _event(db, p['id'], 'PAUSED', actor, why='a child order has no answer from the broker')
        return _set(db, p, actor, state='PAUSED', last_note='paused: a child order is UNKNOWN — resolve it (Execution › Unknown), then resume')
    if pr['open']:
        return _set(db, p, actor, last_note=f"waiting for the working child ({_n(pr['working'])} left)")
    last = db.execute('SELECT state, reasons FROM oms_orders WHERE parent_id=%s ORDER BY created_at DESC LIMIT 1', (str(p['id']),)).fetchone()
    if last and last['state'] == 'REJECTED' and int(p['children']) > int(p['params'].get('resume_from', 0)):
        why = '; '.join(json.loads(last['reasons']) if isinstance(last['reasons'], str) else (last['reasons'] or []))
        _event(db, p['id'], 'PAUSED', actor, why=f'child refused: {why}')
        return _set(db, p, actor, state='PAUSED', last_note=f'paused: the last child was refused — {why[:300]}')
    if now >= p['end_at'] and not (p['algo'] == 'POV' and p['params'].get('finish', True)) and p['algo'] in ('POV', 'ICEBERG'):
        _event(db, p['id'], 'EXPIRED', actor, filled=str(pr['filled']))
        return _set(db, p, actor, state='EXPIRED', last_note=f"end time reached with {_n(pr['filled'])} of {_n(Q)} done")
    target, why = _target(db, p, now)
    want, problem = risk.lot(db, p['instrument_id'], max(ZERO, target - pr['filled']))
    if problem or want <= 0:
        return _set(db, p, actor, last_note=f'{why} · nothing to send yet')
    q = quote(db, p['account_id'], p['instrument_id'])
    cap = D(p['price_cap'])
    if cap and q and ((p['side'] == 'BUY' and q['ask'] > cap) or (p['side'] == 'SELL' and q['bid'] < cap)):
        return _set(db, p, actor, last_note=f"{why} · waiting: the price {q['ask'] if p['side'] == 'BUY' else q['bid']} is beyond the cap {cap}")
    n = int(p['children']) + 1
    child_type = p['params'].get('child_type', 'MARKET')
    dep = None
    if p.get('deployment_id'):
        found = deployments(db, None, p['deployment_id'])
        dep = found[0] if found else None
    o = oms.place(db, account_id=p['account_id'], instrument_id=p['instrument_id'], side=p['side'], quantity=want, order_type=child_type,
                  limit_price=cap if child_type == 'LIMIT' else None, time_in_force='IOC' if child_type == 'LIMIT' else 'GTC', mode=p['mode'],
                  deployment=dep, seed=f"algo:{p['id']}:{n}", actor=p['created_by'], source='RUNTIME' if p.get('deployment_id') else 'MANUAL', at=now,
                  stop_loss=p.get('stop_loss'), take_profit=p.get('take_profit'))
    db.execute('UPDATE oms_orders SET parent_id=%s WHERE id=%s', (str(p['id']), str(o['id'])))
    _event(db, p['id'], 'CHILD', actor, n=n, order_id=str(o['id']), quantity=str(want), state=o['state'], why=why)
    p = _set(db, p, actor, children=n, last_note=f"{why} · child {n}: {_n(want)} {o['state'].lower()}")
    if o['state'] in ('FILLED', 'CANCELLED', 'REJECTED'):
        pr = _progress(db, p)
        p = _set(db, p, actor, filled_qty=pr['filled'], avg_price=pr['avg'])
        if pr['filled'] >= Q:
            _event(db, p['id'], 'COMPLETED', actor, filled=str(pr['filled']))
            p = _set(db, p, actor, state='COMPLETED', last_note='done')
        elif o['state'] == 'REJECTED':
            why_r = '; '.join(o.get('reasons') or [])
            _event(db, p['id'], 'PAUSED', actor, why=f'child refused: {why_r}')
            p = _set(db, p, actor, state='PAUSED', last_note=f'paused: child {n} was refused — {why_r[:300]}')
    return p


def tick(now: datetime | None = None) -> dict:
    """Every working parent, each in its own transaction (one failure stays local)."""
    now = now or _now()
    with store.transaction('read') as db:
        ids = [r['id'] for r in db.execute("SELECT id FROM ex_parent_orders WHERE state='WORKING' ORDER BY created_at").fetchall()]
    out = {'parents': len(ids), 'children': 0, 'errors': []}
    broker = False
    for pid in ids:
        try:
            with store.transaction('write') as db:
                p = db.execute("SELECT * FROM ex_parent_orders WHERE id=%s AND state='WORKING' FOR UPDATE SKIP LOCKED", (str(pid),)).fetchone()
                if not p:
                    continue
                before = int(p['children'])
                after = step(db, dict(p), now)
                if int(after['children']) > before:
                    out['children'] += 1
                    broker = broker or after['mode'] == 'BROKER'
        except Exception as exc:  # noqa: BLE001
            out['errors'].append(f'{pid}: {type(exc).__name__}: {str(getattr(exc, "detail", exc))[:200]}')
    if broker:
        from . import execution
        execution.dispatch_soon()
    return out


def ticket(actor: str, data: ParentIn) -> dict:
    with store.transaction('write') as db:
        p = create(db, actor, data, account_id=data.account_id, instrument_id=data.instrument_id, side=data.side, quantity=data.quantity, mode=data.mode,
                   key=f'ticket:{data.ticket_key}', now=_now(), stop_loss=data.stop_loss, take_profit=data.take_profit, reason=data.reason)
        if not p.get('duplicate'):
            locked = db.execute('SELECT * FROM ex_parent_orders WHERE id=%s FOR UPDATE', (str(p['id']),)).fetchone()
            p = _view(step(db, dict(locked), _now()))  # the first child goes at once
    if p.get('mode') == 'BROKER':
        from . import execution
        execution.dispatch_soon()
    return p


def _locked(db, pid) -> dict:
    p = db.execute('SELECT * FROM ex_parent_orders WHERE id=%s FOR UPDATE', (str(pid),)).fetchone()
    if not p:
        raise HTTPException(404, 'Algorithm order not found.')
    return dict(p)


def control(actor: str, pid: UUID, action: Literal['pause', 'resume', 'cancel'], reason: str) -> dict:
    to_cancel: list = []
    with store.transaction('write') as db:
        p = _locked(db, pid)
        before = store.clean(p)
        if action == 'pause':
            if p['state'] != 'WORKING':
                raise HTTPException(409, f"Only a working algorithm can be paused; this one is {p['state'].lower()}.")
            p = _set(db, p, actor, state='PAUSED', last_note=f'paused by {actor}: {reason}')
        elif action == 'resume':
            if p['state'] != 'PAUSED':
                raise HTTPException(409, f"Only a paused algorithm can be resumed; this one is {p['state'].lower()}.")
            if db.execute("SELECT 1 FROM oms_orders WHERE parent_id=%s AND state='UNKNOWN'", (str(pid),)).fetchone():
                raise HTTPException(409, 'A child order is UNKNOWN: resolve it first (Execution › Unknown). It is never sent again.')
            # A refused child stays refused; resuming sends the next child with a fresh risk check.
            p = _set(db, p, actor, state='WORKING', last_note=f'resumed by {actor}: {reason}',
                     params=json.dumps({**p['params'], 'resume_from': int(p['children'])}))
        else:
            if p['state'] in ('COMPLETED', 'CANCELLED', 'EXPIRED'):
                raise HTTPException(409, f"This algorithm is already {p['state'].lower()}.")
            to_cancel = [k['id'] for k in db.execute('SELECT id FROM oms_orders WHERE parent_id=%s AND state = ANY(%s)', (str(pid), list(oms.OPEN))).fetchall()]
            p = _set(db, p, actor, state='CANCELLED', last_note=f'cancelled by {actor}: {reason}')
        _event(db, pid, action.upper(), actor, reason=reason, working_children=[str(x) for x in to_cancel])
        store.audit(db, actor, f'{action.upper()}_ALGO_ORDER', 'algo_order', pid, before, store.clean(p), reason)
    for oid in to_cancel:   # after the parent is cancelled (so no new child goes), each working child is cancelled in its own transaction
        try:
            oms.cancel(actor, oid, f'algorithm cancelled: {reason}')
        except HTTPException:
            pass   # already filled or no longer cancellable: its fill still counts, as the detail shows
    return store.clean(p)


def parents(db, limit: int = 200, active: bool = False) -> list[dict]:
    rows = db.execute("""SELECT p.*, i.symbol, a.code AS account_code FROM ex_parent_orders p JOIN cfg_instruments i ON i.id=p.instrument_id
                         JOIN cfg_accounts a ON a.id=p.account_id""" + (" WHERE p.state IN ('WORKING','PAUSED')" if active else '') +
                      ' ORDER BY p.created_at DESC LIMIT %s', (limit,)).fetchall()
    return [_view(dict(r)) for r in rows]


def _view(p: dict) -> dict:
    Q, f = D(p['quantity']), D(p['filled_qty']) or ZERO
    arr = D(p['arrival_ask']) if p['side'] == 'BUY' else D(p['arrival_bid'])
    avg = D(p['avg_price'])
    slip = None if avg is None or arr is None else ((avg - arr) if p['side'] == 'BUY' else (arr - avg))
    slip_bp = None if slip is None or not arr else float(slip / arr * 10000)
    return {**store.clean(p), 'done_pct': float(f / Q * 100) if Q else 0.0, 'remaining': Q - f, 'arrival': arr, 'slippage': slip,
            'slippage_bp': None if slip_bp is None else round(slip_bp, 2), 'equation': ALGOS[p['algo']]['equation'],
            'plan': schedule(p['params'], p['algo'])}


def detail(db, pid: UUID) -> dict:
    p = db.execute("""SELECT p.*, i.symbol, a.code AS account_code FROM ex_parent_orders p JOIN cfg_instruments i ON i.id=p.instrument_id
                      JOIN cfg_accounts a ON a.id=p.account_id WHERE p.id=%s""", (str(pid),)).fetchone()
    if not p:
        raise HTTPException(404, 'Algorithm order not found.')
    kids = db.execute('SELECT id, client_order_id, state, quantity, filled_qty, avg_fill_price, order_type, limit_price, created_at, reasons FROM oms_orders '
                      'WHERE parent_id=%s ORDER BY created_at', (str(pid),)).fetchall()
    events = db.execute('SELECT * FROM ex_parent_events WHERE parent_id=%s ORDER BY id', (str(pid),)).fetchall()
    return {**_view(dict(p)), 'children_orders': [store.clean(k) for k in kids], 'events': [store.clean(e) for e in events]}
