from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

from character_roster import (
    CharacterRosterError,
    read_character_roster,
    validate_character_roster,
)
from character_roster_actions import list_character_roster_revisions
from generation_state import fingerprint_value
from roster_import_reconciliation import (
    RosterImportReconciliationError,
    get_pending_issue_focused_roster_reconciliation,
)


SCHEMA_VERSION = 1


class RosterReconciliationError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.details = copy.deepcopy(details or {})

    def as_detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "details": copy.deepcopy(self.details),
        }


def _issue(
    code: str,
    title: str,
    explanation: str,
    *,
    target_id: str,
    blocking: bool = True,
    requires_acknowledgement: bool = False,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    seed = {
        "code": code,
        "target_id": target_id,
        "context": dict(context or {}),
    }
    return {
        "issue_id": "roster_issue_" + fingerprint_value(seed)[:24],
        "code": code,
        "title": title,
        "explanation": explanation,
        "native_destination": "cast",
        "target_id": target_id,
        "blocking": bool(blocking),
        "requires_acknowledgement": bool(requires_acknowledgement),
        "context": copy.deepcopy(dict(context or {})),
    }


def _inspect_roster_artifact(
    path: str | Path,
    *,
    expected_status: str,
    source_snapshot: Mapping[str, Any] | None,
    source_text: str | None,
) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {
            "status": "missing",
            "exists": False,
            "roster": None,
            "fingerprint": None,
            "error": None,
        }
    try:
        roster = read_character_roster(
            target,
            expected_status=expected_status,
        )
    except CharacterRosterError as exc:
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raw = None
        return {
            "status": "invalid",
            "exists": True,
            "roster": raw if isinstance(raw, Mapping) else None,
            "fingerprint": None,
            "error": str(exc),
        }

    fingerprint_key = (
        "draft_fingerprint"
        if expected_status == "draft"
        else "roster_fingerprint"
    )
    current_source_fingerprint = (
        str(source_snapshot.get("fingerprint") or "")
        if source_snapshot is not None
        else ""
    )
    roster_source_fingerprint = str(
        (roster.get("source") or {}).get("fingerprint") or ""
    )
    if not current_source_fingerprint or source_text is None:
        return {
            "status": "source_unavailable",
            "exists": True,
            "roster": roster,
            "fingerprint": roster.get(fingerprint_key),
            "error": "A readable selected source is required to validate this roster.",
        }
    if roster_source_fingerprint != current_source_fingerprint:
        return {
            "status": "incompatible_source",
            "exists": True,
            "roster": roster,
            "fingerprint": roster.get(fingerprint_key),
            "error": "The roster belongs to a different selected source.",
        }
    try:
        validated = validate_character_roster(
            roster,
            source_text=source_text,
            expected_status=expected_status,
        )
    except CharacterRosterError as exc:
        return {
            "status": "invalid",
            "exists": True,
            "roster": roster,
            "fingerprint": roster.get(fingerprint_key),
            "error": str(exc),
        }
    return {
        "status": expected_status,
        "exists": True,
        "roster": validated,
        "fingerprint": validated.get(fingerprint_key),
        "error": None,
    }


def _stable_relationship_issues(draft: Mapping[str, Any]) -> list[dict[str, Any]]:
    entries = [
        item
        for item in draft.get("entries") or []
        if isinstance(item, Mapping)
    ]
    by_id = {
        str(item.get("id") or ""): item
        for item in entries
        if str(item.get("id") or "")
    }
    issues: list[dict[str, Any]] = []
    emitted: set[tuple[str, str]] = set()
    for entry_id, entry in by_id.items():
        for other_id_value in entry.get("possible_duplicate_ids") or []:
            other_id = str(other_id_value or "")
            relation = tuple(sorted((entry_id, other_id)))
            if relation in emitted:
                continue
            emitted.add(relation)
            other = by_id.get(other_id)
            reciprocal = bool(
                other is not None
                and entry_id in set(other.get("possible_duplicate_ids") or [])
            )
            if other_id == entry_id or other is None or not reciprocal:
                issues.append(
                    _issue(
                        "invalid_stable_id_relationship",
                        "Duplicate relationship is invalid",
                        "A possible-duplicate relationship is missing, self-referential, or not reciprocal.",
                        target_id=f"cast:character:{entry_id}",
                        context={
                            "entry_id": entry_id,
                            "other_entry_id": other_id,
                        },
                    )
                )
    return issues


def _draft_issues(draft: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues = _stable_relationship_issues(draft)
    for entry in draft.get("entries") or []:
        if not isinstance(entry, Mapping):
            continue
        entry_id = str(entry.get("id") or "")
        name = str(
            entry.get("display_name")
            or entry.get("canonical_name")
            or entry_id
        )
        status = str(entry.get("resolution_status") or "")
        if status == "duplicate_candidate":
            issues.append(
                _issue(
                    "duplicate_candidate",
                    f"Resolve possible duplicate: {name}",
                    "Merge this identity with the correct character or explicitly keep the identities separate before approval.",
                    target_id=f"cast:character:{entry_id}",
                    context={"entry_id": entry_id},
                )
            )
        elif status in {"unresolved", "unnamed"}:
            issues.append(
                _issue(
                    "unresolved_identity",
                    f"Unresolved identity: {name}",
                    "Resolve this identity, exclude it, or explicitly acknowledge that it will remain unresolved in the approved roster.",
                    target_id=f"cast:character:{entry_id}",
                    blocking=False,
                    requires_acknowledgement=True,
                    context={
                        "entry_id": entry_id,
                        "resolution_status": status,
                        "questions": copy.deepcopy(
                            entry.get("unresolved_questions") or []
                        ),
                    },
                )
            )
    for candidate in draft.get("duplicate_candidates") or []:
        if not isinstance(candidate, Mapping):
            continue
        entry_ids = [str(value or "") for value in candidate.get("entry_ids") or []]
        issues.append(
            _issue(
                "duplicate_candidate",
                "Duplicate decision required",
                str(
                    candidate.get("reason")
                    or "A possible duplicate pair requires an explicit merge or keep-separate decision."
                ),
                target_id=(
                    f"cast:character:{entry_ids[0]}"
                    if entry_ids
                    else "cast:issues"
                ),
                context={
                    "entry_ids": entry_ids,
                    "confidence": candidate.get("confidence"),
                },
            )
        )
    unique: dict[str, dict[str, Any]] = {}
    for item in issues:
        unique[item["issue_id"]] = item
    return list(unique.values())


def inspect_roster_reconciliation_project(
    *,
    root_dir: str | Path,
    source_snapshot: Mapping[str, Any] | None,
    source_text: str | None,
    draft_path: str | Path,
    approved_path: str | Path,
    history_root: str | Path,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    draft_artifact = _inspect_roster_artifact(
        draft_path,
        expected_status="draft",
        source_snapshot=source_snapshot,
        source_text=source_text,
    )
    approved_artifact = _inspect_roster_artifact(
        approved_path,
        expected_status="approved",
        source_snapshot=source_snapshot,
        source_text=source_text,
    )
    approved = approved_artifact.get("roster")
    draft = draft_artifact.get("roster")
    working_draft = bool(
        draft_artifact["status"] == "draft"
        and (
            approved_artifact["status"] != "approved"
            or draft.get("draft_fingerprint")
            != approved.get("approved_draft_fingerprint")
        )
    )

    issues: list[dict[str, Any]] = []
    for label, artifact in (
        ("Draft", draft_artifact),
        ("Approved roster", approved_artifact),
    ):
        if artifact["status"] == "invalid":
            relationship_issues = (
                _stable_relationship_issues(artifact["roster"])
                if isinstance(artifact.get("roster"), Mapping)
                else []
            )
            issues.extend(relationship_issues)
            issues.append(
                _issue(
                    "invalid_roster_artifact",
                    f"{label} is invalid",
                    str(artifact.get("error") or "The roster artifact is invalid."),
                    target_id="cast:issues",
                )
            )
        elif artifact["status"] == "incompatible_source":
            issues.append(
                _issue(
                    "incompatible_approved_roster"
                    if label == "Approved roster"
                    else "incompatible_roster_draft",
                    f"{label} belongs to another source",
                    str(artifact.get("error") or "The roster source does not match."),
                    target_id="cast:issues",
                )
            )
        elif artifact["status"] == "source_unavailable" and artifact["exists"]:
            issues.append(
                _issue(
                    "roster_source_unavailable",
                    f"{label} cannot be validated",
                    str(artifact.get("error") or "The selected source is unavailable."),
                    target_id="projects:source",
                )
            )

    if working_draft and isinstance(draft, Mapping):
        issues.extend(_draft_issues(draft))

    pending_import = None
    artifact_blocked = any(
        item["code"]
        in {
            "invalid_roster_artifact",
            "incompatible_approved_roster",
            "incompatible_roster_draft",
            "roster_source_unavailable",
        }
        for item in issues
    )
    if not artifact_blocked and source_snapshot is not None and source_text is not None:
        try:
            pending_import = get_pending_issue_focused_roster_reconciliation(
                root_dir=root_dir,
                source_snapshot=dict(source_snapshot),
                source_text=source_text,
                draft_path=draft_path,
                approved_path=approved_path,
                candidate_id=candidate_id,
            )
        except RosterImportReconciliationError as exc:
            issues.append(
                _issue(
                    exc.code,
                    "Imported roster reconciliation is unavailable",
                    str(exc),
                    target_id="cast:issues",
                    context=exc.details,
                )
            )
    if pending_import is not None:
        issues.extend(copy.deepcopy(pending_import["issues"]))

    blocking_issues = [item for item in issues if item.get("blocking")]
    acknowledgement_issues = [
        item for item in issues if item.get("requires_acknowledgement")
    ]
    unresolved_count = len(acknowledgement_issues)
    draft_fingerprint = (
        str(draft.get("draft_fingerprint") or "")
        if working_draft and isinstance(draft, Mapping)
        else None
    )
    approved_fingerprint = (
        str(approved.get("roster_fingerprint") or "")
        if approved_artifact["status"] == "approved"
        and isinstance(approved, Mapping)
        else None
    )
    import_pending = pending_import is not None
    approval_blocked = bool(blocking_issues or import_pending or not working_draft)
    can_approve_with_unresolved = bool(
        working_draft and not blocking_issues and not import_pending
    )
    can_approve_resolved = bool(
        can_approve_with_unresolved and unresolved_count == 0
    )

    revisions = list_character_roster_revisions(history_root)
    rollback_revision = next(
        (
            item
            for item in revisions
            if item.get("status") == "available"
            and item.get("replacement_roster_fingerprint")
            == approved_fingerprint
        ),
        None,
    )

    if any(item["code"] == "invalid_roster_artifact" for item in issues):
        state = "invalid"
    elif any(
        item["code"]
        in {"incompatible_approved_roster", "incompatible_roster_draft"}
        for item in issues
    ):
        state = "incompatible"
    elif pending_import is not None and pending_import["issues"]:
        state = "import_issues"
    elif pending_import is not None:
        state = "import_ready"
    elif blocking_issues:
        state = "review_required"
    elif can_approve_resolved:
        state = "ready_to_approve"
    elif can_approve_with_unresolved:
        state = "acknowledgement_required"
    elif approved_fingerprint:
        state = "approved"
    else:
        state = "not_started"

    safe_changes = (
        copy.deepcopy(pending_import.get("safe_changes") or [])
        if pending_import is not None
        else []
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "state": state,
        "source": {
            "available": source_snapshot is not None and source_text is not None,
            "fingerprint": (
                source_snapshot.get("fingerprint")
                if source_snapshot is not None
                else None
            ),
            "basename": (
                source_snapshot.get("basename")
                if source_snapshot is not None
                else None
            ),
        },
        "current": {
            "kind": (
                "draft"
                if working_draft
                else "approved"
                if approved_fingerprint
                else "none"
            ),
            "draft_fingerprint": draft_fingerprint,
            "approved_fingerprint": approved_fingerprint,
            "working_draft": working_draft,
        },
        "pending_import": copy.deepcopy(pending_import),
        "safe_changes": safe_changes,
        "issues": issues,
        "summary": {
            "issue_count": len(issues),
            "blocking_issue_count": len(blocking_issues),
            "unresolved_acknowledgement_count": unresolved_count,
            "safe_change_count": len(safe_changes),
            "working_draft": working_draft,
            "approved": approved_fingerprint is not None,
        },
        "approval": {
            "blocked": approval_blocked,
            "mode": "replacement" if approved_fingerprint else "initial",
            "draft_fingerprint": draft_fingerprint,
            "expected_approved_fingerprint": approved_fingerprint,
            "requires_unresolved_acknowledgement": unresolved_count > 0,
            "can_approve_resolved": can_approve_resolved,
            "can_approve_with_unresolved": can_approve_with_unresolved,
        },
        "rollback": {
            "available": rollback_revision is not None,
            "revision": copy.deepcopy(rollback_revision),
        },
        "revision_count": len(revisions),
    }
