from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from character_roster import (
    CharacterRosterSourceMismatchError,
    CharacterRosterValidationError,
    compute_draft_fingerprint,
    compute_roster_fingerprint,
    read_character_roster,
    save_character_roster,
    validate_character_roster,
)
from generation_state import atomic_json_write, fingerprint_value
from roster_mutation_lock import APPROVED_ROSTER_MUTATION_LOCK


_ROSTER_ACTION_LOCK = APPROVED_ROSTER_MUTATION_LOCK


class CharacterRosterActionError(RuntimeError):
    pass


class CharacterRosterConflictError(CharacterRosterActionError):
    pass


def utc_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _ordered_unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)

    return result


def _deduplicate_evidence(
    values: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result = []
    seen = set()

    for value in sorted(
        values,
        key=lambda item: (
            item["start_char"],
            item["end_char"],
            item["category"],
            item["source_quote"],
        ),
    ):
        key = fingerprint_value(value)
        if key in seen:
            continue
        seen.add(key)
        result.append(copy.deepcopy(value))

    return result


def _entry_map(draft: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["id"]: entry for entry in draft["entries"]}


def _require_entry(
    draft: dict[str, Any],
    entry_id: str | None,
) -> dict[str, Any]:
    if not isinstance(entry_id, str) or not entry_id:
        raise CharacterRosterActionError("entry_id is required.")

    entry = _entry_map(draft).get(entry_id)
    if entry is None:
        raise CharacterRosterActionError(
            f"Character roster entry {entry_id!r} was not found."
        )
    return entry


def _check_source(
    draft: dict[str, Any],
    source_fingerprint: str,
) -> None:
    if draft["source"]["fingerprint"] != source_fingerprint:
        raise CharacterRosterSourceMismatchError(
            "The character roster draft belongs to a different source."
        )


def _check_fingerprint(
    draft: dict[str, Any],
    expected_fingerprint: str,
) -> None:
    current = draft["draft_fingerprint"]
    if expected_fingerprint != current:
        raise CharacterRosterConflictError(
            "The character roster draft changed after this edit was "
            "loaded. Refresh and retry with the current draft."
        )


def _history_record(
    *,
    action: str,
    entries: list[dict[str, Any]],
    value: str | None,
    reason: str,
    at_utc: str,
    source_draft_fingerprint: str,
) -> dict[str, Any]:
    entry_ids = [entry["id"] for entry in entries]
    payload = {
        "action": action,
        "entry_ids": entry_ids,
        "value": value,
        "reason": reason,
        "at_utc": at_utc,
        "source_draft_fingerprint": source_draft_fingerprint,
        "before_entries": entries,
    }
    return {
        "operation_id": "review_" + fingerprint_value(payload)[:24],
        **copy.deepcopy(payload),
    }


def _synchronize_duplicate_links(draft: dict[str, Any]) -> None:
    ids = {entry["id"] for entry in draft["entries"]}
    candidates = []
    seen_pairs = set()

    for candidate in draft["duplicate_candidates"]:
        pair = [
            entry_id
            for entry_id in candidate["entry_ids"]
            if entry_id in ids
        ]
        if len(pair) != 2 or pair[0] == pair[1]:
            continue
        pair_key = tuple(sorted(pair))
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)
        candidates.append(
            {
                **copy.deepcopy(candidate),
                "entry_ids": pair,
            }
        )

    links: dict[str, list[str]] = {entry_id: [] for entry_id in ids}
    for candidate in candidates:
        first, second = candidate["entry_ids"]
        links[first].append(second)
        links[second].append(first)

    for entry in draft["entries"]:
        entry["possible_duplicate_ids"] = _ordered_unique(
            links[entry["id"]]
        )
        entry["mistaken_merge_risk"] = bool(
            entry["possible_duplicate_ids"]
        )
        if (
            entry["resolution_status"] == "duplicate_candidate"
            and not entry["possible_duplicate_ids"]
        ):
            entry["resolution_status"] = (
                "resolved"
                if entry["canonical_name"]
                else "unresolved"
            )

    draft["duplicate_candidates"] = candidates


def _rebuild_unresolved(draft: dict[str, Any]) -> None:
    unresolved = []

    for entry in draft["entries"]:
        if entry["resolution_status"] not in {"unresolved", "unnamed"}:
            continue
        questions = entry["unresolved_questions"] or [
            "This identity requires explicit review."
        ]
        for question in questions:
            unresolved.append(
                {
                    "entry_id": entry["id"],
                    "question": question,
                    "confidence": entry["confidence"],
                }
            )

    draft["unresolved"] = unresolved


def _finalize_draft(
    draft: dict[str, Any],
    *,
    source_text: str,
) -> dict[str, Any]:
    _synchronize_duplicate_links(draft)
    _rebuild_unresolved(draft)
    draft["draft_fingerprint"] = compute_draft_fingerprint(draft)
    return validate_character_roster(
        draft,
        source_text=source_text,
        expected_status="draft",
    )


def _merge_entries(
    draft: dict[str, Any],
    *,
    primary: dict[str, Any],
    secondary: dict[str, Any],
) -> None:
    primary_id = primary["id"]
    secondary_id = secondary["id"]

    aliases = [
        *primary["aliases"],
        secondary["canonical_name"],
        secondary["display_name"],
        *secondary["aliases"],
    ]
    aliases = [
        value
        for value in aliases
        if value
        and value not in {
            primary["canonical_name"],
            primary["display_name"],
        }
    ]

    primary["aliases"] = _ordered_unique(aliases)

    for field in (
        "titles",
        "nicknames",
        "pronouns",
        "species",
        "relationships",
        "unresolved_questions",
        "voice_clues",
        "sample_lines",
    ):
        primary[field] = _ordered_unique(
            [*primary[field], *secondary[field]]
        )

    primary["evidence"] = _deduplicate_evidence(
        [*primary["evidence"], *secondary["evidence"]]
    )
    locations = _ordered_unique(
        [item["source_location"] for item in primary["evidence"]]
    )
    primary["first_evidence_location"] = locations[0]
    primary["additional_evidence_locations"] = locations[1:]
    primary["confidence"] = max(
        primary["confidence"],
        secondary["confidence"],
    )

    if primary["entity_kind"] == "unknown":
        primary["entity_kind"] = secondary["entity_kind"]
    elif (
        secondary["entity_kind"] != "unknown"
        and secondary["entity_kind"] != primary["entity_kind"]
    ):
        draft["warnings"] = _ordered_unique(
            [
                *draft["warnings"],
                "Merge preserved conflicting entity kinds for "
                f"{primary_id} and {secondary_id}; kept "
                f"{primary['entity_kind']!r}.",
            ]
        )

    if primary["speaking_status"] == "uncertain":
        primary["speaking_status"] = secondary["speaking_status"]
    elif (
        secondary["speaking_status"] != "uncertain"
        and secondary["speaking_status"]
        != primary["speaking_status"]
    ):
        draft["warnings"] = _ordered_unique(
            [
                *draft["warnings"],
                "Merge preserved conflicting speaking statuses for "
                f"{primary_id} and {secondary_id}; kept "
                f"{primary['speaking_status']!r}.",
            ]
        )

    primary["resolution_status"] = (
        "resolved"
        if primary["canonical_name"]
        else "unresolved"
    )

    draft["entries"] = [
        entry
        for entry in draft["entries"]
        if entry["id"] != secondary_id
    ]

    for entry in draft["entries"]:
        entry["possible_duplicate_ids"] = _ordered_unique(
            [
                primary_id if value == secondary_id else value
                for value in entry["possible_duplicate_ids"]
                if value != entry["id"]
            ]
        )

    updated_candidates = []
    for candidate in draft["duplicate_candidates"]:
        pair = [
            primary_id if value == secondary_id else value
            for value in candidate["entry_ids"]
        ]
        if pair[0] == pair[1]:
            continue
        updated_candidates.append(
            {
                **copy.deepcopy(candidate),
                "entry_ids": pair,
            }
        )
    draft["duplicate_candidates"] = updated_candidates


def apply_character_roster_action(
    draft: dict[str, Any],
    *,
    expected_fingerprint: str,
    source_fingerprint: str,
    source_text: str,
    action: str,
    entry_id: str | None = None,
    other_entry_id: str | None = None,
    value: str | None = None,
    display_name: str | None = None,
    reason: str | None = None,
    preserve_old_as_alias: bool = True,
    at_utc: str | None = None,
) -> dict[str, Any]:
    normalized = validate_character_roster(
        draft,
        source_text=source_text,
        expected_status="draft",
    )
    _check_source(normalized, source_fingerprint)
    _check_fingerprint(normalized, expected_fingerprint)

    if action not in {
        "confirm",
        "rename",
        "add_alias",
        "reject_alias",
        "keep_separate",
        "merge",
        "mark_unresolved",
        "exclude",
    }:
        raise CharacterRosterActionError(
            f"Unsupported character roster action: {action!r}."
        )

    working = copy.deepcopy(normalized)
    working.pop("draft_fingerprint", None)
    primary = _require_entry(working, entry_id)
    secondary = None

    if action in {"keep_separate", "merge"}:
        secondary = _require_entry(working, other_entry_id)
        if secondary["id"] == primary["id"]:
            raise CharacterRosterActionError(
                "The two character roster entries must be different."
            )

    before_entries = [copy.deepcopy(primary)]
    if secondary is not None:
        before_entries.append(copy.deepcopy(secondary))

    reason_text = (reason or "").strip()
    value_text = value.strip() if isinstance(value, str) else None

    if action == "confirm":
        if not primary["canonical_name"]:
            raise CharacterRosterActionError(
                "An unnamed identity must be renamed before confirmation."
            )
        primary["resolution_status"] = "resolved"
        primary["unresolved_questions"] = []
        reason_text = reason_text or "Confirmed character identity."

    elif action == "rename":
        if not value_text:
            raise CharacterRosterActionError(
                "A non-empty canonical name is required."
            )
        old_names = [
            primary["canonical_name"],
            primary["display_name"],
        ]
        primary["canonical_name"] = value_text
        primary["display_name"] = (
            display_name.strip()
            if isinstance(display_name, str) and display_name.strip()
            else value_text
        )
        if preserve_old_as_alias:
            primary["aliases"] = _ordered_unique(
                [
                    *primary["aliases"],
                    *[
                        name
                        for name in old_names
                        if name
                        and name not in {
                            primary["canonical_name"],
                            primary["display_name"],
                        }
                    ],
                ]
            )
        reason_text = reason_text or "Renamed canonical identity."

    elif action == "add_alias":
        if not value_text:
            raise CharacterRosterActionError(
                "A non-empty alias is required."
            )
        primary["aliases"] = _ordered_unique(
            [*primary["aliases"], value_text]
        )
        reason_text = reason_text or "Accepted alias."

    elif action == "reject_alias":
        if not value_text:
            raise CharacterRosterActionError(
                "A non-empty rejected alias is required."
            )
        primary["aliases"] = [
            alias
            for alias in primary["aliases"]
            if alias.casefold() != value_text.casefold()
        ]
        reason_text = reason_text or "Rejected alias proposal."

    elif action == "mark_unresolved":
        question = reason_text or value_text
        if not question:
            raise CharacterRosterActionError(
                "An unresolved question or reason is required."
            )
        primary["resolution_status"] = "unresolved"
        primary["unresolved_questions"] = _ordered_unique(
            [*primary["unresolved_questions"], question]
        )
        reason_text = question

    elif action == "keep_separate":
        assert secondary is not None
        pair = {primary["id"], secondary["id"]}
        working["duplicate_candidates"] = [
            candidate
            for candidate in working["duplicate_candidates"]
            if set(candidate["entry_ids"]) != pair
        ]
        reason_text = reason_text or (
            "Confirmed that the proposed duplicate identities remain "
            "separate."
        )

    elif action == "merge":
        assert secondary is not None
        _merge_entries(
            working,
            primary=primary,
            secondary=secondary,
        )
        reason_text = reason_text or "Merged confirmed duplicate identities."

    elif action == "exclude":
        reason_text = reason_text or value_text or (
            "Excluded because this entity is not part of the canonical "
            "character roster."
        )
        working["entries"] = [
            entry
            for entry in working["entries"]
            if entry["id"] != primary["id"]
        ]
        working["excluded_entities"].append(
            {
                "name": (
                    primary["display_name"]
                    or primary["canonical_name"]
                    or primary["id"]
                ),
                "reason": reason_text,
                "evidence": copy.deepcopy(primary["evidence"]),
            }
        )
        working["duplicate_candidates"] = [
            candidate
            for candidate in working["duplicate_candidates"]
            if primary["id"] not in candidate["entry_ids"]
        ]

    timestamp = at_utc or utc_timestamp()
    history = _history_record(
        action=action,
        entries=before_entries,
        value=value_text,
        reason=reason_text,
        at_utc=timestamp,
        source_draft_fingerprint=expected_fingerprint,
    )
    working["review_history"] = [
        *working.get("review_history", []),
        history,
    ]
    return _finalize_draft(working, source_text=source_text)


def build_approved_roster(
    draft: dict[str, Any],
    *,
    expected_fingerprint: str,
    source_fingerprint: str,
    source_text: str,
    acknowledged_unresolved: bool,
    approved_at_utc: str | None = None,
) -> dict[str, Any]:
    normalized = validate_character_roster(
        draft,
        source_text=source_text,
        expected_status="draft",
    )
    _check_source(normalized, source_fingerprint)
    _check_fingerprint(normalized, expected_fingerprint)

    if normalized["duplicate_candidates"] or any(
        entry["resolution_status"] == "duplicate_candidate"
        for entry in normalized["entries"]
    ):
        raise CharacterRosterActionError(
            "Every duplicate candidate must be explicitly merged or kept "
            "separate before approval."
        )

    unresolved_count = sum(
        entry["resolution_status"] in {"unresolved", "unnamed"}
        for entry in normalized["entries"]
    )

    if unresolved_count and not acknowledged_unresolved:
        raise CharacterRosterActionError(
            "Unresolved identities require explicit acknowledgment before "
            "approval."
        )

    approved = {
        key: copy.deepcopy(normalized[key])
        for key in (
            "schema_version",
            "source",
            "discovery",
            "entries",
            "unresolved",
            "duplicate_candidates",
            "excluded_entities",
            "warnings",
        )
    }
    approved["review_history"] = copy.deepcopy(
        normalized.get("review_history", [])
    )
    approved.update(
        {
            "status": "approved",
            "approved_at_utc": approved_at_utc or utc_timestamp(),
            "approved_draft_fingerprint": expected_fingerprint,
            "approval_summary": {
                "resolved_count": sum(
                    entry["resolution_status"] == "resolved"
                    for entry in normalized["entries"]
                ),
                "unresolved_count": unresolved_count,
                "merged_count": sum(
                    item["action"] == "merge"
                    for item in normalized.get(
                        "review_history",
                        [],
                    )
                ),
                "excluded_count": len(normalized["excluded_entities"]),
                "acknowledged_unresolved": bool(
                    acknowledged_unresolved
                ),
            },
        }
    )
    approved["roster_fingerprint"] = compute_roster_fingerprint(approved)
    return validate_character_roster(
        approved,
        source_text=source_text,
        expected_status="approved",
    )


def mutate_character_roster_draft_file(
    *,
    draft_path: str | Path,
    source_text: str,
    source_fingerprint: str,
    expected_fingerprint: str,
    action: str,
    entry_id: str | None = None,
    other_entry_id: str | None = None,
    value: str | None = None,
    display_name: str | None = None,
    reason: str | None = None,
    preserve_old_as_alias: bool = True,
    at_utc: str | None = None,
) -> dict[str, Any]:
    with _ROSTER_ACTION_LOCK:
        draft = read_character_roster(
            draft_path,
            source_text=source_text,
            expected_status="draft",
        )
        updated = apply_character_roster_action(
            draft,
            expected_fingerprint=expected_fingerprint,
            source_fingerprint=source_fingerprint,
            source_text=source_text,
            action=action,
            entry_id=entry_id,
            other_entry_id=other_entry_id,
            value=value,
            display_name=display_name,
            reason=reason,
            preserve_old_as_alias=preserve_old_as_alias,
            at_utc=at_utc,
        )
        return save_character_roster(
            updated,
            draft_path,
            source_text=source_text,
            expected_status="draft",
        )


def _atomic_bytes_write(
    content: bytes,
    path: str | Path,
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _validated_revision_id(revision_id: str) -> str:
    value = str(revision_id or "").strip()
    if (
        not value.startswith("roster_")
        or len(value) != 31
        or not value.removeprefix("roster_").isalnum()
    ):
        raise CharacterRosterActionError(
            "The character-roster revision identifier is invalid."
        )
    return value


def _revision_manifest_path(
    history_root: str | Path,
    revision_id: str,
) -> Path:
    return (
        Path(history_root)
        / _validated_revision_id(revision_id)
        / "revision.json"
    )


def _read_revision_manifest(
    history_root: str | Path,
    revision_id: str,
) -> dict[str, Any]:
    target = _revision_manifest_path(history_root, revision_id)
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CharacterRosterActionError(
            "The requested character-roster revision was not found."
        ) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CharacterRosterValidationError(
            f"Character-roster revision metadata is invalid: {exc}"
        ) from exc
    if not isinstance(value, dict) or value.get("revision_id") != revision_id:
        raise CharacterRosterValidationError(
            "Character-roster revision metadata does not match its directory."
        )
    return value


def list_character_roster_revisions(
    history_root: str | Path,
) -> list[dict[str, Any]]:
    root = Path(history_root)
    if not root.exists():
        return []
    revisions: list[dict[str, Any]] = []
    for directory in root.iterdir():
        if not directory.is_dir():
            continue
        manifest = directory / "revision.json"
        try:
            value = json.loads(manifest.read_text(encoding="utf-8"))
        except (
            FileNotFoundError,
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            continue
        if not isinstance(value, dict):
            continue
        if value.get("revision_id") != directory.name:
            continue
        revisions.append(copy.deepcopy(value))
    revisions.sort(
        key=lambda item: str(item.get("created_at_utc") or ""),
        reverse=True,
    )
    return revisions


def replace_approved_character_roster_file(
    *,
    draft_path: str | Path,
    approved_path: str | Path,
    history_root: str | Path,
    source_text: str,
    source_fingerprint: str,
    expected_draft_fingerprint: str,
    expected_approved_fingerprint: str,
    acknowledged_unresolved: bool,
    approved_at_utc: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    with _ROSTER_ACTION_LOCK:
        target = Path(approved_path)
        current = read_character_roster(
            target,
            source_text=source_text,
            expected_status="approved",
        )
        if current["roster_fingerprint"] != expected_approved_fingerprint:
            raise CharacterRosterConflictError(
                "The approved character roster changed before replacement."
            )
        if current["source"]["fingerprint"] != source_fingerprint:
            raise CharacterRosterSourceMismatchError(
                "The approved character roster belongs to a different source."
            )
        draft = read_character_roster(
            draft_path,
            source_text=source_text,
            expected_status="draft",
        )
        if draft["draft_fingerprint"] == current["approved_draft_fingerprint"]:
            raise CharacterRosterConflictError(
                "The draft is the source of the current approved roster and "
                "does not contain a replacement revision."
            )
        timestamp = approved_at_utc or utc_timestamp()
        replacement = build_approved_roster(
            draft,
            expected_fingerprint=expected_draft_fingerprint,
            source_fingerprint=source_fingerprint,
            source_text=source_text,
            acknowledged_unresolved=acknowledged_unresolved,
            approved_at_utc=timestamp,
        )
        revision_id = "roster_" + fingerprint_value(
            {
                "created_at_utc": timestamp,
                "previous": current["roster_fingerprint"],
                "replacement": replacement["roster_fingerprint"],
                "draft": expected_draft_fingerprint,
            }
        )[:24]
        revision_dir = Path(history_root) / revision_id
        if revision_dir.exists():
            raise CharacterRosterConflictError(
                "This character-roster replacement revision already exists."
            )
        previous_bytes = target.read_bytes()
        replacement_draft_bytes = Path(draft_path).read_bytes()
        previous_path = revision_dir / "previous_character_roster.json"
        replacement_path = revision_dir / "replacement_character_roster.json"
        replacement_draft_path = (
            revision_dir / "replacement_character_roster.draft.json"
        )
        manifest_path = revision_dir / "revision.json"
        manifest = {
            "schema_version": 1,
            "revision_id": revision_id,
            "operation": "replace_approved_roster",
            "status": "available",
            "created_at_utc": timestamp,
            "restored_at_utc": None,
            "source_fingerprint": source_fingerprint,
            "draft_fingerprint": expected_draft_fingerprint,
            "previous_roster_fingerprint": current["roster_fingerprint"],
            "replacement_roster_fingerprint": replacement["roster_fingerprint"],
            "previous_path": previous_path.name,
            "replacement_path": replacement_path.name,
            "replacement_draft_path": replacement_draft_path.name,
        }
        try:
            _atomic_bytes_write(previous_bytes, previous_path)
            _atomic_bytes_write(
                replacement_draft_bytes,
                replacement_draft_path,
            )
            atomic_json_write(manifest, manifest_path)
            saved = save_character_roster(
                replacement,
                target,
                source_text=source_text,
                expected_status="approved",
            )
            verified = read_character_roster(
                target,
                source_text=source_text,
                expected_status="approved",
            )
            if saved != verified:
                raise CharacterRosterValidationError(
                    "Replacement roster verification did not match the saved artifact."
                )
            _atomic_bytes_write(target.read_bytes(), replacement_path)
        except Exception:
            _atomic_bytes_write(previous_bytes, target)
            raise
        return verified, copy.deepcopy(manifest)


def rollback_approved_character_roster_file(
    *,
    draft_path: str | Path,
    approved_path: str | Path,
    history_root: str | Path,
    revision_id: str,
    source_text: str,
    source_fingerprint: str,
    expected_current_fingerprint: str,
    restored_at_utc: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    with _ROSTER_ACTION_LOCK:
        target = Path(approved_path)
        current = read_character_roster(
            target,
            source_text=source_text,
            expected_status="approved",
        )
        if current["roster_fingerprint"] != expected_current_fingerprint:
            raise CharacterRosterConflictError(
                "The approved character roster changed before rollback."
            )
        manifest = _read_revision_manifest(history_root, revision_id)
        if manifest.get("status") != "available":
            raise CharacterRosterConflictError(
                "This character-roster revision has already been restored."
            )
        if manifest.get("source_fingerprint") != source_fingerprint:
            raise CharacterRosterSourceMismatchError(
                "The saved character-roster revision belongs to a different source."
            )
        if (
            manifest.get("replacement_roster_fingerprint")
            != current["roster_fingerprint"]
        ):
            raise CharacterRosterConflictError(
                "The saved revision is not the current approved roster version."
            )
        revision_id = _validated_revision_id(revision_id)
        revision_dir = Path(history_root) / revision_id
        expected_previous_name = "previous_character_roster.json"
        expected_replacement_name = "replacement_character_roster.json"
        expected_draft_name = "replacement_character_roster.draft.json"
        if (
            manifest.get("previous_path") != expected_previous_name
            or manifest.get("replacement_path") != expected_replacement_name
            or manifest.get("replacement_draft_path") != expected_draft_name
        ):
            raise CharacterRosterValidationError(
                "Character-roster revision file references are invalid."
            )
        previous_path = revision_dir / expected_previous_name
        previous_bytes = previous_path.read_bytes()
        previous = read_character_roster(
            previous_path,
            source_text=source_text,
            expected_status="approved",
        )
        if (
            previous["roster_fingerprint"]
            != manifest.get("previous_roster_fingerprint")
        ):
            raise CharacterRosterValidationError(
                "The saved previous roster does not match its revision metadata."
            )
        active_draft_path = Path(draft_path)
        active_draft_bytes = (
            active_draft_path.read_bytes()
            if active_draft_path.exists()
            else None
        )
        if active_draft_bytes is not None:
            active_draft = read_character_roster(
                active_draft_path,
                source_text=source_text,
                expected_status="draft",
            )
            if active_draft["draft_fingerprint"] != manifest.get(
                "draft_fingerprint"
            ):
                raise CharacterRosterConflictError(
                    "The reviewed replacement draft changed before rollback."
                )
        current_bytes = target.read_bytes()
        restored_manifest = {
            **copy.deepcopy(manifest),
            "status": "restored",
            "restored_at_utc": restored_at_utc or utc_timestamp(),
        }
        try:
            _atomic_bytes_write(previous_bytes, target)
            verified = read_character_roster(
                target,
                source_text=source_text,
                expected_status="approved",
            )
            if verified["roster_fingerprint"] != previous["roster_fingerprint"]:
                raise CharacterRosterValidationError(
                    "Rolled-back roster verification failed."
                )
            if active_draft_bytes is not None:
                active_draft_path.unlink()
            atomic_json_write(
                restored_manifest,
                _revision_manifest_path(history_root, revision_id),
            )
        except Exception:
            _atomic_bytes_write(current_bytes, target)
            if active_draft_bytes is not None:
                _atomic_bytes_write(active_draft_bytes, active_draft_path)
            raise
        return verified, restored_manifest


def approve_character_roster_file(
    *,
    draft_path: str | Path,
    approved_path: str | Path,
    source_text: str,
    source_fingerprint: str,
    expected_fingerprint: str,
    acknowledged_unresolved: bool,
    approved_at_utc: str | None = None,
) -> dict[str, Any]:
    with _ROSTER_ACTION_LOCK:
        target = Path(approved_path)
        if target.exists():
            raise CharacterRosterConflictError(
                "An approved character roster already exists."
            )
        draft = read_character_roster(
            draft_path,
            source_text=source_text,
            expected_status="draft",
        )
        approved = build_approved_roster(
            draft,
            expected_fingerprint=expected_fingerprint,
            source_fingerprint=source_fingerprint,
            source_text=source_text,
            acknowledged_unresolved=acknowledged_unresolved,
            approved_at_utc=approved_at_utc,
        )
        saved = save_character_roster(
            approved,
            target,
            source_text=source_text,
            expected_status="approved",
        )
        verified = read_character_roster(
            target,
            source_text=source_text,
            expected_status="approved",
        )
        if saved != verified:
            raise CharacterRosterValidationError(
                "Approved roster verification did not match the saved "
                "artifact."
            )
        return verified
