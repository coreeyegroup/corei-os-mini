"""Indicators of the feature store (Stage 2 · Market intelligence).

Why it exists: every strategy needs the same handful of measures (trend, momentum, volatility, range). Computing them
once, the same way, from stored bars removes "my RSI differs from yours" and makes every later decision replayable.
Purpose: pure functions from a list of closed bars to indicator series.
Responsibilities: SMA, EMA, Wilder RSI, Wilder ATR, Bollinger bands, Wilder ADX with +DI/−DI, Kaufman efficiency ratio,
realised volatility, returns and range position. Same inputs give the same outputs (no clock, no randomness).
Must never: read the database or the clock, or be used for money, quantity or P&L (analytics only; prices arrive as
the stored double-precision bar values and the results are descriptive measures, not order values).
Dependencies: standard library only. Consumers: intel.engine, tests.
Architectural position: Stage 2, below the engine; above it sit regime classification and the screener.
Conventions: series are aligned to the input (None until enough bars); Wilder smoothing is seeded with the simple
average of the first `n` values, as in Wilder (1978).
"""
from __future__ import annotations

import math
from collections.abc import Sequence

FEATURE_VERSION = 1  # bump when any definition below changes; stored with every feature row


def sma(values: Sequence[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def ema(values: Sequence[float], n: int) -> list[float | None]:
    """Exponential average, alpha 2/(n+1), seeded with the SMA of the first n values."""
    out: list[float | None] = [None] * len(values)
    if len(values) < n:
        return out
    alpha = 2.0 / (n + 1)
    prev = sum(values[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(values)):
        prev = alpha * values[i] + (1 - alpha) * prev
        out[i] = prev
    return out


def _wilder(values: Sequence[float], n: int, start: int = 0) -> list[float | None]:
    """Wilder's smoothing over values[start:], seeded with their first-n mean."""
    out: list[float | None] = [None] * len(values)
    if len(values) - start < n:
        return out
    prev = sum(values[start:start + n]) / n
    out[start + n - 1] = prev
    for i in range(start + n, len(values)):
        prev = (prev * (n - 1) + values[i]) / n
        out[i] = prev
    return out


def rsi(closes: Sequence[float], n: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = [0.0] + [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [0.0] + [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    ag, al = _wilder(gains, n, 1), _wilder(losses, n, 1)
    for i in range(len(closes)):
        if ag[i] is None or al[i] is None:
            continue
        out[i] = 100.0 if al[i] == 0 else (0.0 if ag[i] == 0 and al[i] > 0 else 100.0 - 100.0 / (1.0 + ag[i] / al[i]))
    return out


def true_range(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> list[float]:
    return [highs[0] - lows[0]] + [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
                                   for i in range(1, len(closes))]


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], n: int = 14) -> list[float | None]:
    return _wilder(true_range(highs, lows, closes), n, 1) if len(closes) > n else [None] * len(closes)


def bollinger(closes: Sequence[float], n: int = 20, k: float = 2.0) -> tuple[list[float | None], list[float | None], list[float | None]]:
    mid = sma(closes, n)
    upper: list[float | None] = [None] * len(closes)
    lower: list[float | None] = [None] * len(closes)
    for i in range(n - 1, len(closes)):
        window = closes[i - n + 1:i + 1]
        sd = math.sqrt(sum((x - mid[i]) ** 2 for x in window) / n)  # population SD, as Bollinger defines it
        upper[i], lower[i] = mid[i] + k * sd, mid[i] - k * sd
    return mid, upper, lower


def adx(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], n: int = 14) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """(ADX, +DI, −DI), Wilder."""
    size = len(closes)
    none: list[float | None] = [None] * size
    if size <= 2 * n:
        return none, list(none), list(none)
    plus_dm = [0.0] + [(highs[i] - highs[i - 1]) if highs[i] - highs[i - 1] > lows[i - 1] - lows[i] and highs[i] - highs[i - 1] > 0 else 0.0
                       for i in range(1, size)]
    minus_dm = [0.0] + [(lows[i - 1] - lows[i]) if lows[i - 1] - lows[i] > highs[i] - highs[i - 1] and lows[i - 1] - lows[i] > 0 else 0.0
                        for i in range(1, size)]
    tr = true_range(highs, lows, closes)
    # Wilder sums (not means) for DM and TR; the ratio is the same either way.
    s_tr, s_p, s_m = _wilder(tr, n, 1), _wilder(plus_dm, n, 1), _wilder(minus_dm, n, 1)
    pdi: list[float | None] = [None] * size
    mdi: list[float | None] = [None] * size
    dx: list[float] = [0.0] * size
    first = None
    for i in range(size):
        if s_tr[i] is None or not s_tr[i]:
            continue
        pdi[i] = 100.0 * s_p[i] / s_tr[i]
        mdi[i] = 100.0 * s_m[i] / s_tr[i]
        total = pdi[i] + mdi[i]
        dx[i] = 0.0 if total == 0 else 100.0 * abs(pdi[i] - mdi[i]) / total
        first = i if first is None else first
    adx_out = _wilder(dx, n, first) if first is not None else none
    return adx_out, pdi, mdi


def efficiency_ratio(closes: Sequence[float], n: int = 20) -> list[float | None]:
    """Kaufman: net move over the sum of bar-to-bar moves, 0 (noise, range) to 1 (straight line, trend)."""
    out: list[float | None] = [None] * len(closes)
    for i in range(n, len(closes)):
        path = sum(abs(closes[j] - closes[j - 1]) for j in range(i - n + 1, i + 1))
        out[i] = 0.0 if path == 0 else abs(closes[i] - closes[i - n]) / path
    return out


def realised_vol(closes: Sequence[float], n: int, bars_per_year: float) -> list[float | None]:
    """Annualised standard deviation of log returns over n bars, in percent."""
    out: list[float | None] = [None] * len(closes)
    rets = [0.0] + [math.log(closes[i] / closes[i - 1]) if closes[i - 1] > 0 and closes[i] > 0 else 0.0 for i in range(1, len(closes))]
    for i in range(n, len(closes)):
        window = rets[i - n + 1:i + 1]
        mean = sum(window) / n
        var = sum((r - mean) ** 2 for r in window) / (n - 1)
        out[i] = 100.0 * math.sqrt(var * bars_per_year)
    return out


def range_position(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], n: int = 20) -> list[float | None]:
    """Where the close sits in the last n bars' high-low range: 0 at the low, 100 at the high."""
    out: list[float | None] = [None] * len(closes)
    for i in range(n - 1, len(closes)):
        hi, lo = max(highs[i - n + 1:i + 1]), min(lows[i - n + 1:i + 1])
        out[i] = 50.0 if hi == lo else 100.0 * (closes[i] - lo) / (hi - lo)
    return out
