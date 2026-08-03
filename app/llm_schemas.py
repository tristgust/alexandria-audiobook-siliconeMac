from __future__ import annotations

import re
from typing import Any, Callable

from backend_render_plan import normalize_backend_render_plan
from character_visuals import PROFILE_BUCKETS, VISUAL_SCOPES


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


PERSONA_CATALOG_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "speaker": {"type": "string"},
        "description": {"type": "string"},
        "ref_text": {"type": "string"},
    },
    "required": ["speaker", "description", "ref_text"],
    "additionalProperties": False,
}


PERSONA_CATALOG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "personas": {
            "type": "array",
            "items": PERSONA_CATALOG_ITEM_SCHEMA,
        },
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["personas", "warnings"],
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


FISH_INLINE_CUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "anchor": {
            "type": "string",
            "enum": ["start", "before_phrase", "after_phrase", "end"],
        },
        "tag": {"type": "string"},
        "kind": {
            "type": "string",
            "enum": ["delivery", "reaction", "reset"],
        },
        "phrase": {"type": "string"},
        "occurrence": {"type": "integer", "minimum": 1},
    },
    "required": ["anchor", "tag", "kind"],
    "additionalProperties": False,
}


BACKEND_RENDER_PLAN_ENTRY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "index": {"type": "integer", "minimum": 0},
        "chunk_id": {"type": "string"},
        "speaker": {"type": "string"},
        "text_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "qwen_instruction": {"type": "string"},
        "fish_direction": {"type": "string"},
        "fish_cues": {
            "type": "array",
            "maxItems": 8,
            "items": FISH_INLINE_CUE_SCHEMA,
        },
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "index",
        "chunk_id",
        "speaker",
        "text_sha256",
        "qwen_instruction",
        "fish_direction",
        "fish_cues",
        "warnings",
    ],
    "additionalProperties": False,
}


BACKEND_RENDER_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "integer", "const": 1},
        "script_fingerprint": {
            "type": "string",
            "pattern": "^[0-9a-f]{64}$",
        },
        "chunks_fingerprint": {
            "type": "string",
            "pattern": "^[0-9a-f]{64}$",
        },
        "entries": {
            "type": "array",
            "minItems": 1,
            "items": BACKEND_RENDER_PLAN_ENTRY_SCHEMA,
        },
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "schema_version",
        "script_fingerprint",
        "chunks_fingerprint",
        "entries",
        "warnings",
    ],
    "additionalProperties": False,
}


_NULLABLE_STRING_SCHEMA: dict[str, Any] = {
    "anyOf": [{"type": "string"}, {"type": "null"}],
}


PRONUNCIATION_GUIDANCE_ENTRY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "chunk_index": {"type": "integer", "minimum": 0},
        "start_char": {"type": "integer", "minimum": 0},
        "end_char": {"type": "integer", "minimum": 1},
        "original": {"type": "string"},
        "chunk_text_sha256": {
            "type": "string",
            "pattern": "^[0-9a-f]{64}$",
        },
        "spoken_form": _NULLABLE_STRING_SCHEMA,
        "phonetic_hint": _NULLABLE_STRING_SCHEMA,
        "languages": {"type": "array", "items": {"type": "string"}},
        "character_labels": {
            "type": "array",
            "items": {"type": "string"},
        },
        "voice_ids": {"type": "array", "items": {"type": "string"}},
        "engine_ids": {"type": "array", "items": {"type": "string"}},
        "engine_source": {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "engine": _NULLABLE_STRING_SCHEMA,
                "revision": _NULLABLE_STRING_SCHEMA,
                "phoneme_alphabet": _NULLABLE_STRING_SCHEMA,
            },
            "required": [
                "kind",
                "engine",
                "revision",
                "phoneme_alphabet",
            ],
            "additionalProperties": False,
        },
        "fallback": {
            "type": "object",
            "properties": {
                "strategy": {
                    "type": "string",
                    "enum": ["bypass", "spoken_form"],
                },
                "spoken_form": _NULLABLE_STRING_SCHEMA,
                "reason": _NULLABLE_STRING_SCHEMA,
            },
            "required": ["strategy", "spoken_form", "reason"],
            "additionalProperties": False,
        },
        "rationale": {"type": "string"},
    },
    "required": [
        "chunk_index",
        "start_char",
        "end_char",
        "original",
        "chunk_text_sha256",
        "spoken_form",
        "phonetic_hint",
        "languages",
        "character_labels",
        "voice_ids",
        "engine_ids",
        "engine_source",
        "fallback",
        "rationale",
    ],
    "additionalProperties": False,
}


PRONUNCIATION_GUIDANCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "integer", "const": 1},
        "entries": {
            "type": "array",
            "items": PRONUNCIATION_GUIDANCE_ENTRY_SCHEMA,
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["schema_version", "entries", "warnings"],
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


VISUAL_DISCOVERY_OBSERVATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "character_id": {"type": "string"},
        "category": {
            "type": "string",
            "enum": list(PROFILE_BUCKETS),
        },
        "detail": {"type": "string"},
        "scope": {
            "type": "string",
            "enum": sorted(VISUAL_SCOPES),
        },
        "certainty": {"type": "number"},
        "basis": {
            "type": "string",
            "enum": ["explicit", "inferred"],
        },
        "quote": {"type": "string"},
        "start_char": {"type": "integer"},
        "end_char": {"type": "integer"},
    },
    "required": [
        "character_id",
        "category",
        "detail",
        "scope",
        "certainty",
        "basis",
        "quote",
        "start_char",
        "end_char",
    ],
    "additionalProperties": False,
}

VISUAL_DISCOVERY_SCHEMA = {
    "type": "object",
    "properties": {
        "observations": {
            "type": "array",
            "items": VISUAL_DISCOVERY_OBSERVATION_SCHEMA,
        },
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["observations", "warnings"],
    "additionalProperties": False,
}

VISUAL_RECONCILIATION_FACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "detail": {"type": "string"},
        "certainty": {"type": "number"},
        "observation_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["detail", "certainty", "observation_ids"],
    "additionalProperties": False,
}

VISUAL_RECONCILIATION_VARIANT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "label": {"type": "string"},
        "scope": {
            "type": "string",
            "enum": sorted(VISUAL_SCOPES - {"stable"}),
        },
        "details": {
            "type": "array",
            "items": {"type": "string"},
        },
        "observation_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "label",
        "scope",
        "details",
        "observation_ids",
    ],
    "additionalProperties": False,
}

VISUAL_RECONCILIATION_CONFLICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": list(PROFILE_BUCKETS),
        },
        "details": {
            "type": "array",
            "items": {"type": "string"},
        },
        "observation_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["category", "details", "observation_ids"],
    "additionalProperties": False,
}

VISUAL_RECONCILIATION_UNKNOWN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": list(PROFILE_BUCKETS),
        },
        "question": {"type": "string"},
    },
    "required": ["category", "question"],
    "additionalProperties": False,
}

VISUAL_RECONCILIATION_CHARACTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "character_id": {"type": "string"},
        "profile": {
            "type": "object",
            "properties": {
                bucket: {
                    "type": "array",
                    "items": VISUAL_RECONCILIATION_FACT_SCHEMA,
                }
                for bucket in PROFILE_BUCKETS
            },
            "required": list(PROFILE_BUCKETS),
            "additionalProperties": False,
        },
        "variants": {
            "type": "array",
            "items": VISUAL_RECONCILIATION_VARIANT_SCHEMA,
        },
        "conflicts": {
            "type": "array",
            "items": VISUAL_RECONCILIATION_CONFLICT_SCHEMA,
        },
        "unknowns": {
            "type": "array",
            "items": VISUAL_RECONCILIATION_UNKNOWN_SCHEMA,
        },
    },
    "required": [
        "character_id",
        "profile",
        "variants",
        "conflicts",
        "unknowns",
    ],
    "additionalProperties": False,
}

VISUAL_RECONCILIATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "characters": {
            "type": "array",
            "items": VISUAL_RECONCILIATION_CHARACTER_SCHEMA,
        },
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["characters", "warnings"],
    "additionalProperties": False,
}


CAST_VOICE_TRAIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "value": {"type": "string"},
        "basis": {
            "type": "string",
            "enum": [
                "explicit",
                "inferred",
                "casting_recommendation",
                "unknown",
            ],
        },
        "evidence_quotes": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["value", "basis", "evidence_quotes"],
    "additionalProperties": False,
}


CAST_VOICE_DOSSIER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "speaker": {"type": "string"},
        "persona_summary": {"type": "string"},
        "designed_voice_description": {"type": "string"},
        "ref_text": {"type": "string"},
        "vocal_age_impression": CAST_VOICE_TRAIT_SCHEMA,
        "pitch": CAST_VOICE_TRAIT_SCHEMA,
        "weight_and_resonance": CAST_VOICE_TRAIT_SCHEMA,
        "texture_and_timbre": CAST_VOICE_TRAIT_SCHEMA,
        "accent_and_language": CAST_VOICE_TRAIT_SCHEMA,
        "cadence_and_rhythm": CAST_VOICE_TRAIT_SCHEMA,
        "energy_range": CAST_VOICE_TRAIT_SCHEMA,
        "emotional_range": CAST_VOICE_TRAIT_SCHEMA,
        "casting_guidance": CAST_VOICE_TRAIT_SCHEMA,
        "uncertainties": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "speaker",
        "persona_summary",
        "designed_voice_description",
        "ref_text",
        "vocal_age_impression",
        "pitch",
        "weight_and_resonance",
        "texture_and_timbre",
        "accent_and_language",
        "cadence_and_rhythm",
        "energy_range",
        "emotional_range",
        "casting_guidance",
        "uncertainties",
    ],
    "additionalProperties": False,
}


CAST_VISUAL_OBSERVATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "observation_id": {"type": "string"},
        **VISUAL_DISCOVERY_OBSERVATION_SCHEMA["properties"],
    },
    "required": [
        "observation_id",
        *VISUAL_DISCOVERY_OBSERVATION_SCHEMA["required"],
    ],
    "additionalProperties": False,
}


COMPLETE_CAST_DOSSIER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "selected_sections": {
            "type": "object",
            "properties": {
                "roster_and_relationships": {"type": "boolean"},
                "voice_personas_and_designs": {"type": "boolean"},
                "visual_dossiers": {"type": "boolean"},
            },
            "required": [
                "roster_and_relationships",
                "voice_personas_and_designs",
                "visual_dossiers",
            ],
            "additionalProperties": False,
        },
        "roster": {
            "anyOf": [ROSTER_DISCOVERY_SCHEMA, {"type": "null"}],
        },
        "voice_dossiers": {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "voices": {
                            "type": "array",
                            "items": CAST_VOICE_DOSSIER_SCHEMA,
                        },
                        "warnings": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["voices", "warnings"],
                    "additionalProperties": False,
                },
                {"type": "null"},
            ],
        },
        "visual_observations": {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "observations": {
                            "type": "array",
                            "items": CAST_VISUAL_OBSERVATION_SCHEMA,
                        },
                        "warnings": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["observations", "warnings"],
                    "additionalProperties": False,
                },
                {"type": "null"},
            ],
        },
        "visual_dossiers": {
            "anyOf": [VISUAL_RECONCILIATION_SCHEMA, {"type": "null"}],
        },
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "selected_sections",
        "roster",
        "voice_dossiers",
        "visual_observations",
        "visual_dossiers",
        "warnings",
    ],
    "additionalProperties": False,
}


SCHEMAS: dict[str, dict[str, Any]] = {
    "persona": PERSONA_SCHEMA,
    "persona_catalog": PERSONA_CATALOG_SCHEMA,
    "script": SCRIPT_SCHEMA,
    "review": SCRIPT_SCHEMA,
    "alias": ALIAS_SCHEMA,
    "advanced_discovery": ADVANCED_DISCOVERY_SCHEMA,
    "roster_discovery": ROSTER_DISCOVERY_SCHEMA,
    "roster_reconciliation": ROSTER_RECONCILIATION_SCHEMA,
    "visual_discovery": VISUAL_DISCOVERY_SCHEMA,
    "visual_reconciliation": VISUAL_RECONCILIATION_SCHEMA,
    "complete_cast_dossier": COMPLETE_CAST_DOSSIER_SCHEMA,
    "backend_render_plan": BACKEND_RENDER_PLAN_SCHEMA,
    "pronunciation_guidance": PRONUNCIATION_GUIDANCE_SCHEMA,
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


def validate_persona_catalog(value: Any) -> dict[str, Any]:
    obj = _require_dict(value, "Persona catalog response")
    _require_exact_keys(
        obj,
        {"personas", "warnings"},
        "Persona catalog response",
    )
    raw_personas = obj["personas"]
    if not isinstance(raw_personas, list) or not raw_personas:
        raise ContractValidationError(
            "Persona catalog personas must be a nonempty array"
        )
    personas: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw_persona in enumerate(raw_personas):
        persona = _require_dict(
            raw_persona,
            f"Persona catalog item {index}",
        )
        _require_exact_keys(
            persona,
            {"speaker", "description", "ref_text"},
            f"Persona catalog item {index}",
        )
        speaker = persona["speaker"]
        description = persona["description"]
        ref_text = persona["ref_text"]
        if not isinstance(speaker, str) or not speaker.strip():
            raise ContractValidationError(
                f"Persona catalog item {index} speaker must be nonempty text"
            )
        speaker = speaker.strip()
        if speaker != speaker.upper():
            raise ContractValidationError(
                f"Persona catalog item {index} speaker must be uppercase"
            )
        if speaker in seen:
            raise ContractValidationError(
                f"Persona catalog speaker {speaker!r} appears more than once"
            )
        seen.add(speaker)
        if not isinstance(description, str) or not description.strip():
            raise ContractValidationError(
                f"Persona catalog item {index} description must be nonempty text"
            )
        if not isinstance(ref_text, str) or not ref_text.strip():
            raise ContractValidationError(
                f"Persona catalog item {index} ref_text must be nonempty text"
            )
        personas.append(
            {
                "speaker": speaker,
                "description": description.strip(),
                "ref_text": ref_text.strip(),
            }
        )
    warnings = obj["warnings"]
    if not isinstance(warnings, list) or any(
        not isinstance(item, str) or not item.strip()
        for item in warnings
    ):
        raise ContractValidationError(
            "Persona catalog warnings must be an array of nonempty strings"
        )
    return {
        "personas": personas,
        "warnings": [item.strip() for item in warnings],
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


def validate_backend_render_plan(value: Any) -> dict[str, Any]:
    try:
        return normalize_backend_render_plan(value)
    except ValueError as exc:
        raise ContractValidationError(str(exc)) from exc


def validate_pronunciation_guidance(value: Any) -> dict[str, Any]:
    obj = _require_dict(value, "Pronunciation guidance response")
    _require_exact_keys(
        obj,
        {"schema_version", "entries", "warnings"},
        "Pronunciation guidance response",
    )
    if obj["schema_version"] != 1:
        raise ContractValidationError(
            "Pronunciation guidance schema_version must be 1"
        )
    raw_entries = obj["entries"]
    if not isinstance(raw_entries, list):
        raise ContractValidationError(
            "Pronunciation guidance entries must be an array"
        )

    def optional_text(raw: Any, label: str) -> str | None:
        if raw is None:
            return None
        if not isinstance(raw, str) or not raw.strip():
            raise ContractValidationError(
                f"{label} must be null or nonempty text"
            )
        return raw.strip()

    def string_list(raw: Any, label: str) -> list[str]:
        if not isinstance(raw, list):
            raise ContractValidationError(f"{label} must be an array")
        values: list[str] = []
        seen: set[str] = set()
        for index, item in enumerate(raw):
            if not isinstance(item, str) or not item.strip():
                raise ContractValidationError(
                    f"{label} item {index} must be nonempty text"
                )
            value = item.strip()
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            values.append(value)
        return values

    normalized: list[dict[str, Any]] = []
    anchors: list[tuple[int, int, int]] = []
    expected_keys = {
        "chunk_index",
        "start_char",
        "end_char",
        "original",
        "chunk_text_sha256",
        "spoken_form",
        "phonetic_hint",
        "languages",
        "character_labels",
        "voice_ids",
        "engine_ids",
        "engine_source",
        "fallback",
        "rationale",
    }
    for index, raw_entry in enumerate(raw_entries):
        entry = _require_dict(
            raw_entry,
            f"Pronunciation guidance entry {index}",
        )
        _require_exact_keys(
            entry,
            expected_keys,
            f"Pronunciation guidance entry {index}",
        )
        chunk_index = entry["chunk_index"]
        start_char = entry["start_char"]
        end_char = entry["end_char"]
        if (
            not isinstance(chunk_index, int)
            or isinstance(chunk_index, bool)
            or chunk_index < 0
            or not isinstance(start_char, int)
            or isinstance(start_char, bool)
            or start_char < 0
            or not isinstance(end_char, int)
            or isinstance(end_char, bool)
            or end_char <= start_char
        ):
            raise ContractValidationError(
                f"Pronunciation guidance entry {index} has an invalid source span"
            )
        original = entry["original"]
        if not isinstance(original, str) or not original:
            raise ContractValidationError(
                f"Pronunciation guidance entry {index} original must be nonempty text"
            )
        chunk_hash = entry["chunk_text_sha256"]
        if not isinstance(chunk_hash, str) or not re.fullmatch(
            r"[0-9a-f]{64}",
            chunk_hash,
        ):
            raise ContractValidationError(
                f"Pronunciation guidance entry {index} requires a lowercase SHA-256 chunk hash"
            )
        spoken_form = optional_text(
            entry["spoken_form"],
            f"Pronunciation guidance entry {index} spoken_form",
        )
        phonetic_hint = optional_text(
            entry["phonetic_hint"],
            f"Pronunciation guidance entry {index} phonetic_hint",
        )
        if spoken_form is None and phonetic_hint is None:
            raise ContractValidationError(
                f"Pronunciation guidance entry {index} needs spoken_form or phonetic_hint"
            )
        engine_source = _require_dict(
            entry["engine_source"],
            f"Pronunciation guidance entry {index} engine_source",
        )
        _require_exact_keys(
            engine_source,
            {"kind", "engine", "revision", "phoneme_alphabet"},
            f"Pronunciation guidance entry {index} engine_source",
        )
        kind = engine_source["kind"]
        if not isinstance(kind, str) or not kind.strip():
            raise ContractValidationError(
                f"Pronunciation guidance entry {index} engine_source.kind must be nonempty text"
            )
        fallback = _require_dict(
            entry["fallback"],
            f"Pronunciation guidance entry {index} fallback",
        )
        _require_exact_keys(
            fallback,
            {"strategy", "spoken_form", "reason"},
            f"Pronunciation guidance entry {index} fallback",
        )
        fallback_strategy = fallback["strategy"]
        if fallback_strategy not in {"bypass", "spoken_form"}:
            raise ContractValidationError(
                f"Pronunciation guidance entry {index} fallback.strategy is invalid"
            )
        fallback_spoken_form = optional_text(
            fallback["spoken_form"],
            f"Pronunciation guidance entry {index} fallback.spoken_form",
        )
        if fallback_strategy == "spoken_form" and fallback_spoken_form is None:
            raise ContractValidationError(
                f"Pronunciation guidance entry {index} spoken-form fallback needs text"
            )
        rationale = entry["rationale"]
        if not isinstance(rationale, str) or not rationale.strip():
            raise ContractValidationError(
                f"Pronunciation guidance entry {index} rationale must be nonempty text"
            )
        anchor = (chunk_index, start_char, end_char)
        for prior_chunk, prior_start, prior_end in anchors:
            if (
                prior_chunk == chunk_index
                and max(prior_start, start_char) < min(prior_end, end_char)
            ):
                raise ContractValidationError(
                    "Pronunciation guidance entries may not overlap in one chunk"
                )
        anchors.append(anchor)
        normalized.append(
            {
                "chunk_index": chunk_index,
                "start_char": start_char,
                "end_char": end_char,
                "original": original,
                "chunk_text_sha256": chunk_hash,
                "spoken_form": spoken_form,
                "phonetic_hint": phonetic_hint,
                "languages": string_list(
                    entry["languages"],
                    f"Pronunciation guidance entry {index} languages",
                ),
                "character_labels": string_list(
                    entry["character_labels"],
                    f"Pronunciation guidance entry {index} character_labels",
                ),
                "voice_ids": string_list(
                    entry["voice_ids"],
                    f"Pronunciation guidance entry {index} voice_ids",
                ),
                "engine_ids": string_list(
                    entry["engine_ids"],
                    f"Pronunciation guidance entry {index} engine_ids",
                ),
                "engine_source": {
                    "kind": kind.strip(),
                    "engine": optional_text(
                        engine_source["engine"],
                        f"Pronunciation guidance entry {index} engine_source.engine",
                    ),
                    "revision": optional_text(
                        engine_source["revision"],
                        f"Pronunciation guidance entry {index} engine_source.revision",
                    ),
                    "phoneme_alphabet": optional_text(
                        engine_source["phoneme_alphabet"],
                        f"Pronunciation guidance entry {index} engine_source.phoneme_alphabet",
                    ),
                },
                "fallback": {
                    "strategy": fallback_strategy,
                    "spoken_form": fallback_spoken_form,
                    "reason": optional_text(
                        fallback["reason"],
                        f"Pronunciation guidance entry {index} fallback.reason",
                    ),
                },
                "rationale": rationale.strip(),
            }
        )
    warnings = string_list(
        obj["warnings"],
        "Pronunciation guidance warnings",
    )
    return {
        "schema_version": 1,
        "entries": normalized,
        "warnings": warnings,
    }


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


def _require_list(
    value: Any,
    label: str,
) -> list[Any]:
    if not isinstance(value, list):
        raise ContractValidationError(f"{label} must be an array")
    return value


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


def _validate_optional_roster_string_list(
    value: Any,
    label: str,
    *,
    warnings: list[str],
    preserve_exact_text: bool = False,
) -> list[str]:
    if not isinstance(value, list):
        warnings.append(
            f"Dropped invalid optional container from {label}; "
            "expected an array of strings."
        )
        return []

    normalized = []
    dropped_count = 0
    for index, item in enumerate(value):
        if not isinstance(item, str):
            dropped_count += 1
            continue
        if preserve_exact_text:
            if not item:
                raise ContractValidationError(
                    f"{label}[{index}] must be nonempty text"
                )
            normalized.append(item)
        elif item.strip():
            normalized.append(item.strip())

    if dropped_count:
        warnings.append(
            f"Dropped {dropped_count} non-string optional member(s) "
            f"from {label}."
        )

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
    ):
        wrapped = value["roster_discovery"]
        if isinstance(wrapped, dict):
            value = wrapped
        elif isinstance(wrapped, list):
            raise ContractValidationError(
                "roster_discovery wrapper lists are unsupported; the top "
                "level must contain entities and warnings."
            )

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

    warnings = _validate_string_list(
        obj["warnings"],
        "Roster discovery warnings",
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
                "titles": _validate_optional_roster_string_list(
                    entity["titles"],
                    f"{label}.titles",
                    warnings=warnings,
                ),
                "aliases": _validate_optional_roster_string_list(
                    entity["aliases"],
                    f"{label}.aliases",
                    warnings=warnings,
                ),
                "nicknames": _validate_optional_roster_string_list(
                    entity["nicknames"],
                    f"{label}.nicknames",
                    warnings=warnings,
                ),
                "pronouns": _validate_optional_roster_string_list(
                    entity["pronouns"],
                    f"{label}.pronouns",
                    warnings=warnings,
                ),
                "species": _validate_optional_roster_string_list(
                    entity["species"],
                    f"{label}.species",
                    warnings=warnings,
                ),
                "relationships": _validate_optional_roster_string_list(
                    entity["relationships"],
                    f"{label}.relationships",
                    warnings=warnings,
                ),
                "voice_clues": _validate_optional_roster_string_list(
                    entity["voice_clues"],
                    f"{label}.voice_clues",
                    warnings=warnings,
                ),
                "sample_lines": _validate_optional_roster_string_list(
                    entity["sample_lines"],
                    f"{label}.sample_lines",
                    warnings=warnings,
                    preserve_exact_text=True,
                ),
                "confidence": _validate_contract_confidence(
                    entity["confidence"],
                    f"{label}.confidence",
                ),
                "resolution_status": resolution_status,
                "unresolved_questions": _validate_optional_roster_string_list(
                    entity["unresolved_questions"],
                    f"{label}.unresolved_questions",
                    warnings=warnings,
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
        "warnings": warnings,
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


def validate_visual_discovery(
    value: Any,
) -> dict[str, Any]:
    if (
        isinstance(value, dict)
        and set(value) == {"visual_discovery"}
        and isinstance(value["visual_discovery"], dict)
    ):
        value = value["visual_discovery"]

    obj = _require_dict(
        value,
        "Visual discovery response",
    )
    _require_exact_keys(
        obj,
        {"observations", "warnings"},
        "Visual discovery response",
    )
    raw_observations = obj["observations"]
    if not isinstance(raw_observations, list):
        raise ContractValidationError(
            "Visual discovery observations must be an array"
        )

    expected = {
        "character_id",
        "category",
        "detail",
        "scope",
        "certainty",
        "basis",
        "quote",
        "start_char",
        "end_char",
    }
    observations = []

    for index, raw_observation in enumerate(
        raw_observations
    ):
        label = f"Visual discovery observation {index}"
        observation = _require_dict(
            raw_observation,
            label,
        )
        _require_exact_keys(
            observation,
            expected,
            label,
        )
        character_id = observation["character_id"]
        detail = observation["detail"]
        quote = observation["quote"]
        start = observation["start_char"]
        end = observation["end_char"]

        for field_name, field_value in (
            ("character_id", character_id),
            ("detail", detail),
            ("quote", quote),
        ):
            if (
                not isinstance(field_value, str)
                or not field_value.strip()
            ):
                raise ContractValidationError(
                    f"{label}.{field_name} must be nonempty text"
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
                f"{label}.end_char must be greater than start_char"
            )

        observations.append(
            {
                "character_id": character_id.strip(),
                "category": _validate_enum_text(
                    observation["category"],
                    f"{label}.category",
                    set(PROFILE_BUCKETS),
                ),
                "detail": detail.strip(),
                "scope": _validate_enum_text(
                    observation["scope"],
                    f"{label}.scope",
                    set(VISUAL_SCOPES),
                ),
                "certainty": _validate_contract_confidence(
                    observation["certainty"],
                    f"{label}.certainty",
                ),
                "basis": _validate_enum_text(
                    observation["basis"],
                    f"{label}.basis",
                    {"explicit", "inferred"},
                ),
                "quote": quote,
                "start_char": start,
                "end_char": end,
            }
        )

    return {
        "observations": observations,
        "warnings": _validate_string_list(
            obj["warnings"],
            "Visual discovery warnings",
        ),
    }


def _validate_visual_fact_contract(
    value: Any,
    label: str,
) -> dict[str, Any]:
    fact = _require_dict(value, label)
    _require_exact_keys(
        fact,
        {"detail", "certainty", "observation_ids"},
        label,
    )
    detail = fact["detail"]
    if not isinstance(detail, str) or not detail.strip():
        raise ContractValidationError(
            f"{label}.detail must be nonempty text"
        )
    observation_ids = _validate_string_list(
        fact["observation_ids"],
        f"{label}.observation_ids",
    )
    if not observation_ids:
        raise ContractValidationError(
            f"{label}.observation_ids must not be empty"
        )
    return {
        "detail": detail.strip(),
        "certainty": _validate_contract_confidence(
            fact["certainty"],
            f"{label}.certainty",
        ),
        "observation_ids": observation_ids,
    }


def validate_visual_reconciliation(
    value: Any,
) -> dict[str, Any]:
    if (
        isinstance(value, dict)
        and set(value) == {"visual_reconciliation"}
        and isinstance(value["visual_reconciliation"], dict)
    ):
        value = value["visual_reconciliation"]

    obj = _require_dict(
        value,
        "Visual reconciliation response",
    )
    _require_exact_keys(
        obj,
        {"characters", "warnings"},
        "Visual reconciliation response",
    )
    raw_characters = obj["characters"]
    if not isinstance(raw_characters, list):
        raise ContractValidationError(
            "Visual reconciliation characters must be an array"
        )

    characters = []
    seen_character_ids = set()

    for index, raw_character in enumerate(raw_characters):
        label = f"Visual reconciliation character {index}"
        character = _require_dict(
            raw_character,
            label,
        )
        _require_exact_keys(
            character,
            {
                "character_id",
                "profile",
                "variants",
                "conflicts",
                "unknowns",
            },
            label,
        )
        character_id = character["character_id"]
        if (
            not isinstance(character_id, str)
            or not character_id.strip()
        ):
            raise ContractValidationError(
                f"{label}.character_id must be nonempty text"
            )
        character_id = character_id.strip()
        if character_id in seen_character_ids:
            raise ContractValidationError(
                "Visual reconciliation character IDs must be unique"
            )
        seen_character_ids.add(character_id)

        profile_raw = _require_dict(
            character["profile"],
            f"{label}.profile",
        )
        _require_exact_keys(
            profile_raw,
            set(PROFILE_BUCKETS),
            f"{label}.profile",
        )
        profile = {
            bucket: [
                _validate_visual_fact_contract(
                    item,
                    f"{label}.profile.{bucket}[{fact_index}]",
                )
                for fact_index, item in enumerate(
                    profile_raw[bucket]
                    if isinstance(
                        profile_raw[bucket],
                        list,
                    )
                    else (_ for _ in ()).throw(
                        ContractValidationError(
                            f"{label}.profile.{bucket} must be an array"
                        )
                    )
                )
            ]
            for bucket in PROFILE_BUCKETS
        }

        variants = []
        raw_variants = character["variants"]
        if not isinstance(raw_variants, list):
            raise ContractValidationError(
                f"{label}.variants must be an array"
            )
        for variant_index, raw_variant in enumerate(
            raw_variants
        ):
            variant_label = (
                f"{label}.variants[{variant_index}]"
            )
            variant = _require_dict(
                raw_variant,
                variant_label,
            )
            _require_exact_keys(
                variant,
                {
                    "label",
                    "scope",
                    "details",
                    "observation_ids",
                },
                variant_label,
            )
            name = variant["label"]
            if not isinstance(name, str) or not name.strip():
                raise ContractValidationError(
                    f"{variant_label}.label must be nonempty text"
                )
            details = _validate_string_list(
                variant["details"],
                f"{variant_label}.details",
            )
            observation_ids = _validate_string_list(
                variant["observation_ids"],
                f"{variant_label}.observation_ids",
            )
            if not details or not observation_ids:
                raise ContractValidationError(
                    f"{variant_label} details and observation_ids "
                    "must not be empty"
                )
            variants.append(
                {
                    "label": name.strip(),
                    "scope": _validate_enum_text(
                        variant["scope"],
                        f"{variant_label}.scope",
                        set(VISUAL_SCOPES - {"stable"}),
                    ),
                    "details": details,
                    "observation_ids": observation_ids,
                }
            )

        conflicts = []
        raw_conflicts = character["conflicts"]
        if not isinstance(raw_conflicts, list):
            raise ContractValidationError(
                f"{label}.conflicts must be an array"
            )
        for conflict_index, raw_conflict in enumerate(
            raw_conflicts
        ):
            conflict_label = (
                f"{label}.conflicts[{conflict_index}]"
            )
            conflict = _require_dict(
                raw_conflict,
                conflict_label,
            )
            _require_exact_keys(
                conflict,
                {
                    "category",
                    "details",
                    "observation_ids",
                },
                conflict_label,
            )
            details = _validate_string_list(
                conflict["details"],
                f"{conflict_label}.details",
            )
            observation_ids = _validate_string_list(
                conflict["observation_ids"],
                f"{conflict_label}.observation_ids",
            )
            if len(details) < 2 or not observation_ids:
                raise ContractValidationError(
                    f"{conflict_label} requires at least two details "
                    "and one observation_id"
                )
            conflicts.append(
                {
                    "category": _validate_enum_text(
                        conflict["category"],
                        f"{conflict_label}.category",
                        set(PROFILE_BUCKETS),
                    ),
                    "details": details,
                    "observation_ids": observation_ids,
                }
            )

        unknowns = []
        raw_unknowns = character["unknowns"]
        if not isinstance(raw_unknowns, list):
            raise ContractValidationError(
                f"{label}.unknowns must be an array"
            )
        for unknown_index, raw_unknown in enumerate(
            raw_unknowns
        ):
            unknown_label = (
                f"{label}.unknowns[{unknown_index}]"
            )
            unknown = _require_dict(
                raw_unknown,
                unknown_label,
            )
            _require_exact_keys(
                unknown,
                {"category", "question"},
                unknown_label,
            )
            question = unknown["question"]
            if (
                not isinstance(question, str)
                or not question.strip()
            ):
                raise ContractValidationError(
                    f"{unknown_label}.question must be nonempty text"
                )
            unknowns.append(
                {
                    "category": _validate_enum_text(
                        unknown["category"],
                        f"{unknown_label}.category",
                        set(PROFILE_BUCKETS),
                    ),
                    "question": question.strip(),
                }
            )

        characters.append(
            {
                "character_id": character_id,
                "profile": profile,
                "variants": variants,
                "conflicts": conflicts,
                "unknowns": unknowns,
            }
        )

    return {
        "characters": characters,
        "warnings": _validate_string_list(
            obj["warnings"],
            "Visual reconciliation warnings",
        ),
    }


def _validate_cast_voice_trait(
    value: Any,
    label: str,
    *,
    repair_warnings: list[str] | None = None,
) -> dict[str, Any]:
    trait = _require_dict(value, label)
    _require_exact_keys(
        trait,
        {"value", "basis", "evidence_quotes"},
        label,
    )
    text = trait["value"]
    if not isinstance(text, str) or not text.strip():
        raise ContractValidationError(
            f"{label}.value must be nonempty text"
        )
    basis = _validate_enum_text(
        trait["basis"],
        f"{label}.basis",
        {
            "explicit",
            "inferred",
            "casting_recommendation",
            "unknown",
        },
    )
    quotes = _validate_string_list(
        trait["evidence_quotes"],
        f"{label}.evidence_quotes",
    )
    if basis in {"explicit", "inferred"} and not quotes:
        original_basis = basis
        basis = "casting_recommendation"
        if repair_warnings is not None:
            repair_warnings.append(
                f"{label} was marked {original_basis} without a source quote; "
                "Alexandria retained the text as a casting recommendation."
            )
    return {
        "value": text.strip(),
        "basis": basis,
        "evidence_quotes": quotes,
    }


def _complete_cast_identity_keys(value: Any) -> set[str]:
    normalized = " ".join(
        "".join(
            character if character.isalnum() else " "
            for character in str(value or "").casefold()
        ).split()
    )
    if not normalized:
        return set()
    keys = {normalized}
    if normalized.startswith("the "):
        keys.add(normalized[4:])
    return keys


def validate_complete_cast_dossier(
    value: Any,
) -> dict[str, Any]:
    if (
        isinstance(value, dict)
        and set(value) == {"complete_cast_dossier"}
        and isinstance(value["complete_cast_dossier"], dict)
    ):
        value = value["complete_cast_dossier"]
    obj = _require_dict(value, "Complete Cast dossier response")
    _require_exact_keys(
        obj,
        {
            "selected_sections",
            "roster",
            "voice_dossiers",
            "visual_observations",
            "visual_dossiers",
            "warnings",
        },
        "Complete Cast dossier response",
    )
    sections = _require_dict(
        obj["selected_sections"],
        "Complete Cast selected_sections",
    )
    section_keys = {
        "roster_and_relationships",
        "voice_personas_and_designs",
        "visual_dossiers",
    }
    _require_exact_keys(
        sections,
        section_keys,
        "Complete Cast selected_sections",
    )
    normalized_sections: dict[str, bool] = {}
    for key in sorted(section_keys):
        selected = sections[key]
        if not isinstance(selected, bool):
            raise ContractValidationError(
                f"Complete Cast selected_sections.{key} must be boolean"
            )
        normalized_sections[key] = selected
    if not any(normalized_sections.values()):
        raise ContractValidationError(
            "Complete Cast selected_sections must enable at least one section"
        )

    roster = obj["roster"]
    if normalized_sections["roster_and_relationships"]:
        if roster is None:
            raise ContractValidationError(
                "Complete Cast roster is required when roster_and_relationships is selected"
            )
        roster = validate_roster_discovery(roster)
    elif roster is not None:
        raise ContractValidationError(
            "Complete Cast roster must be null when roster_and_relationships is not selected"
        )

    voice_dossiers = obj["voice_dossiers"]
    if normalized_sections["voice_personas_and_designs"]:
        voice_obj = _require_dict(
            voice_dossiers,
            "Complete Cast voice_dossiers",
        )
        _require_exact_keys(
            voice_obj,
            {"voices", "warnings"},
            "Complete Cast voice_dossiers",
        )
        raw_voices = voice_obj["voices"]
        if not isinstance(raw_voices, list):
            raise ContractValidationError(
                "Complete Cast voice_dossiers.voices must be an array"
            )
        voices: list[dict[str, Any]] = []
        voice_repairs: list[str] = []
        seen_speakers: set[str] = set()
        voice_keys = {
            "speaker",
            "persona_summary",
            "designed_voice_description",
            "ref_text",
            "vocal_age_impression",
            "pitch",
            "weight_and_resonance",
            "texture_and_timbre",
            "accent_and_language",
            "cadence_and_rhythm",
            "energy_range",
            "emotional_range",
            "casting_guidance",
            "uncertainties",
        }
        trait_keys = (
            "vocal_age_impression",
            "pitch",
            "weight_and_resonance",
            "texture_and_timbre",
            "accent_and_language",
            "cadence_and_rhythm",
            "energy_range",
            "emotional_range",
            "casting_guidance",
        )
        for index, raw_voice in enumerate(raw_voices):
            label = f"Complete Cast voice dossier {index}"
            voice = _require_dict(raw_voice, label)
            _require_exact_keys(voice, voice_keys, label)
            speaker = voice["speaker"]
            persona_summary = voice["persona_summary"]
            description = voice["designed_voice_description"]
            ref_text = voice["ref_text"]
            for field_name, field_value in (
                ("speaker", speaker),
                ("persona_summary", persona_summary),
                ("designed_voice_description", description),
                ("ref_text", ref_text),
            ):
                if not isinstance(field_value, str) or not field_value.strip():
                    raise ContractValidationError(
                        f"{label}.{field_name} must be nonempty text"
                    )
            speaker = speaker.strip()
            if speaker in seen_speakers:
                raise ContractValidationError(
                    "Complete Cast voice dossier speakers must be unique"
                )
            seen_speakers.add(speaker)
            voices.append(
                {
                    "speaker": speaker,
                    "persona_summary": persona_summary.strip(),
                    "designed_voice_description": description.strip(),
                    "ref_text": ref_text,
                    **{
                        key: _validate_cast_voice_trait(
                            voice[key],
                            f"Complete Cast voice dossier {speaker}.{key}",
                            repair_warnings=voice_repairs,
                        )
                        for key in trait_keys
                    },
                    "uncertainties": _validate_string_list(
                        voice["uncertainties"],
                        f"{label}.uncertainties",
                    ),
                }
            )
        voice_dossiers = {
            "voices": voices,
            "warnings": [
                *_validate_string_list(
                    voice_obj["warnings"],
                    "Complete Cast voice_dossiers warnings",
                ),
                *voice_repairs,
            ],
        }
    elif voice_dossiers is not None:
        raise ContractValidationError(
            "Complete Cast voice_dossiers must be null when voice_personas_and_designs is not selected"
        )

    visual_observations = obj["visual_observations"]
    visual_dossiers = obj["visual_dossiers"]
    if normalized_sections["visual_dossiers"]:
        if visual_observations is None or visual_dossiers is None:
            raise ContractValidationError(
                "Complete Cast visual sections require both observations and dossiers"
            )
        visual_obj = _require_dict(
            visual_observations,
            "Complete Cast visual_observations",
        )
        _require_exact_keys(
            visual_obj,
            {"observations", "warnings"},
            "Complete Cast visual_observations",
        )
        raw_observations = visual_obj["observations"]
        if not isinstance(raw_observations, list):
            raise ContractValidationError(
                "Complete Cast visual_observations.observations must be an array"
            )
        observation_ids: list[str] = []
        stripped_observations: list[dict[str, Any]] = []
        expected_visual_keys = {
            "observation_id",
            "character_id",
            "category",
            "detail",
            "scope",
            "certainty",
            "basis",
            "quote",
            "start_char",
            "end_char",
        }
        for index, raw_observation in enumerate(raw_observations):
            label = f"Complete Cast visual observation {index}"
            observation = _require_dict(raw_observation, label)
            _require_exact_keys(
                observation,
                expected_visual_keys,
                label,
            )
            observation_id = observation["observation_id"]
            if (
                not isinstance(observation_id, str)
                or not observation_id.strip()
            ):
                raise ContractValidationError(
                    f"{label}.observation_id must be nonempty text"
                )
            observation_id = observation_id.strip()
            if observation_id in observation_ids:
                raise ContractValidationError(
                    "Complete Cast visual observation IDs must be unique"
                )
            observation_ids.append(observation_id)
            stripped_observations.append(
                {
                    key: value
                    for key, value in observation.items()
                    if key != "observation_id"
                }
            )
        normalized_visual = validate_visual_discovery(
            {
                "observations": stripped_observations,
                "warnings": visual_obj["warnings"],
            }
        )
        visual_observations = {
            "observations": [
                {
                    "observation_id": observation_id,
                    **observation,
                }
                for observation_id, observation in zip(
                    observation_ids,
                    normalized_visual["observations"],
                )
            ],
            "warnings": normalized_visual["warnings"],
        }
        visual_dossiers = validate_visual_reconciliation(
            visual_dossiers
        )
        known_observation_ids = set(observation_ids)
        referenced_observation_ids = {
            observation_id
            for character in visual_dossiers["characters"]
            for facts in character["profile"].values()
            for fact in facts
            for observation_id in fact["observation_ids"]
        }
        referenced_observation_ids.update(
            observation_id
            for character in visual_dossiers["characters"]
            for variant in character["variants"]
            for observation_id in variant["observation_ids"]
        )
        referenced_observation_ids.update(
            observation_id
            for character in visual_dossiers["characters"]
            for conflict in character["conflicts"]
            for observation_id in conflict["observation_ids"]
        )
        unknown_references = sorted(
            referenced_observation_ids - known_observation_ids
        )
        if unknown_references:
            raise ContractValidationError(
                "Complete Cast visual dossiers reference unknown observation IDs: "
                + ", ".join(unknown_references)
            )
    elif visual_observations is not None or visual_dossiers is not None:
        raise ContractValidationError(
            "Complete Cast visual sections must be null when visual_dossiers is not selected"
        )

    roster_labels: set[str] = set()
    if isinstance(roster, dict):
        for entity in roster["entities"]:
            for raw_label in (
                entity.get("identity_seed"),
                entity.get("canonical_name"),
                entity.get("display_name"),
                *(entity.get("aliases") or []),
                *(entity.get("nicknames") or []),
            ):
                roster_labels.update(_complete_cast_identity_keys(raw_label))
    if roster_labels and isinstance(voice_dossiers, dict):
        unknown_speakers = sorted(
            voice["speaker"]
            for voice in voice_dossiers["voices"]
            if _complete_cast_identity_keys(voice["speaker"]).isdisjoint(
                roster_labels
            )
        )
        if unknown_speakers:
            raise ContractValidationError(
                "Complete Cast voice dossiers reference identities absent from the returned roster: "
                + ", ".join(unknown_speakers)
            )
    if roster_labels and isinstance(visual_dossiers, dict):
        unknown_visuals = sorted(
            character["character_id"]
            for character in visual_dossiers["characters"]
            if _complete_cast_identity_keys(character["character_id"]).isdisjoint(
                roster_labels
            )
        )
        if unknown_visuals:
            raise ContractValidationError(
                "Complete Cast visual dossiers reference identities absent from the returned roster: "
                + ", ".join(unknown_visuals)
            )

    return {
        "selected_sections": normalized_sections,
        "roster": roster,
        "voice_dossiers": voice_dossiers,
        "visual_observations": visual_observations,
        "visual_dossiers": visual_dossiers,
        "warnings": _validate_string_list(
            obj["warnings"],
            "Complete Cast warnings",
        ),
    }


VALIDATORS: dict[str, Callable[[Any], Any]] = {
    "persona": validate_persona,
    "persona_catalog": validate_persona_catalog,
    "script": validate_script,
    "review": validate_script,
    "alias": validate_alias_map,
    "advanced_discovery": validate_advanced_discovery,
    "roster_discovery": validate_roster_discovery,
    "roster_reconciliation": validate_roster_reconciliation,
    "visual_discovery": validate_visual_discovery,
    "visual_reconciliation": validate_visual_reconciliation,
    "complete_cast_dossier": validate_complete_cast_dossier,
    "backend_render_plan": validate_backend_render_plan,
    "pronunciation_guidance": validate_pronunciation_guidance,
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
