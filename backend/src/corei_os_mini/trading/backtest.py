"""Backtest (Stage 4 · Strategy intelligence): a strategy version's rules on stored history, point-in-time.

Why it exists: before a version is approved or deployed, the desk needs to see how its exact rules behaved on the
data COREI collected — with costs — using the same code the live runtime uses.
Purpose: load closed bars of the source (the broker's own timeframe, else built from M1) for a period, compute the
same features as the feature store, run the algorithm, and trade its target changes: an order on a bar's close is
filled at the NEXT bar's open (no look-ahead); buys pay the spread (bars are bid prices) and every fill a commission per lot. Returns
trades, an equity curve and metrics (net P&L, trades, win rate, profit factor, average trade, largest drawdown,
Sharpe and exposure), and stores the run.
Responsibilities: tr_backtests.
Must never: read the clock for a decision, use a bar after the decision bar for the decision, or touch orders,
positions or risk (a backtest is a separate, simulated book).
Dependencies: intel.engine, trading.algorithms, trading.common. Consumers: trading_api.
Architectural position: Stage 4, beside the runtime.
Conventions: prices are converted to exact decimals with the instrument's precision; P&L is in the instrument's
quote currency; the Sharpe ratio is descriptive (per-bar returns, annualised by the timeframe's bars per year).
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .. import config_store as store
from ..intel import engine
from . import algorithms
from .common import D, ZERO, instrument, quantize


class BacktestIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    strategy_version_id: UUID | None = None   # or algorithm + parameters for a draft idea
    algorithm: str | None = None
    parameters: dict | None = None
    account_id: UUID                           # the collected source: account + broker symbol
    broker_symbol: str = Field(min_length=1, max_length=64)
    range_from: datetime
    range_to: datetime
    quantity: Decimal = Field(Decimal(1), gt=0, max_digits=38, decimal_places=12)
    spread_ticks: int = Field(0, ge=0, le=100000, description='Spread in ticks paid on each buy (bars are bid prices); 0 = no spread cost')
    commission_per_lot: Decimal = Field(Decimal(0), ge=0, max_digits=20, decimal_places=6)
    starting_capital: Decimal = Field(Decimal(100000), gt=0, max_digits=20, decimal_places=2)
    direction: Literal['AS_STRATEGY', 'BOTH', 'LONG_ONLY', 'SHORT_ONLY'] = 'AS_STRATEGY'

    @model_validator(mode='after')
    def _what(self) -> 'BacktestIn':
        if not self.strategy_version_id and not (self.algorithm and self.parameters is not None):
            raise ValueError('give a strategy version, or an algorithm and its parameters')
        if self.range_to <= self.range_from:
            raise ValueError('the period must end after it starts')
        return self


MAX_BARS = 200_000


def load_bars(db, account_id, symbol: str, tf: str, start: datetime, end: datetime) -> tuple[list[dict], str]:
    rows = db.execute('SELECT bar_time, open, high, low, close, ticks, volume, spread_avg FROM md_bars WHERE account_id=%s AND broker_symbol=%s '
                      'AND timeframe=%s AND complete AND bar_time BETWEEN %s AND %s ORDER BY bar_time LIMIT %s',
                      (str(account_id), symbol, tf, start, end, MAX_BARS)).fetchall()
    native = [dict(r) for r in rows]
    if tf == 'M1':
        return native, 'BROKER'
    m1 = [dict(r) for r in db.execute('SELECT bar_time, open, high, low, close, ticks, volume, spread_avg FROM md_bars WHERE account_id=%s AND '
                                      "broker_symbol=%s AND timeframe='M1' AND complete AND bar_time BETWEEN %s AND %s ORDER BY bar_time LIMIT %s",
                                      (str(account_id), symbol, start, end, MAX_BARS)).fetchall()]
    market = engine.market_of(db, account_id, symbol)
    bars, used = engine.choose(m1, native, tf, market, window=None)   # the same bar-source policy as the runtime
    return bars, {'BROKER': 'BROKER', 'M1': 'BUILT_FROM_M1', 'MIXED': 'BROKER_THEN_M1'}[used]


def simulate(bars: list[dict], walk: list[dict], ins: dict, qty: Decimal, spread_ticks: int, commission: Decimal, capital: Decimal,
             tf: str) -> dict:
    """Trade the target changes: decided on bar i's close, filled at bar i+1's open."""
    tick = D(ins.get('tick_size')) or Decimal('0.00001')
    contract = D(ins.get('contract_size')) or Decimal(1)
    pos, entry, entry_time = ZERO, None, None
    cash = ZERO
    trades, equity = [], []
    peak, max_dd = capital, ZERO
    rets, prev_eq = [], capital
    bars_in_market = 0

    def px(i: int, side: int) -> Decimal:
        # Bars are bid prices (MT5 convention): a buy pays the spread (ask = bid + spread), a sell gets the bid.
        o = quantize(D(bars[i]['open']), tick)
        return o + tick * spread_ticks if side > 0 else o

    for i in range(len(bars) - 1):
        want = {'LONG': qty, 'SHORT': -qty, 'FLAT': ZERO}[walk[i]['target']]
        if want != pos:
            j = i + 1
            if pos != 0:  # close
                exit_px = px(j, -1 if pos > 0 else 1)
                pnl = (exit_px - entry) * pos * contract - commission * abs(pos)
                cash += pnl
                trades.append({'side': 'LONG' if pos > 0 else 'SHORT', 'entry_time': entry_time.isoformat(), 'entry': str(entry),
                               'exit_time': bars[j]['bar_time'].isoformat(), 'exit': str(exit_px), 'quantity': str(abs(pos)),
                               'pnl': str(pnl.quantize(Decimal('0.01'))), 'reason': walk[i]['reason']})
            pos = want
            if pos != 0:
                entry, entry_time = px(j, 1 if pos > 0 else -1), bars[j]['bar_time']
                cash -= commission * abs(pos)
        mark = quantize(D(bars[i]['close']), tick)
        eq = capital + cash + ((mark - entry) * pos * contract if pos != 0 else ZERO)
        if pos != 0:
            bars_in_market += 1
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak * 100 if peak > 0 else ZERO)
        if prev_eq:
            rets.append(float(eq / prev_eq - 1))
        prev_eq = eq
        equity.append((bars[i]['bar_time'], eq))
    wins = [D(t['pnl']) for t in trades if D(t['pnl']) > 0]
    losses = [D(t['pnl']) for t in trades if D(t['pnl']) < 0]
    net = sum((D(t['pnl']) for t in trades), ZERO)
    mean = sum(rets) / len(rets) if rets else 0.0
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) if len(rets) > 1 else 0.0
    per_year = engine.BARS_PER_YEAR.get(tf, 252)
    step = max(1, len(equity) // 400)
    return {'metrics': {'bars': len(bars), 'trades': len(trades), 'net_pnl': str(net.quantize(Decimal('0.01'))),
                        'return_pct': str((net / capital * 100).quantize(Decimal('0.01'))),
                        'win_rate_pct': round(100 * len(wins) / len(trades), 1) if trades else None,
                        'profit_factor': round(float(sum(wins) / -sum(losses)), 2) if losses else None,
                        'avg_trade': str((net / len(trades)).quantize(Decimal('0.01'))) if trades else None,
                        'max_drawdown_pct': str(max_dd.quantize(Decimal('0.01'))),
                        'sharpe': round(mean / sd * math.sqrt(per_year), 2) if sd else None,
                        'exposure_pct': round(100 * bars_in_market / max(1, len(bars) - 1), 1),
                        'open_position_at_end': str(pos), 'currency': ins.get('quote_currency') or ''},
            'trades': trades[-1000:], 'equity': [{'t': t.isoformat(), 'equity': str(e.quantize(Decimal('0.01')))} for t, e in equity[::step]]}


def run(actor: str, data: BacktestIn) -> dict:
    with store.transaction('read') as db:
        algo, params = data.algorithm, data.parameters
        if data.strategy_version_id:
            v = db.execute('SELECT algorithm, parameters FROM cfg_strategy_versions WHERE id=%s', (data.strategy_version_id,)).fetchone()
            if not v:
                raise HTTPException(404, 'Strategy version not found.')
            algo, params = v['algorithm'], dict(v['parameters'])
        if algo not in algorithms.ALGORITHMS:
            raise HTTPException(422, f'Unknown algorithm {algo}.')
        if algo == 'GRAPH' and not params.get('definition'):
            params = algorithms.resolve_graph(db, params)
        try:
            params = algorithms.ALGORITHMS[algo]['model'].model_validate(params).model_dump(mode='json')
        except Exception as exc:  # noqa: BLE001 - pydantic
            raise HTTPException(422, f'Parameters: {exc}') from None
        if data.direction != 'AS_STRATEGY':
            params['direction'] = data.direction
        tf = params['timeframe']
        plan = db.execute('SELECT instrument_id FROM md_collection_plans WHERE account_id=%s AND broker_symbol=%s ORDER BY created_at LIMIT 1',
                          (str(data.account_id), data.broker_symbol)).fetchone()
        ins = instrument(db, plan['instrument_id']) if plan and plan['instrument_id'] else {}
        bars, source = load_bars(db, data.account_id, data.broker_symbol, tf, data.range_from, data.range_to)
        market = engine.market_of(db, data.account_id, data.broker_symbol)
    need = algorithms.warmup(algo, params) + 3
    if len(bars) < need:
        raise HTTPException(422, f'Only {len(bars)} closed {tf} bars of {data.broker_symbol} in that period; at least {need} are needed. '
                                 'Collect or backfill more history (Market data › Backfill, or Market intelligence › Get history).')
    custom = [d for d in engine.custom_definitions() if tf in (d.get('timeframes') or engine.TIMEFRAMES)]
    rows = engine.features(bars, tf, custom, market)
    walk = algorithms.run(algo, params, bars, rows)
    result = simulate(bars, walk, ins, data.quantity, data.spread_ticks, data.commission_per_lot, data.starting_capital, tf)
    result['metrics']['bars_source'] = source
    if not ins:
        result['metrics']['note'] = 'No COREI instrument is linked to this symbol: contract size 1 and tick 0.00001 were assumed.'
    run_id = uuid4()
    settings = data.model_dump(mode='json', exclude={'strategy_version_id', 'algorithm', 'parameters', 'account_id', 'broker_symbol', 'range_from', 'range_to'})
    with store.transaction('write') as db:
        db.execute('INSERT INTO tr_backtests(id, strategy_version_id, algorithm, parameters, account_id, broker_symbol, instrument_id, timeframe, range_from, range_to, '
                   'settings, metrics, trades, equity, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                   (run_id, str(data.strategy_version_id) if data.strategy_version_id else None, algo, json.dumps(params), str(data.account_id), data.broker_symbol,
                    ins.get('id'), tf, data.range_from, data.range_to, json.dumps(settings), json.dumps(result['metrics']), json.dumps(result['trades']),
                    json.dumps(result['equity']), actor))
    return {'id': str(run_id), 'algorithm': algo, 'parameters': params, 'timeframe': tf, **result}


def listing(db, limit: int = 100) -> list[dict]:
    rows = db.execute("""SELECT b.id, b.algorithm, b.parameters, b.broker_symbol, b.timeframe, b.range_from, b.range_to, b.metrics, b.created_by, b.created_at,
                                s.code AS strategy_code, v.number AS version_number, a.code AS account_code
                         FROM tr_backtests b LEFT JOIN cfg_strategy_versions v ON v.id=b.strategy_version_id LEFT JOIN cfg_strategies s ON s.id=v.strategy_id
                         JOIN cfg_accounts a ON a.id=b.account_id ORDER BY b.created_at DESC LIMIT %s""", (limit,)).fetchall()
    return [store.clean(r) for r in rows]


def detail(db, run_id: UUID) -> dict:
    r = db.execute('SELECT * FROM tr_backtests WHERE id=%s', (run_id,)).fetchone()
    if not r:
        raise HTTPException(404, 'Backtest not found.')
    return store.clean(r)
