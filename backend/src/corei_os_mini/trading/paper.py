"""Paper accounts: many simulated accounts, each with its own currency, amount, market scope, costs and price source.

Why it exists: paper trading was a book inside a broker account (a broker login was needed, every book started from
one firm-wide amount in the broker account's currency, and fills cost nothing). Traders need several paper accounts —
an INR account for NSE/MCX, a USD account for forex, an EUR account for both — each running any strategy.
Purpose:
  - A paper account is a real cfg_accounts row (environment PAPER) on COREI's built-in simulator connection, so every
    stage (strategies, workspaces, risk, portfolio, post-trade) works on it unchanged. Its starting amount and currency
    are portfolio capital (pf_accounts); its scope, costs and price source live in pf_paper_accounts.
  - Scope: ANY, FOREX (FX and metals), INDIA (NSE, BSE, MCX, NCDEX or INR-quoted) or CUSTOM (asset classes, markets,
    instruments). Enforced in the pre-trade risk check.
  - Costs per fill: commission per lot, % of traded value, minimum per fill (account currency), and slippage ticks.
  - Price source: the freshest feed of the instrument, or one chosen connection.
  - Clone ("reset"): a fresh account with the same settings and any new amount; the old one is archived with its
    history intact. Funds: the amount can be changed (audited).
  - FX rates entered by a person, used only when no collected pair gives a rate.
Must never: send anything to a broker (a paper account has no broker); hide a cost; use binary floating point for an
amount, rate or cost; delete an account's history.
Dependencies: config_store, trading.portfolio, trading.common, trading.governance (risk presets).
Consumers: trading_api (/api/trading/paper-accounts, /api/trading/fx-rates), trading.oms (costs, slippage),
trading.risk (scope), trading.common (price source, FX), strategy_registry (readiness).
Architectural position: Stage 6 portfolio / Stage 7 execution simulation.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from typing_extensions import Annotated

from .. import config_store as store
from .common import D, ZERO

PROVIDER_CODE, CONNECTION_CODE = 'COREI_SIMULATOR', 'COREI_PAPER'
INDIA_MARKETS = ('NSE', 'BSE', 'MCX', 'NCDEX', 'NSE_FO', 'NSE_EQ', 'BSE_EQ', 'NSE_INDEX', 'BSE_INDEX', 'MCX_FO')
FOREX_CLASSES = ('FX', 'METAL')
Currency = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r'^[A-Z]{3}$')]
Money = Annotated[Decimal, Field(ge=0, max_digits=28, decimal_places=10)]
CURRENCIES = ['USD', 'INR', 'EUR', 'GBP', 'JPY', 'AUD', 'CAD', 'CHF', 'NZD', 'SGD', 'AED', 'HKD', 'CNY', 'ZAR', 'SEK', 'NOK']


class PaperIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(pattern=r'^[A-Z][A-Z0-9_]{1,31}$')
    name: str = Field(min_length=1, max_length=120)
    currency: Currency = 'USD'
    amount: Decimal = Field(Decimal('100000'), gt=0, max_digits=20, decimal_places=2)
    scope: Literal['ANY', 'FOREX', 'INDIA', 'CUSTOM'] = 'ANY'
    asset_classes: list[Literal['FX', 'METAL', 'INDEX', 'COMMODITY', 'CRYPTO', 'EQUITY', 'FUTURE', 'OPTION']] = Field(default_factory=list)
    markets: list[Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r'^[A-Z0-9_]{1,20}$')]] = Field(default_factory=list, max_length=20)
    instruments: list[UUID] = Field(default_factory=list, max_length=500)
    commission_per_lot: Money = Decimal(0)
    commission_pct: Decimal = Field(Decimal(0), ge=0, le=10, max_digits=12, decimal_places=6)
    min_commission: Money = Decimal(0)
    slippage_ticks: int | None = Field(None, ge=0, le=1000)
    price_source_id: UUID | None = None
    notes: str = Field('', max_length=1000)
    risk_profile: Literal['NONE', 'RETAIL', 'PROFESSIONAL', 'INSTITUTIONAL'] = 'NONE'   # apply limit presets sized on the amount
    reason: str = Field('Paper account', min_length=5, max_length=500)

    @model_validator(mode='after')
    def _custom(self) -> 'PaperIn':
        if self.scope != 'CUSTOM' and (self.asset_classes or self.markets or self.instruments):
            raise ValueError('Asset classes, markets and instruments are chosen only with the Custom scope.')
        return self


class PaperUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)
    scope: Literal['ANY', 'FOREX', 'INDIA', 'CUSTOM'] = 'ANY'
    asset_classes: list[str] = Field(default_factory=list, max_length=8)
    markets: list[str] = Field(default_factory=list, max_length=20)
    instruments: list[UUID] = Field(default_factory=list, max_length=500)
    commission_per_lot: Money = Decimal(0)
    commission_pct: Decimal = Field(Decimal(0), ge=0, le=10, max_digits=12, decimal_places=6)
    min_commission: Money = Decimal(0)
    slippage_ticks: int | None = Field(None, ge=0, le=1000)
    price_source_id: UUID | None = None
    notes: str = Field('', max_length=1000)
    version: int = Field(ge=1)
    reason: str = Field(min_length=5, max_length=500)


class FundsIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    amount: Decimal = Field(gt=0, max_digits=20, decimal_places=2)
    reason: str = Field(min_length=5, max_length=500)


class CloneIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(pattern=r'^[A-Z][A-Z0-9_]{1,31}$')
    amount: Decimal | None = Field(None, gt=0, max_digits=20, decimal_places=2)
    currency: Currency | None = None
    archive_old: bool = True
    reason: str = Field(min_length=5, max_length=500)


class FxIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base: Currency
    quote: Currency
    rate: Decimal = Field(gt=0, max_digits=30, decimal_places=12)
    note: str = Field('', max_length=200)


# ---------- the simulator connection ----------

def _connection(db) -> str:
    """COREI's built-in simulator provider and connection, created on first use."""
    c = db.execute('SELECT id FROM cfg_connections WHERE code=%s', (CONNECTION_CODE,)).fetchone()
    if c:
        return str(c['id'])
    p = db.execute('SELECT id FROM cfg_providers WHERE code=%s', (PROVIDER_CODE,)).fetchone()
    pid = str(p['id']) if p else str(db.execute("""INSERT INTO cfg_providers(id, code, name, kind) VALUES (%s,%s,%s,'SIMULATOR') RETURNING id""",
                                                (uuid4(), PROVIDER_CODE, 'COREI paper simulator')).fetchone()['id'])
    cid = db.execute("""INSERT INTO cfg_connections(id, provider_id, code, name, platform, environment, purpose, settings)
                        VALUES (%s,%s,%s,%s,'SIMULATOR','PAPER','TRADING','{}'::jsonb) RETURNING id""",
                     (uuid4(), pid, CONNECTION_CODE, 'COREI paper trading (simulated fills at live prices)')).fetchone()['id']
    store.audit(db, 'system', 'CREATE', 'connection', cid, None, {'code': CONNECTION_CODE, 'environment': 'PAPER'}, 'Paper trading simulator')
    return str(cid)


def is_paper(db, account_id) -> bool:
    r = db.execute("""SELECT c.environment FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id WHERE a.id=%s""",
                   (str(account_id),)).fetchone()
    return bool(r) and r['environment'] == 'PAPER'


def settings_of(db, account_id) -> dict | None:
    r = db.execute('SELECT * FROM pf_paper_accounts WHERE account_id=%s', (str(account_id),)).fetchone()
    return dict(r) if r else None


# ---------- scope and costs (used by risk and the OMS) ----------

def scope_reason(db, account_id, instrument: dict) -> str | None:
    """Why this paper account may not trade this instrument, or None."""
    s = settings_of(db, account_id)
    if not s or s['scope'] == 'ANY':
        return None
    sym, cls, mkt = instrument.get('symbol'), instrument.get('asset_class') or '', (instrument.get('market') or '').upper()
    if s['scope'] == 'FOREX' and cls not in FOREX_CLASSES:
        return f'{sym} is {cls.lower()}: this paper account trades forex and metals only'
    if s['scope'] == 'INDIA' and not (mkt.split('_')[0] in ('NSE', 'BSE', 'MCX', 'NCDEX') or instrument.get('quote_currency') == 'INR'):
        return f'{sym} is not an Indian-market instrument: this paper account trades NSE, BSE, MCX and INR-quoted instruments only'
    if s['scope'] == 'CUSTOM':
        if s['instruments'] and str(instrument['id']) not in {str(i) for i in s['instruments']}:
            return f'{sym} is not in this paper account\'s instrument list'
        if s['asset_classes'] and cls not in s['asset_classes']:
            return f'{sym} is {cls.lower()}: not an asset class this paper account trades'
        if s['markets'] and mkt not in s['markets'] and mkt.split('_')[0] not in s['markets']:
            return f'{sym} trades on {mkt or "no listed market"}: not a market this paper account trades'
    return None


def commission(db, account_id, instrument: dict, qty: Decimal, price: Decimal) -> Decimal | None:
    """The cost of one fill in the instrument's quote currency (the unit of realised P&L); None when no cost is set or
    no FX rate converts the account currency."""
    s = settings_of(db, account_id)
    if not s:
        return None
    per_lot, pct, minimum = D(s['commission_per_lot']) or ZERO, D(s['commission_pct']) or ZERO, D(s['min_commission']) or ZERO
    if not (per_lot or pct or minimum):
        return None
    from .common import account_currency, fx_rate
    quote_ccy = instrument.get('quote_currency') or 'USD'
    rate = fx_rate(db, account_currency(db, account_id), quote_ccy)          # 1 account currency in quote currency
    if rate is None:
        return None
    size = D(instrument.get('contract_size')) or Decimal(1)
    value_quote = abs(qty) * size * price
    cost_quote = per_lot * abs(qty) * rate + value_quote * pct / 100
    cost_quote = max(cost_quote, minimum * rate)
    return cost_quote.quantize(Decimal('0.0000000001'))


def slippage_ticks(db, account_id) -> int | None:
    s = settings_of(db, account_id)
    return s['slippage_ticks'] if s and s['slippage_ticks'] is not None else None


def price_source(db, account_id) -> str | None:
    s = settings_of(db, account_id)
    return str(s['price_source_id']) if s and s['price_source_id'] else None


# ---------- accounts ----------

def _public(db, account_id) -> dict:
    from . import portfolio
    a = db.execute("""SELECT a.id, a.code, a.name, a.status, a.base_currency, a.created_at, p.*, x.code AS price_source_code, y.code AS cloned_from_code
                      FROM cfg_accounts a JOIN pf_paper_accounts p ON p.account_id=a.id LEFT JOIN cfg_connections x ON x.id=p.price_source_id
                      LEFT JOIN cfg_accounts y ON y.id=p.cloned_from WHERE a.id=%s""", (str(account_id),)).fetchone()
    if not a:
        raise HTTPException(404, 'Paper account not found.')
    eq = portfolio.equity(db, account_id, 'PAPER')
    stats = db.execute("""SELECT count(*) AS fills, count(*) FILTER (WHERE realised_pnl > 0) AS wins, count(*) FILTER (WHERE realised_pnl < 0) AS losses,
                                 count(DISTINCT deployment_id) AS runs, COALESCE(sum(commission), 0) AS costs_quote
                          FROM oms_fills WHERE account_id=%s AND mode='PAPER'""", (str(account_id),)).fetchone()
    open_n = db.execute("SELECT count(*) AS n FROM pf_positions WHERE account_id=%s AND mode='PAPER' AND quantity <> 0", (str(account_id),)).fetchone()['n']
    start = D(eq['starting_capital'])
    ret = ((eq['equity'] - start) / start * 100).quantize(Decimal('0.01')) if start else None
    return {**store.clean(dict(a)), 'id': str(a['id']), 'currency': eq['currency'], 'starting_amount': str(start), 'equity': str(eq['equity'].quantize(Decimal('0.01'))),
            'realised': str(eq['realised'].quantize(Decimal('0.01'))), 'unrealised': str(eq['unrealised'].quantize(Decimal('0.01'))),
            'return_pct': str(ret) if ret is not None else None, 'drawdown_pct': str(eq['drawdown_pct']), 'complete': eq['complete'],
            'missing_fx': eq['missing_fx'], 'open_positions': open_n, 'fills': stats['fills'], 'wins': stats['wins'], 'losses': stats['losses'],
            'runs': stats['runs']}


def listing(db, include_archived: bool = False) -> list[dict]:
    rows = db.execute("""SELECT a.id FROM cfg_accounts a JOIN pf_paper_accounts p ON p.account_id=a.id
                         WHERE %s OR a.status <> 'ARCHIVED' ORDER BY a.status, a.code""", (include_archived,)).fetchall()
    return [_public(db, r['id']) for r in rows]


def create(actor: str, data: PaperIn, cloned_from=None) -> dict:
    with store.transaction('create') as db:
        if db.execute('SELECT 1 FROM cfg_accounts WHERE code=%s', (data.code,)).fetchone():
            raise HTTPException(409, f'An account {data.code} already exists. Choose another code.')
        if data.price_source_id and not db.execute("SELECT 1 FROM cfg_connections WHERE id=%s AND purpose IN ('MARKET_DATA','BOTH')",
                                                   (str(data.price_source_id),)).fetchone():
            raise HTTPException(422, 'The price source must be a market-data connection.')
        cid = _connection(db)
        acc = db.execute("""INSERT INTO cfg_accounts(id, connection_id, code, name, external_account_id, base_currency, trading_permission)
                            VALUES (%s,%s,%s,%s,%s,%s,'TRADING') RETURNING *""",
                         (uuid4(), cid, data.code, data.name, f'PAPER-{data.code}'[:64], data.currency)).fetchone()
        db.execute('INSERT INTO pf_accounts(account_id, starting_capital, currency, updated_by) VALUES (%s,%s,%s,%s)',
                   (acc['id'], data.amount, data.currency, actor))
        db.execute("""INSERT INTO pf_paper_accounts(account_id, scope, asset_classes, markets, instruments, commission_per_lot, commission_pct,
                          min_commission, slippage_ticks, price_source_id, cloned_from, notes, updated_by)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                   (acc['id'], data.scope, data.asset_classes, data.markets, [str(i) for i in data.instruments], data.commission_per_lot,
                    data.commission_pct, data.min_commission, data.slippage_ticks, str(data.price_source_id) if data.price_source_id else None,
                    str(cloned_from) if cloned_from else None, data.notes, actor))
        store.audit(db, actor, 'CREATE', 'account', acc['id'], None, {**store.clean(dict(acc)), 'paper': True, 'amount': str(data.amount),
                                                                       'currency': data.currency, 'scope': data.scope}, data.reason)
        account_id = acc['id']
    if data.risk_profile != 'NONE':
        from . import governance
        governance.apply_preset(actor, governance.PresetIn(profile=data.risk_profile, account_id=account_id, equity=data.amount,
                                                           reason=f'{data.risk_profile.lower()} limits for paper account {data.code}'))
    with store.transaction('read') as db:
        return _public(db, account_id)


def update(actor: str, account_id: UUID, data: PaperUpdate) -> dict:
    with store.transaction('update') as db:
        before = db.execute('SELECT * FROM pf_paper_accounts WHERE account_id=%s FOR UPDATE', (str(account_id),)).fetchone()
        if not before:
            raise HTTPException(404, 'Paper account not found.')
        if before['version'] != data.version:
            raise HTTPException(409, 'This paper account was changed by someone else. Reload it.')
        if data.scope != 'CUSTOM' and (data.asset_classes or data.markets or data.instruments):
            raise HTTPException(422, 'Asset classes, markets and instruments are chosen only with the Custom scope.')
        db.execute('UPDATE cfg_accounts SET name=%s, version=version+1, updated_at=now() WHERE id=%s', (data.name, str(account_id)))
        after = db.execute("""UPDATE pf_paper_accounts SET scope=%s, asset_classes=%s, markets=%s, instruments=%s, commission_per_lot=%s,
                                  commission_pct=%s, min_commission=%s, slippage_ticks=%s, price_source_id=%s, notes=%s, version=version+1,
                                  updated_by=%s, updated_at=now() WHERE account_id=%s RETURNING *""",
                           (data.scope, [a.upper() for a in data.asset_classes], [m.upper() for m in data.markets], [str(i) for i in data.instruments],
                            data.commission_per_lot, data.commission_pct, data.min_commission, data.slippage_ticks,
                            str(data.price_source_id) if data.price_source_id else None, data.notes, actor, str(account_id))).fetchone()
        store.audit(db, actor, 'UPDATE', 'paper account', account_id, dict(before), dict(after), data.reason)
        return _public(db, account_id)


def set_funds(actor: str, account_id: UUID, data: FundsIn) -> dict:
    from . import portfolio
    with store.transaction('read') as db:
        if not is_paper(db, account_id):
            raise HTTPException(404, 'Paper account not found.')
    portfolio.set_capital(actor, account_id, data.amount, data.reason)
    with store.transaction('read') as db:
        return _public(db, account_id)


def clone(actor: str, account_id: UUID, data: CloneIn) -> dict:
    """Start again: a new paper account with the same settings (and any new amount or currency); the old one is archived
    with its whole history, never wiped."""
    with store.transaction('read') as db:
        old = _public(db, account_id)
        runs = db.execute("SELECT count(*) AS n FROM cfg_deployments WHERE account_id=%s AND status='ACTIVE'", (str(account_id),)).fetchone()['n']
    if data.archive_old and runs:
        raise HTTPException(422, f'{old["code"]} still has {runs} active run(s). Pause or move them first, or keep the old account.')
    new = create(actor, PaperIn(code=data.code, name=old['name'], currency=data.currency or old['currency'], amount=data.amount or D(old['starting_amount']),
                                scope=old['scope'], asset_classes=old['asset_classes'] if old['scope'] == 'CUSTOM' else [],
                                markets=old['markets'] if old['scope'] == 'CUSTOM' else [],
                                instruments=old['instruments'] if old['scope'] == 'CUSTOM' else [],
                                commission_per_lot=D(old['commission_per_lot']), commission_pct=D(old['commission_pct']),
                                min_commission=D(old['min_commission']), slippage_ticks=old['slippage_ticks'],
                                price_source_id=old['price_source_id'], notes=old['notes'], reason=data.reason), cloned_from=account_id)
    if data.archive_old:
        archive(actor, account_id, f"Replaced by {data.code}: {data.reason}")
    return new


def archive(actor: str, account_id: UUID, reason: str) -> dict:
    with store.transaction('update') as db:
        if not is_paper(db, account_id):
            raise HTTPException(404, 'Paper account not found.')
        runs = db.execute("SELECT count(*) AS n FROM cfg_deployments WHERE account_id=%s AND status='ACTIVE'", (str(account_id),)).fetchone()['n']
        if runs:
            raise HTTPException(422, f'This paper account has {runs} active run(s). Pause them first.')
        before = db.execute('SELECT * FROM cfg_accounts WHERE id=%s FOR UPDATE', (str(account_id),)).fetchone()
        after = db.execute("UPDATE cfg_accounts SET status='ARCHIVED', version=version+1, updated_at=now() WHERE id=%s RETURNING *",
                           (str(account_id),)).fetchone()
        store.audit(db, actor, 'ARCHIVE', 'account', account_id, dict(before), dict(after), reason)
        return _public(db, account_id)


# ---------- FX rates entered by a person ----------

def fx_rates(db) -> list[dict]:
    return [store.clean(dict(r)) for r in db.execute('SELECT * FROM cfg_fx_rates ORDER BY base, quote').fetchall()]


def set_fx(actor: str, data: FxIn) -> dict:
    if data.base == data.quote:
        raise HTTPException(422, 'Choose two different currencies.')
    with store.transaction('write') as db:
        before = db.execute('SELECT * FROM cfg_fx_rates WHERE base=%s AND quote=%s FOR UPDATE', (data.base, data.quote)).fetchone()
        row = db.execute("""INSERT INTO cfg_fx_rates(base, quote, rate, note, set_by) VALUES (%s,%s,%s,%s,%s)
                            ON CONFLICT (base, quote) DO UPDATE SET rate=EXCLUDED.rate, note=EXCLUDED.note, set_by=EXCLUDED.set_by, set_at=now()
                            RETURNING *""", (data.base, data.quote, data.rate, data.note, actor)).fetchone()
        store.audit(db, actor, 'SET_FX_RATE', 'fx rate', f'{data.base}{data.quote}', dict(before) if before else None, dict(row), data.note or 'Manual rate')
        return store.clean(dict(row))


def delete_fx(actor: str, base: str, quote: str) -> None:
    with store.transaction('delete') as db:
        before = db.execute('DELETE FROM cfg_fx_rates WHERE base=%s AND quote=%s RETURNING *', (base.upper(), quote.upper())).fetchone()
        if not before:
            raise HTTPException(404, 'Rate not found.')
        store.audit(db, actor, 'DELETE_FX_RATE', 'fx rate', f'{base}{quote}', dict(before), None, 'Manual rate removed')
