#!/usr/bin/env python3
"""Generate a bounded IndexTTS2 validation matrix with two persistent MPS workers.

The runner is evaluation-only. Each worker loads the pinned local model once,
processes multiple queued samples, writes one WAV and receipt per sample, and
performs no downloads or production-state mutation.
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
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--diffusion-steps", type=int, default=8)
    parser.add_argument("--greedy", action="store_true")
    parser.add_argument("--ready-timeout", type=float, default=300.0)
    parser.add_argument("--generation-timeout", type=float, default=1800.0)
    parser.add_argument("--no-resume", action="store_true")
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_hash(value: Any) -> str:
    return sha256_text(json.dumps(value, sort_keys=True, separators=(",", ":")))


def normalize_samples(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    samples = list(manifest.get("samples") or [])
    if len(samples) != 24:
        raise ValueError(f"Winner validation requires exactly 24 samples, found {len(samples)}")
    seen = set()
    normalized = []
    for raw in samples:
        sample_id = str(raw.get("sample_id") or "").strip()
        if not sample_id or sample_id in seen:
            raise ValueError(f"Duplicate or empty sample_id: {sample_id!r}")
        seen.add(sample_id)
        reference = Path(str(raw.get("reference_audio") or "")).expanduser().resolve()
        emotion = Path(str(raw.get("emotion_audio_prompt") or "")).expanduser().resolve()
        text = str(raw.get("text") or "").strip()
        if not reference.is_file():
            raise FileNotFoundError(reference)
        if not emotion.is_file():
            raise FileNotFoundError(emotion)
        if not text:
            raise ValueError(f"Sample {sample_id!r} has no text")
        if sha256_file(reference) != raw.get("reference_audio_sha256"):
            raise ValueError(f"Speaker reference hash mismatch for {sample_id}")
        if sha256_file(emotion) != raw.get("emotion_audio_sha256"):
            raise ValueError(f"Emotion reference hash mismatch for {sample_id}")
        normalized.append(
            {
                **raw,
                "sample_id": sample_id,
                "reference_audio": str(reference),
                "emotion_audio_prompt": str(emotion),
                "text": text,
            }
        )
    expected_speakers = {"narrator", "benny", "doctor"}
    expected_styles = {"fear", "panic", "contempt", "relief", "urgent", "calm", "pleading", "shout"}
    if {row["speaker"] for row in normalized} != expected_speakers:
        raise ValueError("Winner validation speaker set changed")
    if {row["style"] for row in normalized} != expected_styles:
        raise ValueError("Winner validation style set changed")
    for speaker in expected_speakers:
        if sum(row["speaker"] == speaker for row in normalized) != 8:
            raise ValueError(f"Expected eight samples for {speaker}")
    return normalized


def worker_main(
    worker_index: int,
    config: dict[str, Any],
    task_queue: Any,
    ready_queue: Any,
    result_queue: Any,
) -> None:
    try:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        os.environ.setdefault("PYTORCH_MPS_FAST_MATH", "1")
        os.environ.setdefault("PYTORCH_MPS_PREFER_METAL", "1")

        import numpy as np
        import torch

        from indextts.infer_v2 import IndexTTS2
        from run_indextts2_finalist_matrix import (
            audio_metrics,
            install_cfm_overrides,
            install_gpt_generation_overrides,
            parse_stage_timings,
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
        install_gpt_generation_overrides(model, greedy=bool(config["greedy"]))
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
                "pid": os.getpid(),
            }
        )

        completed_jobs = 0
        while True:
            task = task_queue.get()
            if task is None:
                break
            sample = task["sample"]
            try:
                sample_dir = Path(task["sample_dir"])
                sample_dir.mkdir(parents=True, exist_ok=True)
                output_path = sample_dir / "audio.wav"
                torch.manual_seed(int(sample["seed"]))
                np.random.seed(int(sample["seed"]))
                captured = StringIO()
                generation_started = time.perf_counter()
                with torch.inference_mode(), redirect_stdout(captured):
                    returned = model.infer(
                        spk_audio_prompt=sample["reference_audio"],
                        text=sample["text"],
                        output_path=str(output_path),
                        emo_audio_prompt=sample["emotion_audio_prompt"],
                        emo_alpha=float(sample["emotion_strength"]),
                        use_random=False,
                        verbose=False,
                        num_beams=1,
                        max_mel_tokens=int(sample.get("generation", {}).get("max_mel_tokens", 600)),
                    )
                generation_seconds = time.perf_counter() - generation_started
                if not output_path.is_file():
                    raise RuntimeError(f"No audio produced; model returned {returned!r}")
                log = captured.getvalue()
                metrics = audio_metrics(output_path, len(sample["text"].split()))
                completed_jobs += 1
                record = {
                    "schema_version": 1,
                    "sample_id": sample["sample_id"],
                    "sample_fingerprint": task["sample_fingerprint"],
                    "worker_index": worker_index,
                    "worker_job_index": completed_jobs,
                    "worker_load_seconds": load_seconds,
                    "speaker": sample["speaker"],
                    "identity_label": sample["identity_label"],
                    "style": sample["style"],
                    "selection_kind": sample["selection_kind"],
                    "source_selection_sample_id": sample["source_selection_sample_id"],
                    "source_instruction_sha256": sample["source_instruction_sha256"],
                    "source_seed": sample["source_seed"],
                    "seed": sample["seed"],
                    "reference_audio_sha256": sample["reference_audio_sha256"],
                    "emotion_audio_sha256": sample["emotion_audio_sha256"],
                    "emotion_strength": sample["emotion_strength"],
                    "emotion_strength_origin": sample["emotion_strength_origin"],
                    "target_text_sha256": sha256_text(sample["text"]),
                    "generation_seconds": generation_seconds,
                    "real_time_factor": generation_seconds / metrics["duration_seconds"],
                    "stage_timings": parse_stage_timings(log),
                    "peak_rss_gib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024**3),
                    "audio": metrics,
                    "audio_sha256": sha256_file(output_path),
                    "output_file": str(output_path),
                    "model_log_tail": log[-2000:],
                    "runtime_controls": {
                        "device": config["device"],
                        "use_fp16": False,
                        "mps_fast_math": os.getenv("PYTORCH_MPS_FAST_MATH") == "1",
                        "mps_prefer_metal": os.getenv("PYTORCH_MPS_PREFER_METAL") == "1",
                        "num_beams": 1,
                        "greedy_generation": bool(config["greedy"]),
                        "diffusion_steps": int(config["diffusion_steps"]),
                    },
                    "production_promotion_allowed": False,
                    "manual_listening_required": True,
                }
                (sample_dir / "result.json").write_text(
                    json.dumps(record, indent=2) + "\n", encoding="utf-8"
                )
                result_queue.put({"status": "complete", "record": record})
            except BaseException as exc:
                result_queue.put(
                    {
                        "status": "error",
                        "sample_id": sample.get("sample_id"),
                        "worker_index": worker_index,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
    except BaseException as exc:
        ready_queue.put(
            {
                "worker_index": worker_index,
                "status": "error",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )


def reusable_result(
    sample: dict[str, Any], sample_dir: Path, sample_fingerprint: str
) -> dict[str, Any] | None:
    result_path = sample_dir / "result.json"
    audio_path = sample_dir / "audio.wav"
    if not result_path.is_file() or not audio_path.is_file():
        return None
    try:
        record = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if record.get("sample_id") != sample["sample_id"]:
        return None
    if record.get("sample_fingerprint") != sample_fingerprint:
        return None
    if record.get("audio_sha256") != sha256_file(audio_path):
        return None
    return record


def main() -> int:
    args = parse_args()
    if args.workers != 2:
        raise ValueError("The accepted bounded profile requires exactly two persistent workers.")
    if not args.greedy:
        raise ValueError("The accepted bounded profile requires greedy decoding.")
    if args.diffusion_steps != 8:
        raise ValueError("The accepted bounded profile requires eight diffusion steps.")
    if args.device != "mps":
        raise ValueError("Winner validation is bounded to the accepted MPS profile.")

    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    samples = normalize_samples(manifest)
    profile = manifest.get("runtime_profile") or {}
    if profile.get("persistent_worker_count") != 2:
        raise ValueError("Manifest persistent-worker count changed")
    if profile.get("use_fp16") is not False:
        raise ValueError("Manifest must require FP32")
    if profile.get("greedy_generation") is not True or profile.get("diffusion_steps") != 8:
        raise ValueError("Manifest runtime profile changed")

    model_dir = Path(args.model_dir).expanduser().resolve()
    aux_root = Path(args.aux_root).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    if not (model_dir / "config.yaml").is_file():
        raise FileNotFoundError(model_dir / "config.yaml")
    for path in (
        aux_root / "w2v-bert-2.0",
        aux_root / "semantic_codec" / "model.safetensors",
        aux_root / "campplus_cn_common.bin",
        aux_root / "bigvgan",
    ):
        if not path.exists():
            raise FileNotFoundError(path)
    output_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "model_dir": str(model_dir),
        "aux_root": str(aux_root),
        "device": args.device,
        "diffusion_steps": args.diffusion_steps,
        "greedy": args.greedy,
    }
    manifest_sha256 = sha256_file(manifest_path)
    pending = []
    reused = []
    for sample in samples:
        sample_fingerprint = canonical_hash(
            {
                "sample": sample,
                "runtime": config,
                "manifest_sha256": manifest_sha256,
            }
        )
        sample_dir = output_dir / sample["sample_id"]
        existing = None if args.no_resume else reusable_result(sample, sample_dir, sample_fingerprint)
        if existing is not None:
            reused.append(existing)
        else:
            pending.append(
                {
                    "sample": sample,
                    "sample_dir": str(sample_dir),
                    "sample_fingerprint": sample_fingerprint,
                }
            )

    ready = []
    generated_records = []
    errors = []
    wall_generation_seconds = 0.0
    if pending:
        context = mp.get_context("spawn")
        task_queue = context.Queue()
        ready_queue = context.Queue()
        result_queue = context.Queue()
        workers = [
            context.Process(
                target=worker_main,
                args=(index, config, task_queue, ready_queue, result_queue),
            )
            for index in range(1, args.workers + 1)
        ]
        for process in workers:
            process.start()
        try:
            deadline = time.monotonic() + args.ready_timeout
            while len(ready) < len(workers):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Timed out waiting for persistent workers to load")
                item = ready_queue.get(timeout=remaining)
                if item.get("status") != "ready":
                    raise RuntimeError(json.dumps(item, indent=2))
                ready.append(item)

            wall_started = time.perf_counter()
            for task in pending:
                task_queue.put(task)
            for _ in workers:
                task_queue.put(None)

            deadline = time.monotonic() + args.generation_timeout
            while len(generated_records) + len(errors) < len(pending):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Timed out waiting for winner-validation generation")
                item = result_queue.get(timeout=remaining)
                if item.get("status") == "complete":
                    generated_records.append(item["record"])
                else:
                    errors.append(item)
            wall_generation_seconds = time.perf_counter() - wall_started
        except queue.Empty as exc:
            raise TimeoutError("Persistent worker queue timed out") from exc
        finally:
            for process in workers:
                process.join(timeout=10)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=10)

    all_records = sorted(reused + generated_records, key=lambda row: row["sample_id"])
    worker_job_counts: dict[str, int] = {}
    for record in generated_records:
        key = str(record["worker_index"])
        worker_job_counts[key] = worker_job_counts.get(key, 0) + 1
    aggregate_audio_seconds = sum(row["audio"]["duration_seconds"] for row in generated_records)
    summary = {
        "schema_version": 1,
        "purpose": "bounded_cross_speaker_winner_validation_generation",
        "manifest": str(manifest_path),
        "manifest_sha256": manifest_sha256,
        "sample_count": len(samples),
        "generated_sample_count": len(generated_records),
        "reused_sample_count": len(reused),
        "complete_sample_count": len(all_records),
        "error_count": len(errors),
        "errors": errors,
        "persistent_pool": {
            "worker_count": args.workers,
            "workers_loaded_once": True,
            "workers_process_multiple_jobs": True,
            "ready": sorted(ready, key=lambda row: row["worker_index"]),
            "generated_job_count_by_worker": worker_job_counts,
            "wall_generation_seconds": wall_generation_seconds,
            "generated_audio_seconds": aggregate_audio_seconds,
            "aggregate_throughput_rtf": (
                wall_generation_seconds / aggregate_audio_seconds
                if aggregate_audio_seconds
                else None
            ),
        },
        "runtime_controls": {
            "device": args.device,
            "use_fp16": False,
            "mps_fast_math": os.getenv("PYTORCH_MPS_FAST_MATH") == "1",
            "mps_prefer_metal": os.getenv("PYTORCH_MPS_PREFER_METAL") == "1",
            "num_beams": 1,
            "greedy_generation": args.greedy,
            "diffusion_steps": args.diffusion_steps,
        },
        "samples": all_records,
        "generic_ryan_regenerated": False,
        "temporary_paths_used": False,
        "license_review_complete": False,
        "production_promotion_allowed": False,
        "production_registry_changed": False,
        "voice_assignment_changed": False,
        "live_project_audio_changed": False,
    }
    summary_path = output_dir / "persistent_pool_result.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "summary": str(summary_path),
                "complete_sample_count": len(all_records),
                "generated_sample_count": len(generated_records),
                "reused_sample_count": len(reused),
                "error_count": len(errors),
                "worker_job_counts": worker_job_counts,
            },
            indent=2,
        )
    )
    if errors:
        raise RuntimeError(json.dumps(errors, indent=2))
    if len(all_records) != 24:
        raise RuntimeError(f"Expected 24 complete samples, found {len(all_records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
