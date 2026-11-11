"""Configuration domain tests.

Unit tests need nothing external. The PostgreSQL test needs COREI_TEST_DATABASE_URL and
runs every migration in a throwaway schema, then drives the API end to end.
"""
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import ValidationError
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from corei_os_mini import credentials
from corei_os_mini.api import app
from corei_os_mini.config_models import ConnectionIn, InstrumentIn, Mt5Settings, RestApiSettings
from corei_os_mini.config_store import account_readiness
from corei_os_mini.operator_auth import Principal, current_user

EVERYONE = Principal('alice', frozenset({'ADMIN', 'TRADING_OPS', 'RISK_APPROVER', 'OBSERVER'}))

HEADERS = {'x-corei-request': 'workstation'}
MT5_SETTINGS = {'server_name': 'Broker-Demo', 'access_points': [{'name': 'Access 1', 'host': '192.0.2.1', 'port': 443}]}
XAU = dict(symbol='xauusd', name='Gold vs US Dollar', asset_class='METAL', base_currency='XAU', quote_currency='USD',
           price_precision=2, quantity_precision=2, tick_size='0.01', min_quantity='0.01', quantity_step='0.01',
           contract_size='100', contract_unit='oz')


class ModelTests(unittest.TestCase):
    def test_mt5_settings_require_unique_valid_access_points(self):
        Mt5Settings.model_validate(MT5_SETTINGS)
        for points in ([], [{'name': 'a', 'host': 'bad host', 'port': 1}],
                       [{'name': 'a', 'host': 'h.example.com', 'port': 1}, {'name': 'b', 'host': 'H.example.com', 'port': 1}]):
            with self.subTest(points=points), self.assertRaises(ValidationError):
                Mt5Settings.model_validate({'server_name': 'S', 'access_points': points})

    def test_mt5_settings_accept_large_server_lists(self):
        # MetaQuotes-Demo's servers.dat lists 50 access points, IPv4, IPv6 and hostnames.
        points = [{'name': f'Access Point {i}', 'host': host, 'port': port} for i, (host, port) in enumerate(
            [(f'78.140.180.{i}', 443) for i in range(1, 25)] + [(f'2a00:1178:1:60::{i:x}', 1950) for i in range(1, 25)]
            + [('access.metatrader5.com', 443), ('access.metatrader5.com', 1950)], start=1)]
        self.assertEqual(len(Mt5Settings.model_validate({'server_name': 'MetaQuotes-Demo', 'access_points': points}).access_points), 50)
        too_many = [{'name': f'P{i}', 'host': f'10.0.{i // 250}.{i % 250 + 1}', 'port': 443} for i in range(257)]
        with self.assertRaises(ValidationError):
            Mt5Settings.model_validate({'server_name': 'S', 'access_points': too_many})

    def test_mt5_access_point_selection_and_server_description(self):
        point = lambda i, on=True: {'name': f'AP {i}', 'host': f'192.0.2.{i}', 'port': 443, 'enabled': on}
        settings = Mt5Settings.model_validate({'server_name': 'MetaQuotes-Demo', 'company_name': 'MetaQuotes Ltd.',
                                               'server_details': {'CompanyName': 'MetaQuotes Ltd.', 'TimeZone': 120, 'ServerName': 'x', 'Note': ' a\x00b '},
                                               'access_points': [point(1), point(2, False)]})
        self.assertEqual([p.enabled for p in settings.access_points], [True, False])
        self.assertEqual(settings.server_details, {'CompanyName': 'MetaQuotes Ltd.', 'TimeZone': 120, 'Note': 'a b'})
        with self.assertRaises(ValidationError):
            Mt5Settings.model_validate({'server_name': 'S', 'access_points': [point(1, False)]})
        for bad in ({'bad key': 1}, {'Nested': {'a': 1}}, {f'K{i}': i for i in range(65)}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                Mt5Settings.model_validate({'server_name': 'S', 'server_details': bad, 'access_points': [point(1)]})

    def fake_reader(self, folder, output):
        parser = Path(folder) / 'reader'
        parser.write_text('#!/bin/sh\ncat <<\'JSON\'\n' + json.dumps(output) + '\nJSON\n'); parser.chmod(0o755)
        return patch.dict(os.environ, {'COREI_MT5_CATALOG_PARSER': str(parser)})

    def test_server_file_returns_full_description_per_server(self):
        from corei_os_mini.broker_catalog import parse_catalog
        output = {'skipped_servers': 1, 'servers': [
            {'server_name': 'MetaQuotes-Demo', 'details': {'ServerName': 'MetaQuotes-Demo', 'CompanyName': 'MetaQuotes Ltd.',
                                                           'TimeZone': 120, 'Type': 'Demo', 'Description': 'Demo server for testing'},
             'access_points': [{'name': 'Access Point 1', 'address': '78.140.180.201:443'},
                               {'name': 'Access Point 2', 'address': '[2a00:1178:1:60::c9]:1950'},
                               {'name': 'Unbracketed', 'address': '2001:db8::50:443'},
                               {'name': 'Duplicate', 'address': '78.140.180.201:443'},
                               {'name': 'Broken', 'address': 'not an address'}]},
            {'server_name': 'Empty-Server', 'details': {}, 'access_points': []},
            {'server_name': '', 'access_points': []}]}
        with tempfile.TemporaryDirectory() as folder, self.fake_reader(folder, output):
            catalog = parse_catalog(b'x' * 64)
        self.assertEqual({k: catalog[k] for k in ('server_count', 'access_point_count', 'skipped_servers', 'invalid_addresses',
                                                   'servers_without_access_points')}, {'server_count': 2, 'access_point_count': 3,
                         'skipped_servers': 2, 'invalid_addresses': 1, 'servers_without_access_points': 1})
        self.assertEqual(catalog['servers'][1], {'server_name': 'MetaQuotes-Demo', 'company_name': 'MetaQuotes Ltd.',
                         'details': {'CompanyName': 'MetaQuotes Ltd.', 'TimeZone': 120, 'Type': 'Demo', 'Description': 'Demo server for testing'},
                         'access_points': [{'name': 'Access Point 1', 'host': '78.140.180.201', 'port': 443},
                                           {'name': 'Access Point 2', 'host': '2a00:1178:1:60::c9', 'port': 1950},
                                           {'name': 'Unbracketed', 'host': '2001:db8::50', 'port': 443}]})

    def test_online_broker_directory_search(self):
        from corei_os_mini import broker_catalog
        found = {'servers': [{'server_name': 'VTMarkets-Live 3', 'details': {'CompanyName': 'Vantage Global Prime Pty Ltd'},
                              'access_points': [{'name': 'Access point', 'address': 'ad2b63bfc27a58ec7.awsglobalaccelerator.com:700'},
                                                {'name': 'Access point', 'address': '16.163.13.43:443'}]}]}
        with tempfile.TemporaryDirectory() as folder:
            parser = Path(folder) / 'reader'
            parser.write_text('#!/bin/sh\n[ "$1" = "--search" ] || exit 9\ncase "$2" in\n'
                              f"  VTMarkets) cat <<'JSON'\n{json.dumps(found)}\nJSON\n;;\n"
                              '  Nobody) echo SEARCH_NO_RESULTS >&2; exit 3;;\n  *) exit 5;;\nesac\n')
            parser.chmod(0o755)
            app.dependency_overrides[current_user] = lambda: EVERYONE
            try:
                with patch.dict(os.environ, {'COREI_MT5_CATALOG_PARSER': str(parser)}), TestClient(app, headers=HEADERS) as client:
                    result = client.post('/api/config/mt5/broker-search', json={'query': ' VTMarkets '})
                    self.assertEqual(result.status_code, 200, result.text)
                    body = result.json()
                    self.assertEqual((body['source'], body['query'], body['server_count'], body['access_point_count']), ('broker-directory', 'VTMarkets', 1, 2))
                    rows = client.get(f"/api/config/mt5/server-file/{body['catalog_id']}/access-points", params={'broker': 'vantage'}).json()
                    self.assertEqual([(r['host'], r['port']) for r in rows['items']], [('ad2b63bfc27a58ec7.awsglobalaccelerator.com', 700), ('16.163.13.43', 443)])
                    self.assertEqual(client.post('/api/config/mt5/broker-search', json={'query': 'Nobody'}).status_code, 404)
                    self.assertEqual(client.post('/api/config/mt5/broker-search', json={'query': 'Offline'}).status_code, 502)
                    self.assertEqual(client.post('/api/config/mt5/broker-search', json={'query': ';rm -rf'}).status_code, 422)
            finally:
                app.dependency_overrides.clear()
                broker_catalog._catalogs.clear()

    def test_server_file_search_across_all_brokers(self):
        from corei_os_mini import broker_catalog
        servers = [{'server_name': name, 'details': {'CompanyName': company}, 'access_points': [{'name': f'AP {i}', 'address': a} for i, a in enumerate(addrs, 1)]}
                   for name, company, addrs in [('MarketEquityInc-Demo', 'Market Equity Inc.', ['192.109.15.68:443', '192.109.15.68:1950']),
                                                ('Marketmates-Live', 'Marketmates Ltd', ['142.91.121.204:1950', '5.181.78.117:1950']),
                                                ('MetaQuotes-Demo', 'MetaQuotes Ltd.', ['78.140.180.201:443']),
                                                ('Other-Live', 'Market Makers LLC', ['203.0.113.9:443']),
                                                ('Empty-Broker', 'Nothing Readable Ltd', [])]]
        with tempfile.TemporaryDirectory() as folder, self.fake_reader(folder, {'servers': servers}):
            app.dependency_overrides[current_user] = lambda: EVERYONE
            try:
                with TestClient(app, headers=HEADERS) as client:
                    analysed = client.post('/api/config/mt5/server-file', content=b'x' * 64).json()
                    self.assertEqual((analysed['server_count'], analysed['access_point_count'], analysed['servers_without_access_points']), (5, 6, 1))
                    base = f"/api/config/mt5/server-file/{analysed['catalog_id']}"
                    market = client.get(base + '/access-points', params={'broker': 'market'}).json()
                    # Broker filter matches server name or company (Other-Live's company is Market Makers).
                    self.assertEqual((market['total'], market['server_matches']), (5, 3))
                    self.assertEqual([i['server_name'] for i in market['items']], ['MarketEquityInc-Demo'] * 2 + ['Marketmates-Live'] * 2 + ['Other-Live'])
                    self.assertEqual(client.get(base + '/access-points', params={'broker': 'market', 'q': '1950'}).json()['total'], 3)
                    self.assertEqual(client.get(base + '/access-points', params={'broker': 'market live', 'q': '142.91'}).json()['items'][0]['port'], 1950)
                    blank = client.get(base + '/access-points', params={'broker': 'empty'}).json()
                    self.assertEqual((blank['total'], blank['items'][0]['host'], blank['items'][0]['server_name']), (1, None, 'Empty-Broker'))
                    page = client.get(base + '/access-points', params={'offset': 4, 'limit': 2}).json()
                    self.assertEqual((page['total'], len(page['items'])), (7, 2))
                    listed = client.get(base + '/servers', params={'q': 'market 1950'}).json()
                    self.assertEqual([(i['server_name'], i['access_point_count']) for i in listed['items']], [('MarketEquityInc-Demo', 2), ('Marketmates-Live', 2)])
                    self.assertEqual(client.get(base + '/servers', params={'q': '203.0.113.9'}).json()['items'][0]['company_name'], 'Market Makers LLC')
                    detail = client.get(base + '/server', params={'name': 'MetaQuotes-Demo'}).json()
                    self.assertEqual((detail['company_name'], detail['access_points'][0]['host']), ('MetaQuotes Ltd.', '78.140.180.201'))
                    # Another user cannot read this analysis; unknown ids expire cleanly.
                    app.dependency_overrides[current_user] = lambda: Principal('bob', frozenset({'TRADING_OPS'}))
                    self.assertEqual(client.get(base + '/access-points').status_code, 404)
                    self.assertIn('expired', client.get('/api/config/mt5/server-file/unknown/access-points').text)
            finally:
                app.dependency_overrides.clear()
                broker_catalog._catalogs.clear()

    def test_rest_settings_require_secure_urls_and_oauth_redirect(self):
        upstox = RestApiSettings.model_validate({'api_base_url': 'https://api.upstox.com', 'auth_method': 'OAUTH2',
                                                 'oauth_redirect_url': 'https://corei-os-mini.coreeye.in/api/oauth/upstox/callback',
                                                 'notifier_webhook_url': 'https://corei-os-mini.coreeye.in/api/oauth/upstox/notify'})
        self.assertTrue(upstox.oauth_redirect_url.endswith('/callback'))
        RestApiSettings.model_validate({'api_base_url': 'https://api.upstox.com', 'auth_method': 'OAUTH2', 'oauth_redirect_url': 'http://localhost:8000/callback'})
        for bad in ({'oauth_redirect_url': 'http://example.com/callback'}, {'oauth_redirect_url': 'https://example.com', 'notifier_webhook_url': 'http://localhost/n'}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                RestApiSettings.model_validate({'api_base_url': 'https://api.upstox.com', 'auth_method': 'OAUTH2', **bad})
        RestApiSettings.model_validate({'api_base_url': 'https://api.example.com/v2/', 'auth_method': 'API_KEY'})
        for bad in ({'api_base_url': 'http://api.example.com', 'auth_method': 'API_KEY'},
                    {'api_base_url': 'https://user:pw@api.example.com', 'auth_method': 'API_KEY'},
                    {'api_base_url': 'https://api.example.com', 'stream_url': 'ws://x.example.com', 'auth_method': 'API_KEY'},
                    {'api_base_url': 'https://api.example.com', 'auth_method': 'OAUTH2'}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                RestApiSettings.model_validate(bad)

    def test_connection_settings_must_match_platform(self):
        base = dict(provider_id=str(uuid4()), code='c1', name='C', environment='LIVE', purpose='BOTH')
        self.assertEqual(ConnectionIn.model_validate({**base, 'platform': 'MT5', 'settings': MT5_SETTINGS}).code, 'C1')
        with self.assertRaises(ValidationError):
            ConnectionIn.model_validate({**base, 'platform': 'REST_API', 'settings': MT5_SETTINGS})

    def test_instrument_precision_rules_and_exact_decimals(self):
        instrument = InstrumentIn.model_validate(XAU)
        self.assertEqual(instrument.symbol, 'XAUUSD')
        self.assertIsInstance(instrument.tick_size, Decimal)
        for change in ({'tick_size': '0.001'}, {'quantity_step': '0.001'}, {'min_quantity': '0.015', 'quantity_precision': 3},
                       {'tick_size': '0'}, {'tick_size': '-1'}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                InstrumentIn.model_validate({**XAU, **change})

    def test_readiness_explains_every_blocker(self):
        row = dict(provider_status='ACTIVE', connection_status='ACTIVE', status='ACTIVE', trading_permission='TRADING',
                   platform='MT5', purpose='BOTH', environment='LIVE', credential_kind='MT5_LOGIN',
                   credential_access_level='FULL', connection_credential_kind=None, master_credential_kind='MT5_LOGIN',
                   margin_mode='HEDGING', server_utc_offset_minutes=120, max_orders_per_second=5)
        result = account_readiness(row, {'LIVE': {'trading_enabled': False}})
        self.assertEqual(result['data_blockers'], ['ADAPTER_NOT_AVAILABLE'])
        self.assertEqual(result['trading_blockers'], ['ADAPTER_NOT_AVAILABLE', 'ORDER_SENDING_OFF_LIVE'])
        self.assertEqual(result['warnings'], [])
        # The facts the broker decides must be known before trading.
        unknown = account_readiness({**row, 'margin_mode': 'UNKNOWN', 'server_utc_offset_minutes': None, 'master_credential_kind': None,
                                     'relationship': 'CLIENT', 'max_orders_per_second': None}, {'LIVE': {'trading_enabled': True}})
        self.assertEqual(unknown['trading_blockers'], ['ADAPTER_NOT_AVAILABLE', 'MARGIN_MODE_UNKNOWN', 'SERVER_CLOCK_UNKNOWN',
                                                       'MANDATE_MISSING', 'MASTER_PASSWORD_MISSING'])
        self.assertEqual(unknown['warnings'], ['RATE_LIMIT_NOT_SET'])
        missing = account_readiness({**row, 'credential_kind': None, 'status': 'INACTIVE'}, {'LIVE': {'trading_enabled': True}})
        self.assertIn('ACCOUNT_CREDENTIAL_MISSING', missing['data_blockers'])
        self.assertIn('ACCOUNT_INACTIVE', missing['data_blockers'])


class ApiGuardTests(unittest.TestCase):
    def test_sign_in_required(self):
        with TestClient(app) as client:
            for method, path in [('get', '/api/config/providers'), ('post', '/api/config/providers'),
                                 ('put', '/api/config/execution-controls/LIVE'), ('get', '/api/config/audit'),
                                 ('get', '/api/admin/users')]:
                self.assertEqual(getattr(client, method)(path).status_code, 401, path)

    def test_malformed_body_never_echoed(self):
        app.dependency_overrides[current_user] = lambda: EVERYONE
        try:
            with TestClient(app) as client:
                result = client.put(f'/api/config/accounts/{uuid4()}/credential', headers={**HEADERS, 'content-type': 'application/json'},
                                    content='{"credential":{"kind":"MT5_LOGIN","password":"do-not-echo"')
                self.assertEqual(result.status_code, 422)
                self.assertNotIn('do-not-echo', result.text)
                result = client.put(f'/api/config/accounts/{uuid4()}/credential', headers=HEADERS,
                                    json={'credential': {'kind': 'MT5_LOGIN', 'password': 'do-not-echo', 'extra': 'do-not-echo'}})
                self.assertEqual(result.status_code, 422)
                self.assertNotIn('do-not-echo', result.text)
        finally:
            app.dependency_overrides.clear()

    def test_credentials_bound_to_their_record(self):
        with tempfile.TemporaryDirectory() as folder:
            key = Path(folder) / 'key'; key.write_bytes(os.urandom(32)); key.chmod(0o600)
            with patch.dict(os.environ, {'COREI_BROKER_KEY_FILE': str(key)}):
                owner = uuid4()
                blob = credentials.encrypt(owner, {'password': 'p'})
                self.assertNotIn(b'"p"', blob)
                self.assertEqual(credentials.decrypt(owner, blob), {'password': 'p'})
                with self.assertRaises(Exception):
                    credentials.decrypt(uuid4(), blob)
                key.chmod(0o644)
                self.assertFalse(credentials.storage_ready())


@unittest.skipUnless(os.getenv('COREI_TEST_DATABASE_URL'), 'Set COREI_TEST_DATABASE_URL for PostgreSQL integration tests')
class ConfigurationPostgresTests(unittest.TestCase):
    def setUp(self):
        self.url = os.environ['COREI_TEST_DATABASE_URL']
        self.schema = 'test_cfg_' + uuid4().hex
        with psycopg.connect(self.url, autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        with self.connect() as db:
            for path in sorted((Path(__file__).parents[1] / 'sql').glob('[0-9][0-9][0-9]_*.sql')):
                db.execute(path.read_text())
            # Four-eyes on LIVE changes is covered in test_controls; these tests exercise other rules.
            db.execute("UPDATE cfg_policies SET value='false' WHERE key='four_eyes_live_changes'")
        self.folder = tempfile.TemporaryDirectory()
        key = Path(self.folder.name) / 'key'; key.write_bytes(os.urandom(32)); key.chmod(0o600)
        self.patches = [patch.dict(os.environ, {'COREI_BROKER_KEY_FILE': str(key)}),
                        patch('corei_os_mini.config_store._connect', self.connect)]
        for p in self.patches:
            p.start()
        app.dependency_overrides[current_user] = lambda: EVERYONE
        self.client = TestClient(app, headers=HEADERS)

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

    def post(self, entity, body, status=201):
        result = self.client.post(f'/api/config/{entity}', json=body)
        self.assertEqual(result.status_code, status, result.text)
        return result.json()

    def put(self, entity, record, changes, status=200):
        body = {k: v for k, v in record.items() if k in self.fields[entity]}
        body.update(changes, version=changes.get('version', record['version']))
        result = self.client.put(f"/api/config/{entity}/{record['id']}", json=body)
        self.assertEqual(result.status_code, status, result.text)
        return result.json()

    fields = {
        'connections': ('provider_id', 'code', 'name', 'platform', 'environment', 'purpose', 'settings', 'status'),
        'accounts': ('connection_id', 'code', 'name', 'external_account_id', 'base_currency', 'trading_permission', 'status'),
    }

    def test_full_configuration_lifecycle(self):
        provider = self.post('providers', {'code': 'icm', 'name': 'IC Markets', 'kind': 'BROKER'})
        self.assertEqual(provider['code'], 'ICM')
        self.post('providers', {'code': 'ICM', 'name': 'Duplicate', 'kind': 'BROKER'}, 409)

        mt5 = self.post('connections', dict(provider_id=provider['id'], code='ICM_MT5_DEMO', name='ICM MT5 demo',
                                            platform='MT5', environment='DEMO', purpose='BOTH', settings=MT5_SETTINGS))
        self.assertEqual(mt5['settings']['access_points'][0]['port'], 443)
        described = self.put('connections', mt5, {'settings': {**MT5_SETTINGS, 'company_name': 'Broker Ltd', 'server_details': {'TimeZone': 120},
                             'access_points': [*MT5_SETTINGS['access_points'], {'name': 'Access 2', 'host': '192.0.2.2', 'port': 443, 'enabled': False}]}})
        self.assertEqual((described['settings']['company_name'], described['settings']['server_details'], described['settings']['access_points'][1]['enabled']),
                         ('Broker Ltd', {'TimeZone': 120}, False))
        mt5 = described
        data_only = self.post('connections', dict(provider_id=provider['id'], code='ICM_FEED', name='Feed', platform='MT5',
                                                  environment='LIVE', purpose='MARKET_DATA', settings=MT5_SETTINGS))

        # Accounts: MT5 logins are digits; a data-only connection cannot host a trading account.
        self.post('accounts', dict(connection_id=mt5['id'], code='A1', name='A', external_account_id='abc', base_currency='USD'), 422)
        self.post('accounts', dict(connection_id=data_only['id'], code='A0', name='A', external_account_id='1',
                                   base_currency='USD', trading_permission='TRADING'), 422)
        account = self.post('accounts', dict(connection_id=mt5['id'], code='ICM_DEMO_1', name='Demo 1', holder_name=' Yatheesh N S ',
                                             external_account_id='51234567', base_currency='usd', trading_permission='TRADING'))
        self.assertEqual(account['holder_name'], 'Yatheesh N S')
        self.assertEqual([a['code'] for a in self.client.get('/api/config/accounts', params={'q': 'yatheesh'}).json()['items']], ['ICM_DEMO_1'])
        self.assertEqual(account['environment'], 'DEMO')
        self.assertIn('ACCOUNT_CREDENTIAL_MISSING', account['data_blockers'])
        self.assertIn('ORDER_SENDING_OFF_DEMO', account['trading_blockers'])
        self.post('accounts', dict(connection_id=mt5['id'], code='DUP', name='Dup', external_account_id='51234567',
                                   base_currency='USD'), 409)

        # Credentials: investor password cannot back a trading account; secrets never come back.
        path = f"/api/config/accounts/{account['id']}/credential"
        self.assertEqual(self.client.put(path, json={'credential': {'kind': 'MT5_LOGIN', 'password': 'investor-secret',
                                                                    'access_level': 'READ_ONLY'}}).status_code, 422)
        saved = self.client.put(path, json={'credential': {'kind': 'MT5_LOGIN', 'password': 'master-secret'}})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertNotIn('master-secret', saved.text)
        listing = self.client.get('/api/config/accounts').text
        self.assertNotIn('master-secret', listing)
        self.assertNotIn('ACCOUNT_CREDENTIAL_MISSING', listing)
        with self.connect() as db:
            row = db.execute('SELECT id, ciphertext FROM cfg_credentials WHERE account_id=%s', (account['id'],)).fetchone()
        self.assertEqual(credentials.decrypt(row['id'], bytes(row['ciphertext'])), {'password': 'master-secret'})

        # Versioning and immutable identity.
        renamed = self.put('accounts', account, {'name': 'Demo one'})
        self.assertEqual(renamed['version'], 2)
        self.put('accounts', account, {'name': 'stale'}, 409)
        self.put('accounts', renamed, {'external_account_id': '999'}, 422)

        # Instruments and mappings.
        xau = self.post('instruments', XAU)
        self.assertEqual((xau['tick_size'], xau['contract_size']), ('0.01', '100'))
        self.post('instruments', {**XAU, 'symbol': 'XAGUSD', 'tick_size': '0.001'}, 422)
        self.post('mappings', dict(connection_id=data_only['id'], instrument_id=xau['id'], broker_symbol='XAUUSD',
                                   usage='TRADING'), 422)
        mapping = self.post('mappings', dict(connection_id=mt5['id'], instrument_id=xau['id'], broker_symbol='XAUUSD.r',
                                             usage='BOTH', broker_contract_size='100', broker_price_digits=2))
        self.assertEqual(mapping['instrument_symbol'], 'XAUUSD')
        self.post('mappings', dict(connection_id=mt5['id'], instrument_id=xau['id'], broker_symbol='GOLD', usage='BOTH'), 409)

        # Cross-record protection.
        self.put('connections', mt5, {'purpose': 'MARKET_DATA'}, 422)
        self.assertEqual(self.client.delete(f"/api/config/providers/{provider['id']}?version=1").status_code, 409)

        # Order-sending switch: confirmation required to turn on; readiness follows it.
        controls = {c['environment']: c for c in self.client.get('/api/config/execution-controls').json()['items']}
        self.assertFalse(controls['LIVE']['trading_enabled'])
        on = {'trading_enabled': True, 'reason': 'Go-live approved', 'version': controls['DEMO']['version']}
        self.assertEqual(self.client.put('/api/config/execution-controls/DEMO', json=on).status_code, 422)
        enabled = self.client.put('/api/config/execution-controls/DEMO', json={**on, 'confirm_environment': 'DEMO'})
        self.assertEqual(enabled.status_code, 200, enabled.text)
        self.assertEqual(self.client.put('/api/config/execution-controls/DEMO', json={**on, 'confirm_environment': 'DEMO'}).status_code, 409)
        current = self.client.get(f"/api/config/accounts/{account['id']}").json()
        # Margin mode and server clock are not known until read from the broker or entered.
        self.assertEqual(current['trading_blockers'], ['ADAPTER_NOT_AVAILABLE', 'MARGIN_MODE_UNKNOWN', 'SERVER_CLOCK_UNKNOWN'])

        # REST connections hold API credentials; MT5 logins never go on a REST account.
        rest = self.post('connections', dict(provider_id=provider['id'], code='ICM_API', name='ICM API', platform='REST_API',
                                             environment='LIVE', purpose='BOTH',
                                             settings={'api_base_url': 'https://api.example.com', 'auth_method': 'API_KEY'}))
        cpath = f"/api/config/connections/{rest['id']}/credential"
        self.assertEqual(self.client.put(cpath, json={'credential': {'kind': 'OAUTH_CLIENT', 'client_id': 'x', 'client_secret': 'y'}}).status_code, 422)
        self.assertEqual(self.client.put(cpath, json={'credential': {'kind': 'API_KEY', 'api_key': 'k', 'api_secret': 'api-secret'}}).status_code, 200)
        rest_account = self.post('accounts', dict(connection_id=rest['id'], code='API_1', name='API', external_account_id='U-1', base_currency='USD'))
        self.assertEqual(self.client.put(f"/api/config/accounts/{rest_account['id']}/credential",
                                         json={'credential': {'kind': 'MT5_LOGIN', 'password': 'x'}}).status_code, 422)
        self.put('connections', rest, {'settings': {'api_base_url': 'https://api.example.com', 'auth_method': 'OAUTH2',
                                                    'oauth_redirect_url': 'https://app.example.com/cb'}}, 422)

        # Delete in dependency order; credential goes with its account.
        self.assertEqual(self.client.delete(f"/api/config/accounts/{rest_account['id']}?version=1").status_code, 204)

        # Audit: every change recorded with actor, never with secrets, and cannot be altered.
        audit = self.client.get('/api/config/audit?limit=1000').json()['items']
        actions = {(a['entity'], a['action']) for a in audit}
        for expected in [('provider', 'CREATE'), ('account', 'UPDATE'), ('account', 'SET_CREDENTIAL'),
                         ('execution control', 'ORDER_SENDING_ON'), ('account', 'DELETE')]:
            self.assertIn(expected, actions)
        self.assertTrue(all(a['actor'] == 'alice' for a in audit))
        self.assertNotIn('master-secret', str(audit))
        self.assertNotIn('api-secret', str(audit))
        with self.connect() as db, self.assertRaises(psycopg.errors.RaiseException):
            db.execute('UPDATE cfg_audit_log SET actor=%s', ('mallory',))

        summary = self.client.get('/api/config/summary').json()
        self.assertEqual(summary['counts'], {'providers': 1, 'connections': 3, 'accounts': 1, 'instruments': 1, 'mappings': 1, 'groups': 0, 'approvals': 0,
                                             'strategies': 0, 'deployments': 0})


if __name__ == '__main__':
    unittest.main()
