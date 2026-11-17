"""Smart order routing (Stage 7 · Execution intelligence): which account (broker) an order goes to.

Why it exists: the same instrument is often tradable on several accounts (two MT5 brokers, a demo and a live, an
Indian broker and another). The operator states the rule; COREI applies it on live prices and records why.
Purpose: rank candidate accounts for one order — BEST_PRICE (best ask to buy, best bid to sell; tighter spread breaks
ties), LOWEST_COST (that price plus each account's measured average slippage on this instrument, last 30 days),
PRIORITY (the first eligible account in the order given), SPLIT_EQUAL (the quantity shared across every eligible
account, rounded down to each lot step, the remainder to the best) — and return the legs with the decision record.
Eligible: a fresh price on that account; for real orders an order adapter; no engaged account kill switch; a size
that meets the lot rules. Each leg still passes the full risk check in oms.place.
Must never: send anything (oms and execution do), or pick an account without saying why.
Dependencies: trading.common, trading.risk, trading.brokers. Consumers: oms.ticket.
Architectural position: Stage 7, before the order is created.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException

from . import brokers, risk
from .common import D, ZERO, quote


def _slippage(db, account_id, instrument_id) -> Decimal:
    r = db.execute('SELECT avg(slippage) AS s FROM oms_fills WHERE account_id=%s AND instrument_id=%s AND at > %s AND slippage IS NOT NULL',
                   (str(account_id), str(instrument_id), datetime.now(timezone.utc) - timedelta(days=30))).fetchone()
    return D(r['s']) if r and r['s'] is not None else ZERO


def candidates(db, accounts: list, instrument_id, side: str, quantity: Decimal, mode: str) -> list[dict]:
    out = []
    for aid in dict.fromkeys(str(a) for a in accounts):
        code = db.execute('SELECT code FROM cfg_accounts WHERE id=%s', (aid,)).fetchone()
        c = {'account_id': aid, 'account_code': code['code'] if code else '?', 'eligible': True, 'why': []}
        q = quote(db, aid, instrument_id)
        if not q:
            c['eligible'] = False
            c['why'].append('no live price on this account')
        else:
            c.update({'bid': q['bid'], 'ask': q['ask'], 'spread': q['ask'] - q['bid'], 'source': q['source'],
                      'price': q['ask'] if side == 'BUY' else q['bid'], 'slippage': _slippage(db, aid, instrument_id)})
        if mode == 'BROKER':
            cap = brokers.capability(db, aid)
            if not cap['adapter']:
                c['eligible'] = False
                c['why'] += [r.split(':')[0] for r in cap['reasons']]
        if any(k['scope'] == 'ACCOUNT' for k in risk.engaged(db, {'ACCOUNT': aid})):
            c['eligible'] = False
            c['why'].append('account kill switch engaged')
        _, problem = risk.lot(db, instrument_id, quantity)
        if problem:
            c['eligible'] = False
            c['why'].append(problem.split(':')[0])
        out.append(c)
    return out


def choose(db, rule: str, accounts: list, instrument_id, side: str, quantity: Decimal, mode: str) -> list[tuple]:
    """[(account_id, quantity, decision)] — one leg, or several for SPLIT_EQUAL."""
    if len(accounts) < 1:
        raise HTTPException(422, 'Routing needs the accounts it may choose from.')
    cands = candidates(db, accounts, instrument_id, side, quantity, mode)
    ok = [c for c in cands if c['eligible']]
    if not ok:
        raise HTTPException(409, 'No account can take this order now: ' + '; '.join(f"{c['account_code']}: {', '.join(c['why'])}" for c in cands))
    buy = side == 'BUY'
    if rule == 'PRIORITY':
        ranked = ok
    elif rule == 'LOWEST_COST':
        ranked = sorted(ok, key=lambda c: (c['price'] + c['slippage']) if buy else -(c['price'] - c['slippage']))
    else:
        ranked = sorted(ok, key=lambda c: ((c['price'] if buy else -c['price']), c['spread']))
    record = {'rule': rule, 'side': side, 'candidates': [{k: (str(v) if isinstance(v, Decimal) else v) for k, v in c.items()} for c in cands],
              'at': datetime.now(timezone.utc).isoformat()}
    if rule != 'SPLIT_EQUAL':
        best = ranked[0]
        return [(best['account_id'], quantity, {**record, 'chosen': best['account_code'],
                                                'because': f"{rule.lower().replace('_', ' ')}: {best.get('price')} on {best['account_code']}"})]
    legs, left = [], quantity
    share = quantity / len(ranked)
    for c in ranked:
        q, problem = risk.lot(db, instrument_id, share)
        if not problem and q > 0:
            legs.append([c['account_id'], q, c['account_code']])
            left -= q
    if not legs:
        raise HTTPException(409, 'Split too small: each share is under the smallest tradable size.')
    legs[0][1] += left  # the remainder goes to the best-priced account
    return [(a, q, {**record, 'chosen': code, 'because': f'split equally across {len(legs)} accounts'}) for a, q, code in legs]
