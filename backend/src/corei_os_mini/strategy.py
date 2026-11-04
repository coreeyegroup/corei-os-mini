from __future__ import annotations

from dataclasses import dataclass

from .contracts import StrategyInput


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    action: str
    reason: str
    decision_price_units: int


def evaluate_demo_strategy(prices_units: list[int], config: StrategyInput) -> StrategyDecision:
    required = config.slow_window
    if len(prices_units) < required:
        raise ValueError(f"at least {required} prices are required for this strategy")
    if any(price <= 0 for price in prices_units):
        raise ValueError("synthetic prices must be positive")

    latest = prices_units[-required:]
    fast = latest[-config.fast_window :]
    fast_sum = sum(fast)
    slow_sum = sum(latest)
    left = fast_sum * config.slow_window
    right = slow_sum * config.fast_window

    if left > right:
        return StrategyDecision("BUY", "fast average is above slow average", prices_units[-1])
    if left < right:
        return StrategyDecision("SELL", "fast average is below slow average", prices_units[-1])
    return StrategyDecision("HOLD", "fast and slow averages are equal", prices_units[-1])
