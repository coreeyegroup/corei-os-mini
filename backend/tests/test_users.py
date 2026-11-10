"""Users, roles and sessions against PostgreSQL (needs COREI_TEST_DATABASE_URL)."""
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
BOOT_PASSWORD = 'server-login-pass-1'


@unittest.skipUnless(os.getenv('COREI_TEST_DATABASE_URL'), 'Set COREI_TEST_DATABASE_URL for PostgreSQL integration tests')
class UsersAndRolesTests(unittest.TestCase):
    def setUp(self):
        self.url = os.environ['COREI_TEST_DATABASE_URL']
        self.schema = 'test_users_' + uuid4().hex
        with psycopg.connect(self.url, autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        with self.connect() as db:
            for path in sorted((Path(__file__).parents[1] / 'sql').glob('[0-9][0-9][0-9]_*.sql')):
                db.execute(path.read_text())
        self.patches = [patch('corei_os_mini.config_store._connect', self.connect),
                        patch.dict(os.environ, {'COREI_OPERATOR_USER': 'Owner', 'COREI_OPERATOR_PASSWORD_HASH': hash_password(BOOT_PASSWORD)})]
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

    def client(self):
        return TestClient(app, base_url='https://testserver', headers=HEADERS)

    def sign_in(self, username, password, status=200):
        client = self.client()
        result = client.post('/api/operator/login', json={'username': username, 'password': password})
        self.assertEqual(result.status_code, status, result.text)
        return client, result.json()

    def test_bootstrap_roles_sessions_and_admin_rules(self):
        # Wrong server password on an empty install creates nothing.
        self.sign_in('Owner', 'wrong-password-x', 401)
        owner, body = self.sign_in('Owner', BOOT_PASSWORD)
        self.assertEqual(body['roles'], ['ADMIN', 'OBSERVER', 'RISK_APPROVER', 'TRADING_OPS'])
        self.assertFalse(body['must_change_password'])
        # Bootstrap happens once: the server login no longer works for a second account.
        os.environ['COREI_OPERATOR_USER'] = 'Other'
        self.sign_in('Other', BOOT_PASSWORD, 401)

        # Admin creates an observer and a trader; weak passwords are refused.
        weak = owner.post('/api/admin/users', json={'username': 'olga', 'display_name': 'Olga', 'roles': ['OBSERVER'], 'temporary_password': 'aaaaaaaaaaaa'})
        self.assertEqual(weak.status_code, 422)
        olga = owner.post('/api/admin/users', json={'username': 'Olga', 'display_name': 'Olga Observer', 'roles': ['OBSERVER'],
                                                    'temporary_password': 'Temp-pass-4821'}).json()
        self.assertEqual(olga['username'], 'olga')
        self.assertNotIn('password_hash', olga)
        tom = owner.post('/api/admin/users', json={'username': 'tom', 'display_name': 'Tom Trader', 'roles': ['TRADING_OPS'],
                                                   'temporary_password': 'Temp-pass-7310'}).json()
        self.assertEqual(owner.post('/api/admin/users', json={'username': 'tom', 'display_name': 'x', 'roles': ['OBSERVER'],
                                                              'temporary_password': 'Temp-pass-7310'}).status_code, 409)

        # First sign-in requires a password change before anything else.
        observer, body = self.sign_in('olga', 'Temp-pass-4821')
        self.assertTrue(body['must_change_password'])
        self.assertEqual(observer.get('/api/config/providers').status_code, 403)
        self.assertEqual(observer.post('/api/operator/password', json={'current_password': 'nope', 'new_password': 'Observer-pass-91'}).status_code, 422)
        self.assertEqual(observer.post('/api/operator/password', json={'current_password': 'Temp-pass-4821', 'new_password': 'Observer-pass-91'}).status_code, 200)
        self.assertEqual(observer.get('/api/config/providers').status_code, 200)

        # Observer reads only.
        provider = {'code': 'ICM', 'name': 'IC Markets', 'kind': 'BROKER'}
        self.assertEqual(observer.post('/api/config/providers', json=provider).status_code, 403)
        self.assertEqual(observer.get('/api/admin/users').status_code, 403)

        # Trading operations changes configuration but cannot turn order sending on.
        trader, _ = self.sign_in('tom', 'Temp-pass-7310')
        trader.post('/api/operator/password', json={'current_password': 'Temp-pass-7310', 'new_password': 'Trader-pass-552'})
        self.assertEqual(trader.post('/api/config/providers', json=provider).status_code, 201)
        live = {c['environment']: c for c in trader.get('/api/config/execution-controls').json()['items']}['LIVE']
        on = {'trading_enabled': True, 'reason': 'Go live', 'version': live['version'], 'confirm_environment': 'LIVE'}
        self.assertEqual(trader.put('/api/config/execution-controls/LIVE', json=on).status_code, 403)
        # Risk approver (the owner here) can.
        self.assertEqual(owner.put('/api/config/execution-controls/LIVE', json=on).status_code, 200)
        # Trading operations may turn it off as a safety action.
        off = {'trading_enabled': False, 'reason': 'Halt trading', 'version': live['version'] + 1}
        self.assertEqual(trader.put('/api/config/execution-controls/LIVE', json=off).status_code, 200)
        self.assertEqual(observer.put('/api/config/execution-controls/LIVE', json={**off, 'version': live['version'] + 2}).status_code, 403)

        # Admin cannot change own roles, and the last admin cannot be removed.
        me = next(u for u in owner.get('/api/admin/users').json()['items'] if u['username'] == 'owner')
        self.assertEqual(owner.put(f"/api/admin/users/{me['id']}", json={'display_name': 'Owner', 'roles': ['OBSERVER'], 'status': 'ACTIVE',
                                                                          'version': me['version']}).status_code, 422)

        # Changing a user's roles ends their sessions immediately; disabling blocks sign-in.
        tom_row = next(u for u in owner.get('/api/admin/users').json()['items'] if u['username'] == 'tom')
        changed = owner.put(f"/api/admin/users/{tom['id']}", json={'display_name': 'Tom Trader', 'roles': ['OBSERVER'], 'status': 'ACTIVE',
                                                                     'version': tom_row['version']})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(trader.get('/api/config/providers').status_code, 401)
        self.assertEqual(owner.put(f"/api/admin/users/{tom['id']}", json={'display_name': 'Tom', 'roles': ['OBSERVER'], 'status': 'DISABLED',
                                                                           'version': tom_row['version']}).status_code, 409)
        owner.put(f"/api/admin/users/{tom['id']}", json={'display_name': 'Tom', 'roles': ['OBSERVER'], 'status': 'DISABLED', 'version': changed.json()['version']})
        self.sign_in('tom', 'Trader-pass-552', 401)

        # Password reset: temporary password, forced change, other sessions ended.
        self.assertEqual(owner.post(f"/api/admin/users/{olga['id']}/password", json={'temporary_password': 'Reset-pass-6620'}).status_code, 200)
        self.assertEqual(observer.get('/api/config/providers').status_code, 401)
        _, body = self.sign_in('olga', 'Reset-pass-6620')
        self.assertTrue(body['must_change_password'])

        # Sign-out ends the session server-side.
        self.assertEqual(owner.post('/api/operator/logout').status_code, 200)
        self.assertEqual(owner.get('/api/config/providers').status_code, 401)

        # Audit covers user changes; no password material is recorded.
        with self.connect() as db:
            audit = db.execute("SELECT action, before_state, after_state FROM cfg_audit_log WHERE entity='user'").fetchall()
        actions = {a['action'] for a in audit}
        for expected in ('BOOTSTRAP_ADMIN', 'CREATE', 'UPDATE', 'SIGN_IN', 'SIGN_IN_FAILED', 'CHANGE_PASSWORD', 'RESET_PASSWORD'):
            self.assertIn(expected, actions)
        self.assertNotIn('pbkdf2', str(audit))

    def test_lockout_after_repeated_failures(self):
        owner, _ = self.sign_in('Owner', BOOT_PASSWORD)
        owner.post('/api/admin/users', json={'username': 'lee', 'display_name': 'Lee', 'roles': ['OBSERVER'], 'temporary_password': 'Temp-pass-1934'})
        for _ in range(5):
            self.sign_in('lee', 'wrong-password', 401)
        operator_auth.attempts.clear()
        client = self.client()
        result = client.post('/api/operator/login', json={'username': 'lee', 'password': 'Temp-pass-1934'})
        self.assertEqual(result.status_code, 401)
        self.assertIn('locked', result.json()['detail'])
        lee = next(u for u in owner.get('/api/admin/users').json()['items'] if u['username'] == 'lee')
        owner.post(f"/api/admin/users/{lee['id']}/password", json={'temporary_password': 'Unlock-pass-2210'})
        self.sign_in('lee', 'Unlock-pass-2210')


if __name__ == '__main__':
    unittest.main()
