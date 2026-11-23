"""Broker adapters (Stage 7 · Execution intelligence): how a COREI order reaches a real account, demo or live.

Why it exists: the order engine owns order state; the adapters only carry a request to the broker and bring back
exactly what the broker said. Two adapters exist: MT5 through the COREI terminal bridge (the official terminal executes;
demo and live accounts alike) and Upstox through its order API (Indian exchanges).
Purpose: say whether an account can take real orders and through what (capability); send, pre-check, cancel and
modify orders; read the broker's book (money, positions, working orders, deals) and look an order up by its tag.
Failure classes (the core of order safety):
  NotSent  — the request certainly did not reach the broker (no route, TLS or token refused, nothing written):
             the next route may be tried, and the order may be rejected without doubt.
  Lost     — the request was written and no answer came back: the outcome is UNKNOWN; nothing is sent again;
             the order is found later by its tag.
Routes: an MT5 account with several bridge routes (Setup › account › routes) uses them in priority order, moving on
only on NotSent; a Lost answer never moves to another route.
Must never: decide whether an order may be sent (risk and the engine do), keep a password or token beyond the call,
log one, or retry a request whose outcome is unknown.
Dependencies: config_store (credentials, accounts), account_routes (routes and tokens), md.bridge_client (TLS with a
pinned certificate), broker_login (Upstox day token). Consumers: trading.execution, trading.posttrade.
Architectural position: Stage 7, the last step before the broker.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from typing import Any
from uuid import UUID

from .. import config_store as store
from ..md.bridge_client import BridgeError, BridgeTransport, bridge_of

ADAPTERS = ('MT5_BRIDGE', 'UPSTOX')


class NotSent(Exception):
    """The request did not reach the broker."""


class Lost(Exception):
    """The request was sent and no answer came back: outcome unknown."""


class Throttled(NotSent):
    """The connection's order rate is used up: nothing was sent; try again shortly."""


def throttle(account_id, wait_up_to: float = 0.0, clock=None) -> None:
    """Take one slot of the account's connection order rate before an outbound send, amend or cancel; raises
    Throttled (nothing sent) when none frees within `wait_up_to` seconds. Cancels pass a wait (they remove risk)."""
    import time as _time
    from .. import rate_limit
    with store.transaction('read') as db:
        row = db.execute('SELECT c.id, c.max_orders_per_second, c.max_order_burst FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id '
                         'WHERE a.id=%s', (str(account_id),)).fetchone()
    if not row:
        raise NotSent('ACCOUNT_NOT_FOUND')
    limiter = rate_limit.order_limiter(str(row['id']), row['max_orders_per_second'], row['max_order_burst'])
    deadline = (clock or _time.monotonic)() + wait_up_to
    while True:
        wait = limiter.try_acquire()
        if wait <= 0:
            return
        if (clock or _time.monotonic)() + wait > deadline:
            raise Throttled(f'RATE_LIMITED: {limiter.limit} orders per second for this connection are in use; nothing was sent')
        _time.sleep(min(wait, 0.25))


class Refused(Exception):
    """The broker (or the bridge on its behalf) answered no — the answer is in the message."""


def tag_of(client_order_id: str) -> str:
    """What the broker sees: 20 characters (MT5 comments and Upstox tags both fit), unique per order."""
    return client_order_id[:20]


# ---------- which accounts can trade, and how ----------

def _account(account_id) -> dict:
    with store.transaction('read') as db:
        account = store.get_record(db, 'accounts', UUID(str(account_id)))
        if not account:
            raise NotSent('ACCOUNT_NOT_FOUND')
        conn = db.execute('SELECT settings, algo_name FROM cfg_connections WHERE id=%s', (account['connection_id'],)).fetchone() or {}
        account['connection_settings'] = conn.get('settings') or {}
        account['algo_name'] = conn.get('algo_name')
    return account


def mt5_targets(account: dict) -> list[dict]:
    """The account as seen through each enabled bridge route (priority order), else through its server's bridge."""
    from .. import account_routes
    out = [account_routes.apply(dict(account), r) for r in account_routes.enabled_routes(account['id']) if r['method'] == 'TERMINAL_BRIDGE']
    if not out and account.get('adapter') == 'TERMINAL_BRIDGE' and bridge_of(account.get('connection_settings') or {}):
        out.append(account)
    return out


def capability(db, account_id) -> dict:
    """{adapter, reasons}: which adapter would carry real orders for this account, or why none can."""
    row = db.execute("""SELECT a.id, a.code, a.trading_permission, c.platform, c.adapter, c.purpose, c.environment, c.settings->>'api_base_url' AS api_base,
                               (SELECT count(*) FROM cfg_account_routes r WHERE r.account_id=a.id AND r.enabled AND r.method='TERMINAL_BRIDGE') AS bridges,
                               c.settings->'bridge' AS bridge
                        FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id WHERE a.id=%s""", (str(account_id),)).fetchone()
    if not row:
        return {'adapter': None, 'reasons': ['ACCOUNT_NOT_FOUND']}
    reasons = []
    if row['trading_permission'] != 'TRADING':
        reasons.append('ACCOUNT_NOT_PERMITTED_TO_TRADE: the account is set to data only (Setup › account › trading permission)')
    if row['purpose'] == 'MARKET_DATA':
        reasons.append('CONNECTION_DATA_ONLY: the connection is for market data only')
    adapter = None
    if row['platform'] == 'MT5':
        if row['bridges'] or (row['adapter'] == 'TERMINAL_BRIDGE' and row['bridge']):
            adapter = 'MT5_BRIDGE'
        else:
            reasons.append('ORDER_NEEDS_BRIDGE: MT5 orders go through the COREI terminal bridge; add a bridge route to this account '
                           '(Setup › Bridge nodes, then the account › Routes)')
    elif row['platform'] == 'REST_API' and upstox_host(row['api_base']):
        adapter = 'UPSTOX'
        if row['environment'] != 'LIVE':
            reasons.append('UPSTOX_LIVE_ACCOUNT_REQUIRED: use Paper for practice; the connected Upstox order route is live')
    else:
        reasons.append(f"ORDER_ADAPTER_NOT_AVAILABLE: no order adapter for {row['platform']}")
    return {'adapter': adapter if not reasons else None, 'wanted': adapter, 'reasons': reasons}


# ---------- MT5 through the bridge ----------

MIN_ORDER_BRIDGE = (0, 43, 0)
_versions: dict = {}


def _too_old(bridge: dict, token: str) -> str | None:
    """Ask the bridge its version (ping: no login, no terminal) before an order or book request; cached 10 minutes.
    None when it is new enough; else why not — nothing has been sent."""
    import time
    key = (bridge['host'], int(bridge['port']), bridge['fingerprint'])
    hit = _versions.get(key)
    if hit and time.monotonic() - hit[1] < 600:
        version = hit[0]
    else:
        try:
            t = BridgeTransport(bridge['host'], int(bridge['port']), bridge['fingerprint'], timeout=10)
        except BridgeError as exc:
            return str(exc)
        try:
            t.send({'token': token, 'mode': 'ping'})
            t.sock.settimeout(10)
            answer = json.loads(next(t.lines(), b'') or b'{}')
        except (OSError, ValueError):
            answer = {}
        finally:
            t.close()
        if answer.get('t') == 'failed':
            return next((a.get('error') for a in answer.get('attempts') or [] if a.get('error')), 'BRIDGE_REFUSED')
        version = str(answer.get('version') or '0') if answer.get('t') == 'pong' else '0'
        _versions[key] = (version, time.monotonic())
    parts = tuple(int(x) for x in version.split('.')[:3] if x.isdigit())
    if parts < MIN_ORDER_BRIDGE:
        return (f"BRIDGE_TOO_OLD: the bridge at {bridge['host']}:{bridge['port']} is {version if version != '0' else 'older than 0.42'}; orders and "
                f"broker reads need {'.'.join(map(str, MIN_ORDER_BRIDGE))} or later. Update it on the machine: "
                'install-corei-bridge.ps1 -Update -Name <node> (Setup › Bridge nodes shows the command).')
    return None


def mt5_call(account_id, actor: str, mode: str, payload: dict, purpose: str, timeout: float = 30) -> dict:
    """One request to the bridge with the account's master password; routes in priority order on NotSent."""
    from .. import account_routes
    account = _account(account_id)
    targets = mt5_targets(account)
    if not targets:
        raise NotSent('ORDER_NEEDS_BRIDGE: this account has no bridge route')
    if not str(account['external_account_id']).isdigit():
        raise NotSent('MT5_LOGIN_NOT_NUMERIC')
    try:
        secret = store.use_credential('accounts', UUID(str(account['id'])), actor, purpose, 'FULL')
    except Exception as exc:  # noqa: BLE001 - no credential: nothing was sent
        raise NotSent(f'NO_PASSWORD: {getattr(exc, "detail", exc)}') from None
    errors: list[str] = []
    try:
        for target in targets:
            bridge = target['connection_settings']['bridge']
            route = target.get('route')
            try:
                token = account_routes.bridge_token(target, actor, purpose)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{(route or {}).get('label', 'server')}: no token ({getattr(exc, 'detail', exc)})")
                continue
            old = _too_old(bridge, token['token'])
            if old:  # an older bridge would take this request for a price stream: never send it an order
                token.clear()
                errors.append(f"{(route or {}).get('label', 'server')}: {old}")
                if route:
                    account_routes.record(route['id'], False, old)
                continue
            request = {'token': token['token'], 'mode': mode, 'login': int(account['external_account_id']), 'password': secret['password'],
                       'server': account['connection_settings'].get('server_name'), **payload}
            token.clear()
            try:
                transport = BridgeTransport(bridge['host'], int(bridge['port']), bridge['fingerprint'], timeout=10)
            except BridgeError as exc:
                request.clear()
                errors.append(str(exc))
                if route:
                    account_routes.record(route['id'], False, str(exc))
                continue
            try:
                try:
                    transport.send(request)
                except (OSError, ValueError) as exc:  # nothing complete reached the bridge: it acts only on a whole line
                    errors.append(f'send failed: {exc}')
                    continue
                finally:
                    request.clear()
                try:
                    transport.sock.settimeout(timeout)
                    line = next(transport.lines(), b'')
                except (OSError, ValueError) as exc:
                    raise Lost(f'BRIDGE_NO_ANSWER: sent through {bridge["host"]}:{bridge["port"]}, then {type(exc).__name__}: {exc}'[:300]) from None
            finally:
                transport.close()
            try:
                reply = json.loads(line) if line.strip() else None
            except ValueError:
                reply = None
            if reply is None:
                raise Lost(f'BRIDGE_CLOSED: sent through {bridge["host"]}:{bridge["port"]}, the bridge closed without an answer')
            if reply.get('t') == 'failed':
                text = reply.get('error') or next((a.get('error') for a in reply.get('attempts') or [] if a.get('error')), 'failed')
                errors.append(text)
                if route:
                    account_routes.record(route['id'], False, text)
                continue  # the bridge refused before acting (token, login, terminal busy): nothing was sent
            if route:
                account_routes.record(route['id'], True)
            reply['via'] = {'host': bridge['host'], 'port': int(bridge['port']), 'route': (route or {}).get('label')}
            return reply
    finally:
        secret.clear()
    raise NotSent('; '.join(errors)[:600] or 'NO_ROUTE_ANSWERED')


def mt5_order(order: dict, symbol: str, magic: int, deviation: int, net: bool = True) -> dict:
    """COREI order → the bridge's order fields (decimal strings; the bridge converts at the last boundary)."""
    kind = order['order_type']
    price = order.get('limit_price') if kind == 'LIMIT' else order.get('stop_price') if kind == 'STOP' else None
    return {'tag': order['broker_tag'], 'symbol': symbol, 'side': order['side'], 'volume': str(Decimal(order['quantity']) - Decimal(order.get('filled_qty') or 0)),
            'order_type': kind, 'price': str(price) if price else None, 'sl': str(order['stop_loss']) if order.get('stop_loss') else None,
            'tp': str(order['take_profit']) if order.get('take_profit') else None, 'time_in_force': order.get('time_in_force'),
            'magic': magic, 'deviation': deviation, 'net': net, 'reduce_only': bool(order.get('reduce_only'))}


# ---------- Upstox ----------

UPSTOX_DATA = 'https://api.upstox.com'
UPSTOX_ORDERS = 'https://api-hft.upstox.com'


def upstox_host(value) -> bool:
    try:
        url = urllib.parse.urlsplit(str(value or ''))
        return (url.scheme == 'https' and url.netloc in ('api.upstox.com', 'api-hft.upstox.com')
                and url.path.rstrip('/') in ('', '/v2', '/v3') and not url.query and not url.fragment)
    except ValueError:
        return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _upstox_quantity(value) -> int:
    quantity = Decimal(str(value))
    if not quantity.is_finite() or quantity != quantity.to_integral_value() or not 1 <= quantity <= 2_147_483_647:
        raise NotSent('UPSTOX_INVALID_QUANTITY: use whole broker units within the supported range')
    return int(quantity)


def _valid_order_id(value) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value))


class Upstox:
    """Use V3 for order writes and the documented V2 account and reconciliation reads."""

    def __init__(self, account_id, actor: str, purpose: str):
        from ..broker_login import session_token
        account = _account(account_id)
        settings = account['connection_settings']
        if not upstox_host(settings.get('api_base_url')):
            raise NotSent('UPSTOX_INVALID_HOST: select an official Upstox connection URL')
        # Exchange-registered algo, sent as X-Algo-Name: the connection setting, else the Wave 0 column.
        self.algo_name = (settings.get('upstox_algo_name') or account.get('algo_name') or '').strip() or None
        try:
            self._auth = 'Bearer ' + session_token(UUID(str(account_id)), actor, purpose)
        except Exception as exc:  # noqa: BLE001
            raise NotSent(f'BROKER_LOGIN_NEEDED_TODAY: {getattr(exc, "detail", exc)}') from None

    def call(self, method: str, path: str, params: dict | None = None, body: dict | None = None, sending: bool = False) -> Any:
        url = (UPSTOX_ORDERS if sending else UPSTOX_DATA) + path + ('?' + urllib.parse.urlencode(params) if params else '')
        data = json.dumps(body, allow_nan=False).encode() if body is not None else None
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json',
                   'Authorization': self._auth, 'User-Agent': 'COREI-OS-MINI/1.0'}
        if sending and self.algo_name:
            headers['X-Algo-Name'] = self.algo_name
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(req, timeout=20) as resp:  # noqa: S310 — official HTTPS origin only
                out = json.loads(resp.read(5_000_000) or b'{}')
        except urllib.error.HTTPError as exc:
            detail = ''
            try:
                payload = json.loads(exc.read(100_000))
                detail = ', '.join(str(item.get('errorCode', '')) for item in payload.get('errors', [])
                                   if isinstance(item, dict) and re.fullmatch(r'UDAPI[0-9]+', str(item.get('errorCode', ''))))
            except (ValueError, AttributeError, TypeError, OSError):
                pass
            finally:
                exc.close()
            if sending and (not detail or exc.code in (408, 409, 429) or not 400 <= exc.code < 500):
                raise Lost(f'UPSTOX_UNCERTAIN_HTTP_{exc.code}: reconcile the broker order before taking another action') from None
            raise Refused(f'UPSTOX_{exc.code}' + (f': {detail}' if detail else '')) from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            if sending and not isinstance(getattr(exc, 'reason', None), ConnectionRefusedError):
                raise Lost(f'UPSTOX_NO_ANSWER: {type(exc).__name__}') from None
            raise NotSent(f'UPSTOX_UNREACHABLE: {type(exc).__name__}') from None
        if not isinstance(out, dict) or out.get('status') != 'success' or 'data' not in out:
            if sending:
                raise Lost('UPSTOX_UNRECOGNIZED_RESPONSE: look up the broker order')
            raise Refused('UPSTOX_UNRECOGNIZED_RESPONSE')
        return out['data']

    def place(self, order: dict, instrument_key: str, product: str) -> dict:
        if not re.fullmatch(r'[A-Z0-9_]+\|[^|\r\n]{1,100}', instrument_key):
            raise NotSent('UPSTOX_INSTRUMENT_KEY_REQUIRED: map the account instrument to its Upstox instrument key')
        kind = {'MARKET': 'MARKET', 'LIMIT': 'LIMIT', 'STOP': 'SL-M'}[order['order_type']]
        body = {'quantity': _upstox_quantity(order['quantity']), 'product': product,
                'validity': 'IOC' if order.get('time_in_force') == 'IOC' else 'DAY',
                'price': float(order['limit_price']) if kind == 'LIMIT' else 0,
                'tag': order['broker_tag'], 'instrument_token': instrument_key, 'order_type': kind,
                'transaction_type': order['side'], 'disclosed_quantity': 0,
                'trigger_price': float(order['stop_price']) if kind == 'SL-M' else 0, 'is_amo': False, 'slice': False}
        if kind in ('MARKET', 'SL-M'):
            body['market_protection'] = -1
        data = self.call('POST', '/v3/order/place', body=body, sending=True)
        ids = data.get('order_ids') if isinstance(data, dict) else None
        if not isinstance(ids, list) or len(ids) != 1 or not _valid_order_id(ids[0]):
            raise Lost('UPSTOX_ORDER_ID_UNCERTAIN: reconcile using the client order tag')
        return {'order_id': ids[0]}

    def cancel(self, broker_order_id: str) -> dict:
        if not _valid_order_id(broker_order_id):
            raise NotSent('UPSTOX_ORDER_ID_REQUIRED')
        data = self.call('DELETE', '/v3/order/cancel', params={'order_id': broker_order_id}, sending=True)
        if not isinstance(data, dict) or data.get('order_id') != broker_order_id:
            raise Lost('UPSTOX_CANCEL_UNCERTAIN: look up the broker order')
        return data

    def modify_price(self, order: dict, changes: dict) -> dict:
        ticket = order.get('broker_order_id')
        if not _valid_order_id(ticket):
            raise NotSent('UPSTOX_ORDER_ID_REQUIRED')
        kind = {'LIMIT': 'LIMIT', 'STOP': 'SL-M'}[order['order_type']]
        body = {'order_id': ticket, 'order_type': kind,
                'validity': 'IOC' if order.get('time_in_force') == 'IOC' else 'DAY',
                'price': float(changes['limit_price']) if kind == 'LIMIT' else 0,
                'trigger_price': float(changes['stop_price']) if kind == 'SL-M' else 0}
        if kind == 'SL-M':
            body['market_protection'] = -1
        data = self.call('PUT', '/v3/order/modify', body=body, sending=True)
        if not isinstance(data, dict) or data.get('order_id') != ticket:
            raise Lost('UPSTOX_MODIFICATION_UNCERTAIN: look up the broker order')
        return data

    def history(self, tag: str | None = None, order_id: str | None = None) -> list[dict]:
        params = {'order_id': order_id} if order_id else {'tag': tag}
        try:
            return self.call('GET', '/v2/order/history', params=params) or []
        except Refused as exc:
            if 'UPSTOX_400' in str(exc) or 'UPSTOX_404' in str(exc):
                return []  # not found at the broker
            raise

    def trades(self, order_id: str) -> list[dict]:
        return self.call('GET', '/v2/order/trades', params={'order_id': order_id}) or []

    def positions(self) -> list[dict]:
        return self.call('GET', '/v2/portfolio/short-term-positions') or []

    def funds(self) -> dict:
        return self.call('GET', '/v2/user/get-funds-and-margin') or {}

    def day_orders(self) -> list[dict]:
        return self.call('GET', '/v2/order/retrieve-all') or []

    def day_trades(self) -> list[dict]:
        return self.call('GET', '/v2/order/trades/get-trades-for-day') or []


UPSTOX_STATE = {'complete': 'FILLED', 'rejected': 'REJECTED', 'cancelled': 'CANCELLED', 'open': 'ACKNOWLEDGED', 'trigger pending': 'ACKNOWLEDGED',
                'after market order req received': 'ACKNOWLEDGED', 'modify pending': 'ACKNOWLEDGED', 'cancel pending': 'CANCEL_PENDING',
                'put order req received': 'SUBMITTED', 'validation pending': 'SUBMITTED', 'open pending': 'SUBMITTED', 'not modified': 'ACKNOWLEDGED',
                'modify validation pending': 'ACKNOWLEDGED', 'modified': 'ACKNOWLEDGED', 'cancelled after market order': 'CANCELLED'}
