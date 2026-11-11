"""Feed quality per account × symbol, scored every minute. Pure; no I/O.

Measured from the live stream: latency (receive time − broker time converted to UTC),
the longest silence, frozen prices (identical bid/ask while time passes), spread in
points, spikes (a move far outside recent volatility that reverses on the next update),
update count, and the deviation from other sources for the same instrument (set by the
collector). Threshold breaches are reported once when they start, with the measured value.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median

from .bars import floor


@dataclass
class Limits:
    max_latency_ms: float = 1500
    max_gap_seconds: float = 60
    max_spread_points: float | None = None
    stale_seconds: float = 10
    deviation_bp: float = 10


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(math.ceil(q * len(ordered))) - 1)]


def score(row: dict, limits: Limits) -> float:
    if not row['ticks']:
        return 0.0
    s = 100.0
    if row['latency_p95_ms'] is not None and row['latency_p95_ms'] > limits.max_latency_ms:
        s -= min(30.0, 15.0 + 15.0 * (row['latency_p95_ms'] / limits.max_latency_ms - 1.0))
    if row['max_gap_ms'] is not None and row['max_gap_ms'] > limits.max_gap_seconds * 1000:
        s -= 25.0
    s -= 30.0 * min(1.0, row['frozen_ms'] / 60000.0)
    if limits.max_spread_points and row['spread_max'] is not None and row['spread_max'] > limits.max_spread_points:
        s -= 15.0
    s -= min(20.0, 10.0 * row['spikes'])
    if row['deviation_bp'] is not None and abs(row['deviation_bp']) > limits.deviation_bp:
        s -= 15.0
    return max(0.0, min(100.0, round(s, 1)))


@dataclass
class SymbolQuality:
    limits: Limits
    point: float = 0.00001
    minute: datetime | None = None
    last_rx: datetime | None = None
    last_bid: float | None = None
    last_ask: float | None = None
    last_mid: float | None = None
    var: float = 0.0
    n: int = 0
    pending_spike: float | None = None
    breaching: set = field(default_factory=set)
    _lat: list = field(default_factory=list)
    _spreads: list = field(default_factory=list)
    _ticks: int = 0
    _max_gap: float = 0.0
    _frozen: float = 0.0
    _spikes: int = 0
    _deviation: float | None = None

    def on_tick(self, rx: datetime, source_utc: datetime | None, bid: float | None, ask: float | None) -> tuple[list[dict], list[dict]]:
        """Returns (finished minute rows, events)."""
        rows, events = [], []
        minute = floor(rx, 60)
        if self.minute is not None and minute > self.minute:
            rows.append(self.close_minute())
        if self.minute is None or minute > self.minute:
            self.minute = minute
        self._ticks += 1
        if source_utc is not None:
            latency = (rx - source_utc).total_seconds() * 1000
            self._lat.append(latency)
            self._edge(events, 'LATENCY', latency > self.limits.max_latency_ms, {'latency_ms': round(latency, 1), 'limit_ms': self.limits.max_latency_ms})
        if self.last_rx is not None:
            gap = (rx - self.last_rx).total_seconds() * 1000
            self._max_gap = max(self._max_gap, gap)
            if gap > self.limits.max_gap_seconds * 1000:
                events.append({'kind': 'GAP', 'severity': 'WARNING', 'detail': {'gap_seconds': round(gap / 1000, 1), 'from': self.last_rx.isoformat(), 'to': rx.isoformat()}})
            if bid == self.last_bid and ask == self.last_ask:
                self._frozen += gap
        if bid and ask:
            spread = (ask - bid) / self.point if self.point else ask - bid
            self._spreads.append(spread)
            if self.limits.max_spread_points:
                self._edge(events, 'SPREAD', spread > self.limits.max_spread_points, {'spread_points': round(spread, 1), 'limit_points': self.limits.max_spread_points})
            mid = (bid + ask) / 2
            if self.last_mid:
                r = (mid - self.last_mid) / self.last_mid
                sigma = math.sqrt(self.var) if self.var > 0 else 0.0
                if self.pending_spike is not None and r * self.pending_spike < 0 and abs(r) >= 0.5 * abs(self.pending_spike):
                    self._spikes += 1
                    events.append({'kind': 'SPIKE', 'severity': 'WARNING', 'detail': {'move_bp': round(self.pending_spike * 1e4, 2), 'sigma_bp': round(sigma * 1e4, 3)}})
                    self.pending_spike = None
                elif self.n > 50 and sigma > 0 and abs(r) > 8 * sigma:
                    self.pending_spike = r
                else:
                    self.pending_spike = None
                    self.var = 0.98 * self.var + 0.02 * r * r if self.n else r * r
                self.n += 1
            self.last_mid = mid
        self.last_rx, self.last_bid, self.last_ask = rx, bid, ask
        return rows, events

    def set_deviation(self, bp: float | None) -> list[dict]:
        events: list[dict] = []
        if bp is None:
            return events
        if self._deviation is None or abs(bp) > abs(self._deviation):
            self._deviation = bp
        self._edge(events, 'DEVIATION', abs(bp) > self.limits.deviation_bp, {'deviation_bp': round(bp, 2), 'limit_bp': self.limits.deviation_bp})
        return events

    def stale(self, now: datetime) -> list[dict]:
        """Called on a timer while the plan should be collecting."""
        events: list[dict] = []
        age = (now - self.last_rx).total_seconds() if self.last_rx else None
        self._edge(events, 'STALE', age is None or age > self.limits.stale_seconds,
                   {'seconds_since_last_price': None if age is None else round(age, 1), 'limit_seconds': self.limits.stale_seconds})
        return events

    def roll(self, now: datetime) -> list[dict]:
        """Close the minute when no tick arrived to close it."""
        if self.minute is not None and floor(now, 60) > self.minute:
            row = self.close_minute()
            self.minute = floor(now, 60)
            return [row]
        if self.minute is None:
            self.minute = floor(now, 60)
        return []

    def healthy(self, now: datetime) -> bool:
        return 'STALE' not in self.breaching and 'LATENCY' not in self.breaching and self.last_rx is not None \
            and (now - self.last_rx).total_seconds() <= self.limits.stale_seconds

    def close_minute(self) -> dict:
        frozen = self._frozen
        if self.last_rx is not None and self.minute is not None and self._ticks == 0:
            frozen = 0.0
        row = {'minute': self.minute, 'ticks': self._ticks,
               'latency_p50_ms': percentile(self._lat, 0.5), 'latency_p95_ms': percentile(self._lat, 0.95),
               'max_gap_ms': self._max_gap if self._ticks > 1 else None,
               'spread_avg': sum(self._spreads) / len(self._spreads) if self._spreads else None,
               'spread_max': max(self._spreads) if self._spreads else None,
               'frozen_ms': min(frozen, 60000.0), 'spikes': self._spikes, 'deviation_bp': self._deviation}
        row['score'] = score(row, self.limits)
        self._lat, self._spreads, self._ticks, self._max_gap, self._frozen, self._spikes, self._deviation = [], [], 0, 0.0, 0.0, 0, None
        return row

    def _edge(self, events: list[dict], kind: str, breach: bool, detail: dict) -> None:
        if breach and kind not in self.breaching:
            self.breaching.add(kind)
            events.append({'kind': kind, 'severity': 'WARNING', 'detail': {**detail, 'state': 'started'}})
        elif not breach and kind in self.breaching:
            self.breaching.discard(kind)
            events.append({'kind': kind, 'severity': 'INFO', 'detail': {**detail, 'state': 'cleared'}})


def consensus_deviation(mine: float, others: list[float]) -> float | None:
    """Deviation of my mid from the median of the other sources, in basis points."""
    if not others:
        return None
    ref = median(others)
    return (mine - ref) / ref * 1e4 if ref else None


def server_offset(server_time: str | None, utc_now: str | None) -> timedelta:
    """Broker server time minus UTC, rounded to 15 minutes (as in broker_session)."""
    if not server_time or not utc_now:
        return timedelta(0)
    delta = datetime.fromisoformat(server_time) - datetime.fromisoformat(utc_now)
    return timedelta(minutes=round(delta.total_seconds() / 60 / 15) * 15)
