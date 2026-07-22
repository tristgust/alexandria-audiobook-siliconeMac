from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from character_roster import (
    CharacterRosterError,
    CharacterRosterValidationError,
    read_character_roster,
    validate_character_roster,
)
from generation_state import fingerprint_text


DEFAULT_ROSTER_FILENAME = "character_roster.json"
ROSTER_CONTEXT_VERSION = 1


class RosterContextError(RuntimeError):
    pass


class RosterContextInvalidError(RosterContextError):
    pass


class RosterContextSourceMismatchError(RosterContextError):
    pass


class RosterContextSourceUnavailableError(RosterContextError):
    pass


def selected_project_source_path(
    *,
    root_dir: str | Path,
    explicit_source_path: str | Path | None = None,
) -> Path | None:
    if explicit_source_path is not None:
        text = str(explicit_source_path).strip()
        return Path(text) if text else None
    state_path = Path(root_dir) / "state.json"
    if not state_path.exists():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(state, dict):
        return None
    value = state.get("input_file_path")
    if not isinstance(value, str) or not value.strip():
        return None
    return Path(value.strip())


def load_project_roster_context(
    *,
    root_dir: str | Path,
    source_path: str | Path | None = None,
    normalizer=None,
) -> tuple[dict[str, Any] | None, str | None, Path | None]:
    root = Path(root_dir)
    roster_path = root / DEFAULT_ROSTER_FILENAME
    if not roster_path.exists():
        return None, None, None
    selected_source = selected_project_source_path(
        root_dir=root,
        explicit_source_path=source_path,
    )
    if selected_source is None:
        raise RosterContextSourceUnavailableError(
            "An approved character roster exists, but no source file is selected."
        )
    if not selected_source.exists():
        raise RosterContextSourceUnavailableError(
            f"The selected source file does not exist: {selected_source}"
        )
    try:
        source_text = selected_source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RosterContextSourceUnavailableError(
            f"The selected source file could not be read: {exc}"
        ) from exc
    if normalizer is not None:
        source_text = normalizer(source_text)
    roster = load_approved_roster_for_source(
        root_dir=root,
        source_text=source_text,
        roster_path=roster_path,
    )
    return roster, source_text, selected_source


def load_approved_roster_for_source(
    *,
    root_dir: str | Path,
    source_text: str,
    roster_path: str | Path | None = None,
) -> dict[str, Any] | None:
    target = (
        Path(roster_path)
        if roster_path is not None
        else Path(root_dir) / DEFAULT_ROSTER_FILENAME
    )
    if not target.exists():
        return None
    try:
        roster = read_character_roster(
            target,
            source_text=source_text,
            expected_status="approved",
        )
    except CharacterRosterValidationError as exc:
        raise RosterContextInvalidError(str(exc)) from exc
    except CharacterRosterError as exc:
        raise RosterContextInvalidError(str(exc)) from exc

    current_fingerprint = fingerprint_text(source_text)
    approved_fingerprint = roster["source"]["fingerprint"]
    if approved_fingerprint != current_fingerprint:
        raise RosterContextSourceMismatchError(
            "The approved character roster belongs to a different source."
        )
    return roster


def _entry_names(entry: dict[str, Any]) -> list[str]:
    values = [
        entry.get("canonical_name", ""),
        entry.get("display_name", ""),
        *entry.get("titles", []),
        *entry.get("aliases", []),
        *entry.get("nicknames", []),
    ]
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def build_speaker_alias_index(
    approved_roster: dict[str, Any],
) -> dict[str, str | None]:
    roster = validate_character_roster(
        approved_roster,
        expected_status="approved",
    )
    candidates: dict[str, set[str]] = {}
    canonical_by_id: dict[str, str] = {}

    for entry in roster["entries"]:
        if entry["speaking_status"] != "speaker":
            continue
        if entry["resolution_status"] != "resolved":
            continue
        canonical = entry["canonical_name"].strip()
        if not canonical:
            continue
        canonical_by_id[entry["id"]] = canonical
        for name in _entry_names(entry):
            candidates.setdefault(name.casefold(), set()).add(entry["id"])

    index: dict[str, str | None] = {}
    for name_key, entry_ids in candidates.items():
        if len(entry_ids) != 1:
            index[name_key] = None
            continue
        entry_id = next(iter(entry_ids))
        index[name_key] = canonical_by_id[entry_id]
    return index


def canonical_speaker_name(
    speaker: Any,
    approved_roster: dict[str, Any] | None,
) -> Any:
    if approved_roster is None or not isinstance(speaker, str):
        return speaker
    normalized = speaker.strip()
    if not normalized or normalized.casefold() == "narrator":
        return "NARRATOR" if normalized else speaker
    canonical = build_speaker_alias_index(approved_roster).get(
        normalized.casefold()
    )
    return canonical if canonical is not None else speaker


def canonicalize_script_entries(
    entries: list[dict[str, Any]],
    approved_roster: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if approved_roster is None:
        return copy.deepcopy(entries)
    alias_index = build_speaker_alias_index(approved_roster)
    normalized_entries: list[dict[str, Any]] = []
    for entry in entries:
        updated = copy.deepcopy(entry)
        speaker = updated.get("speaker")
        if isinstance(speaker, str):
            stripped = speaker.strip()
            if stripped.casefold() == "narrator":
                updated["speaker"] = "NARRATOR"
            elif stripped:
                canonical = alias_index.get(stripped.casefold())
                if canonical is not None:
                    updated["speaker"] = canonical
        normalized_entries.append(updated)
    return normalized_entries


def _format_resolved_entry(entry: dict[str, Any]) -> str:
    canonical = entry["canonical_name"] or entry["display_name"]
    aliases = [
        value
        for value in _entry_names(entry)
        if value.casefold() != canonical.casefold()
    ]
    details = []
    if aliases:
        details.append("aliases: " + ", ".join(aliases))
    if entry.get("species"):
        details.append("species: " + ", ".join(entry["species"]))
    suffix = " | " + "; ".join(details) if details else ""
    return f"- {canonical}{suffix}"


def _format_unresolved_entry(entry: dict[str, Any]) -> str:
    label = (
        entry.get("display_name")
        or entry.get("canonical_name")
        or entry["id"]
    )
    questions = entry.get("unresolved_questions") or [
        "identity remains unresolved"
    ]
    return (
        f"- [{entry['resolution_status'].upper()} {entry['id']}] "
        f"{label} | {'; '.join(questions)}"
    )


def build_roster_prompt_context(
    approved_roster: dict[str, Any] | None,
    *,
    stage: str,
    max_chars: int = 12000,
) -> str:
    if approved_roster is None:
        return ""
    roster = validate_character_roster(
        approved_roster,
        expected_status="approved",
    )
    stage_label = str(stage).strip() or "LLM"
    header = [
        "APPROVED CHARACTER ROSTER (canonical identity authority)",
        f"Stage: {stage_label}",
        f"Roster fingerprint: {roster['roster_fingerprint']}",
        "Rules:",
        "- Use canonical names exactly for resolved dialogue speakers.",
        "- Treat listed aliases, titles, and nicknames as the same resolved identity.",
        "- Keep NARRATOR for all non-dialogue and attribution narration.",
        "- Do not merge, rename, guess, or resolve entries marked unresolved or unnamed.",
        "- Do not turn named non-speakers into dialogue speakers.",
        "- This roster may change speaker labels only; it never authorizes wording, punctuation, order, or quantity changes.",
        "Resolved speaking identities:",
    ]
    resolved = [
        entry
        for entry in roster["entries"]
        if entry["speaking_status"] == "speaker"
        and entry["resolution_status"] == "resolved"
    ]
    unresolved = [
        entry
        for entry in roster["entries"]
        if entry["resolution_status"] in {"unresolved", "unnamed"}
    ]
    lines = [*header]
    lines.extend(_format_resolved_entry(entry) for entry in resolved)
    if unresolved:
        lines.append("Unresolved identities — preserve separately:")
        lines.extend(_format_unresolved_entry(entry) for entry in unresolved)

    result: list[str] = []
    omitted = 0
    current_length = 0
    for line in lines:
        added = len(line) + (1 if result else 0)
        if current_length + added > max_chars:
            omitted += 1
            continue
        result.append(line)
        current_length += added
    if omitted:
        marker = f"[Roster context truncated: {omitted} lines omitted.]"
        while result and (
            sum(len(item) for item in result)
            + len(result)
            + len(marker)
            > max_chars
        ):
            result.pop()
            omitted += 1
            marker = (
                f"[Roster context truncated: {omitted} lines omitted.]"
            )
        if len(marker) <= max_chars:
            result.append(marker)
    return "\n".join(result)


def roster_generation_identity(
    approved_roster: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if approved_roster is None:
        return None
    roster = validate_character_roster(
        approved_roster,
        expected_status="approved",
    )
    return {
        "context_version": ROSTER_CONTEXT_VERSION,
        "source_fingerprint": roster["source"]["fingerprint"],
        "roster_fingerprint": roster["roster_fingerprint"],
    }
