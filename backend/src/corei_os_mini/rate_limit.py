"""Order rate limiting per connection (audit blocker 8; SEBI / exchange order-rate limits).

Owns: a strict rolling-window limiter per connection that every outbound order mutation (send, modify, cancel) must
pass: at most N in any 1-second window, N = the connection's max_orders_per_second (capped by max_order_burst when
that is lower). A token bucket would let a full burst plus its refill through in the first second, so it is not used
for orders. With no rate set a conservative default applies, so an unconfigured connection is never unlimited.
Scope and limits: one limiter per connection inside the workstation process. The workstation runs one API process
(uvicorn, one worker) that owns dispatch, amend and cancel, so this covers every COREI order path; a second process
sending orders would need a shared (database) limiter first. Orders sent outside COREI are not counted.
Consumers: trading.brokers.throttle (the dispatcher, amend and cancel paths).
"""
from __future__ import annotations

import threading
import time

DEFAULT_RATE = 5.0
DEFAULT_BURST = 5


class TokenBucket:
    def __init__(self, rate: float, burst: int, clock=time.monotonic):
        if rate <= 0 or burst < 1:
            raise ValueError('rate and burst must be positive')
        self.rate, self.burst, self.clock = float(rate), int(burst), clock
        self.tokens = float(burst)
        self.updated = clock()
        self.lock = threading.Lock()

    def _refill(self) -> None:
        now = self.clock()
        self.tokens = min(self.burst, self.tokens + (now - self.updated) * self.rate)
        self.updated = now

    def try_acquire(self) -> float:
        """Take one token. Returns 0 when allowed, else the seconds to wait."""
        with self.lock:
            self._refill()
            if self.tokens >= 1:
                self.tokens -= 1
                return 0.0
            return (1 - self.tokens) / self.rate


_buckets: dict[str, TokenBucket] = {}
_lock = threading.Lock()


def bucket_for(connection_id: str, rate: float | None, burst: int | None) -> TokenBucket:
    rate = float(rate) if rate else DEFAULT_RATE
    burst = int(burst) if burst else max(1, min(DEFAULT_BURST, int(rate)))
    with _lock:
        current = _buckets.get(connection_id)
        if current is None or current.rate != rate or current.burst != burst:
            current = _buckets[connection_id] = TokenBucket(rate, burst)
        return current


class SlidingWindow:
    """At most `limit` acquisitions in any rolling `window` seconds."""

    def __init__(self, limit: int, window: float = 1.0, clock=time.monotonic):
        if limit < 1 or window <= 0:
            raise ValueError('limit and window must be positive')
        self.limit, self.window, self.clock = int(limit), float(window), clock
        self.times: list[float] = []
        self.lock = threading.Lock()

    def try_acquire(self) -> float:
        """Take one slot. Returns 0 when allowed, else the seconds until a slot frees."""
        with self.lock:
            now = self.clock()
            self.times = [t for t in self.times if now - t < self.window]
            if len(self.times) < self.limit:
                self.times.append(now)
                return 0.0
            return self.window - (now - self.times[0])


_windows: dict[str, SlidingWindow] = {}


def order_limiter(connection_id: str, per_second, burst) -> SlidingWindow:
    limit = int(float(per_second)) if per_second else int(DEFAULT_RATE)
    if burst:
        limit = min(limit, int(burst))
    limit = max(1, limit)
    with _lock:
        current = _windows.get(connection_id)
        if current is None or current.limit != limit:
            current = _windows[connection_id] = SlidingWindow(limit)
        return current
