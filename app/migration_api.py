from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from migration import (
    MIGRATION_STATE_FILENAME,
    MigrationConflictError,
    MigrationValidationError,
    apply_migration_plan,
    build_migration_plan,
    list_migration_operations,
    load_migration_operation,
    rollback_migration,
)


class MigrationApiError(RuntimeError):
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
        return {
            "code": self.code,
            "message": self.detail,
        }


def _read_migration_state(root: Path) -> dict[str, Any] | None:
    path = root / MIGRATION_STATE_FILENAME
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MigrationApiError(
            status_code=409,
            code="migration_state_unreadable",
            detail=f"Migration state could not be read: {exc}",
        ) from exc
    if not isinstance(value, dict):
        raise MigrationApiError(
            status_code=409,
            code="migration_state_invalid",
            detail="Migration state must be a JSON object.",
        )
    return value


def get_migration_status_payload(
    *,
    root_dir: str | Path,
    config_path: str | Path,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    try:
        plan = build_migration_plan(
            root_dir=root,
            config_path=config_path,
        )
    except MigrationValidationError as exc:
        raise MigrationApiError(
            status_code=409,
            code="migration_context_invalid",
            detail=str(exc),
        ) from exc
    return {
        **plan,
        "migration_required": bool(plan["actions"]),
        "migration_blocked": bool(plan["blockers"]),
        "last_migration": _read_migration_state(root),
    }


def apply_migration_payload(
    *,
    root_dir: str | Path,
    config_path: str | Path,
    expected_plan_fingerprint: str,
    confirm: bool,
    at_utc: str | None = None,
) -> dict[str, Any]:
    try:
        return apply_migration_plan(
            root_dir=root_dir,
            config_path=config_path,
            expected_plan_fingerprint=expected_plan_fingerprint,
            confirm=confirm,
            at_utc=at_utc,
        )
    except MigrationConflictError as exc:
        raise MigrationApiError(
            status_code=409,
            code="stale_migration_plan",
            detail=str(exc),
        ) from exc
    except MigrationValidationError as exc:
        raise MigrationApiError(
            status_code=422,
            code="migration_rejected",
            detail=str(exc),
        ) from exc


def get_migration_history_payload(
    *,
    root_dir: str | Path,
) -> dict[str, Any]:
    try:
        return list_migration_operations(root_dir=root_dir)
    except MigrationValidationError as exc:
        raise MigrationApiError(
            status_code=409,
            code="migration_history_invalid",
            detail=str(exc),
        ) from exc


def get_migration_operation_payload(
    *,
    root_dir: str | Path,
    operation_id: str,
) -> dict[str, Any]:
    try:
        return load_migration_operation(
            root_dir=root_dir,
            operation_id=operation_id,
        )
    except MigrationValidationError as exc:
        message = str(exc)
        if "was not found" in message:
            raise MigrationApiError(
                status_code=404,
                code="migration_operation_not_found",
                detail=message,
            ) from exc
        raise MigrationApiError(
            status_code=422,
            code="migration_operation_invalid",
            detail=message,
        ) from exc


def rollback_migration_payload(
    *,
    root_dir: str | Path,
    config_path: str | Path,
    operation_id: str,
    at_utc: str | None = None,
) -> dict[str, Any]:
    try:
        rollback = rollback_migration(
            root_dir=root_dir,
            operation_id=operation_id,
            at_utc=at_utc,
        )
    except MigrationConflictError as exc:
        raise MigrationApiError(
            status_code=409,
            code="migration_rollback_conflict",
            detail=str(exc),
        ) from exc
    except MigrationValidationError as exc:
        message = str(exc)
        if "was not found" in message:
            raise MigrationApiError(
                status_code=404,
                code="migration_operation_not_found",
                detail=message,
            ) from exc
        raise MigrationApiError(
            status_code=422,
            code="migration_rollback_rejected",
            detail=message,
        ) from exc
    return {
        "rollback": rollback,
        "status": get_migration_status_payload(
            root_dir=root_dir,
            config_path=config_path,
        ),
    }
