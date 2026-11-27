"""Platform mode (release 0.66.0, migration 053): DEVELOPMENT for building and testing, PRODUCTION for real trading.

Evidence for: a new installation starts in PRODUCTION (deploying changes nothing by itself); only an administrator
switches, with a reason, and every switch is audited; PRODUCTION needs the typed word PRODUCTION; passwords need 6
characters in PRODUCTION and 2 in DEVELOPMENT; entering DEVELOPMENT turns LIVE order sending OFF (audited) and keeps
it off: the switch refuses, the pre-trade check and the last check before sending refuse LIVE orders; "Allow LIVE in
Development" needs the typed word LIVE and removing it turns LIVE off again; returning to PRODUCTION never turns LIVE
on; in DEVELOPMENT PAPER and DEMO setup gates only warn while LIVE gates keep their levels and the configured levels
are untouched; resetting test data is refused in PRODUCTION, needs RESET, archives paper accounts with their history
and starts fresh copies, and skips accounts with active runs.
"""
from __future__ import annotations

import importlib
import importlib.util
from uuid import uuid4

te = importlib.import_module('test_execution') if importlib.util.find_spec('test_execution') else importlib.import_module('tests.test_execution')
tc = importlib.import_module('test_controls') if importlib.util.find_spec('test_controls') else importlib.import_module('tests.test_controls')
from corei_os_mini import config_store as store  # noqa: E402
from corei_os_mini.operator_auth import hash_password  # noqa: E402
from corei_os_mini.trading import execution, setup_gates  # noqa: E402


class PlatformMode(te.Execution):
    test_sending_disabled_during_precheck = None
    test_real_orders_on_demo_every_path = None
    test_four_eyes_on_live = None
    test_risk_extras = None
    test_portfolio_extras = None
    test_optimise_walk_forward_and_drift = None

    def mode(self, mode, version=None, allow=False, confirm='', client=None, status=200):
        if version is None:
            version = self.ok(self.alice.get('/api/config/platform-mode'))['version']
        r = (client or self.alice).put('/api/config/platform-mode', json={'mode': mode, 'allow_live_in_development': allow,
                                                                          'confirm': confirm, 'reason': 'test switch', 'version': version})
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def live_on(self):
        with self.connect() as db:
            return db.execute("SELECT trading_enabled FROM cfg_execution_controls WHERE environment='LIVE'").fetchone()['trading_enabled']

    def test_starts_in_production_and_only_an_administrator_switches(self):
        m = self.ok(self.alice.get('/api/config/platform-mode'))
        self.assertEqual((m['mode'], m['password_min'], m['reset_allowed']), ('PRODUCTION', 6, False))
        self.assertEqual(self.ok(self.alice.get('/api/operator/session'))['password_min'], 6)
        self.assertEqual(self.ok(self.alice.get('/api/config/overview'))['platform_mode']['mode'], 'PRODUCTION')
        # Passwords: 6 in Production, 2 in Development, no complexity.
        self.ok(self.alice.post('/api/admin/users', json={'username': 'pfive', 'display_name': 'P', 'roles': ['OBSERVER'], 'temporary_password': 'abcde'}), 422)
        self.ok(self.alice.post('/api/admin/users', json={'username': 'psix', 'display_name': 'P', 'roles': ['OBSERVER'], 'temporary_password': 'abcdef'}), 201)
        # A risk approver who is not an administrator cannot switch.
        with self.connect() as db:
            db.execute("INSERT INTO cfg_users(id, username, display_name, password_hash, roles, must_change_password) "
                       "VALUES (%s,'carol','Carol',%s,ARRAY['RISK_APPROVER','TRADING_OPS'],false)", (uuid4(), hash_password(tc.PASSWORD)))
        carol = self.sign_in('carol')
        self.mode('DEVELOPMENT', client=carol, status=403)
        dev = self.mode('DEVELOPMENT')
        self.assertEqual((dev['mode'], dev['password_min'], dev['reset_allowed']), ('DEVELOPMENT', 2, True))
        self.ok(self.alice.post('/api/admin/users', json={'username': 'dtwo', 'display_name': 'D', 'roles': ['OBSERVER'], 'temporary_password': 'ab'}), 201)
        self.ok(self.alice.post('/api/admin/users', json={'username': 'done', 'display_name': 'D', 'roles': ['OBSERVER'], 'temporary_password': 'a'}), 422)
        # Back to Production needs the typed word, and a stale version is refused.
        self.mode('PRODUCTION', version=dev['version'], status=422)
        self.mode('PRODUCTION', version=dev['version'] - 1, confirm='PRODUCTION', status=409)
        self.assertEqual(self.mode('PRODUCTION', confirm='PRODUCTION')['mode'], 'PRODUCTION')
        audit = self.ok(self.alice.get('/api/config/audit', params={'entity': 'policy', 'entity_id': 'platform_mode'}))['items']
        self.assertEqual([(a['action'], a['actor'], a['after_state']['mode']) for a in audit][:2],
                         [('PLATFORM_MODE', 'alice', 'PRODUCTION'), ('PLATFORM_MODE', 'alice', 'DEVELOPMENT')])

    def test_development_keeps_live_off_unless_allowed(self):
        _connection, a, _b, ins = self.real_account('LIVE')
        self.sending('LIVE')
        dev = self.mode('DEVELOPMENT')
        self.assertTrue(dev['live_switched_off'])
        self.assertFalse(self.live_on(), 'entering Development turns LIVE order sending off')
        audit = self.ok(self.alice.get('/api/config/audit', params={'entity': 'execution control', 'entity_id': 'LIVE'}))['items']
        self.assertEqual(audit[0]['action'], 'ORDER_SENDING_OFF')
        # The switch refuses to turn LIVE on in Development.
        with self.assertRaises(Exception) as refused:
            store.set_execution_control('alice', 'LIVE', True, 'try in development',
                                        self.ok(self.alice.get('/api/config/execution-controls'))['items'][1]['version'], 'LIVE')
        self.assertEqual(refused.exception.status_code, 409)
        # Even if the switch were on (e.g. set directly in the database), pre-trade and last checks refuse LIVE.
        self.sending('LIVE')
        order = self.place(a, ins, 'dev-live-1')
        self.assertEqual(order['state'], 'REJECTED')
        self.assertTrue(any(r.startswith('LIVE_OFF_IN_DEVELOPMENT') for r in order['reasons']), order['reasons'])
        with store.transaction('read') as db:
            row = dict(db.execute('SELECT * FROM oms_orders WHERE id=%s', (order['id'],)).fetchone())
            _adapter, reasons = execution.gate(db, row)
        self.assertTrue(any(r.startswith('LIVE_OFF_IN_DEVELOPMENT') for r in reasons), reasons)
        # Allowing LIVE in Development needs the typed word LIVE.
        self.mode('DEVELOPMENT', allow=True, status=422)
        allowed = self.mode('DEVELOPMENT', allow=True, confirm='LIVE')
        self.assertEqual((allowed['allow_live_in_development'], allowed['live_blocked']), (True, False))
        with store.transaction('read') as db:
            _adapter, reasons = execution.gate(db, row)
        self.assertFalse(any(r.startswith('LIVE_OFF_IN_DEVELOPMENT') for r in reasons), reasons)
        self.assertTrue(self.live_on(), 'allowing LIVE never changes the switch itself')
        # Removing the allowance turns LIVE off again; returning to Production never turns it on.
        self.assertTrue(self.mode('DEVELOPMENT')['live_switched_off'])
        self.assertFalse(self.live_on())
        self.mode('PRODUCTION', confirm='PRODUCTION')
        self.assertFalse(self.live_on())

    def test_development_gates_only_warn_but_live_gates_keep_their_levels(self):
        with self.connect() as db:
            db.execute("UPDATE cfg_policies SET value=jsonb_set(value, '{preset}', '\"RECOMMENDED\"') WHERE key='setup_gates'")
            prod = setup_gates.policy(db)
        self.assertEqual(prod['levels']['DEMO']['ACCOUNTS'], 'SOFT')
        self.mode('DEVELOPMENT')
        with self.connect() as db:
            dev = setup_gates.policy(db)
        self.assertTrue(dev['development_mode'])
        self.assertFalse([lvl for t in ('PAPER', 'DEMO') for lvl in dev['levels'][t].values() if lvl in setup_gates.BLOCKING])
        self.assertEqual(dev['levels']['LIVE'], prod['levels']['LIVE'])
        self.assertEqual(dev['configured_levels'], prod['levels'], 'the chosen levels are kept for Production')
        self.mode('PRODUCTION', confirm='PRODUCTION')
        with self.connect() as db:
            self.assertEqual(setup_gates.policy(db)['levels'], prod['levels'])

    def test_reset_test_data_only_in_development_and_keeps_history(self):
        body = {'code': 'PAPER_USD', 'name': 'Paper USD', 'currency': 'USD', 'amount': '50000', 'commission_per_lot': '7', 'reason': 'test paper account'}
        old = self.ok(self.alice.post('/api/trading/paper-accounts', json=body), 201)
        self.ok(self.alice.post('/api/config/platform-mode/reset-test-data', json={'confirm': 'RESET', 'reason': 'clean start'}), 409)
        self.mode('DEVELOPMENT')
        self.ok(self.alice.post('/api/config/platform-mode/reset-test-data', json={'confirm': 'reset', 'reason': 'clean start'}), 422)
        result = self.ok(self.alice.post('/api/config/platform-mode/reset-test-data', json={'confirm': 'RESET', 'reason': 'clean start'}))
        self.assertEqual([(r['old'], r['new']) for r in result['reset']], [('PAPER_USD', 'PAPER_USD_R2')])
        listed = {x['code']: x for x in self.ok(self.alice.get('/api/trading/paper-accounts', params={'archived': 'true'}))['items']}
        self.assertEqual(listed['PAPER_USD_R2']['commission_per_lot'], old['commission_per_lot'])
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM cfg_accounts WHERE id=%s', (old['id'],)).fetchone()['status'], 'ARCHIVED')
        # A second reset makes R3 from R2; nothing is deleted.
        again = self.ok(self.alice.post('/api/config/platform-mode/reset-test-data', json={'confirm': 'RESET', 'reason': 'again'}))
        self.assertEqual([r['new'] for r in again['reset']], ['PAPER_USD_R3'])
        audit = self.ok(self.alice.get('/api/config/audit', params={'entity': 'policy', 'entity_id': 'platform_mode'}))['items']
        self.assertEqual(audit[0]['action'], 'RESET_TEST_DATA')
