#!/usr/bin/env python3
"""Prepare durable sentence-bounded identity references for Round 1.

The source files remain untouched. Each output keeps the full original recording,
a mono PCM conditioning clip, the exact conditioning transcript, source/clip
hashes, trim bounds, and a pinned local Whisper transcript check.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata as metadata
import json
import re
import shutil
import subprocess
import sys
import types
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_ROOT = Path("/Users/tristan/pinokio/api/alexandria-audiobook.git")
DEFAULT_OUTPUT = ROOT / ".omo" / "evidence" / "b17-t05-multimodel-round1" / "references"
WHISPER_REVISION = "1e3e249fb8d01c655324bd6841b1deadffd6d04c"
WHISPER_REPO_FOLDER = "models--mlx-community--whisper-base-mlx"
WHISPER_RUNTIME_VERSION = "0.4.3"
_WORD_PATTERN = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)

IDENTITIES = {
    "narrator": {
        "label": "Narrator",
        "source": "clone_voices/narratorvoicelines_-_01_1784553553.mp3",
        "source_transcript": (
            "This is the story of a man named Stanley. Stanley worked for a company "
            "in a big building where he was employee number 427. Employee number "
            "427's job was simple. He sat at his desk in room 427 and he pushed "
            "buttons on a keyboard. Orders came to him through a monitor on his desk, "
            "telling him what buttons to push, how long to push them, and in what "
            "order. This is what employee 427 did every day of every month of every year."
        ),
        "clip_start_seconds": 0.0,
        "clip_duration_seconds": 9.6,
        "clip_transcript": (
            "This is the story of a man named Stanley. Stanley worked for a company "
            "in a big building where he was employee number 427."
        ),
    },
    "benny": {
        "label": "Benny",
        "source": "clone_voices/bennyvoice1_1784053953.mp3",
        "source_transcript": (
            "Just the five of us against the might and money of Irving Braxietel. "
            "But we can't keep running and hiding. We decided to fight back. But we "
            "have to choose our moment. Wait until Brax shows his hand and makes his "
            "move. Until then, we're acting normal. And normal for me is digging up "
            "stuff. So here I am on forgotten Gevada, following a tantalizing lead "
            "from a banjoal trader."
        ),
        "clip_start_seconds": 0.0,
        "clip_duration_seconds": 8.4,
        "clip_transcript": (
            "Just the five of us against the might and money of Irving Braxietel. "
            "But we can't keep running and hiding."
        ),
    },
    "doctor": {
        "label": "Doctor",
        "source": "clone_voices/dw7voice1_1784300409.mp3",
        "source_transcript": (
            "The portal through which Hector Thomas entered this world, and the means "
            "by which he's supposed to leave it. She always puts you down, tells you "
            "how stupid you are. I can see what she means. I might as well be talking "
            "to a door. Fear me... tell this to your gods. When they punish you, when "
            "they stretch you on the neutron."
        ),
        "clip_start_seconds": 0.0,
        "clip_duration_seconds": 6.2,
        "clip_transcript": (
            "The portal through which Hector Thomas entered this world, and the means "
            "by which he's supposed to leave it."
        ),
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalized_words(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return [word.replace("’", "'") for word in _WORD_PATTERN.findall(normalized)]


def word_error_rate(reference: str, hypothesis: str) -> float:
    left = normalized_words(reference)
    right = normalized_words(hypothesis)
    if not left:
        return 0.0 if not right else 1.0
    previous = list(range(len(right) + 1))
    for index, left_word in enumerate(left, start=1):
        current = [index]
        for column, right_word in enumerate(right, start=1):
            current.append(
                min(
                    previous[column - 1] + (left_word != right_word),
                    previous[column] + 1,
                    current[column - 1] + 1,
                )
            )
        previous = current
    return previous[-1] / len(left)


def _median_filter(volume: Any, kernel_size: Any = None) -> np.ndarray:
    array = np.asarray(volume)
    if kernel_size is None:
        sizes = (3,) * array.ndim
    elif isinstance(kernel_size, int):
        sizes = (kernel_size,) * array.ndim
    else:
        sizes = tuple(int(value) for value in kernel_size)
    padding = tuple((value // 2, value // 2) for value in sizes)
    padded = np.pad(array, padding, mode="constant")
    windows = np.lib.stride_tricks.sliding_window_view(padded, sizes)
    axes = tuple(range(array.ndim, windows.ndim))
    return np.median(windows, axis=axes).astype(array.dtype, copy=False)


def load_whisper_runtime() -> Any:
    if metadata.version("mlx-whisper") != WHISPER_RUNTIME_VERSION:
        raise RuntimeError(f"mlx-whisper=={WHISPER_RUNTIME_VERSION} is required.")
    prior = {
        name: module
        for name, module in list(sys.modules.items())
        if name == "scipy" or name.startswith("scipy.")
    }
    for name in prior:
        sys.modules.pop(name, None)
    scipy = types.ModuleType("scipy")
    signal = types.ModuleType("scipy.signal")
    signal.medfilt = _median_filter
    scipy.signal = signal
    scipy.__version__ = "1.15.3"
    sys.modules["scipy"] = scipy
    sys.modules["scipy.signal"] = signal
    try:
        return importlib.import_module("mlx_whisper")
    finally:
        for name in list(sys.modules):
            if name == "scipy" or name.startswith("scipy."):
                sys.modules.pop(name, None)
        sys.modules.update(prior)


def find_whisper_snapshot() -> Path:
    roots = [
        Path.home() / ".cache" / "huggingface" / "hub",
        Path("/Users/tristan/pinokio/cache/HF_HOME/hub"),
    ]
    for root in roots:
        snapshot = root / WHISPER_REPO_FOLDER / "snapshots" / WHISPER_REVISION
        if snapshot.is_dir():
            return snapshot
    raise FileNotFoundError("Pinned Whisper Base snapshot is not cached.")


def trim_to_wav(source: Path, target: Path, *, start: float, duration: float) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(target),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not target.is_file():
        raise RuntimeError(completed.stderr[-2000:])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", default=str(DEFAULT_SOURCE_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    source_root = Path(args.source_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    whisper = load_whisper_runtime()
    whisper_snapshot = find_whisper_snapshot()

    records = []
    for identity_key, identity in IDENTITIES.items():
        source = (source_root / identity["source"]).resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        identity_dir = output_root / identity_key
        identity_dir.mkdir(parents=True, exist_ok=True)
        original = identity_dir / f"original{source.suffix.lower()}"
        if not original.is_file() or sha256_file(original) != sha256_file(source):
            shutil.copy2(source, original)
        conditioning = identity_dir / "conditioning.wav"
        trim_to_wav(
            source,
            conditioning,
            start=float(identity["clip_start_seconds"]),
            duration=float(identity["clip_duration_seconds"]),
        )
        audio, sample_rate = sf.read(conditioning, dtype="float32", always_2d=True)
        result = whisper.transcribe(
            str(conditioning),
            path_or_hf_repo=str(whisper_snapshot),
            language="en",
            word_timestamps=False,
            condition_on_previous_text=False,
            verbose=False,
        )
        transcript = str(result.get("text") or "").strip()
        record = {
            "identity_key": identity_key,
            "label": identity["label"],
            "source_file": str(original.relative_to(output_root)),
            "source_sha256": sha256_file(original),
            "source_transcript": identity["source_transcript"],
            "source_transcript_sha256": sha256_text(identity["source_transcript"]),
            "conditioning_file": str(conditioning.relative_to(output_root)),
            "conditioning_sha256": sha256_file(conditioning),
            "conditioning_transcript": identity["clip_transcript"],
            "conditioning_transcript_sha256": sha256_text(identity["clip_transcript"]),
            "trim": {
                "start_seconds": identity["clip_start_seconds"],
                "duration_seconds": identity["clip_duration_seconds"],
            },
            "audio": {
                "duration_seconds": len(audio) / int(sample_rate),
                "sample_rate": int(sample_rate),
                "channels": int(audio.shape[1]),
            },
            "pinned_asr": {
                "model": "mlx-community/whisper-base-mlx",
                "revision": WHISPER_REVISION,
                "runtime": f"mlx-whisper=={WHISPER_RUNTIME_VERSION}",
                "transcript": transcript,
                "word_error_rate": word_error_rate(identity["clip_transcript"], transcript),
                "exact_normalized_text": normalized_words(identity["clip_transcript"])
                == normalized_words(transcript),
            },
        }
        (identity_dir / "reference.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )
        records.append(record)
        print(json.dumps({"identity": identity_key, "asr": record["pinned_asr"]}))

    manifest = {
        "schema_version": 1,
        "purpose": "multimodel_round1_sentence_bounded_identity_references",
        "identity_count": len(records),
        "identities": records,
        "production_promotion_allowed": False,
        "voice_assignment_changed": False,
        "live_project_audio_changed": False,
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"manifest": str(output_root / "manifest.json")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
