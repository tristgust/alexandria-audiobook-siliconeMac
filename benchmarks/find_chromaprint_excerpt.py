#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import struct
import subprocess
from pathlib import Path


def chromaprint(path: Path) -> list[int]:
    process = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "chromaprint",
            "-fp_format",
            "raw",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    )
    payload = process.stdout
    if not payload or len(payload) % 4:
        raise ValueError(f"Invalid Chromaprint payload for {path}: {len(payload)} bytes")
    return list(struct.unpack(f"<{len(payload) // 4}I", payload))


def duration_seconds(path: Path) -> float:
    process = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(process.stdout.strip())


def bit_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def window_score(source: list[int], query: list[int], start: int) -> float:
    distances = [bit_distance(source[start + index], value) for index, value in enumerate(query)]
    return sum(distances) / (32.0 * len(distances))


def candidates(source: list[int], query: list[int], count: int) -> list[tuple[int, float]]:
    if len(source) < len(query):
        raise ValueError("Source fingerprint is shorter than query fingerprint")
    scores = [(start, window_score(source, query, start)) for start in range(len(source) - len(query) + 1)]
    scores.sort(key=lambda item: item[1])
    selected: list[tuple[int, float]] = []
    exclusion = max(1, len(query) // 2)
    for start, score in scores:
        if any(abs(start - existing) < exclusion for existing, _ in selected):
            continue
        selected.append((start, score))
        if len(selected) >= count:
            break
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description="Locate a compact Chromaprint excerpt inside a local source recording.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--query", required=True, help="Comma-separated unsigned 32-bit Chromaprint integers")
    parser.add_argument("--query-duration", required=True, type=float)
    parser.add_argument("--top", type=int, default=5)
    args = parser.parse_args()

    source_path = Path(args.source).expanduser().resolve()
    if not source_path.is_file():
        raise SystemExit(f"Source is missing: {source_path}")
    query = [int(value) for value in args.query.split(",") if value]
    if len(query) < 8:
        raise SystemExit("Query fingerprint is too short")
    source = chromaprint(source_path)
    source_duration = duration_seconds(source_path)
    frames_per_second = len(source) / source_duration
    ranked = candidates(source, query, args.top)
    print(
        json.dumps(
            {
                "source": str(source_path),
                "source_duration_seconds": source_duration,
                "source_fingerprint_frames": len(source),
                "frames_per_second": frames_per_second,
                "query_duration_seconds": args.query_duration,
                "query_fingerprint_frames": len(query),
                "candidates": [
                    {
                        "start_seconds": start / frames_per_second,
                        "end_seconds": start / frames_per_second + args.query_duration,
                        "normalized_hamming_distance": score,
                        "similarity": 1.0 - score,
                    }
                    for start, score in ranked
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
