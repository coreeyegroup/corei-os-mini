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
        account['connection_settings'] = (db.execute('SELECT settings FROM cfg_connections WHERE id=%s', (account['connection_id'],)).fetchone()
                                          or {}).get('settings') or {}
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
    row = db.execute("""SELECT a.id, a.code, a.trading_permission, c.platform, c.adapter, c.purpose, c.settings->>'api_base_url' AS api_base,
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
    elif row['platform'] == 'REST_API' and 'upstox' in str(row['api_base'] or ''):
        adapter = 'UPSTOX'
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

class Upstox:
    """Upstox v2 order API with today's access token. Quantities are shares (or contracts × lot)."""

    def __init__(self, account_id, actor: str, purpose: str):
        from ..broker_login import session_token
        account = _account(account_id)
        self.base = str(account['connection_settings'].get('api_base_url') or 'https://api.upstox.com').rstrip('/').removesuffix('/v2')
        try:
            self._auth = 'Bearer ' + session_token(UUID(str(account_id)), actor, purpose)
        except Exception as exc:  # noqa: BLE001
            raise NotSent(f'BROKER_LOGIN_NEEDED_TODAY: {getattr(exc, "detail", exc)}') from None

    def call(self, method: str, path: str, params: dict | None = None, body: dict | None = None, sending: bool = False) -> Any:
        url = self.base + path + ('?' + urllib.parse.urlencode(params) if params else '')
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={'Accept': 'application/json', 'Content-Type': 'application/json',
                                                                            'Authorization': self._auth, 'User-Agent': 'COREI-OS-MINI/1.0'})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - https base from the connection
                out = json.loads(resp.read(5_000_000) or b'{}')
        except urllib.error.HTTPError as exc:
            try:
                body_ = json.loads(exc.read(100_000))
                detail = '; '.join(f"{e.get('errorCode', '')} {e.get('message', '')}".strip() for e in body_.get('errors') or [])
            except (ValueError, AttributeError):
                detail = ''
            raise Refused(f'UPSTOX_{exc.code}: {detail or exc.reason}'[:400]) from None  # an HTTP answer is an answer
        except (urllib.error.URLError, OSError, ValueError) as exc:
            if sending and not isinstance(getattr(exc, 'reason', None), (ConnectionRefusedError,)):
                raise Lost(f'UPSTOX_NO_ANSWER: {exc}'[:300]) from None
            raise NotSent(f'UPSTOX_UNREACHABLE: {exc}'[:300]) from None
        return out.get('data') if isinstance(out, dict) else out

    def place(self, order: dict, instrument_key: str, product: str) -> dict:
        kind = {'MARKET': 'MARKET', 'LIMIT': 'LIMIT', 'STOP': 'SL-M'}[order['order_type']]
        body = {'quantity': int(Decimal(order['quantity'])), 'product': product, 'validity': 'IOC' if order.get('time_in_force') == 'IOC' else 'DAY',
                'price': float(order['limit_price']) if order.get('limit_price') else 0, 'tag': order['broker_tag'], 'instrument_token': instrument_key,
                'order_type': kind, 'transaction_type': order['side'], 'disclosed_quantity': 0,
                'trigger_price': float(order['stop_price']) if order.get('stop_price') else 0, 'is_amo': False}
        return self.call('POST', '/v2/order/place', body=body, sending=True) or {}

    def cancel(self, broker_order_id: str) -> dict:
        return self.call('DELETE', '/v2/order/cancel', params={'order_id': broker_order_id}, sending=True) or {}

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
