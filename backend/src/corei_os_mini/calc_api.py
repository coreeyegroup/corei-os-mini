"""HTTP API of Calculation Graphs under /api/calc: the node catalogue, the graph library, checking a graph, and
previewing it on stored bars with the trace of every node.

Why it exists: the workstation shows every calculation as a graph with live numbers (the n8n execution view); the
editor (phase 3) checks and previews a graph before anyone proposes it.
Purpose:
  GET  /api/calc/nodes                         node catalogue, families, port types and what connects, purposes, limits, functions
  GET  /api/calc/graphs                        the library: built-ins (read-only, APPROVED) and the desk's graphs
  GET  /api/calc/graphs/{code}                 one graph and its versions
  GET  /api/calc/graphs/{code}/versions/{n}    one version with its definition
  POST /api/calc/validate                      check a definition: every error names the node, port or parameter
  POST /api/calc/preview                       run a definition (or a stored version) on the stored bars of an instrument;
                                               returns the result, the trace at a chosen bar and per-node errors; every
                                               preview is recorded in calc_runs in the same transaction
  GET  /api/calc/runs                          the latest recorded runs
  POST /api/calc/graphs                        a new graph (version 1, DRAFT)            — Trading ops or Risk approver
  POST /api/calc/graphs/{code}/copy            copy any version (built-ins too) as a new DRAFT graph
  PUT  /api/calc/graphs/{code}/draft           save the open draft, or open the next version
  POST /api/calc/graphs/{code}/versions/{n}/{propose|approve|reject|retire}
                                               the life cycle; approve/reject/retire need the Risk approver role and
                                               approval is never by the person who proposed it (four-eyes)
  GET  /api/calc/bindings, POST /api/calc/bindings, DELETE /api/calc/bindings/{id}
                                               where approved versions are used (a signal definition is then scored by its graph)
  GET  /api/calc/graphs/{code}/usage           every binding of the graph, past and present
  POST /api/calc/parity                        a built-in graph against the code it draws, on stored bars
Must never: run a graph that failed validation; accept a binary float where an exact decimal is required (single values
for money, prices and quantities must be sent as decimal strings); write anything but the insert-only run record.
Dependencies: calc.*, config_store (transactions), trading.backtest (stored bars), operator_auth.
Consumers: the workstation UI (ui/src/calc, phase 3) and Stage screens that show graph traces.
Architectural position: API layer of Calculation Graphs (DEC-93).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from . import config_store as store
from .calc import expr as E, store as cstore, types as T
from .calc.model import LIMITS, PURPOSES
from .calc.nodes import FAMILIES, Ctx, catalogue
from .calc.run import GraphInvalid, compile_graph, execute
from .operator_auth import Principal, require_operator, require_role
from .trading import signal_library
from .trading import backtest

router = APIRouter(prefix='/api/calc', tags=['calculation graphs'])
TF_MIN = {'M1': 1, 'M5': 5, 'M15': 15, 'M30': 30, 'H1': 60, 'H4': 240, 'D1': 1440}
SCALAR_SOURCES = ('quote', 'position', 'account', 'instrument', 'settings')


def _no_store(response: Response) -> None:
    response.headers['Cache-Control'] = 'no-store'


@router.get('/nodes')
def nodes(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return {'families': FAMILIES, 'items': T.to_jsonable(catalogue()),
            'types': {'all': list(T.TYPES), 'widening': {T.FLOAT: T.SERIES_FLOAT, T.BOOL: T.SERIES_BOOL, T.SIDE: T.SERIES_SIDE},
                      'rule': 'A wire joins equal types; a single value may feed a series (repeated on every bar). '
                              'A number never feeds money, a price or a quantity except through To decimal.'},
            'purposes': PURPOSES, 'limits': LIMITS, 'functions': {k: v['doc'] for k, v in E.FUNCTIONS.items()}}


@router.get('/graphs')
def graphs(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': T.to_jsonable(cstore.graphs(db))}


@router.get('/graphs/{code}')
def graph(code: str, response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        g = cstore.graph(db, code)
    if g is None:
        raise HTTPException(404, f'There is no calculation graph {code}.')
    return T.to_jsonable(g)


@router.get('/graphs/{code}/versions/{n}')
def graph_version(code: str, n: int, response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        v = cstore.version(db, code, n)
    if v is None:
        raise HTTPException(404, f'Calculation graph {code} has no version {n}.')
    return T.to_jsonable(v)


class ValidateIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    definition: dict
    parameters: dict[str, float] = Field(default_factory=dict)


@router.post('/validate')
def validate(body: ValidateIn, _: str = Depends(require_operator)):
    try:
        plan = compile_graph(body.definition, body.parameters)
    except GraphInvalid as exc:
        return {'ok': False, 'errors': exc.errors}
    return {'ok': True, 'errors': [], 'hash': plan.hash, 'warmup': plan.warmup, 'order': [pn.id for pn in plan.order],
            'output': plan.output.id, 'parameters': plan.params}


class PreviewIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    definition: dict | None = None
    code: str | None = Field(None, max_length=60)
    version: int | None = Field(None, ge=1)
    parameters: dict[str, float] = Field(default_factory=dict)
    account_id: UUID
    symbol: str = Field(min_length=1, max_length=64)
    timeframe: Literal['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'] = 'H1'
    bars: int = Field(600, ge=10, le=5000)
    trace_bar: int | None = Field(None, description='bar index (0 = oldest loaded); default the last bar')
    tail: int = Field(120, ge=1, le=2000, description='how many of the last bars of the result to return')
    scalars: dict[str, dict[str, Any]] = Field(default_factory=dict)


def _bars(db, account_id: UUID, symbol: str, timeframe: str, count: int) -> tuple[list[dict], str]:
    row = db.execute('SELECT max(bar_time) AS t FROM md_bars WHERE account_id=%s AND broker_symbol=%s', (str(account_id), symbol)).fetchone()
    if not row or not row['t']:
        raise HTTPException(409, f'There are no stored bars for {symbol} on this account yet.')
    end = row['t'] + timedelta(minutes=1)   # windows end at the newest stored bar (market time), never the machine clock
    start = end - timedelta(minutes=TF_MIN[timeframe] * (count + 1))
    bars, source = backtest.load_bars(db, account_id, symbol, timeframe, start, end)
    bars = bars[-count:]
    if not bars:
        raise HTTPException(409, f'There are no stored {timeframe} bars for {symbol} in that window.')
    return bars, source


def _scalars(raw: dict[str, dict[str, Any]], db, account_id: UUID, symbol: str) -> dict[str, dict]:
    """Single values for the run. Money, prices and quantities must be decimal strings (a JSON number is a binary float
    and is refused by the source node). The latest stored quote fills Quote when it is not given."""
    out: dict[str, dict] = {}
    for src, fields in raw.items():
        if src not in SCALAR_SOURCES:
            raise HTTPException(422, f"Unknown single-value source {src}; use {', '.join(SCALAR_SOURCES)}.")
        conv = {}
        for k, v in fields.items():
            if isinstance(v, str) and src != 'settings' and k not in ('currency', 'side'):
                try:
                    v = Decimal(v)
                except InvalidOperation:
                    raise HTTPException(422, f'{src}.{k} must be a decimal number written as text, e.g. "1.08512".') from None
            conv[k] = v
        out[src] = conv
    if 'quote' not in out:
        q = db.execute('SELECT bid, ask FROM md_latest WHERE account_id=%s AND broker_symbol=%s', (str(account_id), symbol)).fetchone()
        if q and q['bid'] is not None and q['ask'] is not None:
            bid, ask = Decimal(str(q['bid'])), Decimal(str(q['ask']))
            out['quote'] = {'bid': bid, 'ask': ask, 'mid': (bid + ask) / 2, 'spread': ask - bid}
    return out


@router.post('/preview')
def preview(body: PreviewIn, response: Response, user: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('write') as db:
        version_row = None
        if body.definition is not None:
            definition, code = body.definition, None
        elif body.code:
            version_row = cstore.version(db, body.code, body.version)
            if version_row is None:
                raise HTTPException(404, f'Calculation graph {body.code} has no version {body.version or "yet"}.')
            definition, code = version_row['definition'], body.code
        else:
            raise HTTPException(422, 'Send a definition, or the code (and version) of a stored graph.')
        try:
            plan = compile_graph(definition, body.parameters)
        except GraphInvalid as exc:
            return {'ok': False, 'valid': False, 'errors': exc.errors}
        bars, source = _bars(db, body.account_id, body.symbol, body.timeframe, body.bars)
        ctx = Ctx(bars, body.timeframe, _scalars(body.scalars, db, body.account_id, body.symbol))
        result = execute(plan, ctx, body.trace_bar)
        run_id = cstore.record_run(db, kind='PREVIEW', actor=user, plan=plan, result=result, code=code,
                                   version_id=version_row.get('id') if version_row else None, account_id=body.account_id, symbol=body.symbol,
                                   timeframe=body.timeframe, last_bar_time=bars[-1]['bar_time'], parameters=body.parameters)
    times = [b['bar_time'] for b in bars]
    k = min(body.tail, len(bars))

    def tail(v):
        return v[-k:] if isinstance(v, list) else v
    outputs = None if result.outputs is None else {name: T.to_jsonable(tail(v)) for name, v in result.outputs.items()}
    return {'ok': result.ok, 'valid': True, 'errors': result.errors, 'outputs': outputs, 'times': [t.isoformat() for t in times[-k:]],
            'trace': result.trace, 'trace_bar': result.bar, 'trace_time': None if result.bar is None or result.bar < 0 else times[result.bar].isoformat(),
            'bars': len(bars), 'source': source, 'ms': result.ms, 'warmup': result.warmup, 'hash': plan.hash, 'order': [pn.id for pn in plan.order],
            'run_id': run_id}


@router.get('/runs')
def runs(response: Response, limit: int = Query(50, ge=1, le=500), _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': T.to_jsonable([{k: (v.isoformat() if isinstance(v, datetime) else str(v) if isinstance(v, UUID) else v)
                                         for k, v in r.items()} for r in cstore.recent_runs(db, limit)])}



# ---------- life cycle (DEC-93 phase 3) ----------
_ops = require_role('TRADING_OPS', 'RISK_APPROVER')
CODE = r'^[A-Z][A-Z0-9_]{1,59}$'


class GraphIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(pattern=CODE)
    name: str = Field(min_length=1, max_length=120)
    purpose: Literal['FEATURE', 'SIGNAL', 'STRATEGY_RULE', 'SIZING', 'PROTECTIVE', 'RISK_CHECK', 'ALLOCATION', 'MEASURE']
    description: str = Field('', max_length=1000)
    definition: dict
    notes: str = Field('', max_length=2000)
    reason: str = Field(min_length=5, max_length=500)


@router.post('/graphs', status_code=201)
def create_graph(body: GraphIn, user: Principal = Depends(_ops)):
    with store.transaction('write') as db:
        return T.to_jsonable(cstore.clean_version(cstore.create(db, user.username, body.code, body.name, body.purpose, body.description,
                                                                body.definition, body.notes, body.reason)))


class CopyIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(pattern=CODE)
    name: str = Field(min_length=1, max_length=120)
    version: int | None = Field(None, ge=1)
    reason: str = Field(min_length=5, max_length=500)


@router.post('/graphs/{src}/copy', status_code=201)
def copy_graph(src: str, body: CopyIn, user: Principal = Depends(_ops)):
    with store.transaction('write') as db:
        return T.to_jsonable(cstore.clean_version(cstore.copy(db, user.username, src, body.version, body.code, body.name, body.reason)))


class DraftIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    definition: dict
    notes: str = Field('', max_length=2000)
    reason: str = Field(min_length=5, max_length=500)


@router.put('/graphs/{code}/draft')
def save_draft(code: str, body: DraftIn, user: Principal = Depends(_ops)):
    with store.transaction('write') as db:
        return T.to_jsonable(cstore.clean_version(cstore.save_draft(db, user.username, code, body.definition, body.notes, body.reason)))


class ReasonIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason: str = Field(min_length=5, max_length=500)


@router.post('/graphs/{code}/versions/{n}/{action}')
def version_action(code: str, n: int, action: Literal['propose', 'approve', 'reject', 'retire'], body: ReasonIn, user: Principal = Depends(_ops)):
    with store.transaction('write') as db:
        return T.to_jsonable(cstore.clean_version(cstore.transition(db, user.username, user.has('RISK_APPROVER'), code, n, action, body.reason)))


@router.get('/graphs/{code}/usage')
def graph_usage(code: str, response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': T.to_jsonable(cstore.usage(db, code))}


@router.get('/bindings')
def list_bindings(response: Response, history: bool = False, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': T.to_jsonable(cstore.bindings(db, active_only=not history)), 'live_kinds': sorted(cstore.LIVE_KINDS),
                'targets': {'SIGNAL_DEFINITION': [d['code'] for d in signal_library.definitions(db)]}}


class BindIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['SIGNAL_DEFINITION', 'DEPLOYMENT', 'FEATURE', 'RISK_CHECK', 'SIZING', 'MEASURE']
    target: str = Field(min_length=1, max_length=120)
    code: str = Field(pattern=CODE)
    version: int | None = Field(None, ge=1)
    parameters: dict[str, float] = Field(default_factory=dict)
    reason: str = Field(min_length=5, max_length=500)


_risk = require_role('RISK_APPROVER')


@router.post('/bindings', status_code=201)
def create_binding(body: BindIn, user: Principal = Depends(_risk)):
    if body.kind not in cstore.LIVE_KINDS:
        raise HTTPException(409, f"Binding a {body.kind.lower().replace('_', ' ')} arrives with its phase; today a graph can score a signal definition.")
    with store.transaction('write') as db:
        return T.to_jsonable(cstore.bind(db, user.username, body.kind, body.target, body.code, body.version, body.parameters, body.reason))


@router.delete('/bindings/{binding_id}')
def end_binding(binding_id: UUID, reason: str = Query(..., min_length=5, max_length=500), user: Principal = Depends(_risk)):
    with store.transaction('write') as db:
        return T.to_jsonable(cstore.unbind(db, user.username, binding_id, reason))


class ParityIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(pattern=CODE)
    version: int | None = Field(None, ge=1)
    account_id: UUID
    symbol: str = Field(min_length=1, max_length=64)
    timeframe: Literal['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'] = 'H1'
    bars: int = Field(600, ge=30, le=5000)
    parameters: dict[str, float] = Field(default_factory=dict)


@router.post('/parity')
def parity(body: ParityIn, user: str = Depends(require_operator)):
    """A graph against the library formula it draws, bar by bar, on stored bars: the largest difference and where."""
    from .calc.builtin import BUILTINS
    with store.transaction('write') as db:
        v = cstore.version(db, body.code, body.version)
        if v is None:
            raise HTTPException(404, f'Calculation graph {body.code} has no version {body.version or "yet"}.')
        ref_kind = BUILTINS.get(body.code, {}).get('kind') or _copied(v, 'kind')
        ref_feature = BUILTINS.get(body.code, {}).get('feature') or _copied(v, 'feature')
        if ref_kind is None and ref_feature is None:
            raise HTTPException(409, 'This graph has no reference formula to compare with.')
        bars, _src = _bars(db, body.account_id, body.symbol, body.timeframe, body.bars)
        plan = compile_graph(v['definition'], body.parameters)
        res = execute(plan, Ctx(bars, body.timeframe))
        if ref_kind:
            lib_params = {k: x for k, x in {**plan.params}.items() if k in signal_library.KINDS[ref_kind]['defaults']}
            ref = [None if r is None else r[0] for r in signal_library.compute(ref_kind, lib_params, bars)]
            mine = res.outputs['score'] if res.ok else [None] * len(bars)
            reference = f'signal_library.compute("{ref_kind}")'
        else:
            from .intel import engine
            ref = [f[ref_feature] for f in engine.features(bars, body.timeframe)]
            mine = res.outputs['value'] if res.ok else [None] * len(bars)
            reference = f'intel.engine.features()["{ref_feature}"]'
        worst, at, mismatch = 0.0, None, 0
        for i, r in enumerate(ref):
            g = mine[i]
            if (r is None) != (g is None):
                mismatch += 1
                continue
            if r is not None and abs(g - r) > worst:
                worst, at = abs(g - r), i
        ok = res.ok and mismatch == 0 and worst <= 1e-9
        run_id = cstore.record_run(db, kind='PARITY', actor=user, plan=plan, result=res, code=body.code, version_id=v['id'], account_id=body.account_id,
                                   symbol=body.symbol, timeframe=body.timeframe, last_bar_time=bars[-1]['bar_time'], parameters=body.parameters)
    return {'ok': ok, 'reference': reference, 'bars': len(bars), 'max_difference': worst,
            'at_bar': at, 'warmup_mismatches': mismatch, 'tolerance': 1e-9, 'errors': res.errors, 'run_id': run_id}


def _copied(v: dict, field: str) -> str | None:
    """The reference of the built-in a desk graph was copied from (its description says "copy of <code> v<n>")."""
    from .calc.builtin import BUILTINS
    text = f"{v.get('description') or ''} {v.get('notes') or ''}"
    for code, b in BUILTINS.items():
        if f'copy of {code} v' in text:
            return b.get(field)
    return None


# ---------- every formula as a graph, and the platform flow (DEC-96) ----------
from .calc import formula_graphs as fg  # noqa: E402

LIVE_FEATURES = {'in_ema': 'ema20', 'in_rsi': 'rsi14', 'in_atr': 'atr_pct', 'in_atr_rank': 'atr_pct_rank100', 'in_bollinger': 'bb_width_pct', 'in_adx': 'adx14', 'in_er': 'er20',
                 'in_rv': 'rv20_pct', 'in_range_pos': 'range_pos20', 'in_returns': 'ret_20_pct', 'in_macd': 'macd_hist', 'in_stoch': 'stoch_k', 'in_donchian': 'donchian_high20',
                 'in_zscore': 'zscore20', 'in_volume': 'rel_volume20', 'in_vwap': 'vwap_dev_pct', 'in_obv': 'obv', 'in_sma': 'bb_mid'}


@router.get('/formulas')
def formula_graphs(response: Response, _: str = Depends(require_operator)):
    response.headers['Cache-Control'] = 'private, max-age=300'
    return {'stages': fg.STAGES, 'items': fg.catalogue(), 'functions': {k: v['doc'] for k, v in E.FUNCTIONS.items()}}


class FormulaIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    values: dict[str, float | bool] = Field(default_factory=dict)


@router.post('/formulas/{fid}/evaluate')
def evaluate_formula(fid: str, body: FormulaIn, _: str = Depends(require_operator)):
    """Work out every step of one formula for the numbers given (the examples where none is given). Pure arithmetic in
    the platform's expression language; nothing is stored or sent."""
    f = fg.get(fid)
    if f is None:
        raise HTTPException(404, f'There is no formula {fid}.')
    unknown = set(body.values) - {i['name'] for i in f['inputs']}
    if unknown:
        raise HTTPException(422, f"{fid} has no input {', '.join(sorted(unknown))}.")
    r = fg.evaluate(fid, body.values)
    return T.to_jsonable({**r, 'worked': fg.worked(f, {**r['inputs'], **r['steps']})})


@router.get('/flow')
def platform_flow(response: Response, _: str = Depends(require_operator)):
    response.headers['Cache-Control'] = 'private, max-age=300'
    return fg.flow()


@router.get('/flow/live')
def platform_flow_live(response: Response, account_id: UUID, symbol: str = Query(..., min_length=1, max_length=64),
                       timeframe: Literal['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'] = 'H1', _: str = Depends(require_operator)):
    """Live values for the flow on one instrument: every feature and every active signal on its newest stored bar."""
    _no_store(response)
    from .intel import engine
    with store.transaction('read') as db:
        bars, source = _bars(db, account_id, symbol, timeframe, 600)
        defs = signal_library.definitions(db, active_only=True)
    feats = engine.features(bars, timeframe)[-1] if bars else {}
    values: dict[str, dict] = {}
    for fid, key in LIVE_FEATURES.items():
        v = feats.get(key)
        values[fid] = {'value': v, 'label': key}
    for d in defs:
        fid = f"sig_{d['kind']}"
        if fid in values or fg.get(fid) is None:
            continue
        try:
            s = signal_library.score_series(d, bars, timeframe)[-1]
        except Exception:  # noqa: BLE001 - a failing definition shows no value
            s = None
        values[fid] = {'value': None if s is None else round(s[0], 2), 'label': d['code']}
    return T.to_jsonable({'bars': len(bars), 'source': source, 'bar_time': bars[-1]['bar_time'] if bars else None, 'close': bars[-1]['close'] if bars else None,
                          'values': values})
