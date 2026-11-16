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
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import config_store as store
from .intel import analytics, engine, regime, sessions, workspace
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
                   i.symbol AS instrument, i.asset_class, i.base_currency, i.quote_currency, p.instrument_id, p.plan_id, l.bid, l.ask, l.received_at AS price_at,
                   q.score AS quality, q.latency_p95_ms, q.spread_avg
            FROM intel_snapshot s
            JOIN cfg_accounts a ON a.id = s.account_id
            JOIN cfg_connections c ON c.id = a.connection_id
            JOIN cfg_providers pr ON pr.id = c.provider_id
            LEFT JOIN LATERAL (SELECT id AS plan_id, instrument_id FROM md_collection_plans p WHERE p.account_id = s.account_id
                               AND p.broker_symbol = s.broker_symbol AND p.status <> 'ARCHIVED' ORDER BY p.created_at LIMIT 1) p ON true
            LEFT JOIN cfg_instruments i ON i.id = p.instrument_id
            LEFT JOIN md_latest l ON l.account_id = s.account_id AND l.broker_symbol = s.broker_symbol
            LEFT JOIN LATERAL (SELECT avg(score) AS score, max(latency_p95_ms) AS latency_p95_ms, avg(spread_avg) AS spread_avg
                               FROM md_quality WHERE account_id = s.account_id AND broker_symbol = s.broker_symbol
                                 AND minute > now() - interval '60 minutes') q ON true
            WHERE s.timeframe = %s""", (timeframe,)).fetchall()
        everything = db.execute('SELECT account_id, broker_symbol, timeframe, regime FROM intel_snapshot').fetchall()
        window = (engine.settings().get('event_window_minutes') or 30)
        now = datetime.now(timezone.utc)
        upcoming = [store.clean(e) for e in db.execute("SELECT * FROM intel_events WHERE at BETWEEN %s AND %s AND impact IN ('HIGH', 'HOLIDAY')",
                                                       (now - timedelta(minutes=window), now + timedelta(hours=24))).fetchall()]
    by_pair: dict = {}
    for x in everything:
        by_pair.setdefault((x['account_id'], x['broker_symbol']), []).append(x)
    items = []
    for r in rows:
        f = r['features'] or {}
        spread = (r['ask'] - r['bid']) if r['ask'] is not None and r['bid'] is not None else r['spread_avg']
        att = regime.attention(f, r['quality'], spread)
        risk = workspace.event_risk(r['broker_symbol'], r['base_currency'], r['quote_currency'], now, window, upcoming)
        nxt = sorted((e for e in upcoming if analytics.currencies_of(r['broker_symbol'], r['base_currency'], r['quote_currency'])
                      and (e['currency'] in analytics.currencies_of(r['broker_symbol'], r['base_currency'], r['quote_currency']) or e['currency'] == 'ALL')
                      and e['at'] >= now), key=lambda e: e['at'])
        items.append({**store.clean({k: v for k, v in r.items() if k != 'features'}), 'features': f, 'spread': spread,
                      'alignment': analytics.alignment(by_pair.get((r['account_id'], r['broker_symbol']), [])),
                      'event_risk': [{'at': e['at'], 'currency': e['currency'], 'title': e['title'], 'impact': e['impact']} for e in risk],
                      'next_event': ({'at': nxt[0]['at'], 'currency': nxt[0]['currency'], 'title': nxt[0]['title']} if nxt else None),
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
            'SELECT bar_time, features, regime FROM intel_features WHERE account_id=%s AND broker_symbol=%s AND timeframe=%s AND version=%s '
            'ORDER BY bar_time DESC LIMIT %s', (account_id, broker_symbol, timeframe, engine.ind.FEATURE_VERSION, min(max(limit, 10), 2000))).fetchall()]
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
    """Recompute every instrument now (also when the engine is paused)."""
    return await asyncio.to_thread(engine.refresh_all, True)


# ----- Dashboard: overview and controls -----

@router.get('/overview')
def overview(response: Response, timeframe: str = 'H1', _: str = Depends(require_operator)):
    """Coverage, regime mix per timeframe, what is still warming up (and what history it needs), the top of the
    screen, the biggest moves, stale features, the engine and its settings, and the session now."""
    _no_store(response)
    with store.transaction('read') as db:
        mix = db.execute('SELECT timeframe, regime, volatility, count(*) AS n FROM intel_snapshot GROUP BY 1, 2, 3').fetchall()
        collected = db.execute("SELECT count(DISTINCT (account_id, broker_symbol)) AS n FROM md_collection_plans WHERE status <> 'ARCHIVED'").fetchone()['n']
        covered = db.execute('SELECT count(DISTINCT (account_id, broker_symbol)) AS n FROM intel_snapshot').fetchone()['n']
        stale = db.execute("SELECT count(*) AS n FROM intel_snapshot WHERE computed_at < now() - interval '10 minutes'").fetchone()['n']
        history = db.execute('SELECT count(*) AS n, min(bar_time) AS since FROM intel_features').fetchone()
    by_tf: dict = {}
    for r in mix:
        t = by_tf.setdefault(r['timeframe'], {'total': 0, 'regime': {}, 'volatility': {}})
        t['total'] += r['n']
        t['regime'][r['regime']] = t['regime'].get(r['regime'], 0) + r['n']
        if r['volatility']:
            t['volatility'][r['volatility']] = t['volatility'].get(r['volatility'], 0) + r['n']
    screen = screener(Response(), timeframe, '')['items']
    warming = [{k: x[k] for k in ('account_id', 'account_code', 'provider_name', 'broker_symbol', 'instrument', 'plan_id', 'bars', 'reasons')}
               for x in screen if x['regime'] == 'WARMING_UP']
    ranked = [x for x in screen if x['attention'] is not None]
    movers = sorted((x for x in screen if x['features'].get('ret_20_pct') is not None), key=lambda x: -abs(x['features']['ret_20_pct']))
    slim = lambda x: {k: x.get(k) for k in ('account_id', 'broker_symbol', 'instrument', 'account_code', 'provider_name', 'regime', 'volatility',
                                            'confidence', 'attention', 'attention_parts', 'reasons')} | {'ret_20_pct': x['features'].get('ret_20_pct'),
                                                                                                         'adx14': x['features'].get('adx14'), 'rsi14': x['features'].get('rsi14')}
    return {'timeframe': timeframe, 'collected': collected, 'covered': covered, 'stale': stale, 'history_rows': history['n'],
            'history_since': history['since'], 'by_timeframe': by_tf, 'warming_up': warming, 'top': [slim(x) for x in ranked[:8]],
            'movers': [slim(x) for x in movers[:8]], 'engine': engine.STATUS, 'settings': store.clean(engine.settings()),
            'session': sessions.sessions_at(datetime.now(timezone.utc)),
            'events_next_24h': workspace.events(datetime.now(timezone.utc), datetime.now(timezone.utc) + timedelta(hours=24)),
            'recent_fires': workspace.fires(10), 'rules': len(workspace.rules()), 'watchlists': len(workspace.watchlists())}


class SettingsIn(BaseModel):
    enabled: bool
    interval_seconds: int = Field(ge=15, le=3600)
    timeframes: list[Literal['M1', 'M5', 'M15', 'H1', 'H4', 'D1']] = Field(min_length=1)
    excluded: list[dict] = Field(default_factory=list, max_length=2000)
    retention_days: int = Field(default=365, ge=7, le=3650)
    event_window_minutes: int = Field(default=30, ge=0, le=720)
    version: int
    reason: str = Field(min_length=5, max_length=500)


@router.put('/settings')
def put_settings(payload: dict, user: Principal = Depends(_ops)):
    from .config_api import parse
    data = parse(SettingsIn, payload)
    excluded = [{'account_id': str(x.get('account_id')), 'broker_symbol': str(x.get('broker_symbol'))} for x in data.excluded
                if x.get('account_id') and x.get('broker_symbol')]
    with store.transaction('write') as db:
        before = db.execute('SELECT * FROM intel_settings WHERE id=1 FOR UPDATE').fetchone()
        if not before:
            raise HTTPException(503, 'Run scripts/deploy-workstation.sh to apply migrations (intel_settings is missing).')
        if before['version'] != data.version:
            raise HTTPException(409, 'The engine settings were changed by someone else: reload and try again.')
        db.execute('UPDATE intel_settings SET enabled=%s, interval_seconds=%s, timeframes=%s, excluded=%s, retention_days=%s, '
                   'event_window_minutes=%s, version=version+1, updated_by=%s, updated_at=now() WHERE id=1',
                   (data.enabled, data.interval_seconds, list(data.timeframes), Jsonb(excluded), data.retention_days, data.event_window_minutes,
                    user.username))
        after = db.execute('SELECT * FROM intel_settings WHERE id=1').fetchone()
        store.audit(db, user.username, 'INTEL_SETTINGS', 'intel_settings', '1', store.clean(before), store.clean(after), data.reason)
    return store.clean(after)


class HistoryIn(BaseModel):
    plan_id: UUID
    reason: str = Field(min_length=5, max_length=500)


@router.post('/history')
def get_history(payload: dict, user: Principal = Depends(_ops)):
    """One click for a warming-up instrument: backfill 14 days of M1 (builds M5…H1) and a year of H4 and D1 from the broker."""
    from .config_api import parse
    from .md import plans
    data = parse(HistoryIn, payload)
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    jobs = []
    for tf, days in (('M1', 14), ('H4', 365), ('D1', 365)):
        jobs.append(plans.create_job(user.username, plans.BackfillIn(plan_id=data.plan_id, timeframe=tf, range_from=now - timedelta(days=days),
                                                                      range_to=now, reason=data.reason)))
    return {'jobs': jobs}


# ----- Multi-source, correlation, currency strength -----

@router.get('/instruments')
def collected_instruments(response: Response, _: str = Depends(require_operator)):
    """Canonical instruments being collected, with how many sources (broker accounts) collect each."""
    _no_store(response)
    with store.transaction('read') as db:
        rows = db.execute("""SELECT i.id, i.symbol, i.name, i.asset_class, count(DISTINCT p.account_id) AS sources
                             FROM md_collection_plans p JOIN cfg_instruments i ON i.id = p.instrument_id
                             WHERE p.status <> 'ARCHIVED' GROUP BY i.id ORDER BY i.symbol""").fetchall()
    return {'items': [store.clean(r) for r in rows]}


@router.get('/sources/{instrument_id}')
def instrument_sources(instrument_id: UUID, response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    out = analytics.sources(str(instrument_id))
    if not out['instrument']:
        raise HTTPException(404, 'Instrument not found.')
    return out


@router.get('/correlation')
def correlation(response: Response, timeframe: str = 'H1', window: int = 100, _: str = Depends(require_operator)):
    _no_store(response)
    if timeframe not in engine.HISTORY_TIMEFRAMES:
        raise HTTPException(422, f"Timeframe must be one of {', '.join(engine.HISTORY_TIMEFRAMES)}.")
    return analytics.correlation(timeframe, min(max(window, 20), 1000))


@router.get('/strength')
def strength(response: Response, timeframe: str = 'H1', bars: int = 20, _: str = Depends(require_operator)):
    _no_store(response)
    if timeframe not in engine.HISTORY_TIMEFRAMES:
        raise HTTPException(422, f"Timeframe must be one of {', '.join(engine.HISTORY_TIMEFRAMES)}.")
    return analytics.currency_strength(timeframe, min(max(bars, 2), 500))


# ----- Watchlists -----

@router.get('/watchlists')
def list_watchlists(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': workspace.watchlists()}


@router.post('/watchlists', status_code=201)
def create_watchlist(payload: dict, user: Principal = Depends(_ops)):
    from .config_api import parse
    return workspace.save_watchlist(user.username, parse(workspace.WatchlistIn, payload))


@router.put('/watchlists/{wid}')
def update_watchlist(wid: UUID, payload: dict, user: Principal = Depends(_ops)):
    from .config_api import parse
    version = payload.pop('version', None)
    return workspace.save_watchlist(user.username, parse(workspace.WatchlistIn, payload), wid, version)


@router.delete('/watchlists/{wid}', status_code=204)
def remove_watchlist(wid: UUID, user: Principal = Depends(_ops)):
    workspace.delete_watchlist(user.username, wid)


# ----- Condition rules -----

@router.get('/rules')
def list_rules(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': workspace.rules(), 'fields': list(workspace.FIELDS)}


@router.post('/rules', status_code=201)
def create_rule(payload: dict, user: Principal = Depends(_ops)):
    from .config_api import parse
    return workspace.save_rule(user.username, parse(workspace.RuleIn, payload))


@router.put('/rules/{rid}')
def update_rule(rid: UUID, payload: dict, user: Principal = Depends(_ops)):
    from .config_api import parse
    version = payload.pop('version', None)
    return workspace.save_rule(user.username, parse(workspace.RuleIn, payload), rid, version)


@router.delete('/rules/{rid}', status_code=204)
def remove_rule(rid: UUID, user: Principal = Depends(_ops)):
    workspace.delete_rule(user.username, rid)


@router.get('/fires')
def rule_fires(response: Response, limit: int = 100, _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': workspace.fires(min(max(limit, 1), 1000))}


# ----- Economic calendar -----

@router.get('/events')
def list_events(response: Response, days_back: int = 1, days_ahead: int = 7, _: str = Depends(require_operator)):
    _no_store(response)
    now = datetime.now(timezone.utc)
    return {'items': workspace.events(now - timedelta(days=min(max(days_back, 0), 90)), now + timedelta(days=min(max(days_ahead, 0), 90)))}


@router.post('/events', status_code=201)
def add_event(payload: dict, user: Principal = Depends(_ops)):
    from .config_api import parse
    return {'added': workspace.add_events(user.username, [parse(workspace.EventIn, payload)])}


@router.post('/events/import')
def import_events(payload: dict, user: Principal = Depends(_ops)):
    """CSV text (datetime_utc,currency,impact,title[,forecast,previous,actual]); all rows valid or nothing imported."""
    return {'added': workspace.add_events(user.username, workspace.parse_csv(str(payload.get('csv') or '')), 'CSV')}


@router.delete('/events/{eid}', status_code=204)
def remove_event(eid: UUID, user: Principal = Depends(_ops)):
    workspace.delete_event(user.username, eid)
