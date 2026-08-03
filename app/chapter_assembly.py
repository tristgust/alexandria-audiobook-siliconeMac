from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

from pydub import AudioSegment

from audio_artifacts import sha256_file, validate_audio_file
from audio_edge_safety import ensure_click_safe_fade_in
from generation_state import fingerprint_value


CHAPTER_MODES = frozenset({"smart", "per_chunk", "none"})
MIN_RENDITION_DURATION_MS = 250
MAX_EDGE_TRIM_MS = 30_000
MIN_SPLIT_EDGE_MS = 50
MIN_SPLIT_PAUSE_MS = 20
MAX_SPLIT_PAUSE_MS = 5_000


class ChapterAssemblyError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.context = copy.deepcopy(dict(context or {}))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _integer(value: Any, *, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ChapterAssemblyError(
            "chapter_assembly_setting_invalid",
            f"{field} must be an integer between {minimum} and {maximum}.",
            context={"field": field, "value": value},
        )
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ChapterAssemblyError(
            "chapter_assembly_setting_invalid",
            f"{field} must be an integer between {minimum} and {maximum}.",
            context={"field": field, "value": value},
        ) from exc
    if not minimum <= result <= maximum:
        raise ChapterAssemblyError(
            "chapter_assembly_setting_invalid",
            f"{field} must be between {minimum} and {maximum}.",
            context={"field": field, "value": result},
        )
    return result


def _duration_ms(chunk: Mapping[str, Any]) -> int | None:
    value = chunk.get("duration_ms")
    if not isinstance(value, int) or value <= 0:
        value = chunk.get("audio_duration_ms")
    return int(value) if isinstance(value, int) and value > 0 else None


def _chunk_id(chunk: Mapping[str, Any], index: int) -> str:
    value = chunk.get("chunk_id")
    if value is not None:
        text = str(value)
        return text if text.startswith("chunk:") else f"chunk:{text}"
    return f"chunk:{chunk.get('id', index)}"


def source_order_fingerprint(chunks: list[Mapping[str, Any]]) -> str:
    return fingerprint_value(
        [
            {
                "chunk_id": _chunk_id(chunk, index),
                "speaker": str(chunk.get("speaker") or "UNKNOWN"),
                "text": str(chunk.get("text") or ""),
            }
            for index, chunk in enumerate(chunks)
            if isinstance(chunk, Mapping)
        ]
    )


def chapter_rows(
    chunks: list[Mapping[str, Any]],
    *,
    config: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    tts = _mapping(_mapping(config).get("tts"))
    between = int(tts.get("pause_between_speakers_ms") or 500)
    same = int(tts.get("pause_same_speaker_ms") or 250)
    rows: list[dict[str, Any]] = []
    cursor = 0
    for index, raw in enumerate(chunks):
        if not isinstance(raw, Mapping):
            continue
        duration = _duration_ms(raw)
        if duration is None:
            continue
        speaker = str(raw.get("speaker") or "UNKNOWN")
        start = cursor
        end = start + duration
        explicit_pause = raw.get("pause_after_ms")
        if not isinstance(explicit_pause, int) or explicit_pause < 0:
            explicit_pause = raw.get("pause_after")
        if isinstance(explicit_pause, int) and explicit_pause >= 0:
            pause = explicit_pause
        elif index + 1 < len(chunks):
            next_raw = chunks[index + 1]
            next_speaker = (
                str(next_raw.get("speaker") or "")
                if isinstance(next_raw, Mapping)
                else ""
            )
            pause = same if next_speaker == speaker else between
        else:
            pause = 0
        rows.append(
            {
                "chunk_id": _chunk_id(raw, index),
                "source_index": index,
                "speaker": speaker,
                "text": str(raw.get("text") or ""),
                "start_ms": start,
                "end_ms": end,
                "duration_ms": duration,
                "pause_after_ms": pause,
                "audio": copy.deepcopy(dict(_mapping(raw.get("audio")))),
                "state": raw.get("state"),
            }
        )
        cursor = end + pause
    return rows


def build_chapters(
    chunks: list[Mapping[str, Any]],
    *,
    config: Mapping[str, Any] | None = None,
    mode: str,
) -> list[dict[str, Any]]:
    import re

    if mode not in CHAPTER_MODES:
        raise ChapterAssemblyError(
            "chapter_assembly_mode_invalid",
            "The requested chapter mode is invalid.",
            context={"chapter_mode": mode},
        )
    timeline = chapter_rows(chunks, config=config)
    if mode == "none" or not timeline:
        return []
    if mode == "per_chunk":
        return [
            {
                "chapter_id": f"chapter:{index}",
                "order": index,
                "name": f"[{item['speaker']}] {item['text'][:80]}",
                "start_ms": item["start_ms"],
                "end_ms": item["end_ms"],
                "start_chunk_id": item["chunk_id"],
                "end_chunk_id": item["chunk_id"],
            }
            for index, item in enumerate(timeline)
        ]
    heading = re.compile(
        r"^(chapter|part|book|volume|prologue|epilogue|introduction|"
        r"conclusion|act|section)\b",
        re.IGNORECASE,
    )
    headings = [
        index
        for index, item in enumerate(timeline)
        if heading.match(item["text"].strip())
    ]
    if not headings:
        return build_chapters(chunks, config=config, mode="per_chunk")
    chapters: list[dict[str, Any]] = []
    if headings[0] > 0:
        end_item = timeline[headings[0] - 1]
        chapters.append(
            {
                "chapter_id": "chapter:0",
                "order": 0,
                "name": "Introduction",
                "start_ms": timeline[0]["start_ms"],
                "end_ms": end_item["end_ms"],
                "start_chunk_id": timeline[0]["chunk_id"],
                "end_chunk_id": end_item["chunk_id"],
            }
        )
    for position, timeline_index in enumerate(headings):
        start_item = timeline[timeline_index]
        next_index = (
            headings[position + 1]
            if position + 1 < len(headings)
            else len(timeline)
        )
        end_item = timeline[next_index - 1]
        title = start_item["text"].strip()
        if len(title) > 120:
            title = title[:117] + "..."
        chapters.append(
            {
                "chapter_id": f"chapter:{len(chapters)}",
                "order": len(chapters),
                "name": title,
                "start_ms": start_item["start_ms"],
                "end_ms": end_item["end_ms"],
                "start_chunk_id": start_item["chunk_id"],
                "end_chunk_id": end_item["chunk_id"],
            }
        )
    return chapters


def transition_context(
    chunks: list[Mapping[str, Any]],
    *,
    selected_chunk_id: str,
    config: Mapping[str, Any] | None = None,
    mode: str = "smart",
) -> dict[str, Any] | None:
    timeline = chapter_rows(chunks, config=config)
    selected_index = next(
        (
            index
            for index, row in enumerate(timeline)
            if row["chunk_id"] == selected_chunk_id
        ),
        None,
    )
    if selected_index is None:
        return None
    chapters = build_chapters(chunks, config=config, mode=mode)
    current = timeline[selected_index]
    chapter = next(
        (
            item
            for item in chapters
            if int(item["start_ms"]) <= int(current["start_ms"])
            and int(current["end_ms"]) <= int(item["end_ms"])
        ),
        None,
    )
    previous = timeline[selected_index - 1] if selected_index > 0 else None
    following = (
        timeline[selected_index + 1]
        if selected_index + 1 < len(timeline)
        else None
    )
    return {
        "schema_version": 1,
        "chapter_mode": mode,
        "chapter": copy.deepcopy(chapter),
        "is_chapter_start": bool(
            chapter and chapter.get("start_chunk_id") == selected_chunk_id
        ),
        "is_chapter_end": bool(
            chapter and chapter.get("end_chunk_id") == selected_chunk_id
        ),
        "previous": copy.deepcopy(previous),
        "current": copy.deepcopy(current),
        "next": copy.deepcopy(following),
        "transition_before_ms": (
            int(previous["pause_after_ms"]) if previous is not None else None
        ),
        "transition_after_ms": int(current["pause_after_ms"]),
        "source_order_fingerprint": source_order_fingerprint(chunks),
    }


def create_processed_rendition(
    *,
    source_audio_path: str | Path,
    output_path: str | Path,
    operation: str,
    settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source = Path(source_audio_path).expanduser().resolve()
    destination = Path(output_path).expanduser().resolve()
    if not source.is_file() or source.is_symlink():
        raise ChapterAssemblyError(
            "chapter_assembly_source_missing",
            "The source Take audio is missing or unsafe.",
        )
    try:
        with source.open("rb") as handle:
            audio = AudioSegment.from_file(
                handle,
                format=source.suffix.casefold().lstrip(".") or None,
            )
    except Exception as exc:
        raise ChapterAssemblyError(
            "chapter_assembly_source_invalid",
            "The source Take audio could not be decoded.",
        ) from exc
    source_duration = len(audio)
    if source_duration < MIN_RENDITION_DURATION_MS:
        raise ChapterAssemblyError(
            "chapter_assembly_source_too_short",
            "The source Take is too short for a safe Final Listen edit.",
        )
    values = dict(settings or {})
    if operation == "trim_edges":
        start = _integer(
            values.get("trim_start_ms", 0),
            field="trim_start_ms",
            minimum=0,
            maximum=MAX_EDGE_TRIM_MS,
        )
        end = _integer(
            values.get("trim_end_ms", 0),
            field="trim_end_ms",
            minimum=0,
            maximum=MAX_EDGE_TRIM_MS,
        )
        if start == 0 and end == 0:
            raise ChapterAssemblyError(
                "chapter_assembly_trim_empty",
                "Trim at least one leading or trailing edge.",
            )
        if start + end > source_duration - MIN_RENDITION_DURATION_MS:
            raise ChapterAssemblyError(
                "chapter_assembly_trim_excessive",
                "The requested trim would remove the complete spoken rendition.",
                context={"source_duration_ms": source_duration},
            )
        output = audio[start : source_duration - end]
        normalized_settings = {
            "trim_start_ms": start,
            "trim_end_ms": end,
        }
        operation_id = "final_listen_trim_edges"
    elif operation == "split_with_pause":
        split_at = _integer(
            values.get("split_at_ms"),
            field="split_at_ms",
            minimum=MIN_SPLIT_EDGE_MS,
            maximum=max(MIN_SPLIT_EDGE_MS, source_duration - MIN_SPLIT_EDGE_MS),
        )
        pause = _integer(
            values.get("pause_ms"),
            field="pause_ms",
            minimum=MIN_SPLIT_PAUSE_MS,
            maximum=MAX_SPLIT_PAUSE_MS,
        )
        if split_at >= source_duration - MIN_SPLIT_EDGE_MS:
            raise ChapterAssemblyError(
                "chapter_assembly_split_edge_invalid",
                "Split the rendition away from its leading and trailing edges.",
                context={"source_duration_ms": source_duration},
            )
        fade = min(8, split_at, source_duration - split_at)
        left = audio[:split_at].fade_out(fade)
        right = audio[split_at:].fade_in(fade)
        silence = (
            AudioSegment.silent(duration=pause, frame_rate=audio.frame_rate)
            .set_channels(audio.channels)
            .set_sample_width(audio.sample_width)
        )
        output = left + silence + right
        normalized_settings = {
            "split_at_ms": split_at,
            "pause_ms": pause,
        }
        operation_id = "final_listen_split_with_pause"
    else:
        raise ChapterAssemblyError(
            "chapter_assembly_operation_invalid",
            "Final Listen supports only edge trim or one internal split with pause.",
            context={"operation": operation},
        )
    output = ensure_click_safe_fade_in(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        export = output.export(destination, format="wav")
        close = getattr(export, "close", None)
        if callable(close):
            close()
        validation = validate_audio_file(destination, format_hint="wav")
    except Exception as exc:
        destination.unlink(missing_ok=True)
        if isinstance(exc, ChapterAssemblyError):
            raise
        raise ChapterAssemblyError(
            "chapter_assembly_output_invalid",
            "The processed rendition could not be validated.",
        ) from exc
    processing = {
        "schema_version": 1,
        "operation": operation_id,
        "settings": normalized_settings,
        "source_sha256": sha256_file(source),
        "source_duration_ms": source_duration,
        "output_sha256": validation["sha256"],
        "output_duration_ms": validation["duration_ms"],
    }
    processing["processing_fingerprint"] = fingerprint_value(processing)
    return processing
