"""Copy trading (Stage 6 · Portfolio intelligence): followers mirror a master account's fills.

Why it exists: a firm often runs one book and replicates it to client or sub-accounts with their own size. Doing this
inside COREI keeps every copy behind the follower's own risk check, kill switches and order-sending switch.
Purpose: copy links (master → follower, sizing method, ratio, reverse, cap, symbol filter, paper or broker), and the
hook that turns each master fill into one follower order in the fill's own transaction.
Sizing: FIXED_RATIO = master fill × ratio; EQUITY_RATIO = master fill × follower equity ÷ master equity × ratio;
FIXED_QTY = the fixed quantity in proportion to how much of the master order this fill is. Rounded down to the
follower's lot step; a size under the minimum is skipped with a warning.
Must never: copy a copy (no chains, no loops), bypass the follower's risk (the order goes through oms.place), or
copy into the master itself.
Dependencies: trading.oms, trading.risk, trading.portfolio, config_store. Consumers: oms._fill (hook), trading_api.
Architectural position: Stage 6 → Stage 5/7 (a copy is a new order that passes risk like any other).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from .. import config_store as store
from .common import D, ZERO


class LinkIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    master_account_id: UUID
    follower_account_id: UUID
    method: Literal['FIXED_RATIO', 'EQUITY_RATIO', 'FIXED_QTY'] = 'FIXED_RATIO'
    ratio: Decimal = Field(default=Decimal(1), gt=0, max_digits=20, decimal_places=6)
    fixed_qty: Decimal | None = Field(default=None, gt=0, max_digits=38, decimal_places=12)
    mode: Literal['PAPER', 'BROKER'] = 'PAPER'
    reverse: bool = False
    max_qty: Decimal | None = Field(default=None, gt=0, max_digits=38, decimal_places=12)
    symbols: list[str] = Field(default_factory=list, max_length=500)
    enabled: bool = True
    reason: str = Field(min_length=5, max_length=500)


def save(actor: str, data: LinkIn, link_id: UUID | None = None, version: int | None = None) -> dict:
    if data.master_account_id == data.follower_account_id:
        raise HTTPException(422, 'An account cannot copy itself.')
    if data.method == 'FIXED_QTY' and not data.fixed_qty:
        raise HTTPException(422, 'Fixed quantity copying needs the quantity.')
    values = data.model_dump(exclude={'reason'})
    values['symbols'] = Jsonb(values['symbols'])
    with store.transaction('write') as db:
        loop = db.execute('SELECT 1 FROM tr_copy_links WHERE master_account_id=%s AND follower_account_id=%s AND enabled',
                          (str(data.follower_account_id), str(data.master_account_id))).fetchone()
        if loop and data.enabled:
            raise HTTPException(409, 'The follower already copies into the master: two accounts cannot copy each other.')
        cols = ['master_account_id', 'follower_account_id', 'method', 'ratio', 'fixed_qty', 'mode', 'reverse', 'max_qty', 'symbols', 'enabled']
        args = [str(values['master_account_id']), str(values['follower_account_id']), *[values[c] for c in cols[2:]]]
        if link_id is None:
            row = db.execute(f"INSERT INTO tr_copy_links(id, {', '.join(cols)}, created_by, updated_by) VALUES (%s, {', '.join(['%s'] * len(cols))}, %s, %s) RETURNING *",
                             (uuid4(), *args, actor, actor)).fetchone()
            store.audit(db, actor, 'CREATE_COPY_LINK', 'copy_link', row['id'], None, dict(row), data.reason)
        else:
            before = db.execute('SELECT * FROM tr_copy_links WHERE id=%s FOR UPDATE', (str(link_id),)).fetchone()
            if not before:
                raise HTTPException(404, 'Copy link not found.')
            if version is not None and before['version'] != version:
                raise HTTPException(409, 'This link was changed by someone else: reload and try again.')
            row = db.execute(f"UPDATE tr_copy_links SET {', '.join(c + '=%s' for c in cols)}, version=version+1, updated_by=%s, updated_at=now() "
                             'WHERE id=%s RETURNING *', (*args, actor, str(link_id))).fetchone()
            store.audit(db, actor, 'UPDATE_COPY_LINK', 'copy_link', link_id, dict(before), dict(row), data.reason)
        return store.clean(row)


def delete(actor: str, link_id: UUID, reason: str) -> None:
    with store.transaction('delete') as db:
        before = db.execute('DELETE FROM tr_copy_links WHERE id=%s RETURNING *', (str(link_id),)).fetchone()
        if not before:
            raise HTTPException(404, 'Copy link not found.')
        store.audit(db, actor, 'DELETE_COPY_LINK', 'copy_link', link_id, dict(before), None, reason)


def links(db) -> list[dict]:
    rows = db.execute("""SELECT l.*, m.code AS master_code, f.code AS follower_code,
                                (SELECT count(*) FROM oms_orders o WHERE o.source='COPY' AND o.account_id=l.follower_account_id) AS copies
                         FROM tr_copy_links l JOIN cfg_accounts m ON m.id=l.master_account_id JOIN cfg_accounts f ON f.id=l.follower_account_id
                         ORDER BY m.code, f.code""").fetchall()
    return [store.clean(r) for r in rows]


def on_fill(db, order: dict, fill: dict) -> list[dict]:
    """Each enabled link of the master account gets one follower order for this fill (same transaction)."""
    if order.get('source') == 'COPY':
        return []
    rows = db.execute('SELECT * FROM tr_copy_links WHERE master_account_id=%s AND enabled', (str(order['account_id']),)).fetchall()
    if not rows:
        return []
    from . import oms, portfolio, risk
    sym = db.execute('SELECT symbol FROM cfg_instruments WHERE id=%s', (str(order['instrument_id']),)).fetchone()['symbol']
    out = []
    for link in rows:
        if link['symbols'] and sym not in link['symbols']:
            continue
        qty = D(fill['quantity'])
        if link['method'] == 'FIXED_RATIO':
            qty = qty * D(link['ratio'])
        elif link['method'] == 'EQUITY_RATIO':
            m, f = portfolio.equity(db, link['master_account_id'])['equity'], portfolio.equity(db, link['follower_account_id'])['equity']
            qty = qty * (f / m if m > 0 else ZERO) * D(link['ratio'])
        else:
            qty = D(link['fixed_qty']) * qty / D(order['quantity'])
        if link['max_qty']:
            qty = min(qty, D(link['max_qty']))
        q, problem = risk.lot(db, order['instrument_id'], qty)
        if problem or q <= 0:
            oms._alert(db, 'WARNING', f'Copy skipped: {sym}', f"Follower size {qty.normalize()} for this fill is under the smallest tradable size.")
            continue
        side = order['side'] if not link['reverse'] else ('SELL' if order['side'] == 'BUY' else 'BUY')
        o = oms.place(db, account_id=link['follower_account_id'], instrument_id=order['instrument_id'], side=side, quantity=q,
                      mode=link['mode'], seed=f"copy:{link['id']}:{fill['id']}", actor='copy', source='COPY', copy_of=fill['id'])
        out.append(o)
    return out
