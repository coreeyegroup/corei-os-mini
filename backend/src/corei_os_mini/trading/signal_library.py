"""Signal library (Stage 3 · Signal intelligence): every signal formula, one standard score, shown with its working.

Why it exists: in an institutional flow the formula belongs to the signal, not to the strategy. A signal is a forecast
per instrument and bar — a score from −100 (strong short) to +100 (strong long) with a horizon — computed for every
collected instrument whether or not anything trades it, researched on its own (does it predict?), and then combined
by strategies (Stage 4). Showing the formula, the inputs and the arithmetic for every score is the point.
Purpose:
  KINDS        the formulas: each with its parameters, its equation in words and symbols, and a function that returns,
               per bar, the score and every intermediate number (the inputs) used to get it.
  compute      scores for a bar series (the same code for the live board, research, backtests and strategies).
  explain      the latest score of one instrument with the equation filled in with today's numbers.
  research     does the signal predict? forward returns over several horizons, information coefficient (rank
               correlation), its t-statistic, hit rate, average return by score quintile, turnover, coverage, and
               the typical move against the typical spread (cost).
  definitions  the library itself (built-in presets plus the desk's own), versioned and audited.
  overview, instrument_matrix, events, correlation, research_all
               the dashboard (strongest scores, consensus per instrument), one instrument across timeframes, every flip
               of side (sig_events), how alike the signals are, and every signal researched on the same bars.
Must never: use money values (scores are dimensionless analytics), peek at future bars when computing a score
(forward returns are used only in research, never inside a score), or hide how a number was made.
Dependencies: intel.indicators (the same indicators as Stage 2), config_store. Consumers: intel.engine (live
scores each run), trading.algorithms (the SIGNAL_BLEND strategy), trading_api, the Stage 3 workspace.
Architectural position: Stage 3, between features (Stage 2) and strategies (Stage 4).
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Callable
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from .. import config_store as store
from ..intel import indicators as ind

Series = list[float | None]


def _clamp(x: float) -> float:
    return max(-100.0, min(100.0, x))


def _r(x: float | None, d: int = 6) -> float | None:
    return None if x is None else round(x, d)


def _hlc(bars: list[dict]) -> tuple[list[float], list[float], list[float]]:
    return [float(b['high']) for b in bars], [float(b['low']) for b in bars], [float(b['close']) for b in bars]


# ---------- the formulas ----------
# Each returns per bar: None (warming up) or (score, inputs). Inputs are every number the equation used.

def _ema_trend(bars, p) -> list:
    h, lo, c = _hlc(bars)
    f, s, a = ind.ema(c, p['fast']), ind.ema(c, p['slow']), ind.atr(h, lo, c, 14)
    out = []
    for i in range(len(c)):
        if None in (f[i], s[i], a[i]) or not a[i]:
            out.append(None)
            continue
        gap = f[i] - s[i]
        out.append((_clamp(100 * gap / (p['scale'] * a[i])), {'close': c[i], 'ema_fast': _r(f[i]), 'ema_slow': _r(s[i]), 'gap': _r(gap), 'atr14': _r(a[i])}))
    return out


def _sma_trend(bars, p) -> list:
    h, lo, c = _hlc(bars)
    f, s, a = ind.sma(c, p['fast']), ind.sma(c, p['slow']), ind.atr(h, lo, c, 14)
    return [None if None in (f[i], s[i], a[i]) or not a[i] else
            (_clamp(100 * (f[i] - s[i]) / (p['scale'] * a[i])), {'close': c[i], 'sma_fast': _r(f[i]), 'sma_slow': _r(s[i]), 'gap': _r(f[i] - s[i]), 'atr14': _r(a[i])})
            for i in range(len(c))]


def _macd(bars, p) -> list:
    h, lo, c = _hlc(bars)
    line, sig, hist = ind.macd(c, p['fast'], p['slow'], p['signal'])
    a = ind.atr(h, lo, c, 14)
    return [None if None in (hist[i], a[i]) or not a[i] else
            (_clamp(100 * hist[i] / (p['scale'] * a[i])), {'macd': _r(line[i]), 'signal_line': _r(sig[i]), 'histogram': _r(hist[i]), 'atr14': _r(a[i])})
            for i in range(len(c))]


def _rsi(sign: int):
    def fn(bars, p) -> list:
        c = [float(b['close']) for b in bars]
        r = ind.rsi(c, p['period'])
        return [None if r[i] is None else (_clamp(sign * 2 * (r[i] - 50)), {'rsi': _r(r[i], 2)}) for i in range(len(c))]
    return fn


def _bollinger(bars, p) -> list:
    c = [float(b['close']) for b in bars]
    mid, up, dn = ind.bollinger(c, p['period'], p['deviations'])
    out = []
    for i in range(len(c)):
        if None in (mid[i], up[i]) or up[i] == mid[i]:
            out.append(None)
            continue
        sigma = (up[i] - mid[i]) / p['deviations']
        z = (c[i] - mid[i]) / sigma
        out.append((_clamp(-100 * z / p['deviations']), {'close': c[i], 'middle': _r(mid[i]), 'sigma': _r(sigma), 'upper': _r(up[i]), 'lower': _r(dn[i]), 'z': _r(z, 3)}))
    return out


def _zscore(bars, p) -> list:
    c = [float(b['close']) for b in bars]
    z = ind.zscore(c, p['period'])
    return [None if z[i] is None else (_clamp(-100 * z[i] / p['zmax']), {'close': c[i], 'z': _r(z[i], 3)}) for i in range(len(c))]


def _donchian(bars, p) -> list:
    h, lo, c = _hlc(bars)
    n, out = p['period'], []
    for i in range(len(c)):
        if i < n:
            out.append(None)
            continue
        hi, low = max(h[i - n:i]), min(lo[i - n:i])  # the previous n bars, not this one
        pos = 0.5 if hi == low else (c[i] - low) / (hi - low)
        out.append((_clamp(200 * (pos - 0.5)), {'close': c[i], 'channel_high': hi, 'channel_low': low, 'position': _r(pos, 3)}))
    return out


def _momentum(bars, p) -> list:
    c = [float(b['close']) for b in bars]
    n, out = p['period'], []
    lr = [0.0] + [math.log(c[i] / c[i - 1]) if c[i] > 0 and c[i - 1] > 0 else 0.0 for i in range(1, len(c))]
    for i in range(len(c)):
        if i < n or c[i - n] <= 0:
            out.append(None)
            continue
        w = lr[i - n + 1:i + 1]
        m = sum(w) / n
        sd = math.sqrt(sum((x - m) ** 2 for x in w) / (n - 1)) if n > 1 else 0.0
        ret = math.log(c[i] / c[i - n])
        t = 0.0 if sd == 0 else ret / (sd * math.sqrt(n))
        out.append((_clamp(100 * t / p['scale']), {'close': c[i], 'close_n_ago': c[i - n], 'log_return': _r(ret), 'sigma_1bar': _r(sd), 't': _r(t, 3)}))
    return out


def _stochastic(bars, p) -> list:
    h, lo, c = _hlc(bars)
    k, d = ind.stochastic(h, lo, c, p['period'], p['smooth'], 3)
    return [None if k[i] is None else (_clamp(-2 * (k[i] - 50)), {'percent_k': _r(k[i], 2), 'percent_d': _r(d[i], 2)}) for i in range(len(c))]


def _adx(bars, p) -> list:
    h, lo, c = _hlc(bars)
    a, pdi, mdi = ind.adx(h, lo, c, p['period'])
    out = []
    for i in range(len(c)):
        if None in (a[i], pdi[i], mdi[i]) or (pdi[i] + mdi[i]) == 0:
            out.append(None)
            continue
        di = (pdi[i] - mdi[i]) / (pdi[i] + mdi[i])
        strength = min(1.0, a[i] / p['full_at'])
        out.append((_clamp(100 * di * strength), {'adx': _r(a[i], 2), 'plus_di': _r(pdi[i], 2), 'minus_di': _r(mdi[i], 2), 'di_balance': _r(di, 3), 'adx_factor': _r(strength, 3)}))
    return out


def _vwap(bars, p) -> list:
    h, lo, c = _hlc(bars)
    ticks = [float(b.get('ticks') or 0) for b in bars]
    real = [float(b.get('volume') or 0) for b in bars]
    v = ind.vwap(h, lo, c, real if any(real) else ticks, p['period'])
    a = ind.atr(h, lo, c, 14)
    return [None if None in (v[i], a[i]) or not a[i] else
            (_clamp(-100 * (c[i] - v[i]) / (p['scale'] * a[i])), {'close': c[i], 'vwap': _r(v[i]), 'distance': _r(c[i] - v[i]), 'atr14': _r(a[i])})
            for i in range(len(c))]


KINDS: dict[str, dict[str, Any]] = {
    'EMA_TREND': {'label': 'EMA trend', 'family': 'Trend', 'fn': _ema_trend, 'defaults': {'fast': 9, 'slow': 21, 'scale': 1.0},
                  'equation': 'score = clamp(100 × (EMA(fast) − EMA(slow)) ÷ (scale × ATR14), −100, +100)',
                  'detail': 'EMA(n)ₜ = EMA(n)ₜ₋₁ + α·(closeₜ − EMA(n)ₜ₋₁), α = 2 ÷ (n + 1), seeded with the SMA of the first n closes. '
                            'Positive when the fast average is above the slow one; the gap is measured in ATRs, so it means the same on every instrument.',
                  'horizon': 'medium (10–50 bars)'},
    'SMA_TREND': {'label': 'SMA trend', 'family': 'Trend', 'fn': _sma_trend, 'defaults': {'fast': 20, 'slow': 50, 'scale': 1.0},
                  'equation': 'score = clamp(100 × (SMA(fast) − SMA(slow)) ÷ (scale × ATR14), −100, +100)',
                  'detail': 'SMA(n) = (close₁ + … + closeₙ) ÷ n over the last n bars.', 'horizon': 'medium to long'},
    'MACD_MOMENTUM': {'label': 'MACD momentum', 'family': 'Momentum', 'fn': _macd, 'defaults': {'fast': 12, 'slow': 26, 'signal': 9, 'scale': 0.5},
                      'equation': 'score = clamp(100 × histogram ÷ (scale × ATR14)),  histogram = (EMA(fast) − EMA(slow)) − EMA(signal) of that line',
                      'detail': 'Positive when the MACD line is above its own signal line: momentum is building.', 'horizon': 'short to medium'},
    'RSI_REVERSION': {'label': 'RSI reversion', 'family': 'Mean reversion', 'fn': _rsi(-1), 'defaults': {'period': 14},
                      'equation': 'score = clamp(2 × (50 − RSI), −100, +100)',
                      'detail': "RSI = 100 − 100 ÷ (1 + RS), RS = Wilder average gain ÷ Wilder average loss over the period. RSI 30 → +40 (oversold: expect a rise); RSI 70 → −40.",
                      'horizon': 'short (2–10 bars)'},
    'RSI_MOMENTUM': {'label': 'RSI momentum', 'family': 'Momentum', 'fn': _rsi(1), 'defaults': {'period': 14},
                     'equation': 'score = clamp(2 × (RSI − 50), −100, +100)', 'detail': 'The same RSI read as strength: above 50 is up-momentum.', 'horizon': 'short'},
    'BOLLINGER_REVERSION': {'label': 'Bollinger reversion', 'family': 'Mean reversion', 'fn': _bollinger, 'defaults': {'period': 20, 'deviations': 2.0},
                            'equation': 'score = clamp(−100 × z ÷ k),  z = (close − SMA(n)) ÷ σ(n)',
                            'detail': 'σ = population standard deviation of the last n closes; bands = SMA ± k·σ. At the lower band (z = −k) the score is +100.',
                            'horizon': 'short'},
    'ZSCORE_REVERSION': {'label': 'Z-score reversion', 'family': 'Mean reversion', 'fn': _zscore, 'defaults': {'period': 20, 'zmax': 2.0},
                         'equation': 'score = clamp(−100 × z ÷ zmax),  z = (close − mean(n)) ÷ stdev(n)', 'detail': 'How far price is from its recent mean, in standard deviations.',
                         'horizon': 'short'},
    'DONCHIAN_BREAKOUT': {'label': 'Donchian breakout', 'family': 'Breakout', 'fn': _donchian, 'defaults': {'period': 20},
                          'equation': 'score = clamp(200 × (position − 0.5)),  position = (close − low(n)) ÷ (high(n) − low(n)) of the previous n bars',
                          'detail': 'Above the previous n-bar high the score is +100 (breakout up); below the low, −100.', 'horizon': 'medium'},
    'MOMENTUM_TSTAT': {'label': 'Momentum (t-stat)', 'family': 'Momentum', 'fn': _momentum, 'defaults': {'period': 20, 'scale': 2.0},
                       'equation': 'score = clamp(100 × t ÷ scale),  t = ln(closeₜ ÷ closeₜ₋ₙ) ÷ (σ₁ × √n)',
                       'detail': 'σ₁ = standard deviation of 1-bar log returns over n bars. t is the n-bar move in units of its own noise; t = 2 is a strong move.',
                       'horizon': 'medium'},
    'STOCHASTIC_REVERSION': {'label': 'Stochastic reversion', 'family': 'Mean reversion', 'fn': _stochastic, 'defaults': {'period': 14, 'smooth': 3},
                             'equation': 'score = clamp(−2 × (%K − 50)),  %K = SMA(smooth) of 100 × (close − low(n)) ÷ (high(n) − low(n))',
                             'detail': '%K 20 → +60 (near the bottom of the range: expect a rise).', 'horizon': 'short'},
    'ADX_DIRECTION': {'label': 'ADX direction', 'family': 'Trend', 'fn': _adx, 'defaults': {'period': 14, 'full_at': 30.0},
                      'equation': 'score = clamp(100 × (+DI − −DI) ÷ (+DI + −DI) × min(1, ADX ÷ full_at))',
                      'detail': 'Direction from the directional indicators, weighted by trend strength (ADX). Weak trends score near 0.', 'horizon': 'medium'},
    'VWAP_REVERSION': {'label': 'VWAP reversion', 'family': 'Mean reversion', 'fn': _vwap, 'defaults': {'period': 20, 'scale': 1.0},
                       'equation': 'score = clamp(−100 × (close − VWAP(n)) ÷ (scale × ATR14)),  VWAP = Σ(typical price × volume) ÷ Σ volume',
                       'detail': 'Typical price = (high + low + close) ÷ 3; volume is real volume where the source has it, else tick volume.', 'horizon': 'intraday'},
}


def params_of(kind: str, params: dict | None) -> dict:
    if kind not in KINDS:
        raise HTTPException(422, f'Unknown signal formula {kind}.')
    out = dict(KINDS[kind]['defaults'])
    for k, v in (params or {}).items():
        if k not in out:
            raise HTTPException(422, f'{kind} has no parameter {k}; it has {", ".join(out)}.')
        out[k] = type(out[k])(v)
    for k, v in out.items():
        if isinstance(v, (int, float)) and v <= 0:
            raise HTTPException(422, f'{k} must be above 0.')
    if 'fast' in out and 'slow' in out and out['fast'] >= out['slow']:
        raise HTTPException(422, 'fast must be shorter than slow.')
    return out


def compute(kind: str, params: dict | None, bars: list[dict]) -> list:
    """Per bar: None while warming up, else (score, inputs). Only bars up to and including t are used for bar t."""
    return KINDS[kind]['fn'](bars, params_of(kind, params))


def direction(score: float | None, threshold: float) -> str:
    return 'FLAT' if score is None or abs(score) < threshold else ('LONG' if score > 0 else 'SHORT')


def catalogue() -> list[dict]:
    return [{'kind': k, **{x: v[x] for x in ('label', 'family', 'defaults', 'equation', 'detail', 'horizon')}} for k, v in KINDS.items()]


def worked(kind: str, params: dict, score: float | None, inputs: dict | None) -> str:
    """The equation with this bar's numbers in it."""
    if score is None or not inputs:
        return 'warming up: not enough bars yet'
    i, p = inputs, params
    fmt = lambda x: f'{x:.6g}' if isinstance(x, (int, float)) else str(x)  # noqa: E731
    if kind in ('EMA_TREND', 'SMA_TREND'):
        a, b = (i['ema_fast'], i['ema_slow']) if kind == 'EMA_TREND' else (i['sma_fast'], i['sma_slow'])
        return f"100 × ({fmt(a)} − {fmt(b)}) ÷ ({fmt(p['scale'])} × {fmt(i['atr14'])}) = {score:+.1f}"
    if kind == 'MACD_MOMENTUM':
        return f"100 × {fmt(i['histogram'])} ÷ ({fmt(p['scale'])} × {fmt(i['atr14'])}) = {score:+.1f}   (MACD {fmt(i['macd'])} − signal {fmt(i['signal_line'])})"
    if kind == 'RSI_REVERSION':
        return f"2 × (50 − {fmt(i['rsi'])}) = {score:+.1f}"
    if kind == 'RSI_MOMENTUM':
        return f"2 × ({fmt(i['rsi'])} − 50) = {score:+.1f}"
    if kind == 'BOLLINGER_REVERSION':
        return f"z = ({fmt(i['close'])} − {fmt(i['middle'])}) ÷ {fmt(i['sigma'])} = {fmt(i['z'])};  −100 × {fmt(i['z'])} ÷ {fmt(p['deviations'])} = {score:+.1f}"
    if kind == 'ZSCORE_REVERSION':
        return f"−100 × {fmt(i['z'])} ÷ {fmt(p['zmax'])} = {score:+.1f}"
    if kind == 'DONCHIAN_BREAKOUT':
        return (f"position = ({fmt(i['close'])} − {fmt(i['channel_low'])}) ÷ ({fmt(i['channel_high'])} − {fmt(i['channel_low'])}) = {fmt(i['position'])};  "
                f"200 × ({fmt(i['position'])} − 0.5) = {score:+.1f}")
    if kind == 'MOMENTUM_TSTAT':
        return (f"t = ln({fmt(i['close'])} ÷ {fmt(i['close_n_ago'])}) ÷ ({fmt(i['sigma_1bar'])} × √{p['period']}) = {fmt(i['t'])};  "
                f"100 × {fmt(i['t'])} ÷ {fmt(p['scale'])} = {score:+.1f}")
    if kind == 'STOCHASTIC_REVERSION':
        return f"−2 × ({fmt(i['percent_k'])} − 50) = {score:+.1f}"
    if kind == 'ADX_DIRECTION':
        return (f"100 × ({fmt(i['plus_di'])} − {fmt(i['minus_di'])}) ÷ ({fmt(i['plus_di'])} + {fmt(i['minus_di'])}) × min(1, {fmt(i['adx'])} ÷ "
                f"{fmt(p['full_at'])}) = {score:+.1f}")
    if kind == 'VWAP_REVERSION':
        return f"−100 × ({fmt(i['close'])} − {fmt(i['vwap'])}) ÷ ({fmt(p['scale'])} × {fmt(i['atr14'])}) = {score:+.1f}"
    return f'{score:+.1f}'


# ---------- research: does it predict? ----------

def _rank(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda k: xs[k])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def _corr(a: list[float], b: list[float]) -> float | None:
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va, vb = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
    if va == 0 or vb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


def research(kind: str, params: dict | None, bars: list[dict], horizons: list[int], threshold: float) -> dict:
    """Forward-looking statistics of the score. Forward returns use later bars — that is the test, never the score."""
    p = params_of(kind, params)
    s = compute(kind, p, bars)
    c = [float(b['close']) for b in bars]
    spreads = [float(b['spread_avg']) for b in bars if b.get('spread_avg')]
    out_h = []
    for h in horizons:
        xs, ys = [], []
        for t in range(len(bars) - h):
            if s[t] is None or c[t] <= 0:
                continue
            xs.append(s[t][0])
            ys.append(c[t + h] / c[t] - 1)
        if len(xs) < 10:
            out_h.append({'horizon': h, 'n': len(xs), 'note': 'too few bars'})
            continue
        ic = _corr(_rank(xs), _rank(ys))
        t_ic = None if ic is None or abs(ic) >= 1 else ic * math.sqrt((len(xs) - 2) / (1 - ic * ic))
        active = [(x, y) for x, y in zip(xs, ys) if abs(x) >= threshold]
        hits = sum(1 for x, y in active if (x > 0 and y > 0) or (x < 0 and y < 0))
        order = sorted(zip(xs, ys))
        q = max(1, len(order) // 5)
        quint = []
        for k in range(5):
            part = order[k * q:(k + 1) * q] if k < 4 else order[4 * q:]
            if part:
                quint.append({'quintile': k + 1, 'score_from': round(part[0][0], 1), 'score_to': round(part[-1][0], 1),
                              'avg_return_bp': round(10000 * sum(y for _, y in part) / len(part), 2), 'n': len(part)})
        signed = [(1 if x > 0 else -1) * y for x, y in active]
        out_h.append({'horizon': h, 'n': len(xs), 'ic': None if ic is None else round(ic, 4), 'ic_t': None if t_ic is None else round(t_ic, 2),
                      'hit_rate_pct': round(100 * hits / len(active), 1) if active else None, 'active_bars': len(active),
                      'avg_signed_return_bp': round(10000 * sum(signed) / len(signed), 2) if signed else None,
                      'avg_abs_move_bp': round(10000 * sum(abs(y) for y in ys) / len(ys), 2), 'quintiles': quint})
    valid = [x[0] for x in s if x is not None]
    turnover = (sum(abs(valid[k] - valid[k - 1]) for k in range(1, len(valid))) / (200 * (len(valid) - 1))) if len(valid) > 1 else None
    flips = sum(1 for k in range(1, len(valid)) if direction(valid[k], threshold) != direction(valid[k - 1], threshold))
    cost_bp = round(10000 * (sum(spreads) / len(spreads)) / (sum(c) / len(c)), 2) if spreads and c else None
    best = max((h for h in out_h if h.get('ic') is not None), key=lambda h: abs(h['ic']), default=None)
    verdict = None
    if best:
        edge = abs(best.get('avg_signed_return_bp') or 0)
        verdict = ('PREDICTIVE' if abs(best['ic']) >= 0.03 and abs(best.get('ic_t') or 0) >= 2 and (cost_bp is None or edge > cost_bp) else
                   'WEAK' if abs(best.get('ic_t') or 0) >= 1 else 'NO_EDGE')
    return {'kind': kind, 'parameters': p, 'equation': KINDS[kind]['equation'], 'bars': len(bars), 'scored_bars': len(valid),
            'threshold': threshold, 'horizons': out_h, 'turnover_per_bar': None if turnover is None else round(turnover, 4), 'direction_changes': flips,
            'coverage_pct': round(100 * sum(1 for v in valid if abs(v) >= threshold) / len(valid), 1) if valid else None,
            'avg_spread_bp': cost_bp, 'best_horizon': best['horizon'] if best else None, 'verdict': verdict,
            'how_to_read': 'IC = rank correlation between the score and the return h bars later (0.02–0.05 is useful in practice; |t| ≥ 2 is '
                           'unlikely by chance). Hit rate counts bars with |score| ≥ threshold. Quintiles should rise from 1 to 5 for a good '
                           'signal. The edge must beat the spread (cost).',
            'series': [{'t': b['bar_time'].isoformat() if isinstance(b['bar_time'], datetime) else b['bar_time'], 'close': c[k],
                        'score': None if s[k] is None else round(s[k][0], 1)} for k, b in enumerate(bars)][-500:]}


# ---------- the library (stored definitions) ----------

class DefinitionIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(min_length=2, max_length=40, pattern=r'^[A-Z0-9_]+$')
    name: str = Field(min_length=2, max_length=120)
    kind: str
    parameters: dict = Field(default_factory=dict)
    threshold: float = Field(20, ge=1, le=99)
    timeframes: list[str] = Field(default_factory=lambda: ['M5', 'M15', 'H1', 'H4', 'D1'])
    status: str = Field('ACTIVE', pattern=r'^(ACTIVE|PAUSED)$')
    notes: str = Field('', max_length=1000)
    reason: str = Field(min_length=5, max_length=500)


def definitions(db, active_only: bool = False) -> list[dict]:
    rows = db.execute('SELECT * FROM sig_definitions' + (" WHERE status='ACTIVE'" if active_only else '') + ' ORDER BY builtin DESC, code').fetchall()
    return [{**store.clean(r), 'equation': KINDS.get(r['kind'], {}).get('equation'), 'family': KINDS.get(r['kind'], {}).get('family'),
             'label': KINDS.get(r['kind'], {}).get('label')} for r in rows]


def save(actor: str, data: DefinitionIn, def_id: UUID | None = None) -> dict:
    params = params_of(data.kind, data.parameters)
    bad = [t for t in data.timeframes if t not in ('M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1')]
    if bad:
        raise HTTPException(422, f'Unknown timeframe {bad[0]}.')
    with store.transaction('write') as db:
        if def_id is None:
            row = db.execute('INSERT INTO sig_definitions(id, code, name, kind, parameters, threshold, timeframes, status, notes, created_by, updated_by) '
                             'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                             (uuid4(), data.code, data.name, data.kind, Jsonb(params), data.threshold, Jsonb(data.timeframes), data.status, data.notes,
                              actor, actor)).fetchone()
            store.audit(db, actor, 'CREATE_SIGNAL_DEFINITION', 'signal_definition', row['id'], None, dict(row), data.reason)
        else:
            before = db.execute('SELECT * FROM sig_definitions WHERE id=%s FOR UPDATE', (str(def_id),)).fetchone()
            if not before:
                raise HTTPException(404, 'Signal definition not found.')
            row = db.execute('UPDATE sig_definitions SET code=%s, name=%s, kind=%s, parameters=%s, threshold=%s, timeframes=%s, status=%s, notes=%s, '
                             'version=version+1, updated_by=%s, updated_at=now() WHERE id=%s RETURNING *',
                             (data.code, data.name, data.kind, Jsonb(params), data.threshold, Jsonb(data.timeframes), data.status, data.notes, actor,
                              str(def_id))).fetchone()
            db.execute('DELETE FROM sig_live WHERE definition_id=%s', (str(def_id),))
            store.audit(db, actor, 'UPDATE_SIGNAL_DEFINITION', 'signal_definition', def_id, dict(before), dict(row), data.reason)
        return store.clean(row)


def delete(actor: str, def_id: UUID, reason: str) -> None:
    with store.transaction('delete') as db:
        before = db.execute('SELECT * FROM sig_definitions WHERE id=%s', (str(def_id),)).fetchone()
        if not before:
            raise HTTPException(404, 'Signal definition not found.')
        if before['builtin']:
            raise HTTPException(409, 'Built-in signals are kept; pause it or add your own instead.')
        db.execute('DELETE FROM sig_definitions WHERE id=%s', (str(def_id),))
        store.audit(db, actor, 'DELETE_SIGNAL_DEFINITION', 'signal_definition', def_id, dict(before), None, reason)


def live_update(db, account_id, symbol: str, tf: str, bars: list[dict], defs: list[dict]) -> int:
    """Called by the feature engine for each instrument and timeframe: the latest score of every active definition."""
    n = 0
    if not bars:
        return 0
    for d in defs:
        if tf not in (d.get('timeframes') or []):
            continue
        try:
            res = compute(d['kind'], d['parameters'], bars)
        except Exception:  # noqa: BLE001 - one bad definition never stops the others
            continue
        last, prev = res[-1], (res[-2] if len(res) > 1 else None)
        score = None if last is None else round(last[0], 2)
        if last is not None and prev is not None:
            th = float(d['threshold'])
            was, now_dir = direction(prev[0], th), direction(last[0], th)
            if was != now_dir:  # a flip on this bar: kept once per bar, so a re-run never doubles it
                db.execute('INSERT INTO sig_events(definition_id, account_id, broker_symbol, timeframe, bar_time, from_direction, to_direction, score, previous_score) '
                           'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                           (str(d['id']), str(account_id), symbol, tf, bars[-1]['bar_time'], was, now_dir, score, round(prev[0], 2)))
        db.execute("""INSERT INTO sig_live(definition_id, account_id, broker_symbol, timeframe, bar_time, score, previous_score, direction, inputs, computed_at)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                      ON CONFLICT (definition_id, account_id, broker_symbol, timeframe) DO UPDATE SET bar_time=EXCLUDED.bar_time, score=EXCLUDED.score,
                        previous_score=EXCLUDED.previous_score, direction=EXCLUDED.direction, inputs=EXCLUDED.inputs, computed_at=now()""",
                   (str(d['id']), str(account_id), symbol, tf, bars[-1]['bar_time'], score, None if prev is None else round(prev[0], 2),
                    direction(score, float(d['threshold'])), Jsonb((last or (None, {}))[1] or {})))
        n += 1
    return n


def board(db, timeframe: str) -> dict:
    rows = db.execute("""SELECT l.*, d.code, d.name, d.kind, d.parameters, d.threshold, a.code AS account_code, p.instrument_id, i.symbol AS instrument
                         FROM sig_live l JOIN sig_definitions d ON d.id=l.definition_id JOIN cfg_accounts a ON a.id=l.account_id
                         LEFT JOIN md_collection_plans p ON p.account_id=l.account_id AND p.broker_symbol=l.broker_symbol AND p.status <> 'ARCHIVED'
                         LEFT JOIN cfg_instruments i ON i.id=p.instrument_id
                         WHERE l.timeframe=%s AND d.status='ACTIVE' ORDER BY a.code, l.broker_symbol, d.code""", (timeframe,)).fetchall()
    out = []
    for r in rows:
        x = store.clean(r)
        x['worked'] = worked(r['kind'], params_of(r['kind'], r['parameters']), None if r['score'] is None else float(r['score']), r['inputs'])
        x['family'] = KINDS.get(r['kind'], {}).get('family')
        out.append(x)
    return {'timeframe': timeframe, 'items': out}


def consensus(scores: list[float]) -> dict:
    """Composite of several signals on one instrument: mean score, and the share agreeing with its sign."""
    xs = [x for x in scores if x is not None]
    if not xs:
        return {'score': None, 'agree_pct': None, 'n': 0}
    m = sum(xs) / len(xs)
    same = sum(1 for x in xs if (x > 0) == (m > 0) and x != 0)
    return {'score': round(m, 1), 'agree_pct': round(100 * same / len(xs)), 'n': len(xs)}


def overview(db, timeframe: str) -> dict:
    """Stage 3 dashboard: what the library says now on one timeframe."""
    defs = db.execute("SELECT count(*) FILTER (WHERE status='ACTIVE') AS active, count(*) AS total FROM sig_definitions").fetchone()
    rows = board(db, timeframe)['items']
    by_inst: dict = {}
    for r in rows:
        k = (r['account_code'], r['broker_symbol'], str(r['account_id']))
        by_inst.setdefault(k, []).append(float(r['score']) if r['score'] is not None else None)
    cons = sorted(({'account_code': k[0], 'broker_symbol': k[1], 'account_id': k[2], **consensus(v)} for k, v in by_inst.items()),
                  key=lambda x: -abs(x['score'] or 0))
    scored = [r for r in rows if r['score'] is not None]
    strongest = sorted(scored, key=lambda r: -abs(float(r['score'])))[:12]
    flips = db.execute("SELECT count(*) AS n FROM sig_events WHERE at >= now() - interval '24 hours' AND timeframe=%s", (timeframe,)).fetchone()['n']
    last = db.execute('SELECT max(computed_at) AS t FROM sig_live WHERE timeframe=%s', (timeframe,)).fetchone()['t']
    tfs = {r['timeframe']: r['n'] for r in db.execute('SELECT timeframe, count(DISTINCT (account_id, broker_symbol)) AS n FROM sig_live GROUP BY 1').fetchall()}
    return {'timeframe': timeframe, 'definitions_active': defs['active'], 'definitions_total': defs['total'], 'instruments': len(by_inst),
            'scores': len(scored), 'long': sum(1 for r in scored if r['direction'] == 'LONG'), 'short': sum(1 for r in scored if r['direction'] == 'SHORT'),
            'flat': sum(1 for r in scored if r['direction'] == 'FLAT'), 'flips_24h': flips, 'last_computed': last, 'instruments_by_timeframe': tfs,
            'strongest': [{k: r[k] for k in ('code', 'account_code', 'broker_symbol', 'score', 'direction', 'worked', 'bar_time', 'threshold')} for r in strongest],
            'consensus': cons}


def instrument_matrix(db, account_id, symbol: str) -> dict:
    """One instrument: every active signal × every timeframe scored, with the consensus per timeframe."""
    rows = db.execute("""SELECT l.*, d.code, d.kind, d.threshold, d.parameters FROM sig_live l JOIN sig_definitions d ON d.id=l.definition_id
                         WHERE l.account_id=%s AND l.broker_symbol=%s AND d.status='ACTIVE' ORDER BY d.code""", (str(account_id), symbol)).fetchall()
    order = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1']
    tfs = [t for t in order if any(r['timeframe'] == t for r in rows)]
    cells: dict = {}
    for r in rows:
        x = store.clean(r)
        x['worked'] = worked(r['kind'], params_of(r['kind'], r['parameters']), None if r['score'] is None else float(r['score']), r['inputs'])
        cells.setdefault(r['code'], {})[r['timeframe']] = x
    cons = {t: consensus([float(c[t]['score']) for c in cells.values() if t in c and c[t]['score'] is not None]) for t in tfs}
    allv = [float(c[t]['score']) for c in cells.values() for t in c if c[t]['score'] is not None]
    return {'broker_symbol': symbol, 'timeframes': tfs, 'signals': sorted(cells), 'cells': cells, 'consensus': cons, 'overall': consensus(allv)}


def events(db, limit: int = 200, timeframe: str | None = None, code: str | None = None, symbol: str | None = None) -> list[dict]:
    where, args = ['TRUE'], []
    for col, v in (('e.timeframe', timeframe), ('d.code', code), ('e.broker_symbol', symbol)):
        if v:
            where.append(f'{col}=%s'); args.append(v)
    rows = db.execute(f"""SELECT e.*, d.code, d.name, a.code AS account_code FROM sig_events e JOIN sig_definitions d ON d.id=e.definition_id
                          JOIN cfg_accounts a ON a.id=e.account_id WHERE {' AND '.join(where)} ORDER BY e.bar_time DESC, e.id DESC LIMIT %s""", [*args, limit]).fetchall()
    return [store.clean(r) for r in rows]


def correlation(defs: list[dict], bars: list[dict]) -> dict:
    """Pairwise correlation of the signals' score series on one instrument: two signals at 0.9 say the same thing
    twice (a blend would double-count it); low or negative correlation adds information."""
    series = {}
    for d in defs:
        try:
            series[d['code']] = [None if x is None else x[0] for x in compute(d['kind'], d['parameters'], bars)]
        except Exception:  # noqa: BLE001
            continue
    codes = list(series)
    m = []
    for a in codes:
        row = []
        for b in codes:
            pairs = [(x, y) for x, y in zip(series[a], series[b]) if x is not None and y is not None]
            c = _corr([x for x, _ in pairs], [y for _, y in pairs]) if len(pairs) >= 20 else None
            row.append(None if c is None else round(c, 2))
        m.append(row)
    redundant = [{'a': codes[i], 'b': codes[j], 'r': m[i][j]} for i in range(len(codes)) for j in range(i + 1, len(codes))
                 if m[i][j] is not None and abs(m[i][j]) >= 0.8]
    return {'codes': codes, 'matrix': m, 'redundant': redundant, 'bars': len(bars)}


def research_all(defs: list[dict], bars: list[dict], horizons: list[int]) -> list[dict]:
    """Every signal researched on the same bars, ranked by the strength of its best horizon (|IC t-stat|)."""
    out = []
    for d in defs:
        try:
            r = research(d['kind'], d['parameters'], bars, horizons, float(d['threshold']))
        except Exception as exc:  # noqa: BLE001
            out.append({'code': d['code'], 'kind': d['kind'], 'error': str(exc)[:200]})
            continue
        best = next((h for h in r['horizons'] if h['horizon'] == r['best_horizon']), None) or {}
        out.append({'code': d['code'], 'name': d['name'], 'kind': d['kind'], 'family': KINDS[d['kind']]['family'], 'verdict': r['verdict'],
                    'best_horizon': r['best_horizon'], 'ic': best.get('ic'), 'ic_t': best.get('ic_t'), 'hit_rate_pct': best.get('hit_rate_pct'),
                    'edge_bp': best.get('avg_signed_return_bp'), 'cost_bp': r['avg_spread_bp'], 'coverage_pct': r['coverage_pct'],
                    'turnover_per_bar': r['turnover_per_bar'], 'ic_by_horizon': {h['horizon']: h.get('ic') for h in r['horizons']}})
    return sorted(out, key=lambda x: -abs(x.get('ic_t') or 0))


Fn = Callable[..., Any]
