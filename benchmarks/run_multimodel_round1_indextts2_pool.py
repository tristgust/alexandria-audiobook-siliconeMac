#!/usr/bin/env python3
"""Generate arbitrary-size Round 1 IndexTTS2 samples with two persistent MPS workers."""

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
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE_BENCHMARKS = Path(
    "/Users/tristan/pinokio/api/alexandria-audiobook.git/benchmarks"
)
if str(SOURCE_BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(SOURCE_BENCHMARKS))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--aux-root", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--diffusion-steps", type=int, default=8)
    parser.add_argument("--greedy", action="store_true")
    parser.add_argument("--ready-timeout", type=float, default=300.0)
    parser.add_argument("--generation-timeout", type=float, default=10800.0)
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


def read_manifest(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    samples = list(manifest.get("samples") or [])
    if not samples:
        raise ValueError("IndexTTS2 Round 1 manifest has no samples")
    seen = set()
    normalized = []
    for raw in samples:
        sample_id = str(raw.get("sample_id") or "").strip()
        if not sample_id or sample_id in seen:
            raise ValueError(f"Duplicate or empty sample ID: {sample_id!r}")
        seen.add(sample_id)
        reference = Path(str(raw.get("reference_audio") or "")).expanduser().resolve()
        emotion = Path(str(raw.get("emotion_audio_prompt") or "")).expanduser().resolve()
        output = Path(str(raw.get("output_file") or "")).expanduser().resolve()
        result = Path(str(raw.get("result_file") or "")).expanduser().resolve()
        text = str(raw.get("text") or "").strip()
        if not reference.is_file():
            raise FileNotFoundError(reference)
        if not emotion.is_file():
            raise FileNotFoundError(emotion)
        if not text:
            raise ValueError(f"Missing target text: {sample_id}")
        if sha256_file(reference) != raw.get("reference_audio_sha256"):
            raise ValueError(f"Speaker reference hash mismatch: {sample_id}")
        if sha256_file(emotion) != raw.get("emotion_audio_sha256"):
            raise ValueError(f"Emotion reference hash mismatch: {sample_id}")
        if output.suffix.lower() != ".wav" or result.suffix.lower() != ".json":
            raise ValueError(f"Invalid output contract: {sample_id}")
        normalized.append({
            **raw,
            "sample_id": sample_id,
            "reference_audio": str(reference),
            "emotion_audio_prompt": str(emotion),
            "output_file": str(output),
            "result_file": str(result),
            "text": text,
        })
    return manifest, normalized


def reusable_result(sample: dict[str, Any], fingerprint: str) -> dict[str, Any] | None:
    output = Path(sample["output_file"])
    result = Path(sample["result_file"])
    if not output.is_file() or not result.is_file():
        return None
    try:
        record = json.loads(result.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if record.get("sample_id") != sample["sample_id"]:
        return None
    if record.get("sample_fingerprint") != fingerprint:
        return None
    if record.get("audio_sha256") != sha256_file(output):
        return None
    return record


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
        loaded_at = time.perf_counter()
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
        load_seconds = time.perf_counter() - loaded_at
        ready_queue.put({
            "status": "ready",
            "worker_index": worker_index,
            "pid": os.getpid(),
            "load_seconds": load_seconds,
        })

        job_index = 0
        while True:
            task = task_queue.get()
            if task is None:
                break
            sample = task["sample"]
            try:
                output = Path(sample["output_file"])
                result = Path(sample["result_file"])
                output.parent.mkdir(parents=True, exist_ok=True)
                result.parent.mkdir(parents=True, exist_ok=True)
                partial_output = output.with_name(output.stem + ".partial.wav")
                partial_result = result.with_name(result.stem + ".partial.json")
                partial_output.unlink(missing_ok=True)
                partial_result.unlink(missing_ok=True)

                torch.manual_seed(int(sample["seed"]))
                np.random.seed(int(sample["seed"]))
                captured = StringIO()
                started = time.perf_counter()
                with torch.inference_mode(), redirect_stdout(captured):
                    returned = model.infer(
                        spk_audio_prompt=sample["reference_audio"],
                        text=sample["text"],
                        output_path=str(partial_output),
                        emo_audio_prompt=sample["emotion_audio_prompt"],
                        emo_alpha=float(sample["emotion_strength"]),
                        use_random=False,
                        verbose=False,
                        num_beams=1,
                        max_mel_tokens=int(sample.get("generation", {}).get("max_mel_tokens", 600)),
                    )
                generation_seconds = time.perf_counter() - started
                if not partial_output.is_file():
                    raise RuntimeError(f"No audio produced; model returned {returned!r}")
                metrics = audio_metrics(partial_output, len(sample["text"].split()))
                log = captured.getvalue()
                job_index += 1
                audio_sha = sha256_file(partial_output)
                record = {
                    "schema_version": 1,
                    "round_id": config["round_id"],
                    "sample_id": sample["sample_id"],
                    "blind_id": sample["blind_id"],
                    "sample_fingerprint": task["sample_fingerprint"],
                    "model_key": "indextts2",
                    "group": sample["group"],
                    "identity_key": sample["identity_key"],
                    "identity_label": sample["identity_label"],
                    "style": sample["style"],
                    "worker_index": worker_index,
                    "worker_job_index": job_index,
                    "worker_load_seconds": load_seconds,
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
                    "audio_sha256": audio_sha,
                    "output_file": str(output),
                    "runtime_controls": {
                        "device": config["device"],
                        "use_fp16": False,
                        "mps_fast_math": os.getenv("PYTORCH_MPS_FAST_MATH") == "1",
                        "mps_prefer_metal": os.getenv("PYTORCH_MPS_PREFER_METAL") == "1",
                        "num_beams": 1,
                        "greedy_generation": bool(config["greedy"]),
                        "diffusion_steps": int(config["diffusion_steps"]),
                    },
                    "model_log_tail": log[-2000:],
                    "manual_listening_required": True,
                    "production_promotion_allowed": False,
                }
                partial_result.write_text(
                    json.dumps(record, indent=2) + "\n", encoding="utf-8"
                )
                os.replace(partial_output, output)
                os.replace(partial_result, result)
                result_queue.put({"status": "complete", "record": record})
            except BaseException as exc:
                result_queue.put({
                    "status": "error",
                    "sample_id": sample.get("sample_id"),
                    "worker_index": worker_index,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                })
    except BaseException as exc:
        ready_queue.put({
            "status": "error",
            "worker_index": worker_index,
            "error_type": type(exc).__name__,
            "error": str(exc),
        })


def main() -> int:
    args = parse_args()
    if args.workers != 2:
        raise ValueError("Round 1 IndexTTS2 uses exactly two persistent workers")
    if args.device != "mps" or not args.greedy or args.diffusion_steps != 8:
        raise ValueError("Round 1 IndexTTS2 must use MPS, greedy decoding, and eight diffusion steps")

    manifest_path = Path(args.manifest).expanduser().resolve()
    summary_path = Path(args.summary).expanduser().resolve()
    manifest, samples = read_manifest(manifest_path)
    profile = manifest.get("runtime_profile") or {}
    if profile.get("persistent_worker_count") != 2 or profile.get("use_fp16") is not False:
        raise ValueError("Manifest does not specify the accepted FP32 two-worker profile")

    model_dir = Path(args.model_dir).expanduser().resolve()
    aux_root = Path(args.aux_root).expanduser().resolve()
    if not (model_dir / "config.yaml").is_file():
        raise FileNotFoundError(model_dir / "config.yaml")
    for required in (
        aux_root / "w2v-bert-2.0",
        aux_root / "semantic_codec" / "model.safetensors",
        aux_root / "campplus_cn_common.bin",
        aux_root / "bigvgan",
    ):
        if not required.exists():
            raise FileNotFoundError(required)

    config = {
        "round_id": manifest["round_id"],
        "model_dir": str(model_dir),
        "aux_root": str(aux_root),
        "device": args.device,
        "diffusion_steps": args.diffusion_steps,
        "greedy": args.greedy,
    }
    manifest_sha = sha256_file(manifest_path)
    pending = []
    reused = []
    for sample in samples:
        fingerprint = canonical_hash({
            "sample": sample,
            "runtime": config,
            "manifest_sha256": manifest_sha,
        })
        existing = None if args.no_resume else reusable_result(sample, fingerprint)
        if existing is None:
            pending.append({"sample": sample, "sample_fingerprint": fingerprint})
        else:
            reused.append(existing)

    ready = []
    generated = []
    errors = []
    wall_seconds = 0.0
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
            for index in range(1, 3)
        ]
        for process in workers:
            process.start()
        try:
            deadline = time.monotonic() + args.ready_timeout
            while len(ready) < 2:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Timed out loading IndexTTS2 workers")
                item = ready_queue.get(timeout=remaining)
                if item.get("status") != "ready":
                    raise RuntimeError(json.dumps(item, indent=2))
                ready.append(item)

            started = time.perf_counter()
            for task in pending:
                task_queue.put(task)
            for _ in workers:
                task_queue.put(None)
            deadline = time.monotonic() + args.generation_timeout
            while len(generated) + len(errors) < len(pending):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Timed out generating IndexTTS2 Round 1 samples")
                item = result_queue.get(timeout=remaining)
                if item.get("status") == "complete":
                    generated.append(item["record"])
                    if len(generated) % 10 == 0 or len(generated) + len(errors) == len(pending):
                        print(json.dumps({
                            "generated": len(generated),
                            "errors": len(errors),
                            "pending_total": len(pending),
                        }), flush=True)
                else:
                    errors.append(item)
            wall_seconds = time.perf_counter() - started
        except queue.Empty as exc:
            raise TimeoutError("IndexTTS2 worker queue timed out") from exc
        finally:
            for process in workers:
                process.join(timeout=10)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=10)

    all_records = sorted(reused + generated, key=lambda item: item["sample_id"])
    generated_audio_seconds = sum(
        float(item["audio"]["duration_seconds"]) for item in generated
    )
    summary = {
        "schema_version": 1,
        "round_id": manifest["round_id"],
        "purpose": "multimodel_round1_indextts2_persistent_pool",
        "manifest": str(manifest_path),
        "manifest_sha256": manifest_sha,
        "sample_count": len(samples),
        "generated_sample_count": len(generated),
        "reused_sample_count": len(reused),
        "complete_sample_count": len(all_records),
        "error_count": len(errors),
        "errors": errors,
        "persistent_pool": {
            "worker_count": 2,
            "workers_loaded_once": True,
            "workers_process_multiple_jobs": True,
            "ready": sorted(ready, key=lambda item: item["worker_index"]),
            "wall_generation_seconds": wall_seconds,
            "generated_audio_seconds": generated_audio_seconds,
            "aggregate_throughput_rtf": (
                wall_seconds / generated_audio_seconds if generated_audio_seconds else None
            ),
        },
        "runtime_controls": {
            "device": args.device,
            "use_fp16": False,
            "mps_fast_math": True,
            "mps_prefer_metal": True,
            "num_beams": 1,
            "greedy_generation": args.greedy,
            "diffusion_steps": args.diffusion_steps,
        },
        "samples": all_records,
        "temporary_paths_used": False,
        "license_review_complete": False,
        "manual_listening_required": True,
        "production_promotion_allowed": False,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "summary": str(summary_path),
        "complete_sample_count": len(all_records),
        "generated_sample_count": len(generated),
        "reused_sample_count": len(reused),
        "error_count": len(errors),
    }, indent=2))
    if errors:
        raise RuntimeError(json.dumps(errors, indent=2))
    if len(all_records) != len(samples):
        raise RuntimeError(f"Expected {len(samples)} complete samples, found {len(all_records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
