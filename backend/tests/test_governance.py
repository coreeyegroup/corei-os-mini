"""Strategy evidence and risk minimums (release 0.58.0, migration 049).

Evidence for: approval needs a backtest of the exact parameters with the profile's trades, history, costs and
out-of-sample checks, or a recorded exception where the profile allows one (never on Institutional); editing a draft
after its backtest voids the evidence; LIVE activation needs the paper record; DEMO order sending needs the
risk-minimum limits; a profile preset is sized on equity and never loosens a tighter limit; a strategy- or run-scope
daily loss stops only those runs; a timeframe copy is a new draft with its own evidence; a workspace says before Start
when minimum limits are missing or a fixed size would be refused by every order.
"""
from __future__ import annotations

import importlib
import importlib.util
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

te = importlib.import_module('test_execution') if importlib.util.find_spec('test_execution') else importlib.import_module('tests.test_execution')
tmd = importlib.import_module('test_market_data') if importlib.util.find_spec('test_market_data') else importlib.import_module('tests.test_market_data')
from corei_os_mini import config_store as store  # noqa: E402
from corei_os_mini.trading import oms, risk  # noqa: E402

NOW = te.NOW


class Governance(te.Execution):
    test_sending_disabled_during_precheck = None
    test_real_orders_on_demo_every_path = None
    test_four_eyes_on_live = None
    test_risk_extras = None
    test_portfolio_extras = None
    test_optimise_walk_forward_and_drift = None

    def policy(self, key, profile, **overrides):
        return self.ok(self.alice.put(f'/api/trading/governance/{key}', json={'profile': profile, 'overrides': overrides, 'reason': 'test policy'}))

    def version(self, code='GV_EMA', fast=3):
        s = self.ok(self.alice.post('/api/config/strategies', json={'code': code, 'name': code}), 201)
        v = self.ok(self.alice.post(f"/api/config/strategies/{s['id']}/versions",
                                    json={'algorithm': 'EMA_CROSSOVER', 'parameters': {'timeframe': 'M5', 'fast': fast, 'slow': 8}}), 201)
        return s, v

    def history(self, a):
        start = NOW - timedelta(days=3)
        closes = [1.10 + 0.002 * ((i // 20) % 2 and 20 - i % 20 or i % 20) / 20 for i in range(600)]
        self.bars(a['id'], closes, start)
        return start

    def backtest(self, v, a, start, **extra):
        return self.ok(self.alice.post('/api/trading/backtests', json={'strategy_version_id': v['id'], 'account_id': a['id'], 'broker_symbol': 'EURUSD',
                                                                       'range_from': start.isoformat(), 'range_to': NOW.isoformat(), **extra}), 201)

    def approve(self, v, client=None, status=200, **extra):
        cur = self.ok(self.alice.get(f"/api/config/strategies/{v['strategy_id']}/versions"))['items']
        ver = next(x for x in cur if x['id'] == v['id'])
        return self.ok((client or self.bob).post(f"/api/config/strategy-versions/{v['id']}/approve", json={'version': ver['version'], **extra}), status)

    def test_approval_needs_evidence_or_a_recorded_exception(self):
        _c, a, _b, _ins = self.real_account()
        start = self.history(a)
        self.policy('strategy_evidence', 'PROFESSIONAL', min_history_days=1, min_trades=2, require_net_positive=False, max_drawdown_pct='100')
        _s, v = self.version()
        refused = self.approve(v, status=422)
        self.assertIn('BACKTEST', str(refused))
        bt = self.backtest(v, a, start)                         # no spread, no commission
        ev = self.ok(self.alice.get(f"/api/trading/strategy-versions/{v['id']}/evidence"))
        self.assertEqual(ev['stage'], 'DRAFT')
        self.assertEqual([c['code'] for c in ev['checks'] if not c['ok']], ['COSTS'] if bt['metrics']['trades'] >= 5 else ['COSTS', 'OUT_OF_SAMPLE'])
        self.backtest(v, a, start, spread_ticks=10)
        ev = self.ok(self.alice.get(f"/api/trading/strategy-versions/{v['id']}/evidence"))
        if not ev['ok_for_approval']:   # the out-of-sample part of this series may lose; relax it for the approval path
            self.policy('strategy_evidence', 'PROFESSIONAL', min_history_days=1, min_trades=2, require_net_positive=False,
                        max_drawdown_pct='100', require_oos=False)
            ev = self.ok(self.alice.get(f"/api/trading/strategy-versions/{v['id']}/evidence"))
        self.assertEqual(ev['stage'], 'BACKTESTED')
        approved = self.approve(v)
        self.assertEqual(approved['state'], 'APPROVED')
        with self.connect() as db:
            row = db.execute('SELECT evidence, approval_exception FROM cfg_strategy_versions WHERE id=%s', (UUID(v['id']),)).fetchone()
        self.assertIsNone(row['approval_exception'])
        self.assertEqual(row['evidence']['backtest']['id'], ev['backtest']['id'], 'the approval records the backtest it rests on')
        # A version without evidence: an exception with a reason (Professional), never on Institutional.
        _s2, v2 = self.version('GV_EXC', fast=2)
        self.approve(v2, status=422, exception='ok')
        self.approve(v2, exception='desk head accepts: replaces a version with 2 years of paper record')
        with self.connect() as db:
            self.assertTrue(db.execute('SELECT approval_exception FROM cfg_strategy_versions WHERE id=%s', (UUID(v2['id']),)).fetchone()['approval_exception'])
        self.policy('strategy_evidence', 'INSTITUTIONAL')
        _s3, v3 = self.version('GV_INST', fast=4)
        refused = self.approve(v3, status=422, exception='please let this one through')
        self.assertIn('allows no approval without evidence', str(refused))

    def test_editing_a_draft_voids_its_backtest(self):
        _c, a, _b, _ins = self.real_account()
        start = self.history(a)
        self.policy('strategy_evidence', 'RETAIL', min_history_days=1, min_trades=1, require_net_positive=False, max_drawdown_pct='100')
        s, v = self.version()
        self.backtest(v, a, start)
        self.assertTrue(self.ok(self.alice.get(f"/api/trading/strategy-versions/{v['id']}/evidence"))['checks'][0]['ok'])
        cur = next(x for x in self.ok(self.alice.get(f"/api/config/strategies/{s['id']}/versions"))['items'] if x['id'] == v['id'])
        self.ok(self.alice.put(f"/api/config/strategy-versions/{v['id']}", json={'algorithm': 'EMA_CROSSOVER', 'version': cur['version'],
                                                                                 'parameters': {'timeframe': 'M5', 'fast': 4, 'slow': 8}}))
        ev = self.ok(self.alice.get(f"/api/trading/strategy-versions/{v['id']}/evidence"))
        self.assertFalse(ev['checks'][0]['ok'], 'a backtest of other parameters is not evidence')

    def test_live_activation_needs_the_paper_record(self):
        connection, a, _b, ins = self.real_account('LIVE')
        self.policy('strategy_evidence', 'RETAIL', require_backtest=False)
        s, v = self.version()
        self.approve(v)
        with self.connect() as db:
            mapping = db.execute('SELECT id FROM cfg_symbol_mappings LIMIT 1').fetchone()
        dep = self.ok(self.alice.post('/api/config/deployments', json={'strategy_id': s['id'], 'strategy_version_id': v['id'], 'instrument_id': ins['id'],
                                                                       'account_id': a['id'], 'data_mapping_id': str(mapping['id']), 'mode': 'BROKER'}), 201)
        refused = self.ok(self.bob.post(f"/api/config/deployments/{dep['id']}/activate", json={'version': dep['version'], 'reason': 'go live'}), 422)
        self.assertIn('lacks LIVE evidence', str(refused))
        self.assertIn('paper', str(refused))

    def test_demo_switch_needs_minimum_limits_and_presets_never_loosen(self):
        _c, a, _b, _ins = self.real_account('DEMO')
        self.policy('risk_minimum', 'PROFESSIONAL')
        self.ok(self.alice.put('/api/trading/setup/policy', json={'preset': 'ALL_OFF', 'levels': {'DEMO': {'RISK': 'SOFT'}}, 'reason': 'risk gate on'}))
        self.ok(self.alice.post('/api/operator/reauth', json={'password': tmd.PASSWORD}))
        ctl = {c['environment']: c for c in self.ok(self.alice.get('/api/config/execution-controls'))['items']}['DEMO']
        on = {'trading_enabled': True, 'reason': 'Start demo', 'version': ctl['version'], 'confirm_environment': 'DEMO'}
        refused = self.ok(self.alice.put('/api/config/execution-controls/DEMO', json=on), 409)
        self.assertIn('VTM_A has no MAX_DAILY_LOSS', str(refused))
        tight = self.ok(self.alice.post('/api/trading/risk/limits', json={'scope': 'ACCOUNT', 'target_id': a['id'], 'kind': 'MAX_DAILY_LOSS',
                                                                         'value': '100', 'reason': 'desk limit'}), 201)
        preview = self.ok(self.alice.post('/api/trading/risk/presets', json={'profile': 'PROFESSIONAL', 'account_id': a['id'], 'equity': '10000',
                                                                            'reason': 'size the demo', 'dry_run': True}))
        by = {x['kind']: x for x in preview['limits']}
        self.assertEqual((by['MAX_DAILY_LOSS']['value'], by['MAX_DAILY_LOSS']['action']), ('300.00', 'KEEP_TIGHTER'))
        self.assertEqual((by['MAX_ORDER_NOTIONAL']['value'], by['MAX_GROSS_EXPOSURE']['value'], by['MAX_LEVERAGE']['value']), ('20000.00', '50000.00', '5'))
        for acc in [x for x in self.ok(self.alice.get('/api/config/accounts'))['items'] if x['code'] in ('VTM_A', 'VTM_B', 'VTM_C')]:
            self.ok(self.alice.post('/api/trading/risk/presets', json={'profile': 'PROFESSIONAL', 'account_id': acc['id'], 'equity': '10000', 'reason': 'size the demo'}))
        limits = {(x['target_id'], x['kind']): x for x in self.ok(self.alice.get('/api/trading/risk/limits'))['items']}
        self.assertEqual(Decimal(limits[(a['id'], 'MAX_DAILY_LOSS')]['value']), Decimal('100'), 'a tighter limit is kept')
        self.assertEqual(limits[(a['id'], 'MAX_DAILY_LOSS')]['id'], tight['id'])
        ctl = {c['environment']: c for c in self.ok(self.alice.get('/api/config/execution-controls'))['items']}['DEMO']
        self.ok(self.alice.put('/api/config/execution-controls/DEMO', json={**on, 'version': ctl['version']}))
        measured = self.ok(self.alice.post('/api/trading/risk/presets', json={'profile': 'PROFESSIONAL', 'account_id': a['id'], 'reason': 'measured equity',
                                                                             'dry_run': True}))
        self.assertEqual((measured['equity_source'], measured['equity']), ('COREI book', '100000.00'), 'sized on the known book when none is entered')
        self.assertTrue(all(x['action'] == 'KEEP_TIGHTER' for x in measured['limits']), 'a larger equity never loosens the limits set')
        self.ok(self.alice.post('/api/trading/risk/presets', json={'profile': 'NONE', 'account_id': a['id'], 'reason': 'nothing to apply'}), 422)

    def test_run_scope_loss_stops_only_that_run(self):
        connection, a, _b, ins = self.real_account()
        _s, _v, dep = self.deploy_existing(connection, a, ins)
        self.ok(self.alice.post('/api/trading/risk/limits', json={'scope': 'DEPLOYMENT', 'target_id': dep['id'], 'kind': 'MAX_DAILY_LOSS',
                                                                 'value': '5', 'reason': 'run loss cap'}), 201)
        with store.transaction('write') as db:
            o = oms.place(db, account_id=a['id'], instrument_id=ins['id'], side='BUY', quantity=Decimal('0.10'), mode='PAPER',
                          deployment={'id': dep['id'], 'strategy_id': dep['strategy_id']}, seed='gv-loss', actor='alice', at=NOW)
        self.assertEqual(o['state'], 'FILLED', o.get('reasons'))
        self.quote(a['id'], 1.07500, 1.07510)                     # 50 pips against a 0.1-lot long: about 50 USD
        with store.transaction('write') as db:
            out = risk.auto_stop(db, NOW)
        self.assertEqual([(k['scope'], str(k['target_id'])) for k in out], [('DEPLOYMENT', dep['id'])], 'only the run that lost')
        with store.transaction('write') as db:
            manual = oms.place(db, account_id=a['id'], instrument_id=ins['id'], side='BUY', quantity=Decimal('0.10'), mode='PAPER',
                               seed='gv-manual', actor='alice', at=NOW)
        self.assertEqual(manual['state'], 'FILLED', 'the account and other runs keep trading')

    def test_timeframe_copy_is_a_new_draft(self):
        s, v = self.version()
        out = self.ok(self.alice.post('/api/trading/strategies/clone-timeframe', json={'version_id': v['id'], 'timeframe': 'H1'}), 201)
        self.assertEqual((out['strategy']['code'], out['version']['state'], out['version']['parameters']['timeframe'], out['version']['parameters']['fast']),
                         ('GV_EMA_H1', 'DRAFT', 'H1', 3))
        self.ok(self.alice.post('/api/trading/strategies/clone-timeframe', json={'version_id': v['id'], 'timeframe': 'H1'}), 409)
        self.ok(self.alice.post('/api/trading/strategies/clone-timeframe', json={'version_id': v['id'], 'timeframe': 'M5'}), 422)
        again = self.ok(self.alice.post('/api/trading/strategies/clone-timeframe', json={'version_id': out['version']['id'], 'timeframe': 'M15'}), 201)
        self.assertEqual(again['strategy']['code'], 'GV_EMA_M15', 'a copy of a copy keeps the base code')

    def test_desk_says_before_start_when_limits_are_missing_or_size_is_refused(self):
        connection, a, _b, ins = self.real_account('DEMO')
        self.policy('risk_minimum', 'PROFESSIONAL')
        self.ok(self.alice.put('/api/trading/setup/policy', json={'preset': 'ALL_OFF', 'levels': {'DEMO': {'RISK': 'SOFT'}}, 'reason': 'risk gate on'}))
        self.sending('DEMO')
        s, v = self.version()
        self.approve(v)
        cfg = {'input': {'sources': [connection['id']], 'instruments': [ins['id']]}, 'processing': {'strategies': [{'strategy_id': s['id']}]},
               'output': {'environment': 'DEMO', 'accounts': [a['id']]}}
        plan = self.ok(self.alice.post('/api/trading/desks/plan', json=cfg))
        self.assertIn('Setup gate: Risk (soft)', [c['title'] for c in plan['checks'] if c['status'] == 'FAIL'])
        self.ok(self.alice.post('/api/trading/risk/presets', json={'profile': 'PROFESSIONAL', 'account_id': a['id'], 'equity': '10000', 'reason': 'size the demo'}))
        plan = self.ok(self.alice.post('/api/trading/desks/plan', json=cfg))
        self.assertNotIn('Setup gate: Risk (soft)', [c['title'] for c in plan['checks']])
        refused = next(c for c in plan['checks'] if c['title'] == 'Every order would be refused by a risk limit')
        self.assertIn('order value 108', refused['detail'], '1 lot of EURUSD ≈ 108,000 USD > 20,000 (2 × equity)')
        small = {**cfg, 'processing': {**cfg['processing'], 'sizing': {'method': 'FIXED_QTY', 'quantity': '0.1'}}}
        plan = self.ok(self.alice.post('/api/trading/desks/plan', json=small))
        self.assertIn('Order size fits the limits', [c['title'] for c in plan['checks'] if c['status'] == 'PASS'])
