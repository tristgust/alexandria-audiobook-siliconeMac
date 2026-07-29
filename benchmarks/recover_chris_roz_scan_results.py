#!/usr/bin/env python3
"""Recover final scan metadata from already-generated ranked preview WAVs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))

from prepare_chris_roz_reference_round import (  # noqa: E402
    CONFIG_PATH,
    WAVLM_MODEL,
    SpeakerEmbedder,
    acoustic_metrics,
    cosine,
    l2_normalize,
    read_mono_16k,
    sha256_file,
    write_json,
)

DEFAULT_REFERENCE_ROOT = ROOT / ".omo/evidence/chris-roz-reference-selection-v1"
DEFAULT_SCAN_ROOT = Path("/private/tmp/alexandria-chris-roz-scan-v1")
_FILENAME = re.compile(r"^(chris|roz)-(\d+)-(.+)-(\d+)\.wav$")


def pad(array: np.ndarray, minimum: int = 16000) -> np.ndarray:
    array = array.astype(np.float32, copy=False)
    if array.size >= minimum:
        return array
    return np.pad(array, (0, minimum - array.size))


def embed_batches(embedder: SpeakerEmbedder, rows: list[np.ndarray], size: int = 16) -> list[np.ndarray]:
    result: list[np.ndarray] = []
    for offset in range(0, len(rows), size):
        result.extend(embedder._batch_embeddings(rows[offset : offset + size]))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--reference-root", default=str(DEFAULT_REFERENCE_ROOT))
    parser.add_argument("--scan-root", default=str(DEFAULT_SCAN_ROOT))
    parser.add_argument("--wavlm-cache", default="/private/tmp/alexandria-chris-roz-20260729/hf-cache")
    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    reference_root = Path(args.reference_root).expanduser().resolve()
    scan_root = Path(args.scan_root).expanduser().resolve()
    preview_root = scan_root / "previews"
    transcript_root = Path(str(config["transcript_root"])).expanduser().resolve()
    source_by_key = {str(row["key"]): dict(row) for row in config["sources"]}

    embedder = SpeakerEmbedder(cache_dir=Path(args.wavlm_cache).expanduser().resolve())
    anchors: dict[str, np.ndarray] = {}
    for identity in ("chris", "roz"):
        paths = sorted((reference_root / "anchors").glob(f"{identity}-*.wav"))
        if not paths:
            raise FileNotFoundError(f"Missing {identity} anchor")
        anchors[identity] = l2_normalize(np.mean(np.stack([embedder.embed(path)[0] for path in paths]), axis=0))

    records: list[dict[str, Any]] = []
    full_arrays: list[np.ndarray] = []
    half_arrays: list[np.ndarray] = []
    for path in sorted(preview_root.rglob("*.wav")):
        match = _FILENAME.match(path.name)
        if match is None:
            continue
        identity, rank_text, source_key, segment_text = match.groups()
        source = source_by_key[source_key]
        transcript_path = transcript_root / str(source["transcript"])
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        segment_index = int(segment_text)
        segment = transcript["segments"][segment_index]
        audio = read_mono_16k(path)
        midpoint = audio.size // 2
        full_arrays.append(pad(audio))
        half_arrays.extend([pad(audio[:midpoint]), pad(audio[midpoint:])])
        info = sf.info(path)
        records.append(
            {
                "preview_id": path.stem,
                "identity": identity,
                "rank": int(rank_text),
                "source_key": source_key,
                "source_label": source["label"],
                "segment_index": segment_index,
                "start_seconds": float(segment["start"]),
                "end_seconds": float(segment["end"]),
                "duration_seconds": info.frames / info.samplerate,
                "text": str(segment.get("text") or "").strip(),
                "word_count": len(str(segment.get("text") or "").split()),
                "audio_path": str((Path(str(config["source_root"])).expanduser().resolve() / str(source["audio"])).resolve()),
                "preview_audio": str(path.relative_to(scan_root)),
                "preview_sha256": sha256_file(path),
                "acoustic_metrics": acoustic_metrics(path),
            }
        )

    full_embeddings = embed_batches(embedder, full_arrays)
    half_embeddings = embed_batches(embedder, half_arrays)
    for index, (record, vector) in enumerate(zip(records, full_embeddings)):
        first = half_embeddings[index * 2]
        second = half_embeddings[index * 2 + 1]
        identity = str(record["identity"])
        other = "roz" if identity == "chris" else "chris"
        similarities = {key: cosine(vector, anchor) for key, anchor in anchors.items()}
        halves = {key: [cosine(first, anchor), cosine(second, anchor)] for key, anchor in anchors.items()}
        record["similarity"] = similarities
        record["half_similarity"] = halves
        record["half_consistency"] = cosine(first, second)
        record["target_similarity"] = similarities[identity]
        record["identity_margin"] = similarities[identity] - similarities[other]
        record["min_half_similarity"] = min(halves[identity])

    previews = {
        identity: sorted([row for row in records if row["identity"] == identity], key=lambda row: int(row["rank"]))
        for identity in ("chris", "roz")
    }
    source_records = []
    for source in config["sources"]:
        audio_path = (Path(str(config["source_root"])).expanduser().resolve() / str(source["audio"])).resolve()
        transcript_path = transcript_root / str(source["transcript"])
        source_records.append(
            {
                "source_key": source["key"],
                "audio_sha256": sha256_file(audio_path),
                "transcript_sha256": sha256_file(transcript_path),
            }
        )
    payload = {
        "schema_version": 1,
        "purpose": "recovered_exhaustive_chris_roz_reference_candidate_scan",
        "recovered_from_ranked_preview_audio": True,
        "config_sha256": sha256_file(config_path),
        "speaker_model": WAVLM_MODEL,
        "source_records": source_records,
        "previews": previews,
        "production_promotion_allowed": False,
        "source_audio_changed": False,
    }
    write_json(scan_root / "scan-results.json", payload)
    print(json.dumps({"output": str(scan_root / "scan-results.json"), "chris": len(previews["chris"]), "roz": len(previews["roz"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
