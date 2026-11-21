"""Tests for the additive, non-executing strategy workflow compiler."""
import unittest

from corei_os_mini.strategy_workflow import Workflow, compile_workflow
from corei_os_mini.strategy_preview import preview


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
