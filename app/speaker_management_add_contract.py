from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypeAlias, TypedDict

from character_roster import ENTITY_KINDS, SPEAKING_STATUSES


JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = (
    JsonScalar | Mapping[str, "JsonValue"] | list["JsonValue"]
)
JsonObject: TypeAlias = Mapping[str, JsonValue]


class SpeakerAddError(RuntimeError):
    pass


class SpeakerAddConflictError(SpeakerAddError):
    pass


class SpeakerAddValidationError(SpeakerAddError):
    pass


class SpeakerEvidence(TypedDict):
    source_quote: str
    source_location: str
    start_char: int
    end_char: int
    passage_index: None
    entry_index: int
    batch_index: int
    category: str
    confidence: float
    basis: str


@dataclass(frozen=True, slots=True)
class SpeakerAddCommand:
    script_speaker: str
    expected_roster_fingerprint: str
    display_name: str
    require_exclusion_audit: bool
    entity_kind: str
    speaking_status: str
    confidence: float
    titles: tuple[str, ...]
    aliases: tuple[str, ...]
    nicknames: tuple[str, ...]
    pronouns: tuple[str, ...]
    species: tuple[str, ...]
    relationships: tuple[str, ...]
    voice_clues: tuple[str, ...]
    designed_voice_description: str


@dataclass(frozen=True, slots=True)
class SpeakerAddContext:
    working_script: Sequence[JsonObject]
    roster: JsonObject
    roster_entries: Sequence[JsonObject]
    script_mapping_by_id: Mapping[str, JsonObject]
    voice_config: JsonObject


@dataclass(frozen=True, slots=True)
class SpeakerAddReview:
    sample_lines: tuple[str, ...]
    evidence: tuple[SpeakerEvidence, ...]


@dataclass(frozen=True, slots=True)
class SpeakerAddResult:
    roster_entries: list[dict[str, JsonValue]]
    voice_config: dict[str, JsonValue]
    affected_speakers: tuple[str, ...]


def _required_text(value: JsonValue, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SpeakerAddValidationError(
            f"{label} must be non-empty text."
        )
    return value.strip()


def _text_list(value: JsonValue, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise SpeakerAddValidationError(
            f"{label} must be a JSON array."
        )
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise SpeakerAddValidationError(
                f"{label} must contain non-empty text."
            )
        text = item.strip()
        if text not in result:
            result.append(text)
    return tuple(result)


def _confidence(value: JsonValue) -> float:
    if value is None:
        return 1.0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpeakerAddValidationError(
            "confidence must be a number from 0 through 1."
        )
    result = float(value)
    if result < 0.0 or result > 1.0:
        raise SpeakerAddValidationError(
            "confidence must be a number from 0 through 1."
        )
    return result


def _optional_text(
    value: JsonValue,
    label: str,
    fallback: str = "",
) -> str:
    if value is None:
        return fallback
    if not isinstance(value, str):
        raise SpeakerAddValidationError(f"{label} must be text.")
    return value.strip()


def parse_speaker_add(payload: JsonObject) -> SpeakerAddCommand:
    script_speaker = _required_text(
        payload.get("script_speaker") or payload.get("canonical_name"),
        "script_speaker",
    )
    display_name = _optional_text(
        payload.get("display_name"), "display_name", script_speaker
    )
    if not display_name:
        raise SpeakerAddValidationError(
            "display_name must be non-empty text."
        )
    require_audit = payload.get("require_exclusion_audit", False)
    if not isinstance(require_audit, bool):
        raise SpeakerAddValidationError(
            "require_exclusion_audit must be a boolean."
        )
    entity_kind = _optional_text(
        payload.get("entity_kind"), "entity_kind", "character"
    )
    speaking_status = _optional_text(
        payload.get("speaking_status"), "speaking_status", "speaker"
    )
    if entity_kind not in ENTITY_KINDS:
        raise SpeakerAddValidationError(
            f"entity_kind is unsupported: {entity_kind!r}."
        )
    if speaking_status not in SPEAKING_STATUSES:
        raise SpeakerAddValidationError(
            f"speaking_status is unsupported: {speaking_status!r}."
        )
    return SpeakerAddCommand(
        script_speaker=script_speaker,
        expected_roster_fingerprint=_required_text(
            payload.get("expected_roster_fingerprint"),
            "expected_roster_fingerprint",
        ),
        display_name=display_name,
        require_exclusion_audit=require_audit,
        entity_kind=entity_kind,
        speaking_status=speaking_status,
        confidence=_confidence(payload.get("confidence")),
        titles=_text_list(payload.get("titles"), "titles"),
        aliases=_text_list(payload.get("aliases"), "aliases"),
        nicknames=_text_list(payload.get("nicknames"), "nicknames"),
        pronouns=_text_list(payload.get("pronouns"), "pronouns"),
        species=_text_list(payload.get("species"), "species"),
        relationships=_text_list(
            payload.get("relationships"), "relationships"
        ),
        voice_clues=_text_list(payload.get("voice_clues"), "voice_clues"),
        designed_voice_description=_optional_text(
            payload.get("designed_voice_description"),
            "designed_voice_description",
        ),
    )
