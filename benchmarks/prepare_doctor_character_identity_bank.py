#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable
import zipfile

import numpy as np
import soundfile as sf

BANKS = {
    "core_identity": (
        "sample_0197.wav",
        "sample_0198.wav",
        "sample_0199.wav",
        "sample_0201.wav",
        "sample_0202.wav",
        "sample_0204.wav",
        "sample_0205.wav",
        "sample_0206.wav",
        "sample_0207.wav",
        "sample_0208.wav",
    ),
    "calm_authoritative": (
        "sample_0204.wav",
        "sample_0205.wav",
        "sample_0206.wav",
        "sample_0207.wav",
        "sample_0208.wav",
    ),
    "dry_irritated": (
        "sample_0201.wav",
        "sample_0202.wav",
    ),
    "dark_intense": (
        "sample_0197.wav",
        "sample_0198.wav",
        "sample_0199.wav",
        "sample_0203.wav",
        "sample_0209.wav",
        "sample_0210.wav",
        "sample_0211.wav",
        "sample_0212.wav",
        "sample_0213.wav",
    ),
}


class IdentityBankError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_metadata(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "metadata.jsonl"
    if not path.is_file():
        raise IdentityBankError(f"metadata.jsonl is missing: {path}")
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows[row["audio_filepath"]] = row
    return rows


def load_mono(path: Path) -> tuple[np.ndarray, int]:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = np.mean(audio, axis=1, dtype=np.float32)
    if not mono.size:
        raise IdentityBankError(f"Empty audio: {path}")
    return mono, int(sample_rate)


def concatenate(
    sources: list[Path],
    output: Path,
    *,
    sample_rate: int = 24000,
    silence_seconds: float = 0.18,
) -> dict[str, Any]:
    parts = []
    durations = []
    silence = np.zeros(max(1, int(round(sample_rate * silence_seconds))), dtype=np.float32)
    for index, source in enumerate(sources):
        audio, rate = load_mono(source)
        if rate != sample_rate:
            raise IdentityBankError(f"Unexpected sample rate for {source}: {rate}")
        if index:
            parts.append(silence)
        parts.append(audio)
        durations.append(len(audio) / rate)
    merged = np.concatenate(parts)
    peak = float(np.max(np.abs(merged)))
    if peak > 0:
        merged = merged * min(1.0, 0.70795 / peak)
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, merged, sample_rate, subtype="PCM_16")
    return {
        "duration_seconds": len(merged) / sample_rate,
        "source_duration_seconds": sum(durations),
        "silence_seconds": silence_seconds,
        "sample_rate": sample_rate,
        "channels": 1,
        "peak_after_normalization": float(np.max(np.abs(merged))),
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    archive = Path(args.prepared_zip).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    if not archive.is_file():
        raise IdentityBankError(f"Prepared Doctor ZIP is missing: {archive}")
    output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="doctor-character-bank-") as temporary:
        extracted = Path(temporary)
        with zipfile.ZipFile(archive) as zip_file:
            zip_file.extractall(extracted)
        metadata = load_metadata(extracted)
        required = {name for values in BANKS.values() for name in values}
        missing = sorted(name for name in required if not (extracted / name).is_file())
        if missing:
            raise IdentityBankError(f"Prepared Doctor clips are missing: {missing}")

        clip_root = output_root / "clips"
        clip_root.mkdir(parents=True, exist_ok=True)
        clip_rows = []
        for name in sorted(required):
            source = extracted / name
            target = clip_root / name
            shutil.copy2(source, target)
            row = metadata[name]
            clip_rows.append(
                {
                    "audio_filepath": name,
                    "audio": str(target),
                    "audio_sha256": sha256_file(target),
                    "text": row["text"],
                    "duration_seconds": row["duration_seconds"],
                    "transcript_confidence": row["transcript_confidence"],
                    "snr_db": row["snr_db"],
                    "source_start_seconds": row["source_start_seconds"],
                    "source_end_seconds": row["source_end_seconds"],
                }
            )

        bank_rows = []
        banks_root = output_root / "banks"
        by_name = {row["audio_filepath"]: row for row in clip_rows}
        for label, names in BANKS.items():
            output = banks_root / f"doctor_{label}.wav"
            metrics = concatenate([clip_root / name for name in names], output)
            bank_rows.append(
                {
                    "label": label,
                    "audio": str(output),
                    "audio_sha256": sha256_file(output),
                    "clip_count": len(names),
                    "source_clips": list(names),
                    "transcript": " ".join(by_name[name]["text"] for name in names),
                    "metrics": metrics,
                }
            )

    anchor_map = output_root / "doctor-character-anchor-map.json"
    anchor_map.write_text(
        json.dumps(
            {
                "doctor": [
                    {"label": row["label"], "audio": row["audio"]}
                    for row in bank_rows
                ]
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    metric_map = output_root / "doctor-character-identity-metric-map.json"
    core = next(row for row in bank_rows if row["label"] == "core_identity")
    metric_map.write_text(
        json.dumps({"doctor": core["audio"]}, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "purpose": "seventh_doctor_in_character_identity_bank",
        "prepared_zip": str(archive),
        "prepared_zip_sha256": sha256_file(archive),
        "source_window_seconds": [1333.20, 1434.26],
        "clip_count": len(clip_rows),
        "clips": clip_rows,
        "bank_count": len(bank_rows),
        "banks": bank_rows,
        "anchor_map": str(anchor_map),
        "identity_metric_map": str(metric_map),
        "interview_audio_excluded": True,
        "production_promotion_allowed": False,
    }
    manifest_path = output_root / "doctor-character-identity-bank.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {
        "clip_count": len(clip_rows),
        "bank_count": len(bank_rows),
        "anchor_map": str(anchor_map),
        "identity_metric_map": str(metric_map),
        "manifest": str(manifest_path),
    }


def validate(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    manifest = json.loads((output_root / "doctor-character-identity-bank.json").read_text(encoding="utf-8"))
    missing = []
    bad_hash = []
    for row in [*manifest["clips"], *manifest["banks"]]:
        path = Path(row["audio"])
        if not path.is_file():
            missing.append(str(path))
        elif sha256_file(path) != row["audio_sha256"]:
            bad_hash.append(str(path))
    if missing or bad_hash:
        raise IdentityBankError(f"Validation failed: missing={missing}, bad_hash={bad_hash}")
    return {
        "clip_count": manifest["clip_count"],
        "bank_count": manifest["bank_count"],
        "missing_count": len(missing),
        "bad_hash_count": len(bad_hash),
        "interview_audio_excluded": manifest["interview_audio_excluded"],
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Build a Seventh Doctor identity bank from in-character source clips.")
    sub = result.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--prepared-zip", required=True)
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
