"""Run against a disposable PostgreSQL database using COREI_TEST_DATABASE_URL."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from fastapi.testclient import TestClient
from corei_os_mini.api import app
from corei_os_mini.operator_auth import require_operator
from corei_os_mini.broker_accounts import save_catalog
from corei_os_mini.broker_secrets import decrypt_password


@unittest.skipUnless(os.getenv('COREI_TEST_DATABASE_URL'),'Set COREI_TEST_DATABASE_URL for PostgreSQL integration tests')
class BrokerPostgresTests(unittest.TestCase):
    def test_migrations_atomic_save_duplicate_rollback_and_password_edit(self):
        url=os.environ['COREI_TEST_DATABASE_URL']; schema='test_broker_'+uuid4().hex
        with psycopg.connect(url,autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        def connect(_=None):return psycopg.connect(url,options='-c search_path='+schema,row_factory=dict_row)
        try:
            with connect() as db:
                for path in sorted((Path(__file__).parents[1]/'sql').glob('*.sql')):db.execute(path.read_text())
            with tempfile.TemporaryDirectory() as folder:
                key=Path(folder)/'key';key.write_bytes(os.urandom(32));key.chmod(0o600)
                with patch.dict(os.environ,{'COREI_BROKER_KEY_FILE':str(key)}),patch('corei_os_mini.broker_accounts._connect',connect),patch('corei_os_mini.registry._connect',connect):
                    app.dependency_overrides[require_operator]=lambda:'operator'
                    with TestClient(app) as client:
                        points=save_catalog(b'x'*32,[dict(server_name='Broker-Demo',name='Access 1',host='192.0.2.1',port=443)])['items']
                        payload=dict(name='Account A',broker='Broker',mode='SANDBOX',login='123',password='secret-one',server_name='Broker-Demo',access_point_id=str(points[0]['id']))
                        first=client.post('/api/registry/broker-accounts',json=payload)
                        self.assertEqual(first.status_code,201,first.text)
                        a=first.json();self.assertNotIn('secret-one',first.text)
                        self.assertFalse(a['execution_available']);self.assertEqual(a['session_status'],'NOT_CONNECTED')
                        self.assertEqual(client.post('/api/registry/broker-accounts',json=payload).status_code,409)
                        second=client.post('/api/registry/broker-accounts',json={**payload,'name':'Account B','login':'456'})
                        self.assertEqual(second.status_code,201,second.text)
                        other=save_catalog(b'y'*32,[dict(server_name='Broker-Demo2',name='Access 2',host='192.0.2.2',port=443)])['items']
                        third=client.post('/api/registry/broker-accounts',json={**payload,'name':'Account C','login':'789','server_name':'Broker-Demo2','access_point_id':str(other[0]['id'])})
                        self.assertEqual(third.status_code,201,third.text)
                        live=client.post('/api/registry/broker-accounts',json={**payload,'login':'999','mode':'LIVE'})
                        self.assertEqual(live.status_code,422)
                        with connect() as db:
                            # Same server shares one connection and profile; a second server gets its own.
                            for table,count in [('corei_providers',1),('corei_connections',2),('corei_connector_profiles',2),('corei_accounts',3),('corei_broker_secrets',3)]:
                                self.assertEqual(db.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(table))).fetchone()['n'],count)
                            secret=db.execute('SELECT b.id,b.ciphertext FROM corei_broker_secrets b JOIN corei_broker_account_settings s ON s.secret_id=b.id WHERE s.account_id=%s',(a['id'],)).fetchone()
                        self.assertEqual(decrypt_password(secret['id'],bytes(secret['ciphertext'])),'secret-one')
                        update=dict(name='Account renamed',access_point_id=a['access_point_id'],enabled=False,version=a['version'],password='secret-two')
                        updated=client.put('/api/registry/broker-accounts/'+a['id'],json=update)
                        self.assertEqual(updated.status_code,200,updated.text)
                        self.assertEqual(client.put('/api/registry/broker-accounts/'+a['id'],json=update).status_code,409)
                        self.assertEqual(client.patch('/api/registry/accounts/'+a['id'],json={'enabled':True}).status_code,409)
                        listing=client.get('/api/registry/broker-accounts').json()
                        self.assertEqual(listing['total'],3)
                        self.assertNotIn('secret-',str(listing))
                        with connect() as db:
                            new=db.execute('SELECT ciphertext FROM corei_broker_secrets WHERE id=%s',(secret['id'],)).fetchone()
                        self.assertEqual(decrypt_password(secret['id'],bytes(new['ciphertext'])),'secret-two')
                        # Paper-only boundary holds in the database even if the API is bypassed.
                        with connect() as db:
                            with self.assertRaises(psycopg.errors.CheckViolation):
                                db.execute("INSERT INTO corei_accounts(id,connection_id,name,external_reference,mode) "
                                           "VALUES(%s,(SELECT id FROM corei_connections LIMIT 1),'x','live-1','LIVE')",(uuid4(),))
                        with connect() as db:
                            db.execute('ALTER TABLE corei_accounts DISABLE TRIGGER corei_accounts_paper_only')
                            legacy=uuid4();db.execute("INSERT INTO corei_accounts(id,connection_id,name,external_reference,mode) "
                                       "VALUES(%s,(SELECT id FROM corei_connections LIMIT 1),'legacy','live-2','LIVE')",(legacy,))
                            db.execute('ALTER TABLE corei_accounts ENABLE TRIGGER corei_accounts_paper_only')
                        self.assertEqual(client.patch('/api/registry/accounts/'+str(legacy),json={'enabled':True}).status_code,422)
                        self.assertEqual(client.patch('/api/registry/accounts/'+str(legacy),json={'enabled':False}).status_code,200)
        finally:
            app.dependency_overrides.clear()
            with psycopg.connect(url,autocommit=True) as admin:
                admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
