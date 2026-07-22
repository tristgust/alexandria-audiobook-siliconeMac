from __future__ import annotations

import argparse
import hashlib
import json
import platform
import resource
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import librosa
import mlx.core as mx
import numpy as np
import soundfile as sf
from mlx_audio.tts.utils import load_model


ROOT = Path(__file__).resolve().parents[1]
VOXCPM_MODEL = "mlx-community/VoxCPM2-4bit"
QWEN_SPEAKER_MODEL = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def peak_rss_gib() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if platform.system() == "Darwin":
        return value / (1024**3)
    return value * 1024 / (1024**3)


def speaker_embedding(model: Any, path: str | Path) -> np.ndarray:
    audio, _ = librosa.load(path, sr=24000, mono=True)
    embedding = np.asarray(
        model.extract_speaker_embedding(mx.array(audio), sr=24000)
    ).reshape(-1)
    return embedding / (np.linalg.norm(embedding) + 1e-12)


def cosine(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.dot(left, right))


def run(args: argparse.Namespace) -> dict[str, Any]:
    reference = Path(args.reference_audio).expanduser().resolve()
    if not reference.is_file():
        raise FileNotFoundError(reference)

    load_started = time.perf_counter()
    model = load_model(args.model)
    load_seconds = time.perf_counter() - load_started

    styles = (
        ("neutral", args.neutral_instruction),
        ("expressive", args.expressive_instruction),
    )
    outputs: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="alexandria-controlled-clone-") as temp:
        output_dir = Path(temp)
        generated_paths: dict[str, Path] = {}
        for style, instruction in styles:
            started = time.perf_counter()
            results = list(
                model.generate(
                    text=args.text,
                    ref_audio=str(reference),
                    ref_text=args.reference_text,
                    instruct=instruction,
                    cfg_value=args.cfg_value,
                    inference_timesteps=args.inference_timesteps,
                    max_tokens=args.max_tokens,
                )
            )
            elapsed = time.perf_counter() - started
            if not results:
                raise RuntimeError(f"No audio returned for {style}.")
            audio = np.asarray(results[0].audio, dtype=np.float32).reshape(-1)
            target = output_dir / f"{style}.wav"
            sf.write(target, audio, model.sample_rate)
            duration = len(audio) / model.sample_rate
            generated_paths[style] = target
            outputs[style] = {
                "instruction_sha256": sha256_text(instruction),
                "elapsed_seconds": elapsed,
                "audio_duration_seconds": duration,
                "real_time_factor": elapsed / duration if duration else None,
                "audio_sha256": sha256_file(target),
            }

        speaker_model = load_model(args.speaker_model)
        reference_embedding = speaker_embedding(speaker_model, reference)
        for style, _instruction in styles:
            output_embedding = speaker_embedding(
                speaker_model,
                generated_paths[style],
            )
            outputs[style]["speaker_cosine_to_reference"] = cosine(
                reference_embedding,
                output_embedding,
            )

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {
        "schema_version": 1,
        "created_at_utc": utc_now(),
        "repository_head": head,
        "hardware": {
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "backend": "voxcpm2_controlled",
        "model": args.model,
        "speaker_evaluation_model": args.speaker_model,
        "reference_audio_sha256": sha256_file(reference),
        "reference_text_sha256": sha256_text(args.reference_text),
        "test_text_sha256": sha256_text(args.text),
        "cold_load_seconds": load_seconds,
        "peak_process_rss_gib": peak_rss_gib(),
        "settings": {
            "cfg_value": args.cfg_value,
            "inference_timesteps": args.inference_timesteps,
            "max_tokens": args.max_tokens,
        },
        "measurements": outputs,
        "acceptance": {
            "faster_than_or_equal_to_real_time": all(
                item["real_time_factor"] <= 1.05
                for item in outputs.values()
            ),
            "speaker_similarity_floor": 0.95,
            "speaker_identity_passed": all(
                item["speaker_cosine_to_reference"] >= 0.95
                for item in outputs.values()
            ),
            "manual_audio_review_required": True,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-audio", required=True)
    parser.add_argument("--reference-text", required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument(
        "--neutral-instruction",
        default=(
            "Calm, measured, conversational delivery. Preserve the original "
            "speaker identity and accent."
        ),
    )
    parser.add_argument(
        "--expressive-instruction",
        default=(
            "Controlled anger, hard consonants, restrained intensity, "
            "slightly faster pace. Preserve the original speaker identity "
            "and accent."
        ),
    )
    parser.add_argument("--model", default=VOXCPM_MODEL)
    parser.add_argument("--speaker-model", default=QWEN_SPEAKER_MODEL)
    parser.add_argument("--cfg-value", type=float, default=2.0)
    parser.add_argument("--inference-timesteps", type=int, default=10)
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--output")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run(args)
    rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
