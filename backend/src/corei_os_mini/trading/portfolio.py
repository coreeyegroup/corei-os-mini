"""Portfolio (Stage 6 · Portfolio intelligence): positions, P&L, exposure and equity, built only from fills.

Why it exists: risk needs to know what we hold and how much we have lost today; strategies and operators need P&L per
account, strategy and instrument. One owner computes them, the same way everywhere.
Purpose: apply a fill to its position (average cost; realised P&L on the part that closes; a flip closes then
opens), value positions at the live mid, aggregate exposure (gross and net notional, converted to the account's
currency with collected FX pairs), keep starting capital per account, equity snapshots and the peak for drawdown.
Responsibilities: pf_positions (written in the fill's transaction), pf_accounts, pf_equity; reads of all three.
Must never: create fills or orders (execution does), or use floating point for an amount (all Decimal).
Dependencies: trading.common, config_store. Consumers: trading.oms (apply_fill), trading.risk (exposure, loss,
drawdown), trading.runtime (snapshots), trading_api.
Architectural position: Stage 6, after execution and before risk's next decision.
Conventions: P&L of an instrument is in its quote currency (EURUSD in USD, NIFTY in INR) and converted to the
account's currency for equity; a missing FX rate is reported, never guessed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from .. import config_store as store
from .common import D, ZERO, account_currency, fx_rate, instrument, quote

NIL = '00000000-0000-0000-0000-000000000000'


def apply_fill(db, fill: dict) -> Decimal:
    """Update the position for (account, instrument, deployment, mode) with one fill; returns the realised P&L."""
    ins = instrument(db, fill['instrument_id'])
    size = D(ins.get('contract_size')) or Decimal(1)
    ccy = ins.get('quote_currency') or 'USD'
    row = db.execute("""SELECT * FROM pf_positions WHERE account_id=%s AND instrument_id=%s AND COALESCE(deployment_id, %s::uuid)=%s::uuid AND mode=%s
                        FOR UPDATE""", (str(fill['account_id']), str(fill['instrument_id']), NIL, str(fill.get('deployment_id') or NIL), fill['mode'])).fetchone()
    qty = D(row['quantity']) if row else ZERO
    avg = D(row['avg_price']) if row and row['avg_price'] is not None else None
    signed = fill['quantity'] if fill['side'] == 'BUY' else -fill['quantity']
    price = fill['price']
    realised = ZERO
    if qty == 0 or (qty > 0) == (signed > 0):
        new_qty = qty + signed
        avg = price if qty == 0 else (avg * abs(qty) + price * abs(signed)) / abs(new_qty)
    else:
        closing = min(abs(qty), abs(signed))
        direction = Decimal(1) if qty > 0 else Decimal(-1)
        realised = (price - avg) * closing * size * direction
        new_qty = qty + signed
        if new_qty == 0:
            avg = None
        elif (new_qty > 0) != (qty > 0):
            avg = price  # flipped: the remainder opens at the fill price
    tick = D(ins.get('tick_size')) or Decimal('0.00001')
    avg = avg.quantize(tick / 1000) if avg is not None else None
    if row:
        db.execute('UPDATE pf_positions SET quantity=%s, avg_price=%s, realised_pnl=realised_pnl+%s, opened_at=CASE WHEN %s THEN %s ELSE opened_at END, '
                   'updated_at=now() WHERE id=%s', (new_qty, avg, realised, qty == 0 and new_qty != 0, fill['at'], row['id']))
    else:
        db.execute('INSERT INTO pf_positions(id, account_id, instrument_id, deployment_id, mode, quantity, avg_price, realised_pnl, currency, opened_at) '
                   'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', (uuid4(), str(fill['account_id']), str(fill['instrument_id']),
                                                            str(fill['deployment_id']) if fill.get('deployment_id') else None, fill['mode'],
                                                            new_qty, avg, realised, ccy, fill['at']))
    return realised


def position_qty(db, account_id, instrument_id, deployment_id=None, mode: str | None = None) -> Decimal:
    """Net quantity: of one deployment when given, else of the whole account in that instrument."""
    q = 'SELECT COALESCE(sum(quantity), 0) AS q FROM pf_positions WHERE account_id=%s AND instrument_id=%s'
    args = [str(account_id), str(instrument_id)]
    if deployment_id is not None:
        q += ' AND deployment_id=%s'; args.append(str(deployment_id))
    if mode:
        q += ' AND mode=%s'; args.append(mode)
    return D(db.execute(q, args).fetchone()['q']) or ZERO


def positions(db, account_id=None, open_only: bool = True) -> list[dict]:
    """Positions valued at the live mid: unrealised P&L in the quote currency and in the account's currency."""
    where = ['TRUE']
    args: list = []
    if account_id:
        where.append('p.account_id=%s'); args.append(str(account_id))
    if open_only:
        where.append('p.quantity <> 0')
    rows = db.execute(f"""SELECT p.*, i.symbol, i.contract_size, i.asset_class, i.quote_currency, a.code AS account_code, a.base_currency AS account_currency,
                                s.code AS strategy_code, d.mode AS deployment_mode
                         FROM pf_positions p JOIN cfg_instruments i ON i.id=p.instrument_id JOIN cfg_accounts a ON a.id=p.account_id
                         LEFT JOIN cfg_deployments d ON d.id=p.deployment_id LEFT JOIN cfg_strategies s ON s.id=d.strategy_id
                         WHERE {' AND '.join(where)} ORDER BY a.code, i.symbol""", args).fetchall()
    out = []
    for r in rows:
        r = dict(r)
        qty, avg, size = D(r['quantity']), D(r['avg_price']), D(r['contract_size']) or Decimal(1)
        q = quote(db, r['account_id'], r['instrument_id'])
        mark = q['mid'] if q else None
        unreal = (mark - avg) * qty * size if (mark is not None and avg is not None and qty) else ZERO
        rate = fx_rate(db, r['quote_currency'], r['account_currency'])
        notional = abs(qty) * (mark or avg or ZERO) * size
        r.update({'mark': mark, 'mark_source': q and q['source'], 'unrealised_pnl': unreal, 'notional': notional,
                  'fx_to_account': rate, 'unrealised_account': unreal * rate if rate is not None else None,
                  'realised_account': D(r['realised_pnl']) * rate if rate is not None else None,
                  'notional_account': notional * rate if rate is not None else None})
        out.append(r)
    return out


def capital(db, account_id) -> dict:
    row = db.execute('SELECT * FROM pf_accounts WHERE account_id=%s', (str(account_id),)).fetchone()
    if row:
        return dict(row)
    s = db.execute('SELECT default_capital FROM tr_settings WHERE id').fetchone()
    return {'account_id': str(account_id), 'starting_capital': D(s['default_capital']) if s else Decimal(100000),
            'currency': account_currency(db, account_id), 'peak_equity': None, 'version': 0}


def equity(db, account_id) -> dict:
    """Starting capital + realised + unrealised, in the account's currency; missing FX rates are listed."""
    cap = capital(db, account_id)
    ccy = cap['currency']
    realised = unreal = ZERO
    missing = set()
    for r in db.execute('SELECT p.realised_pnl, p.currency FROM pf_positions p WHERE p.account_id=%s', (str(account_id),)).fetchall():
        rate = fx_rate(db, r['currency'], ccy)
        if rate is None:
            missing.add(r['currency']); continue
        realised += D(r['realised_pnl']) * rate
    for p in positions(db, account_id):
        if p['unrealised_account'] is None:
            missing.add(p['quote_currency']); continue
        unreal += p['unrealised_account']
    eq = D(cap['starting_capital']) + realised + unreal
    peak = max(D(cap['peak_equity']) or eq, eq)
    dd = ((peak - eq) / peak * 100) if peak > 0 else ZERO
    return {'account_id': str(account_id), 'currency': ccy, 'starting_capital': D(cap['starting_capital']), 'realised': realised,
            'unrealised': unreal, 'equity': eq, 'peak': peak, 'drawdown_pct': dd.quantize(Decimal('0.01')), 'missing_fx': sorted(missing)}


def day_pnl(db, account_id, day_start: datetime | None = None) -> Decimal:
    """Today's P&L in the account's currency: realised on today's fills plus current unrealised (UTC day)."""
    start = day_start or datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    ccy = account_currency(db, account_id)
    total = ZERO
    for r in db.execute('SELECT currency, sum(realised_pnl) AS r FROM oms_fills WHERE account_id=%s AND at >= %s GROUP BY currency',
                        (str(account_id), start)).fetchall():
        rate = fx_rate(db, r['currency'], ccy)
        if rate is not None:
            total += D(r['r']) * rate
    for p in positions(db, account_id):
        if p['unrealised_account'] is not None:
            total += p['unrealised_account']
    return total


def exposure(db, account_id=None) -> dict:
    """Gross and net notional per instrument and per account (account currency)."""
    by_instrument: dict = {}
    by_account: dict = {}
    for p in positions(db, account_id):
        if p['notional_account'] is None:
            continue
        signed = p['notional_account'] if D(p['quantity']) > 0 else -p['notional_account']
        k = (p['account_id'], p['instrument_id'])
        e = by_instrument.setdefault(k, {'account_id': str(p['account_id']), 'account_code': p['account_code'], 'instrument_id': str(p['instrument_id']),
                                         'symbol': p['symbol'], 'currency': p['account_currency'], 'net': ZERO, 'gross': ZERO})
        e['net'] += signed; e['gross'] += p['notional_account']
        a = by_account.setdefault(str(p['account_id']), {'account_id': str(p['account_id']), 'account_code': p['account_code'],
                                                         'currency': p['account_currency'], 'net': ZERO, 'gross': ZERO})
        a['net'] += signed; a['gross'] += p['notional_account']
    return {'instruments': list(by_instrument.values()), 'accounts': list(by_account.values())}


def snapshot(db, at: datetime | None = None) -> int:
    """Equity of every account with positions or fills, stored once a minute; raises the peak for drawdown."""
    at = (at or datetime.now(timezone.utc)).replace(second=0, microsecond=0)
    ids = [r['account_id'] for r in db.execute('SELECT DISTINCT account_id FROM pf_positions').fetchall()]
    for aid in ids:
        e = equity(db, aid)
        db.execute('INSERT INTO pf_equity(account_id, at, equity, realised, unrealised, currency) VALUES (%s,%s,%s,%s,%s,%s) '
                   'ON CONFLICT (account_id, at) DO UPDATE SET equity=EXCLUDED.equity, realised=EXCLUDED.realised, unrealised=EXCLUDED.unrealised',
                   (str(aid), at, e['equity'].quantize(Decimal('0.01')), e['realised'].quantize(Decimal('0.01')),
                    e['unrealised'].quantize(Decimal('0.01')), e['currency']))
        cap = capital(db, aid)
        if cap.get('version'):
            db.execute('UPDATE pf_accounts SET peak_equity=GREATEST(COALESCE(peak_equity, 0), %s) WHERE account_id=%s', (e['equity'], str(aid)))
        else:
            db.execute('INSERT INTO pf_accounts(account_id, starting_capital, currency, peak_equity, updated_by) VALUES (%s,%s,%s,%s,%s) '
                       'ON CONFLICT (account_id) DO UPDATE SET peak_equity=GREATEST(COALESCE(pf_accounts.peak_equity, 0), EXCLUDED.peak_equity)',
                       (str(aid), cap['starting_capital'], cap['currency'], max(e['equity'], cap['starting_capital']), 'portfolio'))
    db.execute("DELETE FROM pf_equity WHERE at < now() - interval '400 days'")
    return len(ids)


def set_capital(actor: str, account_id, amount: Decimal, reason: str) -> dict:
    with store.transaction('write') as db:
        before = db.execute('SELECT * FROM pf_accounts WHERE account_id=%s FOR UPDATE', (str(account_id),)).fetchone()
        ccy = account_currency(db, account_id)
        if before:
            db.execute('UPDATE pf_accounts SET starting_capital=%s, peak_equity=NULL, version=version+1, updated_by=%s, updated_at=now() WHERE account_id=%s',
                       (amount, actor, str(account_id)))
        else:
            db.execute('INSERT INTO pf_accounts(account_id, starting_capital, currency, updated_by) VALUES (%s,%s,%s,%s)', (str(account_id), amount, ccy, actor))
        after = db.execute('SELECT * FROM pf_accounts WHERE account_id=%s', (str(account_id),)).fetchone()
        store.audit(db, actor, 'SET_PAPER_CAPITAL', 'account', account_id, dict(before) if before else None, dict(after), reason)
        return store.clean(after)


def performance(db, since: datetime | None = None) -> list[dict]:
    """Per deployment (and manual trading per account): fills, realised P&L, open quantity, unrealised, win rate."""
    since = since or datetime.now(timezone.utc) - timedelta(days=3650)
    rows = db.execute("""SELECT f.deployment_id, f.account_id, f.instrument_id, f.currency, count(*) AS fills,
                                sum(f.realised_pnl) AS realised, count(*) FILTER (WHERE f.realised_pnl > 0) AS wins,
                                count(*) FILTER (WHERE f.realised_pnl < 0) AS losses,
                                sum(f.realised_pnl) FILTER (WHERE f.realised_pnl > 0) AS gross_win,
                                sum(f.realised_pnl) FILTER (WHERE f.realised_pnl < 0) AS gross_loss
                         FROM oms_fills f WHERE f.at >= %s GROUP BY 1, 2, 3, 4""", (since,)).fetchall()
    open_pos = {(str(p['deployment_id']) if p['deployment_id'] else None, str(p['account_id']), str(p['instrument_id'])): p for p in positions(db)}
    names = {str(r['id']): r for r in db.execute('SELECT d.id, s.code AS strategy, v.number AS version, d.mode FROM cfg_deployments d '
                                                 'JOIN cfg_strategies s ON s.id=d.strategy_id JOIN cfg_strategy_versions v ON v.id=d.strategy_version_id').fetchall()}
    syms = {str(r['id']): r['symbol'] for r in db.execute('SELECT id, symbol FROM cfg_instruments').fetchall()}
    accts = {str(r['id']): r['code'] for r in db.execute('SELECT id, code FROM cfg_accounts').fetchall()}
    out = []
    for r in rows:
        dep = str(r['deployment_id']) if r['deployment_id'] else None
        p = open_pos.get((dep, str(r['account_id']), str(r['instrument_id'])))
        closed = (r['wins'] or 0) + (r['losses'] or 0)
        gw, gl = D(r['gross_win']) or ZERO, D(r['gross_loss']) or ZERO
        out.append({'deployment_id': dep, 'strategy': names.get(dep, {}).get('strategy') if dep else 'Manual', 'version': names.get(dep, {}).get('version') if dep else None,
                    'mode': names.get(dep, {}).get('mode') if dep else None, 'account_id': str(r['account_id']), 'account_code': accts.get(str(r['account_id'])),
                    'instrument_id': str(r['instrument_id']), 'symbol': syms.get(str(r['instrument_id'])), 'currency': r['currency'],
                    'fills': r['fills'], 'realised': D(r['realised']) or ZERO, 'closing_fills': closed,
                    'win_rate_pct': round(100 * (r['wins'] or 0) / closed, 1) if closed else None,
                    'profit_factor': round(float(gw / -gl), 2) if gl < 0 else None,
                    'open_qty': D(p['quantity']) if p else ZERO, 'unrealised': p['unrealised_pnl'] if p else ZERO})
    return out
