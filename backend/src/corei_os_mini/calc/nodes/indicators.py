"""Indicator nodes: every platform indicator (intel.indicators) as a node — wrapped, never re-implemented, so a graph
gives exactly the number the rest of the platform gives.

Why it exists: Stage 2 and Stage 3 formulas are built from the same indicators; one implementation keeps them equal.
Purpose: SMA, EMA, RSI, True range, ATR, Bollinger (mid, upper, lower), ADX (adx, +DI, −DI), Efficiency ratio,
Realised volatility, Range position, MACD (line, signal, histogram), Stochastic (%K, %D), Donchian (upper, lower),
Z-score, VWAP, OBV, Rate of change.
A series that starts with missing values (an indicator of an indicator) is computed from its first complete bar and
padded, exactly as if it had started there; a missing value after that start is refused with the bar number, because
the indicators cannot step over a hole without changing their meaning.
Must never: change an indicator's formula (that belongs in intel.indicators, where every stage sees it).
Dependencies: intel.indicators, intel.engine.BARS_PER_YEAR, calc.nodes. Consumers: calc.run.
Architectural position: Stage 2 building blocks inside graphs.
"""
from __future__ import annotations

from ...intel import indicators as ind
from .. import types as T
from . import Ctx, NodeType, Param, Port, register

S = T.SERIES_FLOAT


def _apply(node: str, fn, series: list[list], n_out: int):
    """Run `fn` on the part of the inputs where every input has a value, pad the warm-up with None."""
    length = len(series[0])
    start = 0
    while start < length and any(s[start] is None for s in series):
        start += 1
    for s in series:
        for j in range(start, length):
            if s[j] is None:
                raise ValueError(f'{node}: an input is missing on bar {j + 1} after it had started; indicators cannot step over a gap.')
    if start >= length:
        out = [None] * length
        return out if n_out == 1 else tuple([None] * length for _ in range(n_out))
    res = fn(*[s[start:] for s in series])
    pad = [None] * start
    if n_out == 1:
        return pad + list(res)
    return tuple(pad + list(r) for r in res)


def _ind(type_: str, label: str, doc: str, ins: list[str], outs: list[tuple[str, str]], params: list[Param], call, formula, lookback):
    def run(ctx: Ctx, values: dict, p: dict) -> dict:
        res = _apply(label, lambda *xs: call(ctx, p, *xs), [values[i] for i in ins], len(outs))
        if len(outs) == 1:
            return {outs[0][0]: res}
        return {name: r for (name, _d), r in zip(outs, res)}
    register(NodeType(type_, 'indicators', label, doc, [Port(i, S) for i in ins], [Port(o, S, d) for o, d in outs], params, run, formula, lookback))


def N(default: int, doc: str = 'bars', lo: int = 1, hi: int = 1000, name: str = 'n') -> Param:
    return Param(name, 'int', default, lo, hi, doc=doc)


_ind('ind.sma', 'SMA', 'Simple moving average.', ['x'], [('out', 'SMA(n)')], [N(20)],
     lambda c, p, x: ind.sma(x, p['n']), lambda p: f"SMA(n={p.get('n')}) = (xₜ + … + xₜ₋ₙ₊₁) ÷ n", lambda p: p['n'])
_ind('ind.ema', 'EMA', 'Exponential moving average, seeded with the SMA of the first n values.', ['x'], [('out', 'EMA(n)')], [N(20)],
     lambda c, p, x: ind.ema(x, p['n']), lambda p: f"EMA(n={p.get('n')})ₜ = EMAₜ₋₁ + α·(xₜ − EMAₜ₋₁), α = 2 ÷ (n + 1)", lambda p: p['n'])
_ind('ind.rsi', 'RSI', 'Relative strength index (Wilder).', ['close'], [('out', 'RSI 0…100')], [N(14)],
     lambda c, p, x: ind.rsi(x, p['n']), lambda p: f"RSI(n={p.get('n')}) = 100 − 100 ÷ (1 + avg gain ÷ avg loss)  (Wilder averages)", lambda p: p['n'] + 1)
_ind('ind.true_range', 'True range', 'max(high − low, |high − previous close|, |low − previous close|).', ['high', 'low', 'close'], [('out', 'true range')], [],
     lambda c, p, h, lo, cl: ind.true_range(h, lo, cl), lambda p: 'TR = max(high − low, |high − closeₜ₋₁|, |low − closeₜ₋₁|)', lambda p: 1)
_ind('ind.atr', 'ATR', 'Average true range (Wilder).', ['high', 'low', 'close'], [('out', 'ATR(n)')], [N(14)],
     lambda c, p, h, lo, cl: ind.atr(h, lo, cl, p['n']), lambda p: f"ATR(n={p.get('n')}) = Wilder average of the true range", lambda p: p['n'] + 1)
_ind('ind.bollinger', 'Bollinger bands', 'SMA ± k population standard deviations.', ['close'],
     [('mid', 'SMA(n)'), ('upper', 'mid + k·σ'), ('lower', 'mid − k·σ')], [N(20), Param('k', 'float', 2.0, 0.1, 10, doc='deviations')],
     lambda c, p, x: ind.bollinger(x, p['n'], p['k']), lambda p: f"mid = SMA({p.get('n')}), upper/lower = mid ± {p.get('k')}·σ (population)", lambda p: p['n'])
_ind('ind.adx', 'ADX', 'Average directional index with +DI and −DI (Wilder).', ['high', 'low', 'close'],
     [('adx', 'ADX'), ('plus_di', '+DI'), ('minus_di', '−DI')], [N(14)],
     lambda c, p, h, lo, cl: ind.adx(h, lo, cl, p['n']), lambda p: f"+DI, −DI = 100 × smoothed ±DM ÷ ATR({p.get('n')}); ADX = Wilder average of 100·|+DI − −DI| ÷ (+DI + −DI)",
     lambda p: 2 * p['n'] + 1)
_ind('ind.efficiency_ratio', 'Efficiency ratio', 'Net move ÷ total path over n bars (0 = noise, 1 = straight line).', ['close'], [('out', 'ER 0…1')], [N(20)],
     lambda c, p, x: ind.efficiency_ratio(x, p['n']), lambda p: f"ER = |closeₜ − closeₜ₋{p.get('n')}| ÷ Σ|closeᵢ − closeᵢ₋₁|", lambda p: p['n'] + 1)


def _bpy(ctx: Ctx) -> float:
    from ...intel.engine import BARS_PER_YEAR
    return BARS_PER_YEAR.get(ctx.timeframe, BARS_PER_YEAR['H1'])


_ind('ind.realised_vol', 'Realised volatility', 'Annualised standard deviation of log returns over n bars, in %.', ['close'], [('out', 'volatility %')], [N(20)],
     lambda c, p, x: ind.realised_vol(x, p['n'], _bpy(c)), lambda p: f"RV = 100 × √(sample variance of log returns over {p.get('n')} bars × bars per year)", lambda p: p['n'] + 1)
_ind('ind.range_position', 'Range position', 'Where the close sits in the n-bar high–low range (0 = at the low, 100 = at the high, 50 when flat).', ['high', 'low', 'close'],
     [('out', '0…100')], [N(20)], lambda c, p, h, lo, cl: ind.range_position(h, lo, cl, p['n']),
     lambda p: f"100 × (close − lowest low) ÷ (highest high − lowest low) over {p.get('n')} bars (this bar included)", lambda p: p['n'])
_ind('ind.macd', 'MACD', 'EMA(fast) − EMA(slow), its EMA(signal), and the histogram.', ['close'],
     [('line', 'MACD line'), ('signal', 'signal line'), ('hist', 'histogram')],
     [N(12, 'fast', name='fast'), N(26, 'slow', name='slow'), N(9, 'signal', name='signal')],
     lambda c, p, x: ind.macd(x, p['fast'], p['slow'], p['signal']),
     lambda p: f"line = EMA({p.get('fast')}) − EMA({p.get('slow')}); signal = EMA({p.get('signal')}) of line; hist = line − signal",
     lambda p: p['slow'] + p['signal'])
_ind('ind.stochastic', 'Stochastic', '%K = where the close sits in the n-bar range, smoothed; %D = its average.', ['high', 'low', 'close'],
     [('k', '%K'), ('d', '%D')], [N(14), N(3, 'smoothing of %K', name='k_smooth'), N(3, '%D average', name='d')],
     lambda c, p, h, lo, cl: ind.stochastic(h, lo, cl, p['n'], p['k_smooth'], p['d']),
     lambda p: f"%K = SMA({p.get('k_smooth')}) of 100 × (close − lowest low) ÷ (highest high − lowest low) over {p.get('n')}; %D = SMA({p.get('d')}) of %K",
     lambda p: p['n'] + p['k_smooth'] + p['d'])
_ind('ind.donchian', 'Donchian channel', 'Highest high and lowest low of the last n bars, including this one.', ['high', 'low'],
     [('upper', 'highest high'), ('lower', 'lowest low')], [N(20)], lambda c, p, h, lo: ind.donchian(h, lo, p['n']),
     lambda p: f"upper = max(high, {p.get('n')} bars), lower = min(low, {p.get('n')} bars) — this bar included", lambda p: p['n'])
_ind('ind.zscore', 'Z-score', '(close − SMA) ÷ population standard deviation over n bars (0 when flat).', ['close'], [('out', 'z')], [N(20)],
     lambda c, p, x: ind.zscore(x, p['n']), lambda p: f"z = (close − SMA({p.get('n')})) ÷ σ({p.get('n')})", lambda p: p['n'])
_ind('ind.vwap', 'VWAP', 'Volume-weighted average of the typical price over n bars.', ['high', 'low', 'close', 'volume'], [('out', 'VWAP')], [N(20)],
     lambda c, p, h, lo, cl, v: ind.vwap(h, lo, cl, v, p['n']), lambda p: f"VWAP = Σ(typical × volume) ÷ Σ volume over {p.get('n')} bars, typical = (h + l + c) ÷ 3",
     lambda p: p['n'])
_ind('ind.obv', 'OBV', 'On-balance volume: volume added on up closes, subtracted on down closes.', ['close', 'volume'], [('out', 'OBV')], [],
     lambda c, p, x, v: ind.obv(x, v), lambda p: 'OBVₜ = OBVₜ₋₁ ± volumeₜ (sign of closeₜ − closeₜ₋₁)', lambda p: 1)
_ind('ind.roc', 'Rate of change', '100 × (close ÷ close n bars ago − 1).', ['close'], [('out', '%')], [N(10)],
     lambda c, p, x: ind.roc(x, p['n']), lambda p: f"ROC = 100 × (closeₜ ÷ closeₜ₋{p.get('n')} − 1)", lambda p: p['n'])
