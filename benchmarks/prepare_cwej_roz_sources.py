#!/usr/bin/env python3
"""Prepare and hash the owned and public sources for the Cwej/Roz evaluation.

Public reference audio remains private evaluation evidence. The script does not
modify the user's owned masters and does not add any voice to Alexandria's
production registry.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import urllib.parse
import urllib.request
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "benchmarks/cwej_roz_sources.json"
DEFAULT_OUTPUT_ROOT = ROOT / ".omo/evidence/cwej-roz-voice-evaluation"


class SourcePreparationError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], *, capture: bool = False) -> str:
    completed = subprocess.run(
        command,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    return completed.stdout if capture else ""


def ffprobe(path: Path) -> dict[str, Any]:
    payload = json.loads(
        run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration,size,format_name:stream=index,codec_name,codec_type,sample_rate,channels",
                "-of",
                "json",
                str(path),
            ],
            capture=True,
        )
    )
    audio_streams = [
        row for row in payload.get("streams", []) if row.get("codec_type") == "audio"
    ]
    if not audio_streams:
        raise SourcePreparationError(f"No audio stream found: {path}")
    fmt = payload.get("format") or {}
    return {
        "duration_seconds": round(float(fmt.get("duration") or 0.0), 6),
        "size_bytes": int(fmt.get("size") or path.stat().st_size),
        "format_name": fmt.get("format_name"),
        "audio_streams": audio_streams,
    }


def normalize_audio(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
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


def download_direct(url: str, target: Path) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        with target.open("wb") as handle:
            shutil.copyfileobj(response, handle)
        return {
            "resolved_url": response.geturl(),
            "content_type": response.headers.get("Content-Type"),
            "content_length": response.headers.get("Content-Length"),
            "etag": response.headers.get("ETag"),
            "last_modified": response.headers.get("Last-Modified"),
        }


def download_youtube(url: str, key: str, raw_root: Path) -> tuple[Path, dict[str, Any]]:
    raw_root.mkdir(parents=True, exist_ok=True)
    metadata = json.loads(
        run(
            [
                "yt-dlp",
                "--no-playlist",
                "--dump-single-json",
                "--skip-download",
                url,
            ],
            capture=True,
        )
    )
    output_template = str(raw_root / f"{key}.%(ext)s")
    run(
        [
            "yt-dlp",
            "--no-playlist",
            "-f",
            "bestaudio/best",
            "-x",
            "--audio-format",
            "wav",
            "--audio-quality",
            "0",
            "-o",
            output_template,
            url,
        ]
    )
    target = raw_root / f"{key}.wav"
    if not target.is_file():
        matches = sorted(raw_root.glob(f"{key}.*"))
        if not matches:
            raise SourcePreparationError(f"yt-dlp produced no audio for {key}")
        target = matches[0]
    return target, {
        "id": metadata.get("id"),
        "title": metadata.get("title"),
        "uploader": metadata.get("uploader"),
        "channel": metadata.get("channel"),
        "upload_date": metadata.get("upload_date"),
        "duration_seconds": metadata.get("duration"),
        "webpage_url": metadata.get("webpage_url") or url,
    }


def prepare_sources(config_path: Path, output_root: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("schema_version") != 1:
        raise SourcePreparationError("Unsupported source configuration schema.")
    permission = config.get("permission") or {}
    if permission.get("confirmed_by_user") is not True:
        raise SourcePreparationError("Explicit voice-use permission is not recorded.")

    private_root = output_root / "private"
    raw_root = private_root / "public-sources/raw"
    prepared_root = private_root / "public-sources/prepared"
    output_root.mkdir(parents=True, exist_ok=True)

    owned_rows: list[dict[str, Any]] = []
    for row in config["owned_dramas"]:
        path = Path(row["path"]).expanduser().resolve()
        if not path.is_file():
            raise SourcePreparationError(f"Owned drama is missing: {path}")
        owned_rows.append(
            {
                **row,
                "path": str(path),
                "sha256": sha256_file(path),
                **ffprobe(path),
                "master_modified": False,
            }
        )

    public_rows: list[dict[str, Any]] = []
    for row in config["public_references"]:
        key = row["key"]
        kind = row["kind"]
        if kind == "direct_audio":
            suffix = Path(urllib.parse.urlparse(row["url"]).path).suffix or ".bin"
            raw_path = raw_root / f"{key}{suffix}"
            source_metadata = download_direct(row["url"], raw_path)
        elif kind == "youtube":
            raw_path, source_metadata = download_youtube(
                row["url"], key, raw_root
            )
        else:
            raise SourcePreparationError(f"Unsupported public source kind: {kind}")
        prepared_path = prepared_root / f"{key}.wav"
        normalize_audio(raw_path, prepared_path)
        public_rows.append(
            {
                **row,
                "raw_path": str(raw_path.resolve()),
                "raw_sha256": sha256_file(raw_path),
                "raw_audio": ffprobe(raw_path),
                "prepared_path": str(prepared_path.resolve()),
                "prepared_sha256": sha256_file(prepared_path),
                "prepared_audio": ffprobe(prepared_path),
                "source_metadata": source_metadata,
            }
        )

    manifest = {
        "schema_version": 1,
        "round_id": config["round_id"],
        "prepared_at": utc_now(),
        "config_path": str(config_path.resolve()),
        "config_sha256": sha256_file(config_path),
        "permission": permission,
        "owned_dramas": owned_rows,
        "public_references": public_rows,
        "normalization": {
            "channels": 1,
            "sample_rate": 24000,
            "codec": "pcm_s16le",
        },
        "production_mutation": False,
    }
    manifest_path = output_root / "source-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    manifest = prepare_sources(args.config.resolve(), args.output_root.resolve())
    print(
        json.dumps(
            {
                "round_id": manifest["round_id"],
                "owned_dramas": len(manifest["owned_dramas"]),
                "public_references": len(manifest["public_references"]),
                "manifest": str((args.output_root.resolve() / "source-manifest.json")),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
