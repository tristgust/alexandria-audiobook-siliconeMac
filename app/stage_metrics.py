"""Compatibility facade for persisted stage timing metrics."""

from stage_metric_contract import (
    DEFAULT_MAX_UNITS,
    STAGE_METRICS_SCHEMA_VERSION,
    StageMetricsError,
)
from stage_metric_schema import validate_stage_metrics
from stage_metric_store import (
    mark_stage_metrics_failed,
    prepare_stage_metrics,
    record_stage_event,
    record_stage_unit,
)
from stage_metric_summary import (
    DEFAULT_STATUS_LIMIT,
    ETA_FRESHNESS_SECONDS,
    MIN_ETA_UNITS,
    read_stage_metrics,
    summarize_stage_metrics,
)


__all__ = [
    "DEFAULT_MAX_UNITS",
    "DEFAULT_STATUS_LIMIT",
    "ETA_FRESHNESS_SECONDS",
    "MIN_ETA_UNITS",
    "STAGE_METRICS_SCHEMA_VERSION",
    "StageMetricsError",
    "mark_stage_metrics_failed",
    "prepare_stage_metrics",
    "read_stage_metrics",
    "record_stage_event",
    "record_stage_unit",
    "summarize_stage_metrics",
    "validate_stage_metrics",
]
