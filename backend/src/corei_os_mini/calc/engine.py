"""Running approved calculation graphs inside the stages: the bridge between a stage's own loop and the executor.

Why it exists: once a signal definition is bound to an approved graph version (calc_bindings), the platform scores it
with that graph everywhere — the live board, flips, explain, correlation, research — in exactly the shape the library
formula gives, so nothing downstream needs to know which one ran.
Purpose: `plan_for(definition, hash, params)` (compiled plans cached by hash and parameters — a version never changes,
so its plan never goes stale); `signal_series(graph, params, bars, tf)` → per bar None or (score, inputs).
Graph parameters come from the signal definition (the same names as the library's) and from the binding; names the
graph does not have are ignored, values outside the graph's bounds are refused.
Must never: run an unapproved version (only bound versions arrive here; bindings require APPROVED); hide an error —
a graph that fails raises, and the caller records it the way it records a failing formula.
Dependencies: calc.run, calc.nodes. Consumers: trading.signal_library (graph-bound signals).
Architectural position: stage integration of Calculation Graphs (DEC-93).
"""
from __future__ import annotations

import json
from collections import OrderedDict

from .nodes import Ctx
from .run import Plan, compile_graph, execute

_CACHE: OrderedDict[str, Plan] = OrderedDict()
_MAX = 512


def plan_for(definition: dict, hash_: str, params: dict) -> Plan:
    names = {p['name'] for p in definition.get('parameters', [])}
    over = {k: v for k, v in (params or {}).items() if k in names}
    key = hash_ + json.dumps(over, sort_keys=True, default=str)
    plan = _CACHE.get(key)
    if plan is None:
        plan = compile_graph(definition, over)
        _CACHE[key] = plan
        if len(_CACHE) > _MAX:
            _CACHE.popitem(last=False)
    else:
        _CACHE.move_to_end(key)
    return plan


def signal_series(graph: dict, params: dict, bars: list[dict], tf: str = 'H1') -> list:
    plan = plan_for(graph['definition'], graph['hash'], params)
    res = execute(plan, Ctx(bars, tf))
    if not res.ok:
        first = next(iter(res.errors.items()), ('graph', 'failed'))
        raise ValueError(f"Graph {graph.get('code')} v{graph.get('version')}: node {first[0]}: {first[1]}")
    scores, inputs = res.outputs['score'], res.outputs['inputs']
    return [None if s is None else (s, inputs[i]) for i, s in enumerate(scores)]
