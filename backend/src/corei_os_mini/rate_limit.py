"""Order rate limiting per connection (audit blocker 8; SEBI 10 orders/second).

Owns: a token bucket per connection that adapters must pass before sending any order
or modification. The rate and burst come from the connection (max_orders_per_second,
max_order_burst); with no rate set, a conservative default applies so an unconfigured
connection is never unlimited.
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
