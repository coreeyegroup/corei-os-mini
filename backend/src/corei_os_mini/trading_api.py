"""HTTP API for the trading core, Stages 3–7 (signals, strategy runtime and backtests, risk, portfolio, orders).

Why it exists: the stage screens and the system map read and control the trading core through one place.
Purpose: /api/trading/* — overview KPIs per stage; settings; runtime state and Run now; signals (list, detail,
manual, cancel, quality); intents; backtests; risk limits, kill switches, decisions and a pre-trade preview;
orders (blotter, detail, ticket, cancel, resolve UNKNOWN); fills; positions, exposure, equity, capital, performance.
Roles: signed-in operators read; Trading operations places tickets, manual signals, flattens, cancels and engages
kill switches; Risk approver changes limits, releases kill switches, resolves UNKNOWN orders and changes settings.
Must never: bypass trading.risk or trading.oms, or return a secret.
Dependencies: trading.*, operator_auth, config_store. Consumers: the workstation UI.
Architectural position: API layer over Stages 3–7.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from . import config_store as store
from .config_api import parse
from .operator_auth import Principal, require_operator, require_role
from .trading import algorithms, backtest, oms, portfolio, risk, runtime, signals
from .trading.common import settings as read_settings

router = APIRouter(prefix='/api/trading', tags=['trading'])
_ops = require_role('TRADING_OPS')
_risk = require_role('RISK_APPROVER')
_ops_or_risk = require_role('TRADING_OPS', 'RISK_APPROVER')


def _no_store(response: Response) -> None:
    response.headers['Cache-Control'] = 'no-store'


def _j(v):
    """JSON-safe: Decimals as strings (exact), datetimes as ISO text."""
    if isinstance(v, Decimal):
        return format(v.normalize(), 'f')  # exact, without trailing zeros or exponent
    if isinstance(v, dict):
        return {k: _j(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_j(x) for x in v]
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, UUID):
        return str(v)
    return v


# ----- overview: one line per stage for the system map and each stage's header -----

@router.get('/overview')
def overview(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    with store.transaction('read') as db:
        dep = {r['status']: r['n'] for r in db.execute('SELECT status, count(*) AS n FROM cfg_deployments GROUP BY 1').fetchall()}
        modes = {r['mode']: r['n'] for r in db.execute("SELECT mode, count(*) AS n FROM cfg_deployments WHERE status='ACTIVE' GROUP BY 1").fetchall()}
        sig = {r['state']: r['n'] for r in db.execute('SELECT state, count(*) AS n FROM tr_signals WHERE created_at >= %s GROUP BY 1', (day,)).fetchall()}
        orders = {r['state']: r['n'] for r in db.execute('SELECT state, count(*) AS n FROM oms_orders WHERE created_at >= %s GROUP BY 1', (day,)).fetchall()}
        open_orders = db.execute('SELECT count(*) AS n FROM oms_orders WHERE state = ANY(%s)', (list(oms.OPEN),)).fetchone()['n']
        unknown = db.execute("SELECT count(*) AS n FROM oms_orders WHERE state='UNKNOWN'").fetchone()['n']
        kills = db.execute('SELECT count(*) AS n FROM risk_kill_switches WHERE engaged').fetchone()['n']
        limits_n = db.execute('SELECT count(*) AS n FROM risk_limits WHERE enabled').fetchone()['n']
        refused = db.execute('SELECT count(*) AS n FROM risk_decisions WHERE NOT approved AND at >= %s', (day,)).fetchone()['n']
        pos = portfolio.positions(db)
        accounts = sorted({str(p['account_id']) for p in pos} | {str(r['account_id']) for r in db.execute('SELECT DISTINCT account_id FROM oms_fills WHERE at >= %s', (day,)).fetchall()})
        pnl = [{'account_id': a, **_j({k: v for k, v in portfolio.equity(db, a).items() if k in ('currency', 'equity', 'drawdown_pct')}),
                'today': _j(portfolio.day_pnl(db, a))} for a in accounts]
        backtests = db.execute('SELECT count(*) AS n FROM tr_backtests').fetchone()['n']
        cfg = read_settings(db)
        errors = db.execute('SELECT count(*) AS n FROM tr_runtime_state r JOIN cfg_deployments d ON d.id=r.deployment_id WHERE d.status=%s AND r.last_error IS NOT NULL',
                            ('ACTIVE',)).fetchone()['n']
    return {'deployments': dep, 'active_modes': modes, 'signals_today': sig, 'orders_today': orders, 'open_orders': open_orders, 'unknown_orders': unknown,
            'kill_switches': kills, 'limits': limits_n, 'refused_today': refused, 'positions': len(pos), 'accounts': pnl, 'backtests': backtests,
            'runtime': {**runtime.STATUS, 'enabled': cfg.get('runtime_enabled'), 'deployments_with_problems': errors}}


# ----- settings -----

class SettingsIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    runtime_enabled: bool
    max_quote_age_seconds: int = Field(ge=1, le=86400)
    max_spread_atr_pct: Decimal = Field(gt=0, le=1000)
    block_on_event: bool
    min_strength: int = Field(ge=0, le=100)
    decision_mode: Literal['LATEST', 'AGREE']
    agree_count: int = Field(ge=2, le=10)
    paper_slippage_ticks: int = Field(ge=0, le=1000)
    default_capital: Decimal = Field(gt=0, max_digits=20, decimal_places=2)
    version: int
    reason: str = Field(min_length=5, max_length=500)


@router.get('/settings')
def get_settings(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return _j(read_settings())


@router.put('/settings')
def put_settings(payload: dict, user: Principal = Depends(_risk)):
    data = parse(SettingsIn, payload)
    with store.transaction('write') as db:
        before = db.execute('SELECT * FROM tr_settings WHERE id FOR UPDATE').fetchone()
        if before['version'] != data.version:
            raise HTTPException(409, 'The settings were changed by someone else: reload and try again.')
        vals = data.model_dump(exclude={'version', 'reason'})
        after = db.execute('UPDATE tr_settings SET ' + ', '.join(f'{k}=%s' for k in vals) + ', version=version+1, updated_by=%s, updated_at=now() WHERE id RETURNING *',
                           (*vals.values(), user.username)).fetchone()
        store.audit(db, user.username, 'UPDATE_TRADING_SETTINGS', 'trading_settings', 'singleton', dict(before), dict(after), data.reason)
        return _j(dict(after))


# ----- Stage 4: runtime, algorithms, backtests -----

@router.get('/runtime')
def runtime_state(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': _j(runtime.state(db)), 'status': _j(runtime.STATUS)}


@router.post('/runtime/run')
def run_now(user: Principal = Depends(_ops_or_risk)):
    """One trading cycle now (also when the runtime is paused: an explicit operator action)."""
    return _j(runtime.cycle(force=True))


@router.get('/algorithms')
def algorithm_list(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': [{'id': k, 'label': v['label'], 'description': v['description'], 'schema': v['model'].model_json_schema()}
                      for k, v in algorithms.ALGORITHMS.items()]}


@router.post('/backtests', status_code=201)
def new_backtest(payload: dict, user: Principal = Depends(_ops_or_risk)):
    return _j(backtest.run(user.username, parse(backtest.BacktestIn, payload)))


@router.get('/backtests')
def backtests(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': _j(backtest.listing(db))}


@router.get('/backtests/{run_id}')
def backtest_detail(run_id: UUID, response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return _j(backtest.detail(db, run_id))


@router.get('/sources')
def sources(response: Response, _: str = Depends(require_operator)):
    """Collected (account, symbol) pairs a backtest can run on, with how many bars exist per timeframe."""
    _no_store(response)
    with store.transaction('read') as db:
        rows = db.execute("""SELECT p.account_id, a.code AS account_code, p.broker_symbol, i.symbol AS instrument,
                                    (SELECT min(bar_time) FROM md_bars b WHERE b.account_id=p.account_id AND b.broker_symbol=p.broker_symbol) AS first_bar,
                                    (SELECT max(bar_time) FROM md_bars b WHERE b.account_id=p.account_id AND b.broker_symbol=p.broker_symbol) AS last_bar
                             FROM md_collection_plans p JOIN cfg_accounts a ON a.id=p.account_id LEFT JOIN cfg_instruments i ON i.id=p.instrument_id
                             WHERE p.status <> 'ARCHIVED' ORDER BY a.code, p.broker_symbol""").fetchall()
    return {'items': _j([dict(r) for r in rows])}


@router.get('/performance')
def performance(response: Response, days: int = Query(3650, ge=1, le=3650), _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': _j(portfolio.performance(db, datetime.now(timezone.utc) - timedelta(days=days)))}


# ----- Stage 3: signals -----

@router.get('/signals')
def signal_list(response: Response, state: str | None = Query(None, max_length=20), deployment_id: UUID | None = None,
                limit: int = Query(300, ge=1, le=2000), _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': _j(signals.listing(db, state, deployment_id, limit))}


@router.get('/signals/quality')
def signal_quality(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': _j(signals.quality(db))}


@router.get('/signals/{signal_id}')
def signal_detail(signal_id: UUID, response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return _j(signals.history(db, signal_id))


@router.post('/signals', status_code=201)
def manual_signal(payload: dict, user: Principal = Depends(_ops)):
    return _j(signals.manual(user.username, parse(signals.ManualIn, payload)))


class ReasonIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason: str = Field(min_length=5, max_length=500)


@router.post('/signals/{signal_id}/cancel')
def cancel_signal(signal_id: UUID, payload: dict, user: Principal = Depends(_ops_or_risk)):
    return _j(signals.cancel(user.username, signal_id, parse(ReasonIn, payload).reason))


@router.get('/intents')
def intents(response: Response, limit: int = Query(200, ge=1, le=2000), _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        rows = db.execute("""SELECT t.*, s.code AS strategy_code, i.symbol, a.code AS account_code FROM tr_intents t
                             LEFT JOIN cfg_deployments d ON d.id=t.deployment_id LEFT JOIN cfg_strategies s ON s.id=d.strategy_id
                             JOIN cfg_instruments i ON i.id=t.instrument_id JOIN cfg_accounts a ON a.id=t.account_id ORDER BY t.created_at DESC LIMIT %s""",
                          (limit,)).fetchall()
        return {'items': _j([store.clean(r) for r in rows])}


# ----- Stage 5: risk -----

@router.get('/risk/limits')
def limit_list(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': _j(risk.limits(db)), 'kinds': risk.KINDS}


@router.post('/risk/limits', status_code=201)
def add_limit(payload: dict, user: Principal = Depends(_risk)):
    return _j(risk.save_limit(user.username, parse(risk.LimitIn, payload)))


@router.put('/risk/limits/{limit_id}')
def change_limit(limit_id: UUID, payload: dict, user: Principal = Depends(_risk)):
    version = int(payload.pop('version', 0) or 0)
    return _j(risk.save_limit(user.username, parse(risk.LimitIn, payload), limit_id, version))


@router.delete('/risk/limits/{limit_id}', status_code=204)
def remove_limit(limit_id: UUID, reason: str = Query(min_length=5, max_length=500), user: Principal = Depends(_risk)):
    risk.delete_limit(user.username, limit_id, reason)
    return Response(status_code=204, headers={'Cache-Control': 'no-store'})


@router.get('/risk/kill-switches')
def kills(response: Response, history: bool = False, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        rows = risk.kill_switches(db, history)
        names = _target_names(db)
    return {'items': _j([{**r, 'target_name': names.get(str(r['target_id']), 'Firm' if r['scope'] == 'FIRM' else '?')} for r in rows])}


def _target_names(db) -> dict:
    out = {str(r['id']): r['n'] for r in db.execute('SELECT id, code AS n FROM cfg_accounts UNION ALL SELECT id, code FROM cfg_strategies '
                                                    'UNION ALL SELECT id, symbol FROM cfg_instruments').fetchall()}
    for r in db.execute('SELECT d.id, s.code || \' · \' || i.symbol || \' · \' || a.code AS n FROM cfg_deployments d JOIN cfg_strategies s ON s.id=d.strategy_id '
                        'JOIN cfg_instruments i ON i.id=d.instrument_id JOIN cfg_accounts a ON a.id=d.account_id').fetchall():
        out[str(r['id'])] = r['n']
    return out


@router.post('/risk/kill-switches', status_code=201)
def engage(payload: dict, user: Principal = Depends(_ops_or_risk)):
    return _j(risk.engage(user.username, parse(risk.KillIn, payload)))


@router.post('/risk/kill-switches/{switch_id}/release')
def release(switch_id: UUID, payload: dict, user: Principal = Depends(_risk)):
    return _j(risk.release(user.username, switch_id, parse(ReasonIn, payload).reason))


@router.get('/risk/decisions')
def decision_list(response: Response, approved: bool | None = None, limit: int = Query(200, ge=1, le=2000), _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': _j(risk.decisions(db, limit, approved))}


@router.get('/risk/exposure')
def exposure(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        exp = portfolio.exposure(db)
        accts = [{**portfolio.equity(db, a['account_id']), 'day_pnl': portfolio.day_pnl(db, a['account_id']), **a} for a in exp['accounts']]
        return _j({'instruments': exp['instruments'], 'accounts': accts})


# ----- Stage 7: orders -----

@router.get('/orders')
def order_list(response: Response, state: str | None = Query(None, max_length=20), open_only: bool = False, account_id: UUID | None = None,
               limit: int = Query(300, ge=1, le=2000), _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': _j(oms.orders(db, state, account_id, limit, open_only))}


@router.get('/orders/{order_id}')
def order_detail(order_id: UUID, response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return _j(oms.order_detail(db, order_id))


@router.post('/orders', status_code=201)
def order_ticket(payload: dict, user: Principal = Depends(_ops)):
    return _j(oms.ticket(user.username, parse(oms.TicketIn, payload)))


@router.post('/orders/{order_id}/cancel')
def cancel_order(order_id: UUID, payload: dict, user: Principal = Depends(_ops_or_risk)):
    return _j(oms.cancel(user.username, order_id, parse(ReasonIn, payload).reason))


@router.post('/orders/{order_id}/resolve')
def resolve_order(order_id: UUID, payload: dict, user: Principal = Depends(_risk)):
    return _j(oms.resolve(user.username, order_id, parse(oms.ResolveIn, payload)))


@router.get('/fills')
def fills(response: Response, limit: int = Query(300, ge=1, le=2000), _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        rows = db.execute("""SELECT f.*, a.code AS account_code, i.symbol, s.code AS strategy_code FROM oms_fills f JOIN cfg_accounts a ON a.id=f.account_id
                             JOIN cfg_instruments i ON i.id=f.instrument_id LEFT JOIN cfg_deployments d ON d.id=f.deployment_id
                             LEFT JOIN cfg_strategies s ON s.id=d.strategy_id ORDER BY f.at DESC LIMIT %s""", (limit,)).fetchall()
        return {'items': _j([store.clean(r) for r in rows])}


# ----- Stage 6: portfolio -----

@router.get('/portfolio/positions')
def positions(response: Response, include_closed: bool = False, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': _j(portfolio.positions(db, None, not include_closed))}


@router.get('/portfolio/accounts')
def accounts(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        ids = [r['id'] for r in db.execute("SELECT DISTINCT a.id FROM cfg_accounts a LEFT JOIN cfg_deployments d ON d.account_id=a.id "
                                           "LEFT JOIN pf_positions p ON p.account_id=a.id WHERE d.id IS NOT NULL OR p.id IS NOT NULL").fetchall()]
        codes = {str(r['id']): r['code'] for r in db.execute('SELECT id, code FROM cfg_accounts').fetchall()}
        out = [{**portfolio.equity(db, a), 'account_code': codes.get(str(a)), 'day_pnl': portfolio.day_pnl(db, a),
                'capital_set': bool(db.execute('SELECT 1 FROM pf_accounts WHERE account_id=%s AND version > 0', (str(a),)).fetchone())} for a in ids]
        return {'items': _j(out)}


@router.get('/portfolio/equity/{account_id}')
def equity_curve(account_id: UUID, response: Response, days: int = Query(30, ge=1, le=400), _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        rows = db.execute('SELECT at, equity FROM pf_equity WHERE account_id=%s AND at > now() - make_interval(days => %s) ORDER BY at', (account_id, days)).fetchall()
        step = max(1, len(rows) // 500)
        return {'items': _j([dict(r) for r in rows[::step]])}


class CapitalIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    amount: Decimal = Field(gt=0, max_digits=20, decimal_places=2)
    reason: str = Field(min_length=5, max_length=500)


@router.put('/portfolio/accounts/{account_id}/capital')
def set_capital(account_id: UUID, payload: dict, user: Principal = Depends(_risk)):
    data = parse(CapitalIn, payload)
    return _j(portfolio.set_capital(user.username, account_id, data.amount, data.reason))


class FlattenIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_id: UUID
    instrument_id: UUID
    reason: str = Field(min_length=5, max_length=500)
    key: str = Field(min_length=8, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')


@router.post('/portfolio/flatten')
def flatten(payload: dict, user: Principal = Depends(_ops_or_risk)):
    data = parse(FlattenIn, payload)
    return _j(oms.flatten(user.username, data.account_id, data.instrument_id, data.reason, data.key))
