"""Strategy algorithms (Stage 4 · Strategy intelligence).

Why it exists: a strategy version names an algorithm and its parameters; the live runtime and the backtest must turn
the same closed bars into the same decisions, so both call these functions.
Purpose: parameter models (validated when a version is saved) and one pure function per algorithm that walks closed
bars and their features and returns the strategy's target position after each bar (LONG, SHORT or FLAT) with the
strength and the reasons for each change.
Responsibilities: moving-average and EMA crossovers, RSI and Bollinger mean reversion, Donchian breakout, and a rule
builder on any feature (standard or custom indicator). Direction filter (both, long only, short only).
Must never: read the clock or the database, size or send anything (risk and execution do), or use the values it
computes for money (indicators are descriptive).
Dependencies: intel.indicators. Consumers: trading.runtime, trading.backtest, strategy_registry (catalogue).
Architectural position: Stage 4, below the runtime and the backtest.
Convention: a decision is taken on a closed bar, from that bar and earlier ones only (point-in-time); the walk starts
FLAT at the first bar of the window, so a window long enough for the indicators is part of the contract.
"""
from __future__ import annotations

from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..intel import indicators as ind

TF = Literal['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1']
Direction = Literal['BOTH', 'LONG_ONLY', 'SHORT_ONLY']


class _P(BaseModel):
    model_config = ConfigDict(extra='forbid')
    timeframe: TF = Field(description='Bar period the strategy runs on')
    direction: Direction = Field('BOTH', description='Trade both ways, long only or short only')


class MaCross(_P):
    """Long when the fast simple moving average crosses above the slow one, short when it crosses below."""
    fast_window: int = Field(ge=1, le=500, description='Bars in the fast moving average')
    slow_window: int = Field(ge=2, le=1000, description='Bars in the slow moving average')

    @model_validator(mode='after')
    def _order(self) -> 'MaCross':
        if self.fast_window >= self.slow_window:
            raise ValueError('fast_window must be smaller than slow_window')
        return self


class EmaCross(_P):
    """Long when the fast EMA crosses above the slow EMA (e.g. 9/21), short when it crosses below."""
    fast: int = Field(9, ge=2, le=500, description='Fast EMA length')
    slow: int = Field(21, ge=3, le=1000, description='Slow EMA length')

    @model_validator(mode='after')
    def _order(self) -> 'EmaCross':
        if self.fast >= self.slow:
            raise ValueError('fast must be smaller than slow')
        return self


class RsiReversion(_P):
    """Long when RSI rises back above the lower level, short when it falls back below the upper level; exit at the middle."""
    period: int = Field(14, ge=2, le=200, description='RSI length')
    lower: float = Field(30, gt=0, lt=50, description='Oversold level')
    upper: float = Field(70, gt=50, lt=100, description='Overbought level')
    exit_level: float = Field(50, gt=0, lt=100, description='Exit when RSI reaches this level')


class DonchianBreakout(_P):
    """Long on a close above the highest high of the last N bars, short below the lowest low; exit on the shorter channel."""
    entry: int = Field(20, ge=2, le=500, description='Entry channel (bars)')
    exit: int = Field(10, ge=2, le=500, description='Exit channel (bars)')


class BollingerReversion(_P):
    """Long when the close comes back inside the lower band, short when it comes back inside the upper band; exit at the middle."""
    period: int = Field(20, ge=2, le=500, description='Band length')
    deviations: float = Field(2.0, ge=0.5, le=5, description='Band width in standard deviations')


class Condition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    field: str = Field(pattern=r'^[a-z0-9_]{1,60}$', description='A feature, e.g. rsi14, ema_9, close')
    op: Literal['>', '<', '>=', '<=', 'CROSSES_ABOVE', 'CROSSES_BELOW']
    value: str = Field(min_length=1, max_length=60, description='A number, or another feature (e.g. ema_21)')


class Rules(_P):
    """Your own conditions on any feature: all entry conditions must hold to enter; any exit condition exits."""
    long_entry: list[Condition] = Field(default_factory=list, max_length=10)
    short_entry: list[Condition] = Field(default_factory=list, max_length=10)
    long_exit: list[Condition] = Field(default_factory=list, max_length=10)
    short_exit: list[Condition] = Field(default_factory=list, max_length=10)

    @model_validator(mode='after')
    def _some(self) -> 'Rules':
        if not self.long_entry and not self.short_entry:
            raise ValueError('add at least one long or short entry condition')
        return self


def _closes(bars):
    return [float(b['close']) for b in bars]


def _strength(gap: float, atr: float | None) -> int:
    if not atr:
        return 50
    return max(10, min(100, round(100 * abs(gap) / atr)))


Step = tuple[str | None, int, str]  # (new target or None, strength, reason)


def _walk(n: int, step: Callable[[int, str], Step], direction: str) -> list[dict]:
    """Run a target state machine over n bars: returns per bar {target, change, strength, reason}."""
    state, out = 'FLAT', []
    for i in range(n):
        new, strength, reason = step(i, state) if i else (None, 0, '')
        if new == 'LONG' and direction == 'SHORT_ONLY':
            new = 'FLAT' if state == 'SHORT' else None
        if new == 'SHORT' and direction == 'LONG_ONLY':
            new = 'FLAT' if state == 'LONG' else None
        changed = new is not None and new != state
        if changed:
            state = new
        out.append({'target': state, 'change': changed, 'strength': strength if changed else 0, 'reason': reason if changed else ''})
    return out


def _cross(fast, slow, rows, i, state, label):
    f0, s0, f1, s1 = fast[i - 1], slow[i - 1], fast[i], slow[i]
    if None in (f0, s0, f1, s1):
        return None, 0, ''
    atr = rows[i].get('atr14')
    if f0 <= s0 and f1 > s1:
        return 'LONG', _strength(f1 - s1, atr), f'{label}: fast {f1:.5g} crossed above slow {s1:.5g}'
    if f0 >= s0 and f1 < s1:
        return 'SHORT', _strength(f1 - s1, atr), f'{label}: fast {f1:.5g} crossed below slow {s1:.5g}'
    return None, 0, ''


def ma_cross(bars, rows, p: MaCross):
    c = _closes(bars)
    fast, slow = ind.sma(c, p.fast_window), ind.sma(c, p.slow_window)
    return _walk(len(bars), lambda i, s: _cross(fast, slow, rows, i, s, f'SMA {p.fast_window}/{p.slow_window}'), p.direction)


def ema_cross(bars, rows, p: EmaCross):
    c = _closes(bars)
    fast, slow = ind.ema(c, p.fast), ind.ema(c, p.slow)
    return _walk(len(bars), lambda i, s: _cross(fast, slow, rows, i, s, f'EMA {p.fast}/{p.slow}'), p.direction)


def rsi_reversion(bars, rows, p: RsiReversion):
    r = ind.rsi(_closes(bars), p.period)

    def step(i, state):
        a, b = r[i - 1], r[i]
        if a is None or b is None:
            return None, 0, ''
        if state == 'LONG' and b >= p.exit_level:
            return 'FLAT', 50, f'RSI {b:.1f} reached the exit level {p.exit_level:g}'
        if state == 'SHORT' and b <= p.exit_level:
            return 'FLAT', 50, f'RSI {b:.1f} reached the exit level {p.exit_level:g}'
        if a < p.lower <= b:
            return 'LONG', max(10, min(100, round(2 * (p.exit_level - min(a, b))))), f'RSI rose back above {p.lower:g} ({a:.1f} → {b:.1f})'
        if a > p.upper >= b:
            return 'SHORT', max(10, min(100, round(2 * (max(a, b) - p.exit_level)))), f'RSI fell back below {p.upper:g} ({a:.1f} → {b:.1f})'
        return None, 0, ''
    return _walk(len(bars), step, p.direction)


def donchian_breakout(bars, rows, p: DonchianBreakout):
    h, lo, c = [float(b['high']) for b in bars], [float(b['low']) for b in bars], _closes(bars)

    def step(i, state):
        if i < p.entry:
            return None, 0, ''
        hi_e, lo_e = max(h[i - p.entry:i]), min(lo[i - p.entry:i])
        hi_x, lo_x = max(h[max(0, i - p.exit):i]), min(lo[max(0, i - p.exit):i])
        atr = rows[i].get('atr14')
        if state == 'LONG' and c[i] < lo_x:
            return 'FLAT', 50, f'Close {c[i]:.5g} below the {p.exit}-bar low {lo_x:.5g}'
        if state == 'SHORT' and c[i] > hi_x:
            return 'FLAT', 50, f'Close {c[i]:.5g} above the {p.exit}-bar high {hi_x:.5g}'
        if c[i] > hi_e and state != 'LONG':
            return 'LONG', _strength(c[i] - hi_e, atr), f'Close {c[i]:.5g} broke the {p.entry}-bar high {hi_e:.5g}'
        if c[i] < lo_e and state != 'SHORT':
            return 'SHORT', _strength(lo_e - c[i], atr), f'Close {c[i]:.5g} broke the {p.entry}-bar low {lo_e:.5g}'
        return None, 0, ''
    return _walk(len(bars), step, p.direction)


def bollinger_reversion(bars, rows, p: BollingerReversion):
    c = _closes(bars)
    mid, up, dn = ind.bollinger(c, p.period, p.deviations)

    def step(i, state):
        if None in (up[i - 1], dn[i - 1], up[i], dn[i]):
            return None, 0, ''
        if state == 'LONG' and c[i] >= mid[i]:
            return 'FLAT', 50, f'Close {c[i]:.5g} reached the middle band {mid[i]:.5g}'
        if state == 'SHORT' and c[i] <= mid[i]:
            return 'FLAT', 50, f'Close {c[i]:.5g} reached the middle band {mid[i]:.5g}'
        width = (up[i] - dn[i]) or 1
        if c[i - 1] < dn[i - 1] and c[i] >= dn[i]:
            return 'LONG', max(10, min(100, round(100 * (mid[i] - c[i]) / width * 2))), f'Close back inside the lower band {dn[i]:.5g}'
        if c[i - 1] > up[i - 1] and c[i] <= up[i]:
            return 'SHORT', max(10, min(100, round(100 * (c[i] - mid[i]) / width * 2))), f'Close back inside the upper band {up[i]:.5g}'
        return None, 0, ''
    return _walk(len(bars), step, p.direction)


class BlendComponent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: str = Field(description='A signal formula from the Stage 3 library (EMA_TREND, RSI_REVERSION, …)')
    parameters: dict = Field(default_factory=dict, description='Its parameters (blank: the library defaults)')
    weight: float = Field(1.0, ge=-10, le=10, description='Weight in the combined score (negative inverts the signal)')


class SignalBlend(_P):
    """Combine Stage 3 signals: combined = Σ wᵢ·scoreᵢ ÷ Σ|wᵢ| (−100…+100). Enter long at or above the entry level,
    short at or below −entry; exit when it falls back inside ±exit (or reverses)."""
    components: list[BlendComponent] = Field(min_length=1, max_length=12)
    entry: float = Field(40, gt=0, le=100, description='Enter when |combined| ≥ this')
    exit: float = Field(10, ge=0, lt=100, description='Exit when |combined| falls below this (or the sign turns)')

    @model_validator(mode='after')
    def _check(self) -> 'SignalBlend':
        from .signal_library import params_of
        for c in self.components:
            c.parameters = params_of(c.kind, c.parameters)
        if self.exit >= self.entry:
            raise ValueError('exit must be below entry')
        if not any(c.weight for c in self.components):
            raise ValueError('at least one weight must be non-zero')
        return self


def signal_blend(bars, rows, p: SignalBlend):
    from .signal_library import compute
    series = [compute(c.kind, c.parameters, bars) for c in p.components]
    total_w = sum(abs(c.weight) for c in p.components)

    def combined(i):
        parts = [(c, s[i]) for c, s in zip(p.components, series)]
        if any(v is None for _, v in parts):
            return None, ''
        score = sum(c.weight * v[0] for c, v in parts) / total_w
        text = ' + '.join(f'{c.kind} {v[0]:+.1f}×{c.weight:g}' for c, v in parts) + f' ÷ {total_w:g} = {score:+.1f}'
        return score, text

    def step(i, state):
        score, text = combined(i)
        if score is None:
            return None, 0, ''
        strength = max(10, min(100, round(abs(score))))
        if state == 'LONG' and (score < p.exit or score <= -p.entry):
            return ('SHORT' if score <= -p.entry else 'FLAT'), strength, f'Combined score {score:+.1f} (exit {p.exit:g}): {text}'
        if state == 'SHORT' and (score > -p.exit or score >= p.entry):
            return ('LONG' if score >= p.entry else 'FLAT'), strength, f'Combined score {score:+.1f} (exit {p.exit:g}): {text}'
        if state == 'FLAT' and score >= p.entry:
            return 'LONG', strength, f'Combined score {score:+.1f} ≥ entry {p.entry:g}: {text}'
        if state == 'FLAT' and score <= -p.entry:
            return 'SHORT', strength, f'Combined score {score:+.1f} ≤ −{p.entry:g}: {text}'
        return None, 0, ''
    return _walk(len(bars), step, p.direction)


def _value(v: str, row: dict):
    try:
        return float(v)
    except ValueError:
        return row.get(v)


def _holds(cond: Condition, now: dict, prev: dict) -> bool:
    a, x = now.get(cond.field), _value(cond.value, now)
    if a is None or x is None:
        return False
    if cond.op == '>':
        return a > x
    if cond.op == '<':
        return a < x
    if cond.op == '>=':
        return a >= x
    if cond.op == '<=':
        return a <= x
    pa, px = prev.get(cond.field), _value(cond.value, prev)
    if pa is None or px is None:
        return False
    return (pa <= px and a > x) if cond.op == 'CROSSES_ABOVE' else (pa >= px and a < x)


def _text(conds: list[Condition]) -> str:
    return ' and '.join(f'{c.field} {c.op.lower().replace("_", " ")} {c.value}' for c in conds)


def rules(bars, rows, p: Rules):
    def step(i, state):
        now, prev = rows[i], rows[i - 1]
        if state == 'LONG' and p.long_exit and any(_holds(c, now, prev) for c in p.long_exit):
            return 'FLAT', 50, 'Long exit: ' + _text([c for c in p.long_exit if _holds(c, now, prev)])
        if state == 'SHORT' and p.short_exit and any(_holds(c, now, prev) for c in p.short_exit):
            return 'FLAT', 50, 'Short exit: ' + _text([c for c in p.short_exit if _holds(c, now, prev)])
        if p.long_entry and state != 'LONG' and all(_holds(c, now, prev) for c in p.long_entry):
            return 'LONG', 60, 'Long entry: ' + _text(p.long_entry)
        if p.short_entry and state != 'SHORT' and all(_holds(c, now, prev) for c in p.short_entry):
            return 'SHORT', 60, 'Short entry: ' + _text(p.short_entry)
        return None, 0, ''
    return _walk(len(bars), step, p.direction)


ALGORITHMS: dict[str, dict] = {
    'MA_CROSSOVER': {'label': 'Moving-average crossover (SMA)', 'model': MaCross, 'run': ma_cross},
    'EMA_CROSSOVER': {'label': 'EMA crossover', 'model': EmaCross, 'run': ema_cross},
    'RSI_REVERSION': {'label': 'RSI mean reversion', 'model': RsiReversion, 'run': rsi_reversion},
    'DONCHIAN_BREAKOUT': {'label': 'Donchian channel breakout', 'model': DonchianBreakout, 'run': donchian_breakout},
    'BOLLINGER_REVERSION': {'label': 'Bollinger band reversion', 'model': BollingerReversion, 'run': bollinger_reversion},
    'RULES': {'label': 'Rule builder (any feature)', 'model': Rules, 'run': rules},
    'SIGNAL_BLEND': {'label': 'Signal blend (Stage 3 signals combined)', 'model': SignalBlend, 'run': signal_blend},
}

# The decision rule of each algorithm, in words and symbols (shown on the Strategies workspace).
EQUATIONS = {
    'MA_CROSSOVER': ('LONG when SMA(fast)ₜ₋₁ ≤ SMA(slow)ₜ₋₁ and SMA(fast)ₜ > SMA(slow)ₜ; SHORT on the opposite cross',
                     'strength = clamp(100 × |SMA(fast) − SMA(slow)| ÷ ATR14, 10, 100)'),
    'EMA_CROSSOVER': ('LONG when EMA(fast)ₜ₋₁ ≤ EMA(slow)ₜ₋₁ and EMA(fast)ₜ > EMA(slow)ₜ; SHORT on the opposite cross. EMA(n)ₜ = EMA(n)ₜ₋₁ + 2÷(n+1)·(closeₜ − EMA(n)ₜ₋₁)',
                      'strength = clamp(100 × |EMA(fast) − EMA(slow)| ÷ ATR14, 10, 100)'),
    'RSI_REVERSION': ('LONG when RSIₜ₋₁ < lower ≤ RSIₜ; SHORT when RSIₜ₋₁ > upper ≥ RSIₜ; FLAT when RSI reaches exit_level. RSI = 100 − 100 ÷ (1 + avg gain ÷ avg loss), Wilder',
                      'strength = clamp(2 × (exit_level − min(RSIₜ₋₁, RSIₜ)), 10, 100) for longs'),
    'DONCHIAN_BREAKOUT': ('LONG when closeₜ > max(high of the previous entry bars); SHORT when closeₜ < min(low of the previous entry bars); '
                          'FLAT when the close crosses the exit channel the other way', 'strength = clamp(100 × (close − channel) ÷ ATR14, 10, 100)'),
    'BOLLINGER_REVERSION': ('LONG when closeₜ₋₁ < lowerₜ₋₁ and closeₜ ≥ lowerₜ; SHORT at the upper band; FLAT at the middle. Bands = SMA(n) ± k·σ(n)',
                            'strength = clamp(200 × |close − middle| ÷ (upper − lower), 10, 100)'),
    'RULES': ('LONG when every long-entry condition holds; FLAT when any long-exit condition holds (shorts the same)', 'strength = 60 on entry, 50 on exit'),
    'SIGNAL_BLEND': ('combined = Σ wᵢ · scoreᵢ ÷ Σ |wᵢ| (scores from the Stage 3 library); LONG when combined ≥ entry, SHORT when ≤ −entry; '
                     'FLAT when it falls back inside ±exit; reverse when it crosses the opposite entry', 'strength = clamp(|combined|, 10, 100)'),
}
for _k, (_rule, _strength_eq) in EQUATIONS.items():
    ALGORITHMS[_k]['equation'], ALGORITHMS[_k]['strength_equation'] = _rule, _strength_eq
for _k, _v in ALGORITHMS.items():
    _v['description'] = _v['model'].__doc__


def run(algorithm: str, parameters: dict, bars: list[dict], rows: list[dict]) -> list[dict]:
    spec = ALGORITHMS[algorithm]
    return spec['run'](bars, rows, spec['model'].model_validate(parameters))


def warmup(algorithm: str, parameters: dict) -> int:
    """Bars a decision needs before it means anything (the window must be longer)."""
    p = parameters
    return {'MA_CROSSOVER': p.get('slow_window', 50), 'EMA_CROSSOVER': 3 * p.get('slow', 21), 'RSI_REVERSION': 3 * p.get('period', 14),
            'DONCHIAN_BREAKOUT': p.get('entry', 20) + 1, 'BOLLINGER_REVERSION': p.get('period', 20) + 1,
            'SIGNAL_BLEND': 3 * max([max([v for v in (c.get('parameters') or {}).values() if isinstance(v, (int, float))] or [26])
                                     for c in p.get('components') or [{}]] or [26])}.get(algorithm, 210)
