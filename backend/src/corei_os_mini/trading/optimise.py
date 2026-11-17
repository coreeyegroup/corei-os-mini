"""Parameter optimisation and walk-forward (Stage 4 · Strategy intelligence).

Why it exists: choosing parameters by eye over-fits; a desk needs to see every combination, and whether the chosen
one holds up on data it was not chosen on.
Purpose:
  grid search   every combination of the given parameter values on one period (capped at 400), each with the
                backtest's own metrics, ranked by the objective (net P&L, Sharpe, profit factor, return ÷ drawdown);
  walk-forward  the period split into N folds; in each, the best combination on the first part (in-sample) is
                traded on the rest (out-of-sample); out-of-sample results are joined, with the efficiency (OOS ÷ IS)
                and how stable the chosen parameters were across folds.
Same code as the backtest: features computed once on the bars, the algorithm's targets per combination, fills at the
next bar's open with spread and commission (trading.backtest.simulate).
Must never: change a strategy version (results are advice; a person makes a new version), or look at the
out-of-sample part when choosing.
Dependencies: trading.backtest, trading.algorithms, intel.engine. Consumers: trading_api.
Architectural position: Stage 4.
"""
from __future__ import annotations

import itertools
import json
from collections import Counter
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .. import config_store as store
from ..intel import engine
from . import algorithms, backtest
from .common import D, instrument

MAX_COMBINATIONS = 400


class OptimiseIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    algorithm: str
    base_parameters: dict
    grid: dict[str, list] = Field(min_length=1)
    objective: Literal['NET_PNL', 'SHARPE', 'PROFIT_FACTOR', 'RETURN_OVER_DD'] = 'SHARPE'
    account_id: UUID
    broker_symbol: str = Field(min_length=1, max_length=64)
    range_from: datetime
    range_to: datetime
    quantity: Decimal = Field(Decimal(1), gt=0, max_digits=38, decimal_places=12)
    spread_ticks: int = Field(0, ge=0, le=100000)
    commission_per_lot: Decimal = Field(Decimal(0), ge=0, max_digits=20, decimal_places=6)
    starting_capital: Decimal = Field(Decimal(100000), gt=0, max_digits=20, decimal_places=2)
    walk_forward_folds: int = Field(0, ge=0, le=12)
    in_sample_pct: int = Field(70, ge=40, le=90)
    min_trades: int = Field(5, ge=0, le=10000)

    @model_validator(mode='after')
    def _check(self) -> 'OptimiseIn':
        n = 1
        for k, values in self.grid.items():
            if not values:
                raise ValueError(f'parameter {k} has no values')
            n *= len(values)
        if n > MAX_COMBINATIONS:
            raise ValueError(f'{n} combinations; at most {MAX_COMBINATIONS}')
        if self.range_to <= self.range_from:
            raise ValueError('the period must end after it starts')
        return self


def score(m: dict, objective: str, min_trades: int) -> float | None:
    if (m.get('trades') or 0) < min_trades:
        return None
    if objective == 'NET_PNL':
        return float(D(m['net_pnl']))
    if objective == 'SHARPE':
        return m.get('sharpe')
    if objective == 'PROFIT_FACTOR':
        return m.get('profit_factor')
    dd = float(D(m['max_drawdown_pct']))
    return float(D(m['return_pct'])) / dd if dd > 0 else float(D(m['return_pct']))


def _combos(base: dict, grid: dict, model) -> list[dict]:
    keys = list(grid)
    out = []
    for values in itertools.product(*[grid[k] for k in keys]):
        p = {**base, **dict(zip(keys, values))}
        try:
            out.append(model.model_validate(p).model_dump(mode='json'))
        except Exception:  # noqa: BLE001 - an invalid combination (fast ≥ slow) is skipped, not an error
            continue
    return out


def run(actor: str, data: OptimiseIn) -> dict:
    if data.algorithm not in algorithms.ALGORITHMS:
        raise HTTPException(422, f'Unknown algorithm {data.algorithm}.')
    model = algorithms.ALGORITHMS[data.algorithm]['model']
    combos = _combos(data.base_parameters, data.grid, model)
    if not combos:
        raise HTTPException(422, 'No valid combination: check the values (for example the fast window must be shorter than the slow one).')
    tf = combos[0]['timeframe']
    if any(c['timeframe'] != tf for c in combos):
        raise HTTPException(422, 'Optimise one timeframe at a time.')
    with store.transaction('read') as db:
        plan = db.execute('SELECT instrument_id FROM md_collection_plans WHERE account_id=%s AND broker_symbol=%s ORDER BY created_at LIMIT 1',
                          (str(data.account_id), data.broker_symbol)).fetchone()
        ins = instrument(db, plan['instrument_id']) if plan and plan['instrument_id'] else {}
        bars, source = backtest.load_bars(db, data.account_id, data.broker_symbol, tf, data.range_from, data.range_to)
    need = max(algorithms.warmup(data.algorithm, c) for c in combos) + 3
    if len(bars) < need * (2 if data.walk_forward_folds else 1):
        raise HTTPException(422, f'Only {len(bars)} closed {tf} bars; at least {need * (2 if data.walk_forward_folds else 1)} are needed.')
    custom = [d for d in engine.custom_definitions() if tf in (d.get('timeframes') or engine.TIMEFRAMES)]
    rows = engine.features(bars, tf, custom)
    walks = {json.dumps(c, sort_keys=True): algorithms.run(data.algorithm, c, bars, rows) for c in combos}

    def sim(c: dict, lo: int, hi: int) -> dict:
        w = walks[json.dumps(c, sort_keys=True)]
        return backtest.simulate(bars[lo:hi], w[lo:hi], ins, data.quantity, data.spread_ticks, data.commission_per_lot, data.starting_capital, tf)['metrics']

    results = []
    for c in combos:
        m = sim(c, 0, len(bars))
        results.append({'parameters': {k: c[k] for k in data.grid}, 'metrics': m, 'score': score(m, data.objective, data.min_trades)})
    results.sort(key=lambda r: (r['score'] is not None, r['score'] or 0), reverse=True)
    best = results[0] if results and results[0]['score'] is not None else None
    folds, robustness = [], None
    if data.walk_forward_folds:
        size = len(bars) // data.walk_forward_folds
        chosen = []
        for f in range(data.walk_forward_folds):
            lo, hi = f * size, (f + 1) * size if f < data.walk_forward_folds - 1 else len(bars)
            cut = lo + (hi - lo) * data.in_sample_pct // 100
            scored = [(score(sim(c, lo, cut), data.objective, max(1, data.min_trades // data.walk_forward_folds)), c) for c in combos]
            scored = [s for s in scored if s[0] is not None]
            if not scored:
                folds.append({'fold': f + 1, 'from': bars[lo]['bar_time'].isoformat(), 'to': bars[hi - 1]['bar_time'].isoformat(), 'note': 'no combination traded enough'})
                continue
            is_score, pick = max(scored, key=lambda s: s[0])
            oos = sim(pick, cut, hi)
            oos_score = score(oos, data.objective, 0)
            chosen.append(json.dumps({k: pick[k] for k in data.grid}, sort_keys=True))
            folds.append({'fold': f + 1, 'in_sample': [bars[lo]['bar_time'].isoformat(), bars[cut - 1]['bar_time'].isoformat()],
                          'out_of_sample': [bars[cut]['bar_time'].isoformat(), bars[hi - 1]['bar_time'].isoformat()],
                          'parameters': {k: pick[k] for k in data.grid}, 'in_sample_score': is_score, 'out_of_sample_score': oos_score,
                          'out_of_sample': oos})
        done = [f for f in folds if 'out_of_sample_score' in f]
        if done:
            is_avg = sum(f['in_sample_score'] for f in done) / len(done)
            oos_avg = sum((f['out_of_sample_score'] or 0) for f in done) / len(done)
            top, times = Counter(chosen).most_common(1)[0]
            robustness = {'folds': len(done), 'in_sample_avg': round(is_avg, 4), 'out_of_sample_avg': round(oos_avg, 4),
                          'efficiency_pct': round(100 * oos_avg / is_avg, 1) if is_avg else None,
                          'oos_net_pnl': str(sum((D(f['out_of_sample']['net_pnl']) for f in done), Decimal(0))),
                          'oos_profitable_folds': sum(1 for f in done if D(f['out_of_sample']['net_pnl']) > 0),
                          'most_chosen': json.loads(top), 'stability_pct': round(100 * times / len(done), 1),
                          'verdict': 'ROBUST' if is_avg and oos_avg > 0 and oos_avg / is_avg >= 0.5 else 'FRAGILE' if oos_avg <= 0 else 'WEAK'}
    rid = uuid4()
    with store.transaction('write') as db:
        db.execute('INSERT INTO tr_optimisations(id, algorithm, base_parameters, grid, objective, account_id, broker_symbol, timeframe, range_from, range_to, '
                   'walk_forward, results, folds, best, robustness, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                   (rid, data.algorithm, json.dumps(data.base_parameters), json.dumps(data.grid), data.objective, str(data.account_id), data.broker_symbol, tf,
                    data.range_from, data.range_to, json.dumps({'folds': data.walk_forward_folds, 'in_sample_pct': data.in_sample_pct}) if data.walk_forward_folds else None,
                    json.dumps(results, default=str), json.dumps(folds, default=str), json.dumps(best, default=str), json.dumps(robustness, default=str), actor))
    return {'id': str(rid), 'timeframe': tf, 'bars': len(bars), 'bars_source': source, 'combinations': len(combos), 'objective': data.objective,
            'results': results, 'best': best, 'folds': folds, 'robustness': robustness}


def listing(db, limit: int = 50) -> list[dict]:
    rows = db.execute("""SELECT o.id, o.algorithm, o.objective, o.broker_symbol, o.timeframe, o.range_from, o.range_to, o.best, o.robustness, o.created_by,
                                o.created_at, a.code AS account_code, jsonb_array_length(o.results) AS combinations
                         FROM tr_optimisations o JOIN cfg_accounts a ON a.id=o.account_id ORDER BY o.created_at DESC LIMIT %s""", (limit,)).fetchall()
    return [store.clean(r) for r in rows]


def detail(db, run_id: UUID) -> dict:
    r = db.execute('SELECT * FROM tr_optimisations WHERE id=%s', (str(run_id),)).fetchone()
    if not r:
        raise HTTPException(404, 'Optimisation not found.')
    return store.clean(r)
