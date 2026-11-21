"""Portable, read-only strategy workflow compiler (v1).

Draft validation only. It never executes arbitrary code, creates orders, or
changes approved strategy versions. Existing GRAPH runtime remains authoritative.
"""
from __future__ import annotations

from collections import deque
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NodeType = Literal['input.bars', 'indicator.ema', 'indicator.rsi', 'indicator.atr',
                   'condition.compare', 'logic.and', 'logic.or', 'signal.direction',
                   'plan.atr_stop', 'plan.r_multiple']
PORTS = {
    'input.bars': ({}, {'close': 'series', 'high': 'series', 'low': 'series'}),
    'indicator.ema': ({'source': 'series'}, {'value': 'series'}),
    'indicator.rsi': ({'source': 'series'}, {'value': 'series'}),
    'indicator.atr': ({'high': 'series', 'low': 'series', 'close': 'series'}, {'value': 'series'}),
    'condition.compare': ({'left': 'series', 'right': 'series'}, {'result': 'boolean'}),
    'logic.and': ({'a': 'boolean', 'b': 'boolean'}, {'result': 'boolean'}),
    'logic.or': ({'a': 'boolean', 'b': 'boolean'}, {'result': 'boolean'}),
    'signal.direction': ({'long': 'boolean', 'short': 'boolean'}, {'target': 'signal'}),
    'plan.atr_stop': ({'target': 'signal', 'atr': 'series'}, {'plan': 'plan'}),
    'plan.r_multiple': ({'plan': 'plan'}, {'plan': 'plan'}),
}
PARAMS = {
    'indicator.ema': {'period': (2, 1000)},
    'indicator.rsi': {'period': (2, 200)},
    'indicator.atr': {'period': (2, 200)},
    'condition.compare': {'operator': ('>', '<', '>=', '<=', 'CROSSES_ABOVE', 'CROSSES_BELOW')},
    'plan.atr_stop': {'multiple': (0.1, 20)},
    'plan.r_multiple': {'reward_r': (0.1, 20)},
}


class Node(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_-]{0,63}$')
    type: NodeType
    params: dict[str, int | float | str] = Field(default_factory=dict)


class Edge(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source: str
    source_port: str
    target: str
    target_port: str


class Workflow(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['1.0'] = '1.0'
    name: str = Field(min_length=1, max_length=120)
    nodes: list[Node] = Field(min_length=1, max_length=256)
    edges: list[Edge] = Field(default_factory=list, max_length=1024)

    @model_validator(mode='after')
    def unique_ids(self):
        ids = [node.id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate node IDs')
        return self


def compile_workflow(workflow: Workflow) -> dict:
    """Validate ports, types, graph topology and required parameters.

    Return a deterministic topological plan, not an executable trading strategy.
    """
    nodes = {n.id: n for n in workflow.nodes}
    incoming = {n.id: set() for n in workflow.nodes}
    outgoing = {n.id: set() for n in workflow.nodes}
    connected = set()
    for n in workflow.nodes:
        rules = PARAMS.get(n.type, {})
        unknown = set(n.params) - set(rules)
        if unknown:
            raise ValueError(f'{n.id}: unknown parameters: {sorted(unknown)}')
        for key, rule in rules.items():
            if key not in n.params:
                raise ValueError(f'{n.id}: missing parameter {key}')
            value = n.params[key]
            if isinstance(rule[0], str):
                if value not in rule:
                    raise ValueError(f'{n.id}: invalid {key}')
            elif isinstance(value, bool) or not isinstance(value, (int, float)) or not rule[0] <= value <= rule[1]:
                raise ValueError(f'{n.id}: {key} outside supported range')
            elif key == 'period' and (not isinstance(value, int) or isinstance(value, bool)):
                raise ValueError(f'{n.id}: period must be an integer')
    for e in workflow.edges:
        if e.source not in nodes or e.target not in nodes:
            raise ValueError('Edge references unknown node')
        if e.source == e.target:
            raise ValueError('Self-referential edge')
        source_outputs = PORTS[nodes[e.source].type][1]
        target_inputs = PORTS[nodes[e.target].type][0]
        if e.source_port not in source_outputs or e.target_port not in target_inputs:
            raise ValueError('Edge references unknown port')
        if source_outputs[e.source_port] != target_inputs[e.target_port]:
            raise ValueError('Incompatible port types')
        key = (e.target, e.target_port)
        if key in connected:
            raise ValueError('Multiple connections to one input port')
        connected.add(key)
        incoming[e.target].add(e.source)
        outgoing[e.source].add(e.target)
    for n in workflow.nodes:
        for port in PORTS[n.type][0]:
            if (n.id, port) not in connected:
                raise ValueError(f'{n.id}: required input {port} not connected')
    queue = deque(sorted(k for k, v in incoming.items() if not v))
    ordered = []
    while queue:
        node_id = queue.popleft()
        ordered.append(node_id)
        for nxt in sorted(outgoing[node_id]):
            incoming[nxt].discard(node_id)
            if not incoming[nxt]:
                queue.append(nxt)
    if len(ordered) != len(nodes):
        raise ValueError('Workflow contains a cycle')
    if not any(n.type == 'signal.direction' for n in workflow.nodes):
        raise ValueError('Workflow requires a signal.direction node')
    return {'valid': True, 'schema_version': workflow.schema_version,
            'execution_order': ordered, 'node_count': len(nodes),
            'edge_count': len(workflow.edges),
            'scope': 'DRAFT_VALIDATION_ONLY',
            'notice': 'Graph is structurally valid, not executable or backtested. No order or signal created.'}


def catalog() -> dict:
    return {'schema_version': '1.0', 'nodes': [
        {'type': key, 'inputs': ins, 'outputs': outs,
         'parameters': PARAMS.get(key, {})}
        for key, (ins, outs) in PORTS.items()
    ], 'scope': 'DRAFT_VALIDATION_ONLY'}
