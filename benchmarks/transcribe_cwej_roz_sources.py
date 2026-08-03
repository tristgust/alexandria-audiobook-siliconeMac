#!/usr/bin/env python3
"""Transcribe Cwej/Roz source audio in resumable bounded chunks."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE_ROOT = ROOT / ".omo/evidence/cwej-roz-voice-evaluation"
DEFAULT_PYTHON_ROOT = Path(
    "/Users/tristan/pinokio/api/alexandria-audiobook.git/app/env"
)
DEFAULT_MLX_WHISPER = DEFAULT_PYTHON_ROOT / "bin/mlx_whisper"


class TranscriptionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Job:
    source_group: str
    source_key: str
    source_path: Path
    source_sha256: str
    start_seconds: float
    end_seconds: float
    job_id: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(evidence_root: Path) -> dict[str, Any]:
    path = evidence_root / "source-manifest.json"
    if not path.is_file():
        raise TranscriptionError(f"Source manifest is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise TranscriptionError("Unsupported source manifest schema.")
    return payload


def source_rows(manifest: dict[str, Any]) -> list[tuple[str, str, Path, str, float]]:
    rows: list[tuple[str, str, Path, str, float]] = []
    for row in manifest["owned_dramas"]:
        rows.append(
            (
                "owned_drama",
                row["key"],
                Path(row["path"]),
                row["sha256"],
                float(row["duration_seconds"]),
            )
        )
    for row in manifest["public_references"]:
        prepared = row["prepared_audio"]
        rows.append(
            (
                "public_reference",
                row["key"],
                Path(row["prepared_path"]),
                row["prepared_sha256"],
                float(prepared["duration_seconds"]),
            )
        )
    return rows


def build_jobs(manifest: dict[str, Any], chunk_seconds: int) -> list[Job]:
    jobs: list[Job] = []
    for group, key, path, source_sha256, duration in source_rows(manifest):
        count = max(1, math.ceil(duration / chunk_seconds))
        for index in range(count):
            start = float(index * chunk_seconds)
            end = min(duration, float((index + 1) * chunk_seconds))
            jobs.append(
                Job(
                    source_group=group,
                    source_key=key,
                    source_path=path,
                    source_sha256=source_sha256,
                    start_seconds=start,
                    end_seconds=end,
                    job_id=f"{group}-{key}-{index:03d}",
                )
            )
    return jobs


def output_path(evidence_root: Path, job: Job) -> Path:
    return evidence_root / "private/transcripts/chunks" / f"{job.job_id}.json"


def receipt_path(evidence_root: Path, job: Job) -> Path:
    return evidence_root / "private/transcripts/receipts" / f"{job.job_id}.json"


def valid_existing(evidence_root: Path, job: Job, model: str) -> bool:
    transcript = output_path(evidence_root, job)
    receipt = receipt_path(evidence_root, job)
    if not transcript.is_file() or not receipt.is_file():
        return False
    try:
        row = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        row.get("source_sha256") == job.source_sha256
        and row.get("model") == model
        and row.get("start_seconds") == job.start_seconds
        and row.get("end_seconds") == job.end_seconds
        and row.get("transcript_sha256") == sha256_file(transcript)
    )


def transcribe_job(
    *,
    job: Job,
    evidence_root: Path,
    mlx_whisper: Path,
    model: str,
    language: str,
) -> dict[str, Any]:
    transcript_root = evidence_root / "private/transcripts/chunks"
    receipt_root = evidence_root / "private/transcripts/receipts"
    log_root = evidence_root / "private/transcripts/logs"
    transcript_root.mkdir(parents=True, exist_ok=True)
    receipt_root.mkdir(parents=True, exist_ok=True)
    log_root.mkdir(parents=True, exist_ok=True)
    if valid_existing(evidence_root, job, model):
        return {"job_id": job.job_id, "status": "reused"}

    command = [
        str(mlx_whisper),
        str(job.source_path),
        "--model",
        model,
        "--language",
        language,
        "--clip-timestamps",
        f"{job.start_seconds},{job.end_seconds}",
        "--word-timestamps",
        "True",
        "--condition-on-previous-text",
        "False",
        "--output-format",
        "json",
        "--output-name",
        job.job_id,
        "--output-dir",
        str(transcript_root),
        "--verbose",
        "False",
    ]
    completed = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    log_path = log_root / f"{job.job_id}.log"
    log_path.write_text(completed.stdout or "", encoding="utf-8")
    transcript = output_path(evidence_root, job)
    if completed.returncode != 0 or not transcript.is_file():
        raise TranscriptionError(
            f"Transcription failed for {job.job_id}; see {log_path}"
        )
    payload = json.loads(transcript.read_text(encoding="utf-8"))
    segments = payload.get("segments")
    if not isinstance(segments, list):
        raise TranscriptionError(f"Transcript has no segments: {transcript}")
    receipt = {
        "schema_version": 1,
        "job_id": job.job_id,
        "source_group": job.source_group,
        "source_key": job.source_key,
        "source_path": str(job.source_path),
        "source_sha256": job.source_sha256,
        "start_seconds": job.start_seconds,
        "end_seconds": job.end_seconds,
        "model": model,
        "language": language,
        "word_timestamps": True,
        "condition_on_previous_text": False,
        "segment_count": len(segments),
        "text_character_count": len(str(payload.get("text") or "")),
        "transcript_path": str(transcript),
        "transcript_sha256": sha256_file(transcript),
        "completed_at": utc_now(),
    }
    receipt_path(evidence_root, job).write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {"job_id": job.job_id, "status": "generated", **receipt}


def merge_transcripts(
    *, evidence_root: Path, manifest: dict[str, Any], jobs: list[Job], model: str
) -> dict[str, Any]:
    by_source: dict[tuple[str, str], list[Job]] = {}
    for job in jobs:
        by_source.setdefault((job.source_group, job.source_key), []).append(job)
    merged_root = evidence_root / "private/transcripts/merged"
    merged_root.mkdir(parents=True, exist_ok=True)
    rows = []
    for (group, key), source_jobs in sorted(by_source.items()):
        source_jobs.sort(key=lambda row: row.start_seconds)
        segments: list[dict[str, Any]] = []
        text_parts: list[str] = []
        for job in source_jobs:
            path = output_path(evidence_root, job)
            if not path.is_file():
                raise TranscriptionError(f"Cannot merge missing chunk: {path}")
            payload = json.loads(path.read_text(encoding="utf-8"))
            text_parts.append(str(payload.get("text") or "").strip())
            for segment in payload.get("segments") or []:
                segment = dict(segment)
                segment["source_job_id"] = job.job_id
                segments.append(segment)
        merged = {
            "schema_version": 1,
            "source_group": group,
            "source_key": key,
            "model": model,
            "text": " ".join(part for part in text_parts if part),
            "segments": segments,
            "chunk_count": len(source_jobs),
            "merged_at": utc_now(),
        }
        path = merged_root / f"{group}-{key}.json"
        path.write_text(
            json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        rows.append(
            {
                "source_group": group,
                "source_key": key,
                "path": str(path),
                "sha256": sha256_file(path),
                "segment_count": len(segments),
            }
        )
    summary = {
        "schema_version": 1,
        "round_id": manifest["round_id"],
        "model": model,
        "sources": rows,
        "merged_at": utc_now(),
    }
    summary_path = evidence_root / "transcription-summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE_ROOT)
    parser.add_argument("--mlx-whisper", type=Path, default=DEFAULT_MLX_WHISPER)
    parser.add_argument("--model", default="mlx-community/whisper-large-v3-turbo")
    parser.add_argument("--language", default="en")
    parser.add_argument("--chunk-seconds", type=int, default=600)
    parser.add_argument("--batch-index", type=int)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--source-group", choices=["owned_drama", "public_reference"])
    parser.add_argument("--merge", action="store_true")
    parser.add_argument("--list-jobs", action="store_true")
    args = parser.parse_args()

    evidence_root = args.evidence_root.resolve()
    manifest = load_manifest(evidence_root)
    jobs = build_jobs(manifest, args.chunk_seconds)
    if args.source_group:
        jobs = [job for job in jobs if job.source_group == args.source_group]

    if args.list_jobs:
        print(
            json.dumps(
                {
                    "job_count": len(jobs),
                    "batch_count": math.ceil(len(jobs) / args.batch_size),
                    "jobs": [job.job_id for job in jobs],
                },
                indent=2,
            )
        )
        return 0

    if args.merge:
        summary = merge_transcripts(
            evidence_root=evidence_root,
            manifest=manifest,
            jobs=jobs,
            model=args.model,
        )
        print(json.dumps(summary, indent=2))
        return 0

    if args.batch_index is None:
        pending = [job for job in jobs if not valid_existing(evidence_root, job, args.model)]
        print(
            json.dumps(
                {
                    "job_count": len(jobs),
                    "pending_count": len(pending),
                    "batch_count": math.ceil(len(jobs) / args.batch_size),
                },
                indent=2,
            )
        )
        return 0

    start = args.batch_index * args.batch_size
    selected = jobs[start : start + args.batch_size]
    if not selected:
        raise TranscriptionError(f"Batch index is out of range: {args.batch_index}")
    results = []
    with ThreadPoolExecutor(max_workers=min(args.workers, len(selected))) as executor:
        futures = {
            executor.submit(
                transcribe_job,
                job=job,
                evidence_root=evidence_root,
                mlx_whisper=args.mlx_whisper,
                model=args.model,
                language=args.language,
            ): job
            for job in selected
        }
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda row: row["job_id"])
    print(json.dumps({"batch_index": args.batch_index, "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
