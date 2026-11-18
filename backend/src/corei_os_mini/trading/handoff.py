"""Execution handoff: how a strategy's decision reaches Stage 7 (Execution), per deployment.

Why it exists: a desk decides, per strategy, whether its decisions go straight to the market, wait for a trader, or
are only recorded. Execution must see every decision coming from the strategies — with its side, size, price terms
and protective levels — and act on the ones waiting for it.
Purpose:
  policy        the deployment's execution policy (handoff AUTO / APPROVE / SIGNAL_ONLY, order type MARKET / LIMIT,
                limit mode and offset, time in force, approval window), with defaults that keep the old behaviour.
  order_terms   order type, limit price and time in force for one order from the policy and the live quote.
  pend, record  a decision waiting for execution (PENDING, older ones of the deployment SUPERSEDED), or recorded
                only (RECORDED) — both with the full size already worked out by risk sizing.
  execute       a person executes a PENDING decision: re-read the position (the order is target − position now),
                optional smaller quantity or other price terms, then the normal order path (risk check, paper fill or
                dispatcher); the same client order ID as an automatic order, so it can never be sent twice.
  decline, expire  a person declines; the approval window passes. The signal is cancelled / expired accordingly.
  flow          every decision from the strategies with its signal, risk result and order: the execution queue.
  policies, set_policy  the policy table and its audited change.
Must never: send without the risk check (oms.place runs it), re-send a decision that already has an order, execute an
expired or superseded decision, or let a policy change skip the audit.
Dependencies: trading.oms, trading.risk, trading.portfolio, trading.signals (state changes), config_store.
Consumers: trading.signals.process, trading.runtime (expiry), trading_api.
Architectural position: the seam between Stage 4 (decisions) and Stage 7 (orders); Stage 5 risk runs inside.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from .. import config_store as store
from .common import D, data_source, deployments, instrument, quote

DEFAULT = {'handoff': 'AUTO', 'order_type': 'MARKET', 'limit_mode': 'MARKETABLE', 'limit_offset_ticks': 0, 'time_in_force': 'GTC', 'approval_minutes': 10}


class PolicyIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    handoff: Literal['AUTO', 'APPROVE', 'SIGNAL_ONLY'] = 'AUTO'
    order_type: Literal['MARKET', 'LIMIT'] = 'MARKET'
    limit_mode: Literal['MARKETABLE', 'PASSIVE'] = 'MARKETABLE'
    limit_offset_ticks: int = Field(0, ge=0, le=100000)
    time_in_force: Literal['GTC', 'DAY', 'IOC'] = 'GTC'
    approval_minutes: int = Field(10, ge=1, le=1440)
    version: int = Field(ge=1)
    reason: str = Field(min_length=5, max_length=500)


class ExecuteIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    quantity: Decimal | None = Field(default=None, gt=0, max_digits=38, decimal_places=12)
    order_type: Literal['MARKET', 'LIMIT'] | None = None
    limit_price: Decimal | None = Field(default=None, gt=0, max_digits=38, decimal_places=12)
    note: str = Field('', max_length=500)


def policy(dep: dict) -> dict:
    return {**DEFAULT, **(dep.get('execution') or {})}


def order_terms(pol: dict, side: str, q: dict | None, tick: Decimal) -> tuple[str, Decimal | None, str]:
    """MARKET, or LIMIT at: MARKETABLE buy = ask + n·tick (a price cap), sell = bid − n·tick; PASSIVE buy = bid − n·tick, sell = ask + n·tick."""
    if pol['order_type'] != 'LIMIT' or q is None:
        return 'MARKET', None, pol['time_in_force']
    off = tick * int(pol.get('limit_offset_ticks') or 0)
    buy = side == 'BUY'
    if pol.get('limit_mode') == 'PASSIVE':
        price = q['bid'] - off if buy else q['ask'] + off
    else:
        price = q['ask'] + off if buy else q['bid'] - off
    return 'LIMIT', price if price > 0 else None, pol['time_in_force']


def _tick(db, dep) -> Decimal:
    return D(instrument(db, dep['instrument_id']).get('tick_size')) or Decimal('0.00001')


def _insert(db, sig, dep, *, target, current, want, outcome, handoff, side, qty, terms, stop, take, q, note, expires_at, reasons=None) -> dict:
    row = db.execute("""INSERT INTO tr_intents(id, signal_id, deployment_id, account_id, instrument_id, target, current_qty, target_qty, outcome, reasons, handoff,
                            side, quantity, order_type, limit_price, time_in_force, stop_loss, take_profit, decision_bid, decision_ask, sizing_note, expires_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                     (uuid4(), str(sig['id']), str(dep['id']), str(dep['account_id']), str(dep['instrument_id']), target, current, want, outcome,
                      json.dumps(reasons or []), handoff, side, qty, terms[0], terms[1], terms[2], stop, take, q and q['bid'], q and q['ask'], note or None,
                      expires_at)).fetchone()
    return dict(row)


def pend(db, sig, dep, *, target, current, want, stop, take, q, note, now) -> dict:
    """The decision waits in the execution queue. A newer decision of the same deployment replaces an older one."""
    from . import signals
    pol = policy(dep)
    for old in db.execute("UPDATE tr_intents SET outcome='SUPERSEDED', decided_at=now(), decided_by='runtime', decision_note='replaced by a newer decision' "
                          "WHERE deployment_id=%s AND outcome='PENDING' RETURNING signal_id", (str(dep['id']),)).fetchall():
        if old['signal_id']:
            signals._set(db, old['signal_id'], 'CANCELLED', 'execution', 'superseded by a newer decision of the same deployment')
    delta = want - current
    side = 'BUY' if delta > 0 else 'SELL'
    expires = now + timedelta(minutes=int(pol['approval_minutes']))
    it = _insert(db, sig, dep, target=target, current=current, want=want, outcome='PENDING', handoff='APPROVE', side=side, qty=abs(delta),
                 terms=order_terms(pol, side, q, _tick(db, dep)), stop=stop, take=take, q=q, note=note, expires_at=expires)
    db.execute('UPDATE tr_signals SET expires_at=GREATEST(expires_at, %s), updated_at=now() WHERE id=%s', (expires, str(sig['id'])))
    signals._event(db, sig['id'], 'VALID', f"waiting for execution: {side.lower()} {abs(delta).normalize()} until {expires.strftime('%H:%M UTC')}", 'execution')
    return {'state': 'PENDING', 'intent': store.clean(it)}


def record(db, sig, dep, *, target, current, want, stop, take, q, note) -> dict:
    from . import signals
    delta = want - current
    side = 'BUY' if delta > 0 else 'SELL'
    it = _insert(db, sig, dep, target=target, current=current, want=want, outcome='RECORDED', handoff='SIGNAL_ONLY', side=side, qty=abs(delta),
                 terms=order_terms(policy(dep), side, q, _tick(db, dep)), stop=stop, take=take, q=q, note=note, expires_at=None)
    signals._set(db, sig['id'], 'ACTED', 'execution', f'recorded only (signal-only deployment): would {side.lower()} {abs(delta).normalize()}')
    return {'state': 'RECORDED', 'intent': store.clean(it)}


def expire(db, now: datetime) -> int:
    from . import signals
    rows = db.execute("UPDATE tr_intents SET outcome='EXPIRED', decided_at=now(), decided_by='runtime', decision_note='approval window passed' "
                      "WHERE outcome='PENDING' AND expires_at <= %s RETURNING signal_id", (now,)).fetchall()
    for r in rows:
        if r['signal_id']:
            db.execute("UPDATE tr_signals SET state='EXPIRED', updated_at=now() WHERE id=%s AND state IN ('NEW','VALID')", (str(r['signal_id']),))
            signals._event(db, r['signal_id'], 'EXPIRED', 'not executed within the approval window', 'execution')
    return len(rows)


def _pending(db, intent_id) -> dict:
    it = db.execute('SELECT * FROM tr_intents WHERE id=%s FOR UPDATE', (str(intent_id),)).fetchone()
    if not it:
        raise HTTPException(404, 'Decision not found.')
    if it['outcome'] != 'PENDING':
        raise HTTPException(409, f"This decision is {it['outcome'].lower()}, not waiting for execution.")
    return dict(it)


def _now() -> datetime:
    """The time of a person's action (the only machine-clock entry here; a seam for tests)."""
    return datetime.now(timezone.utc)


def execute(actor: str, intent_id: UUID, data: ExecuteIn) -> dict:
    from . import oms, portfolio, risk, signals
    now = _now()
    with store.transaction('write') as db:
        it = _pending(db, intent_id)
        if it['expires_at'] and it['expires_at'] <= now:
            expire(db, now)
            raise HTTPException(409, 'The approval window of this decision has passed; it is now expired.')
        deps = deployments(db, None, it['deployment_id'])
        if not deps or deps[0]['status'] != 'ACTIVE':
            raise HTTPException(409, 'The deployment is not active: resume it (Strategies) or decline this decision.')
        dep = deps[0]
        sig = dict(db.execute('SELECT * FROM tr_signals WHERE id=%s', (str(it['signal_id']),)).fetchone())
        working = db.execute('SELECT client_order_id, state FROM oms_orders WHERE deployment_id=%s AND state = ANY(%s) LIMIT 1',
                             (str(dep['id']), list(oms.OPEN))).fetchone()
        if working:
            raise HTTPException(409, f"Order {working['client_order_id'][:12]}… of this deployment is {working['state'].lower()}: it settles first.")
        mode = dep.get('mode') or 'PAPER'
        current = portfolio.position_qty(db, dep['account_id'], dep['instrument_id'], dep['id'], mode)
        delta = D(it['target_qty']) - current
        if delta == 0:
            db.execute("UPDATE tr_intents SET outcome='NO_CHANGE', current_qty=%s, decided_by=%s, decided_at=now(), decision_note=%s WHERE id=%s",
                       (current, actor, 'the position is already at the target' + (f' · {data.note}' if data.note else ''), str(intent_id)))
            signals._set(db, sig['id'], 'ACTED', actor, 'position already at the target when executed')
            return {'state': 'NO_CHANGE'}
        side = 'BUY' if delta > 0 else 'SELL'
        qty = abs(delta)
        if data.quantity is not None:
            if data.quantity > qty:
                raise HTTPException(422, f'The quantity can be at most {qty.normalize()} (target − position now).')
            qty, problem = risk.lot(db, dep['instrument_id'], data.quantity)
            if problem:
                raise HTTPException(422, problem)
        src = data_source(db, dep)
        q = quote(db, dep['account_id'], dep['instrument_id'], src)
        pol = policy(dep)
        if data.order_type:
            pol = {**pol, 'order_type': data.order_type}
        otype, limit, tif = order_terms(pol, side, q, _tick(db, dep))
        if otype == 'LIMIT' and data.limit_price is not None:
            limit = data.limit_price
        order = oms.place(db, account_id=dep['account_id'], instrument_id=dep['instrument_id'], side=side, quantity=qty, order_type=otype, limit_price=limit,
                          time_in_force=tif, mode=mode, deployment=dep, intent_id=it['id'], seed=f"intent:{dep['id']}:{sig['id']}", actor=actor,
                          source='RUNTIME', at=now, data_source=src, stop_loss=it['stop_loss'], take_profit=it['take_profit'])
        refused = order['state'] == 'REJECTED'
        db.execute('UPDATE tr_intents SET outcome=%s, order_id=%s, current_qty=%s, quantity=%s, side=%s, order_type=%s, limit_price=%s, reasons=%s, '
                   'decided_by=%s, decided_at=now(), decision_note=%s WHERE id=%s',
                   ('REFUSED' if refused else 'ORDERED', str(order['id']), current, qty, side, otype, limit, json.dumps(order.get('reasons') or []), actor,
                    data.note or None, str(intent_id)))
        signals._set(db, sig['id'], 'REJECTED' if refused else 'ACTED', 'risk' if refused else actor,
                     ('; '.join(order.get('reasons') or [])) if refused else f"executed by {actor}: order {order['client_order_id']} {order['state'].lower()}",
                     order.get('reasons') if refused else None)
        store.audit(db, actor, 'EXECUTE_DECISION', 'intent', intent_id, store.clean(it), {'order_id': str(order['id']), 'state': order['state'],
                    'quantity': str(qty), 'order_type': otype, 'limit_price': str(limit) if limit else None}, data.note or 'executed from the execution queue')
    if mode == 'BROKER' and not refused:
        from . import execution
        execution.dispatch_soon()
    return {'state': 'REFUSED' if refused else 'ORDERED', 'order': store.clean(order)}


def decline(actor: str, intent_id: UUID, reason: str) -> dict:
    from . import signals
    with store.transaction('write') as db:
        it = _pending(db, intent_id)
        db.execute("UPDATE tr_intents SET outcome='DECLINED', decided_by=%s, decided_at=now(), decision_note=%s WHERE id=%s", (actor, reason, str(intent_id)))
        if it['signal_id']:
            signals._set(db, it['signal_id'], 'CANCELLED', actor, f'declined in execution: {reason}')
        store.audit(db, actor, 'DECLINE_DECISION', 'intent', intent_id, store.clean(it), {'outcome': 'DECLINED'}, reason)
    return {'state': 'DECLINED'}


FLOW_SELECT = """SELECT t.*, s.code AS strategy_code, v.number AS version_number, v.algorithm, i.symbol, a.code AS account_code, d.mode, d.execution,
    sg.side AS signal_side, sg.strength, sg.bar_time, sg.timeframe, sg.source AS signal_source, sg.explanation, sg.state AS signal_state,
    o.client_order_id, o.state AS order_state, o.filled_qty, o.avg_fill_price, o.created_at AS order_at,
    (SELECT f.slippage FROM oms_fills f WHERE f.order_id=o.id ORDER BY f.at LIMIT 1) AS slippage
    FROM tr_intents t LEFT JOIN cfg_deployments d ON d.id=t.deployment_id LEFT JOIN cfg_strategies s ON s.id=d.strategy_id
    LEFT JOIN cfg_strategy_versions v ON v.id=d.strategy_version_id JOIN cfg_instruments i ON i.id=t.instrument_id JOIN cfg_accounts a ON a.id=t.account_id
    LEFT JOIN tr_signals sg ON sg.id=t.signal_id LEFT JOIN oms_orders o ON o.id=t.order_id"""


def flow(db, outcome: str | None = None, limit: int = 200) -> list[dict]:
    where, args = '', []
    if outcome:
        where, args = ' WHERE t.outcome = ANY(%s)', [outcome.split(',')]
    rows = db.execute(FLOW_SELECT + where + ' ORDER BY (t.outcome=%s) DESC, t.created_at DESC LIMIT %s', [*args, 'PENDING', limit]).fetchall()
    return [store.clean(r) for r in rows]


def summary(db) -> dict:
    day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    by = {r['outcome']: r['n'] for r in db.execute('SELECT outcome, count(*) AS n FROM tr_intents WHERE created_at >= %s GROUP BY 1', (day,)).fetchall()}
    pend_ = db.execute("SELECT count(*) AS n, min(expires_at) AS next_expiry, min(created_at) AS oldest FROM tr_intents WHERE outcome='PENDING'").fetchone()
    hand = {r['h']: r['n'] for r in db.execute("SELECT COALESCE(execution->>'handoff','AUTO') AS h, count(*) AS n FROM cfg_deployments WHERE status='ACTIVE' GROUP BY 1").fetchall()}
    return {'today': by, 'pending': pend_['n'], 'next_expiry': pend_['next_expiry'], 'oldest_pending': pend_['oldest'], 'active_by_handoff': hand}


def policies(db) -> list[dict]:
    rows = db.execute("""SELECT d.id, d.status, d.mode, d.execution, d.version, s.code AS strategy_code, v.number AS version_number, v.algorithm, i.symbol,
                                a.code AS account_code, c.environment
                         FROM cfg_deployments d JOIN cfg_strategies s ON s.id=d.strategy_id JOIN cfg_strategy_versions v ON v.id=d.strategy_version_id
                         JOIN cfg_instruments i ON i.id=d.instrument_id JOIN cfg_accounts a ON a.id=d.account_id JOIN cfg_connections c ON c.id=a.connection_id
                         WHERE d.status <> 'ENDED' ORDER BY s.code, i.symbol""").fetchall()
    return [{**store.clean(r), 'policy': policy(r)} for r in rows]


def set_policy(actor: str, deployment_id: UUID, data: PolicyIn) -> dict:
    with store.transaction('write') as db:
        before = db.execute('SELECT * FROM cfg_deployments WHERE id=%s FOR UPDATE', (str(deployment_id),)).fetchone()
        if not before:
            raise HTTPException(404, 'Deployment not found.')
        if before['version'] != data.version:
            raise HTTPException(409, 'The deployment was changed by someone else: reload and try again.')
        new = data.model_dump(exclude={'version', 'reason'})
        after = db.execute('UPDATE cfg_deployments SET execution=%s, version=version+1, updated_at=now() WHERE id=%s RETURNING *',
                           (Jsonb(new), str(deployment_id))).fetchone()
        store.audit(db, actor, 'UPDATE_EXECUTION_POLICY', 'deployment', deployment_id, {'execution': before['execution']}, {'execution': new}, data.reason)
        return {**store.clean(after), 'policy': policy(after)}


