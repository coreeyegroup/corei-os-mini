"""Flexible configuration (release 0.68.0): identifying fields change with a reason; only a few stay fixed.

Evidence for: a server's platform and environment and an account's broker login stay fixed; codes and symbols of
providers, servers, accounts, instruments, groups and strategies change with a reason (refused without), kept in the
audit log; a renamed instrument keeps its old symbol as an alias; an account moves only to a server with the same
platform and environment, and its own symbol mappings move with it; a mapping can apply to a group of accounts, and the
symbol resolves account → group → server; a deployment's strategy/instrument/account and a plan's account/symbol change
only while not running, with a reason; plans have a display name and save every editable field.
"""
from __future__ import annotations

import importlib
import importlib.util

te = importlib.import_module('test_execution') if importlib.util.find_spec('test_execution') else importlib.import_module('tests.test_execution')
from corei_os_mini import config_store as store  # noqa: E402
from corei_os_mini.trading import common  # noqa: E402


class Flexible(te.Execution):
    test_sending_disabled_during_precheck = test_real_orders_on_demo_every_path = test_four_eyes_on_live = None
    test_risk_extras = test_portfolio_extras = test_optimise_walk_forward_and_drift = None

    def record(self, entity, record_id):
        return next(x for x in self.ok(self.alice.get(f'/api/config/{entity}'))['items'] if x['id'] == record_id)

    def put(self, entity, rec, status=200, reason=None, **changes):
        fields = store.SPECS[entity].model.model_fields
        body = {k: rec[k] for k in fields if k in rec}
        if entity == 'accounts':
            body.pop('group_ids', None)
        body.update(changes, version=rec['version'])
        if reason is not None:
            body['reason'] = reason
        r = self.alice.put(f"/api/config/{entity}/{rec['id']}", json=body)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def test_identity_changes_need_a_reason_and_only_platform_environment_login_stay_fixed(self):
        connection, (a, b, _c), instrument = self.setup_accounts()
        conn = self.record('connections', connection['id'])
        self.assertIn('cannot be changed', self.put('connections', conn, 422, environment='LIVE')['detail'])
        self.put('connections', conn, 422, platform='FIX')   # refused (its settings do not fit another platform either)
        acc = self.record('accounts', a['id'])
        self.assertIn('cannot be changed', self.put('accounts', acc, 422, external_account_id='99999')['detail'])
        # Codes and symbols: refused without a reason, saved with one, reason in the audit log.
        self.assertIn('needs a reason', self.put('connections', conn, 422, code='VTM_LIVE3')['detail'])
        renamed = self.put('connections', conn, reason='clearer name', code='VTM_LIVE3')
        self.assertEqual(renamed['code'], 'VTM_LIVE3')
        audit = self.ok(self.alice.get('/api/config/audit', params={'entity': 'connection', 'entity_id': connection['id']}))['items'][0]
        self.assertEqual((audit['reason'], audit['after_state']['code']), ('clearer name', 'VTM_LIVE3'))
        provider = self.record('providers', conn['provider_id'])
        self.assertEqual(self.put('providers', provider, reason='brand name', code='VT_MARKETS')['code'], 'VT_MARKETS')
        # A renamed instrument keeps its old symbol as an alias.
        ins = self.record('instruments', instrument['id'])
        moved = self.put('instruments', ins, reason='broker-neutral name', symbol='EURUSD_SPOT')
        self.assertEqual(moved['symbol'], 'EURUSD_SPOT')
        self.assertIn(ins['symbol'], moved['aliases'])
        # A plain edit needs no reason.
        self.assertEqual(self.put('accounts', self.record('accounts', a['id']), name='Main account')['name'], 'Main account')

    def test_account_moves_to_a_matching_server_with_its_mappings(self):
        connection, (a, _b, _c), instrument = self.setup_accounts()
        conn = self.record('connections', connection['id'])
        other = self.ok(self.alice.post('/api/config/connections', json={**{k: conn[k] for k in store.SPECS['connections'].model.model_fields if k in conn},
                                                                         'code': 'VTM_L4', 'name': 'Live 4'}), 201)
        mapping = self.ok(self.alice.post('/api/config/mappings', json={'connection_id': connection['id'], 'account_id': a['id'],
                                                                       'instrument_id': instrument['id'], 'broker_symbol': 'EURUSD-VIP', 'usage': 'BOTH'}), 201)
        acc = self.record('accounts', a['id'])
        self.put('accounts', acc, reason='broker moved the account', connection_id=other['id'])
        self.assertEqual(self.record('mappings', mapping['id'])['connection_id'], other['id'], 'its own mapping moved with it')
        live = self.ok(self.alice.post('/api/config/connections', json={**{k: conn[k] for k in store.SPECS['connections'].model.model_fields if k in conn},
                                                                        'code': 'VTM_OTHER', 'name': 'Other'}), 201)
        with self.connect() as db:
            db.execute("UPDATE cfg_connections SET environment='LIVE' WHERE id=%s", (live['id'],))
        self.assertEqual(conn['environment'], 'DEMO')
        self.assertIn('same platform and environment', self.put('accounts', self.record('accounts', a['id']), 422, reason='try live', connection_id=live['id'])['detail'])

    def test_group_mapping_resolves_between_account_and_server(self):
        connection, (a, b, _c), instrument = self.setup_accounts()
        group = self.ok(self.alice.post('/api/config/groups', json={'code': 'VIP', 'name': 'VIP accounts'}), 201)
        with self.connect() as db:
            db.execute('INSERT INTO cfg_account_group_members(group_id, account_id) VALUES (%s,%s),(%s,%s)', (group['id'], a['id'], group['id'], b['id']))
        base = {'connection_id': connection['id'], 'instrument_id': instrument['id'], 'usage': 'BOTH'}
        self.ok(self.alice.post('/api/config/mappings', json={**base, 'broker_symbol': 'EURUSD'}), 201)
        self.ok(self.alice.post('/api/config/mappings', json={**base, 'broker_symbol': 'EURUSD-VIP', 'account_group_id': group['id']}), 201)
        self.ok(self.alice.post('/api/config/mappings', json={**base, 'broker_symbol': 'EURUSD.r', 'account_id': b['id']}), 201)
        self.ok(self.alice.post('/api/config/mappings', json={**base, 'broker_symbol': 'X', 'account_id': a['id'], 'account_group_id': group['id']}), 422)
        with self.connect() as db:
            self.assertEqual(common.trading_symbol(db, a['id'], instrument['id']), 'EURUSD-VIP', 'group beats server')
            self.assertEqual(common.trading_symbol(db, b['id'], instrument['id']), 'EURUSD.r', 'account beats group')
            db.execute('DELETE FROM cfg_account_group_members WHERE account_id=%s', (a['id'],))
            self.assertEqual(common.trading_symbol(db, a['id'], instrument['id']), 'EURUSD', 'outside the group: the server default')
        listed = [m for m in self.ok(self.alice.get('/api/config/mappings'))['items'] if m['broker_symbol'] == 'EURUSD-VIP'][0]
        self.assertEqual(listed['group_code'], 'VIP')

    def test_plan_saves_name_and_changes_source_only_while_paused(self):
        _connection, (a, b, _c), instrument = self.setup_accounts()
        p = self.plan(a, instrument, 'EU_A', name='EURUSD · broker A · main feed')
        self.assertEqual(p['name'], 'EURUSD · broker A · main feed')
        p = self.start(p)
        body = {k: p[k] for k in ('code', 'name', 'account_id', 'broker_symbol', 'instrument_id', 'purpose', 'feed_role', 'data_types', 'bar_timeframes',
                                  'stale_seconds', 'max_spread_points')}
        r = self.alice.put(f"/api/md/plans/{p['id']}", json={**body, 'account_id': b['id'], 'reason': 'move', 'version': p['version']})
        self.assertEqual(r.status_code, 409, r.text)
        p = self.ok(self.alice.post(f"/api/md/plans/{p['id']}/status", json={'status': 'PAUSED', 'version': p['version'], 'reason': 'Pause to move'}))
        r = self.alice.put(f"/api/md/plans/{p['id']}", json={**body, 'account_id': b['id'], 'version': p['version']})
        self.assertEqual(r.status_code, 422, r.text)
        moved = self.ok(self.alice.put(f"/api/md/plans/{p['id']}", json={**body, 'account_id': b['id'], 'code': 'EU_B_MAIN', 'reason': 'use broker B', 'version': p['version']}))
        self.assertEqual((moved['account_id'], moved['code']), (b['id'], 'EU_B_MAIN'))

    def test_strategy_code_and_deployment_links_change_with_a_reason(self):
        connection, (a, b, _c), ins = self.setup_accounts()
        mapping = self.ok(self.alice.post('/api/config/mappings', json={'connection_id': connection['id'], 'instrument_id': ins['id'],
                                                                       'broker_symbol': 'EURUSD', 'usage': 'BOTH'}), 201)
        s = self.ok(self.alice.post('/api/config/strategies', json={'code': 'FLEX_EMA', 'name': 'Flex EMA'}), 201)
        body = {'code': 'FLEX_EMA_FAST', 'name': s['name'], 'description': '', 'status': 'ACTIVE', 'version': s['version']}
        self.ok(self.alice.put(f"/api/config/strategies/{s['id']}", json=body), 422)
        renamed = self.ok(self.alice.put(f"/api/config/strategies/{s['id']}", json={**body, 'reason': 'clearer code'}))
        self.assertEqual(renamed['code'], 'FLEX_EMA_FAST')
        v = self.ok(self.alice.post(f"/api/config/strategies/{s['id']}/versions",
                                    json={'algorithm': 'EMA_CROSSOVER', 'parameters': {'timeframe': 'M5', 'fast': 3, 'slow': 8}}), 201)
        self.ok(self.bob.post(f"/api/config/strategy-versions/{v['id']}/approve", json={'version': v['version']}))
        dep = self.ok(self.alice.post('/api/config/deployments', json={'strategy_id': s['id'], 'strategy_version_id': v['id'], 'instrument_id': ins['id'],
                                                                       'account_id': a['id'], 'data_mapping_id': mapping['id']}), 201)
        keep = {k: dep[k] for k in ('strategy_id', 'strategy_version_id', 'instrument_id', 'data_mapping_id', 'mode', 'sizing') if k in dep}
        self.ok(self.alice.put(f"/api/config/deployments/{dep['id']}", json={**keep, 'account_id': b['id'], 'version': dep['version']}), 422)
        moved = self.ok(self.alice.put(f"/api/config/deployments/{dep['id']}", json={**keep, 'account_id': b['id'], 'reason': 'use account B', 'version': dep['version']}))
        self.assertEqual(moved['account_id'], b['id'])
        with self.connect() as db:
            db.execute("UPDATE cfg_deployments SET status='ACTIVE' WHERE id=%s", (dep['id'],))
        refused = self.ok(self.alice.put(f"/api/config/deployments/{dep['id']}", json={**keep, 'account_id': a['id'], 'reason': 'back to A', 'version': moved['version']}), 422)
        self.assertIn('Pause this deployment', str(refused))
