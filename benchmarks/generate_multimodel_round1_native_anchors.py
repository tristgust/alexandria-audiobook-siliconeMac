#!/usr/bin/env python3
"""Generate stable model-native identity anchors for cached Round 1 candidates."""

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
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from mlx_backend import MLXBackend  # noqa: E402
from model_registry import model_cache_status  # noqa: E402

DEFAULT_OUTPUT = (
    ROOT
    / ".omo"
    / "evidence"
    / "b17-t05-multimodel-round1"
    / "references"
    / "native"
)
ANCHOR_TEXT = "The old clock marked the hour while the quiet library settled around us."


def disable_optional_sklearn() -> None:
    import transformers.utils as transformers_utils
    import transformers.utils.import_utils as import_utils

    unavailable = lambda: False
    import_utils.is_sklearn_available = unavailable
    transformers_utils.is_sklearn_available = unavailable


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def collect_results(model: Any, results: Any) -> tuple[np.ndarray, int]:
    arrays: list[np.ndarray] = []
    sample_rate = int(getattr(model, "sample_rate", 24000))
    for result in results:
        array = np.asarray(result.audio, dtype=np.float32).reshape(-1)
        if len(array):
            arrays.append(array)
        if getattr(result, "sample_rate", None):
            sample_rate = int(result.sample_rate)
    if not arrays:
        raise RuntimeError("Model-native anchor generation returned no audio.")
    return (arrays[0] if len(arrays) == 1 else np.concatenate(arrays), sample_rate)


def metrics(path: Path) -> dict[str, Any]:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    return {
        "duration_seconds": len(audio) / int(sample_rate),
        "sample_rate": int(sample_rate),
        "channels": int(audio.shape[1]),
    }


def load_mlx_model(repo_id: str, revision: str):
    from mlx_audio.tts.utils import load_model
    from mlx_audio.utils import get_model_name_parts

    cache_root = Path.home() / ".cache" / "huggingface" / "hub"
    snapshot = (
        cache_root
        / ("models--" + repo_id.replace("/", "--"))
        / "snapshots"
        / revision
    ).resolve()
    if not snapshot.is_dir():
        raise FileNotFoundError(snapshot)
    required = [snapshot / "config.json"]
    if not all(path.is_file() for path in required):
        raise RuntimeError(f"Required model files are missing from {snapshot}.")
    model = load_model(
        snapshot,
        model_name_parts=get_model_name_parts(repo_id),
        strict=False,
    )
    return model, snapshot


def write_audio(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, audio, sample_rate)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--seed", type=int, default=7000)
    parser.add_argument("--reuse-existing", action="store_true")
    args = parser.parse_args()

    disable_optional_sklearn()
    output_root = Path(args.output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []

    # Qwen Aiden: documented built-in CustomVoice speaker.
    qwen_path = output_root / "qwen_aiden.wav"
    started = time.perf_counter()
    if not (args.reuse_existing and qwen_path.is_file()):
        mx.random.seed(args.seed)
        backend = MLXBackend(language="English")
        backend.generate_custom(
            text=ANCHOR_TEXT,
            instruct="Natural neutral audiobook delivery with clear diction.",
            voice="Aiden",
            output_path=str(qwen_path),
        )
    records.append(
        {
            "identity_key": "native_qwen_aiden",
            "review_name": "Aiden",
            "model_key": "qwen3_tts",
            "kind": "documented_built_in_custom_voice",
            "audio_file": qwen_path.name,
            "audio_sha256": sha256_file(qwen_path),
            "transcript": ANCHOR_TEXT,
            "transcript_sha256": sha256_text(ANCHOR_TEXT),
            "seed": args.seed,
            "generation_seconds": time.perf_counter() - started,
            "audio": metrics(qwen_path),
        }
    )

    # Vox Rowan: fixed VoiceDesign anchor, then later cloned for each style.
    vox_path = output_root / "voxcpm2_rowan.wav"
    started = time.perf_counter()
    vox_model, vox_snapshot = load_mlx_model(
        "mlx-community/VoxCPM2-4bit",
        "dc9e5c187858da5f4a13dc4c247e297339216381",
    )
    vox_description = (
        "A mature androgynous English audiobook voice with clear midrange, "
        "measured pace, natural warmth, and restrained theatrical expression."
    )
    if not (args.reuse_existing and vox_path.is_file()):
        mx.random.seed(args.seed + 1)
        audio, sample_rate = collect_results(
            vox_model,
            vox_model.generate(
                text=ANCHOR_TEXT,
                instruct=vox_description,
                cfg_value=2.0,
                inference_timesteps=10,
                warmup_patches=1,
                max_tokens=1200,
            ),
        )
        write_audio(vox_path, audio, sample_rate)
    records.append(
        {
            "identity_key": "native_voxcpm2_rowan",
            "review_name": "Rowan",
            "model_key": "voxcpm2",
            "kind": "fixed_voice_design_anchor",
            "audio_file": vox_path.name,
            "audio_sha256": sha256_file(vox_path),
            "transcript": ANCHOR_TEXT,
            "transcript_sha256": sha256_text(ANCHOR_TEXT),
            "voice_design_instruction": vox_description,
            "voice_design_instruction_sha256": sha256_text(vox_description),
            "seed": args.seed + 1,
            "generation_seconds": time.perf_counter() - started,
            "model_snapshot": str(vox_snapshot),
            "audio": metrics(vox_path),
        }
    )

    # Fish Marlow: fixed-seed reference-less anchor, then cloned for each style.
    fish_path = output_root / "fish_marlow.wav"
    started = time.perf_counter()
    fish_model, fish_snapshot = load_mlx_model(
        "mlx-community/fish-audio-s2-pro",
        "eccd57bf5c1ebc13cb2f993df867f4e49931a36a",
    )
    fish_description = (
        "A composed adult English audiobook narrator with a clear medium-low "
        "voice, natural phrasing, and restrained warmth."
    )
    if not (args.reuse_existing and fish_path.is_file()):
        mx.random.seed(args.seed + 2)
        audio, sample_rate = collect_results(
            fish_model,
            fish_model.generate(
                text=ANCHOR_TEXT,
                instruct=fish_description,
                max_tokens=1024,
                temperature=0.7,
                top_p=0.7,
                top_k=30,
                verbose=False,
            ),
        )
        write_audio(fish_path, audio, sample_rate)
    records.append(
        {
            "identity_key": "native_fish_marlow",
            "review_name": "Marlow",
            "model_key": "fish_s2_pro",
            "kind": "fixed_seed_reference_less_anchor",
            "audio_file": fish_path.name,
            "audio_sha256": sha256_file(fish_path),
            "transcript": ANCHOR_TEXT,
            "transcript_sha256": sha256_text(ANCHOR_TEXT),
            "native_instruction": fish_description,
            "native_instruction_sha256": sha256_text(fish_description),
            "seed": args.seed + 2,
            "generation_seconds": time.perf_counter() - started,
            "model_snapshot": str(fish_snapshot),
            "audio": metrics(fish_path),
        }
    )

    manifest = {
        "schema_version": 1,
        "purpose": "cached_model_native_identity_anchors_for_round1",
        "anchor_text": ANCHOR_TEXT,
        "anchor_text_sha256": sha256_text(ANCHOR_TEXT),
        "record_count": len(records),
        "records": records,
        "pending_native_anchors": [
            "native_moss_alder",
            "native_chatterbox_linden",
            "native_higgs_belinda",
        ],
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
                "generated": [item["identity_key"] for item in records],
                "pending": manifest["pending_native_anchors"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
