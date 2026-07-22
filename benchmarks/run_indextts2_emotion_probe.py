#!/usr/bin/env python3
"""Run one isolated IndexTTS2 emotion probe against a supplied voice reference.

This intentionally runs one direction per process so a transport retry cannot
corrupt a multi-direction result directory. It does not download models and it
never promotes a backend for production use.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import resource
import time
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import torch


EMOTION_CONTROLS: dict[str, dict[str, Any]] = {
    "calm": {"emo_vector": [0, 0, 0, 0, 0, 0, 0, 0.8]},
    "angry": {"emo_vector": [0, 0.8, 0, 0, 0, 0, 0, 0]},
    "sad": {"emo_vector": [0, 0, 0.8, 0, 0, 0, 0, 0]},
    "afraid": {"emo_vector": [0, 0, 0, 0.8, 0, 0, 0, 0]},
    "melancholic": {"emo_vector": [0, 0, 0, 0, 0, 0.8, 0, 0]},
    "happy": {"emo_vector": [0.8, 0, 0, 0, 0, 0, 0, 0]},
    "surprised": {"emo_vector": [0, 0, 0, 0, 0, 0, 0.8, 0]},
    "text_frightened_whisper": {
        "use_emo_text": True,
        "emo_text": (
            "Speak in a terrified whisper, barely above a breath, as though "
            "someone is listening nearby."
        ),
        "emo_alpha": 0.6,
    },
}


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def audio_metrics(path: Path, word_count: int) -> dict[str, Any]:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = audio.mean(axis=1)
    duration = len(mono) / sample_rate
    rms = float(np.sqrt(np.mean(mono * mono))) if len(mono) else 0.0
    peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
    return {
        "duration_seconds": duration,
        "sample_rate": int(sample_rate),
        "rms_dbfs": 20.0 * math.log10(max(rms, 1e-12)),
        "peak_dbfs": 20.0 * math.log10(max(peak, 1e-12)),
        "words_per_second": word_count / duration if duration else None,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--aux-root", required=True)
    parser.add_argument("--reference-audio", required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--direction", required=True, choices=sorted(EMOTION_CONTROLS))
    parser.add_argument("--seed", type=int, default=1001)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    from indextts.infer_v2 import IndexTTS2

    model_dir = Path(args.model_dir).expanduser().resolve()
    aux_root = Path(args.aux_root).expanduser().resolve()
    reference_audio = Path(args.reference_audio).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    aux_paths = {
        "w2v_bert": str(aux_root / "w2v-bert-2.0"),
        "semantic_codec": str(aux_root / "semantic_codec" / "model.safetensors"),
        "campplus": str(aux_root / "campplus_cn_common.bin"),
        "bigvgan": str(aux_root / "bigvgan"),
    }
    for path in [model_dir, reference_audio, *map(Path, aux_paths.values())]:
        if not path.exists():
            raise FileNotFoundError(path)

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    load_started = time.perf_counter()
    model = IndexTTS2(
        cfg_path=str(model_dir / "config.yaml"),
        model_dir=str(model_dir),
        use_fp16=False,
        device=args.device,
        use_cuda_kernel=False,
        use_deepspeed=False,
        use_accel=False,
        use_torch_compile=False,
        aux_paths=aux_paths,
    )
    load_seconds = time.perf_counter() - load_started

    output_path = output_dir / f"{args.direction}_{args.seed}.wav"
    generation_started = time.perf_counter()
    returned = model.infer(
        spk_audio_prompt=str(reference_audio),
        text=args.text,
        output_path=str(output_path),
        use_random=False,
        verbose=False,
        **EMOTION_CONTROLS[args.direction],
    )
    generation_seconds = time.perf_counter() - generation_started
    if not output_path.is_file():
        raise RuntimeError(f"IndexTTS2 did not create {output_path}; returned {returned!r}")

    metrics = audio_metrics(output_path, len(args.text.split()))
    result = {
        "schema_version": 1,
        "candidate": "indextts2",
        "device": args.device,
        "direction": args.direction,
        "seed": args.seed,
        "reference_kind": "supplied_recording_clone",
        "reference_audio_sha256": hashlib.sha256(reference_audio.read_bytes()).hexdigest(),
        "target_text_sha256": sha256_text(args.text),
        "expected_text": args.text,
        "control": EMOTION_CONTROLS[args.direction],
        "load_seconds": load_seconds,
        "generation_seconds": generation_seconds,
        "real_time_factor": generation_seconds / metrics["duration_seconds"],
        "peak_rss_gib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024**3),
        "audio": metrics,
        "output_file": output_path.name,
        "production_promotion_allowed": False,
        "manual_listening_required": True,
    }
    review = {
        "sample_id": f"indextts2_{args.direction}_{args.seed}",
        "file": output_path.name,
        "requested_direction": args.direction,
        "expected_text": args.text,
        "automatic_transcription_status": "unavailable",
        "automatic_transcript": None,
        "word_error_rate": None,
        "spoken_text_matches_expected": None,
        "narrator_identity_1_to_5": None,
        "delivery_adherence_1_to_5": None,
        "naturalness_1_to_5": None,
        "artifact_severity_1_to_5": None,
        "approve_for_candidate_comparison": None,
        "notes": "",
    }
    (output_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    (output_dir / "listening_review.json").write_text(json.dumps(review, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
