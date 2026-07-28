from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from character_roster import (
    CharacterRosterError,
    CharacterRosterValidationError,
    build_draft_roster,
    read_character_roster,
    stable_entry_id,
)
from character_roster_actions import build_approved_roster
from generation_state import fingerprint_text, fingerprint_value
from llm_schemas import ContractValidationError, validate_contract
from roster_context import selected_project_source_path


SCRIPT_FILENAME = "annotated_script.json"
CATALOG_TIMESTAMP = "1970-01-01T00:00:00Z"


class VoiceIdentityContextError(RuntimeError):
    pass


class VoiceIdentityContextUnavailableError(VoiceIdentityContextError):
    pass


class VoiceIdentityContextInvalidError(VoiceIdentityContextError):
    pass


class VoiceIdentityContextSourceMismatchError(VoiceIdentityContextError):
    pass


def _load_script(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise VoiceIdentityContextUnavailableError(
            "Generate or import an annotated script before preparing expressive voices."
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VoiceIdentityContextInvalidError(
            f"The annotated script could not be read: {exc}"
        ) from exc
    try:
        script = validate_contract("script", value)
    except ContractValidationError as exc:
        raise VoiceIdentityContextInvalidError(
            f"The annotated script is invalid: {exc}"
        ) from exc
    if not script:
        raise VoiceIdentityContextUnavailableError(
            "The annotated script has no speakers to prepare."
        )
    return script


def _ordered_unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def _script_speaker_entries(
    script: list[dict[str, str]],
    *,
    source_fingerprint: str,
    source_text: str | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for index, item in enumerate(script):
        speaker = item["speaker"].strip()
        key = speaker.casefold()
        if key not in grouped:
            grouped[key] = {
                "speaker": speaker,
                "first_index": index,
                "texts": [],
                "instructions": [],
            }
            order.append(key)
        grouped[key]["texts"].append(item["text"])
        grouped[key]["instructions"].append(item["instruct"])

    entries: list[dict[str, Any]] = []
    for key in order:
        group = grouped[key]
        speaker = group["speaker"]
        first_index = int(group["first_index"])
        texts = _ordered_unique(list(group["texts"]))
        instructions = _ordered_unique(list(group["instructions"]))
        sample = texts[0]
        source_start = -1
        if source_text is not None:
            for candidate in texts:
                candidate_start = source_text.find(candidate)
                if candidate_start >= 0:
                    sample = candidate
                    source_start = candidate_start
                    break
            if source_start < 0:
                raise VoiceIdentityContextInvalidError(
                    f"No exact source-backed Script line was found for speaker {speaker!r}."
                )
        narrator = speaker.casefold() == "narrator"
        location = f"{SCRIPT_FILENAME} entry {first_index + 1}"
        evidence_start = source_start if source_start >= 0 else 0
        evidence_end = evidence_start + len(sample)
        entries.append(
            {
                "id": stable_entry_id(
                    f"script-speaker:{source_fingerprint}:{speaker.casefold()}"
                ),
                "canonical_name": "NARRATOR" if narrator else speaker,
                "display_name": "Narrator" if narrator else speaker,
                "entity_kind": "narrator_role" if narrator else "unknown",
                "speaking_status": "narrator" if narrator else "speaker",
                "titles": [],
                "aliases": [],
                "nicknames": [],
                "pronouns": [],
                "species": [],
                "relationships": [],
                "first_evidence_location": location,
                "additional_evidence_locations": [],
                "confidence": 1.0,
                "resolution_status": "resolved",
                "possible_duplicate_ids": [],
                "mistaken_merge_risk": False,
                "unresolved_questions": [],
                "evidence": [
                    {
                        "source_quote": sample,
                        "source_location": location,
                        "start_char": evidence_start,
                        "end_char": max(evidence_start + 1, evidence_end),
                        "passage_index": None,
                        "entry_index": first_index,
                        "batch_index": 0,
                        "category": "speaking",
                        "confidence": 1.0,
                        "basis": "explicit",
                    }
                ],
                "voice_clues": instructions[:20],
                "sample_lines": texts[:50],
            }
        )
    return entries


def build_script_speaker_roster(
    *,
    root_dir: str | Path,
    source_text: str | None = None,
    current_source_fingerprint: str | None = None,
    script_path: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(root_dir)
    target = Path(script_path) if script_path is not None else root / SCRIPT_FILENAME
    script = _load_script(target)
    script_fingerprint = fingerprint_value(script)
    source_fingerprint = (
        current_source_fingerprint
        or (fingerprint_text(source_text) if source_text is not None else None)
        or script_fingerprint
    )
    selected_source = selected_project_source_path(root_dir=root)
    source_path = selected_source if selected_source is not None else target
    source = {
        "path": str(source_path),
        "basename": source_path.name,
        "fingerprint": source_fingerprint,
        "character_count": (
            len(source_text)
            if source_text is not None
            else sum(len(item["text"]) for item in script)
        ),
    }
    entries = _script_speaker_entries(
        script,
        source_fingerprint=source_fingerprint,
        source_text=source_text,
    )
    discovery_identity = {
        "source_fingerprint": source_fingerprint,
        "script_fingerprint": script_fingerprint,
        "speaker_ids": [entry["id"] for entry in entries],
    }
    draft = build_draft_roster(
        source=source,
        discovery={
            "created_at_utc": CATALOG_TIMESTAMP,
            "model_name": "script-speaker-catalog",
            "backend": "local",
            "generation_fingerprint": fingerprint_value(discovery_identity),
            "batch_count": 1,
            "completed_batches": 1,
        },
        entries=entries,
        source_text=None,
    )
    return build_approved_roster(
        draft,
        expected_fingerprint=draft["draft_fingerprint"],
        source_fingerprint=source_fingerprint,
        source_text=None,
        acknowledged_unresolved=False,
        approved_at_utc=CATALOG_TIMESTAMP,
    )


def load_voice_identity_context(
    *,
    approved_roster_path: str | Path,
    source_text: str | None = None,
    current_source_fingerprint: str | None = None,
    script_path: str | Path | None = None,
    required: bool = True,
    allow_script_fallback: bool = True,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    roster_path = Path(approved_roster_path)
    root = roster_path.parent
    roster_warning: str | None = None
    roster_issue: VoiceIdentityContextError | None = None

    if roster_path.exists():
        try:
            roster = read_character_roster(
                roster_path,
                source_text=source_text,
                expected_status="approved",
            )
            if (
                current_source_fingerprint is not None
                and roster["source"]["fingerprint"]
                != current_source_fingerprint
            ):
                roster_warning = (
                    "The approved roster belongs to a different source, so "
                    "Expressive voices is using current script speakers instead."
                )
                roster_issue = VoiceIdentityContextSourceMismatchError(
                    "The approved character roster belongs to a different "
                    "selected source."
                )
            else:
                return roster, {
                    "identity_source": "approved_roster",
                    "roster_enriched": True,
                    "context_warning": None,
                    "source_compatible": (
                        None if current_source_fingerprint is None else True
                    ),
                }
        except (CharacterRosterValidationError, CharacterRosterError) as exc:
            roster_warning = (
                "The approved roster is unavailable, so Expressive voices is "
                f"using current script speakers instead: {exc}"
            )
            roster_issue = VoiceIdentityContextInvalidError(str(exc))

    if not allow_script_fallback:
        if required:
            if roster_issue is not None:
                raise roster_issue
            raise VoiceIdentityContextUnavailableError(
                "Approve the Character roster before creating Voice profiles."
            )
        return None, {
            "identity_source": "none",
            "roster_enriched": False,
            "context_warning": (
                roster_warning
                or "Approve the Character roster before creating Voice profiles."
            ),
            "source_compatible": (
                False
                if isinstance(
                    roster_issue,
                    VoiceIdentityContextSourceMismatchError,
                )
                else None
            ),
        }

    try:
        roster = build_script_speaker_roster(
            root_dir=root,
            source_text=source_text,
            current_source_fingerprint=current_source_fingerprint,
            script_path=script_path,
        )
    except VoiceIdentityContextError:
        if required:
            if roster_issue is not None:
                raise roster_issue
            raise
        return None, {
            "identity_source": "none",
            "roster_enriched": False,
            "context_warning": roster_warning,
            "source_compatible": (
                False
                if isinstance(
                    roster_issue,
                    VoiceIdentityContextSourceMismatchError,
                )
                else None
            ),
        }

    return roster, {
        "identity_source": "script",
        "roster_enriched": False,
        "context_warning": roster_warning,
        "source_compatible": (
            None if current_source_fingerprint is None else True
        ),
    }
