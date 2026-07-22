from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from stage_metric_contract import (
    ALLOWED_EVENTS,
    DEFAULT_MAX_UNITS,
    StageMetricsError,
    non_negative_int,
    nonempty_text,
    normalize_write_timestamp,
    safe_stage,
    validate_event,
    validate_unit,
)
from stage_metric_schema import new_stage_metric_document, validate_stage_metrics
from stage_metric_types import StageMetricDocument
from utils import atomic_json_write


_METRICS_LOCK = threading.RLock()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_timestamp(value: str | None, label: str) -> str:
    return normalize_write_timestamp(value, label) if value is not None else _timestamp()


def _read_document_unlocked(
    path: str | Path,
    *,
    stage: str,
) -> StageMetricDocument | None:
    target = Path(path)
    if not target.exists():
        return None
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise StageMetricsError(f"Could not read stage metrics: {exc}") from exc
    return validate_stage_metrics(value, stage=stage)


def read_stage_metric_document(
    path: str | Path,
    *,
    stage: str,
) -> StageMetricDocument | None:
    stage_name = safe_stage(stage)
    with _METRICS_LOCK:
        return _read_document_unlocked(path, stage=stage_name)


def _write_document(
    document: StageMetricDocument,
    *,
    target: Path,
    stage: str,
) -> StageMetricDocument:
    validated = validate_stage_metrics(document, stage=stage)
    atomic_json_write(validated, str(target))
    return validated


def prepare_stage_metrics(
    path: str | Path,
    *,
    stage: str,
    run_id: str,
    total_units: int,
    baseline_completed_units: int = 0,
    started_at: str | None = None,
) -> StageMetricDocument:
    stage_name = safe_stage(stage)
    normalized_run_id = nonempty_text(run_id, "Stage metrics run_id")
    total = non_negative_int(total_units, "Stage metrics total_units")
    baseline = non_negative_int(
        baseline_completed_units,
        "Stage metrics baseline_completed_units",
    )
    if baseline > total:
        raise StageMetricsError(
            "Stage metrics baseline_completed_units cannot exceed total_units."
        )
    target = Path(path)
    with _METRICS_LOCK:
        existing = _read_document_unlocked(target, stage=stage_name)
        if (
            existing is not None
            and existing["run_id"] == normalized_run_id
            and existing["total_units"] == total
            and not (existing["status"] == "complete" and baseline == 0)
        ):
            if existing["baseline_completed_units"] > baseline:
                raise StageMetricsError(
                    "Stage metrics baseline regressed behind the persisted run."
                )
            measured_completed = max(
                existing["baseline_completed_units"],
                max((unit["index"] for unit in existing["units"]), default=0),
            )
            if measured_completed < baseline:
                existing["baseline_completed_units"] = baseline
                existing["units"] = []
                existing["updated_at"] = _timestamp()
                return _write_document(existing, target=target, stage=stage_name)
            return existing
        document = new_stage_metric_document(
            stage=stage_name,
            run_id=normalized_run_id,
            total_units=total,
            baseline_completed_units=baseline,
            timestamp=_write_timestamp(
                started_at,
                "Stage metrics started_at timestamp",
            ),
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        return _write_document(document, target=target, stage=stage_name)


def record_stage_unit(
    path: str | Path,
    *,
    stage: str,
    index: int,
    input_characters: int,
    output_items: int,
    attempts: int,
    corrective_retries: int,
    prompt_tokens: int | None,
    output_tokens: int | None,
    validation_mode: str | None,
    phases_seconds: Mapping[str, float],
    completed_at: str | None = None,
    max_units: int = DEFAULT_MAX_UNITS,
) -> StageMetricDocument:
    stage_name = safe_stage(stage)
    if max_units < 1:
        raise StageMetricsError("Stage metrics unit limit must be positive.")
    target = Path(path)
    with _METRICS_LOCK:
        document = _read_document_unlocked(target, stage=stage_name)
        if document is None:
            raise StageMetricsError("Stage metrics must be prepared before recording units.")
        unit = validate_unit(
            {
                "index": index,
                "input_characters": input_characters,
                "output_items": output_items,
                "attempts": attempts,
                "corrective_retries": corrective_retries,
                "prompt_tokens": prompt_tokens,
                "output_tokens": output_tokens,
                "validation_mode": validation_mode,
                "phases_seconds": dict(phases_seconds),
                "completed_at": _write_timestamp(
                    completed_at,
                    "Stage metrics unit completed_at timestamp",
                ),
            },
            f"Stage metrics unit {index}",
        )
        if "unit_wall" not in unit["phases_seconds"]:
            raise StageMetricsError("Stage metrics unit phases_seconds.unit_wall is required.")
        if index <= document["baseline_completed_units"]:
            raise StageMetricsError(
                "Stage metrics cannot overwrite an unmeasured baseline unit."
            )
        existing_indices = {item["index"] for item in document["units"]}
        if index in existing_indices:
            raise StageMetricsError("Stage metrics unit was already recorded.")
        completed_index = (
            document["units"][-1]["index"]
            if document["units"]
            else document["baseline_completed_units"]
        )
        if index != completed_index + 1:
            raise StageMetricsError(
                "Stage metrics unit indices must be contiguous after the baseline."
            )
        units = [*document["units"], unit]
        if len(units) > max_units:
            units = units[-max_units:]
            document["baseline_completed_units"] = units[0]["index"] - 1
        document["units"] = units
        document["status"] = "running"
        document["error"] = None
        document["updated_at"] = unit["completed_at"]
        return _write_document(document, target=target, stage=stage_name)


def record_stage_event(
    path: str | Path,
    *,
    stage: str,
    event: str,
    phases_seconds: Mapping[str, float],
    attempts: int = 0,
    corrective_retries: int = 0,
    prompt_tokens: int | None = None,
    output_tokens: int | None = None,
    validation_mode: str | None = None,
    completed_at: str | None = None,
    mark_complete: bool = False,
) -> StageMetricDocument:
    stage_name = safe_stage(stage)
    if event not in ALLOWED_EVENTS:
        raise StageMetricsError("Stage metrics event is unsupported.")
    target = Path(path)
    with _METRICS_LOCK:
        document = _read_document_unlocked(target, stage=stage_name)
        if document is None:
            raise StageMetricsError("Stage metrics must be prepared before recording events.")
        timestamp = _write_timestamp(
            completed_at,
            f"Stage metrics {event} completed_at timestamp",
        )
        metric_event = validate_event(
            {
                "attempts": attempts,
                "corrective_retries": corrective_retries,
                "prompt_tokens": prompt_tokens,
                "output_tokens": output_tokens,
                "validation_mode": validation_mode,
                "phases_seconds": dict(phases_seconds),
                "completed_at": timestamp,
            },
            f"Stage metrics {event}",
        )
        if event == "reconciliation":
            document["reconciliation"] = metric_event
        else:
            document["finalization"] = metric_event
        if mark_complete:
            document["status"] = "complete"
        document["error"] = None
        document["updated_at"] = timestamp
        return _write_document(document, target=target, stage=stage_name)


def mark_stage_metrics_failed(
    path: str | Path,
    *,
    stage: str,
    error: str,
) -> StageMetricDocument:
    stage_name = safe_stage(stage)
    error_message = nonempty_text(error, "Stage metrics failure")
    target = Path(path)
    with _METRICS_LOCK:
        document = _read_document_unlocked(target, stage=stage_name)
        if document is None:
            raise StageMetricsError("Stage metrics must be prepared before marking failure.")
        document["status"] = "failed"
        document["error"] = error_message
        document["updated_at"] = _timestamp()
        return _write_document(document, target=target, stage=stage_name)
