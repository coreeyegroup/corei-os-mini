"""Combine nodes: many scores into one — weighted sum, vote, consensus — and ranking across inputs.

Why it exists: blends of signals (Stage 3 and the SIGNAL_BLEND strategy) and portfolio scoring combine several inputs;
the rule for weights and for missing inputs must be written down and visible, not buried in a loop.
Purpose: Weighted sum (Σ wᵢ·xᵢ ÷ Σ|wᵢ|, missing inputs either skipped or making the result missing), Vote
(100 × (longs − shorts) ÷ inputs), Consensus (share of inputs on the side of the average, signed), Rank (1 = highest
across the inputs on each bar).
The number of inputs (2…8) is a parameter; the ports in1…inN follow it.
Must never: read a later bar; drop an input without saying so (the rule is a parameter shown in the formula).
Dependencies: calc.nodes. Consumers: calc.run.
Architectural position: combination inside graphs (Stages 3, 4 and 6).
"""
from __future__ import annotations

from .. import types as T
from . import Ctx, NodeType, Param, Port, register

S = T.SERIES_FLOAT
COUNT = Param('count', 'int', 2, 2, 8, doc='how many inputs')


def _ins(p: dict) -> list[str]:
    return [f'in{k}' for k in range(1, int(p.get('count') or 2) + 1)]


def _weighted_ports(p: dict):
    return [Port(n, S) for n in _ins(p)], [Port('out', S)]


def _weights(p: dict) -> list[float]:
    return [p[f'w{k}'] for k in range(1, len(_ins(p)) + 1)]


def _weighted_check(p: dict) -> None:
    if sum(abs(w) for w in _weights(p)) == 0:
        raise ValueError('at least one weight must be other than 0')


def _weighted(ctx: Ctx, ins: dict, p: dict) -> dict:
    names, ws, skip = _ins(p), _weights(p), p['missing'] == 'skip'
    out = []
    for i in range(ctx.n):
        num = den = 0.0
        missing = False
        for n, w in zip(names, ws):
            x = ins[n][i]
            if x is None:
                missing = True
                continue
            num += w * x
            den += abs(w)
        out.append(None if (missing and not skip) or den == 0 else num / den)
    return {'out': out}


def _weighted_formula(p: dict) -> str:
    ws = _weights({**{f'w{k}': 1.0 for k in range(1, 9)}, **p})
    terms = ' + '.join(f'{w:g}·in{k}' for k, w in enumerate(ws, 1))
    return f"out = ({terms}) ÷ {sum(abs(w) for w in ws):g}" + ('  (a missing input is left out, with its weight)' if p.get('missing') == 'skip' else '')


register(NodeType('combine.weighted_sum', 'combine', 'Weighted sum', 'Σ wᵢ·xᵢ ÷ Σ|wᵢ| — a blend that stays on the inputs\' scale.',
                  [Port('in1', S), Port('in2', S)], [Port('out', S)],
                  [COUNT, *[Param(f'w{k}', 'float', 1.0, -1000, 1000, doc=f'weight of in{k}') for k in range(1, 9)],
                   Param('missing', 'choice', 'skip', choices=('skip', 'missing'), doc='skip: leave a missing input out; missing: the result is missing')],
                  _weighted, _weighted_formula, ports=_weighted_ports, check=_weighted_check))


def _sides(ins: dict, names: list[str], i: int, thr: float):
    longs = shorts = present = 0
    for n in names:
        x = ins[n][i]
        if x is None:
            continue
        present += 1
        longs += x >= thr
        shorts += x <= -thr
    return longs, shorts, present


def _vote(ctx: Ctx, ins: dict, p: dict) -> dict:
    names = _ins(p)
    out = []
    for i in range(ctx.n):
        lo, sh, present = _sides(ins, names, i, p['threshold'])
        out.append(None if present == 0 else 100.0 * (lo - sh) / len(names))
    return {'out': out}


register(NodeType('combine.vote', 'combine', 'Vote', 'Each input votes LONG (≥ +threshold), SHORT (≤ −threshold) or abstains; '
                  'out = 100 × (longs − shorts) ÷ inputs.', [Port('in1', S), Port('in2', S)], [Port('out', S)],
                  [COUNT, Param('threshold', 'float', 20.0, 0, 100)], _vote,
                  lambda p: f"out = 100 × (inputs ≥ {p.get('threshold')} − inputs ≤ −{p.get('threshold')}) ÷ {p.get('count')}",
                  ports=_weighted_ports))


def _consensus(ctx: Ctx, ins: dict, p: dict) -> dict:
    names = _ins(p)
    out = []
    for i in range(ctx.n):
        xs = [ins[n][i] for n in names if ins[n][i] is not None]
        if not xs:
            out.append(None)
            continue
        avg = sum(xs) / len(xs)
        sign = (avg > 0) - (avg < 0)
        agree = sum(1 for x in xs if (x > 0) - (x < 0) == sign) if sign else 0
        out.append(100.0 * sign * agree / len(names))
    return {'out': out}


register(NodeType('combine.consensus', 'combine', 'Consensus', 'Share of the inputs on the same side as their average, in %, signed by that side.',
                  [Port('in1', S), Port('in2', S)], [Port('out', S)], [COUNT], _consensus,
                  lambda p: f"out = sign(average) × 100 × (inputs on that side) ÷ {p.get('count')}", ports=_weighted_ports))


def _rank_ports(p: dict):
    names = _ins(p)
    return [Port(n, S) for n in names], [Port(f'rank{k}', S) for k in range(1, len(names) + 1)]


def _rank(ctx: Ctx, ins: dict, p: dict) -> dict:
    names = _ins(p)
    out = {f'rank{k}': [] for k in range(1, len(names) + 1)}
    for i in range(ctx.n):
        xs = [ins[n][i] for n in names]
        for k, x in enumerate(xs, 1):
            out[f'rank{k}'].append(None if x is None else 1.0 + sum(1 for y in xs if y is not None and y > x))
    return out


register(NodeType('combine.rank', 'combine', 'Rank', 'The rank of each input among the inputs on each bar (1 = highest; ties share a rank).',
                  [Port('in1', S), Port('in2', S)], [Port('rank1', S), Port('rank2', S)], [COUNT], _rank,
                  lambda p: 'rankₖ = 1 + (inputs above inₖ)', ports=_rank_ports))
