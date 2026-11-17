"""Capital partitions and allocation (Stage 6 · Portfolio intelligence).

Why it exists: one account usually funds several strategies. Each needs its own slice of capital to size from, its own
drawdown limit, and a way to rebalance the slices — without opening separate broker accounts.
Purpose: partitions per account (capital, weight, own drawdown limit, active or paused); deployments assigned to a
partition with a weight (sizing multiplies by it; 0 stops new entries); allocation that rewrites the partitions'
capital from a total by EQUAL shares, by WEIGHT, or by RISK_PARITY (inverse volatility of each partition's instruments,
from daily bars); a preview before applying. Every change is audited.
Must never: move money at the broker (partitions are COREI's own book-keeping), or let partition capital exceed what
the operator states as the total.
Dependencies: trading.portfolio, trading.common, trading.backtest (bars for volatility), config_store.
Consumers: trading.risk (sizing, partition limits), trading_api.
Architectural position: Stage 6, feeding Stage 5 sizing.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .. import config_store as store
from . import portfolio
from .common import D, ZERO, quote


class PartitionIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_id: UUID
    name: str = Field(min_length=2, max_length=80)
    capital: Decimal = Field(ge=0, max_digits=38, decimal_places=2)
    weight: Decimal = Field(Decimal(1), ge=0, max_digits=20, decimal_places=6)
    max_drawdown_pct: Decimal | None = Field(default=None, ge=0, le=100, max_digits=10, decimal_places=2)
    status: Literal['ACTIVE', 'PAUSED'] = 'ACTIVE'
    note: str = Field(default='', max_length=500)
    reason: str = Field(min_length=5, max_length=500)


class AssignIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    partition_id: UUID | None = None
    weight: Decimal = Field(Decimal(1), ge=0, le=100, max_digits=20, decimal_places=6)
    reason: str = Field(min_length=5, max_length=500)


class AllocateIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_id: UUID
    method: Literal['EQUAL', 'WEIGHT', 'RISK_PARITY']
    total: Decimal = Field(gt=0, max_digits=38, decimal_places=2)
    apply: bool = False
    reason: str = Field(default='', max_length=500)


def save(actor: str, data: PartitionIn, partition_id: UUID | None = None, version: int | None = None) -> dict:
    with store.transaction('write') as db:
        if partition_id is None:
            row = db.execute('INSERT INTO pf_partitions(id, account_id, name, capital, weight, max_drawdown_pct, status, note, created_by, updated_by) '
                             'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                             (uuid4(), str(data.account_id), data.name, data.capital, data.weight, data.max_drawdown_pct, data.status, data.note,
                              actor, actor)).fetchone()
            store.audit(db, actor, 'CREATE_PARTITION', 'partition', row['id'], None, dict(row), data.reason)
        else:
            before = db.execute('SELECT * FROM pf_partitions WHERE id=%s FOR UPDATE', (str(partition_id),)).fetchone()
            if not before:
                raise HTTPException(404, 'Partition not found.')
            if version is not None and before['version'] != version:
                raise HTTPException(409, 'This partition was changed by someone else: reload and try again.')
            if str(before['account_id']) != str(data.account_id):
                raise HTTPException(422, 'A partition stays on its account.')
            row = db.execute('UPDATE pf_partitions SET name=%s, capital=%s, weight=%s, max_drawdown_pct=%s, status=%s, note=%s, version=version+1, '
                             'updated_by=%s, updated_at=now() WHERE id=%s RETURNING *',
                             (data.name, data.capital, data.weight, data.max_drawdown_pct, data.status, data.note, actor, str(partition_id))).fetchone()
            store.audit(db, actor, 'UPDATE_PARTITION', 'partition', partition_id, dict(before), dict(row), data.reason)
        return store.clean(row)


def delete(actor: str, partition_id: UUID, reason: str) -> None:
    with store.transaction('delete') as db:
        before = db.execute('SELECT * FROM pf_partitions WHERE id=%s', (str(partition_id),)).fetchone()
        if not before:
            raise HTTPException(404, 'Partition not found.')
        n = db.execute('SELECT count(*) AS n FROM cfg_deployments WHERE partition_id=%s', (str(partition_id),)).fetchone()['n']
        if n:
            raise HTTPException(409, f'{n} deployment(s) use this partition: move them first.')
        db.execute('DELETE FROM pf_partitions WHERE id=%s', (str(partition_id),))
        store.audit(db, actor, 'DELETE_PARTITION', 'partition', partition_id, dict(before), None, reason)


def assign(actor: str, deployment_id: UUID, data: AssignIn) -> dict:
    with store.transaction('write') as db:
        before = db.execute('SELECT id, account_id, partition_id, weight FROM cfg_deployments WHERE id=%s FOR UPDATE', (str(deployment_id),)).fetchone()
        if not before:
            raise HTTPException(404, 'Deployment not found.')
        if data.partition_id:
            p = db.execute('SELECT account_id FROM pf_partitions WHERE id=%s', (str(data.partition_id),)).fetchone()
            if not p or str(p['account_id']) != str(before['account_id']):
                raise HTTPException(422, "The partition must belong to the deployment's account.")
        after = db.execute('UPDATE cfg_deployments SET partition_id=%s, weight=%s WHERE id=%s RETURNING id, partition_id, weight',
                           (str(data.partition_id) if data.partition_id else None, data.weight, str(deployment_id))).fetchone()
        store.audit(db, actor, 'ASSIGN_ALLOCATION', 'deployment', deployment_id, dict(before), dict(after), data.reason)
        return store.clean(after)


def listing(db, account_id=None) -> list[dict]:
    rows = db.execute('SELECT p.*, a.code AS account_code FROM pf_partitions p JOIN cfg_accounts a ON a.id=p.account_id'
                      + (' WHERE p.account_id=%s' if account_id else '') + ' ORDER BY a.code, p.name', ([str(account_id)] if account_id else [])).fetchall()
    out = []
    for r in rows:
        pe = portfolio.partition_equity(db, r['id'])
        deps = db.execute("""SELECT d.id, s.code AS strategy, i.symbol, d.weight, d.mode, d.status FROM cfg_deployments d JOIN cfg_strategies s ON s.id=d.strategy_id
                             JOIN cfg_instruments i ON i.id=d.instrument_id WHERE d.partition_id=%s ORDER BY s.code""", (r['id'],)).fetchall()
        out.append({**store.clean(r), **{k: pe.get(k) for k in ('equity', 'realised', 'unrealised', 'drawdown_pct')},
                    'deployments': [store.clean(d) for d in deps]})
    return out


def _volatility(db, partition_id) -> Decimal | None:
    """Average daily volatility (stdev of D1 returns, 60 bars) of the partition's instruments."""
    from .backtest import load_bars
    vols = []
    for d in db.execute('SELECT account_id, instrument_id FROM cfg_deployments WHERE partition_id=%s', (str(partition_id),)).fetchall():
        q = quote(db, d['account_id'], d['instrument_id'])
        if not q:
            continue
        end = datetime.now(timezone.utc)
        bars, _ = load_bars(db, q['account_id'], q['broker_symbol'], 'D1', end - timedelta(days=120), end)
        closes = [float(b['close']) for b in bars[-61:] if b['close']]
        rets = [b / a - 1 for a, b in zip(closes, closes[1:]) if a]
        if len(rets) > 10:
            m = sum(rets) / len(rets)
            vols.append(math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1)))
    return Decimal(str(sum(vols) / len(vols))) if vols else None


def allocate(actor: str, data: AllocateIn) -> dict:
    """Preview (apply=false) or write the partitions' capital from the total by the method."""
    with store.transaction('write' if data.apply else 'read') as db:
        parts = [dict(r) for r in db.execute("SELECT * FROM pf_partitions WHERE account_id=%s AND status='ACTIVE' ORDER BY name FOR UPDATE",
                                             (str(data.account_id),)).fetchall()]
        if not parts:
            raise HTTPException(409, 'This account has no active partition.')
        if data.method == 'EQUAL':
            raw = {p['id']: Decimal(1) for p in parts}
        elif data.method == 'WEIGHT':
            raw = {p['id']: D(p['weight']) for p in parts}
        else:
            raw, missing = {}, []
            for p in parts:
                v = _volatility(db, p['id'])
                if not v:
                    missing.append(p['name'])
                else:
                    raw[p['id']] = Decimal(1) / v
            if missing:
                raise HTTPException(409, f"Risk parity needs 10+ daily bars for every partition's instruments; missing for: {', '.join(missing)}.")
        total_w = sum(raw.values(), ZERO)
        if total_w <= 0:
            raise HTTPException(409, 'The weights add up to 0.')
        plan = [{'partition_id': str(p['id']), 'name': p['name'], 'before': D(p['capital']),
                 'after': (data.total * raw[p['id']] / total_w).quantize(Decimal('0.01'), ROUND_DOWN),
                 'share_pct': (raw[p['id']] / total_w * 100).quantize(Decimal('0.1'))} for p in parts]
        if data.apply:
            if len(data.reason.strip()) < 5:
                raise HTTPException(422, 'Give a reason (at least 5 characters) to apply an allocation.')
            for row in plan:
                db.execute('UPDATE pf_partitions SET capital=%s, version=version+1, updated_by=%s, updated_at=now() WHERE id=%s',
                           (row['after'], actor, row['partition_id']))
            store.audit(db, actor, 'ALLOCATE_CAPITAL', 'account', data.account_id, None,
                        {'method': data.method, 'total': str(data.total), 'plan': [{k: str(v) for k, v in r.items()} for r in plan]}, data.reason)
        return {'method': data.method, 'total': data.total, 'applied': data.apply, 'plan': plan}
