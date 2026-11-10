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
from decimal import Decimal
import json
import re
from typing import Any, Iterator
from uuid import UUID, uuid4

from fastapi import HTTPException
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from . import credentials
from .config import get_settings
from .config_models import (AccountIn, ConnectionIn, InstrumentIn, MappingIn, ProviderIn,
                            ApiKeyCredential, OAuthClientCredential, Mt5LoginCredential)
from .persistence import PersistenceNotConfigured, _connect

# Provider adapters register their platform here when implemented. Until then every
# account reports ADAPTER_NOT_AVAILABLE rather than implying it can connect.
AVAILABLE_ADAPTERS: set[str] = set()


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
    'mappings': Spec('cfg_symbol_mappings', 'symbol mapping', MappingIn, ('connection_id', 'instrument_id')),
}

UNIQUE_MESSAGES = {
    'cfg_providers_code_key': 'A provider with this code already exists.',
    'cfg_connections_code_key': 'A connection with this code already exists.',
    'cfg_accounts_code_key': 'An account with this code already exists.',
    'cfg_accounts_connection_id_external_account_id_key': 'This broker account number is already registered on this connection.',
    'cfg_instruments_symbol_key': 'An instrument with this symbol already exists.',
    'cfg_symbol_mappings_connection_id_broker_symbol_key': 'This broker symbol is already mapped on this connection.',
    'cfg_symbol_mappings_connection_id_instrument_id_key': 'This instrument is already mapped on this connection.',
}


# ---------- transaction and error handling ----------

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
    except psycopg.errors.ForeignKeyViolation:
        if operation == 'delete':
            raise HTTPException(409, 'This record is used by other records. Remove or deactivate those first.') from None
        raise HTTPException(422, 'A referenced record does not exist.') from None
    except psycopg.errors.CheckViolation:
        raise HTTPException(422, 'A value is outside its allowed range.') from None
    except credentials.CredentialStorageUnavailable as exc:
        raise HTTPException(503, f'Credential storage is not configured: {exc}. Run scripts/setup-broker-accounts.sh.') from None
    except (psycopg.Error, PersistenceNotConfigured):
        raise HTTPException(503, 'Configuration database unavailable. Check the database and apply migrations.') from None


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
        (SELECT count(*) FROM cfg_symbol_mappings m WHERE m.connection_id=c.id) AS mapping_count
        FROM cfg_connections c JOIN cfg_providers p ON p.id=c.provider_id
        LEFT JOIN cfg_credentials cr ON cr.connection_id=c.id''',
    'accounts': '''SELECT a.*, c.code AS connection_code, c.name AS connection_name, c.platform, c.environment,
        c.purpose, c.status AS connection_status, c.settings->>'auth_method' AS auth_method,
        p.id AS provider_id, p.code AS provider_code, p.name AS provider_name, p.status AS provider_status,
        acr.kind AS credential_kind, acr.access_level AS credential_access_level,
        acr.updated_at AS credential_updated_at, ccr.kind AS connection_credential_kind
        FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id
        JOIN cfg_providers p ON p.id=c.provider_id
        LEFT JOIN cfg_credentials acr ON acr.account_id=a.id
        LEFT JOIN cfg_credentials ccr ON ccr.connection_id=c.id''',
    'instruments': '''SELECT i.*, (SELECT count(*) FROM cfg_symbol_mappings m WHERE m.instrument_id=i.id) AS mapping_count
        FROM cfg_instruments i''',
    'mappings': '''SELECT m.*, i.symbol AS instrument_symbol, c.code AS connection_code, c.environment,
        c.purpose AS connection_purpose, p.code AS provider_code
        FROM cfg_symbol_mappings m JOIN cfg_instruments i ON i.id=m.instrument_id
        JOIN cfg_connections c ON c.id=m.connection_id JOIN cfg_providers p ON p.id=c.provider_id''',
}
ALIAS = {'providers': 'p', 'connections': 'c', 'accounts': 'a', 'instruments': 'i', 'mappings': 'm'}
FILTERS = {
    'connections': {'provider_id': 'c.provider_id'},
    'accounts': {'provider_id': 'p.id', 'connection_id': 'a.connection_id'},
    'instruments': {'asset_class': 'i.asset_class'},
    'mappings': {'connection_id': 'm.connection_id', 'instrument_id': 'm.instrument_id'},
}
ORDER = {'providers': 'p.code', 'connections': 'p.code, c.code', 'accounts': 'p.code, c.code, a.code',
         'instruments': 'i.symbol', 'mappings': 'c.code, i.symbol'}


def execution_controls(db) -> dict[str, dict]:
    rows = db.execute('SELECT * FROM cfg_execution_controls ORDER BY environment').fetchall()
    return {r['environment']: clean(r) for r in rows}


def account_readiness(row: dict, controls: dict[str, dict]) -> dict[str, list[str]]:
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
    if row['platform'] not in AVAILABLE_ADAPTERS:
        data.append('ADAPTER_NOT_AVAILABLE')
    trading = list(data)
    if row['purpose'] == 'MARKET_DATA':
        trading.append('CONNECTION_DATA_ONLY')
    if row['trading_permission'] != 'TRADING':
        trading.append('ACCOUNT_NOT_PERMITTED_TO_TRADE')
    if row.get('credential_access_level') == 'READ_ONLY':
        trading.append('READ_ONLY_CREDENTIAL')
    if not controls.get(row['environment'], {}).get('trading_enabled'):
        trading.append(f"ORDER_SENDING_OFF_{row['environment']}")
    return {'data_blockers': data, 'trading_blockers': trading}


def _enrich(entity: str, rows: list[dict], db) -> list[dict]:
    if entity == 'accounts':
        controls = execution_controls(db)
        return [{**clean(r), **account_readiness(r, controls)} for r in rows]
    return [clean(r) for r in rows]


def list_records(entity: str, filters: dict[str, str], q: str = '') -> list[dict]:
    alias = ALIAS[entity]
    clauses, params = [], []
    for key, column in FILTERS.get(entity, {}).items():
        if filters.get(key):
            clauses.append(f'{column}=%s')
            params.append(filters[key])
    if q:
        text_columns = {'providers': ['p.code', 'p.name'], 'connections': ['c.code', 'c.name', 'p.code'],
                        'accounts': ['a.code', 'a.name', 'a.external_account_id', 'c.code', 'p.code'],
                        'instruments': ['i.symbol', 'i.name'], 'mappings': ['m.broker_symbol', 'i.symbol', 'c.code']}[entity]
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
        if data['trading_permission'] == 'TRADING':
            if connection['purpose'] == 'MARKET_DATA':
                raise HTTPException(422, 'This connection is market-data only. Use a trading connection to allow trading.')
            if record_id and db.execute("SELECT 1 FROM cfg_credentials WHERE account_id=%s AND access_level='READ_ONLY'",
                                        (record_id,)).fetchone():
                raise HTTPException(422, 'The saved password is an investor (read-only) password. Save the master password to allow trading.')
    elif entity == 'mappings':
        connection = _connection(db, data['connection_id'])
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

def create(actor: str, entity: str, data: BaseModel) -> dict:
    spec = SPECS[entity]
    values = data.model_dump(mode='python')
    if entity == 'connections':
        values['settings'] = Jsonb(values['settings'])
    record_id = uuid4()
    with transaction('create') as db:
        _rules(db, entity, data.model_dump(mode='python'), None)
        columns = ['id', *values]
        db.execute(sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(
            sql.Identifier(spec.table), sql.SQL(',').join(map(sql.Identifier, columns)),
            sql.SQL(',').join(sql.Placeholder() * len(columns))), (record_id, *values.values()))
        after = db.execute(sql.SQL('SELECT * FROM {} WHERE id=%s').format(sql.Identifier(spec.table)), (record_id,)).fetchone()
        audit(db, actor, 'CREATE', spec.label, record_id, None, after)
        return get_record(db, entity, record_id)


def _locked(db, spec: Spec, record_id: UUID, version: int) -> dict:
    row = db.execute(sql.SQL('SELECT * FROM {} WHERE id=%s FOR UPDATE').format(sql.Identifier(spec.table)), (record_id,)).fetchone()
    if not row:
        raise HTTPException(404, f'{spec.label.capitalize()} not found.')
    if row['version'] != version:
        raise HTTPException(409, f'This {spec.label} was changed by someone else. Reload it before saving.')
    return row


def update(actor: str, entity: str, record_id: UUID, data: BaseModel, version: int) -> dict:
    spec = SPECS[entity]
    values = data.model_dump(mode='python')
    with transaction('update') as db:
        before = _locked(db, spec, record_id, version)
        for field in spec.immutable:
            if str(before[field]) != str(values[field]):
                raise HTTPException(422, f'{field} cannot be changed. Create a new {spec.label} instead.')
        _rules(db, entity, values, record_id)
        changes = {k: v for k, v in values.items() if k not in spec.immutable}
        if entity == 'connections':
            changes['settings'] = Jsonb(changes['settings'])
        assignments = sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(k)) for k in changes)
        db.execute(sql.SQL('UPDATE {} SET {}, version=version+1, updated_at=now() WHERE id=%s').format(
            sql.Identifier(spec.table), assignments), (*changes.values(), record_id))
        after = db.execute(sql.SQL('SELECT * FROM {} WHERE id=%s').format(sql.Identifier(spec.table)), (record_id,)).fetchone()
        audit(db, actor, 'UPDATE', spec.label, record_id, before, after)
        return get_record(db, entity, record_id)


def delete(actor: str, entity: str, record_id: UUID, version: int) -> None:
    spec = SPECS[entity]
    with transaction('delete') as db:
        before = _locked(db, spec, record_id, version)
        db.execute(sql.SQL('DELETE FROM {} WHERE id=%s').format(sql.Identifier(spec.table)), (record_id,))
        audit(db, actor, 'DELETE', spec.label, record_id, before, None)


# ---------- credentials ----------

def _secret_fields(credential: BaseModel) -> dict[str, str]:
    return {name: getattr(credential, name).get_secret_value()
            for name, value in credential if hasattr(value, 'get_secret_value')}


def set_credential(actor: str, owner: str, owner_id: UUID, credential: BaseModel) -> dict:
    access_level = getattr(credential, 'access_level', 'FULL')
    with transaction('update') as db:
        if owner == 'accounts':
            account = db.execute('SELECT a.*, c.platform FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id '
                                 'WHERE a.id=%s FOR UPDATE OF a', (owner_id,)).fetchone()
            if not account:
                raise HTTPException(404, 'Account not found.')
            if account['platform'] != 'MT5' or not isinstance(credential, Mt5LoginCredential):
                raise HTTPException(422, 'Only MT5 accounts hold their own login. API credentials belong to the connection.')
            if access_level == 'READ_ONLY' and account['trading_permission'] == 'TRADING':
                raise HTTPException(422, 'An investor (read-only) password cannot be used on an account permitted to trade. '
                                         'Change the trading permission first or save the master password.')
            column = 'account_id'
        else:
            connection = db.execute('SELECT * FROM cfg_connections WHERE id=%s FOR UPDATE', (owner_id,)).fetchone()
            if not connection:
                raise HTTPException(404, 'Connection not found.')
            expected = {'API_KEY': ApiKeyCredential, 'OAUTH2': OAuthClientCredential}.get(
                connection['settings'].get('auth_method'))
            if connection['platform'] != 'REST_API' or not isinstance(credential, expected or ()):
                raise HTTPException(422, 'This connection does not take this kind of credential. MT5 logins are saved on each account.')
            column = 'connection_id'
        existing = db.execute(sql.SQL('SELECT id, kind, access_level FROM cfg_credentials WHERE {}=%s').format(
            sql.Identifier(column)), (owner_id,)).fetchone()
        credential_id = existing['id'] if existing else uuid4()
        ciphertext = credentials.encrypt(credential_id, _secret_fields(credential))
        if existing:
            db.execute('UPDATE cfg_credentials SET kind=%s, access_level=%s, ciphertext=%s, updated_at=now() WHERE id=%s',
                       (credential.kind, access_level, ciphertext, credential_id))
        else:
            db.execute(sql.SQL('INSERT INTO cfg_credentials(id, {}, kind, access_level, ciphertext) VALUES (%s,%s,%s,%s,%s)').format(
                sql.Identifier(column)), (credential_id, owner_id, credential.kind, access_level, ciphertext))
        summary = {'kind': credential.kind, 'access_level': access_level}
        audit(db, actor, 'REPLACE_CREDENTIAL' if existing else 'SET_CREDENTIAL', SPECS[owner].label, owner_id,
              {'kind': existing['kind'], 'access_level': existing['access_level']} if existing else None, summary)
        return summary


def clear_credential(actor: str, owner: str, owner_id: UUID) -> None:
    column = 'account_id' if owner == 'accounts' else 'connection_id'
    with transaction('delete') as db:
        row = db.execute(sql.SQL('DELETE FROM cfg_credentials WHERE {}=%s RETURNING kind, access_level').format(
            sql.Identifier(column)), (owner_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'No credential is stored for this record.')
        audit(db, actor, 'CLEAR_CREDENTIAL', SPECS[owner].label, owner_id, dict(row), None)


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
                          'cfg_symbol_mappings', 'cfg_credentials', 'cfg_execution_controls', 'cfg_audit_log'):
                db.execute(sql.SQL('SELECT 1 FROM {} LIMIT 0').format(sql.Identifier(table)))
        return True
    except HTTPException:
        return False
