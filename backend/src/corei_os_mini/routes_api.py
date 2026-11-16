"""HTTP API for an MT5 account's routes (DEC-69), mounted at /api/config/accounts/{account_id}/routes.
Reading needs a signed-in operator; changes need Trading operations; a token is write-only and needs a fresh password."""
from __future__ import annotations

import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from . import account_routes as routes
from .config_api import parse
from .operator_auth import Principal, require_operator, require_reauth, require_role

router = APIRouter(prefix='/api/config/accounts/{account_id}/routes', tags=['configuration'])
_ops = require_role('TRADING_OPS')


def _no_store(response: Response) -> None:
    response.headers['Cache-Control'] = 'no-store'


@router.get('')
def list_routes(account_id: UUID, response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': routes.list_routes(account_id), 'switch_after_failures': routes.SWITCH_AFTER_FAILURES,
            'failback_seconds': routes.FAILBACK_SECONDS}


@router.post('', status_code=201)
def create_route(account_id: UUID, payload: dict, user: Principal = Depends(_ops)):
    return routes.create(user.username, account_id, parse(routes.RouteIn, payload))


@router.put('/{route_id}')
def update_route(account_id: UUID, route_id: UUID, payload: dict, user: Principal = Depends(_ops)):
    version = int(payload.pop('version', 0) or 0)
    return routes.update(user.username, account_id, route_id, parse(routes.RouteIn, payload), version)


@router.delete('/{route_id}', status_code=204)
def delete_route(account_id: UUID, route_id: UUID, user: Principal = Depends(_ops)):
    routes.delete(user.username, account_id, route_id)


@router.put('/{route_id}/token', status_code=204)
def set_token(account_id: UUID, route_id: UUID, payload: dict, user: Principal = Depends(_ops), __: Principal = Depends(require_reauth)):
    """Save the bridge's token (from its corei-bridge.token file). Write-only."""
    routes.set_token(user.username, account_id, route_id, str(payload.get('token') or '').strip())


@router.post('/{route_id}/new-token')
def new_token(account_id: UUID, route_id: UUID, response: Response, user: Principal = Depends(_ops), __: Principal = Depends(require_reauth)):
    """Issue a token for a push route (the COREI EA's Token input). Shown once; the old one stops working."""
    _no_store(response)
    token = secrets.token_urlsafe(32)
    routes.set_token(user.username, account_id, route_id, token)
    from .md import push
    push.forget()
    return {'token': token, 'note': 'Paste it into the COREI EA (input Token). It is shown only now.'}
