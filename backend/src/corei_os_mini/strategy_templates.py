"""Read-only starter templates for the universal strategy draft builder.

Templates are examples, not validated profitable strategies. They are not
approved versions and cannot be deployed through this API.
"""
from __future__ import annotations

from copy import deepcopy

from .strategy_workflow import Workflow, compile_workflow


def ema_directional(fast: int = 20, slow: int = 50) -> dict:
    return {
        'schema_version': '1.0',
        'name': f'EMA {fast}/{slow} trend direction (research draft)',
        'nodes': [
            {'id': 'bars', 'type': 'input.bars'},
            {'id': 'fast', 'type': 'indicator.ema', 'params': {'period': fast}},
            {'id': 'slow', 'type': 'indicator.ema', 'params': {'period': slow}},
            {'id': 'bull', 'type': 'condition.compare', 'params': {'operator': '>'}},
            {'id': 'bear', 'type': 'condition.compare', 'params': {'operator': '<'}},
            {'id': 'signal', 'type': 'signal.direction'},
        ],
        'edges': [
            {'source': 'bars', 'source_port': 'close', 'target': 'fast', 'target_port': 'source'},
            {'source': 'bars', 'source_port': 'close', 'target': 'slow', 'target_port': 'source'},
            {'source': 'fast', 'source_port': 'value', 'target': 'bull', 'target_port': 'left'},
            {'source': 'slow', 'source_port': 'value', 'target': 'bull', 'target_port': 'right'},
            {'source': 'fast', 'source_port': 'value', 'target': 'bear', 'target_port': 'left'},
            {'source': 'slow', 'source_port': 'value', 'target': 'bear', 'target_port': 'right'},
            {'source': 'bull', 'source_port': 'result', 'target': 'signal', 'target_port': 'long'},
            {'source': 'bear', 'source_port': 'result', 'target': 'signal', 'target_port': 'short'},
        ],
    }


def ema_crossover(fast: int = 12, slow: int = 26) -> dict:
    result = ema_directional(fast, slow)
    result['name'] = f'EMA {fast}/{slow} crossover (research draft)'
    result['nodes'][3]['params']['operator'] = 'CROSSES_ABOVE'
    result['nodes'][4]['params']['operator'] = 'CROSSES_BELOW'
    return result


TEMPLATES = {
    'ema_trend_20_50': ('Trend following', ema_directional(20, 50)),
    'ema_trend_50_200': ('Trend following', ema_directional(50, 200)),
    'ema_cross_12_26': ('Momentum crossover', ema_crossover(12, 26)),
    'ema_cross_20_50': ('Momentum crossover', ema_crossover(20, 50)),
}


def template_list() -> dict:
    return {
        'scope': 'RESEARCH_DRAFT_ONLY',
        'notice': 'Illustrative starting points. Not backtested, approved or executable.',
        'items': [
            {'id': key, 'category': category, 'name': definition['name']}
            for key, (category, definition) in TEMPLATES.items()
        ],
    }


def template_get(template_id: str) -> dict:
    if template_id not in TEMPLATES:
        raise KeyError(template_id)
    definition = deepcopy(TEMPLATES[template_id][1])
    compile_workflow(Workflow.model_validate(definition))
    return definition
