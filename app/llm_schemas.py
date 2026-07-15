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


SCHEMAS: dict[str, dict[str, Any]] = {
    "persona": PERSONA_SCHEMA,
    "script": SCRIPT_SCHEMA,
    "review": SCRIPT_SCHEMA,
    "alias": ALIAS_SCHEMA,
    "advanced_discovery": ADVANCED_DISCOVERY_SCHEMA,
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
