#!/usr/bin/env python3
"""Generate durable neutral and style-matched Ryan reference clips.

The built-in Qwen CustomVoice Ryan is used only to create comparison anchors.
The neutral anchor has one fixed transcript. Every acted anchor uses a distinct
reference line from the Round 1 contract, never the target line that models will
later synthesize. These anchors do not mutate Alexandria Voices or production
registries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import mlx.core as mx
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
BENCHMARKS = ROOT / "benchmarks"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))

from mlx_backend import MLXBackend  # noqa: E402
from model_registry import model_cache_status, model_spec  # noqa: E402
from multimodel_blind_round1_contract import STYLES  # noqa: E402

DEFAULT_OUTPUT = (
    ROOT
    / ".omo"
    / "evidence"
    / "b17-t05-multimodel-round1"
    / "references"
    / "ryan"
)
NEUTRAL_REFERENCE_TEXT = (
    "The lantern stood on the table beside a stack of unopened letters."
)
NEUTRAL_INSTRUCTION = (
    "Speak in the stable natural Ryan voice with neutral conversational delivery, "
    "clear diction, and no added emotional performance."
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def audio_metrics(path: Path) -> dict[str, Any]:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = audio.mean(axis=1)
    rms = float(np.sqrt(np.mean(mono * mono))) if len(mono) else 0.0
    peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
    return {
        "duration_seconds": len(mono) / int(sample_rate),
        "sample_rate": int(sample_rate),
        "channels": int(audio.shape[1]),
        "rms": rms,
        "peak": peak,
    }


def generate_one(
    backend: MLXBackend,
    *,
    text: str,
    instruction: str,
    seed: int,
    output_path: Path,
    reuse: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    reused = reuse and output_path.is_file()
    if not reused:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        mx.random.seed(int(seed))
        backend.generate_custom(
            text=text,
            instruct=instruction,
            voice="Ryan",
            output_path=str(output_path),
        )
    elapsed = time.perf_counter() - started
    if not output_path.is_file():
        raise RuntimeError(f"Ryan generation produced no output: {output_path}")
    metrics = audio_metrics(output_path)
    return {
        "audio_file": output_path.name,
        "audio_sha256": sha256_file(output_path),
        "text": text,
        "text_sha256": sha256_text(text),
        "instruction": instruction,
        "instruction_sha256": sha256_text(instruction),
        "seed": int(seed),
        "generation_seconds": elapsed,
        "real_time_factor": elapsed / metrics["duration_seconds"],
        "reused": reused,
        "audio": metrics,
    }


def disable_optional_sklearn() -> None:
    import transformers.utils as transformers_utils
    import transformers.utils.import_utils as import_utils

    unavailable = lambda: False
    import_utils.is_sklearn_available = unavailable
    transformers_utils.is_sklearn_available = unavailable


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--seed", type=int, default=6100)
    parser.add_argument("--reuse-existing", action="store_true")
    args = parser.parse_args()

    disable_optional_sklearn()
    output_root = Path(args.output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    status = model_cache_status("mlx_custom_voice")
    if not status.get("cached"):
        raise RuntimeError("Pinned Qwen CustomVoice model is not cached.")
    spec = model_spec("mlx_custom_voice")
    backend = MLXBackend(language="English")

    neutral = generate_one(
        backend,
        text=NEUTRAL_REFERENCE_TEXT,
        instruction=NEUTRAL_INSTRUCTION,
        seed=args.seed,
        output_path=output_root / "ryan_neutral_anchor.wav",
        reuse=args.reuse_existing,
    )
    neutral.update(
        {
            "identity_key": "ryan_neutral",
            "label": "Ryan — neutral clone anchor",
            "kind": "built_in_qwen_custom_voice_anchor",
        }
    )

    acted = []
    for index, style in enumerate(STYLES, start=1):
        record = generate_one(
            backend,
            text=style["acted_reference_text"],
            instruction=style["instruction"],
            seed=args.seed + index,
            output_path=output_root / f"ryan_acted_{style['key']}.wav",
            reuse=args.reuse_existing,
        )
        record.update(
            {
                "identity_key": "ryan_acted",
                "label": "Ryan — style-matched acted anchor",
                "style": style["key"],
                "kind": "built_in_qwen_custom_voice_acted_anchor",
            }
        )
        acted.append(record)
        print(
            json.dumps(
                {
                    "style": style["key"],
                    "duration_seconds": record["audio"]["duration_seconds"],
                    "generation_seconds": record["generation_seconds"],
                    "reused": record["reused"],
                }
            ),
            flush=True,
        )

    manifest = {
        "schema_version": 1,
        "purpose": "round1_neutral_and_style_matched_ryan_reference_anchors",
        "model": {
            "key": spec.key,
            "repo_id": spec.repo_id,
            "revision": spec.revision,
            "snapshot_path": status.get("snapshot_path"),
        },
        "voice": "Ryan",
        "neutral": neutral,
        "acted_style_count": len(acted),
        "acted": acted,
        "target_text_reused_as_reference": False,
        "production_promotion_allowed": False,
        "production_registry_changed": False,
        "voice_assignment_changed": False,
        "live_project_audio_changed": False,
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "manifest": str(output_root / "manifest.json"),
                "neutral": neutral["audio_file"],
                "acted_style_count": len(acted),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
