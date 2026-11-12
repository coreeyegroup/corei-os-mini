"""Stored runs and assessments are not readable without a session (audit 12 Nov 2026)."""
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from corei_os_mini.api import app


class StoredResultsNeedSignIn(unittest.TestCase):
    def test_reads_and_demo_routes_require_a_session(self):
        client = TestClient(app, base_url='https://testserver')
        for path in (f'/api/paper-runs/{uuid4()}', f'/api/market-data/assessments/{uuid4()}'):
            self.assertEqual(client.get(path).status_code, 401, path)
        self.assertEqual(client.post('/api/demo/paper-runs', json={}, headers={'x-corei-request': 'workstation'}).status_code, 401)

    def test_workstation_reports_adapters_truthfully(self):
        client = TestClient(app, base_url='https://testserver')
        caps = {c['id']: c for c in client.get('/api/workstation').json()['capabilities']}
        self.assertIn('MT5', caps['providers']['reason'])
        self.assertFalse(caps['execution']['available'])
