"""Market analysis snapshot: one request, many instruments and timeframes, with provenance, quality and honest gaps.

Why it exists: an AI or workflow should not call a dozen endpoints and paste thousands of raw candles into a prompt.
COREI already computes indicators and a market regime per instrument and timeframe every minute (intel engine) and
measures feed health and quality; this assembles them into one bounded, model-friendly answer.
Purpose: build(db, client, request) → snapshot with snapshot_id, schema_version, per-instrument metadata, source
(active reference feed or a chosen plan), latest quote with age, closed candles per timeframe (from the bar-source
policy of the intel engine), stored indicators and regime, derived reference levels, economic events, data quality
(PASS / WARNING / FAIL / UNAVAILABLE with reasons), an explicit list of missing datasets, and the output size. Formats:
RAW, NORMALIZED, COMPACT, AI_COMPACT, SUMMARY, FEATURE_VECTOR.
Must never: fabricate a price, bar, open interest, Greek or option chain; silently drop a requested dataset or
timeframe to fit a size budget (candles may be shortened and this is reported; otherwise the request is refused with
the size it needs); present a polled quote as an exchange tick stream.
Dependencies: config_store, intel.engine, md.store (holidays), integration.auth. Consumers: integration.gateway.
Architectural position: integration gateway, read model over market data and market intelligence.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from .. import config_store as store
from ..intel import engine
from .auth import Client, GatewayError, in_scope

SCHEMA = 'corei.snapshot.v1'
TIMEFRAMES = ('M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1')
DATASETS = ('instrument_metadata', 'latest_quote', 'candles', 'indicators', 'market_regime', 'support_resistance', 'economic_events',
            'data_quality', 'open_interest', 'option_chain', 'greeks', 'market_depth')
NOT_COLLECTED = {
    'open_interest': 'Open interest is not collected: the Upstox reader polls REST quotes and V3 candles and does not keep OI.',
    'option_chain': 'Option chains are not collected yet.',
    'greeks': 'Option Greeks and implied volatility are not collected yet.',
    'market_depth': 'Order-book depth is not stored for external use (quotes carry best bid/ask only).',
}
KEY_FEATURES = ('close', 'ema20', 'ema50', 'ema200', 'rsi14', 'atr14', 'atr_pct', 'adx14', 'macd_hist', 'bb_upper', 'bb_lower',
                'donchian_high20', 'donchian_low20', 'vwap_session', 'zscore20', 'ret_20_pct', 'rv20_pct')


class SnapshotIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str | None = Field(None, max_length=100, pattern=r'^[A-Za-z0-9_.:-]+$')
    instruments: list[str] = Field(min_length=1, max_length=50)
    timeframes: list[Literal['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1']] = Field(default_factory=lambda: ['H1'], min_length=1, max_length=7)
    include: list[str] = Field(default_factory=lambda: ['instrument_metadata', 'latest_quote', 'candles', 'indicators', 'market_regime',
                                                        'data_quality'], min_length=1)
    feed_selection: str = Field('ACTIVE_REFERENCE', max_length=64)   # ACTIVE_REFERENCE or a collection plan code
    closed_bars_only: bool = True
    format: Literal['RAW', 'NORMALIZED', 'COMPACT', 'AI_COMPACT', 'SUMMARY', 'FEATURE_VECTOR'] = 'AI_COMPACT'
    candles_per_timeframe: int = Field(100, ge=1, le=2000)
    max_output_chars: int = Field(200_000, ge=2_000, le=5_000_000)


def resolve_instruments(db, c: Client, refs: list[str]) -> tuple[list[dict], list[dict]]:
    found, missing = [], []
    for ref in refs:
        try:
            UUID(ref)
            row = db.execute('SELECT * FROM cfg_instruments WHERE id=%s', (ref,)).fetchone()
        except ValueError:
            row = db.execute('SELECT * FROM cfg_instruments WHERE upper(symbol)=upper(%s) OR upper(%s) = ANY(SELECT upper(x) FROM unnest(aliases) x)',
                             (ref, ref)).fetchone()
        if not row:
            missing.append({'instrument': ref, 'code': 'INVALID_INSTRUMENT', 'message': 'No such instrument in the catalogue.'})
            continue
        in_scope(c, 'instruments', row['id'], f"Instrument {row['symbol']}")
        found.append(dict(row))
    return found, missing


def source_for(db, instrument_id, selection: str) -> dict | None:
    """The collection plan whose prices represent this instrument: the active reference feed, else the primary plan."""
    base = """SELECT p.id, p.code, p.account_id, p.broker_symbol, p.feed_role, p.status, a.code AS account_code, c.environment,
                     h.state AS health, h.detail AS health_detail, h.last_price_at
              FROM md_collection_plans p JOIN cfg_accounts a ON a.id=p.account_id JOIN cfg_connections c ON c.id=a.connection_id
              LEFT JOIN md_plan_health h ON h.plan_id=p.id WHERE p.instrument_id=%s AND p.status <> 'ARCHIVED'"""
    if selection != 'ACTIVE_REFERENCE':
        row = db.execute(base + ' AND p.code=%s', (instrument_id, selection)).fetchone()
        if not row:
            raise GatewayError(422, 'INVALID_FEED', f'No collection plan {selection} for this instrument.')
        return dict(row)
    active = db.execute('SELECT active_plan_id FROM md_feed_state WHERE instrument_id=%s', (instrument_id,)).fetchone()
    if active and active['active_plan_id']:
        row = db.execute(base + ' AND p.id=%s', (instrument_id, active['active_plan_id'])).fetchone()
        if row:
            return dict(row)
    row = db.execute(base + " ORDER BY (p.status='ACTIVE') DESC, (p.feed_role='PRIMARY') DESC, (h.state='LIVE') DESC, p.created_at LIMIT 1",
                     (instrument_id,)).fetchone()
    return dict(row) if row else None


def _quality(db, src: dict | None, market: str | None, now: datetime) -> dict:
    if not src:
        return {'state': 'UNAVAILABLE', 'reasons': ['NO_COLLECTION_PLAN'], 'message': 'This instrument has no market-data collection plan.'}
    q = db.execute("""SELECT avg(score) AS score, max(latency_p95_ms) AS latency_p95_ms, max(max_gap_ms) AS max_gap_ms, sum(spikes) AS spikes,
                             max(spread_max) AS spread_max, count(*) AS minutes
                      FROM md_quality WHERE account_id=%s AND broker_symbol=%s AND minute > now() - interval '60 minutes'""",
                   (src['account_id'], src['broker_symbol'])).fetchone()
    health = src.get('health') or 'UNKNOWN'
    reasons = []
    open_now = engine.markets.is_open(market, now) if hasattr(engine, 'markets') else None
    if health in ('ERROR', 'INVALID_SYMBOL', 'OFFLINE'):
        state = 'FAIL'
        reasons.append(f'FEED_{health}')
    elif health in ('STALE', 'DEGRADED', 'WAITING', 'PAUSED', 'UNKNOWN'):
        state = 'WARNING'
        reasons.append(f'FEED_{health}')
    elif health == 'CLOSED' or open_now is False:
        state = 'WARNING'
        reasons.append('MARKET_CLOSED')
    else:
        state = 'PASS'
    score = float(q['score']) if q and q['score'] is not None else None
    if score is not None and score < 50:
        state, _ = 'FAIL', reasons.append('LOW_QUALITY_SCORE')
    elif score is not None and score < 80 and state == 'PASS':
        state, _ = 'WARNING', reasons.append('QUALITY_SCORE_BELOW_80')
    if q and (q['spikes'] or 0) > 0:
        reasons.append('PRICE_SPIKES_SEEN')
    return {'state': state, 'reasons': reasons, 'feed_health': health, 'feed_detail': src.get('health_detail') or '',
            'score_60m': None if score is None else round(score, 1), 'latency_p95_ms': q and q['latency_p95_ms'],
            'max_gap_ms': q and q['max_gap_ms'], 'spread_max': q and q['spread_max'], 'measured_minutes': q and q['minutes'],
            'market_open': open_now, 'last_price_at': src.get('last_price_at')}


def _num(v):
    return None if v is None else str(v)


def _levels(f: dict) -> list[dict]:
    out = []
    for key, kind in (('donchian_high20', 'RESISTANCE'), ('donchian_low20', 'SUPPORT'), ('bb_upper', 'RESISTANCE'), ('bb_lower', 'SUPPORT'),
                      ('vwap_session', 'PIVOT'), ('ema200', 'PIVOT')):
        if f.get(key) is not None:
            out.append({'kind': kind, 'level': f[key], 'method': key})
    return out


def build(db, c: Client, req: SnapshotIn) -> dict:
    unknown = [d for d in req.include if d not in DATASETS]
    if unknown:
        raise GatewayError(422, 'INVALID_DATASET', f"Unknown dataset(s): {', '.join(unknown)}. Choose from: {', '.join(DATASETS)}.")
    now = datetime.now(timezone.utc)
    instruments, missing_instruments = resolve_instruments(db, c, req.instruments)
    holidays = {}
    try:
        from ..md import store as md_store
        holidays = md_store.holidays()
    except Exception:  # noqa: BLE001 - holidays only sharpen freshness
        pass
    inc = set(req.include)
    missing_datasets: list[dict] = []
    items = []
    for ins in instruments:
        src = source_for(db, ins['id'], req.feed_selection)
        market = ins.get('market')
        item: dict = {'instrument': {'id': str(ins['id']), 'symbol': ins['symbol']}}
        if 'instrument_metadata' in inc:
            item['instrument'].update({k: _num(ins.get(k)) if k in ('tick_size', 'contract_size', 'min_quantity', 'quantity_step', 'lot_size', 'strike')
                                       else (str(ins[k]) if isinstance(ins.get(k), (datetime,)) else ins.get(k))
                                       for k in ('name', 'asset_class', 'base_currency', 'quote_currency', 'exchange', 'market', 'tick_size', 'contract_size',
                                                 'min_quantity', 'quantity_step', 'lot_size', 'expiry', 'strike', 'option_type', 'underlying', 'trading_session')})
        item['source'] = None if not src else {'plan': src['code'], 'account': src['account_code'], 'broker_symbol': src['broker_symbol'],
                                                'feed_role': src['feed_role'], 'environment': src['environment'],
                                                'selection': 'ACTIVE_REFERENCE' if req.feed_selection == 'ACTIVE_REFERENCE' else 'EXPLICIT'}
        if 'data_quality' in inc:
            item['data_quality'] = _quality(db, src, market, now)
        if 'latest_quote' in inc:
            q = src and db.execute('SELECT * FROM md_latest WHERE account_id=%s AND broker_symbol=%s', (src['account_id'], src['broker_symbol'])).fetchone()
            if q:
                age = (now - q['received_at']).total_seconds()
                item['latest_quote'] = {'bid': _num(q['bid']), 'ask': _num(q['ask']), 'last': _num(q['last']), 'source_time': q['source_time'],
                                        'received_at': q['received_at'], 'age_seconds': round(age, 1),
                                        'kind': 'POLLED_OBSERVATION' if src and src['environment'] != 'PAPER' else 'OBSERVATION'}
            else:
                missing_datasets.append({'instrument': ins['symbol'], 'dataset': 'latest_quote', 'code': 'DATA_NOT_COLLECTED',
                                         'message': 'No quote has been collected for this instrument.'})
        tfs: dict = {}
        for tf in req.timeframes:
            block: dict = {}
            if src and ('candles' in inc or 'indicators' in inc or 'market_regime' in inc or 'support_resistance' in inc):
                bars, bar_source = engine.bars_for(db, src['account_id'], src['broker_symbol'], tf, market, window=req.candles_per_timeframe) \
                    if tf in engine.TIMEFRAMES or tf == 'M30' else ([], 'NONE')
                if 'candles' in inc:
                    if bars:
                        block['candles'] = {'source': bar_source, 'closed_only': True, 'count': len(bars),
                                            'rows': [{'t': b['bar_time'], 'o': _num(b['open']), 'h': _num(b['high']), 'l': _num(b['low']),
                                                      'c': _num(b['close']), 'v': _num(b.get('volume') if b.get('volume') is not None else b.get('ticks')),
                                                      **({'ticks': b.get('ticks'), 'spread_avg': b.get('spread_avg')} if req.format == 'RAW' else {})}
                                                     for b in bars]}
                        state, ended = engine.freshness(bars[-1]['bar_time'], tf, market, now, holidays.get(market or '', frozenset()) if isinstance(holidays, dict) else frozenset())
                        block['freshness'] = {'state': state, 'last_bar_end': ended}
                    else:
                        missing_datasets.append({'instrument': ins['symbol'], 'timeframe': tf, 'dataset': 'candles', 'code': 'DATA_NOT_COLLECTED',
                                                 'message': 'No closed bars collected for this timeframe.'})
                snap = db.execute('SELECT * FROM intel_snapshot WHERE account_id=%s AND broker_symbol=%s AND timeframe=%s',
                                  (src['account_id'], src['broker_symbol'], tf)).fetchone()
                if snap:
                    f = snap['features'] or {}
                    if 'indicators' in inc:
                        block['indicators'] = {'as_of_bar': snap['bar_time'], 'feature_version': snap['version'], 'bars_used': snap['bars'],
                                               'values': f if req.format in ('RAW', 'NORMALIZED', 'FEATURE_VECTOR') else {k: f.get(k) for k in KEY_FEATURES if k in f},
                                               'settings': {'ema': [20, 50, 200], 'rsi': 14, 'atr': 14, 'adx': 14, 'bollinger': [20, 2], 'donchian': 20}}
                    if 'market_regime' in inc:
                        block['market_regime'] = {'regime': snap['regime'], 'volatility': snap['volatility'], 'confidence': snap['confidence'],
                                                  'reasons': snap['reasons'], 'session': snap['session'], 'computed_at': snap['computed_at']}
                    if 'support_resistance' in inc:
                        block['support_resistance'] = {'method': 'deterministic levels from stored features (Donchian 20, Bollinger 20/2, session VWAP, EMA 200)',
                                                       'levels': _levels(f)}
                else:
                    for d in ('indicators', 'market_regime', 'support_resistance'):
                        if d in inc:
                            missing_datasets.append({'instrument': ins['symbol'], 'timeframe': tf, 'dataset': d, 'code': 'NOT_COMPUTED',
                                                     'message': 'The intelligence engine has no features for this timeframe yet '
                                                                + ('(M30 is not computed).' if tf == 'M30' else '(needs collected bars).')})
            elif not src:
                for d in ('candles', 'indicators', 'market_regime', 'support_resistance'):
                    if d in inc:
                        missing_datasets.append({'instrument': ins['symbol'], 'timeframe': tf, 'dataset': d, 'code': 'NO_COLLECTION_PLAN',
                                                 'message': 'This instrument has no market-data collection plan.'})
            tfs[tf] = block
        item['timeframes'] = tfs
        if 'economic_events' in inc:
            codes = {ins['base_currency'], ins['quote_currency'], 'ALL'}
            if ins['asset_class'] in ('INDEX', 'EQUITY', 'FUTURE', 'OPTION') and (market or '').upper() in ('NSE', 'BSE', 'MCX', 'NFO'):
                codes.add('INR')
            item['economic_events'] = [store.clean(e) for e in db.execute(
                """SELECT at, currency, impact, title, forecast, previous, actual FROM intel_events
                   WHERE at BETWEEN %s AND %s AND currency = ANY(%s) ORDER BY at LIMIT 50""",
                (now - timedelta(hours=2), now + timedelta(hours=48), list(codes))).fetchall()]
        for d in ('open_interest', 'option_chain', 'greeks', 'market_depth'):
            if d in inc:
                missing_datasets.append({'instrument': ins['symbol'], 'dataset': d, 'code': 'CAPABILITY_UNAVAILABLE', 'message': NOT_COLLECTED[d]})
        items.append(item)
    out = {'snapshot_id': str(uuid4()), 'schema_version': SCHEMA, 'request_id': req.request_id, 'snapshot_at': now, 'format': req.format,
           'timeframes': req.timeframes, 'include': req.include, 'closed_bars_only': True,
           'notes': ['Open (forming) bars are never served.'] if not req.closed_bars_only else [],
           'items': items, 'missing_instruments': missing_instruments, 'missing_datasets': missing_datasets}
    out = _shape(out, req)
    return _fit(out, req)


def _compact(v):
    if isinstance(v, dict):
        return {k: _compact(x) for k, x in v.items() if x is not None and x != [] and x != {}}
    if isinstance(v, list):
        return [_compact(x) for x in v]
    return v


def _shape(out: dict, req: SnapshotIn) -> dict:
    if req.format in ('RAW', 'NORMALIZED'):
        return out
    for item in out['items']:
        for tf, block in item.get('timeframes', {}).items():
            c = block.get('candles')
            if c and req.format in ('COMPACT', 'AI_COMPACT'):
                c['columns'] = ['t', 'o', 'h', 'l', 'c', 'v']
                c['rows'] = [[r['t'], r['o'], r['h'], r['l'], r['c'], r['v']] for r in c['rows']]
            if req.format in ('SUMMARY', 'FEATURE_VECTOR'):
                block.pop('candles', None)
        if req.format == 'AI_COMPACT':
            item['summary'] = [_line(tf, b) for tf, b in item.get('timeframes', {}).items()]
        if req.format == 'FEATURE_VECTOR':
            item['features'] = {tf: {k: v for k, v in (b.get('indicators') or {}).get('values', {}).items() if isinstance(v, (int, float))}
                                for tf, b in item.get('timeframes', {}).items()}
            item.pop('timeframes', None)
    return _compact(out) if req.format != 'FEATURE_VECTOR' else out


def _line(tf: str, b: dict) -> str:
    r = b.get('market_regime') or {}
    f = (b.get('indicators') or {}).get('values') or {}
    parts = [tf]
    if r:
        parts.append(f"{r.get('regime')} vol {r.get('volatility')} conf {r.get('confidence')}")
    for k in ('close', 'ema20', 'ema50', 'rsi14', 'atr14', 'adx14'):
        if f.get(k) is not None:
            parts.append(f'{k} {f[k]}')
    fr = b.get('freshness') or {}
    if fr:
        parts.append(f"bars {fr.get('state')}")
    return ' · '.join(str(p) for p in parts)


def _size(out: dict) -> int:
    return len(json.dumps(out, default=str, separators=(',', ':')))


def _fit(out: dict, req: SnapshotIn) -> dict:
    """Shorten candle history (never drop a dataset or timeframe) until the output fits, and report it; else refuse."""
    size = _size(out)
    coverage = {'candles_per_timeframe_requested': req.candles_per_timeframe, 'candles_per_timeframe_returned': req.candles_per_timeframe,
                'reduced': False}
    n = req.candles_per_timeframe
    while size > req.max_output_chars and n > 10:
        n = max(10, n // 2)
        for item in out['items']:
            for b in (item.get('timeframes') or {}).values():
                c = b.get('candles')
                if c and c.get('rows'):
                    c['rows'] = c['rows'][-n:]
                    c['count'] = len(c['rows'])
                    c['truncated_to_latest'] = n
        coverage.update(candles_per_timeframe_returned=n, reduced=True,
                        reason='Candle history was shortened to the latest bars to fit max_output_chars; '
                               'fetch the rest with GET /market-data/candles.')
        size = _size(out)
    if size > req.max_output_chars:
        raise GatewayError(413, 'REQUEST_SIZE_EXCEEDED', f'The snapshot needs about {size} characters with every requested dataset '
                                                         f'(budget {req.max_output_chars}). Raise max_output_chars, ask for fewer instruments or timeframes, '
                                                         'or use the SUMMARY format.')
    out['coverage'] = coverage
    out['output_chars'] = size
    return out
