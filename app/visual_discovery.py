from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from character_visuals import (
    PROFILE_BUCKETS,
    VISUAL_SCOPES,
    CharacterVisualValidationError,
    build_visual_dossier,
    persona_reference_targets,
    write_visual_dossiers_transaction,
)
from generation_state import (
    atomic_json_write,
    fingerprint_value,
)
from roster_discovery import build_discovery_passages


VISUAL_STATE_SCHEMA_VERSION = 1
VISUAL_DISCOVERY_CONTRACT_VERSION = 1
VISUAL_RECONCILIATION_CONTRACT_VERSION = 1
DEFAULT_PASSAGE_SIZE = 12000
DEFAULT_PASSAGE_OVERLAP = 1200


class VisualDiscoveryError(RuntimeError):
    pass


class VisualDiscoveryStateError(VisualDiscoveryError):
    pass


class VisualDiscoveryMismatchError(VisualDiscoveryError):
    pass


class VisualDiscoveryEvidenceError(VisualDiscoveryError):
    pass


class VisualReconciliationError(VisualDiscoveryError):
    pass


def _runtime_identity(runtime_client: Any) -> dict[str, Any]:
    return {
        "model_name": runtime_client.model_name,
        "backend": runtime_client.backend,
        "thinking": bool(runtime_client.thinking),
        "structured_output": bool(runtime_client.structured_output),
        "corrective_retry": bool(runtime_client.corrective_retry),
        "context_length": runtime_client.context_length,
    }


def build_visual_identity(
    runtime_client: Any,
    *,
    passage_size: int,
    overlap_chars: int,
    temperature: float,
    max_tokens: int,
    seed: int | None,
    compilation_runtime_client: Any | None = None,
) -> dict[str, Any]:
    discovery_identity = _runtime_identity(runtime_client)
    identity = {
        **discovery_identity,
        "passage_size": passage_size,
        "overlap_chars": overlap_chars,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "seed": seed,
        "visual_discovery_contract_version": (
            VISUAL_DISCOVERY_CONTRACT_VERSION
        ),
        "visual_reconciliation_contract_version": (
            VISUAL_RECONCILIATION_CONTRACT_VERSION
        ),
    }
    if compilation_runtime_client is not None:
        compilation_identity = _runtime_identity(
            compilation_runtime_client
        )
        if compilation_identity != discovery_identity:
            identity["visual_compilation_runtime"] = (
                compilation_identity
            )
    return identity


def new_visual_discovery_state(
    *,
    source: dict[str, Any],
    roster_fingerprint: str,
    character_ids: list[str],
    generation_identity: dict[str, Any],
    passages: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": VISUAL_STATE_SCHEMA_VERSION,
        "source": copy.deepcopy(source),
        "roster_fingerprint": roster_fingerprint,
        "character_ids": list(character_ids),
        "generation_identity": copy.deepcopy(
            generation_identity
        ),
        "generation_fingerprint": fingerprint_value(
            generation_identity
        ),
        "passage_fingerprints": [
            passage["fingerprint"]
            for passage in passages
        ],
        "total_passages": len(passages),
        "completed_passages": [],
        "reconciliation": None,
    }


def _validate_state_observation(
    value: Any,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise VisualDiscoveryStateError(
            "Visual discovery observation must be an object."
        )
    expected = {
        "character_id",
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
    if set(value) != expected:
        raise VisualDiscoveryStateError(
            "Visual discovery observation has invalid fields."
        )
    result = copy.deepcopy(value)
    for key in (
        "character_id",
        "observation_id",
        "category",
        "detail",
        "scope",
        "basis",
        "source_location",
        "quote",
    ):
        if (
            not isinstance(result[key], str)
            or not result[key].strip()
        ):
            raise VisualDiscoveryStateError(
                f"Visual discovery observation {key} is invalid."
            )
    if result["category"] not in PROFILE_BUCKETS:
        raise VisualDiscoveryStateError(
            "Visual discovery observation category is invalid."
        )
    if result["scope"] not in VISUAL_SCOPES:
        raise VisualDiscoveryStateError(
            "Visual discovery observation scope is invalid."
        )
    if result["basis"] not in {"explicit", "inferred"}:
        raise VisualDiscoveryStateError(
            "Visual discovery observation basis is invalid."
        )
    for key in ("start_char", "end_char", "passage_index"):
        if (
            not isinstance(result[key], int)
            or isinstance(result[key], bool)
        ):
            raise VisualDiscoveryStateError(
                f"Visual discovery observation {key} is invalid."
            )
    if (
        result["start_char"] < 0
        or result["end_char"] <= result["start_char"]
        or result["passage_index"] < 1
    ):
        raise VisualDiscoveryStateError(
            "Visual discovery observation offsets are invalid."
        )
    certainty = result["certainty"]
    if (
        not isinstance(certainty, (int, float))
        or isinstance(certainty, bool)
        or not 0.0 <= float(certainty) <= 1.0
    ):
        raise VisualDiscoveryStateError(
            "Visual discovery observation certainty is invalid."
        )
    result["certainty"] = float(certainty)
    return result


def validate_visual_discovery_state(
    value: Any,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise VisualDiscoveryStateError(
            "Visual discovery state must be an object."
        )
    expected = {
        "schema_version",
        "source",
        "roster_fingerprint",
        "character_ids",
        "generation_identity",
        "generation_fingerprint",
        "passage_fingerprints",
        "total_passages",
        "completed_passages",
        "reconciliation",
    }
    if set(value) != expected:
        raise VisualDiscoveryStateError(
            "Visual discovery state has invalid fields."
        )
    if value["schema_version"] != VISUAL_STATE_SCHEMA_VERSION:
        raise VisualDiscoveryStateError(
            "Unsupported visual discovery state schema."
        )
    source = value["source"]
    if (
        not isinstance(source, dict)
        or set(source)
        != {
            "path",
            "basename",
            "fingerprint",
            "character_count",
        }
    ):
        raise VisualDiscoveryStateError(
            "Visual discovery state source is invalid."
        )
    roster_fingerprint = value["roster_fingerprint"]
    if (
        not isinstance(roster_fingerprint, str)
        or not roster_fingerprint
    ):
        raise VisualDiscoveryStateError(
            "Visual discovery roster fingerprint is invalid."
        )
    character_ids = value["character_ids"]
    if (
        not isinstance(character_ids, list)
        or not character_ids
        or not all(
            isinstance(item, str) and item
            for item in character_ids
        )
        or len(character_ids) != len(set(character_ids))
    ):
        raise VisualDiscoveryStateError(
            "Visual discovery character IDs are invalid."
        )
    identity = value["generation_identity"]
    if not isinstance(identity, dict):
        raise VisualDiscoveryStateError(
            "Visual discovery identity is invalid."
        )
    if (
        value["generation_fingerprint"]
        != fingerprint_value(identity)
    ):
        raise VisualDiscoveryStateError(
            "Visual discovery generation fingerprint is invalid."
        )
    fingerprints = value["passage_fingerprints"]
    total = value["total_passages"]
    if (
        not isinstance(fingerprints, list)
        or not all(
            isinstance(item, str) and item
            for item in fingerprints
        )
        or not isinstance(total, int)
        or isinstance(total, bool)
        or total != len(fingerprints)
    ):
        raise VisualDiscoveryStateError(
            "Visual discovery passage layout is invalid."
        )
    completed = value["completed_passages"]
    if not isinstance(completed, list):
        raise VisualDiscoveryStateError(
            "Visual discovery completed passages are invalid."
        )
    normalized_completed = []
    observation_ids = set()
    for position, record in enumerate(completed, start=1):
        if (
            not isinstance(record, dict)
            or set(record)
            != {
                "index",
                "passage_fingerprint",
                "observations",
                "warnings",
            }
        ):
            raise VisualDiscoveryStateError(
                "Visual discovery passage record is invalid."
            )
        if record["index"] != position:
            raise VisualDiscoveryStateError(
                "Visual discovery passage records must be contiguous."
            )
        if (
            position > total
            or record["passage_fingerprint"]
            != fingerprints[position - 1]
        ):
            raise VisualDiscoveryStateError(
                "Visual discovery passage fingerprint is invalid."
            )
        raw_observations = record["observations"]
        warnings = record["warnings"]
        if not isinstance(raw_observations, list):
            raise VisualDiscoveryStateError(
                "Visual discovery observations must be an array."
            )
        if (
            not isinstance(warnings, list)
            or not all(
                isinstance(item, str)
                for item in warnings
            )
        ):
            raise VisualDiscoveryStateError(
                "Visual discovery warnings must be text."
            )
        observations = []
        for raw_observation in raw_observations:
            observation = _validate_state_observation(
                raw_observation
            )
            if observation["character_id"] not in character_ids:
                raise VisualDiscoveryStateError(
                    "Visual discovery observation references an "
                    "unselected character."
                )
            if observation["observation_id"] in observation_ids:
                raise VisualDiscoveryStateError(
                    "Visual observation IDs must be unique."
                )
            observation_ids.add(observation["observation_id"])
            observations.append(observation)
        normalized_completed.append(
            {
                "index": position,
                "passage_fingerprint": record[
                    "passage_fingerprint"
                ],
                "observations": observations,
                "warnings": list(warnings),
            }
        )
    reconciliation = value["reconciliation"]
    if reconciliation is not None and not isinstance(
        reconciliation,
        dict,
    ):
        raise VisualDiscoveryStateError(
            "Visual discovery reconciliation must be an object or null."
        )
    result = copy.deepcopy(value)
    result["completed_passages"] = normalized_completed
    return result


def load_visual_discovery_state(
    path: str | Path,
) -> dict[str, Any] | None:
    target = Path(path)
    if not target.exists():
        return None
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (
        OSError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        raise VisualDiscoveryStateError(
            f"Visual discovery state could not be read: {exc}"
        ) from exc
    return validate_visual_discovery_state(value)


def prepare_visual_discovery_state(
    *,
    path: str | Path,
    source: dict[str, Any],
    roster_fingerprint: str,
    character_ids: list[str],
    generation_identity: dict[str, Any],
    passages: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = new_visual_discovery_state(
        source=source,
        roster_fingerprint=roster_fingerprint,
        character_ids=character_ids,
        generation_identity=generation_identity,
        passages=passages,
    )
    existing = load_visual_discovery_state(path)
    if existing is None:
        atomic_json_write(expected, path)
        return expected
    mismatches = []
    if (
        existing["source"]["fingerprint"]
        != source["fingerprint"]
    ):
        mismatches.append("source")
    if existing["roster_fingerprint"] != roster_fingerprint:
        mismatches.append("approved roster")
    if existing["character_ids"] != character_ids:
        mismatches.append("selected characters")
    if (
        existing["generation_fingerprint"]
        != expected["generation_fingerprint"]
    ):
        mismatches.append("visual configuration")
    if (
        existing["passage_fingerprints"]
        != expected["passage_fingerprints"]
    ):
        mismatches.append("passage layout")
    if mismatches:
        raise VisualDiscoveryMismatchError(
            "Existing visual discovery progress does not match "
            "the current "
            + ", ".join(mismatches)
            + ". Discard progress explicitly before starting a "
            "different run."
        )
    return existing


def checkpoint_visual_passage(
    *,
    state: dict[str, Any],
    path: str | Path,
    passage: dict[str, Any],
    observations: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    state = validate_visual_discovery_state(state)
    expected_index = len(state["completed_passages"]) + 1
    if passage["index"] != expected_index:
        raise VisualDiscoveryStateError(
            "Visual checkpoint must be the next contiguous passage."
        )
    if (
        passage["fingerprint"]
        != state["passage_fingerprints"][expected_index - 1]
    ):
        raise VisualDiscoveryMismatchError(
            "Visual passage does not match prepared state."
        )
    updated = {
        **state,
        "completed_passages": [
            *state["completed_passages"],
            {
                "index": expected_index,
                "passage_fingerprint": passage["fingerprint"],
                "observations": [
                    _validate_state_observation(item)
                    for item in observations
                ],
                "warnings": list(warnings),
            },
        ],
    }
    updated = validate_visual_discovery_state(updated)
    atomic_json_write(updated, path)
    return updated


def checkpoint_visual_reconciliation(
    *,
    state: dict[str, Any],
    path: str | Path,
    reconciliation: dict[str, Any],
) -> dict[str, Any]:
    state = validate_visual_discovery_state(state)
    if len(state["completed_passages"]) != state["total_passages"]:
        raise VisualDiscoveryStateError(
            "Visual reconciliation cannot checkpoint before all "
            "passages are complete."
        )
    validated = validate_reconciliation_integrity(
        state=state,
        reconciliation=reconciliation,
    )
    updated = {
        **state,
        "reconciliation": validated,
    }
    atomic_json_write(updated, path)
    return validate_visual_discovery_state(updated)


def clear_visual_discovery_state(path: str | Path) -> bool:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        return False
    return True


def inspect_visual_discovery_state(
    path: str | Path,
    *,
    current_source: dict[str, Any] | None,
    roster_fingerprint: str | None,
) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {
            "exists": False,
            "status": "none",
            "completed_passages": 0,
            "total_passages": 0,
            "next_passage": None,
            "reconciliation_complete": False,
            "character_ids": [],
            "compatible_source": None,
            "compatible_roster": None,
            "error": None,
        }
    try:
        state = load_visual_discovery_state(target)
    except VisualDiscoveryStateError as exc:
        return {
            "exists": True,
            "status": "invalid",
            "completed_passages": 0,
            "total_passages": 0,
            "next_passage": None,
            "reconciliation_complete": False,
            "character_ids": [],
            "compatible_source": None,
            "compatible_roster": None,
            "error": str(exc),
        }
    assert state is not None
    source_match = (
        None
        if current_source is None
        else state["source"]["fingerprint"]
        == current_source["fingerprint"]
    )
    roster_match = (
        None
        if roster_fingerprint is None
        else state["roster_fingerprint"]
        == roster_fingerprint
    )
    completed = len(state["completed_passages"])
    total = state["total_passages"]
    if source_match is False:
        status = "incompatible_source"
    elif roster_match is False:
        status = "incompatible_roster"
    elif state["reconciliation"] is not None:
        status = "complete_pending_write"
    elif completed == total:
        status = "complete_pending_reconciliation"
    else:
        status = "resumable"
    return {
        "exists": True,
        "status": status,
        "completed_passages": completed,
        "total_passages": total,
        "next_passage": (
            completed + 1 if completed < total else None
        ),
        "reconciliation_complete": (
            state["reconciliation"] is not None
        ),
        "character_ids": list(state["character_ids"]),
        "compatible_source": source_match,
        "compatible_roster": roster_match,
        "error": None,
    }


def _resolve_exact_offsets(
    passage_text: str,
    *,
    quote: str,
    claimed_start: int,
    claimed_end: int,
) -> tuple[int, int, bool]:
    if (
        claimed_end <= len(passage_text)
        and passage_text[claimed_start:claimed_end] == quote
    ):
        return claimed_start, claimed_end, False
    positions = []
    cursor = 0
    while True:
        found = passage_text.find(quote, cursor)
        if found < 0:
            break
        positions.append(found)
        cursor = found + 1
    if len(positions) == 1:
        start = positions[0]
        return start, start + len(quote), True
    if not positions:
        raise VisualDiscoveryEvidenceError(
            "Visual evidence quote is not exact passage text."
        )
    raise VisualDiscoveryEvidenceError(
        "Visual evidence quote occurs multiple times and the "
        "supplied offsets do not identify one exact occurrence."
    )


def build_visual_discovery_prompt(
    *,
    passage: dict[str, Any],
    total_passages: int,
    characters: list[dict[str, Any]],
) -> str:
    compact = [
        {
            "character_id": entry["id"],
            "canonical_name": entry["canonical_name"],
            "display_name": entry["display_name"],
            "entity_kind": entry["entity_kind"],
            "aliases": entry["aliases"],
            "titles": entry["titles"],
            "nicknames": entry["nicknames"],
        }
        for entry in characters
    ]
    return (
        "Extract only source-backed visual observations for the "
        "approved character IDs below. Return only the "
        "visual_discovery JSON contract. Do not discover new "
        "characters and do not alter IDs.\n\n"
        "Rules:\n"
        "- Every observation needs one exact quote and zero-based "
        "start_char/end_char relative to this passage.\n"
        "- detail may summarize the quote but may not invent facts.\n"
        "- Use stable only for traits presented as enduring. Use "
        "scene_specific, temporary, injury, disguise, transformation, "
        "age_variant, or unknown when appropriate.\n"
        "- Keep scene clothing and temporary conditions out of stable.\n"
        "- Record nonhuman anatomy explicitly under nonhuman_anatomy.\n"
        "- Do not infer appearance from name, gender, species stereotype, "
        "personality, voice, or role.\n"
        "- Categories must use the published visual profile bucket names.\n"
        "- Empty observations are valid when this passage contains no "
        "visual evidence for the approved IDs.\n\n"
        "APPROVED CHARACTERS:\n"
        + json.dumps(compact, ensure_ascii=False)
        + "\n\n"
        f"PASSAGE {passage['index']} OF {total_passages}\n"
        f"ABSOLUTE RANGE {passage['start_char']}-"
        f"{passage['end_char']}\n\n"
        "SOURCE PASSAGE START\n"
        + passage["text"]
        + "\nSOURCE PASSAGE END"
    )


def normalize_visual_passage_result(
    result: dict[str, Any],
    *,
    passage: dict[str, Any],
    source_text: str,
    allowed_character_ids: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    observations = []
    warnings = list(result.get("warnings", []))
    for index, item in enumerate(result.get("observations", [])):
        character_id = item["character_id"]
        if character_id not in allowed_character_ids:
            raise VisualDiscoveryEvidenceError(
                "Visual observation references an unapproved "
                f"character ID: {character_id}."
            )
        try:
            start, end, repaired = _resolve_exact_offsets(
                passage["text"],
                quote=item["quote"],
                claimed_start=item["start_char"],
                claimed_end=item["end_char"],
            )
        except VisualDiscoveryEvidenceError as exc:
            raise VisualDiscoveryEvidenceError(
                f"Passage {passage['index']} observation {index}: {exc}"
            ) from exc
        absolute_start = passage["start_char"] + start
        absolute_end = passage["start_char"] + end
        if source_text[absolute_start:absolute_end] != item["quote"]:
            raise VisualDiscoveryEvidenceError(
                "Visual observation does not match the whole-book "
                "source at its absolute offsets."
            )
        if repaired:
            warnings.append(
                "Repaired unique exact visual evidence offsets in "
                f"passage {passage['index']} for "
                f"{item['quote'][:80]!r}."
            )
        identity = {
            "character_id": character_id,
            "category": item["category"],
            "detail": item["detail"],
            "scope": item["scope"],
            "start_char": absolute_start,
            "end_char": absolute_end,
        }
        observations.append(
            {
                "character_id": character_id,
                "observation_id": (
                    "visual_"
                    + fingerprint_value(identity)[:24]
                ),
                "category": item["category"],
                "detail": item["detail"],
                "scope": item["scope"],
                "certainty": float(item["certainty"]),
                "basis": item["basis"],
                "source_location": (
                    f"characters {absolute_start}-{absolute_end}"
                ),
                "start_char": absolute_start,
                "end_char": absolute_end,
                "passage_index": passage["index"],
                "quote": item["quote"],
            }
        )
    unique_warnings = []
    seen = set()
    for warning in warnings:
        if warning not in seen:
            seen.add(warning)
            unique_warnings.append(warning)
    return observations, unique_warnings


def completed_visual_observations(
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    state = validate_visual_discovery_state(state)
    result = []
    signatures = set()
    for record in state["completed_passages"]:
        for observation in record["observations"]:
            signature = (
                observation["character_id"],
                observation["category"],
                observation["detail"].casefold(),
                observation["scope"],
                observation["start_char"],
                observation["end_char"],
            )
            if signature in signatures:
                continue
            signatures.add(signature)
            result.append(observation)
    return result


def build_visual_reconciliation_prompt(
    *,
    characters: list[dict[str, Any]],
    observations: list[dict[str, Any]],
) -> str:
    character_map = {
        entry["id"]: {
            "canonical_name": entry["canonical_name"],
            "display_name": entry["display_name"],
            "entity_kind": entry["entity_kind"],
        }
        for entry in characters
    }
    compact_observations = [
        {
            "character_id": item["character_id"],
            "observation_id": item["observation_id"],
            "category": item["category"],
            "detail": item["detail"],
            "scope": item["scope"],
            "certainty": item["certainty"],
            "basis": item["basis"],
            "quote": item["quote"],
            "source_location": item["source_location"],
        }
        for item in observations
    ]
    return (
        "Reconcile validated visual observations into one visual "
        "dossier structure for every approved character ID. Return "
        "only the visual_reconciliation JSON contract.\n\n"
        "Rules:\n"
        "- Return every approved character exactly once, even when it "
        "has no observations.\n"
        "- Reference only supplied observation_ids owned by that "
        "character.\n"
        "- Stable profile facts may reference only stable observations "
        "of the same category.\n"
        "- Scene-specific or temporary facts belong in variants, not "
        "the stable profile.\n"
        "- Keep genuinely contradictory descriptions in conflicts. "
        "Complementary facts are not conflicts.\n"
        "- Do not invent facts. Use unknowns for missing important "
        "profile categories.\n"
        "- Preserve nonhuman anatomy without forcing a human template.\n\n"
        "APPROVED CHARACTERS:\n"
        + json.dumps(character_map, ensure_ascii=False)
        + "\n\nVALIDATED OBSERVATIONS:\n"
        + json.dumps(
            compact_observations,
            ensure_ascii=False,
        )
    )


def validate_reconciliation_integrity(
    *,
    state: dict[str, Any],
    reconciliation: dict[str, Any],
) -> dict[str, Any]:
    state = validate_visual_discovery_state(state)
    expected_ids = set(state["character_ids"])
    characters = reconciliation.get("characters", [])
    returned_ids = [
        item["character_id"]
        for item in characters
    ]
    if set(returned_ids) != expected_ids:
        raise VisualReconciliationError(
            "Visual reconciliation must return every selected "
            "character exactly once."
        )
    if len(returned_ids) != len(set(returned_ids)):
        raise VisualReconciliationError(
            "Visual reconciliation character IDs must be unique."
        )
    observations = completed_visual_observations(state)
    observations_by_id = {
        item["observation_id"]: item
        for item in observations
    }
    normalized = copy.deepcopy(reconciliation)
    for character in normalized["characters"]:
        character_id = character["character_id"]
        own_ids = {
            item["observation_id"]
            for item in observations
            if item["character_id"] == character_id
        }
        for category in PROFILE_BUCKETS:
            for fact in character["profile"][category]:
                linked = fact["observation_ids"]
                _validate_visual_links(
                    linked,
                    own_ids=own_ids,
                    observations_by_id=observations_by_id,
                    category=category,
                    stable_only=True,
                    label=(
                        f"Visual profile {character_id} {category}"
                    ),
                )
        for variant in character["variants"]:
            _validate_visual_links(
                variant["observation_ids"],
                own_ids=own_ids,
                observations_by_id=observations_by_id,
                stable_only=False,
                require_nonstable=True,
                label=f"Visual variant {character_id}",
            )
        for conflict in character["conflicts"]:
            _validate_visual_links(
                conflict["observation_ids"],
                own_ids=own_ids,
                observations_by_id=observations_by_id,
                category=conflict["category"],
                stable_only=False,
                label=f"Visual conflict {character_id}",
            )
    return normalized


def _validate_visual_links(
    linked_ids: list[str],
    *,
    own_ids: set[str],
    observations_by_id: dict[str, dict[str, Any]],
    label: str,
    category: str | None = None,
    stable_only: bool = False,
    require_nonstable: bool = False,
) -> None:
    if not linked_ids:
        raise VisualReconciliationError(
            f"{label} must reference evidence observations."
        )
    unknown = set(linked_ids) - own_ids
    if unknown:
        raise VisualReconciliationError(
            f"{label} references unknown or cross-character "
            f"observations: {sorted(unknown)}."
        )
    if category is not None:
        wrong = [
            observation_id
            for observation_id in linked_ids
            if observations_by_id[observation_id]["category"]
            != category
        ]
        if wrong:
            raise VisualReconciliationError(
                f"{label} references the wrong visual category: "
                f"{wrong}."
            )
    if stable_only:
        nonstable = [
            observation_id
            for observation_id in linked_ids
            if observations_by_id[observation_id]["scope"]
            != "stable"
        ]
        if nonstable:
            raise VisualReconciliationError(
                f"{label} promotes scene-specific evidence into "
                f"the stable profile: {nonstable}."
            )
    if require_nonstable:
        stable = [
            observation_id
            for observation_id in linked_ids
            if observations_by_id[observation_id]["scope"]
            == "stable"
        ]
        if stable:
            raise VisualReconciliationError(
                f"{label} places stable evidence in a variant: "
                f"{stable}."
            )


def build_visual_dossiers_from_state(
    *,
    state: dict[str, Any],
    approved_roster: dict[str, Any],
    source_text: str,
) -> dict[str, dict[str, Any]]:
    state = validate_visual_discovery_state(state)
    if state["reconciliation"] is None:
        raise VisualDiscoveryStateError(
            "Visual reconciliation is not complete."
        )
    reconciliation = validate_reconciliation_integrity(
        state=state,
        reconciliation=state["reconciliation"],
    )
    observations = completed_visual_observations(state)
    observations_by_character = {
        character_id: []
        for character_id in state["character_ids"]
    }
    for observation in observations:
        dossier_observation = {
            key: copy.deepcopy(value)
            for key, value in observation.items()
            if key != "character_id"
        }
        observations_by_character[
            observation["character_id"]
        ].append(dossier_observation)
    dossiers = {}
    for character in reconciliation["characters"]:
        character_id = character["character_id"]
        dossiers[character_id] = build_visual_dossier(
            observations=observations_by_character[character_id],
            profile=character["profile"],
            variants=character["variants"],
            conflicts=character["conflicts"],
            unknowns=character["unknowns"],
            source_text=source_text,
        )
    return dossiers


def run_visual_discovery(
    *,
    runtime_client: Any,
    compilation_runtime_client: Any | None = None,
    source: dict[str, Any],
    source_text: str,
    approved_roster: dict[str, Any],
    character_ids: list[str],
    state_path: str | Path,
    persona_refs_dir: str | Path,
    passage_size: int = DEFAULT_PASSAGE_SIZE,
    overlap_chars: int = DEFAULT_PASSAGE_OVERLAP,
    temperature: float = 0.1,
    max_tokens: int = 5000,
    seed: int | None = 42,
    replace_existing: bool = False,
) -> dict[str, Any]:
    roster_by_id = {
        entry["id"]: entry
        for entry in approved_roster["entries"]
    }
    if not character_ids:
        raise VisualDiscoveryError(
            "At least one character must be selected."
        )
    unknown_ids = set(character_ids) - set(roster_by_id)
    if unknown_ids:
        raise VisualDiscoveryError(
            "Visual collection references unknown approved "
            f"character IDs: {sorted(unknown_ids)}."
        )
    selected = [
        roster_by_id[character_id]
        for character_id in character_ids
    ]
    passages = build_discovery_passages(
        source_text,
        passage_size=passage_size,
        overlap=overlap_chars,
    )
    if not passages:
        raise VisualDiscoveryError(
            "The selected source contains no text to inspect."
        )
    compilation_runtime = (
        compilation_runtime_client or runtime_client
    )
    identity = build_visual_identity(
        runtime_client,
        passage_size=passage_size,
        overlap_chars=overlap_chars,
        temperature=temperature,
        max_tokens=max_tokens,
        seed=seed,
        compilation_runtime_client=compilation_runtime,
    )
    state = prepare_visual_discovery_state(
        path=state_path,
        source=source,
        roster_fingerprint=approved_roster[
            "roster_fingerprint"
        ],
        character_ids=character_ids,
        generation_identity=identity,
        passages=passages,
    )
    completed_count = len(state["completed_passages"])
    if completed_count:
        print(
            "Resuming visual discovery after "
            f"{completed_count}/{len(passages)} passages."
        )
    allowed_ids = set(character_ids)
    for passage in passages[completed_count:]:
        messages = [
            {
                "role": "system",
                "content": (
                    "You produce exact, evidence-backed JSON for "
                    "audiobook character visual discovery."
                ),
            },
            {
                "role": "user",
                "content": build_visual_discovery_prompt(
                    passage=passage,
                    total_passages=len(passages),
                    characters=selected,
                ),
            },
        ]
        observations = []
        warnings = []
        for attempt in range(2):
            result = runtime_client.complete_json(
                messages=messages,
                contract="visual_discovery",
                temperature=temperature,
                max_tokens=max_tokens,
                seed=seed,
            )
            try:
                observations, warnings = (
                    normalize_visual_passage_result(
                        result.data,
                        passage=passage,
                        source_text=source_text,
                        allowed_character_ids=allowed_ids,
                    )
                )
                break
            except VisualDiscoveryEvidenceError as exc:
                if attempt >= 1:
                    raise
                messages.extend(
                    [
                        {
                            "role": "assistant",
                            "content": getattr(
                                result,
                                "content",
                                json.dumps(result.data),
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                "The structure passed, but exact visual "
                                "evidence validation failed.\n\n"
                                f"Error: {exc}\n\n"
                                "Return the complete corrected "
                                "visual_discovery object. Use only exact "
                                "quotes and correct relative offsets. "
                                "Do not invent facts or character IDs."
                            ),
                        },
                    ]
                )
        state = checkpoint_visual_passage(
            state=state,
            path=state_path,
            passage=passage,
            observations=observations,
            warnings=warnings,
        )
    if state["reconciliation"] is None:
        observations = completed_visual_observations(state)
        messages = [
            {
                "role": "system",
                "content": (
                    "You reconcile validated visual observations "
                    "without changing evidence."
                ),
            },
            {
                "role": "user",
                "content": build_visual_reconciliation_prompt(
                    characters=selected,
                    observations=observations,
                ),
            },
        ]
        for attempt in range(2):
            result = compilation_runtime.complete_json(
                messages=messages,
                contract="visual_reconciliation",
                temperature=temperature,
                max_tokens=max_tokens,
                seed=seed,
            )
            try:
                state = checkpoint_visual_reconciliation(
                    state=state,
                    path=state_path,
                    reconciliation=result.data,
                )
                break
            except VisualReconciliationError as exc:
                if attempt >= 1:
                    raise
                messages.extend(
                    [
                        {
                            "role": "assistant",
                            "content": getattr(
                                result,
                                "content",
                                json.dumps(result.data),
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                "The structure passed, but visual "
                                "reconciliation integrity failed.\n\n"
                                f"Error: {exc}\n\n"
                                "Return every selected character once. "
                                "Reference only that character's supplied "
                                "observation IDs. Keep stable and variant "
                                "facts in their proper layers."
                            ),
                        },
                    ]
                )
    dossiers = build_visual_dossiers_from_state(
        state=state,
        approved_roster=approved_roster,
        source_text=source_text,
    )
    ownership = [
        {
            "entry_id": entry["id"],
            "character_name": (
                entry["canonical_name"]
                or entry["display_name"]
            ),
        }
        for entry in approved_roster["entries"]
    ]
    targets = persona_reference_targets(
        persona_refs_dir=persona_refs_dir,
        selected_entries=[
            item
            for item in ownership
            if item["entry_id"] in character_ids
        ],
        all_entries=ownership,
    )
    write_items = []
    for entry in selected:
        character_name = (
            entry["canonical_name"]
            or entry["display_name"]
        )
        write_items.append(
            {
                "persona_ref_path": targets[entry["id"]],
                "visual": dossiers[entry["id"]],
                "character_name": character_name,
                "aliases": [
                    *entry["aliases"],
                    *entry["titles"],
                    *entry["nicknames"],
                ],
                "entry_id": entry["id"],
                "source_fingerprint": source["fingerprint"],
                "roster_fingerprint": approved_roster[
                    "roster_fingerprint"
                ],
            }
        )
    write_visual_dossiers_transaction(
        dossiers=write_items,
        source_text=source_text,
        replace_existing=replace_existing,
    )
    clear_visual_discovery_state(state_path)
    return {
        "status": "complete",
        "character_ids": character_ids,
        "dossiers": dossiers,
        "warnings": [
            warning
            for record in state["completed_passages"]
            for warning in record["warnings"]
        ]
        + list(state["reconciliation"].get("warnings", [])),
    }
