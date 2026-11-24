"""Order dispatcher (Stage 7 · Execution intelligence): real orders to demo and live accounts, safely.

Why it exists: a real order must be on record as SUBMITTED before it leaves COREI, and whatever happens next — an
answer, a refusal, silence — must be recorded exactly. The order engine (oms) cannot do this inside its transaction,
because a broker call must never hold a database transaction open; this module does it in three short steps.
Purpose:
  dispatch   APPROVED BROKER orders (not held for four-eyes): last gate (kill switches, order sending, adapter),
             optional broker pre-check (MT5 order_check), SUBMITTED committed, then the adapter call, then the
             broker's answer applied in a new transaction: fills (once per deal ticket), ACKNOWLEDGED with the broker's
             order number, REJECTED with its reason — or UNKNOWN when no answer came.
  sync       working and UNKNOWN orders are looked up at the broker by their tag (MT5 comment, Upstox tag) every few
             seconds: new deals become fills; cancelled, rejected or expired orders are closed; a SUBMITTED order left
             by a crash becomes UNKNOWN first. Nothing is ever sent again to find out.
  cancel     CANCEL_PENDING is committed, then the broker is asked, then the result is looked up.
Must never: send an order that risk refused or that a kill switch now blocks; send an UNKNOWN order again; hold a
transaction during a broker call; apply a broker deal twice (oms_fills.broker_deal_id is unique per account).
Dependencies: trading.oms (state machine and fills), trading.brokers (adapters), trading.risk (kill switches),
config_store. Consumers: oms.ticket/flatten and signals (dispatch_soon), the background loop (start), trading_api.
Architectural position: Stage 7, between the order engine and the broker.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time as _time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from uuid import UUID

from fastapi import HTTPException

from .. import config_store as store
from . import brokers, oms, risk
from .common import D, ZERO, settings

log = logging.getLogger('corei.execution')
_wake = threading.Event()
_started = threading.Lock()
_running = {'thread': None}
STATUS: dict = {'last_dispatch_at': None, 'last_sync_at': None, 'sent': 0, 'unknown': 0, 'errors': []}
WORKING = ('ACKNOWLEDGED', 'PARTIALLY_FILLED', 'CANCEL_PENDING', 'UNKNOWN')


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dec(v) -> Decimal | None:
    try:
        d = Decimal(str(v))
        return d if d.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _lock(db, order_id) -> dict | None:
    row = db.execute('SELECT * FROM oms_orders WHERE id=%s FOR UPDATE', (str(order_id),)).fetchone()
    return dict(row) if row else None


# ---------- the gate just before sending ----------

def gate(db, o: dict) -> tuple[str | None, list[str]]:
    """(adapter, reasons): what still stops this approved order now (a kill switch engaged after approval, order
    sending turned off, no adapter). Risk's own limits were checked at approval."""
    reasons: list[str] = []
    dep = db.execute('SELECT strategy_id FROM cfg_deployments WHERE id=%s', (str(o['deployment_id']),)).fetchone() if o.get('deployment_id') else None
    scopes = {'ACCOUNT': str(o['account_id']), 'INSTRUMENT': str(o['instrument_id']), 'DEPLOYMENT': str(o['deployment_id']) if o.get('deployment_id') else None,
              'STRATEGY': str(dep['strategy_id']) if dep else None}
    for k in risk.engaged(db, scopes):
        if not (o.get('source') == 'FLATTEN' and o.get('reduce_only')):
            reasons.append(f"KILL_SWITCH_{k['scope']}: {k['reason']}")
    env = db.execute('SELECT c.environment FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id WHERE a.id=%s',
                     (str(o['account_id']),)).fetchone()['environment']
    if not (store.execution_controls(db).get(env) or {}).get('trading_enabled'):
        reasons.append(f'ORDER_SENDING_OFF_{env}: order sending is off for {env} accounts')
    cap = brokers.capability(db, o['account_id'])
    reasons += cap['reasons']
    if not o.get('broker_symbol'):
        reasons.append('NO_BROKER_SYMBOL: map the instrument to this account\'s broker symbol (Setup › Instruments › mapping)')
    return cap.get('adapter'), reasons


# ---------- dispatch ----------

def dispatch(limit: int = 50) -> dict:
    ids = []
    with store.transaction('read') as db:
        ids = [r['id'] for r in db.execute("SELECT id FROM oms_orders WHERE mode='BROKER' AND state='APPROVED' AND NOT hold_for_approval "
                                           'ORDER BY created_at LIMIT %s', (limit,)).fetchall()]
    out = {'sent': 0, 'rejected': 0, 'unknown': 0}
    for oid in ids:
        try:
            r = send_one(oid)
        except Exception:  # noqa: BLE001 - one order's failure never stops the others
            log.exception('dispatch failed for order %s', oid)
            continue
        if r:
            out[{'REJECTED': 'rejected', 'UNKNOWN': 'unknown'}.get(r.get('state'), 'sent')] += 1
    STATUS['last_dispatch_at'] = _now().isoformat()
    STATUS['sent'] += out['sent']
    return out


def send_one(order_id) -> dict | None:
    """One approved order to its broker. Returns the order after the broker's answer (or None if not ours to send)."""
    with store.transaction('read') as db:
        o = db.execute("SELECT * FROM oms_orders WHERE id=%s AND state='APPROVED' AND mode='BROKER' AND NOT hold_for_approval", (str(order_id),)).fetchone()
        if not o:
            return None
        o = dict(o)
        cfg = settings(db)
        adapter, reasons = gate(db, o)
    if reasons:
        return _reject(order_id, reasons, sent=False)
    payload = brokers.mt5_order(o, o['broker_symbol'], int(cfg['broker_magic']), int(cfg['max_deviation_points'])) if adapter == 'MT5_BRIDGE' else None
    # Pre-check at the broker (MT5 order_check): margin, volume, price and trading rules, without trading.
    if adapter == 'MT5_BRIDGE' and cfg.get('pre_check_orders'):
        try:
            check = brokers.mt5_call(o['account_id'], 'dispatcher', 'trade', {'action': 'check', 'order': payload}, 'Pre-check an order at the broker', 20)
        except (brokers.NotSent, brokers.Lost) as exc:
            return _reject(order_id, [f'PRE_CHECK_FAILED: {exc}'], sent=False)
        bad = [r for r in check.get('results') or [] if not r.get('ok')]
        if check.get('error') or bad:
            return _reject(order_id, [f"BROKER_CHECK: {check.get('error') or bad[0].get('text')} ({(bad[0] if bad else {}).get('retcode')})"],
                           sent=False, detail={'check': check.get('results')})
    # SUBMITTED is committed before anything leaves COREI.
    with store.transaction('write') as db:
        locked = db.execute("SELECT * FROM oms_orders WHERE id=%s AND state='APPROVED' AND NOT hold_for_approval FOR UPDATE SKIP LOCKED",
                            (str(order_id),)).fetchone()
        if not locked:
            return None  # another dispatcher has it, or it was cancelled meanwhile
        checked_adapter, blockers = gate(db, locked)
        if checked_adapter != adapter and not blockers:
            blockers.append('ADAPTER_CHANGED: the route changed during broker pre-check')
        if blockers:
            return _reject_locked(db, dict(locked), blockers, sent=False)
        o = oms._move(db, dict(locked), 'SUBMITTED', 'execution', {'adapter': adapter, 'tag': o['broker_tag'], 'symbol': o['broker_symbol']},
                      submitted_at=_now(), adapter=adapter)
    try:
        if adapter == 'MT5_BRIDGE':
            reply = brokers.mt5_call(o['account_id'], 'dispatcher', 'trade', {'action': 'send', 'order': payload}, 'Send an order to the broker',
                                     int(cfg['broker_timeout_seconds']))
            return apply_mt5(order_id, reply)
        up = brokers.Upstox(o['account_id'], 'dispatcher', 'Send an order to Upstox')
        data = up.place(o, o['broker_symbol'], cfg.get('upstox_product') or 'D')
        with store.transaction('write') as db:
            cur = _lock(db, order_id)
            cur = oms._move(db, cur, 'ACKNOWLEDGED', 'broker', {'broker_order_id': data.get('order_id')}, broker_order_id=str(data.get('order_id') or ''),
                            acknowledged_at=_now())
        return check_one(order_id, 'dispatcher') or cur
    except brokers.NotSent as exc:
        return _settle(order_id, 'REJECTED', {'not_sent': True, 'reason': str(exc)}, reasons=[f'NOT_SENT: {exc}'])
    except brokers.Refused as exc:
        return _settle(order_id, 'REJECTED', {'broker': str(exc)}, reasons=[f'BROKER_REFUSED: {exc}'])
    except brokers.Lost as exc:
        STATUS['unknown'] += 1
        out = _settle(order_id, 'UNKNOWN', {'reason': str(exc), 'next': 'COREI looks the order up at the broker by its tag; it is never sent again'})
        with store.transaction('write') as db:
            oms._alert(db, 'CRITICAL', 'Order outcome unknown', f"{o['broker_symbol']} {o['side']} {o['quantity']}: {exc}. COREI is checking the broker.")
        return out


def _reject(order_id, reasons: list[str], sent: bool, detail: dict | None = None) -> dict:
    with store.transaction('write') as db:
        o = _lock(db, order_id)
        if o['state'] != 'APPROVED':
            return store.clean(o)
        return _reject_locked(db, o, reasons, sent, detail)


def _reject_locked(db, o: dict, reasons: list[str], sent: bool, detail: dict | None = None) -> dict:
    db.execute('INSERT INTO risk_decisions(id, order_id, account_id, instrument_id, side, requested_qty, approved, reasons, inputs, deployment_id) '
               'VALUES (gen_random_uuid(),%s,%s,%s,%s,%s,false,%s,%s,%s)',
               (str(o['id']), str(o['account_id']), str(o['instrument_id']), o['side'], o['quantity'], json.dumps(reasons),
                json.dumps({'stage': 'dispatch', 'sent': sent}), str(o['deployment_id']) if o.get('deployment_id') else None))
    out = oms._move(db, o, 'REJECTED', 'execution', {'reasons': reasons, 'sent': sent, **(detail or {})}, reasons=json.dumps(reasons))
    oms._alert(db, 'WARNING', f"Order not sent: {o.get('broker_symbol')} {o['side']} {D(o['quantity']).normalize()}", '; '.join(reasons)[:1500])
    return store.clean(out)


def _settle(order_id, to: str, detail: dict, reasons: list[str] | None = None) -> dict:
    with store.transaction('write') as db:
        o = _lock(db, order_id)
        if to not in oms.TRANSITIONS.get(o['state'], set()):
            return store.clean(o)
        fields = {'reasons': json.dumps(reasons)} if reasons else {}
        return store.clean(oms._move(db, o, to, 'execution', detail, **fields))


# ---------- applying the broker's answer ----------

def apply_mt5(order_id, reply: dict) -> dict:
    """The bridge's answer to a send: fills per deal, ACKNOWLEDGED for a placed pending order, REJECTED with the
    terminal's reason; a reply showing the tag was already at the broker is applied like a look-up."""
    with store.transaction('write') as db:
        o = _lock(db, order_id)
        if reply.get('duplicate'):
            return store.clean(_apply_found(db, o, reply.get('found') or {}, 'broker'))
        results = reply.get('results') or []
        if reply.get('error') and not results:  # the bridge refused before sending anything (unknown symbol, bad action)
            return store.clean(oms._move(db, o, 'REJECTED', 'broker', {'reasons': [reply['error']], 'sent': False}, reasons=json.dumps([reply['error']])))
        now = _now()
        failed = None
        for r in results:
            if not r.get('ok'):
                failed = r
                break
            vol, price = _dec(r.get('volume')) or ZERO, _dec(r.get('price')) or ZERO
            if r.get('deal') and vol > 0 and price > 0:
                o = oms._fill(db, o, vol, price, None, 'broker', now, deal_id=str(r['deal']),
                              detail={'retcode': r.get('retcode'), 'broker_order': r.get('order'), 'via': reply.get('via')})
                if o.get('broker_order_id') is None and r.get('order'):
                    db.execute('UPDATE oms_orders SET broker_order_id=%s WHERE id=%s', (str(r['order']), str(order_id)))
            elif o['state'] == 'SUBMITTED':
                o = oms._move(db, o, 'ACKNOWLEDGED', 'broker', {'retcode': r.get('retcode'), 'text': r.get('text'), 'broker_order': r.get('order'),
                                                               'via': reply.get('via')}, broker_order_id=str(r.get('order') or ''), acknowledged_at=now)
        if failed:
            text = f"BROKER_{failed.get('retcode')}: {failed.get('text') or failed.get('error') or 'refused'}"
            if D(o['filled_qty']) > 0:
                o = oms._move(db, o, 'CANCELLED', 'broker', {'reason': f'rest not done: {text}', 'result': failed}, completed_at=now)
            else:
                o = oms._move(db, o, 'REJECTED', 'broker', {'reasons': [text], 'result': failed}, reasons=json.dumps([text]), completed_at=now)
                oms._alert(db, 'WARNING', f"Broker refused {o.get('broker_symbol')} {o['side']} {D(o['quantity']).normalize()}", text)
        elif o['state'] == 'SUBMITTED':
            o = oms._move(db, o, 'ACKNOWLEDGED', 'broker', {'note': 'sent; waiting for the deal'}, acknowledged_at=now)
        return store.clean(o)


def _apply_found(db, o: dict, found: dict, actor: str) -> dict:
    """What the broker holds under this order's tag → the order's state. Deals become fills (once each)."""
    now = _now()
    for d in sorted(found.get('deals') or [], key=lambda x: str(x.get('time'))):
        vol, price = _dec(d.get('volume')) or ZERO, _dec(d.get('price')) or ZERO
        if vol <= 0 or price <= 0 or d.get('side') != o['side']:
            continue
        if o['state'] in ('FILLED', 'CANCELLED', 'REJECTED', 'EXPIRED'):
            break
        if o['state'] in ('SUBMITTED',):
            o = oms._move(db, o, 'ACKNOWLEDGED', actor, {'found': 'deal at the broker'}, acknowledged_at=now)
        o = oms._fill(db, o, min(vol, D(o['quantity']) - D(o['filled_qty'])), price, None, actor, now, deal_id=str(d.get('ticket')),
                      detail={'found_by_tag': True, 'broker_time': d.get('time')})
    open_orders = found.get('orders') or []
    if o['state'] in ('UNKNOWN', 'SUBMITTED') and open_orders:
        o = oms._move(db, o, 'ACKNOWLEDGED', actor, {'found': 'working at the broker', 'ticket': open_orders[0].get('ticket')},
                      broker_order_id=str(open_orders[0].get('ticket')), acknowledged_at=now)
    elif not open_orders and o['state'] in ('ACKNOWLEDGED', 'PARTIALLY_FILLED', 'CANCEL_PENDING', 'UNKNOWN'):
        done = [h for h in found.get('history_orders') or [] if h.get('state') in ('CANCELED', 'REJECTED', 'EXPIRED')]
        if done:
            kind = done[-1]['state']
            to = 'EXPIRED' if kind == 'EXPIRED' else 'REJECTED' if kind == 'REJECTED' and D(o['filled_qty']) == 0 else 'CANCELLED'
            if to == 'REJECTED' and 'REJECTED' not in oms.TRANSITIONS.get(o['state'], set()):
                to = 'CANCELLED'
            if to in oms.TRANSITIONS.get(o['state'], set()):
                o = oms._move(db, o, to, actor, {'found': f'{kind.lower()} at the broker', 'ticket': done[-1].get('ticket')}, completed_at=now)
    return o


# ---------- looking orders up ----------

def check_one(order_id, actor: str = 'execution') -> dict | None:
    """Ask the broker what it holds under this order's tag and apply it. Lookups only — never a new order."""
    with store.transaction('read') as db:
        o = db.execute('SELECT * FROM oms_orders WHERE id=%s', (str(order_id),)).fetchone()
    if not o or o['mode'] != 'BROKER' or not o['broker_tag']:
        return None
    o = dict(o)
    now = _now()
    try:
        if o['adapter'] == 'UPSTOX' or (o['adapter'] is None and brokers_adapter(o['account_id']) == 'UPSTOX'):
            found = _upstox_found(o, actor)
        else:
            reply = brokers.mt5_call(o['account_id'], actor, 'book', {'tag': o['broker_tag'], 'days': 7}, 'Look an order up at the broker', 30)
            found = reply.get('tagged') or {}
            try:
                from . import posttrade
                posttrade.store_book(o['account_id'], reply)
            except Exception:  # noqa: BLE001 - the look-up result stands on its own
                log.warning('book snapshot not stored', exc_info=True)
    except (brokers.NotSent, brokers.Lost, brokers.Refused) as exc:
        with store.transaction('write') as db:
            db.execute('UPDATE oms_orders SET last_check_at=%s, last_check=%s, checks=checks+1 WHERE id=%s',
                       (now, json.dumps({'ok': False, 'error': str(exc)[:300]}), str(order_id)))
        return None
    with store.transaction('write') as db:
        cur = _lock(db, order_id)
        before = cur['state']
        if cur['state'] == 'SUBMITTED':  # left by a crash between the send and its answer
            cur = oms._move(db, cur, 'UNKNOWN', actor, {'reason': 'no answer recorded for the send; looking it up'})
        cur = _apply_found(db, cur, found, actor)
        nothing = not any(found.get(k) for k in ('deals', 'orders', 'positions', 'history_orders'))
        summary = {'ok': True, 'deals': len(found.get('deals') or []), 'open': len(found.get('orders') or []),
                   'history': len(found.get('history_orders') or []), 'nothing_at_broker': nothing}
        db.execute('UPDATE oms_orders SET last_check_at=%s, last_check=%s, checks=checks+1 WHERE id=%s', (now, json.dumps(summary), str(order_id)))
        if cur['state'] == 'UNKNOWN' and nothing and (cur['checks'] or 0) in (2, 20):
            oms._alert(db, 'CRITICAL', 'Order still unknown: the broker shows nothing under its tag',
                       f"{cur.get('broker_symbol')} {cur['side']} {cur['quantity']} (tag {cur['broker_tag']}). Check the terminal; then resolve it on "
                       'Execution › Orders. It will not be sent again.')
        if cur['state'] != before:
            log.info('order %s %s → %s by look-up', order_id, before, cur['state'])
        return store.clean(cur)


def brokers_adapter(account_id) -> str | None:
    with store.transaction('read') as db:
        return brokers.capability(db, account_id).get('wanted')


def _upstox_found(o: dict, actor: str) -> dict:
    """Upstox order history and trades in the bridge's 'tagged' shape."""
    up = brokers.Upstox(o['account_id'], actor, 'Look an order up at Upstox')
    hist = up.history(order_id=o['broker_order_id']) if o.get('broker_order_id') else up.history(tag=o['broker_tag'])
    hist = hist if isinstance(hist, list) else [hist]
    last = hist[-1] if hist else None
    found: dict = {'orders': [], 'history_orders': [], 'deals': [], 'positions': []}
    if not last:
        return found
    state = brokers.UPSTOX_STATE.get(str(last.get('status') or '').lower(), 'ACKNOWLEDGED')
    ticket = last.get('order_id')
    if state in ('ACKNOWLEDGED', 'SUBMITTED', 'CANCEL_PENDING'):
        found['orders'].append({'ticket': ticket, 'state': 'PLACED'})
    elif state in ('CANCELLED', 'REJECTED'):
        found['history_orders'].append({'ticket': ticket, 'state': 'CANCELED' if state == 'CANCELLED' else 'REJECTED', 'comment': last.get('status_message')})
    for t in up.trades(ticket) if ticket else []:
        found['deals'].append({'ticket': t.get('trade_id'), 'side': str(t.get('transaction_type') or '').upper(), 'volume': str(t.get('quantity')),
                               'price': str(t.get('average_price') or t.get('price')), 'time': t.get('exchange_timestamp') or t.get('order_timestamp')})
    return found


def sync(now: datetime | None = None, limit: int = 50) -> dict:
    """Working, cancel-pending, UNKNOWN and stale SUBMITTED broker orders: look each up when due."""
    now = now or _now()
    with store.transaction('read') as db:
        cfg = settings(db)
        every = int(cfg.get('unknown_check_seconds') or 15)
        stale = now - timedelta(seconds=int(cfg.get('broker_timeout_seconds') or 30) + 30)
        rows = db.execute("""SELECT id FROM oms_orders WHERE mode='BROKER' AND broker_tag IS NOT NULL
                               AND ((state = ANY(%s) AND (last_check_at IS NULL OR last_check_at < %s))
                                    OR (state='SUBMITTED' AND submitted_at < %s))
                             ORDER BY updated_at LIMIT %s""", (list(WORKING), now - timedelta(seconds=every), stale, limit)).fetchall()
    n = 0
    for r in rows:
        try:
            check_one(r['id'], 'execution')
            n += 1
        except Exception:  # noqa: BLE001
            log.exception('look-up failed for order %s', r['id'])
    STATUS['last_sync_at'] = now.isoformat()
    return {'checked': n}


def cancel_at_broker(actor: str, order_id) -> dict:
    """After CANCEL_PENDING is committed: ask the broker to cancel, then look the order up for the final state."""
    with store.transaction('read') as db:
        o = dict(db.execute('SELECT * FROM oms_orders WHERE id=%s', (str(order_id),)).fetchone())
    try:
        if o['adapter'] == 'UPSTOX':
            brokers.Upstox(o['account_id'], actor, 'Cancel an order at Upstox').cancel(o['broker_order_id'])
        else:
            brokers.mt5_call(o['account_id'], actor, 'trade', {'action': 'cancel', 'order': {'tag': o['broker_tag'], 'ticket': o.get('broker_order_id') or None}},
                             'Cancel an order at the broker', 30)
    except (brokers.NotSent, brokers.Refused) as exc:
        with store.transaction('write') as db:
            cur = _lock(db, order_id)
            if cur['state'] == 'CANCEL_PENDING':
                cur = oms._move(db, cur, 'ACKNOWLEDGED', 'execution', {'cancel_failed': str(exc)})
            return {**store.clean(cur), 'cancel_error': str(exc)}
    except brokers.Lost:
        pass  # the look-up below (or the next sync) finds out
    return check_one(order_id, actor) or store.clean(o)


# ---------- the background loop ----------

def dispatch_soon() -> None:
    """Called after a transaction that approved BROKER orders has committed."""
    if os.getenv('COREI_DISPATCH') == 'sync':
        dispatch()
        return
    if _running['thread'] is not None and _running['thread'].is_alive():
        _wake.set()
    else:
        threading.Thread(target=_safe_dispatch, daemon=True, name='corei-dispatch-once').start()


def _safe_dispatch() -> None:
    try:
        dispatch()
    except Exception:  # noqa: BLE001
        log.exception('dispatch failed')


def start() -> None:
    """One dispatcher thread per process: sends approved orders at once (woken) and looks orders up every 2 s."""
    with _started:
        if _running['thread'] is not None and _running['thread'].is_alive():
            return
        t = threading.Thread(target=_loop, daemon=True, name='corei-dispatch')
        _running['thread'] = t
        t.start()


def check_nodes() -> int:
    from .. import bridge_nodes
    with store.transaction('read') as db:
        ids = [r['id'] for r in db.execute('SELECT id FROM cfg_bridge_nodes WHERE fingerprint IS NOT NULL AND token IS NOT NULL').fetchall()]
    for nid in ids:
        try:
            bridge_nodes.check(nid, 'health')
        except Exception:  # noqa: BLE001 - one node never stops the others
            log.warning('node check failed', exc_info=True)
    return len(ids)


def _loop() -> None:
    last_recon = 0.0
    last_nodes = 0.0
    while True:
        _wake.wait(2.0)
        _wake.clear()
        try:
            with store.transaction('write') as db:
                oms.protect(db, _now())  # paper and Upstox stops between trading cycles
            from . import algos
            algos.tick(_now())        # execution algorithms between trading cycles (TWAP slices, iceberg clips)
            dispatch()
            sync()
            if _time.monotonic() - last_recon > 60:
                last_recon = _time.monotonic()
                from . import posttrade
                posttrade.reconcile_due()
            if _time.monotonic() - last_nodes > 300:  # bridge nodes: version, clock, pool and certificate, every 5 minutes
                last_nodes = _time.monotonic()
                check_nodes()
        except Exception as exc:  # noqa: BLE001 - the loop keeps running; the error is shown on the screen
            STATUS['errors'] = ([f'{_now().isoformat()} {type(exc).__name__}: {exc}'[:300]] + STATUS['errors'])[:20]
            log.exception('execution loop')


def status(db) -> dict:
    counts = {r['state']: r['n'] for r in db.execute("SELECT state, count(*) AS n FROM oms_orders WHERE mode='BROKER' AND state = ANY(%s) GROUP BY 1",
                                                     (['APPROVED', 'SUBMITTED', *WORKING],)).fetchall()}
    held = db.execute("SELECT count(*) AS n FROM oms_orders WHERE state='APPROVED' AND hold_for_approval").fetchone()['n']
    return {**STATUS, 'running': bool(_running['thread'] and _running['thread'].is_alive()), 'open': counts, 'held_for_approval': held}


def broker_order_detail(order_id: UUID) -> dict:
    """Look-up now (operator's button): what the broker holds for this order."""
    out = check_one(order_id, 'operator')
    if out is None:
        raise HTTPException(409, 'Only a real (broker) order that has been sent can be looked up; or the broker could not be reached — see the last check.')
    return out
