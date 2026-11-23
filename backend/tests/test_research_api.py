import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from corei_os_mini import research_api


class ResearchApiTest(unittest.TestCase):
    def test_token_scope_and_read_only_bars(self):
        account = uuid4()
        token = 'fixture-read-only-token-012345678901'
        app = FastAPI()
        app.include_router(research_api.router)
        client = TestClient(app)
        prefix = '/api/research/v1'
        with patch.dict(os.environ, {'COREI_RESEARCH_API_TOKEN': token, 'COREI_RESEARCH_API_ACCOUNTS': str(account)}):
            self.assertEqual(client.get(prefix + '/capabilities').status_code, 401)
            headers = {'Authorization': f'Bearer {token}'}
            capabilities = client.get(prefix + '/capabilities', headers=headers)
            self.assertEqual(capabilities.json()['account_ids'], [str(account)])
            self.assertEqual(capabilities.headers['cache-control'], 'no-store')
            with patch.object(research_api.store, 'bars', return_value=[{'close': '1.20'}]) as store:
                params = {'account_id': str(uuid4()), 'symbol': 'EURUSD'}
                self.assertEqual(client.get(prefix + '/bars', headers=headers, params=params).status_code, 404)
                store.assert_not_called()
                params['account_id'] = str(account)
                self.assertEqual(client.get(prefix + '/bars', headers=headers, params={**params,'limit':201}).status_code, 422)
                result = client.get(prefix + '/bars', headers=headers, params=params)
                self.assertEqual(result.json()['items'], [{'close': '1.20'}])
                store.assert_called_once_with(account, 'EURUSD', 'H1', 100)
            self.assertEqual(client.post(prefix + '/bars', headers=headers).status_code, 405)

    def test_connection_disabled_by_default(self):
        app = FastAPI()
        app.include_router(research_api.router)
        with patch.dict(os.environ, {'COREI_RESEARCH_API_TOKEN': '', 'COREI_RESEARCH_API_ACCOUNTS': ''}):
            self.assertEqual(TestClient(app).get('/api/research/v1/capabilities').status_code, 503)
