#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

BENCHMARKS_ROOT = Path(__file__).resolve().parent
if str(BENCHMARKS_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS_ROOT))

from build_transcript_guided_source_bank import TranscriptBankError, sha256_file

ROUND_ID = "alexandria_three_voice_historical_provenance_review_v1"
SOURCE_ROUND_ID = "alexandria_transcript_guided_source_isolation_v1"
CONTEXT_ROUND_ID = "alexandria_transcript_guided_source_isolation_v1"
KNOWN_WRONG_SPEAKER_CLIP_ID = "doctor_acf_emergency_command"
EXPECTED_COUNTS = {"benny": 10, "doctor": 4}
ASSET_ROOT = Path(__file__).with_name("three_voice_historical_provenance_assets")
RANGE_SERVER = Path(__file__).with_name("range_http_server.py")


class ProvenanceReviewError(RuntimeError):
    pass


def load_json(path: Path) -> Any:
    if not path.is_file():
        raise ProvenanceReviewError(f"JSON file is missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProvenanceReviewError(f"Invalid JSON in {path}: {exc}") from exc


def require_round(payload: Any, expected: str, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ProvenanceReviewError(f"{label} must be a JSON object.")
    actual = payload.get("round_id")
    if actual != expected:
        raise ProvenanceReviewError(
            f"Unexpected {label} round_id: {actual!r}; expected {expected!r}."
        )
    return payload


def encode_mp3(source: Path, output: Path) -> None:
    if not source.is_file():
        raise ProvenanceReviewError(f"Audio source is missing: {source}")
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(output),
        ],
        check=True,
    )


def encode_context_mp3(source: Path, output: Path, start: float, end: float) -> None:
    if not source.is_file():
        raise ProvenanceReviewError(f"Context source is missing: {source}")
    if end <= start:
        raise ProvenanceReviewError(f"Invalid context range {start}–{end}: {source}")
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(source),
            "-t",
            f"{end - start:.3f}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(output),
        ],
        check=True,
    )


def audio_probe(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,sample_rate,channels:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams") or []
    if not streams:
        raise ProvenanceReviewError(f"No audio stream found: {path}")
    stream = streams[0]
    return {
        "codec_name": stream.get("codec_name"),
        "sample_rate": int(stream.get("sample_rate") or 0),
        "channels": int(stream.get("channels") or 0),
        "duration_seconds": float((payload.get("format") or {}).get("duration") or 0.0),
    }


def choose_context(row: dict[str, Any], contexts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    transcript_start = float(row["transcript_start_seconds"])
    transcript_end = float(row["transcript_end_seconds"])
    candidates = []
    for context_id in row["context_ids"]:
        context = contexts.get(context_id)
        if context is None:
            raise ProvenanceReviewError(f"Context is missing: {context_id}")
        if (
            float(context["context_start_seconds"]) <= transcript_start
            and float(context["context_end_seconds"]) >= transcript_end
        ):
            candidates.append(context)
    if candidates:
        return min(
            candidates,
            key=lambda item: float(item["context_end_seconds"]) - float(item["context_start_seconds"]),
        )
    return contexts[row["context_ids"][0]]


def context_excerpt(context: dict[str, Any], start: float, end: float) -> str:
    excerpt_start = start - 8.0
    excerpt_end = end + 8.0
    segments = [
        str(segment.get("text") or "").strip()
        for segment in context.get("segments") or []
        if float(segment.get("end_seconds") or 0.0) >= excerpt_start
        and float(segment.get("start_seconds") or 0.0) <= excerpt_end
    ]
    excerpt = " ".join(text for text in segments if text)
    return excerpt or str(context.get("transcript") or "").strip()


def package(args: argparse.Namespace) -> dict[str, Any]:
    bank_path = Path(args.bank).expanduser().resolve()
    contexts_path = Path(args.contexts).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    identity_sources = {
        "benny": Path(args.benny_identity).expanduser().resolve(),
        "doctor": Path(args.doctor_identity).expanduser().resolve(),
    }

    bank = require_round(load_json(bank_path), SOURCE_ROUND_ID, "source bank")
    context_payload = require_round(load_json(contexts_path), CONTEXT_ROUND_ID, "context transcript")
    rows = bank.get("accepted_candidates")
    if not isinstance(rows, list) or len(rows) != 14:
        raise ProvenanceReviewError(
            f"Expected 14 historical candidates; found {len(rows) if isinstance(rows, list) else 'invalid'}."
        )
    counts = Counter(str(row.get("target")) for row in rows)
    if dict(sorted(counts.items())) != EXPECTED_COUNTS:
        raise ProvenanceReviewError(f"Unexpected target counts: {dict(counts)}")
    if any(row.get("user_correction_required_before_bank_approval") is not True for row in rows):
        raise ProvenanceReviewError("Every historical candidate must still require human validation.")

    contexts = {
        str(row["context_id"]): row
        for row in context_payload.get("contexts") or []
    }
    if not contexts:
        raise ProvenanceReviewError("Context transcript rows are missing.")

    review_root = output_root / "review"
    if review_root.exists():
        shutil.rmtree(review_root)
    for directory in ("identity", "candidate", "context"):
        (review_root / "audio" / directory).mkdir(parents=True, exist_ok=True)
    for asset in ("index.html", "styles.css", "app.js"):
        shutil.copy2(ASSET_ROOT / asset, review_root / asset)
    shutil.copy2(RANGE_SERVER, review_root / "serve_review.py")

    identity_urls: dict[str, str] = {}
    for target, source in identity_sources.items():
        destination = review_root / "audio" / "identity" / f"{target}.mp3"
        encode_mp3(source, destination)
        identity_urls[target] = f"audio/identity/{target}.mp3"

    public_rows: list[dict[str, Any]] = []
    answer_rows: list[dict[str, Any]] = []
    warning_count = 0
    for ordinal, row in enumerate(rows, start=1):
        clip_id = str(row["clip_id"])
        target = str(row["target"])
        candidate_source = Path(str(row["audio_path"])).expanduser().resolve()
        if sha256_file(candidate_source) != row.get("audio_sha256"):
            raise ProvenanceReviewError(f"Candidate hash mismatch: {clip_id}")
        candidate_destination = review_root / "audio" / "candidate" / f"{clip_id}.mp3"
        encode_mp3(candidate_source, candidate_destination)

        context = choose_context(row, contexts)
        source_path = Path(str(context["source_path"])).expanduser().resolve()
        expected_source_hash = context.get("source_sha256") or row.get("source_sha256")
        if expected_source_hash and sha256_file(source_path) != expected_source_hash:
            raise ProvenanceReviewError(f"Source hash mismatch: {clip_id}")
        selected_start = float(row["audio_start_seconds"])
        selected_end = float(row["audio_end_seconds"])
        context_start = max(float(context["context_start_seconds"]), selected_start - 8.0)
        context_end = min(float(context["context_end_seconds"]), selected_end + 8.0)
        context_destination = review_root / "audio" / "context" / f"{clip_id}.mp3"
        encode_context_mp3(source_path, context_destination, context_start, context_end)

        warning_only = clip_id == KNOWN_WRONG_SPEAKER_CLIP_ID
        if warning_only:
            warning_count += 1
        public_rows.append(
            {
                "clip_id": clip_id,
                "ordinal": ordinal,
                "target": target,
                "target_label": row.get("target_label"),
                "source_title": context.get("source_title"),
                "selected_transcript": row.get("transcript"),
                "context_transcript": context_excerpt(context, selected_start, selected_end),
                "selection_reason": row.get("selection_reason"),
                "assistant_speaker_role": row.get("speaker_role"),
                "assistant_primary_emotion": row.get("primary_emotion"),
                "assistant_secondary_emotion": row.get("secondary_emotion"),
                "assistant_dramatic_function": row.get("dramatic_function"),
                "assistant_intensity_1_to_5": row.get("intensity_1_to_5"),
                "selected_start_seconds": selected_start,
                "selected_end_seconds": selected_end,
                "context_start_seconds": context_start,
                "context_end_seconds": context_end,
                "identity_audio": identity_urls[target],
                "candidate_audio": f"audio/candidate/{clip_id}.mp3",
                "context_audio": f"audio/context/{clip_id}.mp3",
                "warning_only": warning_only,
                "warning_reason": (
                    "Already rejected from later human evidence: this performance is not the Seventh Doctor. "
                    "It is retained here only to document the provenance failure."
                    if warning_only
                    else None
                ),
            }
        )
        answer_rows.append(
            {
                **row,
                "context_id_used": context["context_id"],
                "context_start_seconds": context_start,
                "context_end_seconds": context_end,
                "context_audio_sha256": sha256_file(context_destination),
                "candidate_review_audio_sha256": sha256_file(candidate_destination),
                "warning_only": warning_only,
                "known_disposition": "rejected_wrong_speaker" if warning_only else None,
            }
        )

    public = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "candidate_count": len(public_rows),
        "actionable_count": len(public_rows) - warning_count,
        "warning_count": warning_count,
        "target_counts": dict(sorted(counts.items())),
        "rows": public_rows,
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }
    (review_root / "data.js").write_text(
        "window.THREE_VOICE_HISTORICAL_PROVENANCE_DATA = "
        + json.dumps(public, ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "candidate_count": len(public_rows),
        "actionable_count": len(public_rows) - warning_count,
        "warning_count": warning_count,
        "target_counts": dict(sorted(counts.items())),
        "maximum_simultaneous_audio_elements": 3,
        "lazy_audio_loading": True,
        "range_requests_required": True,
        "source_context_audio_included": True,
        "known_wrong_speaker_locked": True,
        "answer_key_outside_review_root": True,
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }
    (review_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    (output_root / "answer-key.json").write_text(
        json.dumps(answer_rows, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (output_root / "START_HERE.txt").write_text(
        "Three-Voice Historical Provenance Review\n"
        "========================================\n\n"
        f'cd "{review_root}"\n'
        "python3 serve_review.py --bind 127.0.0.1 --port 8792\n\n"
        "Then open http://127.0.0.1:8792/\n",
        encoding="utf-8",
    )
    return {
        "candidate_count": len(public_rows),
        "actionable_count": len(public_rows) - warning_count,
        "warning_count": warning_count,
        "review": str(review_root / "index.html"),
    }


def validate(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    review_root = output_root / "review"
    data_path = review_root / "data.js"
    prefix = "window.THREE_VOICE_HISTORICAL_PROVENANCE_DATA = "
    text = data_path.read_text(encoding="utf-8").strip()
    if not text.startswith(prefix):
        raise ProvenanceReviewError("Review data prefix is invalid.")
    data = json.loads(text[len(prefix) :].rstrip(";"))
    failures: list[str] = []
    rows = data.get("rows") or []
    if len(rows) != 14:
        failures.append(f"candidate_count:{len(rows)}")
    if Counter(str(row.get("target")) for row in rows) != Counter(EXPECTED_COUNTS):
        failures.append("target_counts")
    warning_rows = [row for row in rows if row.get("warning_only")]
    if [row.get("clip_id") for row in warning_rows] != [KNOWN_WRONG_SPEAKER_CLIP_ID]:
        failures.append("warning_row")
    for row in rows:
        for key in ("identity_audio", "candidate_audio", "context_audio"):
            path = review_root / str(row.get(key) or "")
            if not path.is_file():
                failures.append(f"missing:{row.get('clip_id')}:{key}")
                continue
            probe = audio_probe(path)
            if probe["codec_name"] != "mp3" or probe["channels"] != 1:
                failures.append(f"audio:{row.get('clip_id')}:{key}:{probe}")
        for key in (
            "selected_transcript",
            "context_transcript",
            "selection_reason",
            "assistant_speaker_role",
            "assistant_primary_emotion",
            "assistant_dramatic_function",
        ):
            if row.get(key) in (None, ""):
                failures.append(f"field:{row.get('clip_id')}:{key}")
    body = (review_root / "index.html").read_text(encoding="utf-8")
    if body.lower().count("<audio") != 3:
        failures.append("audio_element_count")
    if re.search(r"IndexTTS2|VoxCPM|Fish S2|Qwen", body, re.IGNORECASE):
        failures.append("model_name_leak")
    manifest = load_json(review_root / "manifest.json")
    if manifest.get("production_promotion_allowed") is not False:
        failures.append("production_promotion_allowed")
    if manifest.get("automatic_production_assignment") is not False:
        failures.append("automatic_production_assignment")
    if failures:
        raise ProvenanceReviewError(f"Review validation failed: {failures}")
    return {
        "candidate_count": len(rows),
        "actionable_count": data.get("actionable_count"),
        "warning_count": data.get("warning_count"),
        "failure_count": 0,
        "review": str(review_root / "index.html"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Package a strict human provenance review for the 14 quarantined Benny and Doctor clips."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    package_parser = sub.add_parser("package")
    package_parser.add_argument("--bank", required=True)
    package_parser.add_argument("--contexts", required=True)
    package_parser.add_argument("--benny-identity", required=True)
    package_parser.add_argument("--doctor-identity", required=True)
    package_parser.add_argument("--output-root", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--output-root", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "package":
            result = package(args)
        else:
            result = validate(args)
    except (
        ProvenanceReviewError,
        TranscriptBankError,
        subprocess.CalledProcessError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
