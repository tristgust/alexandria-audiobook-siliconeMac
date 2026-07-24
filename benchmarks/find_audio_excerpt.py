#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import zlib
from pathlib import Path

import numpy as np
from scipy.signal import fftconvolve


def decode_template(payload: str) -> np.ndarray:
    raw = zlib.decompress(base64.b64decode(payload.encode("ascii")))
    template = np.frombuffer(raw, dtype="<f4").astype(np.float32)
    if template.size < 1000:
        raise ValueError("Template is too short")
    template = template - float(np.mean(template))
    peak = float(np.max(np.abs(template)))
    if peak > 0:
        template = template / peak
    return template


def load_source(path: Path, sample_rate: int) -> np.ndarray:
    process = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "f32le",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    )
    source = np.frombuffer(process.stdout, dtype="<f4").astype(np.float32)
    if source.size == 0:
        raise ValueError(f"No audio decoded from {path}")
    return source


def normalized_cross_correlation(source: np.ndarray, template: np.ndarray) -> np.ndarray:
    width = template.size
    if source.size < width:
        raise ValueError("Source is shorter than template")
    centered_template = template - float(np.mean(template))
    template_norm = float(np.linalg.norm(centered_template))
    if template_norm <= 1e-9:
        raise ValueError("Template has no usable energy")
    correlation = fftconvolve(source, centered_template[::-1], mode="valid")
    cumulative = np.concatenate(([0.0], np.cumsum(source, dtype=np.float64)))
    cumulative_sq = np.concatenate(([0.0], np.cumsum(source * source, dtype=np.float64)))
    sums = cumulative[width:] - cumulative[:-width]
    sums_sq = cumulative_sq[width:] - cumulative_sq[:-width]
    centered_energy = np.maximum(sums_sq - (sums * sums) / width, 1e-12)
    return correlation / (np.sqrt(centered_energy) * template_norm)


def top_nonoverlapping(scores: np.ndarray, count: int, exclusion: int) -> list[tuple[int, float]]:
    working = scores.copy()
    results: list[tuple[int, float]] = []
    for _ in range(count):
        index = int(np.argmax(working))
        score = float(working[index])
        if not np.isfinite(score):
            break
        results.append((index, score))
        left = max(0, index - exclusion)
        right = min(working.size, index + exclusion + 1)
        working[left:right] = -np.inf
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Locate an uploaded audio excerpt inside a local source recording.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--payload", required=True)
    parser.add_argument("--sample-rate", type=int, default=1000)
    parser.add_argument("--top", type=int, default=5)
    args = parser.parse_args()

    source_path = Path(args.source).expanduser().resolve()
    if not source_path.is_file():
        raise SystemExit(f"Source is missing: {source_path}")
    template = decode_template(args.payload)
    source = load_source(source_path, args.sample_rate)
    scores = normalized_cross_correlation(source, template)
    candidates = top_nonoverlapping(scores, args.top, max(1, template.size // 2))
    print(
        json.dumps(
            {
                "source": str(source_path),
                "sample_rate": args.sample_rate,
                "template_samples": int(template.size),
                "template_duration_seconds": template.size / args.sample_rate,
                "candidates": [
                    {
                        "start_seconds": index / args.sample_rate,
                        "end_seconds": (index + template.size) / args.sample_rate,
                        "correlation": score,
                    }
                    for index, score in candidates
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
