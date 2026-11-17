"""Order engine (Stage 7 · Execution intelligence): the single owner of order state.

Why it exists: an order must never be sent twice, never be lost, and always say what happened to it. One module
owns the state machine; everything else asks it.
Purpose: place an order (idempotent client order ID, risk check, adapter), move it through its states with an
append-only event per change, record fills and hand them to the portfolio in the same transaction, cancel, expire,
match working paper limit orders against live prices, and resolve an order whose outcome is UNKNOWN.
States: NEW → APPROVED | REJECTED; APPROVED → SUBMITTED → ACKNOWLEDGED → PARTIALLY_FILLED → FILLED;
ACKNOWLEDGED/PARTIALLY_FILLED → CANCEL_PENDING → CANCELLED; → EXPIRED; SUBMITTED/ACKNOWLEDGED/CANCEL_PENDING →
UNKNOWN (no answer from the broker) → resolved by a query or an operator, never by sending again.
Adapters: PAPER (fills at the live bid/ask plus slippage in ticks; limit orders when the price trades through).
BROKER orders are refused by risk until the MT5 order adapter exists and order sending is on.
Must never: bypass risk, re-send an order whose outcome is unknown, change an event, or use floating point for a
price or quantity.
Dependencies: trading.common, trading.risk, trading.portfolio, config_store. Consumers: trading.signals (intents),
trading.runtime (matching, expiry), trading_api (tickets, cancel, resolve).
Architectural position: Stage 7, after risk.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .. import config_store as store
from ..md import markets
from . import portfolio, risk
from .common import D, instrument, quantize, quote, settings, trading_symbol

OPEN = ('APPROVED', 'SUBMITTED', 'ACKNOWLEDGED', 'PARTIALLY_FILLED', 'CANCEL_PENDING', 'UNKNOWN')
TRANSITIONS = {
    'NEW': {'APPROVED', 'REJECTED'},
    'APPROVED': {'SUBMITTED', 'REJECTED'},
    'SUBMITTED': {'ACKNOWLEDGED', 'REJECTED', 'FILLED', 'PARTIALLY_FILLED', 'UNKNOWN', 'CANCELLED', 'EXPIRED'},
    'ACKNOWLEDGED': {'PARTIALLY_FILLED', 'FILLED', 'CANCEL_PENDING', 'EXPIRED', 'UNKNOWN', 'CANCELLED'},
    'PARTIALLY_FILLED': {'PARTIALLY_FILLED', 'FILLED', 'CANCEL_PENDING', 'EXPIRED', 'UNKNOWN'},
    'CANCEL_PENDING': {'CANCELLED', 'FILLED', 'PARTIALLY_FILLED', 'UNKNOWN'},
    'UNKNOWN': {'FILLED', 'PARTIALLY_FILLED', 'CANCELLED', 'REJECTED', 'ACKNOWLEDGED'},
}


def client_order_id(seed: str) -> str:
    """Stable ID from what the order is for (an intent, or a ticket's own key): the same seed is the same order."""
    return 'C' + hashlib.sha256(seed.encode()).hexdigest()[:31].upper()


def _move(db, order: dict, to: str, actor: str, detail: dict | None = None, **fields) -> dict:
    if to not in TRANSITIONS.get(order['state'], set()):
        raise HTTPException(409, f"An order in {order['state']} cannot become {to}.")
    sets = ', '.join([f'{k}=%s' for k in fields] + ['state=%s', 'version=version+1', 'updated_at=now()'])
    row = db.execute(f'UPDATE oms_orders SET {sets} WHERE id=%s RETURNING *', (*fields.values(), to, str(order['id']))).fetchone()
    db.execute('INSERT INTO oms_order_events(order_id, state_from, state_to, detail, actor) VALUES (%s,%s,%s,%s,%s)',
               (str(order['id']), order['state'], to, json.dumps(detail or {}, default=str), actor))
    return dict(row)


def _fill(db, order: dict, qty: Decimal, price: Decimal, q: dict | None, actor: str, at: datetime) -> dict:
    ins = instrument(db, order['instrument_id'])
    ref = (q['ask'] if order['side'] == 'BUY' else q['bid']) if q else None
    slip = ((price - ref) if order['side'] == 'BUY' else (ref - price)) if ref is not None else None
    fill = {'id': uuid4(), 'order_id': order['id'], 'deployment_id': order.get('deployment_id'), 'account_id': order['account_id'],
            'instrument_id': order['instrument_id'], 'side': order['side'], 'quantity': qty, 'price': price, 'mode': order['mode'], 'at': at}
    realised = portfolio.apply_fill(db, fill)
    db.execute('INSERT INTO oms_fills(id, order_id, deployment_id, account_id, instrument_id, side, quantity, price, quote_bid, quote_ask, slippage, '
               'realised_pnl, currency, mode, at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
               (fill['id'], str(order['id']), str(order['deployment_id']) if order.get('deployment_id') else None, str(order['account_id']),
                str(order['instrument_id']), order['side'], qty, price, q and q['bid'], q and q['ask'], slip, realised,
                ins.get('quote_currency') or 'USD', order['mode'], at))
    filled = D(order['filled_qty']) + qty
    avg = price if not D(order['filled_qty']) else (D(order['avg_fill_price']) * D(order['filled_qty']) + price * qty) / filled
    to = 'FILLED' if filled >= D(order['quantity']) else 'PARTIALLY_FILLED'
    return _move(db, order, to, actor, {'fill_qty': str(qty), 'price': str(price), 'slippage': str(slip) if slip is not None else None,
                                        'realised_pnl': str(realised)}, filled_qty=filled, avg_fill_price=quantize(avg, D(ins.get('tick_size')) / 1000))


def _paper_price(db, order: dict, q: dict) -> Decimal:
    ins = instrument(db, order['instrument_id'])
    tick = D(ins.get('tick_size')) or Decimal('0.00001')
    slip = tick * int(settings(db).get('paper_slippage_ticks') or 0)
    return (q['ask'] + slip) if order['side'] == 'BUY' else (q['bid'] - slip)


def _expiry(db, ins: dict, tif: str, at: datetime, q: dict | None) -> datetime | None:
    if tif != 'DAY':
        return None
    market = markets.market_for(ins.get('market'), q['broker_symbol'] if q else '', '', ins.get('asset_class') or '')
    nxt = markets.next_change(market, at) if market else None
    if nxt and nxt['event'] == 'closes':
        return datetime.fromisoformat(nxt['at'])
    return at + timedelta(hours=24)


def place(db, *, account_id, instrument_id, side: str, quantity: Decimal, order_type: str = 'MARKET', limit_price: Decimal | None = None,
          time_in_force: str = 'GTC', mode: str = 'PAPER', deployment: dict | None = None, intent_id=None, seed: str, actor: str,
          source: str = 'MANUAL', flatten: bool = False, at: datetime | None = None, data_source: tuple | None = None) -> dict:
    """Create the order, run the risk check, and (paper) execute — all in the caller's transaction."""
    at = at or datetime.now(timezone.utc)
    cid = client_order_id(seed)
    existing = db.execute('SELECT * FROM oms_orders WHERE client_order_id=%s', (cid,)).fetchone()
    if existing:
        return {**store.clean(existing), 'duplicate': True}  # idempotent: never a second order for the same intent or ticket
    ins = instrument(db, instrument_id)
    if not ins:
        raise HTTPException(404, 'Instrument not found.')
    q = quote(db, account_id, instrument_id, data_source)
    tick = D(ins.get('tick_size')) or Decimal('0.00001')
    order = dict(db.execute("""INSERT INTO oms_orders(id, client_order_id, intent_id, deployment_id, account_id, instrument_id, broker_symbol, side, order_type,
                               time_in_force, quantity, limit_price, mode, reduce_only, state, decision_bid, decision_ask, expires_at, source, created_by)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'NEW',%s,%s,%s,%s,%s) RETURNING *""",
                            (uuid4(), cid, str(intent_id) if intent_id else None, str(deployment['id']) if deployment else None, str(account_id),
                             str(instrument_id), trading_symbol(db, account_id, instrument_id), side, order_type, time_in_force, quantity,
                             quantize(limit_price, tick) if limit_price else None, mode, flatten, q and q['bid'], q and q['ask'],
                             _expiry(db, ins, time_in_force, at, q), source, actor)).fetchone())
    db.execute("INSERT INTO oms_order_events(order_id, state_from, state_to, detail, actor) VALUES (%s,NULL,'NEW',%s,%s)",
               (str(order['id']), json.dumps({'quote': {'bid': str(q['bid']), 'ask': str(q['ask']), 'source': q['source']} if q else None}), actor))
    verdict = risk.check(db, order, deployment, at, q, flatten=flatten)
    if not verdict['approved']:
        order = _move(db, order, 'REJECTED', 'risk', {'reasons': verdict['reasons']}, reasons=json.dumps(verdict['reasons']))
        _alert(db, 'WARNING', f"Order refused: {ins.get('symbol')} {side} {quantity.normalize()}", '; '.join(verdict['reasons'])[:1500])
        return store.clean(order)
    order = _move(db, order, 'APPROVED', 'risk', {'inputs': verdict['inputs']})
    if mode != 'PAPER':  # unreachable while risk refuses BROKER orders; kept so the path is explicit
        return store.clean(_move(db, order, 'REJECTED', 'execution', {'reasons': ['BROKER_ORDER_ADAPTER_NOT_BUILT']}))
    order = _move(db, order, 'SUBMITTED', 'paper', {'adapter': 'PAPER'})
    order = _move(db, order, 'ACKNOWLEDGED', 'paper', {})
    if order_type == 'MARKET':
        order = _fill(db, order, quantity, _paper_price(db, order, q), q, 'paper', at)
    else:
        crossed = (q['ask'] <= D(order['limit_price'])) if side == 'BUY' else (q['bid'] >= D(order['limit_price']))
        if crossed:
            order = _fill(db, order, quantity, q['ask'] if side == 'BUY' else q['bid'], q, 'paper', at)
        elif time_in_force == 'IOC':
            order = _move(db, order, 'CANCELLED', 'paper', {'reason': 'IOC: not marketable on arrival'})
    return store.clean(order)


def _alert(db, severity: str, title: str, body: str) -> None:
    try:
        from .. import alerts
        alerts.enqueue(db, 'TRADING', severity, title, body)
    except Exception:  # noqa: BLE001 - never stop an order path for an alert
        pass


def match(db, now: datetime) -> dict:
    """Working paper limit orders: fill when the live price trades through; expire DAY orders at the market close."""
    filled = expired = 0
    for o in db.execute("SELECT * FROM oms_orders WHERE mode='PAPER' AND state IN ('ACKNOWLEDGED','PARTIALLY_FILLED') FOR UPDATE SKIP LOCKED").fetchall():
        o = dict(o)
        if o['expires_at'] and o['expires_at'] <= now:
            _move(db, o, 'EXPIRED', 'paper', {'reason': 'time in force ended'})
            expired += 1
            continue
        if o['order_type'] != 'LIMIT':
            continue
        q = quote(db, o['account_id'], o['instrument_id'], None)
        if not q:
            continue
        limit = D(o['limit_price'])
        if (o['side'] == 'BUY' and q['ask'] <= limit) or (o['side'] == 'SELL' and q['bid'] >= limit):
            _fill(db, o, D(o['quantity']) - D(o['filled_qty']), q['ask'] if o['side'] == 'BUY' else q['bid'], q, 'paper', now)
            filled += 1
    return {'filled': filled, 'expired': expired}


def cancel(actor: str, order_id: UUID, reason: str) -> dict:
    with store.transaction('write') as db:
        o = db.execute('SELECT * FROM oms_orders WHERE id=%s FOR UPDATE', (order_id,)).fetchone()
        if not o:
            raise HTTPException(404, 'Order not found.')
        o = dict(o)
        if o['state'] not in ('ACKNOWLEDGED', 'PARTIALLY_FILLED', 'SUBMITTED'):
            raise HTTPException(409, f"A {o['state'].lower().replace('_', ' ')} order cannot be cancelled.")
        if o['state'] == 'SUBMITTED':
            return store.clean(_move(db, o, 'CANCELLED', actor, {'reason': reason}))
        o = _move(db, o, 'CANCEL_PENDING', actor, {'reason': reason})
        if o['mode'] == 'PAPER':
            o = _move(db, o, 'CANCELLED', 'paper', {'reason': reason})
        return store.clean(o)


class ResolveIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    outcome: Literal['FILLED', 'NOT_FILLED']
    price: Decimal | None = Field(default=None, gt=0, max_digits=38, decimal_places=12)
    quantity: Decimal | None = Field(default=None, gt=0, max_digits=38, decimal_places=12)
    reason: str = Field(min_length=5, max_length=500)


def resolve(actor: str, order_id: UUID, data: ResolveIn) -> dict:
    """An order in UNKNOWN: an operator states what the broker did (after checking the broker), audited. Never re-sent."""
    with store.transaction('write') as db:
        o = db.execute('SELECT * FROM oms_orders WHERE id=%s FOR UPDATE', (order_id,)).fetchone()
        if not o:
            raise HTTPException(404, 'Order not found.')
        o = dict(o)
        if o['state'] != 'UNKNOWN':
            raise HTTPException(409, 'Only an order whose outcome is unknown is resolved this way.')
        store.audit(db, actor, 'RESOLVE_UNKNOWN_ORDER', 'order', order_id, store.clean(o), data.model_dump(mode='json'), data.reason)
        if data.outcome == 'NOT_FILLED':
            return store.clean(_move(db, o, 'CANCELLED', actor, {'resolved': 'not filled', 'reason': data.reason}))
        if not data.price:
            raise HTTPException(422, 'A filled outcome needs the fill price the broker reports.')
        qty = data.quantity or (D(o['quantity']) - D(o['filled_qty']))
        return store.clean(_fill(db, o, qty, data.price, None, actor, datetime.now(timezone.utc)))


class TicketIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_id: UUID
    instrument_id: UUID
    side: Literal['BUY', 'SELL']
    quantity: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    order_type: Literal['MARKET', 'LIMIT'] = 'MARKET'
    limit_price: Decimal | None = Field(default=None, gt=0, max_digits=38, decimal_places=12)
    time_in_force: Literal['GTC', 'DAY', 'IOC'] = 'GTC'
    ticket_key: str = Field(min_length=8, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')  # the screen's own key: a double click is one order
    reason: str = Field(default='', max_length=500)


def ticket(actor: str, data: TicketIn) -> dict:
    with store.transaction('write') as db:
        out = place(db, account_id=data.account_id, instrument_id=data.instrument_id, side=data.side, quantity=data.quantity, order_type=data.order_type,
                    limit_price=data.limit_price, time_in_force=data.time_in_force, mode='PAPER', seed=f'ticket:{data.ticket_key}', actor=actor)
        if not out.get('duplicate'):
            store.audit(db, actor, 'PLACE_ORDER', 'order', out['id'], None, out, data.reason)
        return out


def flatten(actor: str, account_id: UUID, instrument_id: UUID, reason: str, key: str) -> dict:
    """Close every open position of the account in one instrument (each deployment's and the manual one) with market
    orders attributed to their owner, so each book closes; allowed through a kill switch (it removes risk)."""
    with store.transaction('write') as db:
        rows = db.execute('SELECT p.*, d.strategy_id FROM pf_positions p LEFT JOIN cfg_deployments d ON d.id=p.deployment_id '
                          'WHERE p.account_id=%s AND p.instrument_id=%s AND p.quantity <> 0 ORDER BY p.deployment_id NULLS FIRST',
                          (str(account_id), str(instrument_id))).fetchall()
        if not rows:
            raise HTTPException(409, 'There is no open position to close.')
        out = []
        for r in rows:
            qty = D(r['quantity'])
            dep = {'id': r['deployment_id'], 'strategy_id': r['strategy_id']} if r['deployment_id'] else None
            o = place(db, account_id=account_id, instrument_id=instrument_id, side='SELL' if qty > 0 else 'BUY', quantity=abs(qty), mode=r['mode'],
                      deployment=dep, seed=f"flatten:{key}:{r['id']}", actor=actor, source='FLATTEN', flatten=True)
            store.audit(db, actor, 'FLATTEN_POSITION', 'order', o['id'], None, o, reason)
            out.append(o)
        return {'orders': out, 'state': 'FILLED' if all(o['state'] == 'FILLED' for o in out) else 'PARTLY'}


def orders(db, state: str | None = None, account_id=None, limit: int = 300, open_only: bool = False) -> list[dict]:
    where, args = ['TRUE'], []
    if state:
        where.append('o.state=%s'); args.append(state)
    if open_only:
        where.append('o.state = ANY(%s)'); args.append(list(OPEN))
    if account_id:
        where.append('o.account_id=%s'); args.append(str(account_id))
    rows = db.execute(f"""SELECT o.*, a.code AS account_code, i.symbol, s.code AS strategy_code FROM oms_orders o
                          JOIN cfg_accounts a ON a.id=o.account_id JOIN cfg_instruments i ON i.id=o.instrument_id
                          LEFT JOIN cfg_deployments d ON d.id=o.deployment_id LEFT JOIN cfg_strategies s ON s.id=d.strategy_id
                          WHERE {' AND '.join(where)} ORDER BY o.created_at DESC LIMIT %s""", [*args, limit]).fetchall()
    return [store.clean(r) for r in rows]


def order_detail(db, order_id: UUID) -> dict:
    o = db.execute('SELECT o.*, a.code AS account_code, i.symbol FROM oms_orders o JOIN cfg_accounts a ON a.id=o.account_id '
                   'JOIN cfg_instruments i ON i.id=o.instrument_id WHERE o.id=%s', (order_id,)).fetchone()
    if not o:
        raise HTTPException(404, 'Order not found.')
    events = db.execute('SELECT * FROM oms_order_events WHERE order_id=%s ORDER BY id', (order_id,)).fetchall()
    fills = db.execute('SELECT * FROM oms_fills WHERE order_id=%s ORDER BY at', (order_id,)).fetchall()
    decision = db.execute('SELECT * FROM risk_decisions WHERE order_id=%s ORDER BY at DESC LIMIT 1', (order_id,)).fetchone()
    return {**store.clean(o), 'events': [store.clean(e) for e in events], 'fills': [store.clean(f) for f in fills], 'risk': store.clean(decision)}
