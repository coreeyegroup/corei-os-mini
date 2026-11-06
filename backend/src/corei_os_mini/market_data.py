from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import Identifier, PositiveInt


class FeedMapping(BaseModel):
    """Explicit mapping from one provider connection symbol to a COREI instrument."""

    model_config = ConfigDict(extra="forbid")

    provider_id: Identifier
    connection_id: Identifier
    native_symbol: Identifier
    instrument_id: Identifier
    price_scale: Annotated[int, Field(ge=0, le=12)]
    enabled: bool = True
    priority: Annotated[int, Field(ge=0, le=10_000)] = 0
    max_age_ms: Annotated[int, Field(gt=0, le=3_600_000)] = 2_000


class FeedObservation(BaseModel):
    """A captured price observation retaining provider and connection provenance."""

    model_config = ConfigDict(extra="forbid")

    provider_id: Identifier
    connection_id: Identifier
    native_symbol: Identifier
    source_time: datetime
    received_at: datetime
    sequence: Annotated[int, Field(ge=0)] | None = None
    price_units: PositiveInt
    price_scale: Annotated[int, Field(ge=0, le=12)]
    clock_confidence: Literal["HIGH", "LOW", "UNKNOWN"] = "UNKNOWN"

    @model_validator(mode="after")
    def timestamps_are_timezone_aware(self) -> "FeedObservation":
        for name, value in (("source_time", self.source_time), ("received_at", self.received_at)):
            if value.utcoffset() is None:
                raise ValueError(f"{name} must include a timezone")
        return self


class MarketDataAssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: Identifier
    price_scale: Annotated[int, Field(ge=0, le=12)]
    evaluated_at: datetime
    max_future_skew_ms: Annotated[int, Field(ge=0, le=60_000)] = 250
    require_high_clock_confidence: bool = True
    require_contiguous_sequence: bool = True
    mappings: Annotated[list[FeedMapping], Field(min_length=1, max_length=100)]
    observations: Annotated[list[FeedObservation], Field(min_length=1, max_length=1_000)]

    @model_validator(mode="after")
    def mappings_are_unique_and_match_instrument(self) -> "MarketDataAssessmentRequest":
        if self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must include a timezone")
        keys = [
            (item.provider_id, item.connection_id, item.native_symbol)
            for item in self.mappings
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("feed mapping identities must be unique")
        for mapping in self.mappings:
            if mapping.instrument_id != self.instrument_id:
                raise ValueError("every feed mapping must target instrument_id")
            if mapping.price_scale != self.price_scale:
                raise ValueError("every feed mapping must use price_scale")
        return self


class ObservationAssessment(BaseModel):
    observation_id: str
    provider_id: str
    connection_id: str
    native_symbol: str
    instrument_id: str
    source_time: datetime
    received_at: datetime
    sequence: int | None
    price_units: int
    price_scale: int
    clock_confidence: str
    timestamp_delta_ms: int | None
    status: Literal["VALID", "DEGRADED", "INVALID"]
    reasons: list[str]
    mapping_priority: int | None
    selection_eligible: bool


class FeedAssessment(BaseModel):
    provider_id: str
    connection_id: str
    native_symbol: str
    instrument_id: str
    enabled: bool
    priority: int
    status: Literal["DISABLED", "NO_OBSERVATIONS", "READY", "DEGRADED", "BLOCKED"]
    observation_count: int
    eligible_count: int
    degraded_count: int
    invalid_count: int
    latest_source_time: datetime | None
    latest_received_at: datetime | None


class MarketDataAssessmentResult(BaseModel):
    instrument_id: str
    evaluated_at: datetime
    selected_observation_id: str | None
    selection_reason: str
    feeds: list[FeedAssessment]
    observations: list[ObservationAssessment]
    note: str = (
        "Assessment of supplied observations only. This demo endpoint does not connect to providers."
    )


def assess_market_data(request: MarketDataAssessmentRequest) -> MarketDataAssessmentResult:
    """Validate and deterministically select from explicitly mapped observations."""
    mapping_by_key = {
        (item.provider_id, item.connection_id, item.native_symbol): item
        for item in request.mappings
    }
    prior_sequence: dict[tuple[str, str, str], int] = {}
    prior_source_time: dict[tuple[str, str, str], datetime] = {}
    results: list[ObservationAssessment] = []

    for index, observation in enumerate(request.observations, start=1):
        source_key = (
            observation.provider_id,
            observation.connection_id,
            observation.native_symbol,
        )
        mapping = mapping_by_key.get(source_key)
        reasons: list[str] = []
        warnings: list[str] = []
        eligible = mapping is not None and mapping.enabled

        if mapping is None:
            reasons.append("NO_EXPLICIT_INSTRUMENT_MAPPING")
        elif not mapping.enabled:
            reasons.append("FEED_MAPPING_DISABLED")
        elif observation.price_scale != mapping.price_scale:
            reasons.append("PRICE_SCALE_MISMATCH")
        elif observation.clock_confidence != "HIGH" and request.require_high_clock_confidence:
            reasons.append("CLOCK_CONFIDENCE_INSUFFICIENT")

        age_ms = int((request.evaluated_at - observation.source_time).total_seconds() * 1_000)
        if age_ms < -request.max_future_skew_ms:
            reasons.append("SOURCE_TIME_IN_FUTURE")
        elif mapping is not None and age_ms > mapping.max_age_ms:
            reasons.append("STALE_OBSERVATION")
        if observation.received_at > request.evaluated_at:
            reasons.append("RECEIPT_TIME_IN_FUTURE")

        timestamp_delta_ms: int | None = None
        if observation.received_at >= observation.source_time:
            timestamp_delta_ms = int(
                (observation.received_at - observation.source_time).total_seconds() * 1_000
            )
        else:
            warnings.append("SOURCE_CLOCK_AHEAD_OF_RECEIPT_CLOCK")

        previous_time = prior_source_time.get(source_key)
        if previous_time is not None and observation.source_time < previous_time:
            reasons.append("SOURCE_TIME_OUT_OF_ORDER")
        if observation.sequence is not None:
            previous_sequence = prior_sequence.get(source_key)
            if previous_sequence is not None:
                if observation.sequence <= previous_sequence:
                    reasons.append("SEQUENCE_NOT_INCREASING")
                elif observation.sequence > previous_sequence + 1:
                    if request.require_contiguous_sequence:
                        reasons.append("SEQUENCE_GAP")
                    else:
                        warnings.append("SEQUENCE_GAP")
            prior_sequence[source_key] = max(
                prior_sequence.get(source_key, observation.sequence),
                observation.sequence,
            )
        prior_source_time[source_key] = max(
            prior_source_time.get(source_key, observation.source_time),
            observation.source_time,
        )

        if reasons:
            status: Literal["VALID", "DEGRADED", "INVALID"] = "INVALID"
        elif warnings:
            status = "DEGRADED"
        else:
            status = "VALID"

        results.append(
            ObservationAssessment(
                observation_id=f"observation-{index:04d}",
                provider_id=observation.provider_id,
                connection_id=observation.connection_id,
                native_symbol=observation.native_symbol,
                instrument_id=request.instrument_id,
                source_time=observation.source_time,
                received_at=observation.received_at,
                sequence=observation.sequence,
                price_units=observation.price_units,
                price_scale=observation.price_scale,
                clock_confidence=observation.clock_confidence,
                timestamp_delta_ms=timestamp_delta_ms,
                status=status,
                reasons=reasons + warnings,
                mapping_priority=mapping.priority if mapping is not None else None,
                selection_eligible=eligible and not reasons,
            )
        )

    eligible_results = [item for item in results if item.selection_eligible]
    feed_results: list[FeedAssessment] = []
    for mapping in request.mappings:
        source_rows = [
            item
            for item in results
            if item.provider_id == mapping.provider_id
            and item.connection_id == mapping.connection_id
            and item.native_symbol == mapping.native_symbol
        ]
        eligible_count = sum(item.selection_eligible for item in source_rows)
        degraded_count = sum(item.status == "DEGRADED" for item in source_rows)
        invalid_count = sum(item.status == "INVALID" for item in source_rows)
        if not mapping.enabled:
            feed_status = "DISABLED"
        elif not source_rows:
            feed_status = "NO_OBSERVATIONS"
        elif eligible_count == 0:
            feed_status = "BLOCKED"
        elif invalid_count or degraded_count:
            feed_status = "DEGRADED"
        else:
            feed_status = "READY"
        feed_results.append(
            FeedAssessment(
                provider_id=mapping.provider_id,
                connection_id=mapping.connection_id,
                native_symbol=mapping.native_symbol,
                instrument_id=mapping.instrument_id,
                enabled=mapping.enabled,
                priority=mapping.priority,
                status=feed_status,
                observation_count=len(source_rows),
                eligible_count=eligible_count,
                degraded_count=degraded_count,
                invalid_count=invalid_count,
                latest_source_time=max(
                    (item.source_time for item in source_rows), default=None
                ),
                latest_received_at=max(
                    (item.received_at for item in source_rows), default=None
                ),
            )
        )

    selected = None
    if eligible_results:
        selected = sorted(
            eligible_results,
            key=lambda item: (
                -(item.mapping_priority or 0),
                -int(item.source_time.timestamp() * 1_000_000),
                -int(item.received_at.timestamp() * 1_000_000),
                item.provider_id,
                item.connection_id,
                item.native_symbol,
                item.observation_id,
            ),
        )[0]
        selection_reason = "HIGHEST_CONFIGURED_PRIORITY_THEN_NEWEST_VALID_SOURCE_TIME"
    else:
        selection_reason = "NO_ELIGIBLE_OBSERVATION"

    return MarketDataAssessmentResult(
        instrument_id=request.instrument_id,
        evaluated_at=request.evaluated_at,
        selected_observation_id=selected.observation_id if selected else None,
        selection_reason=selection_reason,
        feeds=feed_results,
        observations=results,
    )
