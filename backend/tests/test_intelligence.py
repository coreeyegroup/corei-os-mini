"""Stage 2 · Market intelligence: indicators against hand-checked values, session map across daylight saving,
regime rules, the feature engine from stored M1 bars (resampling, closed bars only, history, snapshot), and the API."""
from __future__ import annotations

import importlib
import importlib.util
import math
import unittest
from datetime import datetime, timedelta, timezone

from corei_os_mini.intel import engine, indicators as ind, regime, sessions

tmd = importlib.import_module('test_market_data') if importlib.util.find_spec('test_market_data') else importlib.import_module('tests.test_market_data')
UTC = timezone.utc


class Indicators(unittest.TestCase):
    def test_sma_ema(self):
        self.assertEqual(ind.sma([1, 2, 3, 4, 5], 3), [None, None, 2.0, 3.0, 4.0])
        e = ind.ema([1, 2, 3, 4, 5], 3)  # seed 2.0, alpha 0.5
        self.assertEqual(e, [None, None, 2.0, 3.0, 4.0])
        self.assertAlmostEqual(ind.ema([2, 4, 6, 8, 20], 3)[-1], 0.5 * 20 + 0.5 * (0.5 * 8 + 0.5 * 4))

    def test_rsi_extremes_and_known_value(self):
        self.assertEqual(ind.rsi(list(range(1, 30)), 14)[-1], 100.0)
        self.assertEqual(ind.rsi(list(range(30, 1, -1)), 14)[-1], 0.0)
        # Alternating +2/−1: average gain 2·7/14=1, loss 0.5 → RS 2 → RSI 66.67 at the first value.
        c = [10.0]
        for i in range(14):
            c.append(c[-1] + (2 if i % 2 == 0 else -1))
        self.assertAlmostEqual(ind.rsi(c, 14)[14], 100 - 100 / 3, places=6)

    def test_atr_bollinger_adx(self):
        h = [10 + i * 0.5 + 1 for i in range(60)]
        lo = [10 + i * 0.5 - 1 for i in range(60)]
        c = [10 + i * 0.5 for i in range(60)]
        a = ind.atr(h, lo, c, 14)
        self.assertAlmostEqual(a[-1], 2.0, places=6)  # TR = max(2, |h-prev c|=1.5, |l-prev c|=0.5) = 2
        mid, up, dn = ind.bollinger([5.0] * 25, 20)
        self.assertEqual((mid[-1], up[-1], dn[-1]), (5.0, 5.0, 5.0))
        adx, pdi, mdi = ind.adx(h, lo, c, 14)
        self.assertGreater(adx[-1], 90, 'a straight line up is a maximal trend')
        self.assertGreater(pdi[-1], mdi[-1])
        self.assertAlmostEqual(ind.efficiency_ratio(c, 20)[-1], 1.0)
        self.assertEqual(ind.range_position(h, lo, c, 20)[-1], 100 * (c[-1] - lo[-20]) / (h[-1] - lo[-20]))
        rv = ind.realised_vol([100 * math.exp(0.01 * (i % 2)) for i in range(40)], 20, 252)[-1]
        self.assertGreater(rv, 0)


class Sessions(unittest.TestCase):
    def test_sessions_and_daylight_saving(self):
        # 2026-07-15 (London BST, NY EDT): 13:00 UTC = London 14:00 and NY 09:00 → overlap.
        s = sessions.sessions_at(datetime(2026, 7, 15, 13, 0, tzinfo=UTC))
        self.assertEqual(s['sessions'], ['London', 'New York'])
        self.assertEqual(s['label'], 'London + New York')
        # 2026-01-14 (GMT, EST): 12:30 UTC = London 12:30, NY 07:30 → London only.
        self.assertEqual(sessions.sessions_at(datetime(2026, 1, 14, 12, 30, tzinfo=UTC))['sessions'], ['London'])
        # Rollover: 17:00 NY = 22:00 UTC in winter.
        self.assertEqual(sessions.sessions_at(datetime(2026, 1, 14, 22, 5, tzinfo=UTC))['label'], 'Rollover')
        # Weekend: Saturday.
        self.assertEqual(sessions.sessions_at(datetime(2026, 1, 17, 12, 0, tzinfo=UTC))['label'], 'Closed (weekend)')
        # Sunday 22:30 UTC (17:30 NY winter): open; Sydney Monday 09:30 → Sydney.
        self.assertTrue(sessions.sessions_at(datetime(2026, 1, 18, 22, 30, tzinfo=UTC))['market_open'])
        nxt = sessions.next_changes(datetime(2026, 1, 14, 12, 30, tzinfo=UTC))
        self.assertEqual(nxt[0]['label'], 'London + New York')
        self.assertTrue(nxt[0]['at'].startswith('2026-01-14T13:00'))


class Regime(unittest.TestCase):
    def test_rules(self):
        base = {'ema20': 1.2, 'ema50': 1.1, 'plus_di': 30, 'minus_di': 10, 'atr_pct_rank100': 90, 'atr14': 0.01}
        self.assertEqual(regime.classify({**base, 'adx14': 30, 'er20': 0.5}, 300)['regime'], 'TREND_UP')
        self.assertEqual(regime.classify({**base, 'adx14': 30, 'er20': 0.5}, 300)['volatility'], 'HIGH')
        self.assertEqual(regime.classify({**base, 'ema20': 1.0, 'plus_di': 10, 'minus_di': 30, 'adx14': 30, 'er20': 0.5}, 300)['regime'], 'TREND_DOWN')
        self.assertEqual(regime.classify({**base, 'adx14': 15, 'er20': 0.1}, 300)['regime'], 'RANGE')
        self.assertEqual(regime.classify({**base, 'adx14': 22, 'er20': 0.5}, 300)['regime'], 'TRANSITION')
        self.assertEqual(regime.classify({**base, 'adx14': 30, 'er20': 0.5}, 10)['regime'], 'WARMING_UP')
        a = regime.attention({**base, 'adx14': 25}, 100, 0.0)
        self.assertEqual(a['score'], 100 * (0.5 * 0.9 + 0.5 * 0.5))
        self.assertEqual(regime.attention({**base, 'adx14': 25}, 100, 0.002)['parts']['spread_vs_atr'], 0.0)


class Resample(unittest.TestCase):
    def test_closed_bars_only(self):
        t0 = datetime(2026, 1, 14, 10, 0, tzinfo=UTC)
        m1 = [{'bar_time': t0 + timedelta(minutes=i), 'open': 1 + i, 'high': 2 + i, 'low': i, 'close': 1.5 + i, 'ticks': 1} for i in range(12)]
        m5 = engine.resample(m1, 'M5')
        self.assertEqual([b['bar_time'].minute for b in m5], [0, 5], 'the 10:10 bar is still forming')
        self.assertEqual((m5[0]['open'], m5[0]['high'], m5[0]['low'], m5[0]['close'], m5[0]['ticks']), (1, 6, 0, 5.5, 5))


class Engine(tmd.MarketData):
    test_plans_estimate_controls_and_jobs = None
    test_collector_end_to_end = None
    test_refused_login_reported_once_and_on_overview = None

    def test_features_snapshot_history_and_api(self):
        _, (a, _b, _c), instrument = self.setup_accounts()
        self.start(self.plan(a, instrument, 'EU_RT'))
        t0 = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=6000)
        rows = []
        price = 1.1
        for i in range(6000):  # ~100 hours of M1: a steady climb with noise
            price *= 1 + 0.00002 + 0.0001 * math.sin(i / 7)
            rows.append((a['id'], 'EURUSD', 'M1', t0 + timedelta(minutes=i), price, price * 1.0002, price * 0.9998, price * 1.00005, 10))
        with self.connect() as db:
            with db.cursor() as cur:
                cur.executemany('INSERT INTO md_bars(account_id, broker_symbol, timeframe, bar_time, open, high, low, close, ticks) '
                                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)', rows)
        status = engine.refresh_all()
        self.assertEqual(status['errors'], [])
        with self.connect() as db:
            snaps = {r['timeframe']: r for r in db.execute('SELECT * FROM intel_snapshot').fetchall()}
            hist = db.execute("SELECT count(*) AS n FROM intel_features WHERE timeframe='H1'").fetchone()['n']
        self.assertEqual(set(snaps), set(engine.TIMEFRAMES))
        self.assertEqual(snaps['H1']['bars_source'], 'M1')
        self.assertEqual(snaps['H1']['regime'], 'TREND_UP', snaps['H1']['reasons'])
        self.assertEqual(snaps['D1']['regime'], 'WARMING_UP')
        self.assertGreater(hist, 90)
        again = engine.refresh_all()
        self.assertEqual(again['history_rows'], 0, 'nothing new: the history is not written twice')
        s = self.ok(self.alice.get('/api/intel/screener?timeframe=H1'))
        self.assertEqual(s['items'][0]['broker_symbol'], 'EURUSD')
        self.assertEqual(s['items'][0]['regime'], 'TREND_UP')
        self.assertIsNotNone(s['items'][0]['attention'])
        self.assertIn('label', s['session'])
        detail = self.ok(self.alice.get(f"/api/intel/instrument/{a['id']}/EURUSD?timeframe=H1"))
        self.assertEqual([x['timeframe'] for x in detail['snapshots']], list(engine.TIMEFRAMES))
        self.assertGreater(len(detail['history']), 90)
        self.assertIn('label', self.ok(self.alice.get('/api/intel/sessions'))['now'])
        self.ok(self.alice.get('/api/intel/screener?timeframe=W1'), 422)


class Controls(tmd.MarketData):
    test_plans_estimate_controls_and_jobs = None
    test_collector_end_to_end = None
    test_refused_login_reported_once_and_on_overview = None

    def test_overview_settings_and_history(self):
        _, (a, _b, _c), instrument = self.setup_accounts()
        plan = self.plan(a, instrument, 'EU_RT')
        self.start(plan)
        t0 = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=600)
        with self.connect() as db:
            with db.cursor() as cur:
                cur.executemany('INSERT INTO md_bars(account_id, broker_symbol, timeframe, bar_time, open, high, low, close, ticks) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                                [(a['id'], 'EURUSD', 'M1', t0 + timedelta(minutes=i), 1.1, 1.1002, 1.0998, 1.1 + i * 1e-6, 5) for i in range(600)])
        engine.refresh_all()
        o = self.ok(self.alice.get('/api/intel/overview?timeframe=H1'))
        self.assertEqual((o['collected'], o['covered']), (1, 1))
        self.assertEqual(o['by_timeframe']['H1']['regime'], {'WARMING_UP': 1})
        self.assertEqual(o['warming_up'][0]['plan_id'], plan['id'])
        s = o['settings']
        bad = self.ok(self.alice.put('/api/intel/settings', json={**s, 'interval_seconds': 5, 'reason': 'test cycle'}), 422)
        self.assertIn('interval_seconds', str(bad))
        done = self.ok(self.alice.put('/api/intel/settings', json={**s, 'enabled': False, 'timeframes': ['H1'], 'reason': 'pause for test',
                                                                     'excluded': [{'account_id': a['id'], 'broker_symbol': 'EURUSD'}]}))
        self.assertEqual((done['enabled'], done['timeframes'], done['version']), (False, ['H1'], s['version'] + 1))
        self.ok(self.alice.put('/api/intel/settings', json={**s, 'reason': 'stale version'}), 409)
        self.assertTrue(engine.refresh_all()['paused'], 'paused: the cycle does nothing')
        forced = self.ok(self.alice.post('/api/intel/refresh'))
        self.assertEqual(forced['instruments'], 0, 'the excluded instrument is left out even when forced')
        jobs = self.ok(self.alice.post('/api/intel/history', json={'plan_id': plan['id'], 'reason': 'warm up EURUSD'}))
        self.assertEqual([j['timeframe'] for j in jobs['jobs']], ['M1', 'H4', 'D1'])
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) AS n FROM cfg_audit_log WHERE action='INTEL_SETTINGS'").fetchone()['n'], 1)


class PublicShell(unittest.TestCase):
    def test_every_api_router_is_reachable_through_caddy(self):
        """The public shell forwards only listed /api prefixes; a router missing there answers 403 in production."""
        import re
        from pathlib import Path
        from corei_os_mini.api import app
        caddy = (Path(__file__).resolve().parents[2] / 'deploy' / 'workstation.Caddyfile').read_text()
        allowed = re.search(r'@operational path ([^\n]+)', caddy).group(1).split()
        prefixes = {'/'.join(r.path.split('/')[:3]) for r in app.routes if getattr(r, 'path', '').startswith('/api/')}
        internal = {'/api/workstation', '/api/market-data', '/api/paper-runs', '/api/demo'}  # deliberately not on the public shell
        missing = sorted(p for p in prefixes if p not in internal and f'{p}/*' not in allowed)
        self.assertEqual(missing, [], f'add these to @operational in deploy/workstation.Caddyfile: {missing}')
