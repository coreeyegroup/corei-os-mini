"""Stage 3 · Signal intelligence integrity (release 0.56.0, migration 047): hysteresis (an exit threshold keeps a side
until the score falls back, cutting flips), honest research (overlapping returns counted as n ÷ h, an out-of-sample
check before PREDICTIVE, edge net of the spread, multiple-testing note), stale scores left out of the consensus, and
strategy-signal quality net of the spread paid. PostgreSQL tests need COREI_TEST_DATABASE_URL."""
from __future__ import annotations

import importlib
import importlib.util
import math
import os
import random
import unittest
from datetime import timedelta
from decimal import Decimal

from corei_os_mini import config_store as store
from corei_os_mini.trading import runtime, signal_library, signals

tt = importlib.import_module('test_trading') if importlib.util.find_spec('test_trading') else importlib.import_module('tests.test_trading')
NOW = tt.NOW


def bars_from(closes, spread=0.00008):
    return [{'bar_time': NOW + timedelta(minutes=5 * i), 'open': c, 'high': c + 0.0004, 'low': c - 0.0004, 'close': c, 'ticks': 10, 'volume': 0,
             'spread_avg': spread} for i, c in enumerate(closes)]


class Hysteresis(unittest.TestCase):
    def test_exit_threshold_holds_a_side_and_cuts_flips(self):
        scores = [25, 18, 22, 15, 21, 5, -25, -12, -21, -8, None]
        plain = signal_library.directions(scores, 20)
        held = signal_library.directions(scores, 20, 10)
        self.assertEqual(plain, ['LONG', 'FLAT', 'LONG', 'FLAT', 'LONG', 'FLAT', 'SHORT', 'FLAT', 'SHORT', 'FLAT', 'FLAT'])
        self.assertEqual(held, ['LONG', 'LONG', 'LONG', 'LONG', 'LONG', 'FLAT', 'SHORT', 'SHORT', 'SHORT', 'FLAT', 'FLAT'])
        flips = lambda s: sum(1 for a, b in zip(s, s[1:]) if a != b)  # noqa: E731
        self.assertLess(flips(held), flips(plain))
        self.assertEqual(signal_library.direction(-30, 20, 10, 'LONG'), 'SHORT', 'a reversal past the threshold is taken at once')
        self.assertEqual(signal_library.directions(scores, 20, None), plain, 'no exit threshold: the behaviour before')


class HonestResearch(unittest.TestCase):
    def closes(self, n=600):
        return [1.10 + 0.004 * math.sin(i / 3.1) + 0.003 * math.sin(i / 7.3) for i in range(n)]

    def test_overlap_adjusted_t_and_out_of_sample(self):
        c = self.closes()
        bars = bars_from(c)
        h = 10
        cut = int(0.7 * (len(c) - h))
        # A score that "knows" the next 10 bars on the first 70% and is reversed afterwards: it looks predictive in
        # sample and fails out of sample.
        series = [None if t + h >= len(c) else ((100 if c[t + h] > c[t] else -100) * (1 if t < cut else -1), {}) for t in range(len(c))]
        out = signal_library.research('EMA_TREND', {}, bars, [h], 20, series)
        hz = out['horizons'][0]
        self.assertAlmostEqual(hz['n_effective'], hz['n'] / h, places=1)
        naive_t = hz['ic'] * math.sqrt((hz['n'] - 2) / (1 - hz['ic'] ** 2))
        self.assertLess(abs(hz['ic_t']), abs(naive_t) / 2, 'overlapping 10-bar returns no longer count as 10× the evidence')
        self.assertLess(hz['oos_ic'], 0, 'the last 30% goes the other way')
        self.assertNotEqual(out['verdict'], 'PREDICTIVE')
        self.assertIn('out of sample', out['verdict_note'])
        self.assertIn('net_edge_bp', hz)
        self.assertAlmostEqual(hz['net_edge_bp'], hz['avg_signed_return_bp'] - out['avg_spread_bp'], places=2)
        honest = [None if t + h >= len(c) else (100 if c[t + h] > c[t] else -100, {}) for t in range(len(c))]
        good = signal_library.research('EMA_TREND', {}, bars_from(c, spread=0.000001), [h], 20, honest)
        self.assertEqual(good['verdict'], 'PREDICTIVE', 'consistent in and out of sample, edge above cost')


@unittest.skipUnless(os.getenv('COREI_TEST_DATABASE_URL'), 'needs PostgreSQL')
class Stage3(tt.Trading):
    test_paper_trade_end_to_end = None
    test_tickets_limits_and_unknown = None
    test_broker_mode_refused_and_loss_limit_stops = None
    test_backtest_no_look_ahead = None

    def test_exit_threshold_definition_and_fewer_flips(self):
        _connection, (a, _b, _c), ins = self.setup_accounts()
        self.start(self.plan(a, ins, 'EU_SIG'))
        rng, x, closes = random.Random(7), 1.10, []
        for _ in range(400):   # a random walk: RSI wanders around 50 and its score keeps crossing ±20
            x += rng.gauss(0, 0.0004)
            closes.append(round(x, 5))
        self.bars(a['id'], closes, NOW - timedelta(minutes=5 * 400))
        body = {'code': 'RSI_WOBBLE', 'name': 'RSI reversion, plain', 'kind': 'RSI_REVERSION', 'parameters': {}, 'threshold': 20, 'reason': 'flip count test',
                'timeframes': ['M5']}
        bad = self.bob.post('/api/trading/signal-library/definitions', json={**body, 'code': 'RSI_BAD', 'exit_threshold': 25})
        self.assertEqual(bad.status_code, 422, 'the exit threshold must be below the threshold')
        plain = self.ok(self.bob.post('/api/trading/signal-library/definitions', json=body), 201)
        held = self.ok(self.bob.post('/api/trading/signal-library/definitions', json={**body, 'code': 'RSI_HELD', 'exit_threshold': 8}), 201)
        self.assertEqual(float(held['exit_threshold']), 8.0)
        with store.transaction('write') as db:
            bars = [dict(r) for r in db.execute("SELECT * FROM md_bars WHERE account_id=%s AND timeframe='M5' ORDER BY bar_time", (a['id'],)).fetchall()]
            defs = [d for d in signal_library.definitions(db, active_only=True) if d['code'] in ('RSI_WOBBLE', 'RSI_HELD')]
            for k in range(200, 400):
                signal_library.live_update(db, a['id'], 'EURUSD', 'M5', bars[:k], defs)
            counts = {r['code']: r['n'] for r in db.execute(
                'SELECT d.code, count(*) AS n FROM sig_events e JOIN sig_definitions d ON d.id=e.definition_id GROUP BY 1').fetchall()}
            versions = {r['definition_version'] for r in db.execute('SELECT definition_version FROM sig_events').fetchall()}
        self.assertLess(counts.get('RSI_HELD', 0), counts['RSI_WOBBLE'], counts)
        self.assertEqual(versions, {1})
        self.assertTrue(plain['id'])
        # Research of one formula with an exit threshold; every signal ranked with the multiple-testing note.
        res = self.ok(self.alice.post('/api/trading/signal-library/research', json={'account_id': a['id'], 'symbol': 'EURUSD', 'timeframe': 'M5',
                                                                                     'kind': 'RSI_REVERSION', 'days': 30, 'exit_threshold': 8}))
        self.assertEqual(res['exit_threshold'], 8.0)
        self.assertIn('oos_ic', res['horizons'][0])
        ranked = self.ok(self.alice.post('/api/trading/signal-library/research-all', json={'account_id': a['id'], 'symbol': 'EURUSD', 'timeframe': 'M5',
                                                                                           'days': 30, 'horizons': [1, 5]}))
        self.assertEqual(ranked['multiple_testing']['tests'], 2 * len(ranked['items']))
        self.assertIn('by chance', ranked['multiple_testing']['note'])
        self.assertIn('net_edge_bp', ranked['items'][0])

    def test_stale_scores_leave_the_consensus(self):
        _connection, (a, _b, _c), ins = self.setup_accounts()
        self.start(self.plan(a, ins, 'EU_SIG'))
        self.bars(a['id'], [1.10 + 0.01 * math.sin(i / 9) for i in range(300)], NOW - timedelta(minutes=5 * 300))
        with store.transaction('write') as db:
            bars = [dict(r) for r in db.execute("SELECT * FROM md_bars WHERE account_id=%s AND timeframe='M5' ORDER BY bar_time", (a['id'],)).fetchall()]
            signal_library.live_update(db, a['id'], 'EURUSD', 'M5', bars, signal_library.definitions(db, active_only=True))
        fresh = self.ok(self.alice.get('/api/trading/signal-library/overview', params={'timeframe': 'M5'}))
        self.assertGreater(fresh['consensus'][0]['n'], 0)
        self.assertEqual(fresh['stale_scores'], 0)
        with self.connect() as db:
            db.execute("""INSERT INTO intel_snapshot(account_id, broker_symbol, timeframe, bar_time, version, bars, bars_source, features, regime, freshness)
                          VALUES (%s,'EURUSD','M5',%s,4,300,'M1','{}','RANGE','STALE')""", (a['id'], bars[-1]['bar_time']))
        stale = self.ok(self.alice.get('/api/trading/signal-library/overview', params={'timeframe': 'M5'}))
        self.assertEqual(stale['consensus'][0]['n'], 0, 'a stale bar does not vote')
        self.assertEqual(stale['consensus'][0]['stale_scores'], stale['scores'])
        self.assertTrue(all(s['freshness'] == 'STALE' for s in stale['strongest']))
        mx = self.ok(self.alice.get('/api/trading/signal-library/instrument', params={'account_id': a['id'], 'symbol': 'EURUSD'}))
        self.assertEqual(mx['overall']['n'], 0)

    def test_quality_net_of_the_spread_and_manual_signals_measured(self):
        connection, (a, _b, _c), instrument = self.setup_accounts()
        self.start(self.plan(a, instrument, 'EU_M5'))
        _strat, _ver, dep = self.deploy(connection, a, instrument, sizing={'method': 'FIXED_QTY', 'quantity': '0.10'})
        start = NOW - timedelta(minutes=5 * 41)
        self.bars(a['id'], tt.series(40, False), start)
        self.quote(a['id'], 1.08000, 1.08010)
        runtime.cycle(NOW)
        self.bars(a['id'], tt.series(41, True)[40:], start + timedelta(minutes=5 * 40))
        self.assertEqual(runtime.cycle(NOW)['signals'], 1)
        later = [1.1010 + 0.0001 * i for i in range(12)]   # the market keeps rising after the BUY (signal bar closed at 1.1005)
        self.bars(a['id'], later, start + timedelta(minutes=5 * 41))
        with store.transaction('write') as db:
            series = [(r['bar_time'], Decimal(str(r['close']))) for r in db.execute(
                "SELECT bar_time, close FROM md_bars WHERE account_id=%s AND timeframe='M5' ORDER BY bar_time", (a['id'],)).fetchall()]
            # A manual signal stamped inside a bar (not on its start) is measured from the bar it fell in.
            sig = db.execute('SELECT * FROM tr_signals LIMIT 1').fetchone()
            db.execute("UPDATE tr_signals SET bar_time = bar_time + interval '90 seconds' WHERE id=%s", (sig['id'],))
            self.assertEqual(signals.evaluate_quality(db, NOW + timedelta(days=1), {str(dep['id']): series}), 1)
            q = signals.quality(db)[0]
        self.assertGreater(float(q['avg_return_pct']), 0)
        expected_cost = 100 * (1.08010 - 1.08000) / 1.08005
        self.assertAlmostEqual(float(q['avg_cost_pct']), expected_cost, places=6)
        self.assertAlmostEqual(q['net_return_pct'], float(q['avg_return_pct']) - expected_cost, places=6)
        self.assertIsNotNone(q['hit_rate_after_cost_pct'])
        api = self.ok(self.alice.get('/api/trading/signals/quality'))['items'][0]
        self.assertIn('net_return_pct', api)


if __name__ == '__main__':
    unittest.main()
