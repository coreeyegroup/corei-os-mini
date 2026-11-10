"""HTTP API for the configuration domain, mounted at /api/config. Operator sign-in required.

Owns: request parsing, safe validation messages (input values, and therefore secrets,
are never echoed) and route layout. All rules and persistence live in config_store.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

from . import config_store as store
from .broker_catalog import MAX_FILE, parse_catalog
from .config_models import CredentialEnvelope, ExecutionControlIn, VersionIn
from .credentials import storage_ready
from .operator_auth import Principal, current_user, require_operator, require_role

_trading_ops = require_role('TRADING_OPS')


def editor(user: Principal = Depends(_trading_ops)) -> str:
    """Configuration changes need the Trading operations role."""
    return user.username

router = APIRouter(prefix='/api/config', tags=['configuration'])
Entity = Literal['providers', 'connections', 'accounts', 'instruments', 'mappings']


def _no_store(response: Response) -> None:
    response.headers['Cache-Control'] = 'no-store'


def parse(model: type[BaseModel], payload: object, secret: bool = False) -> BaseModel:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        if secret:
            raise HTTPException(422, 'Invalid credential. Check that every required field is filled in.') from None
        detail = [{'field': '.'.join(str(p) for p in e['loc']) or 'record',
                   'message': e['msg'].removeprefix('Value error, ')} for e in exc.errors()]
        raise HTTPException(422, detail) from None


# ---------- fixed routes (declared before /{entity}) ----------

@router.get('/summary')
def summary(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    counts = {entity: len(store.list_records(entity, {})) for entity in store.SPECS}
    return {'counts': counts, 'execution_controls': store.list_execution_controls(),
            'credential_storage_ready': storage_ready(), 'available_adapters': sorted(store.AVAILABLE_ADAPTERS)}


@router.get('/execution-controls')
def get_execution_controls(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': store.list_execution_controls()}


@router.put('/execution-controls/{environment}')
def put_execution_control(environment: Literal['DEMO', 'LIVE'], payload: dict, response: Response,
                          user: Principal = Depends(current_user)):
    """Turning order sending ON needs Risk approver. Turning it OFF is a safety action
    that Risk approver or Trading operations may take."""
    _no_store(response)
    data = parse(ExecutionControlIn, payload)
    if data.trading_enabled and not user.has('RISK_APPROVER'):
        raise HTTPException(403, 'Turning order sending on needs the Risk approver role.')
    if not data.trading_enabled and not user.has('RISK_APPROVER', 'TRADING_OPS'):
        raise HTTPException(403, 'Turning order sending off needs the Risk approver or Trading operations role.')
    return store.set_execution_control(user.username, environment, data.trading_enabled, data.reason, data.version,
                                       data.confirm_environment)


@router.get('/audit')
def get_audit(response: Response, entity: str | None = Query(None, max_length=40),
              entity_id: str | None = Query(None, max_length=64), limit: int = Query(200, ge=1, le=1000),
              _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': store.list_audit(entity, entity_id, limit)}


@router.post('/mt5/server-file')
async def read_mt5_server_file(request: Request, response: Response, _: str = Depends(editor)):
    """Read servers and access points from an MT5 servers.dat. Nothing is stored."""
    _no_store(response)
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_FILE:
            raise HTTPException(413, 'Server file exceeds 1 MiB.')
    servers = await run_in_threadpool(parse_catalog, bytes(data))
    return {'servers': sorted(servers, key=lambda server: server['server_name'].lower())}


# ---------- records ----------

@router.get('/{entity}')
def list_entity(entity: Entity, response: Response, q: str = Query('', max_length=120),
                provider_id: UUID | None = None, connection_id: UUID | None = None,
                instrument_id: UUID | None = None, asset_class: str | None = Query(None, max_length=20),
                _: str = Depends(require_operator)):
    _no_store(response)
    filters = {'provider_id': provider_id, 'connection_id': connection_id,
               'instrument_id': instrument_id, 'asset_class': asset_class}
    return {'items': store.list_records(entity, filters, q)}


@router.get('/{entity}/{record_id}')
def get_entity(entity: Entity, record_id: UUID, response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        row = store.get_record(db, entity, record_id)
    if row is None:
        raise HTTPException(404, 'Record not found.')
    return row


@router.post('/{entity}', status_code=201)
def create_entity(entity: Entity, payload: dict, response: Response, actor: str = Depends(editor)):
    _no_store(response)
    return store.create(actor, entity, parse(store.SPECS[entity].model, payload))


@router.put('/{entity}/{record_id}')
def update_entity(entity: Entity, record_id: UUID, payload: dict, response: Response,
                  actor: str = Depends(editor)):
    _no_store(response)
    version = parse(VersionIn, {'version': payload.pop('version', None)}).version
    return store.update(actor, entity, record_id, parse(store.SPECS[entity].model, payload), version)


@router.delete('/{entity}/{record_id}', status_code=204)
def delete_entity(entity: Entity, record_id: UUID, version: int = Query(ge=1),
                  actor: str = Depends(editor)):
    store.delete(actor, entity, record_id, version)
    return Response(status_code=204, headers={'Cache-Control': 'no-store'})


@router.put('/{owner}/{owner_id}/credential')
def put_credential(owner: Literal['connections', 'accounts'], owner_id: UUID, payload: dict, response: Response,
                   actor: str = Depends(editor)):
    _no_store(response)
    envelope = parse(CredentialEnvelope, payload, secret=True)
    return store.set_credential(actor, owner, owner_id, envelope.credential)


@router.delete('/{owner}/{owner_id}/credential', status_code=204)
def delete_credential(owner: Literal['connections', 'accounts'], owner_id: UUID,
                      actor: str = Depends(editor)):
    store.clear_credential(actor, owner, owner_id)
    return Response(status_code=204, headers={'Cache-Control': 'no-store'})
