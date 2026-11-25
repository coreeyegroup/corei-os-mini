"""Setup gates (release 0.59.0, migration 050): eight steps per track, levels from off to hard, scoped time-boxed overrides.

Evidence for: gate status is computed from real data per track; a soft gate blocks order sending until fixed or
overridden by the person with a reason; a supervised override needs a second person; a hard gate takes no override
and is also checked at the dispatcher; an override for one account covers only that account, expires and can be
revoked; loosening a LIVE gate waits for a second person while tightening applies at once; activation and the workspace
plan honour the gates; the landing tells a firm whose target track is incomplete to open Setup.
"""
from __future__ import annotations

import importlib
import importlib.util
from datetime import datetime

te = importlib.import_module('test_execution') if importlib.util.find_spec('test_execution') else importlib.import_module('tests.test_execution')
tmd = importlib.import_module('test_market_data') if importlib.util.find_spec('test_market_data') else importlib.import_module('tests.test_market_data')
from corei_os_mini import config_store as store  # noqa: E402
from corei_os_mini.trading import setup_gates  # noqa: E402


class SetupGates(te.Execution):
    test_sending_disabled_during_precheck = None
    test_real_orders_on_demo_every_path = None
    test_four_eyes_on_live = None
    test_risk_extras = None
    test_portfolio_extras = None
    test_optimise_walk_forward_and_drift = None

    def gates(self, preset='ALL_OFF', client=None, status=200, **levels):
        body = {'preset': preset, 'levels': levels, 'reason': 'test gate levels'}
        return self.ok((client or self.alice).put('/api/trading/setup/policy', json=body), status)

    def risk_min(self):
        self.ok(self.alice.put('/api/trading/governance/risk_minimum', json={'profile': 'PROFESSIONAL', 'overrides': {}, 'reason': 'test policy'}))

    def demo_on(self, status):
        self.ok(self.alice.post('/api/operator/reauth', json={'password': tmd.PASSWORD}))
        ctl = {c['environment']: c for c in self.ok(self.alice.get('/api/config/execution-controls'))['items']}['DEMO']
        return self.ok(self.alice.put('/api/config/execution-controls/DEMO', json={'trading_enabled': True, 'reason': 'Start demo',
                                                                                  'version': ctl['version'], 'confirm_environment': 'DEMO'}), status)

    def test_status_is_computed_per_track(self):
        self.real_account('DEMO')
        st = self.ok(self.alice.get('/api/trading/setup'))
        self.assertEqual([len(st['tracks'][t]['gates']) for t in ('PAPER', 'DEMO', 'LIVE')], [5, 8, 8])
        demo = {g['id']: g for g in st['tracks']['DEMO']['gates']}
        self.assertEqual(demo['ORDER_SENDING']['state'], 'OPEN', 'DEMO order sending is off in a fresh install')
        self.assertTrue(all(i['fix'] and i['goto']['view'] for g in demo.values() for i in g['items']), 'every open item says how and where to fix it')
        live = {g['id']: g for g in st['tracks']['LIVE']['gates']}
        self.assertIn('No active alert channel', ' '.join(i['text'] for i in live['PEOPLE']['items']))

    def test_soft_gate_blocks_until_fixed_or_overridden(self):
        _c, a, _b, _ins = self.real_account('DEMO')
        self.risk_min()
        self.gates(DEMO={'RISK': 'SOFT'})
        refused = self.demo_on(409)
        self.assertIn('held by setup gates', str(refused))
        self.assertIn('no MAX_DAILY_LOSS', str(refused))
        self.ok(self.alice.post('/api/trading/setup/overrides', json={'gate': 'RISK', 'track': 'DEMO', 'hours': 200, 'reason': 'too long'}), 422)
        o = self.ok(self.alice.post('/api/trading/setup/overrides', json={'gate': 'RISK', 'track': 'DEMO', 'hours': 2,
                                                                          'reason': 'limits being sized today'}), 201)
        self.assertEqual((o['approved_by'], o['level']), ('alice', 'SOFT'), 'a soft override is granted by the person')
        self.demo_on(200)
        st = {g['id']: g for g in self.ok(self.alice.get('/api/trading/setup'))['tracks']['DEMO']['gates']}
        self.assertEqual(st['RISK']['state'], 'OVERRIDDEN')
        self.ok(self.alice.post(f"/api/trading/setup/overrides/{o['id']}/revoke", json={'reason': 'limits applied instead'}))
        st = {g['id']: g for g in self.ok(self.alice.get('/api/trading/setup'))['tracks']['DEMO']['gates']}
        self.assertEqual(st['RISK']['state'], 'BLOCKED')
        with self.connect() as db:
            actions = [r['action'] for r in db.execute("SELECT action FROM cfg_audit_log WHERE entity='setup gate' ORDER BY id").fetchall()]
        self.assertEqual(actions, ['GRANT_OVERRIDE', 'REVOKE_OVERRIDE'])

    def test_supervised_needs_a_second_person_and_hard_takes_none(self):
        _c, a, _b, _ins = self.real_account('DEMO')
        self.risk_min()
        self.gates(DEMO={'RISK': 'SUPERVISED'})
        o = self.ok(self.alice.post('/api/trading/setup/overrides', json={'gate': 'RISK', 'track': 'DEMO', 'reason': 'urgent hedge'}), 201)
        self.assertIsNone(o['approved_at'], 'pending until a second person approves')
        self.demo_on(409)
        self.ok(self.alice.post(f"/api/trading/setup/overrides/{o['id']}/approve"), 422)
        ok = self.ok(self.bob.post(f"/api/trading/setup/overrides/{o['id']}/approve"))
        self.assertEqual(ok['approved_by'], 'bob')
        self.assertEqual(int((datetime.fromisoformat(ok['expires_at']) - datetime.fromisoformat(ok['approved_at'])).total_seconds()), 24 * 3600)
        self.demo_on(200)
        self.gates(DEMO={'RISK': 'HARD'})
        refused = self.ok(self.alice.post('/api/trading/setup/overrides', json={'gate': 'RISK', 'track': 'DEMO', 'reason': 'please'}), 422)
        self.assertIn('hard gate', str(refused))
        with store.transaction('read') as db:
            reason = setup_gates.hard_reason(db, 'DEMO', a['id'])
        self.assertIn('SETUP_GATE_HARD', reason or '', 'a hard gate also holds orders at the dispatcher')
        self.assertIn('Risk', reason)

    def test_account_override_covers_only_that_account(self):
        _c, a, b, _ins = self.real_account('DEMO')
        self.risk_min()
        self.gates(DEMO={'RISK': 'SOFT'})
        self.ok(self.alice.post('/api/trading/setup/overrides', json={'gate': 'RISK', 'track': 'DEMO', 'account_id': a['id'],
                                                                     'reason': 'one account only'}), 201)
        with store.transaction('read') as db:
            self.assertEqual(setup_gates.check(db, 'DEMO', accounts=[a['id']])['blocking'], [])
            self.assertEqual([x['gate'] for x in setup_gates.check(db, 'DEMO', accounts=[b['id']])['blocking']], ['RISK'])
        with self.connect() as db:
            db.execute("UPDATE setup_overrides SET expires_at=now() - interval '1 minute'")
        setup_gates._cache.clear()
        with store.transaction('read') as db:
            self.assertEqual([x['gate'] for x in setup_gates.check(db, 'DEMO', accounts=[a['id']])['blocking']], ['RISK'], 'an expired override covers nothing')

    def test_loosening_live_waits_for_a_second_person(self):
        self.gates(LIVE={'RISK': 'HARD'})                                   # tightening: at once
        self.assertEqual(self.ok(self.alice.get('/api/trading/setup'))['policy']['levels']['LIVE']['RISK'], 'HARD')
        out = self.gates(LIVE={'RISK': 'WARN'})
        self.assertTrue(out['pending'])
        self.assertEqual(out['levels']['LIVE']['RISK'], 'HARD', 'still hard until approved')
        self.ok(self.alice.post('/api/trading/setup/policy/approve', json={'reason': 'my own change'}), 422)
        done = self.ok(self.bob.post('/api/trading/setup/policy/approve', json={'reason': 'reviewed with alice'}))
        self.assertEqual((done['levels']['LIVE']['RISK'], done['pending']), ('WARN', None))

    def test_activation_and_workspace_plan_honour_the_gates(self):
        connection, a, _b, ins = self.real_account('DEMO')
        self.risk_min()
        self.sending('DEMO')
        self.gates(DEMO={'RISK': 'SOFT'})
        s = self.ok(self.alice.post('/api/config/strategies', json={'code': 'SG_EMA', 'name': 'SG'}), 201)
        v = self.ok(self.alice.post(f"/api/config/strategies/{s['id']}/versions",
                                    json={'algorithm': 'EMA_CROSSOVER', 'parameters': {'timeframe': 'M5', 'fast': 3, 'slow': 5}}), 201)
        self.ok(self.bob.post(f"/api/config/strategy-versions/{v['id']}/approve", json={'version': v['version']}))
        cfg = {'input': {'sources': [connection['id']], 'instruments': [ins['id']]}, 'processing': {'strategies': [{'strategy_id': s['id']}]},
               'output': {'environment': 'DEMO', 'accounts': [a['id']]}}
        plan = self.ok(self.alice.post('/api/trading/desks/plan', json=cfg))
        self.assertIn('Setup gate: Risk (soft)', [c['title'] for c in plan['checks'] if c['status'] == 'FAIL'])
        with self.connect() as db:
            mapping = db.execute('SELECT id FROM cfg_symbol_mappings LIMIT 1').fetchone()
        dep = self.ok(self.alice.post('/api/config/deployments', json={'strategy_id': s['id'], 'strategy_version_id': v['id'], 'instrument_id': ins['id'],
                                                                       'account_id': a['id'], 'data_mapping_id': str(mapping['id']), 'mode': 'BROKER'}), 201)
        self.ok(self.bob.post(f"/api/config/deployments/{dep['id']}/activate", json={'version': dep['version'], 'reason': 'go demo'}), 409)
        self.ok(self.alice.post('/api/trading/setup/overrides', json={'gate': 'RISK', 'track': 'DEMO', 'reason': 'sizing limits after the open'}), 201)
        plan = self.ok(self.alice.post('/api/trading/desks/plan', json=cfg))
        self.assertTrue(any(c['title'].startswith('Setup gate: Risk overridden until') and c['status'] == 'WARN' for c in plan['checks']))
        self.ok(self.bob.post(f"/api/config/deployments/{dep['id']}/activate", json={'version': dep['version'], 'reason': 'go demo'}))

    def test_landing_until_the_target_track_is_complete(self):
        self.real_account('DEMO')
        land = self.ok(self.alice.get('/api/trading/setup/landing'))
        self.assertEqual((land['target_track'], land['land_on_setup']), ('DEMO', True))
        self.assertTrue(land['next']['fix'])
        rep = self.ok(self.alice.get('/api/trading/setup/report'))
        self.assertIn('history', rep)
