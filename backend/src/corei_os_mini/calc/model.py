"""The graph model: a graph, its nodes and edges, its exposed parameters, its purpose and output contract, its limits.

Why it exists: one shape for every calculation in every stage, stored as JSON in a version, checked before it runs.
Purpose: Pydantic models `Graph`, `Node`, `Edge`, `GraphParam`; `PURPOSES` (each with the output node it must end in);
`LIMITS` (nodes, edges, depth, expression size) so a graph can never be large enough to hurt the engine.
Must never: hold executable code — a node is a catalogue type plus parameters; expressions are text parsed by calc.expr.
Dependencies: pydantic. Consumers: calc.run, calc.store, calc_api.
Architectural position: the data model of Calculation Graphs (DEC-93).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PURPOSES: dict[str, dict[str, str]] = {
    'FEATURE': {'output': 'output.feature', 'label': 'Feature', 'doc': 'A series of numbers per bar (Stage 2).'},
    'SIGNAL': {'output': 'output.signal', 'label': 'Signal', 'doc': 'A score from −100 (strong short) to +100 (strong long) per bar, with the inputs shown (Stage 3).'},
    'STRATEGY_RULE': {'output': 'output.rule', 'label': 'Strategy rule', 'doc': 'A target per bar — LONG, SHORT, FLAT or HOLD — with a strength and a reason (Stage 4).'},
    'SIZING': {'output': 'output.sizing', 'label': 'Sizing', 'doc': 'An order quantity (exact decimal) and how it was worked out (Stage 5).'},
    'PROTECTIVE': {'output': 'output.protective', 'label': 'Protective levels', 'doc': 'Stop and target prices (exact decimal) (Stage 4).'},
    'RISK_CHECK': {'output': 'output.risk_check', 'label': 'Risk check', 'doc': 'An extra check that can only refuse, with a reason; the built-in limits always run (Stage 5).'},
    'ALLOCATION': {'output': 'output.allocation', 'label': 'Allocation', 'doc': 'Weights that sum to 1 (Stage 6).'},
    'MEASURE': {'output': 'output.measure', 'label': 'Measure', 'doc': 'A named number for a portfolio, execution or post-trade view (Stages 6–8).'},
}
LIMITS = {'nodes': 200, 'edges': 400, 'depth': 50, 'expression_chars': 400, 'expression_tokens': 200, 'expression_depth': 40, 'bars': 20000}
ID = r'^[A-Za-z][A-Za-z0-9_]{0,39}$'


class Node(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=ID)
    type: str = Field(min_length=1, max_length=60)
    params: dict[str, Any] = Field(default_factory=dict)
    position: tuple[float, float] = (0.0, 0.0)
    label: str = Field('', max_length=80)
    note: str = Field('', max_length=500)


class Edge(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    source: str = Field(alias='from', min_length=3, max_length=81, description='node.port')
    target: str = Field(alias='to', min_length=3, max_length=81, description='node.port')


class GraphParam(BaseModel):
    """A knob the graph exposes (referenced in node parameters as "{{name}}"); changed per use without editing the graph."""
    model_config = ConfigDict(extra='forbid')
    name: str = Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')
    kind: Literal['int', 'float'] = 'float'
    default: float
    min: float | None = None
    max: float | None = None
    doc: str = Field('', max_length=300)


class Graph(BaseModel):
    model_config = ConfigDict(extra='forbid')
    purpose: Literal['FEATURE', 'SIGNAL', 'STRATEGY_RULE', 'SIZING', 'PROTECTIVE', 'RISK_CHECK', 'ALLOCATION', 'MEASURE']
    nodes: list[Node] = Field(default_factory=list, max_length=LIMITS['nodes'])
    edges: list[Edge] = Field(default_factory=list, max_length=LIMITS['edges'])
    parameters: list[GraphParam] = Field(default_factory=list, max_length=40)
    notes: str = Field('', max_length=4000)

    def param_defaults(self) -> dict[str, float]:
        return {p.name: (int(p.default) if p.kind == 'int' else float(p.default)) for p in self.parameters}
