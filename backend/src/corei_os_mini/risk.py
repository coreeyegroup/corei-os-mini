git status --shortfrom __future__ import annotations git diff -- 
backend/src/corei_os_mini/risk.py from dataclasses import dataclass git add 
backend/src/corei_os_mini/risk.py from .allocation import ProposedChild from .contracts 
import AccountCandidate
git commit -m "Update paper risk checks"
git push origin main

@dataclass(frozen=True, slots=True)
class RiskResult:
    approved: bool
    reasons: tuple[str, ...]


def check_child(
    child: ProposedChild,
    action: str,
    price_units: int,
) -> RiskResult:
    account: AccountCandidate = child.account
    reasons: list[str] = []

    if action not in {"BUY", "SELL"}:
        reasons.append("NO_TRADE_SIGNAL")
    if not account.risk_state_fresh:
        reasons.append("ACCOUNT_RISK_STATE_STALE")
    if child.quantity_units <= 0:
        reasons.append("ZERO_ALLOCATED_QUANTITY")
    if child.quantity_units > account.max_order_quantity_units:
        reasons.append("MAX_ORDER_QUANTITY_EXCEEDED")

    proposed_position = account.current_position_units
    if action == "BUY":
        proposed_position += child.quantity_units
    elif action == "SELL":
        proposed_position -= child.quantity_units
    if proposed_position < 0 and not account.allow_short:
        reasons.append("SHORT_POSITION_NOT_ALLOWED")
    if abs(proposed_position) > account.max_position_units:
        reasons.append("MAX_POSITION_EXCEEDED")

    notional_units = child.quantity_units * price_units
    if notional_units > account.max_order_notional_units:
        reasons.append("MAX_ORDER_NOTIONAL_EXCEEDED")

    return RiskResult(approved=not reasons, reasons=tuple(reasons))
