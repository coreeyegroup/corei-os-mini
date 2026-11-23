"""Bars built from ticks. Pure; no I/O.

Why it exists: every chart, feature, signal and backtest reads these bars, so a bar must mean exactly what it says.
Purpose: turn a stream of quotes into OHLC bars per timeframe, stating how each bar was made.
Responsibilities:
  - Time: a quote goes into the bar of its exchange (event) time when the source states one that is plausible
    (within MAX_EVENT_SKEW of receipt); otherwise its receipt time. The bar records which (`time_basis`).
  - Alignment (`anchor`): markets with fixed hours (NSE, BSE, MCX, see markets.py) align intraday bars to the
    session open in exchange time (NSE H1 = 09:15, 10:15 ... 15:15), and D1 to the exchange-local midnight, so the
    bars match exchange and broker charts. Round-the-clock markets (FX, crypto, unknown) align to UTC, as before.
  - Price (`price_basis`): exchange instruments use the last traded price (bid as a fall-back before the first
    trade); FX/CFD use the bid (the MT5 convention).
  - Volume (`volume_kind`): a source that reports the day's running total (Upstox `volume` = traded today) adds
    only the increase since the previous quote (EXCHANGE); a total that falls (new day) starts again. Other sources'
    per-update volume is summed (PER_UPDATE).
  A bar closes when a quote of a later period arrives or LATE_GRACE after its period has passed (quiet markets still
  close). A quote received more than LATE_GRACE after the end of its bar is late for that bar and is not added to it
  (decided from the quote's own times, so the journal replay makes the same choice); a quote older than the open bar
  is ignored for building. Late and old quotes are still stored as ticks.
Must never: read the clock or the database, or round prices (prices are stored as received; fixed-point storage is
  a separate decision, see the Stage 1 audit MD-6).
Dependencies: markets (session hours), zoneinfo. Consumers: md.collector, md.replay, tests.
Architectural position: Stage 1 · Market data, inside the collector process.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import markets

FRAMES = {'S1': 1, 'M1': 60, 'M5': 300, 'M15': 900, 'M30': 1800, 'H1': 3600, 'H4': 14400, 'D1': 86400}
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
MAX_EVENT_SKEW = timedelta(minutes=2)  # an event time further than this from receipt is not trusted for bucketing
LATE_GRACE = timedelta(seconds=2)       # a bar stays open this long after its end for prices still on their way
EXCHANGE_MARKETS = {'NSE', 'NSE_FO', 'NSE_CD', 'BSE', 'BSE_FO', 'MCX'}


def floor(t: datetime, seconds: int) -> datetime:
    """UTC-epoch alignment (round-the-clock markets, quality minutes)."""
    return EPOCH + timedelta(seconds=int((t - EPOCH).total_seconds()) // seconds * seconds)


def anchor_of(market: str | None) -> str:
    m = markets.MARKETS.get(market or '')
    return 'SESSION' if m is not None and m.open is not None else 'UTC'


def bucket(t: datetime, seconds: int, market: str | None = None) -> datetime:
    """The start of the bar holding instant t. Session markets: intraday bars counted from the session open in
    exchange time, D1 from exchange-local midnight; a quote before the open (pre-open) belongs to the bar counted
    back from the open. Others: UTC epoch."""
    m = markets.MARKETS.get(market or '')
    if m is None or m.open is None:
        return floor(t, seconds)
    zone = ZoneInfo(m.zone)
    local = t.astimezone(zone)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if seconds >= 86400:
        return midnight.astimezone(timezone.utc)
    start = midnight.replace(hour=m.open.hour, minute=m.open.minute)
    offset = (local - start).total_seconds()
    return (start + timedelta(seconds=(offset // seconds) * seconds)).astimezone(timezone.utc)


def event_time(rx: datetime, source_utc: datetime | None) -> tuple[datetime, str]:
    """The time to bucket by, and its basis (EVENT or RECEIPT)."""
    if source_utc is not None and abs(rx - source_utc) <= MAX_EVENT_SKEW:
        return source_utc, 'EVENT'
    return rx, 'RECEIPT'


class BarBuilder:
    def __init__(self, frames: list[str], market: str | None = None, price_basis: str | None = None,
                 cumulative_volume: bool = False):
        self.frames = [f for f in frames if f in FRAMES]
        self.market = market
        self.anchor = anchor_of(market)
        self.price_basis = price_basis or ('LAST' if market in EXCHANGE_MARKETS else 'BID')
        self.cumulative_volume = cumulative_volume
        self.volume_kind = 'EXCHANGE' if cumulative_volume else 'PER_UPDATE'
        self.day_volume: float | None = None
        self.open: dict[str, dict] = {}

    def _price(self, bid: float | None, last: float | None) -> float | None:
        if self.price_basis == 'LAST':
            return last or bid
        return bid or last

    def _volume(self, volume: float | None) -> float:
        if volume is None:
            return 0.0
        if not self.cumulative_volume:
            return float(volume)
        v = float(volume)
        previous, self.day_volume = self.day_volume, v
        if previous is None:
            return 0.0          # the first total of a session says nothing about this bar
        return v - previous if v >= previous else v   # a lower total: a new trading day started

    def add(self, t: datetime, bid: float | None, ask: float | None, last: float | None = None,
            volume: float | None = None, time_basis: str = 'RECEIPT', received: datetime | None = None) -> list[dict]:
        price = self._price(bid, last)
        traded = self._volume(volume)
        if not price:
            return []
        closed = []
        spread = ask - bid if bid and ask else None
        for frame in self.frames:
            start = bucket(t, FRAMES[frame], self.market)
            if received is not None and received > start + timedelta(seconds=FRAMES[frame]) + LATE_GRACE:
                continue   # too late for its bar (the timer may already have closed it)
            bar = self.open.get(frame)
            if bar and start < bar['bar_time']:
                continue
            if bar and start > bar['bar_time']:
                closed.append(self._close(frame))
                bar = None
            if bar is None:
                self.open[frame] = {'timeframe': frame, 'bar_time': start, 'open': price, 'high': price, 'low': price, 'close': price,
                                    'ticks': 1, 'spread_sum': spread or 0.0, 'spread_n': 1 if spread is not None else 0,
                                    'volume': traded, 'time_basis': time_basis}
            else:
                bar['high'], bar['low'], bar['close'] = max(bar['high'], price), min(bar['low'], price), price
                bar['ticks'] += 1
                bar['volume'] += traded
                if time_basis == 'RECEIPT':
                    bar['time_basis'] = 'RECEIPT'   # one receipt-timed quote makes the whole bar receipt-timed
                if spread is not None:
                    bar['spread_sum'] += spread
                    bar['spread_n'] += 1
        return closed

    def due(self, now: datetime) -> list[dict]:
        """Close bars whose period has ended."""
        return [self._close(f) for f, bar in list(self.open.items())
                if now >= bar['bar_time'] + timedelta(seconds=FRAMES[f]) + LATE_GRACE]

    def partial(self) -> list[dict]:
        return [self._row(bar, complete=False) for bar in self.open.values()]

    def _close(self, frame: str) -> dict:
        return self._row(self.open.pop(frame), complete=True)

    def _row(self, bar: dict, complete: bool) -> dict:
        return {'timeframe': bar['timeframe'], 'bar_time': bar['bar_time'], 'open': bar['open'], 'high': bar['high'],
                'low': bar['low'], 'close': bar['close'], 'ticks': bar['ticks'],
                'spread_avg': bar['spread_sum'] / bar['spread_n'] if bar['spread_n'] else None,
                'volume': bar['volume'], 'complete': complete,
                'price_basis': self.price_basis, 'volume_kind': self.volume_kind, 'anchor': self.anchor,
                'time_basis': bar['time_basis'], 'derivation': 'TICKS'}
