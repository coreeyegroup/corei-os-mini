"""Trading-core tests: strategy, allocation, child risk, paper workflow, market-data selection.

These cover the pure decision logic only. No database, broker or network is used.
"""
from datetime import datetime, timedelta, timezone
import unittest

from pydantic import ValidationError

from corei_os_mini.allocation import SELECT_ONE_POLICY, ProposedChild, allocate_children
from corei_os_mini.contracts import AccountCandidate, DispatchMode, PaperRunRequest, StrategyInput
from corei_os_mini.market_data import MarketDataAssessmentRequest, assess_market_data
from corei_os_mini.risk import check_child
from corei_os_mini.strategy import evaluate_demo_strategy
from corei_os_mini.workflow import run_paper_workflow

RISING = [100, 101, 102, 103, 104, 105]
FALLING = list(reversed(RISING))


def strategy(target=10, fast=2, slow=4):
    return StrategyInput(strategy_id='demo', fast_window=fast, slow_window=slow, target_quantity_units=target)


def account(account_id='A', **overrides):
    values = dict(account_id=account_id, current_position_units=0, max_order_quantity_units=1_000,
                  max_order_notional_units=10_000_000, max_position_units=1_000, risk_state_fresh=True)
    values.update(overrides)
    return AccountCandidate(**values)


def request(mode, accounts, prices=RISING, target=10):
    return PaperRunRequest(instrument_id='XAUUSD', price_scale=2, quantity_scale=2,
                           synthetic_prices_units=prices, dispatch_mode=mode,
                           strategy=strategy(target), accounts=accounts)


class StrategyTests(unittest.TestCase):
    def test_direction_and_hold(self):
        self.assertEqual(evaluate_demo_strategy(RISING, strategy()).action, 'BUY')
        self.assertEqual(evaluate_demo_strategy(FALLING, strategy()).action, 'SELL')
        self.assertEqual(evaluate_demo_strategy([100] * 6, strategy()).action, 'HOLD')

    def test_deterministic_for_same_input(self):
        first = evaluate_demo_strategy(RISING, strategy())
        self.assertEqual(first, evaluate_demo_strategy(list(RISING), strategy()))
        self.assertEqual(first.decision_price_units, 105)

    def test_rejects_short_history_and_bad_prices(self):
        with self.assertRaises(ValueError):
            evaluate_demo_strategy([100, 101], strategy())
        with self.assertRaises(ValueError):
            evaluate_demo_strategy([100, 0, 101, 102], strategy())

    def test_window_order_enforced(self):
        with self.assertRaises(ValidationError):
            strategy(fast=4, slow=4)


class AllocationTests(unittest.TestCase):
    def test_replicate_never_copies_parent_by_default(self):
        with self.assertRaises(ValidationError):
            request(DispatchMode.REPLICATE, [account('A'), account('B')])

    def test_replicate_uses_each_account_scale(self):
        accounts = [account('A', replicate_scale_bps=5_000), account('B', replicate_scale_bps=2_500)]
        result = allocate_children(DispatchMode.REPLICATE, accounts, strategy(target=10))
        self.assertEqual([c.quantity_units for c in result.children], [5, 2])

    def test_allocate_sums_exactly_to_target(self):
        accounts = [account('A', weight_bps=3_333), account('B', weight_bps=3_333), account('C', weight_bps=3_334)]
        result = allocate_children(DispatchMode.ALLOCATE, accounts, strategy(target=10))
        self.assertEqual(sum(c.quantity_units for c in result.children), 10)
        self.assertEqual([c.quantity_units for c in result.children], [3, 3, 4])

    def test_allocate_weights_must_total_10000(self):
        with self.assertRaises(ValidationError):
            request(DispatchMode.ALLOCATE, [account('A', weight_bps=5_000), account('B', weight_bps=4_000)])

    def test_select_one_is_deterministic_and_prefers_fresh_priority(self):
        accounts = [account('C', route_priority=5), account('A', route_priority=9, risk_state_fresh=False),
                    account('B', route_priority=5)]
        result = allocate_children(DispatchMode.SELECT_ONE, accounts, strategy())
        self.assertEqual([c.account.account_id for c in result.children], ['B'])
        self.assertEqual(result.route_policy, SELECT_ONE_POLICY)
        self.assertEqual(allocate_children(DispatchMode.SELECT_ONE, list(reversed(accounts)), strategy()).children,
                         result.children)

    def test_select_one_falls_back_to_stale_so_risk_shows_reason(self):
        accounts = [account('A', risk_state_fresh=False)]
        result = run_paper_workflow(request(DispatchMode.SELECT_ONE, accounts))
        self.assertEqual(result.children[0].risk_reasons, ['ACCOUNT_RISK_STATE_STALE'])

    def test_individual_requires_one_selected_account(self):
        with self.assertRaises(ValidationError):
            request(DispatchMode.INDIVIDUAL, [account('A', selected=True), account('B')])
        with self.assertRaises(ValidationError):
            request(DispatchMode.INDIVIDUAL, [account('A')])

    def test_mode_specific_fields_rejected_in_other_modes(self):
        cases = [
            (DispatchMode.ALLOCATE, dict(weight_bps=10_000, route_priority=1)),
            (DispatchMode.SELECT_ONE, dict(replicate_scale_bps=1)),
            (DispatchMode.REPLICATE, dict(replicate_scale_bps=10_000, weight_bps=10_000)),
            (DispatchMode.SELECT_ONE, dict(selected=True)),
        ]
        for mode, fields in cases:
            with self.subTest(mode=mode, fields=fields), self.assertRaises(ValidationError):
                request(mode, [account('A', **fields)])


class RiskTests(unittest.TestCase):
    def check(self, quantity=10, action='BUY', price=100, **overrides):
        return check_child(ProposedChild(account(**overrides), quantity), action, price)

    def test_approves_within_limits(self):
        self.assertTrue(self.check().approved)

    def test_each_limit_blocks_with_reason(self):
        cases = {
            'ACCOUNT_RISK_STATE_STALE': dict(risk_state_fresh=False),
            'MAX_ORDER_QUANTITY_EXCEEDED': dict(max_order_quantity_units=5),
            'MAX_POSITION_EXCEEDED': dict(current_position_units=995),
            'MAX_ORDER_NOTIONAL_EXCEEDED': dict(max_order_notional_units=999),
        }
        for reason, overrides in cases.items():
            with self.subTest(reason=reason):
                result = self.check(**overrides)
                self.assertFalse(result.approved)
                self.assertIn(reason, result.reasons)

    def test_short_blocked_unless_allowed(self):
        self.assertIn('SHORT_POSITION_NOT_ALLOWED', self.check(action='SELL').reasons)
        self.assertTrue(self.check(action='SELL', allow_short=True).approved)

    def test_zero_quantity_and_no_signal_blocked(self):
        self.assertIn('ZERO_ALLOCATED_QUANTITY', self.check(quantity=0).reasons)
        self.assertIn('NO_TRADE_SIGNAL', self.check(action='HOLD').reasons)

    def test_notional_uses_exact_integer_product(self):
        self.assertTrue(self.check(quantity=10, price=100, max_order_notional_units=1_000).approved)
        self.assertFalse(self.check(quantity=10, price=101, max_order_notional_units=1_000).approved)


class WorkflowTests(unittest.TestCase):
    def test_blocked_child_does_not_affect_or_feed_others(self):
        accounts = [account('A', weight_bps=5_000), account('B', weight_bps=5_000, risk_state_fresh=False)]
        result = run_paper_workflow(request(DispatchMode.ALLOCATE, accounts))
        by_id = {c.account_id: c for c in result.children}
        self.assertEqual(by_id['A'].order_status, 'SIMULATED_FILLED')
        self.assertEqual(by_id['A'].quantity_units, 5)  # B's blocked half is not redistributed
        self.assertEqual(by_id['B'].order_status, 'NOT_SUBMITTED')
        self.assertEqual(result.execution_mode, 'PAPER')

    def test_hold_submits_nothing(self):
        result = run_paper_workflow(request(DispatchMode.INDIVIDUAL, [account('A', selected=True)], prices=[100] * 6))
        self.assertEqual(result.decision, 'HOLD')
        self.assertTrue(all(c.order_status == 'NOT_SUBMITTED' for c in result.children))

    def test_same_input_same_outcome_except_run_id(self):
        build = lambda: request(DispatchMode.SELECT_ONE, [account('A'), account('B', route_priority=3)])
        first = run_paper_workflow(build()).model_dump(exclude={'run_id'})
        self.assertEqual(first, run_paper_workflow(build()).model_dump(exclude={'run_id'}))
        self.assertEqual(first['children'][0]['account_id'], 'B')


class MarketDataTests(unittest.TestCase):
    NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)

    def mapping(self, provider='P1', priority=0, enabled=True):
        return dict(provider_id=provider, connection_id='C-' + provider, native_symbol='XAUUSD',
                    instrument_id='XAUUSD', price_scale=2, enabled=enabled, priority=priority)

    def obs(self, provider='P1', age_ms=100, sequence=None, price=200_000, clock='HIGH', received_delay_ms=10):
        source = self.NOW - timedelta(milliseconds=age_ms)
        return dict(provider_id=provider, connection_id='C-' + provider, native_symbol='XAUUSD',
                    source_time=source, received_at=source + timedelta(milliseconds=received_delay_ms),
                    sequence=sequence, price_units=price, price_scale=2, clock_confidence=clock)

    def assess(self, mappings, observations, **extra):
        return assess_market_data(MarketDataAssessmentRequest(
            instrument_id='XAUUSD', price_scale=2, evaluated_at=self.NOW,
            mappings=mappings, observations=observations, **extra), 'test')

    def test_selects_priority_then_newest_and_keeps_all_sources(self):
        result = self.assess([self.mapping('P1', 1), self.mapping('P2', 5)],
                             [self.obs('P1', 50), self.obs('P2', 900), self.obs('P2', 400)])
        self.assertEqual(result.selected_observation_id, 'observation-0003')
        self.assertEqual(len(result.observations), 3)
        self.assertEqual({f.status for f in result.feeds}, {'READY'})

    def test_stale_unmapped_low_clock_and_disabled_excluded(self):
        result = self.assess([self.mapping('P1'), self.mapping('P2', enabled=False)],
                             [self.obs('P1', age_ms=5_000), self.obs('P3'), self.obs('P1', clock='LOW'), self.obs('P2')])
        reasons = [o.reasons for o in result.observations]
        self.assertIn('STALE_OBSERVATION', reasons[0])
        self.assertIn('NO_EXPLICIT_INSTRUMENT_MAPPING', reasons[1])
        self.assertIn('CLOCK_CONFIDENCE_INSUFFICIENT', reasons[2])
        self.assertIn('FEED_MAPPING_DISABLED', reasons[3])
        self.assertIsNone(result.selected_observation_id)
        self.assertEqual(result.selection_reason, 'NO_ELIGIBLE_OBSERVATION')
        self.assertEqual({f.provider_id: f.status for f in result.feeds}, {'P1': 'BLOCKED', 'P2': 'DISABLED'})

    def test_sequence_gap_and_regression(self):
        result = self.assess([self.mapping()], [self.obs(sequence=1, age_ms=300), self.obs(sequence=3, age_ms=200),
                                                self.obs(sequence=2, age_ms=100)])
        self.assertIn('SEQUENCE_GAP', result.observations[1].reasons)
        self.assertIn('SEQUENCE_NOT_INCREASING', result.observations[2].reasons)
        self.assertEqual(result.feeds[0].status, 'DEGRADED')

    def test_future_source_time_rejected(self):
        result = self.assess([self.mapping()], [self.obs(age_ms=-1_000, received_delay_ms=-1_000)])
        self.assertIn('SOURCE_TIME_IN_FUTURE', result.observations[0].reasons)

    def test_naive_timestamps_rejected(self):
        bad = self.obs()
        bad['source_time'] = bad['source_time'].replace(tzinfo=None)
        with self.assertRaises(ValidationError):
            self.assess([self.mapping()], [bad])


if __name__ == '__main__':
    unittest.main()
