"""Database editor: rows added, edited and deleted; columns added, changed and dropped; tables created and dropped;
secrets stay masked and cannot be written; every change is audited; the database's own message is shown."""
import importlib
import importlib.util

test_configuration = importlib.import_module('test_configuration') if importlib.util.find_spec('test_configuration') \
    else importlib.import_module('tests.test_configuration')
Base = test_configuration.ConfigurationPostgresTests
R = 'admin test change'


class DatabaseEditor(Base):
    def test_rows_columns_tables(self):
        c = self.client
        tables = {t['name'] for t in c.get('/api/admin/db/tables').json()['items']}
        self.assertIn('cfg_providers', tables)
        r = c.post('/api/admin/db/tables', json={'name': 'scratch_notes', 'reason': R})
        self.assertEqual(r.status_code, 201, r.text)
        r = c.post('/api/admin/db/tables/scratch_notes/columns', json={'name': 'note', 'type': 'text', 'reason': R})
        self.assertEqual(r.status_code, 200, r.text)
        r = c.post('/api/admin/db/tables/scratch_notes/columns', json={'name': 'qty', 'type': 'integer', 'nullable': False, 'default': '0', 'reason': R})
        self.assertEqual(r.status_code, 200, r.text)
        row = c.post('/api/admin/db/tables/scratch_notes/rows', json={'values': {'note': 'first', 'qty': '3'}, 'reason': R}).json()
        self.assertEqual((row['note'], row['qty']), ('first', 3))
        r = c.patch('/api/admin/db/tables/scratch_notes/rows', json={'key': {'id': row['id']}, 'values': {'note': 'edited', 'qty': None}, 'reason': R})
        self.assertEqual(r.status_code, 422, 'NOT NULL refused')
        self.assertIn('The database refused', r.json()['detail'])
        r = c.patch('/api/admin/db/tables/scratch_notes/rows', json={'key': {'id': row['id']}, 'values': {'note': 'edited'}, 'reason': R})
        self.assertEqual(r.json()['note'], 'edited')
        r = c.patch('/api/admin/db/tables/scratch_notes/columns/note', json={'rename_to': 'memo', 'reason': R})
        self.assertIn('memo', [x['name'] for x in r.json()['columns']])
        listing = c.get('/api/admin/db/tables/scratch_notes/rows?q=edit').json()
        self.assertEqual((listing['total'], listing['rows'][0]['memo']), (1, 'edited'))
        r = c.post('/api/admin/db/tables/scratch_notes/columns/qty/drop', json={'confirm': 'qty', 'reason': R})
        self.assertEqual(r.status_code, 422, 'typed table.column needed')
        r = c.post('/api/admin/db/tables/scratch_notes/columns/qty/drop', json={'confirm': 'scratch_notes.qty', 'reason': R})
        self.assertNotIn('qty', [x['name'] for x in r.json()['columns']])
        r = c.post('/api/admin/db/tables/scratch_notes/rows/delete', json={'key': {'id': row['id']}, 'reason': R})
        self.assertEqual(r.status_code, 204)
        r = c.post('/api/admin/db/tables/scratch_notes/drop', json={'confirm': 'scratch_notes', 'reason': R})
        self.assertEqual(r.status_code, 204)
        self.assertEqual(c.get('/api/admin/db/tables/scratch_notes/rows').status_code, 404)
        with self.connect() as db:
            actions = [x['action'] for x in db.execute("SELECT action FROM cfg_audit_log WHERE entity='db:scratch_notes' ORDER BY id").fetchall()]
        self.assertEqual(actions, ['DB_CREATE_TABLE', 'DB_ADD_COLUMN', 'DB_ADD_COLUMN', 'DB_INSERT', 'DB_UPDATE', 'DB_CHANGE_COLUMN', 'DB_DROP_COLUMN', 'DB_DELETE', 'DB_DROP_TABLE'])

    def test_secrets_masked_and_not_writable(self):
        c = self.client
        with self.connect() as db:
            db.execute("INSERT INTO cfg_users(id, username, display_name, password_hash, roles) VALUES (gen_random_uuid(), 'bob', 'Bob', 'scrypt$secret', ARRAY['OBSERVER']) ON CONFLICT DO NOTHING")
        users = c.get('/api/admin/db/tables/cfg_users/rows').json()
        hashed = next(x for x in users['columns'] if x['name'] == 'password_hash')
        self.assertTrue(hashed['secret'])
        self.assertTrue(all(r['password_hash'] == '‹hidden›' for r in users['rows']))
        key = {k['name']: users['rows'][0][k['name']] for k in users['columns'] if k['primary_key']}
        r = c.patch('/api/admin/db/tables/cfg_users/rows', json={'key': key, 'values': {'password_hash': 'x'}, 'reason': R})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(c.get('/api/admin/db/tables/no_such_table/rows').status_code, 404)
        self.assertEqual(c.post('/api/admin/db/tables', json={'name': 'Bad Name', 'reason': R}).status_code, 422)
