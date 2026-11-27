"""Shared facts for the trading core (Stages 3–7): exact numbers, instruments, quotes, FX and settings.

Why it exists: signals, risk, orders and the portfolio need the same answers to "what is this instrument's tick and
lot", "what is the price now and how old is it" and "what is 1 USD in INR"; answering them in one place keeps the
stages consistent.
Purpose: Decimal helpers (exact money and quantities), instrument facts, quote lookup (the account's own symbol, else
the deployment's data source, else any source of the instrument), FX conversion from collected FX pairs, the
trading settings row, and deployment loading with its data source resolved.
Must never: write anything, or use binary floating point for a price, quantity or amount that leaves this module
(stored prices are double precision; they are converted here with the instrument's precision).
Dependencies: config_store, md.markets. Consumers: trading.*.
Architectural position: the trading core's base layer.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

from .. import config_store as store

UTC = timezone.utc
ZERO = Decimal(0)
TIMEFRAME_SECONDS = {'M1': 60, 'M5': 300, 'M15': 900, 'M30': 1800, 'H1': 3600, 'H4': 14400, 'D1': 86400}


def D(v: Any) -> Decimal | None:  # noqa: N802 - short name for "exact number"
    """An exact Decimal from a stored value (NUMERIC, text, or a double precision price via its shortest repr)."""
    if v is None or v == '':
        return None
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(repr(v)) if isinstance(v, float) else Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def quantize(v: Decimal, step: Decimal, rounding=ROUND_HALF_EVEN) -> Decimal:
    """Round to a multiple of step (a tick for prices, ROUND_DOWN for quantities)."""
    if not step or step <= 0:
        return v
    return (v / step).to_integral_value(rounding=rounding) * step


def floor_step(v: Decimal, step: Decimal) -> Decimal:
    return quantize(v, step, ROUND_DOWN)


def now() -> datetime:
    return datetime.now(UTC)


def settings(db=None) -> dict:
    def read(c):
        return dict(c.execute('SELECT * FROM tr_settings WHERE id').fetchone() or {})
    if db is not None:
        return read(db)
    with store.transaction('read') as c:
        return read(c)


def instrument(db, instrument_id) -> dict:
    row = db.execute('SELECT * FROM cfg_instruments WHERE id=%s', (str(instrument_id),)).fetchone()
    return dict(row) if row else {}


def _quote_row(db, account_id, broker_symbol) -> dict | None:
    if not account_id or not broker_symbol:
        return None
    r = db.execute('SELECT account_id, broker_symbol, bid, ask, source_time, received_at FROM md_latest WHERE account_id=%s AND broker_symbol=%s',
                   (str(account_id), broker_symbol)).fetchone()
    return dict(r) if r and r['bid'] is not None and r['ask'] is not None else None


def trading_symbol(db, account_id, instrument_id) -> str | None:
    """The account's own broker symbol for the instrument: an account mapping first, then its group's, then the server's."""
    r = db.execute("""SELECT m.broker_symbol FROM cfg_symbol_mappings m JOIN cfg_accounts a ON a.connection_id=m.connection_id
                      WHERE a.id=%s AND m.instrument_id=%s AND m.status='ACTIVE' AND (m.account_id IS NULL OR m.account_id=a.id)
                        AND (m.account_group_id IS NULL OR EXISTS (SELECT 1 FROM cfg_account_group_members g
                                                                   WHERE g.group_id=m.account_group_id AND g.account_id=a.id))
                      ORDER BY (m.account_id IS NOT NULL) DESC, (m.account_group_id IS NOT NULL) DESC,
                               (m.usage IN ('TRADING','BOTH')) DESC LIMIT 1""",
                   (str(account_id), str(instrument_id))).fetchone()
    return r['broker_symbol'] if r else None


def quote(db, account_id, instrument_id, data_source: tuple | None = None) -> dict | None:
    """The price to decide and fill on, with where it came from: the account's own symbol, else the deployment's data
    source, else the freshest source of the instrument. Prices as exact Decimals rounded to the instrument's tick."""
    ins = instrument(db, instrument_id)
    candidates = []
    sym = trading_symbol(db, account_id, instrument_id)
    if sym:
        candidates.append((account_id, sym, 'ACCOUNT'))
    if data_source:
        candidates.append((data_source[0], data_source[1], 'DATA_SOURCE'))
    preferred = db.execute("""SELECT p.account_id, p.broker_symbol FROM pf_paper_accounts x JOIN cfg_accounts a ON a.connection_id=x.price_source_id
                              JOIN md_collection_plans p ON p.account_id=a.id AND p.instrument_id=%s AND p.status <> 'ARCHIVED'
                              WHERE x.account_id=%s LIMIT 1""", (str(instrument_id), str(account_id))).fetchone()
    if preferred:   # a paper account's chosen price source comes before any other feed
        candidates.append((preferred['account_id'], preferred['broker_symbol'], 'PAPER_SOURCE'))
    for account, symbol, kind in candidates:
        r = _quote_row(db, account, symbol)
        if r:
            return _exact(r, ins, kind)
    r = db.execute("""SELECT l.account_id, l.broker_symbol, l.bid, l.ask, l.source_time, l.received_at FROM md_collection_plans p
                      JOIN md_latest l ON l.account_id=p.account_id AND l.broker_symbol=p.broker_symbol
                      WHERE p.instrument_id=%s AND p.status <> 'ARCHIVED' AND l.bid IS NOT NULL AND l.ask IS NOT NULL
                      ORDER BY l.received_at DESC LIMIT 1""", (str(instrument_id),)).fetchone()
    return _exact(dict(r), ins, 'OTHER_SOURCE') if r else None


def _exact(r: dict, ins: dict, kind: str) -> dict:
    tick = D(ins.get('tick_size')) or Decimal('0.00001')
    bid, ask = quantize(D(r['bid']), tick), quantize(D(r['ask']), tick)
    return {'bid': bid, 'ask': ask, 'mid': quantize((bid + ask) / 2, tick / 10), 'source': kind, 'account_id': str(r['account_id']),
            'broker_symbol': r['broker_symbol'], 'source_time': r['source_time'], 'received_at': r['received_at']}


def _live_rate(db, a: str, b: str) -> Decimal | None:
    """1 a in b from a collected FX (or metal) pair, either direction."""
    rows = db.execute("""SELECT i.base_currency, i.quote_currency, l.bid, l.ask FROM cfg_instruments i
                         JOIN md_collection_plans p ON p.instrument_id=i.id AND p.status <> 'ARCHIVED'
                         JOIN md_latest l ON l.account_id=p.account_id AND l.broker_symbol=p.broker_symbol
                         WHERE i.asset_class IN ('FX', 'METAL') AND ((i.base_currency=%s AND i.quote_currency=%s) OR (i.base_currency=%s AND i.quote_currency=%s))
                           AND l.bid IS NOT NULL AND l.ask IS NOT NULL ORDER BY l.received_at DESC LIMIT 1""",
                      (a, b, b, a)).fetchone()
    if not rows:
        return None
    mid = (D(rows['bid']) + D(rows['ask'])) / 2
    if not mid:
        return None
    return mid if rows['base_currency'] == a else Decimal(1) / mid


def _manual_rate(db, a: str, b: str) -> Decimal | None:
    """1 a in b from a rate a person entered (cfg_fx_rates), either direction."""
    r = db.execute('SELECT base, rate FROM cfg_fx_rates WHERE (base=%s AND quote=%s) OR (base=%s AND quote=%s) ORDER BY set_at DESC LIMIT 1',
                   (a, b, b, a)).fetchone()
    if not r:
        return None
    return D(r['rate']) if r['base'] == a else Decimal(1) / D(r['rate'])


def fx_rate(db, from_ccy: str, to_ccy: str) -> Decimal | None:
    """1 unit of from_ccy in to_ccy. In order: a collected pair (direct or inverse); a cross through USD or EUR from
    collected pairs; a rate a person entered (direct, inverse, or a cross with a collected leg). None when nothing
    gives a rate: the caller reports the holding as not valued rather than guessing."""
    if not from_ccy or not to_ccy or from_ccy == to_ccy:
        return Decimal(1)
    direct = _live_rate(db, from_ccy, to_ccy)
    if direct is not None:
        return direct
    for via in ('USD', 'EUR'):
        if via in (from_ccy, to_ccy):
            continue
        a, b = _live_rate(db, from_ccy, via), _live_rate(db, via, to_ccy)
        if a is not None and b is not None:
            return a * b
    manual = _manual_rate(db, from_ccy, to_ccy)
    if manual is not None:
        return manual
    for via in ('USD', 'EUR'):
        if via in (from_ccy, to_ccy):
            continue
        a = _live_rate(db, from_ccy, via) or _manual_rate(db, from_ccy, via)
        b = _live_rate(db, via, to_ccy) or _manual_rate(db, via, to_ccy)
        if a is not None and b is not None:
            return a * b
    return None


DEPLOYMENT_SELECT = """SELECT d.*, s.code AS strategy_code, v.algorithm, v.parameters, v.number AS version_number, v.state AS version_state,
    i.symbol AS instrument_symbol, i.quote_currency, i.base_currency, i.market AS instrument_market, i.asset_class,
    a.code AS account_code, a.base_currency AS account_currency, c.environment, c.platform,
    dm.broker_symbol AS data_symbol, dm.connection_id AS data_connection_id, dm.account_id AS data_mapping_account
    FROM cfg_deployments d JOIN cfg_strategies s ON s.id=d.strategy_id JOIN cfg_strategy_versions v ON v.id=d.strategy_version_id
    JOIN cfg_instruments i ON i.id=d.instrument_id JOIN cfg_accounts a ON a.id=d.account_id JOIN cfg_connections c ON c.id=a.connection_id
    JOIN cfg_symbol_mappings dm ON dm.id=d.data_mapping_id"""


def data_source(db, dep: dict) -> tuple | None:
    """Where the deployment's bars come from: a collected (account, broker symbol) on the data mapping's connection."""
    r = db.execute("""SELECT p.account_id FROM md_collection_plans p JOIN cfg_accounts a ON a.id=p.account_id
                      WHERE a.connection_id=%s AND p.broker_symbol=%s AND p.status <> 'ARCHIVED'
                      ORDER BY (p.account_id = %s) DESC, (p.account_id = %s) DESC, p.created_at LIMIT 1""",
                   (str(dep['data_connection_id']), dep['data_symbol'], str(dep.get('data_mapping_account') or dep['account_id']),
                    str(dep['account_id']))).fetchone()
    return (str(r['account_id']), dep['data_symbol']) if r else None


def deployments(db, status: str | None = 'ACTIVE', deployment_id=None) -> list[dict]:
    where, args = [], []
    if status:
        where.append('d.status=%s'); args.append(status)
    if deployment_id:
        where.append('d.id=%s'); args.append(str(deployment_id))
    q = DEPLOYMENT_SELECT + (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY s.code, i.symbol'
    return [dict(r) for r in db.execute(q, args).fetchall()]


def account_currency(db, account_id) -> str:
    r = db.execute('SELECT base_currency FROM cfg_accounts WHERE id=%s', (str(account_id),)).fetchone()
    return (r and r['base_currency']) or 'USD'
