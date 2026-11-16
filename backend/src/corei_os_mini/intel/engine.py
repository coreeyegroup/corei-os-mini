"""Feature engine (Stage 2 · Market intelligence).

Why it exists: the indicators and regimes must be computed once, from the stored bars, and kept, so every screen,
signal and strategy reads the same numbers and any past decision can be replayed against the features it saw.
Purpose: for every collected instrument (each plan's source × broker symbol) and timeframe M1…D1, take the closed
bars (the broker's own bars when there are enough, else bars built from the source's M1), compute the features,
classify the regime, and store a per-bar history (M5 and up) and the latest snapshot.
Responsibilities: bar loading and resampling, feature rows, snapshot and history writes, the refresh cycle and its
status. Only closed bars are used; the bar still forming is ignored.
Must never: use the machine clock in a feature (bar times only; the clock only stamps computed_at), write outside
the intel_* tables, or touch orders.
Dependencies: config_store (database), intel.indicators, intel.regime, intel.sessions.
Consumers: intel_api (screener, instrument), the background loop in api.py.
Architectural position: Stage 2, fed by Stage 1 (md_bars), feeding Stage 3 (signals).
Alignment: M5/M15/M30/H1/H4 buckets start at multiples of their length from 00:00 UTC; D1 is the UTC day.
"""
from __future__ import annotations

import logging
import time
from bisect import bisect_left
from datetime import datetime, timedelta, timezone
from typing import Any

from psycopg.types.json import Jsonb

from .. import config_store as store
from . import indicators as ind
from . import regime, sessions

log = logging.getLogger('corei.intel')
TIMEFRAMES = ('M1', 'M5', 'M15', 'H1', 'H4', 'D1')
MINUTES = {'M1': 1, 'M5': 5, 'M15': 15, 'M30': 30, 'H1': 60, 'H4': 240, 'D1': 1440}
HISTORY_TIMEFRAMES = ('M5', 'M15', 'H1', 'H4', 'D1')  # per-bar history kept; M1 keeps only the snapshot
WINDOW = 300            # bars per computation
M1_LIMIT = 20000        # M1 bars read per instrument for building higher timeframes (~14 days)
BARS_PER_YEAR = {tf: 252 * 1440 / m for tf, m in MINUTES.items()}  # FX trading minutes per year, by timeframe
DEFAULTS = {'enabled': True, 'interval_seconds': 60, 'timeframes': list(TIMEFRAMES), 'excluded': [], 'version': 0}
STATUS: dict[str, Any] = {'last_run_at': None, 'duration_ms': None, 'instruments': 0, 'snapshots': 0, 'history_rows': 0, 'errors': []}


def _bucket(t: datetime, tf: str) -> datetime:
    if tf == 'D1':
        return t.replace(hour=0, minute=0, second=0, microsecond=0)
    m = MINUTES[tf]
    epoch = int(t.timestamp()) // 60
    return datetime.fromtimestamp((epoch - epoch % m) * 60, tz=timezone.utc)


def resample(m1: list[dict], tf: str) -> list[dict]:
    """Closed bars of `tf` from closed M1 bars (sorted by time). The last bucket counts as closed only when the M1
    bar that ends its period is present."""
    if tf == 'M1':
        return m1
    out: list[dict] = []
    for b in m1:
        start = _bucket(b['bar_time'], tf)
        if out and out[-1]['bar_time'] == start:
            o = out[-1]
            o['high'], o['low'], o['close'] = max(o['high'], b['high']), min(o['low'], b['low']), b['close']
            o['ticks'] += b.get('ticks') or 0
            o['_last'] = b['bar_time']
        else:
            out.append({'bar_time': start, 'open': b['open'], 'high': b['high'], 'low': b['low'], 'close': b['close'],
                        'ticks': b.get('ticks') or 0, '_last': b['bar_time']})
    if out and out[-1]['_last'] + timedelta(minutes=1) < out[-1]['bar_time'] + timedelta(minutes=MINUTES[tf]):
        out.pop()  # still forming
    return out


def features(bars: list[dict], tf: str) -> list[dict]:
    """One feature row per bar (aligned to `bars`), version FEATURE_VERSION."""
    h = [b['high'] for b in bars]
    lo = [b['low'] for b in bars]
    c = [b['close'] for b in bars]
    e20, e50, e200 = ind.ema(c, 20), ind.ema(c, 50), ind.ema(c, 200)
    r14 = ind.rsi(c, 14)
    a14 = ind.atr(h, lo, c, 14)
    mid, up, dn = ind.bollinger(c, 20, 2.0)
    adx, pdi, mdi = ind.adx(h, lo, c, 14)
    er = ind.efficiency_ratio(c, 20)
    rv = ind.realised_vol(c, 20, BARS_PER_YEAR[tf])
    rp = ind.range_position(h, lo, c, 20)
    atr_pct = [None if a is None or not c[i] else 100 * a / c[i] for i, a in enumerate(a14)]
    rows = []
    for i, b in enumerate(bars):
        window = [x for x in atr_pct[max(0, i - 99):i + 1] if x is not None]
        rank = None if atr_pct[i] is None or len(window) < 20 else 100.0 * bisect_left(sorted(window), atr_pct[i]) / (len(window) - 1 or 1)
        rows.append({
            'close': c[i], 'ema20': e20[i], 'ema50': e50[i], 'ema200': e200[i], 'rsi14': r14[i], 'atr14': a14[i], 'atr_pct': atr_pct[i],
            'atr_pct_rank100': rank, 'bb_mid': mid[i], 'bb_upper': up[i], 'bb_lower': dn[i],
            'bb_width_pct': None if mid[i] in (None, 0) else 100 * (up[i] - dn[i]) / mid[i],
            'adx14': adx[i], 'plus_di': pdi[i], 'minus_di': mdi[i], 'er20': er[i], 'rv20_pct': rv[i], 'range_pos20': rp[i],
            'ret_1_pct': None if i < 1 or not c[i - 1] else 100 * (c[i] / c[i - 1] - 1),
            'ret_20_pct': None if i < 20 or not c[i - 20] else 100 * (c[i] / c[i - 20] - 1),
        })
    return rows


def _round(f: dict) -> dict:
    return {k: (None if v is None else round(v, 10)) for k, v in f.items()}


def _instruments(db) -> list[dict]:
    return db.execute("""SELECT DISTINCT p.account_id, p.broker_symbol FROM md_collection_plans p
                         WHERE p.status <> 'ARCHIVED' ORDER BY 1, 2""").fetchall()


def _load(db, account_id, symbol: str, tf: str) -> list[dict]:
    rows = db.execute('SELECT bar_time, open, high, low, close, ticks FROM md_bars WHERE account_id=%s AND broker_symbol=%s AND timeframe=%s '
                      'AND complete ORDER BY bar_time DESC LIMIT %s', (account_id, symbol, tf, M1_LIMIT if tf == 'M1' else WINDOW)).fetchall()
    return [dict(r) for r in reversed(rows)]


def settings() -> dict:
    try:
        with store.transaction('read') as db:
            row = db.execute('SELECT * FROM intel_settings WHERE id=1').fetchone()
        return dict(row) if row else dict(DEFAULTS)
    except Exception:  # noqa: BLE001 - not migrated yet: defaults
        return dict(DEFAULTS)


def refresh_instrument(account_id, symbol: str, timeframes: tuple[str, ...] = TIMEFRAMES) -> tuple[int, int]:
    """Recompute every timeframe of one instrument; returns (snapshots written, history rows written)."""
    with store.transaction('read') as db:
        m1 = _load(db, account_id, symbol, 'M1')
        native = {tf: _load(db, account_id, symbol, tf) for tf in TIMEFRAMES if tf != 'M1'}
        last = {r['timeframe']: r['bar_time'] for r in db.execute(
            'SELECT timeframe, bar_time FROM intel_snapshot WHERE account_id=%s AND broker_symbol=%s', (account_id, symbol)).fetchall()}
    snaps = hist = 0
    for tf in timeframes:
        built = resample(m1, tf)
        nat = native.get(tf) or []
        use_native = bool(nat) and len(nat) >= len(built)
        bars = (nat if use_native else built)[-WINDOW:]
        source = 'BROKER' if use_native else 'M1'
        if not bars:
            continue
        rows = features(bars, tf)
        f = _round(rows[-1])
        cls = regime.classify(f, len(bars))
        bar_time = bars[-1]['bar_time']
        with store.transaction('write') as db:
            db.execute("""INSERT INTO intel_snapshot(account_id, broker_symbol, timeframe, bar_time, version, bars, bars_source, features, regime,
                              volatility, confidence, reasons, session, computed_at)
                          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                          ON CONFLICT (account_id, broker_symbol, timeframe) DO UPDATE SET bar_time=EXCLUDED.bar_time, version=EXCLUDED.version,
                              bars=EXCLUDED.bars, bars_source=EXCLUDED.bars_source, features=EXCLUDED.features, regime=EXCLUDED.regime,
                              volatility=EXCLUDED.volatility, confidence=EXCLUDED.confidence, reasons=EXCLUDED.reasons,
                              session=EXCLUDED.session, computed_at=now()""",
                       (account_id, symbol, tf, bar_time, ind.FEATURE_VERSION, len(bars), source, Jsonb(f), cls['regime'], cls['volatility'],
                        cls['confidence'], Jsonb(cls['reasons']), sessions.sessions_at(bar_time)['label']))
            snaps += 1
            if tf in HISTORY_TIMEFRAMES:
                since = last.get(tf)
                new = [(b, r) for b, r in zip(bars, rows) if since is None or b['bar_time'] > since]
                if new:
                    with db.cursor() as cur:
                        cur.executemany("""INSERT INTO intel_features(account_id, broker_symbol, timeframe, bar_time, version, features, regime)
                                           VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                                        [(account_id, symbol, tf, b['bar_time'], ind.FEATURE_VERSION, Jsonb(_round(r)),
                                          regime.classify(_round(r), k + 1 + (len(bars) - len(new)))['regime'])
                                         for k, (b, r) in enumerate(new)])
                    hist += len(new)
    return snaps, hist


def refresh_all(force: bool = False) -> dict:
    cfg = settings()
    if not cfg['enabled'] and not force:
        STATUS.update(paused=True)
        return dict(STATUS)
    started = time.monotonic()
    errors: list[str] = []
    snaps = hist = 0
    skip = {(str(x.get('account_id')), x.get('broker_symbol')) for x in cfg.get('excluded') or []}
    tfs = tuple(tf for tf in TIMEFRAMES if tf in (cfg.get('timeframes') or TIMEFRAMES))
    with store.transaction('read') as db:
        items = [it for it in _instruments(db) if (str(it['account_id']), it['broker_symbol']) not in skip]
    for it in items:
        try:
            s, h = refresh_instrument(it['account_id'], it['broker_symbol'], tfs)
            snaps, hist = snaps + s, hist + h
        except Exception as exc:  # noqa: BLE001 - one instrument must not stop the others
            log.exception('Feature refresh failed for %s', it['broker_symbol'])
            errors.append(f"{it['broker_symbol']}: {type(exc).__name__}: {exc}"[:300])
    STATUS.update(paused=False, excluded=len(skip), timeframes=list(tfs), last_run_at=datetime.now(timezone.utc).isoformat(), duration_ms=round(1000 * (time.monotonic() - started)),
                  instruments=len(items), snapshots=snaps, history_rows=hist, errors=errors[:20])
    return dict(STATUS)


async def run_forever() -> None:
    """The background cycle in the API process (COREI_INTEL=off turns it off; Intelligence › Dashboard › Engine
    pauses it and sets its cycle)."""
    import asyncio
    await asyncio.sleep(15)
    while True:
        interval = 60
        try:
            interval = (await asyncio.to_thread(settings)).get('interval_seconds') or 60
            await asyncio.to_thread(refresh_all)
        except Exception:  # noqa: BLE001 - keep the loop alive (database restarting, …)
            log.exception('Feature refresh cycle failed')
        await asyncio.sleep(interval)
