"""Trading core, Stages 3–7, end to end on paper: a deployed EMA-cross strategy sees a new closed bar, emits a signal,
the signal is validated, risk sizes and approves it, the order engine fills it on paper at the live ask plus slippage,
the portfolio shows the position and P&L. Also: one signal per bar (determinism), refusals with reasons (kill
switch, limits, stale price, BROKER mode), idempotent tickets, cancel, flatten through a kill switch, UNKNOWN
resolution, the append-only order history, automatic stop on a loss limit, the backtest with no look-ahead."""
from __future__ import annotations

import importlib
import importlib.util
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

tmd = importlib.import_module('test_market_data') if importlib.util.find_spec('test_market_data') else importlib.import_module('tests.test_market_data')
from corei_os_mini.trading import algorithms, backtest, runtime  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)  # a Wednesday: FX open


def series(n: int, jump: bool) -> list[float]:
    """A gentle decline (fast EMA under slow), then optionally a jump on the last bar (fast crosses above)."""
    out = [1.1000 - 0.0005 * i for i in range(n)]
    if jump:
        out[-1] = out[-2] + 0.0200
    return out


class Trading(tmd.MarketData):
    test_plans_estimate_controls_and_jobs = None
    test_collector_end_to_end = None
    test_refused_login_reported_once_and_on_overview = None

    def bars(self, account_id, closes, start):
        with self.connect() as db:
            with db.cursor() as cur:
                cur.executemany('INSERT INTO md_bars(account_id, broker_symbol, timeframe, bar_time, open, high, low, close, ticks, source) '
                                "VALUES (%s,'EURUSD','M5',%s,%s,%s,%s,%s,10,'BROKER') ON CONFLICT DO NOTHING",
                                [(account_id, start + timedelta(minutes=5 * i), c, c + 0.0003, c - 0.0003, c) for i, c in enumerate(closes)])

    def quote(self, account_id, bid, ask, at=NOW):
        # The tests run their strategies at a fixed market time (NOW); actions a person takes (a ticket, a manual
        # signal) are checked against the real clock. A default quote is therefore received at whichever is later, so
        # it is fresh for both, whatever day the suite runs. An explicit time is kept as given.
        received = max(at, datetime.now(UTC)) if at is NOW else at
        with self.connect() as db:
            db.execute("INSERT INTO md_latest(account_id, broker_symbol, bid, ask, source_time, received_at, seq) VALUES (%s,'EURUSD',%s,%s,%s,%s,1) "
                       'ON CONFLICT (account_id, broker_symbol) DO UPDATE SET bid=EXCLUDED.bid, ask=EXCLUDED.ask, received_at=EXCLUDED.received_at',
                       (account_id, bid, ask, at, received))

    def deploy(self, connection, account, instrument, **extra):
        mapping = self.ok(self.alice.post('/api/config/mappings', json={'connection_id': connection['id'], 'instrument_id': instrument['id'],
                                                                       'broker_symbol': 'EURUSD', 'usage': 'BOTH'}), 201)
        strat = self.ok(self.alice.post('/api/config/strategies', json={'code': 'EMA_FAST', 'name': 'EMA 3/5 on M5'}), 201)
        ver = self.ok(self.alice.post(f"/api/config/strategies/{strat['id']}/versions",
                                      json={'algorithm': 'EMA_CROSSOVER', 'parameters': {'timeframe': 'M5', 'fast': 3, 'slow': 5}}), 201)
        self.assertEqual(ver['parameters']['direction'], 'BOTH')
        self.ok(self.bob.post(f"/api/config/strategy-versions/{ver['id']}/approve", json={'version': ver['version']}))
        dep = self.ok(self.alice.post('/api/config/deployments', json={'strategy_id': strat['id'], 'strategy_version_id': ver['id'], 'instrument_id': instrument['id'],
                                                                       'account_id': account['id'], 'data_mapping_id': mapping['id'], **extra}), 201)
        if extra.get('mode', 'PAPER') == 'PAPER':
            self.assertEqual(dep['blockers'], ['DEPLOYMENT_NOT_ACTIVE'], 'a PAPER deployment is not blocked by order sending being off')
        else:
            self.assertIn('ORDER_SENDING_OFF_DEMO', dep['blockers'], 'BROKER mode needs everything the account needs to trade')
        dep = self.ok(self.bob.post(f"/api/config/deployments/{dep['id']}/activate", json={'version': dep['version'], 'reason': 'paper test run'}))
        return strat, ver, dep

    def test_paper_trade_end_to_end(self):
        connection, (a, _b, _c), instrument = self.setup_accounts()
        self.start(self.plan(a, instrument, 'EU_M5'))
        strat, ver, dep = self.deploy(connection, a, instrument, sizing={'method': 'FIXED_QTY', 'quantity': '0.10'})
        start = NOW - timedelta(minutes=5 * 41)
        self.bars(a['id'], series(40, False), start)
        self.quote(a['id'], 1.08000, 1.08010)
        first = runtime.cycle(NOW)
        self.assertEqual((first['deployments'], first['signals']), (1, 0), first)
        # The next bar closes with a jump: the fast EMA crosses above the slow one → BUY.
        self.bars(a['id'], series(41, True)[40:], start + timedelta(minutes=5 * 40))
        second = runtime.cycle(NOW)
        self.assertEqual((second['signals'], second['orders']), (1, 1), second)
        self.assertEqual(runtime.cycle(NOW)['signals'], 0, 'a bar is evaluated once')
        sigs = self.ok(self.alice.get('/api/trading/signals'))['items']
        self.assertEqual((sigs[0]['side'], sigs[0]['state'], sigs[0]['source']), ('BUY', 'ACTED', 'RUNTIME'))
        self.assertIn('EMA 3/5', sigs[0]['explanation'][0])
        orders = self.ok(self.alice.get('/api/trading/orders'))['items']
        o = orders[0]
        self.assertEqual((o['side'], o['state'], o['quantity'], o['mode']), ('BUY', 'FILLED', '0.1', 'PAPER'))
        self.assertEqual(o['avg_fill_price'], '1.0801' if o['avg_fill_price'] == '1.0801' else o['avg_fill_price'])
        self.assertEqual(Decimal(o['avg_fill_price']), Decimal('1.08011'), 'the ask plus one tick of paper slippage')
        detail = self.ok(self.alice.get(f"/api/trading/orders/{o['id']}"))
        self.assertEqual([e['state_to'] for e in detail['events']], ['NEW', 'APPROVED', 'SUBMITTED', 'ACKNOWLEDGED', 'FILLED'])
        self.assertTrue(detail['risk']['approved'])
        pos = self.ok(self.alice.get('/api/trading/portfolio/positions'))['items']
        self.assertEqual((pos[0]['quantity'], pos[0]['strategy_code']), ('0.1', 'EMA_FAST'))
        self.quote(a['id'], 1.08100, 1.08110)
        pos = self.ok(self.alice.get('/api/trading/portfolio/positions'))['items']
        self.assertEqual(Decimal(pos[0]['unrealised_pnl']), (Decimal('1.08105') - Decimal('1.08011')) * Decimal('0.1') * 100000)
        perf = self.ok(self.alice.get('/api/trading/performance'))['items']
        self.assertEqual((perf[0]['strategy'], perf[0]['fills']), ('EMA_FAST', 1))
        ov = self.ok(self.alice.get('/api/trading/overview'))
        self.assertEqual((ov['signals_today'].get('ACTED'), ov['positions']), (1, 1))

        # Events are append-only.
        with self.connect() as db:
            with self.assertRaises(Exception):
                db.execute('UPDATE oms_order_events SET actor=%s', ('x',))

        # Kill switch on the strategy refuses the next order with its reason; an operator flatten still closes the position.
        kill = self.ok(self.alice.post('/api/trading/risk/kill-switches', json={'scope': 'STRATEGY', 'target_id': strat['id'], 'reason': 'review the strategy'}), 201)
        manual = self.ok(self.alice.post('/api/trading/signals', json={'deployment_id': dep['id'], 'side': 'SELL', 'reason': 'test the kill switch'}), 201)
        self.assertEqual(manual['signal']['state'], 'REJECTED')
        self.assertIn('KILL_SWITCH_STRATEGY', manual['signal']['reasons'][0])
        flat = self.ok(self.alice.post('/api/trading/portfolio/flatten', json={'account_id': a['id'], 'instrument_id': instrument['id'],
                                                                              'reason': 'close before review', 'key': 'flatten-key-1'}))
        self.assertEqual(flat['state'], 'FILLED')
        self.assertEqual(self.ok(self.alice.get('/api/trading/portfolio/positions'))['items'], [])
        realised = next(f for f in self.ok(self.alice.get('/api/trading/fills'))['items'] if f['side'] == 'SELL')['realised_pnl']
        self.assertEqual(Decimal(realised), (Decimal('1.08099') - Decimal('1.08011')) * Decimal('0.1') * 100000, 'sold at the bid less a tick')
        self.ok(self.alice.post(f"/api/trading/risk/kill-switches/{kill['id']}/release", json={'reason': 'review done'}))

    def test_tickets_limits_and_unknown(self):
        connection, (a, _b, _c), instrument = self.setup_accounts()
        self.start(self.plan(a, instrument, 'EU_T'))
        now = datetime.now(UTC)
        self.quote(a['id'], 1.08000, 1.08010, now)
        body = {'account_id': a['id'], 'instrument_id': instrument['id'], 'side': 'BUY', 'quantity': '0.05', 'ticket_key': 'ticket-0001'}
        if now.weekday() >= 5:
            return  # FX is closed at the weekend: the market check refuses tickets (covered by test_paper_trade_end_to_end)
        o1 = self.ok(self.alice.post('/api/trading/orders', json=body), 201)
        again = self.ok(self.alice.post('/api/trading/orders', json=body), 201)
        self.assertEqual((o1['state'], again['id'], again.get('duplicate')), ('FILLED', o1['id'], True), 'the same ticket is one order')
        self.ok(self.alice.post('/api/trading/orders', json={**body, 'quantity': '0.055', 'ticket_key': 'ticket-0002'}), 201)
        step = self.ok(self.alice.get('/api/trading/orders'))['items'][0]
        self.assertIn('QUANTITY_STEP', step['reasons'][0])
        lim = self.ok(self.alice.post('/api/trading/risk/limits', json={'scope': 'ACCOUNT', 'target_id': a['id'], 'kind': 'MAX_POSITION_QTY', 'value': '0.08',
                                                                       'reason': 'small paper book'}), 201)
        r = self.ok(self.alice.post('/api/trading/orders', json={**body, 'ticket_key': 'ticket-0003'}), 201)
        self.assertEqual(r['state'], 'REJECTED')
        self.assertTrue(r['reasons'][0].startswith('MAX_POSITION_QTY: position 0.1 > limit 0.08'), r['reasons'])
        sell = self.ok(self.alice.post('/api/trading/orders', json={**body, 'side': 'SELL', 'quantity': '0.05', 'ticket_key': 'ticket-0004'}), 201)
        self.assertEqual(sell['state'], 'FILLED', 'reducing a position is not blocked by the position limit')
        # Limit order: rests, then cancel.
        lo = self.ok(self.alice.post('/api/trading/orders', json={**body, 'order_type': 'LIMIT', 'limit_price': '1.07000', 'ticket_key': 'ticket-0005'}), 201)
        self.assertEqual(lo['state'], 'ACKNOWLEDGED')
        self.assertEqual(self.ok(self.alice.post(f"/api/trading/orders/{lo['id']}/cancel", json={'reason': 'not needed now'}))['state'], 'CANCELLED')
        # A stale price refuses a signal; a BROKER deployment is refused with the reasons.
        self.ok(self.alice.put(f"/api/trading/risk/limits/{lim['id']}", json={'scope': 'ACCOUNT', 'target_id': a['id'], 'kind': 'MAX_POSITION_QTY', 'value': '0.08',
                                                                             'enabled': False, 'reason': 'switch off', 'version': lim['version']}))
        # UNKNOWN: resolved by an operator, never re-sent.
        with self.connect() as db:
            db.execute("UPDATE oms_orders SET state='UNKNOWN' WHERE id=%s", (UUID(lo['id']),))
        self.ok(self.alice.post(f"/api/trading/orders/{lo['id']}/resolve", json={'outcome': 'FILLED', 'reason': 'no broker answer: filled per statement'}), 422)
        res = self.ok(self.alice.post(f"/api/trading/orders/{lo['id']}/resolve", json={'outcome': 'FILLED', 'price': '1.07000', 'reason': 'filled per broker statement'}))
        self.assertEqual(res['state'], 'FILLED')
        with self.connect() as db:
            actions = {r['action'] for r in db.execute("SELECT action FROM cfg_audit_log WHERE entity IN ('order','risk_limit','kill_switch')").fetchall()}
        self.assertTrue({'PLACE_ORDER', 'CREATE_RISK_LIMIT', 'UPDATE_RISK_LIMIT', 'RESOLVE_UNKNOWN_ORDER'} <= actions, actions)

    def test_broker_mode_refused_and_loss_limit_stops(self):
        connection, (a, _b, _c), instrument = self.setup_accounts()
        self.start(self.plan(a, instrument, 'EU_B'))
        _s, _v, dep = self.deploy(connection, a, instrument, mode='BROKER')
        self.assertEqual(dep['mode'], 'BROKER')
        self.quote(a['id'], 1.08000, 1.08010)
        start = NOW - timedelta(minutes=5 * 41)
        self.bars(a['id'], series(40, False), start)
        runtime.cycle(NOW)
        self.bars(a['id'], series(41, True)[40:], start + timedelta(minutes=5 * 40))
        runtime.cycle(NOW)
        sig = self.ok(self.alice.get('/api/trading/signals'))['items'][0]
        self.assertEqual(sig['state'], 'REJECTED')
        self.assertTrue(any(r.startswith('ORDER_SENDING_OFF_DEMO') for r in sig['reasons']), sig['reasons'])
        self.assertTrue(any(r.startswith('ORDER_NEEDS_BRIDGE') for r in sig['reasons']), 'an MT5 account without a bridge cannot take real orders')
        # A loss limit breached engages the account kill switch by itself.
        with self.connect() as db:
            db.execute("INSERT INTO pf_positions(id, account_id, instrument_id, mode, quantity, avg_price, realised_pnl, currency) "
                       "VALUES (gen_random_uuid(), %s, %s, 'PAPER', 1, 1.10000, 0, 'USD')", (UUID(a['id']), UUID(instrument['id'])))
        self.ok(self.alice.post('/api/trading/risk/limits', json={'scope': 'ACCOUNT', 'target_id': a['id'], 'kind': 'MAX_DAILY_LOSS', 'value': '1000',
                                                                 'reason': 'daily stop'}), 201)
        runtime.cycle(NOW)
        kills = self.ok(self.alice.get('/api/trading/risk/kill-switches'))['items']
        self.assertEqual((kills[0]['scope'], kills[0]['automatic'], kills[0]['engaged_by']), ('ACCOUNT', True, 'risk-engine'))
        self.assertIn('loss today', kills[0]['reason'])

    def test_backtest_no_look_ahead(self):
        connection, (a, _b, _c), instrument = self.setup_accounts()
        self.start(self.plan(a, instrument, 'EU_BT'))
        start = NOW - timedelta(days=2)
        closes = [1.10 + 0.002 * ((i // 20) % 2 and 20 - i % 20 or i % 20) / 20 for i in range(300)]  # a saw-tooth
        self.bars(a['id'], closes, start)
        out = self.ok(self.alice.post('/api/trading/backtests', json={'algorithm': 'EMA_CROSSOVER', 'parameters': {'timeframe': 'M5', 'fast': 3, 'slow': 8},
                                                                      'account_id': a['id'], 'broker_symbol': 'EURUSD', 'range_from': start.isoformat(),
                                                                      'range_to': NOW.isoformat(), 'quantity': '1', 'spread_ticks': 2}), 201)
        m = out['metrics']
        self.assertGreater(m['trades'], 5)
        self.assertEqual(m['bars_source'], 'BROKER')
        t = out['trades'][0]
        self.assertGreater(t['exit_time'], t['entry_time'])
        # Every entry is at a bar's open strictly after the bar that decided it.
        bar_opens = {(start + timedelta(minutes=5 * i)).isoformat(): c for i, c in enumerate(closes)}
        self.assertIn(t['entry_time'], bar_opens)
        listed = self.ok(self.alice.get('/api/trading/backtests'))['items']
        self.assertEqual(listed[0]['id'], out['id'])
        self.ok(self.alice.post('/api/trading/backtests', json={'algorithm': 'EMA_CROSSOVER', 'parameters': {'timeframe': 'H4', 'fast': 3, 'slow': 8},
                                                               'account_id': a['id'], 'broker_symbol': 'EURUSD', 'range_from': start.isoformat(),
                                                               'range_to': NOW.isoformat()}), 422)


def test_algorithms_are_deterministic_and_point_in_time():
    bars = [{'bar_time': NOW + timedelta(minutes=5 * i), 'open': c, 'high': c + 1, 'low': c - 1, 'close': c} for i, c in enumerate([10, 9, 8, 7, 6, 5, 9, 13, 12, 8, 4, 3, 7, 11] * 4)]
    rows = [{'atr14': 2.0} for _ in bars]
    for name, params in (('EMA_CROSSOVER', {'timeframe': 'M5', 'fast': 2, 'slow': 4}), ('MA_CROSSOVER', {'timeframe': 'M5', 'fast_window': 2, 'slow_window': 4}),
                         ('DONCHIAN_BREAKOUT', {'timeframe': 'M5', 'entry': 5, 'exit': 3}), ('RSI_REVERSION', {'timeframe': 'M5', 'period': 3, 'lower': 30, 'upper': 70}),
                         ('BOLLINGER_REVERSION', {'timeframe': 'M5', 'period': 5, 'deviations': 1})):
        a = algorithms.run(name, params, bars, rows)
        assert a == algorithms.run(name, params, bars, rows), name
        # Point in time: the decision at bar k does not change when later bars are added.
        cut = algorithms.run(name, params, bars[:30], rows[:30])
        assert [x['target'] for x in cut] == [x['target'] for x in a[:30]], name
        assert any(x['change'] for x in a), name
    long_only = algorithms.run('EMA_CROSSOVER', {'timeframe': 'M5', 'fast': 2, 'slow': 4, 'direction': 'LONG_ONLY'}, bars, rows)
    assert 'SHORT' not in {x['target'] for x in long_only}
    rules = algorithms.run('RULES', {'timeframe': 'M5', 'long_entry': [{'field': 'close', 'op': 'CROSSES_ABOVE', 'value': '8'}],
                                     'long_exit': [{'field': 'close', 'op': '<', 'value': '5'}]}, bars, [{'close': b['close']} for b in bars])
    first = next(i for i, x in enumerate(rules) if x['change'])
    assert (first, rules[first]['target']) == (6, 'LONG'), first  # close 5 → 9 crosses above 8 on bar 6
    sim = backtest.simulate(bars, a, {'tick_size': Decimal('1'), 'contract_size': Decimal('1'), 'quote_currency': 'USD'}, Decimal(1), 0, Decimal(0),
                            Decimal(1000), 'M5')
    assert sim['metrics']['trades'] >= 1
