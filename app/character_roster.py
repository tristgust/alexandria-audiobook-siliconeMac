from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

from generation_state import (
    atomic_json_write,
    fingerprint_text,
    fingerprint_value,
)


SCHEMA_VERSION = 1

ENTITY_KINDS = frozenset(
    {
        "character",
        "group",
        "creature",
        "narrator_role",
        "named_non_speaker",
        "unknown",
    }
)
SPEAKING_STATUSES = frozenset(
    {
        "speaker",
        "non_speaker",
        "uncertain",
        "narrator",
    }
)
RESOLUTION_STATUSES = frozenset(
    {
        "resolved",
        "unresolved",
        "unnamed",
        "duplicate_candidate",
        "excluded",
    }
)
EVIDENCE_CATEGORIES = frozenset(
    {
        "name",
        "alias",
        "title",
        "nickname",
        "pronoun",
        "species",
        "relationship",
        "speaking",
        "voice",
        "visual",
        "other",
    }
)
EVIDENCE_BASES = frozenset(
    {
        "explicit",
        "inferred",
    }
)

_BASE_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "source",
        "discovery",
        "entries",
        "unresolved",
        "duplicate_candidates",
        "excluded_entities",
        "warnings",
    }
)
_DRAFT_TOP_LEVEL_KEYS = (
    _BASE_TOP_LEVEL_KEYS
    | {
        "status",
        "draft_fingerprint",
    }
)
_APPROVED_TOP_LEVEL_KEYS = (
    _BASE_TOP_LEVEL_KEYS
    | {
        "status",
        "approved_at_utc",
        "approved_draft_fingerprint",
        "roster_fingerprint",
        "approval_summary",
    }
)
_SOURCE_KEYS = frozenset(
    {
        "path",
        "basename",
        "fingerprint",
        "character_count",
    }
)
_DISCOVERY_KEYS = frozenset(
    {
        "created_at_utc",
        "model_name",
        "backend",
        "generation_fingerprint",
        "batch_count",
        "completed_batches",
    }
)
_ENTRY_KEYS = frozenset(
    {
        "id",
        "canonical_name",
        "display_name",
        "entity_kind",
        "speaking_status",
        "titles",
        "aliases",
        "nicknames",
        "pronouns",
        "species",
        "relationships",
        "first_evidence_location",
        "additional_evidence_locations",
        "confidence",
        "resolution_status",
        "possible_duplicate_ids",
        "mistaken_merge_risk",
        "unresolved_questions",
        "evidence",
        "voice_clues",
        "sample_lines",
    }
)
_EVIDENCE_KEYS = frozenset(
    {
        "source_quote",
        "source_location",
        "start_char",
        "end_char",
        "passage_index",
        "entry_index",
        "batch_index",
        "category",
        "confidence",
        "basis",
    }
)
_UNRESOLVED_KEYS = frozenset(
    {
        "entry_id",
        "question",
        "confidence",
    }
)
_DUPLICATE_KEYS = frozenset(
    {
        "entry_ids",
        "reason",
        "confidence",
        "evidence",
    }
)
_EXCLUDED_KEYS = frozenset(
    {
        "name",
        "reason",
        "evidence",
    }
)
_APPROVAL_SUMMARY_KEYS = frozenset(
    {
        "resolved_count",
        "unresolved_count",
        "merged_count",
        "excluded_count",
        "acknowledged_unresolved",
    }
)


class CharacterRosterError(RuntimeError):
    pass


class CharacterRosterCorruptError(CharacterRosterError):
    pass


class CharacterRosterValidationError(CharacterRosterError):
    pass


class CharacterRosterSourceMismatchError(CharacterRosterError):
    pass


def stable_entry_id(identity_seed: str) -> str:
    """Return an opaque stable ID from an immutable identity seed.

    Callers must seed this with source identity and evidence location,
    not merely the current display or canonical name. That keeps the ID
    stable through rename while allowing distinct same-name identities.
    """
    if not isinstance(identity_seed, str) or not identity_seed.strip():
        raise CharacterRosterValidationError(
            "Character identity seed must be non-empty text."
        )

    normalized = " ".join(
        identity_seed.casefold().split()
    )
    digest = hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()[:20]
    return f"character_{digest}"


def build_source_snapshot(
    source_path: str | Path,
    *,
    normalizer: Callable[[str], str] | None = None,
) -> tuple[dict[str, Any], str]:
    target = Path(source_path).expanduser().resolve()

    if not target.exists():
        raise FileNotFoundError(
            f"Source file not found: {target}"
        )

    text = target.read_text(encoding="utf-8")

    if normalizer is not None:
        text = normalizer(text)

    return (
        {
            "path": str(target),
            "basename": target.name,
            "fingerprint": fingerprint_text(text),
            "character_count": len(text),
        },
        text,
    )


def _require_dict(
    value: Any,
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CharacterRosterValidationError(
            f"{label} must be a JSON object."
        )
    return value


def _require_list(
    value: Any,
    label: str,
) -> list[Any]:
    if not isinstance(value, list):
        raise CharacterRosterValidationError(
            f"{label} must be a JSON array."
        )
    return value


def _require_exact_keys(
    value: dict[str, Any],
    expected: frozenset[str] | set[str],
    label: str,
) -> None:
    actual = set(value)
    missing = sorted(set(expected) - actual)
    extra = sorted(actual - set(expected))

    if missing or extra:
        details = []
        if missing:
            details.append(
                "missing " + ", ".join(missing)
            )
        if extra:
            details.append(
                "unexpected " + ", ".join(extra)
            )
        raise CharacterRosterValidationError(
            f"{label} has " + "; ".join(details) + "."
        )


def _require_text(
    value: Any,
    label: str,
    *,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise CharacterRosterValidationError(
            f"{label} must be text."
        )

    normalized = value.strip()

    if not allow_empty and not normalized:
        raise CharacterRosterValidationError(
            f"{label} must not be empty."
        )

    return normalized


def _require_exact_text(
    value: Any,
    label: str,
) -> str:
    if not isinstance(value, str):
        raise CharacterRosterValidationError(
            f"{label} must be text."
        )

    if not value:
        raise CharacterRosterValidationError(
            f"{label} must not be empty."
        )

    return value


def _require_entry_id(
    value: Any,
    label: str,
) -> str:
    entry_id = _require_text(value, label)

    if not re.fullmatch(r"character_[0-9a-f]{20}", entry_id):
        raise CharacterRosterValidationError(
            f"{label} must be an opaque character ID."
        )

    return entry_id


def _require_int(
    value: Any,
    label: str,
    *,
    minimum: int = 0,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
    ):
        raise CharacterRosterValidationError(
            f"{label} must be an integer >= {minimum}."
        )
    return value


def _require_optional_int(
    value: Any,
    label: str,
) -> int | None:
    if value is None:
        return None
    return _require_int(value, label)


def _require_confidence(
    value: Any,
    label: str,
) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
    ):
        raise CharacterRosterValidationError(
            f"{label} must be numeric."
        )

    normalized = float(value)

    if not 0.0 <= normalized <= 1.0:
        raise CharacterRosterValidationError(
            f"{label} must be between 0.0 and 1.0."
        )

    return normalized


def _require_string_list(
    value: Any,
    label: str,
) -> list[str]:
    result = []

    for index, item in enumerate(
        _require_list(value, label)
    ):
        result.append(
            _require_text(
                item,
                f"{label}[{index}]",
            )
        )

    return result


def _require_exact_string_list(
    value: Any,
    label: str,
) -> list[str]:
    return [
        _require_exact_text(
            item,
            f"{label}[{index}]",
        )
        for index, item in enumerate(
            _require_list(value, label)
        )
    ]


def _validate_source(
    value: Any,
) -> dict[str, Any]:
    source = _require_dict(value, "Roster source")
    _require_exact_keys(
        source,
        _SOURCE_KEYS,
        "Roster source",
    )

    return {
        "path": _require_text(
            source["path"],
            "Roster source.path",
        ),
        "basename": _require_text(
            source["basename"],
            "Roster source.basename",
        ),
        "fingerprint": _require_text(
            source["fingerprint"],
            "Roster source.fingerprint",
        ),
        "character_count": _require_int(
            source["character_count"],
            "Roster source.character_count",
        ),
    }


def _validate_discovery(
    value: Any,
) -> dict[str, Any]:
    discovery = _require_dict(
        value,
        "Roster discovery",
    )
    _require_exact_keys(
        discovery,
        _DISCOVERY_KEYS,
        "Roster discovery",
    )

    batch_count = _require_int(
        discovery["batch_count"],
        "Roster discovery.batch_count",
    )
    completed_batches = _require_int(
        discovery["completed_batches"],
        "Roster discovery.completed_batches",
    )

    if completed_batches > batch_count:
        raise CharacterRosterValidationError(
            "Roster discovery.completed_batches cannot exceed "
            "batch_count."
        )

    return {
        "created_at_utc": _require_text(
            discovery["created_at_utc"],
            "Roster discovery.created_at_utc",
        ),
        "model_name": _require_text(
            discovery["model_name"],
            "Roster discovery.model_name",
        ),
        "backend": _require_text(
            discovery["backend"],
            "Roster discovery.backend",
        ),
        "generation_fingerprint": _require_text(
            discovery["generation_fingerprint"],
            "Roster discovery.generation_fingerprint",
        ),
        "batch_count": batch_count,
        "completed_batches": completed_batches,
    }


def _validate_evidence(
    value: Any,
    *,
    label: str,
    source_text: str | None,
) -> dict[str, Any]:
    evidence = _require_dict(value, label)
    _require_exact_keys(
        evidence,
        _EVIDENCE_KEYS,
        label,
    )

    quote = _require_exact_text(
        evidence["source_quote"],
        f"{label}.source_quote",
    )
    start = _require_int(
        evidence["start_char"],
        f"{label}.start_char",
    )
    end = _require_int(
        evidence["end_char"],
        f"{label}.end_char",
    )

    if end <= start:
        raise CharacterRosterValidationError(
            f"{label}.end_char must be greater than start_char."
        )

    passage_index = _require_optional_int(
        evidence["passage_index"],
        f"{label}.passage_index",
    )
    entry_index = _require_optional_int(
        evidence["entry_index"],
        f"{label}.entry_index",
    )

    if passage_index is None and entry_index is None:
        raise CharacterRosterValidationError(
            f"{label} requires passage_index or entry_index."
        )

    category = _require_text(
        evidence["category"],
        f"{label}.category",
    )

    if category not in EVIDENCE_CATEGORIES:
        raise CharacterRosterValidationError(
            f"{label}.category is unsupported: {category!r}."
        )

    basis = _require_text(
        evidence["basis"],
        f"{label}.basis",
    )

    if basis not in EVIDENCE_BASES:
        raise CharacterRosterValidationError(
            f"{label}.basis is unsupported: {basis!r}."
        )

    if source_text is not None:
        if end > len(source_text):
            raise CharacterRosterValidationError(
                f"{label} extends beyond the selected source."
            )

        if source_text[start:end] != quote:
            raise CharacterRosterValidationError(
                f"{label} quote does not match the selected source "
                "at its stored offsets."
            )

    return {
        "source_quote": quote,
        "source_location": _require_text(
            evidence["source_location"],
            f"{label}.source_location",
        ),
        "start_char": start,
        "end_char": end,
        "passage_index": passage_index,
        "entry_index": entry_index,
        "batch_index": _require_int(
            evidence["batch_index"],
            f"{label}.batch_index",
        ),
        "category": category,
        "confidence": _require_confidence(
            evidence["confidence"],
            f"{label}.confidence",
        ),
        "basis": basis,
    }


def _validate_evidence_list(
    value: Any,
    *,
    label: str,
    source_text: str | None,
) -> list[dict[str, Any]]:
    return [
        _validate_evidence(
            item,
            label=f"{label}[{index}]",
            source_text=source_text,
        )
        for index, item in enumerate(
            _require_list(value, label)
        )
    ]


def _validate_entry(
    value: Any,
    *,
    index: int,
    source_text: str | None,
) -> dict[str, Any]:
    label = f"Roster entry {index}"
    entry = _require_dict(value, label)
    _require_exact_keys(entry, _ENTRY_KEYS, label)

    resolution_status = _require_text(
        entry["resolution_status"],
        f"{label}.resolution_status",
    )

    if resolution_status not in RESOLUTION_STATUSES:
        raise CharacterRosterValidationError(
            f"{label}.resolution_status is unsupported: "
            f"{resolution_status!r}."
        )

    canonical_name = _require_text(
        entry["canonical_name"],
        f"{label}.canonical_name",
        allow_empty=(
            resolution_status
            in {"unresolved", "unnamed"}
        ),
    )
    entity_kind = _require_text(
        entry["entity_kind"],
        f"{label}.entity_kind",
    )
    speaking_status = _require_text(
        entry["speaking_status"],
        f"{label}.speaking_status",
    )

    if entity_kind not in ENTITY_KINDS:
        raise CharacterRosterValidationError(
            f"{label}.entity_kind is unsupported: {entity_kind!r}."
        )

    if speaking_status not in SPEAKING_STATUSES:
        raise CharacterRosterValidationError(
            f"{label}.speaking_status is unsupported: "
            f"{speaking_status!r}."
        )

    evidence = _validate_evidence_list(
        entry["evidence"],
        label=f"{label}.evidence",
        source_text=source_text,
    )

    if not evidence:
        raise CharacterRosterValidationError(
            f"{label}.evidence must contain at least one item."
        )

    return {
        "id": _require_entry_id(
            entry["id"],
            f"{label}.id",
        ),
        "canonical_name": canonical_name,
        "display_name": _require_text(
            entry["display_name"],
            f"{label}.display_name",
        ),
        "entity_kind": entity_kind,
        "speaking_status": speaking_status,
        "titles": _require_string_list(
            entry["titles"],
            f"{label}.titles",
        ),
        "aliases": _require_string_list(
            entry["aliases"],
            f"{label}.aliases",
        ),
        "nicknames": _require_string_list(
            entry["nicknames"],
            f"{label}.nicknames",
        ),
        "pronouns": _require_string_list(
            entry["pronouns"],
            f"{label}.pronouns",
        ),
        "species": _require_string_list(
            entry["species"],
            f"{label}.species",
        ),
        "relationships": _require_string_list(
            entry["relationships"],
            f"{label}.relationships",
        ),
        "first_evidence_location": _require_text(
            entry["first_evidence_location"],
            f"{label}.first_evidence_location",
        ),
        "additional_evidence_locations": (
            _require_string_list(
                entry["additional_evidence_locations"],
                f"{label}.additional_evidence_locations",
            )
        ),
        "confidence": _require_confidence(
            entry["confidence"],
            f"{label}.confidence",
        ),
        "resolution_status": resolution_status,
        "possible_duplicate_ids": _require_string_list(
            entry["possible_duplicate_ids"],
            f"{label}.possible_duplicate_ids",
        ),
        "mistaken_merge_risk": entry["mistaken_merge_risk"],
        "unresolved_questions": _require_string_list(
            entry["unresolved_questions"],
            f"{label}.unresolved_questions",
        ),
        "evidence": evidence,
        "voice_clues": _require_string_list(
            entry["voice_clues"],
            f"{label}.voice_clues",
        ),
        "sample_lines": _require_exact_string_list(
            entry["sample_lines"],
            f"{label}.sample_lines",
        ),
    }


def _validate_unresolved(
    value: Any,
    *,
    index: int,
) -> dict[str, Any]:
    label = f"Unresolved identity {index}"
    item = _require_dict(value, label)
    _require_exact_keys(item, _UNRESOLVED_KEYS, label)

    return {
        "entry_id": _require_text(
            item["entry_id"],
            f"{label}.entry_id",
        ),
        "question": _require_text(
            item["question"],
            f"{label}.question",
        ),
        "confidence": _require_confidence(
            item["confidence"],
            f"{label}.confidence",
        ),
    }


def _validate_duplicate_candidate(
    value: Any,
    *,
    index: int,
    source_text: str | None,
) -> dict[str, Any]:
    label = f"Duplicate candidate {index}"
    item = _require_dict(value, label)
    _require_exact_keys(item, _DUPLICATE_KEYS, label)
    entry_ids = _require_string_list(
        item["entry_ids"],
        f"{label}.entry_ids",
    )

    if len(entry_ids) != 2 or entry_ids[0] == entry_ids[1]:
        raise CharacterRosterValidationError(
            f"{label}.entry_ids must contain two distinct IDs."
        )

    evidence = _validate_evidence_list(
        item["evidence"],
        label=f"{label}.evidence",
        source_text=source_text,
    )

    if not evidence:
        raise CharacterRosterValidationError(
            f"{label}.evidence must contain at least one item."
        )

    return {
        "entry_ids": entry_ids,
        "reason": _require_text(
            item["reason"],
            f"{label}.reason",
        ),
        "confidence": _require_confidence(
            item["confidence"],
            f"{label}.confidence",
        ),
        "evidence": evidence,
    }


def _validate_excluded_entity(
    value: Any,
    *,
    index: int,
    source_text: str | None,
) -> dict[str, Any]:
    label = f"Excluded entity {index}"
    item = _require_dict(value, label)
    _require_exact_keys(item, _EXCLUDED_KEYS, label)

    evidence = _validate_evidence_list(
        item["evidence"],
        label=f"{label}.evidence",
        source_text=source_text,
    )

    if not evidence:
        raise CharacterRosterValidationError(
            f"{label}.evidence must contain at least one item."
        )

    return {
        "name": _require_text(
            item["name"],
            f"{label}.name",
        ),
        "reason": _require_text(
            item["reason"],
            f"{label}.reason",
        ),
        "evidence": evidence,
    }


def _base_fingerprint_payload(
    value: dict[str, Any],
) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value[key])
        for key in sorted(_BASE_TOP_LEVEL_KEYS)
    }


def compute_draft_fingerprint(
    value: dict[str, Any],
) -> str:
    return fingerprint_value(
        _base_fingerprint_payload(value)
    )


def compute_roster_fingerprint(
    value: dict[str, Any],
) -> str:
    payload = {
        **_base_fingerprint_payload(value),
        "status": "approved",
        "approved_at_utc": value["approved_at_utc"],
        "approved_draft_fingerprint": (
            value["approved_draft_fingerprint"]
        ),
        "approval_summary": copy.deepcopy(
            value["approval_summary"]
        ),
    }
    return fingerprint_value(payload)


def _validate_approval_summary(
    value: Any,
) -> dict[str, Any]:
    summary = _require_dict(
        value,
        "Roster approval_summary",
    )
    _require_exact_keys(
        summary,
        _APPROVAL_SUMMARY_KEYS,
        "Roster approval_summary",
    )

    acknowledged = summary[
        "acknowledged_unresolved"
    ]

    if not isinstance(acknowledged, bool):
        raise CharacterRosterValidationError(
            "Roster approval_summary.acknowledged_unresolved "
            "must be boolean."
        )

    return {
        "resolved_count": _require_int(
            summary["resolved_count"],
            "Roster approval_summary.resolved_count",
        ),
        "unresolved_count": _require_int(
            summary["unresolved_count"],
            "Roster approval_summary.unresolved_count",
        ),
        "merged_count": _require_int(
            summary["merged_count"],
            "Roster approval_summary.merged_count",
        ),
        "excluded_count": _require_int(
            summary["excluded_count"],
            "Roster approval_summary.excluded_count",
        ),
        "acknowledged_unresolved": acknowledged,
    }


def validate_character_roster(
    value: Any,
    *,
    source_text: str | None = None,
    expected_status: str | None = None,
) -> dict[str, Any]:
    roster = _require_dict(value, "Character roster")
    status = _require_text(
        roster.get("status"),
        "Character roster.status",
    )

    if status not in {"draft", "approved"}:
        raise CharacterRosterValidationError(
            "Character roster.status must be 'draft' or 'approved'."
        )

    if expected_status is not None and status != expected_status:
        raise CharacterRosterValidationError(
            f"Expected {expected_status!r} roster, found {status!r}."
        )

    expected_keys = (
        _DRAFT_TOP_LEVEL_KEYS
        if status == "draft"
        else _APPROVED_TOP_LEVEL_KEYS
    )
    _require_exact_keys(
        roster,
        expected_keys,
        "Character roster",
    )

    if roster["schema_version"] != SCHEMA_VERSION:
        raise CharacterRosterValidationError(
            "Unsupported character roster schema version."
        )

    normalized: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "source": _validate_source(roster["source"]),
        "discovery": _validate_discovery(
            roster["discovery"]
        ),
    }

    entries = [
        _validate_entry(
            item,
            index=index,
            source_text=source_text,
        )
        for index, item in enumerate(
            _require_list(
                roster["entries"],
                "Character roster.entries",
            )
        )
    ]
    entry_ids = [entry["id"] for entry in entries]

    if len(entry_ids) != len(set(entry_ids)):
        raise CharacterRosterValidationError(
            "Character roster entry IDs must be unique."
        )

    entry_id_set = set(entry_ids)

    for entry in entries:
        unknown_duplicates = set(
            entry["possible_duplicate_ids"]
        ) - entry_id_set
        if unknown_duplicates:
            raise CharacterRosterValidationError(
                f"Roster entry {entry['id']!r} references unknown "
                f"duplicate IDs: {sorted(unknown_duplicates)}."
            )
        if entry["id"] in entry["possible_duplicate_ids"]:
            raise CharacterRosterValidationError(
                f"Roster entry {entry['id']!r} cannot duplicate itself."
            )
        if not isinstance(entry["mistaken_merge_risk"], bool):
            raise CharacterRosterValidationError(
                f"Roster entry {entry['id']!r} mistaken_merge_risk "
                "must be boolean."
            )

    normalized["entries"] = entries
    normalized["unresolved"] = [
        _validate_unresolved(item, index=index)
        for index, item in enumerate(
            _require_list(
                roster["unresolved"],
                "Character roster.unresolved",
            )
        )
    ]

    for item in normalized["unresolved"]:
        if item["entry_id"] not in entry_id_set:
            raise CharacterRosterValidationError(
                "Unresolved identity references unknown entry ID "
                f"{item['entry_id']!r}."
            )

    normalized["duplicate_candidates"] = [
        _validate_duplicate_candidate(
            item,
            index=index,
            source_text=source_text,
        )
        for index, item in enumerate(
            _require_list(
                roster["duplicate_candidates"],
                "Character roster.duplicate_candidates",
            )
        )
    ]

    for candidate in normalized["duplicate_candidates"]:
        unknown = set(candidate["entry_ids"]) - entry_id_set
        if unknown:
            raise CharacterRosterValidationError(
                "Duplicate candidate references unknown entry IDs: "
                f"{sorted(unknown)}."
            )

    normalized["excluded_entities"] = [
        _validate_excluded_entity(
            item,
            index=index,
            source_text=source_text,
        )
        for index, item in enumerate(
            _require_list(
                roster["excluded_entities"],
                "Character roster.excluded_entities",
            )
        )
    ]
    normalized["warnings"] = _require_string_list(
        roster["warnings"],
        "Character roster.warnings",
    )

    expected_draft_fingerprint = (
        compute_draft_fingerprint(normalized)
    )

    if status == "draft":
        saved_fingerprint = _require_text(
            roster["draft_fingerprint"],
            "Character roster.draft_fingerprint",
        )
        if saved_fingerprint != expected_draft_fingerprint:
            raise CharacterRosterValidationError(
                "Character roster draft fingerprint does not match "
                "its contents."
            )
        normalized["draft_fingerprint"] = saved_fingerprint
        return normalized

    approved_draft = _require_text(
        roster["approved_draft_fingerprint"],
        "Character roster.approved_draft_fingerprint",
    )

    if approved_draft != expected_draft_fingerprint:
        raise CharacterRosterValidationError(
            "Approved roster does not match its approved draft "
            "fingerprint."
        )

    normalized["approved_at_utc"] = _require_text(
        roster["approved_at_utc"],
        "Character roster.approved_at_utc",
    )
    normalized["approved_draft_fingerprint"] = approved_draft
    normalized["approval_summary"] = (
        _validate_approval_summary(
            roster["approval_summary"]
        )
    )
    actual_resolved = sum(
        entry["resolution_status"] == "resolved"
        for entry in normalized["entries"]
    )
    actual_unresolved = sum(
        entry["resolution_status"]
        in {"unresolved", "unnamed"}
        for entry in normalized["entries"]
    )
    actual_excluded = len(
        normalized["excluded_entities"]
    )
    summary = normalized["approval_summary"]

    if summary["resolved_count"] != actual_resolved:
        raise CharacterRosterValidationError(
            "Roster approval_summary.resolved_count does not "
            "match the approved entries."
        )

    if summary["unresolved_count"] != actual_unresolved:
        raise CharacterRosterValidationError(
            "Roster approval_summary.unresolved_count does not "
            "match the approved entries."
        )

    if summary["excluded_count"] != actual_excluded:
        raise CharacterRosterValidationError(
            "Roster approval_summary.excluded_count does not "
            "match excluded_entities."
        )

    if (
        actual_unresolved > 0
        and not summary["acknowledged_unresolved"]
    ):
        raise CharacterRosterValidationError(
            "Approved rosters with unresolved identities require "
            "acknowledged_unresolved=true."
        )

    saved_roster_fingerprint = _require_text(
        roster["roster_fingerprint"],
        "Character roster.roster_fingerprint",
    )
    expected_roster_fingerprint = (
        compute_roster_fingerprint(normalized)
    )

    if saved_roster_fingerprint != expected_roster_fingerprint:
        raise CharacterRosterValidationError(
            "Approved roster fingerprint does not match its contents."
        )

    normalized["roster_fingerprint"] = (
        saved_roster_fingerprint
    )
    return normalized


def build_draft_roster(
    *,
    source: dict[str, Any],
    discovery: dict[str, Any],
    entries: list[dict[str, Any]],
    unresolved: list[dict[str, Any]] | None = None,
    duplicate_candidates: list[dict[str, Any]] | None = None,
    excluded_entities: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    source_text: str,
) -> dict[str, Any]:
    draft: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "draft",
        "source": copy.deepcopy(source),
        "discovery": copy.deepcopy(discovery),
        "entries": copy.deepcopy(entries),
        "unresolved": copy.deepcopy(unresolved or []),
        "duplicate_candidates": copy.deepcopy(
            duplicate_candidates or []
        ),
        "excluded_entities": copy.deepcopy(
            excluded_entities or []
        ),
        "warnings": list(warnings or []),
    }
    draft["draft_fingerprint"] = (
        compute_draft_fingerprint(draft)
    )
    return validate_character_roster(
        draft,
        source_text=source_text,
        expected_status="draft",
    )


def save_character_roster(
    value: dict[str, Any],
    path: str | Path,
    *,
    source_text: str,
    expected_status: str | None = None,
) -> dict[str, Any]:
    normalized = validate_character_roster(
        value,
        source_text=source_text,
        expected_status=expected_status,
    )
    atomic_json_write(normalized, path)
    return normalized


def read_character_roster(
    path: str | Path,
    *,
    source_text: str | None = None,
    expected_status: str | None = None,
) -> dict[str, Any]:
    target = Path(path)

    if not target.exists():
        raise FileNotFoundError(str(target))

    try:
        value = json.loads(
            target.read_text(encoding="utf-8")
        )
    except (
        OSError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        raise CharacterRosterCorruptError(
            f"Character roster could not be read: {exc}"
        ) from exc

    return validate_character_roster(
        value,
        source_text=source_text,
        expected_status=expected_status,
    )


def _roster_counts(
    roster: dict[str, Any],
) -> dict[str, int]:
    entries = roster["entries"]
    return {
        "entries": len(entries),
        "resolved": sum(
            entry["resolution_status"] == "resolved"
            for entry in entries
        ),
        "unresolved": sum(
            entry["resolution_status"] == "unresolved"
            for entry in entries
        ),
        "unnamed": sum(
            entry["resolution_status"] == "unnamed"
            for entry in entries
        ),
        "duplicate_candidates": len(
            roster["duplicate_candidates"]
        ),
        "excluded": len(roster["excluded_entities"]),
        "speakers": sum(
            entry["speaking_status"]
            in {"speaker", "narrator"}
            for entry in entries
        ),
        "named_non_speakers": sum(
            entry["entity_kind"] == "named_non_speaker"
            for entry in entries
        ),
    }


def inspect_character_roster_file(
    path: str | Path,
    *,
    expected_status: str,
    current_source: dict[str, Any] | None,
    current_source_text: str | None,
) -> dict[str, Any]:
    target = Path(path)

    if not target.exists():
        return {
            "exists": False,
            "status": "missing",
            "compatible_source": None,
            "artifact_status": expected_status,
            "fingerprint": None,
            "counts": None,
            "warnings": [],
            "error": None,
        }

    try:
        roster = read_character_roster(
            target,
            expected_status=expected_status,
        )
    except CharacterRosterCorruptError as exc:
        return {
            "exists": True,
            "status": "corrupt",
            "compatible_source": None,
            "artifact_status": expected_status,
            "fingerprint": None,
            "counts": None,
            "warnings": [],
            "error": str(exc),
        }
    except CharacterRosterValidationError as exc:
        return {
            "exists": True,
            "status": "invalid",
            "compatible_source": None,
            "artifact_status": expected_status,
            "fingerprint": None,
            "counts": None,
            "warnings": [],
            "error": str(exc),
        }

    compatible_source = None

    if current_source is not None:
        compatible_source = (
            roster["source"]["fingerprint"]
            == current_source["fingerprint"]
        )

        if compatible_source and current_source_text is not None:
            try:
                roster = validate_character_roster(
                    roster,
                    source_text=current_source_text,
                    expected_status=expected_status,
                )
            except CharacterRosterValidationError as exc:
                return {
                    "exists": True,
                    "status": "invalid",
                    "compatible_source": True,
                    "artifact_status": expected_status,
                    "fingerprint": None,
                    "counts": None,
                    "warnings": [],
                    "error": str(exc),
                }

    status = (
        expected_status
        if compatible_source is not False
        else "incompatible_source"
    )
    fingerprint = (
        roster["draft_fingerprint"]
        if expected_status == "draft"
        else roster["roster_fingerprint"]
    )

    return {
        "exists": True,
        "status": status,
        "compatible_source": compatible_source,
        "artifact_status": expected_status,
        "fingerprint": fingerprint,
        "source": copy.deepcopy(roster["source"]),
        "counts": _roster_counts(roster),
        "warnings": list(roster["warnings"]),
        "error": None,
    }


def build_character_roster_status(
    *,
    draft_path: str | Path,
    approved_path: str | Path,
    current_source: dict[str, Any] | None,
    current_source_text: str | None,
    current_source_error: str | None,
) -> dict[str, Any]:
    draft = inspect_character_roster_file(
        draft_path,
        expected_status="draft",
        current_source=current_source,
        current_source_text=current_source_text,
    )
    approved = inspect_character_roster_file(
        approved_path,
        expected_status="approved",
        current_source=current_source,
        current_source_text=current_source_text,
    )

    if approved["status"] == "approved":
        active = "approved"
    elif draft["status"] == "draft":
        active = "draft"
    else:
        active = "none"

    return {
        "source": {
            "available": current_source is not None,
            "path": (
                current_source.get("path")
                if current_source
                else None
            ),
            "basename": (
                current_source.get("basename")
                if current_source
                else None
            ),
            "fingerprint": (
                current_source.get("fingerprint")
                if current_source
                else None
            ),
            "character_count": (
                current_source.get("character_count")
                if current_source
                else None
            ),
            "error": current_source_error,
        },
        "active": active,
        "draft": draft,
        "approved": approved,
    }
