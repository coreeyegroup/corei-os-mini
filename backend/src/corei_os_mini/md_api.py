"""HTTP routes for market data (/api/md): collection plans, size estimate, switches,
live prices, quality and broker comparison, events, backfill jobs, bars, archive files
and backups. Reading needs a signed-in operator; changes need Trading ops; switches
and activating LIVE trading feeds follow the controls (reason, four-eyes)."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from .md import plans, store
from .operator_auth import Principal, require_operator, require_role

router = APIRouter(prefix='/api/md', tags=['market data'])
_ops = require_role('TRADING_OPS')


def _no_store(response: Response) -> None:
    response.headers['Cache-Control'] = 'no-store'


def _parse(model, payload):
    from .config_api import parse
    return parse(model, payload)


@router.get('/plans')
def list_plans(response: Response, account_id: UUID | None = None, include_archived: bool = False, _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': plans.list_plans(account_id, include_archived)}


@router.post('/plans', status_code=201)
def create_plan(payload: dict, response: Response, user: Principal = Depends(_ops)):
    _no_store(response)
    return plans.create(user.username, _parse(plans.PlanIn, payload))


@router.put('/plans/{plan_id}')
def update_plan(plan_id: UUID, payload: dict, response: Response, user: Principal = Depends(_ops)):
    _no_store(response)
    body = dict(payload or {})
    version = body.pop('version', None)
    if not isinstance(version, int):
        from fastapi import HTTPException
        raise HTTPException(422, 'version is required.')
    return plans.update(user.username, plan_id, _parse(plans.PlanIn, body), version)


@router.post('/plans/{plan_id}/status')
def plan_status(plan_id: UUID, payload: dict, response: Response, user: Principal = Depends(_ops)):
    _no_store(response)
    result = plans.set_status(user.username, plan_id, _parse(plans.StatusIn, payload))
    if 'change_request' in result:
        response.status_code = 202
    return result


@router.post('/estimate')
def estimate(payload: dict, response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return plans.estimate(_parse(plans.PlanIn, payload))


@router.get('/controls')
def controls(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': plans.controls()}


@router.put('/controls/{scope}')
def set_control(scope: str, payload: dict, response: Response, user: Principal = Depends(_ops)):
    _no_store(response)
    return plans.set_control(user.username, scope, _parse(plans.ControlIn, payload))


@router.get('/collectors')
def collectors(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': store.collectors()}


@router.get('/latest')
def latest(response: Response, account_id: UUID | None = None, _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': store.latest(account_id)}


@router.get('/catalog/{account_id}')
def catalogue(account_id: UUID, response: Response, q: str = Query('', max_length=64), asset_class: str = Query('', max_length=16),
              exchange: str = Query('', max_length=32), segment: str = Query('', max_length=120), show: str = Query('all', pattern='^(all|collected|not_collected)$'),
              limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0), _: str = Depends(require_operator)):
    """Every symbol the source offers (loaded from the provider), with filters, facets and what is collected."""
    _no_store(response)
    from .md import catalog
    return catalog.listing(account_id, q, asset_class, exchange, segment, show, limit, offset)


@router.post('/catalog/{account_id}/refresh')
def refresh_catalogue(account_id: UUID, response: Response, user: Principal = Depends(_ops)):
    """Load the symbol list from the provider now (MT5: a login; Upstox: the daily file; FIX: SecurityListRequest)."""
    _no_store(response)
    from .md import catalog
    return catalog.refresh(user.username, account_id)


@router.post('/catalog/{account_id}/collect', status_code=201)
def collect_from_catalogue(account_id: UUID, payload: dict, response: Response, user: Principal = Depends(_ops)):
    """Collect picked symbols: one plan each (defaults given), instrument and mapping linked, started."""
    _no_store(response)
    from .md import catalog
    return catalog.collect(user.username, account_id, _parse(catalog.CollectIn, payload))


@router.get('/sources')
def sources(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': store.sources()}


@router.get('/instruments')
def instruments(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': store.instruments()}


@router.get('/ticks/{account_id}/{broker_symbol}')
def ticks(account_id: UUID, broker_symbol: str, response: Response, seconds: int = Query(300, ge=5, le=3600),
          limit: int = Query(2000, ge=1, le=10000), _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': store.ticks(account_id, broker_symbol, seconds, limit)}


@router.get('/quality')
def quality(response: Response, hours: int = Query(24, ge=1, le=24 * 30), _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': store.quality_summary(hours)}


@router.get('/quality/{account_id}/{broker_symbol}')
def quality_minutes(account_id: UUID, broker_symbol: str, response: Response, minutes: int = Query(60, ge=1, le=24 * 60),
                    _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': store.quality(account_id, broker_symbol, minutes)}


@router.get('/bars/{account_id}/{broker_symbol}')
def bars(account_id: UUID, broker_symbol: str, response: Response, timeframe: str = Query('M1', pattern='^(S1|M1|M5|M15|M30|H1|H4|D1)$'),
         limit: int = Query(500, ge=1, le=5000), _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': store.bars(account_id, broker_symbol, timeframe, limit)}


@router.get('/events')
def events(response: Response, kind: str | None = Query(None, max_length=20), limit: int = Query(200, ge=1, le=2000),
           _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': store.events(limit, kind)}


@router.get('/jobs')
def jobs(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': plans.jobs()}


@router.post('/jobs', status_code=201)
def create_job(payload: dict, response: Response, user: Principal = Depends(_ops)):
    _no_store(response)
    return plans.create_job(user.username, _parse(plans.BackfillIn, payload))


@router.post('/jobs/{job_id}/cancel')
def cancel_job(job_id: UUID, response: Response, user: Principal = Depends(_ops)):
    _no_store(response)
    return plans.cancel_job(user.username, job_id)


@router.get('/files')
def files(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': store.files()}


@router.get('/backups')
def backups(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': store.backups()}


# ----- MT5 terminal push (the COREI EA) -----
from pathlib import Path as _Path
import secrets as _secrets

from fastapi import Header, HTTPException, Request
from fastapi.responses import PlainTextResponse

from . import config_store as _cfg
from .config_models import BridgeTokenCredential as _Token
from .md import push as _push
from .operator_auth import require_reauth as _reauth

EA_FILE = _Path(__file__).parent / 'md' / 'ea' / 'COREI_Push.mq5'
_REPO = _Path(__file__).resolve().parents[3]
BRIDGE_FILES = {'setup-corei-bridge.ps1': _REPO / 'connectors' / 'mt5-bridge' / 'windows' / 'setup-corei-bridge.ps1',
                'update-corei-bridge.ps1': _REPO / 'connectors' / 'mt5-bridge' / 'windows' / 'update-corei-bridge.ps1',
                'corei_mt5_bridge.py': _REPO / 'connectors' / 'mt5-bridge' / 'corei_mt5_bridge.py'}


@router.post('/push', response_class=PlainTextResponse)
async def terminal_push(request: Request, authorization: str = Header(''), x_corei_account: str = Header('')):
    """From the COREI EA in an MT5 terminal: quotes, bars, login facts, symbol list. Token = the server's push token."""
    if not authorization.startswith('Bearer ') or not x_corei_account:
        raise HTTPException(401, 'Send Authorization: Bearer <push token> and X-COREI-Account: <account code>.')
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(422, 'The body must be JSON: {"lines": [...]}') from None
    lines = body.get('lines') if isinstance(body, dict) else None
    if not isinstance(lines, list):
        raise HTTPException(422, 'The body must be JSON: {"lines": [...]}')
    ip = request.headers.get('x-forwarded-for', request.client.host if request.client else '').split(',')[0].strip() or None
    return PlainTextResponse(_push.receive(x_corei_account.strip(), authorization[7:].strip(), lines, ip), headers={'Cache-Control': 'no-store'})


@router.post('/push-token/{connection_id}')
def new_push_token(connection_id: UUID, response: Response, user: Principal = Depends(_ops), __: Principal = Depends(_reauth)):
    """Issue a new push token for an MT5 server (method: terminal push). Shown once; saved encrypted; the old one stops."""
    _no_store(response)
    token = _secrets.token_urlsafe(32)
    result = _cfg.set_credential(user.username, 'connections', connection_id, _Token(kind='BRIDGE_TOKEN', token=token),
                                 reason='New MT5 terminal push token')
    _push.forget()
    if isinstance(result, dict) and result.get('change_request'):
        return {'change_request': result['change_request'], 'token': None}
    return {'token': token, 'note': 'Paste it into the COREI EA (input Token). It is shown only now.'}


@router.get('/push/ea', response_class=PlainTextResponse)
def ea_source(_: str = Depends(require_operator)):
    """The COREI EA source (MQL5). Open it in MetaEditor and compile (F7), or copy it to MQL5/Experts."""
    return PlainTextResponse(EA_FILE.read_text(encoding='utf-8'), headers={'Content-Disposition': 'attachment; filename="COREI_Push.mq5"'})


@router.get('/bridge/files/{name}', response_class=PlainTextResponse)
def bridge_file(name: str, _: str = Depends(require_operator)):
    """The Windows set-up for a terminal bridge (VPS or PC): the PowerShell script and the bridge itself."""
    path = BRIDGE_FILES.get(name)
    if not path or not path.exists():
        raise HTTPException(404, 'Unknown file.')
    return PlainTextResponse(path.read_text(encoding='utf-8'), headers={'Content-Disposition': f'attachment; filename="{name}"'})


@router.get('/push/terminals')
def push_terminals(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with _cfg.transaction('read') as db:
        return {'items': db.execute('SELECT t.*, a.code AS account_code FROM md_push_terminals t JOIN cfg_accounts a ON a.id=t.account_id ORDER BY a.code').fetchall()}
