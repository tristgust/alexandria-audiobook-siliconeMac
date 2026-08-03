from __future__ import annotations

from collections import defaultdict
from typing import Any

from generate_script import EBOOK_WATERMARK_LINE_PATTERN, fix_mojibake
from script_audit import NARRATOR_LABELS, audit_script_chunk, split_source_segments


class LegacyScriptRepairError(ValueError):
    pass


def _normalized_source_with_raw_map(raw_source: str) -> tuple[str, list[int]]:
    parts: list[str] = []
    normalized_to_raw: list[int] = []
    cursor = 0
    for match in EBOOK_WATERMARK_LINE_PATTERN.finditer(raw_source):
        parts.append(raw_source[cursor:match.start()])
        normalized_to_raw.extend(range(cursor, match.start()))
        cursor = match.end()
    parts.append(raw_source[cursor:])
    normalized_to_raw.extend(range(cursor, len(raw_source)))
    normalized = "".join(parts)
    fully_normalized = fix_mojibake(raw_source)
    if normalized != fully_normalized:
        raise LegacyScriptRepairError(
            "Legacy repair supports watermark removal only; the source also "
            "contains another normalization change. Regenerate the Script instead."
        )
    if len(normalized) != len(normalized_to_raw):
        raise LegacyScriptRepairError("Source normalization map is inconsistent.")
    return normalized, normalized_to_raw


def _entry_spans(source_text: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    cursor = 0
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise LegacyScriptRepairError(f"Script entry {index} is not an object.")
        text = entry.get("text")
        speaker = entry.get("speaker")
        instruct = entry.get("instruct")
        if not isinstance(text, str) or not text.strip():
            raise LegacyScriptRepairError(f"Script entry {index} has empty text.")
        if not isinstance(speaker, str) or not speaker.strip():
            raise LegacyScriptRepairError(f"Script entry {index} has no speaker.")
        if not isinstance(instruct, str):
            raise LegacyScriptRepairError(f"Script entry {index} has invalid direction.")
        start = source_text.find(text, cursor)
        if start < 0:
            raise LegacyScriptRepairError(
                f"Script entry {index} could not be mapped back to the selected source."
            )
        end = start + len(text)
        spans.append(
            {
                "index": index,
                "start": start,
                "end": end,
                "speaker": speaker.strip(),
                "instruct": instruct,
            }
        )
        cursor = end
    return spans


def _segment_normalized_spans(
    normalized_source: str,
    *,
    coordinate_offset: int = 0,
) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    cursor = 0
    for segment in split_source_segments(normalized_source):
        start = normalized_source.find(segment.text, cursor)
        if start < 0:
            raise LegacyScriptRepairError(
                "A corrected source segment could not be mapped to the normalized source."
            )
        end = start + len(segment.text)
        spans.append(
            {
                "kind": segment.kind,
                "text": segment.text,
                "start": start + coordinate_offset,
                "end": end + coordinate_offset,
            }
        )
        cursor = end
    return spans


def _segment_raw_spans(
    normalized_source: str,
    normalized_to_raw: list[int],
) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    cursor = 0
    for segment in split_source_segments(normalized_source):
        start = normalized_source.find(segment.text, cursor)
        if start < 0:
            raise LegacyScriptRepairError(
                "A corrected source segment could not be mapped to the raw source."
            )
        end = start + len(segment.text)
        if end <= start or end > len(normalized_to_raw):
            raise LegacyScriptRepairError("A corrected source segment has an invalid span.")
        spans.append(
            {
                "kind": segment.kind,
                "text": segment.text,
                "start": normalized_to_raw[start],
                "end": normalized_to_raw[end - 1] + 1,
            }
        )
        cursor = end
    return spans


def _overlap(left_start: int, left_end: int, right_start: int, right_end: int) -> int:
    return max(0, min(left_end, right_end) - max(left_start, right_start))


def _best_direction(
    overlaps: list[tuple[int, dict[str, Any]]],
    *,
    speaker: str,
    fallback: str,
) -> str:
    matching = [
        (amount, span)
        for amount, span in overlaps
        if span["speaker"].strip().upper() == speaker.strip().upper()
        and span["instruct"].strip()
    ]
    if matching:
        return max(matching, key=lambda item: (item[0], -item[1]["index"]))[1][
            "instruct"
        ].strip()
    usable = [item for item in overlaps if item[1]["instruct"].strip()]
    if usable:
        return max(usable, key=lambda item: (item[0], -item[1]["index"]))[1][
            "instruct"
        ].strip()
    return fallback


def normalized_source_for_legacy_repair(
    raw_source: str,
    *,
    start_marker: str | None = None,
) -> tuple[str, int]:
    normalized_source, _ = _normalized_source_with_raw_map(raw_source)
    marker = str(start_marker or "")
    if not marker:
        return normalized_source, 0
    first = normalized_source.find(marker)
    if first < 0:
        raise LegacyScriptRepairError(
            "The requested source-start marker was not found in the normalized source."
        )
    if normalized_source.find(marker, first + 1) >= 0:
        raise LegacyScriptRepairError(
            "The requested source-start marker is ambiguous in the normalized source."
        )
    return normalized_source[first:], first


def repair_legacy_curly_apostrophe_script(
    *,
    raw_source: str,
    entries: list[dict[str, Any]],
    start_marker: str | None = None,
) -> tuple[list[dict[str, str]], dict[str, int]]:
    if not isinstance(raw_source, str) or not raw_source:
        raise LegacyScriptRepairError("A nonempty raw source is required.")
    if not isinstance(entries, list) or not entries:
        raise LegacyScriptRepairError("A nonempty Script is required.")

    normalized_source, normalized_to_raw = _normalized_source_with_raw_map(raw_source)
    target_source, target_offset = normalized_source_for_legacy_repair(
        raw_source,
        start_marker=start_marker,
    )
    try:
        old_spans = _entry_spans(normalized_source, entries)
        corrected_spans = _segment_normalized_spans(
            target_source,
            coordinate_offset=target_offset,
        )
    except LegacyScriptRepairError:
        if start_marker:
            raise LegacyScriptRepairError(
                "Front-matter trimming requires a Script already mapped to the normalized source."
            )
        old_spans = _entry_spans(raw_source, entries)
        corrected_spans = _segment_raw_spans(normalized_source, normalized_to_raw)

    repaired: list[dict[str, str]] = []
    old_cursor = 0
    unresolved_count = 0
    for segment in corrected_spans:
        while old_cursor < len(old_spans) and old_spans[old_cursor]["end"] <= segment["start"]:
            old_cursor += 1
        overlaps: list[tuple[int, dict[str, Any]]] = []
        scan = old_cursor
        while scan < len(old_spans) and old_spans[scan]["start"] < segment["end"]:
            amount = _overlap(
                segment["start"],
                segment["end"],
                old_spans[scan]["start"],
                old_spans[scan]["end"],
            )
            if amount:
                overlaps.append((amount, old_spans[scan]))
            scan += 1

        if segment["kind"] == "narration":
            speaker = "NARRATOR"
            instruct = _best_direction(
                overlaps,
                speaker=speaker,
                fallback="Neutral, even narration.",
            )
        else:
            weights: dict[str, int] = defaultdict(int)
            first_seen: dict[str, int] = {}
            display: dict[str, str] = {}
            for amount, span in overlaps:
                label = span["speaker"].strip()
                normalized_label = label.upper()
                if normalized_label in NARRATOR_LABELS:
                    continue
                weights[normalized_label] += amount
                first_seen.setdefault(normalized_label, span["index"])
                display.setdefault(normalized_label, label)
            if weights:
                winner = max(
                    weights,
                    key=lambda label: (
                        weights[label],
                        label != "UNRESOLVED SPEAKER",
                        -first_seen[label],
                    ),
                )
                speaker = display[winner]
            else:
                speaker = "UNRESOLVED SPEAKER"
            if speaker.upper() == "UNRESOLVED SPEAKER":
                unresolved_count += 1
            instruct = _best_direction(
                overlaps,
                speaker=speaker,
                fallback="Natural, conversational delivery with restrained emotional detail.",
            )

        repaired.append(
            {
                "speaker": speaker,
                "text": segment["text"],
                "instruct": instruct,
            }
        )

    audit = audit_script_chunk(target_source, repaired)
    if not audit.passed:
        issue = audit.blocking_issues[0] if audit.blocking_issues else None
        raise LegacyScriptRepairError(
            "Repaired Script failed source fidelity"
            + (f": {issue.code}: {issue.message}" if issue is not None else ".")
        )
    if any("OceanofPDF.com" in entry["text"] for entry in repaired):
        raise LegacyScriptRepairError("Watermark text remained in the repaired Script.")

    return repaired, {
        "original_entry_count": len(entries),
        "repaired_entry_count": len(repaired),
        "removed_entry_count": len(entries) - len(repaired),
        "unresolved_dialogue_count": unresolved_count,
        "watermark_count": len(EBOOK_WATERMARK_LINE_PATTERN.findall(raw_source)),
        "trimmed_character_count": target_offset,
    }
