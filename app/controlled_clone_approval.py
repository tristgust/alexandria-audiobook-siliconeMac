from __future__ import annotations

import copy
import secrets
import threading
import time
from typing import Any, Iterable


CONTROLLED_CLONE_APPROVAL_TTL_SECONDS = 30 * 60
_APPROVAL_LOCK = threading.RLock()
_PENDING_PREVIEWS: dict[str, dict[str, Any]] = {}
_CONFIRMED_APPROVALS: dict[str, dict[str, Any]] = {}


class ControlledCloneApprovalError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = copy.deepcopy(details or {})


class ControlledCloneApprovalValidationError(ControlledCloneApprovalError):
    pass


class ControlledCloneApprovalConflictError(ControlledCloneApprovalError):
    pass


def _require_text(value: Any, label: str, *, max_length: int = 1024) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ControlledCloneApprovalValidationError(
            "controlled_clone_approval_invalid",
            f"{label} must be non-empty text.",
        )
    text = value.strip()
    if len(text) > max_length:
        raise ControlledCloneApprovalValidationError(
            "controlled_clone_approval_invalid",
            f"{label} must be no longer than {max_length} characters.",
        )
    return text


def _cleanup_expired(now: float) -> None:
    for collection in (_PENDING_PREVIEWS, _CONFIRMED_APPROVALS):
        expired = [
            key
            for key, value in collection.items()
            if now - float(value["created_at_monotonic"])
            > CONTROLLED_CLONE_APPROVAL_TTL_SECONDS
        ]
        for key in expired:
            collection.pop(key, None)


def register_controlled_clone_preview(
    *,
    speaker: str,
    preview_fingerprint: str,
    configuration_fingerprint: str,
    created_at_monotonic: float | None = None,
) -> dict[str, Any]:
    normalized_speaker = _require_text(speaker, "Speaker")
    preview = _require_text(
        preview_fingerprint,
        "Preview fingerprint",
        max_length=128,
    )
    configuration = _require_text(
        configuration_fingerprint,
        "Configuration fingerprint",
        max_length=128,
    )
    now = time.monotonic() if created_at_monotonic is None else float(
        created_at_monotonic
    )
    record = {
        "speaker": normalized_speaker,
        "preview_fingerprint": preview,
        "configuration_fingerprint": configuration,
        "created_at_monotonic": now,
    }
    with _APPROVAL_LOCK:
        _cleanup_expired(now)
        _PENDING_PREVIEWS[preview] = record
    return {
        "speaker": normalized_speaker,
        "preview_fingerprint": preview,
        "configuration_fingerprint": configuration,
        "expires_in_seconds": CONTROLLED_CLONE_APPROVAL_TTL_SECONDS,
    }


def confirm_controlled_clone_preview(
    *,
    speaker: str,
    preview_fingerprint: str,
    configuration_fingerprint: str,
    confirmed_at_monotonic: float | None = None,
) -> dict[str, Any]:
    normalized_speaker = _require_text(speaker, "Speaker")
    preview = _require_text(
        preview_fingerprint,
        "Preview fingerprint",
        max_length=128,
    )
    configuration = _require_text(
        configuration_fingerprint,
        "Configuration fingerprint",
        max_length=128,
    )
    now = time.monotonic() if confirmed_at_monotonic is None else float(
        confirmed_at_monotonic
    )
    with _APPROVAL_LOCK:
        _cleanup_expired(now)
        pending = _PENDING_PREVIEWS.get(preview)
        if pending is None:
            raise ControlledCloneApprovalConflictError(
                "controlled_clone_preview_expired",
                "Generate a new controlled-clone preview before confirming it.",
            )
        if pending["speaker"] != normalized_speaker:
            raise ControlledCloneApprovalConflictError(
                "controlled_clone_preview_mismatch",
                "The preview belongs to a different speaker.",
            )
        if pending["configuration_fingerprint"] != configuration:
            raise ControlledCloneApprovalConflictError(
                "controlled_clone_preview_mismatch",
                "The controlled-clone settings changed after preview generation.",
            )
        token = secrets.token_urlsafe(32)
        _CONFIRMED_APPROVALS[token] = {
            **pending,
            "created_at_monotonic": now,
        }
        _PENDING_PREVIEWS.pop(preview, None)
    return {
        "status": "confirmed",
        "speaker": normalized_speaker,
        "preview_fingerprint": preview,
        "configuration_fingerprint": configuration,
        "approval_token": token,
        "expires_in_seconds": CONTROLLED_CLONE_APPROVAL_TTL_SECONDS,
    }


def consume_controlled_clone_approvals(
    approvals: Iterable[dict[str, str]],
    *,
    consumed_at_monotonic: float | None = None,
) -> None:
    requested = [copy.deepcopy(value) for value in approvals]
    if not requested:
        return
    now = time.monotonic() if consumed_at_monotonic is None else float(
        consumed_at_monotonic
    )
    validated: list[str] = []
    with _APPROVAL_LOCK:
        _cleanup_expired(now)
        for request in requested:
            speaker = _require_text(request.get("speaker"), "Speaker")
            token = _require_text(
                request.get("approval_token"),
                "Approval token",
                max_length=512,
            )
            configuration = _require_text(
                request.get("configuration_fingerprint"),
                "Configuration fingerprint",
                max_length=128,
            )
            approval = _CONFIRMED_APPROVALS.get(token)
            if approval is None:
                raise ControlledCloneApprovalConflictError(
                    "controlled_clone_approval_required",
                    "Generate, listen through, and confirm the matching preview before saving this controlled clone.",
                    details={"speaker": speaker},
                )
            if approval["speaker"] != speaker:
                raise ControlledCloneApprovalConflictError(
                    "controlled_clone_approval_mismatch",
                    "The listen-confirmation receipt belongs to a different speaker.",
                    details={"speaker": speaker},
                )
            if approval["configuration_fingerprint"] != configuration:
                raise ControlledCloneApprovalConflictError(
                    "controlled_clone_approval_mismatch",
                    "The controlled-clone identity or settings changed after listening.",
                    details={"speaker": speaker},
                )
            validated.append(token)
        for token in validated:
            _CONFIRMED_APPROVALS.pop(token, None)


def clear_controlled_clone_approvals() -> None:
    with _APPROVAL_LOCK:
        _PENDING_PREVIEWS.clear()
        _CONFIRMED_APPROVALS.clear()
