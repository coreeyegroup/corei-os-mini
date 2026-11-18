"""Port types of Calculation Graphs and the rule for what may connect to what.

Why it exists: a graph is only safe if every connection is checked before it runs — a money value must never arrive
as a binary float, a boolean must never be added, a side must never be averaged.
Purpose: the port types, `compatible(src, dst)` (with the one allowed widening: a single value to a series of the
same kind, repeated on every bar), and `to_jsonable` for traces.
Must never: allow float → decimal (money, price, quantity) without the explicit, logged To-decimal node.
Dependencies: standard library. Consumers: calc.model, calc.nodes, calc.run.
Architectural position: the type layer under the executor.
"""
from __future__ import annotations

from decimal import Decimal

SERIES_FLOAT, SERIES_BOOL, SERIES_SIDE = 'series<float>', 'series<bool>', 'series<side>'
FLOAT, BOOL, SIDE, TEXT = 'float', 'bool', 'side', 'text'
MONEY, PRICE, QUANTITY = 'decimal:money', 'decimal:price', 'decimal:quantity'
TYPES = (SERIES_FLOAT, SERIES_BOOL, SERIES_SIDE, FLOAT, BOOL, SIDE, TEXT, MONEY, PRICE, QUANTITY)
SERIES = (SERIES_FLOAT, SERIES_BOOL, SERIES_SIDE)
DECIMALS = (MONEY, PRICE, QUANTITY)
SIDES = ('LONG', 'SHORT', 'FLAT', 'HOLD')
_WIDEN = {FLOAT: SERIES_FLOAT, BOOL: SERIES_BOOL, SIDE: SERIES_SIDE}


def compatible(src: str, dst: str) -> bool:
    """Same type, or one value widened to a series of the same kind. Nothing else — in particular never float to a
    decimal (money, price, quantity) and never between decimal kinds."""
    return src == dst or _WIDEN.get(src) == dst


def is_series(t: str) -> bool:
    return t in SERIES


def to_jsonable(v):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, float):
        return v if v == v and v not in (float('inf'), float('-inf')) else None
    if isinstance(v, (list, tuple)):
        return [to_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: to_jsonable(x) for k, x in v.items()}
    return v
