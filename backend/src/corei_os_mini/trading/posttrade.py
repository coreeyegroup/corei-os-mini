"""Post-trade (Stage 8): does our record match the broker's, what did trading cost, and what exactly happened.

Why it exists: an order engine's own record is not proof. The broker's record (deals, positions, money) is kept beside
COREI's, every difference is raised as a break and stays visible until a person accepts or clears it (an accepted break is not raised again while its values are the same), and the cost and
speed of execution are measured from the fills themselves.
Purpose:
  book snapshots  the broker's money, positions and working orders, and its deals (once per ticket, matched to COREI
                  orders by tag) — from the MT5 bridge's book, or Upstox's positions, funds and trades;
  reconciliation  per account: broker net positions (COREI's magic number on MT5) against COREI's real (BROKER)
                  positions → POSITION_MISMATCH, MISSING_AT_BROKER, NOT_IN_COREI; broker deals of COREI orders with no
                  COREI fill → FILL_MISSING; deals placed outside COREI → EXTERNAL_TRADE; and, for every account,
                  COREI's positions against the sum of its own fills → INTERNAL_POSITION. A break stays OPEN until the
                  next run matches (cleared, recorded) or a person accepts or resolves it; optionally the account's
                  kill switch is engaged on a position break.
  TCA             slippage against the arrival price (the side's price when the order was decided), in price, basis
                  points and money; latency submit → fill; fill rate and reject rate; commission — by account,
                  broker, instrument, strategy, hour, side, mode or adapter.
  journal         round trips (flat → flat) per book with entry, exit, P&L, costs, duration and the price's best and
                  worst excursion while open.
  statements      per account and day: fills, quantity, turnover, realised P&L, costs, COREI equity and the broker's.
  audit trail     every order event, searchable, and CSV for each report.
Must never: change a position or an order to make a break disappear (a person adopts or applies with a reason, which
goes through the order engine's own records), or hide a break.
Dependencies: trading.brokers, trading.oms, trading.portfolio, trading.risk, trading.common, config_store.
Consumers: trading.execution (book snapshots during look-ups, the periodic run), trading_api.
Architectural position: Stage 8, after the portfolio.
"""
from __future__ import annotations

import csv
import io
import json
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from .. import config_store as store
from . import brokers, oms, portfolio, risk
from .common import D, ZERO, fx_rate, instrument, settings, trading_symbol

log = logging.getLogger('corei.posttrade')
POSITION_KINDS = ('POSITION_MISMATCH', 'MISSING_AT_BROKER', 'NOT_IN_COREI')


def _dec(v) -> Decimal:
    try:
        return Decimal(str(v)) if v not in (None, '') else ZERO
    except (InvalidOperation, ValueError):
        return ZERO


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- the broker's book ----------

def store_book(account_id, reply: dict, source: str = 'MT5_BRIDGE') -> int:
    """Keep a book reply: one snapshot row, and each deal once (matched to the COREI order by its tag)."""
    with store.transaction('write') as db:
        db.execute('INSERT INTO pt_broker_snapshots(account_id, ok, money, positions, orders, source) VALUES (%s,true,%s,%s,%s,%s)',
                   (str(account_id), Jsonb(reply.get('money') or {}), Jsonb(reply.get('positions') or []), Jsonb(reply.get('orders') or []), source))
        tags = {r['broker_tag']: r['id'] for r in db.execute('SELECT id, broker_tag FROM oms_orders WHERE account_id=%s AND broker_tag IS NOT NULL '
                                                             "AND created_at > now() - interval '120 days'", (str(account_id),)).fetchall()}
        n = 0
        deals = list(reply.get('deals') or []) + list((reply.get('tagged') or {}).get('deals') or [])
        for d in deals:
            comment = str(d.get('comment') or '')
            oid = next((tags[t] for t in tags if comment.startswith(t)), None)
            try:
                at = datetime.fromisoformat(str(d.get('time'))).replace(tzinfo=timezone.utc) if d.get('time') else _now()
            except ValueError:
                at = _now()
            cur = db.execute("""INSERT INTO pt_broker_deals(account_id, ticket, order_ticket, position_id, symbol, side, entry, volume, price, commission, swap, fee,
                                profit, at, comment, magic, order_id, reason) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                ON CONFLICT (account_id, ticket) DO UPDATE SET order_id=COALESCE(pt_broker_deals.order_id, EXCLUDED.order_id)""",
                             (str(account_id), str(d.get('ticket')), str(d.get('order') or ''), str(d.get('position_id') or ''), str(d.get('symbol') or ''),
                              d.get('side') or '?', d.get('entry'), _dec(d.get('volume')), _dec(d.get('price')), _dec(d.get('commission')),
                              _dec(d.get('swap')), _dec(d.get('fee')), _dec(d.get('profit')), at, comment[:64], d.get('magic'), oid, d.get('reason')))
            n += cur.rowcount
        book_broker_exits(db, account_id)
        db.execute("DELETE FROM pt_broker_snapshots WHERE at < now() - interval '90 days'")
        return n


def book_broker_exits(db, account_id) -> int:
    """Exits the broker made itself on COREI's positions — stop-loss, take-profit, stop-out — are booked as fills of
    the position's own strategy (found through the deal that opened that broker position), so COREI's position closes
    when the broker's does. Deals that cannot be traced to a COREI position stay for reconciliation."""
    magic = int(settings(db).get('broker_magic') or 0)
    rows = db.execute("""SELECT d.* FROM pt_broker_deals d WHERE d.account_id=%s AND d.order_id IS NULL AND d.magic=%s
                           AND d.entry IN ('OUT', 'OUT_BY', 'INOUT') AND d.at > now() - interval '7 days'
                           AND NOT EXISTS (SELECT 1 FROM oms_fills f WHERE f.account_id=d.account_id AND f.broker_deal_id=d.ticket)
                         ORDER BY d.at""", (str(account_id), magic)).fetchall()
    n = 0
    for d in rows:
        opener = db.execute("""SELECT o.deployment_id, o.instrument_id FROM pt_broker_deals x JOIN oms_orders o ON o.id=x.order_id
                               WHERE x.account_id=%s AND x.position_id=%s AND x.entry='IN' AND x.order_id IS NOT NULL LIMIT 1""",
                            (str(account_id), d['position_id'])).fetchone()
        if not opener:
            continue
        held = D(db.execute('SELECT COALESCE(sum(quantity), 0) AS q FROM pf_positions WHERE account_id=%s AND instrument_id=%s AND mode=%s '
                            'AND deployment_id IS NOT DISTINCT FROM %s', (str(account_id), str(opener['instrument_id']), 'BROKER',
                                                                          opener['deployment_id'])).fetchone()['q'])
        qty = min(D(d['volume']), abs(held))
        if qty <= 0 or (d['side'] == 'SELL') != (held > 0):
            continue
        _book_fill(db, 'broker', account_id, opener['instrument_id'], opener['deployment_id'], d['side'], qty, D(d['price']), d['ticket'],
                   {'broker_exit': d['reason'] or 'broker', 'ticket': d['ticket'], 'sent': False}, 'PROTECT')
        n += 1
    return n


def _book_fill(db, actor: str, account_id, instrument_id, deployment_id, side: str, qty: Decimal, price: Decimal, deal_id, detail: dict, source: str) -> dict:
    """A fill the broker made without a COREI order (a broker-side exit, an adopted position): an order record that was
    never sent, then its fill — so the order history and the position stay complete."""
    cid = oms.client_order_id(f"{source.lower()}:{account_id}:{deal_id or uuid4()}")
    o = dict(db.execute("""INSERT INTO oms_orders(id, client_order_id, account_id, instrument_id, deployment_id, broker_symbol, side, order_type, quantity, mode,
                           state, source, created_by, adapter, reduce_only) VALUES (%s,%s,%s,%s,%s,%s,%s,'MARKET',%s,'BROKER','NEW',%s,%s,'BROKER_SIDE',%s) RETURNING *""",
                        (uuid4(), cid, str(account_id), str(instrument_id), str(deployment_id) if deployment_id else None,
                         trading_symbol(db, account_id, instrument_id), side, qty, source, actor, source == 'PROTECT')).fetchone())
    db.execute("INSERT INTO oms_order_events(order_id, state_from, state_to, detail, actor) VALUES (%s,NULL,'NEW',%s,%s)", (str(o['id']), json.dumps(detail), actor))
    for to in ('APPROVED', 'SUBMITTED', 'ACKNOWLEDGED'):
        o = oms._move(db, o, to, actor, detail)
    return store.clean(oms._fill(db, o, qty, price, None, actor, _now(), deal_id=str(deal_id) if deal_id else None, detail=detail))


def fetch_book(account_id, actor: str) -> dict:
    """The broker's book now, normalised to the bridge's shape; stored."""
    with store.transaction('read') as db:
        cap = brokers.capability(db, account_id)
    adapter = cap.get('wanted')
    try:
        if adapter == 'MT5_BRIDGE':
            reply = brokers.mt5_call(account_id, actor, 'book', {'days': 3}, 'Read positions and deals for reconciliation', 60)
        elif adapter == 'UPSTOX':
            up = brokers.Upstox(account_id, actor, 'Read positions and trades for reconciliation')
            funds = up.funds() or {}
            eq = (funds.get('equity') or {}) if isinstance(funds, dict) else {}
            reply = {'money': {'balance': str(eq.get('available_margin') or ''), 'margin': str(eq.get('used_margin') or ''),
                               'margin_free': str(eq.get('available_margin') or '')},
                     'positions': [{'symbol': p.get('instrument_token'), 'side': 'BUY' if int(p.get('quantity') or 0) >= 0 else 'SELL',
                                    'volume': str(abs(int(p.get('quantity') or 0))), 'price_open': str(p.get('average_price') or 0),
                                    'profit': str(p.get('pnl') or 0), 'magic': None} for p in up.positions() if int(p.get('quantity') or 0)],
                     'orders': [], 'deals': [{'ticket': t.get('trade_id'), 'order': t.get('order_id'), 'symbol': t.get('instrument_token'),
                                              'side': str(t.get('transaction_type') or '').upper(), 'volume': str(t.get('quantity')),
                                              'price': str(t.get('average_price')), 'time': t.get('exchange_timestamp'), 'comment': t.get('tag') or ''}
                                             for t in up.day_trades()]}
        else:
            raise HTTPException(409, 'This account has no broker connection for orders: ' + '; '.join(cap['reasons']))
    except (brokers.NotSent, brokers.Lost, brokers.Refused) as exc:
        with store.transaction('write') as db:
            db.execute('INSERT INTO pt_broker_snapshots(account_id, ok, error, source) VALUES (%s,false,%s,%s)', (str(account_id), str(exc)[:500], adapter))
        raise HTTPException(502, f'The broker could not be read: {exc}') from None
    store_book(account_id, reply, adapter)
    return reply


# ---------- reconciliation ----------

def _raise(db, account_id, kind: str, key: str, seen: set, **fields) -> bool:
    """Open (or refresh) one break; True when it is new."""
    seen.add((kind, key))
    row = db.execute("SELECT id FROM pt_breaks WHERE account_id=%s AND kind=%s AND key=%s AND state='OPEN'", (str(account_id), kind, key)).fetchone()
    vals = {k: (format(v.normalize(), 'f') if isinstance(v, Decimal) else v) for k, v in fields.items()}
    if not row:  # accepted by a person with these same values: known, not raised again (a changed value is a new break)
        known = db.execute("SELECT id FROM pt_breaks WHERE account_id=%s AND kind=%s AND key=%s AND state='ACCEPTED' "
                           'AND corei_value IS NOT DISTINCT FROM %s AND broker_value IS NOT DISTINCT FROM %s ORDER BY resolved_at DESC LIMIT 1',
                           (str(account_id), kind, key, vals.get('corei_value'), vals.get('broker_value'))).fetchone()
        if known:
            db.execute('UPDATE pt_breaks SET last_seen=now() WHERE id=%s', (known['id'],))
            return False
    if row:
        db.execute('UPDATE pt_breaks SET last_seen=now(), corei_value=%s, broker_value=%s, difference=%s, detail=%s WHERE id=%s',
                   (vals.get('corei_value'), vals.get('broker_value'), vals.get('difference'), Jsonb(vals.get('detail') or {}), row['id']))
        return False
    db.execute('INSERT INTO pt_breaks(id, account_id, kind, key, instrument_id, broker_symbol, corei_value, broker_value, difference, detail) '
               'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
               (uuid4(), str(account_id), kind, key, vals.get('instrument_id'), vals.get('broker_symbol'), vals.get('corei_value'), vals.get('broker_value'),
                vals.get('difference'), Jsonb(vals.get('detail') or {})))
    return True


def _internal(db, account_id, seen: set) -> tuple[int, list[str]]:
    """COREI's positions against the sum of COREI's own fills (every mode)."""
    new, checked = [], 0
    rows = db.execute("""SELECT p.instrument_id, p.deployment_id, p.mode, p.quantity,
                                COALESCE((SELECT sum(CASE f.side WHEN 'BUY' THEN f.quantity ELSE -f.quantity END) FROM oms_fills f
                                          WHERE f.account_id=p.account_id AND f.instrument_id=p.instrument_id AND f.mode=p.mode
                                            AND f.deployment_id IS NOT DISTINCT FROM p.deployment_id), 0) AS from_fills, i.symbol
                         FROM pf_positions p JOIN cfg_instruments i ON i.id=p.instrument_id WHERE p.account_id=%s""", (str(account_id),)).fetchall()
    for r in rows:
        checked += 1
        if D(r['quantity']) != D(r['from_fills']):
            key = f"{r['symbol']}:{r['mode']}:{r['deployment_id'] or 'manual'}"
            if _raise(db, account_id, 'INTERNAL_POSITION', key, seen, instrument_id=str(r['instrument_id']), corei_value=D(r['quantity']),
                      broker_value=D(r['from_fills']), difference=D(r['quantity']) - D(r['from_fills']), detail={'note': 'position row vs its fills'}):
                new.append(f'INTERNAL_POSITION {key}')
    return checked, new


def reconcile(account_id, actor: str = 'reconcile', broker: bool | None = None) -> dict:
    """One reconciliation run for an account. broker=None: against the broker when it takes real orders."""
    with store.transaction('read') as db:
        cap = brokers.capability(db, account_id)
        cfg = settings(db)
    if broker is None:  # against the broker only when the account trades for real; a paper-only account checks its own records
        with store.transaction('read') as db:
            real = db.execute("""SELECT EXISTS (SELECT 1 FROM oms_orders WHERE account_id=%s AND mode='BROKER' AND created_at > now() - interval '30 days')
                                     OR EXISTS (SELECT 1 FROM pf_positions WHERE account_id=%s AND mode='BROKER' AND quantity <> 0)
                                     OR EXISTS (SELECT 1 FROM cfg_deployments WHERE account_id=%s AND mode='BROKER' AND status='ACTIVE') AS r""",
                              (str(account_id),) * 3).fetchone()['r']
        broker = real
    use_broker = bool(cap.get('wanted')) and broker
    book, error = None, None
    if use_broker:
        try:
            book = fetch_book(account_id, actor)
        except HTTPException as exc:
            error = str(exc.detail)
    seen: set = set()
    new: list[str] = []
    checked = 0
    tol = D(cfg.get('recon_qty_tolerance')) or ZERO
    with store.transaction('write') as db:
        c, n = _internal(db, account_id, seen)
        checked += c
        new += n
        detail: dict = {'adapter': cap.get('wanted')}
        if book is not None:
            magic = int(cfg.get('broker_magic') or 0)
            ours: dict = {}
            external: dict = {}
            for p in book.get('positions') or []:
                v = _dec(p.get('volume')) * (1 if p.get('side') == 'BUY' else -1)
                target = ours if (p.get('magic') is None or int(p.get('magic') or 0) == magic) else external
                target[p['symbol']] = target.get(p['symbol'], ZERO) + v
            corei: dict = {}
            for r in db.execute("SELECT instrument_id, sum(quantity) AS q FROM pf_positions WHERE account_id=%s AND mode='BROKER' GROUP BY 1",
                                (str(account_id),)).fetchall():
                sym = trading_symbol(db, account_id, r['instrument_id']) or f"instrument {r['instrument_id']}"
                corei[sym] = (r['instrument_id'], D(r['q']))
            for sym in sorted(set(ours) | set(corei)):
                checked += 1
                iid, cq = corei.get(sym, (None, ZERO))
                bq = ours.get(sym, ZERO)
                if abs(cq - bq) <= tol:
                    continue
                kind = 'MISSING_AT_BROKER' if bq == 0 else 'NOT_IN_COREI' if cq == 0 else 'POSITION_MISMATCH'
                if _raise(db, account_id, kind, sym, seen, instrument_id=str(iid) if iid else None, broker_symbol=sym, corei_value=cq, broker_value=bq,
                          difference=cq - bq, detail={'magic': magic}):
                    new.append(f'{kind} {sym}')
            for sym, q in external.items():
                checked += 1
                if q != 0 and _raise(db, account_id, 'EXTERNAL_TRADE', f'position:{sym}', seen, broker_symbol=sym, broker_value=q,
                                     detail={'note': 'position opened outside COREI (another magic number or by hand)'}):
                    new.append(f'EXTERNAL_TRADE position {sym}')
            since = _now() - timedelta(days=3)
            for d in db.execute("""SELECT bd.*, f.id AS fill_id FROM pt_broker_deals bd LEFT JOIN oms_fills f ON f.account_id=bd.account_id AND f.broker_deal_id=bd.ticket
                                   WHERE bd.account_id=%s AND bd.at > %s""", (str(account_id), since)).fetchall():
                checked += 1
                if d['order_id'] and not d['fill_id']:
                    if _raise(db, account_id, 'FILL_MISSING', f"deal:{d['ticket']}", seen, broker_symbol=d['symbol'], broker_value=f"{d['side']} {d['volume']} @ {d['price']}",
                              detail={'order_id': str(d['order_id']), 'ticket': d['ticket']}):
                        new.append(f"FILL_MISSING deal {d['ticket']}")
                elif not d['order_id'] and (d['magic'] is None or int(d['magic'] or 0) != magic):
                    if _raise(db, account_id, 'EXTERNAL_TRADE', f"deal:{d['ticket']}", seen, broker_symbol=d['symbol'],
                              broker_value=f"{d['side']} {d['volume']} @ {d['price']}", detail={'ticket': d['ticket'], 'magic': d['magic']}):
                        new.append(f"EXTERNAL_TRADE deal {d['ticket']}")
            detail['broker_positions'] = len(book.get('positions') or [])
            detail['money'] = book.get('money')
        # Breaks this run no longer sees are cleared (recorded, not deleted). Broker kinds only when the broker was read.
        kinds = ['INTERNAL_POSITION'] + ([*POSITION_KINDS, 'FILL_MISSING', 'EXTERNAL_TRADE'] if book is not None else [])
        for b in db.execute("SELECT id, kind, key FROM pt_breaks WHERE account_id=%s AND state='OPEN' AND kind = ANY(%s)", (str(account_id), kinds)).fetchall():
            if (b['kind'], b['key']) not in seen:
                db.execute("UPDATE pt_breaks SET state='RESOLVED', resolved_by=%s, resolved_at=now(), resolution='cleared: matched at the next run' WHERE id=%s",
                           (actor, b['id']))
        open_n = db.execute("SELECT count(*) AS n FROM pt_breaks WHERE account_id=%s AND state='OPEN'", (str(account_id),)).fetchone()['n']
        status = 'FAILED' if error else ('BREAKS' if open_n else 'MATCHED')
        run_id = uuid4()
        db.execute('INSERT INTO pt_recon_runs(id, account_id, kind, status, checked, breaks, detail, actor) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                   (run_id, str(account_id), 'BROKER' if use_broker else 'INTERNAL', status, checked, open_n,
                    Jsonb({**detail, 'error': error, 'new': new}), actor))
        if new:
            code = db.execute('SELECT code FROM cfg_accounts WHERE id=%s', (str(account_id),)).fetchone()['code']
            oms._alert(db, 'CRITICAL' if any(k.split()[0] in POSITION_KINDS for k in new) else 'WARNING', f'Reconciliation breaks on {code}',
                       '; '.join(new)[:1500])
            if cfg.get('recon_kill_on_break') and any(k.split()[0] in POSITION_KINDS for k in new):
                risk.engage('reconcile', risk.KillIn(scope='ACCOUNT', target_id=UUID(str(account_id)),
                                                     reason=f'Reconciliation break: {new[0]}'[:500]), automatic=True, db=db)
    return {'id': str(run_id), 'status': status, 'checked': checked, 'open_breaks': open_n, 'new': new, 'error': error}


def _active_accounts(db) -> list[str]:
    rows = db.execute("""SELECT DISTINCT account_id FROM (
                            SELECT account_id FROM oms_orders WHERE mode='BROKER' AND created_at > now() - interval '30 days'
                            UNION SELECT account_id FROM cfg_deployments WHERE mode='BROKER' AND status='ACTIVE'
                            UNION SELECT account_id FROM pf_positions WHERE quantity <> 0) x""").fetchall()
    return [str(r['account_id']) for r in rows]


def reconcile_due() -> int:
    with store.transaction('read') as db:
        every = int(settings(db).get('recon_interval_minutes') or 5)
        last = {str(r['account_id']): r['at'] for r in db.execute('SELECT account_id, max(at) AS at FROM pt_recon_runs GROUP BY 1').fetchall()}
        ids = _active_accounts(db)
    n = 0
    for aid in ids:
        if last.get(aid) and last[aid] > _now() - timedelta(minutes=every):
            continue
        try:
            reconcile(aid)
            n += 1
        except Exception:  # noqa: BLE001
            log.exception('reconciliation failed for %s', aid)
    return n


class BreakAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['ACCEPT', 'RESOLVE', 'APPLY_FILL', 'ADOPT_POSITION']
    note: str = Field(min_length=5, max_length=500)


def act_on_break(actor: str, break_id: UUID, data: BreakAction) -> dict:
    """A person's decision on a break: accept it as known, mark it resolved, apply a missing broker fill to its COREI
    order, or adopt a broker position into COREI's book (a RECONCILE order, never sent). Audited."""
    with store.transaction('write') as db:
        b = db.execute('SELECT * FROM pt_breaks WHERE id=%s FOR UPDATE', (str(break_id),)).fetchone()
        if not b:
            raise HTTPException(404, 'Break not found.')
        b = dict(b)
        if b['state'] != 'OPEN':
            raise HTTPException(409, 'This break is already closed.')
        result = None
        if data.action == 'APPLY_FILL':
            if b['kind'] != 'FILL_MISSING':
                raise HTTPException(409, 'Only a missing fill can be applied.')
            d = db.execute('SELECT * FROM pt_broker_deals WHERE account_id=%s AND ticket=%s', (str(b['account_id']), b['detail']['ticket'])).fetchone()
            o = db.execute('SELECT * FROM oms_orders WHERE id=%s FOR UPDATE', (b['detail']['order_id'],)).fetchone()
            if not d or not o:
                raise HTTPException(409, 'The deal or the order is no longer there.')
            o = dict(o)
            if o['state'] in ('SUBMITTED',):
                o = oms._move(db, o, 'UNKNOWN', actor, {'reason': 'a broker deal exists for this order'})
            if o['state'] not in ('UNKNOWN', 'ACKNOWLEDGED', 'PARTIALLY_FILLED', 'CANCEL_PENDING'):
                raise HTTPException(409, f"The order is {o['state'].lower()}: the deal cannot be applied to it; resolve the break by hand.")
            result = store.clean(oms._fill(db, o, min(D(d['volume']), D(o['quantity']) - D(o['filled_qty'])), D(d['price']), None, actor, _now(),
                                           deal_id=d['ticket'], detail={'applied_from_reconciliation': True}))
        elif data.action == 'ADOPT_POSITION':
            if b['kind'] not in ('NOT_IN_COREI', 'POSITION_MISMATCH') or not b['instrument_id'] and not b['broker_symbol']:
                raise HTTPException(409, 'Only a broker position COREI does not hold can be adopted.')
            result = _adopt(db, actor, b, data.note)
        state = 'ACCEPTED' if data.action == 'ACCEPT' else 'RESOLVED'
        after = db.execute('UPDATE pt_breaks SET state=%s, resolved_by=%s, resolved_at=now(), resolution=%s WHERE id=%s RETURNING *',
                           (state, actor, f'{data.action.lower()}: {data.note}', str(break_id))).fetchone()
        store.audit(db, actor, f'BREAK_{data.action}', 'break', break_id, store.clean(b), store.clean(after), data.note)
        return {'break': store.clean(after), 'result': result}


def _adopt(db, actor: str, b: dict, note: str) -> dict:
    iid = b['instrument_id']
    if not iid:
        m = db.execute("""SELECT m.instrument_id FROM cfg_symbol_mappings m JOIN cfg_accounts a ON a.connection_id=m.connection_id
                          WHERE a.id=%s AND m.broker_symbol=%s AND m.status='ACTIVE' LIMIT 1""", (str(b['account_id']), b['broker_symbol'])).fetchone()
        if not m:
            raise HTTPException(409, f"{b['broker_symbol']} is not mapped to a COREI instrument (Setup › Instruments).")
        iid = m['instrument_id']
    diff = D(b['broker_value']) - D(b['corei_value'] or 0)
    if diff == 0:
        raise HTTPException(409, 'Nothing to adopt.')
    snap = db.execute('SELECT positions FROM pt_broker_snapshots WHERE account_id=%s AND ok ORDER BY at DESC LIMIT 1', (str(b['account_id']),)).fetchone()
    price = next((_dec(p.get('price_open')) for p in (snap['positions'] if snap else []) if p.get('symbol') == b['broker_symbol']), ZERO)
    if price <= 0:
        raise HTTPException(409, 'No broker price for the position: read the broker book first.')
    return _book_fill(db, actor, b['account_id'], iid, None, 'BUY' if diff > 0 else 'SELL', abs(diff), price, None,
                      {'adopted_from_broker': True, 'note': note, 'sent': False}, 'RECONCILE')


def breaks(db, state: str | None = 'OPEN', account_id=None, limit: int = 500) -> list[dict]:
    where, args = ['TRUE'], []
    if state:
        where.append('b.state=%s'); args.append(state)
    if account_id:
        where.append('b.account_id=%s'); args.append(str(account_id))
    rows = db.execute(f"""SELECT b.*, a.code AS account_code, i.symbol FROM pt_breaks b JOIN cfg_accounts a ON a.id=b.account_id
                          LEFT JOIN cfg_instruments i ON i.id=b.instrument_id WHERE {' AND '.join(where)} ORDER BY b.state='OPEN' DESC, b.last_seen DESC LIMIT %s""",
                      [*args, limit]).fetchall()
    return [store.clean(r) for r in rows]


def runs(db, limit: int = 200) -> list[dict]:
    rows = db.execute('SELECT r.*, a.code AS account_code FROM pt_recon_runs r JOIN cfg_accounts a ON a.id=r.account_id ORDER BY r.at DESC LIMIT %s', (limit,)).fetchall()
    return [store.clean(r) for r in rows]


def broker_view(db, account_id) -> dict:
    snap = db.execute('SELECT * FROM pt_broker_snapshots WHERE account_id=%s ORDER BY at DESC LIMIT 1', (str(account_id),)).fetchone()
    deals = db.execute('SELECT d.*, o.client_order_id FROM pt_broker_deals d LEFT JOIN oms_orders o ON o.id=d.order_id WHERE d.account_id=%s '
                       'ORDER BY d.at DESC LIMIT 300', (str(account_id),)).fetchall()
    return {'snapshot': store.clean(snap) if snap else None, 'deals': [store.clean(d) for d in deals]}


# ---------- transaction cost analysis ----------

TCA_GROUPS = {'account': 'a.code', 'broker': "COALESCE(p.name, c.code)", 'instrument': 'i.symbol', 'strategy': "COALESCE(s.code, 'Manual')",
              'hour': "to_char(f.at AT TIME ZONE 'UTC', 'HH24') || ':00 UTC'", 'side': 'f.side', 'mode': 'f.mode', 'adapter': "COALESCE(o.adapter, 'PAPER')",
              'source': 'o.source', 'day': "to_char(f.at AT TIME ZONE 'UTC', 'YYYY-MM-DD')"}


def tca(db, by: str = 'account', since: datetime | None = None, until: datetime | None = None, mode: str | None = None) -> dict:
    if by not in TCA_GROUPS:
        by = 'account'
    since = since or _now() - timedelta(days=30)
    until = until or _now() + timedelta(minutes=1)
    g = TCA_GROUPS[by]
    mode_sql = ' AND f.mode=%s' if mode else ''
    margs = [mode] if mode else []
    rows = db.execute(f"""SELECT {g} AS label, f.side, f.quantity, f.price, f.slippage, f.arrival_price, f.latency_ms, f.currency, i.contract_size,
                                 a.base_currency, COALESCE(bd.commission, 0) + COALESCE(bd.fee, 0) AS cost
                          FROM oms_fills f JOIN oms_orders o ON o.id=f.order_id JOIN cfg_accounts a ON a.id=f.account_id
                          JOIN cfg_connections c ON c.id=a.connection_id LEFT JOIN cfg_providers p ON p.id=c.provider_id
                          JOIN cfg_instruments i ON i.id=f.instrument_id LEFT JOIN cfg_deployments d ON d.id=f.deployment_id
                          LEFT JOIN cfg_strategies s ON s.id=d.strategy_id
                          LEFT JOIN pt_broker_deals bd ON bd.account_id=f.account_id AND bd.ticket=f.broker_deal_id
                          WHERE f.at >= %s AND f.at < %s{mode_sql}""", [since, until, *margs]).fetchall()
    ccys = [r['base_currency'] for r in rows if r['base_currency']]
    report = max(set(ccys), key=ccys.count) if ccys else 'USD'
    out: dict = {}
    missing = set()
    for r in rows:
        x = out.setdefault(r['label'], {'label': r['label'], 'fills': 0, 'quantity': ZERO, 'slip_n': 0, 'slip_bps_sum': ZERO, 'slip_cost': ZERO,
                                        'lat': [], 'commission': ZERO, 'positive': 0, 'negative': 0})
        x['fills'] += 1
        x['quantity'] += D(r['quantity'])
        rate = fx_rate(db, r['currency'], report)
        if rate is None:
            missing.add(r['currency'])
            rate = None
        if r['slippage'] is not None and r['arrival_price']:
            x['slip_n'] += 1
            x['slip_bps_sum'] += D(r['slippage']) / D(r['arrival_price']) * 10000
            if rate is not None:
                x['slip_cost'] += D(r['slippage']) * D(r['quantity']) * (D(r['contract_size']) or Decimal(1)) * rate
            x['positive' if D(r['slippage']) <= 0 else 'negative'] += 1
        if r['latency_ms'] is not None:
            x['lat'].append(int(r['latency_ms']))
        x['commission'] += D(r['cost'])
    ords = db.execute(f"""SELECT {g.replace('f.', 'o.') if by in ('side', 'mode') else g} AS label, count(*) AS orders,
                                 count(*) FILTER (WHERE o.state='REJECTED') AS rejected, sum(o.quantity) AS ordered, sum(o.filled_qty) AS filled
                          FROM oms_orders o JOIN cfg_accounts a ON a.id=o.account_id JOIN cfg_connections c ON c.id=a.connection_id
                          LEFT JOIN cfg_providers p ON p.id=c.provider_id JOIN cfg_instruments i ON i.id=o.instrument_id
                          LEFT JOIN cfg_deployments d ON d.id=o.deployment_id LEFT JOIN cfg_strategies s ON s.id=d.strategy_id,
                          LATERAL (SELECT o.created_at AS at) f
                          WHERE o.created_at >= %s AND o.created_at < %s{' AND o.mode=%s' if mode else ''} GROUP BY 1""", [since, until, *margs]).fetchall()
    for o in ords:
        x = out.setdefault(o['label'], {'label': o['label'], 'fills': 0, 'quantity': ZERO, 'slip_n': 0, 'slip_bps_sum': ZERO, 'slip_cost': ZERO,
                                        'lat': [], 'commission': ZERO, 'positive': 0, 'negative': 0})
        x.update({'orders': o['orders'], 'rejected': o['rejected'], 'fill_rate_pct': (D(o['filled']) / D(o['ordered']) * 100).quantize(Decimal('0.1'))
                  if D(o['ordered']) else None, 'reject_rate_pct': round(100 * o['rejected'] / o['orders'], 1) if o['orders'] else None})
    result = []
    for x in out.values():
        lat = sorted(x.pop('lat'))
        n = x.pop('slip_n')
        result.append({**x, 'avg_slippage_bps': (x.pop('slip_bps_sum') / n).quantize(Decimal('0.01')) if n else None,
                       'slippage_cost': x.pop('slip_cost').quantize(Decimal('0.01')), 'commission': x['commission'].quantize(Decimal('0.01')),
                       'latency_avg_ms': round(sum(lat) / len(lat)) if lat else None, 'latency_p95_ms': lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else None,
                       'latency_max_ms': lat[-1] if lat else None, 'price_improved': x.pop('positive'), 'worse_than_arrival': x.pop('negative')})
    result.sort(key=lambda r: str(r['label']))
    tot_cost = sum((r['slippage_cost'] for r in result), ZERO)
    return {'by': by, 'currency': report, 'rows': result, 'slippage_cost_total': tot_cost, 'commission_total': sum((r['commission'] for r in result), ZERO),
            'missing_fx': sorted(missing), 'from': since.isoformat(), 'to': until.isoformat(),
            'note': 'Slippage = fill price against the arrival price (the ask to buy, the bid to sell, when the order was decided); positive = worse.'}


# ---------- trade journal ----------

def journal(db, since: datetime | None = None, until: datetime | None = None, account_id=None, excursions: bool = True, limit: int = 500) -> dict:
    """Round trips per book (account, instrument, deployment, mode): opened when flat → not flat, closed when flat again;
    a flip closes the trip and opens the next with the remainder."""
    since = since or _now() - timedelta(days=90)
    until = until or _now() + timedelta(minutes=1)
    args: list = [until]
    acc = ''
    if account_id:
        acc = ' AND f.account_id=%s'; args.append(str(account_id))
    fills = db.execute(f"""SELECT f.*, i.symbol, i.contract_size, a.code AS account_code, s.code AS strategy_code,
                                  COALESCE(bd.commission, 0) + COALESCE(bd.fee, 0) + COALESCE(bd.swap, 0) AS cost
                           FROM oms_fills f JOIN cfg_instruments i ON i.id=f.instrument_id JOIN cfg_accounts a ON a.id=f.account_id
                           LEFT JOIN cfg_deployments d ON d.id=f.deployment_id LEFT JOIN cfg_strategies s ON s.id=d.strategy_id
                           LEFT JOIN pt_broker_deals bd ON bd.account_id=f.account_id AND bd.ticket=f.broker_deal_id
                           WHERE f.at < %s{acc} ORDER BY f.at, f.id""", args).fetchall()
    books: dict = {}
    trips: list = []
    for f in fills:
        k = (str(f['account_id']), str(f['instrument_id']), str(f['deployment_id']), f['mode'])
        b = books.setdefault(k, {'qty': ZERO, 'trip': None})
        signed = D(f['quantity']) if f['side'] == 'BUY' else -D(f['quantity'])
        before = b['qty']
        after = before + signed
        if before == 0:
            b['trip'] = {'account': f['account_code'], 'symbol': f['symbol'], 'strategy': f['strategy_code'] or 'Manual', 'mode': f['mode'],
                         'account_id': str(f['account_id']), 'instrument_id': str(f['instrument_id']), 'side': 'LONG' if signed > 0 else 'SHORT',
                         'opened_at': f['at'], 'entry_qty': ZERO, 'entry_value': ZERO, 'exit_qty': ZERO, 'exit_value': ZERO, 'max_qty': ZERO,
                         'realised': ZERO, 'costs': ZERO, 'fills': 0, 'currency': f['currency']}
        t = b['trip']
        t['fills'] += 1
        t['realised'] += D(f['realised_pnl'])
        t['costs'] += D(f['cost'])
        opening = before == 0 or (before > 0) == (signed > 0)
        if opening:
            t['entry_qty'] += abs(signed); t['entry_value'] += abs(signed) * D(f['price'])
        else:
            closing = min(abs(before), abs(signed))
            t['exit_qty'] += closing; t['exit_value'] += closing * D(f['price'])
            if after != 0 and (after > 0) != (before > 0):  # flipped: the remainder opens a new trip
                t['closed_at'] = f['at']
                trips.append(t)
                b['trip'] = {**t, 'side': 'LONG' if after > 0 else 'SHORT', 'opened_at': f['at'], 'entry_qty': abs(after), 'entry_value': abs(after) * D(f['price']),
                             'exit_qty': ZERO, 'exit_value': ZERO, 'max_qty': abs(after), 'realised': ZERO, 'costs': ZERO, 'fills': 1}
                b['trip'].pop('closed_at', None)
                b['qty'] = after
                continue
        t['max_qty'] = max(t['max_qty'], abs(after))
        b['qty'] = after
        if after == 0:
            t['closed_at'] = f['at']
            trips.append(t)
            b['trip'] = None
    open_trips = [b['trip'] for b in books.values() if b['trip']]
    out = []
    for t in [x for x in trips if x['closed_at'] >= since][-limit:] + open_trips:
        entry = (t['entry_value'] / t['entry_qty']) if t['entry_qty'] else None
        exit_ = (t['exit_value'] / t['exit_qty']) if t['exit_qty'] else None
        row = {**{k: v for k, v in t.items() if k not in ('entry_value', 'exit_value')}, 'avg_entry': entry, 'avg_exit': exit_,
               'net': t['realised'] + t['costs'], 'open': 'closed_at' not in t,
               'duration_minutes': round(((t.get('closed_at') or _now()) - t['opened_at']).total_seconds() / 60, 1)}
        if excursions and entry is not None and len(out) < 300:
            row.update(_excursion(db, t, entry))
        out.append(row)
    closed = [r for r in out if not r['open']]
    wins = [r for r in closed if r['realised'] > 0]
    return {'trips': list(reversed(out)), 'summary': {'closed': len(closed), 'open': len(out) - len(closed), 'wins': len(wins),
                                                      'win_rate_pct': round(100 * len(wins) / len(closed), 1) if closed else None,
                                                      'net': sum((r['net'] for r in closed), ZERO),
                                                      'avg_minutes': round(sum(r['duration_minutes'] for r in closed) / len(closed), 1) if closed else None}}


def _excursion(db, t: dict, entry: Decimal) -> dict:
    """Best and worst price move while the trip was open (M1 bars of the account's price source), in price units."""
    from .common import quote
    q = quote(db, t['account_id'], t['instrument_id'])
    if not q:
        return {}
    r = db.execute("SELECT max(high) AS hi, min(low) AS lo FROM md_bars WHERE account_id=%s AND broker_symbol=%s AND timeframe='M1' AND bar_time BETWEEN %s AND %s",
                   (q['account_id'], q['broker_symbol'], t['opened_at'], t.get('closed_at') or _now())).fetchone()
    if not r or r['hi'] is None:
        return {}
    hi, lo = D(r['hi']), D(r['lo'])
    long_ = t['side'] == 'LONG'
    return {'mfe': (hi - entry) if long_ else (entry - lo), 'mae': (entry - lo) if long_ else (hi - entry)}


# ---------- statements ----------

def statement(db, account_id, day_from: date, day_to: date) -> dict:
    if (day_to - day_from).days > 400:
        raise HTTPException(422, 'At most 400 days per statement.')
    acc = db.execute('SELECT code, base_currency FROM cfg_accounts WHERE id=%s', (str(account_id),)).fetchone()
    if not acc:
        raise HTTPException(404, 'Account not found.')
    start = datetime.combine(day_from, datetime.min.time(), timezone.utc)
    end = datetime.combine(day_to + timedelta(days=1), datetime.min.time(), timezone.utc)
    rows = db.execute("""SELECT (f.at AT TIME ZONE 'UTC')::date AS day, f.mode, f.currency, count(*) AS fills, sum(f.quantity) AS quantity,
                                sum(f.quantity * f.price * COALESCE(i.contract_size, 1)) AS turnover, sum(f.realised_pnl) AS realised,
                                sum(COALESCE(bd.commission, 0) + COALESCE(bd.fee, 0)) AS commission, sum(COALESCE(bd.swap, 0)) AS swap
                         FROM oms_fills f JOIN cfg_instruments i ON i.id=f.instrument_id
                         LEFT JOIN pt_broker_deals bd ON bd.account_id=f.account_id AND bd.ticket=f.broker_deal_id
                         WHERE f.account_id=%s AND f.at >= %s AND f.at < %s GROUP BY 1, 2, 3 ORDER BY 1""", (str(account_id), start, end)).fetchall()
    eq = {r['day']: r for r in db.execute("""SELECT DISTINCT ON ((at AT TIME ZONE 'UTC')::date) (at AT TIME ZONE 'UTC')::date AS day, equity, realised, unrealised
                                              FROM pf_equity WHERE account_id=%s AND at >= %s AND at < %s ORDER BY (at AT TIME ZONE 'UTC')::date, at DESC""",
                                           (str(account_id), start, end)).fetchall()}
    bk = {r['day']: r for r in db.execute("""SELECT DISTINCT ON ((at AT TIME ZONE 'UTC')::date) (at AT TIME ZONE 'UTC')::date AS day, money
                                              FROM pt_broker_snapshots WHERE account_id=%s AND ok AND at >= %s AND at < %s
                                              ORDER BY (at AT TIME ZONE 'UTC')::date, at DESC""", (str(account_id), start, end)).fetchall()}
    days: dict = {}
    missing = set()
    for r in rows:
        d = days.setdefault(r['day'], {'day': r['day'].isoformat(), 'fills': 0, 'quantity': ZERO, 'turnover': ZERO, 'realised': ZERO, 'commission': ZERO,
                                       'swap': ZERO, 'modes': set()})
        rate = fx_rate(db, r['currency'], acc['base_currency'])
        if rate is None:
            missing.add(r['currency']); rate = Decimal(1)
        d['fills'] += r['fills']; d['quantity'] += D(r['quantity']); d['turnover'] += D(r['turnover']) * rate
        d['realised'] += D(r['realised']) * rate; d['commission'] += D(r['commission']); d['swap'] += D(r['swap'])
        d['modes'].add(r['mode'])
    for day in sorted(set(days) | set(eq) | set(bk)):
        d = days.setdefault(day, {'day': day.isoformat(), 'fills': 0, 'quantity': ZERO, 'turnover': ZERO, 'realised': ZERO, 'commission': ZERO,
                                  'swap': ZERO, 'modes': set()})
        if day in eq:
            d.update({'corei_equity': D(eq[day]['equity']), 'unrealised': D(eq[day]['unrealised'])})
        if day in bk:
            m = bk[day]['money'] or {}
            d.update({'broker_balance': m.get('balance'), 'broker_equity': m.get('equity')})
    out = [{**d, 'modes': sorted(d['modes']), 'net': d['realised'] + d['commission'] + d['swap']} for _, d in sorted(days.items())]
    return {'account': acc['code'], 'currency': acc['base_currency'], 'from': day_from.isoformat(), 'to': day_to.isoformat(), 'days': out,
            'totals': {k: sum((x[k] for x in out), ZERO) for k in ('fills', 'quantity', 'turnover', 'realised', 'commission', 'swap', 'net')},
            'missing_fx': sorted(missing), 'note': 'Commission and swap come from the broker\'s deals (MT5); paper fills have none.'}


# ---------- audit trail ----------

def order_events(db, since: datetime | None = None, until: datetime | None = None, account_id=None, state: str | None = None, actor: str | None = None,
                 search: str | None = None, limit: int = 1000) -> list[dict]:
    where, args = ['e.at >= %s', 'e.at < %s'], [since or _now() - timedelta(days=7), until or _now() + timedelta(minutes=1)]
    if account_id:
        where.append('o.account_id=%s'); args.append(str(account_id))
    if state:
        where.append('e.state_to=%s'); args.append(state)
    if actor:
        where.append('e.actor ILIKE %s'); args.append(f'%{actor}%')
    if search:
        where.append('(o.client_order_id ILIKE %s OR o.broker_tag ILIKE %s OR o.broker_order_id ILIKE %s OR o.id::text ILIKE %s)')
        args += [f'%{search}%'] * 4
    rows = db.execute(f"""SELECT e.*, o.client_order_id, o.broker_tag, o.broker_order_id, o.side, o.quantity, o.mode, o.source, a.code AS account_code, i.symbol
                          FROM oms_order_events e JOIN oms_orders o ON o.id=e.order_id JOIN cfg_accounts a ON a.id=o.account_id
                          JOIN cfg_instruments i ON i.id=o.instrument_id WHERE {' AND '.join(where)} ORDER BY e.id DESC LIMIT %s""", [*args, limit]).fetchall()
    return [store.clean(r) for r in rows]


def to_csv(rows: list[dict], columns: list[str] | None = None) -> str:
    buf = io.StringIO()
    if not rows:
        return ''
    cols = columns or [c for c in rows[0].keys() if not isinstance(rows[0][c], (dict, list, set))]
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([(format(v.normalize(), 'f') if isinstance(v, Decimal) else v.isoformat() if isinstance(v, (datetime, date)) else
                     json.dumps(v, default=str) if isinstance(v, (dict, list)) else v) if v is not None else '' for v in (r.get(c) for c in cols)])
    return buf.getvalue()


def overview(db) -> dict:
    open_breaks = db.execute("SELECT kind, count(*) AS n FROM pt_breaks WHERE state='OPEN' GROUP BY 1").fetchall()
    last = db.execute("""SELECT DISTINCT ON (r.account_id) r.account_id, a.code, r.at, r.status, r.breaks, r.kind FROM pt_recon_runs r
                         JOIN cfg_accounts a ON a.id=r.account_id ORDER BY r.account_id, r.at DESC""").fetchall()
    today = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    fills = db.execute('SELECT count(*) AS n, avg(latency_ms) AS lat FROM oms_fills WHERE at >= %s', (today,)).fetchone()
    return {'open_breaks': {r['kind']: r['n'] for r in open_breaks}, 'open_total': sum(r['n'] for r in open_breaks),
            'accounts': [store.clean(r) for r in last], 'fills_today': fills['n'], 'avg_latency_today_ms': round(float(fills['lat'])) if fills['lat'] else None,
            'unknown_orders': db.execute("SELECT count(*) AS n FROM oms_orders WHERE state='UNKNOWN'").fetchone()['n']}
