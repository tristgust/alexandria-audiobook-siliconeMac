from __future__ import annotations

import statistics
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from stage_metric_contract import (
    StageMetricsError,
    parse_utc_timestamp,
    safe_stage,
)
from stage_metric_schema import validate_stage_metrics
from stage_metric_store import read_stage_metric_document
from stage_metric_types import (
    JsonValue,
    StageMetricReadResult,
    StageMetricSummary,
    StageMetricUnit,
)


DEFAULT_STATUS_LIMIT = 200
MIN_ETA_UNITS = 3
ETA_FRESHNESS_SECONDS = 15 * 60


def _rounded(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def _document_stage(value: JsonValue) -> str:
    if not isinstance(value, Mapping):
        return ""
    stage = cast(Mapping[str, JsonValue], value).get("stage")
    return stage if isinstance(stage, str) else ""


def _fresh_running_update(updated_at: str, *, now: datetime | None) -> bool:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise StageMetricsError("Stage metrics ETA clock must include a timezone.")
    updated = parse_utc_timestamp(updated_at)
    if updated is None:
        return False
    age_seconds = (current.astimezone(timezone.utc) - updated).total_seconds()
    return 0.0 <= age_seconds <= ETA_FRESHNESS_SECONDS


def _timed_units(units: list[StageMetricUnit]) -> list[StageMetricUnit]:
    return [
        unit
        for unit in units
        if unit["phases_seconds"].get("unit_wall", 0.0) > 0
    ]


def summarize_stage_metrics(
    document: JsonValue,
    *,
    now: datetime | None = None,
) -> StageMetricSummary:
    validated = validate_stage_metrics(document, stage=_document_stage(document))
    units = validated["units"]
    completed_units = max(
        validated["baseline_completed_units"],
        max((unit["index"] for unit in units), default=0),
    )
    remaining_units = max(validated["total_units"] - completed_units, 0)
    timed_units = _timed_units(units)
    measured_wall = sum(
        unit["phases_seconds"]["unit_wall"]
        for unit in timed_units
    )
    measured_characters = sum(unit["input_characters"] for unit in units)
    timed_characters = sum(unit["input_characters"] for unit in timed_units)
    measured_output_items = sum(unit["output_items"] for unit in units)
    prompt_tokens = sum(unit["prompt_tokens"] or 0 for unit in units)
    output_tokens = sum(unit["output_tokens"] or 0 for unit in units)
    generation_seconds = sum(
        unit["phases_seconds"].get("model_generation", 0.0)
        for unit in units
    )
    recent = units[-5:]
    recent_durations = [
        unit["phases_seconds"]["unit_wall"]
        for unit in _timed_units(recent)
    ]

    eta_seconds: float | None = None
    eta_reliable = False
    eta_reason = "complete" if remaining_units == 0 else "insufficient_completed_units"
    if remaining_units > 0 and len(recent_durations) >= MIN_ETA_UNITS:
        if len(recent_durations) != len(recent):
            eta_reason = "incomplete_timing_samples"
        elif any(unit["corrective_retries"] > 0 for unit in recent):
            eta_reason = "recent_retries"
        elif validated["status"] != "running":
            eta_reason = "stage_not_running"
        elif not _fresh_running_update(validated["updated_at"], now=now):
            eta_reason = "stale_running_state"
        else:
            seconds_per_unit = max(
                statistics.mean(recent_durations),
                statistics.median(recent_durations),
            )
            eta_seconds = seconds_per_unit * remaining_units * 1.15
            eta_reliable = True
            eta_reason = "rolling_conservative"

    rolling_wall = sum(recent_durations)
    units_per_minute = (
        len(timed_units) / measured_wall * 60.0
        if measured_wall > 0
        else None
    )
    return StageMetricSummary(
        stage=validated["stage"],
        run_id=validated["run_id"],
        status=validated["status"],
        total_units=validated["total_units"],
        completed_units=completed_units,
        measured_units=len(units),
        unmeasured_completed_units=max(completed_units - len(units), 0),
        remaining_units=remaining_units,
        measured_wall_seconds=_rounded(measured_wall),
        measured_characters=measured_characters,
        measured_output_items=measured_output_items,
        prompt_tokens=prompt_tokens or None,
        output_tokens=output_tokens or None,
        units_per_minute=_rounded(units_per_minute),
        characters_per_second=_rounded(
            timed_characters / measured_wall if measured_wall > 0 else None
        ),
        model_output_tokens_per_second=_rounded(
            output_tokens / generation_seconds
            if output_tokens > 0 and generation_seconds > 0
            else None
        ),
        rolling_units_per_minute=_rounded(
            len(recent_durations) / rolling_wall * 60.0
            if rolling_wall > 0
            else None
        ),
        eta_seconds=_rounded(eta_seconds),
        eta_reliable=eta_reliable,
        eta_reason=eta_reason,
        reconciliation=validated["reconciliation"],
        finalization=validated["finalization"],
        started_at=validated["started_at"],
        updated_at=validated["updated_at"],
        error=validated["error"],
    )


def read_stage_metrics(
    path: str | Path,
    *,
    stage: str,
    limit: int = DEFAULT_STATUS_LIMIT,
) -> StageMetricReadResult:
    stage_name = safe_stage(stage)
    if limit < 1:
        raise StageMetricsError("Stage metrics status limit must be positive.")
    target = Path(path)
    exists = target.exists()
    try:
        document = read_stage_metric_document(target, stage=stage_name)
    except StageMetricsError as exc:
        return StageMetricReadResult(
            exists=exists,
            stage=stage_name,
            document=None,
            units=[],
            summary=None,
            truncated=False,
            error=str(exc),
        )
    if document is None:
        return StageMetricReadResult(
            exists=False,
            stage=stage_name,
            document=None,
            units=[],
            summary=None,
            truncated=False,
            error=None,
        )
    units = document["units"][-limit:]
    return StageMetricReadResult(
        exists=True,
        stage=stage_name,
        document=document,
        units=units,
        summary=summarize_stage_metrics(document),
        truncated=len(document["units"]) > limit,
        error=None,
    )
