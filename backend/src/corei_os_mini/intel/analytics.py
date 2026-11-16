"""Cross-instrument and cross-source analytics (Stage 2 · Market intelligence).

Why it exists: a firm with several brokers and accounts needs to compare the same instrument across its sources
(which feed is cheaper, faster, cleaner, and does any price deviate), see how instruments move together
(correlation), see which currencies are strong or weak, and know whether timeframes agree.
Purpose: read the feature store and Stage 1's live tables, and compute those views on request.
Responsibilities: sources(instrument), correlation(timeframe, window), currency_strength(timeframe),
alignment(snapshots), currencies_of(instrument).
Must never: write anything, or be used to price or size an order (descriptive analytics only).
Dependencies: config_store (read), intel.indicators (feature version).
Consumers: intel_api (Sources, Correlation, Strength tabs; screener alignment column).
Architectural position: Stage 2, beside the screener.
"""
from __future__ import annotations

import math
import re
from statistics import median

from .. import config_store as store
from . import indicators as ind

FX = re.compile(r'^([A-Z]{3})([A-Z]{3})')


def currencies_of(symbol: str | None, base: str | None = None, quote: str | None = None) -> tuple[str, str] | None:
    """The two currencies of an FX-style symbol: from the instrument when known, else from the first six letters
    of the symbol (EURUSD.r → EUR, USD)."""
    if base and quote and len(base) == 3 and len(quote) == 3:
        return base, quote
    m = FX.match((symbol or '').upper())
    return (m.group(1), m.group(2)) if m else None


def alignment(snaps: list[dict]) -> dict:
    """How many timeframes agree: +1 per trend up, −1 per trend down, over the timeframes past warming up."""
    counted = [s for s in snaps if s['regime'] != 'WARMING_UP']
    up = sum(1 for s in counted if s['regime'] == 'TREND_UP')
    down = sum(1 for s in counted if s['regime'] == 'TREND_DOWN')
    score = None if not counted else round((up - down) / len(counted), 2)
    label = None if score is None else 'aligned up' if score >= 0.5 else 'aligned down' if score <= -0.5 else 'mixed'
    return {'score': score, 'label': label, 'up': up, 'down': down, 'of': len(counted)}


def sources(instrument_id: str) -> dict:
    """The same canonical instrument from every source collecting it: price, deviation from the median of the
    sources, spread, feed quality and latency (last 60 minutes), age of the last price, and the H1 regime."""
    with store.transaction('read') as db:
        ins = db.execute('SELECT id, symbol, name, asset_class, base_currency, quote_currency FROM cfg_instruments WHERE id=%s', (instrument_id,)).fetchone()
        rows = db.execute("""
            SELECT p.account_id, p.broker_symbol, a.code AS account_code, a.name AS account_name, pr.name AS provider_name,
                   c.environment, l.bid, l.ask, l.received_at, l.source_time,
                   q.score AS quality, q.latency_p50_ms, q.latency_p95_ms, q.spread_avg, q.ticks,
                   s.regime, s.features->>'atr14' AS atr14
            FROM md_collection_plans p
            JOIN cfg_accounts a ON a.id = p.account_id
            JOIN cfg_connections c ON c.id = a.connection_id
            JOIN cfg_providers pr ON pr.id = c.provider_id
            LEFT JOIN md_latest l ON l.account_id = p.account_id AND l.broker_symbol = p.broker_symbol
            LEFT JOIN LATERAL (SELECT avg(score) AS score, avg(latency_p50_ms) AS latency_p50_ms, max(latency_p95_ms) AS latency_p95_ms,
                                      avg(spread_avg) AS spread_avg, sum(ticks) AS ticks
                               FROM md_quality WHERE account_id = p.account_id AND broker_symbol = p.broker_symbol
                                 AND minute > now() - interval '60 minutes') q ON true
            LEFT JOIN intel_snapshot s ON s.account_id = p.account_id AND s.broker_symbol = p.broker_symbol AND s.timeframe = 'H1'
            WHERE p.instrument_id = %s AND p.status <> 'ARCHIVED'
            ORDER BY pr.name, a.code""", (instrument_id,)).fetchall()
    items = [store.clean(r) for r in rows]
    mids = [(r['bid'] + r['ask']) / 2 for r in items if r['bid'] is not None and r['ask'] is not None]
    ref = median(mids) if mids else None
    for r in items:
        mid = (r['bid'] + r['ask']) / 2 if r['bid'] is not None and r['ask'] is not None else None
        r['mid'] = mid
        r['spread'] = None if mid is None else r['ask'] - r['bid']
        r['deviation_bp'] = None if mid is None or not ref else round(10000 * (mid / ref - 1), 2)
    usable = [r for r in items if r['spread'] is not None]
    best = {'cheapest': min(usable, key=lambda r: r['spread'])['account_code'] if usable else None,
            'fastest': min((r for r in items if r['latency_p50_ms'] is not None), key=lambda r: r['latency_p50_ms'], default={}).get('account_code'),
            'cleanest': max((r for r in items if r['quality'] is not None), key=lambda r: r['quality'], default={}).get('account_code')}
    return {'instrument': store.clean(ins) if ins else None, 'reference_mid': ref, 'items': items, 'best': best}


def _closes(timeframe: str, limit: int) -> dict[tuple[str, str], dict]:
    """Per instrument (one source each: the best-quality one), its closes by bar time from the feature history."""
    with store.transaction('read') as db:
        picks = db.execute("""
            SELECT DISTINCT ON (coalesce(p.instrument_id::text, p.account_id::text || p.broker_symbol))
                   p.account_id, p.broker_symbol, coalesce(i.symbol, p.broker_symbol) AS name, i.base_currency, i.quote_currency, i.asset_class
            FROM md_collection_plans p LEFT JOIN cfg_instruments i ON i.id = p.instrument_id
            LEFT JOIN LATERAL (SELECT avg(score) AS q FROM md_quality m WHERE m.account_id = p.account_id AND m.broker_symbol = p.broker_symbol
                               AND m.minute > now() - interval '60 minutes') q ON true
            WHERE p.status <> 'ARCHIVED'
            ORDER BY coalesce(p.instrument_id::text, p.account_id::text || p.broker_symbol), q.q DESC NULLS LAST""").fetchall()
        out = {}
        for p in picks:
            rows = db.execute("SELECT bar_time, (features->>'close')::float8 AS close FROM intel_features WHERE account_id=%s AND broker_symbol=%s "
                              'AND timeframe=%s AND version=%s ORDER BY bar_time DESC LIMIT %s',
                              (p['account_id'], p['broker_symbol'], timeframe, ind.FEATURE_VERSION, limit)).fetchall()
            out[(str(p['account_id']), p['broker_symbol'])] = {**dict(p), 'closes': {r['bar_time']: r['close'] for r in rows if r['close']}}
    return out


def correlation(timeframe: str = 'H1', window: int = 100) -> dict:
    """Pearson correlation of log returns over the bars every pair has in common (at least 20)."""
    data = _closes(timeframe, window + 1)
    keys = [k for k, v in data.items() if len(v['closes']) >= 21]
    rets: dict = {}
    for k in keys:
        times = sorted(data[k]['closes'])
        c = data[k]['closes']
        rets[k] = {t2: math.log(c[t2] / c[t1]) for t1, t2 in zip(times, times[1:]) if c[t1] > 0 and c[t2] > 0}
    matrix = []
    for a in keys:
        row = []
        for b in keys:
            common = sorted(set(rets[a]) & set(rets[b]))
            if a == b:
                row.append({'r': 1.0, 'n': len(common)})
                continue
            if len(common) < 20:
                row.append({'r': None, 'n': len(common)})
                continue
            x, y = [rets[a][t] for t in common], [rets[b][t] for t in common]
            mx, my = sum(x) / len(x), sum(y) / len(y)
            sx = math.sqrt(sum((v - mx) ** 2 for v in x))
            sy = math.sqrt(sum((v - my) ** 2 for v in y))
            r = None if sx == 0 or sy == 0 else sum((p - mx) * (q - my) for p, q in zip(x, y)) / (sx * sy)
            row.append({'r': None if r is None else round(r, 3), 'n': len(common)})
        matrix.append(row)
    labels = [{'account_id': k[0], 'broker_symbol': k[1], 'name': data[k]['name']} for k in keys]
    skipped = [{'broker_symbol': k[1], 'bars': len(v['closes'])} for k, v in data.items() if k not in keys]
    return {'timeframe': timeframe, 'window': window, 'labels': labels, 'matrix': matrix, 'skipped': skipped}


def currency_strength(timeframe: str = 'H1', bars: int = 20) -> dict:
    """Each currency's average move against the others over the last `bars` closed bars: + when it is the base of a
    pair that rose, − when it is the quote. From every FX instrument collected (one source each)."""
    data = _closes(timeframe, bars + 1)
    moves: dict[str, list[float]] = {}
    pairs = []
    for (_, sym), v in data.items():
        cur = currencies_of(sym, v.get('base_currency'), v.get('quote_currency'))
        if not cur or (v.get('asset_class') and v['asset_class'] != 'FX'):
            continue
        times = sorted(v['closes'])
        if len(times) < bars + 1:
            continue
        first, last = v['closes'][times[-bars - 1]], v['closes'][times[-1]]
        if not first:
            continue
        pct = 100 * (last / first - 1)
        moves.setdefault(cur[0], []).append(pct)
        moves.setdefault(cur[1], []).append(-pct)
        pairs.append({'pair': v['name'], 'move_pct': round(pct, 3)})
    table = sorted(({'currency': k, 'strength_pct': round(sum(v) / len(v), 3), 'pairs': len(v)} for k, v in moves.items()),
                   key=lambda r: -r['strength_pct'])
    return {'timeframe': timeframe, 'bars': bars, 'currencies': table, 'pairs': pairs,
            'note': None if len(pairs) >= 3 else 'Collect at least three FX pairs (e.g. EURUSD, GBPUSD, USDJPY) for a meaningful reading.'}

