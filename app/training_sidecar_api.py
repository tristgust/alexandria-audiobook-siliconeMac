from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from training_sidecar_service import (
    TrainingSidecarConflictError,
    TrainingSidecarError,
    TrainingSidecarValidationError,
    build_sidecar_status,
    create_sidecar_job,
    execute_sidecar_job,
    import_external_sidecar_artifact,
    install_mlx_lora_artifact,
    read_sidecar_job,
)


class TrainingSidecarApiError(RuntimeError):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        detail: str,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.detail}


def _translate(exc: Exception) -> TrainingSidecarApiError:
    if isinstance(exc, TrainingSidecarConflictError):
        return TrainingSidecarApiError(
            status_code=409,
            code="training_sidecar_conflict",
            detail=str(exc),
        )
    if isinstance(exc, TrainingSidecarValidationError):
        detail = str(exc)
        if "was not found" in detail:
            return TrainingSidecarApiError(
                status_code=404,
                code="training_sidecar_job_not_found",
                detail=detail,
            )
        return TrainingSidecarApiError(
            status_code=422,
            code="training_sidecar_rejected",
            detail=detail,
        )
    if isinstance(exc, TrainingSidecarError):
        return TrainingSidecarApiError(
            status_code=409,
            code="training_sidecar_error",
            detail=str(exc),
        )
    return TrainingSidecarApiError(
        status_code=500,
        code="training_sidecar_failed",
        detail=str(exc),
    )


def get_training_sidecar_status_payload(
    *,
    root_dir: str | Path,
) -> dict[str, Any]:
    try:
        return build_sidecar_status(root_dir)
    except TrainingSidecarError as exc:
        raise _translate(exc) from exc


def get_training_sidecar_job_payload(
    *,
    root_dir: str | Path,
    job_id: str,
) -> dict[str, Any]:
    try:
        return read_sidecar_job(
            root_dir=root_dir,
            job_id=job_id,
        )
    except TrainingSidecarError as exc:
        raise _translate(exc) from exc


def create_training_sidecar_job_payload(
    *,
    root_dir: str | Path,
    action: str,
    payload: dict[str, Any] | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    try:
        return create_sidecar_job(
            root_dir=root_dir,
            action=action,
            payload=payload,
            created_at_utc=created_at_utc,
        )
    except TrainingSidecarError as exc:
        raise _translate(exc) from exc


def execute_training_sidecar_job_payload(
    *,
    root_dir: str | Path,
    job_id: str,
    timeout: float | None = None,
    run: Callable | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "root_dir": root_dir,
        "job_id": job_id,
        "timeout": timeout,
    }
    if run is not None:
        kwargs["run"] = run
    try:
        return execute_sidecar_job(**kwargs)
    except TrainingSidecarError as exc:
        raise _translate(exc) from exc


def install_training_sidecar_mlx_artifact_payload(
    *,
    root_dir: str | Path,
    source_path: str,
    adapter_id: str,
    name: str,
    dataset_id: str | None = None,
    training_metrics_path: str | None = None,
    installed_at_utc: str | None = None,
) -> dict[str, Any]:
    try:
        return install_mlx_lora_artifact(
            root_dir=root_dir,
            source_path=source_path,
            adapter_id=adapter_id,
            name=name,
            dataset_id=dataset_id,
            training_metrics_path=training_metrics_path,
            installed_at_utc=installed_at_utc,
        )
    except TrainingSidecarError as exc:
        raise _translate(exc) from exc


def import_training_sidecar_artifact_payload(
    *,
    root_dir: str | Path,
    source_path: str,
    imported_at_utc: str | None = None,
) -> dict[str, Any]:
    try:
        return import_external_sidecar_artifact(
            root_dir=root_dir,
            source_path=source_path,
            imported_at_utc=imported_at_utc,
        )
    except TrainingSidecarError as exc:
        raise _translate(exc) from exc
