from __future__ import annotations

import copy
from collections.abc import Mapping

from character_roster import stable_entry_id
from generation_state import fingerprint_value
from speaker_management_add_contract import (
    JsonObject,
    JsonValue,
    SpeakerAddCommand,
    SpeakerAddConflictError,
    SpeakerAddContext,
    SpeakerAddResult,
    SpeakerAddReview,
    SpeakerAddValidationError,
    SpeakerEvidence,
    parse_speaker_add,
)


def _text(value: JsonValue) -> str:
    return str(value or "").strip()


def _objects(value: JsonValue) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _matching_script_indices(
    command: SpeakerAddCommand,
    context: SpeakerAddContext,
) -> tuple[int, ...]:
    indices = tuple(
        index
        for index, item in enumerate(context.working_script)
        if _text(item.get("speaker") or item.get("type")).casefold()
        == command.script_speaker.casefold()
        and _text(item.get("text"))
    )
    if not indices:
        raise SpeakerAddValidationError(
            f"Script speaker {command.script_speaker!r} has no spoken entries."
        )
    return indices


def _reject_existing_identity(
    command: SpeakerAddCommand,
    context: SpeakerAddContext,
) -> None:
    for existing in context.roster_entries:
        entry_id = _text(existing.get("id"))
        mapping = context.script_mapping_by_id.get(entry_id, {})
        labels = {
            _text(existing.get("canonical_name")).casefold(),
            _text(existing.get("display_name")).casefold(),
            _text(mapping.get("script_voice_name")).casefold(),
        }
        if command.script_speaker.casefold() in labels:
            raise SpeakerAddConflictError(
                f"Script speaker {command.script_speaker!r} already has a roster identity."
            )


def _require_current_exclusion_audit(
    command: SpeakerAddCommand,
    context: SpeakerAddContext,
) -> None:
    if not command.require_exclusion_audit:
        return
    has_match = any(
        _text(item.get("name")).casefold()
        == command.script_speaker.casefold()
        for item in _objects(context.roster.get("excluded_entities"))
    )
    if not has_match:
        raise SpeakerAddConflictError(
            "The reviewed exclusion audit is no longer present. "
            "Refresh and review the current roster before retrying."
        )


def review_speaker_add(
    command: SpeakerAddCommand,
    context: SpeakerAddContext,
) -> SpeakerAddReview:
    if (
        command.expected_roster_fingerprint
        != _text(context.roster.get("roster_fingerprint"))
    ):
        raise SpeakerAddConflictError(
            "The character roster changed after this operation was loaded. "
            "Refresh and retry."
        )
    indices = _matching_script_indices(command, context)
    _reject_existing_identity(command, context)
    _require_current_exclusion_audit(command, context)
    sample_lines = tuple(
        _text(context.working_script[index].get("text"))
        for index in indices
    )
    evidence: list[SpeakerEvidence] = []
    for index, line in zip(indices, sample_lines, strict=True):
        evidence.append({
            "source_quote": line,
            "source_location": f"script entry {index} characters 0-{len(line)}",
            "start_char": 0,
            "end_char": len(line),
            "passage_index": None,
            "entry_index": index,
            "batch_index": 0,
            "category": "speaking",
            "confidence": command.confidence,
            "basis": "explicit",
        })
    return SpeakerAddReview(sample_lines, tuple(evidence))


def _new_roster_entry(
    command: SpeakerAddCommand,
    review: SpeakerAddReview,
) -> dict[str, JsonValue]:
    first_line = review.sample_lines[0]
    return {
        "id": stable_entry_id(
            "supplementary-script-speaker:"
            f"{command.script_speaker.casefold()}:"
            f"{fingerprint_value(first_line)}"
        ),
        "canonical_name": command.script_speaker,
        "display_name": command.display_name,
        "entity_kind": command.entity_kind,
        "speaking_status": command.speaking_status,
        "titles": list(command.titles),
        "aliases": list(command.aliases),
        "nicknames": list(command.nicknames),
        "pronouns": list(command.pronouns),
        "species": list(command.species),
        "relationships": list(command.relationships),
        "first_evidence_location": review.evidence[0]["source_location"],
        "additional_evidence_locations": [
            item["source_location"] for item in review.evidence[1:]
        ],
        "confidence": command.confidence,
        "resolution_status": "resolved",
        "possible_duplicate_ids": [],
        "mistaken_merge_risk": False,
        "unresolved_questions": [],
        "evidence": [dict(item) for item in review.evidence],
        "voice_clues": list(command.voice_clues),
        "sample_lines": list(review.sample_lines[:50]),
    }


def _updated_voice_config(
    command: SpeakerAddCommand,
    review: SpeakerAddReview,
    context: SpeakerAddContext,
) -> dict[str, JsonValue]:
    updated = copy.deepcopy(dict(context.voice_config))
    if not command.designed_voice_description:
        return updated
    existing = updated.get(command.script_speaker)
    if isinstance(existing, Mapping) and existing:
        raise SpeakerAddConflictError(
            f"Script speaker {command.script_speaker!r} already has a Voice configuration."
        )
    updated[command.script_speaker] = {
        "type": "design",
        "voice": None,
        "description": command.designed_voice_description,
        "ref_text": review.sample_lines[0],
    }
    return updated


def build_speaker_add(
    command: SpeakerAddCommand,
    review: SpeakerAddReview,
    context: SpeakerAddContext,
) -> SpeakerAddResult:
    entries = [
        copy.deepcopy(dict(entry)) for entry in context.roster_entries
    ]
    entries.append(_new_roster_entry(command, review))
    return SpeakerAddResult(
        roster_entries=entries,
        voice_config=_updated_voice_config(command, review, context),
        affected_speakers=(command.script_speaker,),
    )


def prepare_speaker_add(
    *,
    payload: JsonObject,
    context: SpeakerAddContext,
) -> SpeakerAddResult:
    command = parse_speaker_add(payload)
    review = review_speaker_add(command, context)
    return build_speaker_add(command, review, context)
