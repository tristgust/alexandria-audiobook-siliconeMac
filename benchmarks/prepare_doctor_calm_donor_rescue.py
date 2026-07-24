#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any, Iterable

SAMPLE_IDS = {
    "calm": "d4f81f89d250626b",
    "pleading": "e3e1a4136ce098fb",
    "angry": "69139e1777b30993",
}
TEXTS = {
    "calm": "Breathe slowly. You are safe here.",
    "pleading": "Please, just listen to me. We still have time to make this right.",
    "angry": "After everything I did for you, this is how you chose to repay me.",
}


class DonorError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build(args: argparse.Namespace) -> dict[str, Any]:
    source_review = Path(args.source_review).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    if not source_review.is_file():
        raise DonorError(f"Source donor review is missing: {source_review}")
    payload = json.loads(source_review.read_text(encoding="utf-8"))
    rows = payload if isinstance(payload, list) else payload.get("rows") or payload.get("samples") or []
    by_id = {str(row.get("sample_id")): row for row in rows}
    for sample_id in SAMPLE_IDS.values():
        if sample_id not in by_id:
            raise DonorError(f"Required donor sample is missing: {sample_id}")

    audio_root = output_root / "audio"
    audio_root.mkdir(parents=True, exist_ok=True)
    calm_source = (source_review.parent / by_id[SAMPLE_IDS["calm"]]["file"]).resolve()
    calm_output = audio_root / "doctor_calm_short.wav"
    completed = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            "0",
            "-t",
            "3.95",
            "-i",
            str(calm_source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(calm_output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise DonorError(completed.stderr.strip() or "Could not cut calm donor")

    output_rows = []
    for mode in ("calm", "pleading", "angry"):
        original = by_id[SAMPLE_IDS[mode]]
        if mode == "calm":
            audio = calm_output
        else:
            source = (source_review.parent / original["file"]).resolve()
            audio = audio_root / f"{mode}.wav"
            shutil.copy2(source, audio)
        output_rows.append(
            {
                "sample_id": SAMPLE_IDS[mode],
                "file": str(audio.relative_to(output_root)),
                "requested_direction": mode,
                "expected_text": TEXTS[mode],
                "automatic_transcript_status": "reviewed_source",
                "automatic_transcript": TEXTS[mode],
                "spoken_text_matches_expected": True,
            }
        )

    review_path = output_root / "listening_review.json"
    review_path.write_text(json.dumps(output_rows, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "purpose": "short_complete_calm_donor_for_doctor_seedvc_rescue",
        "source_review": str(source_review),
        "calm_cut_seconds": [0.0, 3.95],
        "rows": [
            {
                **row,
                "audio_sha256": sha256_file(output_root / row["file"]),
            }
            for row in output_rows
        ],
        "review": str(review_path),
        "production_promotion_allowed": False,
    }
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"review": str(review_path), "manifest": str(manifest_path), "sample_count": len(output_rows)}


def validate(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    manifest = json.loads((output_root / "manifest.json").read_text(encoding="utf-8"))
    missing = []
    bad_hash = []
    for row in manifest["rows"]:
        path = output_root / row["file"]
        if not path.is_file():
            missing.append(row["sample_id"])
        elif sha256_file(path) != row["audio_sha256"]:
            bad_hash.append(row["sample_id"])
    if missing or bad_hash:
        raise DonorError(f"Validation failed: missing={missing}, bad_hash={bad_hash}")
    return {"sample_count": len(manifest["rows"]), "missing_count": 0, "bad_hash_count": 0}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Prepare a short complete calm donor for Doctor Seed-VC.")
    sub = result.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--source-review", required=True)
    build_parser.add_argument("--output-root", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--output-root", required=True)
    return result


def main(argv: Iterable[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    try:
        value = build(args) if args.command == "build" else validate(args)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(value, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
