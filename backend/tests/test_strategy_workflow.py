"""Tests for the additive, non-executing strategy workflow compiler."""
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import Mock, patch

from fastapi import Response
from pydantic import ValidationError

from corei_os_mini.calc.nodes import Ctx
from corei_os_mini.calc.run import compile_graph, execute
from corei_os_mini.strategy_drafts import calculation_definition
from corei_os_mini.strategy_workflow import Workflow, compile_workflow
from corei_os_mini.strategy_preview import preview
from corei_os_mini.strategy_studio import SignalScanIn, _plan_signal, signal_scan
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


class SignalScanTests(unittest.TestCase):
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    strategy = {'code': 'BREAKOUT', 'number': 2, 'algorithm': 'DONCHIAN_BREAKOUT',
                'parameters': {'timeframe': 'H1', 'direction': 'BOTH', 'entry': 20, 'exit': 10}}
    instrument = {'id': 'fx', 'symbol': 'EURUSD', 'tick_size': Decimal('0.01'),
                  'contract_size': Decimal('10'), 'quote_currency': 'USD'}

    def bars(self, final, prior=100, end=None):
        end = end or self.now
        return [{'bar_time': end - timedelta(hours=40 - i), 'open': 100, 'high': max(101, close),
                 'low': min(99, close), 'close': close}
                for i, close in enumerate([100] * 38 + [prior, final])]

    def test_reference_plan_for_long_and_short(self):
        payload = SignalScanIn(strategy_code='BREAKOUT')
        features = [{'atr14': 2}] * 40
        for close, expected in [(102, ('BUY', 50, '102.00', '99.00', '108.00')),
                                (98, ('SELL', 50, '98.00', '101.00', '92.00'))]:
            with self.subTest(close=close):
                result = _plan_signal(self.strategy, self.instrument, self.bars(close), features, payload, self.now)
                self.assertEqual(result['status'], 'SIGNAL')
                self.assertEqual(tuple(result[k] for k in ('side', 'strength', 'entry', 'stop_loss', 'take_profit')), expected)
                self.assertEqual(result['risk_per_contract'], '30')
                self.assertEqual(result['reward_risk'], '2.00')

    def test_wait_when_stale_holding_or_protection_unavailable(self):
        payload = SignalScanIn(instrument_symbol='EURUSD')
        for bars, atr, message in [
            (self.bars(102, end=self.now - timedelta(hours=3)), 2, 'freshness'),
            (self.bars(103, prior=102), 2, 'no new entry trigger'),
            (self.bars(102), None, 'ATR14'),
            (self.bars(102), 0.0001, 'tick size'),
        ]:
            with self.subTest(message=message):
                result = _plan_signal(self.strategy, self.instrument, bars, [{'atr14': atr}] * 40, payload, self.now)
                self.assertEqual(result['status'], 'WAIT')
                self.assertIn(message, result['reason'])
                self.assertNotIn('entry', result)

    def test_invalid_scan_options(self):
        for data in ({}, {'instrument_symbol': 'EURUSD', 'strategy_code': 'BREAKOUT'},
                     {'instrument_symbol': 'EURUSD', 'strategy_codes': []},
                     {'strategy_code': 'BREAKOUT', 'strategy_codes': ['BREAKOUT']},
                     {'strategy_code': 'BREAKOUT', 'stop_atr': '0'}):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                SignalScanIn.model_validate(data)

    def test_both_scan_directions_rank_signals_without_deployments(self):
        instruments = [self.instrument, {**self.instrument, 'id': 'gold', 'symbol': 'XAUUSD'},
                       {**self.instrument, 'id': 'silver', 'symbol': 'XAGUSD'}]
        strategies = [self.strategy, {**self.strategy, 'code': 'UNAPPROVED', 'number': None, 'parameters': None}]
        sources = [
            {'instrument_id': 'fx', 'account_id': '1', 'broker_symbol': 'EURUSD', 'account_code': 'FX'},
            {'instrument_id': 'gold', 'account_id': '2', 'broker_symbol': 'GOLD', 'account_code': 'METALS'},
            {'instrument_id': 'gold', 'account_id': '3', 'broker_symbol': 'STALE', 'account_code': 'OLD'},
        ]

        def execute(sql, args):
            if 'FROM cfg_instruments' in sql:
                return Mock(fetchall=lambda: [i for i in instruments if not args[0] or i['symbol'] == args[0]])
            if 'FROM cfg_strategies' in sql:
                return Mock(fetchall=lambda: [s for s in strategies if not args[0] or s['code'] in args[0]])
            if 'FROM md_collection_plans' in sql:
                return Mock(fetchall=lambda: [s for s in sources if s['instrument_id'] in args[0]])
            self.fail(f'Unexpected SQL in a read-only scan: {sql}')

        def load(db, account, symbol, timeframe, limit=None):
            if timeframe == 'M1':
                return []
            close = {'EURUSD': 102, 'GOLD': 95, 'STALE': 105}[symbol]
            return self.bars(close, end=self.now - timedelta(hours=3) if symbol == 'STALE' else self.now)

        db = Mock(execute=execute)
        with (patch('corei_os_mini.strategy_studio.store.transaction', return_value=nullcontext(db)) as transaction,
              patch('corei_os_mini.strategy_studio.engine._load', side_effect=load),
              patch('corei_os_mini.strategy_studio.engine.custom_definitions', return_value=[])):
            response = Response()
            result = signal_scan(SignalScanIn(strategy_code='BREAKOUT'), response, 'operator')['items']
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual([(r['instrument_symbol'], r['status']) for r in result],
                             [('XAUUSD', 'SIGNAL'), ('EURUSD', 'SIGNAL'), ('XAGUSD', 'WAIT')])
            self.assertGreater(result[0]['strength'], result[1]['strength'])
            self.assertEqual(result[0]['account_code'], 'METALS')
            self.assertEqual(result[2]['reason'], 'No mapped market-data source')
            result = signal_scan(SignalScanIn(instrument_symbol='EURUSD'), Response(), 'operator')['items']
            self.assertEqual([r['strategy_code'] for r in result], ['BREAKOUT', 'UNAPPROVED'])
            result = signal_scan(SignalScanIn(instrument_symbol='EURUSD', strategy_codes=['BREAKOUT']),
                                 Response(), 'operator')['items']
            self.assertEqual([r['strategy_code'] for r in result], ['BREAKOUT'])
            self.assertTrue(all(call.args == ('read',) for call in transaction.call_args_list))


if __name__ == '__main__':
    unittest.main()
