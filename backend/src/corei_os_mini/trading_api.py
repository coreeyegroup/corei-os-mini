"""HTTP API for the trading core, Stages 3–8 (signals, strategies, risk, portfolio, execution, post-trade).

Why it exists: the stage screens and the system map read and control the trading core through one place.
Purpose: /api/trading/* — overview KPIs per stage; every formula by stage (/formulas); the signal library (catalogue,
definitions, overview, live board, one instrument across timeframes, flips, correlation, explain with worked equations,
research of one or all signals: IC, hit rate, quintiles); the strategy monitor; the execution handoff (decision flow,
execute / decline a waiting decision, execution policy per deployment); settings; runtime state and Run now; signals (list, detail,
manual, cancel, quality); intents; backtests; risk limits, kill switches, decisions and a pre-trade preview;
orders (blotter, detail, ticket with routing, cancel, resolve UNKNOWN, release a held LIVE order, look up at the broker,
dispatcher status); fills; positions, exposure, equity, capital, performance, partitions and allocation, copy links,
attribution; VaR, stress scenarios, margin; optimisation and walk-forward, drift; post-trade reconciliation and breaks,
broker book, TCA, trade journal, statements, order audit trail and CSV exports.
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
from .trading import (algorithms, algos, backtest, brokers, copytrade, handoff, quickstart, execution, oms, optimise, partitions, portfolio, posttrade, risk, runtime, signals,
                      signal_library, strategy_health, var)
from . import formulas
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
        execution_status = execution.status(db)
        pt = posttrade.overview(db)
    return {'deployments': dep, 'active_modes': modes, 'signals_today': sig, 'orders_today': orders, 'open_orders': open_orders, 'unknown_orders': unknown,
            'kill_switches': kills, 'limits': limits_n, 'refused_today': refused, 'positions': len(pos), 'accounts': pnl, 'backtests': backtests,
            'runtime': {**runtime.STATUS, 'enabled': cfg.get('runtime_enabled'), 'deployments_with_problems': errors},
            'execution': _j(execution_status), 'posttrade': _j(pt)}


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
    # Execution, post-trade, drift and VaR (0.45); left out = unchanged.
    broker_magic: int | None = Field(default=None, ge=1, le=2147483647)
    max_deviation_points: int | None = Field(default=None, ge=0, le=100000)
    broker_timeout_seconds: int | None = Field(default=None, ge=5, le=300)
    unknown_check_seconds: int | None = Field(default=None, ge=5, le=3600)
    pre_check_orders: bool | None = None
    upstox_product: Literal['D', 'I', 'MTF'] | None = None
    live_four_eyes: Literal['OFF', 'MANUAL', 'ALL'] | None = None
    recon_interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    recon_qty_tolerance: Decimal | None = Field(default=None, ge=0, max_digits=38, decimal_places=12)
    recon_kill_on_break: bool | None = None
    drift_min_trades: int | None = Field(default=None, ge=5, le=10000)
    drift_tolerance_pct: Decimal | None = Field(default=None, gt=0, le=1000)
    var_confidence: Literal['90', '95', '97.5', '99', '99.5'] | None = None
    var_lookback_bars: int | None = Field(default=None, ge=30, le=5000)
    var_timeframe: Literal['M15', 'M30', 'H1', 'H4', 'D1'] | None = None
    realign_max_attempts: int | None = Field(default=None, ge=0, le=50)
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
        vals = {k: v for k, v in data.model_dump(exclude={'version', 'reason'}).items() if v is not None}
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
    return {'items': [{'id': k, 'label': v['label'], 'description': v['description'], 'equation': v.get('equation'), 'strength_equation': v.get('strength_equation'), 'schema': v['model'].model_json_schema()}
                      for k, v in algorithms.ALGORITHMS.items()]}


# ----- every calculation, by stage (shown in each workspace) -----

@router.get('/formulas')
def formula_list(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return {'stages': formulas.all_formulas()}


# ----- Stage 3: the signal library -----

_TF_MIN = {'M1': 1, 'M5': 5, 'M15': 15, 'M30': 30, 'H1': 60, 'H4': 240, 'D1': 1440}


def _last_bar_time(db, account_id, symbol: str) -> datetime:
    """Windows end at the newest stored bar (market time), not the machine clock."""
    row = db.execute('SELECT max(bar_time) AS t FROM md_bars WHERE account_id=%s AND broker_symbol=%s', (str(account_id), symbol)).fetchone()
    return (row['t'] if row and row['t'] else datetime.now(timezone.utc)) + timedelta(minutes=1)


class ResearchIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_id: UUID
    symbol: str = Field(min_length=1, max_length=64)
    timeframe: Literal['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'] = 'H1'
    kind: str
    parameters: dict = Field(default_factory=dict)
    threshold: float = Field(20, ge=1, le=99)
    days: int = Field(180, ge=1, le=3650)
    horizons: list[int] = Field(default_factory=lambda: [1, 2, 5, 10, 20])


@router.get('/signal-library/catalogue')
def signal_catalogue(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': signal_library.catalogue()}


@router.get('/signal-library/definitions')
def signal_definitions(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': _j([{**d, 'graph': None if not d.get('graph') else {k: v for k, v in d['graph'].items() if k != 'definition'}}
                             for d in signal_library.definitions(db)])}


@router.post('/signal-library/definitions', status_code=201)
def add_signal_definition(payload: dict, user: Principal = Depends(_risk)):
    return _j(signal_library.save(user.username, parse(signal_library.DefinitionIn, payload)))


@router.put('/signal-library/definitions/{def_id}')
def change_signal_definition(def_id: UUID, payload: dict, user: Principal = Depends(_risk)):
    payload = dict(payload)
    payload.pop('version', None)
    return _j(signal_library.save(user.username, parse(signal_library.DefinitionIn, payload), def_id))


@router.delete('/signal-library/definitions/{def_id}', status_code=204)
def remove_signal_definition(def_id: UUID, reason: str = Query(..., min_length=5, max_length=500), user: Principal = Depends(_risk)):
    signal_library.delete(user.username, def_id, reason)
    return Response(status_code=204)


@router.get('/signal-library/board')
def signal_board(response: Response, timeframe: Literal['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'] = 'H1', _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return _j(signal_library.board(db, timeframe))


@router.get('/signal-library/explain')
def signal_explain(response: Response, account_id: UUID, symbol: str = Query(..., min_length=1, max_length=64),
                   timeframe: Literal['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'] = 'H1', _: str = Depends(require_operator)):
    """Every active signal of one instrument computed now from its stored bars, with the equation filled in and the last 60 scores."""
    _no_store(response)
    with store.transaction('read') as db:
        end = _last_bar_time(db, account_id, symbol)
        start = end - timedelta(minutes=_TF_MIN[timeframe] * 600)
        bars, source = backtest.load_bars(db, account_id, symbol, timeframe, start, end)
        defs = signal_library.definitions(db, active_only=True)
    out = []
    for d in defs:
        params = signal_library.params_of(d['kind'], d['parameters'])
        series = signal_library.score_series(d, bars, timeframe) if bars else []
        last = series[-1] if series else None
        score, inputs = (last if last else (None, None))
        g = d.get('graph')
        out.append({'code': d['code'], 'name': d['name'], 'kind': d['kind'], 'family': d.get('family'), 'parameters': params, 'threshold': d['threshold'],
                    'graph': None if not g else {'code': g['code'], 'version': g['version'], 'hash': g['hash']},
                    'equation': d.get('equation'), 'score': None if score is None else round(score, 1),
                    'direction': signal_library.direction(score, float(d['threshold'])), 'inputs': inputs,
                    'worked': signal_library.worked(d['kind'], params, score, inputs),
                    'recent': [None if x is None else round(x[0], 1) for x in series[-60:]],
                    'history': [None if x is None else round(x[0], 1) for x in series[-200:]]})
    return _j({'symbol': symbol, 'timeframe': timeframe, 'bars': len(bars), 'bar_source': source,
               'last_bar': bars[-1]['bar_time'] if bars else None, 'last_close': bars[-1]['close'] if bars else None, 'items': out,
               'closes': [{'t': b['bar_time'], 'close': b['close']} for b in bars[-200:]],
               'consensus': signal_library.consensus([x['score'] for x in out])})


class BlendPreviewIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_id: UUID
    symbol: str = Field(min_length=1, max_length=64)
    timeframe: Literal['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'] = 'H1'
    components: list[dict] = Field(min_length=1, max_length=12)
    entry: float = 40
    exit: float = 10


@router.post('/signal-library/blend-preview')
def blend_preview(payload: dict, _: str = Depends(require_operator)):
    """A SIGNAL_BLEND recipe on the newest bars of one instrument: each component's score with its working, the
    combined score and what the rule would do — the strategy's arithmetic, shown before it is saved."""
    data = parse(BlendPreviewIn, payload)
    blend = parse(algorithms.SignalBlend, {'timeframe': data.timeframe, 'components': data.components, 'entry': data.entry, 'exit': data.exit})
    with store.transaction('read') as db:
        end = _last_bar_time(db, data.account_id, data.symbol)
        bars, source = backtest.load_bars(db, data.account_id, data.symbol, data.timeframe, end - timedelta(minutes=_TF_MIN[data.timeframe] * 600), end)
    parts, total_w = [], sum(abs(c.weight) for c in blend.components)
    for c in blend.components:
        p = signal_library.params_of(c.kind, c.parameters)
        series = signal_library.compute(c.kind, p, bars) if bars else []
        score, inputs = series[-1] if series and series[-1] else (None, None)
        parts.append({'kind': c.kind, 'weight': c.weight, 'parameters': p, 'equation': signal_library.KINDS[c.kind]['equation'],
                      'score': None if score is None else round(score, 1), 'worked': signal_library.worked(c.kind, p, score, inputs)})
    ready = bool(parts) and all(x['score'] is not None for x in parts) and total_w > 0
    combined = round(sum(x['weight'] * x['score'] for x in parts) / total_w, 1) if ready else None
    side = None if combined is None else 'LONG' if combined >= blend.entry else 'SHORT' if combined <= -blend.entry else 'FLAT or hold (inside ±entry)'
    worked = (' + '.join(f"{x['weight']:g} × {x['score']:+.1f}" for x in parts) + f' ÷ {total_w:g} = {combined:+.1f}') if ready else 'warming up: not enough bars yet'
    return _j({'symbol': data.symbol, 'timeframe': data.timeframe, 'bars': len(bars), 'bar_source': source, 'last_bar': bars[-1]['bar_time'] if bars else None,
               'components': parts, 'combined': combined, 'worked': worked, 'side': side, 'entry': blend.entry, 'exit': blend.exit,
               'equation': algorithms.EQUATIONS['SIGNAL_BLEND'][0]})


TF = Literal['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1']


@router.get('/signal-library/overview')
def signal_overview(response: Response, timeframe: TF = 'H1', _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return _j(signal_library.overview(db, timeframe))


@router.get('/signal-library/instrument')
def signal_instrument(response: Response, account_id: UUID, symbol: str = Query(..., min_length=1, max_length=64), _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return _j(signal_library.instrument_matrix(db, account_id, symbol))


@router.get('/signal-library/events')
def signal_events(response: Response, limit: int = Query(200, ge=1, le=2000), timeframe: TF | None = None, code: str | None = Query(None, max_length=40),
                  symbol: str | None = Query(None, max_length=64), _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': _j(signal_library.events(db, limit, timeframe, code, symbol))}


@router.get('/signal-library/correlation')
def signal_correlation(response: Response, account_id: UUID, symbol: str = Query(..., min_length=1, max_length=64), timeframe: TF = 'H1',
                       _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        end = _last_bar_time(db, account_id, symbol)
        bars, source = backtest.load_bars(db, account_id, symbol, timeframe, end - timedelta(minutes=_TF_MIN[timeframe] * 1000), end)
        defs = signal_library.definitions(db, active_only=True)
    return _j({**signal_library.correlation(defs, bars), 'symbol': symbol, 'timeframe': timeframe, 'bar_source': source})


class ResearchAllIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_id: UUID
    symbol: str = Field(min_length=1, max_length=64)
    timeframe: TF = 'H1'
    days: int = Field(180, ge=1, le=3650)
    horizons: list[int] = Field(default_factory=lambda: [1, 2, 5, 10, 20])


@router.post('/signal-library/research-all')
def signal_research_all(payload: dict, _: str = Depends(require_operator)):
    data = parse(ResearchAllIn, payload)
    hz = sorted({h for h in data.horizons if 1 <= h <= 500})[:8] or [1, 5, 20]
    with store.transaction('read') as db:
        end = _last_bar_time(db, data.account_id, data.symbol)
        bars, source = backtest.load_bars(db, data.account_id, data.symbol, data.timeframe, end - timedelta(days=data.days), end)
        defs = signal_library.definitions(db, active_only=True)
    if len(bars) < 60:
        raise HTTPException(409, f'Only {len(bars)} complete {data.timeframe} bars stored for {data.symbol} in that window — collect more or widen the days.')
    return _j({'symbol': data.symbol, 'timeframe': data.timeframe, 'bars': len(bars), 'bar_source': source, 'horizons': hz,
               'items': signal_library.research_all(defs, bars, hz)})


@router.post('/signal-library/research')
def signal_research(payload: dict, _: str = Depends(require_operator)):
    data = parse(ResearchIn, payload)
    if data.kind not in signal_library.KINDS:
        raise HTTPException(422, f'Unknown signal kind {data.kind}.')
    hz = sorted({h for h in data.horizons if 1 <= h <= 500})[:8] or [1, 5, 20]
    with store.transaction('read') as db:
        end = _last_bar_time(db, data.account_id, data.symbol)
        bars, source = backtest.load_bars(db, data.account_id, data.symbol, data.timeframe, end - timedelta(days=data.days), end)
    if len(bars) < 60:
        raise HTTPException(409, f'Only {len(bars)} complete {data.timeframe} bars stored for {data.symbol} in that window — collect more or widen the days.')
    out = signal_library.research(data.kind, data.parameters, bars, hz, data.threshold)
    out.update(symbol=data.symbol, timeframe=data.timeframe, bar_source=source)
    return _j(out)


@router.get('/strategies/monitor')
def strategy_monitor(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return _j(strategy_health.monitor(db))


# ----- Stage 4 → Stage 7: the execution handoff -----

@router.get('/execution/flow')
def execution_flow(response: Response, outcome: str | None = Query(None, max_length=120, pattern=r'^[A-Z_,]+$'), limit: int = Query(200, ge=1, le=2000),
                   _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': _j(handoff.flow(db, outcome, limit)), 'summary': _j(handoff.summary(db))}


@router.post('/execution/decisions/{intent_id}/execute')
def execute_decision(intent_id: UUID, payload: dict, user: Principal = Depends(_ops_or_risk)):
    return _j(handoff.execute(user.username, intent_id, parse(handoff.ExecuteIn, payload or {})))


# ---------- execution algorithms (DEC-94) ----------

@router.get('/execution/algos')
def algo_orders(response: Response, active: bool = False, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return _j({'items': algos.parents(db, active=active), 'algos': algos.ALGOS})


@router.get('/execution/algos/{parent_id}')
def algo_order(parent_id: UUID, response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return _j(algos.detail(db, parent_id))


@router.post('/execution/algos', status_code=201)
def place_algo_order(payload: dict, user: Principal = Depends(_ops_or_risk)):
    return _j(algos.ticket(user.username, parse(algos.ParentIn, payload)))


class AlgoAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason: str = Field(min_length=5, max_length=500)


@router.post('/execution/algos/{parent_id}/{action}')
def control_algo_order(parent_id: UUID, action: Literal['pause', 'resume', 'cancel'], payload: dict, user: Principal = Depends(_ops_or_risk)):
    return _j(algos.control(user.username, parent_id, action, parse(AlgoAction, payload).reason))


@router.post('/execution/algos-tick')
def tick_algo_orders(user: Principal = Depends(_ops_or_risk)):
    """Look at every working algorithm now (the execution loop does this every 2 s; the runtime every cycle)."""
    return algos.tick()


@router.post('/execution/decisions/{intent_id}/decline')
def decline_decision(intent_id: UUID, payload: dict, user: Principal = Depends(_ops_or_risk)):
    return _j(handoff.decline(user.username, intent_id, parse(ReasonIn, payload).reason))


@router.get('/execution/policies')
def execution_policies(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': _j(handoff.policies(db))}


@router.put('/execution/policies/{deployment_id}')
def put_execution_policy(deployment_id: UUID, payload: dict, user: Principal = Depends(_risk)):
    return _j(handoff.set_policy(user.username, deployment_id, parse(handoff.PolicyIn, payload)))


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


@router.post('/orders/{order_id}/modify')
def modify_order(order_id: UUID, payload: dict, user: Principal = Depends(_ops_or_risk)):
    """Amend a working order: limit / stop price, stop-loss / take-profit, a smaller paper quantity."""
    return _j(oms.modify(user.username, order_id, parse(oms.ModifyIn, payload)))


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


# ----- Stage 7: real execution -----

class ReleaseIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason: str = Field(min_length=5, max_length=500)


@router.post('/orders/{order_id}/release')
def release_order(order_id: UUID, payload: dict, user: Principal = Depends(_risk)):
    """Four-eyes: a Risk approver other than the person who placed it lets a LIVE order go to the broker."""
    out = oms.release(user.username, order_id, parse(ReleaseIn, payload).reason)
    execution.dispatch_soon()
    return _j(out)


@router.post('/orders/{order_id}/check-broker')
def check_at_broker(order_id: UUID, user: Principal = Depends(_ops_or_risk)):
    """Look the order up at the broker by its tag now (never sends it again)."""
    return _j(execution.broker_order_detail(order_id))


@router.get('/execution/status')
def execution_status(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        st = execution.status(db)
        caps = []
        for r in db.execute("""SELECT a.id, a.code, c.environment, c.platform FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id
                               WHERE a.status <> 'ARCHIVED' ORDER BY a.code""").fetchall():
            cap = brokers.capability(db, r['id'])
            caps.append({'account_id': str(r['id']), 'account_code': r['code'], 'environment': r['environment'], 'platform': r['platform'], **cap})
        controls = list(store.execution_controls(db).values())
        return _j({**st, 'accounts': caps, 'order_sending': [{'environment': c['environment'], 'enabled': c['trading_enabled']} for c in controls]})


@router.post('/execution/dispatch')
def dispatch_now(user: Principal = Depends(_ops_or_risk)):
    return _j({'dispatch': execution.dispatch(), 'sync': execution.sync()})


# ----- Stage 5: margin, VaR, stress -----

@router.get('/risk/var')
def var_report(response: Response, account_id: UUID | None = None, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        ids = [account_id] if account_id else [r['account_id'] for r in db.execute('SELECT DISTINCT account_id FROM pf_positions WHERE quantity <> 0').fetchall()]
        codes = {str(r['id']): r['code'] for r in db.execute('SELECT id, code FROM cfg_accounts').fetchall()}
        out = []
        for a in ids:
            eq = portfolio.equity(db, a)
            v = var.account_var(db, a)
            m = risk.margin(db, a)
            out.append({'account_id': str(a), 'account_code': codes.get(str(a)), 'currency': eq['currency'], 'equity': eq['equity'], **v,
                        'var_pct': (v['var'] / eq['equity'] * 100).quantize(Decimal('0.01')) if v.get('var') is not None and eq['equity'] > 0 else None,
                        'margin': m})
        return {'items': _j(out)}


@router.get('/risk/stress')
def stress_report(response: Response, account_id: UUID | None = None, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return _j(var.stress(db, account_id))


@router.get('/risk/scenarios')
def scenario_list(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': _j(var.scenarios(db))}


@router.post('/risk/scenarios', status_code=201)
def scenario_create(payload: dict, user: Principal = Depends(_risk)):
    return _j(var.save_scenario(user.username, parse(var.ScenarioIn, payload)))


@router.put('/risk/scenarios/{scenario_id}')
def scenario_update(scenario_id: UUID, payload: dict, user: Principal = Depends(_risk)):
    return _j(var.save_scenario(user.username, parse(var.ScenarioIn, payload), scenario_id))


@router.delete('/risk/scenarios/{scenario_id}', status_code=204)
def scenario_delete(scenario_id: UUID, reason: str = Query(min_length=5, max_length=500), user: Principal = Depends(_risk)):
    var.delete_scenario(user.username, scenario_id, reason)


# ----- Stage 4: optimisation, drift -----

@router.post('/optimisations', status_code=201)
def optimise_run(payload: dict, user: Principal = Depends(_ops_or_risk)):
    return _j(optimise.run(user.username, parse(optimise.OptimiseIn, payload)))


@router.get('/optimisations')
def optimise_list(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': _j(optimise.listing(db))}


@router.get('/optimisations/{run_id}')
def optimise_detail(run_id: UUID, response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return _j(optimise.detail(db, run_id))


@router.get('/drift')
def drift(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': _j(strategy_health.drift_report(db))}


# ----- Stage 6: attribution, partitions, allocation, copy trading -----

@router.get('/portfolio/attribution')
def attribution(response: Response, by: str = Query('strategy', max_length=20), days: int = Query(30, ge=1, le=3650),
                _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return _j(strategy_health.attribution(db, by, datetime.now(timezone.utc) - timedelta(days=days)))


@router.get('/portfolio/partitions')
def partition_list(response: Response, account_id: UUID | None = None, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': _j(partitions.listing(db, account_id))}


@router.post('/portfolio/partitions', status_code=201)
def partition_create(payload: dict, user: Principal = Depends(_risk)):
    return _j(partitions.save(user.username, parse(partitions.PartitionIn, payload)))


@router.put('/portfolio/partitions/{partition_id}')
def partition_update(partition_id: UUID, payload: dict, version: int | None = None, user: Principal = Depends(_risk)):
    return _j(partitions.save(user.username, parse(partitions.PartitionIn, payload), partition_id, version))


@router.delete('/portfolio/partitions/{partition_id}', status_code=204)
def partition_delete(partition_id: UUID, reason: str = Query(min_length=5, max_length=500), user: Principal = Depends(_risk)):
    partitions.delete(user.username, partition_id, reason)


@router.put('/portfolio/deployments/{deployment_id}/allocation')
def deployment_allocation(deployment_id: UUID, payload: dict, user: Principal = Depends(_risk)):
    return _j(partitions.assign(user.username, deployment_id, parse(partitions.AssignIn, payload)))


@router.post('/portfolio/allocate')
def allocate(payload: dict, user: Principal = Depends(_risk)):
    return _j(partitions.allocate(user.username, parse(partitions.AllocateIn, payload)))


@router.get('/portfolio/copy-links')
def copy_list(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': _j(copytrade.links(db))}


@router.post('/portfolio/copy-links', status_code=201)
def copy_create(payload: dict, user: Principal = Depends(_risk)):
    return _j(copytrade.save(user.username, parse(copytrade.LinkIn, payload)))


@router.put('/portfolio/copy-links/{link_id}')
def copy_update(link_id: UUID, payload: dict, version: int | None = None, user: Principal = Depends(_risk)):
    return _j(copytrade.save(user.username, parse(copytrade.LinkIn, payload), link_id, version))


@router.delete('/portfolio/copy-links/{link_id}', status_code=204)
def copy_delete(link_id: UUID, reason: str = Query(min_length=5, max_length=500), user: Principal = Depends(_risk)):
    copytrade.delete(user.username, link_id, reason)


@router.get('/routing/preview')
def routing_preview(response: Response, instrument_id: UUID, side: Literal['BUY', 'SELL'], quantity: Decimal = Query(gt=0),
                    accounts: str = Query(min_length=36, max_length=2000), mode: Literal['PAPER', 'BROKER'] = 'PAPER', _: str = Depends(require_operator)):
    """Every candidate account's price and eligibility for an order (nothing is placed)."""
    _no_store(response)
    from .trading import routing
    with store.transaction('read') as db:
        return {'items': _j(routing.candidates(db, [a for a in accounts.split(',') if a], instrument_id, side, quantity, mode))}


# ----- Stage 8: post-trade -----

@router.get('/posttrade/overview')
def pt_overview(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return _j(posttrade.overview(db))


@router.post('/posttrade/reconcile/{account_id}')
def pt_reconcile(account_id: UUID, broker: bool | None = None, user: Principal = Depends(_ops_or_risk)):
    return _j(posttrade.reconcile(account_id, user.username, broker))


@router.get('/posttrade/breaks')
def pt_breaks(response: Response, state: str | None = Query('OPEN', max_length=10), account_id: UUID | None = None, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': _j(posttrade.breaks(db, state or None, account_id))}


@router.post('/posttrade/breaks/{break_id}')
def pt_break_action(break_id: UUID, payload: dict, user: Principal = Depends(_risk)):
    return _j(posttrade.act_on_break(user.username, break_id, parse(posttrade.BreakAction, payload)))


@router.get('/posttrade/runs')
def pt_runs(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': _j(posttrade.runs(db))}


@router.get('/posttrade/broker/{account_id}')
def pt_broker(account_id: UUID, response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return _j(posttrade.broker_view(db, account_id))


@router.post('/posttrade/broker/{account_id}/read')
def pt_broker_read(account_id: UUID, user: Principal = Depends(_ops_or_risk)):
    posttrade.fetch_book(account_id, user.username)
    with store.transaction('read') as db:
        return _j(posttrade.broker_view(db, account_id))


def _period(days: int) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    return now - timedelta(days=days), now + timedelta(minutes=1)


@router.get('/posttrade/tca')
def pt_tca(response: Response, by: str = Query('account', max_length=20), days: int = Query(30, ge=1, le=3650),
           mode: Literal['PAPER', 'BROKER'] | None = None, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return _j(posttrade.tca(db, by, *_period(days), mode))


@router.get('/posttrade/journal')
def pt_journal(response: Response, days: int = Query(90, ge=1, le=3650), account_id: UUID | None = None, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return _j(posttrade.journal(db, *_period(days), account_id))


@router.get('/posttrade/statement/{account_id}')
def pt_statement(account_id: UUID, response: Response, day_from: str = Query(pattern=r'^\d{4}-\d{2}-\d{2}$'),
                 day_to: str = Query(pattern=r'^\d{4}-\d{2}-\d{2}$'), _: str = Depends(require_operator)):
    _no_store(response)
    from datetime import date
    with store.transaction('read') as db:
        return _j(posttrade.statement(db, account_id, date.fromisoformat(day_from), date.fromisoformat(day_to)))


@router.get('/posttrade/events')
def pt_events(response: Response, days: int = Query(7, ge=1, le=3650), account_id: UUID | None = None, state: str | None = Query(None, max_length=20),
              actor: str | None = Query(None, max_length=80), search: str | None = Query(None, max_length=80), limit: int = Query(1000, ge=1, le=20000),
              _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': _j(posttrade.order_events(db, *_period(days), account_id, state, actor, search, limit))}


EXPORTS = ('fills', 'orders', 'trades', 'events', 'breaks', 'tca', 'statement', 'attribution')


@router.get('/posttrade/export/{kind}.csv')
def pt_export(kind: str, response: Response, days: int = Query(30, ge=1, le=3650), account_id: UUID | None = None, by: str = Query('account', max_length=20),
              day_from: str | None = Query(None, pattern=r'^\d{4}-\d{2}-\d{2}$'), day_to: str | None = Query(None, pattern=r'^\d{4}-\d{2}-\d{2}$'),
              _: str = Depends(require_operator)):
    """CSV of a post-trade report (exact decimals, ISO times)."""
    if kind not in EXPORTS:
        raise HTTPException(404, f"Unknown export; one of: {', '.join(EXPORTS)}.")
    since, until = _period(days)
    with store.transaction('read') as db:
        if kind == 'fills':
            rows = [store.clean(r) for r in db.execute("""SELECT f.at, a.code AS account, i.symbol, f.side, f.quantity, f.price, f.arrival_price, f.slippage,
                         f.latency_ms, f.realised_pnl, f.currency, f.mode, f.broker_deal_id, o.client_order_id, o.source FROM oms_fills f
                         JOIN oms_orders o ON o.id=f.order_id JOIN cfg_accounts a ON a.id=f.account_id JOIN cfg_instruments i ON i.id=f.instrument_id
                         WHERE f.at >= %s AND f.at < %s""" + (' AND f.account_id=%s' if account_id else '') + ' ORDER BY f.at',
                                                        [since, until, *([str(account_id)] if account_id else [])]).fetchall()]
        elif kind == 'orders':
            rows = [r for r in oms.orders(db, None, account_id, 20000) if r['created_at'] >= since]
        elif kind == 'trades':
            rows = posttrade.journal(db, since, until, account_id, excursions=False, limit=20000)['trips']
        elif kind == 'events':
            rows = posttrade.order_events(db, since, until, account_id, limit=20000)
        elif kind == 'breaks':
            rows = posttrade.breaks(db, None, account_id, 20000)
        elif kind == 'tca':
            rows = posttrade.tca(db, by, since, until)['rows']
        elif kind == 'attribution':
            rows = strategy_health.attribution(db, by if by in strategy_health.DIMENSIONS else 'strategy', since, until)['rows']
        else:
            if not account_id or not day_from or not day_to:
                raise HTTPException(422, 'A statement needs account_id, day_from and day_to.')
            from datetime import date
            rows = posttrade.statement(db, account_id, date.fromisoformat(day_from), date.fromisoformat(day_to))['days']
    response.headers['Content-Disposition'] = f'attachment; filename="corei-{kind}.csv"'
    return Response(content=posttrade.to_csv(rows), media_type='text/csv', headers={'Content-Disposition': f'attachment; filename="corei-{kind}.csv"',
                                                                                     'Cache-Control': 'no-store'})


# ----- Quick start: a working paper strategy in one step -----

@router.get('/quickstart')
def quickstart_options(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return _j(quickstart.options(db))


@router.post('/quickstart', status_code=201)
def quickstart_start(payload: dict, user: Principal = Depends(_ops)):
    return _j(quickstart.start(user, parse(quickstart.QuickIn, payload)))
