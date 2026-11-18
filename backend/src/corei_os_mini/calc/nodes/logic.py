"""Logic nodes: comparisons, crossings, and/or/not, if-then-else, between, and turning a score into a side.

Why it exists: strategy rules and filters are yes/no decisions made from numbers; these nodes make each decision
visible with the values that drove it on the bar being looked at.
Purpose: Compare, Crosses (above, below, either), And, Or, Not, If (number), Between, Side from score (threshold, as
the signal library's direction), Side with hysteresis (enter at one level, leave at another), Latch (the strategy
algorithms' target state machine: go LONG on one event, SHORT on another, hold otherwise; long-only / short-only).
Missing values: a comparison with a missing value is missing; And is false when either side is false, Or is true when
either side is true (otherwise missing) — the same three-valued logic as calc.expr.
Must never: read a later bar (Crosses reads t and t−1; hysteresis carries state forward only).
Dependencies: calc.nodes. Consumers: calc.run.
Architectural position: decisions inside graphs (Stages 3–5).
"""
from __future__ import annotations

from .. import types as T
from . import Ctx, NodeType, Param, Port, register

S, B, SD = T.SERIES_FLOAT, T.SERIES_BOOL, T.SERIES_SIDE
OPS = {'<': lambda a, b: a < b, '<=': lambda a, b: a <= b, '>': lambda a, b: a > b, '>=': lambda a, b: a >= b,
       '==': lambda a, b: a == b, '!=': lambda a, b: a != b}


def _compare(ctx: Ctx, ins: dict, p: dict) -> dict:
    f = OPS[p['op']]
    return {'out': [None if a is None or b is None else f(a, b) for a, b in zip(ins['a'], ins['b'])]}


register(NodeType('logic.compare', 'logic', 'Compare', 'a compared with b on every bar.', [Port('a', S), Port('b', S)], [Port('out', B)],
                  [Param('op', 'choice', '>', choices=tuple(OPS))], _compare, lambda p: f"out = a {p.get('op')} b"))


def _crosses(ctx: Ctx, ins: dict, p: dict) -> dict:
    a, b, d = ins['a'], ins['b'], p['direction']
    out = [None]
    for i in range(1, len(a)):
        if None in (a[i], b[i], a[i - 1], b[i - 1]):
            out.append(None)
            continue
        up = a[i - 1] <= b[i - 1] and a[i] > b[i]
        dn = a[i - 1] >= b[i - 1] and a[i] < b[i]
        out.append(up if d == 'above' else dn if d == 'below' else up or dn)
    return {'out': out[:len(a)]}


register(NodeType('logic.crosses', 'logic', 'Crosses', 'a crosses b on this bar (it was on the other side, or level, on the bar before).',
                  [Port('a', S), Port('b', S)], [Port('out', B)], [Param('direction', 'choice', 'above', choices=('above', 'below', 'either'))],
                  _crosses, lambda p: {'above': 'aₜ₋₁ ≤ bₜ₋₁ and aₜ > bₜ', 'below': 'aₜ₋₁ ≥ bₜ₋₁ and aₜ < bₜ'}.get(p.get('direction'), 'crosses above or below'),
                  lambda p: 1))


def _and(x, y):
    if x is False or y is False:
        return False
    return None if x is None or y is None else True


def _or(x, y):
    if x is True or y is True:
        return True
    return None if x is None or y is None else False


register(NodeType('logic.and', 'logic', 'And', 'True when both are true.', [Port('a', B), Port('b', B)], [Port('out', B)], [],
                  lambda c, i, p: {'out': [_and(x, y) for x, y in zip(i['a'], i['b'])]}, lambda p: 'out = a and b'))
register(NodeType('logic.or', 'logic', 'Or', 'True when either is true.', [Port('a', B), Port('b', B)], [Port('out', B)], [],
                  lambda c, i, p: {'out': [_or(x, y) for x, y in zip(i['a'], i['b'])]}, lambda p: 'out = a or b'))
register(NodeType('logic.not', 'logic', 'Not', 'The opposite.', [Port('a', B)], [Port('out', B)], [],
                  lambda c, i, p: {'out': [None if x is None else not x for x in i['a']]}, lambda p: 'out = not a'))
register(NodeType('logic.if', 'logic', 'If', 'then when the condition holds, otherwise else.', [Port('cond', B), Port('then', S), Port('else', S)],
                  [Port('out', S)], [],
                  lambda c, i, p: {'out': [None if k is None else (t if k else e) for k, t, e in zip(i['cond'], i['then'], i['else'])]},
                  lambda p: 'out = if(cond, then, else)'))


def _between(ctx: Ctx, ins: dict, p: dict) -> dict:
    inc = p['inclusive']
    out = []
    for x, lo, hi in zip(ins['x'], ins['lo'], ins['hi']):
        out.append(None if None in (x, lo, hi) else (lo <= x <= hi if inc else lo < x < hi))
    return {'out': out}


register(NodeType('logic.between', 'logic', 'Between', 'lo ≤ x ≤ hi (or strictly inside).', [Port('x', S), Port('lo', S), Port('hi', S)], [Port('out', B)],
                  [Param('inclusive', 'bool', True)], _between, lambda p: 'out = lo ≤ x ≤ hi' if p.get('inclusive', True) else 'out = lo < x < hi'))


def _side_from(ctx: Ctx, ins: dict, p: dict) -> dict:
    t = p['threshold']
    return {'out': ['FLAT' if s is None or abs(s) < t else ('LONG' if s > 0 else 'SHORT') for s in ins['score']]}


register(NodeType('logic.side_from', 'logic', 'Side from score', 'LONG at or above +threshold, SHORT at or below −threshold, otherwise FLAT '
                  '(the signal library\'s direction).', [Port('score', S)], [Port('out', SD)],
                  [Param('threshold', 'float', 20.0, 0, 100)], _side_from, lambda p: f"LONG if score ≥ {p.get('threshold')}, SHORT if ≤ −{p.get('threshold')}, else FLAT"))


def _hyst_check(p):
    if p['exit'] > p['entry']:
        raise ValueError('exit must not be above entry')


def _hysteresis(ctx: Ctx, ins: dict, p: dict) -> dict:
    entry, exit_ = p['entry'], p['exit']
    side, out = 'FLAT', []
    for s in ins['score']:
        if s is not None:
            if side == 'LONG' and s <= exit_:
                side = 'FLAT'
            elif side == 'SHORT' and s >= -exit_:
                side = 'FLAT'
            if side == 'FLAT':
                side = 'LONG' if s >= entry else 'SHORT' if s <= -entry else 'FLAT'
            elif side == 'LONG' and s <= -entry:
                side = 'SHORT'
            elif side == 'SHORT' and s >= entry:
                side = 'LONG'
        out.append(side)
    return {'out': out}


register(NodeType('logic.hysteresis', 'logic', 'Side with hysteresis', 'Enter LONG at +entry or SHORT at −entry; leave when the score comes back '
                  'to ±exit. Holds the side in between, so a score near the line does not flip every bar.', [Port('score', S)], [Port('out', SD)],
                  [Param('entry', 'float', 30.0, 0, 100), Param('exit', 'float', 5.0, -100, 100)], _hysteresis,
                  lambda p: f"enter at ±{p.get('entry')}, leave at ±{p.get('exit')}", check=_hyst_check))


def _latch(ctx: Ctx, ins: dict, p: dict) -> dict:
    """The target state machine of trading.algorithms._walk: bar 0 never changes; a LONG event in SHORT_ONLY (or a SHORT
    event in LONG_ONLY) only closes the opposite position."""
    lg, sh, d = ins['long'], ins['short'], p['direction']
    state, target, changed = 'FLAT', [], []
    for i in range(ctx.n):
        new = None
        if i:
            if lg[i] is True:
                new = 'LONG'
            elif sh[i] is True:
                new = 'SHORT'
        if new == 'LONG' and d == 'SHORT_ONLY':
            new = 'FLAT' if state == 'SHORT' else None
        if new == 'SHORT' and d == 'LONG_ONLY':
            new = 'FLAT' if state == 'LONG' else None
        ch = new is not None and new != state
        if ch:
            state = new
        target.append(state)
        changed.append(ch)
    return {'target': target, 'changed': changed}


register(NodeType('logic.latch', 'logic', 'Latch (target)', 'Go LONG when "long" is true, SHORT when "short" is true, otherwise hold the last target '
                  '(the strategy algorithms\' state machine). Long only: a short event only closes a long; short only: the reverse.',
                  [Port('long', B), Port('short', B)], [Port('target', SD, 'LONG, SHORT or FLAT, held'), Port('changed', B, 'true on the bar the target changes')],
                  [Param('direction', 'choice', 'BOTH', choices=('BOTH', 'LONG_ONLY', 'SHORT_ONLY'))], _latch,
                  lambda p: f"target ← LONG on long, SHORT on short, else held ({p.get('direction', 'BOTH').lower().replace('_', ' ')})"))
