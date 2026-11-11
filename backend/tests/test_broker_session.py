"""Reading real data from an MT5 account (release 0.12.0): normalising SDK facts, the
password travelling on stdin only, address fallback, and what gets stored.
PostgreSQL tests need COREI_TEST_DATABASE_URL."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from corei_os_mini import broker_session, operator_auth
from corei_os_mini.api import app
from corei_os_mini.operator_auth import hash_password

HEADERS = {'x-corei-request': 'workstation'}
PASSWORD = 'Strong-pass-5521'

SDK_ACCOUNT = {'AccountBalance': 1000.5, 'AccountLeverage': 500, 'AccountCurrency': 'USD', 'AccountMethod': 'Hedging',
               'Account.Group': 'real\\VT-STD', 'Account.MarginStopOut': 50, 'Account.MaxPositions': 0}
SDK_SYMBOLS = {
    'EURUSD': {'Info.Digits': 5, 'Info.ContractSize': 100000, 'Info.FillPolicy': 2, 'Info.MarginCurrency': 'EUR',
               'Group.MinVolume': 0.01, 'Group.MaxVolume': 100, 'Group.VolumeStep': 0.01, 'Group.StopsLevel': 10},
    # Raw server units (1e-8 lot) are converted to lots.
    'XAUUSD': {'Info.Digits': 2, 'Info.FillPolicy': 3, 'Group.MinVolume': 1000000, 'Group.MaxVolume': 5000000000,
               'Group.VolumeStep': 1000000, 'Info.TradeMode': 'CloseOnly'},
    'ODD': {'Info.Digits': 2},
}

# A stand-in reader: records what it was given, fails the first address, answers on the second.
FAKE_READER = r'''#!/usr/bin/env python3
import json, sys, os
assert sys.argv[1:] == ['--session'], sys.argv
request = json.load(sys.stdin)
open(os.environ['FAKE_LOG'], 'w').write(json.dumps({'argv': sys.argv, 'request': request}))
if request['password'] != 'investor-pass':
    print(json.dumps({'connected': False, 'attempts': [{'host': h['host'], 'port': h['port'], 'ok': False, 'error': 'Invalid account'} for h in request['hosts']]}))
    sys.exit(0)
print(json.dumps({'connected': True, 'host': request['hosts'][-1]['host'], 'port': request['hosts'][-1]['port'], 'connect_ms': 41,
    'attempts': [{'host': request['hosts'][0]['host'], 'port': 443, 'ok': False, 'error': 'timeout'}, {'host': request['hosts'][-1]['host'], 'port': 443, 'ok': True}],
    'utc_now': '2026-11-11T10:00:00', 'server_time': '2026-11-11T12:00:03', 'account': ACCOUNT, 'symbols_available': 3,
    'symbols': SYMBOLS, 'symbol_error': None, 'sdk_version': '5.3677.1.2'}))
'''.replace('ACCOUNT', json.dumps(SDK_ACCOUNT)).replace('SYMBOLS', json.dumps(SDK_SYMBOLS))


class Normalise(unittest.TestCase):
    def test_account_facts(self):
        facts = broker_session.account_facts(SDK_ACCOUNT)
        self.assertEqual((facts['margin_mode'], facts['leverage'], facts['broker_group'], facts['stop_out_level']),
                         ('HEDGING', 500, 'real\\VT-STD', 50.0))
        self.assertIsNone(facts['max_open_positions'])  # 0 means "no cap", not a cap of zero
        self.assertEqual(broker_session.account_facts({'AccountMethod': 'Retail Netting'})['margin_mode'], 'NETTING')
        self.assertIsNone(broker_session.account_facts({'AccountMethod': 'Default'})['margin_mode'])

    def test_symbol_specs(self):
        eur, _ = broker_session.symbol_spec(SDK_SYMBOLS['EURUSD'])
        self.assertEqual((eur['digits'], eur['volume_min'], eur['filling_modes'], eur['stops_level_points']), (5, '0.01', ['IOC', 'RETURN'], 10))
        gold, _ = broker_session.symbol_spec(SDK_SYMBOLS['XAUUSD'])
        self.assertEqual((gold['volume_min'], gold['volume_max'], gold['volume_step'], gold['trade_mode']), ('0.01', '50', '0.01', 'CLOSE_ONLY'))
        self.assertEqual(broker_session.symbol_spec(SDK_SYMBOLS['ODD']), (None, 'volume or digits not found'))

    def test_server_clock_offset_rounds_to_quarter_hours(self):
        self.assertEqual(broker_session._offset_minutes('2026-11-11T12:00:03', '2026-11-11T10:00:00'), 120)
        self.assertEqual(broker_session._offset_minutes('2026-11-11T15:29:50', '2026-11-11T10:00:00'), 330)
        self.assertIsNone(broker_session._offset_minutes(None, '2026-11-11T10:00:00'))


@unittest.skipUnless(os.getenv('COREI_TEST_DATABASE_URL'), 'Set COREI_TEST_DATABASE_URL for PostgreSQL integration tests')
class ReadFromBroker(unittest.TestCase):
    def setUp(self):
        self.url = os.environ['COREI_TEST_DATABASE_URL']
        self.schema = 'test_brk_' + uuid4().hex
        with psycopg.connect(self.url, autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        with self.connect() as db:
            for path in sorted((Path(__file__).parents[1] / 'sql').glob('[0-9][0-9][0-9]_*.sql')):
                db.execute(path.read_text())
            db.execute("UPDATE cfg_policies SET value='false'::jsonb WHERE key='four_eyes_live_changes'")
            db.execute("INSERT INTO cfg_users(id, username, display_name, password_hash, roles, must_change_password) "
                       "VALUES (%s,'alice','Alice',%s,ARRAY['ADMIN','TRADING_OPS','RISK_APPROVER'],false)", (uuid4(), hash_password(PASSWORD)))
        self.folder = tempfile.TemporaryDirectory()
        key = Path(self.folder.name) / 'key'
        key.write_bytes(os.urandom(32)); key.chmod(0o600)
        reader = Path(self.folder.name) / 'reader'
        reader.write_text(FAKE_READER); reader.chmod(stat.S_IRWXU)
        self.log = Path(self.folder.name) / 'log.json'
        self.patches = [patch('corei_os_mini.config_store._connect', self.connect),
                        patch.dict(os.environ, {'COREI_BROKER_KEY_FILE': str(key), 'COREI_MT5_CATALOG_PARSER': str(reader),
                                                'FAKE_LOG': str(self.log)})]
        for p in self.patches:
            p.start()
        operator_auth.attempts.clear()
        self.client = TestClient(app, base_url='https://testserver', headers=HEADERS)
        self.assertEqual(self.client.post('/api/operator/login', json={'username': 'alice', 'password': PASSWORD}).status_code, 200)

    def tearDown(self):
        self.client.close()
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

    def test_read_stores_facts_clock_and_symbols(self):
        provider = self.ok(self.client.post('/api/config/providers', json={'code': 'VTM', 'name': 'VT Markets', 'kind': 'BROKER'}), 201)
        settings = {'server_name': 'VTMarkets-Live 3', 'access_points': [
            {'name': 'Live 3', 'host': '198.51.100.10', 'port': 443}, {'name': 'Live 3', 'host': '198.51.100.11', 'port': 443}]}
        connection = self.ok(self.client.post('/api/config/connections', json={
            'provider_id': provider['id'], 'code': 'VTM_L3', 'name': 'Live 3', 'platform': 'MT5', 'environment': 'LIVE',
            'purpose': 'BOTH', 'settings': settings}), 201)
        account = self.ok(self.client.post('/api/config/accounts', json={'connection_id': connection['id'], 'code': 'VTM_1',
                                                                        'name': 'Gold', 'external_account_id': '1298617', 'base_currency': 'USD'}), 201)
        url = f"/api/config/accounts/{account['id']}"
        self.assertIn('Save the investor', self.ok(self.client.post(url + '/read-from-broker'), 409)['detail'])

        # Wrong password: nothing stored on the account, the connection is marked FAILED, the read is kept.
        self.ok(self.client.put(url + '/credential', json={'credential': {'kind': 'MT5_LOGIN', 'password': 'wrong', 'access_level': 'READ_ONLY'}}))
        failed = self.ok(self.client.post(url + '/read-from-broker'))
        self.assertEqual((failed['connected'], failed['error']), (False, 'Invalid account'))

        self.ok(self.client.put(url + '/credential', json={'credential': {'kind': 'MT5_LOGIN', 'password': 'investor-pass', 'access_level': 'READ_ONLY'}}))
        result = self.ok(self.client.post(url + '/read-from-broker', json={'max_symbols': 100}))
        # The password reached the reader on stdin only, with the account's addresses in routing order.
        sent = json.loads(self.log.read_text())
        self.assertEqual(sent['argv'][1:], ['--session'])
        self.assertEqual((sent['request']['login'], [h['host'] for h in sent['request']['hosts']]), (1298617, ['198.51.100.10', '198.51.100.11']))
        self.assertEqual((result['connected'], result['host'], result['credential_level'], result['server_time_offset_minutes']),
                         (True, '198.51.100.11', 'READ_ONLY', 120))
        self.assertEqual((result['symbols_stored'], result['symbols_unmapped']), (2, [{'symbol': 'ODD', 'reason': 'volume or digits not found'}]))
        self.assertNotIn('investor-pass', json.dumps(result))

        stored = next(a for a in self.ok(self.client.get('/api/config/accounts'))['items'] if a['id'] == account['id'])
        self.assertEqual((stored['margin_mode'], stored['margin_mode_source'], stored['leverage'], stored['broker_group']),
                         ('HEDGING', 'BROKER', 500, 'real\\VT-STD'))
        self.assertIsNotNone(stored['broker_facts_read_at'])
        conn = next(c for c in self.ok(self.client.get('/api/config/connections'))['items'] if c['id'] == connection['id'])
        self.assertEqual((conn['live_status']['state'], conn['live_status']['current_host'], conn['live_status']['server_time_offset_ms']),
                         ('CONNECTED', '198.51.100.11', 7_200_000))
        specs = {s['broker_symbol']: s for s in self.ok(self.client.get(url + '/symbol-specs'))['items']}
        self.assertEqual((specs['XAUUSD']['spec']['volume_max'], specs['XAUUSD']['source']), ('50', 'BROKER'))
        # The order check now uses what the broker said.
        check = self.ok(self.client.post(url + '/order-check', json={'broker_symbol': 'XAUUSD', 'side': 'BUY', 'volume': '0.10'}))
        self.assertIn('close', ' '.join(check['problems']).lower())

        reads = self.ok(self.client.get(url + '/broker-reads'))['items']
        self.assertEqual([r['connected'] for r in reads], [True, False])
        uses = self.ok(self.client.get(url + '/credential-use'))['items']
        self.assertEqual(len(uses), 2)
        audit = [a['action'] for a in self.ok(self.client.get('/api/config/audit'))['items']]
        self.assertIn('READ_FROM_BROKER', audit)
        self.assertIn('BROKER_FACTS', audit)
