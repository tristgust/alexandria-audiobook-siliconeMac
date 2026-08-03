#!/usr/bin/env python3
"""Retrieve likely Chris Cwej and Roz Forrester speech from owned dramas.

The script uses Alexandria's local Qwen speaker encoder. Public actor audio is
used only to locate matching canonical Big Finish dialogue. Final clone clips
remain subject to listening and exact-transcript review.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import html
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import mlx.core as mx
import numpy as np
import soundfile as sf

from mlx_backend import MLXBackend

CONFIG_PATH = ROOT / "benchmarks/cwej_roz_sources.json"
DEFAULT_EVIDENCE_ROOT = ROOT / ".omo/evidence/cwej-roz-voice-evaluation"
ECAPA_REPO_ID = "speechbrain/spkrec-ecapa-voxceleb"
ECAPA_REVISION = "0f99f2d0ebe89ac095bcc5903c4dd8f72b367286"


class RetrievalError(RuntimeError):
    pass


@dataclass(frozen=True)
class Segment:
    source_group: str
    source_key: str
    source_path: Path
    start_seconds: float
    end_seconds: float
    text: str
    transcript_path: Path
    segment_index: int

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds

    @property
    def candidate_id(self) -> str:
        value = (
            f"{self.source_group}|{self.source_key}|{self.start_seconds:.3f}|"
            f"{self.end_seconds:.3f}|{self.text}"
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def extract_clip(source: Path, target: Path, start: float, end: float) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        return
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{max(0.0, start):.3f}",
            "-t",
            f"{max(0.05, end - start):.3f}",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(target),
        ]
    )


def load_audio(path: Path) -> np.ndarray:
    audio, sample_rate = sf.read(str(path), dtype="float32", always_2d=False)
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)
    if sample_rate != 24000:
        raise RetrievalError(f"Expected 24 kHz prepared audio: {path}")
    if audio.size < 2400:
        raise RetrievalError(f"Audio is too short for speaker encoding: {path}")
    return audio


class QwenSpeakerEncoder:
    name = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"
    revision = "e7dd0585652209fa0d7783659aad4e8a324de11c"

    def __init__(self) -> None:
        backend = MLXBackend(language="English")
        self._model = backend._model("clone")

    def encode(self, path: Path) -> np.ndarray:
        audio = load_audio(path)
        return np.asarray(
            self._model.extract_speaker_embedding(mx.array(audio), sr=24000),
            dtype=np.float32,
        ).reshape(-1)


class EcapaSpeakerEncoder:
    name = ECAPA_REPO_ID
    revision = ECAPA_REVISION

    def __init__(self, evidence_root: Path) -> None:
        try:
            import torch
            import torchaudio
            from huggingface_hub import snapshot_download
            from speechbrain.inference.speaker import EncoderClassifier
        except ImportError as exc:
            raise RetrievalError(
                "ECAPA retrieval requires the isolated .benchmark-speaker-env."
            ) from exc
        self._torch = torch
        self._torchaudio = torchaudio
        snapshot = snapshot_download(
            repo_id=ECAPA_REPO_ID,
            revision=ECAPA_REVISION,
        )
        self._classifier = EncoderClassifier.from_hparams(
            source=snapshot,
            savedir=str(
                evidence_root
                / "private/models"
                / f"spkrec-ecapa-voxceleb-{ECAPA_REVISION[:8]}"
            ),
            run_opts={"device": "cpu"},
        )

    def encode(self, path: Path) -> np.ndarray:
        waveform, sample_rate = self._torchaudio.load(str(path))
        if waveform.ndim != 2:
            raise RetrievalError(f"Unexpected ECAPA waveform shape: {path}")
        waveform = waveform.mean(dim=0, keepdim=True)
        if sample_rate != 16000:
            waveform = self._torchaudio.functional.resample(
                waveform, sample_rate, 16000
            )
        with self._torch.inference_mode():
            vector = self._classifier.encode_batch(
                waveform, normalize=True
            ).detach().cpu().numpy().reshape(-1)
        return np.asarray(vector, dtype=np.float32)


def embedding(encoder: Any, path: Path) -> np.ndarray:
    vector = np.asarray(encoder.encode(path), dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if not math.isfinite(norm) or norm <= 0:
        raise RetrievalError(f"Invalid speaker embedding: {path}")
    return vector / norm


def average_embeddings(vectors: Iterable[np.ndarray]) -> np.ndarray:
    values = list(vectors)
    if not values:
        raise RetrievalError("No speaker embeddings were provided.")
    mean = np.mean(np.stack(values), axis=0)
    return mean / (np.linalg.norm(mean) + 1e-12)


def cosine(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.dot(left, right))


def acoustic_metrics(path: Path) -> dict[str, float]:
    audio = load_audio(path)
    peak = float(np.max(np.abs(audio)))
    rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
    peak_dbfs = 20.0 * math.log10(max(peak, 1e-8))
    rms_dbfs = 20.0 * math.log10(max(rms, 1e-8))
    clipping_fraction = float(np.mean(np.abs(audio) >= 0.995))
    frame = 480
    hop = 240
    if audio.size < frame:
        silence_fraction = float(rms_dbfs < -45.0)
    else:
        windows = np.lib.stride_tricks.sliding_window_view(audio, frame)[::hop]
        frame_rms = np.sqrt(np.mean(np.square(windows), axis=1) + 1e-12)
        silence_fraction = float(np.mean(frame_rms < 10 ** (-45.0 / 20.0)))
    zero_crossing_rate = float(np.mean(np.abs(np.diff(np.signbit(audio)))))
    return {
        "peak_dbfs": round(peak_dbfs, 4),
        "rms_dbfs": round(rms_dbfs, 4),
        "clipping_fraction": round(clipping_fraction, 8),
        "silence_fraction": round(silence_fraction, 6),
        "zero_crossing_rate": round(zero_crossing_rate, 6),
    }


def quality_penalty(metrics: dict[str, float], duration: float) -> float:
    penalty = 0.0
    if metrics["rms_dbfs"] < -34.0:
        penalty += min(0.12, (-34.0 - metrics["rms_dbfs"]) * 0.008)
    if metrics["rms_dbfs"] > -8.0:
        penalty += min(0.08, (metrics["rms_dbfs"] + 8.0) * 0.01)
    penalty += min(0.12, metrics["silence_fraction"] * 0.3)
    penalty += min(0.15, metrics["clipping_fraction"] * 20.0)
    if duration < 3.5:
        penalty += (3.5 - duration) * 0.02
    if duration > 10.5:
        penalty += (duration - 10.5) * 0.01
    return penalty


def source_maps(manifest: dict[str, Any]) -> tuple[dict[str, Path], dict[str, Path]]:
    owned = {row["key"]: Path(row["path"]) for row in manifest["owned_dramas"]}
    public = {
        row["key"]: Path(row["prepared_path"])
        for row in manifest["public_references"]
    }
    return owned, public


def load_transcript_segments(
    evidence_root: Path,
    owned_paths: dict[str, Path],
    public_paths: dict[str, Path],
) -> list[Segment]:
    rows: list[Segment] = []
    chunk_root = evidence_root / "private/transcripts/chunks"
    for path in sorted(chunk_root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        stem = path.stem
        if stem.startswith("owned_drama-"):
            group = "owned_drama"
            remainder = stem[len("owned_drama-") :]
            source_key = remainder.rsplit("-", 1)[0]
            source_path = owned_paths.get(source_key)
        elif stem.startswith("public_reference-"):
            group = "public_reference"
            remainder = stem[len("public_reference-") :]
            source_key = remainder.rsplit("-", 1)[0]
            source_path = public_paths.get(source_key)
        else:
            continue
        if source_path is None:
            raise RetrievalError(f"Unknown source key in transcript: {path}")
        for index, segment in enumerate(payload.get("segments") or []):
            text = re.sub(r"\s+", " ", str(segment.get("text") or "")).strip()
            rows.append(
                Segment(
                    source_group=group,
                    source_key=source_key,
                    source_path=source_path,
                    start_seconds=float(segment["start"]),
                    end_seconds=float(segment["end"]),
                    text=text,
                    transcript_path=path,
                    segment_index=index,
                )
            )
    return rows


def eligible(segment: Segment, retrieval: dict[str, Any]) -> bool:
    duration = segment.duration_seconds
    words = re.findall(r"[A-Za-z0-9']+", segment.text)
    if duration < float(retrieval["minimum_segment_seconds"]):
        return False
    if duration > float(retrieval["maximum_segment_seconds"]):
        return False
    if len(words) < int(retrieval["minimum_words"]):
        return False
    if len(words) > int(retrieval["maximum_words"]):
        return False
    lowered = segment.text.lower()
    if any(marker in lowered for marker in ("[music]", "♪", "subtitles by")):
        return False
    return True


def make_seed_embeddings(
    *,
    config: dict[str, Any],
    public_paths: dict[str, Path],
    evidence_root: Path,
    model: Any,
) -> tuple[dict[str, np.ndarray], list[dict[str, Any]]]:
    seed_root = evidence_root / "private/retrieval/seeds"
    seed_vectors: dict[str, np.ndarray] = {}
    receipts: list[dict[str, Any]] = []
    for speaker, intervals in config["speaker_seed_intervals"].items():
        vectors: list[np.ndarray] = []
        for index, interval in enumerate(intervals):
            source_key = interval["source_key"]
            source = public_paths[source_key]
            target = seed_root / speaker / f"seed-{index:02d}.wav"
            extract_clip(
                source,
                target,
                float(interval["start_seconds"]),
                float(interval["end_seconds"]),
            )
            vector = embedding(model, target)
            vectors.append(vector)
            receipts.append(
                {
                    "speaker": speaker,
                    "seed_index": index,
                    "source_key": source_key,
                    "start_seconds": interval["start_seconds"],
                    "end_seconds": interval["end_seconds"],
                    "note": interval.get("note"),
                    "path": str(target),
                    "sha256": sha256_file(target),
                    "metrics": acoustic_metrics(target),
                }
            )
        seed_vectors[speaker] = average_embeddings(vectors)
    return seed_vectors, receipts


def rank_demo_segments(
    *,
    segments: list[Segment],
    anchor: np.ndarray,
    evidence_root: Path,
    model: Any,
    retrieval: dict[str, Any],
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    demo = [
        row
        for row in segments
        if row.source_group == "public_reference"
        and row.source_key == "travis_demo_reel"
        and eligible(row, retrieval)
    ]
    clip_root = evidence_root / "private/retrieval/travis-demo"
    rankings: list[dict[str, Any]] = []
    vectors: dict[str, np.ndarray] = {}
    for row in demo:
        path = clip_root / f"{row.candidate_id}.wav"
        extract_clip(row.source_path, path, row.start_seconds, row.end_seconds)
        vector = embedding(model, path)
        vectors[row.candidate_id] = vector
        rankings.append(
            {
                "candidate_id": row.candidate_id,
                "start_seconds": row.start_seconds,
                "end_seconds": row.end_seconds,
                "duration_seconds": row.duration_seconds,
                "text": row.text,
                "cosine_to_anchor": round(cosine(anchor, vector), 6),
                "path": str(path),
                "sha256": sha256_file(path),
                "metrics": acoustic_metrics(path),
            }
        )
    rankings.sort(key=lambda item: item["cosine_to_anchor"], reverse=True)
    selected = [
        vectors[row["candidate_id"]]
        for row in rankings
        if row["cosine_to_anchor"] >= 0.88
    ][:10]
    if not selected:
        selected = [anchor]
    elif all(cosine(anchor, vector) < 0.9999 for vector in selected):
        selected.insert(0, anchor)
    return average_embeddings(selected), rankings


def seed_fingerprint(seed_vectors: dict[str, np.ndarray]) -> str:
    digest = hashlib.sha256()
    for key in sorted(seed_vectors):
        digest.update(key.encode("utf-8"))
        digest.update(np.asarray(seed_vectors[key], dtype=np.float32).tobytes())
    return digest.hexdigest()


def eligible_drama_segments(
    segments: list[Segment], retrieval: dict[str, Any]
) -> list[Segment]:
    return sorted(
        (
            row
            for row in segments
            if row.source_group == "owned_drama" and eligible(row, retrieval)
        ),
        key=lambda row: (row.source_key, row.start_seconds, row.end_seconds),
    )


def score_cache_path(evidence_root: Path, segment: Segment) -> Path:
    return (
        evidence_root
        / "private/retrieval/scores"
        / segment.source_key
        / f"{segment.candidate_id}.json"
    )


def score_drama_segments(
    *,
    candidates: list[Segment],
    seed_vectors: dict[str, np.ndarray],
    evidence_root: Path,
    model: Any,
) -> list[dict[str, Any]]:
    clip_root = evidence_root / "private/retrieval/drama-segments"
    current_seed_fingerprint = seed_fingerprint(seed_vectors)
    rows: list[dict[str, Any]] = []
    for index, segment in enumerate(candidates):
        cache_path = score_cache_path(evidence_root, segment)
        if cache_path.is_file():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if (
                cached.get("candidate_id") == segment.candidate_id
                and cached.get("seed_fingerprint") == current_seed_fingerprint
                and Path(str(cached.get("clip_path") or "")).is_file()
            ):
                rows.append(cached)
                continue
        path = clip_root / segment.source_key / f"{segment.candidate_id}.wav"
        extract_clip(
            segment.source_path,
            path,
            segment.start_seconds,
            segment.end_seconds,
        )
        vector = embedding(model, path)
        metrics = acoustic_metrics(path)
        chris = cosine(seed_vectors["travis_oliver"], vector)
        roz = cosine(seed_vectors["yasmin_bannerman"], vector)
        penalty = quality_penalty(metrics, segment.duration_seconds)
        row = {
            "schema_version": 1,
            "candidate_id": segment.candidate_id,
            "seed_fingerprint": current_seed_fingerprint,
            "source_key": segment.source_key,
            "source_path": str(segment.source_path),
            "start_seconds": round(segment.start_seconds, 3),
            "end_seconds": round(segment.end_seconds, 3),
            "duration_seconds": round(segment.duration_seconds, 3),
            "text_asr_unverified": segment.text,
            "transcript_status": "asr_unverified",
            "transcript_path": str(segment.transcript_path),
            "segment_index": segment.segment_index,
            "clip_path": str(path),
            "clip_sha256": sha256_file(path),
            "cosine_chris": round(chris, 6),
            "cosine_roz": round(roz, 6),
            "identity_margin_chris": round(chris - roz, 6),
            "identity_margin_roz": round(roz - chris, 6),
            "quality_penalty": round(penalty, 6),
            "rank_score_chris": round(chris + 0.15 * (chris - roz) - penalty, 6),
            "rank_score_roz": round(roz + 0.15 * (roz - chris) - penalty, 6),
            "metrics": metrics,
            "scored_at": utc_now(),
        }
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(row, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        rows.append(row)
        if (index + 1) % 50 == 0:
            print(f"Encoded {index + 1}/{len(candidates)} drama segments", flush=True)
    return rows


def load_all_scores(
    *,
    candidates: list[Segment],
    seed_vectors: dict[str, np.ndarray],
    evidence_root: Path,
) -> tuple[list[dict[str, Any]], list[str]]:
    current_seed_fingerprint = seed_fingerprint(seed_vectors)
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for segment in candidates:
        path = score_cache_path(evidence_root, segment)
        if not path.is_file():
            missing.append(segment.candidate_id)
            continue
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("seed_fingerprint") != current_seed_fingerprint:
            missing.append(segment.candidate_id)
            continue
        rows.append(row)
    return rows, missing


def copy_top_candidates(
    *,
    rows: list[dict[str, Any]],
    evidence_root: Path,
    top_n: int,
) -> dict[str, list[dict[str, Any]]]:
    public_root = evidence_root / "source-review/audio"
    result: dict[str, list[dict[str, Any]]] = {}
    for character, score_key in (
        ("chris_cwej", "rank_score_chris"),
        ("roz_forrester", "rank_score_roz"),
    ):
        ranked = sorted(rows, key=lambda item: item[score_key], reverse=True)[:top_n]
        copied: list[dict[str, Any]] = []
        for rank, row in enumerate(ranked, start=1):
            source = Path(row["clip_path"])
            target = public_root / character / f"{rank:03d}-{row['candidate_id']}.wav"
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_file() or sha256_file(target) != row["clip_sha256"]:
                target.write_bytes(source.read_bytes())
            copied.append(
                {
                    **row,
                    "rank": rank,
                    "review_audio": str(target.relative_to(evidence_root / "source-review")),
                }
            )
        result[character] = copied
    return result


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "candidate_id",
        "source_key",
        "start_seconds",
        "end_seconds",
        "duration_seconds",
        "text_asr_unverified",
        "cosine_chris",
        "cosine_roz",
        "identity_margin_chris",
        "identity_margin_roz",
        "quality_penalty",
        "rank_score_chris",
        "rank_score_roz",
        "clip_path",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_review(review_root: Path, ranked: dict[str, list[dict[str, Any]]]) -> None:
    review_root.mkdir(parents=True, exist_ok=True)
    sections = []
    labels = {"chris_cwej": "Chris Cwej", "roz_forrester": "Roz Forrester"}
    for character, rows in ranked.items():
        cards = []
        for row in rows:
            score = row["rank_score_chris"] if character == "chris_cwej" else row["rank_score_roz"]
            cards.append(
                f"""
                <article class="candidate">
                  <header><strong>#{row['rank']} · {html.escape(row['source_key'])}</strong><span>{score:.3f}</span></header>
                  <audio controls preload="none" src="{html.escape(row['review_audio'])}"></audio>
                  <p>{html.escape(row['text_asr_unverified'])}</p>
                  <dl>
                    <div><dt>Time</dt><dd>{row['start_seconds']:.3f}–{row['end_seconds']:.3f}</dd></div>
                    <div><dt>Identity</dt><dd>Chris {row['cosine_chris']:.3f} · Roz {row['cosine_roz']:.3f}</dd></div>
                    <div><dt>RMS</dt><dd>{row['metrics']['rms_dbfs']:.1f} dBFS</dd></div>
                    <div><dt>Silence</dt><dd>{row['metrics']['silence_fraction']:.1%}</dd></div>
                  </dl>
                  <label>Disposition <select><option>Unreviewed</option><option>Keep identity</option><option>Keep expression</option><option>Reject overlap</option><option>Reject music/SFX</option><option>Reject wrong speaker</option><option>Reject transcript</option></select></label>
                  <label>Exact transcript <textarea rows="2">{html.escape(row['text_asr_unverified'])}</textarea></label>
                  <label>Notes <textarea rows="2"></textarea></label>
                </article>
                """
            )
        sections.append(
            f"<section><h2>{labels[character]}</h2><div class='grid'>{''.join(cards)}</div></section>"
        )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cwej / Roz source review</title>
<style>
body{{font-family:system-ui,sans-serif;margin:0;background:#f4f0e8;color:#211f1b}}main{{max-width:1500px;margin:auto;padding:24px}}h1{{margin-bottom:4px}}.notice{{max-width:80ch;color:#5c564d}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:14px}}.candidate{{background:#fffdf7;border:1px solid #cfc7b8;padding:14px}}header{{display:flex;justify-content:space-between;gap:12px}}audio{{width:100%;margin:10px 0}}p{{min-height:3.5em}}dl{{display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:.88rem}}dl div{{display:flex;justify-content:space-between;gap:8px}}dt{{color:#6a6358}}dd{{margin:0}}label{{display:block;margin-top:10px;font-size:.85rem}}select,textarea{{width:100%;box-sizing:border-box;margin-top:4px;font:inherit}}textarea{{resize:vertical}}section{{margin-top:34px}}
</style></head><body><main><h1>Chris Cwej / Roz Forrester source review</h1><p class="notice">Rankings are machine retrieval, not acceptance. Confirm the speaker, reject overlap/music/effects, and correct the transcript before any clip becomes a cloning reference.</p>{''.join(sections)}</main></body></html>"""
    (review_root / "index.html").write_text(document, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE_ROOT)
    parser.add_argument("--top", type=int)
    parser.add_argument("--encoder", choices=["ecapa", "qwen"], default="ecapa")
    parser.add_argument("--batch-index", type=int)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--aggregate", action="store_true")
    parser.add_argument("--list-candidates", action="store_true")
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    evidence_root = args.evidence_root.resolve()
    manifest_path = evidence_root / "source-manifest.json"
    if not manifest_path.is_file():
        raise RetrievalError(f"Source manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    owned_paths, public_paths = source_maps(manifest)
    segments = load_transcript_segments(evidence_root, owned_paths, public_paths)
    if not any(row.source_group == "owned_drama" for row in segments):
        raise RetrievalError("No owned-drama transcript segments are available.")
    candidates = eligible_drama_segments(segments, config["retrieval"])
    if args.list_candidates:
        print(
            json.dumps(
                {
                    "eligible_drama_segments": len(candidates),
                    "batch_size": args.batch_size,
                    "batch_count": math.ceil(len(candidates) / args.batch_size),
                    "sources": sorted({row.source_key for row in candidates}),
                },
                indent=2,
            )
        )
        return 0

    encoder = (
        EcapaSpeakerEncoder(evidence_root)
        if args.encoder == "ecapa"
        else QwenSpeakerEncoder()
    )
    seed_vectors, seed_receipts = make_seed_embeddings(
        config=config,
        public_paths=public_paths,
        evidence_root=evidence_root,
        model=encoder,
    )
    expanded_travis, demo_rankings = rank_demo_segments(
        segments=segments,
        anchor=seed_vectors["travis_oliver"],
        evidence_root=evidence_root,
        model=encoder,
        retrieval=config["retrieval"],
    )
    seed_vectors["travis_oliver"] = expanded_travis

    if args.batch_index is not None:
        start = args.batch_index * args.batch_size
        selected = candidates[start : start + args.batch_size]
        if not selected:
            raise RetrievalError(f"Batch index is out of range: {args.batch_index}")
        rows = score_drama_segments(
            candidates=selected,
            seed_vectors=seed_vectors,
            evidence_root=evidence_root,
            model=encoder,
        )
        print(
            json.dumps(
                {
                    "batch_index": args.batch_index,
                    "batch_size": args.batch_size,
                    "selected_count": len(selected),
                    "scored_count": len(rows),
                    "first_candidate": selected[0].candidate_id,
                    "last_candidate": selected[-1].candidate_id,
                },
                indent=2,
            )
        )
        return 0

    rows, missing = load_all_scores(
        candidates=candidates,
        seed_vectors=seed_vectors,
        evidence_root=evidence_root,
    )
    if not args.aggregate:
        print(
            json.dumps(
                {
                    "eligible_drama_segments": len(candidates),
                    "scored_segments": len(rows),
                    "pending_segments": len(missing),
                    "batch_size": args.batch_size,
                    "batch_count": math.ceil(len(candidates) / args.batch_size),
                },
                indent=2,
            )
        )
        return 0
    if missing:
        raise RetrievalError(
            f"Cannot aggregate while {len(missing)} segment scores are missing."
        )

    top_n = args.top or int(config["retrieval"]["top_candidates_per_character"])
    ranked = copy_top_candidates(
        rows=rows,
        evidence_root=evidence_root,
        top_n=top_n,
    )

    output = {
        "schema_version": 1,
        "round_id": config["round_id"],
        "generated_at": utc_now(),
        "speaker_encoder": {
            "name": encoder.name,
            "revision": encoder.revision,
        },
        "seed_receipts": seed_receipts,
        "travis_demo_rankings": demo_rankings,
        "eligible_drama_segments": len(rows),
        "ranked_candidates": ranked,
        "limitations": [
            "ASR transcripts are provisional and must be corrected by listening.",
            "Speaker similarity does not detect music, sound effects, or overlapping actors reliably.",
            "The Travis identity seed begins from one known Gridlock/Milo line and is expanded only by high cosine matches within his reel.",
            "No candidate is approved for cloning by this automated pass.",
        ],
    }
    output_path = evidence_root / "candidate-rankings.json"
    output_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    write_csv(evidence_root / "candidate-rankings.csv", rows)
    write_review(evidence_root / "source-review", ranked)
    print(
        json.dumps(
            {
                "eligible_drama_segments": len(rows),
                "chris_candidates": len(ranked["chris_cwej"]),
                "roz_candidates": len(ranked["roz_forrester"]),
                "review": str(evidence_root / "source-review/index.html"),
                "rankings": str(output_path),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
