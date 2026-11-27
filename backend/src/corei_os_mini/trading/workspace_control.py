"""Workspace control desks: one shared, owner-held workspace that runs Input → Processing → Output with Start/Pause/Stop.

Why it exists: My Workspace could save a layout and launch a read-only scan, but it could not run anything, and two desks
could drive the same strategy on the same account. A control desk turns its settings into runs (deployments), starts,
pauses and stops only those runs, and shows what they did.
Purpose: Config (input sources, instruments, timeframes; processing strategies, sizing, protection; output environment,
hand-off, accounts, order type, stop policy); plan() — the runs the config implies and every check, each with why and a
fix; start() — create runs it owns and/or adopt existing deployments the operator confirmed; pause / resume / stop with
the desk's stop policy (keep, cancel working orders, close its positions — the last asks to confirm); emergency stop
through scoped kill switches; changes to a running desk are staged, reviewed and applied; results per desk; a saved
per-user layout can be imported.
Responsibilities: one controller per deployment (database unique index); the same activation rules as Strategies
(Risk approver; structural blockers hold a run; on LIVE a different person starts than the one who last changed the
config); every deployment change and the desk change audited in the same transaction.
Must never: send, modify or cancel an order itself (cancels and closes go through trading.oms, which runs the risk check);
resend or cancel an order whose outcome is UNKNOWN (it is listed for resolution instead); turn on LIVE or DEMO order
sending (cfg_execution_controls is read, never written); touch a run it only watches or a run another desk controls.
Dependencies: config_store, strategy_registry (deployment readiness), trading.oms, trading.risk, trading.handoff,
trading.workspaces (timeframes and saved layouts), strategy_studio / strategy_scan_runs (the test run).
Consumers: trading_api (/api/trading/desks/*), the My Workspaces UI.
Architectural position: a workspace layer over Stages 3–8; feeds, engines, risk and brokers stay shared.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .. import config_store as store
from ..strategy_registry import Protect, Sizing, _deployment as registry_deployment
from . import handoff, oms, risk
from .workspaces import Layouts, PREF_KEY, Timeframe

Environment = Literal['OBSERVE', 'PAPER', 'DEMO', 'LIVE']
Handoff = Literal['SIGNAL_ONLY', 'APPROVE', 'AUTO']
StopPolicy = Literal['KEEP', 'CANCEL_ORDERS', 'CLOSE_POSITIONS']
Template = Literal['CUSTOM', 'INSTRUMENT', 'SCANNER', 'MULTI_STRATEGY', 'ACCOUNT', 'RESEARCH', 'EXECUTION']
STRUCTURAL = ('STRATEGY_RETIRED', 'VERSION_NOT_APPROVED', 'INSTRUMENT_INACTIVE', 'NO_TRADING_SYMBOL_ON_ACCOUNT_CONNECTION', 'PAPER_ACCOUNT_IS_SIMULATED',
              'DATA_SOURCE_INACTIVE')
CONTROL = ('OWNED', 'ADOPTED')
HEALTH_OK = ('LIVE', 'CLOSED', 'WAITING')
HEALTH_FAIL = ('INVALID_SYMBOL', 'ERROR')
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class InputCfg(_Strict):
    sources: list[UUID] = Field(default_factory=list, max_length=10)        # data connections, first = primary
    instruments: list[UUID] = Field(default_factory=list, max_length=200)
    timeframes: list[Timeframe] = Field(default_factory=list, max_length=10)  # empty: any strategy timeframe


class StrategyPick(_Strict):
    strategy_id: UUID
    version_id: UUID | None = None   # None: the latest approved version


class ProcessingCfg(_Strict):
    strategies: list[StrategyPick] = Field(default_factory=list, max_length=100)
    sizing: Sizing = Field(default_factory=Sizing)
    protect: Protect = Field(default_factory=Protect)
    signal_expiry_bars: int = Field(2, ge=1, le=500)


class OutputCfg(_Strict):
    environment: Environment = 'OBSERVE'
    handoff: Handoff = 'APPROVE'
    accounts: list[UUID] = Field(default_factory=list, max_length=20)
    order_type: Literal['MARKET', 'LIMIT'] = 'MARKET'
    approval_minutes: int = Field(10, ge=1, le=1440)
    stop_policy: StopPolicy = 'KEEP'


class Config(_Strict):
    input: InputCfg = Field(default_factory=InputCfg)
    processing: ProcessingCfg = Field(default_factory=ProcessingCfg)
    output: OutputCfg = Field(default_factory=OutputCfg)

    @model_validator(mode='after')
    def _unique(self) -> 'Config':
        for label, values in (('source', self.input.sources), ('instrument', self.input.instruments),
                              ('timeframe', self.input.timeframes), ('account', self.output.accounts),
                              ('strategy', [p.strategy_id for p in self.processing.strategies])):
            if len(set(values)) != len(values):
                raise ValueError(f'Each {label} can be chosen once.')
        return self


class DeskIn(_Strict):
    name: Name
    shared: bool = True
    template: Template = 'CUSTOM'
    config: Config = Field(default_factory=Config)


class DeskUpdate(DeskIn):
    version: int = Field(ge=1)


class StartIn(_Strict):
    version: int = Field(ge=1)
    adopt: list[UUID] = Field(default_factory=list, max_length=2000)   # existing deployments the operator confirmed to adopt
    reason: str = Field('Started from the workspace', min_length=5, max_length=500)


class ActIn(_Strict):
    version: int = Field(ge=1)
    reason: str = Field(min_length=5, max_length=500)


class StopIn(ActIn):
    policy: StopPolicy | None = None     # None: the desk's stop policy
    confirm_close: bool = False          # CLOSE_POSITIONS sends closing orders: asked again


def mode_of(env: str) -> str:
    return 'PAPER' if env in ('OBSERVE', 'PAPER') else 'BROKER'


def execution_of(out: OutputCfg) -> dict:
    return {**handoff.DEFAULT, 'handoff': 'SIGNAL_ONLY' if out.environment == 'OBSERVE' else out.handoff,
            'order_type': out.order_type, 'approval_minutes': out.approval_minutes}


# ---------- reading ----------

def _row(db, desk_id: UUID, user, lock: bool = False) -> dict:
    row = db.execute('SELECT * FROM ws_workspaces WHERE id=%s' + (' FOR UPDATE' if lock else ''), (str(desk_id),)).fetchone()
    if not row or (not row['shared'] and row['owner'] != user.username and not user.has('ADMIN')):
        raise HTTPException(404, 'Workspace not found.')
    return dict(row)


def _check_version(row: dict, version: int) -> None:
    if row['version'] != version:
        raise HTTPException(409, 'This workspace was changed by someone else. Reload it before acting.')


def _runs(db, desk_id) -> list[dict]:
    return [dict(r) for r in db.execute("""
        SELECT r.deployment_id, r.role, r.prior_status, r.added_by, r.added_at, d.status, d.mode, d.version,
               s.code AS strategy_code, v.number AS version_number, v.parameters->>'timeframe' AS timeframe,
               i.symbol, a.code AS account_code, c.environment, COALESCE(d.execution->>'handoff', 'AUTO') AS handoff,
               t.last_run_at, t.last_error, t.last_bar_time, t.last_evaluation
        FROM ws_runs r JOIN cfg_deployments d ON d.id=r.deployment_id JOIN cfg_strategies s ON s.id=d.strategy_id
        JOIN cfg_strategy_versions v ON v.id=d.strategy_version_id JOIN cfg_instruments i ON i.id=d.instrument_id
        JOIN cfg_accounts a ON a.id=d.account_id JOIN cfg_connections c ON c.id=a.connection_id
        LEFT JOIN tr_runtime_state t ON t.deployment_id=d.id
        WHERE r.workspace_id=%s ORDER BY s.code, i.symbol, a.code""", (str(desk_id),)).fetchall()]


def _summary(db, desk_ids: list) -> dict:
    """Today's figures and what needs attention, per desk, from its runs (controlled and watched)."""
    if not desk_ids:
        return {}
    day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = db.execute("""
        SELECT r.workspace_id,
               count(*) FILTER (WHERE r.role IN ('OWNED','ADOPTED')) AS controlled,
               count(*) FILTER (WHERE r.role='WATCH') AS watched,
               count(*) FILTER (WHERE r.role IN ('OWNED','ADOPTED') AND d.status='ACTIVE') AS active,
               count(*) FILTER (WHERE t.last_error IS NOT NULL AND t.last_error <> '') AS errors,
               (SELECT count(*) FROM tr_signals s WHERE s.deployment_id = ANY(array_agg(r.deployment_id)) AND s.created_at >= %s) AS signals_today,
               (SELECT count(*) FROM oms_orders o WHERE o.deployment_id = ANY(array_agg(r.deployment_id)) AND o.created_at >= %s) AS orders_today,
               (SELECT count(*) FROM tr_intents x WHERE x.deployment_id = ANY(array_agg(r.deployment_id)) AND x.outcome='PENDING') AS approvals_waiting,
               (SELECT count(*) FROM oms_orders o WHERE o.deployment_id = ANY(array_agg(r.deployment_id)) AND o.state='UNKNOWN') AS unknown_orders,
               (SELECT count(*) FROM pf_positions p WHERE p.deployment_id = ANY(array_agg(r.deployment_id)) AND p.quantity <> 0) AS open_positions
        FROM ws_runs r JOIN cfg_deployments d ON d.id=r.deployment_id LEFT JOIN tr_runtime_state t ON t.deployment_id=d.id
        WHERE r.workspace_id = ANY(%s) GROUP BY r.workspace_id""", (day, day, [str(i) for i in desk_ids])).fetchall()
    return {str(r['workspace_id']): store.clean(dict(r)) for r in rows}


def public(db, row: dict, summary: dict | None = None) -> dict:
    s = summary if summary is not None else _summary(db, [row['id']]).get(str(row['id']), {})
    attention = []
    if s.get('unknown_orders'):
        attention.append(f"{s['unknown_orders']} order(s) with an unknown outcome: resolve them in Execution")
    if s.get('approvals_waiting'):
        attention.append(f"{s['approvals_waiting']} decision(s) waiting for approval")
    if s.get('errors'):
        attention.append(f"{s['errors']} run(s) reported an error")
    if row['staged']:
        attention.append('Changes are staged: review and apply')
    return {**store.clean(row), 'summary': s, 'attention': attention}


def listing(db, user) -> list[dict]:
    rows = [dict(r) for r in db.execute('SELECT * FROM ws_workspaces WHERE shared OR owner=%s OR %s ORDER BY name, created_at',
                                         (user.username, user.has('ADMIN'))).fetchall()]
    sums = _summary(db, [r['id'] for r in rows])
    return [public(db, r, sums.get(str(r['id']), {})) for r in rows]


def detail(db, desk_id: UUID, user) -> dict:
    row = _row(db, desk_id, user)
    return {**public(db, row), 'runs': [store.clean(r) for r in _runs(db, desk_id)]}


def catalogue(db) -> dict:
    """Everything a desk can choose from, with the facts its choices depend on."""
    return {
        'sources': [store.clean(dict(r)) for r in db.execute(
            """SELECT c.id, c.code, c.name, c.environment, c.purpose, c.status, p.code AS provider_code FROM cfg_connections c
               JOIN cfg_providers p ON p.id=c.provider_id WHERE c.purpose IN ('MARKET_DATA','BOTH') ORDER BY c.code""").fetchall()],
        'instruments': [store.clean(dict(r)) for r in db.execute(
            "SELECT id, symbol, name, asset_class, status FROM cfg_instruments ORDER BY symbol").fetchall()],
        'strategies': [store.clean(dict(r)) for r in db.execute(
            """SELECT s.id, s.code, s.name, s.status, v.id AS approved_version_id, v.number AS approved_version,
                      v.parameters->>'timeframe' AS timeframe, v.algorithm,
                      (SELECT json_agg(json_build_object('id', x.id, 'number', x.number, 'state', x.state, 'timeframe', x.parameters->>'timeframe')
                                       ORDER BY x.number DESC) FROM cfg_strategy_versions x WHERE x.strategy_id=s.id AND x.state <> 'RETIRED') AS versions
               FROM cfg_strategies s LEFT JOIN LATERAL (SELECT id, number, parameters, algorithm FROM cfg_strategy_versions
                    WHERE strategy_id=s.id AND state='APPROVED' ORDER BY number DESC LIMIT 1) v ON true
               ORDER BY s.code""").fetchall()],
        'accounts': [store.clean(dict(r)) for r in db.execute(
            """SELECT a.id, a.code, a.name, a.status, a.trading_permission, c.code AS connection_code, c.environment, c.purpose
               FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id ORDER BY a.code""").fetchall()],
        'controls': {k: {'trading_enabled': v['trading_enabled']} for k, v in store.execution_controls(db).items()},
    }


# ---------- the plan: what a config implies, and every check ----------

def plan(db, cfg: Config, desk_id=None) -> dict:
    checks: list[dict] = []

    def check(stage, status, title, detail='', fix='', view=''):
        checks.append({'stage': stage, 'status': status, 'title': title, 'detail': detail, 'fix': fix, 'view': view})

    env = cfg.output.environment
    mode = mode_of(env)
    src_ids = [str(s) for s in cfg.input.sources]
    sources = {str(r['id']): dict(r) for r in db.execute('SELECT id, code, status, purpose FROM cfg_connections WHERE id = ANY(%s)',
                                                          (src_ids,)).fetchall()}
    usable_src = [s for s in src_ids if s in sources and sources[s]['status'] == 'ACTIVE' and sources[s]['purpose'] != 'TRADING']
    bad_src = [sources[s]['code'] if s in sources else s for s in src_ids if s not in usable_src]
    if not src_ids:
        check('Input', 'FAIL', 'Choose at least one data source', 'Without a data source no run has prices or bars.',
              'Add a source under Input → Data sources.', 'Market data')
    elif not usable_src:
        check('Input', 'FAIL', 'No usable data source', f"Inactive or trading-only: {', '.join(bad_src)}.",
              'Activate the connection in Setup, or choose another source.', 'Configuration')
    else:
        check('Input', 'WARN' if bad_src else 'PASS', f"Sources · {', '.join(sources[s]['code'] for s in usable_src)}",
              f"Left out (inactive or trading-only): {', '.join(bad_src)}." if bad_src else 'First source is primary; the others are backups for instruments it does not carry.')

    ins_ids = [str(i) for i in cfg.input.instruments]
    instruments = {str(r['id']): dict(r) for r in db.execute('SELECT id, symbol, status FROM cfg_instruments WHERE id = ANY(%s)',
                                                              (ins_ids,)).fetchall()}
    mappings: dict[str, dict] = {}
    for m in db.execute("""SELECT id, connection_id, instrument_id, broker_symbol FROM cfg_symbol_mappings
                           WHERE instrument_id = ANY(%s) AND connection_id = ANY(%s) AND usage IN ('MARKET_DATA','BOTH') AND status='ACTIVE'""",
                        (ins_ids, usable_src)).fetchall():
        mappings.setdefault(str(m['instrument_id']), {})[str(m['connection_id'])] = dict(m)
    data: dict[str, dict] = {}
    no_map, inactive = [], []
    for i in ins_ids:
        if i not in instruments or instruments[i]['status'] != 'ACTIVE':
            inactive.append(instruments.get(i, {}).get('symbol', i))
            continue
        found = next((mappings[i][s] for s in usable_src if s in mappings.get(i, {})), None)
        if found:
            data[i] = found
        else:
            no_map.append(instruments[i]['symbol'])
    if not ins_ids:
        check('Input', 'FAIL', 'Choose at least one instrument', fix='Add instruments under Input → Instruments.')
    elif not data:
        check('Input', 'FAIL', 'No instrument has a price source', f"No market-data symbol on the chosen sources for: {', '.join(no_map + inactive)}.",
              'Map the instrument on a source connection in Setup → Symbols, or add a source that carries it.', 'Configuration')
    else:
        left = no_map + inactive
        check('Input', 'WARN' if left else 'PASS', f'Instruments · {len(data)} of {len(ins_ids)} have a price source',
              (f"Left out: {', '.join(no_map)} (no market-data symbol on these sources)" if no_map else '')
              + (f"{'; ' if no_map else ''}inactive: {', '.join(inactive)}" if inactive else ''),
              'Map the missing symbols in Setup → Symbols.' if left else '', 'Configuration' if left else '')

    # Market data health, per instrument on its chosen source (Stage 1 plan health).
    if data:
        plans = db.execute("""SELECT p.instrument_id, a.connection_id, p.code, p.status, h.state, h.detail
                              FROM md_collection_plans p JOIN cfg_accounts a ON a.id=p.account_id
                              LEFT JOIN md_plan_health h ON h.plan_id=p.id
                              WHERE p.instrument_id = ANY(%s) AND p.status <> 'ARCHIVED'""", (list(data),)).fetchall()
        failing, waiting, missing = [], [], []
        for i, m in data.items():
            mine = [p for p in plans if str(p['instrument_id']) == i and str(p['connection_id']) == str(m['connection_id'])]
            sym = instruments[i]['symbol']
            if any(p['status'] == 'ACTIVE' and p['state'] in HEALTH_OK for p in mine):
                continue
            bad = [p for p in mine if p['state'] in HEALTH_FAIL]
            if bad:
                failing.append(f"{sym} ({bad[0]['code']}: {bad[0]['detail'] or bad[0]['state'].lower().replace('_', ' ')})")
            elif mine:
                waiting.append(f"{sym} ({(mine[0]['state'] or mine[0]['status']).lower().replace('_', ' ')})")
            else:
                missing.append(sym)
        if failing:
            check('Market data', 'FAIL', 'A collection plan can never receive a price', '; '.join(failing),
                  'Fix the symbol or the error in Market data, or remove the instrument here.', 'Market data')
        if waiting or missing:
            check('Market data', 'WARN', 'Some instruments have no live feed yet',
                  '; '.join(([f"No collection plan: {', '.join(missing)}"] if missing else []) + ([f"Not live: {', '.join(waiting)}"] if waiting else [])),
                  'Create or start a collection plan in Market data; runs evaluate only on bars that arrive.', 'Market data')
        if not (failing or waiting or missing):
            check('Market data', 'PASS', 'Every instrument has a live (or closed-market) feed')

    # Strategies: approved versions only; timeframe must be one of the desk's.
    strat_ids = [str(p.strategy_id) for p in cfg.processing.strategies]
    strategies = {str(r['id']): dict(r) for r in db.execute('SELECT id, code, status FROM cfg_strategies WHERE id = ANY(%s)',
                                                             (strat_ids,)).fetchall()}
    versions = {str(r['id']): dict(r) for r in db.execute(
        """SELECT id, strategy_id, number, state, parameters->>'timeframe' AS timeframe FROM cfg_strategy_versions WHERE strategy_id = ANY(%s)""",
        (strat_ids,)).fetchall()}
    chosen: dict[str, dict] = {}
    out_strats = []
    for p in cfg.processing.strategies:
        sid = str(p.strategy_id)
        s = strategies.get(sid)
        if not s or s['status'] != 'ACTIVE':
            out_strats.append(f"{s['code'] if s else sid}: retired")
            continue
        mine = [v for v in versions.values() if str(v['strategy_id']) == sid]
        v = versions.get(str(p.version_id)) if p.version_id else max((x for x in mine if x['state'] == 'APPROVED'), key=lambda x: x['number'], default=None)
        if not v or str(v['strategy_id']) != sid:
            out_strats.append(f"{s['code']}: no approved version")
            continue
        if v['state'] != 'APPROVED':
            out_strats.append(f"{s['code']} v{v['number']}: {v['state'].lower()}, not approved")
            continue
        tf = v['timeframe'] or 'H1'
        if cfg.input.timeframes and tf not in cfg.input.timeframes:
            out_strats.append(f"{s['code']} v{v['number']}: runs on {tf}, not one of this workspace's timeframes")
            continue
        chosen[sid] = {**v, 'code': s['code'], 'timeframe': tf}
    if not strat_ids:
        check('Processing', 'FAIL', 'Choose at least one strategy', fix='Add strategies under Processing → Strategies.', view='Strategies')
    elif not chosen:
        check('Processing', 'FAIL', 'No strategy can run', '; '.join(out_strats),
              'Approve a version in Strategies, or add its timeframe under Input → Timeframes.', 'Strategies')
    else:
        check('Processing', 'WARN' if out_strats else 'PASS', f'Strategies · {len(chosen)} of {len(strat_ids)} approved and in scope',
              ('Left out: ' + '; '.join(out_strats)) if out_strats else 'Each strategy runs on its approved version\'s own timeframe.',
              'Approve the version in Strategies, or add its timeframe.' if out_strats else '', 'Strategies' if out_strats else '')

    # Accounts: environment must match; order sending must be on for DEMO / LIVE (read only — never switched here).
    acc_ids = [str(a) for a in cfg.output.accounts]
    accounts = {str(r['id']): dict(r) for r in db.execute(
        """SELECT a.id, a.code, a.status, a.trading_permission, a.connection_id, c.environment, c.purpose
           FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id WHERE a.id = ANY(%s)""", (acc_ids,)).fetchall()}
    usable_acc, bad_acc = [], []
    for a in acc_ids:
        r = accounts.get(a)
        why = ('not found' if not r else 'market-data-only connection' if r['purpose'] == 'MARKET_DATA' else 'inactive' if r['status'] != 'ACTIVE'
               else f"a {r['environment']} account" if env in ('DEMO', 'LIVE') and r['environment'] != env else '')
        if env in ('DEMO', 'LIVE') and r and not why and r['trading_permission'] == 'DISABLED':
            why = 'trading permission disabled'
        (bad_acc.append(f"{r['code'] if r else a}: {why}") if why else usable_acc.append(a))
    if not acc_ids:
        check('Output', 'FAIL', 'Choose at least one account',
              'Every run is booked to an account' + (' (Observe and Paper send nothing to it).' if mode == 'PAPER' else '.'),
              'Add an account under Output → Accounts.')
    elif not usable_acc:
        check('Output', 'FAIL', f'No account can be used for {env.title()}', '; '.join(bad_acc),
              'Choose an account of the right environment, or change the environment.', 'Configuration')
    else:
        check('Output', 'WARN' if bad_acc else 'PASS', f"Accounts · {', '.join(accounts[a]['code'] for a in usable_acc)}",
              ('Left out: ' + '; '.join(bad_acc)) if bad_acc else
              {'OBSERVE': 'Observe: decisions are recorded as signals only; nothing is ordered.',
               'PAPER': 'Paper: orders are simulated by COREI at live prices; nothing reaches a broker.',
               'DEMO': 'Broker DEMO: real orders to the demo account.', 'LIVE': 'LIVE: real orders with real money.'}[env])
    controls = store.execution_controls(db)
    if env in ('DEMO', 'LIVE') and not controls.get(env, {}).get('trading_enabled'):
        check('Output', 'FAIL', f'{env} order sending is off',
              'LIVE order sending is an owner control; a workspace never turns it on.' if env == 'LIVE' else 'DEMO order sending is off in Controls.',
              'An authorised owner switches it on in Setup → Controls, or choose Observe or Paper.', 'Configuration')
    if usable_acc and data and chosen:
        from . import setup_gates
        gates = setup_gates.check(db, 'PAPER' if mode == 'PAPER' else env, accounts=usable_acc, instruments=list(data), strategies=list(chosen))
        for b in gates['blocking']:
            check('Setup', 'FAIL', f"Setup gate: {b['name']} ({b['level'].lower()})", '; '.join(i['text'] for i in b['items'][:4]),
                  b['items'][0]['fix'] + (' Or ask for an override in Setup.' if b['level'] != 'HARD' else ' This gate is hard: no override.'), 'Setup')
        for w in gates['warnings']:
            check('Setup', 'WARN', f"Setup gate: {w['name']}", '; '.join(i['text'] for i in w['items'][:4]), w['items'][0]['fix'], 'Setup')
        for o in gates['overridden']:
            check('Setup', 'WARN', f"Setup gate: {o['name']} overridden until {str(o['until'])[:16]}",
                  '; '.join(i['text'] for i in o['items'][:4]))
    if env == 'LIVE':
        from . import readiness
        live = readiness.live_policy(db)
        if not live['ok']:
            check('Risk', 'FAIL', 'The pre-LIVE policy is not met', '; '.join(u['detail'] for u in live['unmet']),
                  'Meet each rule (Risk › Limits, Execution, Setup › Users and Alerts) or record an exception where allowed.', 'Risk')
        from . import governance
        gaps = [g for g in (governance.live_evidence_gap(db, v['id']) for v in chosen.values()) if g]
        if gaps:
            check('Processing', 'FAIL', 'Strategies without LIVE evidence', '; '.join(gaps),
                  'Run them on Paper until the paper record is met (Strategies › Evidence).', 'Strategies')
        check('Output', 'WARN', 'LIVE needs a second person',
              'A different person must start this workspace than the one who last changed its settings; every LIVE order also follows the LIVE four-eyes policy.')

    # Risk: limits are inherited from the risk stage and can never be loosened here.
    limits = db.execute('SELECT kind, scope FROM risk_limits WHERE enabled').fetchall()
    kills = db.execute("SELECT scope FROM risk_kill_switches WHERE engaged").fetchall()
    if any(k['scope'] == 'FIRM' for k in kills):
        check('Risk', 'WARN', 'The firm-wide kill switch is engaged', 'Runs start, but every order is refused until it is released.', view='Risk')
    if mode == 'BROKER' and not limits:
        check('Risk', 'WARN', 'No risk limits are set', 'Every order still passes the risk check, but nothing caps size or loss.',
              'Set limits in Risk (daily loss, position size).', 'Risk')
    else:
        check('Risk', 'PASS', f'Risk · {len(limits)} limit(s) apply',
              'Inherited from Risk; a workspace can never loosen them. Every order passes the pre-trade check.')

    # The runs: strategy × instrument × account, one deployment per combination.
    runs: list[dict] = []
    if chosen and data and usable_acc:
        trading = {(str(r['connection_id']), str(r['instrument_id'])) for r in db.execute(
            """SELECT connection_id, instrument_id FROM cfg_symbol_mappings WHERE usage IN ('TRADING','BOTH') AND status='ACTIVE'
               AND instrument_id = ANY(%s)""", (list(data),)).fetchall()}
        existing = {(str(r['strategy_id']), str(r['instrument_id']), str(r['account_id'])): dict(r) for r in db.execute(
            """SELECT d.id, d.strategy_id, d.instrument_id, d.account_id, d.status, d.mode, d.strategy_version_id,
                      w.id AS desk_id, w.name AS desk_name, r.role,
                      EXISTS (SELECT 1 FROM pf_positions p WHERE p.deployment_id=d.id AND p.mode='BROKER' AND p.quantity <> 0) AS broker_open,
                      EXISTS (SELECT 1 FROM oms_orders o WHERE o.deployment_id=d.id AND o.mode='BROKER' AND o.state = ANY(%s)) AS broker_orders
               FROM cfg_deployments d LEFT JOIN ws_runs r ON r.deployment_id=d.id AND r.role IN ('OWNED','ADOPTED')
               LEFT JOIN ws_workspaces w ON w.id=r.workspace_id
               WHERE d.strategy_id = ANY(%s) AND d.instrument_id = ANY(%s) AND d.account_id = ANY(%s)""",
            (list(oms.OPEN), list(chosen), list(data), usable_acc)).fetchall()}
        no_trade = []
        for sid, v in chosen.items():
            for i, m in data.items():
                for a in usable_acc:
                    base = {'strategy_id': sid, 'strategy_code': v['code'], 'version_id': str(v['id']), 'version_number': v['number'],
                            'timeframe': v['timeframe'], 'instrument_id': i, 'symbol': instruments[i]['symbol'], 'account_id': a,
                            'account_code': accounts[a]['code'], 'data_mapping_id': str(m['id']), 'data_symbol': m['broker_symbol']}
                    if mode == 'BROKER' and (str(accounts[a]['connection_id']), i) not in trading:
                        no_trade.append(f"{instruments[i]['symbol']} on {accounts[a]['code']}")
                        continue
                    e = existing.get((sid, i, a))
                    if not e:
                        runs.append({**base, 'action': 'CREATE', 'reason': 'New run owned by this workspace'})
                    elif e['status'] == 'ENDED':
                        runs.append({**base, 'action': 'SKIP', 'deployment_id': str(e['id']),
                                     'reason': 'An ended deployment holds this combination; delete it in Strategies to run it here'})
                    elif e['desk_id'] and str(e['desk_id']) == str(desk_id):
                        runs.append({**base, 'action': 'KEEP', 'deployment_id': str(e['id']), 'status': e['status'], 'role': e['role'],
                                     'reason': 'Already run by this workspace'})
                    elif e['desk_id']:
                        runs.append({**base, 'action': 'WATCH', 'deployment_id': str(e['id']), 'controlled_by': e['desk_name'],
                                     'reason': f"Runs in “{e['desk_name']}”: only one workspace may control it; watched here"})
                    elif e['mode'] != mode and e['mode'] == 'BROKER' and (e['broker_open'] or e['broker_orders']):
                        runs.append({**base, 'action': 'WATCH', 'deployment_id': str(e['id']),
                                     'reason': 'An existing broker deployment with an open broker position or working order: close them '
                                               'before it can move to paper; watched here'})
                    elif e['mode'] != mode:
                        target = {'PAPER': 'paper', 'BROKER': f"broker {env}"}[mode]
                        runs.append({**base, 'action': 'ADOPT', 'deployment_id': str(e['id']), 'status': e['status'],
                                     'switch_mode': True, 'from_mode': e['mode'],
                                     'reason': f"An existing {e['mode'].lower()} deployment outside any workspace: adopt it and move it to "
                                               f"{target} to run it here (asked at Start), or leave it watched"})
                    else:
                        runs.append({**base, 'action': 'ADOPT', 'deployment_id': str(e['id']), 'status': e['status'],
                                     'reason': f"An existing {e['status'].lower()} deployment outside any workspace: adopt it to control it here, or watch it"})
        if no_trade:
            check('Output', 'WARN', 'Some instruments cannot be traded on some accounts',
                  'No trading symbol on the account connection: ' + ', '.join(no_trade[:12]) + (' …' if len(no_trade) > 12 else ''),
                  'Map the trading symbol in Setup → Symbols.', 'Configuration')
        _size_check(db, cfg, runs, instruments, accounts, check)
        watched = [r for r in runs if r['action'] == 'WATCH' and r.get('controlled_by')]
        if watched:
            check('Ownership', 'WARN', f'{len(watched)} run(s) are controlled by another workspace',
                  '; '.join(f"{r['strategy_code']} × {r['symbol']} × {r['account_code']} in “{r['controlled_by']}”" for r in watched[:6]),
                  'They are watched here. Stop them there, or remove them from that workspace, to run them here.')
        adoptable = [r for r in runs if r['action'] == 'ADOPT']
        if adoptable:
            moving = [r for r in adoptable if r.get('switch_mode')]
            check('Ownership', 'WARN', f'{len(adoptable)} existing deployment(s) can be adopted',
                  'Start asks you to confirm each one; unconfirmed ones are watched, not changed.'
                  + (f" {len(moving)} of them run in another mode ({', '.join(sorted({r['from_mode'].lower() for r in moving}))}); adopting moves "
                     f"them to {'paper' if mode == 'PAPER' else 'broker ' + env}." if moving else ''),
                  'Tick them in the Start window to run them from this workspace.')
        if not watched and not adoptable:
            check('Ownership', 'PASS', 'No other workspace controls these runs')
    controllable = [r for r in runs if r['action'] in ('CREATE', 'KEEP', 'ADOPT')]
    if chosen and data and usable_acc and not controllable:
        why = '; '.join(f"{r['strategy_code']} × {r['symbol']} × {r['account_code']}: {r['reason']}" for r in runs[:6]) \
            or 'No instrument can be traded on these accounts (no trading symbol).'
        check('Ownership', 'FAIL', 'Nothing to run', why,
              'Stop it in the workspace that controls it, delete an ended deployment in Strategies, or map the trading symbol.', 'Strategies')
    order = {'FAIL': 0, 'WARN': 1, 'PASS': 2}
    checks.sort(key=lambda c: (['Setup', 'Input', 'Market data', 'Processing', 'Risk', 'Output', 'Ownership'].index(c['stage']), order[c['status']]))
    return {
        'checks': checks, 'runs': runs, 'ready': not any(c['status'] == 'FAIL' for c in checks) and bool(controllable),
        'environment': env, 'mode': mode, 'handoff': execution_of(cfg.output)['handoff'],
        'coverage': {'instruments': [len(data), len(ins_ids)], 'strategies': [len(chosen), len(strat_ids)],
                     'accounts': [len(usable_acc), len(acc_ids)], 'timeframes': sorted({v['timeframe'] for v in chosen.values()}),
                     'create': sum(r['action'] == 'CREATE' for r in runs), 'keep': sum(r['action'] == 'KEEP' for r in runs),
                     'adopt': sum(r['action'] == 'ADOPT' for r in runs), 'watch': sum(r['action'] == 'WATCH' for r in runs),
                     'skip': sum(r['action'] == 'SKIP' for r in runs)},
        'checked_at': datetime.now(timezone.utc).isoformat(),
    }


def _size_check(db, cfg: Config, runs: list[dict], instruments: dict, accounts: dict, check) -> None:
    """A fixed size that every order would breach is a configuration error, not a market event: say so before Start."""
    from .common import D, fx_rate, quote
    from .risk import applicable
    sz = cfg.processing.sizing
    if sz.method != 'FIXED_QTY' or not sz.quantity:
        return
    qty = D(sz.quantity)
    seen, refused, notes = set(), [], []
    for r in runs:
        if r['action'] not in ('CREATE', 'KEEP', 'ADOPT') or (r['instrument_id'], r['account_id']) in seen:
            continue
        seen.add((r['instrument_id'], r['account_id']))
        lim = applicable(db, {'ACCOUNT': r['account_id'], 'INSTRUMENT': r['instrument_id'], 'STRATEGY': r['strategy_id']})
        ins = db.execute('SELECT symbol, contract_size, quote_currency FROM cfg_instruments WHERE id=%s', (r['instrument_id'],)).fetchone()
        if 'MAX_ORDER_QTY' in lim and qty > D(lim['MAX_ORDER_QTY']['value']):
            refused.append(f"{ins['symbol']} on {r['account_code']}: {qty.normalize()} lots > order limit {D(lim['MAX_ORDER_QTY']['value']).normalize()}")
        q = quote(db, r['account_id'], r['instrument_id'])
        if q and q.get('mid'):
            ccy = db.execute('SELECT base_currency FROM cfg_accounts WHERE id=%s', (r['account_id'],)).fetchone()['base_currency'] or 'USD'
            rate = fx_rate(db, ins['quote_currency'], ccy)
            if rate is not None:
                value = (qty * (D(ins['contract_size']) or 1) * q['mid'] * rate).quantize(D('0.01'))
                notes.append(f"{ins['symbol']}: {qty.normalize()} lots ≈ {value.normalize()} {ccy}")
                if 'MAX_ORDER_NOTIONAL' in lim and value > D(lim['MAX_ORDER_NOTIONAL']['value']):
                    refused.append(f"{ins['symbol']} on {r['account_code']}: order value {value.normalize()} {ccy} > limit "
                                   f"{D(lim['MAX_ORDER_NOTIONAL']['value']).normalize()} {ccy}")
    if refused:
        check('Risk', 'FAIL', 'Every order would be refused by a risk limit', '; '.join(refused),
              'Reduce the quantity under Processing → Sizing, or use Risk % of equity sizing.')
    elif notes:
        check('Risk', 'PASS', 'Order size fits the limits', '; '.join(notes[:6]))


# ---------- writing ----------

def _audit_desk(db, actor, action, before, after, reason=''):
    store.audit(db, actor, action, 'workspace', (after or before)['id'], before, after, reason)


def create(db, user, data: DeskIn) -> dict:
    row = dict(db.execute("""INSERT INTO ws_workspaces(id, name, owner, shared, template, config, config_by)
                             VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                          (uuid4(), data.name, user.username, data.shared, data.template,
                           Jsonb(data.config.model_dump(mode='json')), user.username)).fetchone())
    _audit_desk(db, user.username, 'CREATE', None, row)
    return public(db, row)


def _may_edit(row: dict, user) -> None:
    if row['owner'] != user.username and not user.has('ADMIN', 'TRADING_OPS', 'RISK_APPROVER'):
        raise HTTPException(403, 'Only the owner, trading operations or a risk approver can change this workspace.')


def update(db, user, desk_id: UUID, data: DeskUpdate) -> dict:
    """A stopped desk changes at once; a running or paused desk stages the change for review and apply."""
    before = _row(db, desk_id, user, lock=True)
    _check_version(before, data.version)
    _may_edit(before, user)
    if data.shared is False and before['owner'] != user.username:
        raise HTTPException(403, 'Only the owner can make a workspace private.')
    cfg = Jsonb(data.config.model_dump(mode='json'))
    if before['state'] == 'STOPPED':
        after = db.execute("""UPDATE ws_workspaces SET name=%s, shared=%s, template=%s, config=%s, config_by=%s, staged=NULL, staged_by=NULL,
                              version=version+1, updated_at=now() WHERE id=%s RETURNING *""",
                           (data.name, data.shared, data.template, cfg, user.username, str(desk_id))).fetchone()
        _audit_desk(db, user.username, 'UPDATE', before, dict(after))
    else:
        same = data.config.model_dump(mode='json') == before['config']
        after = db.execute("""UPDATE ws_workspaces SET name=%s, shared=%s, template=%s, staged=%s, staged_by=%s,
                              version=version+1, updated_at=now() WHERE id=%s RETURNING *""",
                           (data.name, data.shared, data.template, None if same else cfg, None if same else user.username, str(desk_id))).fetchone()
        _audit_desk(db, user.username, 'STAGE' if not same else 'UPDATE', before, dict(after),
                    'Staged while running: applies only after review' if not same else '')
    return public(db, dict(after))


def _diff(a, b, path='') -> list[dict]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            out += _diff(a.get(k), b.get(k), f'{path}.{k}' if path else k)
        return out
    return [] if a == b else [{'setting': path, 'running': a, 'staged': b}]


def review(db, user, desk_id: UUID) -> dict:
    row = _row(db, desk_id, user)
    if not row['staged']:
        return {'changes': [], 'plan': None}
    staged = Config.model_validate(row['staged'])
    return {'changes': _diff(row['config'], row['staged']), 'plan': plan(db, staged, desk_id), 'staged_by': row['staged_by']}


def discard(db, user, desk_id: UUID, version: int) -> dict:
    before = _row(db, desk_id, user, lock=True)
    _check_version(before, version)
    after = db.execute('UPDATE ws_workspaces SET staged=NULL, staged_by=NULL, version=version+1, updated_at=now() WHERE id=%s RETURNING *',
                       (str(desk_id),)).fetchone()
    _audit_desk(db, user.username, 'DISCARD_STAGED', before, dict(after))
    return public(db, dict(after))


def validate(db, user, desk_id: UUID, staged: bool = False) -> dict:
    row = _row(db, desk_id, user, lock=True)
    cfg = Config.model_validate(row['staged'] if staged and row['staged'] else row['config'])
    result = plan(db, cfg, desk_id)
    db.execute('UPDATE ws_workspaces SET last_check=%s WHERE id=%s', (Jsonb(json.loads(json.dumps(result, default=str))), str(desk_id)))
    return result


def _set_status(db, dep_id, status: str, actor: str, reason: str) -> dict:
    before = dict(db.execute('SELECT * FROM cfg_deployments WHERE id=%s FOR UPDATE', (str(dep_id),)).fetchone())
    if before['status'] == status:
        return before
    after = dict(db.execute('UPDATE cfg_deployments SET status=%s, version=version+1, updated_at=now()'
                            + (', activated_by=%s, activated_at=now()' if status == 'ACTIVE' else '') + ' WHERE id=%s RETURNING *',
                            (status, actor, str(dep_id)) if status == 'ACTIVE' else (status, str(dep_id))).fetchone())
    store.audit(db, actor, {'ACTIVE': 'ACTIVATE', 'PAUSED': 'PAUSE'}.get(status, status), 'deployment', dep_id, before, after, reason)
    return after


def _activate(db, dep_id, actor: str, reason: str, env: str) -> str | None:
    """Activate under the same rules as Strategies; return why it was held, or None."""
    current = registry_deployment(db, UUID(str(dep_id)))
    structural = [b for b in current['blockers'] if b in STRUCTURAL]
    if structural:
        return ', '.join(b.replace('_', ' ').lower() for b in structural)
    from .. import platform_mode
    if env == 'LIVE' and not platform_mode.second_person_ok(db, current['proposed_by'], actor):
        return 'on LIVE a different person must activate than the one who proposed it'
    if env == 'LIVE':
        from . import governance
        gap = governance.live_evidence_gap(db, current['strategy_version_id'])
        if gap:
            return gap
    _set_status(db, dep_id, 'ACTIVE', actor, reason)
    return None


def _converge(db, desk: dict, cfg: Config, result: dict, actor: str, adopt: set[str], activate: bool, reason: str) -> dict:
    """Make the desk's runs match its plan: create, update, adopt, watch, and let go of runs no longer in it."""
    out = {'created': [], 'adopted': [], 'watched': [], 'activated': [], 'held': [], 'released': [], 'updated': []}
    env, mode = cfg.output.environment, mode_of(cfg.output.environment)
    execution = execution_of(cfg.output)
    sizing, protect = cfg.processing.sizing.model_dump(), cfg.processing.protect.model_dump(exclude_none=True)
    wanted = set()
    for r in result['runs']:
        if r['action'] == 'CREATE':
            row = dict(db.execute("""INSERT INTO cfg_deployments(id, strategy_id, strategy_version_id, instrument_id, account_id, data_mapping_id,
                                         note, proposed_by, mode, sizing, signal_expiry_bars, protect, execution, status)
                                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PROPOSED') RETURNING *""",
                                  (uuid4(), r['strategy_id'], r['version_id'], r['instrument_id'], r['account_id'], r['data_mapping_id'],
                                   f"Workspace: {desk['name']}"[:1000], desk['config_by'], mode, Jsonb(sizing), cfg.processing.signal_expiry_bars,
                                   Jsonb(protect), Jsonb(execution))).fetchone())
            store.audit(db, actor, 'PROPOSE', 'deployment', row['id'], None, row, f"Created by workspace {desk['name']}")
            db.execute("INSERT INTO ws_runs(workspace_id, deployment_id, role, added_by) VALUES (%s,%s,'OWNED',%s)", (str(desk['id']), str(row['id']), actor))
            out['created'].append(str(row['id']))
            wanted.add(str(row['id']))
            dep_id = str(row['id'])
        elif r['action'] == 'KEEP':
            dep_id = r['deployment_id']
            wanted.add(dep_id)
            if r.get('role') == 'OWNED':
                before = dict(db.execute('SELECT * FROM cfg_deployments WHERE id=%s FOR UPDATE', (dep_id,)).fetchone())
                change = {'strategy_version_id': r['version_id'], 'data_mapping_id': r['data_mapping_id'], 'mode': mode,
                          'sizing': sizing, 'signal_expiry_bars': cfg.processing.signal_expiry_bars, 'protect': protect, 'execution': execution}
                if any((str(before[k]) if k in ('strategy_version_id', 'data_mapping_id') else before[k]) != v for k, v in change.items()):
                    after = dict(db.execute("""UPDATE cfg_deployments SET strategy_version_id=%s, data_mapping_id=%s, mode=%s, sizing=%s,
                                                  signal_expiry_bars=%s, protect=%s, execution=%s, version=version+1, updated_at=now()
                                               WHERE id=%s RETURNING *""",
                                            (r['version_id'], r['data_mapping_id'], mode, Jsonb(sizing), cfg.processing.signal_expiry_bars,
                                             Jsonb(protect), Jsonb(execution), dep_id)).fetchone())
                    store.audit(db, actor, 'UPDATE', 'deployment', dep_id, before, after, f"Workspace {desk['name']} settings applied")
                    out['updated'].append(dep_id)
        elif r['action'] == 'ADOPT' and r['deployment_id'] in adopt:
            dep_id = r['deployment_id']
            db.execute("""INSERT INTO ws_runs(workspace_id, deployment_id, role, prior_status, added_by) VALUES (%s,%s,'ADOPTED',%s,%s)
                          ON CONFLICT (workspace_id, deployment_id) DO UPDATE SET role='ADOPTED', prior_status=EXCLUDED.prior_status,
                                                                                added_by=EXCLUDED.added_by, added_at=now()""",
                       (str(desk['id']), dep_id, r['status'], actor))
            store.audit(db, actor, 'ADOPT', 'deployment', dep_id, None, {'workspace': str(desk['id']), 'prior_status': r['status']},
                        f"Adopted by workspace {desk['name']} with confirmation")
            if r.get('switch_mode'):
                before = dict(db.execute('SELECT * FROM cfg_deployments WHERE id=%s FOR UPDATE', (dep_id,)).fetchone())
                after = dict(db.execute('UPDATE cfg_deployments SET mode=%s, version=version+1, updated_at=now() WHERE id=%s RETURNING *',
                                        (mode, dep_id)).fetchone())
                store.audit(db, actor, 'UPDATE', 'deployment', dep_id, before, after,
                            f"Moved from {before['mode'].lower()} to {mode.lower()} on adoption by workspace {desk['name']} (confirmed)")
                out['updated'].append(dep_id)
            out['adopted'].append(dep_id)
            wanted.add(dep_id)
        elif r['action'] in ('ADOPT', 'WATCH'):
            db.execute("""INSERT INTO ws_runs(workspace_id, deployment_id, role, added_by) VALUES (%s,%s,'WATCH',%s)
                          ON CONFLICT (workspace_id, deployment_id) DO NOTHING""", (str(desk['id']), r['deployment_id'], actor))
            out['watched'].append(r['deployment_id'])
            wanted.add(r['deployment_id'])
            continue
        else:
            continue
        if activate:
            held = _activate(db, dep_id, actor, reason, env)
            (out['held'].append({'deployment_id': dep_id, 'run': f"{r['strategy_code']} × {r['symbol']} × {r['account_code']}", 'why': held})
             if held else out['activated'].append(dep_id))
    for link in db.execute('SELECT r.deployment_id, r.role, d.status FROM ws_runs r JOIN cfg_deployments d ON d.id=r.deployment_id '
                           'WHERE r.workspace_id=%s', (str(desk['id']),)).fetchall():
        dep_id = str(link['deployment_id'])
        if dep_id in wanted:
            continue
        if link['role'] in CONTROL and link['status'] == 'ACTIVE':
            _set_status(db, dep_id, 'PAUSED', actor, f"Removed from workspace {desk['name']}")
        db.execute('DELETE FROM ws_runs WHERE workspace_id=%s AND deployment_id=%s', (str(desk['id']), dep_id))
        store.audit(db, actor, 'RELEASE', 'deployment', dep_id, {'workspace': str(desk['id']), 'role': link['role']}, None,
                    f"No longer in workspace {desk['name']}")
        out['released'].append(dep_id)
    return out


def _four_eyes(desk: dict, cfg: Config, actor: str, settings_by: str) -> None:
    from .. import platform_mode
    if cfg.output.environment == 'LIVE' and settings_by == actor and not platform_mode.current()['operating'] == 'SOLO':
        raise HTTPException(422, 'On LIVE a different person must start or apply this workspace than the one who last changed its settings.')


def start(db, user, desk_id: UUID, data: StartIn) -> dict:
    desk = _row(db, desk_id, user, lock=True)
    _check_version(desk, data.version)
    if desk['state'] == 'RUNNING':
        raise HTTPException(409, 'This workspace is already running.')
    if desk['staged']:
        raise HTTPException(409, 'Changes are staged. Apply or discard them before starting.')
    cfg = Config.model_validate(desk['config'])
    result = plan(db, cfg, desk_id)
    if not result['ready']:
        raise HTTPException(422, {'message': 'Fix the failed checks before starting.', 'plan': json.loads(json.dumps(result, default=str))})
    _four_eyes(desk, cfg, user.username, desk['config_by'])
    adopt = {str(a) for a in data.adopt}
    unknown = adopt - {r['deployment_id'] for r in result['runs'] if r['action'] == 'ADOPT'}
    if unknown:
        raise HTTPException(409, 'A deployment to adopt is no longer adoptable. Validate again.')
    if not any(r['action'] in ('CREATE', 'KEEP') for r in result['runs']) and not adopt:
        raise HTTPException(422, 'Nothing would run: every run here already exists outside this workspace. '
                                 'Tick the deployments to adopt in the Start window.')
    done = _converge(db, desk, cfg, result, user.username, adopt, True, data.reason)
    after = dict(db.execute("""UPDATE ws_workspaces SET state='RUNNING', state_reason=%s, state_by=%s, state_at=now(), last_check=%s,
                               version=version+1, updated_at=now() WHERE id=%s RETURNING *""",
                            (data.reason, user.username, Jsonb(json.loads(json.dumps(result, default=str))), str(desk_id))).fetchone())
    _audit_desk(db, user.username, 'START', desk, after, data.reason)
    return {**public(db, after), 'result': done}


def apply_staged(db, user, desk_id: UUID, data: StartIn) -> dict:
    desk = _row(db, desk_id, user, lock=True)
    _check_version(desk, data.version)
    if not desk['staged']:
        raise HTTPException(409, 'Nothing is staged.')
    cfg = Config.model_validate(desk['staged'])
    result = plan(db, cfg, desk_id)
    if not result['ready']:
        raise HTTPException(422, {'message': 'The staged settings fail validation.', 'plan': json.loads(json.dumps(result, default=str))})
    _four_eyes(desk, cfg, user.username, desk['staged_by'])
    adopt = {str(a) for a in data.adopt}
    staged_desk = {**desk, 'config_by': desk['staged_by']}
    done = _converge(db, staged_desk, cfg, result, user.username, adopt, desk['state'] == 'RUNNING', data.reason)
    after = dict(db.execute("""UPDATE ws_workspaces SET config=staged, config_by=staged_by, staged=NULL, staged_by=NULL, last_check=%s,
                               version=version+1, updated_at=now() WHERE id=%s RETURNING *""",
                            (Jsonb(json.loads(json.dumps(result, default=str))), str(desk_id))).fetchone())
    _audit_desk(db, user.username, 'APPLY_STAGED', desk, after, data.reason)
    return {**public(db, after), 'result': done}


def _controlled(db, desk_id) -> list[dict]:
    return [dict(r) for r in db.execute("""SELECT d.id, d.status, d.mode, d.proposed_by, d.account_id, d.instrument_id, d.strategy_id
                                           FROM ws_runs r JOIN cfg_deployments d ON d.id=r.deployment_id
                                           WHERE r.workspace_id=%s AND r.role IN ('OWNED','ADOPTED')""", (str(desk_id),)).fetchall()]


def pause(db, user, desk_id: UUID, data: ActIn) -> dict:
    desk = _row(db, desk_id, user, lock=True)
    _check_version(desk, data.version)
    if desk['state'] != 'RUNNING':
        raise HTTPException(409, 'Only a running workspace can be paused.')
    paused = [str(d['id']) for d in _controlled(db, desk_id) if d['status'] == 'ACTIVE']
    for dep in paused:
        _set_status(db, dep, 'PAUSED', user.username, f"Workspace {desk['name']} paused: {data.reason}")
    after = dict(db.execute("""UPDATE ws_workspaces SET state='PAUSED', state_reason=%s, state_by=%s, state_at=now(), version=version+1,
                               updated_at=now() WHERE id=%s RETURNING *""", (data.reason, user.username, str(desk_id))).fetchone())
    _audit_desk(db, user.username, 'PAUSE', desk, after, data.reason)
    return {**public(db, after), 'result': {'paused': paused}}


def resume(db, user, desk_id: UUID, data: ActIn) -> dict:
    desk = _row(db, desk_id, user, lock=True)
    _check_version(desk, data.version)
    if desk['state'] != 'PAUSED':
        raise HTTPException(409, 'Only a paused workspace can be resumed.')
    cfg = Config.model_validate(desk['config'])
    _four_eyes(desk, cfg, user.username, desk['config_by'])
    out = {'activated': [], 'held': []}
    for d in _controlled(db, desk_id):
        if d['status'] in ('PAUSED', 'PROPOSED'):
            held = _activate(db, d['id'], user.username, f"Workspace {desk['name']} resumed: {data.reason}", cfg.output.environment)
            (out['held'].append({'deployment_id': str(d['id']), 'why': held}) if held else out['activated'].append(str(d['id'])))
    after = dict(db.execute("""UPDATE ws_workspaces SET state='RUNNING', state_reason=%s, state_by=%s, state_at=now(), version=version+1,
                               updated_at=now() WHERE id=%s RETURNING *""", (data.reason, user.username, str(desk_id))).fetchone())
    _audit_desk(db, user.username, 'RESUME', desk, after, data.reason)
    return {**public(db, after), 'result': out}


def stop(db, user, desk_id: UUID, data: StopIn) -> tuple[dict, dict]:
    """Stop new decisions (pause every controlled run). Returns the desk and the follow-up the policy asks for; the caller
    runs it after this transaction commits, through trading.oms."""
    desk = _row(db, desk_id, user, lock=True)
    _check_version(desk, data.version)
    if desk['state'] == 'STOPPED':
        raise HTTPException(409, 'This workspace is already stopped.')
    policy = data.policy or Config.model_validate(desk['config']).output.stop_policy
    if policy == 'CLOSE_POSITIONS' and not data.confirm_close:
        raise HTTPException(422, "Closing this workspace's positions sends closing orders. Confirm to continue.")
    deps = _controlled(db, desk_id)
    for d in deps:
        if d['status'] == 'ACTIVE':
            _set_status(db, d['id'], 'PAUSED', user.username, f"Workspace {desk['name']} stopped: {data.reason}")
    after = dict(db.execute("""UPDATE ws_workspaces SET state='STOPPED', state_reason=%s, state_by=%s, state_at=now(), version=version+1,
                               updated_at=now() WHERE id=%s RETURNING *""", (data.reason, user.username, str(desk_id))).fetchone())
    _audit_desk(db, user.username, 'STOP', desk, after, f'{policy}: {data.reason}')
    return public(db, after), {'policy': policy, 'deployments': [str(d['id']) for d in deps], 'reason': data.reason}


def stop_follow_up(actor: str, follow: dict) -> dict:
    """Cancel working orders (never one with an UNKNOWN outcome) and, if asked, close positions — each through trading.oms,
    which checks risk and audits. Runs after the stop has committed, so the runs are already paused."""
    out = {'cancelled': [], 'cancel_failed': [], 'unknown': [], 'close_orders': [], 'close_failed': []}
    if follow['policy'] == 'KEEP' or not follow['deployments']:
        return out
    with store.transaction('read') as db:
        orders = db.execute('SELECT id, state FROM oms_orders WHERE deployment_id = ANY(%s) AND state = ANY(%s) ORDER BY created_at',
                            (follow['deployments'], list(oms.OPEN))).fetchall()
    for o in orders:
        if o['state'] == 'UNKNOWN':
            out['unknown'].append(str(o['id']))   # resolved by querying the broker or an operator, never cancelled blind
            continue
        if o['state'] == 'CANCEL_PENDING':
            continue
        try:
            oms.cancel(actor, o['id'], f"Workspace stop: {follow['reason']}"[:500])
            out['cancelled'].append(str(o['id']))
        except HTTPException as exc:
            out['cancel_failed'].append({'order_id': str(o['id']), 'why': str(exc.detail)})
    if follow['policy'] == 'CLOSE_POSITIONS':
        with store.transaction('write') as db:
            rows = db.execute('SELECT p.*, d.strategy_id FROM pf_positions p JOIN cfg_deployments d ON d.id=p.deployment_id '
                              'WHERE p.deployment_id = ANY(%s) AND p.quantity <> 0 ORDER BY p.id', (follow['deployments'],)).fetchall()
            key = uuid4().hex
            for r in rows:
                qty = oms.D(r['quantity'])
                try:
                    with db.transaction():
                        o = oms.place(db, account_id=r['account_id'], instrument_id=r['instrument_id'], side='SELL' if qty > 0 else 'BUY',
                                      quantity=abs(qty), mode=r['mode'], deployment={'id': r['deployment_id'], 'strategy_id': r['strategy_id']},
                                      seed=f"desk-close:{key}:{r['id']}", actor=actor, source='FLATTEN', flatten=True)
                        store.audit(db, actor, 'FLATTEN_POSITION', 'order', o['id'], None, o, f"Workspace stop: {follow['reason']}")
                        out['close_orders'].append({'order_id': str(o['id']), 'state': o['state']})
                except HTTPException as exc:
                    out['close_failed'].append({'position_id': str(r['id']), 'why': str(exc.detail)})
        if any(c['state'] != 'FILLED' for c in out['close_orders']):
            from . import execution
            execution.dispatch_soon()
    return out


def emergency(db, user, desk_id: UUID, reason: str) -> dict:
    """Engage a deployment-scoped kill switch on every run this desk controls and pause them. Releasing is done in Risk."""
    desk = _row(db, desk_id, user, lock=True)
    switches = []
    for d in _controlled(db, desk_id):
        sw = risk.engage(user.username, risk.KillIn(scope='DEPLOYMENT', target_id=d['id'], reason=f"Workspace {desk['name']} emergency stop: {reason}"[:500]), db=db)
        switches.append(str(sw['id']))
        if d['status'] == 'ACTIVE':
            _set_status(db, d['id'], 'PAUSED', user.username, f'Emergency stop: {reason}')
    after = dict(db.execute("""UPDATE ws_workspaces SET state='STOPPED', state_reason=%s, state_by=%s, state_at=now(), version=version+1,
                               updated_at=now() WHERE id=%s RETURNING *""", (f'Emergency stop: {reason}'[:500], user.username, str(desk_id))).fetchone())
    _audit_desk(db, user.username, 'EMERGENCY_STOP', desk, after, reason)
    return {**public(db, after), 'result': {'kill_switches': switches}}


def release_run(db, user, desk_id: UUID, deployment_id: UUID, version: int) -> dict:
    """Let go of one run. A controlled run that is active is paused first (its status before adoption is not restored:
    nothing starts again without a person activating it in Strategies)."""
    desk = _row(db, desk_id, user, lock=True)
    _check_version(desk, version)
    link = db.execute('SELECT * FROM ws_runs WHERE workspace_id=%s AND deployment_id=%s', (str(desk_id), str(deployment_id))).fetchone()
    if not link:
        raise HTTPException(404, 'This run is not part of the workspace.')
    dep = db.execute('SELECT status FROM cfg_deployments WHERE id=%s', (str(deployment_id),)).fetchone()
    if link['role'] in CONTROL and dep['status'] == 'ACTIVE':
        _set_status(db, deployment_id, 'PAUSED', user.username, f"Released from workspace {desk['name']}")
    db.execute('DELETE FROM ws_runs WHERE workspace_id=%s AND deployment_id=%s', (str(desk_id), str(deployment_id)))
    store.audit(db, user.username, 'RELEASE', 'deployment', deployment_id, dict(link), None, f"Released from workspace {desk['name']}")
    after = dict(db.execute('UPDATE ws_workspaces SET version=version+1, updated_at=now() WHERE id=%s RETURNING *', (str(desk_id),)).fetchone())
    return public(db, after)


def delete(db, user, desk_id: UUID, version: int) -> None:
    desk = _row(db, desk_id, user, lock=True)
    _check_version(desk, version)
    if desk['owner'] != user.username and not user.has('ADMIN'):
        raise HTTPException(403, 'Only the owner can delete this workspace.')
    if desk['state'] != 'STOPPED':
        raise HTTPException(422, 'Stop the workspace before deleting it.')
    db.execute('DELETE FROM ws_workspaces WHERE id=%s', (str(desk_id),))   # runs stay as (paused) deployments in Strategies
    _audit_desk(db, user.username, 'DELETE', desk, None)


# ---------- test run and results ----------

def test_run(db, user, desk_id: UUID) -> dict:
    """A read-only research scan of the desk's instruments and strategies: no runs, no orders."""
    from ..strategy_scan_runs import _json, _public
    from ..strategy_studio import SignalScanIn, resolve_scan_scope
    desk = _row(db, desk_id, user, lock=True)
    cfg = Config.model_validate(desk['staged'] or desk['config'])
    result = plan(db, cfg, desk_id)
    symbols = sorted({r['symbol'] for r in result['runs']}) or None
    codes = sorted({r['strategy_code'] for r in result['runs']}) or None
    if not symbols or not codes:
        raise HTTPException(422, 'Choose instruments with a price source and approved strategies first.')
    prior = desk['test_run_id'] and db.execute('SELECT status FROM studio_scan_runs WHERE id=%s', (str(desk['test_run_id']),)).fetchone()
    if prior and prior['status'] in ('QUEUED', 'RUNNING'):
        raise HTTPException(409, 'A test run is already in progress.')
    stop_atr = cfg.processing.protect.stop_atr or '1.5'
    request = SignalScanIn(instrument_symbols=symbols, strategy_codes=codes, stop_atr=stop_atr)
    instruments, strategies = resolve_scan_scope(db, request)
    row = db.execute("""INSERT INTO studio_scan_runs (id,operator,mode,request,instruments,strategies,name)
                        VALUES (%s,%s,'instrument',%s,%s,%s,%s) RETURNING *""",
                     (uuid4(), user.username, _json(request.model_dump(mode='json')), _json(instruments), _json(strategies),
                      f"Test: {desk['name']}"[:80])).fetchone()
    db.execute('UPDATE ws_workspaces SET test_run_id=%s WHERE id=%s', (row['id'], str(desk_id)))
    store.audit(db, user.username, 'TEST_RUN', 'workspace', desk_id, None, {'scan_run': str(row['id'])}, 'No runs, no orders')
    return _public(row)


def test_status(db, user, desk_id: UUID) -> dict | None:
    from ..strategy_scan_runs import _public
    desk = _row(db, desk_id, user)
    if not desk['test_run_id']:
        return None
    row = db.execute('SELECT * FROM studio_scan_runs WHERE id=%s', (str(desk['test_run_id']),)).fetchone()
    return _public(row) if row else None


TABS = ('runs', 'signals', 'decisions', 'orders', 'positions', 'log')


def results(db, user, desk_id: UUID, tab: str, limit: int = 100) -> list[dict]:
    _row(db, desk_id, user)
    ids = [str(r['deployment_id']) for r in db.execute('SELECT deployment_id FROM ws_runs WHERE workspace_id=%s', (str(desk_id),)).fetchall()]
    if tab == 'runs':
        return [store.clean(r) for r in _runs(db, desk_id)]
    if tab == 'log':
        return [store.clean(dict(r)) for r in db.execute(
            """SELECT occurred_at, actor, action, entity, entity_id, reason FROM cfg_audit_log
               WHERE (entity='workspace' AND entity_id=%s) OR (entity='deployment' AND entity_id = ANY(%s))
               ORDER BY occurred_at DESC, id DESC LIMIT %s""", (str(desk_id), ids, limit)).fetchall()]
    if not ids:
        return []
    q = {
        'signals': """SELECT x.id, x.created_at AS at, i.symbol, x.timeframe, st.code AS strategy_code, x.side, x.strength, x.state,
                             x.explanation, x.reasons, a.code AS account_code, x.deployment_id
                      FROM tr_signals x JOIN cfg_instruments i ON i.id=x.instrument_id JOIN cfg_accounts a ON a.id=x.account_id
                      LEFT JOIN cfg_strategies st ON st.id=x.strategy_id WHERE x.deployment_id = ANY(%s)
                      ORDER BY x.created_at DESC, x.id DESC LIMIT %s""",
        'decisions': """SELECT x.id, x.created_at AS at, i.symbol, st.code AS strategy_code, x.outcome, x.handoff, x.side, x.quantity,
                               x.target, x.reasons, x.sizing_note, x.order_id, x.expires_at, a.code AS account_code, x.deployment_id
                        FROM tr_intents x JOIN cfg_instruments i ON i.id=x.instrument_id JOIN cfg_accounts a ON a.id=x.account_id
                        JOIN cfg_deployments d ON d.id=x.deployment_id JOIN cfg_strategies st ON st.id=d.strategy_id
                        WHERE x.deployment_id = ANY(%s) ORDER BY x.created_at DESC, x.id DESC LIMIT %s""",
        'orders': """SELECT o.id, o.created_at AS at, i.symbol, st.code AS strategy_code, o.side, o.quantity, o.filled_qty, o.state, o.mode,
                            o.source, a.code AS account_code, o.deployment_id,
                            (SELECT reasons FROM risk_decisions r WHERE r.order_id=o.id ORDER BY r.at DESC LIMIT 1) AS risk_reasons
                     FROM oms_orders o JOIN cfg_instruments i ON i.id=o.instrument_id JOIN cfg_accounts a ON a.id=o.account_id
                     LEFT JOIN cfg_deployments d ON d.id=o.deployment_id LEFT JOIN cfg_strategies st ON st.id=d.strategy_id
                     WHERE o.deployment_id = ANY(%s) ORDER BY o.created_at DESC, o.id DESC LIMIT %s""",
        'positions': """SELECT p.id, p.updated_at AS at, i.symbol, st.code AS strategy_code, p.mode, p.quantity, p.avg_price, p.realised_pnl,
                               p.currency, p.stop_loss, p.take_profit, a.code AS account_code, p.deployment_id
                        FROM pf_positions p JOIN cfg_instruments i ON i.id=p.instrument_id JOIN cfg_accounts a ON a.id=p.account_id
                        JOIN cfg_deployments d ON d.id=p.deployment_id JOIN cfg_strategies st ON st.id=d.strategy_id
                        WHERE p.deployment_id = ANY(%s) AND p.quantity <> 0 ORDER BY i.symbol, p.id LIMIT %s""",
    }[tab]
    return [store.clean(dict(r)) for r in db.execute(q, (ids, limit)).fetchall()]


# ---------- import a saved per-user layout ----------

def import_layout(db, user, number: int) -> dict:
    row = db.execute('SELECT value FROM cfg_user_prefs WHERE username=%s AND key=%s', (user.username, PREF_KEY)).fetchone()
    if not row:
        raise HTTPException(404, 'You have no saved layouts.')
    layout = Layouts.model_validate(row['value'])
    w = next((x for x in layout.items if x.number == number), None)
    if not w:
        raise HTTPException(404, 'That saved layout was not found.')
    if db.execute('SELECT 1 FROM ws_workspaces WHERE legacy_user=%s AND legacy_number=%s', (user.username, number)).fetchone():
        raise HTTPException(409, 'This layout was already imported.')
    symbols = list((w.scan.instrument_symbols if w.scan else []) or [])
    codes = list((w.scan.strategy_codes if w.scan else []) or [])
    ins = [str(r['id']) for r in db.execute('SELECT id FROM cfg_instruments WHERE symbol = ANY(%s) ORDER BY symbol', (symbols,)).fetchall()]
    if w.instrument and str(w.instrument) not in ins:
        ins.insert(0, str(w.instrument))
    strats = [{'strategy_id': str(r['id'])} for r in db.execute('SELECT id FROM cfg_strategies WHERE code = ANY(%s) ORDER BY code', (codes,)).fetchall()]
    accounts = list(dict.fromkeys(str(a) for a in ([w.account] if w.account else []) + list(w.mapping_accounts)))
    sources = []
    if w.source:
        c = db.execute('SELECT connection_id FROM cfg_accounts WHERE id=%s', (str(w.source),)).fetchone()
        sources = [str(c['connection_id'])] if c else []
    cfg = Config.model_validate({'input': {'sources': sources, 'instruments': ins, 'timeframes': [w.timeframe]},
                                 'processing': {'strategies': strats}, 'output': {'environment': w.mode, 'accounts': accounts}})
    created = create(db, user, DeskIn(name=(w.name or f'My Workspace {w.number}')[:80], config=cfg))
    db.execute('UPDATE ws_workspaces SET legacy_user=%s, legacy_number=%s WHERE id=%s', (user.username, number, created['id']))
    return {**created, 'legacy_user': user.username, 'legacy_number': number}
