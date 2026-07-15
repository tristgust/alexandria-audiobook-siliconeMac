from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any


TELEMETRY_SCHEMA_VERSION = 1
TELEMETRY_ENVIRONMENT_VARIABLE = (
    "ALEXANDRIA_LLM_TELEMETRY_PATH"
)
DEFAULT_TELEMETRY_PATH = (
    Path(__file__).resolve().parent.parent
    / "logs"
    / "llm_runtime.json"
)


def llm_telemetry_path(
    path: str | os.PathLike[str] | None = None,
) -> Path:
    if path is not None:
        return Path(path)

    configured = os.environ.get(
        TELEMETRY_ENVIRONMENT_VARIABLE
    )

    if configured:
        return Path(configured)

    return DEFAULT_TELEMETRY_PATH


def _json_safe(
    value: Any,
) -> Any:
    if value is None or isinstance(
        value,
        (str, int, float, bool),
    ):
        return value

    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(child)
            for key, child in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [
            _json_safe(child)
            for child in value
        ]

    return str(value)


def _empty_snapshot() -> dict[str, Any]:
    return {
        "schema_version": (
            TELEMETRY_SCHEMA_VERSION
        ),
        "latest_request": None,
        "telemetry_error": None,
    }


def _write_latest_request(
    event: Mapping[str, Any],
    *,
    path: str | os.PathLike[str] | None = None,
) -> bool:
    destination = llm_telemetry_path(path)
    temporary = destination.with_name(
        (
            f".{destination.name}."
            f"{os.getpid()}."
            f"{time.time_ns()}.tmp"
        )
    )

    snapshot = _empty_snapshot()
    snapshot["latest_request"] = _json_safe(
        dict(event)
    )

    try:
        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with temporary.open(
            "w",
            encoding="utf-8",
        ) as output:
            json.dump(
                snapshot,
                output,
                indent=2,
                ensure_ascii=False,
            )
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())

        os.replace(
            temporary,
            destination,
        )
    except (
        OSError,
        TypeError,
        ValueError,
    ):
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass

        return False

    return True


def record_llm_request(
    *,
    model_name: str,
    contract: str,
    backend: str,
    validation_mode: str,
    metrics: Mapping[str, Any] | None,
    request_elapsed_seconds: float,
    thinking: bool,
    structured_output: bool,
    corrective_retry: bool,
    path: str | os.PathLike[str] | None = None,
) -> bool:
    safe_metrics = (
        dict(metrics)
        if isinstance(metrics, Mapping)
        else {}
    )

    retry_reason = safe_metrics.get(
        "initial_validation_error"
    )

    return _write_latest_request(
        {
            "recorded_at": time.time(),
            "status": "success",
            "model_name": model_name,
            "contract": contract,
            "backend": backend,
            "validation_mode": validation_mode,
            "corrective_retry_used": (
                validation_mode
                == "corrective_retry"
            ),
            "retry_reason": retry_reason,
            "request_elapsed_seconds": (
                request_elapsed_seconds
            ),
            "thinking": thinking,
            "structured_output": (
                structured_output
            ),
            "corrective_retry_enabled": (
                corrective_retry
            ),
            "metrics": safe_metrics,
        },
        path=path,
    )


def record_llm_failure(
    *,
    model_name: str,
    contract: str,
    backend: str,
    request_elapsed_seconds: float,
    error: str,
    thinking: bool,
    structured_output: bool,
    corrective_retry: bool,
    path: str | os.PathLike[str] | None = None,
) -> bool:
    return _write_latest_request(
        {
            "recorded_at": time.time(),
            "status": "error",
            "model_name": model_name,
            "contract": contract,
            "backend": backend,
            "validation_mode": None,
            "corrective_retry_used": None,
            "retry_reason": error,
            "request_elapsed_seconds": (
                request_elapsed_seconds
            ),
            "thinking": thinking,
            "structured_output": (
                structured_output
            ),
            "corrective_retry_enabled": (
                corrective_retry
            ),
            "metrics": {},
        },
        path=path,
    )


def record_llm_pipeline_result(
    *,
    stage: str,
    unit_kind: str,
    unit_index: int,
    unit_total: int,
    outer_attempt: int,
    unit_elapsed_seconds: float,
    audit_kind: str,
    audit_result: Mapping[str, Any],
    expected_contract: str | None = None,
    path: str | os.PathLike[str] | None = None,
) -> bool:
    snapshot = read_llm_telemetry(
        path=path
    )
    latest = snapshot.get("latest_request")

    if not isinstance(latest, Mapping):
        return False

    latest_request = dict(latest)

    if (
        expected_contract is not None
        and latest_request.get("contract")
        != expected_contract
    ):
        return False

    safe_audit = (
        dict(audit_result)
        if isinstance(audit_result, Mapping)
        else {}
    )

    passed = safe_audit.get("passed")
    retry_reason = None

    if passed is False:
        issues = safe_audit.get("issues", [])

        if isinstance(issues, list):
            blocking_issue = next(
                (
                    issue
                    for issue in issues
                    if (
                        isinstance(issue, Mapping)
                        and issue.get("severity")
                        == "blocking"
                    )
                ),
                None,
            )

            if blocking_issue is not None:
                code = blocking_issue.get("code")
                message = blocking_issue.get(
                    "message"
                )

                if code and message:
                    retry_reason = (
                        f"{code}: {message}"
                    )
                else:
                    retry_reason = (
                        message
                        or code
                    )

    latest_request["pipeline"] = {
        "recorded_at": time.time(),
        "stage": stage,
        "unit_kind": unit_kind,
        "unit_index": unit_index,
        "unit_total": unit_total,
        "outer_attempt": outer_attempt,
        "outer_retry_used": (
            outer_attempt > 1
        ),
        "unit_elapsed_seconds": (
            unit_elapsed_seconds
        ),
        "audit_kind": audit_kind,
        "audit_passed": passed,
        "outcome": (
            "accepted"
            if passed is True
            else "blocked"
            if passed is False
            else "unknown"
        ),
        "retry_reason": retry_reason,
        "audit": safe_audit,
    }

    return _write_latest_request(
        latest_request,
        path=path,
    )


def read_llm_telemetry(
    *,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    source = llm_telemetry_path(path)

    if not source.exists():
        return _empty_snapshot()

    try:
        value = json.loads(
            source.read_text(encoding="utf-8")
        )
    except (
        OSError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        snapshot = _empty_snapshot()
        snapshot["telemetry_error"] = str(exc)
        return snapshot

    if not isinstance(value, dict):
        snapshot = _empty_snapshot()
        snapshot["telemetry_error"] = (
            "LLM telemetry root is not an object"
        )
        return snapshot

    snapshot = _empty_snapshot()
    snapshot.update(value)

    if not isinstance(
        snapshot.get("latest_request"),
        (dict, type(None)),
    ):
        snapshot["latest_request"] = None
        snapshot["telemetry_error"] = (
            "latest_request is not an object"
        )

    return snapshot
