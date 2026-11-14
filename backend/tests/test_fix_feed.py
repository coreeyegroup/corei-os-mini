"""FIX 4.4 market data end to end: a fake FIX venue, the reader (logon, subscribe, snapshot and incremental
refresh, heartbeat, test request, logout), refusals, and the collector streaming a FIX data source into the
database. Also: the adapter catalogue and the rule that prices come from data sources, not execution connections."""
import importlib
import importlib.util
import json
import socket
import subprocess
import sys
import threading
import time
import unittest
from datetime import datetime, timezone
from uuid import UUID

from corei_os_mini.md.fix_reader import SOH, FrameReader, encode, split_fields

test_market_data = importlib.import_module('test_market_data') if importlib.util.find_spec('test_market_data') \
    else importlib.import_module('tests.test_market_data')


def stamp():
    now = datetime.now(timezone.utc)
    return now.strftime('%Y%m%d-%H:%M:%S.') + f'{now.microsecond // 1000:03d}'


class FakeVenue:
    """A FIX acceptor: checks the logon (CompIDs, username/password), answers V with W then X updates."""

    def __init__(self, password='pw', depth_symbols=('EUR/USD', 'XAU/USD')):
        self.password, self.symbols = password, set(depth_symbols)
        self.server = socket.socket()
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(('127.0.0.1', 0))
        self.server.listen(5)
        self.port = self.server.getsockname()[1]
        self.logons, self.requests = [], []
        self.stop = threading.Event()
        threading.Thread(target=self.serve, daemon=True).start()

    def close(self):
        self.stop.set()
        self.server.close()

    def serve(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self.session, args=(conn,), daemon=True).start()

    def session(self, conn):
        seq = [1]
        frames = FrameReader()

        def send(kind, fields):
            conn.sendall(encode('FIX.4.4', [(35, kind), (49, 'VENUE'), (56, 'COREI'), (34, str(seq[0])), (52, stamp())] + fields))
            seq[0] += 1
        price = {'EUR/USD': 1.1000, 'XAU/USD': 4000.00}
        subscribed = {}
        try:
            conn.settimeout(0.1)
            while not self.stop.is_set():
                try:
                    data = conn.recv(65536)
                except socket.timeout:
                    data = b''
                    for symbol, rid in list(subscribed.items()):
                        price[symbol] += 0.0001 if symbol == 'EUR/USD' else 0.10
                        send('X', [(262, rid), (268, '2'), (279, '1'), (269, '0'), (55, symbol), (270, f'{price[symbol]:.5f}'), (271, '1000000'),
                                   (279, '1'), (269, '1'), (55, symbol), (270, f'{price[symbol] + 0.0002:.5f}'), (271, '1000000')])
                    continue
                if not data:
                    return
                for raw in frames.feed(data):
                    d = dict(split_fields(raw))
                    kind = d.get(35)
                    if kind == 'A':
                        self.logons.append({k: d.get(k) for k in (49, 56, 553, 141, 108)})
                        if d.get(56) != 'VENUE' or d.get(554) != self.password:
                            send('5', [(58, 'Invalid username or password')])
                            return
                        send('A', [(98, '0'), (108, d.get(108, '30'))])
                    elif kind == 'V':
                        symbol, rid = d.get(55), d.get(262)
                        self.requests.append((d.get(263), symbol))
                        if d.get(263) == '2':
                            subscribed.pop(symbol, None)
                        elif symbol not in self.symbols:
                            send('Y', [(262, rid), (281, '0'), (58, f'Unknown symbol {symbol}')])
                        else:
                            subscribed[symbol] = rid
                            send('W', [(262, rid), (55, symbol), (268, '2'), (269, '0'), (270, f'{price[symbol]:.5f}'), (271, '1000000'),
                                       (269, '1'), (270, f'{price[symbol] + 0.0002:.5f}'), (271, '1000000')])
                    elif kind == 'x':  # SecurityListRequest: two fragments
                        rid = d.get(320)
                        send('y', [(320, rid), (322, 'L1'), (560, '0'), (146, '1'), (55, 'EUR/USD'), (107, 'Euro vs US Dollar'), (167, 'FOR'),
                                   (15, 'USD'), (969, '0.00001'), (893, 'N')])
                        send('y', [(320, rid), (322, 'L1'), (560, '0'), (146, '2'), (55, 'XAU/USD'), (107, 'Gold spot'), (167, 'FOR'), (15, 'USD'),
                                   (969, '0.01'), (55, 'UK100'), (107, 'FTSE 100'), (167, 'INDEX'), (15, 'GBP'), (969, '0.1'), (893, 'Y')])
                    elif kind == '1':
                        send('0', [(112, d.get(112, ''))])
                    elif kind == '5':
                        send('5', [])
                        return
        except OSError:
            return
        finally:
            conn.close()


def request(venue, **extra):
    base = {'host': '127.0.0.1', 'port': venue.port, 'tls': False, 'sender_comp_id': 'COREI', 'target_comp_id': 'VENUE',
            'username': 'corei', 'password': 'pw', 'heartbeat_seconds': 5, 'reset_seq_on_logon': True, 'timeout_seconds': 5}
    return {**base, **extra}


class FixProtocol(unittest.TestCase):
    def test_encode_and_frame(self):
        raw = encode('FIX.4.4', [(35, '0'), (49, 'A'), (56, 'B'), (34, '1'), (52, stamp())])
        body = raw.split(b'\x019=', 1)[1].split(b'\x01', 1)[1].rsplit(b'10=', 1)[0]
        self.assertEqual(int(raw.split(b'\x019=')[1].split(b'\x01')[0]), len(body))
        self.assertEqual(int(raw.rsplit(b'10=', 1)[1][:3]), sum(raw.rsplit(b'10=', 1)[0]) % 256)
        frames = FrameReader()
        self.assertEqual(frames.feed(raw[:10]), [])
        self.assertEqual(frames.feed(raw[10:] + raw), [raw, raw], 'partial and back-to-back messages')


class FixReader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.venue = FakeVenue()

    @classmethod
    def tearDownClass(cls):
        cls.venue.close()

    def run_reader(self, req, args=(), lines=8, commands=()):
        proc = subprocess.Popen([sys.executable, '-m', 'corei_os_mini.md.fix_reader', *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        proc.stdin.write(json.dumps(req) + '\n')
        for c in commands:
            proc.stdin.write(json.dumps(c) + '\n')
        proc.stdin.flush()
        out = []
        deadline = time.monotonic() + 10
        while len(out) < lines and time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            out.append(json.loads(line))
            if out[-1].get('t') == 'failed' or 'connected' in out[-1] and 't' not in out[-1]:
                break
        try:
            proc.stdin.write(json.dumps({'cmd': 'stop'}) + '\n')
            proc.stdin.close()
        except BrokenPipeError:
            pass
        proc.wait(timeout=10)
        return out

    def test_stream_snapshot_then_updates(self):
        out = self.run_reader(request(self.venue, symbols=['EUR/USD']), lines=6)
        self.assertEqual(out[0]['t'], 'connected')
        quotes = [m for m in out if m['t'] == 'q']
        self.assertGreaterEqual(len(quotes), 3)
        self.assertEqual(quotes[0]['s'], 'EUR/USD')
        self.assertAlmostEqual(quotes[0]['a'] - quotes[0]['b'], 0.0002, places=6)
        self.assertGreater(quotes[-1]['b'], quotes[0]['b'], 'incremental refresh moves the book')
        self.assertEqual([q['n'] for q in quotes], sorted(q['n'] for q in quotes))
        logon = self.venue.logons[-1]
        self.assertEqual((logon[49], logon[56], logon[553], logon[141]), ('COREI', 'VENUE', 'corei', 'Y'))
        self.assertNotIn('pw', json.dumps(out))

    def test_unknown_symbol_is_reported_and_others_continue(self):
        out = self.run_reader(request(self.venue, symbols=['BAD/SYM', 'XAU/USD']), lines=6)
        errors = [m for m in out if m['t'] == 'error']
        self.assertTrue(any('BAD/SYM' in e['msg'] and 'Unknown symbol' in e['msg'] for e in errors))
        self.assertTrue(any(m['t'] == 'q' and m['s'] == 'XAU/USD' for m in out))

    def test_wrong_password_is_a_refusal(self):
        out = self.run_reader(request(self.venue, password='nope', symbols=['EUR/USD']), lines=1)
        self.assertEqual(out[0]['t'], 'failed')
        self.assertTrue(out[0]['attempts'][0]['refused'])
        self.assertIn('Invalid username or password', out[0]['attempts'][0]['error'])

    def test_session_mode_logs_on_and_off(self):
        out = self.run_reader(request(self.venue), args=['--session'], lines=1)
        self.assertTrue(out[0]['connected'])
        self.assertIsNotNone(out[0]['server_time'])

    def test_no_route_is_not_a_refusal(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            free = s.getsockname()[1]
        out = self.run_reader({**request(self.venue), 'port': free}, lines=1)
        self.assertEqual(out[0]['t'], 'failed')
        self.assertFalse(out[0]['attempts'][0].get('refused'))


class FixThroughCollector(test_market_data.MarketData):
    """A FIX data source streams into the same tables as MT5; an execution-only connection cannot be collected."""
    test_plans_estimate_controls_and_jobs = test_collector_end_to_end = None

    def test_fix_data_source_and_roles(self):
        venue = FakeVenue()
        self.addCleanup(venue.close)
        catalogue = self.ok(self.alice.get('/api/config/adapters'))
        ids = {a['id']: a for a in catalogue['adapters']}
        self.assertEqual((ids['FIX44']['data'], ids['MT5_DIRECT']['data'], ids['CTRADER']['data']), ('BUILT', 'BUILT', 'PLANNED'))
        self.assertEqual(catalogue['roles']['MARKET_DATA'], 'Data source')

        provider = self.ok(self.alice.post('/api/config/providers', json={'code': 'FASTFEED', 'name': 'Fast feed', 'kind': 'DATA_VENDOR'}), 201)
        settings = {'host': '127.0.0.1', 'port': venue.port, 'tls': False, 'sender_comp_id': 'COREI', 'target_comp_id': 'VENUE', 'heartbeat_seconds': 5}
        connection = self.ok(self.alice.post('/api/config/connections', json={
            'provider_id': provider['id'], 'code': 'FAST_FIX', 'name': 'Fast feed FIX', 'platform': 'FIX', 'environment': 'LIVE',
            'purpose': 'MARKET_DATA', 'adapter': 'NATIVE', 'settings': settings}), 201)
        self.ok(self.alice.put(f"/api/config/connections/{connection['id']}/credential",
                               json={'credential': {'kind': 'FIX_LOGIN', 'username': 'corei', 'password': 'pw'}}))
        account = self.ok(self.alice.post('/api/config/accounts', json={'connection_id': connection['id'], 'code': 'FAST_FEED', 'name': 'Fast feed session',
                                                                       'external_account_id': 'COREI', 'base_currency': 'USD', 'trading_permission': 'READ_ONLY'}), 201)
        stored = self.ok(self.alice.get(f"/api/config/accounts/{account['id']}"))
        self.assertEqual(stored['data_blockers'], [], 'FIX session with its login: nothing blocks data')
        checked = self.ok(self.alice.post(f"/api/config/accounts/{account['id']}/read-from-broker", json={}))
        self.assertTrue(checked['connected'])
        # The venue's instrument list (SecurityListRequest) fills the source's catalogue.
        loaded = self.ok(self.alice.post(f"/api/md/catalog/{account['id']}/refresh"))
        self.assertEqual((loaded['source'], loaded['symbols']), ('FIX_SECURITY_LIST', 3))
        listed = {i['broker_symbol']: i for i in self.ok(self.alice.get(f"/api/md/catalog/{account['id']}"))['items']}
        self.assertEqual((listed['XAU/USD']['suggested_instrument'], listed['XAU/USD']['digits'], listed['UK100']['asset_class']), ('XAUUSD', 2, 'INDEX'))
        self.assertEqual(listed['EUR/USD']['suggested_instrument'], 'EURUSD')

        # An execution-only connection is not a price source.
        exec_conn = self.ok(self.alice.post('/api/config/connections', json={
            'provider_id': provider['id'], 'code': 'EXEC_FIX', 'name': 'Execution FIX', 'platform': 'FIX', 'environment': 'LIVE',
            'purpose': 'TRADING', 'adapter': 'NATIVE', 'settings': settings}), 201)
        exec_acct = self.ok(self.alice.post('/api/config/accounts', json={'connection_id': exec_conn['id'], 'code': 'EXEC_1', 'name': 'Execution',
                                                                         'external_account_id': 'ACC1', 'base_currency': 'USD'}), 201)
        refused = self.alice.post('/api/md/plans', json={'code': 'NO_PRICES', 'account_id': exec_acct['id'], 'broker_symbol': 'EUR/USD',
                                                         'purpose': 'RESEARCH', 'data_types': ['QUOTES']})
        self.assertEqual(refused.status_code, 422)
        self.assertIn('execution connection', refused.text)

        plan = self.ok(self.alice.post('/api/md/plans', json={'code': 'EURUSD_FAST', 'account_id': account['id'], 'broker_symbol': 'EUR/USD',
                                                              'purpose': 'RESEARCH', 'data_types': ['QUOTES', 'BARS'], 'bar_timeframes': ['S1', 'M1'],
                                                              'stale_seconds': 2}), 201)
        self.start(plan)
        from corei_os_mini.md.collector import Collector
        self.collector = Collector(self.root)
        self.run_collector(3.0)
        with self.connect() as db:
            n = db.execute('SELECT count(*) AS n FROM md_ticks WHERE account_id=%s', (UUID(account['id']),)).fetchone()['n']
            status = db.execute('SELECT state, host, port FROM md_collector_status WHERE account_id=%s', (UUID(account['id']),)).fetchone()
            use = db.execute("SELECT count(*) AS n FROM cfg_credential_use WHERE used_by='collector'").fetchone()['n']
        self.assertGreater(n, 5, 'FIX ticks reached the database')
        self.assertEqual((status['state'], status['port']), ('CONNECTED', venue.port))
        self.assertGreaterEqual(use, 1, 'the session login use is logged')
