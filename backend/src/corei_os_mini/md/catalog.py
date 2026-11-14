"""Symbol catalogue: every symbol each source offers, loaded from the provider, and picking from it.

  * MT5 (direct or terminal bridge): the symbol list and rules arrive with every login ("Log in and read now",
    and the refresh here, which is such a login). Stored from broker_session after each read.
  * Upstox: the public daily instrument file (NSE, BSE, MCX: equities, indices, futures, options).
  * FIX: SecurityListRequest on the venue's session (fix_reader --securities).

Each catalogue row carries a suggested COREI instrument name (XAUUSD.r -> XAUUSD, "Nifty 50" -> NIFTY50) so
the same instrument links across sources. collect() turns picked rows into collection plans, optionally
creating the instrument and its symbol mapping, and starts them.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import re
import subprocess
import sys
import urllib.request
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .. import config_store as store
from ..config_models import InstrumentIn, MappingIn
from . import plans

_log = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
UPSTOX_FILE = os.environ.get('COREI_UPSTOX_INSTRUMENTS_URL', 'https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz')
CLASSES = ('FX', 'METAL', 'INDEX', 'COMMODITY', 'CRYPTO', 'EQUITY', 'FUTURE', 'OPTION', 'OTHER')
CCY = r'(EUR|GBP|USD|AUD|NZD|CAD|CHF|JPY|SGD|HKD|NOK|SEK|DKK|ZAR|MXN|TRY|PLN|CNH|INR|HUF|CZK|THB|RUB|ILS)'
ALIASES = {  # common broker names -> one COREI name
    'GOLD': 'XAUUSD', 'SILVER': 'XAGUSD', 'US30': 'US30', 'DJ30': 'US30', 'DJI30': 'US30', 'WS30': 'US30', 'DOW': 'US30', 'USA30': 'US30',
    'NAS100': 'NAS100', 'NAS': 'NAS100', 'USTEC': 'NAS100', 'NDX': 'NAS100', 'US100': 'NAS100', 'USTECH': 'NAS100', 'NQ': 'NAS100',
    'SPX500': 'US500', 'US500': 'US500', 'SP500': 'US500', 'SPX': 'US500', 'USA500': 'US500', 'ES': 'US500',
    'GER40': 'GER40', 'DE40': 'GER40', 'DAX40': 'GER40', 'GER30': 'GER40', 'DE30': 'GER40', 'DAX': 'GER40',
    'UK100': 'UK100', 'FTSE100': 'UK100', 'JP225': 'JP225', 'JPN225': 'JP225', 'NIKKEI': 'JP225', 'HK50': 'HK50', 'AUS200': 'AUS200',
    'USOIL': 'WTI', 'WTI': 'WTI', 'XTIUSD': 'WTI', 'CL': 'WTI', 'CRUDE': 'WTI', 'UKOIL': 'BRENT', 'BRENT': 'BRENT', 'XBRUSD': 'BRENT',
    'NATGAS': 'NGAS', 'XNGUSD': 'NGAS', 'NGAS': 'NGAS',
}


# ---------- classification and instrument names ----------

def mt5_class(name: str, path: str = '') -> str:
    p = (path or '').lower()
    for words, cls in ((('forex', 'fx', 'currenc'), 'FX'), (('metal', 'gold', 'silver', 'spot metal'), 'METAL'), (('indice', 'index', 'cash'), 'INDEX'),
                       (('crypto', 'coin'), 'CRYPTO'), (('energ', 'commod', 'oil', 'agri', 'soft'), 'COMMODITY'),
                       (('stock', 'share', 'equit', 'cfd stock'), 'EQUITY'), (('future',), 'FUTURE'), (('option',), 'OPTION')):
        if any(w in p for w in words):
            return cls
    core = suggest_mt5(name)
    if re.match(r'^(XAU|XAG|XPT|XPD)', core):
        return 'METAL'
    if re.match(r'^(BTC|ETH|XRP|SOL|LTC|DOGE|ADA|BNB|DOT)', core):
        return 'CRYPTO'
    if core in ('US30', 'NAS100', 'US500', 'GER40', 'UK100', 'JP225', 'HK50', 'AUS200'):
        return 'INDEX'
    if core in ('WTI', 'BRENT', 'NGAS'):
        return 'COMMODITY'
    if re.fullmatch(CCY + CCY, core):
        return 'FX'
    return 'OTHER'


def suggest_mt5(name: str) -> str:
    """Broker symbol -> COREI instrument name: drop suffixes (.r, -ECN, _i, #, m, pro) and use common names."""
    name = re.sub(r'^([A-Za-z]{3})/([A-Za-z]{3})', r'\1\2', name.strip())  # FIX style EUR/USD
    core = re.split(r'[.\-_#!+ ]', name)[0] or name
    core = re.sub(r'(?<=[A-Z0-9])[a-z]+$', '', core)  # XAUUSDm, EURUSDpro
    core = core.upper()
    if core in ALIASES:
        return ALIASES[core]
    m = re.match(r'^((?:XAU|XAG|XPT|XPD|' + CCY[1:-1] + r')' + CCY + r')[A-Z]{0,2}$', core)
    if m:
        return m.group(1)
    return re.sub(r'[^A-Z0-9._/-]', '', core)[:32] or 'X'


def upstox_class(item: dict) -> str:
    kind, seg = str(item.get('instrument_type') or '').upper(), str(item.get('segment') or '').upper()
    if kind == 'INDEX' or seg.endswith('_INDEX'):
        return 'INDEX'
    if kind in ('CE', 'PE'):
        return 'OPTION'
    if kind.startswith('FUT') or kind == 'FUT':
        return 'COMMODITY' if seg.startswith('MCX') else 'FUTURE'
    if seg.endswith('_EQ'):
        return 'EQUITY'
    return 'OTHER'


def suggest_upstox(item: dict) -> str:
    cls = upstox_class(item)
    base = item.get('name') if cls == 'INDEX' else item.get('trading_symbol') or item.get('name') or ''
    return re.sub(r'[^A-Z0-9._/-]', '', str(base).upper())[:32] or 'X'


def _num(v: Any) -> Decimal | None:
    try:
        return None if v in (None, '') else Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


# ---------- storing ----------

COLS = ('broker_symbol', 'display_symbol', 'description', 'asset_class', 'exchange', 'segment', 'instrument_type', 'base_currency',
        'quote_currency', 'digits', 'tick_size', 'lot_size', 'volume_min', 'volume_step', 'contract_size', 'expiry', 'strike',
        'underlying', 'suggested_instrument')


def _replace(account_id: UUID, source: str, rows: list[dict]) -> int:
    """Replace the account's catalogue from one source with these rows (one transaction)."""
    seen: dict[str, dict] = {}
    for r in rows:
        if r.get('broker_symbol'):
            seen[str(r['broker_symbol'])[:64]] = r
    with store.transaction('update') as db:
        db.execute('DELETE FROM md_symbol_catalog WHERE account_id=%s', (account_id,))
        with db.cursor() as cur, cur.copy(f"COPY md_symbol_catalog (account_id, source, {', '.join(COLS)}) FROM STDIN") as copy:
            for sym, r in seen.items():
                copy.write_row((account_id, source, sym, *[r.get(c) for c in COLS[1:]]))
    return len(seen)


def _row(**kw: Any) -> dict:
    out = {c: kw.get(c) for c in COLS}
    for c in ('display_symbol', 'description', 'exchange', 'segment', 'instrument_type', 'base_currency', 'quote_currency', 'underlying', 'suggested_instrument'):
        out[c] = str(out[c] or '')[:200]
    out['asset_class'] = out.get('asset_class') if out.get('asset_class') in CLASSES else 'OTHER'
    return out


def store_mt5(account_id: UUID, result: dict) -> int:
    """After an MT5 login: every symbol name the server lists, with the rules read for each."""
    from ..broker_session import _pick, symbol_spec
    raw_by_name: dict = result.get('symbols') or {}
    names = list(dict.fromkeys([*(result.get('symbol_names') or []), *raw_by_name]))
    rows = []
    for name in names:
        raw = raw_by_name.get(name) or {}
        spec, _ = symbol_spec(raw) if raw else (None, None)
        path = str(_pick(raw, 'Path') or '') if raw else ''
        desc = str(_pick(raw, 'Description') or '') if raw else ''
        base, profit = (_pick(raw, 'CurrencyBase', 'BaseCurrency') or '', _pick(raw, 'CurrencyProfit', 'ProfitCurrency') or '') if raw else ('', '')
        digits = (spec or {}).get('digits')
        core = suggest_mt5(name)
        rows.append(_row(broker_symbol=name, display_symbol=name, description=desc, asset_class=mt5_class(name, path), segment=path.replace('\\', ' › '),
                         instrument_type='CFD', base_currency=str(base) or (core[:3] if len(core) == 6 else ''), quote_currency=str(profit) or (core[3:6] if len(core) == 6 else ''),
                         digits=digits, tick_size=Decimal(1).scaleb(-int(digits)) if digits is not None else None,
                         volume_min=_num((spec or {}).get('volume_min')), volume_step=_num((spec or {}).get('volume_step')),
                         contract_size=_num((spec or {}).get('contract_size')), suggested_instrument=core))
    if not rows:
        return 0
    return _replace(account_id, 'MT5_LOGIN', rows)


def _upstox_rows(items: list[dict]) -> list[dict]:
    rows = []
    for it in items:
        key = it.get('instrument_key')
        if not key:
            continue
        expiry = it.get('expiry')
        exp = None
        if isinstance(expiry, (int, float)) and expiry > 0:
            exp = datetime.fromtimestamp(expiry / 1000, IST).date()  # Upstox stamps expiries at 23:59:59 IST
        elif isinstance(expiry, str) and expiry:
            try:
                exp = date.fromisoformat(expiry[:10])
            except ValueError:
                exp = None
        tick = _num(it.get('tick_size'))
        rows.append(_row(broker_symbol=key, display_symbol=it.get('trading_symbol') or it.get('name') or key, description=it.get('name') or '',
                         asset_class=upstox_class(it), exchange=it.get('exchange') or '', segment=it.get('segment') or '',
                         instrument_type=it.get('instrument_type') or '', quote_currency='INR',
                         tick_size=(tick / 100) if tick else None,  # Upstox gives the tick in paise
                         lot_size=_num(it.get('lot_size')), expiry=exp, strike=_num(it.get('strike_price')) or None,
                         underlying=it.get('underlying_symbol') or '', suggested_instrument=suggest_upstox(it)))
    return rows


def _download_upstox() -> list[dict]:
    req = urllib.request.Request(UPSTOX_FILE, headers={'User-Agent': 'COREI-OS-MINI', 'Accept-Encoding': 'gzip'})
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 - fixed https URL (or an operator override)
        data = resp.read()
    if data[:2] == b'\x1f\x8b':
        data = gzip.decompress(data)
    items = json.loads(data)
    if not isinstance(items, list):
        raise ValueError('Upstox instrument file is not a list')
    return items


# ---------- refresh ----------

def _account(db, account_id: UUID) -> dict:
    row = db.execute('SELECT a.*, c.platform, c.adapter, c.settings, c.environment, c.id AS cid FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id '
                     'WHERE a.id=%s', (account_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Account not found.')
    return row


def source_of(account: dict) -> str:
    if account['platform'] == 'MT5':
        return 'MT5_LOGIN'
    if account['platform'] == 'FIX':
        return 'FIX_SECURITY_LIST'
    if 'upstox' in str((account['settings'] or {}).get('api_base_url', '')).lower():
        return 'UPSTOX_FILE'
    return 'MANUAL'


def refresh(actor: str, account_id: UUID) -> dict:
    """Load the account's symbol list from its provider now."""
    with store.transaction('read') as db:
        account = _account(db, account_id)
    source = source_of(account)
    with store.transaction('create') as db:
        run_id = db.execute('INSERT INTO md_catalog_runs(account_id, source, requested_by) VALUES (%s,%s,%s) RETURNING id',
                            (account_id, source, actor[:120])).fetchone()['id']
    count, error = 0, None
    try:
        if source == 'UPSTOX_FILE':
            count = _replace(account_id, source, _upstox_rows(_download_upstox()))
        elif source == 'MT5_LOGIN':
            from .. import broker_session
            read = broker_session.read_account(actor, account_id)  # a login: the symbol list comes with it (stored by broker_session)
            if not read.get('connected'):
                raise HTTPException(502, f"MT5 login failed, so the symbol list could not be read: {read.get('error')}")
            count = read.get('catalogue_symbols') or 0
        elif source == 'FIX_SECURITY_LIST':
            count = _refresh_fix(actor, account)
        else:
            raise HTTPException(422, 'This API has no instrument list COREI can load yet; add symbols by name.')
    except HTTPException as exc:
        error = str(exc.detail)
        raise
    except Exception as exc:  # noqa: BLE001 - recorded and explained
        error = f'{type(exc).__name__}: {exc}'[:300]
        _log.warning('catalogue refresh failed for %s', account_id, exc_info=True)
        raise HTTPException(502, f'Could not load the symbol list: {error}') from None
    finally:
        with store.transaction('update') as db:
            db.execute('UPDATE md_catalog_runs SET finished_at=now(), ok=%s, symbols=%s, error=%s WHERE id=%s', (error is None, count, error, run_id))
    return {'source': source, 'symbols': count, 'refreshed_at': datetime.now(UTC).isoformat()}


def _refresh_fix(actor: str, account: dict) -> int:
    from . import collector
    request = collector.fix_request(account['settings'], account['cid'], f'catalogue:{actor}', 'Load the venue instrument list')
    proc = subprocess.run([sys.executable, '-m', 'corei_os_mini.md.fix_reader', '--securities'], input=json.dumps(request),
                          capture_output=True, text=True, timeout=120)
    request.clear()
    out = json.loads((proc.stdout or '{}').strip().splitlines()[-1] if proc.stdout.strip() else '{}')
    if not out.get('connected'):
        raise HTTPException(502, f"FIX session refused: {out.get('error') or proc.stderr[-200:]}")
    secs = out.get('securities') or []
    if not secs and out.get('error'):
        raise HTTPException(502, f"The venue did not send its instrument list: {out['error']}")
    rows = []
    for s in secs:
        sym = s.get('symbol')
        if not sym:
            continue
        stype = str(s.get('security_type') or '').upper()
        cls = {'FOR': 'FX', 'FXSPOT': 'FX', 'FUT': 'FUTURE', 'OPT': 'OPTION', 'CS': 'EQUITY', 'INDEX': 'INDEX', 'MLEG': 'OTHER'}.get(stype) or mt5_class(sym)
        tick = _num(s.get('min_price_increment'))
        rows.append(_row(broker_symbol=sym, display_symbol=sym, description=s.get('description') or '', asset_class=cls, exchange=s.get('exchange') or '',
                         instrument_type=stype, quote_currency=s.get('currency') or '', tick_size=tick,
                         digits=max(0, -tick.normalize().as_tuple().exponent) if tick else None, contract_size=_num(s.get('contract_multiplier')),
                         strike=_num(s.get('strike')), underlying=s.get('underlying') or '', suggested_instrument=suggest_mt5(sym)))
    return _replace(account['id'], 'FIX_SECURITY_LIST', rows)


def refresh_due(actor: str = 'nightly') -> list[dict]:
    """Nightly: reload the files that change daily (Upstox). MT5 lists refresh with each login."""
    with store.transaction('read') as db:
        ids = [r['id'] for r in db.execute(
            "SELECT a.id FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id WHERE a.status='ACTIVE' AND c.status='ACTIVE' "
            "AND c.platform='REST_API' AND c.settings->>'api_base_url' ILIKE '%%upstox%%'").fetchall()]
    out = []
    for i in ids:
        try:
            out.append({'account_id': str(i), **refresh(actor, i)})
        except HTTPException as exc:
            out.append({'account_id': str(i), 'error': str(exc.detail)})
    return out


# ---------- listing ----------

def listing(account_id: UUID, q: str = '', asset_class: str = '', exchange: str = '', segment: str = '', show: str = 'all',
            limit: int = 200, offset: int = 0) -> dict:
    where, args = ['c.account_id=%s'], [account_id]
    if q.strip():
        where.append('(lower(c.display_symbol) LIKE %s OR lower(c.broker_symbol) LIKE %s OR lower(c.description) LIKE %s OR lower(c.suggested_instrument) LIKE %s)')
        like = f"%{q.strip().lower()}%"
        args += [like, like, like, like]
    filters = list(where), list(args)
    if asset_class:
        where.append('c.asset_class=%s'); args.append(asset_class)
    if exchange:
        where.append('c.exchange=%s'); args.append(exchange)
    if segment:
        where.append('c.segment=%s'); args.append(segment)
    if show == 'collected':
        where.append('p.id IS NOT NULL')
    elif show == 'not_collected':
        where.append('p.id IS NULL')
    join = ("LEFT JOIN md_collection_plans p ON p.account_id=c.account_id AND p.broker_symbol=c.broker_symbol AND p.status <> 'ARCHIVED' "
            'LEFT JOIN cfg_instruments i ON i.symbol=c.suggested_instrument')
    sql_where = ' AND '.join(where)
    with store.transaction('read') as db:
        total = db.execute(f'SELECT count(*) AS n FROM md_symbol_catalog c {join} WHERE {sql_where}', args).fetchone()['n']
        rows = db.execute(f'''SELECT c.*, p.id AS plan_id, p.status AS plan_status, p.code AS plan_code, i.id AS instrument_id
            FROM md_symbol_catalog c {join} WHERE {sql_where}
            ORDER BY (c.asset_class='INDEX') DESC, (c.expiry IS NULL) DESC, c.display_symbol LIMIT %s OFFSET %s''', [*args, limit, offset]).fetchall()
        fw, fa = ' AND '.join(filters[0]), filters[1]
        classes = db.execute(f'SELECT c.asset_class AS k, count(*) AS n FROM md_symbol_catalog c WHERE {fw} GROUP BY 1 ORDER BY 2 DESC', fa).fetchall()
        exchanges = db.execute(f"SELECT c.exchange AS k, count(*) AS n FROM md_symbol_catalog c WHERE {fw} AND c.exchange <> '' GROUP BY 1 ORDER BY 2 DESC", fa).fetchall()
        segments = db.execute(f"SELECT c.segment AS k, count(*) AS n FROM md_symbol_catalog c WHERE {fw} AND c.segment <> '' GROUP BY 1 ORDER BY 2 DESC LIMIT 60", fa).fetchall()
        run = db.execute('SELECT * FROM md_catalog_runs WHERE account_id=%s ORDER BY started_at DESC LIMIT 1', (account_id,)).fetchone()
        size = db.execute('SELECT count(*) AS n, max(refreshed_at) AS at, max(source) AS source FROM md_symbol_catalog WHERE account_id=%s', (account_id,)).fetchone()
        account = _account(db, account_id)
    return {'items': [store.clean(r) for r in rows], 'total': total, 'catalogue_size': size['n'], 'refreshed_at': size['at'] and size['at'].isoformat(),
            'source': size['source'] or source_of(account), 'last_run': run and store.clean(run),
            'facets': {'asset_class': [dict(r) for r in classes], 'exchange': [dict(r) for r in exchanges], 'segment': [dict(r) for r in segments]}}


# ---------- picking: create plans ----------

class Pick(BaseModel):
    model_config = ConfigDict(extra='forbid')
    broker_symbol: str = Field(min_length=1, max_length=64)
    instrument: str | None = Field(default=None, max_length=32)


class CollectIn(BaseModel):
    """Collect picked symbols: one plan each, with these defaults (every option stays editable on the plan)."""
    model_config = ConfigDict(extra='forbid')
    items: list[Pick] = Field(min_length=1, max_length=500)
    purpose: Literal['TRADING_FEED', 'REFERENCE', 'RESEARCH', 'MONITORING'] = 'RESEARCH'
    feed_role: Literal['PRIMARY', 'BACKUP', 'REFERENCE'] = 'PRIMARY'
    data_types: list[Literal['QUOTES', 'DEPTH', 'BARS']] = ['QUOTES', 'BARS']
    bar_timeframes: list[str] = ['M1']
    depth_levels: int = Field(default=0, ge=0, le=50)
    hot_days: int = Field(default=14, ge=1, le=365)
    bars_hot_days: int = Field(default=365, ge=1, le=3650)
    archive: bool = True
    link_instruments: bool = True
    start: bool = True
    reason: str = Field(default='Picked from the source catalogue', min_length=3, max_length=300)


def _code(db, account_code: str, symbol: str) -> str:
    base = re.sub(r'[^A-Z0-9_.-]', '_', f'{account_code}_{symbol}'.upper()).strip('_.-')[:60] or 'PLAN'
    code, n = base, 1
    while db.execute('SELECT 1 FROM md_collection_plans WHERE code=%s', (code,)).fetchone():
        n += 1
        code = f'{base[:58]}_{n}'
    return code


def _places(v: Decimal | None) -> int:
    return max(0, -v.normalize().as_tuple().exponent) if v else 0


def _instrument(actor: str, db, row: dict, name: str) -> tuple[UUID | None, str | None]:
    """The COREI instrument for this symbol: existing by name, or created from the provider's facts."""
    found = db.execute('SELECT id FROM cfg_instruments WHERE symbol=%s', (name,)).fetchone()
    if found:
        return found['id'], None
    digits = row.get('digits')
    tick = row.get('tick_size') or (Decimal(1).scaleb(-int(digits)) if digits is not None else Decimal('0.01'))
    step = row.get('volume_step') or row.get('lot_size') or Decimal(1)
    vmin = row.get('volume_min') or row.get('lot_size') or step
    quote = (row.get('quote_currency') or 'USD')[:10].upper()
    base = (row.get('base_currency') or (name[:3] if row['asset_class'] in ('FX', 'METAL') else quote))[:10].upper()
    try:
        data = InstrumentIn(symbol=name, name=(row.get('description') or name)[:120], asset_class=row['asset_class'] if row['asset_class'] != 'OTHER' else 'EQUITY',
                            base_currency=re.sub(r'[^A-Z0-9]', '', base) or quote, quote_currency=re.sub(r'[^A-Z0-9]', '', quote) or 'USD',
                            price_precision=max(_places(tick), int(digits or 0)), quantity_precision=max(_places(step), _places(vmin)),
                            tick_size=tick, min_quantity=vmin, quantity_step=step, contract_size=row.get('contract_size') or Decimal(1),
                            contract_unit='lot' if row['asset_class'] in ('FX', 'METAL') else 'unit')
    except ValidationError as exc:
        return None, f"instrument {name} not created ({exc.errors()[0]['msg'].removeprefix('Value error, ')})"
    created = store.create(actor, 'instruments', data)
    return UUID(str(created['id'])), None


def collect(actor: str, account_id: UUID, data: CollectIn) -> dict:
    created, skipped, errors, pending, started = [], [], [], [], 0
    with store.transaction('read') as db:
        account = _account(db, account_id)
        cat = {r['broker_symbol']: r for r in db.execute('SELECT * FROM md_symbol_catalog WHERE account_id=%s AND broker_symbol = ANY(%s)',
                                                          (account_id, [p.broker_symbol for p in data.items])).fetchall()}
        have = {r['broker_symbol'] for r in db.execute("SELECT broker_symbol FROM md_collection_plans WHERE account_id=%s AND status <> 'ARCHIVED'",
                                                        (account_id,)).fetchall()}
    for pick in data.items:
        sym = pick.broker_symbol
        if sym in have:
            skipped.append({'broker_symbol': sym, 'reason': 'already collected'})
            continue
        row = cat.get(sym) or _row(broker_symbol=sym, display_symbol=sym, asset_class=mt5_class(sym), suggested_instrument=suggest_mt5(sym))
        instrument_id, note = None, None
        name = (pick.instrument or row.get('suggested_instrument') or '').upper()
        try:
            if data.link_instruments and name:
                with store.transaction('read') as db:
                    instrument_id, note = _instrument(actor, db, row, name)
                if instrument_id:
                    try:
                        store.create(actor, 'mappings', MappingIn(connection_id=account['connection_id'], account_id=account_id, instrument_id=instrument_id,
                                                                  broker_symbol=sym, usage='MARKET_DATA'))
                    except HTTPException as exc:
                        if exc.status_code != 409:  # an existing mapping is fine
                            note = f'mapping not created: {exc.detail}'
            purpose = data.purpose if (data.purpose != 'TRADING_FEED' or instrument_id) else 'RESEARCH'
            with store.transaction('read') as db:
                code = _code(db, account['code'], sym)
            plan = plans.create(actor, plans.PlanIn(code=code, account_id=account_id, broker_symbol=sym, instrument_id=instrument_id, purpose=purpose,
                                                    feed_role=data.feed_role, data_types=data.data_types,
                                                    bar_timeframes=data.bar_timeframes if 'BARS' in data.data_types else [],
                                                    depth_levels=data.depth_levels if 'DEPTH' in data.data_types else 0, hot_days=data.hot_days,
                                                    bars_hot_days=data.bars_hot_days, archive=data.archive))
            item = {'broker_symbol': sym, 'plan_id': plan['id'], 'code': plan['code'], 'instrument': name if instrument_id else None,
                    'purpose': purpose, 'note': note}
            if data.start:
                res = plans.set_status(actor, plan['id'], plans.StatusIn(status='ACTIVE', version=plan['version'], reason=data.reason))
                if isinstance(res, dict) and res.get('change_request'):
                    pending.append(sym)
                else:
                    started += 1
            created.append(item)
        except HTTPException as exc:
            errors.append({'broker_symbol': sym, 'error': str(exc.detail)})
        except ValidationError as exc:
            errors.append({'broker_symbol': sym, 'error': exc.errors()[0]['msg'].removeprefix('Value error, ')})
    return {'created': created, 'started': started, 'awaiting_approval': pending, 'skipped': skipped, 'errors': errors}
