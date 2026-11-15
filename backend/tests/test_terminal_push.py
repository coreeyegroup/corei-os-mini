"""MT5 terminal push: the COREI EA sends quotes, login facts and the symbol list over HTTPS with the server's push
token; the collector reads them like any other source and stores quotes, latest prices and bars; commands
(hello, subscribe, history) go back to the EA as text lines."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib
import importlib.util
import threading
import time

tmd = importlib.import_module('test_market_data') if importlib.util.find_spec('test_market_data') else importlib.import_module('tests.test_market_data')
from fastapi.testclient import TestClient  # noqa: E402
from corei_os_mini.api import app  # noqa: E402
from corei_os_mini.md.collector import Collector  # noqa: E402


def iso(t: datetime) -> str:
    return t.strftime('%Y-%m-%dT%H:%M:%S.') + f'{t.microsecond // 1000:03d}'


class TerminalPush(tmd.MarketData):
    test_plans_estimate_controls_and_jobs = None
    test_collector_end_to_end = None
    test_refused_login_reported_once_and_on_overview = None

    def test_push_end_to_end(self):
        conn, (a, b, _), instrument = self.setup_accounts()
        # The server's method: terminal push; a push token issued once.
        c = self.ok(self.alice.get(f"/api/config/connections/{conn['id']}"))
        body = {k: c[k] for k in ('provider_id', 'code', 'name', 'platform', 'environment', 'purpose', 'settings')}
        self.ok(self.alice.put(f"/api/config/connections/{conn['id']}", json={**body, 'adapter': 'TERMINAL_PUSH', 'version': c['version']}))
        token = self.ok(self.alice.post(f"/api/md/push-token/{conn['id']}"))['token']
        self.assertGreater(len(token), 30)
        ea = TestClient(app, base_url='https://testserver')
        url, hdr = '/api/md/push', {'Authorization': 'Bearer ' + token, 'X-COREI-Account': a['code']}
        self.assertEqual(ea.post(url, json={'lines': []}, headers={**hdr, 'Authorization': 'Bearer wrong'}).status_code, 401)
        r = ea.post(url, json={'lines': [{'t': 'connected', 'login': '9999'}]}, headers=hdr)
        self.assertEqual(r.status_code, 409, 'the terminal must be logged in to this account')
        self.assertIn('9999', r.text)
        # Symbol list from the EA lands in the catalogue.
        r = ea.post(url, json={'lines': [{'t': 'catalog', 'symbols': [
            {'Name': 'EURUSD', 'Path': 'Forex\\Majors\\EURUSD', 'Digits': 5, 'Description': 'Euro vs US Dollar', 'VolumeMin': 0.01, 'VolumeStep': 0.01, 'ContractSize': 100000},
            {'Name': 'XAUUSD', 'Path': 'Metals\\XAUUSD', 'Digits': 2, 'Description': 'Gold'}]}]}, headers=hdr)
        self.assertEqual(r.status_code, 200, r.text)
        names = {x['broker_symbol'] for x in self.ok(self.alice.get(f"/api/md/catalog/{a['id']}"))['items']}
        self.assertEqual(names, {'EURUSD', 'XAUUSD'})
        # A plan on the pushed account; the collector asks the EA to say hello and subscribe.
        self.start(self.plan(a, instrument, 'EU_PUSH'))
        self.collector = Collector(self.root)
        stop = threading.Event()

        def ea_loop():
            seq = 0
            lines = [{'t': 'connected', 'login': a['external_account_id'], 'host': 'VTMarkets-Live 3', 'port': 0, 'company': 'VT Markets',
                      'client_build': 5370, 'server_time': iso(datetime.now(timezone.utc) + timedelta(hours=2)), 'utc_now': iso(datetime.now(timezone.utc))}]
            commands: list[str] = []
            while not stop.is_set():
                now = datetime.now(timezone.utc)
                if 'subscribe EURUSD' in commands:
                    for _ in range(5):
                        seq += 1
                        lines.append({'t': 'q', 's': 'EURUSD', 'st': iso(now + timedelta(hours=2) - timedelta(milliseconds=40)), 'rx': iso(now),
                                      'b': 1.08000 + seq * 1e-5, 'a': 1.08010 + seq * 1e-5, 'l': 0, 'v': 1, 'n': seq})
                r = ea.post(url, json={'lines': lines}, headers=hdr)
                lines = []
                commands += [x for x in r.text.split('\n') if x]
                if 'hello' in commands and not any(x.startswith('subscribe') for x in commands):
                    lines.append({'t': 'connected', 'login': a['external_account_id'], 'host': 'VTMarkets-Live 3', 'port': 0, 'client_build': 5370,
                                  'server_time': iso(datetime.now(timezone.utc) + timedelta(hours=2)), 'utc_now': iso(datetime.now(timezone.utc))})
                time.sleep(0.1)
            self.commands = commands

        t = threading.Thread(target=ea_loop, daemon=True)
        t.start()
        try:
            self.run_collector(4.0)
        finally:
            stop.set()
            t.join(5)
        self.assertIn('hello', self.commands)
        self.assertIn('subscribe EURUSD', self.commands)
        with self.connect() as db:
            n = db.execute('SELECT count(*) AS n FROM md_ticks WHERE account_id=%s', (a['id'],)).fetchone()['n']
            latest = db.execute('SELECT * FROM md_latest WHERE account_id=%s', (a['id'],)).fetchone()
            status = db.execute('SELECT state FROM md_collector_status WHERE account_id=%s', (a['id'],)).fetchone()
            seen = db.execute('SELECT * FROM md_push_terminals WHERE account_id=%s', (a['id'],)).fetchone()
            tick = db.execute('SELECT * FROM md_ticks WHERE account_id=%s ORDER BY received_at LIMIT 1', (a['id'],)).fetchone()
        self.assertGreater(n, 10, 'pushed quotes stored like any other source')
        self.assertIsNotNone(latest)
        self.assertEqual(status['state'], 'CONNECTED')
        self.assertEqual((seen['build'], seen['company']), (5370, None) if seen['company'] is None else (5370, 'VT Markets'))
        # Broker time (UTC+2) stored as UTC, about 40 ms before receipt.
        self.assertLess(abs((tick['received_at'] - tick['source_time']).total_seconds() - 0.04), 0.05)
        # Log in and read now reports what the EA sent; readiness needs no MT5 password for a pushed account.
        read = self.ok(self.alice.post(f"/api/config/accounts/{a['id']}/read-from-broker", json={}))
        self.assertEqual(read['via'], 'COREI EA (terminal push)')
        acc = {x['code']: x for x in self.ok(self.alice.get('/api/config/accounts'))['items']}
        self.assertNotIn('ACCOUNT_CREDENTIAL_MISSING', acc[a['code']]['data_blockers'])
        self.assertNotIn('PUSH_TOKEN_MISSING', acc[a['code']]['data_blockers'])
        self.assertIn('COREI_Push', self.alice.get('/api/md/push/ea').text)
