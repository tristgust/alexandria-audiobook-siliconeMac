from __future__ import annotations

from pathlib import Path
from typing import Any

from generation_state import (
    clear_generation_state,
)


class GenerationActionBlockedError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        checkpoint_status: str,
        reason_codes: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.checkpoint_status = checkpoint_status
        self.reason_codes = list(
            reason_codes or []
        )


def choose_generation_action(
    status: dict[str, Any],
) -> str:
    process = status.get(
        "process",
        {},
    )

    if process.get("running"):
        raise GenerationActionBlockedError(
            "Script generation is already running.",
            checkpoint_status="running",
            reason_codes=[
                "generation_already_running"
            ],
        )

    checkpoint = status.get(
        "checkpoint",
        {},
    )
    checkpoint_status = checkpoint.get(
        "status",
        "unknown",
    )

    if checkpoint_status == "none":
        return "new"

    if checkpoint_status == "compatible":
        completed = checkpoint.get(
            "completed_chunks",
            0,
        )

        return (
            "resume"
            if isinstance(completed, int)
            and not isinstance(completed, bool)
            and completed > 0
            else "new"
        )

    if checkpoint_status == "finalization_pending":
        return "finalize"

    reason_codes = checkpoint.get(
        "reason_codes",
        [],
    )
    explanation = checkpoint.get(
        "explanation",
    )

    if not isinstance(explanation, str) or not explanation:
        explanation = (
            "Saved generation progress cannot "
            "be used with the current inputs."
        )

    raise GenerationActionBlockedError(
        explanation,
        checkpoint_status=str(
            checkpoint_status
        ),
        reason_codes=[
            str(code)
            for code in reason_codes
        ],
    )


def discard_generation_checkpoint(
    path: str | Path,
) -> bool:
    target = Path(path)
    existed = target.exists()

    clear_generation_state(
        target
    )

    return existed
