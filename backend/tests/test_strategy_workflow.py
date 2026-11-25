"""Tests for the additive, non-executing strategy workflow compiler."""
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import os
from uuid import uuid4
import unittest
from unittest.mock import Mock, patch

import psycopg
from psycopg.rows import dict_row
from fastapi import HTTPException, Response
from pydantic import ValidationError

from corei_os_mini import strategy_scan_runs as runs
from corei_os_mini.calc.nodes import Ctx
from corei_os_mini.calc.run import compile_graph, execute
from corei_os_mini.strategy_drafts import calculation_definition
from corei_os_mini.strategy_workflow import Workflow, compile_workflow
from corei_os_mini.strategy_preview import preview
from corei_os_mini.strategy_studio import SignalScanIn, _plan_signal, scan_readiness, signal_scan
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
            (self.bars(103, prior=102), 2, 'No new entry trigger'),
            (self.bars(102), None, 'ATR14'),
            (self.bars(102), 0.0001, 'tick size'),
        ]:
            with self.subTest(message=message):
                result = _plan_signal(self.strategy, self.instrument, bars, [{'atr14': atr}] * 40, payload, self.now)
                self.assertEqual(result['status'], 'WAIT')
                self.assertIn(message, result['reason'])
                self.assertNotIn('entry', result)

    def test_recent_setup_keeps_original_levels_and_expires_on_touch_or_exit(self):
        payload = SignalScanIn(instrument_symbol='EURUSD', entry_lookback=3)
        features = [{'atr14': 2}] * 39 + [{'atr14': 4}]
        for prior, final, side, expected in [
            (102, 103, 'BUY', ('102.00', '99.00', '108.00')),
            (98, 97, 'SELL', ('98.00', '101.00', '92.00')),
        ]:
            with self.subTest(side=side):
                bars = self.bars(final, prior)
                bars[-1].update(open=prior, low=min(prior, final), high=max(prior, final))
                result = _plan_signal(self.strategy, self.instrument, bars, features, payload, self.now)
                self.assertEqual(result['status'], 'SIGNAL')
                self.assertEqual(result['side'], side)
                self.assertEqual(result['bars_since_trigger'], 1)
                self.assertEqual(result['strength'], 50)
                self.assertEqual(result['trigger_bar_time'], bars[-2]['bar_time'])
                self.assertEqual(tuple(result[k] for k in ('entry', 'stop_loss', 'take_profit')), expected)

        for final, low, high, reason in [
            (103, 98, 104, 'touched its stop or target'),
            (103, 102, 108, 'touched its stop or target'),
            (98, 98, 102, 'No active long or short setup'),
        ]:
            bars = self.bars(final, prior=102)
            bars[-1].update(low=low, high=high)
            result = _plan_signal(self.strategy, self.instrument, bars, features, payload, self.now)
            self.assertEqual(result['status'], 'WAIT')
            self.assertIn(reason, result['reason'])

    def test_invalid_scan_options(self):
        for data in ({}, {'instrument_symbol': 'EURUSD', 'strategy_code': 'BREAKOUT'},
                     {'instrument_symbol': 'EURUSD', 'strategy_codes': []},
                     {'strategy_code': 'BREAKOUT', 'strategy_codes': ['BREAKOUT']},
                     {'strategy_code': 'BREAKOUT', 'stop_atr': '0'},
                     {'strategy_code': 'BREAKOUT', 'entry_lookback': 21},
                     {'instrument_symbols': []},
                     {'instrument_symbols': ['EURUSD', 'bad symbol']},
                     {'instrument_symbol': 'EURUSD', 'instrument_symbols': ['XAUUSD']}):
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
                return Mock(fetchall=lambda: [i for i in instruments if not args[0] or i['symbol'] in args[0]])
            if 'FROM cfg_strategies' in sql:
                return Mock(fetchall=lambda: [s for s in strategies if not args[0] or s['code'] in args[0]])
            if 'FROM md_collection_plans' in sql:
                return Mock(fetchall=lambda: [s for s in sources if s['instrument_id'] in args[0]])
            self.fail(f'Unexpected SQL in a read-only scan: {sql}')

        def load(db, account, symbol, timeframe, limit=None, *, cutoff=None):
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
            self.assertEqual(result[0]['series'], 'NATIVE')
            self.assertGreaterEqual(result[0]['available_bars'], result[0]['required_bars'])
            self.assertIsNotNone(result[0]['bar_time'])
            self.assertEqual(result[2]['reason'], 'No mapped market-data source')
            result = signal_scan(SignalScanIn(instrument_symbol='EURUSD'), Response(), 'operator')['items']
            self.assertEqual([r['strategy_code'] for r in result], ['BREAKOUT', 'UNAPPROVED'])
            result = signal_scan(SignalScanIn(instrument_symbol='EURUSD', strategy_codes=['BREAKOUT']),
                                 Response(), 'operator')['items']
            self.assertEqual([r['strategy_code'] for r in result], ['BREAKOUT'])
            result = signal_scan(SignalScanIn(instrument_symbols=['EURUSD', 'XAUUSD'],
                                             strategy_codes=['BREAKOUT', 'UNAPPROVED']), Response(), 'operator')['items']
            self.assertEqual({(r['instrument_symbol'], r['strategy_code']) for r in result},
                             {(symbol, code) for symbol in ('EURUSD', 'XAUUSD') for code in ('BREAKOUT', 'UNAPPROVED')})
            result = signal_scan(SignalScanIn(strategy_codes=['BREAKOUT', 'UNAPPROVED']),
                                 Response(), 'operator')['items']
            self.assertEqual(len(result), 6)
            self.assertEqual({r['instrument_symbol'] for r in result}, {'EURUSD', 'XAUUSD', 'XAGUSD'})
            readiness = scan_readiness(SignalScanIn(strategy_codes=['BREAKOUT', 'UNAPPROVED']), Response(), 'operator')
            self.assertEqual((readiness['requested'], readiness['ready']), (6, 2))
            self.assertEqual(readiness['strategies'][0]['version'], 2)
            self.assertEqual(readiness['strategies'][0]['timeframe'], 'H1')
            self.assertTrue(all(call.args == ('read',) for call in transaction.call_args_list))


    def test_readiness_covers_full_matrix_and_reuses_bars(self):
        instruments = [{**self.instrument, 'id': f'I{i:03}', 'symbol': f'I{i:03}'} for i in range(101)]
        strategies = [{**self.strategy, 'code': f'S{i:03}'} for i in range(102)]
        strategies[-1].update(number=None, parameters=None)
        sources = [{'instrument_id': item['id'], 'account_id': 'account', 'account_code': 'DEMO',
                    'broker_symbol': item['symbol']} for item in instruments[:-1]]
        db = Mock()
        db.execute.return_value.fetchall.return_value = sources

        def load(_db, _account, _symbol, timeframe, _size=None, *, cutoff=None):
            self.assertIsNotNone(cutoff)
            return self.bars(102) if timeframe == 'H1' else []

        payload = SignalScanIn(strategy_codes=[item['code'] for item in strategies])
        with (patch('corei_os_mini.strategy_studio.store.transaction', return_value=nullcontext(db)),
              patch('corei_os_mini.strategy_studio.resolve_scan_scope', return_value=(instruments, strategies)),
              patch('corei_os_mini.strategy_studio.engine._load', side_effect=load) as load_bars,
              patch('corei_os_mini.strategy_studio.engine.features') as calculate):
            data = scan_readiness(payload, Response(), 'operator')
        self.assertEqual((data['requested'], data['ready']), (10_302, 10_100))
        self.assertEqual(data['items'][-1]['blocked'], 102)
        self.assertEqual(data['items'][0]['sources'], ['DEMO · I000'])
        self.assertEqual(load_bars.call_count, 200)
        calculate.assert_not_called()


@unittest.skipUnless(os.getenv('COREI_TEST_DATABASE_URL'), 'Requires a disposable PostgreSQL database')
class BackgroundScanTests(unittest.TestCase):
    def setUp(self):
        url = os.environ['COREI_TEST_DATABASE_URL']
        schema = 'test_scan_' + uuid4().hex
        migrations = Path(__file__).resolve().parents[1] / 'sql'

        @contextmanager
        def transaction(_operation='write'):
            with psycopg.connect(url, row_factory=dict_row, options=f'-c search_path={schema}') as db:
                yield db

        def cleanup():
            with psycopg.connect(url) as db:
                db.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')

        self.addCleanup(cleanup)
        with psycopg.connect(url) as db:
            db.execute(f'CREATE SCHEMA {schema}')
        with transaction() as db:
            for filename in ('041_studio_scan_runs.sql', '042_studio_scan_profiles.sql'):
                db.execute((migrations / filename).read_text())
        patcher = patch.object(runs.store, 'transaction', transaction)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_scan_profile_is_private_and_reloadable(self):
        profile = runs.ScanProfileIn(name='Market open', mode='strategy', all_instruments=True,
                                     request=SignalScanIn(strategy_codes=['S000'], entry_lookback=3))
        self.assertEqual(runs.save_profile(profile, 'operator-1'), {'name': 'Market open'})
        self.assertEqual(runs.profiles('operator-2'), {'items': []})
        saved = runs.profiles('operator-1')['items'][0]
        self.assertEqual(saved['request']['strategy_codes'], ['S000'])
        self.assertTrue(saved['all_instruments'])

    def test_persist_resume_rank_and_retry_at_ten_thousand_pairs(self):
        instruments = [{'symbol': f'I{i:03}'} for i in range(101)]
        strategies = [{'code': f'S{i:03}'} for i in range(102)]
        failed = True

        def evaluate(_db, batch, versions, _request, _as_of, *, freeze_bars):
            self.assertTrue(freeze_bars)
            self.assertEqual(len(batch), 1)
            return [
                {'instrument_symbol': batch[0]['symbol'], 'strategy_code': version['code'],
                 'status': 'ERROR' if failed and (batch[0]['symbol'], version['code']) == ('I000', 'S000') else 'SIGNAL',
                 'strength': int(batch[0]['symbol'][1:])}
                for version in versions
            ]

        payload = runs.ScanRunIn(mode='strategy', request=SignalScanIn(strategy_codes=['S000']))
        with patch.object(runs, 'resolve_scan_scope', return_value=(instruments, strategies)), \
             patch.object(runs, 'evaluate_scan_pairs', side_effect=evaluate) as evaluator:
            job = runs.enqueue(payload, 'operator-1')
            run_id = job['id']
            self.assertEqual(job['total'], 10_302)
            self.assertTrue(runs.step())
            self.assertEqual(runs.read_run(run_id, 'operator-1')['processed_instruments'], 1)
            self.assertEqual(runs.cancel(run_id, 'operator-1')['status'], 'CANCELLED')
            self.assertFalse(runs.step())
            with self.assertRaises(HTTPException) as unauthorized:
                runs.read_run(run_id, 'operator-2')
            self.assertEqual(unauthorized.exception.status_code, 404)
            runs.retry(run_id, 'operator-1')
            while runs.step():
                pass
            completed = runs.read_run(run_id, 'operator-1')
            self.assertEqual(completed['status'], 'COMPLETED')
            self.assertEqual(sum(row['checked'] for row in completed['summary']), 10_302)
            self.assertEqual(completed['summary'][0]['strongest'], ['I100'])
            page = runs.results(run_id, 'S000', 'SIGNAL', 50, 'operator-1')
            self.assertEqual(len(page['items']), 50)
            self.assertFalse(page['has_more'])
            self.assertEqual(page['items'][0]['instrument_symbol'], 'I050')

            failed = False
            evaluator.reset_mock()
            runs.retry(run_id, 'operator-1')
            while runs.step():
                pass
            self.assertEqual(evaluator.call_count, 1)
            summary = runs.read_run(run_id, 'operator-1')['summary']
            self.assertEqual(summary[0]['errors'], 0)
            self.assertEqual(summary[0]['signals'], 101)


if __name__ == '__main__':
    unittest.main()
