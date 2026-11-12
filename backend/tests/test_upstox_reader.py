"""The Upstox reader speaks the collector's line protocol (quotes, bars, refusals) — against a fake Upstox."""
import json
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from corei_os_mini.md.upstox_reader import parse_quote

KEY = 'NSE_EQ|INE002A01018'
STATE = {'price': 1300.0, 'auth': 'Bearer good'}


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.headers.get('Authorization') != STATE['auth']:
            return self._send(401, {'status': 'error', 'errors': [{'errorCode': 'UDAPI100050', 'message': 'Invalid token used to access API'}]})
        if self.path.startswith('/v2/market-quote/quotes'):
            assert 'instrument_key=NSE_EQ|INE002A01018' in self.path, self.path
            STATE['price'] += 0.5
            p = STATE['price']
            return self._send(200, {'status': 'success', 'data': {'NSE_EQ:RELIANCE': {
                'instrument_token': KEY, 'last_price': p, 'volume': 1000, 'last_trade_time': '1762929000000',
                'depth': {'buy': [{'price': p - 0.05, 'quantity': 10}], 'sell': [{'price': p + 0.05, 'quantity': 12}]}}}})
        if self.path.startswith('/v3/historical-candle/intraday/'):
            return self._send(200, {'status': 'success', 'data': {'candles': []}})
        if self.path.startswith('/v3/historical-candle/'):
            return self._send(200, {'status': 'success', 'data': {'candles': [
                ['2026-11-10T09:16:00+05:30', 1, 2, 0.5, 1.5, 100, 0], ['2026-11-10T09:15:00+05:30', 1, 2, 0.5, 1.2, 90, 0]]}})
        return self._send(404, {})


class UpstoxReader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(('127.0.0.1', 0), Fake)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def run_reader(self, request, commands=(), lines=6):
        proc = subprocess.Popen([sys.executable, '-m', 'corei_os_mini.md.upstox_reader'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        proc.stdin.write(json.dumps(request) + '\n')
        for c in commands:
            proc.stdin.write(json.dumps(c) + '\n')
        proc.stdin.flush()
        out = []
        for _ in range(lines):
            line = proc.stdout.readline()
            if not line:
                break
            out.append(json.loads(line))
            if out[-1]['t'] == 'failed':
                break
        proc.stdin.write(json.dumps({'cmd': 'stop'}) + '\n')
        proc.stdin.close()
        proc.wait(timeout=10)
        return out

    def test_quotes_and_history_in_the_collector_protocol(self):
        out = self.run_reader({'token': 'good', 'api_base': self.base, 'symbols': [KEY], 'poll_ms': 500},
                              [{'cmd': 'history', 'id': 'j1', 'symbol': KEY, 'timeframe': 'M1', 'from': '2026-11-10T03:00:00', 'to': '2026-11-10T04:00:00'}], lines=6)
        kinds = [m['t'] for m in out]
        self.assertEqual(kinds[0], 'connected')
        self.assertEqual(out[0]['server_time'], out[0]['utc_now'], 'Upstox times are UTC: no server offset')
        quotes = [m for m in out if m['t'] == 'q']
        self.assertTrue(quotes and quotes[0]['s'] == KEY and quotes[0]['b'] < quotes[0]['a'])
        self.assertTrue(all(m['n'] for m in quotes))
        bars = next(m for m in out if m['t'] == 'bars')
        self.assertEqual([b['t'] for b in bars['bars']], ['2026-11-10T03:45:00+00:00', '2026-11-10T03:46:00+00:00'], 'IST → UTC, sorted')
        self.assertNotIn('good', json.dumps(out), 'the token is never printed')

    def test_expired_login_is_a_refusal(self):
        out = self.run_reader({'token': 'expired', 'api_base': self.base, 'symbols': [KEY]}, lines=1)
        self.assertEqual(out[0]['t'], 'failed')
        self.assertIn('log in at the broker again', out[0]['attempts'][0]['error'])

    def test_parse_quote_without_depth(self):
        q = parse_quote({'last_price': 5.0, 'volume': 3, 'timestamp': '2026-11-12T10:00:00+05:30'})
        self.assertEqual((q['b'], q['a'], q['l'], q['st']), (None, None, 5.0, '2026-11-12T04:30:00+00:00'))
