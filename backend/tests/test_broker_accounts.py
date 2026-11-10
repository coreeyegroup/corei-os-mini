import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from fastapi.testclient import TestClient
from corei_os_mini.api import app
from corei_os_mini.operator_auth import require_operator
from corei_os_mini.broker_secrets import encrypt_password, decrypt_password
from corei_os_mini.broker_catalog import endpoint
from corei_os_mini.broker_accounts import NewAccount, validate


class BrokerAccountTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        key = Path(self.folder.name) / 'key'
        key.write_bytes(os.urandom(32)); key.chmod(0o600)
        self.env = patch.dict(os.environ, {'COREI_BROKER_KEY_FILE':str(key)})
        self.env.start()
        app.dependency_overrides[require_operator] = lambda: 'operator'
        self.client = TestClient(app)
        self.payload = dict(name='Account A',broker='Broker A',platform='MT5',mode='SANDBOX',
                            login='1234',password='do-not-return-this',server_name='Broker-Demo',access_point_id=str(uuid4()))

    def tearDown(self):
        self.client.close(); app.dependency_overrides.clear(); self.env.stop(); self.folder.cleanup()

    def test_secret_is_encrypted_randomised_and_identity_bound(self):
        key = uuid4()
        first = encrypt_password(key,'password')
        second = encrypt_password(key,'password')
        self.assertNotEqual(first,second)
        self.assertNotIn(b'password',first)
        self.assertEqual(decrypt_password(key,first),'password')
        with self.assertRaises(InvalidTag): decrypt_password(uuid4(),first)
        with self.assertRaises(InvalidTag): decrypt_password(key,first[:-1]+bytes([first[-1]^1]))

    def test_invalid_inputs_never_echo_passwords(self):
        for payload in ({**self.payload,'login':'not-a-number'}, {**self.payload,'extra':'private'},
                        {**self.payload,'login':str(2**64)}, {**self.payload,'password':'x'*257}):
            result=self.client.post('/api/registry/broker-accounts',json=payload)
            self.assertEqual(result.status_code,422)
            self.assertNotIn('do-not-return-this',result.text)
            self.assertNotIn('xxx',result.text)
        result=self.client.post('/api/registry/broker-accounts',content='{"password":"do-not-return-this",BROKEN',headers={'Content-Type':'application/json'})
        self.assertEqual(result.status_code,422)
        self.assertNotIn('do-not-return-this',result.text)

    def test_endpoint_supports_ipv4_ipv6_and_hostname(self):
        self.assertEqual(endpoint('[2001:db8::1]:443'),('2001:db8::1',443))
        self.assertEqual(endpoint('access.example.com:1950'),('access.example.com',1950))
        self.assertEqual(endpoint('192.0.2.1:443'),('192.0.2.1',443))
        for value in ('host:0','host:65536','http://host:443','host/path:443','[not-ip]:443'):
            with self.subTest(value=value),self.assertRaises(ValueError):endpoint(value)

    def test_wrong_server_cannot_be_saved(self):
        db=MagicMock(); db.execute.return_value.fetchone.return_value={'server_name':'Other-Live'}
        with patch('corei_os_mini.broker_accounts._connect') as connect:
            connect.return_value.__enter__.return_value=db
            result=self.client.post('/api/registry/broker-accounts',json=self.payload)
            self.assertEqual(result.status_code,422)
            self.assertFalse(any('INSERT' in str(c) for c in db.execute.call_args_list))

    def test_no_key_means_no_database_write(self):
        with patch.dict(os.environ,{'COREI_BROKER_KEY_FILE':'/does/not/exist'}),patch('corei_os_mini.broker_accounts._connect') as connect:
            self.assertEqual(self.client.post('/api/registry/broker-accounts',json=self.payload).status_code,503)
            connect.assert_not_called()

    def test_stale_edit_rejected(self):
        db=MagicMock();db.execute.return_value.fetchone.return_value={'version':2}
        with patch('corei_os_mini.broker_accounts._connect') as connect:
            connect.return_value.__enter__.return_value=db
            result=self.client.put('/api/registry/broker-accounts/'+str(uuid4()),json={
                'name':'new','access_point_id':str(uuid4()),'enabled':False,'version':1})
            self.assertEqual(result.status_code,409)
            self.assertEqual(db.execute.call_count,1)

    def test_upload_limits_and_authentication(self):
        self.assertEqual(self.client.post('/api/registry/broker-accounts/server-catalog',content=b'a'*(1048576+1)).status_code,413)
        app.dependency_overrides.clear()
        with patch('corei_os_mini.operator_auth.credentials',return_value=('operator','test')):
            for method,path in [('get',''),('post','/server-catalog'),('post','')]:
                result=getattr(self.client,method)('/api/registry/broker-accounts'+path)
                self.assertEqual(result.status_code,401)


if __name__ == '__main__':unittest.main()
