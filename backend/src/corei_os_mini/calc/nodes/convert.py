"""Convert nodes: the explicit steps between kinds of value — a series to its value at the last bar, and an analytics
number (binary float) to an exact decimal for money, prices or quantities.

Why it exists: money must never be a binary float (guardrail 2). A graph can still compute a price or a size from
analytics, but only through To decimal, which names the kind, the decimal places and the rounding, and shows the
exact decimal it produced — so the conversion is a visible, reviewed step, never an accident of wiring.
Purpose: Last value (number, true/false, side), To decimal (money | price | quantity; places; HALF_EVEN, HALF_UP,
DOWN, UP).
Must never: convert implicitly; round without saying how.
Dependencies: decimal, calc.nodes. Consumers: calc.run.
Architectural position: the one gate from analytics to money inside graphs (Stages 4–5).
"""
from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_UP, Decimal

from .. import types as T
from . import Ctx, NodeType, Param, Port, register

ROUNDING = {'HALF_EVEN': ROUND_HALF_EVEN, 'HALF_UP': ROUND_HALF_UP, 'DOWN': ROUND_DOWN, 'UP': ROUND_UP}


def _last(type_in: str, type_out: str, label: str):
    def run(ctx: Ctx, ins: dict, p: dict) -> dict:
        x = ins['x']
        return {'out': x[-1] if x else None}
    register(NodeType(f'convert.last_{type_out}', 'convert', label, 'The value on the last bar (the bar being decided).', [Port('x', type_in)],
                      [Port('out', type_out)], [], run, lambda p: 'out = xₜ (last bar)'))


_last(T.SERIES_FLOAT, 'float', 'Last value (number)')
_last(T.SERIES_BOOL, 'bool', 'Last value (true/false)')
_last(T.SERIES_SIDE, 'side', 'Last value (side)')


def _dec_ports(p: dict):
    return [Port('x', T.FLOAT)], [Port('out', f"decimal:{p.get('kind') or 'price'}")]


def _to_decimal(ctx: Ctx, ins: dict, p: dict) -> dict:
    x = ins['x']
    if x is None:
        raise ValueError('To decimal: the number is missing on the last bar.')
    q = Decimal(1).scaleb(-p['places'])
    return {'out': Decimal(repr(float(x))).quantize(q, rounding=ROUNDING[p['rounding']])}


register(NodeType('convert.to_decimal', 'convert', 'To decimal', 'An analytics number made an exact decimal money, price or quantity: '
                  'rounded to the places given, with the rounding named. The only way a float reaches a decimal port.',
                  [Port('x', T.FLOAT)], [Port('out', T.PRICE)],
                  [Param('kind', 'choice', 'price', choices=('money', 'price', 'quantity')), Param('places', 'int', 5, 0, 12),
                   Param('rounding', 'choice', 'HALF_EVEN', choices=tuple(ROUNDING))],
                  _to_decimal, lambda p: f"out = round(x, {p.get('places')} places, {p.get('rounding')}) as an exact {p.get('kind')}",
                  ports=_dec_ports))
