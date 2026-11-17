"""Quick start (Stages 3–8): a working paper strategy on a collected instrument in one step.

Why it exists: Signals, Strategies, Risk, Portfolio, Orders and Post-trade stay empty until a strategy is deployed,
and deploying one by hand takes five screens (strategy, version, approval, symbol mapping, deployment, activation).
A new user should see the whole chain working in a minute, then change it.
Purpose: list what can be traded on paper now (collected sources linked to an instrument, with their bars) and the
starter presets; create, in one transaction and through the same rules as Setup, the strategy, its version (approved
when the person is a Risk approver), the account's symbol mapping when missing, and a PAPER deployment (activated
when the person may activate) — each step audited.
Must never: create a BROKER deployment (real orders are always a person's deliberate step in Setup), skip the
approval or activation roles, or change an existing strategy or deployment.
Dependencies: strategy_registry (the Setup rules), config_store. Consumers: trading_api (the Start here panels).
Architectural position: a shortcut through Setup into Stage 4.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from .. import config_store as store

PRESETS: dict[str, dict] = {
    'EMA_TREND': {'label': 'Trend following · EMA 9/21 cross', 'algorithm': 'EMA_CROSSOVER', 'parameters': {'fast': 9, 'slow': 21},
                  'why': 'Long when the fast EMA crosses above the slow one, short below. Trades trends; loses in ranges.'},
    'EMA_FAST': {'label': 'Fast trend · EMA 3/8 cross', 'algorithm': 'EMA_CROSSOVER', 'parameters': {'fast': 3, 'slow': 8},
                 'why': 'The same rule, quicker: many signals, good for seeing the whole chain work.'},
    'RSI_REVERSION': {'label': 'Mean reversion · RSI 14 (30/70)', 'algorithm': 'RSI_REVERSION', 'parameters': {'period': 14, 'lower': 30, 'upper': 70, 'exit_level': 50},
                      'why': 'Buys oversold, sells overbought, exits at 50. Trades ranges; loses in strong trends.'},
    'BREAKOUT': {'label': 'Breakout · Donchian 20/10', 'algorithm': 'DONCHIAN_BREAKOUT', 'parameters': {'entry': 20, 'exit': 10},
                 'why': 'Enters on a 20-bar high or low, exits on a 10-bar opposite. Classic trend breakout.'},
    'BOLLINGER': {'label': 'Band reversion · Bollinger 20/2', 'algorithm': 'BOLLINGER_REVERSION', 'parameters': {'period': 20, 'deviations': 2.0},
                  'why': 'Fades moves outside the bands back to the middle.'},
}


class QuickIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_id: UUID                       # the collected source: account + broker symbol (the paper book is on this account)
    broker_symbol: str = Field(min_length=1, max_length=64)
    preset: Literal['EMA_TREND', 'EMA_FAST', 'RSI_REVERSION', 'BREAKOUT', 'BOLLINGER'] = 'EMA_TREND'
    timeframe: Literal['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'] = 'M5'
    quantity: str | None = Field(default=None, pattern=r'^\d+(\.\d{1,12})?$')   # blank: the instrument's smallest size
    activate: bool = True


def options(db) -> dict:
    rows = db.execute("""SELECT p.account_id, a.code AS account_code, c.environment, p.broker_symbol, p.instrument_id, i.symbol AS instrument,
                                i.min_quantity, p.status AS plan_status,
                                (SELECT count(*) FROM md_bars b WHERE b.account_id=p.account_id AND b.broker_symbol=p.broker_symbol AND b.timeframe='M1') AS m1_bars,
                                (SELECT max(bar_time) FROM md_bars b WHERE b.account_id=p.account_id AND b.broker_symbol=p.broker_symbol) AS last_bar
                         FROM md_collection_plans p JOIN cfg_accounts a ON a.id=p.account_id JOIN cfg_connections c ON c.id=a.connection_id
                         LEFT JOIN cfg_instruments i ON i.id=p.instrument_id
                         WHERE p.status <> 'ARCHIVED' ORDER BY (p.status='ACTIVE') DESC, a.code, p.broker_symbol""").fetchall()
    counts = db.execute("""SELECT (SELECT count(*) FROM cfg_strategies) AS strategies, (SELECT count(*) FROM cfg_deployments) AS deployments,
                                  (SELECT count(*) FROM cfg_deployments WHERE status='ACTIVE') AS active""").fetchone()
    return {'sources': [{**store.clean(r), 'ready': bool(r['instrument_id']) and r['plan_status'] == 'ACTIVE',
                         'why_not': None if r['instrument_id'] and r['plan_status'] == 'ACTIVE' else
                         ('link the plan to an instrument (Market data › Plans)' if not r['instrument_id'] else f"the plan is {r['plan_status'].lower()}")}
                        for r in rows],
            'presets': [{'id': k, **{x: v[x] for x in ('label', 'algorithm', 'parameters', 'why')}} for k, v in PRESETS.items()], **dict(counts)}


def _code(db, base: str) -> str:
    base = re.sub(r'[^A-Z0-9_]', '_', base.upper())[:40].strip('_') or 'STRATEGY'
    code, n = base, 2
    while db.execute('SELECT 1 FROM cfg_strategies WHERE code=%s', (code,)).fetchone():
        code, n = f'{base}_{n}', n + 1
    return code


def start(user, data: QuickIn) -> dict:
    """Strategy → version (approved when allowed) → mapping (when missing) → PAPER deployment (activated when allowed)."""
    from .. import strategy_registry as reg
    preset = PRESETS[data.preset]
    with store.transaction('read') as db:
        plan = db.execute("""SELECT p.instrument_id, p.status, a.connection_id, i.symbol, i.min_quantity FROM md_collection_plans p
                             JOIN cfg_accounts a ON a.id=p.account_id LEFT JOIN cfg_instruments i ON i.id=p.instrument_id
                             WHERE p.account_id=%s AND p.broker_symbol=%s AND p.status <> 'ARCHIVED' ORDER BY (p.status='ACTIVE') DESC LIMIT 1""",
                          (str(data.account_id), data.broker_symbol)).fetchone()
        if not plan:
            raise HTTPException(404, f'{data.broker_symbol} is not collected on this account (Market data › Plans).')
        if not plan['instrument_id']:
            raise HTTPException(422, f'Link the {data.broker_symbol} plan to an instrument first (Market data › Plans › edit).')
        code = _code(db, f"{preset['algorithm'].split('_')[0]}_{plan['symbol']}_{data.timeframe}")
    steps = []
    strat = reg.create_strategy({'code': code, 'name': f"{preset['label']} on {plan['symbol']} {data.timeframe}",
                                 'description': f"Made by Quick start: {preset['why']}"}, Response(), user)
    steps.append(f"strategy {code}")
    ver = reg.create_version(strat['id'], {'algorithm': preset['algorithm'], 'parameters': {**preset['parameters'], 'timeframe': data.timeframe},
                                           'notes': 'Quick start preset'}, Response(), user)
    steps.append(f"version {ver['number']} ({preset['algorithm']})")
    approved = user.has('RISK_APPROVER')
    if approved:
        ver = reg.approve_version(ver['id'], {'version': ver['version']}, Response(), user)
        steps.append('approved')
    with store.transaction('write') as db:
        m = db.execute("""SELECT id FROM cfg_symbol_mappings WHERE connection_id=%s AND instrument_id=%s AND status='ACTIVE'
                          AND usage IN ('MARKET_DATA','BOTH') AND (account_id IS NULL OR account_id=%s)
                          ORDER BY (broker_symbol=%s) DESC LIMIT 1""", (plan['connection_id'], plan['instrument_id'], str(data.account_id), data.broker_symbol)).fetchone()
        if not m:
            mid = uuid4()
            db.execute("INSERT INTO cfg_symbol_mappings(id, connection_id, instrument_id, broker_symbol, usage) VALUES (%s,%s,%s,%s,'BOTH')",
                       (mid, plan['connection_id'], plan['instrument_id'], data.broker_symbol))
            store.audit(db, user.username, 'CREATE', 'mapping', mid, None, {'broker_symbol': data.broker_symbol, 'instrument': plan['symbol'], 'usage': 'BOTH'},
                        'Quick start: the account\'s symbol for the instrument')
            steps.append(f'symbol mapping {data.broker_symbol} → {plan["symbol"]}')
        else:
            mid = m['id']
    qty = data.quantity or str(plan['min_quantity'] or '1')
    dep = reg.create_deployment({'strategy_id': strat['id'], 'strategy_version_id': ver['id'], 'instrument_id': str(plan['instrument_id']),
                                 'account_id': str(data.account_id), 'data_mapping_id': str(mid), 'mode': 'PAPER',
                                 'sizing': {'method': 'FIXED_QTY', 'quantity': format(Decimal(qty).normalize(), 'f')},
                                 'note': 'Quick start (paper)'}, Response(), user)
    steps.append('paper deployment')
    activated = False
    if data.activate and approved:
        try:
            dep = reg.transition(dep['id'], 'activate', {'version': dep['version'], 'reason': 'Quick start: paper trading'}, Response(), user)
            activated = True
            steps.append('activated')
        except HTTPException as exc:
            steps.append(f'not activated: {exc.detail}')
    return {'strategy': strat, 'version': ver, 'deployment': dep, 'steps': steps, 'activated': activated,
            'next': ('Running on paper: the first closed bar is recorded, signals start from the next one. Watch Signals, Orders and Portfolio.'
                     if activated else 'Created. A Risk approver approves the version and activates the deployment (Setup › Strategies and Deployments).')}
