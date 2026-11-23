"""Real execution (Stage 7) and post-trade (Stage 8) against a scripted broker, plus the advanced risk, strategy and
portfolio controls. The broker adapter's transport is replaced by a fake MT5 account that executes, refuses, goes
silent after executing (the UNKNOWN case), keeps positions and deals and answers look-ups by tag — so every path of
the dispatcher is exercised: SUBMITTED before sending, fills once per deal, REJECTED with the broker's reason, NOT_SENT,
UNKNOWN resolved by a look-up and never by sending again, a crash between send and answer, cancel, four-eyes on LIVE,
a kill switch engaged after approval. Then reconciliation breaks and the actions on them, TCA, journal, statements and
CSV; margin, leverage, notional, price-deviation and VaR limits, stress scenarios; partitions and allocation, copy
trading, smart routing, attribution; optimisation with walk-forward, and drift."""
from __future__ import annotations

import importlib
import importlib.util
import json
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

tt = importlib.import_module('test_trading') if importlib.util.find_spec('test_trading') else importlib.import_module('tests.test_trading')
from corei_os_mini import config_store as store  # noqa: E402
from corei_os_mini.trading import brokers, execution, oms, posttrade  # noqa: E402

UTC = timezone.utc
NOW = tt.NOW
MAGIC = 7770001


class FakeBroker:
    """A scripted MT5 account behind the bridge: what mt5_call would return, with the bridge's shapes."""

    def __init__(self):
        self.behaviour = 'ok'
        self.sends = 0
        self.deals: list[dict] = []
        self.open: list[dict] = []
        self.history: list[dict] = []
        self.external: list[dict] = []
        self.ticket = 5000
        self.price = '1.08012'

    def _deal(self, o: dict, volume: str, price: str) -> dict:
        self.ticket += 1
        d = {'ticket': self.ticket, 'order': self.ticket + 100000, 'symbol': o['symbol'], 'side': o['side'], 'entry': 'IN', 'volume': volume,
             'price': price, 'commission': '-0.7', 'swap': '0', 'fee': '0', 'profit': '0', 'time': '2026-11-18T10:00:01.000', 'comment': o['tag'],
             'magic': MAGIC, 'position_id': 777, 'reason': 'EXPERT'}
        self.deals.append(d)
        return d

    def positions(self) -> list[dict]:
        net: dict = {}
        for d in self.deals:
            if d['magic'] == MAGIC:
                net[d['symbol']] = net.get(d['symbol'], Decimal(0)) + (Decimal(d['volume']) if d['side'] == 'BUY' else -Decimal(d['volume']))
        out = [{'ticket': i, 'symbol': s, 'side': 'BUY' if q > 0 else 'SELL', 'volume': str(abs(q)), 'price_open': '1.08', 'magic': MAGIC}
               for i, (s, q) in enumerate(net.items()) if q]
        return out + self.external

    def call(self, account_id, actor, mode, payload, purpose, timeout=30):
        if mode == 'book':
            tag = payload.get('tag')
            pick = (lambda rows: [r for r in rows if str(r.get('comment') or '').startswith(tag)]) if tag else (lambda rows: [])
            return {'t': 'book', 'money': {'balance': '10000', 'equity': '10050', 'margin': '100', 'margin_free': '9950'}, 'positions': self.positions(),
                    'orders': self.open, 'deals': self.deals,
                    'tagged': {'orders': pick(self.open), 'positions': [], 'history_orders': pick(self.history), 'deals': pick(self.deals)}}
        action, o = payload['action'], payload.get('order') or {}
        if action == 'check':
            return {'t': 'trade', 'action': 'check', 'results': [{'retcode': 0, 'ok': True, 'margin': '108'}]}
        if action == 'cancel':
            gone = [x for x in self.open if x['comment'].startswith(o['tag'])]
            self.open = [x for x in self.open if x not in gone]
            self.history += [{**x, 'state': 'CANCELED'} for x in gone]
            return {'t': 'trade', 'action': 'cancel', 'results': [{'retcode': 10009, 'ok': True} for _ in gone]}
        # send
        if self.behaviour == 'notsent':
            raise brokers.NotSent('BRIDGE_UNREACHABLE: cannot reach the bridge')
        self.sends += 1
        if self.behaviour == 'reject':
            return {'t': 'trade', 'action': 'send', 'results': [{'retcode': 10019, 'ok': False, 'text': 'not enough money'}]}
        if o['order_type'] != 'MARKET':
            self.ticket += 1
            self.open.append({'ticket': self.ticket, 'symbol': o['symbol'], 'type': 'BUY_LIMIT', 'state': 'PLACED', 'comment': o['tag']})
            return {'t': 'trade', 'action': 'send', 'results': [{'retcode': 10008, 'ok': True, 'order': self.ticket, 'deal': 0, 'volume': '0', 'price': '0'}]}
        d = self._deal(o, o['volume'], self.price)
        if self.behaviour == 'lost':
            raise brokers.Lost('BRIDGE_NO_ANSWER: sent, then timeout')
        return {'t': 'trade', 'action': 'send', 'results': [{'retcode': 10009, 'ok': True, 'deal': d['ticket'], 'order': d['order'], 'volume': d['volume'],
                                                             'price': d['price']}], 'via': {'host': 'vps', 'port': 9443, 'route': 'vps-nys'}}


class Execution(tt.Trading):
    test_paper_trade_end_to_end = None
    test_tickets_limits_and_unknown = None
    test_broker_mode_refused_and_loss_limit_stops = None
    test_backtest_no_look_ahead = None

    def setUp(self):
        super().setUp()
        self.broker = FakeBroker()
        self.patches.append(patch('corei_os_mini.trading.brokers.mt5_call', self.broker.call))
        self.patches[-1].start()

    def real_account(self, environment='DEMO'):
        connection, (a, b, c), instrument = self.setup_accounts(environment)
        self.ok(self.alice.post('/api/config/mappings', json={'connection_id': connection['id'], 'instrument_id': instrument['id'],
                                                              'broker_symbol': 'EURUSD', 'usage': 'BOTH'}), 201)
        with self.connect() as db:
            db.execute("UPDATE cfg_connections SET adapter='TERMINAL_BRIDGE', settings = settings || %s::jsonb WHERE id=%s",
                       (json.dumps({'bridge': {'host': '203.0.113.5', 'port': 9443, 'fingerprint': 'AA' * 32}}), UUID(connection['id'])))
            db.execute("UPDATE cfg_accounts SET trading_permission='TRADING'")
        self.quote(a['id'], 1.08000, 1.08010, NOW)
        return connection, a, b, instrument

    def live_ready(self, account):
        """Meet the pre-LIVE policy for one LIVE account: firm limits, four-eyes on every LIVE order, an alert channel
        (alice and bob are both risk approvers)."""
        for kind, value in (('MAX_ORDER_NOTIONAL', '10000000'), ('MAX_DAILY_LOSS', '100000'), ('MAX_POSITION_QTY', '100')):
            self.ok(self.alice.post('/api/trading/risk/limits', json={'scope': 'FIRM', 'kind': kind, 'value': value, 'reason': 'pre-LIVE policy'}), 201)
        with self.connect() as db:
            db.execute("UPDATE tr_settings SET live_four_eyes='ALL'")
            db.execute("INSERT INTO cfg_alert_channels(id, kind, name, target) VALUES (gen_random_uuid(), 'WEBHOOK', 'desk', 'https://example.invalid/hook')")

    def sending(self, env='DEMO', on=True):
        with self.connect() as db:
            db.execute('UPDATE cfg_execution_controls SET trading_enabled=%s WHERE environment=%s', (on, env))

    def place(self, a, instrument, seed, side='BUY', qty='0.10', **kw):
        with store.transaction('write') as db:
            return oms.place(db, account_id=a['id'], instrument_id=instrument['id'], side=side, quantity=Decimal(qty), mode=kw.pop('mode', 'BROKER'),
                             seed=seed, actor=kw.pop('actor', 'alice'), at=kw.pop('at', NOW), **kw)

    def order(self, oid):
        return self.ok(self.alice.get(f'/api/trading/orders/{oid}'))

    def test_real_orders_on_demo_every_path(self):
        connection, a, _b, ins = self.real_account()
        # Order sending off: refused by risk, nothing sent.
        off = self.place(a, ins, 'off-1')
        self.assertEqual(off['state'], 'REJECTED')
        self.assertTrue(any(r.startswith('ORDER_SENDING_OFF_DEMO') for r in off['reasons']), off['reasons'])
        self.sending()
        # Market order: APPROVED in the order's transaction, then SUBMITTED (committed) → the broker's deal → FILLED.
        o = self.place(a, ins, 'buy-1')
        self.assertEqual((o['state'], o['broker_tag']), ('APPROVED', o['client_order_id'][:20]))
        execution.dispatch()
        d = self.order(o['id'])
        self.assertEqual([e['state_to'] for e in d['events']], ['NEW', 'APPROVED', 'SUBMITTED', 'FILLED'])
        self.assertEqual((d['state'], d['adapter'], d['fills'][0]['broker_deal_id']), ('FILLED', 'MT5_BRIDGE', '5001'))
        self.assertEqual(Decimal(d['fills'][0]['slippage']), Decimal('0.00002'), 'filled 2 ticks worse than the arrival ask')
        self.assertIsNotNone(d['fills'][0]['latency_ms'])
        pos = self.ok(self.alice.get('/api/trading/portfolio/positions'))['items']
        self.assertEqual((pos[0]['mode'], pos[0]['quantity']), ('BROKER', '0.1'))
        # The same intent is the same order: nothing new.
        self.assertTrue(self.place(a, ins, 'buy-1').get('duplicate'))
        execution.dispatch()
        self.assertEqual(self.broker.sends, 1)

        # Not sent (bridge unreachable) → REJECTED, clearly not at the broker.
        self.broker.behaviour = 'notsent'
        ns = self.place(a, ins, 'buy-2')
        execution.dispatch()
        d = self.order(ns['id'])
        self.assertEqual(d['state'], 'REJECTED')
        self.assertTrue(d['events'][-1]['detail']['not_sent'])
        # The broker refuses → REJECTED with its code and text.
        self.broker.behaviour = 'reject'
        rj = self.place(a, ins, 'buy-3')
        execution.dispatch()
        self.assertIn('BROKER_10019: not enough money', self.order(rj['id'])['reasons'][0])

        # Sent, executed at the broker, answer lost → UNKNOWN; the look-up by tag finds the deal → FILLED; never sent twice.
        self.broker.behaviour = 'lost'
        lost = self.place(a, ins, 'buy-4')
        execution.dispatch()
        self.assertEqual(self.order(lost['id'])['state'], 'UNKNOWN')
        sends = self.broker.sends
        execution.dispatch()
        self.assertEqual(self.broker.sends, sends, 'an UNKNOWN order is never sent again')
        self.broker.behaviour = 'ok'
        found = self.ok(self.alice.post(f"/api/trading/orders/{lost['id']}/check-broker"))
        self.assertEqual(found['state'], 'FILLED')
        self.assertEqual(self.order(lost['id'])['events'][-1]['detail']['found_by_tag'], True)

        # A crash between send and answer: SUBMITTED left behind → the sync makes it UNKNOWN and looks it up.
        crash = self.place(a, ins, 'buy-5')
        with self.connect() as db:
            db.execute("UPDATE oms_orders SET state='SUBMITTED', submitted_at=now() - interval '10 minutes', adapter='MT5_BRIDGE' WHERE id=%s", (UUID(crash['id']),))
        self.broker._deal({'symbol': 'EURUSD', 'side': 'BUY', 'tag': crash['broker_tag']}, '0.1', '1.08011')
        execution.sync()
        d = self.order(crash['id'])
        self.assertEqual(d['state'], 'FILLED')
        self.assertIn('UNKNOWN', [e['state_to'] for e in d['events']])

        # Limit order rests at the broker; cancel → CANCEL_PENDING committed, broker cancels, look-up → CANCELLED.
        lim = self.place(a, ins, 'lim-1', order_type='LIMIT', limit_price=Decimal('1.07000'))
        execution.dispatch()
        self.assertEqual(self.order(lim['id'])['state'], 'ACKNOWLEDGED')
        self.assertEqual(self.ok(self.alice.post(f"/api/trading/orders/{lim['id']}/cancel", json={'reason': 'not needed now'}))['state'], 'CANCELLED')

        # A kill switch engaged after approval stops the order at the dispatcher.
        late = self.place(a, ins, 'buy-6')
        kill = self.ok(self.alice.post('/api/trading/risk/kill-switches', json={'scope': 'ACCOUNT', 'target_id': a['id'], 'reason': 'stop now please'}), 201)
        execution.dispatch()
        d = self.order(late['id'])
        self.assertEqual(d['state'], 'REJECTED')
        self.assertIn('KILL_SWITCH_ACCOUNT', d['reasons'][0])
        status = self.ok(self.alice.get('/api/trading/execution/status'))
        acct = next(x for x in status['accounts'] if x['account_id'] == a['id'])
        self.assertEqual(acct['adapter'], 'MT5_BRIDGE')

        self.ok(self.alice.post(f"/api/trading/risk/kill-switches/{kill['id']}/release", json={'reason': 'checked, go on'}))

        # ---------- post-trade ----------
        run = self.ok(self.alice.post(f"/api/trading/posttrade/reconcile/{a['id']}"))
        self.assertEqual(run['status'], 'MATCHED', run)
        # A position opened by hand at the broker and a deal of a COREI order not yet applied.
        self.broker.external.append({'ticket': 99, 'symbol': 'GBPUSD', 'side': 'SELL', 'volume': '0.3', 'price_open': '1.27', 'magic': 0})
        work = self.place(a, ins, 'lim-2', order_type='LIMIT', limit_price=Decimal('1.07500'))
        execution.dispatch()
        self.broker._deal({'symbol': 'EURUSD', 'side': 'BUY', 'tag': work['broker_tag']}, '0.1', '1.07500')
        run = self.ok(self.alice.post(f"/api/trading/posttrade/reconcile/{a['id']}"))
        self.assertEqual(run['status'], 'BREAKS')
        kinds = {b['kind']: b for b in self.ok(self.alice.get('/api/trading/posttrade/breaks'))['items']}
        self.assertEqual(set(kinds), {'EXTERNAL_TRADE', 'FILL_MISSING', 'POSITION_MISMATCH'})
        self.assertEqual((kinds['POSITION_MISMATCH']['corei_value'], kinds['POSITION_MISMATCH']['broker_value']), ('0.3', '0.4'))
        applied = self.ok(self.alice.post(f"/api/trading/posttrade/breaks/{kinds['FILL_MISSING']['id']}", json={'action': 'APPLY_FILL', 'note': 'deal seen at broker'}))
        self.assertEqual(applied['result']['state'], 'FILLED')
        self.ok(self.alice.post(f"/api/trading/posttrade/breaks/{kinds['EXTERNAL_TRADE']['id']}", json={'action': 'ACCEPT', 'note': 'desk hedge by hand'}))
        self.ok(self.alice.post(f"/api/trading/posttrade/breaks/{kinds['EXTERNAL_TRADE']['id']}", json={'action': 'ACCEPT', 'note': 'again'}), 409)
        run = self.ok(self.alice.post(f"/api/trading/posttrade/reconcile/{a['id']}"))
        open_kinds = {b['kind'] for b in self.ok(self.alice.get('/api/trading/posttrade/breaks'))['items']}
        self.assertEqual(open_kinds, set(), 'the fill applied: position and deal match at the next run; the accepted break stays accepted')
        # Broker book, TCA, journal, statement, audit trail, CSV.
        book = self.ok(self.alice.get(f"/api/trading/posttrade/broker/{a['id']}"))
        self.assertEqual(book['snapshot']['money']['equity'], '10050')
        self.assertTrue(any(x['client_order_id'] for x in book['deals']))
        tca = self.ok(self.alice.get('/api/trading/posttrade/tca?by=account&mode=BROKER'))
        row = tca['rows'][0]
        self.assertGreater(row['fills'], 0)
        self.assertIsNotNone(row['avg_slippage_bps'])
        self.assertLess(Decimal(row['commission']), 0, 'commission from the broker deals')
        self.assertIsNotNone(row['fill_rate_pct'])
        trips = self.ok(self.alice.get(f"/api/trading/posttrade/journal?account_id={a['id']}"))
        self.assertTrue(trips['trips'] and trips['trips'][0]['open'])
        day = datetime.now(UTC).date().isoformat()
        st = self.ok(self.alice.get(f"/api/trading/posttrade/statement/{a['id']}?day_from={day}&day_to={day}"))
        self.assertGreater(Decimal(st['totals']['fills']), 0)
        ev = self.ok(self.alice.get(f"/api/trading/posttrade/events?search={o['broker_tag']}"))['items']
        self.assertEqual({e['state_to'] for e in ev}, {'NEW', 'APPROVED', 'SUBMITTED', 'FILLED'})
        csv = self.alice.get('/api/trading/posttrade/export/fills.csv')
        self.assertEqual(csv.status_code, 200)
        self.assertTrue(csv.text.startswith('at,account,symbol,side,quantity,price'))
        self.assertEqual(self.alice.get('/api/trading/posttrade/export/nope.csv').status_code, 404)
        ov = self.ok(self.alice.get('/api/trading/overview'))
        self.assertIn('posttrade', ov)

    def test_four_eyes_on_live(self):
        _connection, a, _b, ins = self.real_account('LIVE')
        self.live_ready(a)
        self.sending('LIVE')
        o = self.place(a, ins, 'live-1', source='MANUAL', actor='alice')
        self.assertTrue(o['hold_for_approval'])
        execution.dispatch()
        self.assertEqual(self.broker.sends, 0, 'held: not sent')
        self.ok(self.alice.post(f"/api/trading/orders/{o['id']}/release", json={'reason': 'my own order'}), 403)
        with patch.dict('os.environ', {'COREI_DISPATCH': 'sync'}):
            self.ok(self.bob.post(f"/api/trading/orders/{o['id']}/release", json={'reason': 'checked size and price'}))
        self.assertEqual(self.order(o['id'])['state'], 'FILLED')
        # A flatten never waits.
        f = self.place(a, ins, 'live-flat', side='SELL', source='FLATTEN', flatten=True)
        self.assertFalse(f['hold_for_approval'])

    def test_risk_extras(self):
        _connection, a, _b, ins = self.real_account()
        lim = lambda kind, v: self.ok(self.alice.post('/api/trading/risk/limits', json={'scope': 'ACCOUNT', 'target_id': a['id'], 'kind': kind, 'value': v,  # noqa: E731
                                                                                       'reason': 'test the limit'}), 201)
        lim('MAX_ORDER_NOTIONAL', '5000')
        r = self.place(a, ins, 'n-1', mode='PAPER')
        self.assertTrue(r['reasons'][0].startswith('MAX_ORDER_NOTIONAL'), r['reasons'])
        lim('MAX_PRICE_DEVIATION_PCT', '1')
        r = self.place(a, ins, 'n-2', mode='PAPER', qty='0.01', order_type='LIMIT', limit_price=Decimal('1.00000'))
        self.assertTrue(any(x.startswith('MAX_PRICE_DEVIATION_PCT') for x in r['reasons']), r['reasons'])
        with self.connect() as db:
            db.execute('DELETE FROM risk_limits')
            db.execute('UPDATE cfg_accounts SET leverage=100 WHERE id=%s', (UUID(a['id']),))
        lim('MAX_MARGIN_PCT', '0.5')
        r = self.place(a, ins, 'm-1', mode='PAPER', qty='1')  # 1 lot = 108,005 USD; /100 = 1,080 margin = 1.08% of 100,000
        self.assertTrue(r['reasons'][0].startswith('MAX_MARGIN_PCT'), r['reasons'])
        lim('MAX_LEVERAGE', '0.5')
        # VaR: 40 daily bars give a history; a 1-lot position's VaR is reported, and a tight VaR limit refuses.
        with self.connect() as db:
            db.execute('DELETE FROM risk_limits')
            start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=60)
            closes = [1.08 + 0.004 * ((-1) ** i) * (i % 5) / 5 for i in range(60)]
            with db.cursor() as cur:
                cur.executemany("INSERT INTO md_bars(account_id, broker_symbol, timeframe, bar_time, open, high, low, close, ticks, source) "
                                "VALUES (%s,'EURUSD','D1',%s,%s,%s,%s,%s,10,'BROKER')", [(UUID(a['id']), start + timedelta(days=i), c, c, c, c) for i, c in enumerate(closes)])
        self.assertEqual(self.place(a, ins, 'v-1', mode='PAPER', qty='1')['state'], 'FILLED')
        v = self.ok(self.alice.get(f"/api/trading/risk/var?account_id={a['id']}"))['items'][0]
        self.assertGreater(Decimal(v['var']), 0, v)
        self.assertEqual(v['timeframe'], 'D1')
        self.assertGreaterEqual(Decimal(v['es']), Decimal(v['var']))
        lim('MAX_VAR_PCT', '0.01')
        r = self.place(a, ins, 'v-2', mode='PAPER', qty='1')
        self.assertTrue(any(x.startswith('MAX_VAR_PCT') for x in r['reasons']), r['reasons'])
        st = self.ok(self.alice.get('/api/trading/risk/stress'))
        down = next(s for s in st['scenarios'] if s['name'] == 'Everything −5%')
        self.assertLess(Decimal(down['total']), 0)
        sc = self.ok(self.alice.post('/api/trading/risk/scenarios', json={'name': 'EUR −2%', 'shocks': [{'match': 'SYMBOL', 'value': 'eurusd', 'pct': '-2'}],
                                                                         'reason': 'desk scenario'}), 201)
        self.assertEqual(sc['shocks'][0]['value'], 'EURUSD')
        self.assertEqual(self.alice.delete(f"/api/trading/risk/scenarios/5e000000-0000-0000-0000-000000000001?reason=not%20needed").status_code, 409)
        self.assertEqual(self.alice.delete(f"/api/trading/risk/scenarios/{sc['id']}?reason=not%20needed").status_code, 204)

    def test_portfolio_extras(self):
        connection, a, b, ins = self.real_account()
        self.quote(b['id'], 1.07990, 1.08005, NOW)
        # Smart routing: best price to buy is account B (lower ask).
        legs = self.ok(self.alice.get(f"/api/trading/routing/preview?instrument_id={ins['id']}&side=BUY&quantity=0.1&accounts={a['id']},{b['id']}"))['items']
        self.assertEqual(len(legs), 2)
        with store.transaction('write') as db:
            from corei_os_mini.trading import routing
            (acc, qty, why), = routing.choose(db, 'BEST_PRICE', [a['id'], b['id']], ins['id'], 'BUY', Decimal('0.1'), 'PAPER')
            self.assertEqual((acc, why['chosen']), (b['id'], 'VTM_B'))
            split = routing.choose(db, 'SPLIT_EQUAL', [a['id'], b['id']], ins['id'], 'BUY', Decimal('0.15'), 'PAPER')
            self.assertEqual(sorted(str(q.normalize()) for _, q, _ in split), ['0.07', '0.08'])
        # Copy trading: B follows A at half size; A's fill gives B an order (paper).
        self.ok(self.alice.post('/api/trading/portfolio/copy-links', json={'master_account_id': a['id'], 'follower_account_id': b['id'], 'ratio': '0.5',
                                                                          'reason': 'client account follows'}), 201)
        self.ok(self.alice.post('/api/trading/portfolio/copy-links', json={'master_account_id': b['id'], 'follower_account_id': a['id'],
                                                                          'reason': 'loop is refused'}), 409)
        m = self.place(a, ins, 'master-1', mode='PAPER', qty='0.20')
        self.assertEqual(m['state'], 'FILLED')
        with self.connect() as db:
            f = db.execute("SELECT * FROM oms_orders WHERE source='COPY'").fetchone()
        self.assertEqual((str(f['account_id']), f['quantity'], f['state']), (b['id'], Decimal('0.1'), 'FILLED'))
        # Partitions: a deployment sizes from its partition; allocation by weight.
        p1 = self.ok(self.alice.post('/api/trading/portfolio/partitions', json={'account_id': a['id'], 'name': 'Trend', 'capital': '60000', 'weight': '3',
                                                                               'reason': 'trend book'}), 201)
        p2 = self.ok(self.alice.post('/api/trading/portfolio/partitions', json={'account_id': a['id'], 'name': 'Reversion', 'capital': '40000', 'weight': '1',
                                                                               'reason': 'mean reversion book'}), 201)
        plan = self.ok(self.alice.post('/api/trading/portfolio/allocate', json={'account_id': a['id'], 'method': 'WEIGHT', 'total': '100000'}))
        self.assertEqual({r['name']: r['after'] for r in plan['plan']}, {'Trend': '75000', 'Reversion': '25000'})
        self.ok(self.alice.post('/api/trading/portfolio/allocate', json={'account_id': a['id'], 'method': 'EQUAL', 'total': '100000', 'apply': True}), 422)
        self.ok(self.alice.post('/api/trading/portfolio/allocate', json={'account_id': a['id'], 'method': 'EQUAL', 'total': '100000', 'apply': True,
                                                                        'reason': 'rebalance monthly'}))
        parts = {p['name']: p for p in self.ok(self.alice.get('/api/trading/portfolio/partitions'))['items']}
        self.assertEqual(parts['Trend']['capital'], '50000')
        self.assertEqual(self.alice.delete(f"/api/trading/portfolio/partitions/{p2['id']}?reason=not%20used").status_code, 204)
        self.assertIsNotNone(p1)
        with self.connect() as db:
            db.execute("UPDATE oms_fills SET at=now() - interval '1 minute'")  # the test trades at a fixed future Wednesday
        attr = self.ok(self.alice.get('/api/trading/portfolio/attribution?by=account'))
        self.assertEqual({r['label'] for r in attr['rows']}, {'VTM_A', 'VTM_B'})
        src = self.ok(self.alice.get('/api/trading/portfolio/attribution?by=source'))
        self.assertEqual({r['label'] for r in src['rows']}, {'MANUAL', 'COPY'})

    def test_optimise_walk_forward_and_drift(self):
        connection, a, _b, ins = self.real_account()
        self.start(self.plan(a, ins, 'EU_OPT'))
        start = NOW - timedelta(days=3)
        closes = [1.10 + 0.002 * ((i // 20) % 2 and 20 - i % 20 or i % 20) / 20 for i in range(600)]
        self.bars(a['id'], closes, start)
        out = self.ok(self.alice.post('/api/trading/optimisations', json={
            'algorithm': 'EMA_CROSSOVER', 'base_parameters': {'timeframe': 'M5', 'fast': 3, 'slow': 8}, 'grid': {'fast': [2, 3, 5], 'slow': [8, 13, 3]},
            'objective': 'NET_PNL', 'account_id': a['id'], 'broker_symbol': 'EURUSD', 'range_from': start.isoformat(), 'range_to': NOW.isoformat(),
            'walk_forward_folds': 3, 'min_trades': 1}), 201)
        self.assertEqual(out['combinations'], 7, 'fast ≥ slow combinations are skipped')
        self.assertEqual(len(out['folds']), 3)
        self.assertIn(out['robustness']['verdict'], ('ROBUST', 'WEAK', 'FRAGILE'))
        self.assertEqual(self.ok(self.alice.get('/api/trading/optimisations'))['items'][0]['id'], out['id'])
        bad = self.alice.post('/api/trading/optimisations', json={'algorithm': 'EMA_CROSSOVER', 'base_parameters': {'timeframe': 'M5', 'fast': 3, 'slow': 8},
                                                                  'grid': {'fast': list(range(1, 30)), 'slow': list(range(30, 50))}, 'account_id': a['id'],
                                                                  'broker_symbol': 'EURUSD', 'range_from': start.isoformat(), 'range_to': NOW.isoformat()})
        self.assertEqual(bad.status_code, 422)
        # Drift: a deployment with a profitable backtest and losing live trades.
        _s, ver, dep = self.deploy_existing(connection, a, ins)
        self.ok(self.alice.post('/api/trading/backtests', json={'strategy_version_id': ver['id'], 'account_id': a['id'], 'broker_symbol': 'EURUSD',
                                                                 'range_from': start.isoformat(), 'range_to': NOW.isoformat()}), 201)
        with self.connect() as db:
            db.execute("UPDATE tr_backtests SET metrics = metrics || '{\"win_rate_pct\": 60, \"profit_factor\": 2.0, \"avg_trade\": \"10\"}'::jsonb")
            db.execute('UPDATE tr_settings SET drift_min_trades=5')
        for i in range(6):
            self.place(a, ins, f'd-buy-{i}', mode='PAPER', qty='0.01', deployment={'id': dep['id'], 'strategy_id': dep['strategy_id']})
            self.quote(a['id'], 1.07900, 1.07910, NOW)
            self.place(a, ins, f'd-sell-{i}', side='SELL', mode='PAPER', qty='0.01', deployment={'id': dep['id'], 'strategy_id': dep['strategy_id']})
            self.quote(a['id'], 1.08000, 1.08010, NOW)
        from corei_os_mini.trading import strategy_health
        with self.connect() as db:
            db.execute('INSERT INTO tr_runtime_state(deployment_id, timeframe) VALUES (%s, %s) ON CONFLICT DO NOTHING', (UUID(dep['id']), 'M5'))
        with store.transaction('write') as db:
            self.assertEqual(strategy_health.drift_all(db), 1)
        drift = self.ok(self.alice.get('/api/trading/drift'))['items'][0]['drift']
        self.assertEqual(drift['status'], 'DRIFT')
        self.assertTrue(any('win rate' in r for r in drift['reasons']), drift)

    def deploy_existing(self, connection, a, ins):
        with self.connect() as db:
            mapping = db.execute('SELECT id FROM cfg_symbol_mappings LIMIT 1').fetchone()
        strat = self.ok(self.alice.post('/api/config/strategies', json={'code': 'EMA_DRIFT', 'name': 'EMA drift test'}), 201)
        ver = self.ok(self.alice.post(f"/api/config/strategies/{strat['id']}/versions",
                                      json={'algorithm': 'EMA_CROSSOVER', 'parameters': {'timeframe': 'M5', 'fast': 3, 'slow': 5}}), 201)
        self.ok(self.bob.post(f"/api/config/strategy-versions/{ver['id']}/approve", json={'version': ver['version']}))
        dep = self.ok(self.alice.post('/api/config/deployments', json={'strategy_id': strat['id'], 'strategy_version_id': ver['id'], 'instrument_id': ins['id'],
                                                                       'account_id': a['id'], 'data_mapping_id': str(mapping['id'])}), 201)
        dep = self.ok(self.bob.post(f"/api/config/deployments/{dep['id']}/activate", json={'version': dep['version'], 'reason': 'paper test run'}))
        return strat, ver, dep


class Transport(unittest.TestCase):
    """brokers.mt5_call's classification: a route that is not reached → the next route; refused before acting → the
    next route; written and then silent → Lost (never another route); nothing reached → NotSent."""

    def run_call(self, transports):
        account = {'id': UUID(int=1), 'external_account_id': '1001', 'connection_settings': {'server_name': 'S'}}
        targets = [{**account, 'connection_settings': {'server_name': 'S', 'bridge': {'host': f'h{i}', 'port': 1, 'fingerprint': 'F'}},
                    'route': {'id': i, 'label': f'r{i}'}} for i in range(len(transports))]
        made = iter(transports)

        def transport(host, port, fp, timeout=10):
            t = next(made)
            if isinstance(t, Exception):
                raise t
            return t
        with patch.object(brokers, '_account', return_value=account), patch.object(brokers, 'mt5_targets', return_value=targets), \
                patch.object(brokers.store, 'use_credential', side_effect=lambda *a: {'password': 'p'}), \
                patch('corei_os_mini.account_routes.bridge_token', side_effect=lambda *a: {'token': 't'}), \
                patch('corei_os_mini.account_routes.record'), patch.object(brokers, 'BridgeTransport', transport), \
                patch.object(brokers, '_too_old', side_effect=lambda b, t: self.too_old):
            return brokers.mt5_call(UUID(int=1), 'test', 'trade', {'action': 'send'}, 'test')

    too_old = None

    def test_old_bridge_never_gets_an_order(self):
        brokers._versions.clear()
        pong = FakeTransport(b'{"t":"pong","version":"0.42.0"}\n')
        with patch.object(brokers, 'BridgeTransport', lambda *a, **k: pong):
            why = brokers._too_old({'host': 'h', 'port': 1, 'fingerprint': 'F'}, 't')
        self.assertTrue(why.startswith('BRIDGE_TOO_OLD'), why)
        self.assertEqual(pong.sent['mode'], 'ping', 'only a ping went to the old bridge')
        self.too_old = why
        with self.assertRaises(brokers.NotSent) as ctx:
            self.run_call([FakeTransport(b'{"t":"trade"}\n')])
        self.assertIn('BRIDGE_TOO_OLD', str(ctx.exception))

    def test_classification(self):
        from corei_os_mini.md.bridge_client import BridgeError
        self.assertEqual(self.run_call([BridgeError('BRIDGE_UNREACHABLE'), FakeTransport(b'{"t":"trade","results":[]}\n')])['via']['route'], 'r1')
        self.assertEqual(self.run_call([FakeTransport(b'{"t":"failed","error":"TERMINAL_BUSY"}\n'), FakeTransport(b'{"t":"trade"}\n')])['via']['route'], 'r1')
        with self.assertRaises(brokers.Lost):
            self.run_call([FakeTransport(None), FakeTransport(b'{"t":"trade"}\n')])
        with self.assertRaises(brokers.Lost):
            self.run_call([FakeTransport(b'')])
        with self.assertRaises(brokers.NotSent):
            self.run_call([BridgeError('BRIDGE_UNREACHABLE'), FakeTransport(b'{"t":"failed","error":"BRIDGE_TOKEN_REJECTED"}\n')])


class FakeTransport:
    def __init__(self, line):
        self.line, self.sent = line, None
        self.sock = type('S', (), {'settimeout': lambda self, t: None})()

    def send(self, message):
        self.sent = dict(message)

    def lines(self):
        if self.line is None:
            raise TimeoutError('timed out')
        if self.line:
            yield self.line

    def close(self):
        pass


class QuickStart(tt.Trading):
    test_paper_trade_end_to_end = None
    test_tickets_limits_and_unknown = None
    test_broker_mode_refused_and_loss_limit_stops = None
    test_backtest_no_look_ahead = None

    def test_one_step_paper_strategy(self):
        _connection, (a, _b, _c), ins = self.setup_accounts()
        self.start(self.plan(a, ins, 'EU_QS'))
        opts = self.ok(self.alice.get('/api/trading/quickstart'))
        src = opts['sources'][0]
        self.assertTrue(src['ready'])
        self.assertEqual((opts['strategies'], opts['deployments']), (0, 0))
        out = self.ok(self.alice.post('/api/trading/quickstart', json={'account_id': a['id'], 'broker_symbol': 'EURUSD', 'preset': 'EMA_FAST',
                                                                        'timeframe': 'M5'}), 201)
        self.assertTrue(out['activated'], out['steps'])
        self.assertEqual((out['deployment']['mode'], out['deployment']['status']), ('PAPER', 'ACTIVE'))
        self.assertEqual(out['version']['state'], 'APPROVED')
        self.assertIn('symbol mapping EURUSD → EURUSD', out['steps'])
        second = self.ok(self.alice.post('/api/trading/quickstart', json={'account_id': a['id'], 'broker_symbol': 'EURUSD', 'preset': 'RSI_REVERSION'}), 201)
        self.assertEqual(second['strategy']['code'], 'RSI_EURUSD_M5')
        self.assertNotIn('symbol mapping EURUSD → EURUSD', second['steps'], 'the mapping is reused')
        # The runtime now evaluates it: the deployment shows on Strategies › Runtime.
        start = NOW - timedelta(minutes=5 * 41)
        self.bars(a['id'], tt.series(40, False), start)
        self.quote(a['id'], 1.08000, 1.08010)
        self.assertEqual(tt.runtime.cycle(NOW)['deployments'], 2)
        self.assertEqual(len(self.ok(self.alice.get('/api/trading/runtime'))['items']), 2)


class EndToEnd(Execution):
    """The gaps closed in 0.45.2: working orders block a new decision, protective stops are placed and executed, broker
    exits are booked, stale prices refuse, an unexecuted decision is retried on the next bar."""
    test_real_orders_on_demo_every_path = None
    test_four_eyes_on_live = None
    test_risk_extras = None
    test_portfolio_extras = None
    test_optimise_walk_forward_and_drift = None

    def deploy_on(self, a, ins, **extra):
        with self.connect() as db:
            mapping = db.execute('SELECT id FROM cfg_symbol_mappings LIMIT 1').fetchone()
        strat = self.ok(self.alice.post('/api/config/strategies', json={'code': 'EMA_RA', 'name': 'EMA realign test'}), 201)
        ver = self.ok(self.alice.post(f"/api/config/strategies/{strat['id']}/versions",
                                      json={'algorithm': 'EMA_CROSSOVER', 'parameters': {'timeframe': 'M5', 'fast': 3, 'slow': 5}}), 201)
        self.ok(self.bob.post(f"/api/config/strategy-versions/{ver['id']}/approve", json={'version': ver['version']}))
        dep = self.ok(self.alice.post('/api/config/deployments', json={'strategy_id': strat['id'], 'strategy_version_id': ver['id'], 'instrument_id': ins['id'],
                                                                       'account_id': a['id'], 'data_mapping_id': str(mapping['id']), **extra}), 201)
        dep = self.ok(self.bob.post(f"/api/config/deployments/{dep['id']}/activate", json={'version': dep['version'], 'reason': 'paper test run'}))
        return strat, ver, dep

    def test_stale_price_and_paper_stop(self):
        _c, a, _b, ins = self.real_account()
        stale = self.place(a, ins, 'stale-1', mode='PAPER', at=max(NOW, datetime.now(timezone.utc)) + timedelta(minutes=10))  # 10 min after the price was received
        self.assertTrue(any(r.startswith('STALE_PRICE') for r in stale['reasons']), stale['reasons'])
        o = self.place(a, ins, 'sl-1', mode='PAPER', stop_loss=Decimal('1.07900'), take_profit=Decimal('1.08300'))
        self.assertEqual(o['state'], 'FILLED')
        pos = self.ok(self.alice.get('/api/trading/portfolio/positions'))['items'][0]
        self.assertEqual((Decimal(pos['stop_loss']), Decimal(pos['take_profit'])), (Decimal('1.079'), Decimal('1.083')))
        with store.transaction('write') as db:
            self.assertEqual(oms.protect(db, NOW)['protective_exits'], 0, 'not touched yet')
        self.quote(a['id'], 1.07890, 1.07900, NOW)
        with store.transaction('write') as db:
            self.assertEqual(oms.protect(db, NOW)['protective_exits'], 1)
        self.assertEqual(self.ok(self.alice.get('/api/trading/portfolio/positions'))['items'], [])
        with self.connect() as db:
            src = db.execute("SELECT source, state FROM oms_orders ORDER BY created_at DESC LIMIT 1").fetchone()
        self.assertEqual((src['source'], src['state']), ('PROTECT', 'FILLED'))

    def test_working_order_blocks_and_broker_stop_is_booked(self):
        connection, a, _b, ins = self.real_account()
        self.sending()
        o = self.place(a, ins, 'buy-sl', stop_loss=Decimal('1.07500'))
        execution.dispatch()
        self.assertEqual(self.order(o['id'])['state'], 'FILLED')
        self.assertEqual(self.broker.sends, 1)
        # The broker closes the position on its stop-loss: a deal with COREI's magic, no COREI tag.
        self.broker.ticket += 1
        self.broker.deals.append({'ticket': self.broker.ticket, 'order': 1, 'symbol': 'EURUSD', 'side': 'SELL', 'entry': 'OUT', 'volume': '0.1', 'price': '1.07500',
                                  'commission': '-0.7', 'swap': '0', 'fee': '0', 'profit': '-50.2', 'time': '2026-11-18T11:00:00.000', 'comment': '[sl 1.07500]',
                                  'magic': MAGIC, 'position_id': 777, 'reason': 'SL'})
        run = self.ok(self.alice.post(f"/api/trading/posttrade/reconcile/{a['id']}"))
        self.assertEqual(run['status'], 'MATCHED', run)
        self.assertEqual(self.ok(self.alice.get('/api/trading/portfolio/positions'))['items'], [], 'COREI closed the position with the broker')
        with self.connect() as db:
            ex = db.execute("SELECT o.source, o.state, f.broker_deal_id FROM oms_orders o JOIN oms_fills f ON f.order_id=o.id WHERE o.source='PROTECT'").fetchone()
        self.assertEqual((ex['source'], ex['state'], ex['broker_deal_id']), ('PROTECT', 'FILLED', str(self.broker.ticket)))
        # Booked once: the next read changes nothing.
        self.ok(self.alice.post(f"/api/trading/posttrade/reconcile/{a['id']}"))
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) AS n FROM oms_orders WHERE source='PROTECT'").fetchone()['n'], 1)

    def test_realign_and_working_order_rule(self):
        connection, a, _b, ins = self.real_account()
        self.start(self.plan(a, ins, 'EU_RA'))
        strat, ver, dep = self.deploy_on(a, ins, sizing={'method': 'FIXED_QTY', 'quantity': '0.10'}, protect={'stop_atr': '2', 'take_atr': '3'})
        start = NOW - timedelta(minutes=5 * 41)
        self.bars(a['id'], tt.series(40, False), start)
        tt.runtime.cycle(NOW)
        kill = self.ok(self.alice.post('/api/trading/risk/kill-switches', json={'scope': 'STRATEGY', 'target_id': strat['id'], 'reason': 'hold for a moment'}), 201)
        self.bars(a['id'], tt.series(41, True)[40:], start + timedelta(minutes=5 * 40))
        tt.runtime.cycle(NOW)
        first = self.ok(self.alice.get('/api/trading/signals'))['items'][0]
        self.assertEqual((first['side'], first['state']), ('BUY', 'REJECTED'))
        self.ok(self.alice.post(f"/api/trading/risk/kill-switches/{kill['id']}/release", json={'reason': 'go on now'}))
        last = tt.series(41, True)[-1]
        self.bars(a['id'], [last + 0.0010], start + timedelta(minutes=5 * 41))  # the next bar: still long, no new cross
        out = tt.runtime.cycle(NOW)
        self.assertEqual((out['signals'], out['orders']), (1, 1), out)
        sig = self.ok(self.alice.get('/api/trading/signals'))['items'][0]
        self.assertTrue(sig['explanation'][0].startswith('REALIGN'), sig['explanation'])
        self.assertEqual(sig['state'], 'ACTED')
        orders = self.ok(self.alice.get('/api/trading/orders'))['items']
        self.assertEqual((orders[0]['source'], orders[0]['state']), ('REALIGN', 'FILLED'))
        self.assertIsNotNone(orders[0]['stop_loss'], 'the protective stop from the deployment rides on the order')
        self.assertLess(Decimal(orders[0]['stop_loss']), Decimal(orders[0]['avg_fill_price']))
        # Executed: no further realign on the next bar.
        self.bars(a['id'], [last + 0.0015], start + timedelta(minutes=5 * 42))
        self.assertEqual(tt.runtime.cycle(NOW)['signals'], 0)
        # A working order of the deployment blocks the next decision.
        with self.connect() as db:
            db.execute("UPDATE oms_orders SET state='UNKNOWN' WHERE id=%s", (UUID(orders[0]['id']),))
        manual = self.ok(self.alice.post('/api/trading/signals', json={'deployment_id': dep['id'], 'side': 'SELL', 'reason': 'test the working rule'}), 201)
        self.assertIn('WORKING_ORDER', ' '.join(manual['signal']['reasons']))
