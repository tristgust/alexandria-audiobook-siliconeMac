from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import cast

from stage_metric_types import (
    JsonValue,
    StageMetricEvent,
    StageMetricStatus,
    StageMetricUnit,
)


STAGE_METRICS_SCHEMA_VERSION = 1
DEFAULT_MAX_UNITS = 1000
ALLOWED_STATUSES = frozenset({"running", "complete", "failed"})
ALLOWED_EVENTS = frozenset({"reconciliation", "finalization"})
ALLOWED_PHASES = frozenset(
    {
        "prompt_assembly",
        "request_wall",
        "model_total",
        "model_load",
        "model_prompt",
        "model_generation",
        "schema_validation",
        "response_parse_repair",
        "fidelity_audit",
        "evidence_validation",
        "checkpoint_write",
        "reconciliation_validation",
        "draft_build",
        "artifact_write",
        "finalization",
        "unit_wall",
    }
)


class StageMetricsError(ValueError):
    """Persisted stage metrics did not satisfy the metrics contract."""


def safe_stage(stage: str) -> str:
    if not isinstance(stage, str) or not stage.strip():
        raise StageMetricsError("Stage metrics name is required.")
    value = stage.strip()
    if not all(character.isalnum() or character in {"_", "-"} for character in value):
        raise StageMetricsError("Stage metrics name contains unsupported characters.")
    return value


def non_negative_int(value: JsonValue, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise StageMetricsError(f"{label} must be a non-negative integer.")
    return value


def optional_non_negative_int(value: JsonValue, label: str) -> int | None:
    if value is None:
        return None
    return non_negative_int(value, label)


def finite_seconds(value: JsonValue, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StageMetricsError(f"{label} must be a non-negative number.")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise StageMetricsError(f"{label} must be a finite non-negative number.")
    return result


def nonempty_text(value: JsonValue, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StageMetricsError(f"{label} must be non-empty text.")
    return value.strip()


def parse_utc_timestamp(value: str) -> datetime | None:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def normalize_write_timestamp(value: JsonValue, label: str) -> str:
    text = nonempty_text(value, label)
    parsed = parse_utc_timestamp(text)
    if parsed is None:
        raise StageMetricsError(f"{label} must be a timezone-aware timestamp.")
    return parsed.isoformat().replace("+00:00", "Z")


def validate_phases(value: JsonValue, label: str) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise StageMetricsError(f"{label} must be an object.")
    raw_phases = cast(Mapping[str, JsonValue], value)
    result: dict[str, float] = {}
    for raw_name, raw_seconds in raw_phases.items():
        if raw_name not in ALLOWED_PHASES:
            raise StageMetricsError(f"{label} contains unsupported phase {raw_name!r}.")
        name = str(raw_name)
        result[name] = finite_seconds(raw_seconds, f"{label}.{name}")
    if "unit_wall" in result and result["unit_wall"] <= 0:
        raise StageMetricsError(f"{label}.unit_wall must be positive when present.")
    return result


def _mapping_with_fields(
    value: JsonValue,
    *,
    label: str,
    required: set[str],
) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise StageMetricsError(f"{label} must be an object.")
    raw = cast(Mapping[str, JsonValue], value)
    if set(raw) != required:
        raise StageMetricsError(f"{label} has unsupported or missing fields.")
    return raw


def validate_unit(value: JsonValue, label: str) -> StageMetricUnit:
    raw = _mapping_with_fields(
        value,
        label=label,
        required={
            "index",
            "input_characters",
            "output_items",
            "attempts",
            "corrective_retries",
            "prompt_tokens",
            "output_tokens",
            "validation_mode",
            "phases_seconds",
            "completed_at",
        },
    )
    index = non_negative_int(raw["index"], f"{label}.index")
    attempts = non_negative_int(raw["attempts"], f"{label}.attempts")
    retries = non_negative_int(raw["corrective_retries"], f"{label}.corrective_retries")
    if index < 1:
        raise StageMetricsError(f"{label}.index must be at least 1.")
    if attempts < 1:
        raise StageMetricsError(f"{label}.attempts must be at least 1.")
    if retries > attempts:
        raise StageMetricsError(f"{label}.corrective_retries cannot exceed attempts.")
    validation_mode = raw["validation_mode"]
    if validation_mode is not None:
        validation_mode = nonempty_text(validation_mode, f"{label}.validation_mode")
    return StageMetricUnit(
        index=index,
        input_characters=non_negative_int(raw["input_characters"], f"{label}.input_characters"),
        output_items=non_negative_int(raw["output_items"], f"{label}.output_items"),
        attempts=attempts,
        corrective_retries=retries,
        prompt_tokens=optional_non_negative_int(raw["prompt_tokens"], f"{label}.prompt_tokens"),
        output_tokens=optional_non_negative_int(raw["output_tokens"], f"{label}.output_tokens"),
        validation_mode=validation_mode,
        phases_seconds=validate_phases(raw["phases_seconds"], f"{label}.phases_seconds"),
        completed_at=nonempty_text(raw["completed_at"], f"{label}.completed_at"),
    )


def validate_event(value: JsonValue, label: str) -> StageMetricEvent | None:
    if value is None:
        return None
    raw = _mapping_with_fields(
        value,
        label=label,
        required={
            "attempts",
            "corrective_retries",
            "prompt_tokens",
            "output_tokens",
            "validation_mode",
            "phases_seconds",
            "completed_at",
        },
    )
    attempts = non_negative_int(raw["attempts"], f"{label}.attempts")
    retries = non_negative_int(raw["corrective_retries"], f"{label}.corrective_retries")
    if retries > attempts:
        raise StageMetricsError(f"{label}.corrective_retries cannot exceed attempts.")
    validation_mode = raw["validation_mode"]
    if validation_mode is not None:
        validation_mode = nonempty_text(validation_mode, f"{label}.validation_mode")
    return StageMetricEvent(
        attempts=attempts,
        corrective_retries=retries,
        prompt_tokens=optional_non_negative_int(raw["prompt_tokens"], f"{label}.prompt_tokens"),
        output_tokens=optional_non_negative_int(raw["output_tokens"], f"{label}.output_tokens"),
        validation_mode=validation_mode,
        phases_seconds=validate_phases(raw["phases_seconds"], f"{label}.phases_seconds"),
        completed_at=nonempty_text(raw["completed_at"], f"{label}.completed_at"),
    )


def normalize_status(value: JsonValue) -> StageMetricStatus:
    if value not in ALLOWED_STATUSES:
        raise StageMetricsError("Stage metrics status is unsupported.")
    return cast(StageMetricStatus, value)
