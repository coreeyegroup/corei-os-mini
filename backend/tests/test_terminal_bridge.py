"""The MT5 terminal bridge end to end: a fake MetaTrader5 package, the real bridge server over TLS with a
pinned certificate, COREI's transport, session and stream replies, a refused token, and the password
never leaving the first line."""
import importlib.util
import io
import json
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from collections import namedtuple
from contextlib import redirect_stdout
from pathlib import Path

from corei_os_mini.md.bridge_client import BridgeError, BridgeTransport

BRIDGE = Path(__file__).resolve().parents[2] / 'connectors' / 'mt5-bridge' / 'corei_mt5_bridge.py'
Acc = namedtuple('Acc', 'login balance equity currency leverage margin_mode margin_so_so limit_orders company server trade_allowed')
Sym = namedtuple('Sym', 'name digits volume_min volume_max volume_step filling_mode trade_mode trade_exemode trade_stops_level '
                        'trade_freeze_level trade_contract_size currency_margin description path spread')
Tick = namedtuple('Tick', 'time time_msc bid ask last volume')
Term = namedtuple('Term', 'build connected')


def fake_mt5():
    m = types.ModuleType('MetaTrader5')
    state = {'logins': [], 'msc': int(time.time() * 1000) + 7_200_000}  # the broker clock runs UTC+2
    m.state = state
    m.__version__ = '5.0.5120'
    m.COPY_TICKS_ALL = 0
    for k in ('TIMEFRAME_M1', 'TIMEFRAME_M5', 'TIMEFRAME_M15', 'TIMEFRAME_M30', 'TIMEFRAME_H1', 'TIMEFRAME_H4', 'TIMEFRAME_D1'):
        setattr(m, k, k)
    m.initialize = lambda **kw: True
    m.last_error = lambda: (-6, 'Terminal: Authorization failed')

    def login(login, password='', server=''):
        state['logins'].append((login, server))
        return password == 'secret'
    m.login = login
    m.account_info = lambda: Acc(35822869, 1000.5, 1000.5, 'USD', 500, 2, 50, 200, 'VT Markets', 'VTMarkets-Live 3', True)
    m.terminal_info = lambda: Term(6241, True)
    syms = [Sym('XAUUSD', 2, 0.01, 50.0, 0.01, 1, 4, 2, 0, 0, 100.0, 'USD', 'Gold vs USD', 'Metals\\XAUUSD', 15),
            Sym('EURUSD', 5, 0.01, 100.0, 0.01, 1, 4, 2, 0, 0, 100000.0, 'USD', 'Euro vs USD', 'Forex\\EURUSD', 2)]
    m.symbols_get = lambda: syms
    m.symbol_info = lambda name: next((s for s in syms if s.name == name), None)
    m.symbol_select = lambda name, on: True

    def tick(name):
        state['msc'] += 250
        return Tick(state['msc'] // 1000, state['msc'], 4000.0 if name == 'XAUUSD' else 1.1, 4000.3 if name == 'XAUUSD' else 1.10002, 0.0, 0)
    m.symbol_info_tick = tick
    m.copy_ticks_from = lambda name, since, count, flags: [tick(name)]
    m.copy_rates_range = lambda name, frame, start, end: [{'time': 1_762_000_000, 'open': 1.0, 'high': 2.0, 'low': 0.5, 'close': 1.5, 'tick_volume': 10}]
    return m


class TerminalBridge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.modules['MetaTrader5'] = fake_mt5()
        spec = importlib.util.spec_from_file_location('corei_mt5_bridge', BRIDGE)
        cls.bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.bridge)
        cls.state = tempfile.mkdtemp()
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            cls.port = s.getsockname()[1]
        out = io.StringIO()
        threading.Thread(target=lambda: cls.bridge.main(['--bind', '127.0.0.1', '--port', str(cls.port), '--state', cls.state]), daemon=True).start()
        for _ in range(100):
            time.sleep(0.05)
            try:
                socket.create_connection(('127.0.0.1', cls.port), timeout=0.2).close()
                break
            except OSError:
                continue
        cls.fingerprint = cls.bridge.ensure_cert(Path(cls.state) / 'corei-bridge.crt', Path(cls.state) / 'corei-bridge.key')
        cls.token = (Path(cls.state) / 'corei-bridge.token').read_text().strip()

    def transport(self):
        return BridgeTransport('127.0.0.1', self.port, self.fingerprint)

    def test_pinned_certificate_is_checked(self):
        with self.assertRaises(BridgeError) as ctx:
            BridgeTransport('127.0.0.1', self.port, 'AA:' * 31 + 'AA')
        self.assertIn('FINGERPRINT_MISMATCH', str(ctx.exception))

    def test_wrong_token_is_refused_before_any_login(self):
        before = len(sys.modules['MetaTrader5'].state['logins'])
        t = self.transport()
        t.send({'token': 'nope', 'mode': 'session', 'login': 1, 'password': 'secret'})
        reply = json.loads(next(t.lines()))
        t.close()
        self.assertEqual(reply['t'], 'failed')
        self.assertIn('BRIDGE_TOKEN_REJECTED', reply['attempts'][0]['error'])
        self.assertEqual(len(sys.modules['MetaTrader5'].state['logins']), before)

    def test_session_reply_has_the_readers_shape(self):
        t = self.transport()
        t.send({'token': self.token, 'mode': 'session', 'login': 35822869, 'password': 'secret', 'server': 'VTMarkets-Live 3', 'symbols': ['XAUUSD']})
        reply = json.loads(next(t.lines()))
        t.close()
        self.assertTrue(reply['connected'])
        self.assertEqual(reply['account']['AccountMethod'], 'Hedging')
        self.assertEqual(reply['account']['AccountCurrency'], 'USD')
        self.assertEqual(reply['symbols']['XAUUSD']['VolumeStep'], 0.01)
        self.assertEqual(reply['symbols']['XAUUSD']['TradeMode'], 'Full')
        self.assertEqual(reply['client_build'], 6241)
        self.assertEqual(reply['symbols_available'], 2)
        self.assertNotIn('secret', json.dumps(reply))

    def test_wrong_password_is_a_broker_refusal(self):
        t = self.transport()
        t.send({'token': self.token, 'mode': 'session', 'login': 1, 'password': 'bad', 'server': 'X'})
        reply = json.loads(next(t.lines()))
        t.close()
        self.assertFalse(reply['connected'])
        self.assertTrue(reply['attempts'][0]['refused'])
        self.assertIn('Authorization failed', reply['attempts'][0]['error'])

    def test_stream_quotes_history_and_stop(self):
        t = self.transport()
        t.send({'token': self.token, 'mode': 'stream', 'login': 35822869, 'password': 'secret', 'server': 'VTMarkets-Live 3', 'symbols': ['XAUUSD'], 'heartbeat_seconds': 1})
        lines = t.lines()
        first = json.loads(next(lines))
        self.assertEqual(first['t'], 'connected')
        self.assertEqual(first['client_build'], 6241)
        t.send({'cmd': 'history', 'id': 'j1', 'symbol': 'XAUUSD', 'timeframe': 'M1', 'from': '2026-11-01T00:00:00', 'to': '2026-11-02T00:00:00'})
        seen = {'q': 0, 'bars': None, 'subscribed': 0}
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (seen['q'] < 3 or seen['bars'] is None):
            m = json.loads(next(lines))
            if m['t'] == 'q':
                seen['q'] += 1
                self.assertEqual(m['s'], 'XAUUSD')
                self.assertLess(m['b'], m['a'])
                self.assertTrue(m['st'] and m['rx'] and m['n'])
            elif m['t'] == 'bars':
                seen['bars'] = m['bars']
            elif m['t'] == 'subscribed':
                seen['subscribed'] += 1
        self.assertGreaterEqual(seen['q'], 3, 'ticks keep coming')
        self.assertEqual(seen['bars'][0]['c'], 1.5)
        t.send({'cmd': 'stop'})
        rest = [json.loads(line) for line in lines]
        self.assertEqual(rest[-1]['t'], 'stopped')
        t.close()


import importlib  # noqa: E402
# The fixture (database, key, signed-in users) lives in test_market_data; pytest imports it by file, not package.
test_market_data = importlib.import_module('test_market_data') if 'test_market_data' in sys.modules or importlib.util.find_spec('test_market_data') \
    else importlib.import_module('tests.test_market_data')
from corei_os_mini.md.collector import Collector  # noqa: E402
from uuid import UUID  # noqa: E402


class CollectorThroughBridge(test_market_data.MarketData):
    """A TERMINAL_BRIDGE connection streams into the same journal and tables as the direct reader."""
    # The fixture's own tests run in their module, not again here.
    test_plans_estimate_controls_and_jobs = test_collector_end_to_end = None

    def test_bridge_connection_streams_ticks(self):
        TerminalBridge.setUpClass()
        provider = self.ok(self.alice.post('/api/config/providers', json={'code': 'VTM', 'name': 'VT Markets', 'kind': 'BROKER'}), 201)
        connection = self.ok(self.alice.post('/api/config/connections', json={
            'provider_id': provider['id'], 'code': 'VTM_BRIDGE', 'name': 'Live 3 via terminal', 'platform': 'MT5', 'environment': 'DEMO',
            'purpose': 'BOTH', 'adapter': 'TERMINAL_BRIDGE',
            'settings': {'server_name': 'VTMarkets-Live 3', 'bridge': {'host': '127.0.0.1', 'port': TerminalBridge.port, 'fingerprint': TerminalBridge.fingerprint},
                         'access_points': [{'name': 'Live 3', 'host': '198.51.100.10', 'port': 443}]}}), 201)
        self.ok(self.alice.put(f"/api/config/connections/{connection['id']}/credential", json={'credential': {'kind': 'BRIDGE_TOKEN', 'token': TerminalBridge.token}}))
        account = self.ok(self.alice.post('/api/config/accounts', json={'connection_id': connection['id'], 'code': 'VTM_T', 'name': 'via terminal',
                                                                       'external_account_id': '35822869', 'base_currency': 'USD', 'trading_permission': 'READ_ONLY'}), 201)
        self.ok(self.alice.put(f"/api/config/accounts/{account['id']}/credential",
                               json={'credential': {'kind': 'MT5_LOGIN', 'password': 'secret', 'access_level': 'FULL'}}))
        ready = self.ok(self.alice.get(f"/api/config/accounts/{account['id']}"))
        self.assertEqual(ready['data_blockers'], [], 'bridge host, fingerprint and token present: nothing blocks data')
        # Log in and read now goes through the bridge too.
        result = self.ok(self.alice.post(f"/api/config/accounts/{account['id']}/read-from-broker", json={'symbols': ['XAUUSD']}))
        self.assertEqual((result['connected'], result['host'], result['port']), (True, '127.0.0.1', TerminalBridge.port))
        stored = self.ok(self.alice.get(f"/api/config/accounts/{account['id']}"))
        self.assertEqual((stored['margin_mode'], stored['margin_mode_source']), ('HEDGING', 'BROKER'))
        self.assertNotIn('secret', json.dumps(result))
        instrument = self.ok(self.alice.post('/api/config/instruments', json={
            'symbol': 'XAUUSD', 'name': 'Gold', 'asset_class': 'COMMODITY', 'base_currency': 'XAU', 'quote_currency': 'USD',
            'price_precision': 2, 'quantity_precision': 2, 'tick_size': '0.01', 'min_quantity': '0.01', 'quantity_step': '0.01',
            'contract_size': '100', 'contract_unit': 'oz'}), 201)
        plan = self.ok(self.alice.post('/api/md/plans', json={'code': 'GOLD_T', 'account_id': account['id'], 'broker_symbol': 'XAUUSD', 'instrument_id': instrument['id'],
                                                              'purpose': 'TRADING_FEED', 'feed_role': 'PRIMARY', 'data_types': ['QUOTES', 'BARS'], 'bar_timeframes': ['S1', 'M1'], 'stale_seconds': 2}), 201)
        self.start(plan)
        self.collector = Collector(self.root)
        self.run_collector(3.0)
        with self.connect() as db:
            n = db.execute('SELECT count(*) AS n FROM md_ticks WHERE account_id=%s', (UUID(account['id']),)).fetchone()['n']
            status = db.execute('SELECT state, host, port FROM md_collector_status').fetchone()
            event = db.execute("SELECT detail FROM md_events WHERE kind='CONNECTED' ORDER BY id LIMIT 1").fetchone()['detail']
        self.assertGreater(n, 5, 'ticks from the terminal reached the database')
        with self.connect() as db:
            measured = db.execute("SELECT detail FROM md_events WHERE kind='CLOCK_MEASURED'").fetchone()
            status_offset = db.execute('SELECT server_time_offset_ms FROM cfg_connection_status').fetchone()['server_time_offset_ms']
            tick = db.execute('SELECT source_time, received_at FROM md_ticks ORDER BY received_at LIMIT 1').fetchone()
        self.assertEqual(measured['detail']['server_offset_minutes'], 120, 'the broker clock (UTC+2) was measured from the first tick')
        self.assertEqual(status_offset, 7_200_000)
        self.assertLess(abs((tick['received_at'] - tick['source_time']).total_seconds()), 5, 'broker time stored as UTC')
        self.assertEqual((status['state'], status['host'], status['port']), ('CONNECTED', '127.0.0.1', TerminalBridge.port))
        self.assertEqual(event['client_build'], 6241)
