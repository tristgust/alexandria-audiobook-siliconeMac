from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from stage_metric_contract import (
    STAGE_METRICS_SCHEMA_VERSION,
    StageMetricsError,
    non_negative_int,
    nonempty_text,
    normalize_status,
    safe_stage,
    validate_event,
    validate_unit,
)
from stage_metric_types import JsonValue, StageMetricDocument


DOCUMENT_FIELDS = {
    "schema_version",
    "stage",
    "run_id",
    "status",
    "total_units",
    "baseline_completed_units",
    "units",
    "reconciliation",
    "finalization",
    "started_at",
    "updated_at",
    "error",
}


def new_stage_metric_document(
    *,
    stage: str,
    run_id: str,
    total_units: int,
    baseline_completed_units: int,
    timestamp: str,
) -> StageMetricDocument:
    return StageMetricDocument(
        schema_version=STAGE_METRICS_SCHEMA_VERSION,
        stage=stage,
        run_id=run_id,
        status="running",
        total_units=total_units,
        baseline_completed_units=baseline_completed_units,
        units=[],
        reconciliation=None,
        finalization=None,
        started_at=timestamp,
        updated_at=timestamp,
        error=None,
    )


def validate_stage_metrics(value: JsonValue, *, stage: str) -> StageMetricDocument:
    stage_name = safe_stage(stage)
    if not isinstance(value, Mapping):
        raise StageMetricsError("Stage metrics must contain a JSON object.")
    raw = cast(Mapping[str, JsonValue], value)
    if set(raw) != DOCUMENT_FIELDS:
        raise StageMetricsError("Stage metrics have unsupported or missing fields.")
    if raw["schema_version"] != STAGE_METRICS_SCHEMA_VERSION:
        raise StageMetricsError("Stage metrics schema version is unsupported.")
    if raw["stage"] != stage_name:
        raise StageMetricsError("Stage metrics belong to another stage.")

    status = normalize_status(raw["status"])
    total_units = non_negative_int(raw["total_units"], "Stage metrics total_units")
    baseline = non_negative_int(
        raw["baseline_completed_units"],
        "Stage metrics baseline_completed_units",
    )
    if baseline > total_units:
        raise StageMetricsError(
            "Stage metrics baseline_completed_units cannot exceed total_units."
        )

    raw_units = raw["units"]
    if not isinstance(raw_units, list):
        raise StageMetricsError("Stage metrics units must be an array.")
    units = [
        validate_unit(item, f"Stage metrics unit {index + 1}")
        for index, item in enumerate(raw_units)
    ]
    indices = [unit["index"] for unit in units]
    expected_indices = list(range(baseline + 1, baseline + len(indices) + 1))
    if indices != expected_indices:
        raise StageMetricsError(
            "Stage metrics unit indices must be contiguous after the baseline."
        )
    if indices and indices[-1] > total_units:
        raise StageMetricsError("Stage metrics unit index exceeds total_units.")
    completed_units = indices[-1] if indices else baseline
    if status == "complete" and completed_units != total_units:
        raise StageMetricsError("Complete stage metrics must account for every unit.")

    error = raw["error"]
    if error is not None:
        if not isinstance(error, str) or not error.strip():
            raise StageMetricsError("Stage metrics error must be text or null.")
        error = error.strip()
    return StageMetricDocument(
        schema_version=STAGE_METRICS_SCHEMA_VERSION,
        stage=stage_name,
        run_id=nonempty_text(raw["run_id"], "Stage metrics run_id"),
        status=status,
        total_units=total_units,
        baseline_completed_units=baseline,
        units=units,
        reconciliation=validate_event(
            raw["reconciliation"],
            "Stage metrics reconciliation",
        ),
        finalization=validate_event(
            raw["finalization"],
            "Stage metrics finalization",
        ),
        started_at=nonempty_text(raw["started_at"], "Stage metrics started_at"),
        updated_at=nonempty_text(raw["updated_at"], "Stage metrics updated_at"),
        error=error,
    )
