"""Stage 2 · Market intelligence integrity (release 0.55.0, migration 045): session-aligned resampling shared with
Stage 1, the bar-source policy (a broker series that stopped is continued, not preferred), freshness by the market's
hours, market-aware volatility and session VWAP, the regime filter (entries only; exits always pass), event risk for
exchange markets, rule preview, Upstox holiday import and the one-run-at-a-time lock. PostgreSQL tests need
COREI_TEST_DATABASE_URL."""
from __future__ import annotations

import importlib
import importlib.util
import json
import math
import os
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

from corei_os_mini.intel import engine, workspace
from corei_os_mini.trading import algorithms

tmd = importlib.import_module('test_market_data') if importlib.util.find_spec('test_market_data') else importlib.import_module('tests.test_market_data')
UTC, IST = timezone.utc, ZoneInfo('Asia/Kolkata')


def ist(day, h, m):
    return datetime(2026, 11, day, h, m, tzinfo=IST).astimezone(UTC)


def m1_bars(start, minutes, price=100.0, step=0.0, volume=0):
    return [{'bar_time': start + timedelta(minutes=i), 'open': price + i * step, 'high': price + i * step + 0.5, 'low': price + i * step - 0.5,
             'close': price + i * step, 'ticks': 1, 'volume': volume} for i in range(minutes)]


class Alignment(unittest.TestCase):
    def test_nse_hours_follow_the_session_and_end_at_the_close(self):
        day = m1_bars(ist(23, 9, 15), 375)                 # 09:15–15:29 IST
        h1 = engine.resample(day, 'H1', 'NSE')
        self.assertEqual([b['bar_time'].astimezone(IST).strftime('%H:%M') for b in h1],
                         ['09:15', '10:15', '11:15', '12:15', '13:15', '14:15', '15:15'])   # the 15:15 bar closes at 15:30
        self.assertEqual(engine.bar_end(h1[-1]['bar_time'], 'H1', 'NSE').astimezone(IST).strftime('%H:%M'), '15:30')
        d1 = engine.resample(day, 'D1', 'NSE')
        self.assertEqual(len(d1), 1)                       # the day is closed at 15:30
        self.assertEqual(engine.bucket(ist(23, 14, 0), 'D1', 'NSE').astimezone(IST).strftime('%d %H:%M'), '23 00:00')
        fx = m1_bars(datetime(2026, 11, 23, 10, 0, tzinfo=UTC), 125)
        self.assertEqual([b['bar_time'].strftime('%H:%M') for b in engine.resample(fx, 'H1')], ['10:00', '11:00'])   # FX unchanged

    def test_broker_grid_is_followed_and_a_stopped_broker_series_is_continued(self):
        # The broker's D1 starts at 22:00 UTC (server midnight) and stopped a week ago; M1 continues to today.
        native = [{'bar_time': datetime(2026, 11, 1, 22, tzinfo=UTC) + timedelta(days=i), 'open': 1, 'high': 2, 'low': 0, 'close': 1.5, 'ticks': 9}
                  for i in range(16)]                       # last: 16 Nov 22:00
        m1 = m1_bars(datetime(2026, 11, 18, 22, tzinfo=UTC), 5 * 1440, 1.0)
        bars, source = engine.choose(m1, native, 'D1', 'FX')
        self.assertEqual(source, 'MIXED')
        self.assertEqual(bars[:16], native)
        self.assertTrue(all(b['bar_time'].hour == 22 for b in bars), 'built bars follow the broker grid')
        self.assertEqual(bars[-1]['bar_time'], datetime(2026, 11, 22, 22, tzinfo=UTC))
        fresh = native + [{**native[-1], 'bar_time': datetime(2026, 11, 23, 22, tzinfo=UTC)}]
        self.assertEqual(engine.choose(m1, fresh, 'D1', 'FX')[1], 'BROKER')   # as recent and longer: the broker's own
        self.assertEqual(engine.choose(m1, [], 'D1', 'FX')[1], 'M1')


class Freshness(unittest.TestCase):
    def test_fresh_lagging_stale_and_closed(self):
        bar = datetime(2026, 11, 23, 10, tzinfo=UTC)          # FX H1 10:00–11:00, Monday
        self.assertEqual(engine.freshness(bar, 'H1', 'FX', datetime(2026, 11, 23, 11, 30, tzinfo=UTC))[0], 'FRESH')
        self.assertEqual(engine.freshness(bar, 'H1', 'FX', datetime(2026, 11, 23, 13, 30, tzinfo=UTC))[0], 'LAGGING')
        self.assertEqual(engine.freshness(bar, 'H1', 'FX', datetime(2026, 11, 23, 20, tzinfo=UTC))[0], 'STALE')
        last_nse = ist(23, 15, 15)                               # the last NSE hour of Monday
        self.assertEqual(engine.freshness(last_nse, 'H1', 'NSE', ist(24, 8, 0))[0], 'CLOSED')      # overnight: nothing to miss
        self.assertEqual(engine.freshness(last_nse, 'H1', 'NSE', ist(24, 13, 0))[0], 'STALE')      # Tuesday traded since
        friday = datetime(2026, 11, 20, 21, tzinfo=UTC)          # FX's last hour before the weekend close (17:00 NY = 22:00 UTC)
        self.assertEqual(engine.freshness(friday, 'H1', 'FX', datetime(2026, 11, 21, 12, tzinfo=UTC))[0], 'CLOSED')
        holiday = frozenset({datetime(2026, 11, 24).date()})
        self.assertEqual(engine.freshness(last_nse, 'H1', 'NSE', ist(24, 13, 0), holiday)[0], 'CLOSED')   # an NSE holiday


class MarketFeatures(unittest.TestCase):
    def test_trading_year_session_vwap_and_label(self):
        self.assertEqual(engine.bars_per_year('M1', 'NSE'), 252 * 375)
        self.assertEqual(engine.bars_per_year('H1', 'NSE'), 252 * 7)
        self.assertEqual(engine.bars_per_year('M1', 'FX'), 252 * 1440)
        self.assertEqual(engine.bars_per_year('D1', 'CRYPTO'), 365)
        two_days = m1_bars(ist(23, 9, 15), 30, 100, 0.1, volume=10) + m1_bars(ist(24, 9, 15), 30, 200, 0.0, volume=10)
        rows = engine.features(two_days, 'M1', market='NSE')
        self.assertAlmostEqual(rows[29]['vwap_session'], sum(100 + i * 0.1 for i in range(30)) / 30, places=6)
        self.assertAlmostEqual(rows[30]['vwap_session'], 200.0, places=6)   # resets at the next session
        self.assertIsNone(engine.features(two_days, 'M1', market='FX')[30]['vwap_session'])
        self.assertEqual(engine.session_label(ist(23, 10, 0), 'NSE'), 'NSE open')
        self.assertEqual(engine.session_label(ist(23, 18, 0), 'NSE'), 'NSE closed')


class RegimeFilter(unittest.TestCase):
    TREND = {'adx14': 35, 'er20': 0.6, 'ema20': 2, 'ema50': 1, 'plus_di': 30, 'minus_di': 10, 'atr_pct_rank100': 50}
    RANGE = {'adx14': 10, 'er20': 0.1, 'ema20': 1, 'ema50': 1, 'plus_di': 15, 'minus_di': 15, 'atr_pct_rank100': 50}

    def walk(self, targets):
        out, state = [], 'FLAT'
        for t in targets:
            out.append({'target': t, 'change': t != state, 'strength': 70 if t != state else 0, 'reason': 'algo' if t != state else ''})
            state = t
        return out

    def test_entries_only_in_allowed_states_and_exits_always(self):
        warm = 60   # the regime needs 60 bars (regime.MIN_BARS) before it is more than "warming up"
        walk = self.walk(['FLAT'] * warm + ['LONG', 'LONG', 'LONG', 'FLAT'])
        rows = [self.RANGE] * warm + [self.RANGE, self.RANGE, self.TREND, self.RANGE]
        out = algorithms.regime_filter(walk, rows, ['TREND_UP'], [])
        self.assertEqual([w['target'] for w in out[warm:]], ['FLAT', 'FLAT', 'LONG', 'FLAT'])   # entry waits for the trend
        self.assertTrue(out[warm + 2]['change'] and 'allowed' in out[warm + 2]['reason'])
        self.assertTrue(out[warm + 3]['change'])                                                 # the exit passes in a range
        self.assertIs(algorithms.regime_filter(walk, rows, [], []), walk)                        # no filter: unchanged
        rev = algorithms.regime_filter(self.walk(['FLAT'] * warm + ['LONG', 'SHORT']), [self.TREND] * (warm + 1) + [self.RANGE],
                                       ['TREND_UP', 'TREND_DOWN'], [])
        self.assertEqual([w['target'] for w in rev[warm:]], ['LONG', 'FLAT'])                    # a blocked reversal exits
        high = {**self.TREND, 'atr_pct_rank100': 95}
        blocked = algorithms.regime_filter(self.walk(['FLAT'] * warm + ['LONG']), [high] * (warm + 1), [], ['HIGH'])
        self.assertEqual(blocked[-1]['target'], 'FLAT')
        p = algorithms.EmaCross.model_validate({'timeframe': 'H1', 'allowed_regimes': ['TREND_UP'], 'blocked_volatility': ['HIGH']})
        self.assertEqual((p.allowed_regimes, p.blocked_volatility), (['TREND_UP'], ['HIGH']))
        with self.assertRaises(ValueError):
            algorithms.EmaCross.model_validate({'timeframe': 'H1', 'allowed_regimes': ['WARMING_UP']})


class EventRisk(unittest.TestCase):
    def test_exchange_markets_have_event_risk(self):
        self.assertEqual(workspace.event_codes('NSE_INDEX|Nifty 50', None, None, 'NSE'), {'NSE', 'INR'})
        self.assertEqual(workspace.event_codes('NSE_FO|NIFTY25DECFUT', None, None, 'NSE_FO'), {'NSE', 'INR'})
        self.assertEqual(workspace.event_codes('EURUSD', None, None, 'FX'), {'EUR', 'USD'})
        at = ist(26, 0, 0)
        hol = [{'at': at, 'currency': 'NSE', 'impact': 'HOLIDAY', 'title': 'Holiday'}]
        self.assertEqual(len(workspace.event_risk('NSE_INDEX|Nifty 50', None, None, at, 30, hol, 'NSE')), 1)
        self.assertEqual(workspace.event_risk('EURUSD', None, None, at, 30, hol, 'FX'), [])


HOLIDAYS = {'status': 'success', 'data': [
    {'date': '2026-11-24', 'description': 'Guru Nanak Jayanti', 'holiday_type': 'TRADING_HOLIDAY', 'closed_exchanges': ['NSE', 'NFO', 'BSE', 'BFO'],
     'open_exchanges': [{'exchange': 'MCX', 'start_time': 1, 'end_time': 2}]},
    {'date': '2026-11-27', 'description': 'Settlement only', 'holiday_type': 'SETTLEMENT_HOLIDAY', 'closed_exchanges': [], 'open_exchanges': []}]}


class FakeUpstox(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps(HOLIDAYS if self.path == '/v2/market/holidays' else {}).encode()
        self.send_response(200 if self.path == '/v2/market/holidays' else 404)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@unittest.skipUnless(os.getenv('COREI_TEST_DATABASE_URL'), 'needs PostgreSQL')
class Stage2(tmd.MarketData):
    test_plans_estimate_controls_and_jobs = None
    test_collector_end_to_end = None
    test_refused_login_reported_once_and_on_overview = None

    def seed(self, a, minutes=6000, native_d1_stop=None):
        t0 = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=minutes)
        rows, price = [], 1.1
        for i in range(minutes):
            price *= 1 + 0.00002 + 0.0001 * math.sin(i / 7)
            rows.append((a['id'], 'EURUSD', 'M1', t0 + timedelta(minutes=i), price, price * 1.0002, price * 0.9998, price * 1.00005, 10))
        if native_d1_stop:
            rows += [(a['id'], 'EURUSD', 'D1', native_d1_stop - timedelta(days=k), 1.0, 1.01, 0.99, 1.0, 100) for k in range(80)]
        with self.connect() as db:
            with db.cursor() as cur:
                cur.executemany('INSERT INTO md_bars(account_id, broker_symbol, timeframe, bar_time, open, high, low, close, ticks) '
                                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)', rows)

    def test_snapshot_freshness_mixed_source_runtime_parity_and_incremental_runs(self):
        from corei_os_mini.trading import runtime
        _, (a, _b, _c), instrument = self.setup_accounts()
        self.start(self.plan(a, instrument, 'EU_RT'))
        stop = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=7)
        self.seed(a, native_d1_stop=stop)
        self.assertEqual(engine.refresh_all(force=True)['errors'], [])
        with self.connect() as db:
            snaps = {r['timeframe']: r for r in db.execute('SELECT * FROM intel_snapshot').fetchall()}
            self.assertEqual(snaps['D1']['bars_source'], 'MIXED')
            self.assertGreater(snaps['D1']['bar_time'], stop, 'the stopped broker series is continued with M1-built days')
            self.assertEqual(snaps['D1']['version'], 4)
            self.assertIn(snaps['H1']['freshness'], ('FRESH', 'CLOSED'))
            bars, rows = runtime.series(db, (a['id'], 'EURUSD'), 'D1')
            self.assertEqual(bars[-1]['bar_time'], snaps['D1']['bar_time'], 'strategies read the same bars as the screen')
            self.assertEqual((rows[-1]['feature_version'], rows[-1]['bars_source']), (4, 'MIXED'))
            computed = {r['timeframe']: r['computed_at'] for r in db.execute('SELECT timeframe, computed_at FROM intel_snapshot').fetchall()}
        again = engine.refresh_all(force=True)
        self.assertEqual((again['snapshots'], again['history_rows']), (0, 0), 'nothing new closed: only freshness is refreshed')
        with self.connect() as db:
            later = {r['timeframe']: r['computed_at'] for r in db.execute('SELECT timeframe, computed_at FROM intel_snapshot').fetchall()}
        self.assertTrue(all(later[tf] >= computed[tf] for tf in computed))
        o = self.ok(self.alice.get('/api/intel/overview?timeframe=H1'))
        self.assertIn('freshness', o)
        s = self.ok(self.alice.get('/api/intel/screener?timeframe=D1'))['items'][0]
        self.assertEqual((s['bars_source'], s['market']), ('MIXED', 'FX'))
        self.assertIn('feed_health', s)

    def test_one_run_at_a_time(self):
        self.assertTrue(engine.RUN_LOCK.acquire())
        try:
            self.assertTrue(engine.refresh_all()['busy'], 'the cycle skips while a run is in progress')
        finally:
            engine.RUN_LOCK.release()

    def test_rule_preview_and_strength_warning(self):
        _, (a, _b, _c), instrument = self.setup_accounts()
        self.start(self.plan(a, instrument, 'EU_RT'))
        self.seed(a)
        engine.refresh_all(force=True)
        never = self.ok(self.alice.post('/api/intel/rules/preview', json={'timeframe': 'H1', 'field': 'ret_1_pct', 'op': 'CROSSES_BELOW', 'value': '30'}))
        self.assertEqual(never['fires'], 0)
        self.assertIn('would not have fired', never['warning'])
        always = self.ok(self.alice.post('/api/intel/rules/preview', json={'timeframe': 'H1', 'field': 'close', 'op': '>', 'value': '0', 'cooldown_minutes': 0}))
        self.assertIn('almost always', always['warning'])
        crosses = self.ok(self.alice.post('/api/intel/rules/preview', json={'timeframe': 'H1', 'field': 'ema20', 'op': 'CROSSES_ABOVE', 'value': 'ema50',
                                                                           'cooldown_minutes': 0, 'days': 30}))
        self.assertEqual(crosses['by_instrument'][0]['broker_symbol'], 'EURUSD')
        self.ok(self.alice.post('/api/intel/rules/preview', json={'timeframe': 'H1', 'field': 'nonsense', 'op': '>', 'value': '1'}), 422)
        strength = self.ok(self.alice.get('/api/intel/strength?timeframe=H1'))
        self.assertIn('at least 3 currencies', strength['warning'])

    def test_upstox_holidays_import(self):
        server = HTTPServer(('127.0.0.1', 0), FakeUpstox)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            out = workspace.import_upstox_holidays('alice', None, f'http://127.0.0.1:{server.server_port}')
            again = workspace.import_upstox_holidays('alice', None, f'http://127.0.0.1:{server.server_port}')
        finally:
            server.shutdown()
        self.assertEqual((out['holidays'], len(out['not_stored'])), (2, 1))   # NSE and BSE; the settlement day is reported
        with self.connect() as db:
            rows = db.execute("SELECT currency, source, (at AT TIME ZONE 'Asia/Kolkata')::text AS local FROM intel_events ORDER BY currency").fetchall()
        self.assertEqual([(r['currency'], r['source'], r['local'][:16]) for r in rows],
                         [('BSE', 'UPSTOX', '2026-11-24 00:00'), ('NSE', 'UPSTOX', '2026-11-24 00:00')])
        self.assertEqual(again['added'], 2)                                    # re-import updates in place
        from corei_os_mini.md import collector, store as md_store
        cal = md_store.holidays()
        plan = {'instrument_market': 'NSE_FO', 'broker_symbol': 'NSE_FO|X', 'platform': 'REST_API'}
        self.assertIn(datetime(2026, 11, 24).date(), collector.plan_holidays(plan, cal), 'Stage 1 closes NSE F&O that day')


if __name__ == '__main__':
    unittest.main()
