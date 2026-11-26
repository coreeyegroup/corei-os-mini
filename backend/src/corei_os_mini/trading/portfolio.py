"""Portfolio (Stage 6 · Portfolio intelligence): positions, P&L, exposure and equity, built only from fills.

Why it exists: risk needs to know what we hold and how much we have lost today; strategies and operators need P&L per
account, strategy and instrument. One owner computes them, the same way everywhere.
Purpose: apply a fill to its position (average cost; realised P&L on the part that closes; a flip closes then
opens), value positions at the live mid, aggregate exposure (gross and net notional, converted to the account's
currency with collected FX pairs), keep starting capital per account, equity snapshots and the peak for drawdown.
Books: an account holds two books, PAPER and BROKER (execution mode). Every money figure risk uses is asked for one
book (`mode`); `mode=None` is the account-wide consolidated view and is labelled 'ALL' — it is for reporting, never
for a decision about one book.
Valuation: a holding with no live mark has unknown unrealised P&L (None, 'NO_MARK'), not zero; a holding whose
currency cannot be converted is 'NO_FX'. Figures list such holdings in `unvalued` and say `complete=False`.
Responsibilities: pf_positions (written in the fill's transaction), pf_accounts, pf_equity; reads of all three.
Must never: create fills or orders (execution does), use floating point for an amount (all Decimal), mix the paper
and broker books in a figure meant for one of them, or treat an unknown value as zero.
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
    """Update the position for (account, instrument, deployment, mode) with one fill (net of its cost); returns the gross realised P&L."""
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
    # A fill's cost is realised at once in the position (net), while the fill keeps its gross P&L and its cost apart, so
    # win/loss counts are not distorted by costs.
    net = realised - (D(fill['commission']) if fill.get('commission') else ZERO)
    tick = D(ins.get('tick_size')) or Decimal('0.00001')
    avg = avg.quantize(tick / 1000) if avg is not None else None
    # Protective levels: set by the order that opens (or reverses) the position, kept while it runs, cleared when flat.
    opens = new_qty != 0 and (qty == 0 or (new_qty > 0) != (qty > 0))
    stop = fill.get('stop_loss') if opens else (None if new_qty == 0 else (row['stop_loss'] if row else None))
    take = fill.get('take_profit') if opens else (None if new_qty == 0 else (row['take_profit'] if row else None))
    if row:
        db.execute('UPDATE pf_positions SET quantity=%s, avg_price=%s, realised_pnl=realised_pnl+%s, opened_at=CASE WHEN %s THEN %s ELSE opened_at END, '
                   'stop_loss=%s, take_profit=%s, updated_at=now() WHERE id=%s', (new_qty, avg, net, opens, fill['at'], stop, take, row['id']))
    else:
        db.execute('INSERT INTO pf_positions(id, account_id, instrument_id, deployment_id, mode, quantity, avg_price, realised_pnl, currency, opened_at, '
                   'stop_loss, take_profit) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                   (uuid4(), str(fill['account_id']), str(fill['instrument_id']), str(fill['deployment_id']) if fill.get('deployment_id') else None,
                    fill['mode'], new_qty, avg, net, ccy, fill['at'], stop, take))
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


def positions(db, account_id=None, open_only: bool = True, mode: str | None = None) -> list[dict]:
    """Positions valued at the live mid: unrealised P&L in the quote currency and in the account's currency.
    `valuation`: OK, NO_MARK (no live price: unrealised unknown, notional at average price) or NO_FX (no conversion)."""
    where = ['TRUE']
    args: list = []
    if account_id:
        where.append('p.account_id=%s'); args.append(str(account_id))
    if mode:
        where.append('p.mode=%s'); args.append(mode)
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
        if not qty:
            unreal = ZERO
        elif mark is not None and r['avg_price'] is not None:
            unreal = (mark - avg) * qty * size
        else:
            unreal = None  # unknown: no live price for an open holding
        rate = fx_rate(db, r['quote_currency'], r['account_currency'])
        notional = abs(qty) * (mark if mark is not None else (avg or ZERO)) * size
        valuation = 'NO_FX' if rate is None else ('NO_MARK' if unreal is None else 'OK')
        r.update({'mark': mark, 'mark_source': q and q['source'], 'mark_at': q and (q.get('received_at') or q.get('source_time')),
                  'mark_received_at': q and q.get('received_at'), 'mark_broker_symbol': q and q.get('broker_symbol'),
                  'unrealised_pnl': unreal, 'notional': notional, 'notional_basis': 'MARK' if mark is not None else 'AVG_PRICE',
                  'fx_to_account': rate, 'valuation': valuation,
                  'unrealised_account': unreal * rate if (rate is not None and unreal is not None) else None,
                  'realised_account': D(r['realised_pnl']) * rate if rate is not None else None,
                  'notional_account': notional * rate if rate is not None else None})
        out.append(r)
    return out


def unvalued(rows: list[dict]) -> list[dict]:
    """The holdings among `rows` whose value is not fully known, with the reason."""
    return [{'account_id': str(p['account_id']), 'instrument_id': str(p['instrument_id']), 'symbol': p['symbol'], 'mode': p['mode'],
             'quantity': str(D(p['quantity'])), 'reason': p['valuation']} for p in rows if p['valuation'] != 'OK']


def capital(db, account_id) -> dict:
    row = db.execute('SELECT * FROM pf_accounts WHERE account_id=%s', (str(account_id),)).fetchone()
    if row:
        return dict(row)
    s = db.execute('SELECT default_capital FROM tr_settings WHERE id').fetchone()
    return {'account_id': str(account_id), 'starting_capital': D(s['default_capital']) if s else Decimal(100000),
            'currency': account_currency(db, account_id), 'peak_equity': None, 'version': 0}


def equity(db, account_id, mode: str | None = None) -> dict:
    """Starting capital + realised + unrealised of one book (mode PAPER or BROKER) or of the whole account (None →
    book 'ALL', for reporting), in the account's currency. Holdings that cannot be valued are listed in `unvalued`
    (and their currencies in `missing_fx`); `complete` is False when any is."""
    cap = capital(db, account_id)
    ccy = cap['currency']
    realised = unreal = ZERO
    missing = set()
    sql = 'SELECT p.realised_pnl, p.currency FROM pf_positions p WHERE p.account_id=%s'
    args: list = [str(account_id)]
    if mode:
        sql += ' AND p.mode=%s'; args.append(mode)
    for r in db.execute(sql, args).fetchall():
        rate = fx_rate(db, r['currency'], ccy)
        if rate is None:
            missing.add(r['currency']); continue
        realised += D(r['realised_pnl']) * rate
    held = positions(db, account_id, mode=mode)
    for p in held:
        if p['valuation'] == 'NO_FX':
            missing.add(p['quote_currency'])
        if p['unrealised_account'] is not None:
            unreal += p['unrealised_account']
    eq = D(cap['starting_capital']) + realised + unreal
    if mode:
        row = db.execute('SELECT max(equity) AS m FROM pf_equity WHERE account_id=%s AND book=%s', (str(account_id), mode)).fetchone()
        peak = max(D(row['m']) if row and row['m'] is not None else eq, eq, D(cap['starting_capital']))
    else:
        peak = max(D(cap['peak_equity']) or eq, eq)
    dd = ((peak - eq) / peak * 100) if peak > 0 else ZERO
    gaps = unvalued(held)
    return {'account_id': str(account_id), 'book': mode or 'ALL', 'currency': ccy, 'starting_capital': D(cap['starting_capital']), 'realised': realised,
            'unrealised': unreal, 'equity': eq, 'peak': peak, 'drawdown_pct': dd.quantize(Decimal('0.01')), 'missing_fx': sorted(missing),
            'unvalued': gaps, 'complete': not gaps and not missing}


def broker_money(db, account_id, max_age_minutes: int = 15) -> dict | None:
    """The broker's own balance, equity and margin from the latest good book snapshot (Decimals), if recent."""
    row = db.execute('SELECT money, at FROM pt_broker_snapshots WHERE account_id=%s AND ok ORDER BY at DESC LIMIT 1', (str(account_id),)).fetchone()
    if not row or row['at'] < datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes):
        return None
    return {**{k: D(v) for k, v in (row['money'] or {}).items() if v not in (None, '')}, 'at': row['at']}


def partition_equity(db, partition_id) -> dict:
    """A capital partition: its capital plus the realised and unrealised P&L of the deployments assigned to it."""
    part = db.execute('SELECT p.*, a.base_currency FROM pf_partitions p JOIN cfg_accounts a ON a.id=p.account_id WHERE p.id=%s', (str(partition_id),)).fetchone()
    if not part:
        return {}
    ccy = part['base_currency'] or 'USD'
    deps = [str(r['id']) for r in db.execute('SELECT id FROM cfg_deployments WHERE partition_id=%s', (str(partition_id),)).fetchall()]
    realised = unreal = ZERO
    if deps:
        for r in db.execute('SELECT realised_pnl, currency FROM pf_positions WHERE deployment_id = ANY(%s::uuid[])', (deps,)).fetchall():
            realised += D(r['realised_pnl']) * (fx_rate(db, r['currency'], ccy) or ZERO)
        for p in positions(db, part['account_id']):
            if p['deployment_id'] and str(p['deployment_id']) in deps and p['unrealised_account'] is not None:
                unreal += p['unrealised_account']
    eq = D(part['capital']) + realised + unreal
    return {'partition_id': str(partition_id), 'name': part['name'], 'currency': ccy, 'capital': D(part['capital']), 'realised': realised,
            'unrealised': unreal, 'equity': eq, 'drawdown_pct': ((D(part['capital']) - eq) / D(part['capital']) * 100).quantize(Decimal('0.01'))
            if D(part['capital']) > 0 and eq < D(part['capital']) else ZERO, 'deployments': len(deps), 'status': part['status'],
            'max_drawdown_pct': part['max_drawdown_pct']}


def sizing_equity(db, dep: dict) -> dict:
    """The capital a deployment sizes from, with its basis and state (never a silent substitute):
    - a capital partition: its equity ('PARTITION');
    - a real (BROKER) deployment: the broker's own equity from a recent book ('BROKER_EQUITY'). Zero, missing, old or
      margin-only replies are reported as such and give no usable figure — the BROKER book is never sized from the
      paper record;
    - a PAPER deployment: the account's paper book ('INTERNAL_BOOK').
    Returns {equity (or None), currency, basis, kind, state, as_of}."""
    ccy = account_currency(db, dep['account_id'])
    if dep.get('partition_id'):
        pe = partition_equity(db, dep['partition_id'])
        if pe:
            return {**pe, 'basis': f"partition {pe['name']}", 'kind': 'PARTITION', 'state': 'PRESENT', 'as_of': None}
    if dep.get('mode') == 'BROKER':
        row = db.execute('SELECT money, at FROM pt_broker_snapshots WHERE account_id=%s AND ok ORDER BY at DESC LIMIT 1',
                         (str(dep['account_id']),)).fetchone()
        money = (row['money'] or {}) if row else {}
        raw = money.get('equity')
        state, value = 'MISSING', None
        if row and raw not in (None, ''):
            value = D(raw)
            if row['at'] < datetime.now(timezone.utc) - timedelta(minutes=15):
                state, value = 'STALE', None
            elif value <= 0:
                state, value = 'ZERO', None
            else:
                state = 'PRESENT'
        elif row and any(money.get(k) not in (None, '') for k in ('balance', 'margin', 'margin_free')):
            state = 'MARGIN_ONLY'
        return {'equity': value, 'currency': ccy, 'basis': 'broker equity', 'kind': 'BROKER_EQUITY', 'state': state,
                'as_of': row['at'] if row else None}
    return {**equity(db, dep['account_id'], 'PAPER'), 'basis': 'paper book equity', 'kind': 'INTERNAL_BOOK', 'state': 'PRESENT', 'as_of': None}


def day_pnl(db, account_id, day_start: datetime | None = None, mode: str | None = None) -> Decimal:
    """Today's P&L of one book (or the whole account when mode is None), in the account's currency: realised on
    today's fills plus current unrealised (UTC day). Holdings with unknown value are left out — callers that decide
    on this figure check `equity(...)['complete']` first."""
    start = day_start or datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    ccy = account_currency(db, account_id)
    total = ZERO
    sql = 'SELECT currency, sum(realised_pnl) AS r FROM oms_fills WHERE account_id=%s AND at >= %s'
    args: list = [str(account_id), start]
    if mode:
        sql += ' AND mode=%s'; args.append(mode)
    for r in db.execute(sql + ' GROUP BY currency', args).fetchall():
        rate = fx_rate(db, r['currency'], ccy)
        if rate is not None:
            total += D(r['r']) * rate
    for p in positions(db, account_id, mode=mode):
        if p['unrealised_account'] is not None:
            total += p['unrealised_account']
    return total


def exposure(db, account_id=None, mode: str | None = None) -> dict:
    """Gross and net notional per instrument and per account (account currency), for one book or all. Holdings that
    cannot be converted are not added to the totals and are listed in `unvalued`."""
    by_instrument: dict = {}
    by_account: dict = {}
    held = positions(db, account_id, mode=mode)
    for p in held:
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
    return {'book': mode or 'ALL', 'instruments': list(by_instrument.values()), 'accounts': list(by_account.values()),
            'unvalued': [u for u in unvalued(held) if u['reason'] == 'NO_FX']}


def snapshot(db, at: datetime | None = None) -> int:
    """Equity of every account with positions or fills, stored once a minute — account-wide ('ALL') and per book —
    and raises the account-wide peak for drawdown."""
    at = (at or datetime.now(timezone.utc)).replace(second=0, microsecond=0)
    ids = [r['account_id'] for r in db.execute('SELECT DISTINCT account_id FROM pf_positions').fetchall()]
    for aid in ids:
        modes = [r['mode'] for r in db.execute('SELECT DISTINCT mode FROM pf_positions WHERE account_id=%s', (str(aid),)).fetchall()]
        e = equity(db, aid)
        for book, fig in [('ALL', e)] + [(m, equity(db, aid, m)) for m in modes]:
            db.execute('INSERT INTO pf_equity(account_id, book, at, equity, realised, unrealised, currency) VALUES (%s,%s,%s,%s,%s,%s,%s) '
                       'ON CONFLICT (account_id, book, at) DO UPDATE SET equity=EXCLUDED.equity, realised=EXCLUDED.realised, unrealised=EXCLUDED.unrealised',
                       (str(aid), book, at, fig['equity'].quantize(Decimal('0.01')), fig['realised'].quantize(Decimal('0.01')),
                        fig['unrealised'].quantize(Decimal('0.01')), fig['currency']))
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
    rows = db.execute("""SELECT f.deployment_id, f.account_id, f.instrument_id, f.currency, f.mode, count(*) AS fills,
                                sum(f.realised_pnl) AS realised, count(*) FILTER (WHERE f.realised_pnl > 0) AS wins,
                                count(*) FILTER (WHERE f.realised_pnl < 0) AS losses,
                                sum(f.realised_pnl) FILTER (WHERE f.realised_pnl > 0) AS gross_win,
                                sum(f.realised_pnl) FILTER (WHERE f.realised_pnl < 0) AS gross_loss
                         FROM oms_fills f WHERE f.at >= %s GROUP BY 1, 2, 3, 4, 5 ORDER BY 2, 3, 1, 5, 4""", (since,)).fetchall()
    open_pos = {(str(p['deployment_id']) if p['deployment_id'] else None, str(p['account_id']), str(p['instrument_id']), p['mode']): p for p in positions(db)}
    names = {str(r['id']): r for r in db.execute('SELECT d.id, s.code AS strategy, v.number AS version FROM cfg_deployments d '
                                                 'JOIN cfg_strategies s ON s.id=d.strategy_id JOIN cfg_strategy_versions v ON v.id=d.strategy_version_id').fetchall()}
    syms = {str(r['id']): r['symbol'] for r in db.execute('SELECT id, symbol FROM cfg_instruments').fetchall()}
    accts = {str(r['id']): r['code'] for r in db.execute('SELECT id, code FROM cfg_accounts').fetchall()}
    out = []
    for r in rows:
        dep = str(r['deployment_id']) if r['deployment_id'] else None
        p = open_pos.get((dep, str(r['account_id']), str(r['instrument_id']), r['mode']))
        closed = (r['wins'] or 0) + (r['losses'] or 0)
        gw, gl = D(r['gross_win']) or ZERO, D(r['gross_loss']) or ZERO
        out.append({'deployment_id': dep, 'strategy': names.get(dep, {}).get('strategy') if dep else 'Manual', 'version': names.get(dep, {}).get('version') if dep else None,
                    'mode': r['mode'], 'account_id': str(r['account_id']), 'account_code': accts.get(str(r['account_id'])),
                    'instrument_id': str(r['instrument_id']), 'symbol': syms.get(str(r['instrument_id'])), 'currency': r['currency'],
                    'fills': r['fills'], 'realised': D(r['realised']) or ZERO, 'closing_fills': closed,
                    'win_rate_pct': round(100 * (r['wins'] or 0) / closed, 1) if closed else None,
                    'profit_factor': round(float(gw / -gl), 2) if gl < 0 else None,
                    'open_qty': D(p['quantity']) if p else ZERO, 'unrealised': p['unrealised_pnl'] if p else ZERO,
                    'valuation': p['valuation'] if p else 'OK'})
    return out
