"""Volatility and regime classification (Stage 2 · Market intelligence).

Why it exists: strategies behave differently in trends, ranges and volatile markets; the screen, the signals and the
risk limits need one shared, explainable label for "what state is this market in" per instrument and timeframe.
Purpose: from one bar's features (and the recent ATR history) to a regime, a volatility state, a confidence and the
reasons, in words.
Responsibilities: classify(), the thresholds (named constants), and the attention score of the screener.
Must never: read the database or the clock, or decide a trade (it describes; signals decide later, risk approves).
Dependencies: none. Consumers: intel.engine, tests.
Architectural position: Stage 2, above indicators, below the screener and the signal stage.
Rules (version 1):
  TREND_UP / TREND_DOWN  ADX >= 25 and efficiency ratio >= 0.30, direction from EMA20 vs EMA50 agreeing with +DI/−DI
  RANGE                  ADX < 20
  TRANSITION             anything between, or a trend whose direction measures disagree
  WARMING_UP             fewer bars than MIN_BARS
  Volatility: ATR% rank among the last 100 bars: HIGH >= 80, LOW <= 20, else NORMAL.
"""
from __future__ import annotations

MIN_BARS = 60
ADX_TREND, ADX_RANGE, ER_TREND = 25.0, 20.0, 0.30
VOL_HIGH, VOL_LOW = 80.0, 20.0


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def classify(f: dict, bars: int) -> dict:
    if bars < MIN_BARS or f.get('adx14') is None or f.get('ema50') is None:
        return {'regime': 'WARMING_UP', 'volatility': None, 'confidence': 0.0,
                'reasons': [f'{bars} of {MIN_BARS} bars needed (backfill history to get there now)']}
    adx, er = f['adx14'], f.get('er20') or 0.0
    up = f['ema20'] > f['ema50'] and (f.get('plus_di') or 0) > (f.get('minus_di') or 0)
    down = f['ema20'] < f['ema50'] and (f.get('minus_di') or 0) > (f.get('plus_di') or 0)
    reasons = [f'ADX {adx:.1f}', f'efficiency {er:.2f}', f"EMA20 {'above' if f['ema20'] > f['ema50'] else 'below'} EMA50",
               f"+DI {f.get('plus_di') or 0:.1f} / −DI {f.get('minus_di') or 0:.1f}"]
    if adx >= ADX_TREND and er >= ER_TREND and (up or down):
        regime = 'TREND_UP' if up else 'TREND_DOWN'
        confidence = 0.6 * _clamp((adx - ADX_RANGE) / 20) + 0.4 * _clamp(er / 0.6)
    elif adx < ADX_RANGE:
        regime = 'RANGE'
        confidence = 0.6 * _clamp((ADX_TREND - adx) / 15) + 0.4 * _clamp((0.35 - er) / 0.35)
    else:
        regime = 'TRANSITION'
        confidence = 0.5
        if adx >= ADX_TREND and not (up or down):
            reasons.append('strong movement but EMA and DI disagree on direction')
    rank = f.get('atr_pct_rank100')
    volatility = None if rank is None else ('HIGH' if rank >= VOL_HIGH else 'LOW' if rank <= VOL_LOW else 'NORMAL')
    if rank is not None:
        reasons.append(f'ATR% at the {rank:.0f}th percentile of the last 100 bars')
    return {'regime': regime, 'volatility': volatility, 'confidence': round(confidence, 3), 'reasons': reasons}


def attention(f: dict, quality: float | None, spread: float | None) -> dict:
    """Screener rank 0–100: tradable (quality, spread against ATR) × worth watching (movement, direction).
    Every part is returned so the rank is explainable."""
    atr = f.get('atr14')
    if atr is None or not atr:
        return {'score': None, 'parts': {}}
    q = _clamp((quality if quality is not None else 50.0) / 100)
    cost = 1.0 if spread is None else _clamp(1 - spread / (0.2 * atr))  # spread = 20% of ATR or more scores 0
    movement = _clamp((f.get('atr_pct_rank100') or 50.0) / 100)
    direction = _clamp((f.get('adx14') or 0.0) / 50)
    score = 100 * q * cost * (0.5 * movement + 0.5 * direction)
    return {'score': round(score, 1), 'parts': {'quality': round(q, 2), 'spread_vs_atr': round(cost, 2),
                                                'movement': round(movement, 2), 'direction': round(direction, 2)}}
