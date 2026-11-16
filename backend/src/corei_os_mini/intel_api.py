"""HTTP API of Stage 2 · Market intelligence, mounted at /api/intel.

Why it exists: the screener, the instrument view and the sessions panel read the feature store through one place.
Purpose: list the screened universe for a timeframe (features, regime, attention score with its parts, live price,
spread and feed quality), one instrument across every timeframe with its feature history, the session map, the
engine's status, and a manual refresh.
Must never: change market data or orders; a refresh only recomputes intel_* rows.
Dependencies: intel.engine, intel.regime, intel.sessions, config_store, operator_auth.
Consumers: ui/src/Intelligence.tsx (Screener, Instrument, Sessions tabs).
Architectural position: Stage 2 boundary.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from . import config_store as store
from .intel import engine, regime, sessions
from .operator_auth import Principal, require_operator, require_role

router = APIRouter(prefix='/api/intel', tags=['market intelligence'])
_ops = require_role('TRADING_OPS')


def _no_store(response: Response) -> None:
    response.headers['Cache-Control'] = 'no-store'


@router.get('/screener')
def screener(response: Response, timeframe: str = 'H1', _: str = Depends(require_operator)):
    _no_store(response)
    if timeframe not in engine.TIMEFRAMES:
        raise HTTPException(422, f"Timeframe must be one of {', '.join(engine.TIMEFRAMES)}.")
    with store.transaction('read') as db:
        rows = db.execute("""
            SELECT s.*, a.code AS account_code, a.name AS account_name, pr.name AS provider_name, c.settings->>'server_name' AS server_name,
                   i.symbol AS instrument, l.bid, l.ask, l.received_at AS price_at,
                   q.score AS quality, q.latency_p95_ms, q.spread_avg
            FROM intel_snapshot s
            JOIN cfg_accounts a ON a.id = s.account_id
            JOIN cfg_connections c ON c.id = a.connection_id
            JOIN cfg_providers pr ON pr.id = c.provider_id
            LEFT JOIN LATERAL (SELECT instrument_id FROM md_collection_plans p WHERE p.account_id = s.account_id
                               AND p.broker_symbol = s.broker_symbol AND p.status <> 'ARCHIVED' ORDER BY p.created_at LIMIT 1) p ON true
            LEFT JOIN cfg_instruments i ON i.id = p.instrument_id
            LEFT JOIN md_latest l ON l.account_id = s.account_id AND l.broker_symbol = s.broker_symbol
            LEFT JOIN LATERAL (SELECT avg(score) AS score, max(latency_p95_ms) AS latency_p95_ms, avg(spread_avg) AS spread_avg
                               FROM md_quality WHERE account_id = s.account_id AND broker_symbol = s.broker_symbol
                                 AND minute > now() - interval '60 minutes') q ON true
            WHERE s.timeframe = %s""", (timeframe,)).fetchall()
    items = []
    for r in rows:
        f = r['features'] or {}
        spread = (r['ask'] - r['bid']) if r['ask'] is not None and r['bid'] is not None else r['spread_avg']
        att = regime.attention(f, r['quality'], spread)
        items.append({**store.clean({k: v for k, v in r.items() if k != 'features'}), 'features': f, 'spread': spread,
                      'spread_atr_pct': None if spread is None or not f.get('atr14') else round(100 * spread / f['atr14'], 1),
                      'attention': att['score'], 'attention_parts': att['parts']})
    items.sort(key=lambda x: (x['attention'] is None, -(x['attention'] or 0)))
    for n, it in enumerate(items, 1):
        it['rank'] = n
    return {'timeframe': timeframe, 'items': items, 'engine': engine.STATUS, 'session': sessions.sessions_at(datetime.now(timezone.utc))}


@router.get('/instrument/{account_id}/{broker_symbol}')
def instrument(account_id: UUID, broker_symbol: str, response: Response, timeframe: str = 'H1', limit: int = 200,
               _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        snaps = [store.clean(r) for r in db.execute(
            'SELECT * FROM intel_snapshot WHERE account_id=%s AND broker_symbol=%s', (account_id, broker_symbol)).fetchall()]
        history = [store.clean(r) for r in db.execute(
            'SELECT bar_time, features, regime FROM intel_features WHERE account_id=%s AND broker_symbol=%s AND timeframe=%s '
            'ORDER BY bar_time DESC LIMIT %s', (account_id, broker_symbol, timeframe, min(max(limit, 10), 2000))).fetchall()]
    if not snaps:
        raise HTTPException(404, 'No features for this instrument yet (it needs collected bars; the engine runs every minute).')
    order = {tf: n for n, tf in enumerate(engine.TIMEFRAMES)}
    snaps.sort(key=lambda s: order.get(s['timeframe'], 99))
    return {'snapshots': snaps, 'history': list(reversed(history)), 'timeframe': timeframe}


@router.get('/sessions')
def session_map(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    now = datetime.now(timezone.utc)
    return {'now': sessions.sessions_at(now), 'next': sessions.next_changes(now), 'table': sessions.week_table()}


@router.get('/status')
def status(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return engine.STATUS


@router.post('/refresh')
async def refresh(_: Principal = Depends(_ops)):
    """Recompute every instrument now (the engine also runs every minute)."""
    return await asyncio.to_thread(engine.refresh_all)
