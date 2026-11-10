"""Candidate destination resolution: turn one parent intent into proposed account children.

Owns: dispatch-mode semantics (INDIVIDUAL, SELECT_ONE, ALLOCATE, REPLICATE) and
deterministic child sizing. Does not own: risk approval, reservation or submission.
Every child produced here still has to pass final child-level risk (risk.py).
"""
from __future__ import annotations

from dataclasses import dataclass

from .contracts import AccountCandidate, DispatchMode, StrategyInput

# Versioned so each run records exactly which selection rule chose its destination.
SELECT_ONE_POLICY = "SELECT_ONE_PRIORITY_V1: fresh risk state first, highest route_priority, then account_id"


@dataclass(frozen=True, slots=True)
class ProposedChild:
    account: AccountCandidate
    quantity_units: int


@dataclass(frozen=True, slots=True)
class Allocation:
    children: list[ProposedChild]
    route_policy: str | None = None


def allocate_children(
    mode: DispatchMode,
    accounts: list[AccountCandidate],
    strategy: StrategyInput,
) -> Allocation:
    """Create proposed account children; this function never authorizes an order."""
    target = strategy.target_quantity_units

    if mode == DispatchMode.INDIVIDUAL:
        account = next(account for account in accounts if account.selected)
        return Allocation([ProposedChild(account, target)])

    if mode == DispatchMode.SELECT_ONE:
        # An account with stale risk state is chosen only if no fresh one exists;
        # risk then blocks it with a visible reason instead of silently skipping.
        chosen = min(
            accounts,
            key=lambda a: (not a.risk_state_fresh, -a.route_priority, a.account_id),
        )
        return Allocation([ProposedChild(chosen, target)], SELECT_ONE_POLICY)

    if mode == DispatchMode.REPLICATE:
        # Each account declares its own scale. Floor rounding never exceeds the
        # declared share; a zero result is blocked by risk, not rounded up.
        return Allocation([
            ProposedChild(account, target * account.replicate_scale_bps // 10_000)
            for account in accounts
        ])

    base = [(account, target * account.weight_bps // 10_000) for account in accounts]
    remainder = target - sum(quantity for _, quantity in base)

    # Remainder assignment is deterministic: descending fractional remainder,
    # then account_id. No rejected child's quantity is redistributed later.
    ranked = sorted(
        accounts,
        key=lambda account: (-(target * account.weight_bps % 10_000), account.account_id),
    )
    increments = {account.account_id: 0 for account in accounts}
    for account in ranked[:remainder]:
        increments[account.account_id] += 1

    return Allocation([
        ProposedChild(account, quantity + increments[account.account_id])
        for account, quantity in base
    ])
