"""Workspace control desks (release 0.57.0, migration 048): a shared, owner-held workspace runs Input → Processing → Output.

Evidence for: plan and checks (every problem with why and a fix); Start creates runs the desk owns and adopts existing
deployments only with confirmation (unconfirmed ones are watched, untouched); one controller per deployment across desks;
Pause / Resume / Stop act only on the desk's own runs; the stop policy cancels working orders but never an UNKNOWN one and
closes positions only when confirmed; staged changes leave the running settings alone until applied; LIVE and DEMO order
sending are read, never switched; on LIVE a second person starts; emergency stop engages scoped kill switches; private
desks are invisible to others; a saved per-user layout imports; every change is audited.
"""
from __future__ import annotations

import importlib
import importlib.util
from decimal import Decimal
from uuid import UUID, uuid4

te = importlib.import_module('test_execution') if importlib.util.find_spec('test_execution') else importlib.import_module('tests.test_execution')
from corei_os_mini import config_store as store  # noqa: E402
from corei_os_mini.operator_auth import hash_password  # noqa: E402
from corei_os_mini.trading import oms  # noqa: E402

tmd = importlib.import_module('test_market_data') if importlib.util.find_spec('test_market_data') else importlib.import_module('tests.test_market_data')


class WorkspaceControl(te.Execution):
    test_sending_disabled_during_precheck = None
    test_real_orders_on_demo_every_path = None
    test_four_eyes_on_live = None
    test_risk_extras = None
    test_portfolio_extras = None
    test_optimise_walk_forward_and_drift = None

    def strategy(self, code='WS_EMA', timeframe='M5'):
        s = self.ok(self.alice.post('/api/config/strategies', json={'code': code, 'name': f'{code} test'}), 201)
        v = self.ok(self.alice.post(f"/api/config/strategies/{s['id']}/versions",
                                    json={'algorithm': 'EMA_CROSSOVER', 'parameters': {'timeframe': timeframe, 'fast': 3, 'slow': 5}}), 201)
        self.ok(self.bob.post(f"/api/config/strategy-versions/{v['id']}/approve", json={'version': v['version']}))
        return s, v

    def setup_desk(self, environment='PAPER', account_env='DEMO', name='Gold desk', client=None, **output):
        connection, a, b, ins = self.real_account(account_env)
        s, v = self.strategy()
        cfg = {'input': {'sources': [connection['id']], 'instruments': [ins['id']], 'timeframes': ['M5']},
               'processing': {'strategies': [{'strategy_id': s['id']}]},
               'output': {'environment': environment, 'accounts': [a['id']], **output}}
        desk = self.ok((client or self.alice).post('/api/trading/desks', json={'name': name, 'config': cfg}), 201)
        return desk, cfg, (connection, a, b, ins, s, v)

    def desk(self, desk_id, client=None):
        return self.ok((client or self.alice).get(f'/api/trading/desks/{desk_id}'))

    def deps(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM cfg_deployments ORDER BY created_at').fetchall()]

    def test_plan_checks_explain_and_start_creates_owned_runs(self):
        empty = self.ok(self.alice.post('/api/trading/desks/plan', json={}))
        self.assertFalse(empty['ready'])
        self.assertTrue(all(c['fix'] for c in empty['checks'] if c['status'] == 'FAIL'), 'every failed check offers a fix')
        desk, _cfg, (_c, a, _b, ins, s, v) = self.setup_desk(handoff='APPROVE')
        plan = self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/validate"))
        self.assertTrue(plan['ready'], plan['checks'])
        self.assertEqual([r['action'] for r in plan['runs']], ['CREATE'])
        self.assertIn('Market data', {c['stage'] for c in plan['checks'] if c['status'] == 'WARN'}, 'no collection plan is a warning')
        started = self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/start", json={'version': desk['version']}))
        self.assertEqual(started['state'], 'RUNNING')
        dep = self.deps()[0]
        self.assertEqual((dep['status'], dep['mode'], dep['execution']['handoff'], str(dep['strategy_version_id'])),
                         ('ACTIVE', 'PAPER', 'APPROVE', v['id']))
        detail = self.desk(desk['id'])
        self.assertEqual([(r['role'], r['status']) for r in detail['runs']], [('OWNED', 'ACTIVE')])
        with self.connect() as db:
            actions = [r['action'] for r in db.execute("SELECT action FROM cfg_audit_log WHERE entity IN ('workspace','deployment') ORDER BY id").fetchall()]
        self.assertEqual(actions, ['CREATE', 'PROPOSE', 'ACTIVATE', 'START'])
        self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/start", json={'version': started['version']}), 409)

    def test_observe_records_signals_only(self):
        desk, _cfg, _ = self.setup_desk('OBSERVE', handoff='AUTO')
        self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/start", json={'version': desk['version']}))
        self.assertEqual(self.deps()[0]['execution']['handoff'], 'SIGNAL_ONLY', 'Observe never orders, whatever the hand-off')

    def test_one_controller_per_run_and_adoption_needs_confirmation(self):
        desk, cfg, (connection, a, _b, ins, s, _v) = self.setup_desk()
        self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/start", json={'version': desk['version']}))
        second = self.ok(self.alice.post('/api/trading/desks', json={'name': 'Second', 'config': cfg}), 201)
        plan = self.ok(self.alice.post(f"/api/trading/desks/{second['id']}/validate"))
        self.assertEqual([(r['action'], r['controlled_by']) for r in plan['runs']], [('WATCH', 'Gold desk')])
        self.assertFalse(plan['ready'], 'nothing to run: the only run is controlled elsewhere')
        with self.connect() as db:
            with self.assertRaises(Exception):
                db.execute("INSERT INTO ws_runs(workspace_id, deployment_id, role, added_by) VALUES (%s,%s,'ADOPTED','x')",
                           (UUID(second['id']), self.deps()[0]['id']))
        # An existing deployment outside any desk: watched unless adoption is confirmed.
        s2, _ = self.strategy('WS_OTHER')
        self.deploy_existing_for(s2, a, ins)
        cfg2 = {**cfg, 'processing': {'strategies': [{'strategy_id': s2['id']}]}}
        third = self.ok(self.alice.post('/api/trading/desks', json={'name': 'Third', 'config': cfg2}), 201)
        plan = self.ok(self.alice.post(f"/api/trading/desks/{third['id']}/validate"))
        self.assertEqual([r['action'] for r in plan['runs']], ['ADOPT'])
        target = plan['runs'][0]['deployment_id']
        before = next(d for d in self.deps() if str(d['id']) == target)
        self.ok(self.alice.post(f"/api/trading/desks/{third['id']}/start", json={'version': third['version']}), 422)
        after = next(d for d in self.deps() if str(d['id']) == target)
        self.assertEqual((after['version'], after['status']), (before['version'], before['status']), 'not adopted: untouched')
        self.assertEqual(self.desk(third['id'])['runs'], [], 'nothing would run: refused, not started empty')
        t = self.desk(third['id'])
        self.ok(self.alice.post(f"/api/trading/desks/{third['id']}/start", json={'version': t['version'], 'adopt': [target]}))
        self.assertEqual([r['role'] for r in self.desk(third['id'])['runs']], ['ADOPTED'])
        self.ok(self.alice.post(f"/api/trading/desks/{third['id']}/start", json={'version': self.desk(third['id'])['version'], 'adopt': [str(uuid4())]}), 409)

    def deploy_existing_for(self, s, a, ins):
        with self.connect() as db:
            mapping = db.execute('SELECT id FROM cfg_symbol_mappings LIMIT 1').fetchone()
            ver = db.execute("SELECT id FROM cfg_strategy_versions WHERE strategy_id=%s AND state='APPROVED'", (UUID(s['id']),)).fetchone()
        dep = self.ok(self.alice.post('/api/config/deployments', json={'strategy_id': s['id'], 'strategy_version_id': str(ver['id']), 'instrument_id': ins['id'],
                                                                       'account_id': a['id'], 'data_mapping_id': str(mapping['id'])}), 201)
        return self.ok(self.bob.post(f"/api/config/deployments/{dep['id']}/activate", json={'version': dep['version'], 'reason': 'existing run'}))

    def test_pause_resume_stop_policies_never_touch_unknown(self):
        desk, _cfg, (_c, a, _b, ins, s, _v) = self.setup_desk()
        d = self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/start", json={'version': desk['version']}))
        dep = self.deps()[0]
        d = self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/pause", json={'version': d['version'], 'reason': 'lunch break'}))
        self.assertEqual((d['state'], self.deps()[0]['status']), ('PAUSED', 'PAUSED'))
        d = self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/resume", json={'version': d['version'], 'reason': 'back again'}))
        self.assertEqual((d['state'], self.deps()[0]['status']), ('RUNNING', 'ACTIVE'))
        # A position of this desk's run, and a broker order of it whose outcome is unknown.
        with store.transaction('write') as db:
            filled = oms.place(db, account_id=a['id'], instrument_id=ins['id'], side='BUY', quantity=Decimal('0.10'), mode='PAPER',
                               deployment={'id': dep['id'], 'strategy_id': dep['strategy_id']}, seed='ws-pos', actor='alice', at=te.NOW)
            unknown = oms.place(db, account_id=a['id'], instrument_id=ins['id'], side='BUY', quantity=Decimal('0.10'), mode='BROKER',
                                deployment={'id': dep['id'], 'strategy_id': dep['strategy_id']}, seed='ws-unknown', actor='alice', at=te.NOW)
            db.execute("UPDATE oms_orders SET state='UNKNOWN' WHERE id=%s", (unknown['id'],))
        self.assertEqual(filled['state'], 'FILLED')
        self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/stop",
                                json={'version': d['version'], 'reason': 'end of day', 'policy': 'CLOSE_POSITIONS'}), 422)
        out = self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/stop",
                                      json={'version': d['version'], 'reason': 'end of day', 'policy': 'CLOSE_POSITIONS', 'confirm_close': True}))
        self.assertEqual(out['state'], 'STOPPED')
        self.assertEqual(out['result']['unknown'], [str(unknown['id'])])
        self.assertNotIn(str(unknown['id']), out['result']['cancelled'])
        self.assertEqual(self.order(unknown['id'])['state'], 'UNKNOWN', 'an unknown outcome is resolved, never cancelled blind')
        self.assertEqual([c['state'] for c in out['result']['close_orders']], ['FILLED'])
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) AS n FROM pf_positions WHERE quantity<>0 AND mode=%s', ('PAPER',)).fetchone()['n'], 0)
        self.assertEqual(self.deps()[0]['status'], 'PAUSED', 'stop pauses; the run stays for the next start')
        d = self.desk(desk['id'])
        d = self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/start", json={'version': d['version']}))
        self.assertEqual((len(self.deps()), self.deps()[0]['status']), (1, 'ACTIVE'), 'restart reuses its own run')

    def test_staged_changes_apply_only_after_review(self):
        desk, cfg, _ = self.setup_desk()
        d = self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/start", json={'version': desk['version']}))
        changed = {**cfg, 'processing': {**cfg['processing'], 'sizing': {'method': 'FIXED_QTY', 'quantity': '2'}}}
        d = self.ok(self.alice.put(f"/api/trading/desks/{desk['id']}", json={'name': 'Gold desk', 'config': changed, 'version': d['version']}))
        self.assertTrue(d['staged'])
        self.assertEqual(self.deps()[0]['sizing']['quantity'], '1', 'running settings unchanged until applied')
        review = self.ok(self.alice.get(f"/api/trading/desks/{desk['id']}/review"))
        self.assertIn({'setting': 'processing.sizing.quantity', 'running': '1', 'staged': '2'}, review['changes'])
        self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/start", json={'version': d['version']}), 409)
        d = self.ok(self.bob.post(f"/api/trading/desks/{desk['id']}/apply", json={'version': d['version'], 'reason': 'size reviewed'}))
        self.assertIsNone(d['staged'])
        self.assertEqual((self.deps()[0]['sizing']['quantity'], self.deps()[0]['status']), ('2', 'ACTIVE'))

    def test_order_sending_is_read_never_switched_and_live_needs_second_person(self):
        desk, _cfg, _ = self.setup_desk('DEMO', 'DEMO')
        plan = self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/validate"))
        self.assertIn('DEMO order sending is off', [c['title'] for c in plan['checks'] if c['status'] == 'FAIL'])
        self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/start", json={'version': self.desk(desk['id'])['version']}), 422)
        with self.connect() as db:
            self.assertFalse(db.execute("SELECT trading_enabled FROM cfg_execution_controls WHERE environment='DEMO'").fetchone()['trading_enabled'])

    def test_live_four_eyes(self):
        desk, _cfg, (_c, a, *_rest) = self.setup_desk('LIVE', 'LIVE', handoff='APPROVE')
        plan = self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/validate"))
        self.assertIn('LIVE order sending is off', [c['title'] for c in plan['checks'] if c['status'] == 'FAIL'])
        self.assertIn('The pre-LIVE policy is not met', [c['title'] for c in plan['checks'] if c['status'] == 'FAIL'])
        self.live_ready(a)
        self.sending('LIVE')
        plan = self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/validate"))
        self.assertTrue(plan['ready'], plan['checks'])
        d = self.desk(desk['id'])
        refused = self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/start", json={'version': d['version']}), 422)
        self.assertIn('different person', str(refused))
        self.assertEqual(self.deps(), [], 'nothing created when refused')
        d = self.ok(self.bob.post(f"/api/trading/desks/{desk['id']}/start", json={'version': d['version']}))
        dep = self.deps()[0]
        self.assertEqual((dep['mode'], dep['proposed_by'], dep['activated_by'], dep['status']), ('BROKER', 'alice', 'bob', 'ACTIVE'))

    def test_emergency_stop_engages_scoped_kill_switches(self):
        desk, _cfg, _ = self.setup_desk()
        d = self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/start", json={'version': desk['version']}))
        out = self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/emergency", json={'reason': 'feed looks wrong'}))
        self.assertEqual(out['state'], 'STOPPED')
        with self.connect() as db:
            sw = db.execute('SELECT scope, target_id, engaged FROM risk_kill_switches').fetchall()
        self.assertEqual([(s['scope'], str(s['target_id']), s['engaged']) for s in sw], [('DEPLOYMENT', str(self.deps()[0]['id']), True)])
        self.assertEqual(self.deps()[0]['status'], 'PAUSED')
        self.assertTrue(d)

    def test_private_desks_roles_and_results(self):
        with self.connect() as db:
            db.execute("INSERT INTO cfg_users(id, username, display_name, password_hash, roles, must_change_password) "
                       "VALUES (%s,'carol','Carol',%s,ARRAY['TRADING_OPS'],false)", (uuid4(), hash_password(tmd.PASSWORD)))
        carol = self.sign_in('carol')
        try:
            desk, cfg, _ = self.setup_desk()
            private = self.ok(self.alice.post('/api/trading/desks', json={'name': 'Mine', 'shared': False, 'config': cfg}), 201)
            self.assertEqual({d['name'] for d in self.ok(carol.get('/api/trading/desks'))['items']}, {'Gold desk'})
            self.ok(carol.get(f"/api/trading/desks/{private['id']}"), 404)
            self.ok(carol.post(f"/api/trading/desks/{desk['id']}/start", json={'version': desk['version']}), 403)
            d = self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/start", json={'version': desk['version']}))
            self.ok(carol.post(f"/api/trading/desks/{desk['id']}/pause", json={'version': d['version'], 'reason': 'ops pause'}))
            log = self.ok(self.alice.get(f"/api/trading/desks/{desk['id']}/results/log"))['items']
            self.assertEqual(log[0]['action'], 'PAUSE')
            for tab in ('runs', 'signals', 'decisions', 'orders', 'positions'):
                self.ok(self.alice.get(f"/api/trading/desks/{desk['id']}/results/{tab}"))
            self.ok(self.alice.delete(f"/api/trading/desks/{desk['id']}?version={self.desk(desk['id'])['version']}"), 422)
        finally:
            carol.close()

    def test_import_saved_layout(self):
        connection, a, _b, ins = self.real_account()
        s, _ = self.strategy()
        panels = [{'id': n} for n in ('market', 'intelligence', 'signals', 'strategy', 'risk', 'execution', 'portfolio', 'posttrade')]
        layout = {'revision': 0, 'next_number': 2, 'active': 1,
                  'items': [{'number': 1, 'name': 'EUR desk', 'mode': 'PAPER', 'timeframe': 'M5', 'account': a['id'], 'source': a['id'],
                             'symbol': 'EURUSD', 'panels': panels, 'scan': {'instrument_symbols': ['EURUSD'], 'strategy_codes': ['WS_EMA']}}]}
        self.ok(self.alice.put('/api/trading/workspaces', json=layout))
        desk = self.ok(self.alice.post('/api/trading/desks/import/1'), 201)
        self.assertEqual((desk['name'], desk['config']['input']['sources'], desk['config']['input']['instruments'],
                          desk['config']['processing']['strategies'], desk['config']['output']['accounts']),
                         ('EUR desk', [connection['id']], [ins['id']], [{'strategy_id': s['id'], 'version_id': None}], [a['id']]))
        self.ok(self.alice.post('/api/trading/desks/import/1'), 409)
        self.assertTrue(self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/validate"))['ready'])

    def test_paper_deployment_adopted_into_broker_demo_with_confirmation(self):
        """The live case of 25 Nov: a paper deployment already holds the combination and the desk is set to Broker DEMO."""
        connection, a, _b, ins = self.real_account('DEMO')
        s, _v = self.strategy()
        existing = self.deploy_existing_for(s, a, ins)
        self.sending('DEMO')
        cfg = {'input': {'sources': [connection['id']], 'instruments': [ins['id']]},
               'processing': {'strategies': [{'strategy_id': s['id']}]},
               'output': {'environment': 'DEMO', 'handoff': 'AUTO', 'accounts': [a['id']]}}
        desk = self.ok(self.alice.post('/api/trading/desks', json={'name': 'Demo desk', 'config': cfg}), 201)
        plan = self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/validate"))
        self.assertTrue(plan['ready'], plan['checks'])
        self.assertEqual([(r['action'], r.get('switch_mode'), r.get('from_mode')) for r in plan['runs']], [('ADOPT', True, 'PAPER')])
        refused = self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/start", json={'version': desk['version']}), 422)
        self.assertIn('Tick the deployments to adopt', str(refused))
        self.assertEqual(self.deps()[0]['mode'], 'PAPER', 'nothing changed without confirmation')
        self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/start", json={'version': desk['version'], 'adopt': [existing['id']]}))
        dep = self.deps()[0]
        self.assertEqual((dep['mode'], dep['status']), ('BROKER', 'ACTIVE'))
        self.assertEqual([r['role'] for r in self.desk(desk['id'])['runs']], ['ADOPTED'])

    def test_broker_run_with_open_orders_is_not_moved_to_paper(self):
        connection, a, _b, ins = self.real_account('DEMO')
        s, _v = self.strategy()
        existing = self.deploy_existing_for(s, a, ins)
        with self.connect() as db:
            db.execute("UPDATE cfg_deployments SET mode='BROKER' WHERE id=%s", (UUID(existing['id']),))
        with store.transaction('write') as db:
            o = oms.place(db, account_id=a['id'], instrument_id=ins['id'], side='BUY', quantity=Decimal('0.10'), mode='BROKER',
                          deployment={'id': existing['id'], 'strategy_id': s['id']}, seed='ws-open', actor='alice', at=te.NOW)
        self.assertIn(o['state'], ('APPROVED', 'REJECTED'))
        if o['state'] == 'REJECTED':
            with self.connect() as db:
                db.execute("UPDATE oms_orders SET state='ACKNOWLEDGED' WHERE id=%s", (o['id'],))
        cfg = {'input': {'sources': [connection['id']], 'instruments': [ins['id']]},
               'processing': {'strategies': [{'strategy_id': s['id']}]}, 'output': {'environment': 'PAPER', 'accounts': [a['id']]}}
        desk = self.ok(self.alice.post('/api/trading/desks', json={'name': 'Paper desk', 'config': cfg}), 201)
        plan = self.ok(self.alice.post(f"/api/trading/desks/{desk['id']}/validate"))
        self.assertEqual([r['action'] for r in plan['runs']], ['WATCH'])
        fail = next(c for c in plan['checks'] if c['title'] == 'Nothing to run')
        self.assertIn('open broker position or working order', fail['detail'], 'Nothing to run names the exact reason')
