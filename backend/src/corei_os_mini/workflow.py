from __future__ import annotations

from uuid import uuid4

from .allocation import allocate_children
from .contracts import ChildResult, PaperRunRequest, PaperRunResult
from .risk import check_child
from .strategy import evaluate_demo_strategy


def run_paper_workflow(request: PaperRunRequest) -> PaperRunResult:
    decision = evaluate_demo_strategy(request.synthetic_prices_units, request.strategy)
    allocation = allocate_children(request.dispatch_mode, request.accounts, request.strategy)
    child_results: list[ChildResult] = []

    for child in allocation.children:
        risk = check_child(child, decision.action, decision.decision_price_units)
        if not risk.approved:
            child_results.append(
                ChildResult(
                    account_id=child.account.account_id,
                    quantity_units=child.quantity_units,
                    risk_status="BLOCKED",
                    risk_reasons=list(risk.reasons),
                    order_status="NOT_SUBMITTED",
                    simulated_fill_price_units=None,
                )
            )
            continue

        if decision.action == "HOLD":
            child_results.append(
                ChildResult(
                    account_id=child.account.account_id,
                    quantity_units=child.quantity_units,
                    risk_status="BLOCKED",
                    risk_reasons=["NO_TRADE_SIGNAL"],
                    order_status="NOT_SUBMITTED",
                    simulated_fill_price_units=None,
                )
            )
            continue

        child_results.append(
            ChildResult(
                account_id=child.account.account_id,
                quantity_units=child.quantity_units,
                risk_status="APPROVED_PAPER_ONLY",
                risk_reasons=[],
                order_status="SIMULATED_FILLED",
                simulated_fill_price_units=decision.decision_price_units,
            )
        )

    return PaperRunResult(
        run_id=str(uuid4()),
        strategy_id=request.strategy.strategy_id,
        instrument_id=request.instrument_id,
        price_scale=request.price_scale,
        quantity_scale=request.quantity_scale,
        decision=decision.action,
        decision_reason=decision.reason,
        decision_price_units=decision.decision_price_units,
        allocation_mode=request.dispatch_mode,
        route_policy=allocation.route_policy,
        children=child_results,
    )
