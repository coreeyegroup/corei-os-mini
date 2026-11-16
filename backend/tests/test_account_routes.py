"""Routes (DEC-69): an MT5 account's ordered ways in (Windows VPS bridge, Windows PC bridge, Docker, EA push, direct
SDK). Tokens are write-only; a failing route hands over to the next one with a FEED_SWITCH event; a test read can
go through one chosen route."""
from __future__ import annotations

import importlib
import importlib.util
import json

tmd = importlib.import_module('test_market_data') if importlib.util.find_spec('test_market_data') else importlib.import_module('tests.test_market_data')
from corei_os_mini import account_routes  # noqa: E402
from corei_os_mini.md.collector import Collector  # noqa: E402

FP = ':'.join(['AB'] * 32)


class AccountRoutes(tmd.MarketData):
    test_plans_estimate_controls_and_jobs = None
    test_collector_end_to_end = None
    test_refused_login_reported_once_and_on_overview = None

    def rurl(self, a, rest=''):
        return f"/api/config/accounts/{a['id']}/routes{rest}"

    def test_routes_crud_and_write_only_token(self):
        _, (a, _b, _c), _ = self.setup_accounts()
        bad = self.ok(self.alice.post(self.rurl(a), json={'label': 'VPS', 'method': 'TERMINAL_BRIDGE', 'host': 'vps.example.com', 'port': 9443}), 422)
        self.assertIn('fingerprint', json.dumps(bad))
        vps = self.ok(self.alice.post(self.rurl(a), json={'label': 'Windows VPS London', 'method': 'TERMINAL_BRIDGE', 'location': 'WINDOWS_VPS',
                                                        'host': 'vps.example.com', 'port': 9443, 'fingerprint': FP.replace(':', '').lower(), 'priority': 1}), 201)
        self.assertEqual(vps['fingerprint'], FP)
        self.assertFalse(vps['token_saved'])
        self.ok(self.alice.post(self.rurl(a), json={'label': 'Windows VPS London', 'method': 'DIRECT'}), 409)
        pc = self.ok(self.alice.post(self.rurl(a), json={'label': 'Office PC (tunnel)', 'method': 'TERMINAL_BRIDGE', 'location': 'WINDOWS_PC',
                                                       'host': '127.0.0.1', 'port': 9450, 'fingerprint': FP, 'priority': 2}), 201)
        ea = self.ok(self.alice.post(self.rurl(a), json={'label': 'EA push', 'method': 'TERMINAL_PUSH', 'priority': 3, 'host': 'ignored'}), 201)
        self.assertIsNone(ea['host'])
        self.ok(self.alice.put(self.rurl(a, f"/{vps['id']}/token"), json={'token': 'short'}), 422)
        self.ok(self.alice.put(self.rurl(a, f"/{vps['id']}/token"), json={'token': 'x' * 43}), 204)
        issued = self.ok(self.alice.post(self.rurl(a, f"/{ea['id']}/new-token")))
        listed = self.ok(self.alice.get(self.rurl(a)))
        self.assertEqual([r['label'] for r in listed['items']], ['Windows VPS London', 'Office PC (tunnel)', 'EA push'])
        self.assertEqual([r['token_saved'] for r in listed['items']], [True, False, True])
        self.assertNotIn('x' * 43, json.dumps(listed))
        self.assertNotIn(issued['token'], json.dumps(listed))
        upd = self.ok(self.alice.put(self.rurl(a, f"/{pc['id']}"), json={**{k: pc[k] for k in ('label', 'method', 'location', 'host', 'port', 'fingerprint')},
                                                                        'priority': 0, 'enabled': False, 'version': pc['version']}))
        self.assertEqual((upd['priority'], upd['enabled'], upd['version']), (0, False, 2))
        self.ok(self.alice.put(self.rurl(a, f"/{pc['id']}"), json={'label': 'x', 'method': 'DIRECT', 'version': 1}), 409)
        self.ok(self.alice.delete(self.rurl(a, f"/{pc['id']}")), 204)
        self.assertEqual(len(self.ok(self.alice.get(self.rurl(a)))['items']), 2)
        with self.connect() as db:
            actions = [r['action'] for r in db.execute("SELECT action FROM cfg_audit_log WHERE entity_id=%s ORDER BY id", (a['id'],)).fetchall()]
        for action in ('CREATE_ROUTE', 'UPDATE_ROUTE', 'DELETE_ROUTE', 'SET_CREDENTIAL'):
            self.assertIn(action, actions)
        # The Windows set-up files are served to operators.
        ps1 = self.alice.get('/api/md/bridge/files/setup-corei-bridge.ps1')
        self.assertEqual(ps1.status_code, 200)
        self.assertIn('-Mode Tunnel', ps1.text)
        self.assertEqual(self.alice.get('/api/md/bridge/files/..%2Fsecret').status_code, 404)
        # The EA push route's token authenticates the push for this account (no server-level push method needed).
        r = self.alice.post('/api/md/push', json={'lines': []}, headers={'Authorization': 'Bearer ' + issued['token'], 'X-COREI-Account': a['code']})
        self.assertEqual(r.status_code, 200, r.text)

    def test_failover_to_the_next_route_and_event(self):
        _, (a, _b, _c), instrument = self.setup_accounts()
        # Route 1: a bridge nobody answers on; route 2: the direct SDK (the test's fake reader).
        dead = self.ok(self.alice.post(self.rurl(a), json={'label': 'VPS (down)', 'method': 'TERMINAL_BRIDGE', 'host': '127.0.0.1', 'port': 1,
                                                         'fingerprint': FP, 'priority': 1}), 201)
        self.ok(self.alice.put(self.rurl(a, f"/{dead['id']}/token"), json={'token': 't' * 40}), 204)
        direct = self.ok(self.alice.post(self.rurl(a), json={'label': 'Direct SDK', 'method': 'DIRECT', 'priority': 2}), 201)
        self.start(self.plan(a, instrument, 'EU_RT'))
        self.collector = Collector(self.root)
        self.run_collector(8.0)
        with self.connect() as db:
            ticks = db.execute('SELECT count(*) AS n FROM md_ticks WHERE account_id=%s', (a['id'],)).fetchone()['n']
            events = db.execute("SELECT detail FROM md_events WHERE kind='FEED_SWITCH' AND account_id=%s", (a['id'],)).fetchall()
            routes = {r['label']: r for r in db.execute('SELECT * FROM cfg_account_routes WHERE account_id=%s', (a['id'],)).fetchall()}
            status = db.execute('SELECT route FROM md_collector_status WHERE account_id=%s', (a['id'],)).fetchone()
        self.assertGreater(ticks, 0, 'prices arrive through the second route')
        self.assertTrue(events and events[0]['detail']['to_route'] == 'Direct SDK', events)
        self.assertIsNotNone(routes['VPS (down)']['last_error'])
        self.assertIsNotNone(routes['Direct SDK']['last_ok_at'])
        self.assertIsNotNone(routes['Direct SDK']['active_since'])
        self.assertEqual(status['route'], 'Direct SDK')
        self.assertTrue(direct['id'])

    def test_apply_maps_route_onto_the_account(self):
        acc = {'adapter': 'MTAPI_DIRECT', 'connection_settings': {'server_name': 'S', 'bridge': None}}
        out = account_routes.apply(acc, {'id': 'r', 'label': 'VPS', 'method': 'TERMINAL_BRIDGE', 'host': 'h', 'port': 1, 'fingerprint': FP})
        self.assertEqual((out['adapter'], out['connection_settings']['bridge']['host'], out['connection_settings']['server_name']), ('TERMINAL_BRIDGE', 'h', 'S'))
        out = account_routes.apply(acc, {'id': 'r', 'label': 'D', 'method': 'DIRECT', 'host': None, 'port': None, 'fingerprint': None})
        self.assertEqual((out['adapter'], out['connection_settings']['bridge']), ('MTAPI_DIRECT', None))
