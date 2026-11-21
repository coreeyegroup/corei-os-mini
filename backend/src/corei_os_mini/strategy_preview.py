"""Deterministic, non-executing preview of v1 draft workflows.

Uses COREI's existing indicator formulas. No DB, broker, risk or order access.
Only closed bars supplied by the caller are evaluated.
"""
from __future__ import annotations

from math import isfinite

from .intel import indicators as ind
from .strategy_workflow import Workflow, compile_workflow, signal_direction


def preview(workflow: Workflow, bars: list[dict]) -> dict:
    plan = compile_workflow(workflow)
    if not 2 <= len(bars) <= 5000:
        raise ValueError('Preview requires 2–5000 closed bars')
    for i, bar in enumerate(bars):
        try:
            o, h, l, c = (float(bar[k]) for k in ('open', 'high', 'low', 'close'))
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(f'Bar {i} has missing or invalid OHLC') from exc
        if not all(isfinite(v) and v > 0 for v in (o, h, l, c)) or h < max(o, c) or l > min(o, c):
            raise ValueError(f'Bar {i} has invalid OHLC range')
    by_id = {n.id: n for n in workflow.nodes}
    wires = {(e.target, e.target_port): (e.source, e.source_port) for e in workflow.edges}
    values: dict[tuple[str, str], list] = {}
    n = len(bars)
    closes = [float(b['close']) for b in bars]
    highs = [float(b['high']) for b in bars]
    lows = [float(b['low']) for b in bars]

    def source(node_id: str, port: str):
        return values[wires[node_id, port]]

    for node_id in plan['execution_order']:
        node = by_id[node_id]
        typ, params = node.type, node.params
        if typ == 'input.bars':
            for port, series in [('close', closes), ('high', highs), ('low', lows)]:
                values[node_id, port] = series
        elif typ == 'indicator.ema':
            values[node_id, 'value'] = ind.ema(source(node_id, 'source'), params['period'])
        elif typ == 'indicator.rsi':
            values[node_id, 'value'] = ind.rsi(source(node_id, 'source'), params['period'])
        elif typ == 'indicator.atr':
            values[node_id, 'value'] = ind.atr(source(node_id, 'high'), source(node_id, 'low'),
                                               source(node_id, 'close'), params['period'])
        elif typ == 'condition.compare':
            a, b, op = source(node_id, 'left'), source(node_id, 'right'), params['operator']
            out = []
            for i in range(n):
                if a[i] is None or b[i] is None:
                    out.append(None)
                elif op == '>':
                    out.append(a[i] > b[i])
                elif op == '<':
                    out.append(a[i] < b[i])
                elif op == '>=':
                    out.append(a[i] >= b[i])
                elif op == '<=':
                    out.append(a[i] <= b[i])
                elif i == 0 or a[i-1] is None or b[i-1] is None:
                    out.append(None)
                elif op == 'CROSSES_ABOVE':
                    out.append(a[i-1] <= b[i-1] and a[i] > b[i])
                else:
                    out.append(a[i-1] >= b[i-1] and a[i] < b[i])
            values[node_id, 'result'] = out
        elif typ in ('logic.and', 'logic.or'):
            a, b = source(node_id, 'a'), source(node_id, 'b')
            values[node_id, 'result'] = [
                (False if False in (x, y) else None if None in (x, y) else True)
                if typ == 'logic.and' else
                (True if True in (x, y) else None if None in (x, y) else False)
                for x, y in zip(a, b)
            ]
        elif typ == 'signal.direction':
            long, short = source(node_id, 'long'), source(node_id, 'short')
            values[node_id, 'target'] = [
                signal_direction(a, b) for a, b in zip(long, short)
            ]
        elif typ in ('plan.atr_stop', 'plan.r_multiple'):
            # Price planning requires instrument specifications and is intentionally
            # unsupported in this non-executing preview.
            raise ValueError(f'{node_id}: trade-plan nodes require the broker-aware planner')
        else:
            raise ValueError(f'{node_id}: unsupported node type')
    signals = [node for node in workflow.nodes if node.type == 'signal.direction']
    if len(signals) != 1:
        raise ValueError('Preview requires exactly one signal.direction node')
    targets = values[signals[0].id, 'target']
    return {**plan, 'bars_evaluated': n, 'latest': targets[-1],
            'tail': targets[-min(100, n):],
            'notice': 'Read-only historical evaluation of supplied closed bars. No fill, position, stop, target or order is implied.'}
