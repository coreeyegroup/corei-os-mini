"""Maths nodes: arithmetic, the common functions, and the Expression node (one line of our own safe grammar).

Why it exists: a formula is mostly arithmetic; a node per operation keeps each step visible with its numbers, and the
Expression node lets a desk write a line such as 100 * (fast - slow) / (k * atr) without being able to run code.
Purpose: Add, Subtract, Multiply, Divide, Function (abs, sqrt, log, exp, negate, sign), Min / max, Clamp, Round,
Expression (inputs named by the author, output a number or true/false, parsed by calc.expr).
A missing input gives a missing output; division by zero, sqrt of a negative and log of zero or less give a missing
value, never an exception or an infinity.
Must never: evaluate text any other way than calc.expr; handle money (these are analytics floats — money uses the
decimal nodes after an explicit To-decimal conversion).
Dependencies: calc.expr, calc.nodes. Consumers: calc.run.
Architectural position: arithmetic inside graphs.
"""
from __future__ import annotations

import math
import re

from .. import expr as E
from .. import types as T
from . import Ctx, NodeType, Param, Port, register

S = T.SERIES_FLOAT


def _binary(type_: str, label: str, sym: str, f):
    def run(ctx: Ctx, ins: dict, p: dict) -> dict:
        return {'out': [None if a is None or b is None else f(a, b) for a, b in zip(ins['a'], ins['b'])]}
    register(NodeType(type_, 'maths', label, f'a {sym} b on every bar.', [Port('a', S), Port('b', S)], [Port('out', S)], [], run,
                      lambda p: f'out = a {sym} b'))


_binary('math.add', 'Add', '+', lambda a, b: a + b)
_binary('math.sub', 'Subtract', '−', lambda a, b: a - b)
_binary('math.mul', 'Multiply', '×', lambda a, b: a * b)
_binary('math.div', 'Divide', '÷', lambda a, b: None if b == 0 else a / b)


def _safe(f):
    def g(x):
        try:
            v = f(x)
        except (ValueError, OverflowError):
            return None
        return None if v is None or (isinstance(v, float) and (v != v or math.isinf(v))) else v
    return g


FUNCS = {'abs': abs, 'sqrt': _safe(lambda x: math.sqrt(x) if x >= 0 else None), 'log': _safe(lambda x: math.log(x) if x > 0 else None),
         'exp': _safe(math.exp), 'negate': lambda x: -x, 'sign': lambda x: float((x > 0) - (x < 0))}


def _func(ctx: Ctx, ins: dict, p: dict) -> dict:
    f = FUNCS[p['fn']]
    return {'out': [None if x is None else f(x) for x in ins['x']]}


register(NodeType('math.func', 'maths', 'Function', 'abs, sqrt, log (natural), exp, negate or sign of x on every bar.',
                  [Port('x', S)], [Port('out', S)], [Param('fn', 'choice', 'abs', choices=tuple(FUNCS))], _func, lambda p: f"out = {p.get('fn')}(x)"))


def _minmax(ctx: Ctx, ins: dict, p: dict) -> dict:
    f = min if p['fn'] == 'min' else max
    return {'out': [None if a is None or b is None else f(a, b) for a, b in zip(ins['a'], ins['b'])]}


register(NodeType('math.minmax', 'maths', 'Min / max', 'The smaller or larger of a and b.', [Port('a', S), Port('b', S)], [Port('out', S)],
                  [Param('fn', 'choice', 'min', choices=('min', 'max'))], _minmax, lambda p: f"out = {p.get('fn')}(a, b)"))


def _clamp_check(p):
    if p['lo'] > p['hi']:
        raise ValueError('lo must not be above hi')


register(NodeType('math.clamp', 'maths', 'Clamp', 'x kept within lo…hi (a score is clamped to −100…+100).', [Port('x', S)], [Port('out', S)],
                  [Param('lo', 'float', -100.0), Param('hi', 'float', 100.0)],
                  lambda c, i, p: {'out': [None if x is None else max(p['lo'], min(p['hi'], x)) for x in i['x']]},
                  lambda p: f"out = max({p.get('lo')}, min({p.get('hi')}, x))", check=_clamp_check))
register(NodeType('math.round', 'maths', 'Round', 'x rounded to a number of decimals (half to even, as Python and the platform round).',
                  [Port('x', S)], [Port('out', S)], [Param('places', 'int', 2, 0, 12)],
                  lambda c, i, p: {'out': [None if x is None else round(x, p['places']) for x in i['x']]},
                  lambda p: f"out = round(x, {p.get('places')})"))

# ---------- Expression ----------
_NAME = re.compile(r'^[a-z][a-z0-9_]{0,23}$')


def _names(p: dict) -> list[str]:
    raw = [n.strip() for n in (p.get('inputs') or '').split(',') if n.strip()]
    return raw


def _expr_check(p: dict) -> None:
    names = _names(p)
    if not 1 <= len(names) <= 8:
        raise ValueError('an expression has 1 to 8 named inputs, e.g. "fast, slow, atr"')
    for n in names:
        if not _NAME.match(n) or n in E.KEYWORDS or n in E.FUNCTIONS:
            raise ValueError(f"input name '{n}' must be lower-case letters, digits or _, start with a letter, and not be a word of the language")
    if len(set(names)) != len(names):
        raise ValueError('input names must be different')
    try:
        parsed = E.parse(p['expression'], {n: 'num' for n in names})
    except E.ExprError as exc:
        raise ValueError(f'expression: {exc}') from None
    want = 'bool' if p['output'] == 'true/false' else 'num'
    if parsed.kind != want:
        raise ValueError(f"the expression gives {'true/false' if parsed.kind == 'bool' else 'a number'} but the output is set to {p['output']}")


def _expr_ports(p: dict):
    names = _names(p) or ['a']
    out = T.SERIES_BOOL if p.get('output') == 'true/false' else S
    return [Port(n, S) for n in names], [Port('out', out)]


def _expr_run(ctx: Ctx, ins: dict, p: dict) -> dict:
    names = _names(p)
    parsed = E.parse(p['expression'], {n: 'num' for n in names})
    env = {n: ins[n] for n in names}
    return {'out': parsed.series(env, ctx.n)}


def _expr_lookback(p: dict) -> int:
    try:
        return E.parse(p['expression'], {n: 'num' for n in _names(p)}).lookback
    except E.ExprError:
        return 0


def _expr_explain(p: dict, ins: dict, outs: dict) -> str:
    text = p['expression']
    for n in sorted(_names(p), key=len, reverse=True):
        v = ins.get(n)
        text = re.sub(rf'\b{n}\b(?!\[)', '—' if v is None else (f'{v:.6g}' if isinstance(v, float) else str(v)), text)
    o = outs.get('out')
    return f"{p['expression']}  =  {text}  =  {'—' if o is None else (f'{o:.6g}' if isinstance(o, float) else o)}"


register(NodeType('math.expression', 'maths', 'Expression',
                  'One line of the formula language over named inputs: + − × ÷ ^, comparisons, and/or/not, x[t-k] for k bars ago, and '
                  + ', '.join(E.FUNCTIONS) + '. No other names, no code.',
                  [Port('a', S)], [Port('out', S)],
                  [Param('inputs', 'text', 'a', doc='the input names, comma-separated, e.g. "fast, slow, atr"'),
                   Param('expression', 'expr', 'a', doc='e.g. 100 * (fast - slow) / atr'),
                   Param('output', 'choice', 'number', choices=('number', 'true/false'))],
                  _expr_run, lambda p: f"out = {p.get('expression')}", _expr_lookback, _expr_explain, _expr_ports, _expr_check))
