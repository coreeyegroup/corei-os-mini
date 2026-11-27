"""Setup gates: the systematic path from nothing to a first trade, per track, with chosen strictness and overrides.

Why it exists: every feature existed, but nothing said what to set up first or whether a step was done. On 25 Nov
2026 order sending was on with zero risk limits. Setup is now eight gates per track (PAPER, DEMO, LIVE). Each gate's
status is computed from real data on every read (never ticked by hand), so a dead feed or a deleted limit reopens
it at once.
Purpose:
  - status(db): every gate of every track: done or not, the failing items (what, why, how to fix, where), its level
    and any active override.
  - Levels per gate and track: OFF (shown only), WARN (allowed, warned), SOFT (blocked; the person may override with
    a reason), SUPERVISED (blocked; an override needs a second person), HARD (blocked; no override; only changing the
    level, audited, lets it through). Presets: RECOMMENDED, ALL_WARN, ALL_OFF (sandbox), STRICT.
  - enforce(db, action, track, ...): the gates relevant to an action, narrowed to the accounts, instruments and
    strategies the action touches; raises 409 when a blocking gate is neither done nor overridden.
  - Overrides: one gate, one track, optionally one account; time-boxed (default and maximum by policy, 24 h at most
    on LIVE by default); revocable; every grant, approval and revocation audited.
Responsibilities: gate levels change only by Admin or Risk approver with a reason. Loosening a LIVE level waits for a
second person. HARD gates are also checked at the order dispatcher (cached 30 s).
Must never: tick a gate by hand; let an override outlive its expiry; let a person approve their own supervised override;
turn order sending, a limit or a strategy on by itself.
Dependencies: config_store, trading.governance, trading.readiness, strategy_registry (deployment track).
In Development mode (platform_mode) PAPER and DEMO levels are lowered to WARN; LIVE levels never change.
Consumers: config_store (order sending), strategy_registry (activation), trading.workspace_control (plan),
trading.execution (dispatcher), trading_api (/api/trading/setup*), the Setup UI.
Architectural position: cross-stage governance over Stages 1–8.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4  # noqa: F401 - UUID types the override ids

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from .. import config_store as store

Track = Literal['PAPER', 'DEMO', 'LIVE']
Level = Literal['OFF', 'WARN', 'SOFT', 'SUPERVISED', 'HARD']
TRACKS = ('PAPER', 'DEMO', 'LIVE')
LEVELS = ('OFF', 'WARN', 'SOFT', 'SUPERVISED', 'HARD')
BLOCKING = ('SOFT', 'SUPERVISED', 'HARD')
GATES = [
    # id, step name, what it unlocks, tracks it applies to
    ('PEOPLE', 'People and alerts', 'Four-eyes rules and alerts', TRACKS),
    ('ACCOUNTS', 'Brokers and accounts', 'Data and orders for each account', TRACKS),
    ('SYMBOLS', 'Instruments and symbols', 'Prices and orders for each instrument', TRACKS),
    ('MARKET_DATA', 'Market data', 'Backtests, signals and runs', TRACKS),
    ('RISK', 'Risk', 'Order sending', ('DEMO', 'LIVE')),
    ('EVIDENCE', 'Strategies on evidence', 'Runs', TRACKS),
    ('PAPER_RECORD', 'Paper record', 'Broker runs', ('DEMO', 'LIVE')),
    ('ORDER_SENDING', 'Order sending', 'Orders to the broker', ('DEMO', 'LIVE')),
]
ENFORCED = [g[0] for g in GATES if g[0] != 'ORDER_SENDING']   # order sending is the action itself, shown as a step
PRESETS: dict[str, dict[str, dict[str, str]]] = {
    'RECOMMENDED': {
        'PAPER': {'PEOPLE': 'OFF', 'ACCOUNTS': 'WARN', 'SYMBOLS': 'SOFT', 'MARKET_DATA': 'WARN', 'EVIDENCE': 'WARN'},
        'DEMO': {'PEOPLE': 'WARN', 'ACCOUNTS': 'SOFT', 'SYMBOLS': 'SOFT', 'MARKET_DATA': 'SOFT', 'RISK': 'SOFT', 'EVIDENCE': 'SOFT',
                 'PAPER_RECORD': 'WARN'},
        'LIVE': {'PEOPLE': 'SUPERVISED', 'ACCOUNTS': 'HARD', 'SYMBOLS': 'HARD', 'MARKET_DATA': 'SUPERVISED', 'RISK': 'SUPERVISED',
                 'EVIDENCE': 'SUPERVISED', 'PAPER_RECORD': 'SUPERVISED'},
    },
    'ALL_WARN': {t: {g: 'WARN' for g in ENFORCED} for t in TRACKS},
    'ALL_OFF': {t: {g: 'OFF' for g in ENFORCED} for t in TRACKS},
    'STRICT': {'PAPER': {g: 'SOFT' for g in ENFORCED}, 'DEMO': {g: 'SUPERVISED' for g in ENFORCED}, 'LIVE': {g: 'HARD' for g in ENFORCED}},
}
WHERE = {   # where each gate is fixed: (view, section)
    'PEOPLE': ('Configuration', 'users'), 'ACCOUNTS': ('Configuration', 'accounts'), 'SYMBOLS': ('Configuration', 'mappings'),
    'MARKET_DATA': ('Market data', 'plans'), 'RISK': ('Risk', 'limits'), 'EVIDENCE': ('Configuration', 'strategies'),
    'PAPER_RECORD': ('My Workspaces', 'desks'), 'ORDER_SENDING': ('Configuration', 'controls'),
}
HEALTH_OK = ('LIVE', 'CLOSED', 'WAITING')
_cache: dict[str, tuple[float, dict]] = {}


# ---------- policy ----------

def policy(db) -> dict:
    v = store.policy(db, 'setup_gates') or {}
    preset = v.get('preset') if v.get('preset') in PRESETS else 'RECOMMENDED'
    levels = {t: dict(PRESETS[preset].get(t, {})) for t in TRACKS}
    for t, gates in (v.get('levels') or {}).items():
        for g, lvl in (gates or {}).items():
            if t in TRACKS and g in ENFORCED and lvl in LEVELS:
                levels[t][g] = lvl
    max_hours = {'PAPER': 168, 'DEMO': 168, 'LIVE': 24, **(v.get('max_hours') or {})}
    # Development mode (0.66.0): PAPER and DEMO gates only warn, so the platform can be tested as a user. LIVE gates keep
    # their chosen levels: in Development LIVE is off unless an administrator allowed it, and then it stays strict.
    from .. import platform_mode
    development = platform_mode.is_development(db)
    configured = {t: dict(v) for t, v in levels.items()}
    if development:
        for t in ('PAPER', 'DEMO'):
            levels[t] = {g: ('WARN' if lvl in BLOCKING else lvl) for g, lvl in levels[t].items()}
    return {'preset': preset, 'levels': levels, 'configured_levels': configured, 'development_mode': development, 'custom': v.get('levels') or {}, 'target_track': v.get('target_track') or 'DEMO',
            'default_hours': int(v.get('default_hours') or 24), 'max_hours': {k: int(x) for k, x in max_hours.items()}}


class PolicyIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    preset: Literal['RECOMMENDED', 'ALL_WARN', 'ALL_OFF', 'STRICT']
    levels: dict[Track, dict[str, Level]] = Field(default_factory=dict)
    target_track: Track = 'DEMO'
    default_hours: int = Field(24, ge=1, le=168)
    max_hours: dict[Track, int] = Field(default_factory=lambda: {'PAPER': 168, 'DEMO': 168, 'LIVE': 24})
    reason: str = Field(min_length=5, max_length=500)


def _loosens_live(before: dict, after: dict) -> list[str]:
    out = []
    for g in ENFORCED:
        a, b = before['levels']['LIVE'].get(g, 'OFF'), after['levels']['LIVE'].get(g, 'OFF')
        if LEVELS.index(b) < LEVELS.index(a):
            out.append(f'{g}: {a} → {b}')
    if after['max_hours'].get('LIVE', 24) > before['max_hours'].get('LIVE', 24):
        out.append(f"LIVE override maximum: {before['max_hours'].get('LIVE')} h → {after['max_hours']['LIVE']} h")
    return out


def set_policy(actor: str, data: PolicyIn) -> dict:
    """Tightening applies at once. Loosening a LIVE gate (or its override maximum) waits for a second person."""
    bad = [f'{t}.{g}' for t, gs in data.levels.items() for g in gs if g not in ENFORCED]
    if bad:
        raise HTTPException(422, f"Unknown gate: {', '.join(bad)}.")
    value = {'preset': data.preset, 'levels': data.levels, 'target_track': data.target_track, 'default_hours': data.default_hours,
             'max_hours': data.max_hours}
    with store.transaction('write') as db:
        before = policy(db)
        db.execute("SELECT 1 FROM cfg_policies WHERE key='setup_gates' FOR UPDATE")
        prior = store.policy(db, 'setup_gates')
        db.execute("INSERT INTO cfg_policies(key, value, updated_by) VALUES ('setup_gates', %s, %s) "
                   "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_by=EXCLUDED.updated_by, updated_at=now()",
                   (Jsonb(value), actor))
        after = policy(db)
        loosened = _loosens_live(before, after)
        if loosened:
            db.execute("INSERT INTO cfg_policies(key, value, updated_by) VALUES ('setup_gates', %s, %s) "
                       "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()", (Jsonb(prior), actor))
            db.execute("INSERT INTO cfg_policies(key, value, updated_by) VALUES ('setup_gates_pending', %s, %s) "
                       "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_by=EXCLUDED.updated_by, updated_at=now()",
                       (Jsonb({'value': value, 'by': actor, 'reason': data.reason, 'loosens': loosened,
                               'at': datetime.now(timezone.utc).isoformat()}), actor))
            store.audit(db, actor, 'REQUEST_POLICY', 'policy', 'setup_gates', prior, value,
                        f"{data.reason} — loosens LIVE ({'; '.join(loosened)}): waits for a second person")
            _cache.clear()
            return {**policy(db), 'pending': store.policy(db, 'setup_gates_pending')}
        db.execute("DELETE FROM cfg_policies WHERE key='setup_gates_pending'")
        store.audit(db, actor, 'UPDATE_POLICY', 'policy', 'setup_gates', prior, value, data.reason)
        _cache.clear()
        return {**after, 'pending': None}


def approve_pending(actor: str, reason: str) -> dict:
    with store.transaction('write') as db:
        pending = store.policy(db, 'setup_gates_pending')
        if not pending:
            raise HTTPException(404, 'No gate change is waiting for approval.')
        if pending['by'] == actor:
            raise HTTPException(422, 'A different person must approve loosening LIVE gates than the one who asked.')
        prior = store.policy(db, 'setup_gates')
        db.execute("UPDATE cfg_policies SET value=%s, updated_by=%s, updated_at=now() WHERE key='setup_gates'", (Jsonb(pending['value']), actor))
        db.execute("DELETE FROM cfg_policies WHERE key='setup_gates_pending'")
        store.audit(db, actor, 'APPROVE_POLICY', 'policy', 'setup_gates', prior, pending['value'],
                    f"{reason} (asked by {pending['by']}: {pending['reason']})")
        _cache.clear()
        return {**policy(db), 'pending': None}


def reject_pending(actor: str, reason: str) -> dict:
    with store.transaction('write') as db:
        pending = store.policy(db, 'setup_gates_pending')
        if not pending:
            raise HTTPException(404, 'No gate change is waiting for approval.')
        db.execute("DELETE FROM cfg_policies WHERE key='setup_gates_pending'")
        store.audit(db, actor, 'REJECT_POLICY', 'policy', 'setup_gates', pending, None, reason)
        return {**policy(db), 'pending': None}


# ---------- what is in use, per track ----------

def _track_of(mode: str, env: str) -> str:
    return 'PAPER' if mode == 'PAPER' else env


def _in_use(db, track: str) -> dict:
    """The accounts, instruments and strategy versions each track actually uses: its deployments and its workspaces."""
    pairs: set[tuple] = set()     # (account_id, instrument_id, strategy_id, version_id|None)
    for d in db.execute("""SELECT d.account_id, d.instrument_id, d.strategy_id, d.strategy_version_id, d.mode, c.environment
                           FROM cfg_deployments d JOIN cfg_accounts a ON a.id=d.account_id JOIN cfg_connections c ON c.id=a.connection_id
                           WHERE d.status IN ('PROPOSED','ACTIVE','PAUSED')""").fetchall():
        if _track_of(d['mode'], d['environment']) == track:
            pairs.add((str(d['account_id']), str(d['instrument_id']), str(d['strategy_id']), str(d['strategy_version_id'])))
    for w in db.execute('SELECT config FROM ws_workspaces').fetchall():
        out = (w['config'] or {}).get('output') or {}
        env = out.get('environment', 'OBSERVE')
        if ('PAPER' if env in ('OBSERVE', 'PAPER') else env) != track:
            continue
        ins = ((w['config'] or {}).get('input') or {}).get('instruments') or []
        strats = ((w['config'] or {}).get('processing') or {}).get('strategies') or []
        for a in out.get('accounts') or []:
            for i in ins:
                for s in strats:
                    pairs.add((str(a), str(i), str(s.get('strategy_id')), str(s['version_id']) if s.get('version_id') else None))
    return {'pairs': pairs, 'accounts': {p[0] for p in pairs}, 'instruments': {p[1] for p in pairs}, 'strategies': {p[2] for p in pairs}}


def _item(gate, text, fix='', account=None, instrument=None, strategy=None, labels=None):
    view, section = WHERE[gate]
    return {'gate': gate, 'text': text, 'fix': fix, 'goto': {'view': view, 'section': section},
            'account_id': account, 'instrument_id': instrument, 'strategy_id': strategy, **(labels or {})}


def _compute(db, track: str) -> dict[str, list[dict]]:
    from . import governance, readiness
    use = _in_use(db, track)
    items: dict[str, list[dict]] = {g[0]: [] for g in GATES}
    env_filter = None if track == 'PAPER' else track

    # 1 People and alerts.
    users = db.execute("SELECT username, roles FROM cfg_users WHERE status='ACTIVE'").fetchall()
    admins = [u['username'] for u in users if 'ADMIN' in u['roles']]
    approvers = [u['username'] for u in users if 'RISK_APPROVER' in u['roles'] or 'ADMIN' in u['roles']]
    if not admins:
        items['PEOPLE'].append(_item('PEOPLE', 'No active administrator.', 'Add a user with the Administrator role.'))
    if track in ('DEMO', 'LIVE') and not any('RISK_APPROVER' in u['roles'] for u in users):
        items['PEOPLE'].append(_item('PEOPLE', 'No risk approver.', 'Give someone the Risk approver role.'))
    if track in ('DEMO', 'LIVE') and not db.execute("SELECT 1 FROM cfg_alert_channels WHERE status='ACTIVE' LIMIT 1").fetchone():
        items['PEOPLE'].append(_item('PEOPLE', 'No active alert channel: nobody is told when something breaks.',
                                     'Add an e-mail, Telegram or webhook channel in Settings › Alerts and send a test.'))
    if track == 'LIVE' and len(set(approvers)) < 2:
        items['PEOPLE'].append(_item('PEOPLE', f"Only {len(set(approvers))} person can approve: LIVE four-eyes needs two.",
                                     'Add a second person with the Risk approver (or Administrator) role.'))

    # 2 Brokers and accounts.
    controls = store.execution_controls(db)
    sql = store.SELECTS['accounts'] + " WHERE a.status <> 'ARCHIVED'"
    rows = [dict(r) for r in db.execute(sql).fetchall()]
    accounts = store._enrich('accounts', rows, db)
    by_id = {str(a['id']): a for a in accounts}
    wanted = [a for a in accounts if str(a['id']) in use['accounts']] or [
        a for a in accounts if a['status'] == 'ACTIVE' and (env_filter is None or (a['environment'] == env_filter and a['trading_permission'] == 'TRADING'))]
    if not wanted:
        items['ACCOUNTS'].append(_item('ACCOUNTS', f'No {track.lower()} account yet.' if track != 'PAPER' else 'No active account yet.',
                                       'Connect › add a provider, a server and an account.'))
    for a in wanted:
        blockers = a['data_blockers'] if track == 'PAPER' else [b for b in a['trading_blockers'] if not b.startswith('ORDER_SENDING_OFF_')]
        if track != 'PAPER' and a['environment'] != track:
            blockers = [f"A {a['environment']} account used on the {track} track"] + blockers
        if blockers:
            items['ACCOUNTS'].append(_item('ACCOUNTS', f"{a['code']}: " + ', '.join(str(b).lower().replace('_', ' ') for b in blockers[:6]),
                                           'Open the account and fix each item (connection, credentials, bridge, permission).',
                                           account=str(a['id']), labels={'account': a['code']}))

    # 3 Instruments and symbols; 4 market data — for the instruments in use, or every instrument with a plan.
    ins_ids = use['instruments'] or {str(r['instrument_id']) for r in db.execute(
        "SELECT DISTINCT instrument_id FROM md_collection_plans WHERE status='ACTIVE' AND instrument_id IS NOT NULL").fetchall()}
    names = {str(r['id']): r['symbol'] for r in db.execute('SELECT id, symbol FROM cfg_instruments WHERE id = ANY(%s)', (list(ins_ids),)).fetchall()}
    maps = db.execute("""SELECT m.instrument_id, m.connection_id, m.usage FROM cfg_symbol_mappings m JOIN cfg_connections c ON c.id=m.connection_id
                         WHERE m.status='ACTIVE' AND c.status='ACTIVE' AND m.instrument_id = ANY(%s)""", (list(ins_ids),)).fetchall()
    data_map = {str(m['instrument_id']) for m in maps if m['usage'] in ('MARKET_DATA', 'BOTH')}
    trade_map = {(str(m['connection_id']), str(m['instrument_id'])) for m in maps if m['usage'] in ('TRADING', 'BOTH')}
    for i in sorted(ins_ids, key=lambda x: names.get(x, x)):
        sym = names.get(i, i)
        if i not in data_map:
            items['SYMBOLS'].append(_item('SYMBOLS', f'{sym}: no market-data symbol on any active connection.',
                                          'Markets › Symbol mappings: map it on a data connection (usage Market data or Both).',
                                          instrument=i, labels={'instrument': sym}))
    if track != 'PAPER':
        # One finding per account and instrument (several strategies on the same pair need the same one mapping).
        for acc, ins in sorted({(acc, ins) for acc, ins, _s, _v in use['pairs']}):
            a = by_id.get(acc)
            if a and (str(a['connection_id']), ins) not in trade_map:
                items['SYMBOLS'].append(_item('SYMBOLS', f"{names.get(ins, ins)} on {a['code']}: no trading symbol on its connection.",
                                              'Markets › Symbol mappings: map it on the account connection (usage Trading or Both).',
                                              account=acc, instrument=ins, labels={'account': a['code'], 'instrument': names.get(ins, ins)}))
    health = db.execute("""SELECT p.instrument_id, p.code, p.status, h.state FROM md_collection_plans p LEFT JOIN md_plan_health h ON h.plan_id=p.id
                           WHERE p.instrument_id = ANY(%s) AND p.status <> 'ARCHIVED'""", (list(ins_ids),)).fetchall()
    for i in sorted(ins_ids, key=lambda x: names.get(x, x)):
        mine = [h for h in health if str(h['instrument_id']) == i]
        if not any(h['status'] == 'ACTIVE' and h['state'] in HEALTH_OK for h in mine):
            why = 'no collection plan' if not mine else ', '.join(f"{h['code']} {(h['state'] or h['status']).lower().replace('_', ' ')}" for h in mine[:3])
            items['MARKET_DATA'].append(_item('MARKET_DATA', f'{names.get(i, i)}: no live feed ({why}).',
                                              'Markets › Market data plans: create or start a plan and fix its health.',
                                              instrument=i, labels={'instrument': names.get(i, i)}))

    # 5 Risk.
    if track == 'DEMO':
        for u in governance.demo_unmet(db, list(use['accounts']) or None):
            items['RISK'].append(_item('RISK', u['detail'], 'Risk › Limits › Size limits from a profile (Preview, then Apply).',
                                       account=u['account_id'], labels={'account': u['account']}))
    elif track == 'LIVE':
        live = readiness.live_policy(db)
        for u in live['unmet']:
            items['RISK'].append(_item('RISK', u['detail'], 'Meet each pre-LIVE rule (limits, four-eyes, approvers, alerts) or record an exception.'))

    # 6 Strategies on evidence; 7 paper record.
    versions: dict[str, dict] = {}
    for _a, _i, sid, vid in use['pairs']:
        if vid:
            v = db.execute("""SELECT v.id, v.number, v.state, v.evidence, v.approval_exception, s.code FROM cfg_strategy_versions v
                              JOIN cfg_strategies s ON s.id=v.strategy_id WHERE v.id=%s""", (vid,)).fetchone()
        else:
            v = db.execute("""SELECT v.id, v.number, v.state, v.evidence, v.approval_exception, s.code FROM cfg_strategies s
                              LEFT JOIN LATERAL (SELECT * FROM cfg_strategy_versions WHERE strategy_id=s.id AND state='APPROVED' ORDER BY number DESC LIMIT 1) v ON true
                              WHERE s.id=%s""", (sid,)).fetchone()
        if v:
            versions.setdefault(sid, dict(v))
    for sid, v in sorted(versions.items(), key=lambda kv: kv[1]['code']):
        if not v['id'] or v['state'] != 'APPROVED':
            items['EVIDENCE'].append(_item('EVIDENCE', f"{v['code']}: no approved version" + (f" (v{v['number']} is {v['state'].lower()})" if v['id'] else '') + '.',
                                           'Strategies › backtest the version, then Evidence & approve.', strategy=sid, labels={'strategy': v['code']}))
        elif track != 'PAPER':
            gap = governance.live_evidence_gap(db, v['id'])
            if gap:
                items['PAPER_RECORD'].append(_item('PAPER_RECORD', gap, 'Run it on a Paper workspace until its paper record meets the profile.',
                                                   strategy=sid, labels={'strategy': v['code']}))

    # 8 Order sending.
    if track != 'PAPER' and not controls.get(track, {}).get('trading_enabled'):
        items['ORDER_SENDING'].append(_item('ORDER_SENDING', f'{track} order sending is off.',
                                            'Settings › Order sending (an authorised person; the gates above must pass).'))
    return items


def _overrides(db) -> list[dict]:
    return [store.clean(dict(r)) for r in db.execute("""SELECT o.*, a.code AS account_code FROM setup_overrides o LEFT JOIN cfg_accounts a ON a.id=o.account_id
                                                        WHERE o.revoked_at IS NULL AND (o.expires_at IS NULL OR o.expires_at > now())
                                                        ORDER BY o.requested_at DESC""").fetchall()]


def _covers(o: dict, item: dict) -> bool:
    return o.get('approved_at') is not None and (not o.get('account_id') or str(o['account_id']) == str(item.get('account_id') or ''))


def status(db, tracks=TRACKS) -> dict:
    pol = policy(db)
    overrides = _overrides(db)
    out = {}
    for t in tracks:
        computed = _compute(db, t)
        gates = []
        for gid, name, unlocks, applies in GATES:
            if t not in applies:
                continue
            level = pol['levels'][t].get(gid, 'OFF') if gid in ENFORCED else 'INFO'
            its = computed[gid]
            # A hard gate takes no override: one granted while the gate was softer stops counting when it is raised.
            ovs = [] if level == 'HARD' else [o for o in overrides if o['gate'] == gid and o['track'] == t]
            open_items = [i for i in its if not any(_covers(o, i) for o in ovs)]
            state = 'DONE' if not its else 'OVERRIDDEN' if not open_items else 'BLOCKED' if level in BLOCKING else 'OPEN'
            gates.append({'id': gid, 'step': len(gates) + 1, 'name': name, 'unlocks': unlocks, 'level': level, 'state': state,
                          'items': its, 'open_items': open_items, 'overrides': ovs, 'goto': {'view': WHERE[gid][0], 'section': WHERE[gid][1]}})
        done = sum(g['state'] in ('DONE', 'OVERRIDDEN') for g in gates)
        nxt = next((g for g in gates if g['state'] not in ('DONE', 'OVERRIDDEN')), None)
        out[t] = {'gates': gates, 'done': done, 'total': len(gates), 'complete': nxt is None,
                  'next': ({'gate': nxt['id'], 'name': nxt['name'], 'text': nxt['open_items'][0]['text'] if nxt['open_items'] else '',
                            'fix': nxt['open_items'][0]['fix'] if nxt['open_items'] else '', 'goto': nxt['goto']} if nxt else None)}
    return {'tracks': out, 'policy': pol, 'pending': store.policy(db, 'setup_gates_pending'), 'overrides': overrides,
            'checked_at': datetime.now(timezone.utc).isoformat()}


# ---------- enforcement ----------

def _relevant(item: dict, accounts, instruments, strategies) -> bool:
    for key, scope in (('account_id', accounts), ('instrument_id', instruments), ('strategy_id', strategies)):
        if scope is not None and item.get(key) and str(item[key]) not in {str(s) for s in scope}:
            return False
    return True


def check(db, track: str, accounts=None, instruments=None, strategies=None, only_hard: bool = False, cached: bool = False) -> dict:
    """What blocks, warns or is overridden for an action on this track, narrowed to what it touches."""
    if cached:
        hit = _cache.get(track)
        st = hit[1] if hit and hit[0] > time.monotonic() else None   # machine clock: a 30 s cache, never trading logic
        if st is None:
            st = status(db, (track,))
            _cache[track] = (time.monotonic() + 30, st)
    else:
        st = status(db, (track,))
    blocking, warnings, overridden = [], [], []
    for g in st['tracks'][track]['gates']:
        if g['id'] not in ENFORCED or g['level'] == 'OFF' or (only_hard and g['level'] != 'HARD'):
            continue
        its = [i for i in g['items'] if _relevant(i, accounts, instruments, strategies)]
        if not its:
            continue
        opened = [i for i in its if not any(_covers(o, i) for o in g['overrides'])]
        if not opened:
            overridden.append({'gate': g['id'], 'name': g['name'], 'items': its, 'until': min(o['expires_at'] for o in g['overrides'])})
        elif g['level'] in BLOCKING:
            blocking.append({'gate': g['id'], 'name': g['name'], 'level': g['level'], 'items': opened})
        else:
            warnings.append({'gate': g['id'], 'name': g['name'], 'items': opened})
    return {'blocking': blocking, 'warnings': warnings, 'overridden': overridden}


def enforce(db, action: str, track: str, **scope) -> dict:
    result = check(db, track, **scope)
    if result['blocking']:
        lines = [f"{b['name']} ({b['level'].lower()}): " + '; '.join(i['text'] for i in b['items'][:4]) for b in result['blocking']]
        hint = ('Fix it, or ask for an override in Setup' if any(b['level'] != 'HARD' for b in result['blocking'])
                else 'A hard gate takes no override: fix it, or change its level in Settings › Gates')
        raise HTTPException(409, {'message': f'{action.replace("_", " ").capitalize()} on {track} is held by setup gates. {hint}.',
                                  'gates': result['blocking'],
                                  'unmet': [{'detail': line} for line in lines]})
    return result


def hard_reason(db, track: str, account_id) -> str | None:
    """For the dispatcher: a HARD gate that this account fails (cached 30 s)."""
    result = check(db, track, accounts=[account_id], only_hard=True, cached=True)
    if result['blocking']:
        return 'SETUP_GATE_HARD: ' + '; '.join(f"{b['name']}: {b['items'][0]['text']}" for b in result['blocking'])[:900]
    return None


# ---------- overrides ----------

class OverrideIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    gate: Literal['PEOPLE', 'ACCOUNTS', 'SYMBOLS', 'MARKET_DATA', 'RISK', 'EVIDENCE', 'PAPER_RECORD']
    track: Track
    account_id: UUID | None = None
    hours: int | None = Field(None, ge=1, le=8760)
    reason: str = Field(min_length=5, max_length=500)


def request_override(user, data: OverrideIn) -> dict:
    if not user.has('ADMIN', 'RISK_APPROVER', 'TRADING_OPS'):
        raise HTTPException(403, 'Overrides need Trading operations, Risk approver or Administrator.')
    with store.transaction('write') as db:
        pol = policy(db)
        level = pol['levels'][data.track].get(data.gate, 'OFF')
        if level == 'HARD':
            raise HTTPException(422, f'{data.gate} on {data.track} is a hard gate: it takes no override. Fix it, or change its level in Settings › Gates.')
        if level in ('OFF', 'WARN'):
            raise HTTPException(422, f'{data.gate} on {data.track} does not block ({level.lower()}): no override is needed.')
        hours = data.hours or pol['default_hours']
        cap = pol['max_hours'].get(data.track, 24)
        if hours > cap:
            raise HTTPException(422, f'An override on {data.track} lasts at most {cap} hours.')
        now = datetime.now(timezone.utc)
        soft = level == 'SOFT'
        row = db.execute("""INSERT INTO setup_overrides(id, gate, track, account_id, level, reason, hours, requested_by, approved_by, approved_at, expires_at)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                         (uuid4(), data.gate, data.track, str(data.account_id) if data.account_id else None, level, data.reason, hours,
                          user.username, user.username if soft else None, now if soft else None,
                          now + timedelta(hours=hours) if soft else None)).fetchone()
        store.audit(db, user.username, 'GRANT_OVERRIDE' if soft else 'REQUEST_OVERRIDE', 'setup gate', row['id'], None, dict(row),
                    f"{data.gate} {data.track}{' (one account)' if data.account_id else ''} for {hours} h: {data.reason}")
        _cache.clear()
        return store.clean(dict(row))


def approve_override(user, override_id: UUID) -> dict:
    if not user.has('ADMIN', 'RISK_APPROVER'):
        raise HTTPException(403, 'Approving an override needs Risk approver or Administrator.')
    with store.transaction('write') as db:
        o = db.execute('SELECT * FROM setup_overrides WHERE id=%s FOR UPDATE', (str(override_id),)).fetchone()
        if not o or o['revoked_at']:
            raise HTTPException(404, 'Override not found.')
        if o['approved_at']:
            raise HTTPException(409, 'This override is already active.')
        if o['requested_by'] == user.username:
            raise HTTPException(422, 'A supervised override needs a second person: you asked for it.')
        now = datetime.now(timezone.utc)
        row = db.execute('UPDATE setup_overrides SET approved_by=%s, approved_at=%s, expires_at=%s WHERE id=%s RETURNING *',
                         (user.username, now, now + timedelta(hours=o['hours']), str(override_id))).fetchone()
        store.audit(db, user.username, 'APPROVE_OVERRIDE', 'setup gate', override_id, dict(o), dict(row), f"{o['gate']} {o['track']}: {o['reason']}")
        _cache.clear()
        return store.clean(dict(row))


def revoke_override(user, override_id: UUID, reason: str) -> dict:
    with store.transaction('write') as db:
        o = db.execute('SELECT * FROM setup_overrides WHERE id=%s FOR UPDATE', (str(override_id),)).fetchone()
        if not o or o['revoked_at']:
            raise HTTPException(404, 'Override not found.')
        if o['requested_by'] != user.username and not user.has('ADMIN', 'RISK_APPROVER'):
            raise HTTPException(403, 'Only the requester, a Risk approver or an Administrator can revoke it.')
        row = db.execute('UPDATE setup_overrides SET revoked_by=%s, revoked_at=now(), revoke_reason=%s WHERE id=%s RETURNING *',
                         (user.username, reason, str(override_id))).fetchone()
        store.audit(db, user.username, 'REVOKE_OVERRIDE', 'setup gate', override_id, dict(o), dict(row), reason)
        _cache.clear()
        return store.clean(dict(row))


def report(db) -> dict:
    """Everything an auditor asks: every gate's state per track, active overrides and the last 200 gate decisions."""
    st = status(db)
    history = [store.clean(dict(r)) for r in db.execute(
        """SELECT occurred_at, actor, action, entity, entity_id, reason FROM cfg_audit_log
           WHERE entity IN ('setup gate') OR (entity='policy' AND entity_id IN ('setup_gates','strategy_evidence','risk_minimum'))
           ORDER BY occurred_at DESC, id DESC LIMIT 200""").fetchall()]
    return {**st, 'history': history}


def landing(db, user) -> dict:
    """Should this person land on Setup: the firm's target track is not complete."""
    pol = policy(db)
    st = status(db, (pol['target_track'],))
    t = st['tracks'][pol['target_track']]
    return {'target_track': pol['target_track'], 'complete': t['complete'], 'done': t['done'], 'total': t['total'], 'next': t['next'],
            'overrides': len(st['overrides']), 'land_on_setup': not t['complete']}

