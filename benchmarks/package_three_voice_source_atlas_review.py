#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

ROUND_ID = "alexandria_three_voice_source_atlas_review_v1"
ASSET_ROOT = Path(__file__).with_name("three_voice_source_atlas_assets")
RANGE_SERVER = Path(__file__).with_name("range_http_server.py")
EXPECTED_COUNTS = {"narrator": 23, "benny": 10, "doctor": 12}


class ReviewPackageError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def encode_mp3(source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-i", str(source),
            "-ac", "1", "-ar", "48000", "-c:a", "libmp3lame", "-b:a", "192k", str(output),
        ],
        check=True,
    )


def audio_probe(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration:stream=codec_name,channels,sample_rate", "-of", "json", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    stream = payload["streams"][0]
    return {
        "codec_name": stream.get("codec_name"),
        "channels": int(stream.get("channels") or 0),
        "sample_rate": int(stream.get("sample_rate") or 0),
        "duration_seconds": round(float(payload.get("format", {}).get("duration") or 0.0), 3),
    }


def package(args: argparse.Namespace) -> dict[str, Any]:
    atlas_path = Path(args.atlas).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    if not atlas_path.is_file():
        raise ReviewPackageError(f"Atlas is missing: {atlas_path}")
    atlas = json.loads(atlas_path.read_text(encoding="utf-8"))
    rows = atlas.get("rows") or []
    counts = Counter(row.get("target") for row in rows)
    if dict(counts) != EXPECTED_COUNTS:
        raise ReviewPackageError(f"Unexpected target counts: {dict(counts)} != {EXPECTED_COUNTS}")
    if atlas.get("production_promotion_allowed") is not False:
        raise ReviewPackageError("The atlas must explicitly forbid production promotion.")

    references = {
        "narrator": Path(args.narrator_reference).expanduser().resolve(),
        "benny": Path(args.benny_reference).expanduser().resolve(),
        "doctor": Path(args.doctor_reference).expanduser().resolve(),
    }
    for target, source in references.items():
        if not source.is_file():
            raise ReviewPackageError(f"{target} reference is missing: {source}")

    review = output_root / "review"
    if review.exists():
        shutil.rmtree(review)
    (review / "audio" / "targets").mkdir(parents=True)
    (review / "audio" / "candidates").mkdir(parents=True)
    for name in ("index.html", "styles.css", "app.js"):
        source = ASSET_ROOT / name
        if not source.is_file():
            raise ReviewPackageError(f"Review asset is missing: {source}")
        shutil.copy2(source, review / name)
    shutil.copy2(RANGE_SERVER, review / "serve_review.py")

    target_urls: dict[str, str] = {}
    target_receipts: dict[str, Any] = {}
    for target, source in references.items():
        destination = review / "audio" / "targets" / f"{target}.mp3"
        encode_mp3(source, destination)
        target_urls[target] = f"audio/targets/{target}.mp3"
        target_receipts[target] = {
            "source": str(source),
            "source_sha256": sha256_file(source),
            "review_audio": str(destination),
            "review_audio_sha256": sha256_file(destination),
            "probe": audio_probe(destination),
        }

    public_rows = []
    answer_rows = []
    total_duration = 0.0
    coverage_by_target: dict[str, list[str]] = defaultdict(list)
    for ordinal, row in enumerate(rows, start=1):
        source = Path(row.get("audio_path") or "")
        if not source.is_file():
            raise ReviewPackageError(f"Candidate audio is missing: {row.get('clip_id')}: {source}")
        if sha256_file(source) != row.get("audio_sha256"):
            raise ReviewPackageError(f"Candidate source hash mismatch: {row.get('clip_id')}")
        destination = review / "audio" / "candidates" / f"{row['clip_id']}.mp3"
        encode_mp3(source, destination)
        probe = audio_probe(destination)
        total_duration += probe["duration_seconds"]
        coverage_by_target[row["target"]].append(row["coverage_gap"])
        public_rows.append(
            {
                "clip_id": row["clip_id"],
                "ordinal": ordinal,
                "target": row["target"],
                "target_label": row["target_label"],
                "source_title": row["source_title"],
                "source_kind": row["source_kind"],
                "youtube_id": row["youtube_id"],
                "transcript_start_seconds": row["selected_start_seconds"],
                "transcript_end_seconds": row["selected_end_seconds"],
                "selected_duration_seconds": row["selected_duration_seconds"],
                "selected_transcript": row["expected_text"],
                "context_transcript": row["context_transcript"],
                "verification_transcript": row["verification_transcript"],
                "verification_similarity": row["verification_similarity"],
                "selection_reason": row["selection_reason"],
                "source_scene": row["source_scene"],
                "coverage_gap": row["coverage_gap"],
                "speaker_certainty": row["speaker_certainty"],
                "source_role_warning": row["source_role_warning"],
                "assistant_speaker_role": row["speaker_role"],
                "assistant_primary_emotion": row["primary_emotion"],
                "assistant_secondary_emotion": row["secondary_emotion"],
                "assistant_dramatic_function": row["dramatic_function"],
                "assistant_intensity_1_to_5": row["intensity_1_to_5"],
                "target_audio": target_urls[row["target"]],
                "candidate_audio": f"audio/candidates/{row['clip_id']}.mp3",
            }
        )
        answer_rows.append(
            {
                **row,
                "review_audio": str(destination),
                "review_audio_sha256": sha256_file(destination),
                "review_audio_probe": probe,
            }
        )

    public = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "title": "Three-Voice Source Atlas",
        "candidate_count": len(public_rows),
        "target_counts": EXPECTED_COUNTS,
        "total_listening_seconds": round(total_duration, 3),
        "rows": public_rows,
    }
    (review / "data.js").write_text(
        "window.THREE_VOICE_SOURCE_ATLAS_DATA = " + json.dumps(public, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )

    manifest = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "candidate_count": len(public_rows),
        "target_counts": EXPECTED_COUNTS,
        "source_count": atlas.get("source_count"),
        "total_listening_seconds": round(total_duration, 3),
        "transcript_guided": True,
        "assistant_labels_prefilled": True,
        "one_click_approval": True,
        "target_and_status_filters": True,
        "keyboard_review_controls": True,
        "maximum_simultaneous_audio_elements": 2,
        "range_server_included": True,
        "answer_key_outside_review_root": True,
        "production_promotion_allowed": False,
    }
    (review / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    coverage_ledger = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "target_counts": EXPECTED_COUNTS,
        "coverage_families": {
            target: sorted(values) for target, values in sorted(coverage_by_target.items())
        },
        "prior_gap_basis": {
            "narrator": ["joy and rage reliability", "shame", "loneliness", "abandonment", "regret"],
            "benny": ["credible fear", "grief", "explosive anger", "soft intimacy"],
            "doctor": ["clean compassion", "ordinary identity", "urgency", "authority", "weariness"],
        },
        "approval_gate": {
            "speaker_role_must_be_confirmed": True,
            "boundary_must_be_confirmed": True,
            "dramatic_label_must_be_confirmed": True,
            "audio_cleanliness_must_be_confirmed": True,
            "automatic_production_promotion": False,
        },
    }
    (output_root / "coverage-ledger.json").write_text(
        json.dumps(coverage_ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_root / "answer-key.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "round_id": ROUND_ID,
                "target_references": target_receipts,
                "rows": answer_rows,
            },
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )
    (output_root / "START_HERE.txt").write_text(
        f'cd "{review}"\npython3 serve_review.py --bind 127.0.0.1 --port {args.port}\n\n'
        f'Then open http://127.0.0.1:{args.port}/\n',
        encoding="utf-8",
    )
    return {
        "review": str(review / "index.html"),
        "candidate_count": len(public_rows),
        "target_counts": EXPECTED_COUNTS,
        "total_listening_seconds": round(total_duration, 3),
    }


def validate(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    review = output_root / "review"
    required = [
        review / "index.html", review / "styles.css", review / "app.js", review / "data.js",
        review / "serve_review.py", review / "manifest.json", output_root / "answer-key.json",
        output_root / "coverage-ledger.json", output_root / "START_HERE.txt",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ReviewPackageError(f"Review files are missing: {missing}")

    prefix = "window.THREE_VOICE_SOURCE_ATLAS_DATA = "
    text = (review / "data.js").read_text(encoding="utf-8").strip()
    if not text.startswith(prefix):
        raise ReviewPackageError("Review data has an invalid JavaScript prefix.")
    data = json.loads(text[len(prefix):].rstrip(";"))
    rows = data.get("rows") or []
    counts = Counter(row.get("target") for row in rows)
    failures = []
    if len(rows) != 45:
        failures.append(f"candidate_count:{len(rows)}")
    if dict(counts) != EXPECTED_COUNTS:
        failures.append(f"target_counts:{dict(counts)}")
    seen = set()
    for row in rows:
        clip_id = row.get("clip_id")
        if clip_id in seen:
            failures.append(f"duplicate:{clip_id}")
        seen.add(clip_id)
        for key in ("target_audio", "candidate_audio"):
            path = review / row.get(key, "")
            if not path.is_file():
                failures.append(f"missing:{clip_id}:{key}")
                continue
            probe = audio_probe(path)
            if probe["codec_name"] != "mp3" or probe["channels"] != 1:
                failures.append(f"format:{clip_id}:{key}:{probe}")
        for key in (
            "selected_transcript", "selection_reason", "source_scene", "coverage_gap",
            "assistant_speaker_role", "assistant_primary_emotion", "assistant_secondary_emotion",
            "assistant_dramatic_function", "speaker_certainty",
        ):
            if not row.get(key):
                failures.append(f"field:{clip_id}:{key}")
    html = (review / "index.html").read_text(encoding="utf-8")
    app = (review / "app.js").read_text(encoding="utf-8")
    if len(re.findall(r"<audio\b", html, re.I)) != 2:
        failures.append("audio_element_count")
    for required_text in (
        "Approve as labeled", "Approve after cleanup", "Mine a better nearby line",
        "target-filters", "status-filters", "alexandria_three_voice_source_atlas_review.json",
    ):
        if required_text not in html and required_text not in app:
            failures.append(f"ui_contract:{required_text}")
    manifest = json.loads((review / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("production_promotion_allowed") is not False:
        failures.append("manifest:production_promotion")
    if manifest.get("maximum_simultaneous_audio_elements") != 2:
        failures.append("manifest:audio_limit")
    if failures:
        raise ReviewPackageError(f"Review validation failed: {failures}")
    return {
        "candidate_count": len(rows),
        "target_counts": dict(counts),
        "missing_count": len(missing),
        "failure_count": len(failures),
        "review": str(review / "index.html"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Package the three-voice source atlas for efficient human review.")
    sub = parser.add_subparsers(dest="command", required=True)
    package_parser = sub.add_parser("package")
    package_parser.add_argument("--atlas", required=True)
    package_parser.add_argument("--output-root", required=True)
    package_parser.add_argument("--narrator-reference", required=True)
    package_parser.add_argument("--benny-reference", required=True)
    package_parser.add_argument("--doctor-reference", required=True)
    package_parser.add_argument("--port", type=int, default=8787)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--output-root", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        result = package(args) if args.command == "package" else validate(args)
    except (ReviewPackageError, subprocess.CalledProcessError, json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
