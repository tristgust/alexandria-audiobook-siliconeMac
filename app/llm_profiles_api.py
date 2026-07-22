from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from generation_state import atomic_json_write
from llm_profiles import (
    LLMProfileConflictError,
    LLMProfileValidationError,
    build_profiles_status,
    config_for_llm_stage,
    remove_stage_profile,
    update_stage_profile,
)


_PROFILE_LOCK = threading.RLock()


class LLMProfilesApiError(RuntimeError):
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


def _read_config(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {}
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LLMProfilesApiError(
            status_code=409,
            code="llm_profiles_config_unreadable",
            detail=f"Configuration could not be read: {exc}",
        ) from exc
    if not isinstance(value, dict):
        raise LLMProfilesApiError(
            status_code=409,
            code="llm_profiles_config_invalid",
            detail="Configuration root must be a JSON object.",
        )
    return value


def get_llm_profiles_payload(
    *,
    config_path: str | Path,
) -> dict[str, Any]:
    config = _read_config(config_path)
    try:
        status = build_profiles_status(config)
    except LLMProfileValidationError as exc:
        raise LLMProfilesApiError(
            status_code=409,
            code="llm_profiles_invalid",
            detail=str(exc),
        ) from exc
    status["config_exists"] = Path(config_path).exists()
    return status


def get_llm_stage_profile_payload(
    *,
    config_path: str | Path,
    stage: str,
) -> dict[str, Any]:
    config = _read_config(config_path)
    try:
        status = build_profiles_status(config)
        effective_config = config_for_llm_stage(
            config,
            stage=stage,
        )
    except LLMProfileValidationError as exc:
        raise LLMProfilesApiError(
            status_code=422,
            code="llm_profile_rejected",
            detail=str(exc),
        ) from exc
    entry = next(
        (item for item in status["stages"] if item["stage"] == stage),
        None,
    )
    if entry is None:
        raise LLMProfilesApiError(
            status_code=404,
            code="llm_profile_stage_not_found",
            detail=f"Unknown LLM profile stage: {stage!r}.",
        )
    return {
        "profiles_fingerprint": status["profiles_fingerprint"],
        "stage": entry,
        "effective_llm": effective_config.get("llm", {}),
    }


def update_llm_stage_profile_payload(
    *,
    config_path: str | Path,
    stage: str,
    profile: dict[str, Any],
    expected_profiles_fingerprint: str,
) -> dict[str, Any]:
    target = Path(config_path)
    with _PROFILE_LOCK:
        config = _read_config(target)
        try:
            updated = update_stage_profile(
                config,
                stage=stage,
                profile=profile,
                expected_profiles_fingerprint=(
                    expected_profiles_fingerprint
                ),
            )
        except LLMProfileConflictError as exc:
            raise LLMProfilesApiError(
                status_code=409,
                code="stale_llm_profiles",
                detail=str(exc),
            ) from exc
        except LLMProfileValidationError as exc:
            raise LLMProfilesApiError(
                status_code=422,
                code="llm_profile_rejected",
                detail=str(exc),
            ) from exc
        atomic_json_write(updated, target)
        return get_llm_stage_profile_payload(
            config_path=target,
            stage=stage,
        )


def remove_llm_stage_profile_payload(
    *,
    config_path: str | Path,
    stage: str,
    expected_profiles_fingerprint: str,
) -> dict[str, Any]:
    target = Path(config_path)
    with _PROFILE_LOCK:
        config = _read_config(target)
        try:
            updated = remove_stage_profile(
                config,
                stage=stage,
                expected_profiles_fingerprint=(
                    expected_profiles_fingerprint
                ),
            )
        except LLMProfileConflictError as exc:
            raise LLMProfilesApiError(
                status_code=409,
                code="stale_llm_profiles",
                detail=str(exc),
            ) from exc
        except LLMProfileValidationError as exc:
            raise LLMProfilesApiError(
                status_code=422,
                code="llm_profile_rejected",
                detail=str(exc),
            ) from exc
        atomic_json_write(updated, target)
        return get_llm_profiles_payload(config_path=target)
