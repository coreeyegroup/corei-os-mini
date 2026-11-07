"""Independent configuration registries; enabled metadata never submits an order."""
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError
import psycopg
from psycopg import sql

from .connector_profiles import ConnectorProfile, profile_view
from .config import get_settings
from .operator_auth import require_operator
from .persistence import _connect, PersistenceNotConfigured

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]

class Record(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = False

class Provider(Record):
    name: Text
    kind: Literal['BROKER','EXCHANGE','DATA_VENDOR']

class Connection(Record):
    provider_id: UUID
    name: Text
    purpose: Literal['DATA','EXECUTION','BOTH']

class Account(Record):
    connection_id: UUID
    name: Text
    external_reference: Text
    mode: Literal['PAPER','SANDBOX','LIVE']

class Instrument(Record):
    symbol: Text
    asset_class: Literal['FX','METAL','CRYPTO','EQUITY','FUTURE','OPTION','OTHER']
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ''

class Mapping(Record):
    connection_id: UUID
    instrument_id: UUID
    native_symbol: Text

class Toggle(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool

MODELS = {'providers':('corei_providers', Provider), 'connections':('corei_connections',Connection),
          'accounts':('corei_accounts',Account), 'instruments':('corei_instruments',Instrument),
          'mappings':('corei_symbol_mappings',Mapping),
          'connector-profiles':('corei_connector_profiles',ConnectorProfile)}
router = APIRouter(prefix='/api/registry', dependencies=[Depends(require_operator)])


def resource_for(resource):
    if resource not in MODELS:
        raise HTTPException(404, 'Unknown registry')
    return MODELS[resource]


def unavailable(exc):
    if isinstance(exc, psycopg.errors.UniqueViolation):
        return HTTPException(409, 'A record with this unique name or reference already exists in this scope.')
    if isinstance(exc, psycopg.errors.ForeignKeyViolation):
        return HTTPException(422, 'Referenced parent record does not exist.')
    return HTTPException(503, 'Registry database or schema unavailable. Check the server setup.')


@router.get('/{resource}')
def list_records(resource: str, response: Response, limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    table, _ = resource_for(resource)
    response.headers['Cache-Control'] = 'no-store'
    try:
        with _connect(get_settings().database_url) as db:
            rows = db.execute(sql.SQL('SELECT * FROM {} ORDER BY created_at,id LIMIT %s OFFSET %s').format(sql.Identifier(table)), (limit,offset)).fetchall()
            total = db.execute(sql.SQL('SELECT count(*) AS total FROM {}').format(sql.Identifier(table))).fetchone()['total']
        return {'items':[profile_view(r) for r in rows] if resource == 'connector-profiles' else rows,
                'total':total,'limit':limit,'offset':offset}
    except (psycopg.Error, PersistenceNotConfigured) as exc:
        raise unavailable(exc) from None


@router.post('/{resource}', status_code=201)
def create_record(resource: str, payload: dict):
    table, model = resource_for(resource)
    try:
        data = model.model_validate(payload).model_dump()
    except ValidationError as exc:
        raise HTTPException(422, [{'field':'.'.join(map(str,e['loc'])),'message':e['msg']} for e in exc.errors()]) from None
    data = {'id':uuid4(), **data}
    query = sql.SQL('INSERT INTO {} ({}) VALUES ({}) RETURNING *').format(sql.Identifier(table), sql.SQL(',').join(map(sql.Identifier,data)),sql.SQL(',').join(sql.Placeholder() for _ in data))
    try:
        with _connect(get_settings().database_url) as db:
            row = db.execute(query, tuple(data.values())).fetchone()
            return profile_view(row) if resource == 'connector-profiles' else row
    except (psycopg.Error, PersistenceNotConfigured) as exc:
        raise unavailable(exc) from None


@router.patch('/{resource}/{record_id}')
def toggle_record(resource: str, record_id: UUID, payload: Toggle):
    table, _ = resource_for(resource)
    try:
        with _connect(get_settings().database_url) as db:
            row = db.execute(sql.SQL('UPDATE {} SET enabled=%s WHERE id=%s RETURNING *').format(sql.Identifier(table)),(payload.enabled,record_id)).fetchone()
            if row is None:
                raise HTTPException(404,'Record not found')
            return profile_view(row) if resource == 'connector-profiles' else row
    except (psycopg.Error, PersistenceNotConfigured) as exc:
        raise unavailable(exc) from None


@router.put('/connector-profiles/{record_id}')
def replace_connector_profile(record_id: UUID, payload: ConnectorProfile):
    """Edit configuration without changing its owning connection or runtime state."""
    data = payload.model_dump(exclude={'connection_id'})
    query = sql.SQL('UPDATE corei_connector_profiles SET {} WHERE id=%s AND connection_id=%s RETURNING *').format(
        sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(k)) for k in data))
    try:
        with _connect(get_settings().database_url) as db:
            row = db.execute(query, (*data.values(), record_id, payload.connection_id)).fetchone()
            if row is None:
                raise HTTPException(404, 'Profile not found for this connection; connection ownership cannot be changed.')
            return profile_view(row)
    except (psycopg.Error, PersistenceNotConfigured) as exc:
        raise unavailable(exc) from None
