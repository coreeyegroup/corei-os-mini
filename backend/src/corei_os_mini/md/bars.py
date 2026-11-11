"""Bars built from ticks. Pure; no I/O.

Bars use the bid price (the MT5 convention) and record tick count and average spread.
Periods are aligned to UTC. A bar closes when a tick of a later period arrives or when
its period has passed (so quiet markets still close their bars); a tick older than the
open bar is ignored for building (it is still stored as a tick).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

FRAMES = {'S1': 1, 'M1': 60, 'M5': 300, 'M15': 900, 'M30': 1800, 'H1': 3600, 'H4': 14400, 'D1': 86400}
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def floor(t: datetime, seconds: int) -> datetime:
    return EPOCH + timedelta(seconds=int((t - EPOCH).total_seconds()) // seconds * seconds)


class BarBuilder:
    def __init__(self, frames: list[str]):
        self.frames = [f for f in frames if f in FRAMES]
        self.open: dict[str, dict] = {}

    def add(self, t: datetime, bid: float | None, ask: float | None, last: float | None = None,
            volume: float | None = None) -> list[dict]:
        price = bid if bid else last
        if not price:
            return []
        closed = []
        spread = ask - bid if bid and ask else None
        for frame in self.frames:
            start = floor(t, FRAMES[frame])
            bar = self.open.get(frame)
            if bar and start < bar['bar_time']:
                continue
            if bar and start > bar['bar_time']:
                closed.append(self._close(frame))
                bar = None
            if bar is None:
                self.open[frame] = {'timeframe': frame, 'bar_time': start, 'open': price, 'high': price, 'low': price, 'close': price,
                                    'ticks': 1, 'spread_sum': spread or 0.0, 'spread_n': 1 if spread is not None else 0,
                                    'volume': volume or 0.0}
            else:
                bar['high'], bar['low'], bar['close'] = max(bar['high'], price), min(bar['low'], price), price
                bar['ticks'] += 1
                bar['volume'] += volume or 0.0
                if spread is not None:
                    bar['spread_sum'] += spread
                    bar['spread_n'] += 1
        return closed

    def due(self, now: datetime) -> list[dict]:
        """Close bars whose period has ended."""
        return [self._close(f) for f, bar in list(self.open.items())
                if now >= bar['bar_time'] + timedelta(seconds=FRAMES[f])]

    def partial(self) -> list[dict]:
        return [self._row(bar, complete=False) for bar in self.open.values()]

    def _close(self, frame: str) -> dict:
        return self._row(self.open.pop(frame), complete=True)

    @staticmethod
    def _row(bar: dict, complete: bool) -> dict:
        return {'timeframe': bar['timeframe'], 'bar_time': bar['bar_time'], 'open': bar['open'], 'high': bar['high'],
                'low': bar['low'], 'close': bar['close'], 'ticks': bar['ticks'],
                'spread_avg': bar['spread_sum'] / bar['spread_n'] if bar['spread_n'] else None,
                'volume': bar['volume'], 'complete': complete}
