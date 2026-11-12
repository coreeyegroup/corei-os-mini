"""Institutional controls (release 0.11.0): four-eyes on LIVE changes, re-authentication,
archive, groups, per-account symbols, credential-use log, master + investor passwords,
CSV import/export, policies, alerts, health, broker facts, order checks, rate limits and
the daily broker login. PostgreSQL tests need COREI_TEST_DATABASE_URL."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
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

from corei_os_mini import alerts, broker_login, controls, operator_auth
from corei_os_mini import config_store as store
from corei_os_mini.api import app
from corei_os_mini.operator_auth import hash_password, optional_reauth, require_reauth
from corei_os_mini.order_checks import OrderDraft, check_order, filling_modes_from_flags
from corei_os_mini.rate_limit import TokenBucket

HEADERS = {'x-corei-request': 'workstation'}
PASSWORD = 'Strong-pass-5521'
MT5 = {'server_name': 'Broker-Live', 'access_points': [{'name': 'Broker-Live', 'host': '198.51.100.10', 'port': 443, 'source': 'DIRECTORY'}]}


class PureRules(unittest.TestCase):
    SPEC = {'spec': {'digits': 2, 'volume_min': '0.01', 'volume_max': '50', 'volume_step': '0.01', 'filling_modes': ['IOC', 'RETURN'],
                     'stops_level_points': 30, 'trade_mode': 'FULL'}, 'read_at': '2026-11-11T00:00:00Z', 'source': 'BROKER'}

    def test_order_checks_catch_what_the_broker_would_reject(self):
        good = check_order(OrderDraft(broker_symbol='XAUUSD.r', side='BUY', volume=Decimal('0.10')), self.SPEC)
        self.assertEqual((good['ok'], good['filling']), (True, 'IOC'))
        bad = check_order(OrderDraft(broker_symbol='XAUUSD.r', side='BUY', volume=Decimal('0.015'), filling='FOK',
                                     reference_price=Decimal('2400.00'), stop_loss=Decimal('2399.90')), self.SPEC)
        self.assertFalse(bad['ok'])
        text = ' '.join(bad['problems'])
        for expected in ('not a multiple of the step', 'Filling FOK is not allowed', 'closer than the broker minimum of 30 points'):
            self.assertIn(expected, text)
        close_only = {'spec': {**self.SPEC['spec'], 'trade_mode': 'CLOSE_ONLY'}}
        self.assertFalse(check_order(OrderDraft(broker_symbol='X', side='SELL', volume=Decimal('1')), close_only)['ok'])
        self.assertTrue(check_order(OrderDraft(broker_symbol='X', side='SELL', volume=Decimal('1'), closes_position=True), close_only)['ok'])
        self.assertIn('No symbol specification', check_order(OrderDraft(broker_symbol='X', side='BUY', volume=Decimal('1')), None)['problems'][0])

    def test_symbol_filling_flags_are_not_order_filling_numbers(self):
        # SYMBOL_FILLING_IOC = 2 means IOC is allowed (ORDER_FILLING_IOC is 1): error 10030 comes from mixing them.
        self.assertEqual(filling_modes_from_flags(2, execution_mode_market=True), ['IOC'])
        self.assertEqual(filling_modes_from_flags(3, execution_mode_market=False), ['FOK', 'IOC', 'RETURN'])

    def test_token_bucket(self):
        now = [0.0]
        bucket = TokenBucket(10, 2, clock=lambda: now[0])
        self.assertEqual([bucket.try_acquire(), bucket.try_acquire()], [0.0, 0.0])
        self.assertAlmostEqual(bucket.try_acquire(), 0.1)
        now[0] += 0.1
        self.assertEqual(bucket.try_acquire(), 0.0)

    def test_upstox_token_expiry_is_0330_ist(self):
        evening = datetime(2026, 11, 10, 14, 30, tzinfo=timezone.utc)   # 20:00 IST Tuesday
        self.assertEqual(broker_login.upstox_expiry(evening), datetime(2026, 11, 10, 22, 0, tzinfo=timezone.utc))  # 03:30 IST Wed
        early = datetime(2026, 11, 10, 21, 0, tzinfo=timezone.utc)      # 02:30 IST Wednesday
        self.assertEqual(broker_login.upstox_expiry(early), datetime(2026, 11, 10, 22, 0, tzinfo=timezone.utc))


@unittest.skipUnless(os.getenv('COREI_TEST_DATABASE_URL'), 'Set COREI_TEST_DATABASE_URL for PostgreSQL integration tests')
class Controls(unittest.TestCase):
    def setUp(self):
        self.url = os.environ['COREI_TEST_DATABASE_URL']
        self.schema = 'test_ctl_' + uuid4().hex
        with psycopg.connect(self.url, autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        with self.connect() as db:
            for path in sorted((Path(__file__).parents[1] / 'sql').glob('[0-9][0-9][0-9]_*.sql')):
                db.execute(path.read_text())
            for name in ('alice', 'bob'):
                db.execute("INSERT INTO cfg_users(id, username, display_name, password_hash, roles, must_change_password) "
                           "VALUES (%s,%s,%s,%s,ARRAY['ADMIN','TRADING_OPS','RISK_APPROVER'],false)",
                           (uuid4(), name, name.title(), hash_password(PASSWORD)))
        self.folder = tempfile.TemporaryDirectory()
        key = Path(self.folder.name) / 'key'
        key.write_bytes(os.urandom(32)); key.chmod(0o600)
        self.patches = [patch('corei_os_mini.config_store._connect', self.connect),
                        patch.dict(os.environ, {'COREI_BROKER_KEY_FILE': str(key)})]
        for p in self.patches:
            p.start()
        operator_auth.attempts.clear()
        self.alice, self.bob = self.sign_in('alice'), self.sign_in('bob')
        self.provider = self.ok(self.alice.post('/api/config/providers', json={'code': 'VTM', 'name': 'VT Markets', 'kind': 'BROKER',
                                                                               'legal_entity': 'VT Markets (Pty) Ltd', 'regulator': 'FSCA'}), 201)

    def tearDown(self):
        for c in (self.alice, self.bob):
            c.close()
        for p in reversed(self.patches):
            p.stop()
        self.folder.cleanup()
        with psycopg.connect(self.url, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def connect(self, _=None):
        return psycopg.connect(self.url, options='-c search_path=' + self.schema, row_factory=dict_row)

    def sign_in(self, username):
        client = TestClient(app, base_url='https://testserver', headers=HEADERS)
        self.assertEqual(client.post('/api/operator/login', json={'username': username, 'password': PASSWORD}).status_code, 200)
        return client

    def ok(self, result, status=200):
        self.assertEqual(result.status_code, status, result.text)
        return result.json()

    def connection(self, client, environment='LIVE', code='VTM_MT5', status=None):
        return client.post('/api/config/connections', json={
            'provider_id': self.provider['id'], 'code': code, 'name': code, 'platform': 'MT5', 'environment': environment,
            'purpose': 'BOTH', 'settings': MT5, 'server_utc_offset_minutes': 120, 'dst_rule': 'US', 'clock_source': 'MANUAL'})

    def test_four_eyes_on_live_changes(self):
        # DEMO is not held; LIVE creation waits for a second person.
        self.ok(self.connection(self.alice, 'DEMO', 'VTM_DEMO'), 201)
        pending = self.ok(self.connection(self.alice), 202)['change_request']
        self.assertEqual(pending['action'], 'CREATE_CONNECTION')
        self.assertEqual(self.ok(self.alice.get('/api/config/connections'))['items'][0]['code'], 'VTM_DEMO')
        # Same person cannot approve their own change; a second person can.
        self.ok(self.alice.post(f"/api/config/approvals/{pending['id']}/decision", json={'approve': True}), 403)
        applied = self.ok(self.bob.post(f"/api/config/approvals/{pending['id']}/decision", json={'approve': True, 'note': 'Checked'}))
        self.assertEqual(applied['status'], 'APPLIED')
        live = next(c for c in self.ok(self.alice.get('/api/config/connections'))['items'] if c['code'] == 'VTM_MT5')
        self.assertEqual((live['server_utc_offset_minutes'], live['dst_rule']), (120, 'US'))

        # Adding an address on LIVE waits; re-ordering or unticking does not.
        body = {k: live[k] for k in ('provider_id', 'code', 'name', 'platform', 'environment', 'purpose', 'status')}
        more = {**MT5, 'access_points': [*MT5['access_points'], {'name': 'x', 'host': '198.51.100.11', 'port': 443, 'source': 'MANUAL'}]}
        held = self.ok(self.alice.put(f"/api/config/connections/{live['id']}", json={**body, 'settings': more, 'version': live['version']}), 202)
        self.assertIn('198.51.100.11:443', held['change_request']['summary'])
        self.ok(self.alice.put(f"/api/config/connections/{live['id']}", json={**body, 'settings': more, 'version': live['version']}), 409)
        rejected = self.ok(self.bob.post(f"/api/config/approvals/{held['change_request']['id']}/decision", json={'approve': False, 'note': 'Unknown host'}))
        self.assertEqual(rejected['status'], 'REJECTED')
        self.ok(self.alice.put(f"/api/config/connections/{live['id']}", json={**body, 'name': 'Renamed', 'settings': MT5, 'version': live['version']}))

        # Replacing a LIVE password waits; the secret is stored encrypted, then applied by the approver.
        account = self.ok(self.alice.post('/api/config/accounts', json={'connection_id': live['id'], 'code': 'VTM_1', 'name': 'Gold',
                                                                       'external_account_id': '1298617', 'base_currency': 'USD'}), 201)
        first = {'credential': {'kind': 'MT5_LOGIN', 'password': 'first-pass', 'access_level': 'FULL'}}
        self.ok(self.alice.put(f"/api/config/accounts/{account['id']}/credential", json=first))
        swap = self.ok(self.alice.put(f"/api/config/accounts/{account['id']}/credential",
                                      json={'credential': {**first['credential'], 'password': 'second-pass'}}), 202)['change_request']
        with self.connect() as db:
            stored = db.execute('SELECT secret, payload FROM cfg_change_requests WHERE id=%s', (swap['id'],)).fetchone()
        self.assertNotIn(b'second-pass', bytes(stored['secret']))
        self.assertNotIn('second-pass', str(stored['payload']))
        self.ok(self.bob.post(f"/api/config/approvals/{swap['id']}/decision", json={'approve': True}))
        self.assertEqual(store.use_credential('accounts', account['id'], 'test', 'verify replacement')['password'], 'second-pass')
        with self.connect() as db:
            self.assertIsNone(db.execute('SELECT secret FROM cfg_change_requests WHERE id=%s', (swap['id'],)).fetchone()['secret'])

        # One-step setup on LIVE that adds a server is held, then applied with its password.
        setup = {'provider_id': self.provider['id'], 'environment': 'LIVE',
                 'server': {'server_name': 'Other-Live', 'access_points': [{'name': 'Other-Live', 'host': '198.51.100.20', 'port': 443}]},
                 'account': {'code': 'VTM_2', 'name': 'Hedge', 'external_account_id': '1298618', 'base_currency': 'USD'},
                 'credential': {'kind': 'MT5_LOGIN', 'password': 'setup-pass', 'access_level': 'FULL'}}
        request = self.ok(self.alice.post('/api/config/mt5/account-setup', json=setup), 202)['change_request']
        result = self.ok(self.bob.post(f"/api/config/approvals/{request['id']}/decision", json={'approve': True}))['result']
        self.assertEqual((result['connection_action'], result['account']['credential_kind']), ('CREATED', 'MT5_LOGIN'))

        # Turning four-eyes off is a recorded, alerted policy change.
        policy = next(p for p in self.ok(self.alice.get('/api/config/policies'))['items'] if p['key'] == 'four_eyes_live_changes')
        self.ok(self.alice.put('/api/config/policies/four_eyes_live_changes', json={'value': False, 'version': policy['version'], 'reason': 'Single operator'}))
        self.ok(self.connection(self.alice, code='VTM_MT5_2'), 201)
        audit = [a['action'] for a in self.ok(self.alice.get('/api/config/audit'))['items']]
        for action in ('REQUEST_CHANGE', 'APPLY_CHANGE', 'REJECT_CHANGE', 'SET_POLICY'):
            self.assertIn(action, audit)

    def test_upstox_account_in_one_step(self):
        no_key = self.alice.post('/api/config/upstox/account-setup', json={'account': {'code': 'UPSTOX_7AB123', 'name': 'Upstox',
                                 'external_account_id': '7AB123', 'base_currency': 'INR', 'trading_permission': 'READ_ONLY'}})
        self.assertEqual(no_key.status_code, 422, no_key.text)
        first = self.ok(self.alice.post('/api/config/upstox/account-setup', json={
            'account': {'code': 'UPSTOX_7AB123', 'name': 'Upstox', 'external_account_id': '7AB123', 'base_currency': 'INR', 'trading_permission': 'READ_ONLY'},
            'credential': {'client_id': 'app-key', 'client_secret': 'app-secret'}}), 201)
        self.assertEqual((first['provider_action'], first['connection_action']), ('CREATED', 'CREATED'))
        conn = first['connection']
        self.assertEqual((conn['platform'], conn['environment'], conn['settings']['auth_method']), ('REST_API', 'LIVE', 'OAUTH2'))
        self.assertTrue(conn['settings']['oauth_redirect_url'].endswith('/api/oauth/upstox/callback'))
        self.assertEqual(first['account']['data_blockers'], ['BROKER_LOGIN_NEEDED_TODAY'], 'only the daily login is missing for market data')
        second = self.ok(self.alice.post('/api/config/upstox/account-setup', json={'account': {
            'code': 'UPSTOX_8CD456', 'name': 'Upstox 2', 'external_account_id': '8CD456', 'base_currency': 'INR'}}), 201)
        self.assertEqual((second['provider_action'], second['connection_action']), ('REUSED', 'REUSED'), 'key already saved: not asked again')
        self.assertNotIn('app-secret', json.dumps([first, second]))

    def test_only_approver_may_self_approve_with_a_reason(self):
        pending = self.ok(self.connection(self.alice), 202)['change_request']
        self.assertEqual(self.ok(self.alice.get('/api/config/approvals'))['approvers'] >= 2, True)
        self.ok(self.alice.post(f"/api/config/approvals/{pending['id']}/decision", json={'approve': True, 'note': 'Need it now'}), 403)
        # Every other approver gone: the only approver self-approves, with a written reason.
        with self.connect() as db:
            db.execute("UPDATE cfg_users SET status='DISABLED' WHERE username <> 'alice' AND roles && ARRAY['RISK_APPROVER']::text[]")
            db.commit()
        self.assertEqual(self.ok(self.alice.get('/api/config/approvals'))['approvers'], 1)
        self.ok(self.alice.post(f"/api/config/approvals/{pending['id']}/decision", json={'approve': True, 'note': 'x'}), 422)
        done = self.ok(self.alice.post(f"/api/config/approvals/{pending['id']}/decision", json={'approve': True, 'note': 'Single-operator firm; checked'}))
        self.assertEqual(done['status'], 'APPLIED')
        with self.connect() as db:
            row = db.execute("SELECT after_state AS after FROM cfg_audit_log WHERE action='APPLY_CHANGE' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertTrue(row['after']['self_approved_only_approver'], 'recorded as self-approved in the audit log')

    def test_reauthentication_for_sensitive_actions(self):
        app.dependency_overrides.pop(require_reauth, None)
        app.dependency_overrides.pop(optional_reauth, None)
        demo = self.ok(self.connection(self.alice, 'DEMO', 'VTM_DEMO'), 201)
        account = self.ok(self.alice.post('/api/config/accounts', json={'connection_id': demo['id'], 'code': 'VTM_D', 'name': 'Demo',
                                                                       'external_account_id': '5001', 'base_currency': 'USD'}), 201)
        cred = {'credential': {'kind': 'MT5_LOGIN', 'password': 'p', 'access_level': 'FULL'}}
        self.ok(self.alice.put(f"/api/config/accounts/{account['id']}/credential", json=cred), 428)
        controls_ = {c['environment']: c for c in self.ok(self.alice.get('/api/config/execution-controls'))['items']}
        on = {'trading_enabled': True, 'reason': 'Start demo', 'version': controls_['DEMO']['version'], 'confirm_environment': 'DEMO'}
        self.ok(self.alice.put('/api/config/execution-controls/DEMO', json=on), 428)
        # Turning sending OFF never needs it.
        self.ok(self.alice.put('/api/config/execution-controls/DEMO', json={**on, 'trading_enabled': False}))
        self.ok(self.alice.post('/api/operator/reauth', json={'password': 'wrong'}), 401)
        self.assertEqual(self.ok(self.alice.post('/api/operator/reauth', json={'password': PASSWORD}))['minutes'], 5)
        self.ok(self.alice.put(f"/api/config/accounts/{account['id']}/credential", json=cred))
        # Another session of another user is not stepped up.
        self.ok(self.bob.delete(f"/api/config/accounts/{account['id']}/credential"), 428)

    def test_archive_groups_symbols_passwords_csv_and_health(self):
        demo = self.ok(self.connection(self.alice, 'DEMO', 'VTM_DEMO'), 201)
        group = self.ok(self.alice.post('/api/config/groups', json={'code': 'GOLD_DESK', 'name': 'Gold desk'}), 201)
        account = self.ok(self.alice.post('/api/config/accounts', json={
            'connection_id': demo['id'], 'code': 'VTM_D1', 'name': 'Demo gold', 'external_account_id': '5001', 'base_currency': 'USD',
            'relationship': 'CLIENT', 'group_ids': [group['id']], 'margin_mode': 'HEDGING', 'expires_on': '2026-11-13'}), 201)
        self.assertEqual((account['group_ids'], account['margin_mode_source']), ([group['id']], 'MANUAL'))
        self.assertIn('MANDATE_MISSING', account['trading_blockers'])
        self.assertIn('MARGIN_MODE_NOT_CONFIRMED_BY_BROKER', account['warnings'])
        self.ok(self.alice.put(f"/api/config/accounts/{account['id']}", json={
            **{k: account[k] for k in ('connection_id', 'code', 'name', 'external_account_id', 'base_currency', 'relationship')},
            'trading_permission': 'TRADING', 'version': account['version']}), 422)  # client without mandate cannot trade

        # Master and investor passwords side by side; every use is logged.
        url = f"/api/config/accounts/{account['id']}/credential"
        self.ok(self.alice.put(url, json={'credential': {'kind': 'MT5_LOGIN', 'password': 'master', 'access_level': 'FULL'}}))
        self.ok(self.alice.put(url, json={'credential': {'kind': 'MT5_LOGIN', 'password': 'investor', 'access_level': 'READ_ONLY'}}))
        self.assertEqual(store.use_credential('accounts', account['id'], 'mt5-adapter', 'Data session', 'READ_ONLY')['password'], 'investor')
        self.assertEqual(store.use_credential('accounts', account['id'], 'mt5-adapter', 'Trading session')['password'], 'master')
        uses = self.ok(self.alice.get(f"/api/config/accounts/{account['id']}/credential-use"))['items']
        self.assertEqual([u['purpose'] for u in uses], ['Trading session', 'Data session'])
        current = self.ok(self.alice.get(f"/api/config/accounts/{account['id']}"))
        self.assertEqual((current['master_credential_kind'], current['investor_credential_kind']), ('MT5_LOGIN', 'MT5_LOGIN'))
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT count(DISTINCT key_id) AS n FROM cfg_credentials').fetchone()['n'], 1)
            with self.assertRaises(psycopg.errors.RaiseException):
                db.execute('DELETE FROM cfg_credential_use')

        # Per-account symbol mapping beside the connection-wide one; specs and order check.
        instrument = self.ok(self.alice.post('/api/config/instruments', json={
            'symbol': 'XAUUSD', 'name': 'Gold', 'asset_class': 'METAL', 'base_currency': 'XAU', 'quote_currency': 'USD',
            'price_precision': 2, 'quantity_precision': 2, 'tick_size': '0.01', 'min_quantity': '0.01', 'quantity_step': '0.01',
            'contract_size': '100', 'contract_unit': 'oz'}), 201)
        mapping = {'connection_id': demo['id'], 'instrument_id': instrument['id'], 'broker_symbol': 'XAUUSD', 'usage': 'BOTH'}
        self.ok(self.alice.post('/api/config/mappings', json=mapping), 201)
        self.ok(self.alice.post('/api/config/mappings', json={**mapping, 'account_id': account['id'], 'broker_symbol': 'XAUUSD.r'}), 201)
        self.ok(self.alice.post('/api/config/mappings', json={**mapping, 'account_id': account['id'], 'broker_symbol': 'XAUUSD.x'}), 409)
        spec = {'digits': 2, 'volume_min': '0.01', 'volume_max': '50', 'volume_step': '0.01', 'filling_modes': ['IOC']}
        self.ok(self.alice.post(f"/api/config/accounts/{account['id']}/symbol-specs", json={'broker_symbol': 'XAUUSD.r', 'spec': spec}), 201)
        check = self.ok(self.alice.post(f"/api/config/accounts/{account['id']}/order-check",
                                        json={'broker_symbol': 'XAUUSD.r', 'side': 'BUY', 'volume': '0.015'}))
        self.assertFalse(check['ok'])
        self.assertEqual(check['spec_source'], 'MANUAL')

        # Broker facts read at login override typed ones; connection failure alerts.
        controls.record_account_facts(account['id'], margin_mode='NETTING', leverage=500)
        facts = self.ok(self.alice.get(f"/api/config/accounts/{account['id']}"))
        self.assertEqual((facts['margin_mode'], facts['margin_mode_source'], facts['leverage']), ('NETTING', 'BROKER', 500))
        channel = self.ok(self.alice.post('/api/config/alerts/channels', json={'kind': 'WEBHOOK', 'name': 'Ops hook',
                                                                               'target': 'https://hooks.example.com/corei', 'events': ['LOGIN_FAILED'],
                                                                               'secret': 'sign-me'}), 201)
        self.assertTrue(channel['has_secret'])
        self.assertNotIn('secret', channel)
        controls.set_connection_status(demo['id'], 'FAILED', error='Invalid account')
        sent = []
        with patch('corei_os_mini.alerts._post', lambda url, payload, headers: sent.append((url, payload, headers))):
            self.assertEqual(alerts.send_pending(store.transaction), 1)
        self.assertIn(b'Invalid account', sent[0][1])
        self.assertTrue(sent[0][2]['X-COREI-Signature'].startswith('sha256='))
        self.assertIn('CONNECTION_FAILED', self.ok(self.alice.get(f"/api/config/accounts/{account['id']}"))['data_blockers'])

        # Health board and checklist.
        health = self.ok(self.alice.get('/api/config/health'))
        self.assertEqual(health['network']['egress_ip'], '203.0.113.200')
        self.assertEqual([c['done'] for c in health['checklist']][:4], [True, True, True, True])
        self.assertIn('ACCOUNT_EXPIRING', health['accounts'][0]['warnings'])

        # CSV: dry run, all-or-nothing on error, formula-safe export.
        csv_text = ('connection_code,code,name,external_account_id,base_currency,relationship,groups\n'
                    'VTM_DEMO,VTM_D2,Second,5002,USD,OWN,GOLD_DESK\n'
                    'VTM_DEMO,VTM_D3,=HYPERLINK("x"),5003,USD,FIRM,\n')
        dry = self.ok(self.alice.post('/api/config/accounts/import', json={'csv': csv_text, 'dry_run': True}))
        self.assertEqual((dry['ok'], dry['applied'], dry['counts']), (True, False, {'ok': 2, 'error': 0}))
        self.assertEqual(len(self.ok(self.alice.get('/api/config/accounts'))['items']), 1)
        broken = csv_text + 'NOPE,VTM_D4,Bad,5004,USD,OWN,\nVTM_DEMO,VTM_D5,Dup,5001,USD,OWN,\n'
        failed = self.ok(self.alice.post('/api/config/accounts/import', json={'csv': broken, 'dry_run': False}))
        self.assertEqual((failed['applied'], [r['status'] for r in failed['rows']]), (False, ['OK', 'OK', 'ERROR', 'ERROR']))
        self.assertEqual(len(self.ok(self.alice.get('/api/config/accounts'))['items']), 1)
        self.assertTrue(self.ok(self.alice.post('/api/config/accounts/import', json={'csv': csv_text, 'dry_run': False}))['applied'])
        exported = self.alice.get('/api/config/export/accounts.csv').text
        self.assertIn('"\'=HYPERLINK(""x"")"', exported)  # a formula is exported as text
        self.ok(self.alice.post('/api/config/accounts/import', json={'csv': 'code,password\nX,secret', 'dry_run': True}), 422)

        # History means archive, not delete; archived records leave the default list.
        self.assertEqual(self.alice.delete(f"/api/config/accounts/{account['id']}?version={facts['version']}").status_code, 409)
        archived = self.ok(self.alice.post(f"/api/config/accounts/{account['id']}/archive", json={'version': facts['version'], 'reason': 'Closed at broker'}))
        self.assertEqual(archived['status'], 'ARCHIVED')
        listed = self.ok(self.alice.get('/api/config/accounts'))['items']
        self.assertNotIn(account['id'], [a['id'] for a in listed])
        self.assertIn(account['id'], [a['id'] for a in self.ok(self.alice.get('/api/config/accounts?include_archived=true'))['items']])

    def test_daily_broker_login_checks_the_account_holder(self):
        api = self.ok(self.alice.post('/api/config/connections', json={
            'provider_id': self.provider['id'], 'code': 'UPSTOX_API', 'name': 'Upstox', 'platform': 'REST_API', 'environment': 'DEMO',
            'purpose': 'BOTH', 'settings': {'api_base_url': 'https://api.upstox.com', 'auth_method': 'OAUTH2',
                                             'oauth_redirect_url': 'https://corei.example.com/api/oauth/upstox/callback'},
            'registered_egress_ip': '203.0.113.9', 'max_orders_per_second': '10'}), 201)
        self.ok(self.alice.put(f"/api/config/connections/{api['id']}/credential",
                               json={'credential': {'kind': 'OAUTH_CLIENT', 'client_id': 'app-key', 'client_secret': 'app-secret'}}))
        account = self.ok(self.alice.post('/api/config/accounts', json={'connection_id': api['id'], 'code': 'UPX_1', 'name': 'Equity',
                                                                       'external_account_id': 'AB1234', 'base_currency': 'INR'}), 201)
        self.assertIn('STATIC_IP_MISMATCH', account['data_blockers'])  # registered 203.0.113.9, this server is .200
        self.assertIn('BROKER_LOGIN_NEEDED_TODAY', account['trading_blockers'])
        started = self.ok(self.alice.post(f"/api/config/accounts/{account['id']}/broker-login/start"))
        self.assertIn('client_id=app-key', started['authorize_url'])
        state = started['authorize_url'].split('state=')[1]
        calls = []

        def fake_http(url, data, headers):
            calls.append((url, data))
            return {'access_token': 'day-token', 'user_id': 'ZZ9999'}
        with patch('corei_os_mini.broker_login._http', fake_http):
            wrong = self.alice.get(f'/api/oauth/upstox/callback?code=c1&state={state}', follow_redirects=False)
            self.assertIn('broker_login=failed', wrong.headers['location'])
            self.assertIn('belongs%20to%20client%20ZZ9999', wrong.headers['location'])
            again = self.alice.get(f'/api/oauth/upstox/callback?code=c1&state={state}', follow_redirects=False)
            self.assertIn('expired%20or%20was%20already%20used', again.headers['location'])
        self.assertIn(b'client_secret=app-secret', calls[0][1])
        with patch('corei_os_mini.broker_login._http', lambda url, data, headers: {'access_token': 'day-token', 'user_id': 'ab1234'}):
            result = self.ok(self.alice.post(f"/api/config/accounts/{account['id']}/broker-login/totp", json={'totp': '123456', 'pin': '654321'}))
        self.assertTrue(result['expires_at'])
        self.assertEqual(broker_login.session_token(account['id'], 'upstox-adapter', 'orders'), 'day-token')
        after = self.ok(self.alice.get(f"/api/config/accounts/{account['id']}"))
        self.assertNotIn('BROKER_LOGIN_NEEDED_TODAY', after['trading_blockers'])
        self.ok(self.alice.post(f"/api/config/accounts/{account['id']}/broker-login/totp", json={'totp': '12', 'pin': '654321'}), 422)


if __name__ == '__main__':
    unittest.main()
