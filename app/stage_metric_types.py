from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, TypeAlias, TypedDict


StageMetricStatus = Literal["running", "complete", "failed"]
JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | Mapping[str, "JsonValue"] | list["JsonValue"]


class StageMetricUnit(TypedDict):
    index: int
    input_characters: int
    output_items: int
    attempts: int
    corrective_retries: int
    prompt_tokens: int | None
    output_tokens: int | None
    validation_mode: str | None
    phases_seconds: dict[str, float]
    completed_at: str


class StageMetricEvent(TypedDict):
    attempts: int
    corrective_retries: int
    prompt_tokens: int | None
    output_tokens: int | None
    validation_mode: str | None
    phases_seconds: dict[str, float]
    completed_at: str


class StageMetricDocument(TypedDict):
    schema_version: int
    stage: str
    run_id: str
    status: StageMetricStatus
    total_units: int
    baseline_completed_units: int
    units: list[StageMetricUnit]
    reconciliation: StageMetricEvent | None
    finalization: StageMetricEvent | None
    started_at: str
    updated_at: str
    error: str | None


class StageMetricSummary(TypedDict):
    stage: str
    run_id: str
    status: StageMetricStatus
    total_units: int
    completed_units: int
    measured_units: int
    unmeasured_completed_units: int
    remaining_units: int
    measured_wall_seconds: float | None
    measured_characters: int
    measured_output_items: int
    prompt_tokens: int | None
    output_tokens: int | None
    units_per_minute: float | None
    characters_per_second: float | None
    model_output_tokens_per_second: float | None
    rolling_units_per_minute: float | None
    eta_seconds: float | None
    eta_reliable: bool
    eta_reason: str
    reconciliation: StageMetricEvent | None
    finalization: StageMetricEvent | None
    started_at: str
    updated_at: str
    error: str | None


class StageMetricReadResult(TypedDict):
    exists: bool
    stage: str
    document: StageMetricDocument | None
    units: list[StageMetricUnit]
    summary: StageMetricSummary | None
    truncated: bool
    error: str | None
