"""Series nodes: operations along time — shift, rolling windows, differences, returns, cumulative totals, normalising
by ATR.

Why it exists: most formulas are "this value compared with its own past"; these nodes say exactly which past bars are
used, so a reader sees the window and a test proves no future bar is read.
Purpose: Shift (k bars ago), Rolling (mean, sum, min, max, sample stdev, population stdev, percentile rank — over the
last n bars including this one, or the n bars before it), Difference, Percent change, Log return, Cumulative (sum,
max, min), Normalise by ATR (x ÷ (scale × ATR)).
Every output position i uses only inputs at positions ≤ i; a window with a missing value gives a missing value.
Must never: read a later bar; fill a missing value.
Dependencies: math, calc.nodes. Consumers: calc.run.
Architectural position: time operations inside graphs (all stages).
"""
from __future__ import annotations

import math
from bisect import bisect_left

from .. import types as T
from . import Ctx, NodeType, Param, Port, register

S = T.SERIES_FLOAT


def _shift(ctx: Ctx, ins: dict, p: dict) -> dict:
    x, k = ins['x'], p['k']
    return {'out': [x[i - k] if i >= k else None for i in range(len(x))]}


register(NodeType('series.shift', 'series', 'Shift', 'The value k bars ago (k = 0 is this bar). A future bar cannot be asked for.',
                  [Port('x', S)], [Port('out', S, 'xₜ₋ₖ')], [Param('k', 'int', 1, 0, 5000, doc='bars ago')], _shift,
                  lambda p: f"out = xₜ₋{p.get('k')}", lambda p: p['k']))

ROLLING = ('mean', 'sum', 'min', 'max', 'stdev', 'stdev_pop', 'rank')


def _window_stat(w: list[float], fn: str):
    n = len(w)
    if fn == 'mean':
        return sum(w) / n
    if fn == 'sum':
        return sum(w)
    if fn == 'min':
        return min(w)
    if fn == 'max':
        return max(w)
    if fn in ('stdev', 'stdev_pop'):
        if fn == 'stdev' and n < 2:
            return None
        m = sum(w) / n
        return math.sqrt(sum((x - m) ** 2 for x in w) / (n - 1 if fn == 'stdev' else n))
    # rank: where the last value sits among the window, 0…100 (the platform's percentile rank)
    last = w[-1]
    return 100.0 * bisect_left(sorted(w), last) / (n - 1 or 1)


def _rolling(ctx: Ctx, ins: dict, p: dict) -> dict:
    x, n, fn, before = ins['x'], p['n'], p['fn'], p['exclude_current']
    out = []
    for i in range(len(x)):
        lo, hi = (i - n, i) if before else (i - n + 1, i + 1)
        if lo < 0:
            out.append(None)
            continue
        w = x[lo:hi]
        out.append(None if any(v is None for v in w) else _window_stat(w, fn))
    return {'out': out}


_RF = {'mean': 'Σx ÷ n', 'sum': 'Σx', 'min': 'min(x)', 'max': 'max(x)', 'stdev': '√(Σ(x − x̄)² ÷ (n − 1)), x̄ = Σx ÷ n',
       'stdev_pop': '√(Σ(x − x̄)² ÷ n)', 'rank': '100 × (values below xₜ) ÷ (n − 1)'}


def _rolling_formula(p: dict) -> str:
    n = p.get('n')
    span = f'xₜ₋{n}…xₜ₋₁' if p.get('exclude_current') else f'xₜ₋{(n or 1) - 1}…xₜ'
    return f"{p.get('fn')} over {span}: {_RF.get(p.get('fn'), '')}"


register(NodeType('series.rolling', 'series', 'Rolling window', 'A statistic of the last n bars — this bar included, or the n bars before it.',
                  [Port('x', S)], [Port('out', S)],
                  [Param('fn', 'choice', 'mean', choices=ROLLING, doc='mean, sum, min, max, stdev (sample), stdev_pop, rank (0…100)'),
                   Param('n', 'int', 20, 1, 5000, doc='bars in the window'),
                   Param('exclude_current', 'bool', False, doc='use the n bars before this one (a breakout compares with them)')],
                  _rolling, _rolling_formula, lambda p: p['n'] + (1 if p['exclude_current'] else 0)))


def _lagged(name: str, label: str, doc: str, f, formula):
    def run(ctx: Ctx, ins: dict, p: dict) -> dict:
        x, k = ins['x'], p['k']
        out = []
        for i in range(len(x)):
            a, b = x[i], (x[i - k] if i >= k else None)
            out.append(None if a is None or b is None else f(a, b))
        return {'out': out}
    register(NodeType(name, 'series', label, doc, [Port('x', S)], [Port('out', S)], [Param('k', 'int', 1, 1, 5000, doc='bars back')],
                      run, formula, lambda p: p['k']))


_lagged('series.diff', 'Difference', 'The change over k bars: xₜ − xₜ₋ₖ.', lambda a, b: a - b, lambda p: f"out = xₜ − xₜ₋{p.get('k')}")
_lagged('series.pct_change', 'Percent change', '100 × (xₜ ÷ xₜ₋ₖ − 1); missing when xₜ₋ₖ is 0.',
        lambda a, b: None if b == 0 else 100.0 * (a / b - 1), lambda p: f"out = 100 × (xₜ ÷ xₜ₋{p.get('k')} − 1)")
_lagged('series.log_return', 'Log return', 'ln(xₜ ÷ xₜ₋ₖ); missing when either is 0 or less.',
        lambda a, b: math.log(a / b) if a > 0 and b > 0 else None, lambda p: f"out = ln(xₜ ÷ xₜ₋{p.get('k')})")


def _cumulative(ctx: Ctx, ins: dict, p: dict) -> dict:
    x, fn = ins['x'], p['fn']
    out, acc = [], None
    for v in x:
        if v is None:
            out.append(None)
            continue
        acc = v if acc is None else (acc + v if fn == 'sum' else max(acc, v) if fn == 'max' else min(acc, v))
        out.append(acc)
    return {'out': out}


register(NodeType('series.cumulative', 'series', 'Cumulative', 'Running total, highest or lowest from the first bar supplied (missing bars are skipped).',
                  [Port('x', S)], [Port('out', S)], [Param('fn', 'choice', 'sum', choices=('sum', 'max', 'min'))], _cumulative,
                  lambda p: f"out = {p.get('fn')}(x₀ … xₜ)"))


def _norm_atr(ctx: Ctx, ins: dict, p: dict) -> dict:
    x, a, k = ins['x'], ins['atr'], p['scale']
    return {'out': [None if v is None or d is None or d == 0 else v / (k * d) for v, d in zip(x, a)]}


register(NodeType('series.normalise_atr', 'series', 'Normalise by ATR', 'x ÷ (scale × ATR): a distance in ATRs, the same on every instrument.',
                  [Port('x', S), Port('atr', S)], [Port('out', S)], [Param('scale', 'float', 1.0, 1e-9, 1e6)], _norm_atr,
                  lambda p: f"out = x ÷ ({p.get('scale')} × ATR)"))
