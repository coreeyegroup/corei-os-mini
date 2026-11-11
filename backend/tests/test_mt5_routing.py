"""MT5 address routing per account, latency test and one-step account setup.

Unit tests need nothing external. The PostgreSQL tests need COREI_TEST_DATABASE_URL.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from corei_os_mini import latency
from corei_os_mini.api import app
from corei_os_mini.mt5_routing import effective_access_points
from corei_os_mini.operator_auth import Principal, current_user

EVERYONE = Principal('tester', frozenset({'ADMIN', 'TRADING_OPS', 'RISK_APPROVER'}), '', 'Tester')
HEADERS = {'Origin': 'http://testserver', 'X-COREI-Operator': '1'}
A, B, C = ({'host': '203.0.113.1', 'port': 443}, {'host': '203.0.113.2', 'port': 443}, {'host': '203.0.113.3', 'port': 443})
SERVER = [{**A, 'enabled': True}, {**B, 'enabled': True}, {**C, 'enabled': False}]


class RoutingRules(unittest.TestCase):
    def hosts(self, result):
        return [p['host'][-1] for p in result['addresses']]

    def test_server_mode_uses_ticked_addresses_in_order(self):
        r = effective_access_points('SERVER', [], SERVER)
        self.assertEqual((self.hosts(r), r['warnings']), (['1', '2'], []))

    def test_fastest_mode_orders_by_latest_measurement(self):
        latency_map = {('203.0.113.1', 443): {'reachable': True, 'latency_ms': 80},
                       ('203.0.113.2', 443): {'reachable': True, 'latency_ms': 20}}
        self.assertEqual(self.hosts(effective_access_points('FASTEST', [], SERVER, latency_map)), ['2', '1'])
        # Unreachable or unmeasured addresses follow, in server order.
        latency_map[('203.0.113.2', 443)] = {'reachable': False, 'latency_ms': None}
        self.assertEqual(self.hosts(effective_access_points('FASTEST', [], SERVER, latency_map)), ['1', '2'])
        r = effective_access_points('FASTEST', [], SERVER, {})
        self.assertEqual((self.hosts(r), r['warnings']), (['1', '2'], ['NO_LATENCY_MEASURED']))

    def test_account_mode_uses_own_order_even_unticked_server_addresses(self):
        r = effective_access_points('ACCOUNT', [C, A], SERVER)
        self.assertEqual((self.hosts(r), r['used_mode'], r['warnings']), (['3', '1'], 'ACCOUNT', []))

    def test_account_mode_flags_removed_addresses_and_falls_back(self):
        gone = {'host': '198.51.100.9', 'port': 443}
        r = effective_access_points('ACCOUNT', [gone, B], SERVER)
        self.assertEqual((self.hosts(r), r['warnings']), (['2'], ['ACCOUNT_ADDRESSES_REMOVED_BY_SERVER']))
        r = effective_access_points('ACCOUNT', [gone], SERVER)
        self.assertEqual((self.hosts(r), r['used_mode']), (['1', '2'], 'SERVER'))
        self.assertEqual(r['warnings'], ['ACCOUNT_ADDRESSES_REMOVED_BY_SERVER', 'ACCOUNT_ADDRESSES_FALLBACK_TO_SERVER'])

    def test_no_address_is_reported(self):
        r = effective_access_points('SERVER', [], [{**A, 'enabled': False}])
        self.assertEqual(r['warnings'], ['NO_ADDRESS_AVAILABLE'])


class LatencySafety(unittest.TestCase):
    def test_private_and_loopback_addresses_are_not_probed(self):
        result = asyncio.run(latency.measure([('127.0.0.1', 22), ('10.30.210.41', 443), ('192.168.1.1', 80)]))
        self.assertTrue(all(r['error'] == 'Private or reserved address; not tested' for r in result['results']))
        self.assertTrue(all(not r['reachable'] for r in result['results']))

    def test_measures_tcp_connect_time(self):
        async def run():
            server = await asyncio.start_server(lambda r, w: w.close(), '127.0.0.1', 0)
            port = server.sockets[0].getsockname()[1]
            closed = await asyncio.start_server(lambda r, w: w.close(), '127.0.0.1', 0)
            dead = closed.sockets[0].getsockname()[1]
            closed.close(); await closed.wait_closed()
            with patch('corei_os_mini.latency._public', lambda ip: True):
                result = await latency.measure([('127.0.0.1', port), ('127.0.0.1', dead)])
            server.close(); await server.wait_closed()
            return result
        result = asyncio.run(run())
        ok, refused = result['results']
        self.assertTrue(ok['reachable']); self.assertGreaterEqual(ok['latency_ms'], 0); self.assertGreaterEqual(ok['median_ms'], ok['latency_ms'])
        self.assertFalse(refused['reachable']); self.assertTrue(refused['error'])
        self.assertEqual(result['attempts'], 3)

    def test_api_validates_and_limits(self):
        app.dependency_overrides[current_user] = lambda: EVERYONE
        try:
            with TestClient(app, headers=HEADERS) as client:
                self.assertEqual(client.post('/api/config/mt5/latency', json={'addresses': []}).status_code, 422)
                self.assertEqual(client.post('/api/config/mt5/latency', json={'addresses': [{'host': 'bad host', 'port': 1}]}).status_code, 422)
                self.assertEqual(client.post('/api/config/mt5/latency', json={'addresses': [{'host': '1.1.1.1', 'port': 443}] * 65}).status_code, 422)
                result = client.post('/api/config/mt5/latency', json={'addresses': [{'host': '10.0.0.1', 'port': 443}]})
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(result.json()['results'][0]['error'], 'Private or reserved address; not tested')
        finally:
            app.dependency_overrides.clear()


@unittest.skipUnless(os.getenv('COREI_TEST_DATABASE_URL'), 'Set COREI_TEST_DATABASE_URL for PostgreSQL integration tests')
class Mt5SetupAndRouting(unittest.TestCase):
    def setUp(self):
        self.url = os.environ['COREI_TEST_DATABASE_URL']
        self.schema = 'test_route_' + uuid4().hex
        with psycopg.connect(self.url, autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        with self.connect() as db:
            for path in sorted((Path(__file__).parents[1] / 'sql').glob('[0-9][0-9][0-9]_*.sql')):
                db.execute(path.read_text())
        self.folder = tempfile.TemporaryDirectory()
        key = Path(self.folder.name) / 'key'; key.write_bytes(os.urandom(32)); key.chmod(0o600)
        self.patches = [patch.dict(os.environ, {'COREI_BROKER_KEY_FILE': str(key)}),
                        patch('corei_os_mini.config_store._connect', self.connect)]
        for p in self.patches:
            p.start()
        app.dependency_overrides[current_user] = lambda: EVERYONE
        self.client = TestClient(app, headers=HEADERS)
        self.provider = self.ok(self.client.post('/api/config/providers', json={'code': 'VTM', 'name': 'VT Markets', 'kind': 'BROKER'}), 201)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        for p in reversed(self.patches):
            p.stop()
        self.folder.cleanup()
        with psycopg.connect(self.url, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def connect(self, _=None):
        return psycopg.connect(self.url, options='-c search_path=' + self.schema, row_factory=dict_row)

    def ok(self, result, status=200):
        self.assertEqual(result.status_code, status, result.text)
        return result.json()

    def setup_body(self, login, points, **account):
        return {'provider_id': self.provider['id'], 'environment': 'LIVE',
                'server': {'server_name': 'VTMarkets-Live 3', 'company_name': 'VT Markets (Pty) Ltd',
                           'access_points': [{'name': 'VTMarkets-Live 3', **p} for p in points]},
                'account': {'code': f'VTM_LIVE_{login}', 'name': 'Gold', 'external_account_id': login, 'holder_name': 'Yatheesh N S',
                            'base_currency': 'USD', **account},
                'credential': {'kind': 'MT5_LOGIN', 'password': 'not-a-real-password', 'access_level': 'FULL'}}

    def test_one_step_setup_reuses_server_and_routes_per_account(self):
        first = self.ok(self.client.post('/api/config/mt5/account-setup', json=self.setup_body('1298617', [A, B])), 201)
        self.assertEqual(first['connection_action'], 'CREATED')
        self.assertEqual(first['connection']['code'], 'VTM_MT5_LIVE')
        self.assertEqual(first['account']['credential_kind'], 'MT5_LOGIN')
        self.assertEqual(first['account']['routing']['mode'], 'SERVER')
        self.assertEqual([p['host'] for p in first['account']['routing']['addresses']], ['203.0.113.1', '203.0.113.2'])
        self.assertNotIn('connection_settings', first['account'])

        # Second account on the same server: connection reused; its extra address is added unticked.
        second = self.ok(self.client.post('/api/config/mt5/account-setup', json=self.setup_body(
            '1298618', [C], access_mode='ACCOUNT', access_points=[C])), 201)
        self.assertEqual(second['connection_action'], 'ADDRESSES_ADDED')
        self.assertEqual(second['connection']['id'], first['connection']['id'])
        self.assertEqual([(p['host'], p['enabled']) for p in second['connection']['settings']['access_points']],
                         [('203.0.113.1', True), ('203.0.113.2', True), ('203.0.113.3', False)])
        self.assertEqual([p['host'] for p in second['account']['routing']['addresses']], ['203.0.113.3'])
        # The first account is unaffected by the second account's choice.
        accounts = {a['external_account_id']: a for a in self.ok(self.client.get('/api/config/accounts'))['items']}
        self.assertEqual(len(accounts['1298617']['routing']['addresses']), 2)

        # Same server typed with different spacing/case is the same server.
        body = self.setup_body('1298619', [A]); body['server']['server_name'] = 'vtmarkets-live  3'
        self.assertEqual(self.ok(self.client.post('/api/config/mt5/account-setup', json=body), 201)['connection_action'], 'REUSED')

        # All or nothing: a bad credential rolls back the account (and nothing half-saved).
        bad = self.setup_body('1298620', [A], trading_permission='TRADING')
        bad['credential']['access_level'] = 'READ_ONLY'
        self.ok(self.client.post('/api/config/mt5/account-setup', json=bad), 422)
        self.assertNotIn('1298620', {a['external_account_id'] for a in self.ok(self.client.get('/api/config/accounts'))['items']})

    def test_account_address_rules_and_latency_history(self):
        made = self.ok(self.client.post('/api/config/mt5/account-setup', json=self.setup_body('5001', [A, B])), 201)
        account, connection = made['account'], made['connection']
        fields = ('connection_id', 'code', 'name', 'external_account_id', 'holder_name', 'base_currency', 'trading_permission', 'status')
        body = {k: account[k] for k in fields}
        # An address that is not the server's is refused.
        self.ok(self.client.put(f"/api/config/accounts/{account['id']}", json={**body, 'version': account['version'],
                'access_mode': 'ACCOUNT', 'access_points': [{'host': '198.51.100.9', 'port': 443}]}), 422)
        updated = self.ok(self.client.put(f"/api/config/accounts/{account['id']}", json={**body, 'version': account['version'],
                          'access_mode': 'ACCOUNT', 'access_points': [B]}))
        self.assertEqual([p['host'] for p in updated['routing']['addresses']], ['203.0.113.2'])
        # Switching back to SERVER keeps the account's own list for later.
        back = self.ok(self.client.put(f"/api/config/accounts/{account['id']}", json={**body, 'version': updated['version'],
                       'access_mode': 'SERVER', 'access_points': [B]}))
        self.assertEqual((back['access_points'], len(back['routing']['addresses'])), ([B], 2))

        # Broker drops address B: the account is flagged and falls back, never left without a route.
        settings = {**connection['settings'], 'access_points': [{'name': 'x', **A, 'enabled': True}]}
        conn_body = {k: connection[k] for k in ('provider_id', 'code', 'name', 'platform', 'environment', 'purpose', 'status')}
        self.ok(self.client.put(f"/api/config/connections/{connection['id']}", json={**conn_body, 'settings': settings, 'version': connection['version']}))
        again = self.ok(self.client.put(f"/api/config/accounts/{account['id']}", json={**body, 'version': back['version'],
                        'access_mode': 'ACCOUNT', 'access_points': [A]}))
        with self.connect() as db:
            db.execute("UPDATE cfg_accounts SET access_points='[{\"host\":\"203.0.113.2\",\"port\":443}]' WHERE id=%s", (account['id'],))
        flagged = self.ok(self.client.get(f"/api/config/accounts/{account['id']}"))
        self.assertEqual(flagged['routing']['used_mode'], 'SERVER')
        self.assertIn('ACCOUNT_ADDRESSES_FALLBACK_TO_SERVER', flagged['routing']['warnings'])
        self.assertTrue(again)

        # Latency history drives FASTEST.
        from corei_os_mini import config_store
        config_store.record_latency('tester', connection['id'], 'core-node',
                                    [{'host': '203.0.113.1', 'port': 443, 'reachable': True, 'latency_ms': 42.5, 'error': None}])
        latest = self.ok(self.client.get(f"/api/config/connections/{connection['id']}/latency"))['items']
        self.assertEqual((latest[0]['latency_ms'], latest[0]['measured_from']), (42.5, 'core-node'))
        fastest = self.ok(self.client.put(f"/api/config/accounts/{account['id']}", json={**body, 'version': flagged['version'],
                          'access_mode': 'FASTEST', 'access_points': []}))
        self.assertEqual((fastest['routing']['addresses'][0]['latency_ms'], fastest['routing']['warnings']), (42.5, []))


if __name__ == '__main__':
    unittest.main()
