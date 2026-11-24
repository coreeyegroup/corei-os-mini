"""Strategy signals and decisions (Stage 4 · the output of a running strategy).

Why it exists: a strategy's output must be recorded, checked and turned into exactly one decision per deployment,
with the reason for every refusal visible, before anything is sized or sent.
Purpose: create a signal (from the runtime on a closed bar, or by hand), validate it (deployment active, live and
fresh price, spread against ATR, market open, event risk, duplicate, minimum strength), decide the deployment's
target (latest signal, or N agreeing signals), compare with the position held, and pass the order needed to risk and
execution; expire and cancel signals; measure each signal's forward return (signal quality).
Responsibilities: tr_signals, tr_signal_events, tr_intents. Signal states: NEW → VALID → ACTED, or REJECTED / EXPIRED
/ CANCELLED.
Must never: size or send an order itself (risk sizes, execution sends), or skip validation for a manual signal.
Dependencies: trading.common, trading.risk, trading.oms, trading.portfolio, md.markets, intel.workspace.
Consumers: trading.runtime, trading_api.
Architectural position: the output side of Stage 4 (a running strategy's signal and its decision), before risk (Stage
5) and execution (Stage 7). How the decision reaches execution is the deployment's execution policy (trading.handoff):
AUTO places the order now, APPROVE leaves it waiting in the execution queue, SIGNAL_ONLY records it. Manual signals are
an operator's decision and are always placed.
Note on time: a signal is stamped with its bar's close time (event time); the freshness of the live price is
measured against the time of the decision, which is the only place the machine clock enters a decision.
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
from ..md import markets
from . import handoff, oms, portfolio, risk
from .common import D, TIMEFRAME_SECONDS, ZERO, data_source, deployments, instrument, quote, settings

SIDE_TARGET = {'BUY': 'LONG', 'SELL': 'SHORT', 'FLAT': 'FLAT'}


def _event(db, signal_id, state: str, detail: str, actor: str) -> None:
    db.execute('INSERT INTO tr_signal_events(signal_id, state, detail, actor) VALUES (%s,%s,%s,%s)', (str(signal_id), state, detail[:1000], actor))


def _set(db, signal_id, state: str, actor: str, detail: str = '', reasons: list[str] | None = None) -> None:
    db.execute('UPDATE tr_signals SET state=%s, reasons=COALESCE(%s, reasons), updated_at=now() WHERE id=%s',
               (state, json.dumps(reasons) if reasons is not None else None, str(signal_id)))
    _event(db, signal_id, state, detail, actor)


def create(db, dep: dict, *, side: str, strength: int, bar_time: datetime, timeframe: str, features: dict, explanation: list[str],
           source: str, actor: str, price: Decimal | None, bar_evidence: dict | None = None) -> dict | None:
    """Record a signal (once per deployment and bar for the runtime). Returns None when that bar already has one."""
    expiry = bar_time + timedelta(seconds=TIMEFRAME_SECONDS.get(timeframe, 60) * int(dep.get('signal_expiry_bars') or 2))
    stored_features = {k: v for k, v in features.items() if isinstance(v, (int, float)) and k in KEEP}
    if bar_evidence is not None:
        stored_features['_workspace_bar'] = bar_evidence
    row = db.execute("""INSERT INTO tr_signals(id, deployment_id, strategy_id, strategy_version_id, account_id, instrument_id, timeframe, bar_time, side, strength,
                                horizon_bars, expires_at, source, features, explanation, price, created_by)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT DO NOTHING RETURNING *""",
                     (uuid4(), str(dep['id']), str(dep['strategy_id']), str(dep['strategy_version_id']), str(dep['account_id']), str(dep['instrument_id']),
                      timeframe, bar_time, side, max(0, min(100, int(strength))), 10, expiry, source,
                      json.dumps(stored_features),
                      json.dumps(explanation), price, actor)).fetchone()
    if not row:
        return None
    _event(db, row['id'], 'NEW', '; '.join(explanation)[:1000], actor)
    return dict(row)


KEEP = {'close', 'atr14', 'atr_pct', 'rsi14', 'adx14', 'ema20', 'ema50', 'ema200', 'macd_hist', 'zscore20', 'rel_volume20', 'bb_width_pct', 'er20'}


def validate(db, sig: dict, dep: dict, now: datetime, cfg: dict, q: dict | None) -> list[str]:
    reasons: list[str] = []
    if dep['status'] != 'ACTIVE':
        reasons.append(f"DEPLOYMENT_NOT_ACTIVE: the deployment is {dep['status'].lower()}")
    if sig['expires_at'] <= now:
        reasons.append('EXPIRED_ON_ARRIVAL: the signal expired before it was checked')
    if q is None:
        reasons.append('NO_PRICE: no live price for this instrument')
    else:
        age = (now - q['received_at']).total_seconds()
        if age > int(cfg['max_quote_age_seconds']):
            reasons.append(f"STALE_PRICE: the last price is {age:.0f} s old (limit {cfg['max_quote_age_seconds']} s)")
        atr = (sig.get('features') or {}).get('atr14')
        if atr:
            spread_pct = float(q['ask'] - q['bid']) / atr * 100
            if spread_pct > float(cfg['max_spread_atr_pct']):
                reasons.append(f"SPREAD_TOO_WIDE: spread is {spread_pct:.0f}% of ATR (limit {D(cfg['max_spread_atr_pct']).normalize()}%)")
    market = markets.market_for(dep.get('instrument_market'), q['broker_symbol'] if q else '', dep.get('platform') or '', dep.get('asset_class') or '')
    if not markets.is_open(market, now):
        reasons.append(f'MARKET_CLOSED: {market} is closed')
    if cfg.get('block_on_event'):
        from ..intel import workspace
        try:
            window = 30
            near = workspace.event_risk(dep['instrument_symbol'], dep.get('base_currency'), dep.get('quote_currency'), now, window,
                                        workspace.events(now - timedelta(minutes=window), now + timedelta(minutes=window)))
        except Exception:  # noqa: BLE001 - no calendar yet
            near = []
        if near:
            reasons.append('EVENT_RISK: ' + ', '.join(f"{e['currency']} {e['title']}" for e in near)[:200])
    if sig['strength'] < int(cfg.get('min_strength') or 0):
        reasons.append(f"WEAK_SIGNAL: strength {sig['strength']} below the minimum {cfg['min_strength']}")
    dup = db.execute("SELECT 1 FROM tr_signals WHERE deployment_id=%s AND id<>%s AND side=%s AND state='VALID' AND expires_at > %s",
                     (str(dep['id']), str(sig['id']), sig['side'], now)).fetchone()
    if dup:
        reasons.append('DUPLICATE: the same signal is already live for this deployment')
    return reasons


def decide(db, sig: dict, dep: dict, cfg: dict) -> tuple[str | None, str]:
    """The deployment's target position from its valid signals."""
    target = SIDE_TARGET[sig['side']]
    if cfg.get('decision_mode') == 'AGREE' and target != 'FLAT':
        n = int(cfg.get('agree_count') or 2)
        recent = db.execute("SELECT side FROM tr_signals WHERE deployment_id=%s AND state IN ('VALID','ACTED') AND side<>'FLAT' ORDER BY bar_time DESC LIMIT %s",
                            (str(dep['id']), n)).fetchall()
        if len(recent) < n or any(r['side'] != sig['side'] for r in recent):
            return None, f'waiting for {n} agreeing signals'
    return target, 'latest signal' if cfg.get('decision_mode') != 'AGREE' else 'signals agree'


def process(db, sig: dict, dep: dict, now: datetime, actor: str = 'runtime') -> dict:
    """Validate → decide → order (sized and checked by risk, executed by the order engine)."""
    cfg = settings(db)
    src = data_source(db, dep)
    q = quote(db, dep['account_id'], dep['instrument_id'], src)
    problems = validate(db, sig, dep, now, cfg, q)
    if problems:
        _set(db, sig['id'], 'REJECTED', 'validation', '; '.join(problems), problems)
        return {'state': 'REJECTED', 'reasons': problems}
    _set(db, sig['id'], 'VALID', 'validation', 'all checks passed', [])
    target, why = decide(db, sig, dep, cfg)
    if target is None:
        return {'state': 'VALID', 'note': why}
    current = portfolio.position_qty(db, dep['account_id'], dep['instrument_id'], dep['id'], dep.get('mode') or 'PAPER')
    # An earlier order of this deployment still working (or with an unknown outcome) settles first: deciding from the
    # position alone could double it.
    working = db.execute("SELECT client_order_id, state FROM oms_orders WHERE deployment_id=%s AND state = ANY(%s) ORDER BY created_at DESC LIMIT 1",
                         (str(dep['id']), list(oms.OPEN))).fetchone()
    if not working:
        algo = db.execute("SELECT algo, state FROM ex_parent_orders WHERE deployment_id=%s AND state IN ('WORKING','PAUSED') LIMIT 1", (str(dep['id']),)).fetchone()
        if algo:
            return _intent(db, sig, dep, target, current, None, 'REFUSED',
                           [f"WORKING_ORDER: a {algo['algo']} execution of this deployment is {algo['state'].lower()}; the next decision waits for it"])
    if working:
        why = (f"WORKING_ORDER: order {working['client_order_id'][:12]}… is {working['state'].lower().replace('_', ' ')}"
               + ('; its outcome at the broker is being looked up' if working['state'] == 'UNKNOWN' else '; the next decision waits for it'))
        return _intent(db, sig, dep, target, current, None, 'REFUSED', [why])
    want, note = ZERO, ''
    if target != 'FLAT':
        if (target == 'LONG' and current > 0) or (target == 'SHORT' and current < 0):
            want = current  # already on that side: keep the position (no pyramiding)
        else:
            raw, note = risk.size(db, dep, q['mid'], (sig.get('features') or {}).get('atr14'))
            if raw is None:
                return _intent(db, sig, dep, target, current, None, 'REFUSED', [note])
            qty, problem = risk.lot(db, dep['instrument_id'], raw * Decimal(dep.get('allocation_weight_bps') or 10000) / 10000)
            if problem:
                return _intent(db, sig, dep, target, current, qty, 'REFUSED', [problem])
            want = qty if target == 'LONG' else -qty
    delta = want - current
    if delta == 0:
        return _intent(db, sig, dep, target, current, want, 'NO_CHANGE', [])
    intent_id = uuid4()
    stop_loss, take_profit = protective_levels(dep, target, want, current, q, (sig.get('features') or {}).get('atr14'))
    pol = handoff.policy(dep)
    if sig['source'] == 'RUNTIME' and pol['handoff'] == 'APPROVE':
        return handoff.pend(db, sig, dep, target=target, current=current, want=want, stop=stop_loss, take=take_profit, q=q, note=note, now=now)
    if sig['source'] == 'RUNTIME' and pol['handoff'] == 'SIGNAL_ONLY':
        return handoff.record(db, sig, dep, target=target, current=current, want=want, stop=stop_loss, take=take_profit, q=q, note=note)
    side = 'BUY' if delta > 0 else 'SELL'
    otype, limit, tif = handoff.order_terms(pol, side, q, D(instrument(db, dep['instrument_id']).get('tick_size')) or Decimal('0.00001'))
    order = oms.place(db, account_id=dep['account_id'], instrument_id=dep['instrument_id'], side=side, quantity=abs(delta), order_type=otype, limit_price=limit,
                      time_in_force=tif, mode=dep.get('mode') or 'PAPER', deployment=dep, intent_id=intent_id, seed=f"intent:{dep['id']}:{sig['id']}", actor=actor,
                      source=('REALIGN' if sig.get('realign') else 'RUNTIME') if sig['source'] == 'RUNTIME' else 'MANUAL', at=now, data_source=src,
                      stop_loss=stop_loss, take_profit=take_profit)
    refused = order['state'] == 'REJECTED'
    _intent(db, sig, dep, target, current, want, 'REFUSED' if refused else 'ORDERED', order.get('reasons') or [], intent_id=intent_id, order_id=order['id'])
    db.execute('UPDATE tr_intents SET side=%s, quantity=%s, order_type=%s, limit_price=%s, time_in_force=%s, stop_loss=%s, take_profit=%s, decision_bid=%s, '
               'decision_ask=%s, sizing_note=%s, decided_by=%s, decided_at=now() WHERE id=%s',
               (side, abs(delta), otype, limit, tif, stop_loss, take_profit, q and q['bid'], q and q['ask'], note or None, actor, str(intent_id)))
    _set(db, sig['id'], 'REJECTED' if refused else 'ACTED', 'risk' if refused else 'execution',
         ('; '.join(order.get('reasons') or [])) if refused else f"order {order['client_order_id']} {order['state'].lower()} ({note or 'kept size'})",
         order.get('reasons') if refused else None)
    return {'state': 'REJECTED' if refused else 'ACTED', 'order': order}


def protective_levels(dep: dict, target: str, want: Decimal, current: Decimal, q: dict | None, atr) -> tuple[Decimal | None, Decimal | None]:
    """Stop-loss and take-profit for an order that opens (or reverses) a position: entry ∓ ATR × the deployment's
    multiples (a RISK_PCT size without its own stop uses its atr_multiple, so the sized-for stop is the placed stop)."""
    if target == 'FLAT' or q is None or not atr or (want > 0) == (current > 0) and current != 0:
        return None, None
    p = dep.get('protect') or {}
    sizing = dep.get('sizing') or {}
    stop_atr = p.get('stop_atr') or (sizing.get('atr_multiple') if sizing.get('method') == 'RISK_PCT' else None)
    take_atr = p.get('take_atr')
    a = Decimal(str(atr))
    long = want > 0
    entry = q['ask'] if long else q['bid']
    stop = (entry - a * Decimal(str(stop_atr)) if long else entry + a * Decimal(str(stop_atr))) if stop_atr else None
    take = (entry + a * Decimal(str(take_atr)) if long else entry - a * Decimal(str(take_atr))) if take_atr else None
    return (stop if stop and stop > 0 else None), (take if take and take > 0 else None)


def _intent(db, sig, dep, target, current, want, outcome, reasons, intent_id=None, order_id=None) -> dict:
    db.execute('INSERT INTO tr_intents(id, signal_id, deployment_id, account_id, instrument_id, target, current_qty, target_qty, outcome, reasons, order_id) '
               'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
               (intent_id or uuid4(), str(sig['id']), str(dep['id']), str(dep['account_id']), str(dep['instrument_id']), target, current, want, outcome,
                json.dumps(reasons), str(order_id) if order_id else None))
    if outcome == 'REFUSED' and not order_id:
        _set(db, sig['id'], 'REJECTED', 'risk', '; '.join(reasons), reasons)
    elif outcome == 'NO_CHANGE':
        _set(db, sig['id'], 'ACTED', 'decision', f'position already {target.lower()}')
    return {'state': outcome, 'reasons': reasons}


def expire(db, now: datetime) -> int:
    rows = db.execute("UPDATE tr_signals SET state='EXPIRED', updated_at=now() WHERE state IN ('NEW','VALID') AND expires_at <= %s RETURNING id", (now,)).fetchall()
    for r in rows:
        _event(db, r['id'], 'EXPIRED', 'expiry passed', 'runtime')
    return len(rows)


class ManualIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    deployment_id: UUID
    side: Literal['BUY', 'SELL', 'FLAT']
    strength: int = Field(60, ge=0, le=100)
    reason: str = Field(min_length=5, max_length=500)


def manual(actor: str, data: ManualIn) -> dict:
    """An operator's signal on a deployment: same validation, decision, risk and execution as the runtime's."""
    with store.transaction('write') as db:
        deps = deployments(db, None, data.deployment_id)
        if not deps:
            raise HTTPException(404, 'Deployment not found.')
        dep = deps[0]
        now = datetime.now(timezone.utc)
        snap = db.execute('SELECT features FROM intel_snapshot s WHERE s.account_id=%s AND s.broker_symbol=%s AND s.timeframe=%s',
                          (*(data_source(db, dep) or (None, None)), dep['parameters'].get('timeframe', 'H1'))).fetchone()
        sig = create(db, dep, side=data.side, strength=data.strength, bar_time=now, timeframe=dep['parameters'].get('timeframe', 'H1'),
                     features=(snap and snap['features']) or {}, explanation=[f'Manual: {data.reason}'], source='MANUAL', actor=actor, price=None)
        store.audit(db, actor, 'MANUAL_SIGNAL', 'signal', sig['id'], None, store.clean(sig), data.reason)
        result = process(db, sig, dep, now, actor)
        return {'signal': store.clean(db.execute('SELECT * FROM tr_signals WHERE id=%s', (sig['id'],)).fetchone()), 'result': store.clean(result) if isinstance(result, dict) else result}


def cancel(actor: str, signal_id: UUID, reason: str) -> dict:
    with store.transaction('write') as db:
        s = db.execute('SELECT * FROM tr_signals WHERE id=%s FOR UPDATE', (signal_id,)).fetchone()
        if not s:
            raise HTTPException(404, 'Signal not found.')
        if s['state'] not in ('NEW', 'VALID'):
            raise HTTPException(409, f"A {s['state'].lower()} signal cannot be cancelled.")
        _set(db, signal_id, 'CANCELLED', actor, reason)
        store.audit(db, actor, 'CANCEL_SIGNAL', 'signal', signal_id, store.clean(s), {'state': 'CANCELLED'}, reason)
        return store.clean(db.execute('SELECT * FROM tr_signals WHERE id=%s', (signal_id,)).fetchone())


def evaluate_quality(db, now: datetime, closes: dict) -> int:
    """Forward return of each signal at its horizon, signed by its side (+ = the market went the signal's way).
    `closes` maps (deployment_id) → [(bar_time, close)] of its strategy timeframe, from the runtime."""
    n = 0
    rows = db.execute("SELECT id, deployment_id, bar_time, side, horizon_bars, timeframe, price FROM tr_signals WHERE forward_return_pct IS NULL "
                      "AND side <> 'FLAT' AND deployment_id IS NOT NULL AND bar_time < %s ORDER BY bar_time LIMIT 500", (now,)).fetchall()
    for r in rows:
        series = closes.get(str(r['deployment_id']))
        if not series:
            continue
        idx = next((i for i, (t, _) in enumerate(series) if t == r['bar_time']), None)
        if idx is None or idx + r['horizon_bars'] >= len(series):
            continue
        start, end = series[idx][1], series[idx + r['horizon_bars']][1]
        if not start:
            continue
        ret = (end / start - 1) * 100 * (1 if r['side'] == 'BUY' else -1)
        db.execute('UPDATE tr_signals SET forward_return_pct=%s, evaluated_at=now() WHERE id=%s', (round(ret, 6), r['id']))
        n += 1
    return n


def quality(db) -> list[dict]:
    rows = db.execute("""SELECT s.strategy_id, st.code AS strategy, i.symbol, s.timeframe, count(*) AS signals,
                                count(*) FILTER (WHERE s.forward_return_pct IS NOT NULL) AS measured,
                                count(*) FILTER (WHERE s.forward_return_pct > 0) AS right,
                                avg(s.forward_return_pct) AS avg_return_pct,
                                count(*) FILTER (WHERE s.state='REJECTED') AS rejected, count(*) FILTER (WHERE s.state='ACTED') AS acted
                         FROM tr_signals s JOIN cfg_strategies st ON st.id=s.strategy_id JOIN cfg_instruments i ON i.id=s.instrument_id
                         GROUP BY 1, 2, 3, 4 ORDER BY 2, 3""").fetchall()
    out = []
    for r in rows:
        r = store.clean(r)
        r['hit_rate_pct'] = round(100 * r['right'] / r['measured'], 1) if r['measured'] else None
        out.append(r)
    return out


def listing(db, state: str | None = None, deployment_id=None, limit: int = 300) -> list[dict]:
    where, args = ['TRUE'], []
    if state:
        where.append('s.state=%s'); args.append(state)
    if deployment_id:
        where.append('s.deployment_id=%s'); args.append(str(deployment_id))
    rows = db.execute(f"""SELECT s.*, st.code AS strategy_code, v.number AS version_number, i.symbol, a.code AS account_code, d.mode
                          FROM tr_signals s LEFT JOIN cfg_strategies st ON st.id=s.strategy_id LEFT JOIN cfg_strategy_versions v ON v.id=s.strategy_version_id
                          JOIN cfg_instruments i ON i.id=s.instrument_id JOIN cfg_accounts a ON a.id=s.account_id LEFT JOIN cfg_deployments d ON d.id=s.deployment_id
                          WHERE {' AND '.join(where)} ORDER BY s.created_at DESC LIMIT %s""", [*args, limit]).fetchall()
    return [store.clean(r) for r in rows]


def history(db, signal_id: UUID) -> dict:
    s = db.execute('SELECT * FROM tr_signals WHERE id=%s', (signal_id,)).fetchone()
    if not s:
        raise HTTPException(404, 'Signal not found.')
    ev = db.execute('SELECT * FROM tr_signal_events WHERE signal_id=%s ORDER BY id', (signal_id,)).fetchall()
    intent = db.execute('SELECT * FROM tr_intents WHERE signal_id=%s ORDER BY created_at DESC LIMIT 1', (signal_id,)).fetchone()
    return {**store.clean(s), 'events': [store.clean(e) for e in ev], 'intent': store.clean(intent)}


def instrument_name(db, instrument_id) -> str:
    return instrument(db, instrument_id).get('symbol', '?')
