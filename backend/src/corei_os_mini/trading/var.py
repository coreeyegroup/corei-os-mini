"""Value at Risk and stress tests (Stage 5 · Risk intelligence).

Why it exists: limits on size and exposure do not say how much a book can lose on a bad day. Historical VaR and
stress scenarios do, from the firm's own collected prices.
Purpose:
  account_var  historical-simulation VaR and expected shortfall of an account's open positions (optionally with one
               more order): each position's value × each past bar's return, summed per bar, on the bars every held
               instrument shares; the loss at the chosen confidence, and the average loss beyond it. Horizon = one bar
               of the configured timeframe (D1 by default: a 1-day VaR). Used by the MAX_VAR_PCT limit.
  stress       each scenario's price shocks (by symbol, asset class, quote currency or everything; the most specific
               shock applies) on today's positions → P&L per scenario and per position.
  scenarios    the scenario list, editable (built-in ones are kept).
Must never: guess a missing price history (an instrument without enough bars is reported, and VaR is not given), or
use floating point for an amount.
Dependencies: trading.portfolio, trading.common, trading.backtest (bars). Consumers: trading.risk, trading_api.
Architectural position: Stage 5.
"""
from __future__ import annotations

import time as _time
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from .. import config_store as store
from . import portfolio
from .common import D, ZERO, instrument, quote, settings

_cache: dict = {}
SPAN = {'M15': timedelta(minutes=15), 'M30': timedelta(minutes=30), 'H1': timedelta(hours=1), 'H4': timedelta(hours=4), 'D1': timedelta(days=1)}


def _returns(db, account_id, instrument_id, tf: str, n: int) -> tuple[dict, str | None]:
    """{bar_time: return} for the last n bars of the instrument's price source."""
    from .backtest import load_bars
    q = quote(db, account_id, instrument_id)
    if not q:
        return {}, 'no price source'
    end = datetime.now(timezone.utc)
    bars, _ = load_bars(db, q['account_id'], q['broker_symbol'], tf, end - SPAN[tf] * (n * 3 + 10), end)
    bars = bars[-(n + 1):]
    out = {}
    for prev, cur in zip(bars, bars[1:]):
        if D(prev['close']):
            out[cur['bar_time']] = D(cur['close']) / D(prev['close']) - 1
    return out, None if len(out) >= min(30, n) else f'only {len(out)} {tf} bars'


def _book(db, account_id, extra: tuple | None) -> list[dict]:
    """Signed position values (account currency) per instrument, with the extra order added."""
    acc = db.execute('SELECT base_currency FROM cfg_accounts WHERE id=%s', (str(account_id),)).fetchone()
    ccy = acc['base_currency'] if acc else 'USD'
    net: dict = {}
    for p in portfolio.positions(db, account_id):
        net[str(p['instrument_id'])] = net.get(str(p['instrument_id']), ZERO) + D(p['quantity'])
    if extra:
        net[str(extra[0])] = net.get(str(extra[0]), ZERO) + D(extra[1])
    out = []
    for iid, qty in net.items():
        if qty == 0:
            continue
        ins = instrument(db, iid)
        q = quote(db, account_id, iid)
        rate = portfolio.fx_rate(db, ins.get('quote_currency'), ccy)
        if not q or rate is None:
            out.append({'instrument_id': iid, 'symbol': ins.get('symbol'), 'value': None, 'missing': 'price' if not q else 'FX rate'})
            continue
        value = qty * q['mid'] * (D(ins.get('contract_size')) or Decimal(1)) * rate
        out.append({'instrument_id': iid, 'symbol': ins.get('symbol'), 'asset_class': ins.get('asset_class'), 'quote_currency': ins.get('quote_currency'),
                    'base_currency': ins.get('base_currency'), 'quantity': qty, 'value': value})
    return out


def account_var(db, account_id, extra: tuple | None = None, confidence: Decimal | None = None) -> dict:
    cfg = settings(db)
    conf = D(confidence or cfg.get('var_confidence') or 99)
    tf, n = cfg.get('var_timeframe') or 'D1', int(cfg.get('var_lookback_bars') or 250)
    key = (str(account_id), str(extra), str(conf), tf, n, int(_time.time() // 60))
    if key in _cache:
        return _cache[key]
    book = _book(db, account_id, extra)
    if not book:
        return {'var': ZERO, 'es': ZERO, 'confidence': conf, 'timeframe': tf, 'bars': 0, 'positions': []}
    missing = [f"{b['symbol']}: no {b['missing']}" for b in book if b['value'] is None]
    series = {}
    for b in book:
        if b['value'] is None:
            continue
        r, problem = _returns(db, account_id, b['instrument_id'], tf, n)
        if problem:
            missing.append(f"{b['symbol']}: {problem}")
        series[b['instrument_id']] = r
    if missing:
        out = {'error': '; '.join(missing), 'confidence': conf, 'timeframe': tf}
        _cache[key] = out
        return out
    times = sorted(set.intersection(*[set(s) for s in series.values()])) if series else []
    if len(times) < 30:
        return {'error': f'only {len(times)} bars shared by every held instrument (30 needed)', 'confidence': conf, 'timeframe': tf}
    pnl = [sum((b['value'] * series[b['instrument_id']][t] for b in book), ZERO) for t in times]
    ordered = sorted(pnl)
    tail_n = max(1, int((Decimal(100) - conf) / 100 * len(ordered) + Decimal('0.999999')))
    var_ = -ordered[tail_n - 1]
    es = -sum(ordered[:tail_n], ZERO) / tail_n
    contrib = []
    for b in book:  # each position's average P&L in the tail bars (component VaR, sums to the ES)
        tail_times = [t for t, v in sorted(zip(times, pnl), key=lambda x: x[1])[:tail_n]]
        contrib.append({'symbol': b['symbol'], 'instrument_id': b['instrument_id'], 'value': b['value'].quantize(Decimal('0.01')),
                        'tail_loss': (-sum((b['value'] * series[b['instrument_id']][t] for t in tail_times), ZERO) / tail_n).quantize(Decimal('0.01'))})
    out = {'var': max(var_, ZERO).quantize(Decimal('0.01'), ROUND_CEILING), 'es': max(es, ZERO).quantize(Decimal('0.01'), ROUND_CEILING),
           'confidence': conf, 'timeframe': tf, 'bars': len(times), 'from': times[0].isoformat(), 'to': times[-1].isoformat(),
           'worst': (-ordered[0]).quantize(Decimal('0.01')), 'positions': contrib}
    _cache[key] = out
    if len(_cache) > 500:
        _cache.clear()
    return out


# ---------- stress scenarios ----------

class ShockIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    match: str = Field(pattern=r'^(ALL|ASSET_CLASS|SYMBOL|CURRENCY)$')
    value: str = Field(default='', max_length=40)
    pct: Decimal = Field(ge=-100, le=1000, max_digits=12, decimal_places=4)


class ScenarioIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(default='', max_length=500)
    shocks: list[ShockIn] = Field(min_length=1, max_length=50)
    reason: str = Field(min_length=5, max_length=500)


def scenarios(db) -> list[dict]:
    return [store.clean(r) for r in db.execute('SELECT * FROM risk_stress_scenarios ORDER BY builtin DESC, name').fetchall()]


def save_scenario(actor: str, data: ScenarioIn, scenario_id: UUID | None = None) -> dict:
    shocks = Jsonb([{'match': s.match, 'value': s.value.upper(), 'pct': str(s.pct)} for s in data.shocks])
    with store.transaction('write') as db:
        if scenario_id is None:
            row = db.execute('INSERT INTO risk_stress_scenarios(id, name, description, shocks, created_by, updated_by) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *',
                             (uuid4(), data.name, data.description, shocks, actor, actor)).fetchone()
            store.audit(db, actor, 'CREATE_STRESS_SCENARIO', 'stress_scenario', row['id'], None, dict(row), data.reason)
        else:
            before = db.execute('SELECT * FROM risk_stress_scenarios WHERE id=%s FOR UPDATE', (str(scenario_id),)).fetchone()
            if not before:
                raise HTTPException(404, 'Scenario not found.')
            row = db.execute('UPDATE risk_stress_scenarios SET name=%s, description=%s, shocks=%s, version=version+1, updated_by=%s, updated_at=now() '
                             'WHERE id=%s RETURNING *', (data.name, data.description, shocks, actor, str(scenario_id))).fetchone()
            store.audit(db, actor, 'UPDATE_STRESS_SCENARIO', 'stress_scenario', scenario_id, dict(before), dict(row), data.reason)
        return store.clean(row)


def delete_scenario(actor: str, scenario_id: UUID, reason: str) -> None:
    with store.transaction('delete') as db:
        before = db.execute('SELECT * FROM risk_stress_scenarios WHERE id=%s', (str(scenario_id),)).fetchone()
        if not before:
            raise HTTPException(404, 'Scenario not found.')
        if before['builtin']:
            raise HTTPException(409, 'Built-in scenarios are kept; add your own instead.')
        db.execute('DELETE FROM risk_stress_scenarios WHERE id=%s', (str(scenario_id),))
        store.audit(db, actor, 'DELETE_STRESS_SCENARIO', 'stress_scenario', scenario_id, dict(before), None, reason)


def _shock(position: dict, shocks: list[dict]) -> Decimal | None:
    order = {'SYMBOL': 0, 'ASSET_CLASS': 1, 'CURRENCY': 2, 'ALL': 3}
    for s in sorted(shocks, key=lambda s: order.get(s['match'], 9)):
        v = str(s.get('value') or '').upper()
        if (s['match'] == 'ALL' or (s['match'] == 'SYMBOL' and v == str(position.get('symbol') or '').upper())
                or (s['match'] == 'ASSET_CLASS' and v == str(position.get('asset_class') or '').upper())
                or (s['match'] == 'CURRENCY' and v == str(position.get('quote_currency') or '').upper())):
            return D(s['pct'])
    return None


def stress(db, account_id=None) -> dict:
    """Every scenario on today's positions (every account with positions, or one)."""
    ids = [account_id] if account_id else [r['account_id'] for r in db.execute('SELECT DISTINCT account_id FROM pf_positions WHERE quantity <> 0').fetchall()]
    books = {str(a): _book(db, a, None) for a in ids}
    codes = {str(r['id']): r['code'] for r in db.execute('SELECT id, code FROM cfg_accounts').fetchall()}
    out = []
    for sc in scenarios(db):
        rows, total = [], {}
        for aid, book in books.items():
            for b in book:
                if b['value'] is None:
                    continue
                pct = _shock(b, sc['shocks'])
                pnl = (b['value'] * pct / 100).quantize(Decimal('0.01')) if pct is not None else ZERO
                total[aid] = total.get(aid, ZERO) + pnl
                rows.append({'account': codes.get(aid), 'symbol': b['symbol'], 'value': b['value'].quantize(Decimal('0.01')),
                             'shock_pct': pct, 'pnl': pnl})
        out.append({'id': sc['id'], 'name': sc['name'], 'description': sc['description'], 'builtin': sc['builtin'], 'shocks': sc['shocks'],
                    'total': sum(total.values(), ZERO), 'by_account': [{'account': codes.get(a), 'account_id': a, 'pnl': v} for a, v in total.items()],
                    'positions': rows})
    return {'scenarios': out, 'missing': [f"{codes.get(a)} {b['symbol']}: no {b['missing']}" for a, bk in books.items() for b in bk if b['value'] is None]}
