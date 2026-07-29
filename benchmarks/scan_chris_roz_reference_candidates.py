#!/usr/bin/env python3
"""Rank every plausible Big Finish speech turn against Travis/Yasmin anchors.

Research-only: source audio and Alexandria production state remain untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))

from prepare_chris_roz_reference_round import (  # noqa: E402
    CONFIG_PATH,
    WAVLM_MODEL,
    SpeakerEmbedder,
    cosine,
    l2_normalize,
    read_mono_16k,
    source_maps,
    write_json,
)

DEFAULT_REFERENCE_ROOT = ROOT / ".omo/evidence/chris-roz-reference-selection-v1"
DEFAULT_OUTPUT = ROOT / ".omo/evidence/chris-roz-reference-scan-v1"
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")
_BAD_RE = re.compile(
    r"\b(you have been listening|was played by|executive producer|script editor|director was|copyright|big finish productions)\b",
    re.IGNORECASE,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decode_source(path: Path) -> np.ndarray:
    return read_mono_16k(path)


def segment_candidates(transcript: dict[str, Any], *, story_end: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, segment in enumerate(transcript.get("segments", [])):
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or 0.0)
        text = str(segment.get("text") or "").strip()
        duration = end - start
        words = _WORD_RE.findall(text)
        if start >= story_end or duration < 2.8 or duration > 18.0:
            continue
        if len(words) < 6 or len(words) > 48 or _BAD_RE.search(text):
            continue
        normalized = " ".join(word.casefold() for word in words)
        if not normalized:
            continue
        unique_ratio = len(set(words)) / max(1, len(words))
        if unique_ratio < 0.38:
            continue
        rows.append(
            {
                "segment_index": index,
                "start_seconds": start,
                "end_seconds": end,
                "duration_seconds": duration,
                "text": text,
                "word_count": len(words),
            }
        )
    return rows


def pad_or_trim(array: np.ndarray, minimum: int = 16000) -> np.ndarray:
    if array.size >= minimum:
        return array.astype(np.float32, copy=False)
    return np.pad(array.astype(np.float32, copy=False), (0, minimum - array.size))


def acoustic_metrics(array: np.ndarray) -> dict[str, float]:
    if array.size == 0:
        return {"rms_dbfs": -180.0, "peak_dbfs": -180.0, "clipping_fraction": 0.0, "silence_ratio": 1.0}
    absolute = np.abs(array)
    rms = float(np.sqrt(np.mean(np.square(array, dtype=np.float64))))
    peak = float(np.max(absolute))
    frame = 400
    usable = array[: (array.size // frame) * frame]
    if usable.size:
        frame_rms = np.sqrt(np.mean(np.square(usable.reshape(-1, frame), dtype=np.float64), axis=1))
        silence_ratio = float(np.mean(frame_rms < 10 ** (-45 / 20)))
    else:
        silence_ratio = 1.0
    return {
        "rms_dbfs": round(20 * math.log10(max(rms, 1e-9)), 4),
        "peak_dbfs": round(20 * math.log10(max(peak, 1e-9)), 4),
        "clipping_fraction": round(float(np.mean(absolute >= 0.999)), 8),
        "silence_ratio": round(silence_ratio, 6),
    }


def embed_batch(embedder: SpeakerEmbedder, arrays: list[np.ndarray], batch_size: int = 24) -> list[np.ndarray]:
    result: list[np.ndarray] = []
    for offset in range(0, len(arrays), batch_size):
        result.extend(embedder._batch_embeddings(arrays[offset : offset + batch_size]))
    return result


def source_path(config: dict[str, Any], source: dict[str, Any]) -> Path:
    return (Path(str(config["source_root"])).expanduser().resolve() / str(source["audio"])).resolve()


def trim_preview(source: Path, target: Path, *, start: float, end: float) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-to",
            f"{end:.3f}",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(target),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-1000:])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--reference-root", default=str(DEFAULT_REFERENCE_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--wavlm-cache", default="/private/tmp/alexandria-chris-roz-20260729/hf-cache")
    parser.add_argument("--top-per-source", type=int, default=20)
    parser.add_argument("--preview-per-identity", type=int, default=36)
    parser.add_argument("--source", action="append", default=[])
    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    reference_root = Path(args.reference_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    embedder = SpeakerEmbedder(cache_dir=Path(args.wavlm_cache).expanduser().resolve())
    anchor_vectors: dict[str, np.ndarray] = {}
    for identity in ("chris", "roz"):
        paths = sorted((reference_root / "anchors").glob(f"{identity}-*.wav"))
        if not paths:
            raise FileNotFoundError(f"No {identity} anchor in {reference_root}")
        vectors = [embedder.embed(path)[0] for path in paths]
        anchor_vectors[identity] = l2_normalize(np.mean(np.stack(vectors), axis=0))

    transcript_root = Path(str(config["transcript_root"])).expanduser().resolve()
    all_ranked: dict[str, list[dict[str, Any]]] = {"chris": [], "roz": []}
    source_records: list[dict[str, Any]] = []

    selected_sources = list(config["sources"])
    if args.source:
        requested = set(args.source)
        available = {str(row["key"]) for row in selected_sources}
        unknown = requested - available
        if unknown:
            raise ValueError(f"Unknown source keys: {sorted(unknown)}")
        selected_sources = [row for row in selected_sources if row["key"] in requested]

    for source in selected_sources:
        audio_path = source_path(config, source)
        transcript_path = transcript_root / str(source["transcript"])
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        candidates = segment_candidates(transcript, story_end=float(source["story_end_seconds"]))
        waveform = decode_source(audio_path)
        arrays: list[np.ndarray] = []
        raw_clips: list[np.ndarray] = []
        valid: list[dict[str, Any]] = []
        for row in candidates:
            start = max(0, int(round(row["start_seconds"] * 16000)))
            end = min(waveform.size, int(round(row["end_seconds"] * 16000)))
            clip = waveform[start:end]
            if clip.size < int(2.5 * 16000):
                continue
            valid.append(row)
            raw_clips.append(clip)
            arrays.append(pad_or_trim(clip))
        embeddings = embed_batch(embedder, arrays)
        base_rows: list[dict[str, Any]] = []
        source_hash = sha256_file(audio_path)
        for row, vector, clip in zip(valid, embeddings, raw_clips):
            similarities = {key: cosine(vector, anchor) for key, anchor in anchor_vectors.items()}
            base_rows.append(
                {
                    **row,
                    "source_key": source["key"],
                    "source_label": source["label"],
                    "audio_path": str(audio_path),
                    "source_sha256": source_hash,
                    "similarity": similarities,
                    "acoustic_metrics": acoustic_metrics(clip),
                }
            )

        preliminary: set[int] = set()
        preliminary_limit = max(60, int(args.top_per_source) * 4)
        for identity in ("chris", "roz"):
            other = "roz" if identity == "chris" else "chris"
            ranked_indexes = sorted(
                range(len(base_rows)),
                key=lambda index: (
                    base_rows[index]["similarity"][identity],
                    base_rows[index]["similarity"][identity] - base_rows[index]["similarity"][other],
                    -base_rows[index]["acoustic_metrics"]["silence_ratio"],
                ),
                reverse=True,
            )
            kept = 0
            for index in ranked_indexes:
                row = base_rows[index]
                target = row["similarity"][identity]
                margin = target - row["similarity"][other]
                if target < 0.62 or margin < -0.03:
                    continue
                preliminary.add(index)
                kept += 1
                if kept >= preliminary_limit:
                    break

        ordered_indexes = sorted(preliminary)
        half_arrays: list[np.ndarray] = []
        for index in ordered_indexes:
            clip = raw_clips[index]
            midpoint = clip.size // 2
            half_arrays.extend([pad_or_trim(clip[:midpoint]), pad_or_trim(clip[midpoint:])])
        half_embeddings = embed_batch(embedder, half_arrays)
        validated_rows: list[dict[str, Any]] = []
        for position, index in enumerate(ordered_indexes):
            first = half_embeddings[position * 2]
            second = half_embeddings[position * 2 + 1]
            halves = {
                key: [cosine(first, anchor), cosine(second, anchor)]
                for key, anchor in anchor_vectors.items()
            }
            validated_rows.append(
                {
                    **base_rows[index],
                    "half_similarity": halves,
                    "half_consistency": cosine(first, second),
                }
            )

        for identity in ("chris", "roz"):
            other = "roz" if identity == "chris" else "chris"
            ranked = sorted(
                validated_rows,
                key=lambda row: (
                    min(row["half_similarity"][identity]),
                    row["similarity"][identity] - row["similarity"][other],
                    row["half_consistency"],
                    -row["acoustic_metrics"]["silence_ratio"],
                ),
                reverse=True,
            )
            selected = []
            for row in ranked:
                target = row["similarity"][identity]
                margin = target - row["similarity"][other]
                min_half = min(row["half_similarity"][identity])
                if target < 0.74 or margin < 0.03 or min_half < 0.70 or row["half_consistency"] < 0.60:
                    continue
                enriched = dict(row)
                enriched["identity"] = identity
                enriched["target_similarity"] = target
                enriched["identity_margin"] = margin
                enriched["min_half_similarity"] = min_half
                selected.append(enriched)
                if len(selected) >= int(args.top_per_source):
                    break
            all_ranked[identity].extend(selected)
        source_records.append(
            {
                "source_key": source["key"],
                "candidate_count": len(base_rows),
                "audio_sha256": sha256_file(audio_path),
                "transcript_sha256": sha256_file(transcript_path),
            }
        )
        write_json(
            output_root / "progress.json",
            {
                "schema_version": 1,
                "completed_sources": [row["source_key"] for row in source_records],
                "selected_source_count": len(selected_sources),
                "retained_chris_candidates": len(all_ranked["chris"]),
                "retained_roz_candidates": len(all_ranked["roz"]),
            },
        )
        del waveform, arrays, raw_clips, half_arrays, embeddings, half_embeddings, base_rows, validated_rows

    previews: dict[str, list[dict[str, Any]]] = {"chris": [], "roz": []}
    for identity in ("chris", "roz"):
        ranked = sorted(
            all_ranked[identity],
            key=lambda row: (
                row["min_half_similarity"],
                row["identity_margin"],
                row["half_consistency"],
                row["duration_seconds"],
            ),
            reverse=True,
        )
        seen_text: set[str] = set()
        for row in ranked:
            normalized = " ".join(_WORD_RE.findall(row["text"].casefold()))
            if normalized in seen_text:
                continue
            seen_text.add(normalized)
            preview_id = f"{identity}-{len(previews[identity]) + 1:02d}-{row['source_key']}-{row['segment_index']}"
            target = output_root / "previews" / identity / f"{preview_id}.wav"
            trim_preview(
                Path(row["audio_path"]),
                target,
                start=float(row["start_seconds"]),
                end=float(row["end_seconds"]),
            )
            preview = dict(row)
            preview["preview_id"] = preview_id
            preview["preview_audio"] = str(target.relative_to(output_root))
            preview["preview_sha256"] = sha256_file(target)
            previews[identity].append(preview)
            if len(previews[identity]) >= int(args.preview_per_identity):
                break

    write_json(
        output_root / "scan-results.json",
        {
            "schema_version": 1,
            "purpose": "exhaustive_chris_roz_reference_candidate_scan",
            "config_sha256": sha256_file(config_path),
            "speaker_model": WAVLM_MODEL,
            "source_records": source_records,
            "previews": previews,
            "production_promotion_allowed": False,
            "source_audio_changed": False,
        },
    )
    summary = {
        "output": str(output_root / "scan-results.json"),
        "chris_previews": len(previews["chris"]),
        "roz_previews": len(previews["roz"]),
        "top_chris": [
            {"id": row["preview_id"], "source": row["source_key"], "time": row["start_seconds"], "score": row["target_similarity"], "text": row["text"]}
            for row in previews["chris"][:10]
        ],
        "top_roz": [
            {"id": row["preview_id"], "source": row["source_key"], "time": row["start_seconds"], "score": row["target_similarity"], "text": row["text"]}
            for row in previews["roz"][:10]
        ],
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
