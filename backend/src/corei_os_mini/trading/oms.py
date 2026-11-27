"""Order engine (Stage 7 · Execution intelligence): the single owner of order state.

Why it exists: an order must never be sent twice, never be lost, and always say what happened to it. One module
owns the state machine; everything else asks it.
Purpose: place an order (idempotent client order ID, risk check, adapter), move it through its states with an
append-only event per change, record fills and hand them to the portfolio in the same transaction, cancel, expire,
match working paper limit orders against live prices, and resolve an order whose outcome is UNKNOWN.
States: NEW → APPROVED | REJECTED; APPROVED → SUBMITTED → ACKNOWLEDGED → PARTIALLY_FILLED → FILLED;
ACKNOWLEDGED/PARTIALLY_FILLED → CANCEL_PENDING → CANCELLED; → EXPIRED; SUBMITTED/ACKNOWLEDGED/CANCEL_PENDING →
UNKNOWN (no answer from the broker) → resolved by a query or an operator, never by sending again.
Adapters: PAPER (fills at the live bid/ask plus slippage in ticks; limit and stop orders when the price trades
through). BROKER orders stop at APPROVED here (held for a second person when four-eyes applies) and the dispatcher
(trading.execution) sends them outside this transaction, so SUBMITTED is on record before anything leaves COREI.
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
    'PARTIALLY_FILLED': {'PARTIALLY_FILLED', 'FILLED', 'CANCEL_PENDING', 'EXPIRED', 'UNKNOWN', 'CANCELLED'},
    'CANCEL_PENDING': {'CANCELLED', 'FILLED', 'PARTIALLY_FILLED', 'UNKNOWN', 'ACKNOWLEDGED', 'EXPIRED'},
    'UNKNOWN': {'FILLED', 'PARTIALLY_FILLED', 'CANCELLED', 'REJECTED', 'ACKNOWLEDGED', 'EXPIRED'},
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


def _fill(db, order: dict, qty: Decimal, price: Decimal, q: dict | None, actor: str, at: datetime, deal_id: str | None = None,
          detail: dict | None = None, *, copy_to_followers: bool = True) -> dict:
    """One fill: the position in the same transaction, then the order's state. A broker deal is applied once (its ticket)."""
    ins = instrument(db, order['instrument_id'])
    if deal_id is not None and db.execute('SELECT 1 FROM oms_fills WHERE account_id=%s AND broker_deal_id=%s',
                                          (str(order['account_id']), str(deal_id))).fetchone():
        return order
    arrival = (D(order['decision_ask']) if order['side'] == 'BUY' else D(order['decision_bid'])) if order.get('decision_bid') is not None else None
    ref = (q['ask'] if order['side'] == 'BUY' else q['bid']) if q else arrival
    slip = ((price - ref) if order['side'] == 'BUY' else (ref - price)) if ref is not None else None
    latency = int((at - order['submitted_at']).total_seconds() * 1000) if order.get('submitted_at') else None
    fill = {'id': uuid4(), 'order_id': order['id'], 'deployment_id': order.get('deployment_id'), 'account_id': order['account_id'],
            'instrument_id': order['instrument_id'], 'side': order['side'], 'quantity': qty, 'price': price, 'mode': order['mode'], 'at': at,
            'stop_loss': order.get('stop_loss'), 'take_profit': order.get('take_profit')}
    if order['mode'] == 'PAPER':   # a paper account's costs (commission per lot, % of value, minimum), in quote currency
        from . import paper
        fill['commission'] = paper.commission(db, order['account_id'], ins, qty, price)
    realised = portfolio.apply_fill(db, fill)
    db.execute('INSERT INTO oms_fills(id, order_id, deployment_id, account_id, instrument_id, side, quantity, price, quote_bid, quote_ask, slippage, '
               'realised_pnl, currency, mode, at, broker_deal_id, latency_ms, arrival_price, commission) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
               (fill['id'], str(order['id']), str(order['deployment_id']) if order.get('deployment_id') else None, str(order['account_id']),
                str(order['instrument_id']), order['side'], qty, price, q and q['bid'], q and q['ask'], slip, realised,
                ins.get('quote_currency') or 'USD', order['mode'], at, str(deal_id) if deal_id is not None else None, latency, arrival,
                fill.get('commission') or Decimal(0)))
    filled = D(order['filled_qty']) + qty
    if filled > D(order['quantity']):
        # The broker reports more than the order asked for. The exposure is real, so it is booked (never dropped), and
        # the excess stays an open break with its evidence in this same transaction until a person reviews it.
        excess = filled - D(order['quantity'])
        db.execute("INSERT INTO pt_breaks(id, account_id, kind, key, instrument_id, corei_value, broker_value, difference, detail) "
                   "VALUES (%s,%s,'ORDER_STATE',%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                   (uuid4(), str(order['account_id']), f"overfill:{order['id']}:{deal_id or fill['id']}", str(order['instrument_id']),
                    format(D(order['quantity']).normalize(), 'f'), format(filled.normalize(), 'f'), format(excess.normalize(), 'f'),
                    json.dumps({'note': 'fills above the order quantity', 'order_id': str(order['id']), 'deal': deal_id, 'fill_id': str(fill['id'])})))
        _alert(db, 'CRITICAL', 'Fill above the order quantity', f"Order {order['client_order_id']}: ordered {order['quantity']}, filled {filled}.")
    avg = price if not D(order['filled_qty']) else (D(order['avg_fill_price']) * D(order['filled_qty']) + price * qty) / filled
    to = 'FILLED' if filled >= D(order['quantity']) else 'PARTIALLY_FILLED'
    out = _move(db, order, to, actor, {'fill_qty': str(qty), 'price': str(price), 'slippage': str(slip) if slip is not None else None,
                                       'realised_pnl': str(realised), 'deal': deal_id, **(detail or {})}, filled_qty=filled,
                avg_fill_price=quantize(avg, D(ins.get('tick_size')) / 1000), **({'completed_at': at} if to == 'FILLED' else {}))
    if not copy_to_followers:
        return out
    try:
        from . import copytrade
        copytrade.on_fill(db, out, fill)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - a follower's problem never undoes the master's fill; copytrade alerts itself
        pass
    return out


def _paper_price(db, order: dict, q: dict) -> Decimal:
    ins = instrument(db, order['instrument_id'])
    tick = D(ins.get('tick_size')) or Decimal('0.00001')
    from . import paper
    own = paper.slippage_ticks(db, order['account_id'])
    slip = tick * int(own if own is not None else (settings(db).get('paper_slippage_ticks') or 0))
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
          source: str = 'MANUAL', flatten: bool = False, at: datetime | None = None, data_source: tuple | None = None,
          stop_price: Decimal | None = None, stop_loss: Decimal | None = None, take_profit: Decimal | None = None,
          routing: dict | None = None, copy_of=None) -> dict:
    """Create the order, run the risk check, and (paper) execute — all in the caller's transaction. A BROKER order stops
    at APPROVED: the dispatcher sends it after this transaction commits."""
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
                               time_in_force, quantity, limit_price, mode, reduce_only, state, decision_bid, decision_ask, expires_at, source, created_by,
                               stop_price, stop_loss, take_profit, routing, copy_of, adapter, broker_tag)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'NEW',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                            (uuid4(), cid, str(intent_id) if intent_id else None, str(deployment['id']) if deployment else None, str(account_id),
                             str(instrument_id), trading_symbol(db, account_id, instrument_id), side, order_type, time_in_force, quantity,
                             quantize(limit_price, tick) if limit_price else None, mode, flatten, q and q['bid'], q and q['ask'],
                             _expiry(db, ins, time_in_force, at, q), source, actor, quantize(stop_price, tick) if stop_price else None,
                             quantize(stop_loss, tick) if stop_loss else None, quantize(take_profit, tick) if take_profit else None,
                             json.dumps(routing, default=str) if routing else None, str(copy_of) if copy_of else None,
                             'PAPER' if mode == 'PAPER' else None, None if mode == 'PAPER' else cid[:20])).fetchone())
    db.execute("INSERT INTO oms_order_events(order_id, state_from, state_to, detail, actor) VALUES (%s,NULL,'NEW',%s,%s)",
               (str(order['id']), json.dumps({'quote': {'bid': str(q['bid']), 'ask': str(q['ask']), 'source': q['source']} if q else None}), actor))
    verdict = risk.check(db, order, deployment, at, q, flatten=flatten)
    if not verdict['approved']:
        order = _move(db, order, 'REJECTED', 'risk', {'reasons': verdict['reasons']}, reasons=json.dumps(verdict['reasons']))
        _alert(db, 'WARNING', f"Order refused: {ins.get('symbol')} {side} {quantity.normalize()}", '; '.join(verdict['reasons'])[:1500])
        return store.clean(order)
    if mode != 'PAPER':
        hold = _four_eyes(db, order, source)
        order = _move(db, order, 'APPROVED', 'risk', {'inputs': verdict['inputs'], 'adapter': verdict['inputs'].get('adapter'),
                                                      'held_for_approval': hold}, adapter=verdict['inputs'].get('adapter'), hold_for_approval=hold)
        if hold:
            _alert(db, 'WARNING', f"Order waits for a second person: {ins.get('symbol')} {side} {quantity.normalize()}",
                   'A LIVE order needs a Risk approver other than the person who placed it (Execution › Orders › Waiting).')
        return store.clean(order)
    order = _move(db, order, 'APPROVED', 'risk', {'inputs': verdict['inputs']})
    order = _move(db, order, 'SUBMITTED', 'paper', {'adapter': 'PAPER'}, submitted_at=at)
    order = _move(db, order, 'ACKNOWLEDGED', 'paper', {}, acknowledged_at=at)
    if order_type == 'MARKET':
        order = _fill(db, order, quantity, _paper_price(db, order, q), q, 'paper', at)
    else:
        if _triggered(order, q):
            order = _fill(db, order, quantity, _paper_price(db, order, q) if order_type == 'STOP' else (q['ask'] if side == 'BUY' else q['bid']), q, 'paper', at)
        elif time_in_force == 'IOC':
            order = _move(db, order, 'CANCELLED', 'paper', {'reason': 'IOC: not marketable on arrival'})
    return store.clean(order)


def _triggered(order: dict, q: dict) -> bool:
    """A working paper order trades: a limit when the price reaches it, a stop when the price passes it."""
    if order['order_type'] == 'LIMIT':
        limit = D(order['limit_price'])
        return q['ask'] <= limit if order['side'] == 'BUY' else q['bid'] >= limit
    if order['order_type'] == 'STOP':
        stop = D(order['stop_price'])
        return q['ask'] >= stop if order['side'] == 'BUY' else q['bid'] <= stop
    return True


def _four_eyes(db, order: dict, source: str) -> bool:
    """LIVE orders wait for a second person: manual tickets (setting MANUAL) or every order (ALL). Flattens never wait."""
    rule = settings(db).get('live_four_eyes') or 'MANUAL'
    if rule == 'OFF' or source == 'FLATTEN':
        return False
    env = db.execute('SELECT c.environment FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id WHERE a.id=%s',
                     (str(order['account_id']),)).fetchone()
    return bool(env) and env['environment'] == 'LIVE' and (rule == 'ALL' or source == 'MANUAL')


def release(actor: str, order_id: UUID, reason: str) -> dict:
    """The second person lets a held LIVE order go to the broker (never the person who placed it)."""
    with store.transaction('write') as db:
        o = db.execute('SELECT * FROM oms_orders WHERE id=%s FOR UPDATE', (order_id,)).fetchone()
        if not o:
            raise HTTPException(404, 'Order not found.')
        o = dict(o)
        if o['state'] != 'APPROVED' or not o['hold_for_approval']:
            raise HTTPException(409, 'This order is not waiting for approval.')
        from .. import platform_mode
        if not platform_mode.second_person_ok(db, o['created_by'], actor):
            raise HTTPException(403, 'Four-eyes: the person who placed the order cannot release it.')
        row = db.execute('UPDATE oms_orders SET hold_for_approval=false, released_by=%s, released_at=now(), version=version+1 WHERE id=%s RETURNING *',
                         (actor, str(order_id))).fetchone()
        db.execute('INSERT INTO oms_order_events(order_id, state_from, state_to, detail, actor) VALUES (%s,%s,%s,%s,%s)',
                   (str(order_id), 'APPROVED', 'APPROVED', json.dumps({'released': True, 'reason': reason}), actor))
        store.audit(db, actor, 'RELEASE_LIVE_ORDER', 'order', order_id, store.clean(o), store.clean(row), reason)
        return store.clean(row)


def _alert(db, severity: str, title: str, body: str) -> None:
    try:
        from .. import alerts
        alerts.enqueue(db, 'TRADING', severity, title, body)
    except Exception:  # noqa: BLE001 - never stop an order path for an alert
        pass


def match(db, now: datetime) -> dict:
    """Working paper limit and stop orders: fill when the live price reaches them; expire DAY orders at the market close."""
    filled = expired = 0
    for o in db.execute("SELECT * FROM oms_orders WHERE mode='PAPER' AND state IN ('ACKNOWLEDGED','PARTIALLY_FILLED') FOR UPDATE SKIP LOCKED").fetchall():
        o = dict(o)
        if o['expires_at'] and o['expires_at'] <= now:
            _move(db, o, 'EXPIRED', 'paper', {'reason': 'time in force ended'})
            expired += 1
            continue
        if o['order_type'] not in ('LIMIT', 'STOP'):
            continue
        q = quote(db, o['account_id'], o['instrument_id'], None)
        if not q:
            continue
        if _triggered(o, q):
            price = _paper_price(db, o, q) if o['order_type'] == 'STOP' else (q['ask'] if o['side'] == 'BUY' else q['bid'])
            _fill(db, o, D(o['quantity']) - D(o['filled_qty']), price, q, 'paper', now)
            filled += 1
    return {'filled': filled, 'expired': expired}


def protect(db, now: datetime) -> dict:
    """Protective exits COREI watches itself: every paper position, and real positions on brokers that do not hold the
    stop (Upstox). MT5 holds them at the broker (sent with the order) and its exits are booked from its deals.
    A level touched → a market order that closes the position for its own book, allowed through a kill switch."""
    from . import brokers
    closed = 0
    rows = db.execute('SELECT p.*, d.strategy_id FROM pf_positions p LEFT JOIN cfg_deployments d ON d.id=p.deployment_id '
                      'WHERE p.quantity <> 0 AND (p.stop_loss IS NOT NULL OR p.take_profit IS NOT NULL)').fetchall()
    for r in rows:
        if r['mode'] == 'BROKER' and brokers.capability(db, r['account_id']).get('wanted') == 'MT5_BRIDGE':
            continue
        q = quote(db, r['account_id'], r['instrument_id'], None)
        if not q:
            continue
        long = D(r['quantity']) > 0
        px = q['bid'] if long else q['ask']
        hit = None
        if r['stop_loss'] is not None and ((long and px <= D(r['stop_loss'])) or (not long and px >= D(r['stop_loss']))):
            hit = f"stop-loss {D(r['stop_loss']).normalize()} touched at {px}"
        elif r['take_profit'] is not None and ((long and px >= D(r['take_profit'])) or (not long and px <= D(r['take_profit']))):
            hit = f"take-profit {D(r['take_profit']).normalize()} touched at {px}"
        if not hit:
            continue
        if db.execute('SELECT 1 FROM oms_orders WHERE account_id=%s AND instrument_id=%s AND deployment_id IS NOT DISTINCT FROM %s AND state = ANY(%s)',
                      (str(r['account_id']), str(r['instrument_id']), r['deployment_id'], list(OPEN))).fetchone():
            continue  # a close is already on its way
        dep = {'id': r['deployment_id'], 'strategy_id': r['strategy_id']} if r['deployment_id'] else None
        o = place(db, account_id=r['account_id'], instrument_id=r['instrument_id'], side='SELL' if long else 'BUY', quantity=abs(D(r['quantity'])),
                  mode=r['mode'], deployment=dep, seed=f"protect:{r['id']}:{r['opened_at'].isoformat() if r['opened_at'] else ''}:{now:%Y%m%d%H%M}",
                  actor='protect', source='PROTECT', flatten=True, at=now)
        db.execute('INSERT INTO oms_order_events(order_id, state_from, state_to, detail, actor) VALUES (%s,%s,%s,%s,%s)',
                   (str(o['id']), o['state'], o['state'], json.dumps({'reason': hit}), 'protect'))
        closed += 1
    return {'protective_exits': closed}


def cancel(actor: str, order_id: UUID, reason: str) -> dict:
    with store.transaction('write') as db:
        o = db.execute('SELECT * FROM oms_orders WHERE id=%s FOR UPDATE', (order_id,)).fetchone()
        if not o:
            raise HTTPException(404, 'Order not found.')
        o = dict(o)
        if o['state'] == 'APPROVED' and o['mode'] == 'BROKER':  # not sent yet (or held for approval): nothing at the broker
            row = db.execute("UPDATE oms_orders SET state='CANCELLED', hold_for_approval=false, version=version+1, updated_at=now() WHERE id=%s RETURNING *",
                             (str(order_id),)).fetchone()
            db.execute('INSERT INTO oms_order_events(order_id, state_from, state_to, detail, actor) VALUES (%s,%s,%s,%s,%s)',
                       (str(order_id), 'APPROVED', 'CANCELLED', json.dumps({'reason': reason, 'sent': False}), actor))
            store.audit(db, actor, 'CANCEL_ORDER', 'order', order_id, store.clean(o), store.clean(row), reason)
            return store.clean(row)
        if o['state'] not in ('ACKNOWLEDGED', 'PARTIALLY_FILLED') and not (o['state'] == 'SUBMITTED' and o['mode'] == 'PAPER'):
            raise HTTPException(409, f"A {o['state'].lower().replace('_', ' ')} order cannot be cancelled"
                                + (' now: its outcome at the broker is not known yet.' if o['state'] in ('SUBMITTED', 'UNKNOWN') else '.'))
        if o['state'] == 'SUBMITTED':
            return store.clean(_move(db, o, 'CANCELLED', actor, {'reason': reason}))
        o = _move(db, o, 'CANCEL_PENDING', actor, {'reason': reason})
        store.audit(db, actor, 'CANCEL_ORDER', 'order', order_id, None, store.clean(o), reason)
        if o['mode'] == 'PAPER':
            o = _move(db, o, 'CANCELLED', 'paper', {'reason': reason})
            return store.clean(o)
    from . import execution  # outside the transaction: CANCEL_PENDING is on record before the broker is asked
    return execution.cancel_at_broker(actor, order_id)


class ModifyIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    limit_price: Decimal | None = Field(None, gt=0, max_digits=38, decimal_places=12)
    stop_price: Decimal | None = Field(None, gt=0, max_digits=38, decimal_places=12)
    stop_loss: Decimal | None = Field(None, ge=0, max_digits=38, decimal_places=12)     # 0 removes it
    take_profit: Decimal | None = Field(None, ge=0, max_digits=38, decimal_places=12)
    quantity: Decimal | None = Field(None, gt=0, max_digits=38, decimal_places=12)      # paper only; smaller, never below what is filled
    reason: str = Field(min_length=5, max_length=500)


MODIFIABLE = ('ACKNOWLEDGED', 'PARTIALLY_FILLED')


def _modified(db, o: dict, changes: dict, actor: str, reason: str, at_broker: dict | None = None) -> dict:
    """Write the new terms and an event (the state is unchanged: the same order, amended)."""
    sets = ', '.join([f'{k}=%s' for k in changes] + ['version=version+1', 'updated_at=now()'])
    row = dict(db.execute(f'UPDATE oms_orders SET {sets} WHERE id=%s RETURNING *', (*changes.values(), str(o['id']))).fetchone())
    txt = lambda v: None if v is None else format(D(v).normalize(), 'f')  # noqa: E731 - exact, as people write it
    detail = {'modified': {k: [txt(o.get(k)), txt(v)] for k, v in changes.items()}, 'reason': reason}
    if at_broker:
        detail['broker'] = at_broker
    db.execute('INSERT INTO oms_order_events(order_id, state_from, state_to, detail, actor) VALUES (%s,%s,%s,%s,%s)',
               (str(o['id']), o['state'], o['state'], json.dumps(detail, default=str), actor))
    store.audit(db, actor, 'MODIFY_ORDER', 'order', o['id'], store.clean(o), store.clean(row), reason)
    return row


def modify(actor: str, order_id: UUID, data: ModifyIn) -> dict:
    """Amend a working order: its limit or stop price, its stop-loss / take-profit, and (paper) a smaller quantity.
    New prices pass the pre-trade check before a broker amendment. Upstox accepts price-only amendments
    for working limit or stop-market orders. The local terms change after the broker accepts."""
    changes: dict = {}
    with store.transaction('write') as db:
        o = db.execute('SELECT * FROM oms_orders WHERE id=%s FOR UPDATE', (str(order_id),)).fetchone()
        if not o:
            raise HTTPException(404, 'Order not found.')
        o = dict(o)
        if o['state'] not in MODIFIABLE:
            raise HTTPException(409, f"Only a working order can be amended; this one is {o['state'].lower().replace('_', ' ')}.")
        ins = instrument(db, o['instrument_id'])
        tick = D(ins.get('tick_size')) or Decimal('0.00001')
        if data.limit_price is not None:
            if o['order_type'] != 'LIMIT':
                raise HTTPException(422, 'Only a limit order has a limit price.')
            changes['limit_price'] = quantize(data.limit_price, tick)
        if data.stop_price is not None:
            if o['order_type'] != 'STOP':
                raise HTTPException(422, 'Only a stop order has a stop price.')
            changes['stop_price'] = quantize(data.stop_price, tick)
        for k in ('stop_loss', 'take_profit'):
            v = getattr(data, k)
            if v is not None:
                changes[k] = None if v == 0 else quantize(v, tick)
        if data.quantity is not None:
            if o['mode'] != 'PAPER':
                raise HTTPException(409, 'The quantity of a real order is changed by cancelling it and placing a new one.')
            q, problem = risk.lot(db, o['instrument_id'], data.quantity)
            if problem:
                raise HTTPException(422, problem)
            if q >= D(o['quantity']):
                raise HTTPException(422, 'An amendment can only make the order smaller; place a new order for more.')
            if q <= D(o['filled_qty']):
                raise HTTPException(422, f"{q.normalize()} is not above what is already filled ({D(o['filled_qty']).normalize()}).")
            changes['quantity'] = q
        if not changes:
            raise HTTPException(422, 'Nothing to change.')
        if 'limit_price' in changes or 'stop_price' in changes:
            q = quote(db, o['account_id'], o['instrument_id'])
            probe = {**o, **changes, 'quantity': D(o['quantity']) - D(o['filled_qty'])}
            verdict = risk.check(db, probe, None, datetime.now(timezone.utc), q, flatten=bool(o.get('reduce_only')))
            bad = [r for r in verdict['reasons'] if r.startswith(('MAX_PRICE_DEVIATION_PCT', 'KILL_SWITCH', 'MAX_ORDER_VALUE', 'NO_PRICE'))]
            if bad:
                raise HTTPException(409, 'The new price is refused: ' + '; '.join(bad))
        if o['mode'] == 'PAPER':
            row = _modified(db, o, changes, actor, data.reason)
            return store.clean(row)
        if not o.get('broker_order_id'):
            raise HTTPException(409, 'Look the order up at the broker before amending it.')
        snapshot = o
        if o.get('adapter') == 'UPSTOX':
            from . import brokers
            if set(changes) - {'limit_price', 'stop_price'}:
                raise HTTPException(422, 'Upstox amendments on this ticket change the working limit or trigger price.')
            # Serialize the broker amendment with other mutations of this order.
            try:
                brokers.Upstox(o['account_id'], actor, 'Amend Upstox order price').modify_price(o, changes)
            except (brokers.NotSent, brokers.Refused) as exc:
                raise HTTPException(409, f'Upstox did not amend the order: {exc}') from None
            except brokers.Lost:
                db.execute('INSERT INTO oms_order_events(order_id,state_from,state_to,detail,actor) VALUES (%s,%s,%s,%s,%s)',
                           (str(order_id), o['state'], o['state'],
                            json.dumps({'modify_unanswered': {key:str(value) for key,value in changes.items()}, 'reason':data.reason}), actor))
            else:
                return store.clean(_modified(db, o, changes, actor, data.reason, {'adapter':'UPSTOX'}))
    if snapshot.get('adapter') == 'UPSTOX':
        raise HTTPException(504, 'Upstox did not confirm the amendment. Check the broker’s order before trying again.')
    from . import brokers   # MT5 uses its existing broker-first amendment path.
    sym = snapshot['broker_symbol']
    payload = {'action': 'modify', 'order': {'ticket': snapshot['broker_order_id'], 'tag': snapshot['broker_tag'], 'symbol': sym,
                                             'price': str(changes.get('limit_price', changes.get('stop_price', snapshot.get('limit_price') or snapshot.get('stop_price') or 0))),
                                             'sl': str(changes.get('stop_loss', snapshot.get('stop_loss')) or 0), 'tp': str(changes.get('take_profit', snapshot.get('take_profit')) or 0)}}
    try:
        brokers.throttle(snapshot['account_id'], wait_up_to=2.0)
        reply = brokers.mt5_call(snapshot['account_id'], actor, 'trade', payload, 'Amend an order at the broker', 30)
    except (brokers.NotSent, brokers.Refused) as exc:
        raise HTTPException(409, f'The broker did not amend it: {exc}') from None
    except brokers.Lost:
        with store.transaction('write') as db:
            db.execute('INSERT INTO oms_order_events(order_id, state_from, state_to, detail, actor) VALUES (%s,%s,%s,%s,%s)',
                       (str(order_id), snapshot['state'], snapshot['state'], json.dumps({'modify_unanswered': {k: str(v) for k, v in changes.items()}, 'reason': data.reason}), actor))
        raise HTTPException(504, 'No answer from the broker: the amendment may or may not have been made. Look the order up at the broker.') from None
    res = (reply.get('results') or [{}])[0]
    if not res.get('ok'):
        raise HTTPException(409, f"The broker refused the amendment: {res.get('text') or res.get('retcode')}")
    with store.transaction('write') as db:
        o = dict(db.execute('SELECT * FROM oms_orders WHERE id=%s FOR UPDATE', (str(order_id),)).fetchone())
        return store.clean(_modified(db, o, changes, actor, data.reason, {'retcode': res.get('retcode'), 'text': res.get('text')}))


class ResolveIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    outcome: Literal['FILLED', 'NOT_FILLED']
    price: Decimal | None = Field(default=None, gt=0, max_digits=38, decimal_places=12)
    quantity: Decimal | None = Field(default=None, gt=0, max_digits=38, decimal_places=12)
    reason: str = Field(min_length=5, max_length=500)
    broker_deal_id: str | None = Field(default=None, max_length=64)


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
        remaining = D(o['quantity']) - D(o['filled_qty'])
        qty = data.quantity if data.quantity is not None else remaining
        if qty <= 0:
            raise HTTPException(422, f'Resolution quantity must be positive and at most the remaining {remaining.normalize()}.')
        if qty > remaining:
            raise HTTPException(422, f'The stated fill ({qty}) is more than the order has left ({remaining}). '
                                     'If the broker really filled more, record what remains here and raise the excess as a break.')
        return store.clean(_fill(db, o, qty, data.price, None, actor, datetime.now(timezone.utc), deal_id=data.broker_deal_id,
                                 detail={'resolved_by_person': True}))


Money = Field(default=None, gt=0, max_digits=38, decimal_places=12)


class TicketIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_id: UUID | None = None                     # one account, or a routing choice among route_accounts
    route: Literal['NONE', 'BEST_PRICE', 'LOWEST_COST', 'PRIORITY', 'SPLIT_EQUAL'] = 'NONE'
    route_accounts: list[UUID] = Field(default_factory=list, max_length=20)
    instrument_id: UUID
    side: Literal['BUY', 'SELL']
    quantity: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    order_type: Literal['MARKET', 'LIMIT', 'STOP'] = 'MARKET'
    limit_price: Decimal | None = Money
    stop_price: Decimal | None = Money
    stop_loss: Decimal | None = Money
    take_profit: Decimal | None = Money
    time_in_force: Literal['GTC', 'DAY', 'IOC'] = 'GTC'
    mode: Literal['PAPER', 'BROKER'] = 'PAPER'
    ticket_key: str = Field(min_length=8, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')  # the screen's own key: a double click is one order
    reason: str = Field(default='', max_length=500)


def ticket(actor: str, data: TicketIn) -> dict:
    """An operator's order. With routing, COREI picks the account (or splits across them) and records why."""
    if data.order_type == 'LIMIT' and not data.limit_price:
        raise HTTPException(422, 'A limit order needs a limit price.')
    if data.order_type == 'STOP' and not data.stop_price:
        raise HTTPException(422, 'A stop order needs a stop price.')
    with store.transaction('write') as db:
        if data.route == 'NONE':
            if not data.account_id:
                raise HTTPException(422, 'Choose an account, or a routing rule and the accounts it may use.')
            legs = [(data.account_id, data.quantity, None)]
        else:
            from . import routing
            legs = routing.choose(db, data.route, data.route_accounts or ([data.account_id] if data.account_id else []), data.instrument_id,
                                  data.side, data.quantity, data.mode)
        out = []
        for n, (account_id, qty, decision) in enumerate(legs):
            o = place(db, account_id=account_id, instrument_id=data.instrument_id, side=data.side, quantity=qty, order_type=data.order_type,
                      limit_price=data.limit_price, stop_price=data.stop_price, stop_loss=data.stop_loss, take_profit=data.take_profit,
                      time_in_force=data.time_in_force, mode=data.mode, seed=f'ticket:{data.ticket_key}' + (f':{n}' if n else ''), actor=actor,
                      routing=decision)
            if not o.get('duplicate'):
                store.audit(db, actor, 'PLACE_ORDER', 'order', o['id'], None, o, data.reason)
            out.append(o)
    if data.mode == 'BROKER':
        from . import execution
        execution.dispatch_soon()
    return out[0] if len(out) == 1 else {**out[0], 'legs': out}


def flatten(actor: str, account_id: UUID, instrument_id: UUID, reason: str, key: str,
            mode: Literal['PAPER', 'BROKER'] | None = None) -> dict:
    """Request closes for an account and instrument, optionally limited to one execution mode."""
    mode_filter = ' AND p.mode=%s' if mode else ''
    args = [str(account_id), str(instrument_id)]
    if mode:
        args.append(mode)
    with store.transaction('write') as db:
        rows = db.execute('SELECT p.*, d.strategy_id FROM pf_positions p LEFT JOIN cfg_deployments d ON d.id=p.deployment_id '
                          'WHERE p.account_id=%s AND p.instrument_id=%s AND p.quantity <> 0' + mode_filter
                          + ' ORDER BY p.deployment_id NULLS FIRST', args).fetchall()
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
    if any(o.get('mode') == 'BROKER' for o in out):
        from . import execution
        execution.dispatch_soon()
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
