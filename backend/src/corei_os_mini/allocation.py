from __future__ import annotations

from dataclasses import dataclass

from .contracts import AccountCandidate, DispatchMode, StrategyInput


@dataclass(frozen=True, slots=True)
class ProposedChild:
    account: AccountCandidate
    quantity_units: int


def allocate_children(
    mode: DispatchMode,
    accounts: list[AccountCandidate],
    strategy: StrategyInput,
) -> list[ProposedChild]:
    """Create proposed account children; this function never authorizes an order."""
    if mode in {DispatchMode.INDIVIDUAL, DispatchMode.SELECT_ONE}:
        account = next(account for account in accounts if account.selected)
        return [ProposedChild(account, strategy.target_quantity_units)]

    if mode == DispatchMode.REPLICATE:
        return [ProposedChild(account, strategy.target_quantity_units) for account in accounts]

    base = [
        (account, strategy.target_quantity_units * account.weight_bps // 10_000)
        for account in accounts
    ]
    remainder = strategy.target_quantity_units - sum(quantity for _, quantity in base)

    # Remainder assignment is deterministic: descending fractional remainder,
    # then account_id. No rejected child's quantity is redistributed later.
    ranked = sorted(
        accounts,
        key=lambda account: (
            -(strategy.target_quantity_units * account.weight_bps % 10_000),
            account.account_id,
        ),
    )
    increments = {account.account_id: 0 for account in accounts}
    for account in ranked[:remainder]:
        increments[account.account_id] += 1

    return [
        ProposedChild(account, quantity + increments[account.account_id])
        for account, quantity in base
    ]
