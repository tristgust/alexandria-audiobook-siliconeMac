from __future__ import annotations

import copy
import json
import re
import time
from pathlib import Path
from typing import Any

from generation_state import atomic_json_write


VISUAL_SCHEMA_VERSION = 1
PROFILE_BUCKETS = (
    "apparent_age",
    "species_or_ancestry",
    "skin_and_complexion",
    "face_and_features",
    "eyes",
    "hair",
    "height_and_build",
    "body_features",
    "distinguishing_marks",
    "cybernetics_or_modifications",
    "posture_and_movement",
    "clothing",
    "accessories_weapons_equipment",
    "nonhuman_anatomy",
)
VISUAL_SCOPES = frozenset(
    {
        "stable",
        "scene_specific",
        "temporary",
        "injury",
        "disguise",
        "transformation",
        "age_variant",
        "unknown",
    }
)
VISUAL_BASES = frozenset({"explicit", "inferred"})

_VISUAL_KEYS = frozenset(
    {
        "schema_version",
        "observations",
        "profile",
        "variants",
        "conflicts",
        "unknowns",
        "image_prompt_summary",
    }
)
_OBSERVATION_KEYS = frozenset(
    {
        "observation_id",
        "category",
        "detail",
        "scope",
        "certainty",
        "basis",
        "source_location",
        "start_char",
        "end_char",
        "passage_index",
        "quote",
    }
)
_FACT_KEYS = frozenset(
    {
        "detail",
        "certainty",
        "observation_ids",
    }
)
_VARIANT_KEYS = frozenset(
    {
        "label",
        "scope",
        "details",
        "observation_ids",
    }
)
_CONFLICT_KEYS = frozenset(
    {
        "category",
        "details",
        "observation_ids",
    }
)
_UNKNOWN_KEYS = frozenset(
    {
        "category",
        "question",
    }
)


class CharacterVisualError(RuntimeError):
    pass


class CharacterVisualValidationError(CharacterVisualError):
    pass


class CharacterVisualCorruptError(CharacterVisualError):
    pass


def sanitize_character_filename(name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise CharacterVisualValidationError(
            "Character name must be non-empty text."
        )
    return re.sub(r"[^\w\-]", "_", name.strip()).lower()


def persona_reference_path(
    persona_refs_dir: str | Path,
    character_name: str,
) -> Path:
    return Path(persona_refs_dir) / (
        sanitize_character_filename(character_name) + ".json"
    )


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CharacterVisualValidationError(
            f"{label} must be a JSON object."
        )
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise CharacterVisualValidationError(
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
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise CharacterVisualValidationError(
            f"{label} has " + "; ".join(details) + "."
        )


def _require_text(
    value: Any,
    label: str,
    *,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise CharacterVisualValidationError(
            f"{label} must be text."
        )
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise CharacterVisualValidationError(
            f"{label} must not be empty."
        )
    return normalized


def _require_int(value: Any, label: str, minimum: int = 0) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
    ):
        raise CharacterVisualValidationError(
            f"{label} must be an integer >= {minimum}."
        )
    return value


def _require_certainty(value: Any, label: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
    ):
        raise CharacterVisualValidationError(
            f"{label} must be numeric."
        )
    normalized = float(value)
    if not 0.0 <= normalized <= 1.0:
        raise CharacterVisualValidationError(
            f"{label} must be between 0.0 and 1.0."
        )
    return normalized


def _require_string_list(value: Any, label: str) -> list[str]:
    return [
        _require_text(item, f"{label}[{index}]")
        for index, item in enumerate(
            _require_list(value, label)
        )
    ]


def _validate_observation(
    value: Any,
    *,
    index: int,
    source_text: str | None,
) -> dict[str, Any]:
    label = f"Visual observation {index}"
    observation = _require_dict(value, label)
    _require_exact_keys(observation, _OBSERVATION_KEYS, label)

    category = _require_text(
        observation["category"],
        f"{label}.category",
    )
    if category not in PROFILE_BUCKETS:
        raise CharacterVisualValidationError(
            f"{label}.category is unsupported: {category!r}."
        )

    scope = _require_text(
        observation["scope"],
        f"{label}.scope",
    )
    if scope not in VISUAL_SCOPES:
        raise CharacterVisualValidationError(
            f"{label}.scope is unsupported: {scope!r}."
        )

    basis = _require_text(
        observation["basis"],
        f"{label}.basis",
    )
    if basis not in VISUAL_BASES:
        raise CharacterVisualValidationError(
            f"{label}.basis is unsupported: {basis!r}."
        )

    start = _require_int(
        observation["start_char"],
        f"{label}.start_char",
    )
    end = _require_int(
        observation["end_char"],
        f"{label}.end_char",
    )
    if end <= start:
        raise CharacterVisualValidationError(
            f"{label}.end_char must be greater than start_char."
        )

    quote = _require_text(
        observation["quote"],
        f"{label}.quote",
    )
    if source_text is not None:
        if end > len(source_text):
            raise CharacterVisualValidationError(
                f"{label} extends beyond the selected source."
            )
        if source_text[start:end] != quote:
            raise CharacterVisualValidationError(
                f"{label}.quote does not match the selected source "
                "at its stored offsets."
            )

    return {
        "observation_id": _require_text(
            observation["observation_id"],
            f"{label}.observation_id",
        ),
        "category": category,
        "detail": _require_text(
            observation["detail"],
            f"{label}.detail",
        ),
        "scope": scope,
        "certainty": _require_certainty(
            observation["certainty"],
            f"{label}.certainty",
        ),
        "basis": basis,
        "source_location": _require_text(
            observation["source_location"],
            f"{label}.source_location",
        ),
        "start_char": start,
        "end_char": end,
        "passage_index": _require_int(
            observation["passage_index"],
            f"{label}.passage_index",
            minimum=1,
        ),
        "quote": quote,
    }


def _validate_fact(
    value: Any,
    *,
    label: str,
    observation_ids: set[str],
    category: str,
    observations_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    fact = _require_dict(value, label)
    _require_exact_keys(fact, _FACT_KEYS, label)
    linked_ids = _require_string_list(
        fact["observation_ids"],
        f"{label}.observation_ids",
    )
    if not linked_ids:
        raise CharacterVisualValidationError(
            f"{label}.observation_ids must not be empty."
        )
    unknown = set(linked_ids) - observation_ids
    if unknown:
        raise CharacterVisualValidationError(
            f"{label} references unknown observations: "
            f"{sorted(unknown)}."
        )
    wrong_category = [
        observation_id
        for observation_id in linked_ids
        if observations_by_id[observation_id]["category"]
        != category
    ]
    if wrong_category:
        raise CharacterVisualValidationError(
            f"{label} references observations from the wrong "
            f"category: {wrong_category}."
        )
    return {
        "detail": _require_text(
            fact["detail"],
            f"{label}.detail",
        ),
        "certainty": _require_certainty(
            fact["certainty"],
            f"{label}.certainty",
        ),
        "observation_ids": linked_ids,
    }


def build_image_prompt_summary(
    *,
    profile: dict[str, list[dict[str, Any]]],
    variants: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
) -> str:
    conflicted = {
        conflict["category"]
        for conflict in conflicts
    }
    parts = []

    for category in PROFILE_BUCKETS:
        if category in conflicted:
            continue
        for fact in profile[category]:
            parts.append(fact["detail"])

    for variant in variants:
        if variant["scope"] in {
            "transformation",
            "age_variant",
        }:
            parts.append(
                f"{variant['label']}: "
                + "; ".join(variant["details"])
            )

    if conflicted:
        parts.append(
            "Conflicting source descriptions exist for "
            + ", ".join(sorted(conflicted))
            + "; consult the evidence dossier."
        )

    if not parts:
        return "No stable source-backed visual traits established."

    return "; ".join(parts) + "."


def validate_visual_dossier(
    value: Any,
    *,
    source_text: str | None = None,
) -> dict[str, Any]:
    visual = _require_dict(value, "Visual dossier")
    _require_exact_keys(visual, _VISUAL_KEYS, "Visual dossier")

    if visual["schema_version"] != VISUAL_SCHEMA_VERSION:
        raise CharacterVisualValidationError(
            "Unsupported visual dossier schema version."
        )

    observations = [
        _validate_observation(
            item,
            index=index,
            source_text=source_text,
        )
        for index, item in enumerate(
            _require_list(
                visual["observations"],
                "Visual dossier.observations",
            )
        )
    ]
    observation_ids = [
        item["observation_id"]
        for item in observations
    ]
    if len(observation_ids) != len(set(observation_ids)):
        raise CharacterVisualValidationError(
            "Visual observation IDs must be unique."
        )
    observation_id_set = set(observation_ids)
    observations_by_id = {
        item["observation_id"]: item
        for item in observations
    }

    profile_raw = _require_dict(
        visual["profile"],
        "Visual dossier.profile",
    )
    _require_exact_keys(
        profile_raw,
        set(PROFILE_BUCKETS),
        "Visual dossier.profile",
    )
    profile = {
        category: [
            _validate_fact(
                item,
                label=(
                    f"Visual dossier.profile.{category}[{index}]"
                ),
                observation_ids=observation_id_set,
                category=category,
                observations_by_id=observations_by_id,
            )
            for index, item in enumerate(
                _require_list(
                    profile_raw[category],
                    f"Visual dossier.profile.{category}",
                )
            )
        ]
        for category in PROFILE_BUCKETS
    }

    variants = []
    for index, raw_variant in enumerate(
        _require_list(
            visual["variants"],
            "Visual dossier.variants",
        )
    ):
        label = f"Visual variant {index}"
        variant = _require_dict(raw_variant, label)
        _require_exact_keys(variant, _VARIANT_KEYS, label)
        scope = _require_text(
            variant["scope"],
            f"{label}.scope",
        )
        if scope not in VISUAL_SCOPES - {"stable"}:
            raise CharacterVisualValidationError(
                f"{label}.scope must be a non-stable visual scope."
            )
        linked_ids = _require_string_list(
            variant["observation_ids"],
            f"{label}.observation_ids",
        )
        if not linked_ids:
            raise CharacterVisualValidationError(
                f"{label}.observation_ids must not be empty."
            )
        unknown = set(linked_ids) - observation_id_set
        if unknown:
            raise CharacterVisualValidationError(
                f"{label} references unknown observations: "
                f"{sorted(unknown)}."
            )
        variants.append(
            {
                "label": _require_text(
                    variant["label"],
                    f"{label}.label",
                ),
                "scope": scope,
                "details": _require_string_list(
                    variant["details"],
                    f"{label}.details",
                ),
                "observation_ids": linked_ids,
            }
        )

    conflicts = []
    for index, raw_conflict in enumerate(
        _require_list(
            visual["conflicts"],
            "Visual dossier.conflicts",
        )
    ):
        label = f"Visual conflict {index}"
        conflict = _require_dict(raw_conflict, label)
        _require_exact_keys(conflict, _CONFLICT_KEYS, label)
        category = _require_text(
            conflict["category"],
            f"{label}.category",
        )
        if category not in PROFILE_BUCKETS:
            raise CharacterVisualValidationError(
                f"{label}.category is unsupported: {category!r}."
            )
        details = _require_string_list(
            conflict["details"],
            f"{label}.details",
        )
        if len(details) < 2:
            raise CharacterVisualValidationError(
                f"{label}.details must contain at least two values."
            )
        linked_ids = _require_string_list(
            conflict["observation_ids"],
            f"{label}.observation_ids",
        )
        if not linked_ids:
            raise CharacterVisualValidationError(
                f"{label}.observation_ids must not be empty."
            )
        unknown = set(linked_ids) - observation_id_set
        if unknown:
            raise CharacterVisualValidationError(
                f"{label} references unknown observations: "
                f"{sorted(unknown)}."
            )
        conflicts.append(
            {
                "category": category,
                "details": details,
                "observation_ids": linked_ids,
            }
        )

    unknowns = []
    for index, raw_unknown in enumerate(
        _require_list(
            visual["unknowns"],
            "Visual dossier.unknowns",
        )
    ):
        label = f"Visual unknown {index}"
        unknown = _require_dict(raw_unknown, label)
        _require_exact_keys(unknown, _UNKNOWN_KEYS, label)
        category = _require_text(
            unknown["category"],
            f"{label}.category",
        )
        if category not in PROFILE_BUCKETS:
            raise CharacterVisualValidationError(
                f"{label}.category is unsupported: {category!r}."
            )
        unknowns.append(
            {
                "category": category,
                "question": _require_text(
                    unknown["question"],
                    f"{label}.question",
                ),
            }
        )

    expected_summary = build_image_prompt_summary(
        profile=profile,
        variants=variants,
        conflicts=conflicts,
    )
    saved_summary = _require_text(
        visual["image_prompt_summary"],
        "Visual dossier.image_prompt_summary",
    )
    if saved_summary != expected_summary:
        raise CharacterVisualValidationError(
            "Visual dossier image_prompt_summary is not the "
            "deterministic summary of its validated facts."
        )

    return {
        "schema_version": VISUAL_SCHEMA_VERSION,
        "observations": observations,
        "profile": profile,
        "variants": variants,
        "conflicts": conflicts,
        "unknowns": unknowns,
        "image_prompt_summary": saved_summary,
    }


def build_visual_dossier(
    *,
    observations: list[dict[str, Any]],
    profile: dict[str, list[dict[str, Any]]],
    variants: list[dict[str, Any]] | None = None,
    conflicts: list[dict[str, Any]] | None = None,
    unknowns: list[dict[str, Any]] | None = None,
    source_text: str | None = None,
) -> dict[str, Any]:
    profile_object = _require_dict(
        profile,
        "Visual dossier.profile",
    )
    _require_exact_keys(
        profile_object,
        set(PROFILE_BUCKETS),
        "Visual dossier.profile",
    )
    draft = {
        "schema_version": VISUAL_SCHEMA_VERSION,
        "observations": copy.deepcopy(observations),
        "profile": copy.deepcopy(profile),
        "variants": copy.deepcopy(variants or []),
        "conflicts": copy.deepcopy(conflicts or []),
        "unknowns": copy.deepcopy(unknowns or []),
        "image_prompt_summary": "placeholder",
    }
    normalized_without_summary = validate_visual_dossier(
        {
            **draft,
            "image_prompt_summary": build_image_prompt_summary(
                profile=draft["profile"],
                variants=draft["variants"],
                conflicts=draft["conflicts"],
            ),
        },
        source_text=source_text,
    )
    return normalized_without_summary


def load_persona_reference(
    path: str | Path,
) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(str(target))
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (
        OSError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        raise CharacterVisualCorruptError(
            f"Persona reference could not be read: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise CharacterVisualValidationError(
            "Persona reference root must be a JSON object."
        )
    return value


def base_persona_reference(
    *,
    name: str,
    aliases: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "name": _require_text(name, "Persona reference name"),
        "aliases": list(aliases or []),
        "features": [],
        "personality": [],
        "voice_clues": [],
        "relationships": [],
        "sample_lines": [],
        "observations": [],
        "updated_at": int(time.time()),
    }


def write_visual_dossier(
    *,
    persona_ref_path: str | Path,
    visual: dict[str, Any],
    character_name: str,
    aliases: list[str] | None = None,
    source_text: str | None = None,
    replace_existing: bool = False,
) -> dict[str, Any]:
    target = Path(persona_ref_path)
    if target.exists():
        reference = load_persona_reference(target)
    else:
        reference = base_persona_reference(
            name=character_name,
            aliases=aliases,
        )

    if "visual" in reference and not replace_existing:
        raise CharacterVisualError(
            "A visual dossier already exists for this character."
        )

    normalized_visual = validate_visual_dossier(
        visual,
        source_text=source_text,
    )
    updated = copy.deepcopy(reference)
    updated["visual"] = normalized_visual
    updated["updated_at"] = int(time.time())
    atomic_json_write(updated, target)
    return updated


def inspect_visual_dossier(
    *,
    persona_ref_path: str | Path,
    source_text: str | None = None,
) -> dict[str, Any]:
    target = Path(persona_ref_path)
    if not target.exists():
        return {
            "status": "absent",
            "path": str(target),
            "observation_count": 0,
            "conflict_count": 0,
            "variant_count": 0,
            "error": None,
        }

    try:
        reference = load_persona_reference(target)
    except CharacterVisualError as exc:
        return {
            "status": "persona_ref_invalid",
            "path": str(target),
            "observation_count": 0,
            "conflict_count": 0,
            "variant_count": 0,
            "error": str(exc),
        }

    if "visual" not in reference:
        return {
            "status": "absent",
            "path": str(target),
            "observation_count": 0,
            "conflict_count": 0,
            "variant_count": 0,
            "error": None,
        }

    try:
        visual = validate_visual_dossier(
            reference["visual"],
            source_text=source_text,
        )
    except CharacterVisualValidationError as exc:
        return {
            "status": "invalid",
            "path": str(target),
            "observation_count": 0,
            "conflict_count": 0,
            "variant_count": 0,
            "error": str(exc),
        }

    return {
        "status": "complete",
        "path": str(target),
        "observation_count": len(visual["observations"]),
        "conflict_count": len(visual["conflicts"]),
        "variant_count": len(visual["variants"]),
        "image_prompt_summary": visual[
            "image_prompt_summary"
        ],
        "error": None,
    }


def build_visual_status(
    *,
    approved_roster: dict[str, Any] | None,
    persona_refs_dir: str | Path,
    source_text: str | None = None,
) -> dict[str, Any]:
    if approved_roster is None:
        return {
            "available": False,
            "reason": "No approved character roster exists.",
            "entries": [],
            "complete_count": 0,
            "absent_count": 0,
            "invalid_count": 0,
        }

    entries = []
    for roster_entry in approved_roster["entries"]:
        character_name = (
            roster_entry["canonical_name"]
            or roster_entry["display_name"]
        )
        path = persona_reference_path(
            persona_refs_dir,
            character_name,
        )
        inspection = inspect_visual_dossier(
            persona_ref_path=path,
            source_text=source_text,
        )
        entries.append(
            {
                "character_id": roster_entry["id"],
                "canonical_name": roster_entry[
                    "canonical_name"
                ],
                "display_name": roster_entry["display_name"],
                "entity_kind": roster_entry["entity_kind"],
                **inspection,
            }
        )

    return {
        "available": True,
        "reason": None,
        "entries": entries,
        "complete_count": sum(
            item["status"] == "complete"
            for item in entries
        ),
        "absent_count": sum(
            item["status"] == "absent"
            for item in entries
        ),
        "invalid_count": sum(
            item["status"]
            in {"invalid", "persona_ref_invalid"}
            for item in entries
        ),
    }
