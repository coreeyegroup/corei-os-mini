from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]
Identifier = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")]


class DispatchMode(StrEnum):
    INDIVIDUAL = "INDIVIDUAL"
    SELECT_ONE = "SELECT_ONE"
    ALLOCATE = "ALLOCATE"
    REPLICATE = "REPLICATE"


class StrategyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: Identifier
    fast_window: Annotated[int, Field(ge=1, le=1000)]
    slow_window: Annotated[int, Field(ge=2, le=5000)]
    target_quantity_units: PositiveInt

    @model_validator(mode="after")
    def windows_are_ordered(self) -> "StrategyInput":
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window must be smaller than slow_window")
        return self


class AccountCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: Identifier
    selected: bool = False
    weight_bps: Annotated[int, Field(ge=0, le=10_000)] = 0
    current_position_units: int
    allow_short: bool = False
    max_order_quantity_units: PositiveInt
    max_order_notional_units: PositiveInt
    max_position_units: PositiveInt
    risk_state_fresh: bool


class PaperRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: Identifier
    price_scale: Annotated[int, Field(ge=0, le=12)]
    quantity_scale: Annotated[int, Field(ge=0, le=12)]
    synthetic_prices_units: Annotated[list[NonNegativeInt], Field(min_length=2, max_length=10_000)]
    dispatch_mode: DispatchMode
    strategy: StrategyInput
    accounts: Annotated[list[AccountCandidate], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def validate_dispatch(self) -> "PaperRunRequest":
        ids = [account.account_id for account in self.accounts]
        if len(set(ids)) != len(ids):
            raise ValueError("account_id values must be unique")

        if self.dispatch_mode in {DispatchMode.INDIVIDUAL, DispatchMode.SELECT_ONE}:
            selected = [account for account in self.accounts if account.selected]
            if len(selected) != 1:
                raise ValueError("INDIVIDUAL and SELECT_ONE require exactly one selected account")
        else:
            if any(account.selected for account in self.accounts):
                raise ValueError("Group dispatch modes do not use the selected-account flag")
            if self.dispatch_mode == DispatchMode.ALLOCATE:
                if any(account.weight_bps == 0 for account in self.accounts):
                    raise ValueError("ALLOCATE requires a positive weight for every account")
                if sum(account.weight_bps for account in self.accounts) != 10_000:
                    raise ValueError("ALLOCATE account weights must sum to 10000 basis points")
            elif any(account.weight_bps != 0 for account in self.accounts):
                raise ValueError("Weights are only valid for ALLOCATE mode")
        return self


class ChildResult(BaseModel):
    account_id: str
    quantity_units: int
    risk_status: str
    risk_reasons: list[str]
    order_status: str
    simulated_fill_price_units: int | None
    simulated: bool = True


class PaperRunResult(BaseModel):
    run_id: str
    execution_mode: str = "PAPER"
    data_mode: str = "SYNTHETIC"
    strategy_id: str
    instrument_id: str
    price_scale: int
    quantity_scale: int
    decision: str
    decision_reason: str
    decision_price_units: int
    allocation_mode: DispatchMode
    children: list[ChildResult]
    note: str = "Synthetic input and paper fill; not a broker execution or approved trading signal."
