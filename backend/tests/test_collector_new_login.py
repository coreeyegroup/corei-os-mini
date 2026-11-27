"""A new daily broker login (Upstox) restarts a refused market-data source at once (release 0.66.2).

Evidence for: the collector's setup stamp changes when a fresh session token is saved for the account, so a source
refused with "Invalid token" (wait 5 minutes) retries within its 30-second check instead of waiting out the back-off.
"""
from __future__ import annotations

import importlib
import importlib.util
from datetime import datetime, timedelta, timezone

te = importlib.import_module('test_execution') if importlib.util.find_spec('test_execution') else importlib.import_module('tests.test_execution')
from corei_os_mini.md import collector  # noqa: E402


class NewLogin(te.Execution):
    test_sending_disabled_during_precheck = test_real_orders_on_demo_every_path = test_four_eyes_on_live = None
    test_risk_extras = test_portfolio_extras = test_optimise_walk_forward_and_drift = None

    def test_new_session_token_changes_the_setup_stamp(self):
        _connection, account, _b, _ins = self.real_account('LIVE')
        w = collector.SourceWorker.__new__(collector.SourceWorker)
        w.account_id = str(account['id'])
        before = w._config_stamp()
        self.assertIsNotNone(before)
        now = datetime.now(timezone.utc)
        with self.connect() as db:
            db.execute("INSERT INTO cfg_session_tokens(account_id, ciphertext, key_id, obtained_at, expires_at, obtained_by) "
                       "VALUES (%s, '\\x00', '', %s, %s, 'alice')", (account['id'], now, now + timedelta(hours=8)))
        first = w._config_stamp()
        self.assertNotEqual(first, before, 'a first daily login restarts the source')
        with self.connect() as db:
            db.execute('UPDATE cfg_session_tokens SET obtained_at=%s WHERE account_id=%s', (now + timedelta(minutes=5), account['id']))
        self.assertNotEqual(w._config_stamp(), first, 'a later login (after the token was revoked) restarts it again')
