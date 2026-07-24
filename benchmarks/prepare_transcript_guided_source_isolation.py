#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROUND_ID = "alexandria_transcript_guided_source_isolation_v1"

SOURCES: dict[str, dict[str, Any]] = {
    "criminal_code": {
        "title": "Bernice Summerfield and the Criminal Code",
        "target": "benny",
        "path": "/Users/tristan/Library/Caches/CloudKit/com.apple.bird/e2eed64c87cf8a2473b56deec49a2fb7ff87f9a5/MMCS/ClonedFiles/documentContent__25FB57D2-BD90-4B51-813A-EE42908B4F90_fileContent",
        "seeds": [
            [2244.93, 2250.87],
            [4153.77, 4158.03],
            [2286.18, 2291.88],
            [929.91, 932.85],
            [3445.65, 3453.24],
            [60.87, 63.84],
        ],
    },
    "hesitation_deviation": {
        "title": "The Hesitation Deviation",
        "target": "benny",
        "path": "/Users/tristan/Library/Caches/CloudKit/com.apple.bird/e2eed64c87cf8a2473b56deec49a2fb7ff87f9a5/MMCS/ClonedFiles/documentContent__46C4050A-2EF6-4A1D-B080-2A27964B86B6_fileContent",
        "seeds": [
            [537.90, 542.10],
            [1203.24, 1209.39],
            [1713.48, 1720.47],
            [1287.12, 1291.62],
            [1282.53, 1287.45],
            [1293.48, 1298.97],
        ],
    },
    "all_consuming_fire": {
        "title": "All-Consuming Fire",
        "target": "doctor",
        "path": "/Users/tristan/Library/Caches/CloudKit/com.apple.bird/e2eed64c87cf8a2473b56deec49a2fb7ff87f9a5/MMCS/ClonedFiles/documentContent__F55DF292-71E4-49EF-99D7-ECB84EF76485_fileContent",
        "seeds": [
            [2153.01, 2159.25],
            [1483.83, 1490.37],
            [3657.99, 3664.86],
            [5121.51, 5125.05],
            [3467.13, 3469.71],
            [1924.05, 1934.70],
        ],
    },
}


class TranscriptIsolationError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def extract_window(source: Path, output: Path, start: float, duration: float) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output),
        ],
        check=True,
    )


def transcribe(args: argparse.Namespace) -> dict[str, Any]:
    import mlx_whisper

    model = Path(args.whisper_model).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    if not model.is_dir():
        raise TranscriptIsolationError(f"Whisper model is missing: {model}")
    output_root.mkdir(parents=True, exist_ok=True)

    contexts: list[dict[str, Any]] = []
    for source_key, spec in SOURCES.items():
        source = Path(spec["path"]).expanduser().resolve()
        if not source.is_file():
            raise TranscriptIsolationError(f"Source audio is missing: {source}")
        for ordinal, (seed_start, seed_end) in enumerate(spec["seeds"], start=1):
            context_start = max(0.0, seed_start - args.before_seconds)
            context_end = seed_end + args.after_seconds
            context_duration = context_end - context_start
            with tempfile.TemporaryDirectory(prefix="alexandria-transcript-context-") as temporary:
                window = Path(temporary) / "context.wav"
                extract_window(source, window, context_start, context_duration)
                result = mlx_whisper.transcribe(
                    str(window),
                    path_or_hf_repo=str(model),
                    language="en",
                    word_timestamps=True,
                    condition_on_previous_text=False,
                    verbose=False,
                )
            segments = []
            words = []
            for segment in result.get("segments", []):
                absolute_start = context_start + float(segment.get("start", 0.0))
                absolute_end = context_start + float(segment.get("end", 0.0))
                segment_words = []
                for word in segment.get("words", []):
                    item = {
                        "text": str(word.get("word") or "").strip(),
                        "start_seconds": round(context_start + float(word.get("start", 0.0)), 3),
                        "end_seconds": round(context_start + float(word.get("end", 0.0)), 3),
                        "probability": round(float(word.get("probability", 0.0)), 6),
                    }
                    segment_words.append(item)
                    words.append(item)
                segments.append(
                    {
                        "start_seconds": round(absolute_start, 3),
                        "end_seconds": round(absolute_end, 3),
                        "text": str(segment.get("text") or "").strip(),
                        "words": segment_words,
                    }
                )
            contexts.append(
                {
                    "context_id": f"{source_key}_{ordinal:02d}",
                    "source_key": source_key,
                    "source_title": spec["title"],
                    "target": spec["target"],
                    "source_path": str(source),
                    "seed_start_seconds": seed_start,
                    "seed_end_seconds": seed_end,
                    "context_start_seconds": round(context_start, 3),
                    "context_end_seconds": round(context_end, 3),
                    "transcript": str(result.get("text") or "").strip(),
                    "segments": segments,
                    "words": words,
                    "selection_status": "requires_transcript_guided_decision",
                }
            )

    payload = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "created_at": now_iso(),
        "selection_policy": {
            "speaker_embedding_role": "coarse_locator_only",
            "transcript_required_before_inclusion": True,
            "complete_utterance_required": True,
            "scene_continuity_required": True,
            "mixed_speaker_windows_rejected": True,
            "emotion_assigned_from_transcript_context_and_delivery": True,
        },
        "context_count": len(contexts),
        "contexts": contexts,
    }
    output = output_root / "context-transcripts.json"
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"context_count": len(contexts), "output": str(output)}


def validate(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.contexts).expanduser().resolve()
    if not path.is_file():
        raise TranscriptIsolationError(f"Context transcript file is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    contexts = payload.get("contexts")
    if not isinstance(contexts, list) or not contexts:
        raise TranscriptIsolationError("Context transcript file has no contexts.")
    failures = []
    for row in contexts:
        if not row.get("transcript"):
            failures.append(f"{row.get('context_id')}: missing transcript")
        if not row.get("words"):
            failures.append(f"{row.get('context_id')}: missing word timestamps")
        if row.get("selection_status") != "requires_transcript_guided_decision":
            failures.append(f"{row.get('context_id')}: invalid selection status")
    policy = payload.get("selection_policy") or {}
    if policy.get("speaker_embedding_role") != "coarse_locator_only":
        failures.append("speaker embedding is not constrained to coarse locator role")
    if policy.get("transcript_required_before_inclusion") is not True:
        failures.append("transcript-first rule is missing")
    if failures:
        raise TranscriptIsolationError("; ".join(failures))
    return {"context_count": len(contexts), "failure_count": 0}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Transcribe source context before selecting expressive reference clips.")
    sub = parser.add_subparsers(dest="command", required=True)
    transcribe_parser = sub.add_parser("transcribe")
    transcribe_parser.add_argument("--whisper-model", required=True)
    transcribe_parser.add_argument("--output-root", required=True)
    transcribe_parser.add_argument("--before-seconds", type=float, default=18.0)
    transcribe_parser.add_argument("--after-seconds", type=float, default=18.0)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--contexts", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        result = transcribe(args) if args.command == "transcribe" else validate(args)
    except (TranscriptIsolationError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
