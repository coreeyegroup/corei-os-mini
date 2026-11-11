"""Strategy registry and deployment matrix against PostgreSQL (needs COREI_TEST_DATABASE_URL)."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from corei_os_mini import operator_auth
from corei_os_mini.api import app
from corei_os_mini.operator_auth import hash_password

HEADERS = {'x-corei-request': 'workstation'}
MT5 = {'server_name': 'Broker-Server', 'access_points': [{'name': 'AP', 'host': '192.0.2.1', 'port': 443}]}


@unittest.skipUnless(os.getenv('COREI_TEST_DATABASE_URL'), 'Set COREI_TEST_DATABASE_URL for PostgreSQL integration tests')
class StrategyDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.url = os.environ['COREI_TEST_DATABASE_URL']
        self.schema = 'test_strat_' + uuid4().hex
        with psycopg.connect(self.url, autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        with self.connect() as db:
            for path in sorted((Path(__file__).parents[1] / 'sql').glob('[0-9][0-9][0-9]_*.sql')):
                db.execute(path.read_text())
            # Four-eyes on LIVE changes is covered in test_controls; these tests exercise other rules.
            db.execute("UPDATE cfg_policies SET value='false' WHERE key='four_eyes_live_changes'")
        self.patches = [patch('corei_os_mini.config_store._connect', self.connect),
                        patch.dict(os.environ, {'COREI_OPERATOR_USER': 'owner', 'COREI_OPERATOR_PASSWORD_HASH': hash_password('owner-login-pass-1')})]
        for p in self.patches:
            p.start()
        operator_auth.attempts.clear()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        with psycopg.connect(self.url, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def connect(self, _=None):
        return psycopg.connect(self.url, options='-c search_path=' + self.schema, row_factory=dict_row)

    def user(self, owner, name, roles):
        owner.post('/api/admin/users', json={'username': name, 'display_name': name, 'roles': roles, 'temporary_password': 'Temporary-pass-1'})
        client = TestClient(app, base_url='https://testserver', headers=HEADERS)
        client.post('/api/operator/login', json={'username': name, 'password': 'Temporary-pass-1'})
        client.post('/api/operator/password', json={'current_password': 'Temporary-pass-1', 'new_password': 'Personal-pass-77'})
        operator_auth.attempts.clear()
        return client

    def ok(self, result, status=200):
        self.assertEqual(result.status_code, status, result.text)
        return None if status == 204 else result.json()

    def test_strategy_versions_and_deployment_matrix(self):
        owner = TestClient(app, base_url='https://testserver', headers=HEADERS)
        self.ok(owner.post('/api/operator/login', json={'username': 'owner', 'password': 'owner-login-pass-1'}))
        trader = self.user(owner, 'tom', ['TRADING_OPS'])
        risk = self.user(owner, 'rita', ['RISK_APPROVER'])
        observer = self.user(owner, 'olga', ['OBSERVER'])

        # Configuration: one provider, demo + live trading connections, a separate live data feed.
        post = lambda path, body, status=201: self.ok(trader.post('/api/config/' + path, json=body), status)
        prov = post('providers', {'code': 'BRK', 'name': 'Broker', 'kind': 'BROKER'})
        conn = lambda code, env, purpose: post('connections', {'provider_id': prov['id'], 'code': code, 'name': code, 'platform': 'MT5',
                                                               'environment': env, 'purpose': purpose, 'settings': MT5})
        demo, live, feed = conn('DEMO_C', 'DEMO', 'BOTH'), conn('LIVE_C', 'LIVE', 'TRADING'), conn('FEED_C', 'LIVE', 'MARKET_DATA')
        acct = lambda code, c: post('accounts', {'connection_id': c['id'], 'code': code, 'name': code, 'external_account_id': str(abs(hash(code)) % 10**8 + 1),
                                                 'base_currency': 'USD', 'trading_permission': 'TRADING'})
        demo_acct, live_acct = acct('DEMO_1', demo), acct('LIVE_1', live)
        xau = post('instruments', {'symbol': 'XAUUSD', 'name': 'Gold', 'asset_class': 'METAL', 'base_currency': 'XAU', 'quote_currency': 'USD',
                                   'price_precision': 2, 'quantity_precision': 2, 'tick_size': '0.01', 'min_quantity': '0.01',
                                   'quantity_step': '0.01', 'contract_size': '100', 'contract_unit': 'oz'})
        demo_map = post('mappings', {'connection_id': demo['id'], 'instrument_id': xau['id'], 'broker_symbol': 'XAUUSD', 'usage': 'BOTH'})
        feed_map = post('mappings', {'connection_id': feed['id'], 'instrument_id': xau['id'], 'broker_symbol': 'GOLD', 'usage': 'MARKET_DATA'})

        # Algorithm catalogue and strategy versions.
        algos = self.ok(observer.get('/api/config/algorithms'))['items']
        self.assertEqual(algos[0]['id'], 'MA_CROSSOVER')
        self.assertIn('fast_window', algos[0]['schema']['properties'])
        strat = post('strategies', {'code': 'gold_trend', 'name': 'Gold trend', 'description': 'Trend following on gold'})
        self.assertEqual(strat['code'], 'GOLD_TREND')
        self.ok(observer.post('/api/config/strategies', json={'code': 'X1', 'name': 'x'}), 403)
        bad = trader.post(f"/api/config/strategies/{strat['id']}/versions", json={'algorithm': 'MA_CROSSOVER', 'parameters': {'timeframe': 'H1', 'fast_window': 50, 'slow_window': 20}})
        self.assertEqual(bad.status_code, 422)
        self.assertIn('fast_window must be smaller', bad.text)
        self.ok(trader.post(f"/api/config/strategies/{strat['id']}/versions", json={'algorithm': 'UNKNOWN', 'parameters': {}}), 422)
        v1 = post(f"strategies/{strat['id']}/versions", {'algorithm': 'ma_crossover', 'parameters': {'timeframe': 'H1', 'fast_window': 20, 'slow_window': 50}})
        self.assertEqual((v1['number'], v1['state']), (1, 'DRAFT'))
        v1 = self.ok(trader.put(f"/api/config/strategy-versions/{v1['id']}", json={'algorithm': 'MA_CROSSOVER', 'parameters':
                                {'timeframe': 'H1', 'fast_window': 10, 'slow_window': 50}, 'notes': 'tuned', 'version': v1['version']}))
        # Only a risk approver approves; approved versions are immutable, even in the database.
        self.ok(trader.post(f"/api/config/strategy-versions/{v1['id']}/approve", json={'version': v1['version']}), 403)
        v1 = self.ok(risk.post(f"/api/config/strategy-versions/{v1['id']}/approve", json={'version': v1['version']}))
        self.assertEqual((v1['state'], v1['approved_by']), ('APPROVED', 'rita'))
        self.ok(trader.put(f"/api/config/strategy-versions/{v1['id']}", json={'algorithm': 'MA_CROSSOVER', 'parameters':
                           {'timeframe': 'H1', 'fast_window': 5, 'slow_window': 50}, 'version': v1['version']}), 422)
        with self.connect() as db, self.assertRaises(psycopg.errors.CheckViolation):
            db.execute("UPDATE cfg_strategy_versions SET parameters='{}' WHERE id=%s", (v1['id'],))
        v2 = post(f"strategies/{strat['id']}/versions", {'algorithm': 'MA_CROSSOVER', 'parameters': {'timeframe': 'M15', 'fast_window': 8, 'slow_window': 21}})
        self.assertEqual(v2['number'], 2)

        # Deployments: data source is validated against the instrument; data-only accounts refused.
        deploy = {'strategy_id': strat['id'], 'strategy_version_id': v2['id'], 'instrument_id': xau['id'], 'account_id': demo_acct['id'],
                  'data_mapping_id': feed_map['id']}
        d_demo = post('deployments', deploy)
        self.assertEqual(d_demo['status'], 'PROPOSED')
        self.assertEqual(d_demo['data_connection_code'], 'FEED_C')  # data from another connection than the account
        self.assertIn('VERSION_NOT_APPROVED', d_demo['blockers'])
        self.assertIn('ORDER_SENDING_OFF_DEMO', d_demo['blockers'])
        post('deployments', deploy, 409)
        # Activation refused while the pinned version is a draft.
        tr = lambda client, d, action, status=200: self.ok(client.post(f"/api/config/deployments/{d['id']}/{action}",
                                                                       json={'version': d['version'], 'reason': 'Reviewed and approved'}), status)
        refused = risk.post(f"/api/config/deployments/{d_demo['id']}/activate", json={'version': d_demo['version'], 'reason': 'Reviewed'})
        self.assertEqual(refused.status_code, 422)
        self.assertIn('version not approved', refused.text)
        d_demo = self.ok(trader.put(f"/api/config/deployments/{d_demo['id']}", json={**deploy, 'strategy_version_id': v1['id'], 'version': d_demo['version']}))
        tr(trader, d_demo, 'activate', 403)
        d_demo = tr(risk, d_demo, 'activate')
        self.assertEqual((d_demo['status'], d_demo['activated_by']), ('ACTIVE', 'rita'))
        self.assertNotIn('DEPLOYMENT_NOT_ACTIVE', d_demo['blockers'])
        # Editing an active deployment sends it back for activation.
        d_demo = self.ok(trader.put(f"/api/config/deployments/{d_demo['id']}", json={**deploy, 'strategy_version_id': v1['id'],
                                                                                     'allocation_weight_bps': 5000, 'version': d_demo['version']}))
        self.assertEqual(d_demo['status'], 'PROPOSED')

        # LIVE account with no trading symbol on its connection cannot activate; four-eyes on LIVE.
        d_live = post('deployments', {**deploy, 'strategy_version_id': v1['id'], 'account_id': live_acct['id']})
        self.assertIn('NO_TRADING_SYMBOL_ON_ACCOUNT_CONNECTION', d_live['blockers'])
        tr(risk, d_live, 'activate', 422)
        post('mappings', {'connection_id': live['id'], 'instrument_id': xau['id'], 'broker_symbol': 'XAUUSD.r', 'usage': 'TRADING'})
        self.ok(owner.post('/api/config/deployments', json={**deploy, 'strategy_version_id': v1['id'], 'account_id': live_acct['id'],
                                                                         'instrument_id': xau['id'], 'strategy_id': strat['id']}), 409)
        refused = owner.post(f"/api/config/deployments/{d_live['id']}/activate", json={'version': d_live['version'], 'reason': 'Reviewed'})
        self.assertEqual(refused.status_code, 200, refused.text)  # owner did not propose it; tom did
        d_live = refused.json()
        d_live = tr(trader, d_live, 'pause')
        self.ok(owner.put(f"/api/config/deployments/{d_live['id']}", json={**deploy, 'strategy_version_id': v1['id'], 'account_id': live_acct['id'],
                                                                           'version': d_live['version']}))
        d_live = self.ok(owner.get('/api/config/deployments', params={'account_id': live_acct['id']}))['items'][0]
        self.assertEqual(d_live['proposed_by'], 'owner')
        self.assertIn('different person', owner.post(f"/api/config/deployments/{d_live['id']}/activate",
                                                     json={'version': d_live['version'], 'reason': 'Reviewed'}).text)
        tr(risk, d_live, 'activate')

        # The matrix from every starting point resolves to the same rows.
        by = lambda **f: {d['id'] for d in self.ok(observer.get('/api/config/deployments', params=f))['items']}
        everything = by()
        self.assertEqual(len(everything), 2)
        self.assertEqual(by(provider_id=prov['id']), everything)
        self.assertEqual(by(instrument_id=xau['id']), everything)
        self.assertEqual(by(strategy_id=strat['id']), everything)
        self.assertEqual(by(account_id=demo_acct['id']), {d_demo['id']})
        self.assertEqual(by(connection_id=live['id']), {d_live['id']})
        self.assertEqual(by(status='ACTIVE'), {d_live['id']})

        # Retirement guards and deletion rules.
        self.ok(trader.post(f"/api/config/strategy-versions/{v1['id']}/retire", json={'version': v1['version']}), 422)
        self.ok(trader.put(f"/api/config/strategies/{strat['id']}", json={'code': 'GOLD_TREND', 'name': 'Gold trend', 'status': 'RETIRED',
                                                                         'version': strat['version']}), 422)
        self.ok(trader.delete(f"/api/config/deployments/{d_live['id']}", params={'version': d_live['version'] + 1}), 422)
        self.ok(trader.delete(f"/api/config/deployments/{d_demo['id']}", params={'version': d_demo['version']}), 204)
        self.assertEqual(trader.delete(f"/api/config/mappings/{feed_map['id']}", params={'version': feed_map['version']}).status_code, 409)

        with self.connect() as db:
            actions = {(r['entity'], r['action']) for r in db.execute('SELECT entity, action FROM cfg_audit_log').fetchall()}
        for expected in [('strategy', 'CREATE'), ('strategy version', 'APPROVE'), ('deployment', 'PROPOSE'),
                         ('deployment', 'ACTIVATE'), ('deployment', 'PAUSE'), ('deployment', 'DELETE')]:
            self.assertIn(expected, actions)


if __name__ == '__main__':
    unittest.main()
