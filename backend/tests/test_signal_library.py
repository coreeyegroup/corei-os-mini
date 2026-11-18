"""Stage 3 signal library and the formulas registry: every formula scores within ±100 without looking ahead, shows its
working, research measures prediction (IC, hit rate, quintiles), the SIGNAL_BLEND strategy combines library scores,
every algorithm and signal has a formula entry, and the API serves the library, the live board, one instrument's
worked equations and research — with definitions changed by the Risk approver role, validated, versioned, and built-ins never deleted."""
from __future__ import annotations

import importlib
import importlib.util
import math
from datetime import timedelta

tt = importlib.import_module('test_trading') if importlib.util.find_spec('test_trading') else importlib.import_module('tests.test_trading')
from corei_os_mini import config_store as store, formulas  # noqa: E402
from corei_os_mini.trading import algorithms, signal_library  # noqa: E402

NOW = tt.NOW


def wave(n: int = 400) -> list[dict]:
    out = []
    for i in range(n):
        c = 1.10 + 0.01 * math.sin(i / 9) + 0.00002 * i
        out.append({'bar_time': NOW + timedelta(minutes=5 * i), 'open': c - 0.0001, 'high': c + 0.0006, 'low': c - 0.0006, 'close': c,
                    'ticks': 10 + i % 7, 'volume': 0, 'spread_avg': 0.00008})
    return out


def test_every_formula_scores_in_range_point_in_time_and_shows_working():
    bars = wave()
    for kind in signal_library.KINDS:
        full = signal_library.compute(kind, {}, bars)
        assert len(full) == len(bars), kind
        scored = [x for x in full if x is not None]
        assert len(scored) > 200, kind
        assert all(-100 <= s <= 100 for s, _ in scored), kind
        cut = signal_library.compute(kind, {}, bars[:250])
        assert [None if x is None else round(x[0], 9) for x in cut] == [None if x is None else round(x[0], 9) for x in full[:250]], f'{kind} looks ahead'
        s, inputs = full[-1]
        text = signal_library.worked(kind, signal_library.params_of(kind, {}), s, inputs)
        assert f'{s:+.1f}' in text, (kind, text)
        assert signal_library.direction(s, 20) in ('LONG', 'SHORT', 'FLAT')
    assert signal_library.worked('EMA_TREND', {}, None, None).startswith('warming up')


def test_research_measures_prediction():
    out = signal_library.research('MOMENTUM_TSTAT', {}, wave(), [1, 5, 20], 20)
    assert [h['horizon'] for h in out['horizons']] == [1, 5, 20]
    h5 = out['horizons'][1]
    assert -1 <= h5['ic'] <= 1 and len(h5['quintiles']) == 5
    assert out['verdict'] in ('PREDICTIVE', 'WEAK', 'NO_EDGE')
    assert out['avg_spread_bp'] > 0 and len(out['series']) <= 500


def test_signal_blend_strategy_and_formula_registry():
    bars = wave()
    params = {'timeframe': 'M5', 'components': [{'kind': 'EMA_TREND', 'weight': 1}, {'kind': 'MOMENTUM_TSTAT', 'weight': 1}], 'entry': 30, 'exit': 5}
    a = algorithms.run('SIGNAL_BLEND', params, bars, [{} for _ in bars])
    changes = [x for x in a if x['change']]
    assert changes and '÷ 2 =' in changes[0]['reason']
    assert [x['target'] for x in algorithms.run('SIGNAL_BLEND', params, bars[:250], [{} for _ in bars[:250]])] == [x['target'] for x in a[:250]]
    stages = formulas.all_formulas()
    assert list(stages) == ['Market data', 'Intelligence', 'Signals', 'Strategies', 'Risk', 'Portfolio', 'Execution', 'Post-trade']
    codes = ' '.join(f['code'] for v in stages.values() for f in v)
    for k in list(algorithms.ALGORITHMS) + list(signal_library.KINDS):
        assert k in codes, k
    for k, v in algorithms.ALGORITHMS.items():
        assert v.get('equation') and v.get('strength_equation'), k
    assert all(f['name'] and f['equation'] for v in stages.values() for f in v)


class SignalLibraryApi(tt.Trading):
    test_paper_trade_end_to_end = None
    test_tickets_limits_and_unknown = None
    test_broker_mode_refused_and_loss_limit_stops = None
    test_backtest_no_look_ahead = None

    def test_library_board_explain_research(self):
        _connection, (a, _b, _c), ins = self.setup_accounts()
        self.start(self.plan(a, ins, 'EU_SIG'))
        closes = [1.10 + 0.01 * math.sin(i / 9) for i in range(400)]
        self.bars(a['id'], closes, NOW - timedelta(minutes=5 * 400))
        cat = self.ok(self.alice.get('/api/trading/signal-library/catalogue'))['items']
        assert len(cat) == len(signal_library.KINDS) and all(c['equation'] for c in cat)
        defs = self.ok(self.alice.get('/api/trading/signal-library/definitions'))['items']
        assert sum(1 for d in defs if d['builtin']) == 12
        body = {'code': 'EMA_FAST_TREND', 'name': 'Fast EMA trend', 'kind': 'EMA_TREND', 'parameters': {'fast': 5, 'slow': 13}, 'threshold': 25,
                'reason': 'desk preset for scalping'}
        mine = self.ok(self.bob.post('/api/trading/signal-library/definitions', json=body), 201)
        self.assertEqual(mine['parameters']['fast'], 5)
        bad = self.bob.post('/api/trading/signal-library/definitions', json={**body, 'code': 'BAD_ONE', 'parameters': {'fast': 30, 'slow': 10}})
        self.assertEqual(bad.status_code, 422)
        changed = self.ok(self.bob.put(f"/api/trading/signal-library/definitions/{mine['id']}", json={**body, 'threshold': 30}))
        self.assertEqual((changed['version'], float(changed['threshold'])), (2, 30.0))
        builtin = next(d for d in defs if d['builtin'])
        self.assertEqual(self.bob.delete(f"/api/trading/signal-library/definitions/{builtin['id']}", params={'reason': 'not needed here'}).status_code, 409)
        # Live board: what the intelligence engine writes after each run.
        with store.transaction('write') as db:
            bars = [dict(r) for r in db.execute("SELECT * FROM md_bars WHERE account_id=%s AND timeframe='M5' ORDER BY bar_time", (a['id'],)).fetchall()]
            n = signal_library.live_update(db, a['id'], 'EURUSD', 'M5', bars, signal_library.definitions(db, active_only=True))
        self.assertEqual(n, 13)
        board = self.ok(self.alice.get('/api/trading/signal-library/board', params={'timeframe': 'M5'}))['items']
        self.assertEqual(len(board), 13)
        self.assertTrue(all('=' in r['worked'] for r in board if r['score'] is not None))
        ex = self.ok(self.alice.get('/api/trading/signal-library/explain', params={'account_id': a['id'], 'symbol': 'EURUSD', 'timeframe': 'M5'}))
        self.assertEqual(ex['bars'], 400)
        row = next(r for r in ex['items'] if r['code'] == 'EMA_FAST_TREND')
        self.assertIn('100 × (', row['worked'])
        self.assertEqual(len(row['recent']), 60)
        res = self.ok(self.alice.post('/api/trading/signal-library/research', json={'account_id': a['id'], 'symbol': 'EURUSD', 'timeframe': 'M5',
                                                                                     'kind': 'RSI_REVERSION', 'days': 30}))
        self.assertEqual([h['horizon'] for h in res['horizons']], [1, 2, 5, 10, 20])
        self.assertIn(res['verdict'], ('PREDICTIVE', 'WEAK', 'NO_EDGE'))
        few = self.alice.post('/api/trading/signal-library/research', json={'account_id': a['id'], 'symbol': 'EURUSD', 'timeframe': 'D1', 'kind': 'RSI_REVERSION'})
        self.assertEqual(few.status_code, 409)
        bp = self.ok(self.alice.post('/api/trading/signal-library/blend-preview', json={
            'account_id': a['id'], 'symbol': 'EURUSD', 'timeframe': 'M5', 'components': [{'kind': 'EMA_TREND', 'weight': 2}, {'kind': 'RSI_REVERSION', 'weight': 1}]}))
        expect = round((2 * bp['components'][0]['score'] + bp['components'][1]['score']) / 3, 1)
        self.assertAlmostEqual(bp['combined'], expect, delta=0.11)
        self.assertIn('÷ 3 =', bp['worked'])
        self.assertEqual(self.alice.post('/api/trading/signal-library/blend-preview', json={
            'account_id': a['id'], 'symbol': 'EURUSD', 'components': [{'kind': 'NOPE'}]}).status_code, 422)
        # Stage 3 dashboard, one instrument across timeframes, flips, correlation, every signal researched.
        ov = self.ok(self.alice.get('/api/trading/signal-library/overview', params={'timeframe': 'M5'}))
        self.assertEqual((ov['instruments'], ov['definitions_active']), (1, 13))
        self.assertEqual(ov['long'] + ov['short'] + ov['flat'], ov['scores'])
        self.assertTrue(ov['strongest'] and ov['consensus'][0]['n'] >= 12)
        mx = self.ok(self.alice.get('/api/trading/signal-library/instrument', params={'account_id': a['id'], 'symbol': 'EURUSD'}))
        self.assertEqual(mx['timeframes'], ['M5'])
        self.assertIn('EMA_TREND', mx['cells'])
        self.assertIn('=', mx['cells']['EMA_TREND']['M5']['worked'])
        with store.transaction('write') as db:
            defs_now = signal_library.definitions(db, active_only=True)
            for k in range(300, 400):
                signal_library.live_update(db, a['id'], 'EURUSD', 'M5', bars[:k], defs_now)
            signal_library.live_update(db, a['id'], 'EURUSD', 'M5', bars[:399], defs_now)  # a re-run never doubles a flip
        flips = self.ok(self.alice.get('/api/trading/signal-library/events', params={'timeframe': 'M5'}))['items']
        self.assertTrue(flips, 'a sine wave flips its signals')
        self.assertTrue(all(e['from_direction'] != e['to_direction'] for e in flips))
        self.assertEqual(len({(e['code'], e['bar_time']) for e in flips}), len(flips))
        cor = self.ok(self.alice.get('/api/trading/signal-library/correlation', params={'account_id': a['id'], 'symbol': 'EURUSD', 'timeframe': 'M5'}))
        i = cor['codes'].index('EMA_TREND')
        self.assertEqual(cor['matrix'][i][i], 1.0)
        self.assertTrue(any(r['a'] == 'EMA_FAST_TREND' or r['b'] == 'EMA_FAST_TREND' for r in cor['redundant']), 'two EMA trends say the same thing')
        allr = self.ok(self.alice.post('/api/trading/signal-library/research-all', json={'account_id': a['id'], 'symbol': 'EURUSD', 'timeframe': 'M5', 'days': 30}))
        self.assertEqual(len(allr['items']), 13)
        ts = [abs(x['ic_t'] or 0) for x in allr['items']]
        self.assertEqual(ts, sorted(ts, reverse=True))
        self.assertIn('closes', ex)
        self.assertEqual(len(ex['closes']), 200)
        f = self.ok(self.alice.get('/api/trading/formulas'))['stages']
        self.assertIn('Risk', f)
        self.ok(self.bob.delete(f"/api/trading/signal-library/definitions/{mine['id']}", params={'reason': 'test clean-up'}), 204)
        qs = self.ok(self.alice.post('/api/trading/quickstart', json={'account_id': a['id'], 'broker_symbol': 'EURUSD', 'preset': 'BLEND', 'timeframe': 'M5'}), 201)
        self.assertEqual(qs['version']['algorithm'], 'SIGNAL_BLEND')
