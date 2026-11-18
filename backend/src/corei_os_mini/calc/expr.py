"""The expression language of Calculation Graphs: our own small grammar, parsed by our own recursive-descent parser,
compiled into closures. Nothing is ever handed to eval, exec or the Python AST.

Why it exists: some steps are easiest to write as one line — "100 * (fast - slow) / (k * atr)" — and a desk must be
able to type that line without being able to run code. The grammar can only do arithmetic, comparisons, logic and a
short list of functions over the node's inputs; it cannot name an attribute, loop, define anything, hold a string or
reach anything outside the inputs it is given.
Purpose: `parse(text, names)` → a checked `Expression` (its inputs, its type, how far back it reads); `Expression.at(env, i)`
evaluates it on bar i; `FUNCTIONS` is the whitelist shown to the editor.
Grammar (lowest to highest precedence):
    expr    := or
    or      := and ('or' and)*
    and     := not ('and' not)*
    not     := 'not' not | cmp
    cmp     := sum (('<'|'<='|'>'|'>='|'=='|'!=') sum)?
    sum     := prod (('+'|'-') prod)*
    prod    := unary (('*'|'/') unary)*
    unary   := '-' unary | power
    power   := atom ('^' unary)?                      (right-associative)
    atom    := NUMBER | 'true' | 'false' | NAME | NAME '[' 't' ('-' INT)? ']' | FUNC '(' expr (',' expr)* ')' | '(' expr ')'
Indexing: x[t] is this bar, x[t-k] is k bars ago; x[t+k] (the future) is a parse error — look-ahead cannot be written.
Missing values: a missing input (None) makes the result missing; division by zero, sqrt of a negative and log of zero or
less give a missing value, never an exception.
Must never: call eval/exec/compile, import, read attributes, or accept a construct outside the grammar.
Dependencies: math. Consumers: calc.nodes.maths (Expression node), calc.nodes.logic.
Architectural position: the one place free text becomes a calculation, with LIMITS from calc.model.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Callable

from .model import LIMITS

FUNCTIONS: dict[str, dict] = {
    'abs': {'args': (1, 1), 'doc': 'abs(x) — absolute value'},
    'min': {'args': (2, 8), 'doc': 'min(a, b, …) — the smallest'},
    'max': {'args': (2, 8), 'doc': 'max(a, b, …) — the largest'},
    'clamp': {'args': (3, 3), 'doc': 'clamp(x, lo, hi) — x kept within lo…hi'},
    'sqrt': {'args': (1, 1), 'doc': 'sqrt(x) — square root (missing for x < 0)'},
    'log': {'args': (1, 1), 'doc': 'log(x) — natural logarithm (missing for x ≤ 0)'},
    'exp': {'args': (1, 1), 'doc': 'exp(x) — e to the power x'},
    'round': {'args': (1, 2), 'doc': 'round(x, n) — rounded to n decimals (half to even)'},
    'sign': {'args': (1, 1), 'doc': 'sign(x) — −1, 0 or +1'},
    'if': {'args': (3, 3), 'doc': 'if(condition, a, b) — a when the condition holds, otherwise b'},
    'coalesce': {'args': (2, 2), 'doc': 'coalesce(x, y) — x, or y when x is missing (warming up)'},
    'floor': {'args': (1, 1), 'doc': 'floor(x) — the whole number at or below x'},
    'ceil': {'args': (1, 1), 'doc': 'ceil(x) — the whole number at or above x'},
}
KEYWORDS = {'and', 'or', 'not', 'true', 'false', 't'}
_TOKEN = re.compile(r'\s*(?:(\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?|([A-Za-z_][A-Za-z0-9_]*)|(<=|>=|==|!=|[-+*/^()<>\[\],]))')


class ExprError(ValueError):
    """A parse or check error, with the character position."""

    def __init__(self, message: str, pos: int | None = None):
        super().__init__(message if pos is None else f'{message} (at character {pos + 1})')
        self.pos = pos


def tokenize(text: str) -> list[tuple[str, str, int]]:
    if len(text) > LIMITS['expression_chars']:
        raise ExprError(f"Expression is longer than {LIMITS['expression_chars']} characters.")
    out, pos = [], 0
    text = text.rstrip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise ExprError(f'Unexpected character {text[pos]!r}', pos)
        start = m.start(1) if m.group(1) else m.start(2) if m.group(2) else m.start(3)
        if m.group(1):
            out.append(('num', m.group(0).strip(), start))
        elif m.group(2):
            out.append(('name', m.group(2), start))
        else:
            out.append(('op', m.group(3), start))
        pos = m.end()
        if len(out) > LIMITS['expression_tokens']:
            raise ExprError(f"Expression has more than {LIMITS['expression_tokens']} parts.")
    out.append(('end', '', len(text)))
    return out


# Compiled node: (fn(env, i) -> value, kind 'num'|'bool', lookback)
Compiled = tuple[Callable, str, int]


def _arith(op: str, a: Callable, b: Callable) -> Callable:
    def run(env, i):
        x = a(env, i)
        if x is None:
            return None
        y = b(env, i)
        if y is None:
            return None
        if op == '+':
            return x + y
        if op == '-':
            return x - y
        if op == '*':
            return x * y
        if op == '/':
            return None if y == 0 else x / y
        try:  # '^'
            r = x ** y
        except (OverflowError, ZeroDivisionError):
            return None
        return None if isinstance(r, complex) else float(r)
    return run


_CMP = {'<': lambda x, y: x < y, '<=': lambda x, y: x <= y, '>': lambda x, y: x > y, '>=': lambda x, y: x >= y,
        '==': lambda x, y: x == y, '!=': lambda x, y: x != y}


def _safe1(fn):
    def call(x):
        try:
            return fn(x)
        except (ValueError, OverflowError):
            return None
    return call


_F1 = {'abs': abs, 'sqrt': _safe1(lambda x: math.sqrt(x) if x >= 0 else None),
       'log': _safe1(lambda x: math.log(x) if x > 0 else None), 'exp': _safe1(math.exp),
       'sign': lambda x: (x > 0) - (x < 0), 'floor': lambda x: float(math.floor(x)), 'ceil': lambda x: float(math.ceil(x))}


class _Parser:
    def __init__(self, text: str, names: dict[str, str]):
        self.toks = tokenize(text)
        self.k = 0
        self.names = names  # alias -> 'num' | 'bool'
        self.used: set[str] = set()
        self.depth = 0

    def peek(self, val: str | None = None, kind: str | None = None) -> bool:
        t = self.toks[self.k]
        return (val is None or t[1] == val) and (kind is None or t[0] == kind)

    def take(self, val: str | None = None, kind: str | None = None) -> tuple[str, str, int]:
        t = self.toks[self.k]
        if (val is not None and t[1] != val) or (kind is not None and t[0] != kind):
            want = val or kind
            raise ExprError(f"Expected {want!r} but found {t[1] or 'the end'!r}", t[2])
        self.k += 1
        return t

    def enter(self):
        self.depth += 1
        if self.depth > LIMITS['expression_depth']:
            raise ExprError(f"Expression is nested more than {LIMITS['expression_depth']} deep.")

    def parse(self) -> Compiled:
        c = self.or_()
        if not self.peek(kind='end'):
            t = self.toks[self.k]
            raise ExprError(f'Unexpected {t[1]!r}', t[2])
        return c

    def need(self, c: Compiled, kind: str, what: str, pos: int) -> Callable:
        if c[1] != kind:
            raise ExprError(f"{what} needs a {'number' if kind == 'num' else 'true/false value'}", pos)
        return c[0]

    def or_(self) -> Compiled:
        self.enter()
        pos = self.toks[self.k][2]
        left = self.and_()
        while self.peek('or', 'name'):
            p = self.take()[2]
            right = self.and_()
            a, b = self.need(left, 'bool', "'or'", pos), self.need(right, 'bool', "'or'", p)

            def run(env, i, a=a, b=b):
                x, y = a(env, i), b(env, i)
                if x is True or y is True:
                    return True
                return None if x is None or y is None else False
            left = (run, 'bool', max(left[2], right[2]))
        self.depth -= 1
        return left

    def and_(self) -> Compiled:
        pos = self.toks[self.k][2]
        left = self.not_()
        while self.peek('and', 'name'):
            p = self.take()[2]
            right = self.not_()
            a, b = self.need(left, 'bool', "'and'", pos), self.need(right, 'bool', "'and'", p)

            def run(env, i, a=a, b=b):
                x, y = a(env, i), b(env, i)
                if x is False or y is False:
                    return False
                return None if x is None or y is None else True
            left = (run, 'bool', max(left[2], right[2]))
        return left

    def not_(self) -> Compiled:
        if self.peek('not', 'name'):
            p = self.take()[2]
            self.enter()
            c = self.not_()
            self.depth -= 1
            a = self.need(c, 'bool', "'not'", p)
            return (lambda env, i: None if (x := a(env, i)) is None else not x), 'bool', c[2]
        return self.cmp()

    def cmp(self) -> Compiled:
        pos = self.toks[self.k][2]
        left = self.sum_()
        t = self.toks[self.k]
        if t[0] == 'op' and t[1] in _CMP:
            self.take()
            right = self.sum_()
            a, b, f = self.need(left, 'num', f"'{t[1]}'", pos), self.need(right, 'num', f"'{t[1]}'", t[2]), _CMP[t[1]]

            def run(env, i, a=a, b=b, f=f):
                x, y = a(env, i), b(env, i)
                return None if x is None or y is None else f(x, y)
            return run, 'bool', max(left[2], right[2])
        return left

    def sum_(self) -> Compiled:
        pos = self.toks[self.k][2]
        left = self.prod()
        while self.peek('+', 'op') or self.peek('-', 'op'):
            op = self.take()[1]
            p = self.toks[self.k - 1][2]
            right = self.prod()
            left = (_arith(op, self.need(left, 'num', f"'{op}'", pos), self.need(right, 'num', f"'{op}'", p)), 'num', max(left[2], right[2]))
        return left

    def prod(self) -> Compiled:
        pos = self.toks[self.k][2]
        left = self.unary()
        while self.peek('*', 'op') or self.peek('/', 'op'):
            op = self.take()[1]
            p = self.toks[self.k - 1][2]
            right = self.unary()
            left = (_arith(op, self.need(left, 'num', f"'{op}'", pos), self.need(right, 'num', f"'{op}'", p)), 'num', max(left[2], right[2]))
        return left

    def unary(self) -> Compiled:
        if self.peek('-', 'op'):
            p = self.take()[2]
            self.enter()
            c = self.unary()
            self.depth -= 1
            a = self.need(c, 'num', "'-'", p)
            return (lambda env, i: None if (x := a(env, i)) is None else -x), 'num', c[2]
        return self.power()

    def power(self) -> Compiled:
        pos = self.toks[self.k][2]
        base = self.atom()
        if self.peek('^', 'op'):
            p = self.take()[2]
            self.enter()
            exp = self.unary()
            self.depth -= 1
            return _arith('^', self.need(base, 'num', "'^'", pos), self.need(exp, 'num', "'^'", p)), 'num', max(base[2], exp[2])
        return base

    def atom(self) -> Compiled:
        t = self.toks[self.k]
        if t[0] == 'num':
            self.take()
            v = float(t[1])
            return (lambda env, i, v=v: v), 'num', 0
        if self.peek('(', 'op'):
            self.take()
            self.enter()
            c = self.or_()
            self.depth -= 1
            self.take(')')
            return c
        if t[0] != 'name':
            raise ExprError(f"Unexpected {t[1] or 'end of expression'!r}", t[2])
        name = t[1]
        self.take()
        if name in ('true', 'false'):
            v = name == 'true'
            return (lambda env, i, v=v: v), 'bool', 0
        if name in FUNCTIONS and self.peek('(', 'op'):
            return self.call(name, t[2])
        if name in KEYWORDS:
            raise ExprError(f"'{name}' cannot be used here", t[2])
        if name not in self.names:
            known = ', '.join(sorted(self.names)) or 'none'
            raise ExprError(f"Unknown name '{name}' — the inputs are: {known}", t[2])
        self.used.add(name)
        back = 0
        if self.peek('[', 'op'):
            self.take()
            self.take('t', 'name')
            if self.peek('+', 'op'):
                raise ExprError(f"'{name}[t+…]' would read a future bar — only t or t-k is allowed", self.toks[self.k][2])
            if self.peek('-', 'op'):
                self.take()
                k = self.take(kind='num')
                if not re.fullmatch(r'\d+', k[1]) or int(k[1]) > LIMITS['bars']:
                    raise ExprError('The bars-ago in [t-k] must be a whole number', k[2])
                back = int(k[1])
            self.take(']')
        kind = self.names[name]

        def run(env, i, name=name, back=back):
            j = i - back
            if j < 0:
                return None
            return env[name][j]
        return run, kind, back

    def call(self, name: str, pos: int) -> Compiled:
        self.take('(')
        self.enter()
        args = [self.or_()]
        while self.peek(',', 'op'):
            self.take()
            args.append(self.or_())
        self.take(')')
        self.depth -= 1
        lo, hi = FUNCTIONS[name]['args']
        if not lo <= len(args) <= hi:
            raise ExprError(f"{name}() takes {lo if lo == hi else f'{lo} to {hi}'} values, not {len(args)}", pos)
        back = max(a[2] for a in args)
        if name == 'coalesce':
            if args[0][1] != args[1][1]:
                raise ExprError('coalesce(x, y): x and y must be of the same kind', pos)
            a, b = args[0][0], args[1][0]

            def run(env, i):
                v = a(env, i)
                return b(env, i) if v is None else v
            return run, args[0][1], back
        if name == 'if':
            cond = self.need(args[0], 'bool', 'if()', pos)
            if args[1][1] != args[2][1]:
                raise ExprError('if(condition, a, b): a and b must both be numbers or both true/false', pos)
            a, b = args[1][0], args[2][0]

            def run(env, i):
                c = cond(env, i)
                return None if c is None else (a(env, i) if c else b(env, i))
            return run, args[1][1], back
        fs = [self.need(a, 'num', f'{name}()', pos) for a in args]
        if name in _F1:
            f, g = _F1[name], fs[0]
            return (lambda env, i: None if (x := g(env, i)) is None else f(x)), 'num', back
        if name in ('min', 'max'):
            agg = min if name == 'min' else max

            def run(env, i):
                vs = [f(env, i) for f in fs]
                return None if any(v is None for v in vs) else agg(vs)
            return run, 'num', back
        if name == 'clamp':
            def run(env, i):
                x, lo_, hi_ = (f(env, i) for f in fs)
                return None if x is None or lo_ is None or hi_ is None else max(lo_, min(hi_, x))
            return run, 'num', back
        # round
        def run(env, i):
            vs = [f(env, i) for f in fs]
            if any(v is None for v in vs):
                return None
            return round(vs[0], int(vs[1])) if len(vs) == 2 else float(round(vs[0]))
        return run, 'num', back


@dataclass
class Expression:
    text: str
    kind: str                       # 'num' | 'bool'
    lookback: int                   # the furthest [t-k]
    used: set[str] = field(default_factory=set)
    _fn: Callable | None = None

    def at(self, env: dict[str, list], i: int):
        v = self._fn(env, i)
        if isinstance(v, float) and (v != v or v in (math.inf, -math.inf)):
            return None
        return v

    def series(self, env: dict[str, list], n: int) -> list:
        return [self.at(env, i) for i in range(n)]


def parse(text: str, names: dict[str, str]) -> Expression:
    """Parse and check `text` against the available input names (alias → 'num' | 'bool')."""
    if not isinstance(text, str) or not text.strip():
        raise ExprError('The expression is empty.')
    p = _Parser(text, names)
    fn, kind, back = p.parse()
    return Expression(text=text.strip(), kind=kind, lookback=back, used=p.used, _fn=fn)
