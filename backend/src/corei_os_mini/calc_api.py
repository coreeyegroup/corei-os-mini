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
from .operator_auth import require_operator
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
            version_row = cstore.version(db, body.code, body.version or 1)
            if version_row is None:
                raise HTTPException(404, f'Calculation graph {body.code} has no version {body.version or 1}.')
            definition, code = version_row['definition'], body.code
        else:
            raise HTTPException(422, 'Send a definition, or the code (and version) of a stored graph.')
        try:
            plan = compile_graph(definition, body.parameters)
        except GraphInvalid as exc:
            return {'ok': False, 'valid': False, 'errors': exc.errors}
        row = db.execute('SELECT max(bar_time) AS t FROM md_bars WHERE account_id=%s AND broker_symbol=%s', (str(body.account_id), body.symbol)).fetchone()
        if not row or not row['t']:
            raise HTTPException(409, f'There are no stored bars for {body.symbol} on this account yet.')
        end = row['t'] + timedelta(minutes=1)   # windows end at the newest stored bar (market time), never the machine clock
        start = end - timedelta(minutes=TF_MIN[body.timeframe] * (body.bars + 1))
        bars, source = backtest.load_bars(db, body.account_id, body.symbol, body.timeframe, start, end)
        bars = bars[-body.bars:]
        if not bars:
            raise HTTPException(409, f'There are no stored {body.timeframe} bars for {body.symbol} in that window.')
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

