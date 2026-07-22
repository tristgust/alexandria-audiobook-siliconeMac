from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any, Iterable


def normalized_voice_label(value: Any) -> str:
    return "".join(
        character.casefold()
        for character in str(value or "")
        if character.isalnum()
    )


def build_script_voice_index(
    entries: Iterable[Any],
) -> tuple[list[str], dict[str, set[str]], Counter[str]]:
    speakers: list[str] = []
    seen_speakers: set[str] = set()
    line_speakers: dict[str, set[str]] = {}
    speaker_counts: Counter[str] = Counter()
    for item in entries:
        if not isinstance(item, dict):
            continue
        speaker = str(item.get("speaker") or item.get("type") or "").strip()
        text = str(item.get("text") or "")
        if not speaker:
            continue
        speaker_counts[speaker] += 1
        key = speaker.casefold()
        if key not in seen_speakers:
            seen_speakers.add(key)
            speakers.append(speaker)
        if text:
            line_speakers.setdefault(text, set()).add(speaker)
    return speakers, line_speakers, speaker_counts


def load_script_voice_index(
    script_path: str | Path,
) -> tuple[list[str], dict[str, set[str]], Counter[str]]:
    target = Path(script_path)
    if not target.exists():
        return [], {}, Counter()
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return [], {}, Counter()
    if not isinstance(value, list):
        return [], {}, Counter()
    return build_script_voice_index(value)


def matching_script_speakers(
    values: Iterable[Any],
    speakers: list[str],
) -> list[str]:
    normalized_values = {
        normalized_voice_label(value)
        for value in values
        if normalized_voice_label(value)
    }
    return [
        speaker
        for speaker in speakers
        if normalized_voice_label(speaker) in normalized_values
    ]


def resolve_script_voice_name(
    entry: dict[str, Any],
    *,
    speakers: list[str],
    line_speakers: dict[str, set[str]],
    speaker_counts: Counter[str],
) -> dict[str, Any]:
    if entry.get("speaking_status") not in {"speaker", "narrator"}:
        return {
            "script_voice_name": None,
            "script_voice_mapping": "not_required",
            "script_voice_candidates": [],
            "script_line_count": 0,
        }

    direct = matching_script_speakers(
        [entry.get("canonical_name"), entry.get("display_name")],
        speakers,
    )
    if len(direct) == 1:
        selected = direct[0]
        return {
            "script_voice_name": selected,
            "script_voice_mapping": "identity_name",
            "script_voice_candidates": direct,
            "script_line_count": int(speaker_counts[selected]),
        }
    if len(direct) > 1:
        return {
            "script_voice_name": None,
            "script_voice_mapping": "ambiguous",
            "script_voice_candidates": direct,
            "script_line_count": 0,
        }

    alternate = matching_script_speakers(
        [
            *list(entry.get("aliases") or []),
            *list(entry.get("nicknames") or []),
            *list(entry.get("titles") or []),
        ],
        speakers,
    )
    if len(alternate) == 1:
        selected = alternate[0]
        return {
            "script_voice_name": selected,
            "script_voice_mapping": "alternate_name",
            "script_voice_candidates": alternate,
            "script_line_count": int(speaker_counts[selected]),
        }
    if len(alternate) > 1:
        return {
            "script_voice_name": None,
            "script_voice_mapping": "ambiguous",
            "script_voice_candidates": alternate,
            "script_line_count": 0,
        }

    sample_scores: Counter[str] = Counter()
    for sample in entry.get("sample_lines") or []:
        for speaker in line_speakers.get(str(sample), set()):
            sample_scores[speaker] += 1
    if sample_scores:
        highest = max(sample_scores.values())
        sample_matches = sorted(
            speaker
            for speaker, score in sample_scores.items()
            if score == highest
        )
        if len(sample_matches) == 1:
            selected = sample_matches[0]
            return {
                "script_voice_name": selected,
                "script_voice_mapping": "sample_lines",
                "script_voice_candidates": sample_matches,
                "script_line_count": int(speaker_counts[selected]),
            }
        return {
            "script_voice_name": None,
            "script_voice_mapping": "ambiguous",
            "script_voice_candidates": sample_matches,
            "script_line_count": 0,
        }

    return {
        "script_voice_name": None,
        "script_voice_mapping": "missing",
        "script_voice_candidates": [],
        "script_line_count": 0,
    }
