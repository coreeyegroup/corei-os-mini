"""Plain-language reasons for broker and collector errors, with the next step.
Used by the screens (problem cards) and by the collector (back off on errors that
retrying cannot fix)."""
from __future__ import annotations

import re

RULES = [
    (r'OLD_VERSION.*no client-build setting', True, 'Broker refused the connector version',
     "The broker requires a newer MT5 version than this MT5 SDK can present, and the SDK has no setting to change it. "
     'The SDK must be updated to a newer release from its vendor, then run scripts/setup-broker-accounts.sh.'),
    (r'OLD_VERSION', True, 'Broker refused the connector version',
     "The broker requires a newer MT5 version than the connector presented. COREI retries automatically with current MT5 client builds "
     '(5430, 5370, 5200, 4755) when the SDK allows it. If it is still refused, set COREI_MT5_CLIENT_BUILD to the build the broker\'s own '
     'MT5 terminal shows (Help › About) in /etc/corei-os-mini/workstation.env and redeploy, or update the MT5 SDK.'),
    (r'INVALID_ACCOUNT|Invalid account|INVALID_PASSWORD|Authorization|AUTH', True, 'Login or password rejected',
     'The broker rejected the login. Check the MT5 login number and save the investor (read-only) password again on the account.'),
    (r'ACCOUNT_DISABLED|disabled|blocked', True, 'Account disabled at the broker',
     'The broker reports this account as disabled or blocked. Contact the broker.'),
    (r'No address|routing', True, 'No address to connect to',
     "Add the broker's server addresses on the connection, or pick a routing mode on the account."),
    (r'not built yet', True, 'Platform not supported yet',
     'Collection for this platform is not built yet. The plan is kept and starts when it is.'),
    (r'timeout|timed out|did not finish|ConnectException|SocketException|refused|unreachable|reset', False, 'Broker server not reachable',
     'The addresses did not answer in time. COREI keeps retrying through the account’s addresses. Check the latency test on the connection.'),
]


def explain(error: str | None) -> dict | None:
    if not error:
        return None
    for pattern, permanent, title, detail in RULES:
        if re.search(pattern, error, re.I):
            return {'title': title, 'detail': detail, 'permanent': permanent, 'raw': error}
    return {'title': 'Connection problem', 'detail': error, 'permanent': False, 'raw': error}
