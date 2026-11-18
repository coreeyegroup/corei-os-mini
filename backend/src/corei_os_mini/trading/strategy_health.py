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
  monitor      one row per deployment for the Stage 4 dashboard: status, execution handoff, last bar and target, the
               position and its P&L, live statistics, drift, the last signal and the last decision, today's counts.
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


def monitor(db) -> dict:
    """Every deployment that is not ended, as the Stage 4 dashboard shows it."""
    from . import portfolio
    day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = db.execute("""SELECT d.id, d.status, d.mode, d.version, d.weight, d.allocation_weight_bps, d.execution, s.code AS strategy_code,
                                v.number AS version_number, v.algorithm, v.parameters, i.symbol, a.code AS account_code, c.environment,
                                r.timeframe, r.last_bar_time, r.last_target, r.last_run_at, r.last_error, r.drift,
                                (SELECT row_to_json(x) FROM (SELECT sg.side, sg.strength, sg.state, sg.bar_time, sg.explanation->>0 AS why
                                    FROM tr_signals sg WHERE sg.deployment_id=d.id ORDER BY sg.created_at DESC LIMIT 1) x) AS last_signal,
                                (SELECT row_to_json(y) FROM (SELECT t.id, t.outcome, t.side, t.quantity, t.created_at, t.expires_at
                                    FROM tr_intents t WHERE t.deployment_id=d.id ORDER BY t.created_at DESC LIMIT 1) y) AS last_decision,
                                (SELECT count(*) FROM tr_signals sg WHERE sg.deployment_id=d.id AND sg.created_at >= %s) AS signals_today,
                                (SELECT count(*) FROM oms_orders o WHERE o.deployment_id=d.id AND o.created_at >= %s) AS orders_today,
                                (SELECT count(*) FROM tr_intents t WHERE t.deployment_id=d.id AND t.outcome='PENDING') AS pending
                         FROM cfg_deployments d JOIN cfg_strategies s ON s.id=d.strategy_id JOIN cfg_strategy_versions v ON v.id=d.strategy_version_id
                         JOIN cfg_instruments i ON i.id=d.instrument_id JOIN cfg_accounts a ON a.id=d.account_id JOIN cfg_connections c ON c.id=a.connection_id
                         LEFT JOIN tr_runtime_state r ON r.deployment_id=d.id
                         WHERE d.status <> 'ENDED' ORDER BY (d.status='ACTIVE') DESC, s.code, i.symbol""", (day, day)).fetchall()
    pos = {}
    for p in portfolio.positions(db, open_only=False):
        if p.get('deployment_id'):
            k = str(p['deployment_id'])
            cur = pos.setdefault(k, {'quantity': ZERO, 'unrealised': ZERO, 'realised': ZERO, 'currency': p.get('account_currency')})
            cur['quantity'] += D(p['quantity']) or ZERO
            cur['unrealised'] += D(p.get('unrealised_account')) or ZERO
            cur['realised'] += D(p.get('realised_account')) or ZERO
    out = []
    for r in rows:
        x = store.clean(r)
        x['handoff'] = (r['execution'] or {}).get('handoff', 'AUTO')
        x['position'] = pos.get(str(r['id']), {'quantity': ZERO, 'unrealised': ZERO, 'realised': ZERO, 'currency': None})
        x['live'] = _live(db, r['id'])
        x['drift'] = r['drift'] or {'status': 'NOT_CHECKED'}
        out.append(x)
    active = [x for x in out if x['status'] == 'ACTIVE']
    return {'items': out, 'summary': {
        'active': len(active), 'paused': sum(1 for x in out if x['status'] == 'PAUSED'), 'proposed': sum(1 for x in out if x['status'] == 'PROPOSED'),
        'paper': sum(1 for x in active if x['mode'] != 'BROKER'), 'real': sum(1 for x in active if x['mode'] == 'BROKER'),
        'long': sum(1 for x in active if x['position']['quantity'] > 0), 'short': sum(1 for x in active if x['position']['quantity'] < 0),
        'drifting': sum(1 for x in active if (x['drift'] or {}).get('status') == 'DRIFT'), 'errors': sum(1 for x in active if x['last_error']),
        'pending': sum(x['pending'] for x in out), 'signals_today': sum(x['signals_today'] for x in out), 'orders_today': sum(x['orders_today'] for x in out),
        'pnl_by_currency': _by_currency(out)}}


def _by_currency(rows: list[dict]) -> dict:
    """Unrealised and realised P&L per account currency — never added across currencies."""
    out: dict = {}
    for x in rows:
        p = x['position']
        if not p.get('currency'):
            continue
        c = out.setdefault(p['currency'], {'unrealised': ZERO, 'realised': ZERO})
        c['unrealised'] += p['unrealised']
        c['realised'] += p['realised']
    return out

