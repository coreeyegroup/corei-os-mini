"""One-form broker account onboarding. Configuration storage only; no sessions/orders."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, ValidationError
import psycopg
from starlette.concurrency import run_in_threadpool

from .broker_catalog import MAX_FILE, parse_catalog
from .broker_secrets import credential_key, encrypt_password
from .config import get_settings
from .operator_auth import require_operator
from .persistence import _connect, PersistenceNotConfigured

router = APIRouter(prefix='/api/registry/broker-accounts', dependencies=[Depends(require_operator)])
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class NewAccount(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: Label
    broker: Label
    platform: Literal['MT5'] = 'MT5'
    mode: Literal['SANDBOX', 'LIVE']
    login: Annotated[str, StringConstraints(pattern=r'^[1-9][0-9]{0,19}$')]
    password: SecretStr = Field(min_length=1, max_length=256)
    server_name: Label
    access_point_id: UUID


class EditAccount(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: Label
    access_point_id: UUID
    password: SecretStr | None = Field(default=None, min_length=1, max_length=256)
    enabled: bool
    version: int = Field(ge=1)


def validate(model, payload):
    try:
        result = model.model_validate(payload)
        if isinstance(result, NewAccount) and int(result.login) > 2**64 - 1:
            raise ValueError()
        return result
    except (ValidationError, ValueError):
        # Pydantic's default HTTP errors include input values, including secrets.
        raise HTTPException(422, 'Invalid account fields. Check required values, login number and selected access point.') from None


@contextmanager
def database():
    try:
        with _connect(get_settings().database_url) as db:
            yield db
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, 'This broker-server/login account is already saved. Refresh the list instead of adding it again.') from None
    except (psycopg.Error, PersistenceNotConfigured):
        raise HTTPException(503, 'Account storage unavailable. Check the database and apply migrations.') from None


def encrypted(secret_id, password):
    try:
        return encrypt_password(secret_id, password.get_secret_value())
    except (OSError, ValueError):
        raise HTTPException(503, 'Credential storage is not configured. Run the broker-account setup command.') from None


SELECT = '''SELECT a.id,a.name,a.external_reference AS login,a.mode,a.enabled,
 p.name AS broker,s.platform,s.server_name,s.version,s.access_point_id,
 ep.name AS access_point_name,ep.host,ep.port
 FROM corei_broker_account_settings s JOIN corei_accounts a ON a.id=s.account_id
 JOIN corei_connections c ON c.id=a.connection_id JOIN corei_providers p ON p.id=c.provider_id
 JOIN corei_mt5_access_points ep ON ep.id=s.access_point_id'''


def public(row):
    return {**row, 'password_saved': True, 'session_status': 'NOT_CONNECTED',
            'account_verified': False, 'execution_available': False}


def selected_endpoint(db, identifier, server):
    row = db.execute('SELECT * FROM corei_mt5_access_points WHERE id=%s', (identifier,)).fetchone()
    if not row or row['server_name'] != server:
        raise HTTPException(422, 'Select an access point belonging to the exact broker-provided server name.')
    return row


@router.get('')
def list_accounts(response: Response, limit: int = Query(50, ge=1, le=200),
                  offset: int = Query(0, ge=0), q: str = Query('', max_length=120)):
    response.headers['Cache-Control'] = 'no-store'
    pattern = '%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    where = ' WHERE a.name ILIKE %s OR p.name ILIKE %s OR s.login ILIKE %s OR s.server_name ILIKE %s'
    with database() as db:
        rows = db.execute(SELECT + where + ' ORDER BY a.created_at DESC,a.id LIMIT %s OFFSET %s',
                          (pattern,)*4 + (limit, offset)).fetchall()
        total = db.execute('SELECT count(*) AS n FROM (' + SELECT + where + ') matches', (pattern,)*4).fetchone()['n']
        legacy = db.execute('SELECT count(*) AS n FROM corei_accounts a WHERE NOT EXISTS '
                            '(SELECT 1 FROM corei_broker_account_settings s WHERE s.account_id=a.id)').fetchone()['n']
    try:
        credential_key()
        ready = True
    except (OSError, ValueError):
        ready = False
    return dict(items=[public(r) for r in rows], total=total, legacy_count=legacy,
                credential_storage_ready=ready, limit=limit, offset=offset)


@router.post('/server-catalog')
async def import_catalog(request: Request, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > MAX_FILE:
            raise HTTPException(413, 'Server file exceeds 1 MiB.')
    data = bytes(chunks)
    rows = await run_in_threadpool(parse_catalog, data)
    return await run_in_threadpool(save_catalog, data, rows)


def save_catalog(data, rows):
    digest = hashlib.sha256(data).hexdigest()
    with database() as db:
        catalog = db.execute('INSERT INTO corei_mt5_catalogs(id,sha256,source_bytes) VALUES(%s,%s,%s) '
             'ON CONFLICT(sha256) DO UPDATE SET sha256=EXCLUDED.sha256 RETURNING id',
             (uuid4(), digest, data)).fetchone()['id']
        for row in rows:
            db.execute('INSERT INTO corei_mt5_access_points(id,catalog_id,server_name,name,host,port) '
                       'VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                       (uuid4(),catalog,row['server_name'],row['name'],row['host'],row['port']))
        points = db.execute('SELECT id,server_name,name,host,port FROM corei_mt5_access_points '
                            'WHERE catalog_id=%s ORDER BY server_name,name,host,port', (catalog,)).fetchall()
    return {'sha256': digest, 'items': points}


@router.post('', status_code=201)
def create_account(payload: dict, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    data = validate(NewAccount, payload)
    account_id, connection_id, secret_id = uuid4(), uuid4(), uuid4()
    ciphertext = encrypted(secret_id, data.password)
    with database() as db:
        selected_endpoint(db, data.access_point_id, data.server_name)
        # Serialise case-insensitive provider lookup/creation; do not change existing identity.
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('broker:' + data.broker.lower(),))
        provider = db.execute('SELECT id,kind FROM corei_providers WHERE lower(name)=lower(%s) ORDER BY created_at LIMIT 1', (data.broker,)).fetchone()
        if provider and provider['kind'] != 'BROKER':
            raise HTTPException(409, 'That organisation exists as a non-broker provider. Use the correct broker name.')
        provider_id = provider['id'] if provider else uuid4()
        if not provider:
            db.execute('INSERT INTO corei_providers(id,name,kind) VALUES(%s,%s,\'BROKER\')', (provider_id, data.broker))
        db.execute('INSERT INTO corei_connections(id,provider_id,name,purpose) VALUES(%s,%s,%s,\'BOTH\')',
                   (connection_id,provider_id,'MT5 ' + str(account_id)))
        db.execute('INSERT INTO corei_connector_profiles(id,connection_id,name,connector_type,adapter_key,environment,runtime,server_name) '
                   "VALUES(%s,%s,%s,'MT5_DIRECT','mt5_direct',%s,'SERVER_API',%s)",
                   (uuid4(),connection_id,data.name,data.mode,data.server_name))
        db.execute('INSERT INTO corei_accounts(id,connection_id,name,external_reference,mode) VALUES(%s,%s,%s,%s,%s)',
                   (account_id,connection_id,data.name,data.login,data.mode))
        db.execute('INSERT INTO corei_broker_secrets(id,ciphertext) VALUES(%s,%s)', (secret_id,ciphertext))
        db.execute('INSERT INTO corei_broker_account_settings(account_id,secret_id,server_name,login,access_point_id) '
                   'VALUES(%s,%s,%s,%s,%s)', (account_id,secret_id,data.server_name,data.login,data.access_point_id))
        return public(db.execute(SELECT + ' WHERE a.id=%s', (account_id,)).fetchone())


@router.put('/{account_id}')
def edit_account(account_id: UUID, payload: dict, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    data = validate(EditAccount, payload)
    with database() as db:
        current = db.execute('SELECT * FROM corei_broker_account_settings WHERE account_id=%s FOR UPDATE', (account_id,)).fetchone()
        if not current:
            raise HTTPException(404, 'Saved broker account not found')
        if current['version'] != data.version:
            raise HTTPException(409, 'Account was changed in another window. Refresh before editing.')
        selected_endpoint(db,data.access_point_id,current['server_name'])
        if data.password is not None:
            db.execute('UPDATE corei_broker_secrets SET ciphertext=%s,updated_at=now() WHERE id=%s',
                       (encrypted(current['secret_id'],data.password),current['secret_id']))
        db.execute('UPDATE corei_accounts SET name=%s,enabled=%s WHERE id=%s', (data.name,data.enabled,account_id))
        db.execute('UPDATE corei_broker_account_settings SET access_point_id=%s,version=version+1,updated_at=now() WHERE account_id=%s',
                   (data.access_point_id,account_id))
        return public(db.execute(SELECT + ' WHERE a.id=%s', (account_id,)).fetchone())


def onboarding_ready(database_url):
    if not database_url:
        return False
    try:
        credential_key()
        if not Path(os.getenv('COREI_MT5_CATALOG_PARSER', '/opt/corei-os-mini/server-catalog/ServerCatalog')).is_file():
            return False
        with _connect(database_url) as db:
            db.execute(SELECT + ' LIMIT 0')
            db.execute('SELECT id,ciphertext FROM corei_broker_secrets LIMIT 0')
            db.execute('SELECT id,source_bytes FROM corei_mt5_catalogs LIMIT 0')
        return True
    except (OSError, ValueError, psycopg.Error, PersistenceNotConfigured):
        return False
