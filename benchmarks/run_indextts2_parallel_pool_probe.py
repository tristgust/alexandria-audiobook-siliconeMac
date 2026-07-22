#!/usr/bin/env python3
"""Measure warm multi-process IndexTTS2 throughput on Apple Silicon.

Each worker loads the same pinned local model, reports ready, waits on a common
barrier, and then synthesizes one independent line. The probe performs no
downloads and cannot promote a backend or mutate production Voice state.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
from io import StringIO
import json
import multiprocessing as mp
import os
from pathlib import Path
import queue
import resource
import time
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--aux-root", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--diffusion-steps", type=int, default=12)
    parser.add_argument("--greedy", action="store_true")
    parser.add_argument("--ready-timeout", type=float, default=240.0)
    parser.add_argument("--generation-timeout", type=float, default=300.0)
    return parser.parse_args()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def worker_main(
    worker_index: int,
    config: dict[str, Any],
    sample: dict[str, Any],
    ready_queue: Any,
    start_event: Any,
    result_queue: Any,
) -> None:
    try:
        import numpy as np
        import soundfile as sf
        import torch

        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

        from indextts.infer_v2 import IndexTTS2
        from run_indextts2_finalist_matrix import (
            install_cfm_overrides,
            install_gpt_generation_overrides,
        )

        torch.set_float32_matmul_precision("high")
        model_dir = Path(config["model_dir"])
        aux_root = Path(config["aux_root"])
        aux_paths = {
            "w2v_bert": str(aux_root / "w2v-bert-2.0"),
            "semantic_codec": str(aux_root / "semantic_codec" / "model.safetensors"),
            "campplus": str(aux_root / "campplus_cn_common.bin"),
            "bigvgan": str(aux_root / "bigvgan"),
        }
        load_started = time.perf_counter()
        model = IndexTTS2(
            cfg_path=str(model_dir / "config.yaml"),
            model_dir=str(model_dir),
            use_fp16=False,
            device=config["device"],
            use_cuda_kernel=False,
            use_deepspeed=False,
            use_accel=False,
            use_torch_compile=False,
            aux_paths=aux_paths,
        )
        install_gpt_generation_overrides(
            model,
            greedy=bool(config["greedy"]),
        )
        install_cfm_overrides(
            model,
            diffusion_steps=int(config["diffusion_steps"]),
            inference_cfg_rate=None,
        )
        load_seconds = time.perf_counter() - load_started
        ready_queue.put(
            {
                "worker_index": worker_index,
                "status": "ready",
                "load_seconds": load_seconds,
            }
        )

        if not start_event.wait(timeout=float(config["generation_timeout"])):
            raise TimeoutError("Generation start barrier timed out.")

        output_dir = Path(config["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"worker_{worker_index}_{sample['sample_id']}.wav"
        torch.manual_seed(int(sample["seed"]))
        np.random.seed(int(sample["seed"]))

        captured = StringIO()
        generation_started = time.perf_counter()
        infer_kwargs = {
            "num_beams": 1,
            "max_mel_tokens": int(sample.get("max_mel_tokens", 600)),
        }
        emotion_audio_prompt = sample.get("emotion_audio_prompt")
        if emotion_audio_prompt:
            infer_kwargs["emo_audio_prompt"] = str(Path(emotion_audio_prompt))
            infer_kwargs["emo_alpha"] = float(sample.get("emotion_strength", 0.6))
        with torch.inference_mode(), redirect_stdout(captured):
            returned = model.infer(
                spk_audio_prompt=str(Path(sample["reference_audio"])),
                text=str(sample["text"]),
                output_path=str(output_path),
                use_random=False,
                verbose=False,
                **infer_kwargs,
            )
        generation_seconds = time.perf_counter() - generation_started
        if not output_path.is_file():
            raise RuntimeError(
                f"Worker {worker_index} produced no output; returned {returned!r}."
            )
        audio, sample_rate = sf.read(output_path, dtype="float32", always_2d=True)
        duration_seconds = len(audio) / int(sample_rate)
        result_queue.put(
            {
                "worker_index": worker_index,
                "status": "complete",
                "sample_id": sample["sample_id"],
                "target_text_sha256": sha256_text(str(sample["text"])),
                "load_seconds": load_seconds,
                "generation_seconds": generation_seconds,
                "duration_seconds": duration_seconds,
                "real_time_factor": generation_seconds / duration_seconds,
                "peak_rss_gib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                / (1024**3),
                "output_file": output_path.name,
                "model_log_tail": captured.getvalue()[-2000:],
            }
        )
    except BaseException as exc:
        result_queue.put(
            {
                "worker_index": worker_index,
                "status": "error",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    samples = list(manifest.get("samples") or [])
    if len(samples) < 2:
        raise ValueError("Parallel pool probe requires at least two samples.")
    for sample in samples:
        reference = Path(str(sample.get("reference_audio") or "")).expanduser().resolve()
        if not reference.is_file():
            raise FileNotFoundError(reference)
        sample["reference_audio"] = str(reference)
        emotion_audio_value = sample.get("emotion_audio_prompt")
        if emotion_audio_value:
            emotion_audio = Path(str(emotion_audio_value)).expanduser().resolve()
            if not emotion_audio.is_file():
                raise FileNotFoundError(emotion_audio)
            sample["emotion_audio_prompt"] = str(emotion_audio)
        if not str(sample.get("text") or "").strip():
            raise ValueError("Each sample requires text.")

    config = {
        "model_dir": str(Path(args.model_dir).expanduser().resolve()),
        "aux_root": str(Path(args.aux_root).expanduser().resolve()),
        "output_dir": str(Path(args.output_dir).expanduser().resolve()),
        "device": args.device,
        "diffusion_steps": args.diffusion_steps,
        "greedy": args.greedy,
        "generation_timeout": args.generation_timeout,
    }
    context = mp.get_context("spawn")
    ready_queue = context.Queue()
    result_queue = context.Queue()
    start_event = context.Event()
    workers = [
        context.Process(
            target=worker_main,
            args=(index, config, sample, ready_queue, start_event, result_queue),
        )
        for index, sample in enumerate(samples, start=1)
    ]
    for process in workers:
        process.start()

    ready = []
    try:
        deadline = time.monotonic() + args.ready_timeout
        while len(ready) < len(workers):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for both workers to load.")
            item = ready_queue.get(timeout=remaining)
            ready.append(item)

        wall_started = time.perf_counter()
        start_event.set()
        results = []
        deadline = time.monotonic() + args.generation_timeout
        while len(results) < len(workers):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for generation results.")
            results.append(result_queue.get(timeout=remaining))
        wall_generation_seconds = time.perf_counter() - wall_started
    except queue.Empty as exc:
        raise TimeoutError("Worker queue timed out.") from exc
    finally:
        for process in workers:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

    errors = [item for item in results if item.get("status") != "complete"]
    if errors:
        raise RuntimeError(json.dumps(errors, indent=2))
    aggregate_audio_seconds = sum(item["duration_seconds"] for item in results)
    summary = {
        "schema_version": 1,
        "candidate": "indextts2",
        "device": args.device,
        "worker_count": len(workers),
        "runtime_controls": {
            "use_fp16": False,
            "num_beams": 1,
            "diffusion_steps": args.diffusion_steps,
            "greedy_generation": args.greedy,
            "mps_fast_math": os.getenv("PYTORCH_MPS_FAST_MATH") == "1",
            "mps_prefer_metal": os.getenv("PYTORCH_MPS_PREFER_METAL") == "1",
        },
        "ready": sorted(ready, key=lambda item: item["worker_index"]),
        "results": sorted(results, key=lambda item: item["worker_index"]),
        "wall_generation_seconds": wall_generation_seconds,
        "aggregate_audio_seconds": aggregate_audio_seconds,
        "aggregate_throughput_rtf": wall_generation_seconds / aggregate_audio_seconds,
        "production_promotion_allowed": False,
        "manual_listening_required": True,
    }
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "parallel_pool_result.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
