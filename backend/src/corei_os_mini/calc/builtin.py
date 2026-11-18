"""Built-in calculation graphs: the platform's own formulas drawn as graphs, read-only and APPROVED, each with the
code it must equal (its parity reference).

Why it exists: built-ins show every desk exactly how the platform computes — node by node, with live numbers — and
are the starting point for a copy the desk can change. A built-in graph replaces formula text only once a test proves
it equals the code it draws, to 1e-9, on every bar (DEC-93, phase 2 adds the remaining library signals).
Purpose: `BUILTINS` — code → {name, purpose, description, parity_ref, definition}.
Must never: change without its parity test; be edited in place (a change is a new graph or a new version).
Dependencies: none (plain data). Consumers: calc.store, calc_api, tests (parity).
Architectural position: reference graphs of Calculation Graphs.
"""
from __future__ import annotations


def _w(a: str, b: str) -> dict:
    return {'from': a, 'to': b}


def _atr_edges(node: str = 'atr') -> list[dict]:
    return [_w('bars.high', f'{node}.high'), _w('bars.low', f'{node}.low'), _w('bars.close', f'{node}.close')]


EMA_TREND = {
    'purpose': 'SIGNAL',
    'parameters': [
        {'name': 'fast', 'kind': 'int', 'default': 9, 'min': 1, 'max': 500, 'doc': 'fast EMA, bars'},
        {'name': 'slow', 'kind': 'int', 'default': 21, 'min': 2, 'max': 1000, 'doc': 'slow EMA, bars'},
        {'name': 'scale', 'kind': 'float', 'default': 1.0, 'min': 0.01, 'max': 100, 'doc': 'ATRs for a full score'},
    ],
    'nodes': [
        {'id': 'bars', 'type': 'source.bars', 'position': (0, 120)},
        {'id': 'fast', 'type': 'ind.ema', 'params': {'n': '{{fast}}'}, 'position': (260, 0), 'label': 'EMA fast'},
        {'id': 'slow', 'type': 'ind.ema', 'params': {'n': '{{slow}}'}, 'position': (260, 110), 'label': 'EMA slow'},
        {'id': 'atr', 'type': 'ind.atr', 'params': {'n': 14}, 'position': (260, 220), 'label': 'ATR 14'},
        {'id': 'scale', 'type': 'source.constant', 'params': {'value': '{{scale}}'}, 'position': (260, 330), 'label': 'scale'},
        {'id': 'raw', 'type': 'math.expression', 'position': (520, 120), 'label': 'gap in ATRs',
         'params': {'inputs': 'fast, slow, atr, scale', 'expression': '100 * (fast - slow) / (scale * atr)'}},
        {'id': 'clamp', 'type': 'math.clamp', 'params': {'lo': -100, 'hi': 100}, 'position': (760, 120)},
        {'id': 'score', 'type': 'output.signal', 'params': {'show': 'fast, slow, atr14'}, 'position': (1000, 120)},
    ],
    'edges': [
        _w('bars.close', 'fast.x'), _w('bars.close', 'slow.x'), *_atr_edges(),
        _w('fast.out', 'raw.fast'), _w('slow.out', 'raw.slow'), _w('atr.out', 'raw.atr'), _w('scale.value', 'raw.scale'),
        _w('raw.out', 'clamp.x'), _w('clamp.out', 'score.score'),
        _w('fast.out', 'score.fast'), _w('slow.out', 'score.slow'), _w('atr.out', 'score.atr14'),
    ],
    'notes': 'score = clamp(100 × (EMA(fast) − EMA(slow)) ÷ (scale × ATR14), −100, +100). Positive when the fast average is above the slow one; '
             'the gap is measured in ATRs, so it means the same on every instrument.',
}

BUILTINS: dict[str, dict] = {
    'SIG_EMA_TREND': {'name': 'EMA trend', 'purpose': 'SIGNAL', 'parity_ref': 'trading.signal_library.compute("EMA_TREND")',
                      'description': 'The signal library\'s EMA trend score as a graph.', 'definition': EMA_TREND},
}
