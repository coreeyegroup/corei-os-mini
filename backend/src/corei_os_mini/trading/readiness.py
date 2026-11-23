"""LIVE readiness (pre-LIVE policy): what must be true before COREI takes new risk on a LIVE account.

Why it exists: the order-sending switch said only "on" or "off". On 23 Nov 2026 LIVE was on with no risk limit, one
person holding every role, automated LIVE orders not held for a second person and no alert channel — and nothing
on Home said so. "Ready" had four meanings collapsed into one: switch on, adapter capable, policy applied, order
authorised. This module computes the policy part, the same way for the switch, the dispatcher and Home.
Purpose: `live_policy(db)` → {ok, unmet: [{code, detail, goto}], accounts}. Rules (each can be waived only by a
recorded, expiring exception where marked):
  LIVE_LIMITS_MISSING        every LIVE account with Trading permission is covered (firm or account scope) by
                             MAX_ORDER_NOTIONAL, MAX_DAILY_LOSS and one of MAX_POSITION_QTY / MAX_GROSS_EXPOSURE.
                             No exception: limits are the minimum.
  LIVE_FOUR_EYES_NOT_ALL     the four-eyes rule holds every LIVE order (live_four_eyes = ALL) — waivable.
  LIVE_NO_SECOND_APPROVER    at least two active users can approve risk (RISK_APPROVER) — waivable.
  LIVE_NO_ALERT_CHANNEL      at least one active alert channel — waivable.
Where it is enforced: turning LIVE order sending on (config_store.set_execution_control) is refused while unmet;
the dispatcher (execution.gate) refuses (REJECTED, LIVE_POLICY_UNMET) any new LIVE order that adds risk while unmet, even if the switch was
turned on earlier; Home lists each unmet rule as CRITICAL. Reducing orders, flattens and cancels are never blocked
by this policy (they remove risk).
Must never: turn order sending off by itself (the owner decides), change a limit, or treat an expired or revoked
exception as valid.
Dependencies: config_store, trading.common. Consumers: config_store, trading.execution, controls_api, trading_api.
Architectural position: Stage 5 governance, read by Stage 7 just before sending.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException

from .. import config_store as store
from .common import settings

WAIVABLE = ('LIVE_FOUR_EYES_NOT_ALL', 'LIVE_NO_SECOND_APPROVER', 'LIVE_NO_ALERT_CHANNEL')
NEEDED_LIMITS = (('MAX_ORDER_NOTIONAL',), ('MAX_DAILY_LOSS',), ('MAX_POSITION_QTY', 'MAX_GROSS_EXPOSURE'))
GOTO_LIMITS = {'view': 'Risk', 'section': 'limits'}


def _exceptions(db) -> dict[str, dict]:
    rows = db.execute('SELECT * FROM cfg_policy_exceptions WHERE revoked_at IS NULL AND expires_at > now() ORDER BY expires_at DESC').fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(r['code'], dict(r))
    return out


def live_accounts(db) -> list[dict]:
    return [dict(r) for r in db.execute("""SELECT a.id, a.code FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id
                                             WHERE c.environment='LIVE' AND a.trading_permission='TRADING' ORDER BY a.code""").fetchall()]


def live_policy(db) -> dict:
    unmet: list[dict] = []
    waived: list[dict] = []
    exc = _exceptions(db)
    limits = [dict(r) for r in db.execute("SELECT scope, target_id, kind FROM risk_limits WHERE enabled AND scope IN ('FIRM','ACCOUNT')").fetchall()]
    accounts = live_accounts(db)
    for a in accounts:
        kinds = {lim['kind'] for lim in limits if lim['scope'] == 'FIRM' or str(lim['target_id']) == str(a['id'])}
        missing = [' or '.join(group) for group in NEEDED_LIMITS if not kinds.intersection(group)]
        if missing:
            unmet.append({'code': 'LIVE_LIMITS_MISSING', 'account': a['code'],
                          'detail': f"{a['code']} has no {', no '.join(missing)} limit (firm or account).", 'goto': GOTO_LIMITS})
    rule = settings(db).get('live_four_eyes') or 'MANUAL'
    checks = []
    if rule != 'ALL':
        checks.append({'code': 'LIVE_FOUR_EYES_NOT_ALL', 'detail': f'Automated LIVE orders are not held for a second person (four-eyes rule is {rule}; ALL holds every LIVE order).',
                       'goto': {'view': 'Execution', 'section': 'execution'}})
    approvers = db.execute("SELECT count(*) AS n FROM cfg_users WHERE status='ACTIVE' AND 'RISK_APPROVER' = ANY(roles)").fetchone()['n']
    if approvers < 2:
        checks.append({'code': 'LIVE_NO_SECOND_APPROVER', 'detail': f'{approvers} active risk approver(s): four-eyes needs two different people.',
                       'goto': {'view': 'Configuration', 'section': 'users'}})
    channels = db.execute("SELECT count(*) AS n FROM cfg_alert_channels WHERE status='ACTIVE'").fetchone()['n']
    if not channels:
        checks.append({'code': 'LIVE_NO_ALERT_CHANNEL', 'detail': 'No active alert channel: LIVE problems would reach nobody.',
                       'goto': {'view': 'Configuration', 'section': 'alerts'}})
    for c in checks:
        if c['code'] in exc:
            waived.append({**c, 'exception': {'reason': exc[c['code']]['reason'], 'approved_by': exc[c['code']]['approved_by'],
                                              'expires_at': exc[c['code']]['expires_at'].isoformat()}})
        else:
            unmet.append(c)
    return {'ok': not unmet, 'unmet': unmet, 'waived': waived, 'accounts': [a['code'] for a in accounts]}


def require_live_policy(db) -> None:
    status = live_policy(db)
    if not status['ok']:
        raise HTTPException(409, {'message': 'LIVE order sending stays off until the pre-LIVE policy is met.', 'unmet': status['unmet']})


def add_exception(actor: str, code: str, reason: str, days: int) -> dict:
    """A recorded, expiring waiver of one waivable rule (an owner decision), audited."""
    if code not in WAIVABLE:
        raise HTTPException(422, f'Only {", ".join(WAIVABLE)} can be waived; risk limits cannot.')
    if not 1 <= days <= 30:
        raise HTTPException(422, 'An exception lasts 1 to 30 days.')
    if len(reason.strip()) < 5:
        raise HTTPException(422, 'Give a reason (at least 5 characters).')
    with store.transaction('write') as db:
        row = db.execute('INSERT INTO cfg_policy_exceptions(id, code, reason, approved_by, expires_at) VALUES (%s,%s,%s,%s,%s) RETURNING *',
                         (uuid4(), code, reason.strip(), actor, datetime.now(timezone.utc) + timedelta(days=days))).fetchone()
        store.audit(db, actor, 'ADD_POLICY_EXCEPTION', 'policy exception', row['id'], None, dict(row), reason)
        return store.clean(row)


def revoke_exception(actor: str, exception_id, reason: str) -> dict:
    with store.transaction('write') as db:
        before = db.execute('SELECT * FROM cfg_policy_exceptions WHERE id=%s FOR UPDATE', (str(exception_id),)).fetchone()
        if not before:
            raise HTTPException(404, 'Exception not found.')
        after = db.execute('UPDATE cfg_policy_exceptions SET revoked_at=now() WHERE id=%s RETURNING *', (str(exception_id),)).fetchone()
        store.audit(db, actor, 'REVOKE_POLICY_EXCEPTION', 'policy exception', exception_id, dict(before), dict(after), reason)
        return store.clean(after)


def adds_risk(db, order: dict) -> bool:
    """True unless the order only shrinks its book's open position (a flatten or reduce-only order that fits)."""
    from . import portfolio
    from .common import D
    cur = portfolio.position_qty(db, order['account_id'], order['instrument_id'], mode=order['mode'])
    signed = D(order['quantity']) - D(order.get('filled_qty') or 0)
    signed = signed if order['side'] == 'BUY' else -signed
    after = cur + signed
    return not (cur != 0 and abs(after) <= abs(cur) and (after == 0 or (after > 0) == (cur > 0)))
