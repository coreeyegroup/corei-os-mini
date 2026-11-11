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
