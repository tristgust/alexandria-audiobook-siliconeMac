"""Path policy for the canonical multimodel Round 1 review handoff."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final


CANONICAL_PUBLIC_ROOT_NAME: Final[str] = "review-round1-complete-final"
ANSWER_KEY_ROOT_NAME: Final[str] = "review-round1-complete-final-answer-keys"

LEGACY_PUBLIC_ROOT_NAMES: Final[tuple[str, ...]] = (
    "review",
    "review-integrity-reconciled",
    "review-round1-complete",
)
LEGACY_ANSWER_KEY_ROOT_NAMES: Final[tuple[str, ...]] = (
    "review-integrity-reconciled-answer-keys",
    "review-round1-complete-answer-keys",
)
LEGACY_HANDOFF_ROOT_NAMES: Final[tuple[str, ...]] = (
    "review",
    "review-integrity-reconciled",
    "review-integrity-reconciled-answer-keys",
    "review-round1-complete",
    "review-round1-complete-answer-keys",
)


class HandoffPolicyError(ValueError):
    """A handoff path violates the canonical Round 1 policy."""

    code: str
    path: Path
    detail: str

    def __init__(self, code: str, path: Path, detail: str) -> None:
        self.code = code
        self.path = path
        self.detail = detail
        super().__init__(f"{code}: {detail}: {path}")


@dataclass(frozen=True, slots=True)
class Round1HandoffPaths:
    """The contained public and private roots for one Round 1 handoff."""

    evidence_root: Path
    public_root: Path
    answer_key_root: Path


def _resolve_evidence_root(evidence_root: Path) -> Path:
    resolved = evidence_root.expanduser().resolve()
    if resolved.exists() and not resolved.is_dir():
        raise HandoffPolicyError(
            "invalid_evidence_root",
            resolved,
            "evidence root must be a directory",
        )
    return resolved


def _resolve_candidate(root: Path, candidate: Path, expected_name: str) -> Path:
    lexical = candidate.expanduser()
    resolved = lexical.resolve()
    if lexical.name in LEGACY_PUBLIC_ROOT_NAMES:
        raise HandoffPolicyError(
            "stale_public_root",
            resolved,
            "legacy public root cannot be used as the canonical handoff",
        )
    if lexical.name != expected_name:
        raise HandoffPolicyError(
            "disallowed_root_name",
            resolved,
            f"root name must be {expected_name}",
        )
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise HandoffPolicyError(
            "path_outside_evidence",
            resolved,
            "handoff root must be contained by evidence root",
        ) from exc
    if len(relative.parts) != 1:
        raise HandoffPolicyError(
            "path_not_evidence_child",
            resolved,
            "handoff root must be a direct evidence child",
        )
    if resolved.name != expected_name:
        raise HandoffPolicyError(
            "disallowed_root_name",
            resolved,
            f"root name must be {expected_name}",
        )
    if resolved.exists() and not resolved.is_dir():
        raise HandoffPolicyError(
            "stale_public_root",
            resolved,
            "an existing handoff root must be a directory",
        )
    return resolved


def resolve_round1_handoff_paths(
    evidence_root: Path,
    *,
    public_root: Path | None = None,
    answer_key_root: Path | None = None,
) -> Round1HandoffPaths:
    """Resolve and validate canonical Round 1 public/private output roots."""

    root = _resolve_evidence_root(evidence_root)
    public = _resolve_candidate(
        root,
        public_root or root / CANONICAL_PUBLIC_ROOT_NAME,
        CANONICAL_PUBLIC_ROOT_NAME,
    )
    answer_keys = _resolve_candidate(
        root,
        answer_key_root or root / ANSWER_KEY_ROOT_NAME,
        ANSWER_KEY_ROOT_NAME,
    )
    colocated = public / "answer-keys"
    if colocated.exists() or colocated.is_symlink():
        raise HandoffPolicyError(
            "colocated_answer_keys",
            colocated,
            "public root contains an answer-keys directory",
        )
    if answer_keys == public or answer_keys.is_relative_to(public):
        raise HandoffPolicyError(
            "path_not_evidence_child",
            answer_keys,
            "answer keys must be a sibling private root",
        )
    return Round1HandoffPaths(root, public, answer_keys)


def supersedable_legacy_roots(evidence_root: Path) -> tuple[Path, ...]:
    """Return existing legacy roots eligible for non-destructive supersession."""

    root = _resolve_evidence_root(evidence_root)
    identified: list[Path] = []
    for name in LEGACY_HANDOFF_ROOT_NAMES:
        candidate = root / name
        if not candidate.exists() and not candidate.is_symlink():
            continue
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root):
            raise HandoffPolicyError(
                "path_outside_evidence",
                resolved,
                "legacy root must be contained by evidence root",
            )
        identified.append(resolved)
    return tuple(identified)
