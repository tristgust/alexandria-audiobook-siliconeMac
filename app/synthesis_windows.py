from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import numpy as np

from audio_processing import (
    AudioProcessingError,
    prepare_generated_speech_audio,
    validate_generated_speech_duration,
)
from model_registry import (
    COMMUNITY_QWEN_ENGINE_ID,
    CUSTOM_VOICE_ENGINE_ID,
    EXTERNAL_GENERIC_ENGINE_ID,
    LORA_ENGINE_ID,
    RESPONSIVE_ROUTER_ENGINE_ID,
    RESPONSIVE_ROUTER_SELECTION_ID,
    STANDARD_CLONE_ENGINE_ID,
    VOICE_DESIGN_ENGINE_ID,
    synthesis_window_record_payloads,
)


SYNTHESIS_WINDOW_SCHEMA_VERSION = 1
SYNTHESIS_SEGMENT_PLAN_SCHEMA_VERSION = 1
SYNTHESIS_SEAM_RECEIPT_SCHEMA_VERSION = 1


class SynthesisWindowError(AudioProcessingError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SynthesisWindow:
    backend_id: str
    family: str
    max_chars: int
    max_words: int | None
    minimum_words: int
    seam_mode: str
    seam_ms: int
    split_priority: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SYNTHESIS_WINDOW_SCHEMA_VERSION,
            "backend_id": self.backend_id,
            "family": self.family,
            "max_chars": self.max_chars,
            "max_words": self.max_words,
            "minimum_words": self.minimum_words,
            "seam_mode": self.seam_mode,
            "seam_ms": self.seam_ms,
            "split_priority": list(self.split_priority),
        }


_WINDOWS: dict[str, SynthesisWindow] = {
    backend_id: SynthesisWindow(
        backend_id=backend_id,
        family=value["family"],
        max_chars=value["max_chars"],
        max_words=value["max_words"],
        minimum_words=value["minimum_words"],
        seam_mode=value["seam_mode"],
        seam_ms=value["seam_ms"],
        split_priority=tuple(value["split_priority"]),
    )
    for backend_id, value in synthesis_window_record_payloads().items()
}


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def synthesis_window(backend_id: str) -> dict[str, Any]:
    key = str(backend_id or EXTERNAL_GENERIC_ENGINE_ID).strip()
    declaration = _WINDOWS.get(key) or _WINDOWS[EXTERNAL_GENERIC_ENGINE_ID]
    result = declaration.as_dict()
    if key not in _WINDOWS:
        result["requested_backend_id"] = key
        result["fallback_declaration"] = True
    else:
        result["fallback_declaration"] = False
    result["declaration_fingerprint"] = hashlib.sha256(
        _canonical_json(result)
    ).hexdigest()
    return result


def synthesis_window_catalog() -> dict[str, dict[str, Any]]:
    return {key: synthesis_window(key) for key in sorted(_WINDOWS)}


def resolve_synthesis_backend_id(
    voice_data: Mapping[str, Any] | None,
    *,
    mode: str,
    use_mlx: bool,
) -> str:
    voice = dict(voice_data or {})
    voice_type = str(voice.get("type") or "custom")
    if voice_type == "community_qvoice":
        return COMMUNITY_QWEN_ENGINE_ID
    if voice_type in {"lora", "builtin_lora"}:
        return LORA_ENGINE_ID
    if voice_type == "design":
        return VOICE_DESIGN_ENGINE_ID
    if voice_type == "clone":
        backend = str(voice.get("clone_backend") or STANDARD_CLONE_ENGINE_ID)
        if backend == RESPONSIVE_ROUTER_SELECTION_ID:
            return RESPONSIVE_ROUTER_ENGINE_ID
        if backend in _WINDOWS:
            return backend
        return EXTERNAL_GENERIC_ENGINE_ID if mode != "local" else STANDARD_CLONE_ENGINE_ID
    if voice_type == "custom":
        return CUSTOM_VOICE_ENGINE_ID if mode == "local" or use_mlx else EXTERNAL_GENERIC_ENGINE_ID
    return EXTERNAL_GENERIC_ENGINE_ID


_BOUNDARY_PATTERNS: dict[str, re.Pattern[str]] = {
    "paragraph": re.compile(r"\n(?:[ \t]*\n)+"),
    "sentence": re.compile(r"(?<=[.!?])(?:[\"'”’)]*)\s+"),
    "clause": re.compile(r"(?<=[,;:—-])\s+"),
    "word": re.compile(r"\s+"),
}


def _word_count(value: str) -> int:
    return len(re.findall(r"\S+", value))


def _candidate_boundaries(text: str, start: int, end: int, kind: str) -> list[int]:
    pattern = _BOUNDARY_PATTERNS.get(kind)
    if pattern is None:
        return []
    return [start + match.end() for match in pattern.finditer(text[start:end])]


def _word_limited_end(text: str, start: int, hard_end: int, max_words: int | None) -> int:
    if max_words is None:
        return hard_end
    matches = list(re.finditer(r"\S+", text[start:hard_end]))
    if len(matches) <= max_words:
        return hard_end
    return start + matches[max_words - 1].end()


def _choose_end(text: str, start: int, declaration: Mapping[str, Any]) -> int:
    max_chars = max(1, int(declaration["max_chars"]))
    hard_end = min(len(text), start + max_chars)
    hard_end = min(
        hard_end,
        _word_limited_end(text, start, hard_end, declaration.get("max_words")),
    )
    if hard_end >= len(text):
        return len(text)
    minimum_end = min(
        hard_end,
        start + max(1, math.floor(max_chars * 0.45)),
    )
    priorities = tuple(declaration.get("split_priority") or ())
    for kind in priorities:
        if kind == "character":
            continue
        candidates = _candidate_boundaries(text, start, hard_end, kind)
        if kind in {"paragraph", "sentence"}:
            candidates = [
                value
                for value in candidates
                if start < value <= hard_end
                and _word_count(text[start:value])
                >= int(declaration.get("minimum_words") or 1)
            ]
        else:
            candidates = [
                value
                for value in candidates
                if minimum_end <= value <= hard_end
            ]
        if candidates:
            return candidates[-1]
    return hard_end


def _segment_record(
    *,
    text: str,
    start: int,
    end: int,
    index: int,
    plan_fingerprint_seed: str,
) -> dict[str, Any]:
    source_text = text[start:end]
    generation_text = source_text.strip()
    if not generation_text:
        raise SynthesisWindowError(
            "synthesis_segment_empty",
            "Internal synthesis segmentation produced a whitespace-only segment.",
        )
    return {
        "segment_id": f"segment_{index:04d}",
        "segment_index": index,
        "source_start": start,
        "source_end": end,
        "source_text": source_text,
        "generation_text": generation_text,
        "source_text_sha256": _sha256_text(source_text),
        "generation_text_sha256": _sha256_text(generation_text),
        "dependency_fingerprint": plan_fingerprint_seed,
    }


def plan_synthesis_segments(
    text: str,
    *,
    backend_id: str,
    dependency_fingerprint: str | None = None,
    max_chars: int | None = None,
    max_words: int | None = None,
) -> dict[str, Any]:
    source = str(text or "")
    declaration = synthesis_window(backend_id)
    if max_chars is not None:
        declaration["max_chars"] = max(1, int(max_chars))
    if max_words is not None:
        declaration["max_words"] = max(1, int(max_words))
    declaration["declaration_fingerprint"] = hashlib.sha256(
        _canonical_json(
            {
                key: value
                for key, value in declaration.items()
                if key != "declaration_fingerprint"
            }
        )
    ).hexdigest()
    seed = dependency_fingerprint or hashlib.sha256(
        _canonical_json(
            {
                "contract": "alexandria_synthesis_segment_dependency_v1",
                "backend_id": declaration["backend_id"],
                "declaration_fingerprint": declaration["declaration_fingerprint"],
                "text_sha256": _sha256_text(source),
            }
        )
    ).hexdigest()
    if not source:
        segments: list[dict[str, Any]] = []
    else:
        spans: list[tuple[int, int]] = []
        cursor = 0
        while cursor < len(source):
            end = _choose_end(source, cursor, declaration)
            if end <= cursor:
                raise SynthesisWindowError(
                    "synthesis_segment_no_progress",
                    "Internal synthesis segmentation did not advance.",
                )
            if not source[cursor:end].strip():
                if spans:
                    prior_start, _prior_end = spans[-1]
                    spans[-1] = (prior_start, end)
                else:
                    next_nonspace = re.search(r"\S", source[end:])
                    if next_nonspace is None:
                        return {
                            "schema_version": SYNTHESIS_SEGMENT_PLAN_SCHEMA_VERSION,
                            "backend": declaration,
                            "source_text": source,
                            "source_text_sha256": _sha256_text(source),
                            "dependency_fingerprint": seed,
                            "segments": [],
                            "segment_count": 0,
                            "plan_fingerprint": hashlib.sha256(
                                _canonical_json(
                                    {
                                        "backend": declaration,
                                        "source_text_sha256": _sha256_text(source),
                                        "dependency_fingerprint": seed,
                                        "segments": [],
                                    }
                                )
                            ).hexdigest(),
                        }
                    end += next_nonspace.start() + 1
                    spans.append((cursor, end))
            else:
                spans.append((cursor, end))
            cursor = end
        if spans and spans[-1][1] < len(source):
            start, _end = spans[-1]
            spans[-1] = (start, len(source))
        segments = [
            _segment_record(
                text=source,
                start=start,
                end=end,
                index=index,
                plan_fingerprint_seed=seed,
            )
            for index, (start, end) in enumerate(spans)
        ]
    reconstructed = "".join(item["source_text"] for item in segments)
    if reconstructed != source and source.strip():
        raise SynthesisWindowError(
            "synthesis_source_span_loss",
            "Internal synthesis segmentation did not preserve every source character.",
        )
    plan_seed = {
        "schema_version": SYNTHESIS_SEGMENT_PLAN_SCHEMA_VERSION,
        "backend": declaration,
        "source_text_sha256": _sha256_text(source),
        "dependency_fingerprint": seed,
        "segments": [
            {
                key: value
                for key, value in item.items()
                if key not in {"source_text", "generation_text"}
            }
            for item in segments
        ],
    }
    plan = {
        **plan_seed,
        "source_text": source,
        "segments": segments,
        "segment_count": len(segments),
        "plan_fingerprint": hashlib.sha256(_canonical_json(plan_seed)).hexdigest(),
    }
    return plan


def split_segment_for_retry(
    segment: Mapping[str, Any],
    *,
    minimum_words: int = 2,
    prefer_sentence: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    source_text = str(segment.get("source_text") or "")
    source_start = int(segment.get("source_start") or 0)
    if _word_count(source_text) < minimum_words * 2:
        return None
    midpoint = len(source_text) // 2
    sentence_candidates = _candidate_boundaries(
        source_text,
        0,
        len(source_text),
        "sentence",
    )
    word_candidates = _candidate_boundaries(
        source_text,
        0,
        len(source_text),
        "word",
    )
    candidates = sentence_candidates if prefer_sentence else word_candidates
    viable = [
        position
        for position in sorted(set(candidates))
        if _word_count(source_text[:position]) >= minimum_words
        and _word_count(source_text[position:]) >= minimum_words
    ]
    if prefer_sentence and not viable:
        viable = [
            position
            for position in sorted(set(word_candidates))
            if _word_count(source_text[:position]) >= minimum_words
            and _word_count(source_text[position:]) >= minimum_words
        ]
        prefer_sentence = False
    if not viable:
        return None
    split_at = (
        viable[0]
        if prefer_sentence
        else min(viable, key=lambda position: abs(position - midpoint))
    )
    dependency = str(segment.get("dependency_fingerprint") or "")
    base_index = int(segment.get("segment_index") or 0)
    left = _segment_record(
        text=source_text,
        start=0,
        end=split_at,
        index=base_index * 2,
        plan_fingerprint_seed=dependency,
    )
    right = _segment_record(
        text=source_text,
        start=split_at,
        end=len(source_text),
        index=base_index * 2 + 1,
        plan_fingerprint_seed=dependency,
    )
    for child in (left, right):
        child["source_start"] += source_start
        child["source_end"] += source_start
        child["parent_segment_id"] = segment.get("segment_id")
    left["segment_id"] = f"{segment.get('segment_id')}.0"
    right["segment_id"] = f"{segment.get('segment_id')}.1"
    return left, right


def _audio_hash(audio: np.ndarray) -> str:
    value = np.asarray(audio, dtype=np.float32).reshape(-1)
    return hashlib.sha256(value.tobytes()).hexdigest()


def _validate_result(
    *,
    segment: Mapping[str, Any],
    result: Mapping[str, Any],
) -> tuple[np.ndarray, int, dict[str, Any]]:
    try:
        sample_rate = int(result.get("sample_rate"))
    except (TypeError, ValueError) as exc:
        raise SynthesisWindowError(
            "synthesis_segment_sample_rate_invalid",
            f"{segment['segment_id']} returned no valid sample rate.",
        ) from exc
    if sample_rate <= 0:
        raise SynthesisWindowError(
            "synthesis_segment_sample_rate_invalid",
            f"{segment['segment_id']} returned no valid sample rate.",
        )
    audio = np.asarray(result.get("audio"), dtype=np.float32).reshape(-1)
    if audio.size == 0 or not np.all(np.isfinite(audio)):
        raise SynthesisWindowError(
            "synthesis_segment_audio_invalid",
            f"{segment['segment_id']} returned invalid or empty audio.",
        )
    try:
        prepared = prepare_generated_speech_audio(
            audio,
            sample_rate,
            str(segment["generation_text"]),
        )
    except AudioProcessingError as exc:
        raise SynthesisWindowError(
            "synthesis_segment_validation_failed",
            f"{segment['segment_id']} failed validation: {exc}",
        ) from exc
    return prepared, sample_rate, {
        "segment_id": segment["segment_id"],
        "segment_index": segment["segment_index"],
        "source_start": segment["source_start"],
        "source_end": segment["source_end"],
        "generation_text_sha256": segment["generation_text_sha256"],
        "dependency_fingerprint": segment["dependency_fingerprint"],
        "sample_rate": sample_rate,
        "raw_sample_count": int(audio.size),
        "prepared_sample_count": int(prepared.size),
        "prepared_audio_sha256": _audio_hash(prepared),
    }


def _crossfade(left: np.ndarray, right: np.ndarray, count: int) -> np.ndarray:
    overlap = min(max(0, count), left.size, right.size)
    if overlap <= 0:
        return np.concatenate((left, right))
    fade_out = np.linspace(1.0, 0.0, overlap, endpoint=False, dtype=np.float32)
    fade_in = 1.0 - fade_out
    blended = left[-overlap:] * fade_out + right[:overlap] * fade_in
    return np.concatenate((left[:-overlap], blended, right[overlap:]))


def assemble_synthesis_segments(
    plan: Mapping[str, Any],
    results: Iterable[Mapping[str, Any]],
) -> tuple[np.ndarray, int, dict[str, Any]]:
    segments = [copy.deepcopy(dict(item)) for item in plan.get("segments") or []]
    result_list = [copy.deepcopy(dict(item)) for item in results]
    if not segments:
        raise SynthesisWindowError(
            "synthesis_segment_plan_empty",
            "An empty synthesis request cannot be assembled into audio.",
        )
    expected_ids = [str(item["segment_id"]) for item in segments]
    observed_ids = [str(item.get("segment_id") or "") for item in result_list]
    if len(set(observed_ids)) != len(observed_ids):
        raise SynthesisWindowError(
            "synthesis_segment_duplicate",
            "Internal synthesis returned a duplicate segment result.",
        )
    missing = sorted(set(expected_ids) - set(observed_ids))
    unexpected = sorted(set(observed_ids) - set(expected_ids))
    if missing or unexpected:
        raise SynthesisWindowError(
            "synthesis_segment_expected_set_mismatch",
            "Internal synthesis did not return the exact planned segment set "
            f"(missing={missing}, unexpected={unexpected}).",
        )
    by_id = {str(item["segment_id"]): item for item in result_list}
    prepared: list[np.ndarray] = []
    records: list[dict[str, Any]] = []
    sample_rate: int | None = None
    for segment in segments:
        audio, rate, record = _validate_result(
            segment=segment,
            result=by_id[str(segment["segment_id"])],
        )
        if sample_rate is None:
            sample_rate = rate
        elif rate != sample_rate:
            raise SynthesisWindowError(
                "synthesis_segment_sample_rate_mismatch",
                "Internal synthesis segments returned different sample rates.",
            )
        prepared.append(audio)
        records.append(record)
    assert sample_rate is not None
    backend = dict(plan.get("backend") or {})
    seam_mode = str(backend.get("seam_mode") or "none")
    seam_samples = max(0, round(sample_rate * int(backend.get("seam_ms") or 0) / 1000.0))
    joined = prepared[0]
    expected_sample_count = int(prepared[0].size)
    seams: list[dict[str, Any]] = []
    for index, right in enumerate(prepared[1:], start=1):
        applied = 0
        before_count = int(joined.size)
        if seam_mode == "silence_gap":
            gap = np.zeros(seam_samples, dtype=np.float32)
            joined = np.concatenate((joined, gap, right))
            expected_sample_count += seam_samples + int(right.size)
            applied = seam_samples
        elif seam_mode == "crossfade":
            applied = min(seam_samples, joined.size, right.size)
            joined = _crossfade(joined, right, applied)
            expected_sample_count += int(right.size) - applied
        elif seam_mode == "discard_overlap":
            applied = min(seam_samples, max(0, right.size - 1))
            joined = np.concatenate((joined, right[applied:]))
            expected_sample_count += int(right.size) - applied
        elif seam_mode == "none":
            joined = np.concatenate((joined, right))
            expected_sample_count += int(right.size)
        else:
            raise SynthesisWindowError(
                "synthesis_seam_mode_unsupported",
                f"Unsupported synthesis seam mode: {seam_mode!r}.",
            )
        seams.append(
            {
                "left_segment_id": segments[index - 1]["segment_id"],
                "right_segment_id": segments[index]["segment_id"],
                "mode": seam_mode,
                "requested_samples": seam_samples,
                "applied_samples": int(applied),
                "output_start_sample": max(0, before_count - int(applied)),
                "output_end_sample": int(joined.size),
            }
        )
    restoration = "none"
    if joined.size > expected_sample_count:
        joined = joined[:expected_sample_count]
        restoration = "trim_tail"
    elif joined.size < expected_sample_count:
        joined = np.pad(joined, (0, expected_sample_count - joined.size))
        restoration = "pad_tail"
    if joined.size != expected_sample_count:
        raise SynthesisWindowError(
            "synthesis_exact_length_failed",
            "Internal synthesis could not restore the exact joined sample count.",
        )
    if joined.size == 0 or not np.all(np.isfinite(joined)):
        raise SynthesisWindowError(
            "synthesis_joined_audio_invalid",
            "Joined synthesis audio is invalid or empty.",
        )
    peak = float(np.max(np.abs(joined)))
    if peak < 1e-4:
        raise SynthesisWindowError(
            "synthesis_joined_audio_silent",
            "Joined synthesis audio is effectively silent.",
        )
    try:
        validate_generated_speech_duration(
            joined.size / sample_rate,
            str(plan.get("source_text") or ""),
        )
    except AudioProcessingError as exc:
        raise SynthesisWindowError(
            "synthesis_joined_duration_invalid",
            f"Joined synthesis audio failed duration validation: {exc}",
        ) from exc
    final_sample_count = int(joined.size)
    receipt_seed = {
        "schema_version": SYNTHESIS_SEAM_RECEIPT_SCHEMA_VERSION,
        "plan_fingerprint": plan.get("plan_fingerprint"),
        "dependency_fingerprint": plan.get("dependency_fingerprint"),
        "backend": backend,
        "source_text_sha256": plan.get("source_text_sha256"),
        "segment_results": records,
        "seams": seams,
        "pre_edge_expected_sample_count": int(expected_sample_count),
        "pre_edge_actual_sample_count": int(expected_sample_count),
        "final_sample_count": final_sample_count,
        "sample_rate": sample_rate,
        "exact_length_restoration": restoration,
    }
    receipt = {
        **receipt_seed,
        "segment_count": len(records),
        "joined_audio_sha256": _audio_hash(joined),
        "receipt_fingerprint": hashlib.sha256(_canonical_json(receipt_seed)).hexdigest(),
    }
    return joined, sample_rate, receipt


def one_segment_receipt(
    *,
    text: str,
    backend_id: str,
    audio: np.ndarray,
    sample_rate: int,
    dependency_fingerprint: str | None = None,
) -> dict[str, Any]:
    plan = plan_synthesis_segments(
        text,
        backend_id=backend_id,
        dependency_fingerprint=dependency_fingerprint,
        max_chars=max(1, len(str(text or "")) or 1),
        max_words=max(1, _word_count(str(text or "")) or 1),
    )
    if len(plan["segments"]) != 1:
        raise SynthesisWindowError(
            "synthesis_one_segment_receipt_invalid",
            "One-segment receipt construction produced multiple segments.",
        )
    _joined, _rate, receipt = assemble_synthesis_segments(
        plan,
        [
            {
                "segment_id": plan["segments"][0]["segment_id"],
                "audio": np.asarray(audio, dtype=np.float32),
                "sample_rate": int(sample_rate),
            }
        ],
    )
    return receipt


def synthesis_receipt_chunk_fields(receipt: Mapping[str, Any] | None) -> dict[str, Any]:
    value = dict(receipt or {})
    if not value:
        return {}
    return {
        "synthesis_window_backend": (value.get("backend") or {}).get("backend_id"),
        "synthesis_window_declaration_fingerprint": (
            value.get("backend") or {}
        ).get("declaration_fingerprint"),
        "synthesis_segment_plan_fingerprint": value.get("plan_fingerprint"),
        "synthesis_segment_dependency_fingerprint": value.get(
            "dependency_fingerprint"
        ),
        "synthesis_segment_count": value.get("segment_count"),
        "synthesis_seam_receipt": copy.deepcopy(value),
        "synthesis_seam_receipt_fingerprint": value.get("receipt_fingerprint"),
        "synthesis_final_sample_count": value.get("final_sample_count"),
        "synthesis_sample_rate": value.get("sample_rate"),
    }


def synthesis_receipt_reset_fields() -> dict[str, Any]:
    return {
        "synthesis_window_backend": None,
        "synthesis_window_declaration_fingerprint": None,
        "synthesis_segment_plan_fingerprint": None,
        "synthesis_segment_dependency_fingerprint": None,
        "synthesis_segment_count": None,
        "synthesis_seam_receipt": None,
        "synthesis_seam_receipt_fingerprint": None,
        "synthesis_final_sample_count": None,
        "synthesis_sample_rate": None,
        "synthesis_segment_backend_metadata": None,
        "synthesis_fish_inline_plan_bypassed_reason": None,
    }


def synthesis_binding_fields(chunk: Mapping[str, Any]) -> dict[str, Any] | None:
    fingerprint = chunk.get("synthesis_seam_receipt_fingerprint")
    if not fingerprint:
        return None
    backend_id = str(chunk.get("synthesis_window_backend") or "").strip()
    current_declaration = (
        synthesis_window(backend_id)
        if backend_id
        else None
    )
    return {
        "window_backend": backend_id or None,
        "recorded_window_declaration_fingerprint": chunk.get(
            "synthesis_window_declaration_fingerprint"
        ),
        "current_window_declaration_fingerprint": (
            current_declaration.get("declaration_fingerprint")
            if current_declaration is not None
            else None
        ),
        "segment_plan_fingerprint": chunk.get("synthesis_segment_plan_fingerprint"),
        "segment_dependency_fingerprint": chunk.get(
            "synthesis_segment_dependency_fingerprint"
        ),
        "seam_receipt_fingerprint": fingerprint,
        "final_sample_count": chunk.get("synthesis_final_sample_count"),
        "sample_rate": chunk.get("synthesis_sample_rate"),
    }
