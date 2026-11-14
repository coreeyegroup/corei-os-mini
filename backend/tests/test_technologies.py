"""Provider › account › server with the technology chosen per account: MT4 and cTrader accounts can be recorded
with their server and login; a provider lists the technologies it offers; logins go where they belong."""
import importlib
import importlib.util

test_configuration = importlib.import_module('test_configuration') if importlib.util.find_spec('test_configuration') \
    else importlib.import_module('tests.test_configuration')
Base = test_configuration.ConfigurationPostgresTests
ARCHIVE_URL = '/api/config/accounts/{id}/archive'


class Technologies(Base):
    def test_mt4_ctrader_and_provider_technologies(self):
        vt = self.post('providers', {'code': 'VTM', 'name': 'VT Markets', 'kind': 'BROKER', 'technologies': ['MT5', 'MT4', 'MT5']})
        self.assertEqual(vt['technologies'], ['MT5', 'MT4'], 'stored once each, in order')
        self.post('providers', {'code': 'BAD', 'name': 'Bad', 'kind': 'BROKER', 'technologies': ['MT9']}, status=422)
        mt4 = self.post('connections', {'provider_id': vt['id'], 'code': 'VTM_MT4_LIVE', 'name': 'VT Markets · MT4 live · VTMarkets-Live',
                                        'platform': 'MT4', 'environment': 'LIVE', 'purpose': 'BOTH',
                                        'settings': {'server_name': 'VTMarkets-Live', 'access_points': [{'name': 'VTMarkets-Live', 'host': '198.51.100.4', 'port': 443}]}})
        acct = self.post('accounts', {'connection_id': mt4['id'], 'code': 'VTM_MT4_1', 'name': 'MT4 one', 'external_account_id': '700123', 'base_currency': 'USD'})
        self.post('accounts', {'connection_id': mt4['id'], 'code': 'VTM_MT4_X', 'name': 'bad', 'external_account_id': 'AB12', 'base_currency': 'USD'}, status=422)
        r = self.client.put(f"/api/config/accounts/{acct['id']}/credential", json={'credential': {'kind': 'MT5_LOGIN', 'password': 'pw-123456', 'access_level': 'READ_ONLY'}})
        self.assertIn(r.status_code, (200, 201), r.text)
        r = self.client.put(f"/api/config/connections/{mt4['id']}/credential", json={'credential': {'kind': 'API_KEY', 'api_key': 'k', 'api_secret': 's'}})
        self.assertEqual(r.status_code, 422, 'MT4 logins live on the account')
        ct = self.post('connections', {'provider_id': vt['id'], 'code': 'VTM_CT_DEMO', 'name': 'VT Markets · cTrader demo', 'platform': 'CTRADER',
                                       'environment': 'DEMO', 'purpose': 'BOTH', 'settings': {'host': 'demo.ctraderapi.com'}})
        self.assertEqual(ct['settings']['port'], 5035)
        r = self.client.put(f"/api/config/connections/{ct['id']}/credential", json={'credential': {'kind': 'OAUTH_CLIENT', 'client_id': 'app', 'client_secret': 'sec'}})
        self.assertIn(r.status_code, (200, 201), r.text)
        self.post('accounts', {'connection_id': ct['id'], 'code': 'VTM_CT_1', 'name': 'cTrader one', 'external_account_id': '3456789', 'base_currency': 'USD'})
        rows = {a['code']: a for a in self.client.get('/api/config/accounts').json()['items']}
        self.assertIn('ADAPTER_NOT_AVAILABLE', ' '.join(rows['VTM_MT4_1']['data_blockers']), 'recorded, not collecting until the connector exists')
        tree = self.client.get('/api/config/tree').json()
        techs = {t['id']: t for t in tree['technologies']}
        self.assertEqual((techs['MT4']['counts']['accounts'], techs['CTRADER']['counts']['accounts']), (1, 1))
        self.assertIn('ANGELONE', [p['id'] for p in tree['api_presets']])


class DeleteOrArchive(Base):
    """An account with nothing collected is deleted with its collection plans; one with history says exactly what
    keeps it, and archiving stops its collection."""
    def _account(self, code, login):
        p = self.post('providers', {'code': 'P' + code[:8], 'name': 'P ' + code, 'kind': 'BROKER'})
        c = self.post('connections', {'provider_id': p['id'], 'code': code + '_SRV', 'name': code, 'platform': 'MT5', 'environment': 'DEMO', 'purpose': 'BOTH',
                                      'settings': {'server_name': 'X-Demo', 'access_points': [{'name': 'X-Demo', 'host': '198.51.100.9', 'port': 443}]}})
        a = self.post('accounts', {'connection_id': c['id'], 'code': code, 'name': code, 'external_account_id': login, 'base_currency': 'USD'})
        with self.connect() as db:
            db.execute("INSERT INTO md_collection_plans(id, code, account_id, broker_symbol, purpose, created_by, updated_by) VALUES (gen_random_uuid(), %s, %s, 'EURUSD', 'RESEARCH', 'alice', 'alice')", (code + '_EURUSD', a['id']))
        return a

    def test_delete_and_archive(self):
        a = self._account('DEL_ME', '1001')
        r = self.client.delete(f"/api/config/accounts/{a['id']}?version={a['version']}")
        self.assertEqual(r.status_code, 204, r.text)
        with self.connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM md_collection_plans WHERE code='DEL_ME_EURUSD'").fetchone())
        b = self._account('KEEP_ME', '1002')
        with self.connect() as db:
            db.execute("INSERT INTO cfg_credential_use(credential_id, owner, owner_id, used_by, purpose) VALUES (gen_random_uuid(), 'account', %s, 'alice', 'LOGIN')", (b['id'],))
        r = self.client.delete(f"/api/config/accounts/{b['id']}?version={b['version']}")
        self.assertEqual(r.status_code, 409)
        self.assertRegex(r.json()['detail'], r'KEEP_ME cannot be deleted: it has 1 login\(s\) in the audit trail.*Archive it instead')
        r = self.client.post(ARCHIVE_URL.format(id=b['id']), json={'version': b['version'], 'reason': 'test done'})
        self.assertEqual(r.status_code, 200, r.text)
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT status FROM md_collection_plans WHERE code='KEEP_ME_EURUSD'").fetchone()['status'], 'ARCHIVED')
