"""Read-only strategy suitability scan over existing ACTIVE deployments.

This does not create signals, risk decisions or orders. It reuses the exact
algorithm used by the live runtime/backtest, with closed bars only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, HTTPException
from pydantic import ValidationError

from .strategy_workflow import Workflow, compile_workflow, catalog

from . import config_store as store
from .operator_auth import require_operator
from .trading import algorithms, runtime
from .trading.common import data_source, deployments

router = APIRouter(prefix='/api/trading/studio', tags=['strategy-studio'])


def classify(dep: dict, bars: list[dict], features: list[dict]) -> dict:
    """Pure decision projection; never converts a target into an executable order."""
    need = algorithms.warmup(dep['algorithm'], dep['parameters'] or {})
    if len(bars) < need + 2:
        return {'status': 'WAIT', 'reason': f'Insufficient closed bars: {len(bars)} of {need + 2} required'}
    walk = algorithms.run(dep['algorithm'], dep['parameters'], bars, features)
    last = walk[-1]
    target = last['target']
    return {
        'status': 'QUALIFIED' if target in ('LONG', 'SHORT') else 'WAIT',
        'target': target,
        'side': {'LONG': 'BUY', 'SHORT': 'SELL'}.get(target),
        'changed_on_last_bar': bool(last['change']),
        'strength': last['strength'] if last['change'] else None,
        'reason': last['reason'] if last['change'] else ('Existing position target; no new entry trigger' if target != 'FLAT' else 'No qualifying entry'),
        'bar_time': bars[-1]['bar_time'].isoformat(),
        'reference_close': str(bars[-1]['close']),
    }


@router.get('/scan')
def scan(response: Response, strategy_code: str | None = Query(None, max_length=32),
         instrument_symbol: str | None = Query(None, max_length=64),
         limit: int = Query(50, ge=1, le=100),
         _: str = Depends(require_operator)):
    response.headers['Cache-Control'] = 'no-store'
    if not strategy_code and not instrument_symbol:
        return {'items': [], 'scope': 'ACTIVE_DEPLOYMENTS_ONLY', 'notice': 'Select an instrument or strategy to scan.'}
    out = []
    with store.transaction('read') as db:
        candidates = [d for d in deployments(db) if
                      (not strategy_code or d['strategy_code'] == strategy_code) and
                      (not instrument_symbol or d['instrument_symbol'] == instrument_symbol)][:limit]
        for dep in candidates:
            base = {'deployment_id': str(dep['id']), 'strategy_code': dep['strategy_code'],
                    'instrument_symbol': dep['instrument_symbol'], 'account_code': dep['account_code'],
                    'algorithm': dep['algorithm'], 'timeframe': (dep['parameters'] or {}).get('timeframe', 'H1')}
            try:
                src = data_source(db, dep)
                if not src:
                    result = {'status': 'WAIT', 'reason': 'No collected market-data source'}
                else:
                    bars, features = runtime.series(db, src, base['timeframe'])
                    result = classify(dep, bars, features)
            except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
                result = {'status': 'ERROR', 'reason': f'Cannot evaluate: {type(exc).__name__}'}
            out.append({**base, **result})
    return {'items': out, 'scope': 'ACTIVE_DEPLOYMENTS_ONLY',
            'notice': 'Read-only closed-bar evaluation. Target is not an entry signal; no entry, stop, target, sizing or order is implied.'}


@router.get('/builder/nodes')
def builder_nodes(response: Response, _: str = Depends(require_operator)):
    response.headers['Cache-Control'] = 'no-store'
    return catalog()


@router.post('/builder/validate')
def validate_builder(payload: Workflow, response: Response,
                     _: str = Depends(require_operator)):
    response.headers['Cache-Control'] = 'no-store'
    try:
        return compile_workflow(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
