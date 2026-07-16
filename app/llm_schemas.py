from __future__ import annotations

from typing import Any, Callable


class ContractValidationError(ValueError):
    """Raised when an LLM response violates an Alexandria output contract."""


PERSONA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "ref_text": {"type": "string"},
    },
    "required": ["description", "ref_text"],
    "additionalProperties": False,
}


SCRIPT_ENTRY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "speaker": {"type": "string"},
        "text": {"type": "string"},
        "instruct": {"type": "string"},
    },
    "required": ["speaker", "text", "instruct"],
    "additionalProperties": False,
}


# Alexandria stores scripts as a bare JSON array.
SCRIPT_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": SCRIPT_ENTRY_SCHEMA,
}


# Some models insist on wrapping arrays despite being asked not to.
# The local validator accepts this shape and unwraps it.
SCRIPT_WRAPPED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entries": SCRIPT_SCHEMA,
    },
    "required": ["entries"],
    "additionalProperties": False,
}


ALIAS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": {"type": "string"},
}


EVIDENCE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entry_index": {"type": "integer"},
        "quote": {"type": "string"},
    },
    "required": ["entry_index", "quote"],
    "additionalProperties": False,
}


ADVANCED_DISCOVERY_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "aliases": {
            "type": "array",
            "items": {"type": "string"},
        },
        "features": {
            "type": "array",
            "items": {"type": "string"},
        },
        "personality": {
            "type": "array",
            "items": {"type": "string"},
        },
        "voice_clues": {
            "type": "array",
            "items": {"type": "string"},
        },
        "relationships": {
            "type": "array",
            "items": {"type": "string"},
        },
        "evidence": {
            "type": "array",
            "items": EVIDENCE_ITEM_SCHEMA,
        },
        "sample_lines": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "aliases",
        "features",
        "personality",
        "voice_clues",
        "relationships",
        "evidence",
        "sample_lines",
    ],
    "additionalProperties": False,
}


ADVANCED_DISCOVERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": ADVANCED_DISCOVERY_ITEM_SCHEMA,
}


ROSTER_EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "quote": {"type": "string"},
        "start_char": {"type": "integer"},
        "end_char": {"type": "integer"},
        "category": {
            "type": "string",
            "enum": [
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
            ],
        },
        "confidence": {"type": "number"},
        "basis": {
            "type": "string",
            "enum": ["explicit", "inferred"],
        },
    },
    "required": [
        "quote",
        "start_char",
        "end_char",
        "category",
        "confidence",
        "basis",
    ],
    "additionalProperties": False,
}


ROSTER_DISCOVERY_ENTITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "identity_seed": {"type": "string"},
        "canonical_name": {"type": "string"},
        "display_name": {"type": "string"},
        "entity_kind": {
            "type": "string",
            "enum": [
                "character",
                "group",
                "creature",
                "narrator_role",
                "named_non_speaker",
                "unknown",
            ],
        },
        "speaking_status": {
            "type": "string",
            "enum": [
                "speaker",
                "non_speaker",
                "uncertain",
                "narrator",
            ],
        },
        "titles": {
            "type": "array",
            "items": {"type": "string"},
        },
        "aliases": {
            "type": "array",
            "items": {"type": "string"},
        },
        "nicknames": {
            "type": "array",
            "items": {"type": "string"},
        },
        "pronouns": {
            "type": "array",
            "items": {"type": "string"},
        },
        "species": {
            "type": "array",
            "items": {"type": "string"},
        },
        "relationships": {
            "type": "array",
            "items": {"type": "string"},
        },
        "voice_clues": {
            "type": "array",
            "items": {"type": "string"},
        },
        "sample_lines": {
            "type": "array",
            "items": {"type": "string"},
        },
        "confidence": {"type": "number"},
        "resolution_status": {
            "type": "string",
            "enum": [
                "resolved",
                "unresolved",
                "unnamed",
                "duplicate_candidate",
            ],
        },
        "unresolved_questions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "evidence": {
            "type": "array",
            "items": ROSTER_EVIDENCE_SCHEMA,
        },
    },
    "required": [
        "identity_seed",
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
        "voice_clues",
        "sample_lines",
        "confidence",
        "resolution_status",
        "unresolved_questions",
        "evidence",
    ],
    "additionalProperties": False,
}


ROSTER_DISCOVERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": ROSTER_DISCOVERY_ENTITY_SCHEMA,
        },
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["entities", "warnings"],
    "additionalProperties": False,
}


ROSTER_RECONCILIATION_ENTRY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "identity_seed": {"type": "string"},
        "canonical_name": {"type": "string"},
        "display_name": {"type": "string"},
        "entity_kind": {
            "type": "string",
            "enum": [
                "character",
                "group",
                "creature",
                "narrator_role",
                "named_non_speaker",
                "unknown",
            ],
        },
        "speaking_status": {
            "type": "string",
            "enum": [
                "speaker",
                "non_speaker",
                "uncertain",
                "narrator",
            ],
        },
        "observation_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "confidence": {"type": "number"},
        "resolution_status": {
            "type": "string",
            "enum": [
                "resolved",
                "unresolved",
                "unnamed",
                "duplicate_candidate",
            ],
        },
        "possible_duplicate_seeds": {
            "type": "array",
            "items": {"type": "string"},
        },
        "mistaken_merge_risk": {"type": "boolean"},
        "unresolved_questions": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "identity_seed",
        "canonical_name",
        "display_name",
        "entity_kind",
        "speaking_status",
        "observation_ids",
        "confidence",
        "resolution_status",
        "possible_duplicate_seeds",
        "mistaken_merge_risk",
        "unresolved_questions",
    ],
    "additionalProperties": False,
}


ROSTER_RECONCILIATION_DUPLICATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "identity_seeds": {
            "type": "array",
            "items": {"type": "string"},
        },
        "reason": {"type": "string"},
        "confidence": {"type": "number"},
        "observation_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "identity_seeds",
        "reason",
        "confidence",
        "observation_ids",
    ],
    "additionalProperties": False,
}


ROSTER_RECONCILIATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": ROSTER_RECONCILIATION_ENTRY_SCHEMA,
        },
        "duplicate_candidates": {
            "type": "array",
            "items": ROSTER_RECONCILIATION_DUPLICATE_SCHEMA,
        },
        "excluded_observation_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "entries",
        "duplicate_candidates",
        "excluded_observation_ids",
        "warnings",
    ],
    "additionalProperties": False,
}


SCHEMAS: dict[str, dict[str, Any]] = {
    "persona": PERSONA_SCHEMA,
    "script": SCRIPT_SCHEMA,
    "review": SCRIPT_SCHEMA,
    "alias": ALIAS_SCHEMA,
    "advanced_discovery": ADVANCED_DISCOVERY_SCHEMA,
    "roster_discovery": ROSTER_DISCOVERY_SCHEMA,
    "roster_reconciliation": ROSTER_RECONCILIATION_SCHEMA,
}


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractValidationError(
            f"{label} must be a JSON object, got {type(value).__name__}"
        )
    return value


def _require_exact_keys(
    value: dict[str, Any],
    expected: set[str],
    label: str,
) -> None:
    actual = set(value.keys())

    missing = expected - actual
    extra = actual - expected

    problems: list[str] = []

    if missing:
        problems.append(f"missing keys: {sorted(missing)}")

    if extra:
        problems.append(f"unexpected keys: {sorted(extra)}")

    if problems:
        raise ContractValidationError(
            f"{label} has invalid fields ({'; '.join(problems)})"
        )


def validate_persona(value: Any) -> dict[str, str]:
    obj = _require_dict(value, "Persona response")
    _require_exact_keys(
        obj,
        {"description", "ref_text"},
        "Persona response",
    )

    description = obj["description"]
    ref_text = obj["ref_text"]

    if not isinstance(description, str) or not description.strip():
        raise ContractValidationError(
            "Persona description must be a nonempty string"
        )

    if not isinstance(ref_text, str) or not ref_text.strip():
        raise ContractValidationError(
            "Persona ref_text must be a nonempty string"
        )

    return {
        "description": description.strip(),
        "ref_text": ref_text.strip(),
    }


def validate_script(value: Any) -> list[dict[str, str]]:
    # Compatibility normalization for models that wrap the requested array.
    if isinstance(value, dict) and set(value.keys()) == {"entries"}:
        value = value["entries"]

    if not isinstance(value, list):
        raise ContractValidationError(
            f"Script response must be an array, got {type(value).__name__}"
        )

    if not value:
        raise ContractValidationError("Script response must not be empty")

    normalized: list[dict[str, str]] = []

    for index, raw_entry in enumerate(value):
        entry = _require_dict(raw_entry, f"Script entry {index}")

        _require_exact_keys(
            entry,
            {"speaker", "text", "instruct"},
            f"Script entry {index}",
        )

        speaker = entry["speaker"]
        text = entry["text"]
        instruct = entry["instruct"]

        if not isinstance(speaker, str) or not speaker.strip():
            raise ContractValidationError(
                f"Script entry {index} speaker must be a nonempty string"
            )

        if not isinstance(text, str) or not text.strip():
            raise ContractValidationError(
                f"Script entry {index} text must be a nonempty string"
            )

        if not isinstance(instruct, str):
            raise ContractValidationError(
                f"Script entry {index} instruct must be a string"
            )

        normalized.append(
            {
                "speaker": speaker.strip(),
                "text": text.strip(),
                "instruct": instruct.strip(),
            }
        )

    return normalized


def validate_alias_map(value: Any) -> dict[str, str]:
    obj = _require_dict(value, "Alias response")
    normalized: dict[str, str] = {}

    for raw_name, canonical_name in obj.items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ContractValidationError(
                "Alias response contains an empty or non-string key"
            )

        if not isinstance(canonical_name, str) or not canonical_name.strip():
            raise ContractValidationError(
                f"Alias target for {raw_name!r} must be a nonempty string"
            )

        normalized[raw_name.strip()] = canonical_name.strip()

    return normalized


def _validate_string_list(
    value: Any,
    label: str,
) -> list[str]:
    if not isinstance(value, list):
        raise ContractValidationError(f"{label} must be an array")

    normalized: list[str] = []

    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ContractValidationError(
                f"{label}[{index}] must be a string"
            )

        if item.strip():
            normalized.append(item.strip())

    return normalized


def _validate_exact_string_list(
    value: Any,
    label: str,
) -> list[str]:
    if not isinstance(value, list):
        raise ContractValidationError(f"{label} must be an array")

    normalized: list[str] = []

    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise ContractValidationError(
                f"{label}[{index}] must be nonempty text"
            )
        normalized.append(item)

    return normalized


def _validate_contract_confidence(
    value: Any,
    label: str,
) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
    ):
        raise ContractValidationError(
            f"{label} must be numeric"
        )

    normalized = float(value)

    if not 0.0 <= normalized <= 1.0:
        raise ContractValidationError(
            f"{label} must be between 0.0 and 1.0"
        )

    return normalized


def _validate_enum_text(
    value: Any,
    label: str,
    allowed: set[str],
    aliases: dict[str, str] | None = None,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(
            f"{label} must be a nonempty string"
        )

    normalized = value.strip()
    alias_key = normalized.casefold().replace("-", "_").replace(" ", "_")

    if aliases and normalized not in allowed:
        normalized = aliases.get(alias_key, normalized)

    if normalized not in allowed:
        raise ContractValidationError(
            f"{label} must be one of {sorted(allowed)}"
        )

    return normalized


_ENTITY_KIND_ALIASES = {
    "human": "character",
    "person": "character",
    "humanoid": "character",
    "named_speaker": "character",
    "unnamed_speaker": "character",
    "alien": "creature",
    "nonhuman": "creature",
    "non_human": "creature",
    "animal": "creature",
    "extraterrestrial": "creature",
    "organization": "group",
    "collective": "group",
    "crowd": "group",
    "narrator": "narrator_role",
    "narration": "narrator_role",
    "machine": "named_non_speaker",
    "object": "named_non_speaker",
    "named_object": "named_non_speaker",
    "vehicle": "named_non_speaker",
    "ship": "named_non_speaker",
    "place": "named_non_speaker",
    "location": "named_non_speaker",
    "time_machine": "named_non_speaker",
    "uncertain": "unknown",
}
_SPEAKING_STATUS_ALIASES = {
    "named_speaker": "speaker",
    "unnamed_speaker": "speaker",
    "speaking_character": "speaker",
    "silent": "non_speaker",
    "non_speaking": "non_speaker",
    "nonspeaking": "non_speaker",
    "unknown": "uncertain",
}
_RESOLUTION_STATUS_ALIASES = {
    "unique": "resolved",
    "confirmed": "resolved",
    "ambiguous": "unresolved",
    "uncertain": "unresolved",
    "unnamed_speaker": "unnamed",
    "possible_duplicate": "duplicate_candidate",
    "duplicate": "duplicate_candidate",
}
_EVIDENCE_CATEGORY_ALIASES = {
    "identity": "name",
    "identity_name": "name",
    "explicit_identity": "name",
    "speech": "speaking",
    "dialogue": "speaking",
    "direct_speech": "speaking",
    "non_speaker": "speaking",
    "speaking_status": "speaking",
    "appearance": "visual",
    "description": "other",
}
_EVIDENCE_BASIS_ALIASES = {
    "explicit_name": "explicit",
    "explicit_statement": "explicit",
    "direct_attribution": "explicit",
    "direct_speech": "explicit",
    "contextual_reference": "inferred",
    "contextual": "inferred",
    "inference": "inferred",
    "inferred_from_context": "inferred",
}


def _normalize_entity_kind(
    value: Any,
    *,
    speaking_status: str,
    label: str,
) -> str:
    normalized = _validate_enum_text(
        value,
        label,
        {
            "character",
            "group",
            "creature",
            "narrator_role",
            "named_non_speaker",
            "unknown",
        },
        aliases=_ENTITY_KIND_ALIASES,
    )

    if (
        normalized == "named_non_speaker"
        and speaking_status in {"speaker", "narrator"}
    ):
        return "character"

    return normalized


def _normalize_evidence_basis(
    value: Any,
    label: str,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(
            f"{label} must be a nonempty string"
        )

    key = value.strip().casefold().replace("-", "_").replace(" ", "_")

    if key.startswith("explicit"):
        return "explicit"

    if key.startswith("inferred"):
        return "inferred"

    return _validate_enum_text(
        value,
        label,
        {"explicit", "inferred"},
        aliases=_EVIDENCE_BASIS_ALIASES,
    )


def _validate_roster_evidence(
    value: Any,
    label: str,
) -> dict[str, Any]:
    evidence = _require_dict(value, label)
    _require_exact_keys(
        evidence,
        {
            "quote",
            "start_char",
            "end_char",
            "category",
            "confidence",
            "basis",
        },
        label,
    )

    quote = evidence["quote"]
    start = evidence["start_char"]
    end = evidence["end_char"]

    if not isinstance(quote, str) or not quote:
        raise ContractValidationError(
            f"{label}.quote must be nonempty exact text"
        )

    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or start < 0
    ):
        raise ContractValidationError(
            f"{label}.start_char must be a non-negative integer"
        )

    if (
        not isinstance(end, int)
        or isinstance(end, bool)
        or end <= start
    ):
        raise ContractValidationError(
            f"{label}.end_char must be an integer greater than start_char"
        )

    return {
        "quote": quote,
        "start_char": start,
        "end_char": end,
        "category": _validate_enum_text(
            evidence["category"],
            f"{label}.category",
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
            },
            aliases=_EVIDENCE_CATEGORY_ALIASES,
        ),
        "confidence": _validate_contract_confidence(
            evidence["confidence"],
            f"{label}.confidence",
        ),
        "basis": _normalize_evidence_basis(
            evidence["basis"],
            f"{label}.basis",
        ),
    }


def validate_roster_discovery(
    value: Any,
) -> dict[str, Any]:
    if (
        isinstance(value, dict)
        and set(value) == {"roster_discovery"}
        and isinstance(value["roster_discovery"], dict)
    ):
        value = value["roster_discovery"]

    obj = _require_dict(
        value,
        "Roster discovery response",
    )
    _require_exact_keys(
        obj,
        {"entities", "warnings"},
        "Roster discovery response",
    )

    raw_entities = obj["entities"]

    if not isinstance(raw_entities, list):
        raise ContractValidationError(
            "Roster discovery entities must be an array"
        )

    entities = []
    expected_keys = {
        "identity_seed",
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
        "voice_clues",
        "sample_lines",
        "confidence",
        "resolution_status",
        "unresolved_questions",
        "evidence",
    }

    for index, raw_entity in enumerate(raw_entities):
        label = f"Roster discovery entity {index}"
        entity = _require_dict(raw_entity, label)
        _require_exact_keys(entity, expected_keys, label)

        resolution_status = _validate_enum_text(
            entity["resolution_status"],
            f"{label}.resolution_status",
            {
                "resolved",
                "unresolved",
                "unnamed",
                "duplicate_candidate",
            },
            aliases=_RESOLUTION_STATUS_ALIASES,
        )
        identity_seed = entity["identity_seed"]
        canonical_name = entity["canonical_name"]
        display_name = entity["display_name"]

        if (
            canonical_name is None
            and resolution_status in {"unresolved", "unnamed"}
        ):
            canonical_name = ""

        if (
            not isinstance(identity_seed, str)
            or not identity_seed.strip()
        ):
            raise ContractValidationError(
                f"{label}.identity_seed must be nonempty text"
            )

        if not isinstance(canonical_name, str):
            raise ContractValidationError(
                f"{label}.canonical_name must be text"
            )

        if (
            resolution_status not in {"unresolved", "unnamed"}
            and not canonical_name.strip()
        ):
            raise ContractValidationError(
                f"{label}.canonical_name must not be empty for "
                f"{resolution_status} identities"
            )

        if (
            not isinstance(display_name, str)
            or not display_name.strip()
        ):
            raise ContractValidationError(
                f"{label}.display_name must be nonempty text"
            )

        raw_evidence = entity["evidence"]
        if not isinstance(raw_evidence, list) or not raw_evidence:
            raise ContractValidationError(
                f"{label}.evidence must be a nonempty array"
            )

        speaking_status = _validate_enum_text(
            entity["speaking_status"],
            f"{label}.speaking_status",
            {
                "speaker",
                "non_speaker",
                "uncertain",
                "narrator",
            },
            aliases=_SPEAKING_STATUS_ALIASES,
        )
        entity_kind = _normalize_entity_kind(
            entity["entity_kind"],
            speaking_status=speaking_status,
            label=f"{label}.entity_kind",
        )

        entities.append(
            {
                "identity_seed": identity_seed.strip(),
                "canonical_name": canonical_name.strip(),
                "display_name": display_name.strip(),
                "entity_kind": entity_kind,
                "speaking_status": speaking_status,
                "titles": _validate_string_list(
                    entity["titles"],
                    f"{label}.titles",
                ),
                "aliases": _validate_string_list(
                    entity["aliases"],
                    f"{label}.aliases",
                ),
                "nicknames": _validate_string_list(
                    entity["nicknames"],
                    f"{label}.nicknames",
                ),
                "pronouns": _validate_string_list(
                    entity["pronouns"],
                    f"{label}.pronouns",
                ),
                "species": _validate_string_list(
                    entity["species"],
                    f"{label}.species",
                ),
                "relationships": _validate_string_list(
                    entity["relationships"],
                    f"{label}.relationships",
                ),
                "voice_clues": _validate_string_list(
                    entity["voice_clues"],
                    f"{label}.voice_clues",
                ),
                "sample_lines": _validate_exact_string_list(
                    entity["sample_lines"],
                    f"{label}.sample_lines",
                ),
                "confidence": _validate_contract_confidence(
                    entity["confidence"],
                    f"{label}.confidence",
                ),
                "resolution_status": resolution_status,
                "unresolved_questions": _validate_string_list(
                    entity["unresolved_questions"],
                    f"{label}.unresolved_questions",
                ),
                "evidence": [
                    _validate_roster_evidence(
                        item,
                        f"{label}.evidence[{evidence_index}]",
                    )
                    for evidence_index, item in enumerate(
                        raw_evidence
                    )
                ],
            }
        )

    return {
        "entities": entities,
        "warnings": _validate_string_list(
            obj["warnings"],
            "Roster discovery warnings",
        ),
    }


def validate_roster_reconciliation(
    value: Any,
) -> dict[str, Any]:
    if (
        isinstance(value, dict)
        and set(value) == {"roster_reconciliation"}
        and isinstance(value["roster_reconciliation"], dict)
    ):
        value = value["roster_reconciliation"]

    obj = _require_dict(
        value,
        "Roster reconciliation response",
    )
    _require_exact_keys(
        obj,
        {
            "entries",
            "duplicate_candidates",
            "excluded_observation_ids",
            "warnings",
        },
        "Roster reconciliation response",
    )

    if not isinstance(obj["entries"], list):
        raise ContractValidationError(
            "Roster reconciliation entries must be an array"
        )

    entries = []
    entry_keys = {
        "identity_seed",
        "canonical_name",
        "display_name",
        "entity_kind",
        "speaking_status",
        "observation_ids",
        "confidence",
        "resolution_status",
        "possible_duplicate_seeds",
        "mistaken_merge_risk",
        "unresolved_questions",
    }

    for index, raw_entry in enumerate(obj["entries"]):
        label = f"Roster reconciliation entry {index}"
        entry = _require_dict(raw_entry, label)
        _require_exact_keys(entry, entry_keys, label)
        resolution_status = _validate_enum_text(
            entry["resolution_status"],
            f"{label}.resolution_status",
            {
                "resolved",
                "unresolved",
                "unnamed",
                "duplicate_candidate",
            },
            aliases=_RESOLUTION_STATUS_ALIASES,
        )
        identity_seed = entry["identity_seed"]
        canonical_name = entry["canonical_name"]
        display_name = entry["display_name"]

        if (
            canonical_name is None
            and resolution_status in {"unresolved", "unnamed"}
        ):
            canonical_name = ""
        observation_ids = _validate_string_list(
            entry["observation_ids"],
            f"{label}.observation_ids",
        )

        if len(observation_ids) != len(set(observation_ids)):
            raise ContractValidationError(
                f"{label}.observation_ids must not contain duplicates"
            )

        if (
            not isinstance(identity_seed, str)
            or not identity_seed.strip()
        ):
            raise ContractValidationError(
                f"{label}.identity_seed must be nonempty text"
            )

        if not isinstance(canonical_name, str):
            raise ContractValidationError(
                f"{label}.canonical_name must be text"
            )

        if (
            resolution_status not in {"unresolved", "unnamed"}
            and not canonical_name.strip()
        ):
            raise ContractValidationError(
                f"{label}.canonical_name must not be empty"
            )

        if (
            not isinstance(display_name, str)
            or not display_name.strip()
        ):
            raise ContractValidationError(
                f"{label}.display_name must be nonempty text"
            )

        if not observation_ids:
            raise ContractValidationError(
                f"{label}.observation_ids must not be empty"
            )

        mistaken_merge_risk = entry[
            "mistaken_merge_risk"
        ]
        if not isinstance(mistaken_merge_risk, bool):
            raise ContractValidationError(
                f"{label}.mistaken_merge_risk must be boolean"
            )

        speaking_status = _validate_enum_text(
            entry["speaking_status"],
            f"{label}.speaking_status",
            {
                "speaker",
                "non_speaker",
                "uncertain",
                "narrator",
            },
            aliases=_SPEAKING_STATUS_ALIASES,
        )
        entity_kind = _normalize_entity_kind(
            entry["entity_kind"],
            speaking_status=speaking_status,
            label=f"{label}.entity_kind",
        )

        entries.append(
            {
                "identity_seed": identity_seed.strip(),
                "canonical_name": canonical_name.strip(),
                "display_name": display_name.strip(),
                "entity_kind": entity_kind,
                "speaking_status": speaking_status,
                "observation_ids": observation_ids,
                "confidence": _validate_contract_confidence(
                    entry["confidence"],
                    f"{label}.confidence",
                ),
                "resolution_status": resolution_status,
                "possible_duplicate_seeds": (
                    _validate_string_list(
                        entry["possible_duplicate_seeds"],
                        f"{label}.possible_duplicate_seeds",
                    )
                ),
                "mistaken_merge_risk": mistaken_merge_risk,
                "unresolved_questions": _validate_string_list(
                    entry["unresolved_questions"],
                    f"{label}.unresolved_questions",
                ),
            }
        )

    if not isinstance(obj["duplicate_candidates"], list):
        raise ContractValidationError(
            "Roster duplicate_candidates must be an array"
        )

    duplicates = []

    for index, raw_candidate in enumerate(
        obj["duplicate_candidates"]
    ):
        label = f"Roster reconciliation duplicate {index}"
        candidate = _require_dict(raw_candidate, label)
        _require_exact_keys(
            candidate,
            {
                "identity_seeds",
                "reason",
                "confidence",
                "observation_ids",
            },
            label,
        )
        seeds = _validate_string_list(
            candidate["identity_seeds"],
            f"{label}.identity_seeds",
        )

        if len(seeds) != 2 or seeds[0] == seeds[1]:
            raise ContractValidationError(
                f"{label}.identity_seeds must contain two distinct values"
            )

        reason = candidate["reason"]
        if not isinstance(reason, str) or not reason.strip():
            raise ContractValidationError(
                f"{label}.reason must be nonempty text"
            )

        observation_ids = _validate_string_list(
            candidate["observation_ids"],
            f"{label}.observation_ids",
        )

        if not observation_ids:
            raise ContractValidationError(
                f"{label}.observation_ids must not be empty"
            )

        if len(observation_ids) != len(set(observation_ids)):
            raise ContractValidationError(
                f"{label}.observation_ids must not contain duplicates"
            )

        duplicates.append(
            {
                "identity_seeds": seeds,
                "reason": reason.strip(),
                "confidence": _validate_contract_confidence(
                    candidate["confidence"],
                    f"{label}.confidence",
                ),
                "observation_ids": observation_ids,
            }
        )

    return {
        "entries": entries,
        "duplicate_candidates": duplicates,
        "excluded_observation_ids": _validate_string_list(
            obj["excluded_observation_ids"],
            "Roster reconciliation excluded_observation_ids",
        ),
        "warnings": _validate_string_list(
            obj["warnings"],
            "Roster reconciliation warnings",
        ),
    }


def validate_advanced_discovery(
    value: Any,
) -> dict[str, dict[str, Any]]:
    # Compatibility with an older list-based response shape.
    if (
        isinstance(value, dict)
        and set(value.keys()) == {"characters"}
        and isinstance(value["characters"], list)
    ):
        converted: dict[str, dict[str, Any]] = {}

        for index, item in enumerate(value["characters"]):
            obj = _require_dict(item, f"Character discovery item {index}")
            name = str(
                obj.get("name")
                or obj.get("speaker")
                or obj.get("speaker_label")
                or ""
            ).strip()

            if not name:
                raise ContractValidationError(
                    f"Character discovery item {index} has no name"
                )

            converted[name] = {
                key: obj.get(key, [])
                for key in (
                    "aliases",
                    "features",
                    "personality",
                    "voice_clues",
                    "relationships",
                    "evidence",
                    "sample_lines",
                )
            }

        value = converted

    obj = _require_dict(value, "Advanced discovery response")
    normalized: dict[str, dict[str, Any]] = {}

    expected_keys = {
        "aliases",
        "features",
        "personality",
        "voice_clues",
        "relationships",
        "evidence",
        "sample_lines",
    }

    for speaker, raw_data in obj.items():
        if not isinstance(speaker, str) or not speaker.strip():
            raise ContractValidationError(
                "Advanced discovery contains an invalid speaker key"
            )

        data = _require_dict(
            raw_data,
            f"Advanced discovery for {speaker!r}",
        )

        # Missing list fields are normalized to empty lists for compatibility.
        extra = set(data.keys()) - expected_keys

        if extra:
            raise ContractValidationError(
                f"Advanced discovery for {speaker!r} has unexpected "
                f"keys: {sorted(extra)}"
            )

        evidence_raw = data.get("evidence", [])

        if not isinstance(evidence_raw, list):
            raise ContractValidationError(
                f"Evidence for {speaker!r} must be an array"
            )

        evidence: list[dict[str, Any]] = []

        for index, raw_evidence in enumerate(evidence_raw):
            evidence_item = _require_dict(
                raw_evidence,
                f"Evidence {index} for {speaker!r}",
            )

            entry_index = evidence_item.get("entry_index")
            quote = evidence_item.get("quote")

            if not isinstance(entry_index, int):
                raise ContractValidationError(
                    f"Evidence {index} for {speaker!r} requires "
                    "an integer entry_index"
                )

            if not isinstance(quote, str):
                raise ContractValidationError(
                    f"Evidence {index} for {speaker!r} requires "
                    "a string quote"
                )

            evidence.append(
                {
                    "entry_index": entry_index,
                    "quote": quote.strip(),
                }
            )

        normalized[speaker.strip()] = {
            "aliases": _validate_string_list(
                data.get("aliases", []),
                f"Aliases for {speaker!r}",
            ),
            "features": _validate_string_list(
                data.get("features", []),
                f"Features for {speaker!r}",
            ),
            "personality": _validate_string_list(
                data.get("personality", []),
                f"Personality for {speaker!r}",
            ),
            "voice_clues": _validate_string_list(
                data.get("voice_clues", []),
                f"Voice clues for {speaker!r}",
            ),
            "relationships": _validate_string_list(
                data.get("relationships", []),
                f"Relationships for {speaker!r}",
            ),
            "evidence": evidence,
            "sample_lines": _validate_string_list(
                data.get("sample_lines", []),
                f"Sample lines for {speaker!r}",
            ),
        }

    return normalized


VALIDATORS: dict[str, Callable[[Any], Any]] = {
    "persona": validate_persona,
    "script": validate_script,
    "review": validate_script,
    "alias": validate_alias_map,
    "advanced_discovery": validate_advanced_discovery,
    "roster_discovery": validate_roster_discovery,
    "roster_reconciliation": validate_roster_reconciliation,
}


def get_schema(contract: str) -> dict[str, Any]:
    try:
        return SCHEMAS[contract]
    except KeyError as exc:
        raise ValueError(f"Unknown LLM contract: {contract!r}") from exc


def validate_contract(contract: str, value: Any) -> Any:
    try:
        validator = VALIDATORS[contract]
    except KeyError as exc:
        raise ValueError(f"Unknown LLM contract: {contract!r}") from exc

    return validator(value)
