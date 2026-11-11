"""Infrastructure console (release 0.12.0): health checks, the table view with secret
columns hidden, the editor's rules (read-only tables, controlled tables, stale rows,
reason and log) and the read-only SQL console. Needs COREI_TEST_DATABASE_URL."""
from __future__ import annotations

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

from corei_os_mini import operator_auth
from corei_os_mini.api import app
from corei_os_mini.operator_auth import hash_password

HEADERS = {'x-corei-request': 'workstation'}
PASSWORD = 'Strong-pass-5521'


@unittest.skipUnless(os.getenv('COREI_TEST_DATABASE_URL'), 'Set COREI_TEST_DATABASE_URL for PostgreSQL integration tests')
class Infrastructure(unittest.TestCase):
    def setUp(self):
        self.url = os.environ['COREI_TEST_DATABASE_URL']
        self.schema = 'test_inf_' + uuid4().hex
        with psycopg.connect(self.url, autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        with self.connect() as db:
            for path in sorted((Path(__file__).parents[1] / 'sql').glob('[0-9][0-9][0-9]_*.sql')):
                db.execute(path.read_text())
            for name, roles in (('alice', ['ADMIN', 'TRADING_OPS']), ('olly', ['TRADING_OPS'])):
                db.execute("INSERT INTO cfg_users(id, username, display_name, password_hash, roles, must_change_password) "
                           "VALUES (%s,%s,%s,%s,%s,false)", (uuid4(), name, name.title(), hash_password(PASSWORD), roles))
        self.folder = tempfile.TemporaryDirectory()
        self.patches = [patch('corei_os_mini.config_store._connect', self.connect),
                        patch.dict(os.environ, {'COREI_BROKER_KEY_FILE': str(Path(self.folder.name) / 'key')})]
        for p in self.patches:
            p.start()
        operator_auth.attempts.clear()
        self.alice, self.olly = self.sign_in('alice'), self.sign_in('olly')

    def tearDown(self):
        for c in (self.alice, self.olly):
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
        return result.json() if result.content else None

    def test_health_lists_every_layer(self):
        report = self.ok(self.olly.get('/api/infra/health'))
        groups = {c['group'] for c in report['checks']}
        self.assertTrue({'Database', 'Services', 'Host', 'Broker tools'} <= groups)
        db = next(c for c in report['checks'] if c['name'] == 'PostgreSQL')
        self.assertEqual(db['status'], 'OK')
        migrations = next(c for c in report['checks'] if c['name'] == 'Migrations')
        self.assertEqual(migrations['status'], 'WARN')  # the test schema has no installer history
        # The database itself is for administrators only.
        self.ok(self.olly.get('/api/infra/database'), 403)
        self.ok(self.olly.get('/api/infra/tables/cfg_users'), 403)

    def test_table_view_hides_secrets_and_filters(self):
        overview = self.ok(self.alice.get('/api/infra/database'))
        modes = {t['name']: t['mode'] for t in overview['tables']}
        self.assertEqual((modes['cfg_audit_log'], modes['cfg_credentials'], modes['cfg_accounts'], modes['cfg_instruments']),
                         ('READ_ONLY', 'READ_ONLY', 'READ_ONLY', 'READ_ONLY'))
        self.assertEqual((modes['corei_paper_runs'], modes['cfg_user_prefs']), ('EDIT', 'EDIT'))
        users = self.ok(self.alice.get('/api/infra/tables/cfg_users'))
        self.assertEqual({r['password_hash'] for r in users['rows']}, {'••••••'})
        self.assertTrue(next(c for c in users['columns'] if c['name'] == 'password_hash')['secret'])
        self.assertEqual(users['total'], 2)
        found = self.ok(self.alice.get('/api/infra/tables/cfg_users', params={'f.username': 'OLL'}))
        self.assertEqual([r['username'] for r in found['rows']], ['olly'])
        self.assertEqual(self.ok(self.alice.get('/api/infra/tables/cfg_users', params={'q': 'alice'}))['total'], 1)
        ordered = self.ok(self.alice.get('/api/infra/tables/cfg_users', params={'sort': 'username', 'desc': 'true', 'limit': 1}))
        self.assertEqual((ordered['rows'][0]['username'], ordered['total']), ('olly', 2))
        self.ok(self.alice.get('/api/infra/tables/no_such_table'), 404)
        self.ok(self.alice.get('/api/infra/tables/bad;name'), 422)

    def test_editing_rules_and_log(self):
        row = {'run_id': '00000000-0000-0000-0000-0000000000aa', 'instrument_id': 'XAUUSD', 'strategy_id': 'S1', 'execution_mode': 'PAPER',
               'request_json': '{"a": 1}', 'result_json': '{}'}
        self.ok(self.alice.post('/api/infra/tables/corei_paper_runs/rows', json={'values': row, 'reason': 'no'}), 422)  # reason too short
        created = self.ok(self.alice.post('/api/infra/tables/corei_paper_runs/rows', json={'values': row, 'reason': 'Seed a test run'}), 201)
        table = self.ok(self.alice.get('/api/infra/tables/corei_paper_runs'))
        key = {k: created[k] for k in table['key']}
        # Values travel as text and are cast to the column type; bad input shows PostgreSQL's message.
        bad = self.ok(self.alice.patch('/api/infra/tables/corei_paper_runs/rows', json={
            'key': key, 'changes': {'request_json': '{not json'}, 'original': {'request_json': created['request_json']}, 'reason': 'Fix input'}), 422)
        self.assertIn('json', str(bad['detail']).lower())
        updated = self.ok(self.alice.patch('/api/infra/tables/corei_paper_runs/rows', json={
            'key': key, 'changes': {'strategy_id': 'S2'}, 'original': {'strategy_id': 'S1'}, 'reason': 'Correct the strategy'}))
        self.assertEqual(updated['strategy_id'], 'S2')
        # Someone else changed it meanwhile: refused.
        stale = self.ok(self.alice.patch('/api/infra/tables/corei_paper_runs/rows', json={
            'key': key, 'changes': {'strategy_id': 'S3'}, 'original': {'strategy_id': 'S1'}, 'reason': 'Correct again'}), 409)
        self.assertIn('changed since you loaded it', stale['detail'])
        # Read-only and secret: refused whatever the role.
        self.ok(self.alice.patch('/api/infra/tables/cfg_audit_log/rows', json={'key': {'id': '1'}, 'changes': {'reason': 'x'}, 'reason': 'Try it'}), 403)
        user = self.ok(self.alice.get('/api/infra/tables/cfg_user_prefs'))
        self.assertEqual(user['mode'], 'EDIT')
        self.ok(self.olly.patch('/api/infra/tables/corei_paper_runs/rows', json={
            'key': key, 'changes': {'strategy_id': 'S9'}, 'reason': 'Not an admin'}), 403)
        # Controlled configuration tables open only by policy.
        self.ok(self.alice.post('/api/infra/tables/cfg_instruments/rows', json={'values': {'symbol': 'XAUUSD'}, 'reason': 'Direct add'}), 403)
        with self.connect() as db:
            db.execute("UPDATE cfg_policies SET value='true'::jsonb WHERE key='infra_edit_controlled_tables'")
        self.assertEqual(self.ok(self.alice.get('/api/infra/tables/cfg_instruments'))['mode'], 'EDIT')
        self.ok(self.alice.post('/api/infra/tables/corei_paper_runs/rows/delete', json={'key': key, 'reason': 'Remove the test run'}), 204)
        log = self.ok(self.alice.get('/api/infra/edits'))['items']
        self.assertEqual([e['action'] for e in log], ['DELETE', 'UPDATE', 'INSERT'])
        self.assertEqual((log[1]['before']['strategy_id'], log[1]['after']['strategy_id'], log[1]['reason']), ('S1', 'S2', 'Correct the strategy'))
        with self.connect() as db, self.assertRaises(psycopg.Error):
            db.execute('DELETE FROM infra_data_edits')
        audit = [a['action'] for a in self.ok(self.alice.get('/api/config/audit'))['items']]
        self.assertTrue({'INFRA_INSERT', 'INFRA_UPDATE', 'INFRA_DELETE'} <= set(audit))

    def test_sql_console_is_read_only(self):
        result = self.ok(self.alice.post('/api/infra/query', json={'sql': 'SELECT username, roles FROM cfg_users ORDER BY username'}))
        self.assertEqual((result['columns'], [r['username'] for r in result['rows']]), (['username', 'roles'], ['alice', 'olly']))
        for statement, status in (('DELETE FROM cfg_users', 422), ('SELECT 1; DELETE FROM cfg_users', 422),
                                  ('SELECT password_hash FROM cfg_users', 403), ('SELECT * FROM cfg_credentials', 403),
                                  ("WITH x AS (DELETE FROM cfg_user_prefs RETURNING *) SELECT * FROM x", 422)):
            self.ok(self.alice.post('/api/infra/query', json={'sql': statement}), status)
        self.ok(self.olly.post('/api/infra/query', json={'sql': 'SELECT 1'}), 403)
