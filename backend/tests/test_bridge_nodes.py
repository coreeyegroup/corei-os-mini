"""Bridge nodes (DEC-75): a machine running the COREI MT5 bridge is registered once, enrols itself with a one-time
code (fingerprint and token over HTTPS, never typed), is health-checked with the bridge's ping (no terminal started)
and is used by many accounts as a route. Codes are single-use; tokens are never returned; a node in use cannot be
deleted; a default node is attached to new MT5 accounts; tunnel-mode keys are written for sshd."""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

tmd = importlib.import_module('test_market_data') if importlib.util.find_spec('test_market_data') else importlib.import_module('tests.test_market_data')
ttb = importlib.import_module('test_terminal_bridge') if importlib.util.find_spec('test_terminal_bridge') else importlib.import_module('tests.test_terminal_bridge')
from corei_os_mini import account_routes, bridge_nodes  # noqa: E402
from corei_os_mini.api import app  # noqa: E402


class LiveBridge:
    """One real bridge (supervisor mode, fake MetaTrader5 package) for the whole module."""
    port = state = None

    @classmethod
    def start(cls):
        if cls.port:
            return
        ttb.TerminalBridge.setUpClass()
        ttb.MultiAccountBridge.setUpClass()
        cls.port, cls.state = ttb.MultiAccountBridge.port, ttb.MultiAccountBridge.state
        cls.fingerprint, cls.token, cls.tmp = ttb.MultiAccountBridge.fingerprint, ttb.MultiAccountBridge.token, ttb.MultiAccountBridge.tmp


class BridgeNodes(tmd.MarketData):
    test_plans_estimate_controls_and_jobs = None
    test_collector_end_to_end = None
    test_refused_login_reported_once_and_on_overview = None

    def setUp(self):
        super().setUp()
        LiveBridge.start()
        self.public = TestClient(app, base_url='https://testserver', headers=tmd.HEADERS)
        self.keys = Path(self.folder.name) / 'tunnel_keys'
        self.env = patch.dict(os.environ, {'COREI_EGRESS_IP': '203.0.113.7', 'COREI_TUNNEL_KEYS': str(self.keys)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.public.close()
        super().tearDown()

    def test_enrol_check_attach_and_remove(self):
        _, accounts, _ = self.setup_accounts()
        node = self.ok(self.alice.post('/api/config/bridge-nodes', json={'name': 'VPS-London', 'mode': 'DIRECT', 'max_terminals': 30}), 201)
        code = node['instructions']['code']
        self.assertEqual((node['name'], node['status'], node['token_saved']), ('vps-london', 'PENDING', False))
        self.assertIn(code, node['instructions']['steps'][2]['command'])
        self.assertNotIn(code, json.dumps(self.ok(self.alice.get('/api/config/bridge-nodes'))), 'the code is shown once')

        info = self.ok(self.public.get(f'/api/bridge-enroll/{code}'))
        self.assertEqual((info['name'], info['mode'], info['corei_ip'], info['max_terminals']), ('vps-london', 'DIRECT', '203.0.113.7', 30))
        self.ok(self.public.get('/api/bridge-enroll/AAAAA-BBBBB-CCCCC-DDDDD'), 404)
        self.assertIn('Install and register', node['instructions']['steps'][2]['title'])
        self.assertIn('install-corei-bridge.ps1', self.public.get('/api/bridge-enroll/files/install-corei-bridge.ps1').headers['content-disposition'])
        self.ok(self.public.get('/api/bridge-enroll/files/..%2F..%2Fsecret'), 404)

        # The machine enrols (as the installer does), from 127.0.0.1 where the test bridge listens.
        body = {'fingerprint': LiveBridge.fingerprint, 'token': LiveBridge.token, 'port': LiveBridge.port, 'bridge_version': '0.43.1',
                'machine': 'VPS1', 'host': '127.0.0.1'}
        done = self.ok(self.public.post(f'/api/bridge-enroll/{code}', json=body))
        self.assertTrue(done['check']['ok'], done)
        self.assertEqual(done['check']['version'], '0.43.1')
        self.assertEqual(done['check']['terminals_open'], 0, 'the ping starts no terminal')
        self.ok(self.public.post(f'/api/bridge-enroll/{code}', json=body), 404)  # single use
        listed = self.ok(self.alice.get('/api/config/bridge-nodes'))['items'][0]
        self.assertEqual((listed['status'], listed['token_saved'], listed['fingerprint'], listed['machine']), ('ACTIVE', True, LiveBridge.fingerprint, 'VPS1'))
        self.assertNotIn(LiveBridge.token, json.dumps(listed))
        again = self.ok(self.alice.post(f"/api/config/bridge-nodes/{node['id']}/check"))
        self.assertTrue(again['ok'], again)

        # Use it for every MT5 account: one route each, no host/fingerprint/token typed.
        used = self.ok(self.alice.post(f"/api/config/bridge-nodes/{node['id']}/attach", json={'account_ids': 'ALL_MT5', 'priority': 5}))
        self.assertEqual(sorted(used['added']), ['VTM_A', 'VTM_B', 'VTM_C'])
        self.assertEqual(self.ok(self.alice.post(f"/api/config/bridge-nodes/{node['id']}/attach", json={}))['already'], ['VTM_A', 'VTM_B', 'VTM_C'])
        routes = account_routes.enabled_routes(accounts[0]['id'])
        self.assertEqual((routes[0]['host'], routes[0]['port'], routes[0]['fingerprint'], routes[0]['node_name'], routes[0]['has_token']),
                         ('127.0.0.1', LiveBridge.port, LiveBridge.fingerprint, 'vps-london', True))
        self.assertEqual(account_routes.use_token(routes[0], 'test', 'test')['token'], LiveBridge.token)
        shown = self.ok(self.alice.get(f"/api/config/accounts/{accounts[0]['id']}/routes"))['items'][0]
        self.assertEqual((shown['node_name'], shown['needs_token'], shown['token_saved']), ('vps-london', False, True))
        self.ok(self.alice.put(f"/api/config/accounts/{accounts[0]['id']}/routes/{shown['id']}/token", json={'token': 'y' * 40}), 422)

        # Disabled node: its routes are skipped by the collector; deleting needs the routes gone first.
        n = self.ok(self.alice.get(f"/api/config/bridge-nodes/{node['id']}"))
        self.assertEqual(len(n['routes']), 3)
        off = self.ok(self.alice.put(f"/api/config/bridge-nodes/{node['id']}", json={'location': 'WINDOWS_VPS', 'host': '127.0.0.1', 'port': LiveBridge.port,
                                                                                    'max_terminals': 30, 'enabled': False, 'version': n['version']}))
        self.assertEqual(off['status'], 'DISABLED')
        self.assertEqual(account_routes.enabled_routes(accounts[0]['id']), [])
        self.ok(self.alice.delete(f"/api/config/bridge-nodes/{node['id']}"), 409)
        self.assertEqual(len(self.ok(self.alice.post(f"/api/config/bridge-nodes/{node['id']}/detach"))['removed']), 3)
        self.ok(self.alice.delete(f"/api/config/bridge-nodes/{node['id']}"), 204)
        with self.connect() as db:
            actions = {r['action'] for r in db.execute("SELECT action FROM cfg_audit_log WHERE entity='bridge_node'").fetchall()}
            uses = db.execute("SELECT count(*) AS n FROM cfg_credential_use WHERE owner='bridge_node'").fetchone()['n']
        self.assertEqual(actions, {'CREATE_BRIDGE_NODE', 'ENROL_BRIDGE_NODE', 'UPDATE_BRIDGE_NODE', 'DELETE_BRIDGE_NODE'})
        self.assertGreaterEqual(uses, 3, 'every use of the node token is logged')

    def test_default_node_for_new_accounts_and_tunnel_keys(self):
        connection, _, _ = self.setup_accounts()
        node = self.ok(self.alice.post('/api/config/bridge-nodes', json={'name': 'office-pc', 'mode': 'TUNNEL', 'location': 'WINDOWS_PC', 'default_for_new': True}), 201)
        self.assertEqual(node['tunnel_port'], 9450)
        second = self.ok(self.alice.post('/api/config/bridge-nodes', json={'name': 'home-pc', 'mode': 'TUNNEL'}), 201)
        self.assertEqual(second['tunnel_port'], 9451, 'each tunnel node gets its own port')
        code = node['instructions']['code']
        info = self.ok(self.public.get(f'/api/bridge-enroll/{code}'))
        self.assertEqual((info['ssh_target'].split('@')[0], info['tunnel_port']), ('corei-tunnel', 9450))
        body = {'fingerprint': LiveBridge.fingerprint, 'token': LiveBridge.token, 'port': 9443, 'bridge_version': '0.43.1', 'machine': 'PC'}
        self.ok(self.public.post(f'/api/bridge-enroll/{code}', json=body), 422)  # tunnel mode needs the key
        key = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB' + 'x' * 43 + ' corei-tunnel-office-pc'
        done = self.ok(self.public.post(f'/api/bridge-enroll/{code}', json={**body, 'ssh_public_key': key}))
        self.assertEqual((done['host'], done['port'], done['tunnel']['automatic']), ('127.0.0.1', 9450, True))
        line = self.keys.read_text().strip()
        self.assertTrue(line.startswith('restrict,port-forwarding,permitlisten="127.0.0.1:9450",command="/bin/false" ssh-ed25519 '), line)
        self.assertTrue(line.endswith('corei-node-office-pc'))
        # A new MT5 account gets a route through the default node by itself.
        made = self.ok(self.alice.post('/api/config/accounts', json={'connection_id': connection['id'], 'code': 'VTM_NEW', 'name': 'VTM_NEW',
                                                                     'external_account_id': '1099', 'base_currency': 'USD'}), 201)
        self.assertTrue(made.get('bridge_route_added'))
        routes = self.ok(self.alice.get(f"/api/config/accounts/{made['id']}/routes"))['items']
        self.assertEqual([r['node_name'] for r in routes], ['office-pc'])
        # A node without a token cannot be checked; a fresh code replaces the old one.
        self.assertFalse(self.ok(self.alice.post(f"/api/config/bridge-nodes/{second['id']}/check"))['ok'])
        fresh = self.ok(self.alice.post(f"/api/config/bridge-nodes/{second['id']}/code"))
        self.ok(self.public.get(f"/api/bridge-enroll/{second['instructions']['code']}"), 404)
        self.ok(self.public.get(f"/api/bridge-enroll/{fresh['instructions']['code']}"))


def test_latest_bridge_matches_the_shipped_bridge():
    src = (Path(__file__).resolve().parents[2] / 'connectors' / 'mt5-bridge' / 'corei_mt5_bridge.py').read_text()
    assert f"BRIDGE_VERSION = '{bridge_nodes.LATEST_BRIDGE}'" in src


def test_windows_scripts_are_ascii_with_crlf():
    folder = Path(__file__).resolve().parents[2] / 'connectors' / 'mt5-bridge' / 'windows'
    for name in ('install-corei-bridge.ps1', 'setup-corei-bridge.ps1', 'update-corei-bridge.ps1'):
        data = (folder / name).read_bytes()
        assert all(b < 128 for b in data), name
        assert b'\r\n' in data and data.count(b'\n') == data.count(b'\r\n'), name
