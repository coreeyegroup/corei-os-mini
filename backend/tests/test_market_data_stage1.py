"""Stage 1 · Market data integrity (release 0.54.0, migration 045): session-anchored bars on exchange time, last-price
bars and exchange volume for exchange instruments, plan health (a symbol the source does not list is caught, left
out and explained), backfill failure memory, symbols on every event, the feed gate in the risk check, and the
Upstox reader setting aside a rejected key. PostgreSQL tests need COREI_TEST_DATABASE_URL."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from uuid import uuid4
from zoneinfo import ZoneInfo

from corei_os_mini import config_store
from corei_os_mini.md import health, store
from corei_os_mini.md.bars import BarBuilder, bucket, event_time
from corei_os_mini.md.collector import Collector

from test_market_data import MarketData

IST = ZoneInfo('Asia/Kolkata')


def ist(h, m, s=0.0, day=23):
    return datetime(2026, 11, day, h, m, 0, tzinfo=IST).astimezone(timezone.utc) + timedelta(seconds=s)


class Bars(unittest.TestCase):
    def test_nse_bars_align_to_the_session_open(self):
        hours = [bucket(ist(h, m), 3600, 'NSE').astimezone(IST).strftime('%H:%M') for h, m in ((9, 15), (10, 14), (10, 15), (15, 29))]
        self.assertEqual(hours, ['09:15', '09:15', '10:15', '15:15'])     # 15:15–15:30 is the short last hour
        self.assertEqual(bucket(ist(9, 20), 900, 'NSE').astimezone(IST).strftime('%H:%M'), '09:15')
        self.assertEqual(bucket(ist(14, 0), 86400, 'NSE').astimezone(IST).strftime('%d %H:%M'), '23 00:00')  # D1: the IST day
        # Round-the-clock markets keep UTC alignment (no change for FX history).
        t = datetime(2026, 11, 23, 10, 37, tzinfo=timezone.utc)
        self.assertEqual(bucket(t, 3600, 'FX'), datetime(2026, 11, 23, 10, 0, tzinfo=timezone.utc))
        self.assertEqual(bucket(t, 3600, None), datetime(2026, 11, 23, 10, 0, tzinfo=timezone.utc))

    def test_bars_use_exchange_time_when_plausible(self):
        sent, received = ist(10, 59, 59.9), ist(11, 0, 0.2)
        at, basis = event_time(received, sent)
        self.assertEqual((at, basis), (sent, 'EVENT'))
        b = BarBuilder(['M1'], market='NSE')
        b.add(at, 100.0, 100.1, 100.05, None, basis)
        self.assertEqual(b.partial()[0]['bar_time'].astimezone(IST).strftime('%H:%M'), '10:59')   # not 11:00
        self.assertEqual(event_time(received, received - timedelta(minutes=10)), (received, 'RECEIPT'))  # old trade time: not trusted
        self.assertEqual(event_time(received, None), (received, 'RECEIPT'))

    def test_exchange_instruments_use_last_price_and_day_volume_increase(self):
        b = BarBuilder(['M1'], market='NSE', cumulative_volume=True)
        t = ist(10, 0, 1)
        for i, (bid, last, vol) in enumerate(((99.0, 100.0, 100), (99.5, 101.0, 150), (98.0, 99.5, 180))):
            b.add(t + timedelta(seconds=i), bid, bid + 1, last, vol, 'EVENT')
        bar = b.partial()[0]
        self.assertEqual((bar['open'], bar['high'], bar['low'], bar['close']), (100.0, 101.0, 99.5, 99.5))
        self.assertEqual(bar['volume'], 80.0)        # 150-100 + 180-150, not 430
        self.assertEqual((bar['price_basis'], bar['volume_kind'], bar['anchor'], bar['time_basis']), ('LAST', 'EXCHANGE', 'SESSION', 'EVENT'))
        b.add(ist(9, 15, 1, day=24), 101.0, 102.0, 101.5, 20, 'EVENT')   # next day: the total starts again
        self.assertEqual(b.partial()[0]['volume'], 20.0)
        # A price received more than 2 s after its bar ended is late for that bar (the timer may have closed it).
        late = BarBuilder(['S1'], market='FX')
        late.add(t, 1.1, 1.1002, None, None, 'EVENT', received=t)
        self.assertEqual(late.due(t + timedelta(seconds=3))[0]['ticks'], 1)
        self.assertEqual(late.add(t + timedelta(milliseconds=500), 1.2, 1.2002, None, None, 'EVENT', received=t + timedelta(seconds=3, milliseconds=100)), [])
        self.assertEqual(late.partial(), [])                           # not reopened
        fx = BarBuilder(['M1'], market='FX')
        fx.add(t, 1.1, 1.1002, None, 3)
        fx.add(t + timedelta(seconds=1), 1.1001, 1.1003, None, 2)
        bar = fx.partial()[0]
        self.assertEqual((bar['close'], bar['volume'], bar['price_basis'], bar['anchor'], bar['time_basis']), (1.1001, 5.0, 'BID', 'UTC', 'RECEIPT'))


class PlanState(unittest.TestCase):
    def test_most_serious_state_first(self):
        now = datetime(2026, 11, 23, 5, 0, tzinfo=timezone.utc)
        base = dict(source_state='CONNECTED', invalid=False, errors=0, in_session=True, last_rx=now, now=now, stale_seconds=10)
        self.assertEqual(health.plan_state(**base), 'LIVE')
        self.assertEqual(health.plan_state(**{**base, 'invalid': True, 'errors': 3, 'source_state': 'FAILED'}), 'INVALID_SYMBOL')
        self.assertEqual(health.plan_state(**{**base, 'errors': 1}), 'ERROR')
        self.assertEqual(health.plan_state(**{**base, 'source_state': 'RECONNECTING'}), 'OFFLINE')
        self.assertEqual(health.plan_state(**{**base, 'in_session': False, 'last_rx': None}), 'CLOSED')
        self.assertEqual(health.plan_state(**{**base, 'last_rx': None}), 'WAITING')
        self.assertEqual(health.plan_state(**{**base, 'last_rx': now - timedelta(seconds=30)}), 'STALE')
        self.assertEqual(health.plan_state(**{**base, 'breaching': {'LATENCY'}}), 'DEGRADED')
        self.assertEqual(set(health.WORDS), set(health.STATES))


@unittest.skipUnless(os.getenv('COREI_TEST_DATABASE_URL'), 'needs PostgreSQL')
class Stage1(MarketData):
    def catalogue(self, account, symbols):
        with self.connect() as db:
            for s in symbols:
                db.execute("INSERT INTO md_symbol_catalog(account_id, broker_symbol, display_symbol, source) VALUES (%s,%s,%s,'MT5_LOGIN')",
                           (account['id'], s, s))

    def test_unlisted_symbol_is_caught_left_out_and_explained(self):
        _, (a, _b, c), instrument = self.setup_accounts()           # account b is the fake's silent login
        bad = self.start(self.plan(a, instrument, 'EU_A'))          # accepted: the source had no symbol list yet
        good = self.start(self.plan(c, instrument, 'EU_C', role='BACKUP'))
        self.catalogue(a, ['EURUSD.a', 'GBPUSD'])                  # the list arrives without EURUSD
        self.catalogue(c, ['EURUSD'])
        self.collector = Collector(self.root)
        self.run_collector(2.5)
        worker = self.collector.workers[a['id']]
        self.assertNotIn('EURUSD', worker.wanted())                  # never subscribed
        with self.connect() as db:
            rows = {str(r['plan_id']): r for r in db.execute('SELECT * FROM md_plan_health').fetchall()}
            event = db.execute("SELECT * FROM md_events WHERE kind='SYMBOL_INVALID'").fetchone()
            ticks_a = db.execute('SELECT count(*) AS n FROM md_ticks WHERE account_id=%s', (a['id'],)).fetchone()['n']
        self.assertEqual(rows[bad['id']]['state'], 'INVALID_SYMBOL')
        self.assertEqual(rows[bad['id']]['suggestions'], ['EURUSD.a'])
        self.assertIn('Did you mean: EURUSD.a', rows[bad['id']]['detail'])
        self.assertEqual(rows[good['id']]['state'], 'LIVE')
        self.assertEqual(ticks_a, 0)
        self.assertEqual((event['broker_symbol'], event['severity']), ('EURUSD', 'CRITICAL'))
        # Screens: the plan list and the overview say it in words; the header counts it as a problem.
        plans = {p['code']: p for p in self.ok(self.alice.get('/api/md/plans'))['items']}
        self.assertEqual((plans['EU_A']['health'], plans['EU_C']['health']), ('INVALID_SYMBOL', 'LIVE'))
        overview = self.ok(self.alice.get('/api/config/overview'))
        self.assertEqual(overview['feeds']['plans'].get('INVALID_SYMBOL'), 1)
        self.assertGreaterEqual(overview['feeds']['problems'], 1)
        self.assertTrue(any('EU_A' in a['title'] and a['severity'] == 'CRITICAL' for a in overview['attention']))
        # The order check names the problem.
        check = self.ok(self.alice.post(f"/api/config/accounts/{a['id']}/order-check", json={'broker_symbol': 'EURUSD', 'side': 'BUY', 'volume': '0.10'}))
        self.assertTrue(check['problems'])

    def test_journal_replay_rebuilds_the_stored_bars(self):
        from corei_os_mini.md import replay
        _, (a, _b, _c), instrument = self.setup_accounts()
        self.start(self.plan(a, instrument, 'EU_A'))
        self.collector = Collector(self.root)
        self.run_collector(3.0)
        self.collector.shutdown()
        self.collector = None
        day = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        result = replay.check('VTM_A', day, self.root)
        self.assertGreater(result['matched'], 0, result)
        self.assertEqual((result['different'], result['missing_in_database']), ([], []), result)
        self.assertTrue(result['exact'])
        self.assertIn('error', replay.check('VTM_A', '2001-01-01', self.root))
        # A changed stored bar is found.
        with self.connect() as db:
            db.execute("UPDATE md_bars SET close=close+0.001 WHERE account_id=%s AND timeframe='S1' AND complete AND bar_time="
                       "(SELECT min(bar_time) FROM md_bars WHERE account_id=%s AND timeframe='S1' AND complete)", (a['id'], a['id']))
        broken = replay.check('VTM_A', day, self.root)
        self.assertFalse(broken['exact'])
        self.assertEqual(broken['different'][0]['fields'], ['close'])

    def test_failed_automatic_backfills_wait_and_events_name_the_symbol(self):
        _, (a, _b, _c), instrument = self.setup_accounts()
        p = self.start(self.plan(a, instrument, 'EU_A'))
        now = store.utcnow()
        with self.connect() as db:
            for i in range(3):
                db.execute("""INSERT INTO md_jobs(id, plan_id, timeframe, range_from, range_to, status, error, requested_by, updated_at)
                              VALUES (%s,%s,'M1',%s,%s,'FAILED','UDAPI1021 invalid instrument','collector',%s)""",
                           (uuid4(), p['id'], now - timedelta(hours=2), now - timedelta(hours=1), now - timedelta(minutes=i)))
        store.queue_gap_backfill(p['id'], 'M1', now - timedelta(minutes=30), now)
        store.queue_gap_backfill(p['id'], 'M1', now - timedelta(minutes=20), now)
        with self.connect() as db:
            queued = db.execute("SELECT count(*) AS n FROM md_jobs WHERE status='QUEUED'").fetchone()['n']
            held = db.execute("SELECT * FROM md_events WHERE kind='BACKFILL_HELD'").fetchall()
            self.assertEqual(queued, 0)
            self.assertEqual(len(held), 1)                            # once, not per outage
            self.assertEqual(held[0]['broker_symbol'], 'EURUSD')
            self.assertIn('3 automatic backfills failed', held[0]['detail']['reason'])
            # After the wait (15 min for the third failure) one more try is allowed.
            self.assertIsNone(store.backfill_hold(db, p['id'], 'M1', now + timedelta(minutes=16)))
            # A manual job that succeeds is not counted; an automatic success resets the count.
            db.execute("""INSERT INTO md_jobs(id, plan_id, timeframe, range_from, range_to, status, requested_by, updated_at)
                          VALUES (%s,%s,'M1',%s,%s,'DONE','collector',%s)""", (uuid4(), p['id'], now - timedelta(hours=1), now, now + timedelta(seconds=1)))
            self.assertIsNone(store.backfill_hold(db, p['id'], 'M1', now + timedelta(seconds=2)))

    def test_feed_gate(self):
        _, (a, _b, _c), instrument = self.setup_accounts()
        p = self.start(self.plan(a, instrument, 'EU_A'))
        now = store.utcnow()
        with self.connect() as db:
            policy = config_store.policy
            self.assertIsNone(health.gate(db, a['id'], 'EURUSD', now, policy))          # no health recorded: price age only
            store.save_health(db, [{'plan_id': p['id'], 'account_id': a['id'], 'broker_symbol': 'EURUSD', 'state': 'ERROR', 'detail': 'UDAPI1087',
                                    'suggestions': [], 'last_price_at': None, 'consecutive_errors': 4, 'last_error': 'UDAPI1087'}])
            self.assertTrue(health.gate(db, a['id'], 'EURUSD', now, policy).startswith('FEED_ERROR'))
            store.save_health(db, [{'plan_id': p['id'], 'account_id': a['id'], 'broker_symbol': 'EURUSD', 'state': 'LIVE', 'detail': '',
                                    'suggestions': [], 'last_price_at': now, 'consecutive_errors': 0, 'last_error': None}])
            self.assertIsNone(health.gate(db, a['id'], 'EURUSD', now, policy))
            db.execute("""INSERT INTO md_quality(account_id, broker_symbol, minute, ticks, frozen_ms, spikes, score)
                          VALUES (%s,'EURUSD',%s,20,60000,0,25)""", (a['id'], now - timedelta(minutes=1)))
            self.assertTrue(health.gate(db, a['id'], 'EURUSD', now, policy).startswith('FEED_QUALITY'))
            db.execute("UPDATE cfg_policies SET value='0'::jsonb WHERE key='md_min_quality_score'")
            self.assertIsNone(health.gate(db, a['id'], 'EURUSD', now, policy))      # the owner can turn the score rule off
            since = db.execute('SELECT since FROM md_plan_health').fetchone()['since']
            store.save_health(db, [{'plan_id': p['id'], 'account_id': a['id'], 'broker_symbol': 'EURUSD', 'state': 'LIVE', 'detail': '',
                                    'suggestions': [], 'last_price_at': None, 'consecutive_errors': 0, 'last_error': None}])
            row = db.execute('SELECT since, last_price_at FROM md_plan_health').fetchone()
            self.assertEqual(row['since'], since)                     # same state: since kept
            self.assertIsNotNone(row['last_price_at'])                # no new price time: last kept


# ---------- Upstox reader: a rejected key is set aside; quotes without a trade carry no trade time ----------
GOOD, BAD = 'NSE_EQ|INE002A01018', 'NSE_INDEX|Nifty50'
STATE = {'bid': 1300.0}


class FakeUpstox(BaseHTTPRequestHandler):
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
        if self.path.startswith('/v2/market-quote/quotes'):
            if 'Nifty50' in self.path:
                return self._send(400, {'status': 'error', 'errors': [{'errorCode': 'UDAPI1087', 'message': 'Invalid instrument key'}]})
            STATE['bid'] += 0.05   # the book moves; no new trade (last price and volume unchanged)
            return self._send(200, {'status': 'success', 'data': {'NSE_EQ:RELIANCE': {
                'instrument_token': GOOD, 'last_price': 1300.5, 'volume': 1000, 'last_trade_time': '1762929000000',
                'depth': {'buy': [{'price': STATE['bid']}], 'sell': [{'price': STATE['bid'] + 1}]}}}})
        return self._send(404, {})


class UpstoxBadKey(unittest.TestCase):
    def test_bad_key_is_set_aside_and_others_keep_flowing(self):
        server = HTTPServer(('127.0.0.1', 0), FakeUpstox)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            proc = subprocess.Popen([sys.executable, '-m', 'corei_os_mini.md.upstox_reader'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            proc.stdin.write(json.dumps({'token': 't', 'api_base': f'http://127.0.0.1:{server.server_port}', 'symbols': [GOOD, BAD], 'poll_ms': 500}) + '\n')
            proc.stdin.flush()
            out = []
            while len([m for m in out if m['t'] == 'q']) < 3:
                out.append(json.loads(proc.stdout.readline()))
            proc.stdin.write(json.dumps({'cmd': 'stop'}) + '\n')
            proc.stdin.close()
            proc.wait(timeout=10)
        finally:
            server.shutdown()
        kinds = [m['t'] for m in out]
        self.assertIn('connected', kinds)
        errors = [m for m in out if m['t'] == 'symbol_error']
        self.assertEqual([e['s'] for e in errors], [BAD])                # reported once
        quotes = [m for m in out if m['t'] == 'q']
        self.assertTrue(all(q['s'] == GOOD for q in quotes))
        self.assertTrue(all(q['st'] is None for q in quotes))            # no new trade: no trade time, no false latency


if __name__ == '__main__':
    unittest.main()
