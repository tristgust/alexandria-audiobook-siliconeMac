from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from character_roster import (
    build_draft_roster,
    save_character_roster,
    stable_entry_id,
)
from generation_state import (
    atomic_json_write,
    fingerprint_text,
    fingerprint_value,
)
from llm_schemas import (
    ContractValidationError,
    validate_roster_discovery,
    validate_roster_reconciliation,
)


STATE_SCHEMA_VERSION = 1
DISCOVERY_CONTRACT_VERSION = 1
DEFAULT_PASSAGE_SIZE = 12000
DEFAULT_PASSAGE_OVERLAP = 1200

_STATE_KEYS = frozenset(
    {
        "schema_version",
        "source",
        "generation_identity",
        "generation_fingerprint",
        "passage_layout",
        "total_passages",
        "completed_passages",
        "reconciliation",
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
_LAYOUT_KEYS = frozenset(
    {
        "index",
        "start_char",
        "end_char",
        "fingerprint",
    }
)
_COMPLETED_KEYS = frozenset(
    {
        "index",
        "passage_fingerprint",
        "observations",
        "warnings",
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
_OBSERVATION_KEYS = frozenset(
    {
        "observation_id",
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
)


class RosterDiscoveryError(RuntimeError):
    pass


class RosterDiscoveryCorruptError(RosterDiscoveryError):
    pass


class RosterDiscoveryMismatchError(RosterDiscoveryError):
    pass


class RosterDiscoveryEvidenceError(RosterDiscoveryError):
    pass


class RosterReconciliationError(RosterDiscoveryError):
    pass


def utc_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RosterDiscoveryCorruptError(
            f"{label} must be a JSON object."
        )
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise RosterDiscoveryCorruptError(
            f"{label} must be a JSON array."
        )
    return value


def _require_exact_keys(
    value: dict[str, Any],
    expected: frozenset[str],
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
        raise RosterDiscoveryCorruptError(
            f"{label} has " + "; ".join(details) + "."
        )


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RosterDiscoveryCorruptError(
            f"{label} must be non-empty text."
        )
    return value


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
        raise RosterDiscoveryCorruptError(
            f"{label} must be an integer >= {minimum}."
        )
    return value


def _choose_passage_end(
    text: str,
    start: int,
    hard_end: int,
) -> int:
    if hard_end >= len(text):
        return len(text)

    minimum = start + max(1, int((hard_end - start) * 0.65))
    candidates = (
        "\n\n",
        "\n",
        ". ",
        "? ",
        "! ",
        "; ",
    )

    best = -1
    width = 0

    for marker in candidates:
        position = text.rfind(marker, minimum, hard_end)
        if position > best:
            best = position
            width = len(marker)

    if best >= minimum:
        return best + width

    return hard_end


def build_discovery_passages(
    source_text: str,
    *,
    passage_size: int = DEFAULT_PASSAGE_SIZE,
    overlap: int = DEFAULT_PASSAGE_OVERLAP,
) -> list[dict[str, Any]]:
    if not isinstance(source_text, str):
        raise TypeError("source_text must be text")

    if passage_size < 100:
        raise ValueError("passage_size must be at least 100 characters")

    if overlap < 0 or overlap >= passage_size:
        raise ValueError(
            "overlap must be non-negative and smaller than passage_size"
        )

    if not source_text:
        return []

    passages = []
    start = 0

    while start < len(source_text):
        hard_end = min(len(source_text), start + passage_size)
        end = _choose_passage_end(source_text, start, hard_end)

        if end <= start:
            end = hard_end

        passage_text = source_text[start:end]
        passages.append(
            {
                "index": len(passages) + 1,
                "start_char": start,
                "end_char": end,
                "text": passage_text,
                "fingerprint": fingerprint_text(passage_text),
            }
        )

        if end >= len(source_text):
            break

        next_start = max(start + 1, end - overlap)
        start = next_start

    return passages


def passage_layout(
    passages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "index": passage["index"],
            "start_char": passage["start_char"],
            "end_char": passage["end_char"],
            "fingerprint": passage["fingerprint"],
        }
        for passage in passages
    ]


def build_discovery_identity(
    *,
    model_name: str,
    backend: str,
    passage_size: int,
    overlap: int,
    temperature: float,
    max_tokens: int,
    seed: int | None,
    runtime_identity: dict[str, Any] | None = None,
    discovery_prompt_version: int = DISCOVERY_CONTRACT_VERSION,
    reconciliation_prompt_version: int = DISCOVERY_CONTRACT_VERSION,
) -> dict[str, Any]:
    return {
        "model_name": str(model_name),
        "backend": str(backend),
        "passage_size": int(passage_size),
        "overlap": int(overlap),
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
        "seed": seed,
        "runtime": copy.deepcopy(runtime_identity or {}),
        "discovery_prompt_version": discovery_prompt_version,
        "reconciliation_prompt_version": reconciliation_prompt_version,
        "discovery_contract": "roster_discovery",
        "reconciliation_contract": "roster_reconciliation",
    }


def new_roster_discovery_state(
    *,
    source: dict[str, Any],
    generation_identity: dict[str, Any],
    passages: list[dict[str, Any]],
) -> dict[str, Any]:
    identity = copy.deepcopy(generation_identity)
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "source": copy.deepcopy(source),
        "generation_identity": identity,
        "generation_fingerprint": fingerprint_value(identity),
        "passage_layout": passage_layout(passages),
        "total_passages": len(passages),
        "completed_passages": [],
        "reconciliation": None,
    }


def _validate_source(value: Any) -> dict[str, Any]:
    source = _require_dict(value, "Roster discovery source")
    _require_exact_keys(source, _SOURCE_KEYS, "Roster discovery source")
    character_count = _require_int(
        source["character_count"],
        "Roster discovery source.character_count",
    )
    return {
        "path": _require_text(
            source["path"],
            "Roster discovery source.path",
        ),
        "basename": _require_text(
            source["basename"],
            "Roster discovery source.basename",
        ),
        "fingerprint": _require_text(
            source["fingerprint"],
            "Roster discovery source.fingerprint",
        ),
        "character_count": character_count,
    }


def _validate_layout(value: Any) -> list[dict[str, Any]]:
    result = []

    for expected_index, raw in enumerate(
        _require_list(value, "Roster discovery passage_layout"),
        start=1,
    ):
        item = _require_dict(raw, f"Passage layout {expected_index}")
        _require_exact_keys(
            item,
            _LAYOUT_KEYS,
            f"Passage layout {expected_index}",
        )
        index = _require_int(
            item["index"],
            f"Passage layout {expected_index}.index",
            minimum=1,
        )
        start = _require_int(
            item["start_char"],
            f"Passage layout {expected_index}.start_char",
        )
        end = _require_int(
            item["end_char"],
            f"Passage layout {expected_index}.end_char",
            minimum=1,
        )

        if index != expected_index:
            raise RosterDiscoveryCorruptError(
                "Roster discovery passages must be contiguous and ordered."
            )

        if end <= start:
            raise RosterDiscoveryCorruptError(
                f"Passage layout {expected_index} has invalid offsets."
            )

        result.append(
            {
                "index": index,
                "start_char": start,
                "end_char": end,
                "fingerprint": _require_text(
                    item["fingerprint"],
                    f"Passage layout {expected_index}.fingerprint",
                ),
            }
        )

    return result


def _validate_state_evidence(
    value: Any,
    label: str,
) -> dict[str, Any]:
    evidence = _require_dict(value, label)
    _require_exact_keys(evidence, _EVIDENCE_KEYS, label)
    quote = _require_text(evidence["source_quote"], f"{label}.source_quote")
    location = _require_text(
        evidence["source_location"],
        f"{label}.source_location",
    )
    start = _require_int(evidence["start_char"], f"{label}.start_char")
    end = _require_int(
        evidence["end_char"],
        f"{label}.end_char",
        minimum=1,
    )
    passage_index = _require_int(
        evidence["passage_index"],
        f"{label}.passage_index",
        minimum=1,
    )
    batch_index = _require_int(
        evidence["batch_index"],
        f"{label}.batch_index",
        minimum=1,
    )

    if end <= start:
        raise RosterDiscoveryCorruptError(
            f"{label} has invalid source offsets."
        )

    if len(quote) != end - start:
        raise RosterDiscoveryCorruptError(
            f"{label}.source_quote length does not match its offsets."
        )

    if location != f"characters {start}-{end}":
        raise RosterDiscoveryCorruptError(
            f"{label}.source_location does not match its offsets."
        )

    entry_index = evidence["entry_index"]
    if entry_index is not None:
        entry_index = _require_int(
            entry_index,
            f"{label}.entry_index",
        )

    confidence = evidence["confidence"]
    if (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not 0.0 <= float(confidence) <= 1.0
    ):
        raise RosterDiscoveryCorruptError(
            f"{label}.confidence must be between 0.0 and 1.0."
        )

    category = _require_text(evidence["category"], f"{label}.category")
    if category not in {
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
    }:
        raise RosterDiscoveryCorruptError(
            f"{label}.category is invalid."
        )

    basis = _require_text(evidence["basis"], f"{label}.basis")

    if basis not in {"explicit", "inferred"}:
        raise RosterDiscoveryCorruptError(
            f"{label}.basis is invalid."
        )

    return {
        "source_quote": quote,
        "source_location": location,
        "start_char": start,
        "end_char": end,
        "passage_index": passage_index,
        "entry_index": entry_index,
        "batch_index": batch_index,
        "category": category,
        "confidence": float(confidence),
        "basis": basis,
    }


def _validate_observation(
    value: Any,
    label: str,
) -> dict[str, Any]:
    observation = _require_dict(value, label)
    _require_exact_keys(observation, _OBSERVATION_KEYS, label)

    raw_evidence = observation["evidence"]
    if not isinstance(raw_evidence, list) or not raw_evidence:
        raise RosterDiscoveryCorruptError(
            f"{label}.evidence must be a non-empty list."
        )

    normalized = copy.deepcopy(observation)
    observation_id = _require_text(
        normalized["observation_id"],
        f"{label}.observation_id",
    )
    if (
        not observation_id.startswith("observation_")
        or len(observation_id) != len("observation_") + 24
        or any(
            character not in "0123456789abcdef"
            for character in observation_id[len("observation_"):]
        )
    ):
        raise RosterDiscoveryCorruptError(
            f"{label}.observation_id is invalid."
        )
    _require_text(normalized["identity_seed"], f"{label}.identity_seed")

    for key in (
        "canonical_name",
        "display_name",
        "entity_kind",
        "speaking_status",
        "resolution_status",
    ):
        if not isinstance(normalized[key], str):
            raise RosterDiscoveryCorruptError(
                f"{label}.{key} must be text."
            )

    if normalized["entity_kind"] not in {
        "character",
        "group",
        "creature",
        "narrator_role",
        "named_non_speaker",
        "unknown",
    }:
        raise RosterDiscoveryCorruptError(
            f"{label}.entity_kind is invalid."
        )

    if normalized["speaking_status"] not in {
        "speaker",
        "non_speaker",
        "uncertain",
        "narrator",
    }:
        raise RosterDiscoveryCorruptError(
            f"{label}.speaking_status is invalid."
        )

    if normalized["resolution_status"] not in {
        "resolved",
        "unresolved",
        "unnamed",
        "duplicate_candidate",
    }:
        raise RosterDiscoveryCorruptError(
            f"{label}.resolution_status is invalid."
        )

    for key in (
        "titles",
        "aliases",
        "nicknames",
        "pronouns",
        "species",
        "relationships",
        "voice_clues",
        "sample_lines",
        "unresolved_questions",
    ):
        if (
            not isinstance(normalized[key], list)
            or not all(isinstance(item, str) for item in normalized[key])
        ):
            raise RosterDiscoveryCorruptError(
                f"{label}.{key} must be a list of text."
            )

    confidence = normalized["confidence"]
    if (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not 0.0 <= float(confidence) <= 1.0
    ):
        raise RosterDiscoveryCorruptError(
            f"{label}.confidence must be between 0.0 and 1.0."
        )

    normalized["evidence"] = [
        _validate_state_evidence(
            item,
            f"{label}.evidence[{index}]",
        )
        for index, item in enumerate(raw_evidence)
    ]
    return normalized


def validate_roster_discovery_state(value: Any) -> dict[str, Any]:
    state = _require_dict(value, "Roster discovery state")
    _require_exact_keys(state, _STATE_KEYS, "Roster discovery state")

    if state["schema_version"] != STATE_SCHEMA_VERSION:
        raise RosterDiscoveryCorruptError(
            "Unsupported roster discovery state schema."
        )

    source = _validate_source(state["source"])
    generation_identity = _require_dict(
        state["generation_identity"],
        "Roster discovery generation_identity",
    )
    generation_fingerprint = _require_text(
        state["generation_fingerprint"],
        "Roster discovery generation_fingerprint",
    )

    if generation_fingerprint != fingerprint_value(generation_identity):
        raise RosterDiscoveryCorruptError(
            "Roster discovery generation fingerprint does not match "
            "its identity."
        )

    layout = _validate_layout(state["passage_layout"])
    total = _require_int(
        state["total_passages"],
        "Roster discovery total_passages",
    )

    if total != len(layout):
        raise RosterDiscoveryCorruptError(
            "Roster discovery total_passages does not match passage_layout."
        )

    if layout:
        if layout[0]["start_char"] != 0:
            raise RosterDiscoveryCorruptError(
                "Roster discovery passage layout must begin at character 0."
            )
        if layout[-1]["end_char"] != source["character_count"]:
            raise RosterDiscoveryCorruptError(
                "Roster discovery passage layout must end at the source "
                "character count."
            )
        for previous, current in zip(layout, layout[1:]):
            if current["start_char"] >= previous["end_char"]:
                raise RosterDiscoveryCorruptError(
                    "Roster discovery passage layout must overlap without "
                    "gaps."
                )

    completed = []
    observation_ids: set[str] = set()

    for expected_index, raw in enumerate(
        _require_list(
            state["completed_passages"],
            "Roster discovery completed_passages",
        ),
        start=1,
    ):
        item = _require_dict(raw, f"Completed passage {expected_index}")
        _require_exact_keys(
            item,
            _COMPLETED_KEYS,
            f"Completed passage {expected_index}",
        )
        index = _require_int(
            item["index"],
            f"Completed passage {expected_index}.index",
            minimum=1,
        )

        if index != expected_index:
            raise RosterDiscoveryCorruptError(
                "Completed roster passages must be contiguous and ordered."
            )

        if index > total:
            raise RosterDiscoveryCorruptError(
                "Completed roster passage exceeds total passages."
            )

        passage_fingerprint = _require_text(
            item["passage_fingerprint"],
            f"Completed passage {expected_index}.passage_fingerprint",
        )

        if passage_fingerprint != layout[index - 1]["fingerprint"]:
            raise RosterDiscoveryCorruptError(
                "Completed roster passage fingerprint does not match layout."
            )

        observations = [
            _validate_observation(
                observation,
                f"Completed passage {expected_index}.observations[{offset}]",
            )
            for offset, observation in enumerate(
                _require_list(
                    item["observations"],
                    f"Completed passage {expected_index}.observations",
                )
            )
        ]

        for observation in observations:
            observation_id = observation["observation_id"]
            if observation_id in observation_ids:
                raise RosterDiscoveryCorruptError(
                    "Roster discovery observation IDs must be unique."
                )
            observation_ids.add(observation_id)

            for evidence in observation["evidence"]:
                if (
                    evidence["passage_index"] != expected_index
                    or evidence["batch_index"] != expected_index
                ):
                    raise RosterDiscoveryCorruptError(
                        "Roster discovery evidence passage indices do not "
                        "match their checkpoint."
                    )
                if evidence["end_char"] > source["character_count"]:
                    raise RosterDiscoveryCorruptError(
                        "Roster discovery evidence exceeds the source."
                    )
                layout_item = layout[expected_index - 1]
                if (
                    evidence["start_char"] < layout_item["start_char"]
                    or evidence["end_char"] > layout_item["end_char"]
                ):
                    raise RosterDiscoveryCorruptError(
                        "Roster discovery evidence exceeds its checkpoint "
                        "passage."
                    )

        warnings = item["warnings"]
        if (
            not isinstance(warnings, list)
            or not all(isinstance(warning, str) for warning in warnings)
        ):
            raise RosterDiscoveryCorruptError(
                f"Completed passage {expected_index}.warnings must be text."
            )

        completed.append(
            {
                "index": index,
                "passage_fingerprint": passage_fingerprint,
                "observations": observations,
                "warnings": list(warnings),
            }
        )

    if len(completed) > total:
        raise RosterDiscoveryCorruptError(
            "Roster discovery has too many completed passages."
        )

    reconciliation = state["reconciliation"]
    if reconciliation is not None:
        if len(completed) != total:
            raise RosterDiscoveryCorruptError(
                "Roster reconciliation cannot exist before all passages "
                "are complete."
            )
        try:
            reconciliation = validate_roster_reconciliation(
                reconciliation
            )
            validate_reconciliation_partition(
                reconciliation,
                [
                    observation
                    for record in completed
                    for observation in record["observations"]
                ],
            )
        except (
            ContractValidationError,
            RosterReconciliationError,
        ) as exc:
            raise RosterDiscoveryCorruptError(
                f"Roster discovery reconciliation is invalid: {exc}"
            ) from exc

    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "source": source,
        "generation_identity": copy.deepcopy(generation_identity),
        "generation_fingerprint": generation_fingerprint,
        "passage_layout": layout,
        "total_passages": total,
        "completed_passages": completed,
        "reconciliation": copy.deepcopy(reconciliation),
    }


def load_roster_discovery_state(
    path: str | Path,
) -> dict[str, Any] | None:
    target = Path(path)
    if not target.exists():
        return None

    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise RosterDiscoveryCorruptError(
            f"Roster discovery state could not be read: {exc}"
        ) from exc

    return validate_roster_discovery_state(value)


def prepare_roster_discovery_state(
    *,
    path: str | Path,
    source: dict[str, Any],
    generation_identity: dict[str, Any],
    passages: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = new_roster_discovery_state(
        source=source,
        generation_identity=generation_identity,
        passages=passages,
    )
    existing = load_roster_discovery_state(path)

    if existing is None:
        atomic_json_write(expected, path)
        return expected

    mismatches = []

    if existing["source"]["fingerprint"] != expected["source"]["fingerprint"]:
        mismatches.append("source")

    if existing["generation_fingerprint"] != expected["generation_fingerprint"]:
        mismatches.append("generation configuration")

    if existing["passage_layout"] != expected["passage_layout"]:
        mismatches.append("passage layout")

    if mismatches:
        raise RosterDiscoveryMismatchError(
            "Existing character-roster discovery state does not match "
            "the current " + ", ".join(mismatches) + ". Discard saved "
            "roster discovery progress explicitly before starting a "
            "different run."
        )

    return existing


def _resolve_exact_quote_offsets(
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

    occurrences = []
    cursor = 0

    while True:
        found = passage_text.find(quote, cursor)
        if found < 0:
            break
        occurrences.append(found)
        cursor = found + 1

    if len(occurrences) == 1:
        start = occurrences[0]
        return start, start + len(quote), True

    if not occurrences:
        raise RosterDiscoveryEvidenceError(
            "Roster evidence quote is not exact source text from "
            "the passage."
        )

    raise RosterDiscoveryEvidenceError(
        "Roster evidence quote occurs multiple times in the passage "
        "and its supplied offsets do not identify one exact occurrence."
    )


def _claim_value_in_quote(
    value: str,
    quote: str,
    *,
    token_match: bool = False,
) -> bool:
    normalized_value = " ".join(value.casefold().split())
    normalized_quote = " ".join(quote.casefold().split())

    if not normalized_value:
        return False

    if token_match:
        return bool(
            re.search(
                r"(?<![\w])"
                + re.escape(normalized_value)
                + r"(?![\w])",
                normalized_quote,
            )
        )

    return normalized_value in normalized_quote


def _derive_speaking_evidence(
    entity: dict[str, Any],
    evidence: list[dict[str, Any]],
    *,
    passage: dict[str, Any],
    warnings: list[str],
) -> None:
    if any(
        item["category"] == "speaking"
        for item in evidence
    ):
        return

    status = entity["speaking_status"]
    candidate = None
    basis = "inferred"

    if status == "non_speaker":
        silence_patterns = (
            "remained silent",
            "was silent",
            "did not speak",
            "does not speak",
            "never spoke",
            "not a speaking character",
            "non-speaking",
            "nonspeaking",
        )
        for item in evidence:
            text = item["source_quote"].casefold()
            if any(pattern in text for pattern in silence_patterns):
                candidate = item
                basis = "explicit"
                break

    elif status == "speaker":
        dialogue_marks = ('"', "“", "”", "‘", "’")
        attribution_words = (
            " said",
            " asked",
            " replied",
            " answered",
            " spoke",
            " whispered",
            " shouted",
            " called",
            " cried",
        )
        for item in evidence:
            text = item["source_quote"]
            lowered = text.casefold()
            if (
                any(mark in text for mark in dialogue_marks)
                or any(word in lowered for word in attribution_words)
            ):
                candidate = item
                basis = item["basis"]
                break

        if candidate is None:
            for sample_line in entity["sample_lines"]:
                if not any(
                    mark in sample_line
                    for mark in dialogue_marks
                ):
                    continue
                start, end, _ = _resolve_exact_quote_offsets(
                    passage["text"],
                    quote=sample_line,
                    claimed_start=0,
                    claimed_end=0,
                )
                candidate = {
                    "source_quote": sample_line,
                    "source_location": (
                        "characters "
                        f"{passage['start_char'] + start}-"
                        f"{passage['start_char'] + end}"
                    ),
                    "start_char": passage["start_char"] + start,
                    "end_char": passage["start_char"] + end,
                    "passage_index": passage["index"],
                    "entry_index": None,
                    "batch_index": passage["index"],
                    "category": "speaking",
                    "confidence": entity["confidence"],
                    "basis": "explicit",
                }
                break

    if candidate is None:
        return

    derived = copy.deepcopy(candidate)
    derived["category"] = "speaking"
    derived["basis"] = basis
    evidence.append(derived)
    warnings.append(
        "Derived speaking evidence from an exact explicit speech or "
        f"silence quote for {entity['display_name']!r}."
    )


def _sanitize_optional_claims(
    entity: dict[str, Any],
    evidence: list[dict[str, Any]],
    *,
    warnings: list[str],
) -> None:
    exact_fields = (
        ("titles", "title", False),
        ("aliases", "alias", False),
        ("nicknames", "nickname", False),
        ("pronouns", "pronoun", True),
        ("species", "species", False),
        ("voice_clues", "voice", False),
    )

    for field, category, token_match in exact_fields:
        category_evidence = [
            item
            for item in evidence
            if item["category"] == category
        ]
        supported = []
        dropped = []

        for value in entity[field]:
            if any(
                _claim_value_in_quote(
                    value,
                    item["source_quote"],
                    token_match=token_match,
                )
                for item in category_evidence
            ):
                supported.append(value)
            else:
                dropped.append(value)

        if dropped:
            warnings.append(
                f"Dropped unsupported {field} for "
                f"{entity['display_name']!r}: "
                + ", ".join(dropped)
                + "."
            )

        entity[field] = supported

        if not supported:
            for item in category_evidence:
                item["category"] = "other"

    if entity["relationships"] and not any(
        item["category"] == "relationship"
        for item in evidence
    ):
        warnings.append(
            "Dropped unsupported relationships for "
            f"{entity['display_name']!r}."
        )
        entity["relationships"] = []


def _require_core_claim_evidence(
    entity: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> None:
    categories = {
        item["category"]
        for item in evidence
    }
    missing = []

    if (
        entity["canonical_name"]
        and not categories.intersection(
            {"name", "alias", "title", "nickname"}
        )
    ):
        missing.append("canonical identity")

    if (
        entity["speaking_status"]
        in {"speaker", "non_speaker", "narrator"}
        and "speaking" not in categories
    ):
        missing.append("speaking_status")

    if missing:
        raise RosterDiscoveryEvidenceError(
            "Roster discovery claims lack category-matched evidence: "
            + ", ".join(missing)
            + "."
        )


def normalize_passage_result(
    result: Any,
    *,
    passage: dict[str, Any],
    source_fingerprint: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    validated = validate_roster_discovery(result)
    passage_text = passage["text"]
    observations = []
    warnings = list(validated["warnings"])

    for entity_index, entity in enumerate(validated["entities"]):
        evidence = []

        for evidence_index, item in enumerate(entity["evidence"]):
            start = item["start_char"]
            end = item["end_char"]
            quote = item["quote"]

            try:
                start, end, repaired = (
                    _resolve_exact_quote_offsets(
                        passage_text,
                        quote=quote,
                        claimed_start=start,
                        claimed_end=end,
                    )
                )
            except RosterDiscoveryEvidenceError as exc:
                raise RosterDiscoveryEvidenceError(
                    f"Passage {passage['index']} evidence "
                    f"{evidence_index}: {exc}"
                ) from exc

            if repaired:
                warnings.append(
                    "Repaired unique exact evidence offsets in "
                    f"passage {passage['index']} for quote "
                    f"{quote[:80]!r}."
                )

            absolute_start = passage["start_char"] + start
            absolute_end = passage["start_char"] + end
            evidence.append(
                {
                    "source_quote": quote,
                    "source_location": (
                        f"characters {absolute_start}-{absolute_end}"
                    ),
                    "start_char": absolute_start,
                    "end_char": absolute_end,
                    "passage_index": passage["index"],
                    "entry_index": None,
                    "batch_index": passage["index"],
                    "category": item["category"],
                    "confidence": item["confidence"],
                    "basis": item["basis"],
                }
            )

        for sample_index, sample_line in enumerate(entity["sample_lines"]):
            if sample_line not in passage_text:
                raise RosterDiscoveryEvidenceError(
                    f"Passage {passage['index']} sample line {sample_index} "
                    "is not exact source text from the passage."
                )

        _derive_speaking_evidence(
            entity,
            evidence,
            passage=passage,
            warnings=warnings,
        )
        _sanitize_optional_claims(
            entity,
            evidence,
            warnings=warnings,
        )
        _require_core_claim_evidence(
            entity,
            evidence,
        )

        observation_payload = {
            "source_fingerprint": source_fingerprint,
            "passage_index": passage["index"],
            "entity_index": entity_index,
            "identity_seed": entity["identity_seed"],
            "evidence": evidence,
        }
        observations.append(
            {
                "observation_id": (
                    "observation_"
                    + fingerprint_value(observation_payload)[:24]
                ),
                "identity_seed": entity["identity_seed"],
                "canonical_name": entity["canonical_name"],
                "display_name": entity["display_name"],
                "entity_kind": entity["entity_kind"],
                "speaking_status": entity["speaking_status"],
                "titles": entity["titles"],
                "aliases": entity["aliases"],
                "nicknames": entity["nicknames"],
                "pronouns": entity["pronouns"],
                "species": entity["species"],
                "relationships": entity["relationships"],
                "voice_clues": entity["voice_clues"],
                "sample_lines": entity["sample_lines"],
                "confidence": entity["confidence"],
                "resolution_status": entity["resolution_status"],
                "unresolved_questions": entity["unresolved_questions"],
                "evidence": evidence,
            }
        )

    return observations, _ordered_unique(warnings)


def checkpoint_roster_passage(
    *,
    state: dict[str, Any],
    path: str | Path,
    passage: dict[str, Any],
    observations: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    current = validate_roster_discovery_state(state)
    expected_index = len(current["completed_passages"]) + 1

    if passage["index"] != expected_index:
        raise RosterDiscoveryError(
            "Roster discovery checkpoint must be the next contiguous "
            "passage."
        )

    layout = current["passage_layout"][expected_index - 1]
    if (
        passage["fingerprint"] != layout["fingerprint"]
        or passage["start_char"] != layout["start_char"]
        or passage["end_char"] != layout["end_char"]
    ):
        raise RosterDiscoveryMismatchError(
            "Roster discovery passage does not match prepared state."
        )

    updated = {
        **current,
        "completed_passages": [
            *current["completed_passages"],
            {
                "index": expected_index,
                "passage_fingerprint": passage["fingerprint"],
                "observations": copy.deepcopy(observations),
                "warnings": list(warnings),
            },
        ],
    }
    normalized = validate_roster_discovery_state(updated)
    atomic_json_write(normalized, path)
    return normalized


def completed_observations(
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    normalized = validate_roster_discovery_state(state)
    return [
        copy.deepcopy(observation)
        for record in normalized["completed_passages"]
        for observation in record["observations"]
    ]


def validate_reconciliation_partition(
    reconciliation: dict[str, Any],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized = validate_roster_reconciliation(reconciliation)
    available = {
        observation["observation_id"]
        for observation in observations
    }
    assigned: list[str] = []
    identity_seeds = []

    for entry in normalized["entries"]:
        assigned.extend(entry["observation_ids"])
        identity_seeds.append(entry["identity_seed"])

    excluded = normalized["excluded_observation_ids"]
    assigned.extend(excluded)

    if len(assigned) != len(set(assigned)):
        raise RosterReconciliationError(
            "Each roster observation must be assigned or excluded exactly "
            "once."
        )

    assigned_set = set(assigned)
    unknown = assigned_set - available
    missing = available - assigned_set

    if unknown:
        raise RosterReconciliationError(
            "Roster reconciliation references unknown observations: "
            f"{sorted(unknown)}."
        )

    if missing:
        raise RosterReconciliationError(
            "Roster reconciliation omitted observations: "
            f"{sorted(missing)}."
        )

    if len(identity_seeds) != len(set(identity_seeds)):
        raise RosterReconciliationError(
            "Roster reconciliation identity seeds must be unique."
        )

    seed_set = set(identity_seeds)
    entry_observations = {
        entry["identity_seed"]: set(entry["observation_ids"])
        for entry in normalized["entries"]
    }

    for entry in normalized["entries"]:
        unknown_seeds = set(entry["possible_duplicate_seeds"]) - seed_set
        if unknown_seeds:
            raise RosterReconciliationError(
                f"Roster entry {entry['identity_seed']!r} references "
                f"unknown duplicate seeds: {sorted(unknown_seeds)}."
            )
        if entry["identity_seed"] in entry["possible_duplicate_seeds"]:
            raise RosterReconciliationError(
                "A roster entry cannot be its own duplicate candidate."
            )

    for candidate in normalized["duplicate_candidates"]:
        seeds = candidate["identity_seeds"]
        if not set(seeds).issubset(seed_set):
            raise RosterReconciliationError(
                "Duplicate candidate references unknown identity seeds."
            )
        allowed_observations = (
            entry_observations[seeds[0]]
            | entry_observations[seeds[1]]
        )
        if not set(candidate["observation_ids"]).issubset(
            allowed_observations
        ):
            raise RosterReconciliationError(
                "Duplicate candidate evidence must belong to the two "
                "candidate identities."
            )

    return normalized


def checkpoint_roster_reconciliation(
    *,
    state: dict[str, Any],
    path: str | Path,
    reconciliation: dict[str, Any],
) -> dict[str, Any]:
    current = validate_roster_discovery_state(state)

    if len(current["completed_passages"]) != current["total_passages"]:
        raise RosterDiscoveryError(
            "All roster discovery passages must be complete before "
            "reconciliation."
        )

    normalized_reconciliation = validate_reconciliation_partition(
        reconciliation,
        completed_observations(current),
    )
    updated = {
        **current,
        "reconciliation": normalized_reconciliation,
    }
    normalized = validate_roster_discovery_state(updated)
    atomic_json_write(normalized, path)
    return normalized


def _ordered_unique(values: list[str]) -> list[str]:
    seen = set()
    result = []

    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)

    return result


def _deduplicate_evidence(
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen = set()
    result = []

    for item in sorted(
        evidence,
        key=lambda value: (
            value["start_char"],
            value["end_char"],
            value["category"],
            value["source_quote"],
        ),
    ):
        key = fingerprint_value(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(copy.deepcopy(item))

    return result


def build_draft_from_discovery_state(
    state: dict[str, Any],
    *,
    source_text: str,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    normalized = validate_roster_discovery_state(state)

    if normalized["reconciliation"] is None:
        raise RosterDiscoveryError(
            "Roster reconciliation is required before draft finalization."
        )

    observations = completed_observations(normalized)
    by_id = {
        observation["observation_id"]: observation
        for observation in observations
    }
    reconciliation = normalized["reconciliation"]
    source = normalized["source"]
    entries = []
    seed_to_id: dict[str, str] = {}
    pending_duplicates: dict[str, list[str]] = {}

    for reconciled in reconciliation["entries"]:
        grouped = [by_id[item] for item in reconciled["observation_ids"]]
        identity_material = (
            source["fingerprint"]
            + ":"
            + "|".join(sorted(reconciled["observation_ids"]))
        )
        entry_id = stable_entry_id(identity_material)
        seed_to_id[reconciled["identity_seed"]] = entry_id
        pending_duplicates[entry_id] = list(
            reconciled["possible_duplicate_seeds"]
        )
        evidence = _deduplicate_evidence(
            [
                item
                for observation in grouped
                for item in observation["evidence"]
            ]
        )
        locations = _ordered_unique(
            [item["source_location"] for item in evidence]
        )
        unresolved_questions = _ordered_unique(
            [
                *reconciled["unresolved_questions"],
                *[
                    question
                    for observation in grouped
                    for question in observation["unresolved_questions"]
                ],
            ]
        )

        entries.append(
            {
                "id": entry_id,
                "canonical_name": reconciled["canonical_name"],
                "display_name": reconciled["display_name"],
                "entity_kind": reconciled["entity_kind"],
                "speaking_status": reconciled["speaking_status"],
                "titles": _ordered_unique(
                    [item for observation in grouped for item in observation["titles"]]
                ),
                "aliases": _ordered_unique(
                    [item for observation in grouped for item in observation["aliases"]]
                ),
                "nicknames": _ordered_unique(
                    [item for observation in grouped for item in observation["nicknames"]]
                ),
                "pronouns": _ordered_unique(
                    [item for observation in grouped for item in observation["pronouns"]]
                ),
                "species": _ordered_unique(
                    [item for observation in grouped for item in observation["species"]]
                ),
                "relationships": _ordered_unique(
                    [item for observation in grouped for item in observation["relationships"]]
                ),
                "first_evidence_location": locations[0],
                "additional_evidence_locations": locations[1:],
                "confidence": reconciled["confidence"],
                "resolution_status": reconciled["resolution_status"],
                "possible_duplicate_ids": [],
                "mistaken_merge_risk": reconciled["mistaken_merge_risk"],
                "unresolved_questions": unresolved_questions,
                "evidence": evidence,
                "voice_clues": _ordered_unique(
                    [item for observation in grouped for item in observation["voice_clues"]]
                ),
                "sample_lines": _ordered_unique(
                    [item for observation in grouped for item in observation["sample_lines"]]
                ),
            }
        )

    for entry in entries:
        entry["possible_duplicate_ids"] = [
            seed_to_id[seed]
            for seed in pending_duplicates[entry["id"]]
        ]

    entry_by_seed = {
        seed: next(entry for entry in entries if entry["id"] == entry_id)
        for seed, entry_id in seed_to_id.items()
    }
    duplicate_candidates = []

    for candidate in reconciliation["duplicate_candidates"]:
        evidence = _deduplicate_evidence(
            [
                item
                for observation_id in candidate["observation_ids"]
                for item in by_id[observation_id]["evidence"]
            ]
        )
        duplicate_candidates.append(
            {
                "entry_ids": [
                    entry_by_seed[seed]["id"]
                    for seed in candidate["identity_seeds"]
                ],
                "reason": candidate["reason"],
                "confidence": candidate["confidence"],
                "evidence": evidence,
            }
        )

    excluded_entities = []

    for observation_id in reconciliation["excluded_observation_ids"]:
        observation = by_id[observation_id]
        excluded_entities.append(
            {
                "name": (
                    observation["display_name"]
                    or observation["canonical_name"]
                    or observation["identity_seed"]
                ),
                "reason": "Excluded during global roster reconciliation.",
                "evidence": _deduplicate_evidence(observation["evidence"]),
            }
        )

    unresolved = []
    entry_by_id = {entry["id"]: entry for entry in entries}

    for entry_id, entry in entry_by_id.items():
        if entry["resolution_status"] not in {"unresolved", "unnamed"}:
            continue
        questions = entry["unresolved_questions"] or [
            "This identity requires user review."
        ]
        for question in questions:
            unresolved.append(
                {
                    "entry_id": entry_id,
                    "question": question,
                    "confidence": entry["confidence"],
                }
            )

    warnings = _ordered_unique(
        [
            *[
                warning
                for record in normalized["completed_passages"]
                for warning in record["warnings"]
            ],
            *reconciliation["warnings"],
        ]
    )
    identity = normalized["generation_identity"]

    return build_draft_roster(
        source=source,
        discovery={
            "created_at_utc": generated_at_utc or utc_timestamp(),
            "model_name": str(identity["model_name"]),
            "backend": str(identity["backend"]),
            "generation_fingerprint": normalized[
                "generation_fingerprint"
            ],
            "batch_count": normalized["total_passages"],
            "completed_batches": len(normalized["completed_passages"]),
        },
        entries=entries,
        unresolved=unresolved,
        duplicate_candidates=duplicate_candidates,
        excluded_entities=excluded_entities,
        warnings=warnings,
        source_text=source_text,
    )


def clear_roster_discovery_state(path: str | Path) -> bool:
    target = Path(path)
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return False


def inspect_roster_discovery_state(
    path: str | Path,
    *,
    current_source: dict[str, Any] | None,
) -> dict[str, Any]:
    target = Path(path)

    if not target.exists():
        return {
            "exists": False,
            "status": "missing",
            "completed_passages": 0,
            "total_passages": 0,
            "next_passage": None,
            "reconciliation_complete": False,
            "compatible_source": None,
            "error": None,
        }

    try:
        state = load_roster_discovery_state(target)
    except RosterDiscoveryCorruptError as exc:
        return {
            "exists": True,
            "status": "corrupt",
            "completed_passages": 0,
            "total_passages": 0,
            "next_passage": None,
            "reconciliation_complete": False,
            "compatible_source": None,
            "error": str(exc),
        }

    assert state is not None
    completed = len(state["completed_passages"])
    total = state["total_passages"]
    compatible = (
        None
        if current_source is None
        else state["source"]["fingerprint"]
        == current_source["fingerprint"]
    )

    if compatible is False:
        status = "incompatible_source"
    elif state["reconciliation"] is not None:
        status = "ready_to_finalize"
    elif completed == total:
        status = "awaiting_reconciliation"
    else:
        status = "resumable"

    return {
        "exists": True,
        "status": status,
        "completed_passages": completed,
        "total_passages": total,
        "next_passage": completed + 1 if completed < total else None,
        "reconciliation_complete": state["reconciliation"] is not None,
        "compatible_source": compatible,
        "generation_fingerprint": state["generation_fingerprint"],
        "error": None,
    }


def build_roster_discovery_prompt(
    passage: dict[str, Any],
    *,
    total_passages: int,
) -> str:
    return (
        "Analyze this exact source-book passage for character identity "
        "evidence. Return only the roster_discovery JSON contract. "
        "You may discover named speakers, unnamed speakers, creatures, "
        "groups, narrator roles, and named non-speakers. Preserve "
        "ambiguity instead of guessing.\n\n"
        "Output shape rules:\n"
        "- The top-level object must contain exactly entities and "
        "warnings. Do not wrap it in roster_discovery or any other "
        "key.\n"
        "- entity_kind must be exactly one of: character, group, "
        "creature, narrator_role, named_non_speaker, unknown. Human "
        "people are character; aliens and nonhuman beings are creature; "
        "silent named machines, places, and objects are "
        "named_non_speaker. Put biological species in species, not in "
        "entity_kind.\n"
        "- speaking_status must be exactly one of: speaker, "
        "non_speaker, uncertain, narrator.\n"
        "- resolution_status must be exactly one of: resolved, "
        "unresolved, unnamed, duplicate_candidate.\n"
        "- evidence category must be exactly one of: name, alias, title, "
        "nickname, pronoun, species, relationship, speaking, voice, "
        "visual, other.\n"
        "- evidence basis must be exactly explicit or inferred.\n\n"
        "Evidence rules:\n"
        "- Every entity must include at least one exact quote from the "
        "passage.\n"
        "- start_char and end_char are zero-based offsets relative to "
        "the passage below.\n"
        "- passage[start_char:end_char] must equal quote exactly, "
        "including whitespace, punctuation, and capitalization.\n"
        "- Sample lines must also be exact substrings of this passage.\n"
        "- Mark each fact explicit or inferred and confidence 0-1.\n"
        "- Every populated claim requires matching evidence category: "
        "canonical name -> name/title/alias/nickname; titles -> title; "
        "aliases -> alias; nicknames -> nickname; pronouns -> pronoun; "
        "species -> species; relationships -> relationship; "
        "speaking_status other than uncertain -> speaking; voice_clues "
        "-> voice. Duplicate an exact quote with different categories "
        "when one passage supports multiple facts.\n"
        "- Do not label a person human or assign any species unless the "
        "passage explicitly or inferentially supports it and includes "
        "species evidence.\n"
        "- Do not invent missing names, pronouns, species, "
        "relationships, appearance, or voice traits.\n"
        "- Named non-speakers remain non-speakers.\n"
        "- Similar names remain separate unless this passage explicitly "
        "establishes identity.\n\n"
        f"Passage {passage['index']} of {total_passages}\n"
        f"Absolute source range: {passage['start_char']}-"
        f"{passage['end_char']}\n\n"
        "SOURCE PASSAGE START\n"
        + passage["text"]
        + "\nSOURCE PASSAGE END"
    )


def _normalized_identity_seed(value: str) -> str:
    return " ".join(value.casefold().split())


def _reconciliation_groups(
    observations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}

    for observation in observations:
        key = _normalized_identity_seed(
            observation["identity_seed"]
        )
        group = grouped.setdefault(
            key,
            {
                "identity_seed": observation["identity_seed"],
                "observation_ids": [],
                "proposed_names": [],
                "entity_kinds": [],
                "speaking_statuses": [],
                "titles": [],
                "aliases": [],
                "nicknames": [],
                "pronouns": [],
                "species": [],
                "relationships": [],
                "voice_clues": [],
                "unresolved_questions": [],
                "evidence": [],
            },
        )
        group["observation_ids"].append(
            observation["observation_id"]
        )
        group["proposed_names"].extend(
            [
                observation["canonical_name"],
                observation["display_name"],
            ]
        )
        group["entity_kinds"].append(
            observation["entity_kind"]
        )
        group["speaking_statuses"].append(
            observation["speaking_status"]
        )

        for field in (
            "titles",
            "aliases",
            "nicknames",
            "pronouns",
            "species",
            "relationships",
            "voice_clues",
            "unresolved_questions",
        ):
            group[field].extend(observation[field])

        for evidence in observation["evidence"]:
            group["evidence"].append(
                {
                    "observation_id": observation[
                        "observation_id"
                    ],
                    "category": evidence["category"],
                    "source_quote": evidence["source_quote"],
                    "source_location": evidence[
                        "source_location"
                    ],
                    "basis": evidence["basis"],
                    "confidence": evidence["confidence"],
                }
            )

    result = []

    for group in grouped.values():
        normalized = {}
        for key, value in group.items():
            if key == "evidence":
                normalized[key] = value[:30]
                continue

            if isinstance(value, list):
                seen = set()
                unique = []
                for item in value:
                    if not isinstance(item, str):
                        continue
                    marker = item.casefold()
                    if item and marker not in seen:
                        seen.add(marker)
                        unique.append(item)
                normalized[key] = unique
            else:
                normalized[key] = value

        result.append(normalized)

    return result


def build_roster_reconciliation_prompt(
    observations: list[dict[str, Any]],
) -> str:
    return (
        "Reconcile validated whole-book character observations into "
        "proposed canonical identities. Return only the "
        "roster_reconciliation JSON contract.\n\n"
        "Rules:\n"
        "- Reference only observation_ids supplied below.\n"
        "- Do not rewrite, add, or omit evidence.\n"
        "- Preserve unnamed and unresolved identities.\n"
        "- Keep similar identities separate unless evidence supports a "
        "merge; use duplicate_candidates for uncertain matches.\n"
        "- Named non-speakers remain non-speakers.\n"
        "- Every observation_id must appear exactly once in one entry "
        "or in excluded_observation_ids.\n"
        "- Every duplicate candidate must cite observation_ids owned by "
        "the two candidate entries.\n"
        "- possible_duplicate_seeds must refer to identity_seed values "
        "from other proposed entries.\n\n"
        "VALIDATED OBSERVATION GROUPS:\n"
        + json.dumps(
            _reconciliation_groups(observations),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def run_roster_discovery(
    *,
    runtime_client: Any,
    source: dict[str, Any],
    source_text: str,
    state_path: str | Path,
    draft_path: str | Path,
    passage_size: int = DEFAULT_PASSAGE_SIZE,
    overlap_chars: int = DEFAULT_PASSAGE_OVERLAP,
    temperature: float = 0.1,
    max_tokens: int = 6000,
    seed: int | None = 42,
) -> dict[str, Any]:
    passages = build_discovery_passages(
        source_text,
        passage_size=passage_size,
        overlap=overlap_chars,
    )

    if not passages:
        raise RosterDiscoveryError(
            "The selected source contains no text to inspect."
        )

    generation_identity = build_discovery_identity(
        model_name=runtime_client.model_name,
        backend=runtime_client.backend,
        passage_size=passage_size,
        overlap=overlap_chars,
        temperature=temperature,
        max_tokens=max_tokens,
        seed=seed,
    )
    state = prepare_roster_discovery_state(
        path=state_path,
        source=source,
        generation_identity=generation_identity,
        passages=passages,
    )
    completed_count = len(state["completed_passages"])

    if completed_count:
        print(
            "Resuming roster discovery after "
            f"{completed_count}/{len(passages)} completed passages."
        )

    for passage in passages[completed_count:]:
        print(
            f"Roster discovery passage {passage['index']}/"
            f"{len(passages)}"
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "You produce exact, evidence-backed JSON for "
                    "audiobook character discovery."
                ),
            },
            {
                "role": "user",
                "content": build_roster_discovery_prompt(
                    passage,
                    total_passages=len(passages),
                ),
            },
        ]
        observations = []
        warnings = []

        for evidence_attempt in range(2):
            result = runtime_client.complete_json(
                messages=messages,
                contract="roster_discovery",
                temperature=temperature,
                max_tokens=max_tokens,
                seed=seed,
            )

            try:
                observations, warnings = normalize_passage_result(
                    result.data,
                    passage=passage,
                    source_fingerprint=source["fingerprint"],
                )
                break
            except RosterDiscoveryEvidenceError as exc:
                if evidence_attempt >= 1:
                    raise

                previous_content = getattr(
                    result,
                    "content",
                    json.dumps(
                        result.data,
                        ensure_ascii=False,
                    ),
                )
                messages.extend(
                    [
                        {
                            "role": "assistant",
                            "content": previous_content,
                        },
                        {
                            "role": "user",
                            "content": (
                                "Your JSON passed the structural schema "
                                "but failed exact source-evidence "
                                "validation.\n\n"
                                f"Validation error: {exc}\n\n"
                                "Return the complete corrected "
                                "roster_discovery object. Use only exact "
                                "quotes from the supplied source passage. "
                                "For every populated claim, include a "
                                "separate matching evidence category. "
                                "Offsets must identify the exact quote; "
                                "use a longer unique quote if the same "
                                "text occurs more than once. Do not add "
                                "unsupported facts."
                            ),
                        },
                    ]
                )

        state = checkpoint_roster_passage(
            state=state,
            path=state_path,
            passage=passage,
            observations=observations,
            warnings=warnings,
        )

    if state["reconciliation"] is None:
        observations = completed_observations(state)
        print(
            "Reconciling "
            f"{len(observations)} validated observations."
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "You reconcile validated character "
                    "observations without altering evidence."
                ),
            },
            {
                "role": "user",
                "content": build_roster_reconciliation_prompt(
                    observations
                ),
            },
        ]

        for reconciliation_attempt in range(2):
            result = runtime_client.complete_json(
                messages=messages,
                contract="roster_reconciliation",
                temperature=temperature,
                max_tokens=max_tokens,
                seed=seed,
            )

            try:
                state = checkpoint_roster_reconciliation(
                    state=state,
                    path=state_path,
                    reconciliation=result.data,
                )
                break
            except RosterReconciliationError as exc:
                if reconciliation_attempt >= 1:
                    raise

                previous_content = getattr(
                    result,
                    "content",
                    json.dumps(
                        result.data,
                        ensure_ascii=False,
                    ),
                )
                messages.extend(
                    [
                        {
                            "role": "assistant",
                            "content": previous_content,
                        },
                        {
                            "role": "user",
                            "content": (
                                "Your JSON passed the structural schema "
                                "but failed reconciliation integrity.\n\n"
                                f"Validation error: {exc}\n\n"
                                "Return the complete corrected "
                                "roster_reconciliation object. Assign "
                                "every supplied observation_id exactly "
                                "once to one entry or to "
                                "excluded_observation_ids. Do not invent "
                                "IDs, and duplicate candidates may cite "
                                "only observations owned by their two "
                                "candidate entries."
                            ),
                        },
                    ]
                )

    draft = build_draft_from_discovery_state(
        state,
        source_text=source_text,
    )
    saved = save_character_roster(
        draft,
        draft_path,
        source_text=source_text,
        expected_status="draft",
    )

    if saved["draft_fingerprint"] != draft[
        "draft_fingerprint"
    ]:
        raise RosterDiscoveryError(
            "Saved roster draft did not verify."
        )

    clear_roster_discovery_state(state_path)
    return saved
