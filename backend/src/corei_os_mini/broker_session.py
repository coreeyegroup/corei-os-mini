"""Read real data from an MT5 account: one read-only login through the installed SDK.

Owns: logging in with the stored password (investor password preferred, master only if
no investor password is stored), walking the account's addresses in its routing order,
and storing what the broker says:
  * account facts (margin mode, leverage, group, stop-out, broker caps) -> cfg_accounts,
    marked BROKER, audited when they change;
  * server clock offset and the address that answered -> cfg_connection_status;
  * every symbol's trading rules -> cfg_symbol_specs (normalised spec + the raw facts);
  * the whole read (attempts, facts, counts, errors) -> cfg_broker_reads.

The password reaches the reader on stdin, never on its command line, and every use is
logged through config_store.use_credential. The reader never sends, changes or cancels
orders. Field names are matched by reflection on the SDK, so the raw facts are always
kept even when a field cannot be normalised; those symbols are listed as unmapped.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from . import config_store as store
from .md.bridge_client import BridgeError, BridgeTransport, bridge_of
from .broker_catalog import _parser
from .order_checks import SymbolSpec, filling_modes_from_flags

_busy: set[str] = set()
_lock = threading.Lock()

MARGIN_MODES = {'hedg': 'HEDGING', 'netting': 'NETTING', 'exchange': 'EXCHANGE'}


def _pick(facts: dict[str, Any], *fragments: str, numeric: bool = False) -> Any:
    """First fact whose name ends with or contains one of the fragments (case-insensitive)."""
    lowered = {k.lower(): v for k, v in facts.items()}
    for fragment in fragments:
        f = fragment.lower()
        for key, value in lowered.items():
            if key.split('.')[-1] == f or key.endswith(f):
                if numeric and (isinstance(value, bool) or not isinstance(value, (int, float))):
                    continue
                return value
    return None


def account_facts(raw: dict[str, Any]) -> dict[str, Any]:
    method = _pick(raw, 'AccountMethod', 'MarginMode', 'Method', 'AccMethod')
    margin_mode = next((mode for key, mode in MARGIN_MODES.items() if isinstance(method, str) and key in method.lower()), None)
    leverage = _pick(raw, 'AccountLeverage', 'Leverage', numeric=True)
    group = _pick(raw, 'Account.Group', 'AccountGroup', 'Group')
    stop_out = _pick(raw, 'MarginStopOut', 'StopOut', 'StopOutLevel', 'MarginSOSO', numeric=True)
    max_orders = _pick(raw, 'MaxOrders', 'LimitOrders', numeric=True)
    max_positions = _pick(raw, 'MaxPositions', 'LimitPositions', numeric=True)
    return {
        'margin_mode': margin_mode,
        'leverage': int(leverage) if leverage and leverage > 0 else None,
        'broker_group': str(group)[:120] if isinstance(group, str) and group.strip() else None,
        'stop_out_level': float(stop_out) if stop_out and stop_out > 0 else None,
        'max_open_orders': int(max_orders) if max_orders and max_orders > 0 else None,
        'max_open_positions': int(max_positions) if max_positions and max_positions > 0 else None,
        'balance': _pick(raw, 'AccountBalance', 'Balance', numeric=True),
        'equity': _pick(raw, 'AccountEquity', 'Equity', numeric=True),
        'currency': _pick(raw, 'AccountCurrency', 'Currency'),
    }


TRADE_MODES = {'full': 'FULL', 'long': 'LONG_ONLY', 'short': 'SHORT_ONLY', 'close': 'CLOSE_ONLY', 'disabled': 'DISABLED'}


def _lots(value: Any, scale: Decimal) -> str:
    return format((Decimal(str(value)) * scale).normalize(), 'f')


def symbol_spec(raw: dict[str, Any]) -> tuple[dict | None, str | None]:
    """Normalise one symbol's SDK facts into a SymbolSpec; returns (spec, reason-if-not)."""
    digits = _pick(raw, 'Digits', numeric=True)
    vmin = _pick(raw, 'MinVolume', 'VolumeMin', 'MinLots', 'LotsMin', numeric=True)
    vmax = _pick(raw, 'MaxVolume', 'VolumeMax', 'MaxLots', 'LotsMax', numeric=True)
    vstep = _pick(raw, 'VolumeStep', 'StepVolume', 'LotsStep', 'StepLots', numeric=True)
    if None in (digits, vmin, vmax, vstep) or min(vmin, vmax, vstep) <= 0:
        return None, 'volume or digits not found'
    # MT5 servers carry volumes as integer units of 1e-8 lot; terminals show lots.
    scale = Decimal('0.00000001') if min(vmin, vstep) >= 10_000 else Decimal(1)
    flags = _pick(raw, 'FillPolicy', 'FillingMode', 'FillFlags', 'Filling', numeric=True)
    execution = _pick(raw, 'ExecutionMode', 'ExecMode', 'TradeExecution')
    trade_mode = _pick(raw, 'TradeMode')
    spec = {
        'digits': int(digits),
        'volume_min': _lots(vmin, scale), 'volume_max': _lots(vmax, scale), 'volume_step': _lots(vstep, scale),
        'filling_modes': filling_modes_from_flags(int(flags or 0), isinstance(execution, str) and 'market' in execution.lower()),
        'stops_level_points': max(int(_pick(raw, 'StopsLevel', 'StopLevel', numeric=True) or 0), 0),
        'freeze_level_points': max(int(_pick(raw, 'FreezeLevel', numeric=True) or 0), 0),
        'trade_mode': next((v for k, v in TRADE_MODES.items() if isinstance(trade_mode, str) and k in trade_mode.lower()), 'FULL'),
        'contract_size': _pick(raw, 'ContractSize', 'TradeContractSize', numeric=True) or None,
        'margin_currency': _pick(raw, 'MarginCurrency'),
        'volume_scale': format(scale.normalize(), 'f'),
    }
    try:
        return SymbolSpec.model_validate(spec).model_dump(mode='json'), None
    except ValidationError as exc:
        return None, exc.errors()[0].get('msg', 'invalid')[:120]


def _offset_minutes(server_time: str | None, utc_now: str | None) -> int | None:
    if not server_time or not utc_now:
        return None
    delta = datetime.fromisoformat(server_time) - datetime.fromisoformat(utc_now)
    minutes = int(round(delta.total_seconds() / 60 / 15) * 15)
    # Broker clocks sit within a day of UTC; anything else is a stale timestamp, not a clock.
    return minutes if abs(minutes) <= 24 * 60 else None


def _run_session(request: dict, timeout: int) -> dict:
    try:
        result = subprocess.run([str(_parser()), '--session'], input=json.dumps(request).encode(), capture_output=True,
                                timeout=timeout, check=False,
                                env={**os.environ, 'DOTNET_GCHeapHardLimit': '0x40000000', 'DOTNET_CLI_TELEMETRY_OPTOUT': '1'})
    except subprocess.TimeoutExpired:
        return {'connected': False, 'attempts': [], 'error': f'Reader did not finish in {timeout} s'}
    if result.returncode or not result.stdout.strip():
        return {'connected': False, 'attempts': [], 'error': (result.stderr.decode('utf-8', 'replace').strip() or 'Reader failed')[:300]}
    return json.loads(result.stdout)


def _run_bridge_session(bridge: dict, request: dict) -> dict:
    """One session reply from the terminal bridge (same shape as the reader's --session)."""
    try:
        transport = BridgeTransport(bridge['host'], int(bridge['port']), bridge['fingerprint'])
    except BridgeError as exc:
        return {'connected': False, 'attempts': [{'host': bridge['host'], 'port': bridge['port'], 'ok': False, 'error': str(exc)}], 'error': str(exc)}
    try:
        transport.send(request)
        transport.sock.settimeout(120)
        line = next(transport.lines(), b'')
    except (OSError, ValueError) as exc:
        return {'connected': False, 'attempts': [], 'error': f'Bridge connection lost: {exc}'[:300]}
    finally:
        transport.close()
    try:
        reply = json.loads(line) if line.strip() else {}
    except ValueError:
        reply = {}
    if reply.get('t') == 'failed':
        reply = {'connected': False, 'attempts': reply.get('attempts') or []}
    if reply.get('connected'):
        # Connected through the bridge: record where the bridge is, not the terminal's placeholder.
        reply['host'], reply['port'] = bridge['host'], int(bridge['port'])
        for attempt in reply.get('attempts') or []:
            attempt['host'], attempt['port'] = bridge['host'], int(bridge['port'])
    return reply or {'connected': False, 'attempts': [], 'error': 'The bridge closed without answering.'}


def read_account(actor: str, account_id: UUID, symbols: list[str] | None = None, max_symbols: int = 2000) -> dict:
    with store.transaction('read') as db:
        account = store.get_record(db, 'accounts', account_id)
    if not account:
        raise HTTPException(404, 'Account not found.')
    if account.get('platform') != 'MT5':
        raise HTTPException(409, 'Reading from the broker is available for MT5 accounts; other platforms use their own login.')
    if not str(account['external_account_id']).isdigit():
        raise HTTPException(409, 'The MT5 login must be a number.')
    if account.get('credential_kind') != 'MT5_LOGIN':
        raise HTTPException(409, 'Save the investor (read-only) or master password first.')
    addresses = (account.get('routing') or {}).get('addresses') or []
    if account.get('adapter') == 'TERMINAL_BRIDGE':
        with store.transaction('read') as db:
            settings = (db.execute('SELECT settings FROM cfg_connections WHERE id=%s', (account['connection_id'],)).fetchone() or {}).get('settings') or {}
        if not bridge_of(settings):
            raise HTTPException(409, 'This connection uses the MT5 terminal bridge: set the bridge host, port and certificate fingerprint on it first.')
        account['connection_settings'] = settings
        addresses = [{'host': settings['bridge']['host'], 'port': settings['bridge']['port']}]
    elif not addresses:
        raise HTTPException(409, 'This account has no address to connect to. Add addresses on its connection.')
    key = str(account_id)
    with _lock:
        if key in _busy:
            raise HTTPException(409, 'A read for this account is already running.')
        _busy.add(key)
    try:
        return _read(actor, account, addresses, symbols, max_symbols)
    finally:
        with _lock:
            _busy.discard(key)


def _read(actor: str, account: dict, addresses: list[dict], symbols: list[str] | None, max_symbols: int) -> dict:
    level = 'READ_ONLY' if account.get('investor_credential_kind') else 'FULL'
    secret = store.use_credential('accounts', account['id'], actor, 'Read account facts and symbol rules from the broker', level)
    timeout = 20
    if account.get('adapter') == 'TERMINAL_BRIDGE':
        bridge = account['connection_settings']['bridge']
        token = store.use_credential('connections', account['connection_id'], actor, 'Open the MT5 terminal bridge')
        request = {'token': token['token'], 'mode': 'session', 'login': int(account['external_account_id']), 'password': secret['password'],
                   'server': account['connection_settings'].get('server_name'), 'symbols': symbols or None, 'max_symbols': max_symbols}
        result = _run_bridge_session(bridge, request)
        token.clear()
    else:
        request = {'login': int(account['external_account_id']), 'password': secret['password'],
                   'hosts': [{'host': a['host'], 'port': a['port']} for a in addresses[:12]],
                   'symbols': symbols or None, 'max_symbols': max_symbols, 'timeout_seconds': timeout}
        result = _run_session(request, timeout * min(len(addresses), 12) + 60)
    secret.clear()
    request.clear()
    from . import controls
    connection_id = account['connection_id']
    if not result.get('connected'):
        last = next((a.get('error') for a in reversed(result.get('attempts') or []) if a.get('error')), None)
        error = result.get('error') or last or 'Login failed'
        controls.set_connection_status(connection_id, 'FAILED', error=error)
        read_id = _store_read(actor, account, level, result, {}, {}, 0, [], error)
        return {'id': read_id, 'connected': False, 'attempts': result.get('attempts') or [], 'error': error}

    facts = account_facts(result.get('account') or {})
    offset = _offset_minutes(result.get('server_time'), result.get('utc_now'))
    controls.set_connection_status(connection_id, 'CONNECTED', host=result['host'], port=result['port'], logged_in=True,
                                   heartbeat=True, failover=result['host'] != addresses[0]['host'],
                                   server_time_offset_ms=offset * 60_000 if offset is not None else None)
    controls.record_account_facts(account['id'], **{k: facts[k] for k in ('margin_mode', 'broker_group', 'leverage', 'stop_out_level',
                                                                         'max_open_orders', 'max_open_positions')})
    stored_specs, unmapped = [], []
    for name, raw in (result.get('symbols') or {}).items():
        spec, reason = symbol_spec(raw)
        if spec:
            stored_specs.append((name, spec, raw))
        else:
            unmapped.append({'symbol': name, 'reason': reason})
    read_id = _store_read(actor, account, level, result, facts, {'server_time_offset_minutes': offset}, len(stored_specs),
                          unmapped[:500], result.get('symbol_error'), offset)
    if stored_specs:
        with store.transaction('update') as db:
            with db.cursor() as cur:
                cur.executemany('INSERT INTO cfg_symbol_specs(account_id, broker_symbol, spec, source, raw, read_id) '
                                "VALUES (%s,%s,%s,'BROKER',%s,%s)",
                                [(account['id'], name[:64], Jsonb(spec), Jsonb(raw), read_id) for name, spec, raw in stored_specs])
    return {'id': read_id, 'connected': True, 'host': result['host'], 'port': result['port'], 'connect_ms': result.get('connect_ms'),
            'attempts': result.get('attempts') or [], 'credential_level': level, 'facts': facts, 'client_build': result.get('client_build'),
            'server_time_offset_minutes': offset, 'symbols_available': result.get('symbols_available', 0),
            'symbols_stored': len(stored_specs), 'symbols_unmapped': unmapped[:50], 'symbol_error': result.get('symbol_error')}


def _store_read(actor: str, account: dict, level: str, result: dict, facts: dict, stored: dict, symbols_stored: int,
                unmapped: list, error: str | None, offset: int | None = None) -> int:
    with store.transaction('update') as db:
        row = db.execute('''INSERT INTO cfg_broker_reads(account_id, connection_id, requested_by, credential_level, connected, host, port,
                connect_ms, attempts, server_time_offset_minutes, account_facts, stored, symbols_available, symbols_stored,
                symbols_unmapped, sdk_version, error)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
                         (account['id'], account['connection_id'], actor[:120], level, bool(result.get('connected')), result.get('host'),
                          result.get('port'), result.get('connect_ms'), Jsonb(result.get('attempts') or []), offset,
                          Jsonb(result.get('account') or {}), Jsonb({**stored, 'facts': facts}), result.get('symbols_available') or 0,
                          symbols_stored, Jsonb(unmapped), result.get('sdk_version'), (error or None) and error[:500])).fetchone()
        store.audit(db, actor, 'READ_FROM_BROKER', 'account', account['id'], None,
                    {'connected': bool(result.get('connected')), 'host': result.get('host'), 'symbols_stored': symbols_stored,
                     'error': error})
        return row['id']


def reads(account_id: UUID, limit: int = 20) -> list[dict]:
    with store.transaction('read') as db:
        rows = db.execute('SELECT id, requested_by, credential_level, connected, host, port, connect_ms, attempts, '
                          'server_time_offset_minutes, stored, symbols_available, symbols_stored, symbols_unmapped, sdk_version, '
                          'error, read_at FROM cfg_broker_reads WHERE account_id=%s ORDER BY id DESC LIMIT %s',
                          (account_id, limit)).fetchall()
    return [store.clean(r) for r in rows]
