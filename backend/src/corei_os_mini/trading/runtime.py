"""Strategy runtime (Stage 4) and the trading cycle that ties Stages 3–7 together.

Why it exists: active deployments must be evaluated on every newly closed bar, their signals processed, working
orders matched, expired items closed, loss limits enforced and equity recorded — on a schedule, in order, and the
same way every time.
Purpose: cycle(): for each ACTIVE deployment, load its data source's closed bars for the strategy timeframe, compute
features (the same function the feature store uses), run its algorithm, and when the target changed on a bar not
evaluated before, create a signal and process it (validate → decide → risk → order); then expire signals, match
paper limit orders, measure signal quality, engage kill switches on breached loss limits and snapshot equity.
Responsibilities: tr_runtime_state (last bar per deployment), the cycle status shown on screens.
Must never: emit a signal for a bar already evaluated (one per deployment and bar), act on the first bar seen after
activation (it only records it), or run when Trading › Settings has the runtime paused.
Dependencies: intel.engine (bars and features), trading.algorithms, signals, oms, risk, portfolio.
Consumers: intel.engine.run_forever (after each feature refresh), trading_api (Run now), tests.
Architectural position: Stage 4 runtime, the scheduler of Stages 3–7.
"""
from __future__ import annotations

import logging
import time as _time
from datetime import datetime, timezone
from typing import Any

from psycopg.types.json import Jsonb

from .. import config_store as store
from ..intel import engine
from . import algorithms, handoff, oms, portfolio, risk, signals
from .common import D, data_source, deployments, settings

log = logging.getLogger(__name__)
STATUS: dict[str, Any] = {'last_run_at': None, 'duration_ms': None, 'deployments': 0, 'signals': 0, 'orders': 0, 'errors': []}


def series(db, source: tuple, timeframe: str) -> tuple[list[dict], list[dict]]:
    """Closed bars of the timeframe (the broker's own when it has enough, else built from M1) and their features."""
    account_id, symbol = source
    m1 = engine._load(db, account_id, symbol, 'M1')
    native = engine._load(db, account_id, symbol, timeframe) if timeframe != 'M1' else []
    built = engine.resample(m1, timeframe)
    bars = (native if native and len(native) >= len(built) else built)[-engine.WINDOW:]
    if not bars:
        return [], []
    custom = [d for d in engine.custom_definitions() if timeframe in (d.get('timeframes') or engine.TIMEFRAMES)]
    return bars, engine.features(bars, timeframe, custom)


def evaluate(db, dep: dict, now: datetime) -> dict:
    """One deployment on its latest closed bar."""
    tf = (dep['parameters'] or {}).get('timeframe', 'H1')
    src = data_source(db, dep)
    state = db.execute('SELECT * FROM tr_runtime_state WHERE deployment_id=%s FOR UPDATE', (str(dep['id']),)).fetchone()
    if not src:
        err = f"NO_DATA_SOURCE: nothing collects {dep['data_symbol']} on the data source's server (Market data › plans)"
        _state(db, dep, tf, None, state and state['last_bar_time'], state and state['last_target'], err)
        return {'error': err}
    bars, rows = series(db, src, tf)
    need = algorithms.warmup(dep['algorithm'], dep['parameters'] or {})
    if len(bars) < need + 2:
        err = f'WARMING_UP: {len(bars)} closed {tf} bars, {need + 2} needed (Market intelligence › Get history)'
        _state(db, dep, tf, src, state and state['last_bar_time'], state and state['last_target'], err)
        return {'error': err}
    closes = [(b['bar_time'], float(b['close'])) for b in bars]
    walk = algorithms.run(dep['algorithm'], dep['parameters'], bars, rows)
    last, bar_time = walk[-1], bars[-1]['bar_time']
    if state and state['last_bar_time'] and bar_time <= state['last_bar_time']:
        _state(db, dep, tf, src, state['last_bar_time'], state['last_target'], None)
        return {'skipped': 'no new bar', 'closes': closes}
    first = not state or state['last_bar_time'] is None
    _state(db, dep, tf, src, bar_time, last['target'], None)
    realign = None
    if not first and not last['change']:
        realign = realign_reason(db, dep, last['target'])
    if first or (not last['change'] and not realign):
        _evaluation(db, dep, src, tf, bars[-1], last, 'BASELINE' if first else 'TARGET_UNCHANGED')
        return {'evaluated': bar_time, 'signal': None, 'first': first, 'closes': closes}
    side = {'LONG': 'BUY', 'SHORT': 'SELL', 'FLAT': 'FLAT'}[last['target']]
    sig = signals.create(db, dep, side=side, strength=last['strength'], bar_time=bar_time, timeframe=tf, features=rows[-1],
                         bar_evidence=_bar_evidence(src, tf, bars[-1]),
                         explanation=[realign] if realign else [last['reason']], source='RUNTIME', actor='runtime', price=D(bars[-1]['close']))
    if not sig:
        _evaluation(db, dep, src, tf, bars[-1], last, 'SIGNAL_ALREADY_RECORDED')
        return {'evaluated': bar_time, 'signal': None}
    sig['realign'] = bool(realign)
    result = signals.process(db, sig, dep, now)
    _evaluation(db, dep, src, tf, bars[-1], last, 'SIGNAL_CREATED', sig['id'], result.get('note'))
    return {'evaluated': bar_time, 'signal': sig['id'], 'result': result.get('state'), 'closes': closes}


def realign_reason(db, dep: dict, target: str) -> str | None:
    """Retry a decision that was not executed: the deployment's latest signal asked for this target but its order was
    refused, rejected, cancelled or expired without a fill (or the signal itself was refused), the position is not at
    the target, and nothing is working. Never after an executed decision (a stop-out stays out until a new signal),
    and at most realign_max_attempts times in a row."""
    if dep.get('realign') is False or (dep.get('execution') or {}).get('handoff', 'AUTO') != 'AUTO':
        return None  # a decision waiting for a person, or recorded only, is never retried by itself
    last = db.execute('SELECT s.id, s.side, s.state, i.outcome, o.state AS order_state, o.filled_qty FROM tr_signals s '
                      'LEFT JOIN tr_intents i ON i.signal_id=s.id LEFT JOIN oms_orders o ON o.id=i.order_id '
                      "WHERE s.deployment_id=%s AND s.source='RUNTIME' ORDER BY s.created_at DESC LIMIT 1", (str(dep['id']),)).fetchone()
    if not last or {'BUY': 'LONG', 'SELL': 'SHORT', 'FLAT': 'FLAT'}[last['side']] != target:
        return None
    executed = last['order_state'] in ('FILLED', 'PARTIALLY_FILLED') or (last['filled_qty'] and D(last['filled_qty']) > 0) or last['outcome'] == 'NO_CHANGE'
    unexecuted = last['state'] in ('REJECTED', 'EXPIRED') or last['outcome'] == 'REFUSED' or last['order_state'] in ('REJECTED', 'CANCELLED', 'EXPIRED')
    if executed or not unexecuted:
        return None
    if db.execute('SELECT 1 FROM oms_orders WHERE deployment_id=%s AND state = ANY(%s)', (str(dep['id']), list(oms.OPEN))).fetchone():
        return None
    qty = portfolio.position_qty(db, dep['account_id'], dep['instrument_id'], dep['id'], dep.get('mode') or 'PAPER')
    if (target == 'LONG' and qty > 0) or (target == 'SHORT' and qty < 0) or (target == 'FLAT' and qty == 0):
        return None
    limit = int(settings(db).get('realign_max_attempts') or 0)
    streak = 0
    for r in db.execute('SELECT s.explanation, s.state, i.outcome, o.state AS order_state FROM tr_signals s LEFT JOIN tr_intents i ON i.signal_id=s.id '
                        "LEFT JOIN oms_orders o ON o.id=i.order_id WHERE s.deployment_id=%s AND s.source='RUNTIME' ORDER BY s.created_at DESC LIMIT 60",
                        (str(dep['id']),)).fetchall():
        if not str((r['explanation'] or [''])[0]).startswith('REALIGN'):
            break
        streak += 1
    if streak >= limit:
        return None
    return (f"REALIGN: the {target.lower()} decision of an earlier bar was not executed ({(last['order_state'] or last['outcome'] or last['state']).lower()}); "
            f"position {qty.normalize()} — attempt {streak + 1} of {limit}")


def _bar_evidence(source, timeframe, bar):
    return {
        'source_account': str(source[0]), 'source_symbol': source[1], 'timeframe': timeframe,
        'bar_time': bar['bar_time'].isoformat(),
        'ohlc': {key: str(bar[key]) for key in ('open', 'high', 'low', 'close') if bar.get(key) is not None},
    }


def _evaluation(db, dep, source, timeframe, bar, rule, outcome, signal_id=None, note=None):
    evidence = {
        **_bar_evidence(source, timeframe, bar), 'strategy_version_id': str(dep['strategy_version_id']),
        'target': rule['target'], 'reason': rule.get('reason'), 'outcome': outcome, 'note': note,
        'signal_id': str(signal_id) if signal_id else None,
    }
    db.execute('UPDATE tr_runtime_state SET last_evaluation=%s WHERE deployment_id=%s',
               (Jsonb(evidence), str(dep['id'])))


def _state(db, dep, tf, src, bar_time, target, error):
    db.execute("""INSERT INTO tr_runtime_state(deployment_id, timeframe, source_account, source_symbol, last_bar_time, last_target, last_run_at, last_error)
                  VALUES (%s,%s,%s,%s,%s,%s,now(),%s)
                  ON CONFLICT (deployment_id) DO UPDATE SET timeframe=EXCLUDED.timeframe, source_account=EXCLUDED.source_account,
                      source_symbol=EXCLUDED.source_symbol, last_bar_time=EXCLUDED.last_bar_time, last_target=EXCLUDED.last_target,
                      last_run_at=now(), last_error=EXCLUDED.last_error""",
               (str(dep['id']), tf, src and src[0], src and src[1], bar_time, target, error))


def cycle(now: datetime | None = None, force: bool = False) -> dict:
    """One trading cycle; returns what it did. Each deployment runs in its own transaction (one failure stays local)."""
    started = _time.monotonic()
    now = now or datetime.now(timezone.utc)
    errors: list[str] = []
    out = {'deployments': 0, 'signals': 0, 'orders': 0, 'expired': 0, 'matched': {}, 'kill_switches': 0, 'quality': 0}
    with store.transaction('read') as db:
        cfg = settings(db)
        deps = deployments(db, 'ACTIVE')
    if not cfg.get('runtime_enabled') and not force:
        STATUS.update({'last_run_at': now.isoformat(), 'paused': True})
        return {**out, 'paused': True}
    closes: dict = {}
    for dep in deps:
        try:
            with store.transaction('write') as db:
                r = evaluate(db, dep, now)
                out['deployments'] += 1
                if r.get('signal'):
                    out['signals'] += 1
                    if r.get('result') == 'ACTED':
                        out['orders'] += 1
                if r.get('error'):
                    errors.append(f"{dep['strategy_code']} {dep['instrument_symbol']} {dep['account_code']}: {r['error']}")
                if r.get('closes'):
                    closes[str(dep['id'])] = r['closes']
        except Exception as exc:  # noqa: BLE001 - one deployment's failure never stops the others
            log.exception('Runtime failed for deployment %s', dep['id'])
            errors.append(f"{dep['strategy_code']} {dep['instrument_symbol']}: {type(exc).__name__}: {str(getattr(exc, 'detail', exc))[:200]}")
    try:
        with store.transaction('write') as db:
            out['expired_decisions'] = handoff.expire(db, now)
            out['expired'] = signals.expire(db, now)
            out['matched'] = oms.match(db, now)
            out['protected'] = oms.protect(db, now)
            out['quality'] = signals.evaluate_quality(db, now, closes)
            out['kill_switches'] = len(risk.auto_stop(db, now))
            portfolio.snapshot(db, now)
    except Exception as exc:  # noqa: BLE001
        log.exception('Trading housekeeping failed')
        errors.append(f'housekeeping: {type(exc).__name__}: {exc}'[:300])
    try:
        from . import algos
        a = algos.tick(now)   # execution algorithms: each working parent sends its next child (own transactions)
        out['algo_children'] = a['children']
        errors.extend(f'algo {e}' for e in a['errors'])
    except Exception as exc:  # noqa: BLE001
        log.exception('Execution algorithms failed')
        errors.append(f'algos: {type(exc).__name__}: {exc}'[:300])
    if out['orders'] and any(d.get('mode') == 'BROKER' for d in deps):
        from . import execution
        execution.dispatch_soon()
    try:
        from . import strategy_health
        with store.transaction('write') as db:
            out['drift'] = strategy_health.drift_all(db, now)
    except Exception as exc:  # noqa: BLE001
        log.exception('Drift check failed')
        errors.append(f'drift: {type(exc).__name__}: {exc}'[:300])
    STATUS.update({'last_run_at': now.isoformat(), 'duration_ms': round((_time.monotonic() - started) * 1000), 'errors': errors[:20],
                   'paused': False, **{k: out[k] for k in ('deployments', 'signals', 'orders')}})
    return {**out, 'errors': errors}


def state(db) -> list[dict]:
    rows = db.execute("""SELECT r.*, s.code AS strategy_code, v.number AS version_number, v.algorithm, i.symbol, a.code AS account_code, d.mode, d.status
                         FROM tr_runtime_state r JOIN cfg_deployments d ON d.id=r.deployment_id JOIN cfg_strategies s ON s.id=d.strategy_id
                         JOIN cfg_strategy_versions v ON v.id=d.strategy_version_id JOIN cfg_instruments i ON i.id=d.instrument_id
                         JOIN cfg_accounts a ON a.id=d.account_id ORDER BY s.code, i.symbol""").fetchall()
    return [store.clean(r) for r in rows]
