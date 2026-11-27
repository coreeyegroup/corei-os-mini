"""HTTP routes for institutional controls, mounted under /api/config before the generic
/{entity} routes: health board, approvals (four-eyes), policies, alerts, network,
CSV import/export, archive, credential-use log and symbol specifications.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from . import alerts
from . import config_store as store
from . import controls
from . import network
from .config_models import AlertChannelIn, DecisionIn, PolicyIn
from .operator_auth import Principal, current_user, require_operator, require_reauth, require_role

router = APIRouter(prefix='/api/config', tags=['controls'])
_ops = require_role('TRADING_OPS')
_approver = require_role('TRADING_OPS', 'RISK_APPROVER')
_risk = require_role('RISK_APPROVER')


def _no_store(response: Response) -> None:
    response.headers['Cache-Control'] = 'no-store'


def _parse(model, payload):
    from .config_api import parse
    return parse(model, payload)


# ---------- health and network ----------

@router.get('/health')
async def health(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return await run_in_threadpool(controls.health)


@router.get('/tree')
async def connectivity_tree(response: Response, _: str = Depends(require_operator)):
    """Connectivity tree: technology › who › server/session/API › accounts, with readiness."""
    _no_store(response)
    return await run_in_threadpool(controls.tree)


@router.get('/network')
async def network_status(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return await run_in_threadpool(network.status)


# ---------- approvals ----------

@router.get('/approvals')
def list_approvals(response: Response, status: Literal['PENDING', 'APPLIED', 'REJECTED', 'WITHDRAWN', 'FAILED'] | None = None,
                   _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': controls.list_requests(status), 'approvers': controls.approver_count()}


@router.post('/approvals/{request_id}/decision')
def decide(request_id: UUID, payload: dict, response: Response, user: Principal = Depends(_approver),
           _: Principal = Depends(require_reauth)):
    _no_store(response)
    data = _parse(DecisionIn, payload)
    return controls.decide(user.username, request_id, data.approve, data.note)


@router.post('/approvals/{request_id}/withdraw')
def withdraw(request_id: UUID, response: Response, user: Principal = Depends(current_user)):
    _no_store(response)
    return controls.withdraw(user.username, request_id)


# ---------- policies ----------

@router.get('/policies')
def list_policies(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': controls.list_policies()}


@router.put('/policies/{key}')
def set_policy(key: str, payload: dict, response: Response, user: Principal = Depends(_risk),
               _: Principal = Depends(require_reauth)):
    _no_store(response)
    data = _parse(PolicyIn, payload)
    return controls.set_policy(user.username, key, data.value, data.version, data.reason)


# ---------- LIVE readiness (pre-LIVE policy) ----------

@router.get('/live-readiness')
def live_readiness(response: Response, _: str = Depends(require_operator)):
    """The pre-LIVE policy: each unmet rule with where to fix it, waived rules with their exception, and the
    recorded exceptions."""
    _no_store(response)
    from .trading import readiness
    with store.transaction('read') as db:
        status = readiness.live_policy(db)
        items = [store.clean(r) for r in db.execute('SELECT * FROM cfg_policy_exceptions ORDER BY created_at DESC LIMIT 100').fetchall()]
    return {**status, 'exceptions': items}


@router.post('/live-readiness/exceptions', status_code=201)
def add_policy_exception(payload: dict, response: Response, user: Principal = Depends(_risk), _: Principal = Depends(require_reauth)):
    """An owner's recorded, expiring waiver of one waivable pre-LIVE rule (not risk limits)."""
    _no_store(response)
    from .trading import readiness
    return readiness.add_exception(user.username, str(payload.get('code') or ''), str(payload.get('reason') or ''), int(payload.get('days') or 0))


@router.post('/live-readiness/exceptions/{exception_id}/revoke')
def revoke_policy_exception(exception_id: UUID, payload: dict, response: Response, user: Principal = Depends(_risk),
                            _: Principal = Depends(require_reauth)):
    _no_store(response)
    from .trading import readiness
    return readiness.revoke_exception(user.username, exception_id, str(payload.get('reason') or ''))


# ---------- alerts ----------

@router.get('/alerts/channels')
def list_channels(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': alerts.list_channels(db)}


@router.post('/alerts/channels', status_code=201)
def create_channel(payload: dict, response: Response, user: Principal = Depends(_ops)):
    _no_store(response)
    data = _parse(AlertChannelIn, payload)
    with store.transaction('create') as db:
        return alerts.save_channel(db, user.username, data, None, None, store.audit)


@router.put('/alerts/channels/{channel_id}')
def update_channel(channel_id: UUID, payload: dict, response: Response, user: Principal = Depends(_ops)):
    _no_store(response)
    version = payload.pop('version', None)
    data = _parse(AlertChannelIn, payload)
    with store.transaction('update') as db:
        return alerts.save_channel(db, user.username, data, channel_id, version, store.audit)


@router.delete('/alerts/channels/{channel_id}', status_code=204)
def delete_channel(channel_id: UUID, user: Principal = Depends(_ops)):
    with store.transaction('delete') as db:
        alerts.delete_channel(db, user.username, channel_id, store.audit)
    return Response(status_code=204, headers={'Cache-Control': 'no-store'})


@router.post('/alerts/channels/{channel_id}/test', status_code=202)
def test_channel(channel_id: UUID, user: Principal = Depends(_ops)):
    with store.transaction('update') as db:
        alerts.queue_test(db, channel_id, user.username)
    return {'queued': True}


@router.get('/alerts/outbox')
def alert_outbox(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    with store.transaction('read') as db:
        return {'items': [store.clean(r) for r in alerts.outbox(db)]}


# ---------- CSV ----------

class ImportIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    csv: str = Field(min_length=1, max_length=2_000_000)
    dry_run: bool = True


@router.post('/accounts/import')
def import_accounts(payload: dict, response: Response, user: Principal = Depends(_ops)):
    _no_store(response)
    data = _parse(ImportIn, payload)
    return controls.import_accounts(user.username, data.csv, data.dry_run)


@router.get('/export/{entity}.csv')
def export(entity: Literal['providers', 'connections', 'accounts'], _: str = Depends(require_operator)):
    return Response(controls.export_csv(entity), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="corei-{entity}.csv"', 'Cache-Control': 'no-store'})


# ---------- archive, credential use, symbol specs ----------

class ArchiveIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(ge=1)
    reason: str = Field(min_length=5, max_length=500)


class PurgeIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(ge=1)
    confirm: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=5, max_length=500)


@router.post('/{entity}/{record_id}/delete-permanently')
def delete_permanently(entity: Literal['providers', 'connections', 'accounts'], record_id: UUID, payload: dict,
                       response: Response, user: Principal = Depends(_ops)):
    """Delete a provider, server or account and everything attached to it (typed code + reason; audited)."""
    _no_store(response)
    data = _parse(PurgeIn, payload)
    return store.purge(user.username, entity, record_id, data.version, data.confirm, data.reason)


@router.post('/{entity}/{record_id}/archive')
def archive(entity: Literal['providers', 'connections', 'accounts', 'groups'], record_id: UUID, payload: dict,
            response: Response, user: Principal = Depends(_ops)):
    _no_store(response)
    data = _parse(ArchiveIn, payload)
    return store.archive(user.username, entity, record_id, data.version, data.reason)


@router.get('/{owner}/{owner_id}/credential-use')
def credential_use(owner: Literal['accounts', 'connections'], owner_id: UUID, response: Response,
                   _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': store.credential_use(owner_id)}


class SpecIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    broker_symbol: str = Field(min_length=1, max_length=64)
    spec: dict


@router.get('/accounts/{account_id}/symbol-specs')
def symbol_specs(account_id: UUID, response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return {'items': controls.latest_specs(account_id)}


@router.post('/accounts/{account_id}/symbol-specs', status_code=201)
def add_symbol_spec(account_id: UUID, payload: dict, response: Response, user: Principal = Depends(_ops)):
    """Manual specification until the adapter reads it from the broker (marked MANUAL)."""
    _no_store(response)
    data = _parse(SpecIn, payload)
    from .order_checks import SymbolSpec
    spec = _parse(SymbolSpec, data.spec)
    return controls.record_symbol_spec(account_id, data.broker_symbol.strip(), spec.model_dump(mode='json'), 'MANUAL', user.username)


@router.post('/accounts/{account_id}/order-check')
def order_check(account_id: UUID, payload: dict, response: Response, _: str = Depends(require_operator)):
    """Check an order against the account's latest symbol specification and limits,
    without sending anything. The same check runs before every real order."""
    _no_store(response)
    from .order_checks import OrderDraft, check_order
    draft = _parse(OrderDraft, payload)
    specs = {s['broker_symbol']: s for s in controls.latest_specs(account_id)}
    with store.transaction('read') as db:
        account = store.get_record(db, 'accounts', account_id)
    if not account:
        raise HTTPException(404, 'Account not found.')
    from .md.store import price_check
    live = price_check(account_id, draft.broker_symbol)
    if draft.reference_price is None and live['price']:
        reference = live['price']['ask'] if draft.side == 'BUY' else live['price']['bid']
        if reference:
            draft = draft.model_copy(update={'reference_price': Decimal(str(reference))})
    result = check_order(draft, specs.get(draft.broker_symbol), account)
    problems = [*result['problems'], *live['problems']]
    return {**result, 'ok': not problems, 'problems': problems, 'warnings': live['warnings'], 'live_price': live['price']}


# ---------- read real data from the broker (MT5, read-only login) ----------

class BrokerReadIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    symbols: list[str] | None = Field(default=None, max_length=500)
    max_symbols: int = Field(default=2000, ge=0, le=5000)
    route_id: UUID | None = None  # test one route (Setup › account › Routes › Test)


@router.post('/accounts/{account_id}/read-from-broker')
async def read_from_broker(account_id: UUID, response: Response, payload: dict | None = None, user: Principal = Depends(_ops)):
    """Log in once (investor password preferred), store account facts, server clock offset
    and every symbol's trading rules, then disconnect. Sends no orders."""
    _no_store(response)
    data = _parse(BrokerReadIn, payload or {})
    from . import broker_session
    return await run_in_threadpool(broker_session.read_account, user.username, account_id, data.symbols, data.max_symbols, data.route_id)


@router.get('/accounts/{account_id}/broker-reads')
def broker_reads(account_id: UUID, response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    from . import broker_session
    return {'items': broker_session.reads(account_id)}


# ---------- workstation overview (top bar, status bar, Home) ----------

@router.get('/overview')
def overview(response: Response, user: Principal = Depends(current_user)):
    """Everything the shell shows at a glance, in one call: market-data feeds, order
    sending, approvals and what needs attention — each item says where to go."""
    _no_store(response)
    from .md import store as md
    attention: list[dict] = []
    collectors = md.collectors()
    with store.transaction('read') as db:
        active_plans = db.execute("SELECT count(*) AS n FROM md_collection_plans WHERE status='ACTIVE'").fetchone()['n']
        approvals = db.execute("SELECT count(*) AS n FROM cfg_change_requests WHERE status='PENDING'").fetchone()['n']
        paused_all = (db.execute("SELECT paused FROM md_controls WHERE scope='ALL'").fetchone() or {}).get('paused', False)
        backup = db.execute("SELECT ok, error, started_at FROM md_backups WHERE kind='DATABASE' ORDER BY id DESC LIMIT 1").fetchone()
        verify = db.execute("SELECT severity, detail FROM md_events WHERE kind='VERIFY' ORDER BY id DESC LIMIT 1").fetchone()
        archive = db.execute("SELECT severity, detail FROM md_events WHERE kind='ARCHIVE' ORDER BY id DESC LIMIT 1").fetchone()
        plan_health = [dict(r) for r in db.execute("""SELECT h.state, h.detail, h.broker_symbol, p.code, p.purpose, a.code AS account_code
            FROM md_plan_health h JOIN md_collection_plans p ON p.id=h.plan_id JOIN cfg_accounts a ON a.id=p.account_id
            WHERE p.status='ACTIVE'""").fetchall()]
    live = sum(1 for c in collectors if c['state'] == 'CONNECTED')
    from .md.health import PROBLEMS, WORDS
    plan_states: dict[str, int] = {}
    for h in plan_health:
        plan_states[h['state']] = plan_states.get(h['state'], 0) + 1
        if h['state'] in PROBLEMS:
            # A plan that collects nothing is a problem even while its source is connected.
            attention.append({'severity': 'CRITICAL' if h['purpose'] == 'TRADING_FEED' and h['state'] in ('INVALID_SYMBOL', 'ERROR') else 'WARNING',
                              'area': 'Market data', 'title': f"{h['code']} ({h['broker_symbol']}): {WORDS[h['state']][0].lower()}",
                              'detail': f"{h['detail'] or WORDS[h['state']][1]} {WORDS[h['state']][2]}".strip(),
                              'goto': {'view': 'Market data', 'section': 'plans'}})
    for c in collectors:
        if c.get('problem'):
            p = c['problem']
            attention.append({'severity': 'CRITICAL' if c['state'] == 'FAILED' else 'WARNING', 'area': 'Market data',
                              'title': f"{c['account_name'] or c['account_code']}: {p['title']}", 'detail': p['detail'],
                              'goto': {'view': 'Market data', 'section': 'live'}})
    stale_service = active_plans and not paused_all and (not collectors or all(
        (c['heartbeat_age_seconds'] or 1e9) > 90 for c in collectors))
    if stale_service:
        attention.append({'severity': 'CRITICAL', 'area': 'Market data', 'title': 'Collector service is not running',
                          'detail': 'Active collection plans exist but no collector has reported in the last 90 seconds. '
                                    'On the server: sudo systemctl restart corei-os-mini-collector',
                          'goto': {'view': 'Infrastructure', 'section': 'health'}})
    if paused_all:
        attention.append({'severity': 'WARNING', 'area': 'Market data', 'title': 'All market-data collection is paused',
                          'detail': 'Resume it under Market data › Controls.', 'goto': {'view': 'Market data', 'section': 'controls'}})
    if approvals:
        attention.append({'severity': 'WARNING', 'area': 'Approvals', 'title': f'{approvals} change{"" if approvals == 1 else "s"} waiting for approval',
                          'detail': 'A second person reviews and approves them.', 'goto': {'view': 'Configuration', 'section': 'approvals'}})
    if verify and verify['severity'] == 'CRITICAL':
        attention.append({'severity': 'CRITICAL', 'area': 'Data integrity', 'title': 'History files changed or missing',
                          'detail': f"{len(verify['detail'].get('mismatch') or [])} changed, {len(verify['detail'].get('missing') or [])} missing.",
                          'goto': {'view': 'Market data', 'section': 'archive'}})
    if archive and archive['severity'] == 'CRITICAL':
        attention.append({'severity': 'WARNING', 'area': 'Data integrity', 'title': 'Last nightly archive had errors',
                          'detail': '; '.join((archive['detail'].get('errors') or [])[:2]), 'goto': {'view': 'Market data', 'section': 'events'}})
    if backup is None or not backup['ok']:
        attention.append({'severity': 'WARNING', 'area': 'Backups', 'title': 'No working database backup',
                          'detail': (backup or {}).get('error') or 'Set COREI_BACKUP_DIR on the server; the nightly job makes them.',
                          'goto': {'view': 'Market data', 'section': 'archive'}})
    # The set-up path: where the firm is and what comes next (same checklist as Setup › Overview).
    checklist = controls.health()['checklist']
    done = sum(1 for c in checklist if c['done'])
    nxt = next((c for c in checklist if not c['done']), None)
    setup = {'done': done, 'total': len(checklist), 'next': None if nxt is None else {
        'step': nxt['step'], 'goto': {'view': 'Market data', 'section': 'plans'} if nxt['section'] == 'plans' else {'view': 'Configuration', 'section': nxt['section']}},
        'steps': [{'step': c['step'], 'done': c['done'], 'section': c['section']} for c in checklist]}
    order = store.list_execution_controls()
    from .trading import readiness
    from . import platform_mode
    with store.transaction('read') as db:
        live_policy = readiness.live_policy(db)
        mode = platform_mode.get(db)
    live_on = any(o['trading_enabled'] for o in order if o['environment'] == 'LIVE')
    for u in live_policy['unmet']:
        attention.append({'severity': 'CRITICAL' if live_on else 'WARNING', 'area': 'LIVE readiness',
                          'title': ('LIVE order sending is ON but ' if live_on else 'Before LIVE: ') + {
                              'LIVE_LIMITS_MISSING': 'risk limits are missing', 'LIVE_FOUR_EYES_NOT_ALL': 'automated LIVE orders are not held for a second person',
                              'LIVE_NO_SECOND_APPROVER': 'there is no second risk approver', 'LIVE_NO_ALERT_CHANNEL': 'no alert channel is set up'}.get(u['code'], u['code']),
                          'detail': u['detail'] + (' New LIVE orders that add risk are refused until this is fixed.' if live_on else ''),
                          'goto': u['goto']})
    if any(o['trading_enabled'] for o in order) and not store.ORDER_ADAPTERS:
        on = ', '.join(o['environment'] for o in order if o['trading_enabled'])
        attention.append({'severity': 'WARNING', 'area': 'Order sending',
                          'title': f'Order sending is ON for {on}, but no order adapter is built',
                          'detail': 'Nothing can be sent yet. Turn it off until the order adapter, risk limits and reconciliation exist (pre-LIVE gate).',
                          'goto': {'view': 'Configuration', 'section': 'controls'}})
    return {'user': {'username': user.username, 'roles': sorted(user.roles)},
            'feeds': {'active_plans': active_plans, 'collectors': len(collectors), 'live': live,
                      'problems': sum(1 for c in collectors if c.get('problem')) + sum(n for k, n in plan_states.items() if k in PROBLEMS),
                      'paused': paused_all, 'plans': plan_states},
            'order_sending': [{'environment': o['environment'], 'enabled': o['trading_enabled']} for o in order],
            'order_adapter': bool(store.ORDER_ADAPTERS),
            'live_policy': live_policy,
            'platform_mode': {k: mode[k] for k in ('mode', 'allow_live_in_development', 'live_blocked', 'updated_by', 'updated_at')},
            'setup': setup,
            'approvals': approvals,
            'attention': sorted(attention, key=lambda a: {'CRITICAL': 0, 'WARNING': 1}.get(a['severity'], 2))}


# ---------- platform mode (0.66.0) ----------

@router.get('/platform-mode')
def get_platform_mode(response: Response, _: str = Depends(require_operator)):
    from . import platform_mode
    response.headers['Cache-Control'] = 'no-store'
    return platform_mode.current()


@router.put('/platform-mode')
def put_platform_mode(payload: dict, response: Response, user: Principal = Depends(require_role('ADMIN'))):
    """Administrator only. Development ⇄ Production, and the "Allow LIVE in Development" option."""
    from . import platform_mode
    from .config_api import parse
    response.headers['Cache-Control'] = 'no-store'
    return platform_mode.set_mode(user.username, parse(platform_mode.ModeIn, payload))


@router.post('/platform-mode/reset-test-data')
def reset_test_data(payload: dict, response: Response, user: Principal = Depends(require_role('ADMIN'))):
    """Development only, administrator only: paper accounts start fresh; old ones are archived with their history."""
    from . import platform_mode
    from .config_api import parse
    response.headers['Cache-Control'] = 'no-store'
    return platform_mode.reset_test_data(user.username, parse(platform_mode.ResetIn, payload))


# ---------- Settings › Update (0.67.0) ----------

@router.get('/update')
def get_update(response: Response, _: Principal = Depends(require_role('ADMIN'))):
    from . import update
    response.headers['Cache-Control'] = 'no-store'
    return update.state()


@router.post('/update/check')
def check_update(response: Response, user: Principal = Depends(require_role('ADMIN'))):
    from . import update
    response.headers['Cache-Control'] = 'no-store'
    return update.request_check(user.username)


@router.post('/update/apply')
def apply_update(payload: dict, request: Request, response: Response, user: Principal = Depends(require_role('ADMIN'))):
    """Administrator, password asked again (428 until re-authenticated), reason, typed UPDATE, every acknowledgement."""
    from . import update
    from .config_api import parse
    require_reauth(request, user)
    response.headers['Cache-Control'] = 'no-store'
    return update.request_apply(user.username, parse(update.ApplyIn, payload))
