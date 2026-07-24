#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Iterable

ANCHORS = (
    {
        "label": "canonical_calm",
        "start_seconds": 0.0,
        "end_seconds": 5.82,
        "transcript": (
            "The portal through which Hector Thomas entered this world, "
            "and the means by which he's supposed to leave it."
        ),
    },
    {
        "label": "dry_sarcastic",
        "start_seconds": 5.82,
        "end_seconds": 11.98,
        "transcript": (
            "She always puts you down, tells you how stupid you are. "
            "I can see what she means."
        ),
    },
    {
        "label": "irritated",
        "start_seconds": 12.30,
        "end_seconds": 15.58,
        "transcript": "I might as well be talking to a door.",
    },
    {
        "label": "threatening",
        "start_seconds": 15.78,
        "end_seconds": 24.02,
        "transcript": (
            "Fear me. Tell this to your gods. When they punish you, "
            "when they stretch you on the neutron."
        ),
    },
)


class AnchorError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ffprobe(path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,sample_rate,channels,duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AnchorError(completed.stderr.strip() or f"ffprobe failed for {path}")
    value = json.loads(completed.stdout)
    streams = value.get("streams") or []
    if not streams:
        raise AnchorError(f"No audio stream in {path}")
    stream = streams[0]
    return {
        "codec_name": stream.get("codec_name"),
        "sample_rate": int(stream.get("sample_rate") or 0),
        "channels": int(stream.get("channels") or 0),
        "duration_seconds": float(stream.get("duration") or 0.0),
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    source = Path(args.source).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    if not source.is_file():
        raise AnchorError(f"Doctor source is missing: {source}")
    anchors_root = output_root / "anchors"
    anchors_root.mkdir(parents=True, exist_ok=True)
    rows = []
    for spec in ANCHORS:
        output = anchors_root / f"{spec['label']}.wav"
        if not output.exists() or args.force:
            completed = subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    str(spec["start_seconds"]),
                    "-t",
                    str(spec["end_seconds"] - spec["start_seconds"]),
                    "-i",
                    str(source),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "24000",
                    "-c:a",
                    "pcm_s16le",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                raise AnchorError(completed.stderr.strip() or f"ffmpeg failed for {spec['label']}")
        info = ffprobe(output)
        if info["codec_name"] != "pcm_s16le" or info["sample_rate"] != 24000 or info["channels"] != 1:
            raise AnchorError(f"Anchor format mismatch for {output}: {info}")
        rows.append(
            {
                **spec,
                "audio": str(output),
                "audio_sha256": sha256_file(output),
                "audio_info": info,
            }
        )
    map_path = output_root / "doctor-anchor-map.json"
    map_path.write_text(
        json.dumps(
            {
                "doctor": [
                    {"label": row["label"], "audio": row["audio"]}
                    for row in rows
                ]
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "purpose": "doctor_register_matched_seedvc_target_anchors",
        "source": str(source),
        "source_sha256": sha256_file(source),
        "anchor_count": len(rows),
        "anchors": rows,
        "anchor_map": str(map_path),
        "production_promotion_allowed": False,
    }
    manifest_path = output_root / "doctor-anchor-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {
        "anchor_count": len(rows),
        "anchor_map": str(map_path),
        "manifest": str(manifest_path),
    }


def validate(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    manifest = json.loads((output_root / "doctor-anchor-manifest.json").read_text(encoding="utf-8"))
    missing = []
    bad_hash = []
    for row in manifest["anchors"]:
        audio = Path(row["audio"])
        if not audio.is_file():
            missing.append(row["label"])
        elif sha256_file(audio) != row["audio_sha256"]:
            bad_hash.append(row["label"])
    if missing or bad_hash:
        raise AnchorError(f"Validation failed: missing={missing}, bad_hash={bad_hash}")
    return {
        "anchor_count": manifest["anchor_count"],
        "missing_count": len(missing),
        "bad_hash_count": len(bad_hash),
        "anchor_map": manifest["anchor_map"],
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Prepare homogeneous Doctor identity anchors for Seed-VC.")
    sub = result.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--source", required=True)
    build_parser.add_argument("--output-root", required=True)
    build_parser.add_argument("--force", action="store_true")
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
