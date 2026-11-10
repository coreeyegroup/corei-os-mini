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
    # REPLICATE only: this account's child is target * scale / 10000 (floor).
    # Required per account so the full parent size is never copied by default.
    replicate_scale_bps: Annotated[int, Field(ge=0, le=10_000)] = 0
    # SELECT_ONE only: higher priority is preferred; ties break on account_id.
    route_priority: Annotated[int, Field(ge=0, le=10_000)] = 0
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

        mode = self.dispatch_mode
        if mode != DispatchMode.INDIVIDUAL and any(a.selected for a in self.accounts):
            raise ValueError("Only INDIVIDUAL uses the selected-account flag")
        if mode != DispatchMode.ALLOCATE and any(a.weight_bps != 0 for a in self.accounts):
            raise ValueError("weight_bps is only valid for ALLOCATE mode")
        if mode != DispatchMode.REPLICATE and any(a.replicate_scale_bps != 0 for a in self.accounts):
            raise ValueError("replicate_scale_bps is only valid for REPLICATE mode")
        if mode != DispatchMode.SELECT_ONE and any(a.route_priority != 0 for a in self.accounts):
            raise ValueError("route_priority is only valid for SELECT_ONE mode")

        if mode == DispatchMode.INDIVIDUAL:
            if len(self.accounts) != 1 or not self.accounts[0].selected:
                raise ValueError("INDIVIDUAL requires exactly one account, marked selected")
        elif mode == DispatchMode.ALLOCATE:
            if any(a.weight_bps == 0 for a in self.accounts):
                raise ValueError("ALLOCATE requires a positive weight for every account")
            if sum(a.weight_bps for a in self.accounts) != 10_000:
                raise ValueError("ALLOCATE account weights must sum to 10000 basis points")
        elif mode == DispatchMode.REPLICATE:
            if any(a.replicate_scale_bps == 0 for a in self.accounts):
                raise ValueError("REPLICATE requires an explicit replicate_scale_bps for every account")
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
    route_policy: str | None = None
    children: list[ChildResult]
    note: str = "Synthetic input and paper fill; not a broker execution or approved trading signal."
