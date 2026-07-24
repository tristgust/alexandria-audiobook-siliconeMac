#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import soundfile as sf

MATCHES: tuple[dict[str, Any], ...] = (
    {
        "upload_name": "dw7voice2.mp3",
        "upload_sha256": "519227b69588f59a85773d2cd31a72a17b195f4ef9c948fe37159ac9e86bd8fe",
        "upload_duration_seconds": 9.247347,
        "source_start_seconds": 1336.0,
        "source_end_seconds": 1345.247347,
        "spectral_similarity": 0.7772552748741303,
        "provisional_kind": "in_character",
    },
    {
        "upload_name": "dw7voice3.mp3",
        "upload_sha256": "f10e691f3f6a3385cdf388a1765d97bae9f113846f67f3d4a3fcd035c853dc2c",
        "upload_duration_seconds": 29.64898,
        "source_start_seconds": 1034.2,
        "source_end_seconds": 1063.84898,
        "spectral_similarity": 0.9481162133527313,
        "provisional_kind": "actor_interview",
    },
    {
        "upload_name": "dw7voice4.mp3",
        "upload_sha256": "77c0e7dba8ca66b128a8ae447a322e984faddb269d6b057fc1b7b3bc19f36920",
        "upload_duration_seconds": 21.237551,
        "source_start_seconds": 0.0,
        "source_end_seconds": 21.237551,
        "spectral_similarity": 0.9735951601259014,
        "provisional_kind": "actor_interview",
    },
)


class DoctorUploadError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def extract(source: Path, output: Path, start: float, duration: float) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{start:.6f}",
            "-t",
            f"{duration:.6f}",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(output),
        ],
        check=True,
    )


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    source = Path(args.source).expanduser().resolve()
    whisper_model = Path(args.whisper_model).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    if not source.is_file():
        raise DoctorUploadError(f"Long-form Doctor source is missing: {source}")
    if not whisper_model.is_dir():
        raise DoctorUploadError(f"Whisper model is missing: {whisper_model}")
    import mlx_whisper

    rows = []
    clips_root = output_root / "clips"
    for match in MATCHES:
        clip = clips_root / match["upload_name"].replace(".mp3", ".wav")
        extract(
            source,
            clip,
            float(match["source_start_seconds"]),
            float(match["upload_duration_seconds"]),
        )
        audio_info = sf.info(clip)
        result = mlx_whisper.transcribe(
            str(clip),
            path_or_hf_repo=str(whisper_model),
            language="en",
            word_timestamps=True,
            condition_on_previous_text=False,
            verbose=False,
        )
        transcript = str(result.get("text") or "").strip()
        segments = [
            {
                "start_seconds": round(float(segment["start"]), 3),
                "end_seconds": round(float(segment["end"]), 3),
                "text": str(segment.get("text") or "").strip(),
            }
            for segment in result.get("segments", [])
        ]
        rows.append(
            {
                **match,
                "source_audio": str(source),
                "source_audio_sha256": sha256_file(source),
                "extracted_audio": str(clip),
                "extracted_audio_sha256": sha256_file(clip),
                "extracted_duration_seconds": round(float(audio_info.duration), 6),
                "sample_rate": int(audio_info.samplerate),
                "channels": int(audio_info.channels),
                "transcript": transcript,
                "segments": segments,
            }
        )
    manifest = {
        "schema_version": 1,
        "created_at": now_iso(),
        "purpose": "recover_new_doctor_chat_uploads_from_local_long_form_source",
        "source_audio": str(source),
        "source_audio_sha256": sha256_file(source),
        "clip_count": len(rows),
        "rows": rows,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"clip_count": len(rows), "manifest": str(path)}


def validate(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    path = output_root / "manifest.json"
    if not path.is_file():
        raise DoctorUploadError(f"Manifest is missing: {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    missing = []
    bad_hash = []
    bad_audio = []
    for row in manifest["rows"]:
        clip = Path(row["extracted_audio"])
        if not clip.is_file():
            missing.append(row["upload_name"])
            continue
        if sha256_file(clip) != row["extracted_audio_sha256"]:
            bad_hash.append(row["upload_name"])
        info = sf.info(clip)
        if info.samplerate != 24000 or info.channels != 1 or info.duration < 1.0:
            bad_audio.append(row["upload_name"])
    if missing or bad_hash or bad_audio:
        raise DoctorUploadError(
            f"Validation failed: missing={missing}, bad_hash={bad_hash}, bad_audio={bad_audio}"
        )
    return {
        "clip_count": len(manifest["rows"]),
        "missing_count": len(missing),
        "bad_hash_count": len(bad_hash),
        "bad_audio_count": len(bad_audio),
        "manifest": str(path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Recover matched Doctor uploads from the local long-form source.")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--source", required=True)
    prepare_parser.add_argument("--whisper-model", required=True)
    prepare_parser.add_argument("--output-root", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--output-root", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        result = prepare(args) if args.command == "prepare" else validate(args)
    except (DoctorUploadError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
