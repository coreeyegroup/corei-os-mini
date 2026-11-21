"""Closed-bar suitability scans, workflow research and versioned strategy drafts.

Saving and handing off a draft use the existing operator roles. A handoff creates
a calculation graph draft for review through the governed lifecycle.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, Response, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID
from typing import Literal
from .calc_api import _bars as load_stored_bars
from .strategy_preview import preview
from .strategy_templates import template_list, template_get

from .strategy_workflow import Workflow, compile_workflow, catalog

from . import config_store as store, strategy_drafts
from .operator_auth import Principal, require_operator, require_role
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


class DraftPreviewIn(BaseModel):
    workflow: Workflow
    bars: list[dict] = Field(min_length=2, max_length=5000)


@router.post('/builder/preview')
def preview_builder(payload: DraftPreviewIn, response: Response,
                    _: str = Depends(require_operator)):
    response.headers['Cache-Control'] = 'no-store'
    try:
        return preview(payload.workflow, payload.bars)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class StoredDraftPreviewIn(BaseModel):
    workflow: Workflow
    account_id: UUID
    symbol: str = Field(min_length=1, max_length=64)
    timeframe: Literal['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'] = 'H1'
    bars: int = Field(default=300, ge=10, le=5000)


@router.post('/builder/preview-stored')
def preview_stored_builder(payload: StoredDraftPreviewIn, response: Response,
                           _: str = Depends(require_operator)):
    """Evaluate a draft against stored historical bars, without creating orders."""
    response.headers['Cache-Control'] = 'no-store'
    with store.transaction('read') as db:
        bars, source = load_stored_bars(db, payload.account_id, payload.symbol,
                                        payload.timeframe, payload.bars)
    try:
        result = preview(payload.workflow, bars)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {**result, 'source': source, 'symbol': payload.symbol,
            'timeframe': payload.timeframe, 'account_id': str(payload.account_id)}


@router.get('/builder/templates')
def builder_templates(response: Response, _: str = Depends(require_operator)):
    response.headers['Cache-Control'] = 'no-store'
    return template_list()


@router.get('/builder/templates/{template_id}')
def builder_template(template_id: str, response: Response,
                     _: str = Depends(require_operator)):
    response.headers['Cache-Control'] = 'no-store'
    try:
        return template_get(template_id)
    except KeyError:
        raise HTTPException(status_code=404, detail='Unknown strategy template') from None


_draft_writer = require_role('TRADING_OPS', 'RISK_APPROVER')


class SaveDraftIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=0, le=2147483646)
    workflow: Workflow


class PromoteDraftIn(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    code: str = Field(pattern=r'^[A-Z][A-Z0-9_]{1,59}$')
    reason: str = Field(min_length=5, max_length=500)


@router.get('/drafts')
def list_drafts(response: Response, _: str = Depends(require_operator)):
    response.headers['Cache-Control'] = 'no-store'
    with store.transaction('read') as db:
        return {'items': strategy_drafts.drafts(db)}


@router.get('/drafts/{draft_id}/versions')
def list_draft_versions(draft_id: UUID, response: Response, _: str = Depends(require_operator)):
    response.headers['Cache-Control'] = 'no-store'
    with store.transaction('read') as db:
        return {'items': strategy_drafts.versions(db, draft_id)}


@router.get('/drafts/{draft_id}/versions/{number}')
def get_draft_version(draft_id: UUID, response: Response, number: int = Path(ge=1, le=2147483647),
                      _: str = Depends(require_operator)):
    response.headers['Cache-Control'] = 'no-store'
    with store.transaction('read') as db:
        return strategy_drafts.version(db, draft_id, number)


@router.put('/drafts/{draft_id}')
def save_draft(draft_id: UUID, payload: SaveDraftIn, response: Response,
               user: Principal = Depends(_draft_writer)):
    response.headers['Cache-Control'] = 'no-store'
    with store.transaction('write') as db:
        return strategy_drafts.save(db, draft_id, payload.expected_version, payload.workflow, user.username)


@router.post('/drafts/{draft_id}/versions/{number}/promote')
def promote_draft(draft_id: UUID, payload: PromoteDraftIn, response: Response,
                  number: int = Path(ge=1, le=2147483647), user: Principal = Depends(_draft_writer)):
    response.headers['Cache-Control'] = 'no-store'
    with store.transaction('write') as db:
        return strategy_drafts.promote(db, draft_id, number, payload.code, payload.reason, user.username)
