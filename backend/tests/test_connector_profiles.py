import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4
from fastapi.testclient import TestClient
from pydantic import ValidationError
from corei_os_mini.api import app
from corei_os_mini.operator_auth import require_operator
from corei_os_mini.connector_profiles import ConnectorProfile


class ConnectorProfileTests(unittest.TestCase):
    def setUp(self):
        self.payload = dict(connection_id=str(uuid4()), name='MT5 demo', connector_type='MT5_EA',
                            adapter_key='mt5_ea', environment='SANDBOX', runtime='WINDOWS_TERMINAL',
                            server_name='Example-Demo')
        app.dependency_overrides[require_operator] = lambda: 'operator'
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.client.close()

    def test_family_runtime_validation_and_secret_fields(self):
        for change in ({'runtime':'SERVER_API'}, {'server_name':' '}, {'password':'secret'},
                       {'adapter_status':'CONNECTED'}, {'adapter_key':'../plugin'}, {'environment':'DEMO'}):
            with self.subTest(change=change):
                self.assertEqual(self.client.post('/api/registry/connector-profiles',
                                 json={**self.payload, **change}).status_code, 422)
        for family in ('MT4_EA', 'MT5_EA'):
            for runtime in ('WINDOWS_TERMINAL', 'UBUNTU_WINE_TERMINAL'):
                self.assertFalse(ConnectorProfile(**{**self.payload, 'connector_type':family, 'runtime':runtime}).enabled)
        for family in ('CTRADER_OPEN_API','BROKER_API','EXCHANGE_API','FIX','DATA_FEED','CUSTOM'):
            with self.assertRaises(ValidationError):
                ConnectorProfile(**{**self.payload, 'connector_type':family})
            ConnectorProfile(**{**self.payload, 'connector_type':family, 'runtime':'SERVER_API', 'server_name':''})

    def test_terminal_inventory_labels_are_optional_and_bounded(self):
        profile = ConnectorProfile(**self.payload)
        self.assertEqual(profile.terminal_host, '')
        profile = ConnectorProfile(**{**self.payload, 'terminal_host':'remote-windows-01', 'terminal_instance':'mt5-demo-01'})
        self.assertEqual(profile.terminal_instance, 'mt5-demo-01')
        with self.assertRaises(ValidationError):
            ConnectorProfile(**{**self.payload, 'terminal_host':'x'*121})

    def test_create_and_edit_do_not_claim_connectivity(self):
        row = {'id':str(uuid4()), **self.payload, 'enabled':True}
        db = MagicMock()
        db.execute.return_value.fetchone.return_value = row
        with patch('corei_os_mini.registry._connect') as connect:
            connect.return_value.__enter__.return_value = db
            for method, path in (('post','/api/registry/connector-profiles'),
                                 ('put','/api/registry/connector-profiles/'+row['id'])):
                result = getattr(self.client, method)(path, json={**self.payload,'enabled':True})
                self.assertIn(result.status_code, (200,201))
                self.assertEqual(result.json()['session_status'], 'NOT_CONNECTED')
                self.assertEqual(result.json()['adapter_status'], 'NOT_IMPLEMENTED')
                self.assertFalse(result.json()['account_verified'])
                self.assertFalse(result.json()['execution_available'])
            db.execute.return_value.fetchone.return_value = None
            self.assertEqual(self.client.put('/api/registry/connector-profiles/'+row['id'],
                                             json=self.payload).status_code,404)

    def test_profiles_require_authentication(self):
        app.dependency_overrides.clear()
        with patch('corei_os_mini.operator_auth.credentials',return_value=('operator','test-hash')):
            self.assertEqual(self.client.get('/api/registry/connector-profiles').status_code,401)
            self.assertEqual(self.client.put('/api/registry/connector-profiles/'+str(uuid4()),json=self.payload).status_code,401)

    def test_missing_schema_does_not_claim_configuration_readiness(self):
        with patch('corei_os_mini.api.check_registry_schema',return_value=True), \
             patch('corei_os_mini.api.check_connector_schema',return_value=False):
            result=self.client.get('/api/workstation').json()
            self.assertFalse(result['connector_schema_ready'])
            self.assertFalse(next(x for x in result['capabilities'] if x['id']=='connector_profiles')['available'])
            self.assertFalse(next(x for x in result['capabilities'] if x['id']=='providers')['available'])

if __name__=='__main__':
    unittest.main()
