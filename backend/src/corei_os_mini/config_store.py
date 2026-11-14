"""Configuration persistence: CRUD, cross-record rules, versioning, audit and readiness.

Owns: every write to cfg_* tables. Each write runs in one transaction together with
its audit record, and uses optimistic versioning so two operators cannot silently
overwrite each other. Secrets are encrypted before storage and never returned.
Does not own: HTTP concerns (config_api) or provider sessions (future adapters).

Blueprint references: §8 entities and relationships, §11 state authority,
§13.3 execution controls, §17.2 audit minimum.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import re
from typing import Any, Iterator
from uuid import UUID, uuid4

from fastapi import HTTPException
import logging
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ValidationError

from . import credentials
from .config import get_settings
from .config_models import (AccountGroupIn, AccountIn, ConnectionIn, InstrumentIn, MappingIn, ProviderIn,
                            ApiKeyCredential, BridgeTokenCredential, FixLoginCredential, OAuthClientCredential, Mt5AccountSetupIn, Mt5LoginCredential,
                            Mt5Settings)
from .mt5_routing import effective_access_points
from .persistence import PersistenceNotConfigured, _connect

# What COREI can actually do per platform. Market data and account reads exist for MT5
# (read-only, through the MT5 SDK); sending orders is built per platform later. An
# account on a platform not listed reports the blocker instead of implying it works.
DATA_ADAPTERS: set[str] = {'MT5', 'REST_API', 'FIX'}  # REST_API: Upstox market data only (md/upstox_reader.py)
ORDER_ADAPTERS: set[str] = set()
AVAILABLE_ADAPTERS = DATA_ADAPTERS


@dataclass(frozen=True)
class Spec:
    table: str
    label: str
    model: type[BaseModel]
    immutable: tuple[str, ...]


SPECS: dict[str, Spec] = {
    'providers': Spec('cfg_providers', 'provider', ProviderIn, ('code',)),
    'connections': Spec('cfg_connections', 'connection', ConnectionIn, ('provider_id', 'code', 'platform', 'environment')),
    'accounts': Spec('cfg_accounts', 'account', AccountIn, ('connection_id', 'code', 'external_account_id')),
    'instruments': Spec('cfg_instruments', 'instrument', InstrumentIn, ('symbol',)),
    'mappings': Spec('cfg_symbol_mappings', 'symbol mapping', MappingIn, ('connection_id', 'instrument_id', 'account_id')),
    'groups': Spec('cfg_account_groups', 'account group', AccountGroupIn, ('code',)),
}
# Records that are archived (kept, read-only) instead of deleted once they have history.
ARCHIVABLE = ('providers', 'connections', 'accounts', 'groups')

UNIQUE_MESSAGES = {
    'cfg_providers_code_key': 'A provider with this code already exists.',
    'cfg_connections_code_key': 'A connection with this code already exists.',
    'cfg_accounts_code_key': 'An account with this code already exists.',
    'cfg_accounts_connection_id_external_account_id_key': 'This broker account number is already registered on this connection.',
    'cfg_instruments_symbol_key': 'An instrument with this symbol already exists.',
    'cfg_symbol_mappings_symbol_scope': 'This broker symbol is already mapped on this connection (or for this account).',
    'cfg_symbol_mappings_instrument_scope': 'This instrument is already mapped on this connection (or for this account).',
    'cfg_account_groups_code_key': 'An account group with this code already exists.',
}


# ---------- transaction and error handling ----------

_log = logging.getLogger(__name__)


@contextmanager
def transaction(operation: str = 'write') -> Iterator[psycopg.Connection]:
    try:
        with _connect(get_settings().database_url) as db:
            yield db
    except HTTPException:
        raise
    except psycopg.errors.UniqueViolation as exc:
        message = UNIQUE_MESSAGES.get(exc.diag.constraint_name or '', 'A record with these identifiers already exists.')
        raise HTTPException(409, message) from None
    except (psycopg.errors.ForeignKeyViolation, psycopg.errors.RestrictViolation):
        # PostgreSQL reports a blocked delete as 23503 (NO ACTION) or 23001 (RESTRICT, as PostgreSQL 18 does here).
        if operation == 'delete':
            raise HTTPException(409, 'This record is used by other records. Remove or deactivate those first.') from None
        raise HTTPException(422, 'A referenced record does not exist.') from None
    except psycopg.errors.CheckViolation as exc:
        _log.warning('Check violation during %s: %s', operation, exc.diag.message_primary)
        raise HTTPException(422, f'A value is outside its allowed range ({exc.diag.message_primary}).') from None
    except credentials.CredentialStorageUnavailable as exc:
        raise HTTPException(503, f'Credential storage is not configured: {exc}. Run scripts/setup-broker-accounts.sh.') from None
    except psycopg.IntegrityError as exc:
        _log.warning('Integrity error during %s: %s %s', operation, type(exc).__name__, exc.diag.constraint_name)
        raise HTTPException(409 if operation == 'delete' else 422, 'The change conflicts with other records.') from None
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn, psycopg.errors.UndefinedFunction) as exc:
        _log.error('Database schema out of date during %s: %s %s', operation, exc.sqlstate, exc.diag.message_primary)
        raise HTTPException(503, f'Database schema out of date ({exc.diag.message_primary}). Run scripts/deploy-workstation.sh to apply migrations.') from None
    except psycopg.errors.InsufficientPrivilege as exc:
        _log.error('Database permission denied during %s: %s', operation, exc.diag.message_primary)
        raise HTTPException(503, f'Database permission denied ({exc.diag.message_primary}). Run scripts/deploy-workstation.sh to repair grants.') from None
    except PersistenceNotConfigured:
        raise HTTPException(503, 'Configuration database is not configured (COREI_DATABASE_URL).') from None
    except psycopg.OperationalError as exc:
        _log.error('Database unreachable during %s: %s', operation, str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__)
        raise HTTPException(503, 'Database unreachable (it may be restarting). Try again in a moment.') from None
    except psycopg.Error as exc:
        # Anything else: say which error it was, so the log line and the screen point at the cause.
        state = getattr(exc, 'sqlstate', None) or type(exc).__name__
        primary = (getattr(getattr(exc, 'diag', None), 'message_primary', None) or '')[:160]
        _log.error('Database error during %s: %s %s %s', operation, type(exc).__name__, state, primary)
        raise HTTPException(503, f'Database error {state} during {operation}{": " + primary if primary else ""}.') from None


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value.normalize(), 'f')
    if isinstance(value, UUID):
        return str(value)
    return value


def clean(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: _plain(v) for k, v in row.items() if k != 'ciphertext'}


def audit(db, actor: str, action: str, entity: str, entity_id: Any,
          before: dict | None, after: dict | None, reason: str = '') -> None:
    db.execute('INSERT INTO cfg_audit_log(actor,action,entity,entity_id,before_state,after_state,reason) '
               'VALUES (%s,%s,%s,%s,%s,%s,%s)',
               (actor, action, entity, str(entity_id),
                json.dumps(clean(before), default=str) if before else None,
                json.dumps(clean(after), default=str) if after else None, reason))


# ---------- enriched reads ----------

SELECTS = {
    'providers': '''SELECT p.*, (SELECT count(*) FROM cfg_connections c WHERE c.provider_id=p.id) AS connection_count
        FROM cfg_providers p''',
    'connections': '''SELECT c.*, p.code AS provider_code, p.name AS provider_name, p.status AS provider_status,
        cr.kind AS credential_kind, cr.updated_at AS credential_updated_at,
        (SELECT count(*) FROM cfg_accounts a WHERE a.connection_id=c.id) AS account_count,
        (SELECT count(*) FROM cfg_symbol_mappings m WHERE m.connection_id=c.id) AS mapping_count,
        to_jsonb(st) - 'connection_id' AS live_status,
        (SELECT count(*) FROM cfg_change_requests r WHERE r.entity='connections' AND r.entity_id=c.id AND r.status='PENDING') AS pending_changes
        FROM cfg_connections c JOIN cfg_providers p ON p.id=c.provider_id
        LEFT JOIN cfg_credentials cr ON cr.connection_id=c.id
        LEFT JOIN cfg_connection_status st ON st.connection_id=c.id''',
    'accounts': '''SELECT a.*, c.code AS connection_code, c.name AS connection_name, c.platform, c.environment,
        c.purpose, c.status AS connection_status, c.settings->>'auth_method' AS auth_method,
        c.adapter, c.server_utc_offset_minutes, c.dst_rule, c.registered_egress_ip, c.max_orders_per_second,
        p.id AS provider_id, p.code AS provider_code, p.name AS provider_name, p.status AS provider_status,
        mcr.kind AS master_credential_kind, mcr.updated_at AS master_credential_updated_at,
        icr.kind AS investor_credential_kind, icr.updated_at AS investor_credential_updated_at,
        COALESCE(mcr.kind, icr.kind) AS credential_kind,
        CASE WHEN mcr.id IS NOT NULL THEN 'FULL' WHEN icr.id IS NOT NULL THEN 'READ_ONLY' END AS credential_access_level,
        COALESCE(mcr.updated_at, icr.updated_at) AS credential_updated_at, ccr.kind AS connection_credential_kind,
        c.settings AS connection_settings, st.state AS connection_state, st.last_error AS connection_error,
        st.server_time_offset_ms AS measured_offset_ms,
        (SELECT coalesce(array_agg(g.group_id::text ORDER BY g.group_id), '{}') FROM cfg_account_group_members g WHERE g.account_id=a.id) AS group_ids,
        (SELECT max(u.used_at) FROM cfg_credential_use u WHERE u.owner_id=a.id) AS credential_last_used_at,
        (SELECT count(*) FROM cfg_change_requests r WHERE r.entity IN ('accounts','credential') AND r.entity_id=a.id AND r.status='PENDING') AS pending_changes,
        t.expires_at AS session_token_expires_at
        FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id
        JOIN cfg_providers p ON p.id=c.provider_id
        LEFT JOIN cfg_credentials mcr ON mcr.account_id=a.id AND mcr.access_level='FULL'
        LEFT JOIN cfg_credentials icr ON icr.account_id=a.id AND icr.access_level='READ_ONLY'
        LEFT JOIN cfg_credentials ccr ON ccr.connection_id=c.id
        LEFT JOIN cfg_connection_status st ON st.connection_id=c.id
        LEFT JOIN cfg_session_tokens t ON t.account_id=a.id''',
    'instruments': '''SELECT i.*, (SELECT count(*) FROM cfg_symbol_mappings m WHERE m.instrument_id=i.id) AS mapping_count
        FROM cfg_instruments i''',
    'mappings': '''SELECT m.*, i.symbol AS instrument_symbol, c.code AS connection_code, c.environment,
        c.purpose AS connection_purpose, p.code AS provider_code, ac.code AS account_code
        FROM cfg_symbol_mappings m JOIN cfg_instruments i ON i.id=m.instrument_id
        JOIN cfg_connections c ON c.id=m.connection_id JOIN cfg_providers p ON p.id=c.provider_id
        LEFT JOIN cfg_accounts ac ON ac.id=m.account_id''',
    'groups': '''SELECT g.*, (SELECT count(*) FROM cfg_account_group_members m WHERE m.group_id=g.id) AS account_count,
        (SELECT coalesce(array_agg(m.account_id::text), '{}') FROM cfg_account_group_members m WHERE m.group_id=g.id) AS account_ids
        FROM cfg_account_groups g''',
}
ALIAS = {'providers': 'p', 'connections': 'c', 'accounts': 'a', 'instruments': 'i', 'mappings': 'm', 'groups': 'g'}
FILTERS = {
    'connections': {'provider_id': 'c.provider_id'},
    'accounts': {'provider_id': 'p.id', 'connection_id': 'a.connection_id'},
    'instruments': {'asset_class': 'i.asset_class'},
    'mappings': {'connection_id': 'm.connection_id', 'instrument_id': 'm.instrument_id', 'account_id': 'm.account_id'},
}
ORDER = {'providers': 'p.code', 'connections': 'p.code, c.code', 'accounts': 'p.code, c.code, a.code',
         'instruments': 'i.symbol', 'mappings': 'c.code, i.symbol', 'groups': 'g.code'}
STATUS_COLUMN = {'providers': 'p.status', 'connections': 'c.status', 'accounts': 'a.status', 'groups': 'g.status'}


def execution_controls(db) -> dict[str, dict]:
    rows = db.execute('SELECT * FROM cfg_execution_controls ORDER BY environment').fetchall()
    return {r['environment']: clean(r) for r in rows}


def latest_latency(db, connection_ids: list) -> dict:
    """{connection_id: {(host, port): latest sample}} from the latency history."""
    if not connection_ids:
        return {}
    rows = db.execute('SELECT DISTINCT ON (connection_id, host, port) connection_id, host, port, reachable, latency_ms, '
                      'measured_at, measured_from FROM cfg_latency_checks WHERE connection_id = ANY(%s) '
                      'ORDER BY connection_id, host, port, measured_at DESC', (connection_ids,)).fetchall()
    out: dict = {}
    for r in rows:
        out.setdefault(str(r['connection_id']), {})[(r['host'], r['port'])] = {
            'reachable': r['reachable'], 'latency_ms': r['latency_ms'], 'measured_at': r['measured_at'].isoformat(),
            'measured_from': r['measured_from']}
    return out


def account_routing(row: dict, latency: dict) -> dict | None:
    if row.get('platform') != 'MT5':
        return None
    settings = row.get('connection_settings') or {}
    return effective_access_points(row.get('access_mode') or 'SERVER', row.get('access_points') or [],
                                   settings.get('access_points') or [], latency.get(str(row['connection_id']), {}))


def _today():
    return datetime.now(timezone.utc).date()


def _now():
    return datetime.now(timezone.utc)


def policy(db, key: str, default: Any = None) -> Any:
    row = db.execute('SELECT value FROM cfg_policies WHERE key=%s', (key,)).fetchone()
    return row['value'] if row else default


def account_readiness(row: dict, controls: dict[str, dict], context: dict | None = None) -> dict[str, list[str]]:
    context = context or {}
    """Explain, in order, every reason an account cannot yet stream data or send orders."""
    data: list[str] = []
    if row['provider_status'] != 'ACTIVE':
        data.append('PROVIDER_INACTIVE')
    if row['connection_status'] != 'ACTIVE':
        data.append('CONNECTION_INACTIVE')
    if row['status'] != 'ACTIVE':
        data.append('ACCOUNT_INACTIVE')
    if row['trading_permission'] == 'DISABLED':
        data.append('ACCOUNT_PERMISSION_DISABLED')
    if row['platform'] == 'MT5' and not row.get('credential_kind'):
        data.append('ACCOUNT_CREDENTIAL_MISSING')
    if row['platform'] == 'REST_API' and not row.get('connection_credential_kind'):
        data.append('CONNECTION_CREDENTIAL_MISSING')
    routing = row.get('routing')
    uses_bridge = row['platform'] == 'MT5' and row.get('adapter') == 'TERMINAL_BRIDGE'
    if uses_bridge:
        # The terminal logs in; COREI needs the bridge's address, certificate fingerprint and token, not server addresses.
        if not ((row.get('connection_settings') or {}).get('bridge') or {}).get('fingerprint'):
            data.append('BRIDGE_NOT_CONFIGURED')
        elif row.get('connection_credential_kind') != 'BRIDGE_TOKEN':
            data.append('BRIDGE_TOKEN_MISSING')
    elif routing and 'NO_ADDRESS_AVAILABLE' in routing['warnings']:
        data.append('NO_ADDRESS_AVAILABLE')
    if row.get('connection_state') == 'FAILED':
        data.append('CONNECTION_FAILED')
    today = _today()
    if row.get('expires_on') and row['expires_on'] < today:
        data.append('ACCOUNT_EXPIRED')
    if row['platform'] == 'REST_API' and row.get('registered_egress_ip') and context.get('egress_ip') \
            and str(row['registered_egress_ip']) != context['egress_ip']:
        data.append('STATIC_IP_MISMATCH')
    api_base = str((row.get('connection_settings') or {}).get('api_base_url') or '')
    if row['platform'] not in DATA_ADAPTERS or (row['platform'] == 'REST_API' and 'upstox' not in api_base):
        data.append('ADAPTER_NOT_AVAILABLE')
    # An OAuth broker (Upstox) issues a token valid until 03:30 IST: data stops without today's login.
    oauth_login_needed = row['platform'] == 'REST_API' and row.get('auth_method') == 'OAUTH2' and (
        not row.get('session_token_expires_at') or row['session_token_expires_at'] <= _now())
    if oauth_login_needed:
        data.append('BROKER_LOGIN_NEEDED_TODAY')
    trading = list(data)
    if row['platform'] not in ORDER_ADAPTERS:
        trading.append('ORDER_ADAPTER_NOT_AVAILABLE')
    if row['platform'] == 'MT5' and row.get('margin_mode', 'UNKNOWN') == 'UNKNOWN':
        trading.append('MARGIN_MODE_UNKNOWN')
    # The broker's clock: entered on the connection, or measured at the last login.
    if row['platform'] == 'MT5' and row.get('server_utc_offset_minutes') is None and row.get('measured_offset_ms') is None:
        trading.append('SERVER_CLOCK_UNKNOWN')
    if row.get('relationship') == 'CLIENT':
        if not row.get('mandate_reference'):
            trading.append('MANDATE_MISSING')
        elif row.get('mandate_expires_on') and row['mandate_expires_on'] < today:
            trading.append('MANDATE_EXPIRED')
    if row['platform'] == 'MT5' and row['trading_permission'] == 'TRADING' and not row.get('master_credential_kind') \
            and row.get('credential_kind'):
        trading.append('MASTER_PASSWORD_MISSING')
    if row['purpose'] == 'MARKET_DATA':
        trading.append('CONNECTION_DATA_ONLY')
    if row['trading_permission'] != 'TRADING':
        trading.append('ACCOUNT_NOT_PERMITTED_TO_TRADE')
    if row.get('credential_access_level') == 'READ_ONLY':
        trading.append('READ_ONLY_CREDENTIAL')
    if not controls.get(row['environment'], {}).get('trading_enabled'):
        trading.append(f"ORDER_SENDING_OFF_{row['environment']}")
    warnings = list((row.get('routing') or {}).get('warnings', []))
    server = str(((row.get('connection_settings') or {}).get('server_name')) or '')
    if row.get('environment') == 'LIVE' and 'demo' in server.lower():
        warnings.append('LIVE_CONNECTION_ON_DEMO_SERVER')
    elif row.get('environment') == 'DEMO' and 'live' in server.lower() and 'demo' not in server.lower():
        warnings.append('DEMO_CONNECTION_ON_LIVE_SERVER')
    warn_days = int(context.get('expiry_warning_days', 7))
    for field, code in (('expires_on', 'ACCOUNT_EXPIRING'), ('mandate_expires_on', 'MANDATE_EXPIRING')):
        if row.get(field) and today <= row[field] <= today + timedelta(days=warn_days):
            warnings.append(code)
    # Order-only concerns are warnings only for accounts that may trade.
    trades = row.get('trading_permission') == 'TRADING'
    if trades and row['platform'] == 'REST_API' and row['environment'] == 'LIVE' and not row.get('registered_egress_ip'):
        warnings.append('STATIC_IP_NOT_RECORDED')
    if trades and row.get('max_orders_per_second') is None:
        warnings.append('RATE_LIMIT_NOT_SET')
    if row.get('margin_mode_source') == 'MANUAL':
        warnings.append('MARGIN_MODE_NOT_CONFIRMED_BY_BROKER')
    return {'data_blockers': data, 'trading_blockers': trading, 'warnings': warnings}


def _enrich(entity: str, rows: list[dict], db) -> list[dict]:
    if entity == 'accounts':
        controls = execution_controls(db)
        latency = latest_latency(db, list({r['connection_id'] for r in rows if r['platform'] == 'MT5'}))
        from .network import egress_ip
        context = {'expiry_warning_days': policy(db, 'expiry_warning_days', 7),
                   'egress_ip': egress_ip(cached_only=True) if any(r.get('registered_egress_ip') for r in rows) else None}
        out = []
        from .md.explain import explain
        for r in rows:
            r = {**r, 'routing': account_routing(r, latency)}
            readiness = account_readiness(r, controls, context)
            # The broker's own reason for a failed connection, in plain words.
            problem = explain(r.get('connection_error')) if r.get('connection_state') in ('FAILED', 'DEGRADED') else None
            r.pop('connection_settings', None)
            out.append({**clean(r), **readiness, 'connection_problem': problem})
        return out
    return [clean(r) for r in rows]


def list_records(entity: str, filters: dict[str, str], q: str = '', include_archived: bool = False) -> list[dict]:
    alias = ALIAS[entity]
    clauses, params = [], []
    if entity in STATUS_COLUMN and not include_archived:
        clauses.append(f"{STATUS_COLUMN[entity]} <> 'ARCHIVED'")
    for key, column in FILTERS.get(entity, {}).items():
        if filters.get(key):
            clauses.append(f'{column}=%s')
            params.append(filters[key])
    if q:
        text_columns = {'providers': ['p.code', 'p.name'], 'connections': ['c.code', 'c.name', 'p.code'],
                        'accounts': ['a.code', 'a.name', 'a.external_account_id', 'a.holder_name', 'c.code', 'p.code'],
                        'instruments': ['i.symbol', 'i.name'], 'mappings': ['m.broker_symbol', 'i.symbol', 'c.code'],
                        'groups': ['g.code', 'g.name']}[entity]
        pattern = '%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        clauses.append('(' + ' OR '.join(f'{c} ILIKE %s' for c in text_columns) + ')')
        params.extend([pattern] * len(text_columns))
    where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
    with transaction('read') as db:
        rows = db.execute(SELECTS[entity] + where + f' ORDER BY {ORDER[entity]} LIMIT 1000', params).fetchall()
        return _enrich(entity, rows, db)


def get_record(db, entity: str, record_id: UUID) -> dict | None:
    row = db.execute(SELECTS[entity] + f' WHERE {ALIAS[entity]}.id=%s', (record_id,)).fetchone()
    return _enrich(entity, [row], db)[0] if row else None


# ---------- cross-record rules ----------

PURPOSE_ALLOWS = {'MARKET_DATA': {'MARKET_DATA'}, 'TRADING': {'TRADING'}, 'BOTH': {'MARKET_DATA', 'TRADING', 'BOTH'}}


def _connection(db, connection_id) -> dict:
    row = db.execute('SELECT * FROM cfg_connections WHERE id=%s', (connection_id,)).fetchone()
    if not row:
        raise HTTPException(422, 'Connection does not exist.')
    return row


def _rules(db, entity: str, data: dict, record_id: UUID | None) -> None:
    if entity == 'accounts':
        connection = _connection(db, data['connection_id'])
        if connection['platform'] == 'MT5' and not re.fullmatch(r'[1-9][0-9]{0,19}', data['external_account_id']):
            raise HTTPException(422, 'MT5 account number must be digits only (the broker login).')
        # FASTEST (the default) and SERVER mean nothing outside MT5 and are accepted; a pinned address list is refused.
        if connection['platform'] != 'MT5' and (data.get('access_mode') == 'ACCOUNT' or data.get('access_points')):
            raise HTTPException(422, 'Address choice applies to MT5 accounts only.')
        if data.get('access_mode') == 'ACCOUNT':
            on_server = {(p['host'], int(p['port'])) for p in connection['settings'].get('access_points', [])}
            missing = [f"{p['host']}:{p['port']}" for p in data['access_points'] if (p['host'], int(p['port'])) not in on_server]
            if missing:
                raise HTTPException(422, f"{', '.join(missing[:3])} {'is' if len(missing) == 1 else 'are'} not among the addresses of "
                                         f"{connection['settings'].get('server_name')}. Add {'it' if len(missing) == 1 else 'them'} to the connection first.")
        if data['trading_permission'] == 'TRADING':
            if connection['purpose'] == 'MARKET_DATA':
                raise HTTPException(422, 'This connection is market-data only. Use a trading connection to allow trading.')
            if record_id and db.execute("SELECT 1 FROM cfg_credentials WHERE account_id=%s AND access_level='READ_ONLY'",
                                        (record_id,)).fetchone() and not db.execute(
                    "SELECT 1 FROM cfg_credentials WHERE account_id=%s AND access_level='FULL'", (record_id,)).fetchone():
                raise HTTPException(422, 'Only an investor (read-only) password is saved. Save the master password to allow trading.')
        if data.get('relationship') == 'CLIENT' and data['trading_permission'] == 'TRADING' and not data.get('mandate_reference'):
            raise HTTPException(422, 'A client account needs a mandate reference before it may trade.')
        if data.get('group_ids'):
            found = db.execute('SELECT count(*) AS n FROM cfg_account_groups WHERE id = ANY(%s)', (list(data['group_ids']),)).fetchone()['n']
            if found != len(set(data['group_ids'])):
                raise HTTPException(422, 'An account group does not exist.')
    elif entity == 'mappings':
        connection = _connection(db, data['connection_id'])
        if data.get('account_id'):
            owner = db.execute('SELECT connection_id FROM cfg_accounts WHERE id=%s', (data['account_id'],)).fetchone()
            if not owner or owner['connection_id'] != data['connection_id']:
                raise HTTPException(422, 'An account-specific mapping must use an account on the same connection.')
        if data['usage'] not in PURPOSE_ALLOWS[connection['purpose']]:
            raise HTTPException(422, f"Usage {data['usage']} is not possible on a {connection['purpose']} connection.")
    elif entity == 'connections' and record_id:
        purpose = data['purpose']
        if purpose == 'MARKET_DATA' and db.execute(
                "SELECT 1 FROM cfg_accounts WHERE connection_id=%s AND trading_permission='TRADING'", (record_id,)).fetchone():
            raise HTTPException(422, 'Accounts on this connection are permitted to trade. Change them before making the connection data-only.')
        blocked = {'MARKET_DATA': ('TRADING', 'BOTH'), 'TRADING': ('MARKET_DATA', 'BOTH'), 'BOTH': ()}[purpose]
        if blocked and db.execute('SELECT 1 FROM cfg_symbol_mappings WHERE connection_id=%s AND usage = ANY(%s)',
                                  (record_id, list(blocked))).fetchone():
            raise HTTPException(422, 'Symbol mappings on this connection use a purpose this change would remove. Update them first.')
        credential = db.execute('SELECT kind FROM cfg_credentials WHERE connection_id=%s', (record_id,)).fetchone()
        expected = {'API_KEY': 'API_KEY', 'OAUTH2': 'OAUTH_CLIENT'}.get(data['settings'].get('auth_method'))
        if credential and credential['kind'] != expected:
            raise HTTPException(422, 'The stored credential does not match the new authentication method. Remove it first.')


# ---------- writes ----------

def _json_columns(entity: str, values: dict) -> dict:
    if entity == 'connections':
        if 'settings' in values:
            values['settings'] = Jsonb(values['settings'])
        if 'maintenance' in values:
            values['maintenance'] = Jsonb(values['maintenance'])
    if entity == 'providers' and 'holidays' in values:
        values['holidays'] = Jsonb(values['holidays'])
    if entity == 'accounts':
        values.pop('group_ids', None)
        if 'access_points' in values:
            values['access_points'] = Jsonb([{'host': p['host'], 'port': int(p['port'])} for p in values['access_points']])
        if 'margin_mode' in values:
            values['margin_mode_source'] = 'UNKNOWN' if values['margin_mode'] == 'UNKNOWN' else 'MANUAL'
    return values


def _set_groups(db, account_id: UUID, group_ids: list) -> None:
    db.execute('DELETE FROM cfg_account_group_members WHERE account_id=%s', (account_id,))
    for group_id in dict.fromkeys(group_ids):
        db.execute('INSERT INTO cfg_account_group_members(group_id, account_id) VALUES (%s,%s)', (group_id, account_id))


# ---------- four-eyes on LIVE changes (DEC-31) ----------

def four_eyes_on(db) -> bool:
    return bool(policy(db, 'four_eyes_live_changes', True))


def request_change(db, actor: str, entity: str, entity_id: UUID, action: str, summary: str, payload: dict,
                   secret_fields: dict | None = None, base_version: int | None = None, reason: str = '') -> dict:
    if db.execute("SELECT 1 FROM cfg_change_requests WHERE entity=%s AND entity_id=%s AND action=%s AND status='PENDING'",
                  (entity, entity_id, action)).fetchone():
        raise HTTPException(409, 'The same change is already waiting for approval. Approve, reject or withdraw it first.')
    request_id = uuid4()
    secret = credentials.encrypt(request_id, secret_fields) if secret_fields else None
    row = db.execute('INSERT INTO cfg_change_requests(id, entity, entity_id, action, summary, payload, secret, base_version, reason, requested_by) '
                     'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id, entity, entity_id, action, summary, status, requested_by, requested_at',
                     (request_id, entity, entity_id, action, summary[:500], Jsonb(payload), secret, base_version, reason[:500], actor)).fetchone()
    audit(db, actor, 'REQUEST_CHANGE', 'change request', request_id, None, {'action': action, 'summary': summary, 'entity_id': str(entity_id)})
    from .alerts import enqueue
    enqueue(db, 'APPROVAL_NEEDED', 'WARNING', f'Approval needed: {action.replace("_", " ").lower()}',
            f'{summary}\nRequested by {actor}. Approve or reject in COREI › Configuration › Approvals.')
    return {'change_request': clean(row)}


def _connection_needs_approval(before: dict | None, values: dict) -> list[str]:
    if values['environment'] != 'LIVE':
        return []
    reasons = []
    old = {(p['host'], int(p['port'])) for p in ((before or {}).get('settings') or {}).get('access_points', [])}
    added = [f"{p['host']}:{p['port']}" for p in values['settings'].get('access_points', []) if (p['host'], int(p['port'])) not in old]
    if added:
        reasons.append(f"adds address{'es' if len(added) > 1 else ''} {', '.join(added[:5])}{' …' if len(added) > 5 else ''}")
    if before and before['status'] != 'ACTIVE' and values['status'] == 'ACTIVE':
        reasons.append(f"reactivates it (was {before['status'].lower()})")
    return reasons


def _insert(db, actor: str, entity: str, data: BaseModel, reason: str = '') -> UUID:
    spec = SPECS[entity]
    _rules(db, entity, data.model_dump(mode='python'), None)
    values = _json_columns(entity, data.model_dump(mode='python'))
    group_ids = data.model_dump().get('group_ids') if entity == 'accounts' else None
    record_id = uuid4()
    columns = ['id', *values]
    db.execute(sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(
        sql.Identifier(spec.table), sql.SQL(',').join(map(sql.Identifier, columns)),
        sql.SQL(',').join(sql.Placeholder() * len(columns))), (record_id, *values.values()))
    if group_ids:
        _set_groups(db, record_id, group_ids)
    after = db.execute(sql.SQL('SELECT * FROM {} WHERE id=%s').format(sql.Identifier(spec.table)), (record_id,)).fetchone()
    audit(db, actor, 'CREATE', spec.label, record_id, None, {**after, 'group_ids': [str(g) for g in group_ids or []]} if group_ids else after, reason)
    return record_id


def create(actor: str, entity: str, data: BaseModel, approved: bool = False, reason: str = '') -> dict:
    with transaction('create') as db:
        if entity == 'connections' and not approved and four_eyes_on(db):
            values = data.model_dump(mode='json')
            reasons = _connection_needs_approval(None, values)
            if reasons:
                _rules(db, entity, data.model_dump(mode='python'), None)
                return request_change(db, actor, 'connections', uuid4(), 'CREATE_CONNECTION',
                                      f"New LIVE connection {values['code']} ({values['settings'].get('server_name') or values['settings'].get('api_base_url')}) {' and '.join(reasons)}",
                                      {'record': values})
        return get_record(db, entity, _insert(db, actor, entity, data, reason))


def _locked(db, spec: Spec, record_id: UUID, version: int) -> dict:
    row = db.execute(sql.SQL('SELECT * FROM {} WHERE id=%s FOR UPDATE').format(sql.Identifier(spec.table)), (record_id,)).fetchone()
    if not row:
        raise HTTPException(404, f'{spec.label.capitalize()} not found.')
    if row['version'] != version:
        raise HTTPException(409, f'This {spec.label} was changed by someone else. Reload it before saving.')
    return row


def update(actor: str, entity: str, record_id: UUID, data: BaseModel, version: int,
           approved: bool = False, reason: str = '') -> dict:
    spec = SPECS[entity]
    values = data.model_dump(mode='python')
    with transaction('update') as db:
        before = _locked(db, spec, record_id, version)
        for field in spec.immutable:
            if str(before[field]) != str(values[field]):
                raise HTTPException(422, f'{field} cannot be changed. Create a new {spec.label} instead.')
        if before.get('status') == 'ARCHIVED' and values.get('status') == 'ARCHIVED':
            raise HTTPException(422, f'This {spec.label} is archived. Restore it (set status Inactive) before editing.')
        _rules(db, entity, values, record_id)
        if entity == 'connections' and not approved and four_eyes_on(db):
            reasons = _connection_needs_approval(before, data.model_dump(mode='json'))
            if reasons:
                return request_change(db, actor, 'connections', record_id, 'UPDATE_CONNECTION',
                                      f"LIVE connection {before['code']}: change {' and '.join(reasons)}",
                                      {'record': data.model_dump(mode='json')}, base_version=version)
        if entity == 'accounts':
            _set_groups(db, record_id, values.get('group_ids') or [])
        changes = _json_columns(entity, {k: v for k, v in values.items() if k not in spec.immutable})
        assignments = sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(k)) for k in changes)
        db.execute(sql.SQL('UPDATE {} SET {}, version=version+1, updated_at=now() WHERE id=%s').format(
            sql.Identifier(spec.table), assignments), (*changes.values(), record_id))
        after = db.execute(sql.SQL('SELECT * FROM {} WHERE id=%s').format(sql.Identifier(spec.table)), (record_id,)).fetchone()
        audit(db, actor, 'UPDATE', spec.label, record_id, before, after, reason)
        return get_record(db, entity, record_id)


HISTORY = {
    'accounts': ["SELECT 1 FROM cfg_deployments WHERE account_id=%s", "SELECT 1 FROM cfg_credential_use WHERE owner_id=%s",
                 "SELECT 1 FROM cfg_symbol_specs WHERE account_id=%s"],
    'connections': ["SELECT 1 FROM cfg_latency_checks WHERE connection_id=%s", "SELECT 1 FROM cfg_credential_use WHERE owner_id=%s",
                    "SELECT 1 FROM cfg_connection_status WHERE connection_id=%s AND last_login_at IS NOT NULL"],
    'providers': [],
    'groups': [],
}


def delete(actor: str, entity: str, record_id: UUID, version: int) -> None:
    spec = SPECS[entity]
    with transaction('delete') as db:
        before = _locked(db, spec, record_id, version)
        if any(db.execute(q, (record_id,)).fetchone() for q in HISTORY.get(entity, [])):
            raise HTTPException(409, f'This {spec.label} has history (activity, measurements or deployments). Archive it instead; it stays on record.')
        db.execute(sql.SQL('DELETE FROM {} WHERE id=%s').format(sql.Identifier(spec.table)), (record_id,))
        audit(db, actor, 'DELETE', spec.label, record_id, before, None)


def archive(actor: str, entity: str, record_id: UUID, version: int, reason: str) -> dict:
    if entity not in ARCHIVABLE:
        raise HTTPException(404, 'This record type is not archived.')
    spec = SPECS[entity]
    with transaction('update') as db:
        before = _locked(db, spec, record_id, version)
        if entity == 'connections' and db.execute("SELECT 1 FROM cfg_accounts WHERE connection_id=%s AND status<>'ARCHIVED'", (record_id,)).fetchone():
            raise HTTPException(409, 'Archive or move the accounts on this connection first.')
        if entity == 'providers' and db.execute("SELECT 1 FROM cfg_connections WHERE provider_id=%s AND status<>'ARCHIVED'", (record_id,)).fetchone():
            raise HTTPException(409, 'Archive the connections of this provider first.')
        if entity == 'accounts':
            db.execute("UPDATE cfg_deployments SET status='ENDED', version=version+1, updated_at=now() WHERE account_id=%s AND status<>'ENDED'", (record_id,))
        after = db.execute(sql.SQL("UPDATE {} SET status='ARCHIVED', version=version+1, updated_at=now() WHERE id=%s RETURNING *").format(
            sql.Identifier(spec.table)), (record_id,)).fetchone()
        audit(db, actor, 'ARCHIVE', spec.label, record_id, before, after, reason)
        return get_record(db, entity, record_id)


# ---------- credentials ----------

def _secret_fields(credential: BaseModel) -> dict[str, str]:
    return {name: getattr(credential, name).get_secret_value()
            for name, value in credential if hasattr(value, 'get_secret_value')}


def set_credential(actor: str, owner: str, owner_id: UUID, credential: BaseModel, approved: bool = False, reason: str = '') -> dict:
    with transaction('update') as db:
        return _save_credential(db, actor, owner, owner_id, credential, approved, reason)


def _credential_owner(db, owner: str, owner_id: UUID, credential: BaseModel) -> tuple[str, dict]:
    access_level = getattr(credential, 'access_level', 'FULL')
    if owner == 'accounts':
        account = db.execute('SELECT a.*, c.platform, c.environment FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id '
                             'WHERE a.id=%s FOR UPDATE OF a', (owner_id,)).fetchone()
        if not account:
            raise HTTPException(404, 'Account not found.')
        if account['platform'] != 'MT5' or not isinstance(credential, Mt5LoginCredential):
            raise HTTPException(422, 'Only MT5 accounts hold their own login. API credentials belong to the connection.')
        if access_level == 'READ_ONLY' and account['trading_permission'] == 'TRADING' and not db.execute(
                "SELECT 1 FROM cfg_credentials WHERE account_id=%s AND access_level='FULL'", (owner_id,)).fetchone():
            raise HTTPException(422, 'This account is permitted to trade: save its master password first. '
                                     'The investor (read-only) password can then be added beside it.')
        return 'account_id', account
    connection = db.execute('SELECT * FROM cfg_connections WHERE id=%s FOR UPDATE', (owner_id,)).fetchone()
    if not connection:
        raise HTTPException(404, 'Connection not found.')
    if connection['platform'] == 'FIX':
        if not isinstance(credential, FixLoginCredential):
            raise HTTPException(422, 'A FIX connection takes the session login (username and password).')
        return 'connection_id', connection
    if connection['platform'] == 'MT5':
        if connection['adapter'] != 'TERMINAL_BRIDGE' or not isinstance(credential, BridgeTokenCredential):
            raise HTTPException(422, 'An MT5 connection takes only the terminal bridge token (when its method is Terminal bridge). '
                                     'MT5 logins are saved on each account.')
        return 'connection_id', connection
    expected = {'API_KEY': ApiKeyCredential, 'OAUTH2': OAuthClientCredential}.get(connection['settings'].get('auth_method'))
    if connection['platform'] != 'REST_API' or not isinstance(credential, expected or ()):
        raise HTTPException(422, 'This connection does not take this kind of credential. MT5 logins are saved on each account.')
    return 'connection_id', connection


def _save_credential(db, actor: str, owner: str, owner_id: UUID, credential: BaseModel,
                     approved: bool = False, reason: str = '') -> dict:
    access_level = getattr(credential, 'access_level', 'FULL')
    column, record = _credential_owner(db, owner, owner_id, credential)
    level_clause = ' AND access_level=%s' if column == 'account_id' else ''
    existing = db.execute(sql.SQL('SELECT id, kind, access_level FROM cfg_credentials WHERE {}=%s' + level_clause).format(
        sql.Identifier(column)), (owner_id, access_level) if level_clause else (owner_id,)).fetchone()
    if existing and record['environment'] == 'LIVE' and not approved and four_eyes_on(db):
        return request_change(db, actor, 'credential', owner_id, f'REPLACE_{"MASTER" if access_level == "FULL" else "INVESTOR"}_CREDENTIAL'
                              if column == 'account_id' else 'REPLACE_API_CREDENTIAL',
                              f"Replace the {'investor' if access_level == 'READ_ONLY' else 'master' if column == 'account_id' else 'API'} "
                              f"credential of LIVE {SPECS[owner].label} {record['code']}",
                              {'owner': owner, 'kind': credential.kind, 'access_level': access_level},
                              secret_fields=_secret_fields(credential), reason=reason)
    credential_id = existing['id'] if existing else uuid4()
    ciphertext = credentials.encrypt(credential_id, _secret_fields(credential))
    key_id = credentials.key_id()
    if existing:
        db.execute('UPDATE cfg_credentials SET kind=%s, access_level=%s, ciphertext=%s, key_id=%s, updated_at=now() WHERE id=%s',
                   (credential.kind, access_level, ciphertext, key_id, credential_id))
    else:
        db.execute(sql.SQL('INSERT INTO cfg_credentials(id, {}, kind, access_level, ciphertext, key_id) VALUES (%s,%s,%s,%s,%s,%s)').format(
            sql.Identifier(column)), (credential_id, owner_id, credential.kind, access_level, ciphertext, key_id))
    if column == 'account_id':
        db.execute('UPDATE cfg_accounts SET password_changed_on=current_date WHERE id=%s', (owner_id,))
    summary = {'kind': credential.kind, 'access_level': access_level}
    audit(db, actor, 'REPLACE_CREDENTIAL' if existing else 'SET_CREDENTIAL', SPECS[owner].label, owner_id,
          {'kind': existing['kind'], 'access_level': existing['access_level']} if existing else None, summary, reason)
    return summary


def clear_credential(actor: str, owner: str, owner_id: UUID, access_level: str | None = None) -> None:
    column = 'account_id' if owner == 'accounts' else 'connection_id'
    with transaction('delete') as db:
        rows = db.execute(sql.SQL('DELETE FROM cfg_credentials WHERE {}=%s AND (%s::text IS NULL OR access_level=%s) RETURNING kind, access_level').format(
            sql.Identifier(column)), (owner_id, access_level, access_level)).fetchall()
        if not rows:
            raise HTTPException(404, 'No credential is stored for this record.')
        for row in rows:
            audit(db, actor, 'CLEAR_CREDENTIAL', SPECS[owner].label, owner_id, dict(row), None)


def use_credential(owner: str, owner_id: UUID, used_by: str, purpose: str, access_level: str = 'FULL') -> dict[str, str]:
    """The only way to read a stored secret: for in-process adapters. Every use is logged
    (append-only cfg_credential_use) before the secret is returned."""
    column = 'account_id' if owner == 'accounts' else 'connection_id'
    with transaction('read') as db:
        row = db.execute(sql.SQL('SELECT id, ciphertext FROM cfg_credentials WHERE {}=%s ORDER BY (access_level=%s) DESC LIMIT 1').format(
            sql.Identifier(column)), (owner_id, access_level)).fetchone()
        if not row:
            raise HTTPException(404, 'No credential is stored for this record.')
        db.execute('INSERT INTO cfg_credential_use(credential_id, owner, owner_id, used_by, purpose) VALUES (%s,%s,%s,%s,%s)',
                   (row['id'], 'account' if owner == 'accounts' else 'connection', owner_id, used_by[:120], purpose[:200]))
    return credentials.decrypt(row['id'], bytes(row['ciphertext']))


def credential_use(owner_id: UUID, limit: int = 100) -> list[dict]:
    with transaction('read') as db:
        return [clean(r) for r in db.execute('SELECT used_by, purpose, used_at, owner FROM cfg_credential_use WHERE owner_id=%s '
                                             'ORDER BY used_at DESC LIMIT %s', (owner_id, limit)).fetchall()]


# ---------- execution controls and audit ----------

def list_execution_controls() -> list[dict]:
    with transaction('read') as db:
        return list(execution_controls(db).values())


def set_execution_control(actor: str, environment: str, enabled: bool, reason: str, version: int,
                          confirm: str | None) -> dict:
    if enabled and confirm != environment:
        raise HTTPException(422, f'To turn order sending on, confirm by entering the environment name: {environment}.')
    with transaction('update') as db:
        before = db.execute('SELECT * FROM cfg_execution_controls WHERE environment=%s FOR UPDATE', (environment,)).fetchone()
        if not before:
            raise HTTPException(404, 'Unknown environment.')
        if before['version'] != version:
            raise HTTPException(409, 'This control was changed by someone else. Reload before changing it.')
        after = db.execute('UPDATE cfg_execution_controls SET trading_enabled=%s, reason=%s, updated_by=%s, '
                           'updated_at=now(), version=version+1 WHERE environment=%s RETURNING *',
                           (enabled, reason, actor, environment)).fetchone()
        audit(db, actor, 'ORDER_SENDING_ON' if enabled else 'ORDER_SENDING_OFF', 'execution control',
              environment, before, after, reason)
        from .alerts import enqueue
        enqueue(db, 'ORDER_SENDING', 'CRITICAL' if enabled and environment == 'LIVE' else 'WARNING',
                f"Order sending {'ON' if enabled else 'OFF'} for {environment}", f'By {actor}. Reason: {reason}')
        return clean(after)


def list_audit(entity: str | None, entity_id: str | None, limit: int) -> list[dict]:
    clauses, params = [], []
    if entity:
        clauses.append('entity=%s'); params.append(entity)
    if entity_id:
        clauses.append('entity_id=%s'); params.append(entity_id)
    where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
    with transaction('read') as db:
        return [clean(r) for r in db.execute(
            f'SELECT * FROM cfg_audit_log{where} ORDER BY id DESC LIMIT %s', (*params, limit)).fetchall()]


def schema_ready() -> bool:
    try:
        with transaction('read') as db:
            for table in ('cfg_providers', 'cfg_connections', 'cfg_accounts', 'cfg_instruments',
                          'cfg_symbol_mappings', 'cfg_credentials', 'cfg_execution_controls', 'cfg_audit_log',
                          'cfg_users', 'cfg_sessions', 'cfg_strategies', 'cfg_strategy_versions', 'cfg_deployments',
                          'cfg_latency_checks', 'cfg_change_requests', 'cfg_policies', 'cfg_alert_channels',
                          'cfg_account_groups', 'cfg_credential_use', 'cfg_user_prefs'):
                db.execute(sql.SQL('SELECT 1 FROM {} LIMIT 0').format(sql.Identifier(table)))
        return True
    except HTTPException:
        return False


# ---------- MT5 latency history and one-step account setup ----------

def record_latency(actor: str, connection_id: UUID, measured_from: str, results: list[dict]) -> None:
    with transaction('update') as db:
        connection = _connection(db, connection_id)
        if connection['platform'] != 'MT5':
            raise HTTPException(422, 'Latency is measured for MT5 connections only.')
        for r in results:
            db.execute('INSERT INTO cfg_latency_checks(connection_id,host,port,reachable,latency_ms,error,measured_from,measured_by) '
                       'VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                       (connection_id, r['host'], r['port'], r['reachable'], r['latency_ms'], (r['error'] or None) and r['error'][:200],
                        measured_from, actor))


# FASTEST routing is only as good as its measurements: a measurement older than this is taken again
# automatically before COREI logs in (broker reads, collector start).
LATENCY_MAX_AGE = timedelta(hours=6)


def refresh_latency_if_stale(connection_id, actor: str = 'system', force: bool = False) -> bool:
    """Measure every enabled address of an MT5 server again when any is unmeasured or older than
    LATENCY_MAX_AGE (or when forced). Returns True when a measurement was taken. Never raises:
    a failed refresh leaves the previous order in place."""
    from . import latency as latency_check
    try:
        with transaction('read') as db:
            conn = db.execute('SELECT platform, adapter, settings FROM cfg_connections WHERE id=%s', (connection_id,)).fetchone()
            if not conn or conn['platform'] != 'MT5' or conn['adapter'] == 'TERMINAL_BRIDGE':
                return False
            points = [(p['host'], int(p['port'])) for p in conn['settings'].get('access_points', []) if p.get('enabled', True)]
            latest = latest_latency(db, [connection_id]).get(str(connection_id), {})
        now = datetime.now(timezone.utc)
        stale = [k for k in points if k not in latest or now - datetime.fromisoformat(latest[k]['measured_at']) > LATENCY_MAX_AGE]
        if not points or (not stale and not force):
            return False
        measured = latency_check.measure_blocking(points)
        record_latency(actor, connection_id, measured['measured_from'], measured['results'])
        return True
    except Exception:  # noqa: BLE001 - routing falls back to the last order
        _log.warning('latency refresh failed for connection %s', connection_id, exc_info=True)
        return False


def connection_latency(connection_id: UUID) -> list[dict]:
    with transaction('read') as db:
        latest = latest_latency(db, [connection_id]).get(str(connection_id), {})
    return [{'host': h, 'port': p, **v} for (h, p), v in latest.items()]


def _server_key(name: str) -> str:
    return ' '.join(name.split()).lower()


def _connection_code(db, provider_code: str, environment: str, tag: str = 'MT5') -> str:
    base = f'{provider_code}_{tag}_{environment}'[:29].rstrip('_')
    taken = {r['code'] for r in db.execute('SELECT code FROM cfg_connections WHERE code LIKE %s', (base + '%',)).fetchall()}
    code, n = base, 2
    while code in taken:
        code, n = f'{base}_{n}', n + 1
    return code


def setup_venue(actor: str, data, approved: bool = False) -> dict:
    """Who + server in one transaction: create the provider when new, then the connection. The connection's
    code and name are filled in when left empty. A LIVE connection that adds addresses needs approval
    (four-eyes); the new provider is kept and the connection waits as a change request."""
    with transaction('create') as db:
        provider_action = 'REUSED'
        if data.provider is not None:
            provider_id = _insert(db, actor, 'providers', data.provider, 'Created with its first connection')
            provider_action = 'CREATED'
        else:
            provider_id = data.provider_id
        provider = db.execute('SELECT * FROM cfg_providers WHERE id=%s', (provider_id,)).fetchone()
        if not provider:
            raise HTTPException(422, 'Provider does not exist.')
        if provider['status'] == 'ARCHIVED':
            raise HTTPException(409, f"Provider {provider['code']} is archived. Restore it first.")
        raw = dict(data.connection)
        platform, env = raw.get('platform'), raw.get('environment', 'DEMO')
        tag = {'MT5': 'MT5', 'FIX': 'FIX', 'REST_API': 'API'}.get(platform, 'CONN')
        if not raw.get('code'):
            raw['code'] = _connection_code(db, provider['code'], env, tag)
        if not raw.get('name'):
            where = (raw.get('settings') or {})
            label = where.get('server_name') or (f"{where.get('host')}:{where.get('port')}" if where.get('host') else where.get('api_base_url', ''))
            raw['name'] = f"{provider['name']} {tag} {str(env).lower()} · {label}"[:120]
        try:
            connection = ConnectionIn.model_validate({**raw, 'provider_id': str(provider_id)})
        except ValidationError as exc:
            def where(loc):  # settings are checked inside the connection model; name them as settings fields
                path = '.'.join(str(p) for p in loc)
                return 'connection.' + (path if not loc or loc[0] in ConnectionIn.model_fields else 'settings.' + path)
            raise HTTPException(422, [{'field': where(e['loc']), 'message': e['msg'].removeprefix('Value error, ')}
                                      for e in exc.errors()]) from None
        values = connection.model_dump(mode='json')
        if not approved and four_eyes_on(db):
            reasons = _connection_needs_approval(None, values)
            if reasons:
                _rules(db, 'connections', connection.model_dump(mode='python'), None)
                pending = request_change(db, actor, 'connections', uuid4(), 'CREATE_CONNECTION',
                                         f"New LIVE connection {values['code']} for {provider['code']} {' and '.join(reasons)}", {'record': values})
                return {**pending, 'provider_action': provider_action, 'provider': clean(provider)}
        connection_id = _insert(db, actor, 'connections', connection, 'Added under Connectivity')
        return {'provider_action': provider_action, 'provider': clean(provider),
                'connection': get_record(db, 'connections', connection_id)}


UPSTOX_API = 'https://api.upstox.com'


def setup_upstox_account(actor: str, data: dict, credential: BaseModel | None, base_url: str) -> dict:
    """Upstox in one step: the broker (created if missing), its API connection (reused when it exists),
    the app's key and secret (saved on the connection when it has none), then the account. Upstox has no
    demo environment for market data, so the connection is LIVE; nothing here can send an order."""
    redirect = base_url.rstrip('/') + '/api/oauth/upstox/callback'
    with transaction('create') as db:
        provider = db.execute("SELECT * FROM cfg_providers WHERE upper(code) IN ('UPSTOX','UPSTOK') AND status <> 'ARCHIVED' "
                              "ORDER BY code='UPSTOX' DESC LIMIT 1").fetchone()
        provider_action = 'REUSED'
        if not provider:
            pid = _insert(db, actor, 'providers', ProviderIn.model_validate({
                'code': 'UPSTOX', 'name': 'Upstox', 'kind': 'BROKER', 'legal_entity': 'RKSV Securities India Pvt. Ltd.',
                'regulator': 'SEBI', 'jurisdiction': 'India', 'execution_model': 'EXCHANGE', 'website': 'https://upstox.com'}),
                'Created while adding an Upstox account')
            provider = db.execute('SELECT * FROM cfg_providers WHERE id=%s', (pid,)).fetchone()
            provider_action = 'CREATED'
        connection = db.execute("SELECT * FROM cfg_connections WHERE provider_id=%s AND platform='REST_API' AND status <> 'ARCHIVED' "
                                "AND settings->>'api_base_url' LIKE '%%upstox%%' ORDER BY created_at LIMIT 1 FOR UPDATE",
                                (provider['id'],)).fetchone()
        connection_action = 'REUSED'
        if not connection:
            cid = _insert(db, actor, 'connections', ConnectionIn.model_validate({
                'provider_id': provider['id'], 'code': _connection_code(db, provider['code'], 'LIVE', 'API'),
                'name': 'Upstox API', 'platform': 'REST_API', 'environment': 'LIVE', 'purpose': 'BOTH', 'adapter': 'NATIVE',
                'settings': {'api_base_url': UPSTOX_API, 'stream_url': 'wss://api.upstox.com/v3/feed/market-data-feed',
                             'auth_method': 'OAUTH2', 'oauth_redirect_url': redirect},
                'server_utc_offset_minutes': 330, 'dst_rule': 'NONE', 'clock_source': 'MANUAL',
                'max_orders_per_second': 10, 'max_order_burst': 10}), 'Created while adding an Upstox account')
            connection = db.execute('SELECT * FROM cfg_connections WHERE id=%s', (cid,)).fetchone()
            connection_action = 'CREATED'
        elif connection['settings'].get('auth_method') != 'OAUTH2':
            raise HTTPException(409, f"Connection {connection['code']} uses API keys, not the Upstox login. Archive it or edit it first.")
        has_key = db.execute('SELECT 1 FROM cfg_credentials WHERE connection_id=%s', (connection['id'],)).fetchone()
        if not has_key and credential is None:
            raise HTTPException(422, [{'field': 'credential', 'message': 'Enter the API key and API secret of your Upstox app.'}])
        try:
            account = AccountIn.model_validate({**data, 'connection_id': str(connection['id'])})
        except ValidationError as exc:
            raise HTTPException(422, [{'field': 'account.' + '.'.join(str(p) for p in e['loc']),
                                       'message': e['msg'].removeprefix('Value error, ')} for e in exc.errors()]) from None
        account_id = _insert(db, actor, 'accounts', account, 'Upstox account')
        if credential is not None and not has_key:
            _save_credential(db, actor, 'connections', connection['id'], credential, approved=True)
        return {'provider_action': provider_action, 'connection_action': connection_action,
                'redirect_url': connection['settings'].get('oauth_redirect_url'),
                'connection': get_record(db, 'connections', connection['id']), 'account': get_record(db, 'accounts', account_id)}


def _mt5_account(db, account_id, lock: bool = False) -> tuple[dict, dict]:
    account = db.execute('SELECT * FROM cfg_accounts WHERE id=%s' + (' FOR UPDATE' if lock else ''), (account_id,)).fetchone()
    if not account:
        raise HTTPException(404, 'Account not found.')
    connection = db.execute('SELECT * FROM cfg_connections WHERE id=%s', (account['connection_id'],)).fetchone()
    if connection['platform'] != 'MT5':
        raise HTTPException(409, 'Servers and addresses are chosen per account for MT5 accounts only.')
    return account, connection


def move_mt5_account(actor: str, account_id: UUID, data, approved: bool = False, reason: str = '') -> dict:
    """Put an MT5 account on another server of the same broker: the server record for (broker, environment,
    server name) is reused or created, addresses it lacks are added, and the account moves with its id,
    password, groups and history. Pinned addresses belong to the old server, so the account goes back to
    'fastest'. A LIVE move changes where the password is sent: it needs approval under four-eyes."""
    from .config_models import Mt5Settings
    with transaction('update') as db:
        account, current = _mt5_account(db, account_id, lock=True)
        if account['version'] != data.version:
            raise HTTPException(409, 'This account was changed by someone else. Reload and try again.')
        if current['adapter'] == 'TERMINAL_BRIDGE':
            raise HTTPException(409, 'This account is read through the MT5 terminal bridge: the terminal chooses the server. '
                                     'Log the terminal in to the new server instead.')
        env = current['environment']
        target_name = data.server.server_name
        if _server_key(target_name) == _server_key(current['settings'].get('server_name', '')):
            raise HTTPException(409, f'The account is already on {target_name}. Use Refresh addresses to update its addresses.')
        matches = [c for c in db.execute(
            "SELECT * FROM cfg_connections WHERE provider_id=%s AND platform='MT5' AND environment=%s AND status <> 'ARCHIVED' "
            'ORDER BY code FOR UPDATE', (current['provider_id'], env)).fetchall()
            if _server_key(c['settings'].get('server_name', '')) == _server_key(target_name)]
        target = matches[0] if matches else None
        if env == 'LIVE' and not approved and four_eyes_on(db):
            return request_change(db, actor, 'accounts', account_id, 'ACCOUNT_SERVER',
                                  f"Move LIVE MT5 account {account['code']} (login {account['external_account_id']}) from "
                                  f"{current['settings'].get('server_name')} to {target_name}"
                                  f"{'' if target else ' (new server record)'}",
                                  {'account_id': str(account_id), 'move': data.model_dump(mode='json')},
                                  base_version=data.version, reason=data.reason)
        action = 'REUSED'
        if target is None:
            provider = db.execute('SELECT * FROM cfg_providers WHERE id=%s', (current['provider_id'],)).fetchone()
            new = ConnectionIn.model_validate({
                'provider_id': current['provider_id'], 'code': _connection_code(db, provider['code'], env),
                'name': f"{provider['name']} MT5 {env.lower()} · {target_name}"[:120], 'platform': 'MT5', 'environment': env,
                'purpose': current['purpose'], 'adapter': current['adapter'], 'settings': data.server.model_dump(mode='json')})
            target_id = _insert(db, actor, 'connections', new, reason or data.reason or f"Created when {account['code']} moved server")
            action = 'CREATED'
        else:
            target_id = target['id']
            have = {(p['host'], int(p['port'])) for p in target['settings'].get('access_points', [])}
            extra = [p for p in data.server.access_points if (p.host, p.port) not in have]
            if extra:
                settings = Mt5Settings.model_validate({**target['settings'], 'access_points': [
                    *target['settings'].get('access_points', []), *[p.model_dump(mode='json') for p in extra]]}).model_dump(mode='json')
                after = db.execute('UPDATE cfg_connections SET settings=%s, version=version+1, updated_at=now() WHERE id=%s RETURNING *',
                                   (Jsonb(settings), target_id)).fetchone()
                audit(db, actor, 'UPDATE', 'connection', target_id, target, after, f"Added {len(extra)} address(es) when {account['code']} moved server")
                action = 'ADDRESSES_ADDED'
        mode = 'FASTEST' if account['access_mode'] == 'ACCOUNT' else account['access_mode']
        after = db.execute("UPDATE cfg_accounts SET connection_id=%s, access_mode=%s, access_points='[]'::jsonb, version=version+1, "
                           'updated_at=now() WHERE id=%s RETURNING *', (target_id, mode, account_id)).fetchone()
        audit(db, actor, 'MOVE_SERVER', 'account', account_id, account, after,
              reason or data.reason or f"{current['settings'].get('server_name')} → {target_name}")
        return {'connection_action': action, 'account': get_record(db, 'accounts', account_id),
                'connection': get_record(db, 'connections', target_id)}


def refresh_mt5_addresses(actor: str, account_id: UUID, catalog_id: str | None = None) -> dict:
    """The account's server as the broker publishes it now (MT5 broker directory, or an analysed
    servers.dat): addresses the record lacks are added (LIVE: through approval under four-eyes),
    addresses the broker no longer lists are reported and left for a person to untick, then every
    address is measured so 'fastest' uses the new list."""
    from . import broker_catalog
    from .config_models import Mt5Settings
    with transaction('read') as db:
        account, connection = _mt5_account(db, account_id)
        provider = db.execute('SELECT name FROM cfg_providers WHERE id=%s', (connection['provider_id'],)).fetchone()
    name = connection['settings'].get('server_name', '')
    if catalog_id:
        listed = broker_catalog.server(broker_catalog.recall(catalog_id, actor), name)
        source = 'SERVERS_DAT'
    else:
        listed = None
        for term in dict.fromkeys([name.split('-')[0].strip(), provider['name']]):
            try:
                found = broker_catalog.search_directory(term[:64])
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
                continue
            listed = next((e for e in found['servers'] if _server_key(e['server_name']) == _server_key(name)), None)
            if listed:
                break
        if listed is None:
            raise HTTPException(404, f'The MT5 broker directory does not list {name}. Use a servers.dat from a terminal logged in to it.')
        source = 'DIRECTORY'
    have = {(p['host'], int(p['port'])) for p in connection['settings'].get('access_points', [])}
    published = {(p['host'].lower(), int(p['port'])): p for p in listed['access_points']}
    extra = [{'name': p.get('name') or name, 'host': h, 'port': port, 'enabled': True, 'source': source,
              'confirmed_at': datetime.now(timezone.utc).isoformat(timespec='seconds')} for (h, port), p in published.items() if (h, port) not in have]
    not_listed = [{'host': h, 'port': p} for (h, p) in have if (h, p) not in published]
    pending = None
    if extra:
        settings = Mt5Settings.model_validate({**connection['settings'], 'access_points': [*connection['settings'].get('access_points', []), *extra]})
        record = ConnectionIn.model_validate({k: connection[k] for k in ConnectionIn.model_fields if k in connection} | {
            'settings': settings.model_dump(mode='json')})
        result = update(actor, 'connections', connection['id'], record, connection['version'],
                        reason=f"{len(extra)} address(es) published by the broker ({source.lower().replace('_', '.')})")
        pending = result.get('change_request')
    if not pending:
        refresh_latency_if_stale(connection['id'], actor, force=True)
    with transaction('read') as db:
        return {'server_name': name, 'source': source, 'published': len(published), 'added': 0 if pending else len(extra),
                'awaiting_approval': len(extra) if pending else 0, 'change_request': pending, 'not_listed': not_listed,
                'account': get_record(db, 'accounts', account_id)}


def setup_mt5_account(actor: str, data: Mt5AccountSetupIn, approved: bool = False, reason: str = '') -> dict:
    """Login + server in one step. Finds the provider's connection for this server and
    environment (or the one named), adds addresses it lacks when asked, creates it when
    there is none, then creates the account and stores its password — all or nothing."""
    picked = [(p.host, p.port) for p in data.server.access_points]
    with transaction('create') as db:
        provider = db.execute('SELECT * FROM cfg_providers WHERE id=%s', (data.provider_id,)).fetchone()
        if not provider:
            raise HTTPException(422, 'Provider does not exist.')
        if data.connection_id:
            connection = db.execute('SELECT * FROM cfg_connections WHERE id=%s FOR UPDATE', (data.connection_id,)).fetchone()
            if not connection or connection['provider_id'] != data.provider_id or connection['platform'] != 'MT5' \
                    or connection['environment'] != data.environment \
                    or _server_key(connection['settings'].get('server_name', '')) != _server_key(data.server.server_name):
                raise HTTPException(422, 'The chosen connection is not this provider\'s MT5 connection for this server and environment.')
        else:
            matches = [c for c in db.execute(
                "SELECT * FROM cfg_connections WHERE provider_id=%s AND platform='MT5' AND environment=%s ORDER BY code FOR UPDATE",
                (data.provider_id, data.environment)).fetchall()
                if _server_key(c['settings'].get('server_name', '')) == _server_key(data.server.server_name)]
            if len(matches) > 1:
                raise HTTPException(409, f'{len(matches)} connections use server {data.server.server_name}: '
                                         f"{', '.join(c['code'] for c in matches)}. Choose one.")
            connection = matches[0] if matches else None
        if data.environment == 'LIVE' and not approved and four_eyes_on(db):
            have = {(p['host'], int(p['port'])) for p in (connection['settings'].get('access_points', []) if connection else [])}
            extra = [p for p in data.server.access_points if (p.host, p.port) not in have]
            if connection is None or (extra and data.add_missing_addresses):
                what = (f'creates LIVE connection for {data.server.server_name}' if connection is None
                        else f'adds {len(extra)} address(es) to LIVE connection {connection["code"]}')
                return request_change(db, actor, 'accounts', uuid4(), 'ACCOUNT_SETUP',
                                      f"Add MT5 account {data.account.get('code')} (login {data.account.get('external_account_id')}): {what}",
                                      {'setup': data.model_dump(mode='json', exclude={'credential', 'bridge_token'}),
                                       'credential': {'kind': 'MT5_LOGIN', 'access_level': data.credential.access_level} if data.credential else None},
                                      secret_fields={**(_secret_fields(data.credential) if data.credential else {}),
                                                     **({'bridge_token': data.bridge_token.get_secret_value()} if data.bridge_token else {})} or None,
                                      reason=reason)
        connection_action = 'REUSED'
        if connection is None:
            new = ConnectionIn.model_validate({
                'provider_id': data.provider_id, 'code': _connection_code(db, provider['code'], data.environment),
                'name': f"{provider['name']} MT5 {data.environment.lower()} · {data.server.server_name}"[:120],
                'platform': 'MT5', 'environment': data.environment, 'purpose': 'BOTH', 'adapter': data.adapter,
                'settings': data.server.model_dump(mode='json')})
            connection_id = _insert(db, actor, 'connections', new, reason or 'Created while adding an MT5 account')
            connection_action = 'CREATED'
            if data.adapter == 'TERMINAL_BRIDGE' and data.bridge_token:
                _save_credential(db, actor, 'connections', connection_id, BridgeTokenCredential(kind='BRIDGE_TOKEN', token=data.bridge_token),
                                 approved=True, reason=reason)
        else:
            connection_id = connection['id']
            have = {(p['host'], int(p['port'])) for p in connection['settings'].get('access_points', [])}
            extra = [p for p in data.server.access_points if (p.host, p.port) not in have]
            if extra and data.add_missing_addresses:
                settings = dict(connection['settings'])
                settings['access_points'] = [*settings.get('access_points', []),
                                             *[{**p.model_dump(mode='json'), 'enabled': False} for p in extra]]
                settings = Mt5Settings.model_validate(settings).model_dump(mode='json')
                after = db.execute('UPDATE cfg_connections SET settings=%s, version=version+1, updated_at=now() WHERE id=%s RETURNING *',
                                   (Jsonb(settings), connection_id)).fetchone()
                audit(db, actor, 'UPDATE', 'connection', connection_id, connection, after,
                      f'Added {len(extra)} address(es) while adding an MT5 account')
                connection_action = 'ADDRESSES_ADDED'
        payload = {**data.account, 'connection_id': str(connection_id)}
        try:
            account = AccountIn.model_validate(payload)
        except ValidationError as exc:
            raise HTTPException(422, [{'field': 'account.' + '.'.join(str(p) for p in e['loc']),
                                       'message': e['msg'].removeprefix('Value error, ')} for e in exc.errors()]) from None
        account_id = _insert(db, actor, 'accounts', account, reason)
        if data.credential:
            _save_credential(db, actor, 'accounts', account_id, data.credential, approved=True, reason=reason)
        return {'connection_action': connection_action, 'connection': get_record(db, 'connections', connection_id),
                'account': get_record(db, 'accounts', account_id), 'picked_addresses': len(picked)}
