"""Market hours per exchange (Stage 1 · Market data).

Why it exists: Indian exchanges are not open around the clock like FX. NSE equities and F&O trade 09:15–15:30 IST,
MCX until late evening, FX from Sunday to Friday evening New York. Collection schedules, stale-price alarms and the
sessions screen must use the hours of the market an instrument actually trades on, or every evening would raise
false "stale" alarms on NIFTY and every Saturday on gold.
Purpose: a fixed registry of markets (code, time zone, opening and closing time per weekday, pre-open) and pure
functions: is_open(market, t, holidays), market_for(...) to infer a market from the instrument or broker symbol,
describe(market) for screens.
Responsibilities: the hours above; holidays are passed in by the caller (provider holidays and calendar HOLIDAY
events in the market's currency), because the clock and the database are not read here.
Must never: read the clock or the database, or decide anything about orders (an order-session check belongs to the
risk stage, which may use this registry).
Dependencies: zoneinfo. Consumers: md.collector (schedule, stale), md.catalog (instrument facts), intel_api
(sessions screen), tests.
Architectural position: Stage 1, beside the collector.
Conventions: hours are local exchange time, so daylight-saving changes are handled by the time zone; MCX closes at
23:30 IST while New York is on daylight-saving time and 23:55 IST otherwise (its published rule). Exchange special
sessions (e.g. Muhurat trading) are added as calendar events, not here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')


@dataclass(frozen=True)
class Market:
    code: str
    name: str
    zone: str
    open: time | None          # None: open round the clock on its trading days
    close: time | None
    days: tuple[int, ...]      # ISO weekdays it trades
    pre_open: time | None = None
    currency: str = ''         # whose HOLIDAY calendar events close it
    note: str = ''


MARKETS: dict[str, Market] = {m.code: m for m in (
    Market('NSE', 'NSE equities and indices', 'Asia/Kolkata', time(9, 15), time(15, 30), (1, 2, 3, 4, 5), time(9, 0), 'INR', 'Pre-open 09:00–09:08, closing session to 16:00'),
    Market('NSE_FO', 'NSE futures and options', 'Asia/Kolkata', time(9, 15), time(15, 30), (1, 2, 3, 4, 5), None, 'INR'),
    Market('NSE_CD', 'NSE currency derivatives', 'Asia/Kolkata', time(9, 0), time(17, 0), (1, 2, 3, 4, 5), None, 'INR'),
    Market('BSE', 'BSE equities and indices', 'Asia/Kolkata', time(9, 15), time(15, 30), (1, 2, 3, 4, 5), time(9, 0), 'INR'),
    Market('BSE_FO', 'BSE futures and options', 'Asia/Kolkata', time(9, 15), time(15, 30), (1, 2, 3, 4, 5), None, 'INR'),
    Market('MCX', 'MCX commodities', 'Asia/Kolkata', time(9, 0), time(23, 30), (1, 2, 3, 4, 5), None, 'INR', 'Closes 23:55 IST while New York is on standard time'),
    Market('FX', 'FX and CFDs (24×5)', 'America/New_York', None, None, (1, 2, 3, 4, 5, 7), None, '', 'Sunday 17:00 to Friday 17:00 New York'),
    Market('CRYPTO', 'Crypto (24×7)', 'UTC', None, None, (1, 2, 3, 4, 5, 6, 7)),
)}
LABELS = {code: m.name for code, m in MARKETS.items()}


def market_for(market: str | None = None, broker_symbol: str = '', platform: str = '', asset_class: str = '') -> str | None:
    """The market an instrument trades on: the instrument's own setting first, then the broker symbol (Upstox keys
    start with the segment, e.g. NSE_INDEX|Nifty 50, NSE_FO|..., MCX_FO|...), then the asset class for MT5 CFDs.
    None when unknown (treated as always open, the behaviour before markets existed)."""
    if market and market in MARKETS:
        return market
    prefix = broker_symbol.split('|', 1)[0].upper() if '|' in broker_symbol else ''
    if prefix:
        if prefix.startswith('NSE_FO'):
            return 'NSE_FO'
        if prefix.startswith(('NCD_', 'NSE_CD')):
            return 'NSE_CD'
        if prefix.startswith('NSE_'):
            return 'NSE'
        if prefix.startswith('BFO') or prefix.startswith('BSE_FO'):
            return 'BSE_FO'
        if prefix.startswith('BSE_'):
            return 'BSE'
        if prefix.startswith('MCX'):
            return 'MCX'
    if platform in ('MT5', 'MT4', 'CTRADER') and asset_class in ('FX', 'METAL'):
        return 'FX'
    if asset_class == 'CRYPTO':
        return 'CRYPTO'
    return None


def _close(m: Market, local: datetime) -> time | None:
    if m.code == 'MCX':
        ny_dst = bool(local.astimezone(NY).dst())
        return time(23, 30) if ny_dst else time(23, 55)
    return m.close


def is_open(market: str | None, t: datetime, holidays: set[date] | frozenset = frozenset(), pre_open: bool = False) -> bool:
    """Is the market trading at instant t (timezone-aware)? Unknown market: always open."""
    m = MARKETS.get(market or '')
    if m is None:
        return True
    local = t.astimezone(ZoneInfo(m.zone))
    if m.code == 'FX':
        wd, hm = local.isoweekday(), local.time()
        if wd == 6:
            return False
        if wd == 7:
            return hm >= time(17)
        if wd == 5:
            return hm < time(17)
        return True
    if local.isoweekday() not in m.days or local.date() in holidays:
        return False
    if m.open is None:
        return True
    start = m.pre_open if (pre_open and m.pre_open) else m.open
    return start <= local.time() < _close(m, local)


def next_change(market: str, t: datetime, holidays: set[date] | frozenset = frozenset()) -> dict | None:
    """The next opening or closing after t (searched minute by minute over the coming 8 days, coarse first)."""
    if market not in MARKETS:
        return None
    now = is_open(market, t, holidays)
    step = timedelta(minutes=5)
    probe = t
    for _ in range(8 * 24 * 12):
        probe += step
        if is_open(market, probe, holidays) != now:
            back = probe - step
            while is_open(market, back + timedelta(minutes=1), holidays) == now:
                back += timedelta(minutes=1)
            return {'at': (back + timedelta(minutes=1)).isoformat(), 'event': 'closes' if now else 'opens'}
    return None


def describe(market: str, t: datetime, holidays: set[date] | frozenset = frozenset()) -> dict:
    m = MARKETS[market]
    close = _close(m, t.astimezone(ZoneInfo(m.zone)))
    hours = 'round the clock' if m.open is None else f"{m.open:%H:%M}–{close:%H:%M} {'IST' if m.zone == 'Asia/Kolkata' else m.zone}"
    return {'code': m.code, 'name': m.name, 'zone': m.zone, 'hours': hours, 'note': m.note, 'currency': m.currency,
            'open': is_open(market, t, holidays), 'next': next_change(market, t, holidays)}


def session_text(market: str | None) -> str:
    """A one-line description for an instrument's trading-session field."""
    m = MARKETS.get(market or '')
    if m is None:
        return ''
    if m.code == 'FX':
        return 'Sun 17:00 – Fri 17:00 New York'
    if m.open is None:
        return 'Every day, round the clock'
    return f"Mon–Fri {m.open:%H:%M}–{m.close:%H:%M} IST" + (' (23:55 in US winter)' if m.code == 'MCX' else '')
