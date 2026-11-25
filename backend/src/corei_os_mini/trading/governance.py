"""Strategy evidence and risk minimums: what a version must show before approval, and what risk must exist before orders.

Why it exists: on 25 Nov 2026 any draft could be approved with no backtest, no version had ever been backtested, and
DEMO and LIVE order sending were on with no risk limit. Retail traders, professional desks and institutions need
different bars, so each policy is a profile (RETAIL, PROFESSIONAL, INSTITUTIONAL) with optional per-value overrides.
Purpose:
  - evidence(db, version_id): the version's lifecycle stage (DRAFT → BACKTESTED → PAPER_PROVEN → APPROVED) and every
    check, each with what was found, what is needed and how to fix it. Approval needs the backtest checks; running on
    LIVE also needs the paper checks. A recorded exception (a reason, a risk approver) replaces the backtest checks only
    where the profile allows it.
  - risk_minimum(db, environment): the limits each DEMO trading account must have (LIVE keeps readiness.live_policy);
    enforced when DEMO order sending is switched on and when a workspace starts on DEMO; at the dispatcher only when the
    profile says so (INSTITUTIONAL).
  - preset(...): limit values for a profile as a share of an account's equity; apply_preset only creates or tightens a
    limit, never loosens one.
  - run_losses(...): today's P&L of runs (deployments), for strategy- and run-scoped loss limits.
  - clone_to_timeframe(...): a copy of a strategy on another timeframe, as a new draft that earns its own evidence.
Must never: approve, activate or change a limit by itself (callers decide); loosen a limit; count a backtest of other
parameters, another algorithm or another timeframe as evidence; use binary floating point for a money or limit value.
Dependencies: config_store, trading.portfolio, trading.risk, trading.common. Consumers: strategy_registry (approve),
trading.workspace_control (plan / activate), config_store (order-sending switch), trading.execution (gate), trading_api.
Architectural position: Stage 4 / Stage 5 governance.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from .. import config_store as store
from .common import D, ZERO, fx_rate

Profile = Literal['NONE', 'RETAIL', 'PROFESSIONAL', 'INSTITUTIONAL']

# ---------- strategy evidence ----------

EVIDENCE_PROFILES: dict[str, dict] = {
    # NONE: no gate (a sandbox or a single-person test install); approval still records whatever evidence exists.
    'NONE': {'require_backtest': False, 'min_trades': 0, 'min_history_days': 0, 'require_costs': False, 'require_net_positive': False,
             'require_oos': False, 'max_drawdown_pct': None, 'live_paper_days': 0, 'live_paper_trades': 0, 'allow_exception': True},
    # min_trades / min_history_days: the backtest; require_costs: spread or commission charged; require_net_positive:
    # net P&L after costs > 0; require_oos: the last 30% of trades still net ≥ 0; max_drawdown_pct: backtest drawdown cap;
    # live_paper_days / live_paper_trades: paper record of this version before it may run on LIVE; allow_exception: a
    # risk approver may approve without the backtest checks, with a recorded reason.
    'RETAIL': {'require_backtest': True, 'min_trades': 20, 'min_history_days': 30, 'require_costs': False, 'require_net_positive': True, 'require_oos': False,
               'max_drawdown_pct': '30', 'live_paper_days': 3, 'live_paper_trades': 5, 'allow_exception': True},
    'PROFESSIONAL': {'require_backtest': True, 'min_trades': 30, 'min_history_days': 90, 'require_costs': True, 'require_net_positive': True, 'require_oos': True,
                     'max_drawdown_pct': '20', 'live_paper_days': 10, 'live_paper_trades': 10, 'allow_exception': True},
    'INSTITUTIONAL': {'require_backtest': True, 'min_trades': 50, 'min_history_days': 180, 'require_costs': True, 'require_net_positive': True, 'require_oos': True,
                      'max_drawdown_pct': '15', 'live_paper_days': 20, 'live_paper_trades': 30, 'allow_exception': False},
}
OOS_SHARE = Decimal('0.3')


class EvidencePolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    require_backtest: bool | None = None
    min_trades: int | None = Field(None, ge=0, le=100000)
    min_history_days: int | None = Field(None, ge=0, le=36500)
    require_costs: bool | None = None
    require_net_positive: bool | None = None
    require_oos: bool | None = None
    max_drawdown_pct: Decimal | None = Field(None, ge=0, le=100, max_digits=6, decimal_places=2)
    live_paper_days: int | None = Field(None, ge=0, le=3650)
    live_paper_trades: int | None = Field(None, ge=0, le=100000)
    allow_exception: bool | None = None


# ---------- risk minimums (limits as a share of equity) ----------

RISK_PROFILES: dict[str, dict] = {
    'NONE': {},
    # Percent of equity unless the kind is itself a percentage, a ratio or a count.
    'RETAIL': {'MAX_DAILY_LOSS': '5', 'MAX_DRAWDOWN_PCT': '25', 'MAX_ORDER_NOTIONAL': '300', 'MAX_GROSS_EXPOSURE': '1000',
               'MAX_LEVERAGE': '10', 'MAX_OPEN_ORDERS': '20', 'MAX_ORDERS_PER_MINUTE': '30', 'MAX_PRICE_DEVIATION_PCT': '2'},
    'PROFESSIONAL': {'MAX_DAILY_LOSS': '3', 'MAX_DRAWDOWN_PCT': '15', 'MAX_ORDER_NOTIONAL': '200', 'MAX_GROSS_EXPOSURE': '500',
                     'MAX_LEVERAGE': '5', 'MAX_OPEN_ORDERS': '50', 'MAX_ORDERS_PER_MINUTE': '60', 'MAX_PRICE_DEVIATION_PCT': '1'},
    'INSTITUTIONAL': {'MAX_DAILY_LOSS': '1', 'MAX_DRAWDOWN_PCT': '8', 'MAX_ORDER_NOTIONAL': '100', 'MAX_GROSS_EXPOSURE': '300',
                      'MAX_LEVERAGE': '3', 'MAX_OPEN_ORDERS': '100', 'MAX_ORDERS_PER_MINUTE': '120', 'MAX_PRICE_DEVIATION_PCT': '0.5'},
}
OF_EQUITY = ('MAX_DAILY_LOSS', 'MAX_ORDER_NOTIONAL', 'MAX_GROSS_EXPOSURE')     # converted to account currency
# The limits a DEMO trading account must have (any one kind of each group, firm or account scope).
DEMO_NEEDED = {
    'NONE': (),
    'RETAIL': (('MAX_DAILY_LOSS',),),
    'PROFESSIONAL': (('MAX_DAILY_LOSS',), ('MAX_ORDER_NOTIONAL', 'MAX_ORDER_QTY'), ('MAX_POSITION_QTY', 'MAX_GROSS_EXPOSURE')),
    'INSTITUTIONAL': (('MAX_ORDER_NOTIONAL',), ('MAX_DAILY_LOSS',), ('MAX_POSITION_QTY', 'MAX_GROSS_EXPOSURE'), ('MAX_DRAWDOWN_PCT',)),
}
DISPATCH_ENFORCED = {'NONE': False, 'RETAIL': False, 'PROFESSIONAL': False, 'INSTITUTIONAL': True}


class RiskPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    demo_needed: list[list[str]] | None = Field(None, max_length=10)
    enforce_demo_at_dispatch: bool | None = None


class PolicyIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    profile: Profile
    overrides: dict = Field(default_factory=dict)
    reason: str = Field(min_length=5, max_length=500)


def _policy_row(db, key: str) -> dict:
    v = store.policy(db, key) or {}
    return {'profile': v.get('profile') or 'PROFESSIONAL', 'overrides': v.get('overrides') or {}}


def evidence_policy(db) -> dict:
    p = _policy_row(db, 'strategy_evidence')
    return {**EVIDENCE_PROFILES[p['profile']], **{k: v for k, v in p['overrides'].items() if v is not None}, 'profile': p['profile']}


def risk_policy(db) -> dict:
    p = _policy_row(db, 'risk_minimum')
    o = p['overrides']
    return {'profile': p['profile'], 'demo_needed': [tuple(g) for g in (o.get('demo_needed') or DEMO_NEEDED[p['profile']])],
            'enforce_demo_at_dispatch': o.get('enforce_demo_at_dispatch', DISPATCH_ENFORCED[p['profile']]),
            'preset': RISK_PROFILES[p['profile']]}


def set_policy(actor: str, key: Literal['strategy_evidence', 'risk_minimum'], data: PolicyIn) -> dict:
    model = EvidencePolicy if key == 'strategy_evidence' else RiskPolicy
    try:
        overrides = model.model_validate(data.overrides).model_dump(mode='json', exclude_none=True)
    except Exception as exc:  # noqa: BLE001 - a pydantic error becomes a 422 the screen can show
        raise HTTPException(422, f'Invalid override: {exc}') from None
    if key == 'risk_minimum' and 'demo_needed' in overrides:
        from .risk import KINDS as LIMIT_KINDS
        bad = [k for g in overrides['demo_needed'] for k in g if k not in LIMIT_KINDS]
        if bad or any(not g for g in overrides['demo_needed']):
            raise HTTPException(422, f"Unknown limit kind: {', '.join(bad) or 'an empty group'}.")
    value = {'profile': data.profile, 'overrides': overrides}
    with store.transaction('write') as db:
        before = db.execute('SELECT * FROM cfg_policies WHERE key=%s FOR UPDATE', (key,)).fetchone()
        row = db.execute("""INSERT INTO cfg_policies(key, value, updated_by) VALUES (%s,%s,%s)
                            ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_by=EXCLUDED.updated_by, version=cfg_policies.version+1,
                                                            updated_at=now() RETURNING *""", (key, Jsonb(value), actor)).fetchone()
        store.audit(db, actor, 'UPDATE_POLICY', 'policy', key, dict(before) if before else None, dict(row), data.reason)
        return evidence_policy(db) if key == 'strategy_evidence' else _public_risk(risk_policy(db))


def _public_risk(p: dict) -> dict:
    return {**p, 'demo_needed': [list(g) for g in p['demo_needed']]}


# ---------- evidence of one version ----------

def _check(code, ok, found, need, fix='', applies=True):
    return {'code': code, 'ok': bool(ok) if applies else True, 'applies': applies, 'found': found, 'need': need, 'fix': fix}


def _oos(trades: list[dict]) -> tuple[int, Decimal]:
    """The last 30% of trades by exit time: how many, and their net P&L."""
    ordered = sorted(trades, key=lambda t: t.get('exit_time') or '')
    n = max(1, int((Decimal(len(ordered)) * OOS_SHARE).to_integral_value())) if ordered else 0
    tail = ordered[-n:] if n else []
    return len(tail), sum((D(t['pnl']) for t in tail), ZERO)


def evidence(db, version_id) -> dict:
    v = db.execute("""SELECT v.*, s.code AS strategy_code FROM cfg_strategy_versions v JOIN cfg_strategies s ON s.id=v.strategy_id
                      WHERE v.id=%s""", (str(version_id),)).fetchone()
    if not v:
        raise HTTPException(404, 'Strategy version not found.')
    pol = evidence_policy(db)
    tf = (v['parameters'] or {}).get('timeframe', 'H1')
    rows = db.execute("""SELECT id, algorithm, parameters, timeframe, range_from, range_to, settings, metrics, trades, account_id, broker_symbol, created_at
                         FROM tr_backtests WHERE strategy_version_id=%s ORDER BY created_at DESC LIMIT 50""", (str(version_id),)).fetchall()
    same = [r for r in rows if r['algorithm'] == v['algorithm'] and (r['parameters'] or {}) == (v['parameters'] or {}) and r['timeframe'] == tf]
    bt = same[0] if same else None
    checks = []
    fix_bt = f"Run a backtest of {v['strategy_code']} v{v['number']} in Strategies › Backtest (its own parameters, {tf})."
    checks.append(_check('BACKTEST', bt, f"{len(same)} backtest(s) of these exact parameters" if rows else 'no backtest',
                         'a backtest of this version, its parameters and timeframe', fix_bt, pol['require_backtest']))
    if bt and pol['require_backtest']:
        m, s = bt['metrics'] or {}, bt['settings'] or {}
        days = (bt['range_to'] - bt['range_from']).days
        trades = int(m.get('trades') or 0)
        net = D(m.get('net_pnl')) or ZERO
        costs = (int(s.get('spread_ticks') or 0) > 0) or (D(s.get('commission_per_lot')) or ZERO) > 0
        n_oos, oos_net = _oos(bt['trades'] or [])
        dd = D(m.get('max_drawdown_pct'))
        checks += [
            _check('TRADES', trades >= pol['min_trades'], f'{trades} trades', f"≥ {pol['min_trades']}", 'Backtest a longer period.'),
            _check('HISTORY', days >= pol['min_history_days'], f'{days} days', f"≥ {pol['min_history_days']} days",
                   'Backtest a longer period (collect or backfill history in Market data).'),
            _check('COSTS', costs, 'spread or commission charged' if costs else 'no spread and no commission',
                   'spread ticks or commission per lot > 0', 'Run it again with the broker spread (ticks) and commission.',
                   pol['require_costs']),
            _check('NET_AFTER_COSTS', net > 0, f"net {net} {m.get('currency', '')}".strip(), '> 0', 'This version loses money in the test: change it.',
                   pol['require_net_positive']),
            _check('OUT_OF_SAMPLE', n_oos >= 5 and oos_net >= 0, f'last 30%: {n_oos} trades, net {oos_net}', '≥ 5 trades, net ≥ 0',
                   'The edge does not hold in the most recent part: more history, or change it.', pol['require_oos']),
            _check('DRAWDOWN', dd is not None and dd <= D(pol['max_drawdown_pct']), f'{dd}%', f"≤ {pol['max_drawdown_pct']}%",
                   'Reduce risk per trade or add a stop.', pol.get('max_drawdown_pct') not in (None, '')),
        ]
    # Paper record of this exact version (fills of its paper runs).
    paper = db.execute("""SELECT count(*) FILTER (WHERE f.realised_pnl <> 0) AS closed, min(f.at) AS first, max(f.at) AS last
                          FROM oms_fills f JOIN cfg_deployments d ON d.id=f.deployment_id
                          WHERE f.mode='PAPER' AND d.strategy_version_id=%s""", (str(version_id),)).fetchone()
    p_days = (paper['last'] - paper['first']).days if paper['first'] else 0
    live = [
        _check('PAPER_DAYS', p_days >= pol['live_paper_days'], f'{p_days} day(s) of paper trading', f"≥ {pol['live_paper_days']}",
               'Run it on Paper first (a workspace on Paper, or a paper deployment).'),
        _check('PAPER_TRADES', int(paper['closed'] or 0) >= pol['live_paper_trades'], f"{int(paper['closed'] or 0)} closed paper trade(s)",
               f"≥ {pol['live_paper_trades']}", 'Let it trade on Paper longer.'),
    ]
    backtest_ok = all(c['ok'] for c in checks)
    paper_ok = all(c['ok'] for c in live)
    stage = ('APPROVED' if v['state'] == 'APPROVED' else 'RETIRED' if v['state'] == 'RETIRED'
             else 'PAPER_PROVEN' if backtest_ok and paper_ok else 'BACKTESTED' if backtest_ok else 'DRAFT')
    return {'version_id': str(v['id']), 'strategy_code': v['strategy_code'], 'number': v['number'], 'timeframe': tf, 'state': v['state'],
            'stage': stage, 'policy': pol, 'checks': checks, 'live_checks': live, 'ok_for_approval': backtest_ok, 'ok_for_live': backtest_ok and paper_ok,
            'backtest': ({'id': str(bt['id']), 'at': bt['created_at'].isoformat(), 'range': [bt['range_from'].isoformat(), bt['range_to'].isoformat()],
                          'metrics': bt['metrics']} if bt else None),
            'approved_with': v['evidence'], 'exception': v['approval_exception']}


def approval_evidence(db, version_id, actor: str, exception: str | None) -> tuple[dict, str | None]:
    """What approval records, or a 422 naming every unmet check. An exception needs a reason and a profile that allows it."""
    ev = evidence(db, version_id)
    snapshot = {'stage': ev['stage'], 'policy': ev['policy'], 'checks': ev['checks'], 'backtest': ev['backtest'], 'by': actor,
                'at': datetime.now(timezone.utc).isoformat()}
    if ev['ok_for_approval']:
        return snapshot, None
    unmet = [c for c in ev['checks'] if not c['ok']]
    if exception:
        if not ev['policy']['allow_exception']:
            raise HTTPException(422, {'message': f"The {ev['policy']['profile'].lower()} profile allows no approval without evidence.",
                                      'unmet': [{'detail': f"{c['code']}: {c['found']} (need {c['need']}). {c['fix']}"} for c in unmet]})
        return {**snapshot, 'exception': exception}, exception
    raise HTTPException(422, {'message': 'This version has not shown the evidence needed for approval.',
                              'unmet': [{'detail': f"{c['code']}: {c['found']} (need {c['need']}). {c['fix']}"} for c in unmet]})


def live_evidence_gap(db, version_id) -> str | None:
    """Why this version may not run on LIVE yet (paper record and backtest), or None."""
    ev = evidence(db, version_id)
    if ev['ok_for_live']:
        return None
    if ev['approved_with'] and ev['approved_with'].get('exception') and all(c['ok'] for c in ev['live_checks']):
        return None
    gaps = [f"{c['code'].lower().replace('_', ' ')}: {c['found']} (need {c['need']})" for c in ev['checks'] + ev['live_checks'] if not c['ok']]
    return f"{ev['strategy_code']} v{ev['number']} lacks LIVE evidence — " + '; '.join(gaps)


# ---------- risk minimums ----------

def demo_unmet(db, account_ids: list | None = None) -> list[dict]:
    """DEMO trading accounts missing a limit group of the profile (firm or account scope)."""
    pol = risk_policy(db)
    limits = [dict(r) for r in db.execute("SELECT scope, target_id, kind FROM risk_limits WHERE enabled AND scope IN ('FIRM','ACCOUNT')").fetchall()]
    sql = """SELECT a.id, a.code FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id
             WHERE c.environment='DEMO' AND a.trading_permission='TRADING'"""
    args: list = []
    if account_ids is not None:
        sql += ' AND a.id = ANY(%s)'; args.append([str(a) for a in account_ids])
    out = []
    for a in db.execute(sql + ' ORDER BY a.code', args).fetchall():
        kinds = {lim['kind'] for lim in limits if lim['scope'] == 'FIRM' or str(lim['target_id']) == str(a['id'])}
        missing = [' or '.join(g) for g in pol['demo_needed'] if not kinds.intersection(g)]
        if missing:
            out.append({'code': 'DEMO_LIMITS_MISSING', 'account': a['code'], 'account_id': str(a['id']),
                        'detail': f"{a['code']} has no {', no '.join(missing)} limit (firm or account).", 'goto': {'view': 'Risk', 'section': 'limits'}})
    return out


def require_demo_minimum(db) -> None:
    unmet = demo_unmet(db)
    if unmet:
        raise HTTPException(409, {'message': 'DEMO order sending stays off until every DEMO trading account has the minimum risk limits '
                                             '(Risk › Limits › Apply a profile).', 'unmet': unmet})


def equity_for(db, account_id) -> tuple[Decimal | None, str]:
    """The equity a preset is sized on: the broker's reported equity when fresh, else COREI's broker book."""
    from . import portfolio
    money = portfolio.broker_money(db, account_id)
    if money and money.get('equity') is not None and D(money['equity']) > 0:
        return D(money['equity']), 'broker'
    eq = portfolio.equity(db, account_id, 'BROKER')
    return (eq['equity'], 'COREI book') if eq['equity'] and eq['equity'] > 0 else (None, '')


def preset(profile: str, equity: Decimal | None) -> list[dict]:
    out = []
    for kind, pct in RISK_PROFILES[profile].items():
        if kind in OF_EQUITY:
            if equity is None:
                continue
            value = (equity * D(pct) / 100).quantize(Decimal('0.01'))
        else:
            value = D(pct)
        out.append({'kind': kind, 'value': value, 'basis': f'{pct}% of equity' if kind in OF_EQUITY else 'fixed'})
    return out


class PresetIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    profile: Profile
    account_id: UUID
    equity: Decimal | None = Field(None, gt=0, max_digits=20, decimal_places=2)   # override the measured equity
    reason: str = Field(min_length=5, max_length=500)
    dry_run: bool = False


def apply_preset(actor: str, data: PresetIn) -> dict:
    """Create the profile's account limits, or tighten existing ones. A tighter existing limit is kept."""
    from . import risk
    if data.profile == 'NONE':
        raise HTTPException(422, 'Choose Retail, Professional or Institutional: the None profile has no limits to apply.')
    with store.transaction('read') as db:
        acc = db.execute('SELECT id, code, base_currency FROM cfg_accounts WHERE id=%s', (str(data.account_id),)).fetchone()
        if not acc:
            raise HTTPException(404, 'Account not found.')
        measured, source = equity_for(db, data.account_id)
        equity = data.equity or measured
        existing = {r['kind']: dict(r) for r in db.execute("SELECT * FROM risk_limits WHERE scope='ACCOUNT' AND target_id=%s",
                                                             (str(data.account_id),)).fetchall()}
    if equity is None:
        raise HTTPException(422, f"No equity is known for {acc['code']}: enter the account equity to size the limits.")
    plan = []
    for p in preset(data.profile, equity):
        cur = existing.get(p['kind'])
        action = 'CREATE' if not cur else 'KEEP_TIGHTER' if cur['enabled'] and D(cur['value']) <= p['value'] else 'TIGHTEN'
        plan.append({**p, 'value': str(p['value']), 'current': str(cur['value']) if cur else None, 'action': action})
    out = {'account': acc['code'], 'currency': acc['base_currency'], 'equity': str(equity), 'equity_source': 'entered' if data.equity else source,
           'profile': data.profile, 'limits': plan}
    if data.dry_run:
        return out
    for p in plan:
        if p['action'] == 'KEEP_TIGHTER':
            continue
        cur = existing.get(p['kind'])
        body = risk.LimitIn(scope='ACCOUNT', target_id=data.account_id, kind=p['kind'], value=D(p['value']), enabled=True,
                            note=f"{data.profile.lower()} profile · {p['basis']}"[:500], reason=data.reason)
        risk.save_limit(actor, body, cur['id'] if cur else None, cur['version'] if cur else None)
    return out


# ---------- run losses (strategy and run scope) ----------

def run_day_loss(db, deployment_ids: list, account_id, day_start: datetime | None = None) -> Decimal:
    """Today's loss (positive = loss) of these runs on one account, in the account currency: realised on today's fills
    plus the current unrealised of their open positions."""
    from . import portfolio
    start = day_start or datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    ids = [str(d) for d in deployment_ids]
    if not ids:
        return ZERO
    ccy = db.execute('SELECT base_currency FROM cfg_accounts WHERE id=%s', (str(account_id),)).fetchone()['base_currency'] or 'USD'
    total = ZERO
    for r in db.execute('SELECT currency, sum(realised_pnl) AS r FROM oms_fills WHERE account_id=%s AND deployment_id = ANY(%s) AND at >= %s GROUP BY currency',
                        (str(account_id), ids, start)).fetchall():
        rate = fx_rate(db, r['currency'], ccy)
        if rate is not None:
            total += D(r['r']) * rate
    for p in portfolio.positions(db, account_id):
        if p['deployment_id'] and str(p['deployment_id']) in ids and p['unrealised_account'] is not None:
            total += p['unrealised_account']
    return -total


# ---------- timeframes ----------

class CloneIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version_id: UUID
    timeframe: Literal['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1']
    code: str | None = Field(None, pattern=r'^[A-Z][A-Z0-9_]{1,31}$')


def clone_to_timeframe(actor: str, data: CloneIn) -> dict:
    """A new strategy holding a draft copy of the version on another timeframe. Its evidence starts from nothing: a
    result on one timeframe says nothing about another."""
    with store.transaction('create') as db:
        v = db.execute("""SELECT v.*, s.code, s.name FROM cfg_strategy_versions v JOIN cfg_strategies s ON s.id=v.strategy_id WHERE v.id=%s""",
                       (str(data.version_id),)).fetchone()
        if not v:
            raise HTTPException(404, 'Strategy version not found.')
        params = dict(v['parameters'] or {})
        if params.get('timeframe', 'H1') == data.timeframe:
            raise HTTPException(422, f'This version already runs on {data.timeframe}.')
        base = v['code']
        for tf in ('M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'):
            if base.endswith('_' + tf):
                base = base[: -len(tf) - 1]
        code = data.code or f'{base}_{data.timeframe}'[:32]
        if db.execute('SELECT 1 FROM cfg_strategies WHERE code=%s', (code,)).fetchone():
            raise HTTPException(409, f'A strategy {code} already exists. Choose another code.')
        params['timeframe'] = data.timeframe
        s = db.execute('INSERT INTO cfg_strategies(id, code, name, description, status) VALUES (%s,%s,%s,%s,%s) RETURNING *',
                       (uuid4(), code, f"{v['name']} · {data.timeframe}"[:120], f"Copy of {v['code']} v{v['number']} on {data.timeframe}.", 'ACTIVE')).fetchone()
        store.audit(db, actor, 'CREATE', 'strategy', s['id'], None, dict(s), f"Copied from {v['code']} v{v['number']} to {data.timeframe}")
        nv = db.execute("""INSERT INTO cfg_strategy_versions(id, strategy_id, number, algorithm, parameters, notes, created_by)
                           VALUES (%s,%s,1,%s,%s,%s,%s) RETURNING *""",
                        (uuid4(), s['id'], v['algorithm'], Jsonb(params), f"From {v['code']} v{v['number']} ({params.get('timeframe')}); needs its own evidence.",
                         actor)).fetchone()
        store.audit(db, actor, 'CREATE', 'strategy version', nv['id'], None, dict(nv), 'Timeframe copy: draft, own evidence needed')
        return {'strategy': store.clean(dict(s)), 'version': store.clean(dict(nv))}

