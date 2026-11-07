import hashlib
import os
import unittest
from unittest.mock import patch, MagicMock
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient
from corei_os_mini.api import app
from corei_os_mini import operator_auth as auth


class RegistryTests(unittest.TestCase):
    def setUp(self):
        salt='ab'*16
        digest=hashlib.pbkdf2_hmac('sha256',b'test-password-long',bytes.fromhex(salt),600000).hex()
        self.env=patch.dict(os.environ,{'COREI_OPERATOR_USER':'operator','COREI_OPERATOR_PASSWORD_HASH':f'pbkdf2_sha256$600000${salt}${digest}'})
        self.env.start()
        auth.attempts.clear()
        self.client=TestClient(app,base_url='https://testserver')
        self.headers={'X-Corei-Request':'workstation','Origin':'https://testserver'}

    def tearDown(self):
        self.client.close()
        self.env.stop()

    def login(self):
        return self.client.post('/api/operator/login',json={'username':'operator','password':'test-password-long'},headers=self.headers)

    def test_anonymous_registry_access_rejected(self):
        self.assertEqual(self.client.get('/api/registry/accounts').status_code,401)
        self.assertEqual(self.client.post('/api/registry/providers',json={}).status_code,401)

    def test_cookie_login_session_and_logout(self):
        r=self.login()
        self.assertEqual(r.status_code,200)
        self.assertIn('HttpOnly',r.headers['set-cookie'])
        self.assertIn('Secure',r.headers['set-cookie'])
        self.assertIn('SameSite=strict',r.headers['set-cookie'])
        self.assertEqual(self.client.get('/api/operator/session').json(),{'username':'operator'})
        self.client.post('/api/operator/logout',headers=self.headers)
        self.assertEqual(self.client.get('/api/operator/session').status_code,401)

    def test_mutation_header_and_origin_required(self):
        self.login()
        self.assertEqual(self.client.post('/api/registry/providers',json={}).status_code,403)
        self.assertEqual(self.client.post('/api/registry/providers',json={},headers={'X-Corei-Request':'workstation','Origin':'https://other.example'}).status_code,403)

    def test_password_and_tampered_cookie_rejected(self):
        self.assertEqual(self.client.post('/api/operator/login',json={'username':'operator','password':'wrong-password'},headers=self.headers).status_code,401)
        self.client.cookies.set(auth.COOKIE,'tampered.signature',path='/api')
        self.assertEqual(self.client.get('/api/operator/session').status_code,401)

    def test_input_validation(self):
        self.login()
        for resource,body in [('providers',{'name':'   ','kind':'BROKER'}),('providers',{'name':'P','kind':'BROKER','password':'secret'}),('accounts',{'name':'A','connection_id':'bad','mode':'LIVE','external_reference':'A'}),('connections',{'provider_id':str(uuid4()),'name':'X','purpose':'UNKNOWN'})]:
            self.assertEqual(self.client.post('/api/registry/'+resource,json=body,headers=self.headers).status_code,422)
        self.assertEqual(self.client.get('/api/registry/unknown').status_code,404)
        self.assertEqual(self.client.get('/api/registry/providers?limit=10000').status_code,422)

    def test_create_defaults_disabled_and_conflicts_are_explicit(self):
        self.login()
        db=MagicMock()
        db.execute.return_value.fetchone.return_value={'id':str(uuid4()),'name':'P','kind':'BROKER','enabled':False}
        with patch('corei_os_mini.registry._connect') as connect:
            connect.return_value.__enter__.return_value=db
            r=self.client.post('/api/registry/providers',json={'name':'P','kind':'BROKER'},headers=self.headers)
            self.assertEqual(r.status_code,201)
            self.assertFalse(r.json()['enabled'])
            self.assertIn(False,db.execute.call_args.args[1])
        for error,code in [(psycopg.errors.UniqueViolation(),409),(psycopg.errors.ForeignKeyViolation(),422),(psycopg.OperationalError('secret database URL'),503)]:
            with patch('corei_os_mini.registry._connect',side_effect=error):
                r=self.client.post('/api/registry/providers',json={'name':'P','kind':'BROKER'},headers=self.headers)
                self.assertEqual(r.status_code,code)
                self.assertNotIn('secret database',r.text)

    def test_expired_session_rejected(self):
        self.login()
        with patch('corei_os_mini.operator_auth.time.time',return_value=10**12):
            self.assertEqual(self.client.get('/api/operator/session').status_code,401)

    def test_six_character_password_accepted_five_rejected(self):
        salt='cd'*16
        digest=hashlib.pbkdf2_hmac('sha256',b'abc123',bytes.fromhex(salt),600000).hex()
        with patch.dict(os.environ,{'COREI_OPERATOR_PASSWORD_HASH':f'pbkdf2_sha256$600000${salt}${digest}'}):
            r=self.client.post('/api/operator/login',json={'username':'operator','password':'abc123'},headers=self.headers)
            self.assertEqual(r.status_code,200)
            r=self.client.post('/api/operator/login',json={'username':'operator','password':'abc12'},headers=self.headers)
            self.assertEqual(r.status_code,422)

    def test_login_rate_limit(self):
        for _ in range(10):auth.attempts.append(auth.time.monotonic())
        self.assertEqual(self.login().status_code,429)

if __name__=='__main__': unittest.main()
