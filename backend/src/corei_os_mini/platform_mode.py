"""Platform mode: DEVELOPMENT (build and test the platform as a user) or PRODUCTION (real trading); and the operating
profile: TEAM (a firm or institution, four-eyes with different people) or SOLO (one person runs everything, 0.68.0).

Why it exists: the owner uses one installation for two jobs — building the trading platform and testing it as a user.
Production rules (blocking setup gates, longer passwords, no resets) get in the way of testing; testing freedoms
(warn-only gates, resets) must never leak into real trading. One audited switch decides which rules apply.
Purpose:
  - get(db): the mode, whether LIVE is allowed in Development, who changed it, when and why.
  - DEVELOPMENT: PAPER and DEMO setup gates only warn (LIVE gates keep their levels); LIVE order sending is forced OFF
    and cannot be switched on, unless an administrator opened "Allow LIVE in Development" (reason + typed LIVE);
    short passwords (COREI_PASSWORD_MIN_LENGTH, default 2); test data (paper accounts) can be reset.
  - PRODUCTION: today's strict gates; LIVE order sending available through its own controls (Risk approver,
    re-authentication, readiness policy, four-eyes); passwords at least 6 characters; no reset.
  - set_mode / reset_test_data: administrator only, with a reason; audited in the same transaction.
  - Operating profile (0.68.0): TEAM keeps every "a different person must…" rule. SOLO lets the one person confirm their own
    change requests, LIVE deployments and workspace starts, held LIVE orders, gate loosening and calculation versions —
    still with the password and reason each asks for, and audited. second_person_ok() is the one rule every site uses;
    set_operating() switches it (administrator, reason, typed word, audited, alerted).
Must never: switch LIVE order sending ON by itself (it only ever forces it OFF); delete order, fill or audit history
(a reset archives paper accounts and starts fresh copies); allow a reset in PRODUCTION.
Dependencies: config_store (policies, execution controls, audit, alerts), trading.setup_gates (cache), trading.paper.
Consumers: operator_auth (password minimum), config_store.set_execution_control, trading.setup_gates.policy,
trading.execution.gate, trading.risk (pre-trade), config_api (/api/config/platform-mode, overview), the UI banner.
Architectural position: cross-cutting operating policy over Stages 1–8.
"""
from __future__ import annotations

import os
import re
from typing import Literal

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from . import config_store as store

MODES = ('DEVELOPMENT', 'PRODUCTION')
OPERATING = ('TEAM', 'SOLO')
KEY = 'platform_mode'
PRODUCTION_PASSWORD_MIN = 6


def development_password_min() -> int:
    return max(1, int(os.environ.get('COREI_PASSWORD_MIN_LENGTH', '2') or 2))


def get(db) -> dict:
    row = db.execute('SELECT value, version, updated_by, updated_at FROM cfg_policies WHERE key=%s', (KEY,)).fetchone()
    v = (row or {}).get('value') or {}
    mode = v.get('mode') if v.get('mode') in MODES else 'PRODUCTION'
    allow = bool(v.get('allow_live_in_development')) and mode == 'DEVELOPMENT'
    operating = v.get('operating') if v.get('operating') in OPERATING else 'TEAM'
    return {'mode': mode, 'operating': operating, 'allow_live_in_development': allow, 'live_blocked': mode == 'DEVELOPMENT' and not allow,
            'password_min': password_min_for(mode), 'reset_allowed': mode == 'DEVELOPMENT',
            'reason': v.get('reason'), 'updated_by': row['updated_by'] if row else None,
            'updated_at': row['updated_at'].isoformat() if row and row.get('updated_at') else None,
            'version': row['version'] if row else 0}


def password_min_for(mode: str) -> int:
    dev = development_password_min()
    return dev if mode == 'DEVELOPMENT' else max(PRODUCTION_PASSWORD_MIN, dev)


def current() -> dict:
    with store.transaction('read') as db:
        return get(db)


def is_development(db) -> bool:
    return get(db)['mode'] == 'DEVELOPMENT'


def live_blocked(db) -> bool:
    return get(db)['live_blocked']


LIVE_BLOCKED_REASON = ('LIVE_OFF_IN_DEVELOPMENT: the platform is in Development mode; LIVE orders need Production mode '
                       'or "Allow LIVE in Development" (Settings › Platform mode)')


class ModeIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['DEVELOPMENT', 'PRODUCTION']
    allow_live_in_development: bool = False
    confirm: str = Field('', max_length=20)
    reason: str = Field(min_length=5, max_length=500)
    version: int = Field(ge=0)


def _force_live_off(db, actor: str, why: str) -> bool:
    before = db.execute("SELECT * FROM cfg_execution_controls WHERE environment='LIVE' FOR UPDATE").fetchone()
    if not before or not before['trading_enabled']:
        return False
    after = db.execute("UPDATE cfg_execution_controls SET trading_enabled=false, reason=%s, updated_by=%s, updated_at=now(), "
                       "version=version+1 WHERE environment='LIVE' RETURNING *", (why[:500], actor)).fetchone()
    store.audit(db, actor, 'ORDER_SENDING_OFF', 'execution control', 'LIVE', dict(before), dict(after), why)
    from .alerts import enqueue
    enqueue(db, 'ORDER_SENDING', 'WARNING', 'Order sending OFF for LIVE', f'By {actor}. {why}')
    return True


def set_mode(actor: str, data: ModeIn) -> dict:
    """Administrator only (checked by the route). PRODUCTION needs the typed word PRODUCTION; allowing LIVE in
    Development needs the typed word LIVE. Entering Development (without the LIVE allowance) turns LIVE order sending OFF."""
    allow = data.mode == 'DEVELOPMENT' and data.allow_live_in_development
    with store.transaction('update') as db:
        db.execute("INSERT INTO cfg_policies(key, value, updated_by) VALUES (%s, %s, 'system') ON CONFLICT (key) DO NOTHING",
                   (KEY, Jsonb({'mode': 'PRODUCTION'})))
        db.execute('SELECT 1 FROM cfg_policies WHERE key=%s FOR UPDATE', (KEY,))
        before = get(db)
        if before['version'] != data.version and not (before['version'] == 1 and data.version == 0):
            raise HTTPException(409, 'The platform mode was changed by someone else. Reload before changing it.')
        if data.mode == 'PRODUCTION' and before['mode'] != 'PRODUCTION' and data.confirm != 'PRODUCTION':
            raise HTTPException(422, 'To switch to Production, confirm by typing PRODUCTION.')
        if allow and not before['allow_live_in_development'] and data.confirm != 'LIVE':
            raise HTTPException(422, 'To allow LIVE order sending in Development, confirm by typing LIVE.')
        value = {'mode': data.mode, 'allow_live_in_development': allow, 'reason': data.reason, 'operating': before['operating']}
        db.execute('UPDATE cfg_policies SET value=%s, updated_by=%s, updated_at=now(), version=version+1 WHERE key=%s',
                   (Jsonb(value), actor, KEY))
        after = get(db)
        store.audit(db, actor, 'PLATFORM_MODE', 'policy', KEY,
                    {k: before[k] for k in ('mode', 'allow_live_in_development')},
                    {k: after[k] for k in ('mode', 'allow_live_in_development')}, data.reason)
        forced = _force_live_off(db, actor, f'Development mode without LIVE allowance. {data.reason}') if after['live_blocked'] else False
        from .alerts import enqueue
        enqueue(db, 'LIVE_CHANGE', 'WARNING', f"Platform mode: {after['mode']}" + (' (LIVE allowed)' if allow else ''),
                f'By {actor}. Reason: {data.reason}')
    from .trading import setup_gates
    setup_gates._cache.clear()
    return {**after, 'live_switched_off': forced}


def is_solo(db) -> bool:
    return get(db)['operating'] == 'SOLO'


def second_person_ok(db, first: str | None, actor: str) -> bool:
    """The one four-eyes rule: a different person, or anyone when the firm runs as a single user (SOLO)."""
    return not first or first != actor or is_solo(db)


class OperatingIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operating: Literal['TEAM', 'SOLO']
    confirm: str = Field('', max_length=20)
    reason: str = Field(min_length=5, max_length=500)
    version: int = Field(ge=0)


def set_operating(actor: str, data: OperatingIn) -> dict:
    """Administrator only (route). SOLO needs the typed word SOLO: one person may then confirm their own LIVE actions."""
    with store.transaction('update') as db:
        db.execute("INSERT INTO cfg_policies(key, value, updated_by) VALUES (%s, %s, 'system') ON CONFLICT (key) DO NOTHING",
                   (KEY, Jsonb({'mode': 'PRODUCTION'})))
        row = db.execute('SELECT value, version FROM cfg_policies WHERE key=%s FOR UPDATE', (KEY,)).fetchone()
        before = get(db)
        if row['version'] != data.version and not (row['version'] == 1 and data.version == 0):
            raise HTTPException(409, 'The platform mode was changed by someone else. Reload before changing it.')
        if data.operating == before['operating']:
            return before
        if data.operating == 'SOLO' and data.confirm != 'SOLO':
            raise HTTPException(422, 'To run as a single user, confirm by typing SOLO.')
        value = {**(row['value'] or {}), 'operating': data.operating}
        db.execute('UPDATE cfg_policies SET value=%s, updated_by=%s, updated_at=now(), version=version+1 WHERE key=%s',
                   (Jsonb(value), actor, KEY))
        after = get(db)
        store.audit(db, actor, 'OPERATING_PROFILE', 'policy', KEY, {'operating': before['operating']}, {'operating': after['operating']}, data.reason)
        from .alerts import enqueue
        enqueue(db, 'LIVE_CHANGE', 'WARNING', f"Operating profile: {'single user' if data.operating == 'SOLO' else 'team'}",
                f'By {actor}. ' + ('One person may now confirm their own LIVE actions (password and reason still asked). '
                                   if data.operating == 'SOLO' else 'LIVE actions need a different person again. ') + f'Reason: {data.reason}')
    from .trading import setup_gates
    setup_gates._cache.clear()
    return after


class ResetIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirm: str = Field(max_length=20)
    reason: str = Field(min_length=5, max_length=500)


def _next_code(db, code: str) -> str:
    base = re.sub(r'_R\d+$', '', code)
    n = 2
    while True:
        cand = f'{base[:31 - len(str(n)) - 2]}_R{n}'
        if not db.execute('SELECT 1 FROM cfg_accounts WHERE code=%s', (cand,)).fetchone():
            return cand
        n += 1


def reset_test_data(actor: str, data: ResetIn) -> dict:
    """Development only: every active paper account is archived with its whole history and replaced by a fresh copy
    (same settings and starting amount). Accounts with active runs are skipped and listed. Nothing is deleted."""
    if data.confirm != 'RESET':
        raise HTTPException(422, 'To reset test data, confirm by typing RESET.')
    with store.transaction('read') as db:
        if not is_development(db):
            raise HTTPException(409, 'Test data can be reset only in Development mode.')
        rows = db.execute("SELECT a.id, a.code FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id "
                          "WHERE c.environment='PAPER' AND a.status='ACTIVE' ORDER BY a.code").fetchall()
        plan = [(r['id'], r['code'], _next_code(db, r['code'])) for r in rows]
    from .trading import paper
    reset, skipped = [], []
    for account_id, code, new_code in plan:
        try:
            new = paper.clone(actor, account_id, paper.CloneIn(code=new_code, reason=f'Test data reset: {data.reason}'))
            reset.append({'old': code, 'new': new['code'], 'id': new['id']})
        except HTTPException as e:
            skipped.append({'code': code, 'why': e.detail if isinstance(e.detail, str) else str(e.detail)})
    with store.transaction('write') as db:
        store.audit(db, actor, 'RESET_TEST_DATA', 'policy', KEY, None, {'reset': reset, 'skipped': skipped}, data.reason)
    return {'reset': reset, 'skipped': skipped}
