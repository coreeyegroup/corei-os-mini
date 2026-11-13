"""Connectivity tree: technology › who › server/session › accounts; the one-step "add who"; provider types."""
import importlib
import importlib.util

test_market_data = importlib.import_module('test_market_data') if importlib.util.find_spec('test_market_data') \
    else importlib.import_module('tests.test_market_data')


class ConnectivityTree(test_market_data.MarketData):
    test_plans_estimate_controls_and_jobs = test_collector_end_to_end = None

    def test_tree_and_add_who(self):
        catalogue = self.ok(self.alice.get('/api/config/adapters'))
        techs = {t['id']: t for t in catalogue['technologies']}
        self.assertEqual(list(techs)[:4], ['MT5', 'MT4', 'CTRADER', 'FIX'])
        self.assertEqual((techs['MT5']['state'], techs['FIX']['state'], techs['API']['state'], techs['CTRADER']['state']),
                         ('BUILT', 'BUILT', 'PARTIAL', 'PLANNED'))
        self.assertEqual(catalogue['provider_kinds']['DATA_CENTRE'], 'Data centre')

        # New who (an ECN) with its FIX session, in one step; code and name are filled in.
        made = self.ok(self.alice.post('/api/config/venues', json={
            'provider': {'code': 'LMAXG', 'name': 'LMAX Global', 'kind': 'ECN'},
            'connection': {'platform': 'FIX', 'environment': 'DEMO', 'purpose': 'MARKET_DATA', 'adapter': 'NATIVE',
                           'settings': {'host': 'fix-md.example.net', 'port': 443, 'sender_comp_id': 'COREI', 'target_comp_id': 'LMXBDM'}}}), 201)
        self.assertEqual(made['provider_action'], 'CREATED')
        self.assertEqual(made['connection']['code'], 'LMAXG_FIX_DEMO')
        self.assertIn('fix-md.example.net:443', made['connection']['name'])

        # A second session for the same who.
        again = self.ok(self.alice.post('/api/config/venues', json={
            'provider_id': made['provider']['id'],
            'connection': {'platform': 'FIX', 'environment': 'DEMO', 'purpose': 'TRADING', 'adapter': 'NATIVE',
                           'settings': {'host': 'fix-order.example.net', 'port': 443, 'sender_comp_id': 'COREI', 'target_comp_id': 'LMXBD'}}}), 201)
        self.assertEqual((again['provider_action'], again['connection']['code']), ('REUSED', 'LMAXG_FIX_DEMO_2'))
        self.ok(self.alice.post('/api/config/accounts', json={'connection_id': again['connection']['id'], 'code': 'LMAX_EXEC_1', 'name': 'LMAX execution 1',
                                                             'external_account_id': 'A1', 'base_currency': 'USD'}), 201)

        # A data centre with no connection yet; invalid input explained per field; both provider forms refused.
        self.ok(self.alice.post('/api/config/providers', json={'code': 'EQ_LD4', 'name': 'Equinix LD4', 'kind': 'DATA_CENTRE'}), 201)
        bad = self.alice.post('/api/config/venues', json={'provider_id': made['provider']['id'],
                                                          'connection': {'platform': 'FIX', 'environment': 'DEMO', 'purpose': 'BOTH', 'settings': {'host': 'x'}}})
        self.assertEqual(bad.status_code, 422)
        self.assertIn('connection.settings', bad.text)
        both = self.alice.post('/api/config/venues', json={'provider_id': made['provider']['id'], 'provider': {'code': 'X', 'name': 'X', 'kind': 'BROKER'},
                                                           'connection': {}})
        self.assertEqual(both.status_code, 422)

        tree = self.ok(self.alice.get('/api/config/tree'))
        fix = next(t for t in tree['technologies'] if t['id'] == 'FIX')
        who = next(w for w in fix['who'] if w['provider']['code'] == 'LMAXG')
        self.assertEqual(len(who['connections']), 2)
        self.assertEqual(fix['counts']['accounts'], 1)
        acct = next(c for c in who['connections'] if c['code'] == 'LMAXG_FIX_DEMO_2')['accounts'][0]
        self.assertIn('data_blockers', acct, 'accounts carry readiness')
        self.assertIn('EQ_LD4', [p['code'] for p in tree['unconnected']])
        self.assertEqual(next(t for t in tree['technologies'] if t['id'] == 'MT4')['who'], [])
