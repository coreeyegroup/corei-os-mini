"""Risk (Stage 5 · Risk intelligence): kill switches, limits, sizing and the pre-trade check.

Why it exists: every order — from a strategy, an operator or a flatten — must pass one gate that can say no, and
whose "no" nothing downstream can bypass. It also decides how big an order may be.
Purpose: scoped kill switches (firm, account, strategy, instrument, deployment) with engage/release audited;
versioned limits per scope (the tightest applies); position sizing (fixed quantity, % of equity at risk with an ATR
stop, fixed notional) rounded down to the lot step; the pre-trade check that returns the approved quantity or every
reason it refused; automatic kill switch when a loss or drawdown limit is breached; the decision log.
Responsibilities: risk_kill_switches, risk_limits, risk_decisions (written in the order's transaction).
Must never: send, change or cancel an order (execution owns orders), let an order through when a check could not be
made (missing price, missing FX rate: refuse), or use floating point for an amount.
Dependencies: trading.common, trading.portfolio, config_store, md.markets. Consumers: trading.oms, trading.runtime,
trading_api.
Architectural position: Stage 5, between the decision (Stage 3) and execution (Stage 7).
Conventions: reduce-only orders (they shrink an open position) skip the position, exposure and loss limits but not
the kill switches, except an operator's flatten, which a kill switch allows (it removes risk).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .. import config_store as store
from ..md import markets
from . import portfolio
from .common import D, ZERO, floor_step, fx_rate, instrument

SCOPES = ('FIRM', 'ACCOUNT', 'STRATEGY', 'INSTRUMENT', 'DEPLOYMENT')
KINDS = {
    'MAX_ORDER_QTY': 'Largest single order (contracts / lots)',
    'MAX_POSITION_QTY': 'Largest net position per instrument (contracts / lots)',
    'MAX_OPEN_ORDERS': 'Most working orders at once',
    'MAX_ORDERS_PER_MINUTE': 'Most new orders in any minute',
    'MAX_DAILY_LOSS': "Largest loss today (account currency); breach stops new orders",
    'MAX_DRAWDOWN_PCT': 'Largest fall from the equity peak (%); breach stops new orders',
    'MAX_GROSS_EXPOSURE': 'Largest total position value (account currency)',
    'MAX_INSTRUMENT_EXPOSURE': 'Largest position value in one instrument (account currency)',
    'MAX_ORDER_NOTIONAL': 'Largest single order value (account currency)',
    'MAX_MARGIN_PCT': 'Most margin in use after the order, as % of equity',
    'MAX_LEVERAGE': 'Largest gross exposure ÷ equity after the order (times)',
    'MAX_VAR_PCT': 'Largest 1-day Value at Risk after the order, as % of equity',
    'MAX_PRICE_DEVIATION_PCT': 'Limit or stop price at most this % from the live price (fat-finger check)',
}


# ---------- kill switches ----------

class KillIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scope: Literal['FIRM', 'ACCOUNT', 'STRATEGY', 'INSTRUMENT', 'DEPLOYMENT']
    target_id: UUID | None = None
    reason: str = Field(min_length=5, max_length=500)


def engaged(db, scopes: dict[str, str | None]) -> list[dict]:
    """Engaged kill switches that apply to an order with these targets ({scope: id})."""
    rows = db.execute('SELECT * FROM risk_kill_switches WHERE engaged').fetchall()
    out = []
    for r in rows:
        if r['scope'] == 'FIRM' or (scopes.get(r['scope']) and str(r['target_id']) == str(scopes[r['scope']])):
            out.append(dict(r))
    return out


def engage(actor: str, data: KillIn, automatic: bool = False, db=None) -> dict:
    if (data.scope == 'FIRM') != (data.target_id is None):
        raise HTTPException(422, 'A firm-wide kill switch has no target; every other scope needs one.')

    def run(c):
        found = c.execute("SELECT * FROM risk_kill_switches WHERE engaged AND scope=%s AND COALESCE(target_id, %s::uuid)=%s::uuid",
                          (data.scope, portfolio.NIL, str(data.target_id or portfolio.NIL))).fetchone()
        if found:
            return dict(found)
        row = c.execute('INSERT INTO risk_kill_switches(id, scope, target_id, automatic, reason, engaged_by) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *',
                        (uuid4(), data.scope, str(data.target_id) if data.target_id else None, automatic, data.reason, actor)).fetchone()
        store.audit(c, actor, 'ENGAGE_KILL_SWITCH', 'kill_switch', row['id'], None, dict(row), data.reason)
        try:
            from .. import alerts
            alerts.enqueue(c, 'KILL_SWITCH', 'CRITICAL', f'Kill switch engaged: {data.scope.lower()}', f'{data.reason} (by {actor})')
        except Exception:  # noqa: BLE001 - an alert must never stop the switch
            pass
        return dict(row)
    if db is not None:
        return store.clean(run(db))
    with store.transaction('write') as c:
        return store.clean(run(c))


def release(actor: str, switch_id: UUID, reason: str) -> dict:
    if len(reason.strip()) < 5:
        raise HTTPException(422, 'Give a reason (at least 5 characters).')
    with store.transaction('write') as db:
        before = db.execute('SELECT * FROM risk_kill_switches WHERE id=%s FOR UPDATE', (switch_id,)).fetchone()
        if not before:
            raise HTTPException(404, 'Kill switch not found.')
        if not before['engaged']:
            raise HTTPException(409, 'This kill switch is already released.')
        after = db.execute('UPDATE risk_kill_switches SET engaged=false, released_by=%s, released_at=now(), release_reason=%s, version=version+1 '
                           'WHERE id=%s RETURNING *', (actor, reason, switch_id)).fetchone()
        store.audit(db, actor, 'RELEASE_KILL_SWITCH', 'kill_switch', switch_id, dict(before), dict(after), reason)
        return store.clean(after)


def kill_switches(db, history: bool = False) -> list[dict]:
    rows = db.execute('SELECT * FROM risk_kill_switches' + ('' if history else ' WHERE engaged') + ' ORDER BY engaged DESC, engaged_at DESC LIMIT 500').fetchall()
    return [store.clean(r) for r in rows]


# ---------- limits ----------

class LimitIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scope: Literal['FIRM', 'ACCOUNT', 'STRATEGY', 'INSTRUMENT', 'DEPLOYMENT']
    target_id: UUID | None = None
    kind: Literal['MAX_ORDER_QTY', 'MAX_POSITION_QTY', 'MAX_OPEN_ORDERS', 'MAX_ORDERS_PER_MINUTE', 'MAX_DAILY_LOSS', 'MAX_DRAWDOWN_PCT',
                  'MAX_GROSS_EXPOSURE', 'MAX_INSTRUMENT_EXPOSURE', 'MAX_ORDER_NOTIONAL', 'MAX_MARGIN_PCT', 'MAX_LEVERAGE', 'MAX_VAR_PCT',
                  'MAX_PRICE_DEVIATION_PCT']
    value: Decimal = Field(ge=0, max_digits=38, decimal_places=12)
    enabled: bool = True
    note: str = Field(default='', max_length=500)
    reason: str = Field(min_length=5, max_length=500)


def save_limit(actor: str, data: LimitIn, limit_id: UUID | None = None, version: int | None = None) -> dict:
    if (data.scope == 'FIRM') != (data.target_id is None):
        raise HTTPException(422, 'A firm-wide limit has no target; every other scope needs one.')
    if data.kind in ('MAX_DRAWDOWN_PCT', 'MAX_VAR_PCT') and data.value > 100:
        raise HTTPException(422, 'This limit is a percentage (0–100).')
    with store.transaction('write') as db:
        if limit_id is None:
            row = db.execute('INSERT INTO risk_limits(id, scope, target_id, kind, value, enabled, note, created_by, updated_by) '
                             'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                             (uuid4(), data.scope, str(data.target_id) if data.target_id else None, data.kind, data.value, data.enabled, data.note,
                              actor, actor)).fetchone()
            store.audit(db, actor, 'CREATE_RISK_LIMIT', 'risk_limit', row['id'], None, dict(row), data.reason)
        else:
            before = db.execute('SELECT * FROM risk_limits WHERE id=%s FOR UPDATE', (limit_id,)).fetchone()
            if not before:
                raise HTTPException(404, 'Limit not found.')
            if version is not None and before['version'] != version:
                raise HTTPException(409, 'This limit was changed by someone else: reload and try again.')
            if (before['scope'], str(before['target_id']), before['kind']) != (data.scope, str(data.target_id) if data.target_id else 'None', data.kind):
                raise HTTPException(422, 'Scope, target and kind are fixed: add a new limit instead.')
            row = db.execute('UPDATE risk_limits SET value=%s, enabled=%s, note=%s, version=version+1, updated_by=%s, updated_at=now() WHERE id=%s RETURNING *',
                             (data.value, data.enabled, data.note, actor, limit_id)).fetchone()
            store.audit(db, actor, 'UPDATE_RISK_LIMIT', 'risk_limit', limit_id, dict(before), dict(row), data.reason)
        return store.clean(row)


def delete_limit(actor: str, limit_id: UUID, reason: str) -> None:
    with store.transaction('delete') as db:
        before = db.execute('DELETE FROM risk_limits WHERE id=%s RETURNING *', (limit_id,)).fetchone()
        if not before:
            raise HTTPException(404, 'Limit not found.')
        store.audit(db, actor, 'DELETE_RISK_LIMIT', 'risk_limit', limit_id, dict(before), None, reason)


def limits(db) -> list[dict]:
    rows = db.execute("""SELECT l.*, CASE l.scope WHEN 'ACCOUNT' THEN a.code WHEN 'STRATEGY' THEN s.code WHEN 'INSTRUMENT' THEN i.symbol
                                WHEN 'DEPLOYMENT' THEN ds.code || ' · ' || di.symbol || ' · ' || da.code ELSE 'Firm' END AS target_name
                         FROM risk_limits l LEFT JOIN cfg_accounts a ON a.id=l.target_id LEFT JOIN cfg_strategies s ON s.id=l.target_id
                         LEFT JOIN cfg_instruments i ON i.id=l.target_id LEFT JOIN cfg_deployments d ON d.id=l.target_id
                         LEFT JOIN cfg_strategies ds ON ds.id=d.strategy_id LEFT JOIN cfg_instruments di ON di.id=d.instrument_id
                         LEFT JOIN cfg_accounts da ON da.id=d.account_id
                         ORDER BY array_position(ARRAY['FIRM','ACCOUNT','STRATEGY','INSTRUMENT','DEPLOYMENT'], l.scope), target_name, l.kind""").fetchall()
    return [store.clean(r) for r in rows]


def applicable(db, scopes: dict[str, str | None]) -> dict[str, dict]:
    """The tightest enabled limit of each kind among the scopes that apply."""
    out: dict[str, dict] = {}
    for r in db.execute('SELECT * FROM risk_limits WHERE enabled').fetchall():
        if r['scope'] != 'FIRM' and not (scopes.get(r['scope']) and str(r['target_id']) == str(scopes[r['scope']])):
            continue
        if r['kind'] not in out or D(r['value']) < D(out[r['kind']]['value']):
            out[r['kind']] = dict(r)
    return out


# ---------- sizing ----------

def size(db, dep: dict, price: Decimal, atr: float | None) -> tuple[Decimal | None, str]:
    """Order quantity for a new position of a deployment, before rounding to the lot step; (None, reason) if not possible."""
    s = dep.get('sizing') or {'method': 'FIXED_QTY', 'quantity': '1'}
    ins = instrument(db, dep['instrument_id'])
    contract = D(ins.get('contract_size')) or Decimal(1)
    method = s.get('method', 'FIXED_QTY')
    weight = D(dep.get('weight')) if dep.get('weight') is not None else Decimal(1)
    if weight == 0:
        return None, 'WEIGHT_ZERO: the deployment weight is 0 (Portfolio › Allocation)'
    if method == 'FIXED_QTY':
        qty = (D(s.get('quantity')) or Decimal(1)) * weight
        return qty, f"fixed {s.get('quantity')}" + (f' × weight {weight.normalize()}' if weight != 1 else '')
    eq = portfolio.sizing_equity(db, dep)
    rate = fx_rate(db, ins.get('quote_currency'), eq['currency'])
    if rate is None:
        return None, f"FX_RATE_MISSING: no collected pair converts {ins.get('quote_currency')} to {eq['currency']}"
    if method == 'FIXED_NOTIONAL':
        per_unit = price * contract * rate
        return (D(s['notional']) * weight / per_unit if per_unit else None), f"notional {s['notional']} {eq['currency']} × weight {weight.normalize()}"
    if not atr:
        return None, 'ATR_UNKNOWN: the ATR of the strategy timeframe is not available yet'
    risk_amount = eq['equity'] * D(s['risk_pct']) / 100 * weight
    per_unit = D(atr) * D(s['atr_multiple']) * contract * rate
    return (risk_amount / per_unit if per_unit else None), (f"{s['risk_pct']}% of {eq.get('basis', 'equity')} {eq['equity']:.2f} over {s['atr_multiple']}×ATR"
                                                            + (f' × weight {weight.normalize()}' if weight != 1 else ''))


def lot(db, instrument_id, qty: Decimal) -> tuple[Decimal, str | None]:
    """Round down to the instrument's quantity step and check the minimum."""
    ins = instrument(db, instrument_id)
    step, minimum = D(ins.get('quantity_step')) or Decimal(1), D(ins.get('min_quantity')) or Decimal(1)
    q = floor_step(qty, step)
    if q < minimum:
        return q, f'SIZE_BELOW_MINIMUM: {qty.normalize()} rounds to {q.normalize()}, under the smallest tradable {minimum.normalize()}'
    return q, None


# ---------- the pre-trade check ----------

def check(db, order: dict, deployment: dict | None, now: datetime, quote: dict | None, flatten: bool = False) -> dict:
    """Every check, in order; returns {approved, quantity, reasons, inputs}. A check that cannot be made refuses."""
    reasons: list[str] = []
    ins = instrument(db, order['instrument_id'])
    scopes = {'ACCOUNT': str(order['account_id']), 'INSTRUMENT': str(order['instrument_id']),
              'STRATEGY': str(deployment['strategy_id']) if deployment else None, 'DEPLOYMENT': str(deployment['id']) if deployment else None}
    qty = order['quantity']
    current = portfolio.position_qty(db, order['account_id'], order['instrument_id'], mode=order['mode'])
    signed = qty if order['side'] == 'BUY' else -qty
    after = current + signed
    reducing = current != 0 and abs(after) < abs(current) and (after == 0 or (after > 0) == (current > 0))
    inputs = {'position_before': str(current), 'position_after': str(after), 'reduce_only': reducing}

    # 1. Kill switches (an operator's flatten may still remove risk).
    for k in engaged(db, scopes):
        if not (flatten and reducing):
            reasons.append(f"KILL_SWITCH_{k['scope']}: {k['reason']}")
    # 1b. Capital partition of the deployment: paused, or past its own drawdown limit (new risk only).
    if deployment and deployment.get('partition_id') and not reducing:
        pe = portfolio.partition_equity(db, deployment['partition_id'])
        if pe and pe['status'] == 'PAUSED':
            reasons.append(f"PARTITION_PAUSED: capital partition {pe['name']} is paused")
        elif pe and pe.get('max_drawdown_pct') is not None and pe['drawdown_pct'] > D(pe['max_drawdown_pct']):
            reasons.append(f"PARTITION_DRAWDOWN: {pe['name']} is {pe['drawdown_pct']}% below its capital (limit {pe['max_drawdown_pct']}%)")
    # 2. Real orders: the order adapter and the environment's order-sending switch.
    if order['mode'] == 'BROKER':
        env = db.execute('SELECT c.environment FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id WHERE a.id=%s',
                         (str(order['account_id']),)).fetchone()['environment']
        ctl = store.execution_controls(db).get(env) or {}
        if not ctl.get('trading_enabled'):
            reasons.append(f'ORDER_SENDING_OFF_{env}: order sending is off for {env} accounts (Setup › Order sending)')
        from . import brokers
        cap = brokers.capability(db, order['account_id'])
        reasons += cap['reasons']
        inputs['adapter'] = cap.get('adapter')
        inputs['environment'] = env
        if not order.get('broker_symbol'):
            reasons.append("NO_BROKER_SYMBOL: this account has no broker symbol for the instrument (Setup › Instruments › mapping)")
        if order.get('order_type') == 'STOP' and not order.get('stop_price'):
            reasons.append('STOP_PRICE_MISSING')
    # 3. Market state and price.
    market = markets.market_for(ins.get('market'), quote['broker_symbol'] if quote else '', '', ins.get('asset_class') or '')
    if not markets.is_open(market, now):
        reasons.append(f'MARKET_CLOSED: {market} is closed now')
    if quote is None:
        reasons.append('NO_PRICE: no live price for this instrument')
    if order.get('order_type') == 'LIMIT' and not order.get('limit_price'):
        reasons.append('LIMIT_PRICE_MISSING')
    if order['mode'] == 'PAPER' and order.get('order_type') == 'STOP' and not order.get('stop_price'):
        reasons.append('STOP_PRICE_MISSING')
    # 4. Lot rules.
    q, problem = lot(db, order['instrument_id'], qty)
    if problem:
        reasons.append(problem)
    elif q != qty:
        reasons.append(f'QUANTITY_STEP: {qty.normalize()} is not a multiple of the quantity step (would be {q.normalize()})')
    # 5. Limits (the tightest of each kind).
    lim = applicable(db, scopes)
    inputs['limits'] = {k: str(v['value']) for k, v in lim.items()}

    def over(kind: str, value: Decimal, text: str):
        if kind in lim and value > D(lim[kind]['value']):
            reasons.append(f"{kind}: {text} {value.normalize() if isinstance(value, Decimal) else value} > limit {D(lim[kind]['value']).normalize()} ({lim[kind]['scope'].lower()})")

    over('MAX_ORDER_QTY', qty, 'order')
    level = order.get('limit_price') or order.get('stop_price')
    if 'MAX_PRICE_DEVIATION_PCT' in lim and level and quote is not None and quote['mid']:
        over('MAX_PRICE_DEVIATION_PCT', (abs(D(level) - quote['mid']) / quote['mid'] * 100).quantize(Decimal('0.01')), 'price away from the market %')
    open_orders = db.execute("SELECT count(*) AS n FROM oms_orders WHERE account_id=%s AND state IN ('APPROVED','SUBMITTED','ACKNOWLEDGED','PARTIALLY_FILLED','CANCEL_PENDING','UNKNOWN')",
                             (str(order['account_id']),)).fetchone()['n']
    over('MAX_OPEN_ORDERS', Decimal(open_orders + 1), 'working orders')
    per_min = db.execute('SELECT count(*) AS n FROM oms_orders WHERE account_id=%s AND created_at > %s', (str(order['account_id']), now - timedelta(minutes=1))).fetchone()['n']
    over('MAX_ORDERS_PER_MINUTE', Decimal(per_min + 1), 'orders this minute')
    if not reducing:
        over('MAX_POSITION_QTY', abs(after), 'position')
        if any(k in lim for k in ('MAX_DAILY_LOSS', 'MAX_DRAWDOWN_PCT')):
            eq = portfolio.equity(db, order['account_id'])
            if eq['missing_fx']:
                reasons.append(f"FX_RATE_MISSING: cannot value positions in {', '.join(eq['missing_fx'])} in {eq['currency']}")
            loss = -portfolio.day_pnl(db, order['account_id'])
            inputs.update({'equity': str(eq['equity']), 'drawdown_pct': str(eq['drawdown_pct']), 'day_loss': str(loss)})
            over('MAX_DAILY_LOSS', loss, 'loss today')
            over('MAX_DRAWDOWN_PCT', eq['drawdown_pct'], 'drawdown %')
        money_kinds = ('MAX_GROSS_EXPOSURE', 'MAX_INSTRUMENT_EXPOSURE', 'MAX_ORDER_NOTIONAL', 'MAX_MARGIN_PCT', 'MAX_LEVERAGE', 'MAX_VAR_PCT')
        if any(k in lim for k in money_kinds):
            eq = portfolio.equity(db, order['account_id'])
            if quote is not None:
                rate = fx_rate(db, ins.get('quote_currency'), eq['currency'])
                if rate is None:
                    reasons.append(f"FX_RATE_MISSING: no collected pair converts {ins.get('quote_currency')} to {eq['currency']}")
                else:
                    contract = D(ins.get('contract_size')) or Decimal(1)
                    exp = portfolio.exposure(db, order['account_id'])
                    gross = sum((a['gross'] for a in exp['accounts']), ZERO)
                    this = next((e['net'] for e in exp['instruments'] if e['instrument_id'] == str(order['instrument_id'])), ZERO)
                    add = qty * quote['mid'] * contract * rate
                    signed_add = add if order['side'] == 'BUY' else -add
                    over('MAX_GROSS_EXPOSURE', (gross + add).quantize(Decimal('0.01')), 'gross exposure')
                    over('MAX_INSTRUMENT_EXPOSURE', abs(this + signed_add).quantize(Decimal('0.01')), 'exposure in this instrument')
                    over('MAX_ORDER_NOTIONAL', add.quantize(Decimal('0.01')), 'order value')
                    equity_now = eq['equity']
                    if any(k in lim for k in ('MAX_MARGIN_PCT', 'MAX_LEVERAGE', 'MAX_VAR_PCT')) and equity_now <= 0:
                        reasons.append('EQUITY_NOT_POSITIVE: margin, leverage and VaR limits cannot be checked')
                    elif equity_now > 0:
                        m = margin(db, order['account_id'], extra=(order['instrument_id'], signed, quote['mid']))
                        inputs.update({'margin_after': str(m['used_after']), 'leverage_after': str(((gross + add) / equity_now).quantize(Decimal('0.01')))})
                        over('MAX_MARGIN_PCT', (m['used_after'] / equity_now * 100).quantize(Decimal('0.01')), 'margin in use %')
                        over('MAX_LEVERAGE', ((gross + add) / equity_now).quantize(Decimal('0.01')), 'leverage')
                        if 'MAX_VAR_PCT' in lim:
                            from . import var
                            v = var.account_var(db, order['account_id'], extra=(str(order['instrument_id']), signed))
                            if v.get('error'):
                                reasons.append(f"VAR_UNAVAILABLE: {v['error']}")
                            else:
                                inputs['var_after'] = str(v['var'])
                                over('MAX_VAR_PCT', (v['var'] / equity_now * 100).quantize(Decimal('0.01')), f"VaR {v['confidence']}% %")
    approved = not reasons
    db.execute('INSERT INTO risk_decisions(id, order_id, intent_id, deployment_id, account_id, instrument_id, side, requested_qty, approved_qty, approved, reasons, inputs, at) '
               'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
               (uuid4(), order.get('id'), order.get('intent_id'), scopes['DEPLOYMENT'], str(order['account_id']), str(order['instrument_id']), order['side'],
                qty, qty if approved else None, approved, json.dumps(reasons), json.dumps(inputs, default=str), now))
    return {'approved': approved, 'quantity': qty, 'reasons': reasons, 'inputs': inputs}


def margin(db, account_id, extra: tuple | None = None) -> dict:
    """Margin in use (account currency) from position values and the account's leverage (1 when not known, i.e. fully
    paid); with extra=(instrument_id, signed qty, price) also after an order. The broker's own figure, when a recent
    snapshot exists, is reported beside it."""
    acc = db.execute('SELECT leverage, base_currency FROM cfg_accounts WHERE id=%s', (str(account_id),)).fetchone()
    lev = Decimal(acc['leverage']) if acc and acc['leverage'] else Decimal(1)
    used = ZERO
    net: dict = {}
    for p in portfolio.positions(db, account_id):
        if p['notional_account'] is None:
            continue
        used += p['notional_account'] / lev
        net[str(p['instrument_id'])] = net.get(str(p['instrument_id']), ZERO) + D(p['quantity'])
    after = used
    if extra:
        iid, signed, price = extra
        ins = instrument(db, iid)
        rate = fx_rate(db, ins.get('quote_currency'), acc['base_currency'] if acc else 'USD') or Decimal(1)
        contract = D(ins.get('contract_size')) or Decimal(1)
        cur = net.get(str(iid), ZERO)
        grow = abs(cur + signed) - abs(cur)  # only the growing part of the position needs more margin
        after = used + grow * D(price) * contract * rate / lev
    broker = portfolio.broker_money(db, account_id)
    return {'leverage': lev, 'used': used.quantize(Decimal('0.01')), 'used_after': max(after, ZERO).quantize(Decimal('0.01')),
            'broker_margin': broker.get('margin') if broker else None, 'broker_free': broker.get('margin_free') if broker else None}


def auto_stop(db, now: datetime) -> list[dict]:
    """Engage the account kill switch when its daily loss or drawdown limit is breached (once; released by a person)."""
    engaged_now = []
    for r in db.execute("SELECT * FROM risk_limits WHERE enabled AND kind IN ('MAX_DAILY_LOSS','MAX_DRAWDOWN_PCT') AND scope IN ('ACCOUNT','FIRM')").fetchall():
        accounts = [r['target_id']] if r['scope'] == 'ACCOUNT' else [x['account_id'] for x in db.execute('SELECT DISTINCT account_id FROM pf_positions').fetchall()]
        for aid in accounts:
            if engaged(db, {'ACCOUNT': str(aid)}) and any(k['scope'] == 'ACCOUNT' for k in engaged(db, {'ACCOUNT': str(aid)})):
                continue
            if r['kind'] == 'MAX_DAILY_LOSS':
                value = -portfolio.day_pnl(db, aid)
            else:
                value = portfolio.equity(db, aid)['drawdown_pct']
            if value > D(r['value']):
                code = db.execute('SELECT code FROM cfg_accounts WHERE id=%s', (str(aid),)).fetchone()['code']
                text = (f"{code}: loss today {value:.2f} above the limit {D(r['value']).normalize()}" if r['kind'] == 'MAX_DAILY_LOSS'
                        else f"{code}: drawdown {value}% above the limit {D(r['value']).normalize()}%")
                engaged_now.append(engage('risk-engine', KillIn(scope='ACCOUNT', target_id=aid, reason=text), automatic=True, db=db))
    return engaged_now


def decisions(db, limit: int = 200, approved: bool | None = None) -> list[dict]:
    rows = db.execute("""SELECT r.*, a.code AS account_code, i.symbol, s.code AS strategy_code FROM risk_decisions r
                         JOIN cfg_accounts a ON a.id=r.account_id JOIN cfg_instruments i ON i.id=r.instrument_id
                         LEFT JOIN cfg_deployments d ON d.id=r.deployment_id LEFT JOIN cfg_strategies s ON s.id=d.strategy_id"""
                      + ('' if approved is None else ' WHERE r.approved=%s') + ' ORDER BY r.at DESC LIMIT %s',
                      ([approved, limit] if approved is not None else [limit])).fetchall()
    return [store.clean(r) for r in rows]


def utc(ts: datetime | None = None) -> datetime:
    return ts or datetime.now(timezone.utc)
