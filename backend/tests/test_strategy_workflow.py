"""Tests for the additive, non-executing strategy workflow compiler."""
import unittest

from corei_os_mini.calc.nodes import Ctx
from corei_os_mini.calc.run import compile_graph, execute
from corei_os_mini.strategy_drafts import calculation_definition
from corei_os_mini.strategy_workflow import Workflow, compile_workflow
from corei_os_mini.strategy_preview import preview
from corei_os_mini.strategy_templates import template_list, template_get


def example():
    return {
        'name': 'EMA directional draft',
        'nodes': [
            {'id': 'bars', 'type': 'input.bars'},
            {'id': 'fast', 'type': 'indicator.ema', 'params': {'period': 20}},
            {'id': 'slow', 'type': 'indicator.ema', 'params': {'period': 50}},
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


class CompilerTests(unittest.TestCase):
    def test_closed_bar_preview(self):
        bars = [{'open': 100+i, 'high': 102+i, 'low': 99+i, 'close': 101+i}
                for i in range(100)]
        result = preview(Workflow.model_validate(example()), bars)
        self.assertEqual(result['bars_evaluated'], 100)
        self.assertEqual(result['latest'], 'LONG')
        self.assertEqual(len(result['tail']), 100)

    def test_preview_rejects_invalid_bar(self):
        bars = [{'open': 100, 'high': 99, 'low': 98, 'close': 101}] * 100
        with self.assertRaisesRegex(ValueError, 'invalid OHLC range'):
            preview(Workflow.model_validate(example()), bars)

    def test_all_research_templates_compile(self):
        templates = template_list()['items']
        self.assertGreaterEqual(len(templates), 4)
        for item in templates:
            with self.subTest(template=item['id']):
                draft = template_get(item['id'])
                self.assertTrue(compile_workflow(Workflow.model_validate(draft))['valid'])

    def test_ema_trend_templates_distinguish_rising_and_falling_bars(self):
        for template in ('ema_trend_20_50', 'ema_trend_50_200'):
            workflow = Workflow.model_validate(template_get(template))
            for direction, target in ((1, 'LONG'), (-1, 'SHORT')):
                with self.subTest(template=template, target=target):
                    closes = [500 + direction * i for i in range(250)]
                    bars = [{'open': c, 'high': c + 1, 'low': c - 1, 'close': c} for c in closes]
                    self.assertEqual(preview(workflow, bars)['latest'], target)

    def test_calculation_handoff_matches_template_previews(self):
        closes = [200 + i for i in range(245)] + [445 - i * 4 for i in range(20)] + [365 + i for i in range(70)]
        bars = [{'open': c, 'high': c + 1, 'low': c - 1, 'close': c} for c in closes]
        for item in template_list()['items']:
            with self.subTest(template=item['id']):
                workflow = Workflow.model_validate(template_get(item['id']))
                definition = calculation_definition(workflow)
                result = execute(compile_graph(definition), Ctx(bars, 'H1'))
                self.assertTrue(result.ok, result.errors)
                targets = preview(workflow, bars)['tail']
                expected = ['HOLD' if target in ('WAIT', 'CONFLICT') else target for target in targets]
                self.assertEqual(result.outputs['target'][-len(expected):], expected)
                self.assertEqual(result.outputs['target'][0], 'HOLD')

    def test_conflicting_directions_hold_in_handoff(self):
        draft = example()
        draft['nodes'][4]['params']['operator'] = '>'
        workflow = Workflow.model_validate(draft)
        closes = range(100, 200)
        bars = [{'open': c, 'high': c + 1, 'low': c - 1, 'close': c} for c in closes]
        self.assertEqual(preview(workflow, bars)['latest'], 'CONFLICT')
        result = execute(compile_graph(calculation_definition(workflow)), Ctx(bars, 'H1'))
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.outputs['target'][-1], 'HOLD')

    def test_template_is_independent_copy(self):
        first = template_get('ema_trend_20_50')
        first['nodes'][1]['params']['period'] = 999
        self.assertEqual(template_get('ema_trend_20_50')['nodes'][1]['params']['period'], 20)

    def test_unknown_template(self):
        with self.assertRaises(KeyError):
            template_get('does_not_exist')

    def test_valid_graph(self):
        plan = compile_workflow(Workflow.model_validate(example()))
        self.assertTrue(plan['valid'])
        self.assertEqual(plan['execution_order'][0], 'bars')
        self.assertEqual(plan['execution_order'][-1], 'signal')

    def test_missing_required_input(self):
        data = example()
        data['edges'].pop()
        with self.assertRaisesRegex(ValueError, 'required input'):
            compile_workflow(Workflow.model_validate(data))

    def test_invalid_port_type(self):
        data = example()
        data['edges'][-1]['source_port'] = 'value'
        with self.assertRaisesRegex(ValueError, 'unknown port'):
            compile_workflow(Workflow.model_validate(data))

    def test_invalid_parameter(self):
        data = example()
        data['nodes'][1]['params']['period'] = 0
        with self.assertRaisesRegex(ValueError, 'outside supported range'):
            compile_workflow(Workflow.model_validate(data))

    def test_duplicate_ids(self):
        data = example()
        data['nodes'][1]['id'] = 'bars'
        with self.assertRaisesRegex(ValueError, 'Duplicate node IDs'):
            Workflow.model_validate(data)


if __name__ == '__main__':
    unittest.main()
