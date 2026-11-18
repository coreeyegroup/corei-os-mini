"""The executor of Calculation Graphs: check a graph completely, then run it in dependency order with a trace of every
node (the n8n "execution view": what went in, what came out, the formula with the numbers, how long it took).

Why it exists: one executor for every stage keeps behaviour identical wherever a graph is used — preview, research,
the live engine, backtests — and makes every value inspectable on any bar.
Purpose:
  `compile_graph(definition, overrides)` → Plan or GraphInvalid(errors): schema, graph parameters ({{name}}), node
      types, node parameters, ports, edge types (naming both ends), one wire per input, required inputs, cycles, depth,
      size limits, exactly one output node of the graph's purpose.
  `execute(plan, ctx, trace_at)` → Result: outputs, per-node errors (one node failing stops only what depends on it),
      the trace at bar trace_at (default the last bar), timings, warm-up (bars before the first full value).
  `run(definition, ctx, …)` = compile + execute.
Determinism: nodes are pure and run in a fixed topological order (ties by node id); the same definition, parameters
and bars always give the same outputs. Nothing here reads the clock except to time nodes for the trace.
Must never: run a graph that failed validation; let one node's exception escape; let a node see bars after the bar
it computes (the catalogue is tested for this node by node).
Dependencies: calc.model, calc.types, calc.nodes, calc.expr. Consumers: calc_api, (from phase 2) the stage engines.
Architectural position: the core of Calculation Graphs (DEC-93).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from . import types as T
from .model import LIMITS, PURPOSES, Graph
from .nodes import REGISTRY, Ctx, NodeType, Port, _load, check_params, fmt

_REF = re.compile(r'^\{\{\s*([a-z][a-z0-9_]*)\s*\}\}$')


class GraphInvalid(ValueError):
    def __init__(self, errors: list[dict]):
        super().__init__('; '.join(e['message'] for e in errors[:5]))
        self.errors = errors


@dataclass
class PlanNode:
    id: str
    nt: NodeType
    params: dict
    inputs: list[Port]
    outputs: list[Port]
    wires: dict[str, tuple[str, str]]      # input port -> (source node, source port)
    label: str = ''


@dataclass
class Plan:
    graph: Graph
    order: list[PlanNode]
    output: PlanNode
    params: dict[str, float]
    warmup: int
    hash: str


@dataclass
class Result:
    ok: bool
    outputs: dict[str, Any] | None
    errors: dict[str, str]
    trace: dict[str, dict]
    bar: int | None
    bars: int
    ms: float
    warmup: int
    values: dict[str, dict] = field(default_factory=dict)


def definition_hash(definition: dict) -> str:
    """Hash of the canonical JSON (sorted keys, positions left out — moving a node is not a change of calculation)."""
    d = json.loads(json.dumps(definition, default=str))
    for n in d.get('nodes', []):
        n.pop('position', None)
    return hashlib.sha256(json.dumps(d, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _err(where: str, message: str, node: str | None = None) -> dict:
    return {'where': where, 'node': node, 'message': message}


def _resolve(value, params: dict[str, float], node_id: str, name: str, errors: list) -> Any:
    if isinstance(value, str):
        m = _REF.match(value)
        if m:
            if m.group(1) not in params:
                errors.append(_err('params', f"Node {node_id}, parameter {name}: there is no graph parameter '{m.group(1)}'.", node_id))
                return None
            return params[m.group(1)]
    return value


def compile_graph(definition: dict | Graph, overrides: dict | None = None) -> Plan:
    _load()
    errors: list[dict] = []
    try:
        g = definition if isinstance(definition, Graph) else Graph.model_validate(definition)
    except ValidationError as exc:
        raise GraphInvalid([_err('schema', f"{'.'.join(map(str, e['loc']))}: {e['msg']}") for e in exc.errors()[:20]]) from None
    # Graph parameters: defaults, overrides within bounds.
    params = g.param_defaults()
    names = [p.name for p in g.parameters]
    if len(set(names)) != len(names):
        errors.append(_err('parameters', 'Graph parameter names must be different.'))
    for k, v in (overrides or {}).items():
        spec = next((p for p in g.parameters if p.name == k), None)
        if spec is None:
            errors.append(_err('parameters', f"The graph has no parameter '{k}'; it has {', '.join(names) or 'none'}."))
            continue
        try:
            v = float(v)
            if spec.kind == 'int':
                if v != int(v):
                    raise ValueError
                v = int(v)
        except (TypeError, ValueError):
            errors.append(_err('parameters', f"Graph parameter '{k}' must be {'a whole number' if spec.kind == 'int' else 'a number'}."))
            continue
        if (spec.min is not None and v < spec.min) or (spec.max is not None and v > spec.max):
            errors.append(_err('parameters', f"Graph parameter '{k}' must be within {spec.min}…{spec.max}; got {v}."))
            continue
        params[k] = v
    # Nodes.
    nodes: dict[str, PlanNode] = {}
    for n in g.nodes:
        if n.id in nodes:
            errors.append(_err('nodes', f'Two nodes are called {n.id}.', n.id))
            continue
        nt = REGISTRY.get(n.type)
        if nt is None:
            errors.append(_err('nodes', f"Node {n.id}: unknown node type '{n.type}'.", n.id))
            continue
        raw = {k: _resolve(v, params, n.id, k, errors) for k, v in n.params.items()}
        try:
            p = check_params(n.id, nt, raw)
        except ValueError as exc:
            errors.append(_err('params', str(exc), n.id))
            continue
        ins, outs = nt.io(p)
        nodes[n.id] = PlanNode(n.id, nt, p, ins, outs, {}, n.label)
    # Edges.
    for e in g.edges:
        try:
            sn, sp = e.source.split('.', 1)
            tn, tp = e.target.split('.', 1)
        except ValueError:
            errors.append(_err('edges', f'A wire must join node.port to node.port; got {e.source} → {e.target}.'))
            continue
        if sn not in nodes or tn not in nodes:
            missing = sn if sn not in nodes else tn
            if not any(x['node'] == missing for x in errors):
                errors.append(_err('edges', f'Wire {e.source} → {e.target}: there is no valid node {missing}.', missing))
            continue
        src = next((p for p in nodes[sn].outputs if p.name == sp), None)
        dst = next((p for p in nodes[tn].inputs if p.name == tp), None)
        if src is None:
            errors.append(_err('edges', f"Wire {e.source} → {e.target}: node {sn} has no output '{sp}' (it has {', '.join(p.name for p in nodes[sn].outputs) or 'none'}).", sn))
            continue
        if dst is None:
            errors.append(_err('edges', f"Wire {e.source} → {e.target}: node {tn} has no input '{tp}' (it has {', '.join(p.name for p in nodes[tn].inputs) or 'none'}).", tn))
            continue
        if tp in nodes[tn].wires:
            errors.append(_err('edges', f'Input {e.target} has two wires; an input takes one.', tn))
            continue
        if not T.compatible(src.type, dst.type):
            hint = ' Money, prices and quantities need a To decimal node.' if dst.type in T.DECIMALS and src.type in (T.FLOAT, T.SERIES_FLOAT) else \
                ' Use a Last value node to take the value on the last bar.' if T.is_series(src.type) and not T.is_series(dst.type) else ''
            errors.append(_err('types', f'Wire {e.source} ({src.type}) → {e.target} ({dst.type}): the types do not match.{hint}', tn))
            continue
        nodes[tn].wires[tp] = (sn, sp)
    for pn in nodes.values():
        for port in pn.inputs:
            if not port.optional and port.name not in pn.wires:
                errors.append(_err('edges', f"Node {pn.id} ({pn.nt.label}): input '{port.name}' ({port.type}) is not connected.", pn.id))
    # Exactly one output, of the graph's purpose.
    outs = [pn for pn in nodes.values() if pn.nt.family == 'outputs']
    want = PURPOSES[g.purpose]['output']
    if len(outs) != 1:
        errors.append(_err('outputs', f"A {PURPOSES[g.purpose]['label'].lower()} graph ends in exactly one output node ({want}); it has {len(outs)}."))
    elif outs[0].nt.type != want:
        errors.append(_err('outputs', f"A {PURPOSES[g.purpose]['label'].lower()} graph must end in {want}, not {outs[0].nt.type}.", outs[0].id))
    # Order: Kahn's algorithm, ties broken by node id (deterministic); a remainder is a cycle.
    order: list[PlanNode] = []
    if not errors:
        indeg = {k: len({s for s, _ in v.wires.values()}) for k, v in nodes.items()}
        users: dict[str, set[str]] = {k: set() for k in nodes}
        for k, v in nodes.items():
            for s, _ in v.wires.values():
                users[s].add(k)
        ready = sorted(k for k, d in indeg.items() if d == 0)
        depth = {k: 1 for k in ready}
        while ready:
            k = ready.pop(0)
            order.append(nodes[k])
            for u in sorted(users[k]):
                indeg[u] -= 1
                depth[u] = max(depth.get(u, 1), depth[k] + 1)
                if indeg[u] == 0:
                    ready.append(u)
                    ready.sort()
        if len(order) != len(nodes):
            loop = sorted(k for k, d in indeg.items() if d > 0)
            errors.append(_err('cycle', f"The wires go round in a loop through {', '.join(loop)}; a calculation must flow one way.", loop[0]))
        elif max(depth.values(), default=0) > LIMITS['depth']:
            errors.append(_err('limits', f"The graph is {max(depth.values())} steps deep; the limit is {LIMITS['depth']}."))
        # Unused nodes are allowed (a scratch branch) but reported in the trace as not reaching the output.
    if errors:
        raise GraphInvalid(errors)
    # Warm-up: the longest chain of look-backs ending at the output.
    warm: dict[str, int] = {}
    for pn in order:
        try:
            own = int(pn.nt.lookback(pn.params))
        except Exception:  # noqa: BLE001 — a look-back is an estimate shown to people, never a reason to fail
            own = 0
        warm[pn.id] = own + max((warm[s] for s, _ in pn.wires.values()), default=0)
    return Plan(g, order, outs[0], params, warm[outs[0].id], definition_hash(g.model_dump(by_alias=True)))


def _at(v, i: int):
    return v[i] if isinstance(v, list) and 0 <= i < len(v) else (None if isinstance(v, list) else v)


def _explain(pn: PlanNode, ins: dict, outs: dict) -> str:
    if pn.nt.explain:
        try:
            return pn.nt.explain(pn.params, ins, outs)
        except Exception:  # noqa: BLE001
            pass
    formula = pn.nt.formula(pn.params)
    left = ', '.join(f'{k} = {fmt(v)}' for k, v in ins.items())
    right = ', '.join(f'{k} = {fmt(v)}' for k, v in outs.items() if not isinstance(v, (dict, list)))
    return f"{formula}" + (f'  |  {left}' if left else '') + (f'  →  {right}' if right else '')


def execute(plan: Plan, ctx: Ctx, trace_at: int | None = None, keep_values: bool = False) -> Result:
    started = time.perf_counter()
    n = ctx.n
    if n > LIMITS['bars']:
        raise GraphInvalid([_err('limits', f"At most {LIMITS['bars']} bars per run; got {n}.")])
    bar = (n - 1 if trace_at is None else max(-1, min(n - 1, trace_at))) if n else None
    values: dict[str, dict] = {}
    errors: dict[str, str] = {}
    trace: dict[str, dict] = {}
    for pn in plan.order:
        t0 = time.perf_counter()
        failed = sorted({s for s, _ in pn.wires.values() if s in errors})
        if failed:
            errors[pn.id] = f"Not run: input from {', '.join(failed)} failed."
            trace[pn.id] = {'type': pn.nt.type, 'label': pn.label or pn.nt.label, 'error': errors[pn.id], 'ms': 0.0}
            continue
        ins: dict[str, Any] = {}
        for port in pn.inputs:
            if port.name not in pn.wires:
                ins[port.name] = None
                continue
            s, sp = pn.wires[port.name]
            v = values[s][sp]
            if T.is_series(port.type) and not isinstance(v, list):
                v = [v] * n                                   # one value on every bar
            ins[port.name] = v
        try:
            out = pn.nt.run(ctx, ins, pn.params)
            for port in pn.outputs:
                v = out.get(port.name)
                if T.is_series(port.type) and (not isinstance(v, list) or len(v) != n):
                    raise ValueError(f'internal: {pn.nt.type} gave {port.name} with the wrong length')
                if port.type in T.DECIMALS and v is not None and not isinstance(v, Decimal):
                    raise ValueError(f'{port.name} must be an exact decimal')
        except Exception as exc:  # noqa: BLE001 — a node failing stops only what depends on it
            errors[pn.id] = str(exc) or exc.__class__.__name__
            trace[pn.id] = {'type': pn.nt.type, 'label': pn.label or pn.nt.label, 'error': errors[pn.id],
                            'ms': round((time.perf_counter() - t0) * 1000, 3)}
            continue
        values[pn.id] = out
        if bar is not None:
            ins_at = {k: _at(v, bar) for k, v in ins.items()}
            outs_at = {p.name: _at(out.get(p.name), bar) for p in pn.outputs} if pn.outputs else \
                {k: _at(v, bar) for k, v in out.items() if k != 'inputs'}
            trace[pn.id] = {'type': pn.nt.type, 'label': pn.label or pn.nt.label, 'formula': pn.nt.formula(pn.params),
                            'inputs': T.to_jsonable(ins_at), 'outputs': T.to_jsonable(outs_at), 'explain': _explain(pn, ins_at, outs_at),
                            'ms': round((time.perf_counter() - t0) * 1000, 3)}
    ok = plan.output.id in values
    return Result(ok, values.get(plan.output.id), errors, trace, bar, n, round((time.perf_counter() - started) * 1000, 3), plan.warmup,
                  values if keep_values else {})


def run(definition: dict | Graph, ctx: Ctx, overrides: dict | None = None, trace_at: int | None = None) -> Result:
    return execute(compile_graph(definition, overrides), ctx, trace_at)


def run_many(jobs: list[tuple[str, Plan]], ctx: Ctx) -> dict[str, Result | dict]:
    """Several graphs on the same bars (shared features and library scores via ctx.cache). One graph failing never stops
    the others: its entry holds the error instead."""
    out: dict[str, Result | dict] = {}
    for key, plan in jobs:
        try:
            out[key] = execute(plan, ctx)
        except Exception as exc:  # noqa: BLE001
            out[key] = {'error': str(exc)}
    return out
