from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
PYTHON = ROOT / "app" / "env" / "bin" / "python"
WORKER_MARKER = "WORKER_JSON="


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def monitor_peak_rss(stop: list[bool], peak: list[int]) -> None:
    import psutil

    process = psutil.Process()
    while not stop[0]:
        peak[0] = max(peak[0], process.memory_info().rss)
        time.sleep(0.05)


def audio_info(path: str | Path) -> dict[str, Any]:
    import soundfile as sf

    info = sf.info(str(path))
    return {
        "duration_seconds": info.duration,
        "sample_rate": info.samplerate,
        "channels": info.channels,
        "frames": info.frames,
    }


def worker_root(name: str) -> Path:
    path = Path(tempfile.gettempdir()) / f"alexandria_phase22_{name}"
    shutil.rmtree(path, ignore_errors=True)
    (path / "app").mkdir(parents=True)
    return path


def worker_design(reference_output: str | None) -> dict[str, Any]:
    import mlx_backend

    root = worker_root("design")
    mlx_backend.__file__ = str(root / "app" / "mlx_backend.py")
    backend = mlx_backend.MLXBackend(language="English")
    stop = [False]
    import psutil

    peak = [psutil.Process().memory_info().rss]
    monitor = threading.Thread(
        target=monitor_peak_rss,
        args=(stop, peak),
        daemon=True,
    )
    monitor.start()
    description = (
        "An older, quick-minded traveler with a warm weathered tenor, "
        "precise diction, restrained humor, and elastic emotional timing."
    )
    first_text = "Tell me what happened here."
    second_text = "We should leave before the doors close."
    started = time.perf_counter()
    first_path, sample_rate = backend.generate_design_preview(
        description,
        first_text,
        seed=314159,
    )
    cold_seconds = time.perf_counter() - started
    started = time.perf_counter()
    second_path, _ = backend.generate_design_preview(
        description,
        second_text,
        seed=314159,
    )
    warm_seconds = time.perf_counter() - started
    stop[0] = True
    monitor.join(timeout=1)
    first = audio_info(first_path)
    second = audio_info(second_path)
    if reference_output:
        shutil.copy2(first_path, reference_output)
    result = {
        "path": "voice_design",
        "cold_seconds": cold_seconds,
        "warm_seconds": warm_seconds,
        "first_audio_seconds": first["duration_seconds"],
        "second_audio_seconds": second["duration_seconds"],
        "cold_rtf": cold_seconds / first["duration_seconds"],
        "warm_rtf": warm_seconds / second["duration_seconds"],
        "peak_rss_gib": peak[0] / (1024**3),
        "sample_rate": sample_rate,
        "seed": 314159,
    }
    shutil.rmtree(root, ignore_errors=True)
    return result


def worker_clone(reference_path: str) -> dict[str, Any]:
    from mlx_backend import MLXBackend

    root = worker_root("clone")
    backend = MLXBackend(language="English")
    stop = [False]
    import psutil

    peak = [psutil.Process().memory_info().rss]
    monitor = threading.Thread(
        target=monitor_peak_rss,
        args=(stop, peak),
        daemon=True,
    )
    monitor.start()
    first_path = root / "clone_1.wav"
    second_path = root / "clone_2.wav"
    ref_text = "Tell me what happened here."
    started = time.perf_counter()
    backend.generate_clone(
        "We should leave before the doors close.",
        reference_path,
        ref_text,
        str(first_path),
    )
    cold_seconds = time.perf_counter() - started
    started = time.perf_counter()
    backend.generate_clone(
        "There is more to this place than you realize.",
        reference_path,
        ref_text,
        str(second_path),
    )
    warm_seconds = time.perf_counter() - started
    stop[0] = True
    monitor.join(timeout=1)
    first = audio_info(first_path)
    second = audio_info(second_path)
    result = {
        "path": "voice_design_generated_clone",
        "cold_seconds": cold_seconds,
        "warm_seconds": warm_seconds,
        "first_audio_seconds": first["duration_seconds"],
        "second_audio_seconds": second["duration_seconds"],
        "cold_rtf": cold_seconds / first["duration_seconds"],
        "warm_rtf": warm_seconds / second["duration_seconds"],
        "peak_rss_gib": peak[0] / (1024**3),
        "sample_rate": first["sample_rate"],
    }
    shutil.rmtree(root, ignore_errors=True)
    return result


def worker_custom() -> dict[str, Any]:
    from mlx_backend import MLXBackend

    root = worker_root("custom")
    backend = MLXBackend(language="English")
    stop = [False]
    import psutil

    peak = [psutil.Process().memory_info().rss]
    monitor = threading.Thread(
        target=monitor_peak_rss,
        args=(stop, peak),
        daemon=True,
    )
    monitor.start()
    first_path = root / "custom_1.wav"
    second_path = root / "custom_2.wav"
    started = time.perf_counter()
    backend.generate_custom(
        "Tell me what happened here.",
        "Alert curiosity, measured pace.",
        "Ryan",
        str(first_path),
    )
    cold_seconds = time.perf_counter() - started
    started = time.perf_counter()
    backend.generate_custom(
        "We should leave before the doors close.",
        "Quiet urgency, restrained concern.",
        "Ryan",
        str(second_path),
    )
    warm_seconds = time.perf_counter() - started
    stop[0] = True
    monitor.join(timeout=1)
    first = audio_info(first_path)
    second = audio_info(second_path)
    result = {
        "path": "custom_voice",
        "voice": "Ryan",
        "cold_seconds": cold_seconds,
        "warm_seconds": warm_seconds,
        "first_audio_seconds": first["duration_seconds"],
        "second_audio_seconds": second["duration_seconds"],
        "cold_rtf": cold_seconds / first["duration_seconds"],
        "warm_rtf": warm_seconds / second["duration_seconds"],
        "peak_rss_gib": peak[0] / (1024**3),
        "sample_rate": first["sample_rate"],
    }
    shutil.rmtree(root, ignore_errors=True)
    return result


def worker_accent() -> dict[str, Any]:
    import mlx_backend

    root = worker_root("accent")
    mlx_backend.__file__ = str(root / "app" / "mlx_backend.py")
    backend = mlx_backend.MLXBackend(language="English")
    stop = [False]
    import psutil

    peak = [psutil.Process().memory_info().rss]
    monitor = threading.Thread(
        target=monitor_peak_rss,
        args=(stop, peak),
        daemon=True,
    )
    monitor.start()
    description = (
        "An older diplomat with a warm, dry baritone, restrained authority, "
        "and a soft French accent."
    )
    started = time.perf_counter()
    path, sample_rate = backend.generate_design_preview(
        description,
        "We should leave before the doors close.",
        seed=271828,
    )
    elapsed = time.perf_counter() - started
    stop[0] = True
    monitor.join(timeout=1)
    info = audio_info(path)
    result = {
        "path": "accent_pipeline",
        "accent": "French",
        "elapsed_seconds": elapsed,
        "audio_seconds": info["duration_seconds"],
        "rtf": elapsed / info["duration_seconds"],
        "peak_rss_gib": peak[0] / (1024**3),
        "sample_rate": sample_rate,
        "design_model_loaded": "design" in backend._models,
        "clone_model_loaded": "clone" in backend._models,
        "seed": 271828,
    }
    shutil.rmtree(root, ignore_errors=True)
    return result


def worker_batch() -> dict[str, Any]:
    from mlx_backend import MLXBackend

    root = worker_root("batch")
    root.mkdir(parents=True, exist_ok=True)
    backend = MLXBackend(language="English")
    stop = [False]
    import psutil

    peak = [psutil.Process().memory_info().rss]
    monitor = threading.Thread(
        target=monitor_peak_rss,
        args=(stop, peak),
        daemon=True,
    )
    monitor.start()
    started = time.perf_counter()
    backend._model("custom")
    model_load_seconds = time.perf_counter() - started
    chunks = [
        {
            "index": 0,
            "speaker": "A",
            "text": "Wait.",
            "instruct": "Sharp warning.",
        },
        {
            "index": 1,
            "speaker": "A",
            "text": "Tell me what happened before the doors closed.",
            "instruct": "Alert curiosity.",
        },
        {
            "index": 2,
            "speaker": "A",
            "text": (
                "There are worlds beyond this place, and some of them have "
                "been waiting a very long time for us to notice them."
            ),
            "instruct": "Measured wonder, restrained urgency.",
        },
    ]
    voice_config = {"A": {"voice": "Ryan"}}
    started = time.perf_counter()
    batch_result = backend.generate_custom_batch(
        chunks,
        voice_config,
        str(root),
    )
    elapsed = time.perf_counter() - started
    items = []
    for chunk in chunks:
        info = audio_info(root / f"temp_batch_{chunk['index']}.wav")
        items.append(
            {
                "index": chunk["index"],
                "text_chars": len(chunk["text"]),
                "audio_seconds": info["duration_seconds"],
            }
        )
    stop[0] = True
    monitor.join(timeout=1)
    total_audio = sum(item["audio_seconds"] for item in items)
    result = {
        "path": "custom_voice_mixed_length_batch",
        "implementation": "sequential_loop",
        "model_load_seconds": model_load_seconds,
        "elapsed_seconds": elapsed,
        "total_audio_seconds": total_audio,
        "aggregate_rtf": elapsed / total_audio,
        "peak_rss_gib": peak[0] / (1024**3),
        "items": items,
        "result": batch_result,
    }
    shutil.rmtree(root, ignore_errors=True)
    return result


def worker_qwen_import() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        from qwen_tts import Qwen3TTSModel  # noqa: F401

        return {
            "imported": True,
            "seconds": time.perf_counter() - started,
            "error_type": None,
            "error": None,
        }
    except Exception as exc:
        return {
            "imported": False,
            "seconds": time.perf_counter() - started,
            "error_type": type(exc).__name__,
            "error": str(exc)[:2000],
        }


def run_worker(args: argparse.Namespace) -> int:
    if args.worker == "design":
        result = worker_design(args.reference_output)
    elif args.worker == "clone":
        result = worker_clone(args.reference_path)
    elif args.worker == "custom":
        result = worker_custom()
    elif args.worker == "accent":
        result = worker_accent()
    elif args.worker == "batch":
        result = worker_batch()
    elif args.worker == "qwen_import":
        result = worker_qwen_import()
    else:
        raise ValueError(args.worker)
    print(WORKER_MARKER + json.dumps(result, sort_keys=True))
    return 0


def invoke_worker(
    worker: str,
    *,
    reference_output: Path | None = None,
    reference_path: Path | None = None,
    offline: bool = False,
) -> dict[str, Any]:
    command = [
        str(PYTHON),
        str(Path(__file__).resolve()),
        "--worker",
        worker,
    ]
    if reference_output is not None:
        command.extend(["--reference-output", str(reference_output)])
    if reference_path is not None:
        command.extend(["--reference-path", str(reference_path)])
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(APP)
    if offline:
        environment["HF_HUB_OFFLINE"] = "1"
        environment["TRANSFORMERS_OFFLINE"] = "1"
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
    )
    combined = completed.stdout + "\n" + completed.stderr
    marker_line = next(
        (
            line
            for line in reversed(combined.splitlines())
            if line.startswith(WORKER_MARKER)
        ),
        None,
    )
    if marker_line is None:
        raise RuntimeError(
            f"{worker} benchmark did not return JSON.\n{combined[-4000:]}"
        )
    result = json.loads(marker_line[len(WORKER_MARKER) :])
    result["worker_exit_code"] = completed.returncode
    return result


def system_hardware() -> dict[str, Any]:
    hardware = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
    }
    try:
        output = subprocess.run(
            ["system_profiler", "SPHardwareDataType", "-json"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        value = json.loads(output.stdout)
        rows = value.get("SPHardwareDataType", [])
        if rows:
            row = rows[0]
            hardware.update(
                {
                    "model_name": row.get("machine_name"),
                    "model_identifier": row.get("machine_model"),
                    "chip": row.get("chip_type"),
                    "memory": row.get("physical_memory"),
                }
            )
    except Exception as exc:
        hardware["system_profiler_error"] = str(exc)
    return hardware


def mps_probe() -> dict[str, Any]:
    import torch

    result = {
        "torch_version": torch.__version__,
        "mps_built": torch.backends.mps.is_built(),
        "mps_available": torch.backends.mps.is_available(),
        "cuda_available": torch.cuda.is_available(),
    }
    if not result["mps_available"]:
        result["basic_autograd"] = False
        return result
    try:
        device = torch.device("mps")
        x = torch.randn(256, 256, device=device, requires_grad=True)
        weight = torch.randn(256, 256, device=device, requires_grad=True)
        torch.mps.synchronize()
        started = time.perf_counter()
        loss = (x @ weight).square().mean()
        loss.backward()
        torch.mps.synchronize()
        result.update(
            {
                "basic_autograd": True,
                "seconds": time.perf_counter() - started,
                "gradient_finite": bool(
                    torch.isfinite(weight.grad).all().cpu()
                ),
            }
        )
    except Exception as exc:
        result.update(
            {
                "basic_autograd": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
    return result


def microbenchmarks() -> dict[str, Any]:
    sys.path.insert(0, str(APP))
    from generation_state import fingerprint_value

    value = [
        {
            "speaker": "NARRATOR" if index % 3 == 0 else "CHARACTER",
            "text": f"Measured script entry {index} with stable content.",
            "instruct": "Neutral narration.",
        }
        for index in range(1000)
    ]
    iterations = 100
    started = time.perf_counter()
    last = None
    for _ in range(iterations):
        last = fingerprint_value(value)
    elapsed = time.perf_counter() - started
    return {
        "script_entries": len(value),
        "hash_iterations": iterations,
        "total_seconds": elapsed,
        "average_seconds": elapsed / iterations,
        "fingerprint": last,
    }


def existing_llm_benchmark() -> dict[str, Any] | None:
    results = ROOT / "benchmarks" / "results"
    if not results.exists():
        return None
    candidates = []
    for path in results.rglob("*.json"):
        if "phase22_apple_silicon" in path.name:
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict):
            continue
        summary = value.get("runner_summary")
        if not isinstance(summary, dict):
            summary = value
        required = {
            "schema_success_rate",
            "script_audit_pass_rate",
            "review_audit_pass_rate",
        }
        if not required.issubset(summary):
            continue
        output_rate = summary.get(
            "output_tokens_per_second",
            summary.get("average_tokens_per_second"),
        )
        case_seconds = summary.get(
            "average_case_elapsed_seconds",
            summary.get("average_case_seconds"),
        )
        if not isinstance(output_rate, (int, float)) or not isinstance(
            case_seconds,
            (int, float),
        ):
            continue
        normalized = {
            "source": str(path.relative_to(ROOT)),
            "model_name": value.get("model_name"),
            "schema_success_rate": summary["schema_success_rate"],
            "script_audit_pass_rate": summary[
                "script_audit_pass_rate"
            ],
            "review_audit_pass_rate": summary[
                "review_audit_pass_rate"
            ],
            "average_tokens_per_second": float(output_rate),
            "average_case_seconds": float(case_seconds),
            "case_count": value.get(
                "case_count",
                summary.get("case_run_count"),
            ),
            "run_count": value.get(
                "case_run_count",
                value.get("run_count"),
            ),
        }
        candidates.append((path.stat().st_mtime, path, normalized))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[2]


def package_environment() -> dict[str, Any]:
    names = [
        "qwen-tts",
        "transformers",
        "torch",
        "peft",
        "accelerate",
        "mlx",
        "mlx-audio",
        "soundfile",
        "librosa",
    ]
    return {
        "python": sys.version,
        "packages": {name: package_version(name) for name in names},
        "sox_available": shutil.which("sox") is not None,
    }


def run_parent(output: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="alexandria-phase22-") as temp:
        reference = Path(temp) / "reference.wav"
        design = invoke_worker(
            "design",
            reference_output=reference,
        )
        clone = invoke_worker(
            "clone",
            reference_path=reference,
        )
        custom = invoke_worker("custom")
        accent = invoke_worker("accent")
        batch = invoke_worker("batch")
        qwen_import = invoke_worker("qwen_import", offline=True)
    environment = package_environment()
    transformers = environment["packages"]["transformers"]
    qwen_tts = environment["packages"]["qwen-tts"]
    mlx_audio = environment["packages"]["mlx-audio"]
    blockers = [
        "qwen-tts 0.1.1 requires Transformers 4.57.3 while mlx-audio 0.4.5 requires Transformers 5.5 through 5.12 in the same environment.",
        "qwen_tts import fails before checkpoint loading against the installed Transformers API.",
        "SoX is absent from the Alexandria environment.",
        "The official PyTorch Qwen3-TTS Base checkpoint is not cached locally.",
        "The MLX backend has no LoRA training or adapter-inference implementation.",
    ]
    result = {
        "schema_version": 1,
        "created_at_utc": utc_now(),
        "repository_head": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip(),
        "hardware": system_hardware(),
        "environment": environment,
        "mps_probe": mps_probe(),
        "qwen_tts_import_probe": qwen_import,
        "llm_measurement": existing_llm_benchmark(),
        "microbenchmarks": {
            "script_fingerprinting": microbenchmarks(),
        },
        "tts_measurements": {
            "voice_design": design,
            "voice_design_generated_clone": clone,
            "custom_voice": custom,
            "accent_pipeline": accent,
            "mixed_length_custom_batch": batch,
        },
        "quality_comparison_scope": {
            "voice_design": "generated and measured",
            "voice_design_generated_clone": "generated and measured",
            "custom_voice": "generated and measured",
            "lora": "not runnable; excluded from subjective preference claims",
            "user_preference": "not collected",
        },
        "stable_lora_outcome": "unsupported",
        "stable_lora_reason": (
            "The first stable Apple Silicon release must not expose LoRA training or adapter inference as supported."
        ),
        "lora_blockers": blockers,
        "version_observations": {
            "installed_transformers": transformers,
            "installed_qwen_tts": qwen_tts,
            "installed_mlx_audio": mlx_audio,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default=str(
            ROOT
            / "benchmarks"
            / "results"
            / (
                datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                + "_phase22_apple_silicon.json"
            )
        ),
    )
    parser.add_argument(
        "--worker",
        choices=(
            "design",
            "clone",
            "custom",
            "accent",
            "batch",
            "qwen_import",
        ),
    )
    parser.add_argument("--reference-output")
    parser.add_argument("--reference-path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.worker:
        return run_worker(args)
    result = run_parent(Path(args.output))
    print(
        json.dumps(
            {
                "output": args.output,
                "stable_lora_outcome": result["stable_lora_outcome"],
                "tts_paths": list(result["tts_measurements"]),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
