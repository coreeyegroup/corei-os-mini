"""Plain-language reasons for broker and collector errors, with the next step.
Used by the screens (problem cards) and by the collector (back off on errors that
retrying cannot fix)."""
from __future__ import annotations

import re

# MT5 login refusals by number (MT5 server return codes, as the mtapi SDK's Msg enumeration names them).
# The SDK release in use names codes up to 1030; a higher number is a code added to MT5 after that release.
LOGIN_CODES = {
    1001: ('Login or password rejected', 'INVALID_ACCOUNT: check the login number and the password.'),
    1002: ('Account disabled at the broker', 'ACCOUNT_DISABLED: contact the broker.'),
    1003: ('Broker requires advanced authorisation', 'ADVANCED_AUTHORIZATION: the account needs certificate or OTP login, which the SDK does not do.'),
    1012: ("COREI's IP is not allowed", "UNALLOWED_IP_ADDRESS: ask the broker to allow the server's outbound IP (Setup › Account readiness)."),
    1018: ('Broker server busy', 'SERVER_BUSY: retried later.'),
    1020: ('Account unknown on this server', 'UNKNOWN_ACCOUNT: the login belongs to another server of the broker (or a demo account expired).'),
    1025: ('Demo logins disabled', 'DEMO_DISABLED: the server does not accept demo accounts.'),
    1026: ('Password must be changed', 'PASSWORD_MUST_CHANGED: log in once with the MT5 terminal, change the password, save the new one in COREI.'),
    1027: ('One-time password required', 'INVALID_ONETIME_PASSWORD: the account has OTP / two-factor login on; turn it off for API access.'),
    1028: ('Secret key required', 'SECRET_KEY_REQUIRED: the account has OTP / two-factor login on; turn it off for API access.'),
}
SDK_LAST_CODE = 1030


def _login_code(error: str) -> dict | None:
    m = re.search(r'ServerException: (\d{4})\b', error)
    if not m:
        return None
    code = int(m.group(1))
    if code in LOGIN_CODES:
        title, detail = LOGIN_CODES[code]
        return {'title': title, 'detail': detail, 'permanent': code != 1018, 'raw': error}
    if code > SDK_LAST_CODE and code < 2000:
        return {'title': f'Server uses a login step newer than the MT5 SDK (code {code})',
                'detail': f'The server answered login code {code}; the installed MT5 SDK only knows codes up to {SDK_LAST_CODE}, so the server '
                          'expects a newer login protocol than the SDK speaks (presenting a newer build number is not enough). Update the MT5 SDK '
                          '(mt5api.dll) to the vendor\'s current release and run scripts/setup-broker-accounts.sh. MetaQuotes-Demo is the '
                          'strictest server; a broker\'s own demo server (VT Markets, IC Markets) usually still accepts the current SDK.',
                'permanent': True, 'raw': error}
    return None


RULES = [
    (r'ServerException: \d+.*with client build \d+', True, 'Broker refused the login (unlisted code)',
     'The newer MT5 build got past the version check, then the server refused the login with a code this MT5 SDK does not name '
     '(the number is shown below). Usually the SDK is older than the server protocol and must be updated; it can also be a '
     'blocked login after many attempts. COREI now stops at the first refusal and retries every 5 minutes. Log in once with the '
     'official MT5 terminal to confirm the account works, then update the SDK if the terminal logs in.'),
    (r'ServerException: \d+', True, 'Broker refused the login (unlisted code)',
     'The server refused the login with a code this MT5 SDK does not name (the number is shown below). Log in once with the '
     'official MT5 terminal: if it works there, the MT5 SDK needs updating; if not, the account or password is the problem. '
     'COREI retries every 5 minutes, one login at a time, to avoid the broker blocking the account.'),
    (r'OLD_VERSION.*no client-build setting', True, 'Broker refused the connector version',
     "The broker requires a newer MT5 version than this MT5 SDK can present, and the SDK has no setting to change it. "
     'The SDK must be updated to a newer release from its vendor, then run scripts/setup-broker-accounts.sh.'),
    (r'OLD_VERSION', True, 'Broker refused the connector version',
     "The broker requires a newer MT5 version than the connector presented. COREI retries automatically with current MT5 client builds "
     '(6241 down to 5370) when the SDK allows it. MetaQuotes\' own demo servers (MetaQuotes-Demo) accept only the newest build. If it is still refused, set COREI_MT5_CLIENT_BUILD to the build the broker\'s own '
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
    coded = _login_code(error)
    if coded:
        return coded
    for pattern, permanent, title, detail in RULES:
        if re.search(pattern, error, re.I):
            return {'title': title, 'detail': detail, 'permanent': permanent, 'raw': error}
    return {'title': 'Connection problem', 'detail': error, 'permanent': False, 'raw': error}
