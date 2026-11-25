"""Execution handoff (Stage 4 → Stage 7): a strategy's decision reaches execution by the deployment's policy.
APPROVE leaves it waiting in the execution queue with its size, price terms and expiry; a person executes it (the order
is target − position now, through the normal risk check, never twice) or declines it; the window passes and it
expires; a newer decision supersedes an older one; SIGNAL_ONLY records without ordering; AUTO keeps placing at once,
now with the policy's order terms. Every policy change is versioned and audited. The strategy monitor shows each
deployment with its handoff, position, last signal and decision."""
from __future__ import annotations

import importlib
import importlib.util
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

tt = importlib.import_module('test_trading') if importlib.util.find_spec('test_trading') else importlib.import_module('tests.test_trading')
from corei_os_mini.trading import handoff, runtime  # noqa: E402

NOW = tt.NOW
series = tt.series


class Handoff(tt.Trading):
    test_paper_trade_end_to_end = None
    test_tickets_limits_and_unknown = None
    test_broker_mode_refused_and_loss_limit_stops = None
    test_backtest_no_look_ahead = None

    def policy(self, dep_id, **kw):
        cur = next(p for p in self.ok(self.alice.get('/api/trading/execution/policies'))['items'] if p['id'] == dep_id)
        body = {**{k: v for k, v in cur['policy'].items()}, **kw, 'version': cur['version'], 'reason': 'desk decides this one'}
        return self.ok(self.bob.put(f'/api/trading/execution/policies/{dep_id}', json=body))

    def setup_signal(self, tag):
        connection, (a, _b, _c), ins = self.setup_accounts()
        self.start(self.plan(a, ins, tag))
        strat, ver, dep = self.deploy(connection, a, ins, sizing={'method': 'FIXED_QTY', 'quantity': '0.10'})
        start = NOW - timedelta(minutes=5 * 41)
        self.bars(a['id'], series(40, False), start)
        self.quote(a['id'], 1.08000, 1.08010)
        runtime.cycle(NOW)
        return a, ins, dep, start

    def test_approve_execute_decline_expire_supersede(self):
        a, ins, dep, start = self.setup_signal('EU_H1')
        p = self.policy(dep['id'], handoff='APPROVE', approval_minutes=15, order_type='LIMIT', limit_mode='MARKETABLE', limit_offset_ticks=2)
        self.assertEqual((p['policy']['handoff'], p['policy']['limit_offset_ticks']), ('APPROVE', 2))
        stale = self.bob.put(f"/api/trading/execution/policies/{dep['id']}", json={'handoff': 'AUTO', 'version': p['version'] - 1, 'reason': 'old version'})
        self.assertEqual(stale.status_code, 409)
        # The crossing bar: the strategy decides BUY 0.1, but nothing is ordered — it waits for execution.
        self.bars(a['id'], series(41, True)[40:], start + timedelta(minutes=5 * 40))
        out = runtime.cycle(NOW)
        self.assertEqual((out['signals'], out['orders']), (1, 0), out)
        self.assertEqual(self.ok(self.alice.get('/api/trading/orders'))['items'], [])
        flow = self.ok(self.alice.get('/api/trading/execution/flow'))
        it = flow['items'][0]
        self.assertEqual((it['outcome'], it['side'], Decimal(it['quantity']), it['order_type'], it['handoff']), ('PENDING', 'BUY', Decimal('0.1'), 'LIMIT', 'APPROVE'))
        self.assertEqual(Decimal(it['limit_price']), Decimal('1.08012'), 'ask + 2 ticks: a price cap')
        self.assertEqual(flow['summary']['pending'], 1)
        scoped_url = '/api/trading/execution/flow?outcome=PENDING&limit=1&deployment_id='
        self.assertEqual(self.ok(self.alice.get(scoped_url + str(dep['id'])))['items'], [it])
        self.assertEqual(self.ok(self.alice.get(scoped_url + '00000000-0000-0000-0000-000000000000'))['items'], [])

        self.assertEqual(it['signal_state'], 'VALID')
        self.assertEqual(it['strategy_code'], 'EMA_FAST')
        mon = self.ok(self.alice.get('/api/trading/strategies/monitor'))
        row = mon['items'][0]
        self.assertEqual((row['handoff'], row['pending'], row['last_decision']['outcome']), ('APPROVE', 1, 'PENDING'))
        self.assertEqual(mon['summary']['pending'], 1)
        # Too large a quantity is refused; a smaller one is executed through the normal order path.
        with patch.object(handoff, '_now', return_value=NOW + timedelta(minutes=1)):
            self.assertEqual(self.alice.post(f"/api/trading/execution/decisions/{it['id']}/execute", json={'quantity': '0.5'}).status_code, 422)
            done = self.ok(self.alice.post(f"/api/trading/execution/decisions/{it['id']}/execute", json={'quantity': '0.05', 'order_type': 'MARKET', 'note': 'half size'}))
            self.assertEqual(done['state'], 'ORDERED')
            self.assertEqual((done['order']['state'], Decimal(done['order']['quantity']), done['order']['order_type']), ('FILLED', Decimal('0.05'), 'MARKET'))
            again = self.alice.post(f"/api/trading/execution/decisions/{it['id']}/execute", json={})
            self.assertEqual(again.status_code, 409, 'an executed decision can never be sent again')
        it = next(x for x in self.ok(self.alice.get('/api/trading/execution/flow'))['items'] if x['id'] == it['id'])
        self.assertEqual((it['outcome'], it['decided_by'], it['decision_note'], it['order_state']), ('ORDERED', 'alice', 'half size', 'FILLED'))
        self.assertEqual(self.ok(self.alice.get('/api/trading/signals'))['items'][0]['state'], 'ACTED')
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) AS n FROM cfg_audit_log WHERE action IN ('EXECUTE_DECISION','UPDATE_EXECUTION_POLICY')").fetchone()['n'], 2)

        # Two more decisions: the newer supersedes the older; one is declined; the next expires.
        from corei_os_mini.trading import signals as sg
        with self.connect() as db:
            deps = __import__('corei_os_mini.trading.common', fromlist=['deployments']).deployments(db, None, dep['id'])
        manual_dep = deps[0]
        self.quote(a['id'], 1.08000, 1.08010, NOW + timedelta(minutes=10))
        with __import__('corei_os_mini.config_store', fromlist=['transaction']).transaction('write') as db:
            s1 = sg.create(db, manual_dep, side='SELL', strength=70, bar_time=NOW + timedelta(minutes=5), timeframe='M5', features={'atr14': 0.002},
                           explanation=['test sell'], source='RUNTIME', actor='runtime', price=None)
            r1 = sg.process(db, s1, manual_dep, NOW + timedelta(minutes=10))
            s2 = sg.create(db, manual_dep, side='FLAT', strength=70, bar_time=NOW + timedelta(minutes=10), timeframe='M5', features={'atr14': 0.002},
                           explanation=['test flat'], source='RUNTIME', actor='runtime', price=None)
            r2 = sg.process(db, s2, manual_dep, NOW + timedelta(minutes=10))
        self.assertEqual((r1['state'], r2['state']), ('PENDING', 'PENDING'))
        rows = {x['signal_id']: x for x in self.ok(self.alice.get('/api/trading/execution/flow'))['items']}
        self.assertEqual(rows[str(s1['id'])]['outcome'], 'SUPERSEDED')
        self.assertEqual(Decimal(rows[str(s2['id'])]['quantity']), Decimal('0.05'), 'FLAT: sell what is held')
        self.assertEqual(self.alice.post(f"/api/trading/execution/decisions/{rows[str(s2['id'])]['id']}/decline", json={'reason': 'x'}).status_code, 422)
        self.ok(self.alice.post(f"/api/trading/execution/decisions/{rows[str(s2['id'])]['id']}/decline", json={'reason': 'not before the data'}))
        self.quote(a['id'], 1.08000, 1.08010, NOW + timedelta(minutes=15))
        with __import__('corei_os_mini.config_store', fromlist=['transaction']).transaction('write') as db:
            s3 = sg.create(db, manual_dep, side='SELL', strength=70, bar_time=NOW + timedelta(minutes=15), timeframe='M5', features={'atr14': 0.002},
                           explanation=['test sell 2'], source='RUNTIME', actor='runtime', price=None)
            sg.process(db, s3, manual_dep, NOW + timedelta(minutes=15))
            self.assertEqual(handoff.expire(db, NOW + timedelta(hours=2)), 1)
        rows = {x['signal_id']: x for x in self.ok(self.alice.get('/api/trading/execution/flow'))['items']}
        self.assertEqual((rows[str(s2['id'])]['outcome'], rows[str(s2['id'])]['signal_state']), ('DECLINED', 'CANCELLED'))
        self.assertEqual((rows[str(s3['id'])]['outcome'], rows[str(s3['id'])]['signal_state']), ('EXPIRED', 'EXPIRED'))
        with patch.object(handoff, '_now', return_value=NOW + timedelta(hours=3)):
            self.assertEqual(self.alice.post(f"/api/trading/execution/decisions/{rows[str(s3['id'])]['id']}/execute", json={}).status_code, 409)
        with self.connect() as db:
            self.assertIsNone(runtime.realign_reason(db, manual_dep, 'SHORT'), 'a decision waiting for a person is never retried by itself')

    def test_signal_only_and_auto_limit_terms(self):
        a, ins, dep, start = self.setup_signal('EU_SO')
        self.policy(dep['id'], handoff='SIGNAL_ONLY')
        self.bars(a['id'], series(41, True)[40:], start + timedelta(minutes=5 * 40))
        runtime.cycle(NOW)
        it = self.ok(self.alice.get('/api/trading/execution/flow?outcome=RECORDED'))['items']
        self.assertEqual((len(it), it[0]['side'], Decimal(it[0]['quantity']), it[0]['signal_state']), (1, 'BUY', Decimal('0.1'), 'ACTED'))
        self.assertEqual(self.ok(self.alice.get('/api/trading/orders'))['items'], [], 'signal-only never orders')
        # AUTO with a passive LIMIT: a sell is placed now at ask + 1 tick and rests (not marketable).
        self.policy(dep['id'], handoff='AUTO', order_type='LIMIT', limit_mode='PASSIVE', limit_offset_ticks=1, time_in_force='DAY')
        from corei_os_mini import config_store as store
        from corei_os_mini.trading import signals as sg
        from corei_os_mini.trading.common import deployments
        self.quote(a['id'], 1.08000, 1.08010, NOW + timedelta(minutes=5))
        with store.transaction('write') as db:
            d = deployments(db, None, dep['id'])[0]
            s = sg.create(db, d, side='SELL', strength=80, bar_time=NOW + timedelta(minutes=5), timeframe='M5', features={'atr14': 0.002},
                          explanation=['auto sell'], source='RUNTIME', actor='runtime', price=None)
            r = sg.process(db, s, d, NOW + timedelta(minutes=5))
        self.assertEqual(r['state'], 'ACTED')
        o = r['order']
        self.assertEqual((o['order_type'], Decimal(o['limit_price']), o['time_in_force'], o['state']), ('LIMIT', Decimal('1.08011'), 'DAY', 'ACKNOWLEDGED'))
        it = next(x for x in self.ok(self.alice.get('/api/trading/execution/flow'))['items'] if x['signal_id'] == str(s['id']))
        self.assertEqual((it['outcome'], it['handoff'], it['order_type'], it['side']), ('ORDERED', 'AUTO', 'LIMIT', 'SELL'))
