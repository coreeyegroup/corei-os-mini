"""Strategy health: drift against the backtest, and P&L attribution (Stage 4 and Stage 6).

Why it exists: a strategy that worked in the backtest can stop working live; the desk must see it before the loss
limit does. And when the book makes or loses money, it must be clear which strategy, instrument, account and order
source it came from.
Purpose:
  drift        per active deployment with enough closed trades: live win rate, profit factor and average trade
               against the latest backtest of the same strategy version; DRIFT when live is worse by more than the
               tolerance (Settings › drift), OK otherwise, NOT_ENOUGH_TRADES before that. Stored on the runtime
               state; a change to DRIFT raises an alert (once).
  attribution  realised P&L (account currency) and its share of the total by strategy, deployment, instrument,
               account and order source, for a period.
Must never: stop a strategy by itself (an alert and the screen tell a person; kill switches stop strategies), or
mix currencies without converting (a missing FX rate is reported).
Dependencies: trading.common, config_store. Consumers: trading.runtime (drift each cycle), trading_api.
Architectural position: Stage 4 (drift) and Stage 6 (attribution).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .. import config_store as store
from .common import D, ZERO, fx_rate, settings


def _live(db, deployment_id) -> dict:
    r = db.execute("""SELECT count(*) FILTER (WHERE realised_pnl <> 0) AS closed, count(*) FILTER (WHERE realised_pnl > 0) AS wins,
                             sum(realised_pnl) FILTER (WHERE realised_pnl > 0) AS gw, sum(realised_pnl) FILTER (WHERE realised_pnl < 0) AS gl,
                             sum(realised_pnl) AS net FROM oms_fills WHERE deployment_id=%s""", (str(deployment_id),)).fetchone()
    closed = r['closed'] or 0
    gw, gl = D(r['gw']) or ZERO, D(r['gl']) or ZERO
    return {'trades': closed, 'win_rate_pct': round(100 * (r['wins'] or 0) / closed, 1) if closed else None,
            'profit_factor': round(float(gw / -gl), 2) if gl < 0 else None, 'avg_trade': (D(r['net']) / closed).quantize(Decimal('0.01')) if closed else None}


def drift_for(db, dep: dict, cfg: dict) -> dict:
    live = _live(db, dep['id'])
    bt = db.execute('SELECT id, metrics, created_at FROM tr_backtests WHERE strategy_version_id=%s ORDER BY created_at DESC LIMIT 1',
                    (str(dep['strategy_version_id']),)).fetchone()
    out = {'live': {k: str(v) if isinstance(v, Decimal) else v for k, v in live.items()}, 'backtest': None, 'status': 'NOT_ENOUGH_TRADES', 'reasons': [],
           'checked_at': datetime.now(timezone.utc).isoformat()}
    if not bt:
        out['status'] = 'NO_BACKTEST'
        out['reasons'].append('No backtest of this strategy version to compare with (Strategies › Backtest).')
        return out
    m = bt['metrics'] or {}
    out['backtest'] = {'id': str(bt['id']), 'win_rate_pct': m.get('win_rate_pct'), 'profit_factor': m.get('profit_factor'), 'avg_trade': m.get('avg_trade'),
                       'trades': m.get('trades')}
    if live['trades'] < int(cfg.get('drift_min_trades') or 20):
        return out
    tol = float(cfg.get('drift_tolerance_pct') or 50) / 100
    if m.get('win_rate_pct') and live['win_rate_pct'] is not None and live['win_rate_pct'] < m['win_rate_pct'] * (1 - tol):
        out['reasons'].append(f"win rate {live['win_rate_pct']}% vs {m['win_rate_pct']}% in the backtest")
    if m.get('profit_factor') and live['profit_factor'] is not None and live['profit_factor'] < m['profit_factor'] * (1 - tol):
        out['reasons'].append(f"profit factor {live['profit_factor']} vs {m['profit_factor']}")
    if m.get('avg_trade') and live['avg_trade'] is not None and D(m['avg_trade']) > 0 and live['avg_trade'] < 0:
        out['reasons'].append(f"average trade {live['avg_trade']} is a loss; the backtest made {m['avg_trade']}")
    out['status'] = 'DRIFT' if out['reasons'] else 'OK'
    return out


def drift_all(db, now: datetime | None = None) -> int:
    cfg = settings(db)
    deps = db.execute("""SELECT d.id, d.strategy_version_id, s.code, i.symbol, a.code AS account_code, r.drift FROM cfg_deployments d
                         JOIN cfg_strategies s ON s.id=d.strategy_id JOIN cfg_instruments i ON i.id=d.instrument_id JOIN cfg_accounts a ON a.id=d.account_id
                         LEFT JOIN tr_runtime_state r ON r.deployment_id=d.id WHERE d.status='ACTIVE'""").fetchall()
    drifting = 0
    for d in deps:
        res = drift_for(db, dict(d), cfg)
        before = (d['drift'] or {}).get('status')
        db.execute('UPDATE tr_runtime_state SET drift=%s WHERE deployment_id=%s', (json.dumps(res, default=str), str(d['id'])))
        if res['status'] == 'DRIFT':
            drifting += 1
            if before != 'DRIFT':
                try:
                    from .. import alerts
                    alerts.enqueue(db, 'TRADING', 'WARNING', f"Strategy drift: {d['code']} {d['symbol']} {d['account_code']}",
                                   'Live results are worse than the backtest: ' + '; '.join(res['reasons']))
                except Exception:  # noqa: BLE001
                    pass
    return drifting


def drift_report(db) -> list[dict]:
    rows = db.execute("""SELECT d.id AS deployment_id, s.code AS strategy, v.number AS version, i.symbol, a.code AS account_code, d.mode, r.drift
                         FROM cfg_deployments d JOIN cfg_strategies s ON s.id=d.strategy_id JOIN cfg_strategy_versions v ON v.id=d.strategy_version_id
                         JOIN cfg_instruments i ON i.id=d.instrument_id JOIN cfg_accounts a ON a.id=d.account_id
                         LEFT JOIN tr_runtime_state r ON r.deployment_id=d.id WHERE d.status='ACTIVE' ORDER BY s.code, i.symbol""").fetchall()
    return [{**store.clean(r), 'drift': r['drift'] or {'status': 'NOT_CHECKED', 'reasons': ['Checked at the next trading cycle (every minute).']}}
            for r in rows]


DIMENSIONS = {'strategy': "COALESCE(s.code, 'Manual')", 'deployment': "COALESCE(s.code || ' · ' || i.symbol || ' · ' || a.code, 'Manual · ' || a.code)",
              'instrument': 'i.symbol', 'account': 'a.code', 'source': 'o.source', 'mode': 'f.mode'}


def attribution(db, by: str = 'strategy', since: datetime | None = None, until: datetime | None = None) -> dict:
    """Realised P&L by one dimension, converted to each account's currency and then to the firm's reporting currency
    (the most common account currency); shares of the total; missing FX rates listed."""
    if by not in DIMENSIONS:
        by = 'strategy'
    since = since or datetime.now(timezone.utc) - timedelta(days=30)
    until = until or datetime.now(timezone.utc) + timedelta(minutes=1)
    rows = db.execute(f"""SELECT {DIMENSIONS[by]} AS label, f.currency, a.base_currency, count(*) AS fills, sum(f.realised_pnl) AS realised,
                                 count(*) FILTER (WHERE f.realised_pnl > 0) AS wins, count(*) FILTER (WHERE f.realised_pnl < 0) AS losses
                          FROM oms_fills f JOIN oms_orders o ON o.id=f.order_id JOIN cfg_accounts a ON a.id=f.account_id JOIN cfg_instruments i ON i.id=f.instrument_id
                          LEFT JOIN cfg_deployments d ON d.id=f.deployment_id LEFT JOIN cfg_strategies s ON s.id=d.strategy_id
                          WHERE f.at >= %s AND f.at < %s GROUP BY 1, 2, 3""", (since, until)).fetchall()
    ccys = [r['base_currency'] for r in rows if r['base_currency']]
    report = max(set(ccys), key=ccys.count) if ccys else 'USD'
    groups: dict = {}
    missing = set()
    for r in rows:
        rate = fx_rate(db, r['currency'], report)
        g = groups.setdefault(r['label'], {'label': r['label'], 'fills': 0, 'wins': 0, 'losses': 0, 'realised': ZERO})
        g['fills'] += r['fills']; g['wins'] += r['wins'] or 0; g['losses'] += r['losses'] or 0
        if rate is None:
            missing.add(r['currency'])
        else:
            g['realised'] += D(r['realised']) * rate
    total = sum((g['realised'] for g in groups.values()), ZERO)
    gross = sum((abs(g['realised']) for g in groups.values()), ZERO)
    out = []
    for g in sorted(groups.values(), key=lambda g: g['realised'], reverse=True):
        out.append({**g, 'realised': g['realised'].quantize(Decimal('0.01')),
                    'share_pct': (g['realised'] / gross * 100).quantize(Decimal('0.1')) if gross else ZERO,
                    'win_rate_pct': round(100 * g['wins'] / (g['wins'] + g['losses']), 1) if (g['wins'] + g['losses']) else None})
    return {'by': by, 'currency': report, 'total': total.quantize(Decimal('0.01')), 'rows': out, 'missing_fx': sorted(missing),
            'from': since.isoformat(), 'to': until.isoformat()}
