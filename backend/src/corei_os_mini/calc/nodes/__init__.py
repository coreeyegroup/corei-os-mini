"""The node catalogue of Calculation Graphs: what a node is (ports, parameters, a pure run, a formula, an explanation,
a look-back) and the registry every family adds itself to.

Why it exists: a graph is only as safe as its nodes. Every node is our own small, pure function with typed ports and
checked parameters, written once and tested once — a graph can only combine them, never add code.
Purpose: `Port`, `Param`, `NodeType`, `register`, `get`, `catalogue()` (what /api/calc/nodes serves and the editor's
palette shows), `check_params` (types, bounds, choices — every refusal names the node and the parameter).
Values a node sees: a series is a list aligned to the bars (None while warming up or where the value is missing); a
single value is a float, bool, side, text or Decimal. A node's `run(ctx, inputs, params)` must only use bars 0…i to
produce position i (tested for every node by truncating the bars: calc tests, "point in time").
Must never: read the clock, the network, files or the database; mutate its inputs; keep state between runs.
Dependencies: calc.types. Consumers: calc.run, calc_api, the families in this package.
Architectural position: the catalogue under the executor (DEC-93).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from .. import types as T

FAMILIES = {
    'sources': 'Sources — bars, features, signal scores, quote, position, account, instrument, settings, constants',
    'indicators': 'Indicators — the platform indicators (intel.indicators), wrapped, never re-implemented',
    'series': 'Series — shift, rolling windows, differences, returns, cumulative, normalise by ATR',
    'maths': 'Maths — arithmetic, functions and the Expression node',
    'logic': 'Logic — compare, crosses, and/or/not, if, between, sides',
    'combine': 'Combine — weighted sum, vote, consensus, rank',
    'convert': 'Convert — the explicit, logged conversions (float → decimal)',
    'outputs': 'Outputs — the contract of each graph purpose',
}


@dataclass(frozen=True)
class Port:
    name: str
    type: str
    doc: str = ''
    optional: bool = False

    def as_dict(self) -> dict:
        return {'name': self.name, 'type': self.type, 'doc': self.doc, 'optional': self.optional}


@dataclass(frozen=True)
class Param:
    name: str
    kind: str                  # int | float | choice | text | expr | bool | decimal
    default: Any = None
    min: float | None = None
    max: float | None = None
    choices: tuple = ()
    doc: str = ''

    def as_dict(self) -> dict:
        d = {'name': self.name, 'kind': self.kind, 'default': self.default, 'doc': self.doc}
        if self.min is not None:
            d['min'] = self.min
        if self.max is not None:
            d['max'] = self.max
        if self.choices:
            d['choices'] = list(self.choices)
        return d


@dataclass
class Ctx:
    """What a run may read: the bars (oldest first, closed bars only), the timeframe, and the single values the caller
    supplied (quote, position, account, instrument, settings). Nothing else — no clock, no database."""
    bars: list[dict]
    timeframe: str = 'H1'
    scalars: dict[str, dict] = field(default_factory=dict)
    cache: dict = field(default_factory=dict)        # shared work within one run (features, library scores)

    @property
    def n(self) -> int:
        return len(self.bars)


@dataclass
class NodeType:
    type: str
    family: str
    label: str
    doc: str
    inputs: list[Port]
    outputs: list[Port]
    params: list[Param]
    run: Callable[[Ctx, dict, dict], dict]
    formula: Callable[[dict], str]
    lookback: Callable[[dict], int] = lambda p: 0
    explain: Callable[[dict, dict, dict], str] | None = None
    ports: Callable[[dict], tuple[list[Port], list[Port]]] | None = None   # ports that depend on the parameters
    check: Callable[[dict], None] | None = None                            # extra parameter rules (raise ValueError)
    purpose: str | None = None                                             # output nodes: the purpose they finish

    def io(self, params: dict) -> tuple[list[Port], list[Port]]:
        return self.ports(params) if self.ports else (self.inputs, self.outputs)

    def as_dict(self) -> dict:
        d = {'type': self.type, 'family': self.family, 'label': self.label, 'doc': self.doc,
             'inputs': [p.as_dict() for p in self.inputs], 'outputs': [p.as_dict() for p in self.outputs],
             'params': [p.as_dict() for p in self.params], 'dynamic_ports': self.ports is not None,
             'formula': self.formula({p.name: p.default for p in self.params})}
        if self.purpose:
            d['purpose'] = self.purpose
        return d


REGISTRY: dict[str, NodeType] = {}


def register(node: NodeType) -> NodeType:
    if node.type in REGISTRY:
        raise RuntimeError(f'Node type {node.type} registered twice')
    if node.family not in FAMILIES:
        raise RuntimeError(f'Node type {node.type}: unknown family {node.family}')
    for p in [*node.inputs, *node.outputs]:
        if p.type not in T.TYPES:
            raise RuntimeError(f'Node type {node.type}: port {p.name} has unknown type {p.type}')
    REGISTRY[node.type] = node
    return node


def get(type_: str) -> NodeType | None:
    _load()
    return REGISTRY.get(type_)


def catalogue() -> list[dict]:
    _load()
    return [n.as_dict() for n in REGISTRY.values()]


def check_params(node_id: str, nt: NodeType, raw: dict) -> dict:
    """Parameters with defaults filled in and every value checked. Raises ValueError naming the node and parameter."""
    known = {p.name: p for p in nt.params}
    extra = set(raw) - set(known)
    if extra:
        raise ValueError(f"Node {node_id} ({nt.label}) has no parameter {', '.join(sorted(extra))}; it has {', '.join(known) or 'none'}.")
    out = {}
    for name, p in known.items():
        v = raw.get(name, p.default)
        where = f'Node {node_id} ({nt.label}), parameter {name}'
        if v is None:
            if p.default is None and p.kind not in ('text',):
                raise ValueError(f'{where} is required.')
            out[name] = v
            continue
        try:
            if p.kind == 'int':
                if isinstance(v, bool) or float(v) != int(float(v)):
                    raise ValueError
                v = int(float(v))
            elif p.kind == 'float':
                if isinstance(v, bool):
                    raise ValueError
                v = float(v)
                if v != v or v in (float('inf'), float('-inf')):
                    raise ValueError
            elif p.kind == 'bool':
                if not isinstance(v, bool):
                    raise ValueError
            elif p.kind == 'decimal':
                v = Decimal(str(v))
                if not v.is_finite():
                    raise ValueError
            elif p.kind in ('text', 'expr', 'choice'):
                if not isinstance(v, str) or len(v) > 400:
                    raise ValueError
        except (ValueError, TypeError, InvalidOperation):
            raise ValueError(f'{where} must be {"a whole number" if p.kind == "int" else "a number" if p.kind in ("float", "decimal") else "true or false" if p.kind == "bool" else "text"} — got {raw.get(name)!r}.') from None
        if p.kind == 'choice' and v not in p.choices:
            raise ValueError(f"{where} must be one of {', '.join(map(str, p.choices))} — got {v!r}.")
        if p.min is not None and isinstance(v, (int, float, Decimal)) and v < p.min:
            raise ValueError(f'{where} must be at least {p.min:g} — got {v}.')
        if p.max is not None and isinstance(v, (int, float, Decimal)) and v > p.max:
            raise ValueError(f'{where} must be at most {p.max:g} — got {v}.')
        out[name] = v
    if nt.check:
        try:
            nt.check(out)
        except ValueError as exc:
            raise ValueError(f'Node {node_id} ({nt.label}): {exc}') from None
    return out


def fmt(v) -> str:
    """A value as shown in formulas and explanations."""
    if v is None:
        return '—'
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, float):
        return f'{v:.6g}'
    return str(v)


_loaded = False


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    from . import sources, indicators, series, maths, logic, combine, convert, outputs  # noqa: F401  (each registers itself)
