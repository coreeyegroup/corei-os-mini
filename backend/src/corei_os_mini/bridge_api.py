"""HTTP API for bridge nodes (DEC-75).

Why it exists: register a Windows machine running the COREI MT5 bridge once, let it enrol itself, and use it for
any number of MT5 accounts.
Purpose: /api/config/bridge-nodes (signed-in operators read; Trading operations change; a new enrolment code needs a
fresh password) and the public /api/bridge-enroll (the machine's installer: the code is the only credential;
requests are rate-limited per address).
Must never: return a node token, or accept an enrolment without a valid unused code.
Dependencies: bridge_nodes, operator_auth, config_api.parse. Consumers: the Setup screen, install-corei-bridge.ps1.
Architectural position: API layer, configuration.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse

from . import bridge_nodes as nodes
from .config_api import parse
from .operator_auth import Principal, require_operator, require_reauth, require_role

router = APIRouter(prefix='/api/config/bridge-nodes', tags=['configuration'])
enrol_router = APIRouter(prefix='/api/bridge-enroll', tags=['bridge enrolment'])
_ops = require_role('TRADING_OPS')
_REPO = Path(__file__).resolve().parents[3]
FILES = {'install-corei-bridge.ps1': _REPO / 'connectors' / 'mt5-bridge' / 'windows' / 'install-corei-bridge.ps1',
         'setup-corei-bridge.ps1': _REPO / 'connectors' / 'mt5-bridge' / 'windows' / 'setup-corei-bridge.ps1',
         'update-corei-bridge.ps1': _REPO / 'connectors' / 'mt5-bridge' / 'windows' / 'update-corei-bridge.ps1',
         'corei_mt5_bridge.py': _REPO / 'connectors' / 'mt5-bridge' / 'corei_mt5_bridge.py'}


def _no_store(response: Response) -> None:
    response.headers['Cache-Control'] = 'no-store'


@router.get('')
def list_nodes(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': nodes.list_nodes(), 'latest_bridge': nodes.LATEST_BRIDGE, 'corei_url': nodes.corei_url()}


@router.post('', status_code=201)
def create_node(payload: dict, response: Response, user: Principal = Depends(_ops), __: Principal = Depends(require_reauth)):
    """A new node and its one-time enrolment code (shown once)."""
    _no_store(response)
    return nodes.create(user.username, parse(nodes.NodeIn, payload))


@router.get('/{node_id}')
def get_node(node_id: UUID, response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return nodes.get(node_id)


@router.put('/{node_id}')
def update_node(node_id: UUID, payload: dict, user: Principal = Depends(_ops)):
    version = int(payload.pop('version', 0) or 0)
    return nodes.update(user.username, node_id, parse(nodes.NodeUpdate, payload), version)


@router.delete('/{node_id}', status_code=204)
def delete_node(node_id: UUID, user: Principal = Depends(_ops)):
    nodes.delete(user.username, node_id)
    return Response(status_code=204, headers={'Cache-Control': 'no-store'})


@router.post('/{node_id}/code')
def new_code(node_id: UUID, response: Response, user: Principal = Depends(_ops), __: Principal = Depends(require_reauth)):
    _no_store(response)
    return nodes.new_code(user.username, node_id)


@router.post('/{node_id}/check')
def check_node(node_id: UUID, response: Response, user: Principal = Depends(require_operator)):
    _no_store(response)
    return nodes.check(node_id, getattr(user, 'username', str(user)))


@router.post('/{node_id}/attach')
def attach(node_id: UUID, payload: dict, user: Principal = Depends(_ops)):
    return nodes.attach(user.username, node_id, parse(nodes.AttachIn, payload))


@router.post('/{node_id}/detach')
def detach(node_id: UUID, user: Principal = Depends(_ops)):
    return nodes.detach_all(user.username, node_id)


@router.get('/{node_id}/tunnel-keys')
def tunnel_keys(node_id: UUID, response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return nodes.write_tunnel_keys()


# ----- public: the machine's installer -----

_hits: dict[str, list[float]] = {}
_lock = threading.Lock()


def _limit(request: Request, per_minute: int = 20) -> str | None:
    ip = request.headers.get('x-forwarded-for', request.client.host if request.client else '').split(',')[0].strip() or None
    now = time.monotonic()
    with _lock:
        recent = [t for t in _hits.get(ip or '-', []) if now - t < 60]
        if len(recent) >= per_minute:
            raise HTTPException(429, 'Too many enrolment requests from this address; wait a minute.')
        recent.append(now)
        _hits[ip or '-'] = recent
        if len(_hits) > 5000:
            _hits.clear()
    return ip


@enrol_router.get('/files/{name}', response_class=PlainTextResponse)
def enrol_file(name: str, request: Request):
    """The installer and the bridge (open source, nothing secret)."""
    _limit(request, 60)
    path = FILES.get(name)
    if not path or not path.exists():
        raise HTTPException(404, 'Unknown file.')
    return PlainTextResponse(path.read_text(encoding='utf-8'), headers={'Content-Disposition': f'attachment; filename="{name}"', 'Cache-Control': 'no-store'})


@enrol_router.get('/{code}')
def enrol_info(code: str, request: Request, response: Response):
    _limit(request)
    _no_store(response)
    return nodes.enrol_info(code)


@enrol_router.post('/{code}')
def enrol(code: str, payload: dict, request: Request, response: Response):
    ip = _limit(request)
    _no_store(response)
    return nodes.enrol(code, parse(nodes.EnrolIn, payload), ip)
