"""Output nodes: the end of every graph, one per purpose, each enforcing that purpose's contract.

Why it exists: a graph's result is used by a stage that expects a precise shape — a signal score within ±100, a target
side, an exact decimal quantity, a refusal with a reason. The output node checks that shape on every run, so a graph
that breaks its contract fails loudly with the reason instead of feeding a wrong value downstream.
Purpose (calc.model.PURPOSES):
  Feature     value per bar.
  Signal      score per bar within −100…+100, plus the inputs shown with it (wired to the node, named by the author).
  Rule        target side per bar (LONG, SHORT, FLAT, HOLD), optional strength 0…100, and a reason.
  Sizing      an exact decimal quantity (≥ 0) and a note.
  Protective  stop and/or target as exact decimal prices.
  Risk check  refuse (true/false) on the last bar and the reason — it can only refuse; a value that cannot be computed
              refuses (fails closed). The built-in risk limits run whatever the graph says.
  Allocation  non-negative weights that sum to 1 (normalised when asked).
  Measure     a named number with its unit.
Must never: pass a value that breaks the contract; let a risk graph approve anything.
Dependencies: calc.nodes. Consumers: calc.run.
Architectural position: the contract boundary between graphs and the stages that use them (DEC-93).
"""
from __future__ import annotations

import re
from decimal import Decimal

from .. import types as T
from . import Ctx, NodeType, Param, Port, register

S = T.SERIES_FLOAT
_NAME = re.compile(r'^[a-z][a-z0-9_]{0,23}$')


def _shown(p: dict) -> list[str]:
    return [n.strip() for n in (p.get('show') or '').split(',') if n.strip()]


def _show_check(p: dict) -> None:
    names = _shown(p)
    if len(names) > 8 or len(set(names)) != len(names) or any(not _NAME.match(n) or n in ('score', 'value') for n in names):
        raise ValueError('show: up to 8 different lower-case names, e.g. "fast, slow, atr14"')


def _signal_ports(p: dict):
    return [Port('score', S), *[Port(n, S, 'shown with the score', optional=True) for n in _shown(p)]], []


def _signal(ctx: Ctx, ins: dict, p: dict) -> dict:
    score = ins['score']
    for i, s in enumerate(score):
        if s is not None and not -100.0 - 1e-9 <= s <= 100.0 + 1e-9:
            raise ValueError(f'Signal contract: the score must be within −100…+100; bar {i + 1} gives {s:.6g}. Clamp it.')
    names = [n for n in _shown(p) if n in ins]
    return {'score': score, 'inputs': [None if s is None else {n: ins[n][i] for n in names} for i, s in enumerate(score)]}


register(NodeType('output.signal', 'outputs', 'Signal score', 'The graph\'s result for Stage 3: a score per bar within −100…+100, '
                  'plus the values shown with it (name them in "show" and wire them in).', [Port('score', S)], [],
                  [Param('show', 'text', '', doc='names of extra values shown with the score, e.g. "fast, slow, atr14"')],
                  _signal, lambda p: 'score ∈ [−100, +100]', ports=_signal_ports, check=_show_check, purpose='SIGNAL'))

register(NodeType('output.feature', 'outputs', 'Feature value', 'The graph\'s result for Stage 2: a number per bar.', [Port('value', S)], [], [],
                  lambda c, i, p: {'value': i['value']}, lambda p: 'feature value per bar', purpose='FEATURE'))


def _rule(ctx: Ctx, ins: dict, p: dict) -> dict:
    target, strength = ins['target'], ins.get('strength')
    for i, s in enumerate(target):
        if s not in T.SIDES:
            raise ValueError(f'Rule contract: the target must be LONG, SHORT, FLAT or HOLD; bar {i + 1} gives {s!r}.')
    if strength is not None:
        for i, s in enumerate(strength):
            if s is not None and not 0 <= s <= 100:
                raise ValueError(f'Rule contract: strength must be within 0…100; bar {i + 1} gives {s:.6g}.')
    return {'target': target, 'strength': strength, 'reason': p['reason']}


register(NodeType('output.rule', 'outputs', 'Strategy rule', 'The graph\'s result for Stage 4: a target side per bar, a strength and a reason.',
                  [Port('target', T.SERIES_SIDE), Port('strength', S, '0…100', optional=True)], [],
                  [Param('reason', 'text', '', doc='why the rule holds, shown with every decision')], _rule,
                  lambda p: 'target ∈ {LONG, SHORT, FLAT, HOLD}', purpose='STRATEGY_RULE'))


def _sizing(ctx: Ctx, ins: dict, p: dict) -> dict:
    q = ins['quantity']
    if not isinstance(q, Decimal):
        raise ValueError('Sizing contract: the quantity must be an exact decimal.')
    if q < 0:
        raise ValueError(f'Sizing contract: the quantity must not be negative; it is {q}.')
    return {'quantity': q, 'note': p['note']}


register(NodeType('output.sizing', 'outputs', 'Order size', 'The graph\'s result for Stage 5: an exact decimal quantity and how it was worked out.',
                  [Port('quantity', T.QUANTITY)], [], [Param('note', 'text', '')], _sizing, lambda p: 'quantity ≥ 0 (exact decimal)', purpose='SIZING'))


def _protective(ctx: Ctx, ins: dict, p: dict) -> dict:
    stop, target = ins.get('stop'), ins.get('target')
    if stop is None and target is None:
        raise ValueError('Protective contract: give a stop, a target or both.')
    for name, v in (('stop', stop), ('target', target)):
        if v is not None and (not isinstance(v, Decimal) or v <= 0):
            raise ValueError(f'Protective contract: the {name} must be an exact decimal price above 0.')
    return {'stop': stop, 'target': target}


register(NodeType('output.protective', 'outputs', 'Stop and target', 'The graph\'s result for protective orders: exact decimal prices.',
                  [Port('stop', T.PRICE, optional=True), Port('target', T.PRICE, optional=True)], [], [], _protective,
                  lambda p: 'stop, target > 0 (exact decimals)', purpose='PROTECTIVE'))


def _risk(ctx: Ctx, ins: dict, p: dict) -> dict:
    r = ins['refuse']
    last = r[-1] if r else None
    if last is None:
        return {'refuse': True, 'reason': f"{p['reason']} — could not be computed on the last bar, so it refuses (fails closed)"}
    return {'refuse': bool(last), 'reason': p['reason'] if last else ''}


def _risk_check(p: dict) -> None:
    if len((p.get('reason') or '').strip()) < 5:
        raise ValueError('a risk check needs the reason it gives when it refuses (5 characters or more)')


register(NodeType('output.risk_check', 'outputs', 'Risk refusal', 'The graph\'s result for Stage 5: refuse on the last bar, with the reason. '
                  'It can only refuse — the built-in limits always run.', [Port('refuse', T.SERIES_BOOL)], [],
                  [Param('reason', 'text', '', doc='shown when it refuses')], _risk, lambda p: 'refuse if true (missing refuses)',
                  check=_risk_check, purpose='RISK_CHECK'))


def _alloc_ports(p: dict):
    return [Port(f'in{k}', T.FLOAT) for k in range(1, int(p.get('count') or 2) + 1)], []


def _alloc(ctx: Ctx, ins: dict, p: dict) -> dict:
    ws = [ins[f'in{k}'] for k in range(1, p['count'] + 1)]
    if any(w is None for w in ws):
        raise ValueError('Allocation contract: every weight must have a value.')
    if any(w < 0 for w in ws):
        raise ValueError('Allocation contract: weights must not be negative.')
    total = sum(ws)
    if p['normalise']:
        if total <= 0:
            raise ValueError('Allocation contract: the weights add up to 0, so they cannot be normalised.')
        ws = [w / total for w in ws]
    elif abs(total - 1) > 1e-9:
        raise ValueError(f'Allocation contract: weights must sum to 1; they sum to {total:.9g}.')
    return {'weights': ws}


register(NodeType('output.allocation', 'outputs', 'Allocation', 'The graph\'s result for Stage 6: non-negative weights that sum to 1.',
                  [Port('in1', T.FLOAT), Port('in2', T.FLOAT)], [],
                  [Param('count', 'int', 2, 1, 8), Param('normalise', 'bool', True, doc='divide by the total')], _alloc,
                  lambda p: 'wₖ ≥ 0, Σw = 1', ports=_alloc_ports, purpose='ALLOCATION'))

register(NodeType('output.measure', 'outputs', 'Measure', 'The graph\'s result for a portfolio, execution or post-trade view: a named number.',
                  [Port('value', S)], [], [Param('name', 'text', 'measure'), Param('unit', 'text', '')],
                  lambda c, i, p: {'value': i['value'], 'name': p['name'], 'unit': p['unit']}, lambda p: f"{p.get('name')} [{p.get('unit')}]",
                  purpose='MEASURE'))
