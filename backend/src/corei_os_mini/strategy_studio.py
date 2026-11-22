"""Closed-bar suitability scans, workflow research and versioned strategy drafts.

Saving and handing off a draft use the existing operator roles. A handoff creates
a calculation graph draft for review through the governed lifecycle.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import cache
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .calc_api import _bars as load_stored_bars
from .strategy_preview import preview
from .strategy_templates import template_list, template_get

from .strategy_workflow import Workflow, compile_workflow, catalog

from . import config_store as store, strategy_drafts
from .operator_auth import Principal, require_operator, require_role
from .intel import engine
from .trading import algorithms, runtime, signals
from .trading.common import D, TIMEFRAME_SECONDS, data_source, deployments, quantize

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


StrategyCode = Annotated[str, Field(pattern=r'^[A-Z][A-Z0-9_]{1,31}$')]


class SignalScanIn(BaseModel):
    model_config = ConfigDict(extra='forbid')

    instrument_symbol: str | None = Field(None, pattern=r'^[A-Z0-9][A-Z0-9._/-]{0,31}$')
    strategy_code: StrategyCode | None = None
    strategy_codes: list[StrategyCode] | None = Field(None, min_length=1)
    stop_atr: Decimal = Field(Decimal('1.5'), ge=Decimal('0.1'), le=Decimal('20'), decimal_places=2)
    reward_risk: Decimal = Field(Decimal('2'), ge=Decimal('0.1'), le=Decimal('20'), decimal_places=2)

    @model_validator(mode='after')
    def validate_scope(self):
        if bool(self.instrument_symbol) == bool(self.strategy_code) or self.strategy_code and self.strategy_codes:
            raise ValueError('Choose one instrument and optional strategy filters, or one strategy across instruments.')
        return self


def _plan_signal(strategy: dict, instrument: dict, bars: list[dict], features: list[dict],
                 payload: SignalScanIn, now: datetime) -> dict:
    """Price a fresh entry at the signal bar's close, using the runtime's protective-level calculation."""
    timeframe = strategy['parameters']['timeframe']
    base = {'bar_time': bars[-1]['bar_time'], 'reference_close': str(bars[-1]['close'])}
    bar_length = timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
    closed_at = bars[-1]['bar_time'] + bar_length
    if closed_at > now or now - closed_at > 2 * bar_length:
        return {**base, 'status': 'WAIT', 'reason': 'Latest closed bar is outside the scan freshness window'}

    decision = classify(strategy, bars, features)
    side = decision['side']
    if not side or not decision['changed_on_last_bar']:
        return {**base, 'status': 'WAIT', 'reason': decision['reason']}

    close, atr, tick = D(bars[-1]['close']), D(features[-1].get('atr14')), D(instrument['tick_size'])
    if close is None or not close.is_finite() or close <= 0 or atr is None or not atr.is_finite() or atr <= 0:
        return {**base, 'status': 'WAIT', 'reason': 'A positive closed-bar price and ATR14 are required for protective levels'}
    entry = quantize(close, tick)
    direction = Decimal(1) if side == 'BUY' else Decimal(-1)
    stop, _ = signals.protective_levels({'protect': {'stop_atr': payload.stop_atr}}, decision['target'],
                                       direction, Decimal(0), {'ask': entry, 'bid': entry}, atr)
    if stop is not None:
        stop = quantize(stop, tick)
    if not stop or min(entry, stop) <= 0 or (entry - stop) * direction <= 0:
        return {**base, 'status': 'WAIT', 'reason': 'Stop loss does not fit the instrument tick size or price range'}
    risk_distance = abs(entry - stop)
    take = quantize(entry + direction * risk_distance * payload.reward_risk, tick)
    if take <= 0 or (take - entry) * direction <= 0:
        return {**base, 'status': 'WAIT', 'reason': 'Take profit does not fit the instrument tick size or price range'}
    precision = tick.normalize()
    return {
        **base, 'status': 'SIGNAL', 'side': side, 'strength': max(0, min(100, decision['strength'])),
        'entry': format(entry.quantize(precision), 'f'), 'stop_loss': format(stop.quantize(precision), 'f'),
        'take_profit': format(take.quantize(precision), 'f'),
        'reward_risk': str(round(abs(take - entry) / risk_distance, 2)),
        'risk_per_contract': format((risk_distance * instrument['contract_size']).normalize(), 'f'),
        'quote_currency': instrument['quote_currency'], 'reason': decision['reason'],
    }


@router.post('/signal-scan')
def signal_scan(payload: SignalScanIn, response: Response, _: str = Depends(require_operator)):
    """Evaluate registered strategies on stored market data, with no deployment or order writes."""
    response.headers['Cache-Control'] = 'no-store'
    now = datetime.now(timezone.utc)
    out = []
    codes = [payload.strategy_code] if payload.strategy_code else payload.strategy_codes
    with store.transaction('read') as db:
        instruments = db.execute("""
            SELECT id, symbol, tick_size, contract_size, quote_currency FROM cfg_instruments
            WHERE status='ACTIVE' AND (%s::text IS NULL OR symbol=%s) ORDER BY symbol
        """, (payload.instrument_symbol, payload.instrument_symbol)).fetchall()
        strategies = db.execute("""
            SELECT s.code, v.number, v.algorithm, v.parameters
            FROM cfg_strategies s LEFT JOIN LATERAL (
                SELECT number, algorithm, parameters FROM cfg_strategy_versions
                WHERE strategy_id=s.id AND state='APPROVED' ORDER BY number DESC LIMIT 1
            ) v ON true WHERE s.status='ACTIVE' AND (%s::text[] IS NULL OR s.code=ANY(%s::text[]))
            ORDER BY s.code
        """, (codes, codes)).fetchall()

        if payload.instrument_symbol and not instruments:
            raise HTTPException(404, 'Instrument is no longer active or could not be found.')
        if codes and set(codes) - {s['code'] for s in strategies}:
            raise HTTPException(404, 'One or more selected strategies are no longer active or could not be found.')

        sources = defaultdict(list)
        for row in db.execute("""
            SELECT p.instrument_id, p.account_id, a.code AS account_code, p.broker_symbol
            FROM md_collection_plans p JOIN cfg_accounts a ON a.id=p.account_id
            WHERE p.instrument_id=ANY(%s::uuid[]) AND p.status<>'ARCHIVED'
            ORDER BY CASE p.status WHEN 'ACTIVE' THEN 0 ELSE 1 END,
                     CASE p.feed_role WHEN 'PRIMARY' THEN 0 WHEN 'BACKUP' THEN 1 ELSE 2 END, p.created_at, p.id
        """, ([i['id'] for i in instruments],)).fetchall():
            sources[row['instrument_id']].append(row)

        custom = engine.custom_definitions()

        @cache
        def closed_bars(account_id, symbol, timeframe, window):
            native = engine._load(db, account_id, symbol, timeframe, window)
            if timeframe == 'M1':
                return (native,)
            expected = engine._bucket(now, timeframe) - timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
            if len(native) == window and native[-1]['bar_time'] >= expected:
                return (native,)
            # A long but delayed native feed must not hide a fresher M1-derived series.
            built = engine.resample(engine._load(db, account_id, symbol, 'M1'), timeframe)
            return native, built[-window:]

        @cache
        def features_for(account_id, symbol, timeframe, window, series_index):
            definitions = [d for d in custom if timeframe in (d.get('timeframes') or engine.TIMEFRAMES)]
            return engine.features(closed_bars(account_id, symbol, timeframe, window)[series_index], timeframe, definitions)

        for strategy in strategies:
            for instrument in instruments:
                tf = (strategy['parameters'] or {}).get('timeframe')
                base = {'instrument_symbol': instrument['symbol'], 'strategy_code': strategy['code'],
                        'version': strategy['number'], 'timeframe': tf}
                try:
                    if strategy['number'] is None:
                        result = {'status': 'WAIT', 'reason': 'Strategy has no approved version'}
                    else:
                        needed = max(15, algorithms.warmup(strategy['algorithm'], strategy['parameters']) + 2)
                        window = max(engine.WINDOW, needed)
                        candidates = [
                            (bars, source, index)
                            for source in sources[instrument['id']]
                            for index, bars in enumerate(closed_bars(source['account_id'], source['broker_symbol'], tf, window))
                        ]
                        available = [candidate for candidate in candidates if len(candidate[0]) >= needed]
                        if not available:
                            count = max((len(bars) for bars, _, _ in candidates), default=0)
                            reason = f'Insufficient closed bars: {count} of {needed} required' if candidates else 'No mapped market-data source'
                            result = {'status': 'WAIT', 'reason': reason}
                        else:
                            bars, source, index = max(available, key=lambda candidate: candidate[0][-1]['bar_time'])
                            rows = features_for(source['account_id'], source['broker_symbol'], tf, window, index)
                            result = {**_plan_signal(strategy, instrument, bars, rows, payload, now),
                                      'account_code': source['account_code'], 'broker_symbol': source['broker_symbol']}
                except (ArithmeticError, KeyError, TypeError, ValueError) as exc:
                    result = {'status': 'ERROR', 'reason': f'Cannot evaluate: {type(exc).__name__}'}
                out.append({**base, **result})

    out.sort(key=lambda row: (row['status'] != 'SIGNAL', -row.get('strength', 0), row['instrument_symbol'], row['strategy_code']))
    return {'items': out, 'scanned_at': now, 'stop_atr': payload.stop_atr, 'reward_risk': payload.reward_risk}


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
