from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

from character_roster import (
    CharacterRosterError,
    build_draft_roster,
    compute_draft_fingerprint,
    read_character_roster,
    save_character_roster,
    stable_entry_id,
    validate_character_roster,
)
from external_workflows import (
    ExternalWorkflowConflictError,
    ExternalWorkflowValidationError,
    get_structured_result_candidate,
    list_structured_result_candidates,
    mark_structured_result_transferred,
    utc_timestamp,
)
from generation_state import fingerprint_value
from roster_discovery import (
    RosterDiscoveryEvidenceError,
    normalize_passage_result,
)


class RosterImportReconciliationError(RuntimeError):
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


class RosterImportReconciliationConflictError(RosterImportReconciliationError):
    pass


class RosterImportReconciliationValidationError(RosterImportReconciliationError):
    pass


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _normalized_label(value: Any) -> str:
    return "".join(
        character.casefold()
        for character in str(value or "")
        if character.isalnum()
    )


def _entity_labels(entity: dict[str, Any]) -> list[str]:
    return _ordered_unique(
        [
            str(entity.get("canonical_name") or ""),
            str(entity.get("display_name") or ""),
            *list(entity.get("aliases") or []),
            *list(entity.get("nicknames") or []),
        ]
    )


def _entry_labels(entry: dict[str, Any]) -> list[str]:
    return _entity_labels(entry)


def _all_occurrences(text: str, quote: str) -> list[int]:
    if not quote:
        return []
    values: list[int] = []
    offset = 0
    while True:
        found = text.find(quote, offset)
        if found < 0:
            return values
        values.append(found)
        offset = found + 1


def _resolve_imported_evidence(
    evidence: dict[str, Any],
    *,
    source_text: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    quote = str(evidence.get("quote") or "")
    claimed_start = evidence.get("start_char")
    claimed_end = evidence.get("end_char")
    diagnostics: dict[str, Any] = {
        "quote": quote,
        "claimed_start_char": claimed_start,
        "claimed_end_char": claimed_end,
        "status": "invalid",
        "resolved_start_char": None,
        "resolved_end_char": None,
        "occurrence_count": 0,
        "message": "The quote could not be located in the selected source.",
    }
    if not quote:
        diagnostics["message"] = "The imported evidence quote is empty."
        return None, diagnostics

    exact = (
        isinstance(claimed_start, int)
        and isinstance(claimed_end, int)
        and 0 <= claimed_start < claimed_end <= len(source_text)
        and source_text[claimed_start:claimed_end] == quote
    )
    occurrences = _all_occurrences(source_text, quote)
    diagnostics["occurrence_count"] = len(occurrences)
    if exact:
        resolved_start = claimed_start
        status = "exact"
        message = "Imported offsets match the selected source."
    elif occurrences:
        anchor = claimed_start if isinstance(claimed_start, int) else 0
        ranked = sorted((abs(value - anchor), value) for value in occurrences)
        if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
            diagnostics["status"] = "ambiguous"
            diagnostics["message"] = (
                "Two source occurrences are equally close to the imported offset."
            )
            return None, diagnostics
        resolved_start = ranked[0][1]
        status = "repaired"
        message = (
            "The imported offset did not match; the nearest exact source "
            "occurrence is proposed for review."
        )
    else:
        return None, diagnostics

    resolved_end = resolved_start + len(quote)
    diagnostics.update(
        {
            "status": status,
            "resolved_start_char": resolved_start,
            "resolved_end_char": resolved_end,
            "message": message,
        }
    )
    return (
        {
            "source_quote": quote,
            "source_location": f"characters {resolved_start}-{resolved_end}",
            "start_char": resolved_start,
            "end_char": resolved_end,
            "passage_index": 1,
            "entry_index": None,
            "batch_index": 1,
            "category": str(evidence.get("category") or "other"),
            "confidence": float(evidence.get("confidence") or 0.0),
            "basis": str(evidence.get("basis") or "inferred"),
        },
        diagnostics,
    )


def _import_id(candidate_id: str, index: int, entity: dict[str, Any]) -> str:
    return "imported_" + fingerprint_value(
        {
            "candidate_id": candidate_id,
            "index": index,
            "identity_seed": entity.get("identity_seed"),
        }
    )[:24]


def _native_semantic_review(
    candidate: dict[str, Any],
    entity: dict[str, Any],
    *,
    source_text: str,
) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    source_fingerprint = str(
        (candidate.get("snapshot") or {}).get("source_fingerprint") or ""
    )
    passage = {
        "index": 1,
        "start_char": 0,
        "end_char": len(source_text),
        "text": source_text,
        "fingerprint": source_fingerprint,
    }
    try:
        observations, warnings = normalize_passage_result(
            {"entities": [copy.deepcopy(entity)], "warnings": []},
            passage=passage,
            source_fingerprint=source_fingerprint,
        )
    except RosterDiscoveryEvidenceError as exc:
        errors = [
            line.strip(" -")
            for line in str(exc).splitlines()
            if line.strip(" -")
        ]
        return None, errors or [str(exc)], []
    if len(observations) != 1:
        return (
            None,
            ["Native roster validation did not return exactly one observation."],
            warnings,
        )
    return observations[0], [], warnings


def _converted_entity(
    candidate: dict[str, Any],
    entity: dict[str, Any],
    index: int,
    *,
    source_text: str,
) -> dict[str, Any]:
    resolved_evidence: list[dict[str, Any]] = []
    evidence_diagnostics: list[dict[str, Any]] = []
    for item in entity.get("evidence") or []:
        resolved, diagnostic = _resolve_imported_evidence(
            item,
            source_text=source_text,
        )
        evidence_diagnostics.append(diagnostic)
        if resolved is not None:
            resolved_evidence.append(resolved)

    imported_id = _import_id(candidate["candidate_id"], index, entity)
    original_resolution = str(entity.get("resolution_status") or "unresolved")
    unresolved_questions = _ordered_unique(
        list(entity.get("unresolved_questions") or [])
    )
    if not resolved_evidence:
        unresolved_questions.append(
            "No imported evidence offset could be bound to an exact source quote."
        )
    repaired_count = sum(
        item["status"] == "repaired" for item in evidence_diagnostics
    )
    invalid_count = sum(
        item["status"] in {"invalid", "ambiguous"}
        for item in evidence_diagnostics
    )
    if repaired_count:
        unresolved_questions.append(
            f"Review {repaired_count} evidence offset repair"
            + ("." if repaired_count == 1 else "s.")
        )

    native_observation, semantic_errors, semantic_warnings = (
        _native_semantic_review(
            candidate,
            entity,
            source_text=source_text,
        )
    )
    semantic_valid = native_observation is not None
    if semantic_errors:
        unresolved_questions.append(
            "Native semantic evidence validation failed; keep this observation "
            "unresolved or exclude it until its identity evidence is corrected."
        )

    canonical_name = str(entity.get("canonical_name") or "").strip()
    display_name = str(entity.get("display_name") or "").strip()
    if not display_name:
        display_name = canonical_name or str(entity.get("identity_seed") or imported_id)
    entry_source = native_observation or entity
    entry_evidence = (
        copy.deepcopy(native_observation["evidence"])
        if native_observation is not None
        else resolved_evidence
    )
    native_resolution = (
        "resolved"
        if original_resolution == "resolved"
        and semantic_valid
        and entry_evidence
        else "unresolved"
    )
    entry_id = stable_entry_id(
        f"{candidate['snapshot'].get('source_fingerprint')}:{candidate['candidate_id']}:{index}"
    )
    locations = _ordered_unique(
        [item["source_location"] for item in entry_evidence]
    )
    sample_lines = [
        line
        for line in _ordered_unique(list(entry_source.get("sample_lines") or []))
        if line in source_text
    ]
    entry = None
    if entry_evidence:
        entry = {
            "id": entry_id,
            "canonical_name": canonical_name,
            "display_name": display_name,
            "entity_kind": str(entry_source.get("entity_kind") or "unknown"),
            "speaking_status": str(
                entry_source.get("speaking_status") or "uncertain"
            ),
            "titles": _ordered_unique(list(entry_source.get("titles") or [])),
            "aliases": _ordered_unique(list(entry_source.get("aliases") or [])),
            "nicknames": _ordered_unique(
                list(entry_source.get("nicknames") or [])
            ),
            "pronouns": _ordered_unique(list(entry_source.get("pronouns") or [])),
            "species": _ordered_unique(list(entry_source.get("species") or [])),
            "relationships": _ordered_unique(
                list(entry_source.get("relationships") or [])
            ),
            "first_evidence_location": locations[0],
            "additional_evidence_locations": locations[1:],
            "confidence": float(entry_source.get("confidence") or 0.0),
            "resolution_status": native_resolution,
            "possible_duplicate_ids": [],
            "mistaken_merge_risk": (
                original_resolution == "duplicate_candidate"
                or invalid_count > 0
                or not semantic_valid
            ),
            "unresolved_questions": unresolved_questions,
            "evidence": entry_evidence,
            "voice_clues": _ordered_unique(
                list(entry_source.get("voice_clues") or [])
            ),
            "sample_lines": sample_lines,
        }

    return {
        "import_id": imported_id,
        "index": index,
        "identity_seed": str(entity.get("identity_seed") or ""),
        "canonical_name": canonical_name,
        "display_name": display_name,
        "entity_kind": str(entity.get("entity_kind") or "unknown"),
        "speaking_status": str(entity.get("speaking_status") or "uncertain"),
        "titles": _ordered_unique(list(entity.get("titles") or [])),
        "aliases": _ordered_unique(list(entity.get("aliases") or [])),
        "nicknames": _ordered_unique(list(entity.get("nicknames") or [])),
        "resolution_status": original_resolution,
        "unresolved_questions": unresolved_questions,
        "confidence": float(entity.get("confidence") or 0.0),
        "voice_clues": _ordered_unique(list(entity.get("voice_clues") or [])),
        "sample_lines": sample_lines,
        "evidence": copy.deepcopy(list(entity.get("evidence") or [])),
        "evidence_diagnostics": evidence_diagnostics,
        "resolved_evidence_count": len(resolved_evidence),
        "repaired_evidence_count": repaired_count,
        "invalid_evidence_count": invalid_count,
        "native_semantic_status": "valid" if semantic_valid else "invalid",
        "native_semantic_errors": semantic_errors,
        "native_semantic_warnings": semantic_warnings,
        "entry": entry,
    }


def _load_current_roster(
    *,
    draft_path: str | Path,
    approved_path: str | Path,
    source_text: str,
) -> tuple[str, dict[str, Any] | None, str | None]:
    def load_optional(
        kind: str,
        path: Path,
        expected_status: str,
    ) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            return read_character_roster(
                path,
                source_text=source_text,
                expected_status=expected_status,
            )
        except CharacterRosterError as exc:
            raise RosterImportReconciliationValidationError(
                "current_roster_invalid",
                f"The current {kind} roster cannot be reconciled: {exc}",
            ) from exc

    draft = load_optional("draft", Path(draft_path), "draft")
    approved = load_optional("approved", Path(approved_path), "approved")

    # Approval intentionally leaves its source draft on disk. Treat that
    # already-approved draft as history, not as newer working state. A later
    # reconciliation draft has a different fingerprint and takes precedence.
    if draft is not None and approved is not None:
        if draft["draft_fingerprint"] == approved["approved_draft_fingerprint"]:
            return "approved", approved, str(approved["roster_fingerprint"])
        return "draft", draft, str(draft["draft_fingerprint"])
    if draft is not None:
        return "draft", draft, str(draft["draft_fingerprint"])
    if approved is not None:
        return "approved", approved, str(approved["roster_fingerprint"])
    return "none", None, None


def _current_entry_summary(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": entry["id"],
        "canonical_name": entry["canonical_name"],
        "display_name": entry["display_name"],
        "entity_kind": entry["entity_kind"],
        "speaking_status": entry["speaking_status"],
        "resolution_status": entry["resolution_status"],
        "aliases": copy.deepcopy(entry.get("aliases") or []),
        "nicknames": copy.deepcopy(entry.get("nicknames") or []),
        "confidence": entry["confidence"],
    }


def build_roster_import_reconciliation(
    *,
    candidate: dict[str, Any],
    source_snapshot: dict[str, Any],
    source_text: str,
    draft_path: str | Path,
    approved_path: str | Path,
) -> dict[str, Any]:
    if candidate.get("task_type") != "roster_discovery":
        raise RosterImportReconciliationValidationError(
            "roster_discovery_candidate_required",
            "This candidate is not a roster-discovery result.",
        )
    if candidate.get("status") != "inspected":
        raise RosterImportReconciliationConflictError(
            "roster_import_already_applied",
            "This imported roster result has already entered native review.",
        )
    expected_source = (candidate.get("snapshot") or {}).get("source_fingerprint")
    if expected_source != source_snapshot.get("fingerprint"):
        raise RosterImportReconciliationConflictError(
            "stale_source",
            "The imported observations belong to a different selected source.",
        )

    result = candidate.get("result") or {}
    entities = result.get("entities")
    if not isinstance(entities, list):
        raise RosterImportReconciliationValidationError(
            "invalid_roster_import",
            "The imported roster result does not contain an entities array.",
        )
    current_kind, current, current_fingerprint = _load_current_roster(
        draft_path=draft_path,
        approved_path=approved_path,
        source_text=source_text,
    )
    current_entries = list((current or {}).get("entries") or [])
    label_index: dict[str, set[str]] = {}
    current_by_id = {entry["id"]: entry for entry in current_entries}
    for entry in current_entries:
        for label in _entry_labels(entry):
            normalized = _normalized_label(label)
            if normalized:
                label_index.setdefault(normalized, set()).add(entry["id"])

    observations: list[dict[str, Any]] = []
    for index, entity in enumerate(entities):
        converted = _converted_entity(
            candidate,
            entity,
            index,
            source_text=source_text,
        )
        matches: set[str] = set()
        match_labels: list[str] = []
        for label in _entity_labels(entity):
            normalized = _normalized_label(label)
            matched = label_index.get(normalized) or set()
            if matched:
                matches.update(matched)
                match_labels.append(label)
        if converted["entry"] is None:
            proposed_action = "exclude"
            proposed_current_entry_id = None
            proposal_reason = "No evidence quote can currently be bound to the source."
        elif converted["native_semantic_status"] == "invalid":
            proposed_action = "unresolved"
            proposed_current_entry_id = None
            proposal_reason = (
                "Native semantic evidence validation failed. Preserve this "
                "observation as unresolved or exclude it."
            )
        elif len(matches) == 1:
            proposed_action = "merge"
            proposed_current_entry_id = next(iter(matches))
            proposal_reason = (
                "A canonical name or alias matches one current roster entry."
            )
        elif len(matches) > 1:
            proposed_action = "unresolved"
            proposed_current_entry_id = None
            proposal_reason = (
                "The imported names match more than one current roster entry."
            )
        elif converted["resolution_status"] in {
            "unresolved",
            "unnamed",
            "duplicate_candidate",
        }:
            proposed_action = "unresolved"
            proposed_current_entry_id = None
            proposal_reason = "The imported result already marks this identity unresolved."
        else:
            proposed_action = "add"
            proposed_current_entry_id = None
            proposal_reason = "No current roster identity shares its names or aliases."

        converted.update(
            {
                "current_matches": [
                    _current_entry_summary(current_by_id[entry_id])
                    for entry_id in sorted(matches)
                ],
                "matched_labels": _ordered_unique(match_labels),
                "proposed_action": proposed_action,
                "proposed_current_entry_id": proposed_current_entry_id,
                "proposal_reason": proposal_reason,
            }
        )
        observations.append(converted)

    summary = {
        "current_entries": len(current_entries),
        "imported_observations": len(observations),
        "proposed_merges": sum(
            item["proposed_action"] == "merge" for item in observations
        ),
        "proposed_additions": sum(
            item["proposed_action"] == "add" for item in observations
        ),
        "proposed_exclusions": sum(
            item["proposed_action"] == "exclude" for item in observations
        ),
        "unresolved": sum(
            item["proposed_action"] == "unresolved" for item in observations
        ),
        "groups": sum(item["entity_kind"] == "group" for item in observations),
        "aliases": sum(len(item["aliases"]) for item in observations),
        "evidence_repairs": sum(
            item["repaired_evidence_count"] for item in observations
        ),
        "evidence_issues": sum(
            item["invalid_evidence_count"] for item in observations
        ),
        "semantic_invalid": sum(
            item["native_semantic_status"] == "invalid"
            for item in observations
        ),
    }
    return {
        "schema_version": 1,
        "status": "pending",
        "candidate_id": candidate["candidate_id"],
        "result_fingerprint": candidate["result_fingerprint"],
        "task_id": candidate.get("task_id"),
        "task_label": candidate.get("task_label") or "Discover character roster",
        "source": copy.deepcopy(source_snapshot),
        "current_kind": current_kind,
        "current_fingerprint": current_fingerprint,
        "current_entries": [
            _current_entry_summary(entry) for entry in current_entries
        ],
        "observations": observations,
        "warnings": copy.deepcopy(result.get("warnings") or []),
        "summary": summary,
    }


def _import_label_collisions(
    observations: list[dict[str, Any]],
) -> dict[str, set[str]]:
    label_index: dict[str, set[str]] = {}
    for observation in observations:
        import_id = str(observation.get("import_id") or "")
        if not import_id:
            continue
        for label in _entity_labels(observation):
            normalized = _normalized_label(label)
            if normalized:
                label_index.setdefault(normalized, set()).add(import_id)
    collisions: dict[str, set[str]] = {}
    for import_ids in label_index.values():
        if len(import_ids) < 2:
            continue
        for import_id in import_ids:
            collisions.setdefault(import_id, set()).update(
                value for value in import_ids if value != import_id
            )
    return collisions


def _safe_import_decision(
    observation: dict[str, Any],
    *,
    collision_ids: set[str],
) -> dict[str, Any] | None:
    entry = observation.get("entry")
    proposed_action = str(observation.get("proposed_action") or "")
    current_matches = list(observation.get("current_matches") or [])
    proposed_target = observation.get("proposed_current_entry_id")
    if not isinstance(entry, dict):
        return None
    if observation.get("native_semantic_status") != "valid":
        return None
    if int(observation.get("repaired_evidence_count") or 0) > 0:
        return None
    if int(observation.get("invalid_evidence_count") or 0) > 0:
        return None
    if observation.get("resolution_status") != "resolved":
        return None
    if entry.get("resolution_status") != "resolved":
        return None
    if entry.get("mistaken_merge_risk") is True:
        return None
    if collision_ids:
        return None
    if proposed_action == "merge":
        if len(current_matches) != 1:
            return None
        target = str(proposed_target or "")
        if target != str(current_matches[0].get("id") or ""):
            return None
        return {
            "import_id": observation["import_id"],
            "action": "merge",
            "current_entry_id": target,
        }
    if proposed_action == "add" and not current_matches:
        return {
            "import_id": observation["import_id"],
            "action": "add",
            "current_entry_id": None,
        }
    return None


def _issue_for_observation(
    observation: dict[str, Any],
    *,
    collision_ids: set[str],
) -> dict[str, Any]:
    entry = observation.get("entry")
    matches = list(observation.get("current_matches") or [])
    repaired = int(observation.get("repaired_evidence_count") or 0)
    invalid = int(observation.get("invalid_evidence_count") or 0)
    semantic_invalid = observation.get("native_semantic_status") != "valid"
    resolution = str(observation.get("resolution_status") or "unresolved")
    proposed_action = str(observation.get("proposed_action") or "unresolved")

    if not isinstance(entry, dict):
        code = "invalid_evidence"
        title = "Evidence could not be bound"
        explanation = (
            "No imported evidence quote can be bound safely to the selected source."
        )
        allowed_actions = ["exclude"]
    elif semantic_invalid or invalid:
        code = "invalid_evidence"
        title = "Evidence validation failed"
        explanation = (
            "Native evidence validation failed. Preserve the observation as "
            "unresolved or exclude it until the evidence is corrected."
        )
        allowed_actions = ["unresolved", "exclude"]
    elif repaired:
        code = "repaired_evidence"
        title = "Evidence offsets were repaired"
        explanation = (
            f"{repaired} imported evidence offset"
            + (" was" if repaired == 1 else "s were")
            + " moved to the nearest exact source occurrence and requires review."
        )
        allowed_actions = _ordered_unique(
            [proposed_action, "unresolved", "exclude"]
        )
    elif len(matches) > 1:
        code = "ambiguous_match"
        title = "More than one current identity matches"
        explanation = (
            "The imported names or aliases match multiple current roster entries."
        )
        allowed_actions = ["merge", "unresolved", "exclude"]
    elif resolution == "duplicate_candidate" or collision_ids:
        code = "duplicate_candidate"
        title = "Possible duplicate identity"
        explanation = (
            "The imported identity overlaps another imported or current identity "
            "and requires an explicit decision."
        )
        allowed_actions = _ordered_unique(
            ["merge" if matches else "add", "unresolved", "exclude"]
        )
    elif resolution in {"unresolved", "unnamed"}:
        code = "unresolved_identity"
        title = "Identity remains unresolved"
        explanation = (
            "The imported result does not establish a canonical resolved identity."
        )
        allowed_actions = ["unresolved", "exclude"]
    elif proposed_action == "merge" and (
        len(matches) != 1
        or str(observation.get("proposed_current_entry_id") or "")
        != str(matches[0].get("id") or "")
    ):
        code = "invalid_stable_id_relationship"
        title = "Merge target is no longer valid"
        explanation = (
            "The proposed current identity no longer matches the imported observation."
        )
        allowed_actions = ["unresolved", "exclude"]
    else:
        code = "identity_conflict"
        title = "Identity decision required"
        explanation = str(
            observation.get("proposal_reason")
            or "The imported identity cannot be applied automatically."
        )
        allowed_actions = _ordered_unique(
            [proposed_action, "unresolved", "exclude"]
        )

    issue_id = "roster_issue_" + fingerprint_value(
        {
            "import_id": observation.get("import_id"),
            "code": code,
            "result": observation.get("entry"),
            "matches": [item.get("id") for item in matches],
        }
    )[:24]
    return {
        "issue_id": issue_id,
        "import_id": observation["import_id"],
        "code": code,
        "title": title,
        "explanation": explanation,
        "blocking": True,
        "allowed_actions": allowed_actions,
        "proposed_action": proposed_action,
        "proposed_current_entry_id": observation.get(
            "proposed_current_entry_id"
        ),
        "current_matches": copy.deepcopy(matches),
        "collision_import_ids": sorted(collision_ids),
        "observation": copy.deepcopy(observation),
    }


def build_issue_focused_roster_import_reconciliation(
    reconciliation: dict[str, Any],
) -> dict[str, Any]:
    if reconciliation.get("status") != "pending":
        raise RosterImportReconciliationValidationError(
            "pending_roster_reconciliation_required",
            "Issue-focused reconciliation requires a pending imported roster result.",
        )
    observations = list(reconciliation.get("observations") or [])
    collisions = _import_label_collisions(observations)
    safe_decisions: list[dict[str, Any]] = []
    safe_changes: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for observation in observations:
        import_id = str(observation.get("import_id") or "")
        collision_ids = collisions.get(import_id, set())
        decision = _safe_import_decision(
            observation,
            collision_ids=collision_ids,
        )
        if decision is not None:
            safe_decisions.append(decision)
            safe_changes.append(
                {
                    "import_id": import_id,
                    "action": decision["action"],
                    "current_entry_id": decision.get("current_entry_id"),
                    "canonical_name": observation.get("canonical_name"),
                    "display_name": observation.get("display_name"),
                    "reason": observation.get("proposal_reason"),
                }
            )
            continue
        issues.append(
            _issue_for_observation(
                observation,
                collision_ids=collision_ids,
            )
        )
    summary = {
        **copy.deepcopy(reconciliation.get("summary") or {}),
        "safe_change_count": len(safe_changes),
        "safe_merge_count": sum(
            item["action"] == "merge" for item in safe_changes
        ),
        "safe_addition_count": sum(
            item["action"] == "add" for item in safe_changes
        ),
        "issue_count": len(issues),
        "repaired_evidence_issue_count": sum(
            item["code"] == "repaired_evidence" for item in issues
        ),
        "duplicate_issue_count": sum(
            item["code"] == "duplicate_candidate" for item in issues
        ),
        "unresolved_issue_count": sum(
            item["code"] == "unresolved_identity" for item in issues
        ),
    }
    return {
        "schema_version": 1,
        "status": reconciliation["status"],
        "candidate_id": reconciliation["candidate_id"],
        "result_fingerprint": reconciliation["result_fingerprint"],
        "task_id": reconciliation.get("task_id"),
        "task_label": reconciliation.get("task_label"),
        "source": copy.deepcopy(reconciliation.get("source") or {}),
        "current_kind": reconciliation["current_kind"],
        "current_fingerprint": reconciliation.get("current_fingerprint"),
        "current_entries": copy.deepcopy(
            reconciliation.get("current_entries") or []
        ),
        "safe_changes": safe_changes,
        "safe_decisions": safe_decisions,
        "issues": issues,
        "warnings": copy.deepcopy(reconciliation.get("warnings") or []),
        "summary": summary,
        "apply_ready": len(issues) == 0,
    }


def get_pending_issue_focused_roster_reconciliation(
    *,
    root_dir: str | Path,
    source_snapshot: dict[str, Any] | None,
    source_text: str | None,
    draft_path: str | Path,
    approved_path: str | Path,
    candidate_id: str | None = None,
) -> dict[str, Any] | None:
    reconciliation = get_pending_roster_import_reconciliation(
        root_dir=root_dir,
        source_snapshot=source_snapshot,
        source_text=source_text,
        draft_path=draft_path,
        approved_path=approved_path,
        candidate_id=candidate_id,
    )
    if reconciliation is None:
        return None
    return build_issue_focused_roster_import_reconciliation(reconciliation)


def get_pending_roster_import_reconciliation(
    *,
    root_dir: str | Path,
    source_snapshot: dict[str, Any] | None,
    source_text: str | None,
    draft_path: str | Path,
    approved_path: str | Path,
    candidate_id: str | None = None,
) -> dict[str, Any] | None:
    if source_snapshot is None or source_text is None:
        return None
    try:
        source_candidates = list_structured_result_candidates(
            root_dir=root_dir,
            task_type="roster_discovery",
            source_fingerprint=source_snapshot["fingerprint"],
        )
        transferred_fingerprints = {
            str(item.get("result_fingerprint") or "")
            for item in source_candidates
            if item.get("status") == "transferred"
        }
        if candidate_id:
            candidate = get_structured_result_candidate(
                root_dir=root_dir,
                candidate_id=candidate_id,
            )
            candidates = [
                candidate
                for candidate in [candidate]
                if candidate.get("status") == "inspected"
                and candidate.get("result_fingerprint")
                not in transferred_fingerprints
            ]
        else:
            candidates = [
                item
                for item in source_candidates
                if item.get("status") == "inspected"
                and item.get("result_fingerprint")
                not in transferred_fingerprints
            ]
    except (ExternalWorkflowValidationError, ExternalWorkflowConflictError) as exc:
        raise RosterImportReconciliationValidationError(
            exc.code,
            str(exc),
            details=exc.details,
        ) from exc
    if not candidates:
        return None
    return build_roster_import_reconciliation(
        candidate=candidates[0],
        source_snapshot=source_snapshot,
        source_text=source_text,
        draft_path=draft_path,
        approved_path=approved_path,
    )


def _merge_entry(primary: dict[str, Any], imported: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(primary)
    aliases = [
        *list(primary.get("aliases") or []),
        *list(imported.get("aliases") or []),
        imported.get("canonical_name") or "",
        imported.get("display_name") or "",
    ]
    for field in (
        "titles",
        "nicknames",
        "pronouns",
        "species",
        "relationships",
        "voice_clues",
        "sample_lines",
    ):
        merged[field] = _ordered_unique(
            [
                *list(primary.get(field) or []),
                *list(imported.get(field) or []),
            ]
        )
    merged["aliases"] = [
        value
        for value in _ordered_unique(aliases)
        if _normalized_label(value)
        not in {
            _normalized_label(primary.get("canonical_name")),
            _normalized_label(primary.get("display_name")),
        }
    ]
    seen_evidence: set[str] = set()
    merged_evidence: list[dict[str, Any]] = []
    for item in [
        *list(primary.get("evidence") or []),
        *list(imported.get("evidence") or []),
    ]:
        key = fingerprint_value(item)
        if key in seen_evidence:
            continue
        seen_evidence.add(key)
        merged_evidence.append(copy.deepcopy(item))
    merged_evidence.sort(
        key=lambda item: (item["start_char"], item["end_char"], item["category"])
    )
    merged["evidence"] = merged_evidence
    locations = _ordered_unique(
        [item["source_location"] for item in merged_evidence]
    )
    merged["first_evidence_location"] = locations[0]
    merged["additional_evidence_locations"] = locations[1:]
    merged["confidence"] = max(
        float(primary.get("confidence") or 0.0),
        float(imported.get("confidence") or 0.0),
    )
    return merged


def _restore_bytes(path: Path, content: bytes | None) -> None:
    if content is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    temporary = path.with_name(path.name + ".roster-import-rollback")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def apply_roster_import_reconciliation(
    *,
    root_dir: str | Path,
    candidate_id: str,
    expected_result_fingerprint: str,
    expected_current_kind: str,
    expected_current_fingerprint: str | None,
    decisions: list[dict[str, Any]],
    source_snapshot: dict[str, Any],
    source_text: str,
    draft_path: str | Path,
    approved_path: str | Path,
    applied_at_utc: str | None = None,
    decision_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reconciliation = get_pending_roster_import_reconciliation(
        root_dir=root_dir,
        source_snapshot=source_snapshot,
        source_text=source_text,
        draft_path=draft_path,
        approved_path=approved_path,
        candidate_id=candidate_id,
    )
    if reconciliation is None:
        raise RosterImportReconciliationValidationError(
            "roster_import_not_found",
            "No pending imported roster observations were found.",
        )
    if reconciliation["result_fingerprint"] != expected_result_fingerprint:
        raise RosterImportReconciliationConflictError(
            "stale_roster_import",
            "The imported roster result changed before reconciliation was applied.",
        )
    if (
        reconciliation["current_kind"] != expected_current_kind
        or reconciliation["current_fingerprint"] != expected_current_fingerprint
    ):
        raise RosterImportReconciliationConflictError(
            "stale_current_roster",
            "The current roster changed. Refresh the comparison before applying it.",
        )

    observations = {
        item["import_id"]: item for item in reconciliation["observations"]
    }
    decision_by_id: dict[str, dict[str, Any]] = {}
    for decision in decisions:
        import_id = str(decision.get("import_id") or "")
        if import_id not in observations or import_id in decision_by_id:
            raise RosterImportReconciliationValidationError(
                "invalid_roster_reconciliation_partition",
                "Every imported observation must have exactly one decision.",
            )
        action = str(decision.get("action") or "")
        if action not in {"merge", "add", "exclude", "unresolved"}:
            raise RosterImportReconciliationValidationError(
                "invalid_roster_reconciliation_action",
                f"Unsupported roster reconciliation action: {action!r}.",
            )
        decision_by_id[import_id] = {
            "import_id": import_id,
            "action": action,
            "current_entry_id": decision.get("current_entry_id"),
        }
    if set(decision_by_id) != set(observations):
        raise RosterImportReconciliationValidationError(
            "incomplete_roster_reconciliation",
            "Choose merge, add, exclude, or unresolved for all imported observations.",
            details={
                "expected": len(observations),
                "received": len(decision_by_id),
            },
        )

    current_kind, current, current_fingerprint = _load_current_roster(
        draft_path=draft_path,
        approved_path=approved_path,
        source_text=source_text,
    )
    if current_kind != expected_current_kind or current_fingerprint != expected_current_fingerprint:
        raise RosterImportReconciliationConflictError(
            "stale_current_roster",
            "The current roster changed. Refresh the comparison before applying it.",
        )
    base = current or {}
    entries = copy.deepcopy(list(base.get("entries") or []))
    entry_by_id = {entry["id"]: entry for entry in entries}
    excluded_entities = copy.deepcopy(list(base.get("excluded_entities") or []))
    warnings = _ordered_unique(
        [
            *list(base.get("warnings") or []),
            *list(reconciliation.get("warnings") or []),
        ]
    )
    added_ids: list[str] = []
    merged_count = 0
    added_count = 0
    excluded_count = 0
    unresolved_count = 0

    for import_id, observation in observations.items():
        decision = decision_by_id[import_id]
        action = decision["action"]
        imported_entry = copy.deepcopy(observation.get("entry"))
        if action == "exclude":
            excluded_count += 1
            if imported_entry is not None:
                excluded_entities.append(
                    {
                        "name": observation["display_name"],
                        "reason": "Excluded during imported roster reconciliation.",
                        "evidence": copy.deepcopy(imported_entry["evidence"]),
                    }
                )
            else:
                warnings.append(
                    f"Excluded imported observation {import_id} had no source-bound evidence; its raw data remains in the Task Bundle candidate."
                )
            continue
        if imported_entry is None:
            raise RosterImportReconciliationValidationError(
                "roster_import_evidence_required",
                f"{observation['display_name']} has no source-bound evidence and can only be excluded until its evidence is corrected.",
                details={"import_id": import_id},
            )
        if (
            observation["native_semantic_status"] == "invalid"
            and action in {"merge", "add"}
        ):
            raise RosterImportReconciliationValidationError(
                "roster_import_semantic_validation_required",
                f"{observation['display_name']} fails native semantic evidence validation and must remain unresolved or be excluded.",
                details={
                    "import_id": import_id,
                    "semantic_errors": observation["native_semantic_errors"],
                },
            )
        if action == "merge":
            current_entry_id = str(decision.get("current_entry_id") or "")
            if current_entry_id not in entry_by_id:
                raise RosterImportReconciliationValidationError(
                    "roster_merge_target_required",
                    f"Choose a current roster entry for {observation['display_name']}.",
                    details={"import_id": import_id},
                )
            merged = _merge_entry(entry_by_id[current_entry_id], imported_entry)
            entry_by_id[current_entry_id].clear()
            entry_by_id[current_entry_id].update(merged)
            merged_count += 1
            continue
        if action == "unresolved":
            imported_entry["resolution_status"] = "unresolved"
            imported_entry["unresolved_questions"] = _ordered_unique(
                [
                    *list(imported_entry.get("unresolved_questions") or []),
                    "Resolve this imported identity before canonical approval.",
                ]
            )
            unresolved_count += 1
        else:
            added_count += 1
        if imported_entry["id"] in entry_by_id:
            raise RosterImportReconciliationConflictError(
                "duplicate_roster_entry_id",
                "An imported stable identity ID already exists in the current roster.",
                details={"entry_id": imported_entry["id"]},
            )
        entries.append(imported_entry)
        entry_by_id[imported_entry["id"]] = imported_entry
        added_ids.append(imported_entry["id"])

    unresolved = []
    for entry in entries:
        if entry["resolution_status"] not in {"unresolved", "unnamed"}:
            continue
        questions = entry.get("unresolved_questions") or [
            "This identity requires user review."
        ]
        for question in questions:
            unresolved.append(
                {
                    "entry_id": entry["id"],
                    "question": question,
                    "confidence": entry["confidence"],
                }
            )

    duplicate_candidates = copy.deepcopy(
        list(base.get("duplicate_candidates") or [])
    )
    added_by_label: dict[str, list[dict[str, Any]]] = {}
    for entry_id in added_ids:
        entry = entry_by_id[entry_id]
        label = _normalized_label(entry.get("canonical_name"))
        if label:
            added_by_label.setdefault(label, []).append(entry)
    for same_name_entries in added_by_label.values():
        if len(same_name_entries) < 2:
            continue
        primary = same_name_entries[0]
        for other in same_name_entries[1:]:
            evidence = copy.deepcopy(
                (primary.get("evidence") or [])[:1]
                + (other.get("evidence") or [])[:1]
            )
            duplicate_candidates.append(
                {
                    "entry_ids": [primary["id"], other["id"]],
                    "reason": "Imported observations share the same canonical label; confirm merge or keep separate.",
                    "confidence": min(primary["confidence"], other["confidence"]),
                    "evidence": evidence,
                }
            )
            primary["possible_duplicate_ids"] = _ordered_unique(
                [*primary["possible_duplicate_ids"], other["id"]]
            )
            other["possible_duplicate_ids"] = _ordered_unique(
                [*other["possible_duplicate_ids"], primary["id"]]
            )

    created = applied_at_utc or utc_timestamp()
    discovery = copy.deepcopy(base.get("discovery") or {})
    if not discovery:
        discovery = {
            "created_at_utc": created,
            "model_name": "Ordinary ChatGPT Task Bundle",
            "backend": "external_chatgpt",
            "generation_fingerprint": fingerprint_value(
                {
                    "candidate_id": candidate_id,
                    "result_fingerprint": expected_result_fingerprint,
                }
            ),
            "batch_count": 1,
            "completed_batches": 1,
        }
    draft = build_draft_roster(
        source=source_snapshot,
        discovery=discovery,
        entries=entries,
        unresolved=unresolved,
        duplicate_candidates=duplicate_candidates,
        excluded_entities=excluded_entities,
        warnings=_ordered_unique(warnings),
        source_text=source_text,
    )
    draft["review_history"] = copy.deepcopy(list(base.get("review_history") or []))
    draft["draft_fingerprint"] = compute_draft_fingerprint(draft)
    draft = validate_character_roster(
        draft,
        source_text=source_text,
        expected_status="draft",
    )

    draft_target = Path(draft_path)
    before = draft_target.read_bytes() if draft_target.exists() else None
    try:
        saved = save_character_roster(
            draft,
            draft_target,
            source_text=source_text,
            expected_status="draft",
        )
        application = {
            "status": "native_review_ready",
            "destination": "character_roster",
            "tab": "characters",
            "stage": "roster_import_reconciliation",
            "observation_count": len(observations),
            "merged_count": merged_count,
            "added_count": added_count,
            "excluded_count": excluded_count,
            "unresolved_count": unresolved_count,
            "draft_fingerprint": saved["draft_fingerprint"],
            "approved_roster_preserved": Path(approved_path).exists(),
            "decision_summary": copy.deepcopy(decision_summary or {}),
            "at_utc": created,
        }
        transferred = mark_structured_result_transferred(
            root_dir=root_dir,
            candidate_id=candidate_id,
            expected_result_fingerprint=expected_result_fingerprint,
            application=application,
        )
    except Exception:
        _restore_bytes(draft_target, before)
        raise
    transferred["draft"] = saved
    return transferred


def apply_issue_focused_roster_import_reconciliation(
    *,
    root_dir: str | Path,
    candidate_id: str,
    expected_result_fingerprint: str,
    expected_current_kind: str,
    expected_current_fingerprint: str | None,
    issue_decisions: list[dict[str, Any]],
    source_snapshot: dict[str, Any],
    source_text: str,
    draft_path: str | Path,
    approved_path: str | Path,
    applied_at_utc: str | None = None,
) -> dict[str, Any]:
    focused = get_pending_issue_focused_roster_reconciliation(
        root_dir=root_dir,
        source_snapshot=source_snapshot,
        source_text=source_text,
        draft_path=draft_path,
        approved_path=approved_path,
        candidate_id=candidate_id,
    )
    if focused is None:
        raise RosterImportReconciliationValidationError(
            "roster_import_not_found",
            "No pending imported roster observations were found.",
        )
    if focused["result_fingerprint"] != expected_result_fingerprint:
        raise RosterImportReconciliationConflictError(
            "stale_roster_import",
            "The imported roster result changed before reconciliation was applied.",
        )
    if (
        focused["current_kind"] != expected_current_kind
        or focused["current_fingerprint"] != expected_current_fingerprint
    ):
        raise RosterImportReconciliationConflictError(
            "stale_current_roster",
            "The current roster changed. Refresh the issue queue before applying it.",
        )

    issue_by_import_id = {
        item["import_id"]: item for item in focused["issues"]
    }
    selected: dict[str, dict[str, Any]] = {}
    for decision in issue_decisions:
        import_id = str(decision.get("import_id") or "")
        issue = issue_by_import_id.get(import_id)
        if issue is None or import_id in selected:
            raise RosterImportReconciliationValidationError(
                "invalid_roster_issue_partition",
                "Every operator issue must have exactly one decision.",
            )
        action = str(decision.get("action") or "")
        if action not in set(issue["allowed_actions"]):
            raise RosterImportReconciliationValidationError(
                "invalid_roster_issue_action",
                f"Action {action!r} is not allowed for {issue['title']}.",
                details={
                    "import_id": import_id,
                    "allowed_actions": issue["allowed_actions"],
                },
            )
        current_entry_id = decision.get("current_entry_id")
        if action == "merge":
            allowed_targets = {
                str(item.get("id") or "")
                for item in issue.get("current_matches") or []
            }
            if str(current_entry_id or "") not in allowed_targets:
                raise RosterImportReconciliationValidationError(
                    "roster_issue_merge_target_invalid",
                    "Choose one of the current identities shown for this issue.",
                    details={
                        "import_id": import_id,
                        "allowed_current_entry_ids": sorted(allowed_targets),
                    },
                )
        selected[import_id] = {
            "import_id": import_id,
            "action": action,
            "current_entry_id": current_entry_id,
        }
    if set(selected) != set(issue_by_import_id):
        raise RosterImportReconciliationValidationError(
            "incomplete_roster_issue_reconciliation",
            "Resolve every displayed roster issue before applying the import.",
            details={
                "expected": len(issue_by_import_id),
                "received": len(selected),
            },
        )

    decisions = [
        *copy.deepcopy(focused["safe_decisions"]),
        *[selected[import_id] for import_id in sorted(selected)],
    ]
    return apply_roster_import_reconciliation(
        root_dir=root_dir,
        candidate_id=candidate_id,
        expected_result_fingerprint=expected_result_fingerprint,
        expected_current_kind=expected_current_kind,
        expected_current_fingerprint=expected_current_fingerprint,
        decisions=decisions,
        source_snapshot=source_snapshot,
        source_text=source_text,
        draft_path=draft_path,
        approved_path=approved_path,
        applied_at_utc=applied_at_utc,
        decision_summary={
            "mode": "issue_focused",
            "safe_change_count": len(focused["safe_changes"]),
            "operator_issue_count": len(focused["issues"]),
        },
    )
