#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import math
import subprocess
import zlib
from pathlib import Path

import numpy as np


def decode_query(payload: str) -> tuple[np.ndarray, dict[str, float | int]]:
    raw = zlib.decompress(base64.b64decode(payload.encode("ascii")))
    if len(raw) < 4:
        raise ValueError("Query payload is truncated")
    header_size = int.from_bytes(raw[:4], "little")
    header = json.loads(raw[4 : 4 + header_size].decode("utf-8"))
    frames = int(header["frames"])
    bands = int(header["bands"])
    data = np.frombuffer(raw[4 + header_size :], dtype=np.int8)
    if data.size != frames * bands:
        raise ValueError(
            f"Query payload shape mismatch: expected {frames * bands}, got {data.size}"
        )
    features = data.astype(np.float32).reshape(frames, bands) / 24.0
    return features, header


def load_audio(path: Path, sample_rate: int) -> np.ndarray:
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
    audio = np.frombuffer(process.stdout, dtype="<f4").astype(np.float32)
    if audio.size == 0:
        raise ValueError(f"No audio decoded from {path}")
    return audio


def spectral_features(
    audio: np.ndarray,
    *,
    sample_rate: int,
    frame_seconds: float,
    hop_seconds: float,
    bands: int,
) -> np.ndarray:
    audio = audio - float(np.mean(audio))
    peak = float(np.max(np.abs(audio)))
    if peak > 0:
        audio = audio / peak
    frame = max(256, int(round(sample_rate * frame_seconds)))
    hop = max(64, int(round(sample_rate * hop_seconds)))
    window = np.hanning(frame).astype(np.float32)
    frequencies = np.fft.rfftfreq(frame, 1.0 / sample_rate)
    edges = np.geomspace(70.0, min(3800.0, sample_rate / 2.0 - 1.0), bands + 1)
    masks = [(frequencies >= left) & (frequencies < right) for left, right in zip(edges[:-1], edges[1:])]
    values: list[np.ndarray] = []
    for start in range(0, max(1, len(audio) - frame + 1), hop):
        chunk = audio[start : start + frame]
        if len(chunk) < frame:
            chunk = np.pad(chunk, (0, frame - len(chunk)))
        spectrum = np.abs(np.fft.rfft(chunk * window)) ** 2
        row = np.asarray(
            [math.log1p(float(np.mean(spectrum[mask]))) if np.any(mask) else 0.0 for mask in masks],
            dtype=np.float32,
        )
        row = (row - float(np.mean(row))) / max(float(np.std(row)), 1e-6)
        values.append(row)
    return np.stack(values)


def normalized_distance(source: np.ndarray, query: np.ndarray, start: int) -> float:
    window = source[start : start + len(query)]
    difference = window - query
    return float(np.sqrt(np.mean(difference * difference)))


def top_candidates(source: np.ndarray, query: np.ndarray, count: int) -> list[tuple[int, float]]:
    if len(source) < len(query):
        raise ValueError("Source features are shorter than query features")
    distances = np.asarray(
        [normalized_distance(source, query, start) for start in range(len(source) - len(query) + 1)],
        dtype=np.float32,
    )
    order = np.argsort(distances)
    selected: list[tuple[int, float]] = []
    exclusion = max(1, len(query) // 2)
    for index in order:
        start = int(index)
        if any(abs(start - existing) < exclusion for existing, _ in selected):
            continue
        selected.append((start, float(distances[start])))
        if len(selected) >= count:
            break
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description="Locate a compact spectral excerpt inside a local source recording.")
    parser.add_argument("--source", required=True)
    payload_group = parser.add_mutually_exclusive_group(required=True)
    payload_group.add_argument("--payload")
    payload_group.add_argument("--payload-file")
    parser.add_argument("--top", type=int, default=5)
    args = parser.parse_args()

    source_path = Path(args.source).expanduser().resolve()
    if not source_path.is_file():
        raise SystemExit(f"Source is missing: {source_path}")
    payload = args.payload
    if args.payload_file:
        payload = Path(args.payload_file).expanduser().resolve().read_text(encoding="utf-8").strip()
    query, header = decode_query(payload)
    sample_rate = int(header["sample_rate"])
    frame_seconds = float(header["frame_seconds"])
    hop_seconds = float(header["hop_seconds"])
    bands = int(header["bands"])
    source_audio = load_audio(source_path, sample_rate)
    source_features = spectral_features(
        source_audio,
        sample_rate=sample_rate,
        frame_seconds=frame_seconds,
        hop_seconds=hop_seconds,
        bands=bands,
    )
    ranked = top_candidates(source_features, query, args.top)
    query_duration = (len(query) - 1) * hop_seconds + frame_seconds
    print(
        json.dumps(
            {
                "source": str(source_path),
                "query_frames": len(query),
                "source_frames": len(source_features),
                "query_duration_seconds": query_duration,
                "hop_seconds": hop_seconds,
                "candidates": [
                    {
                        "start_seconds": start * hop_seconds,
                        "end_seconds": start * hop_seconds + query_duration,
                        "spectral_rmse": distance,
                        "similarity": 1.0 / (1.0 + distance),
                    }
                    for start, distance in ranked
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
