from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from training_sidecar_service import (  # noqa: E402
    sidecar_python_path,
    sidecar_sox_binary_path,
)


DEFAULT_MODEL = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
ARCHITECTURE = "mps_lora_training_merged_mlx_inference_experimental"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def parse_last_json(stdout: str) -> dict[str, Any]:
    for line in reversed(str(stdout or "").splitlines()):
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise RuntimeError("Benchmark command returned no JSON result.")


def sidecar_environment() -> dict[str, str]:
    environment = os.environ.copy()
    sox = sidecar_sox_binary_path(ROOT)
    if not sox.is_file():
        raise RuntimeError(
            "The isolated sidecar SoX executable is not installed."
        )
    environment["PATH"] = (
        str(sox.parent)
        + os.pathsep
        + environment.get("PATH", "")
    )
    environment["ALEXANDRIA_SIDECAR_SOX"] = str(sox)
    if sys.platform == "darwin":
        environment["DYLD_LIBRARY_PATH"] = (
            str(sox.parent.parent / "lib")
            + os.pathsep
            + environment.get("DYLD_LIBRARY_PATH", "")
        )
    return environment


def run_command(
    command: list[str],
    *,
    environment: dict[str, str] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    value = parse_last_json(completed.stdout)
    if completed.returncode != 0:
        raise RuntimeError(
            value.get("error")
            or completed.stderr[-2000:]
            or f"Command exited with {completed.returncode}."
        )
    value["orchestration_elapsed_seconds"] = (
        time.perf_counter() - started
    )
    return value


def dataset_identity(data_dir: Path) -> dict[str, Any]:
    metadata_path = data_dir / "metadata.jsonl"
    reference_text = data_dir / "ref_text.txt"
    if not metadata_path.is_file() or not reference_text.is_file():
        raise RuntimeError(
            "The benchmark dataset requires metadata.jsonl and ref_text.txt."
        )
    audio_files = sorted(
        path
        for path in data_dir.iterdir()
        if path.is_file()
        and path.suffix.casefold()
        in {".wav", ".flac", ".mp3", ".m4a", ".ogg"}
    )
    if not audio_files:
        raise RuntimeError("The benchmark dataset has no reference audio.")
    return {
        "metadata_sha256": sha256_file(metadata_path),
        "reference_text_sha256": sha256_file(reference_text),
        "audio_sha256": [sha256_file(path) for path in audio_files],
        "audio_file_count": len(audio_files),
    }


def hardware() -> dict[str, Any]:
    result = {
        "platform": platform.platform(),
        "machine": platform.machine(),
    }
    if sys.platform == "darwin":
        completed = subprocess.run(
            ["system_profiler", "SPHardwareDataType", "-json"],
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            value = json.loads(completed.stdout)
            entry = value["SPHardwareDataType"][0]
        except Exception:
            entry = {}
        result.update(
            {
                "chip": entry.get("chip_type"),
                "memory": entry.get("physical_memory"),
                "model_identifier": entry.get("machine_model"),
            }
        )
    return result


def run(args: argparse.Namespace) -> dict[str, Any]:
    data_dir = Path(args.data_dir).expanduser().resolve()
    work_dir = Path(args.work_dir).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    if not data_dir.is_dir():
        raise RuntimeError("Benchmark data directory does not exist.")
    if work_dir.exists() and any(work_dir.iterdir()):
        raise RuntimeError("Benchmark work directory must be empty.")
    work_dir.mkdir(parents=True, exist_ok=True)

    sidecar_python = sidecar_python_path(ROOT)
    sidecar_runner = APP / "training_sidecar" / "runner.py"
    mlx_exporter = APP / "training_sidecar" / "mlx_export.py"
    if not sidecar_python.is_file():
        raise RuntimeError("The isolated sidecar environment is not installed.")
    sidecar_env = sidecar_environment()

    adapter_dir = work_dir / "adapter"
    pytorch_audio = work_dir / "pytorch_adapter_inference.wav"
    merged_dir = work_dir / "merged_checkpoint"
    mlx_dir = work_dir / "mlx_model"

    common = [
        "--model-name",
        args.model_name,
        "--device",
        args.device,
    ]
    if args.local_files_only:
        common.append("--local-files-only")

    environment_report = run_command(
        [str(sidecar_python), str(sidecar_runner), "environment"],
        environment=sidecar_env,
        timeout=args.timeout,
    )
    model_probe = run_command(
        [
            str(sidecar_python),
            str(sidecar_runner),
            "model-probe",
            *common,
        ],
        environment=sidecar_env,
        timeout=args.timeout,
    )
    targets = run_command(
        [
            str(sidecar_python),
            str(sidecar_runner),
            "inspect-targets",
            *common,
        ],
        environment=sidecar_env,
        timeout=args.timeout,
    )
    training = run_command(
        [
            str(sidecar_python),
            str(sidecar_runner),
            "train",
            "--mode",
            "lora",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(adapter_dir),
            *common,
            "--epochs",
            str(args.epochs),
            "--max-steps",
            str(args.max_steps),
            "--max-samples",
            str(args.max_samples),
            "--lora-rank",
            str(args.lora_rank),
            "--lora-alpha",
            str(args.lora_alpha),
            "--learning-rate",
            str(args.learning_rate),
            "--max-audio-seconds",
            str(args.max_audio_seconds),
        ],
        environment=sidecar_env,
        timeout=args.timeout,
    )
    pytorch_inference = run_command(
        [
            str(sidecar_python),
            str(sidecar_runner),
            "infer-adapter",
            "--adapter-dir",
            str(adapter_dir),
            "--output-path",
            str(pytorch_audio),
            "--text",
            args.validation_text,
            *common,
            "--max-new-tokens",
            str(args.max_tokens),
        ],
        environment=sidecar_env,
        timeout=args.timeout,
    )
    merge = run_command(
        [
            str(sidecar_python),
            str(sidecar_runner),
            "merge-adapter",
            "--adapter-dir",
            str(adapter_dir),
            "--output-dir",
            str(merged_dir),
            *common,
        ],
        environment=sidecar_env,
        timeout=args.timeout,
    )
    mlx_export = run_command(
        [
            sys.executable,
            str(mlx_exporter),
            "--merged-dir",
            str(merged_dir),
            "--output-dir",
            str(mlx_dir),
            "--validation-text",
            args.validation_text,
            "--neutral-instruction",
            args.neutral_instruction,
            "--expressive-instruction",
            args.expressive_instruction,
            "--q-group-size",
            str(args.q_group_size),
            "--q-bits",
            str(args.q_bits),
            "--max-tokens",
            str(args.max_tokens),
        ],
        timeout=args.timeout,
    )

    training_metrics = training.get("metrics", {})
    step_metrics = training_metrics.get("step_metrics", [])
    result = {
        "schema_version": 1,
        "created_at_utc": utc_timestamp(),
        "repository_head": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip(),
        "architecture": ARCHITECTURE,
        "hardware": hardware(),
        "environment": {
            "sidecar": {
                "status": environment_report.get("status"),
                "platform": environment_report.get("platform"),
                "machine": environment_report.get("machine"),
                "packages": environment_report.get("packages", {}),
                "sox_binary": {
                    key: environment_report.get("sox_binary", {}).get(key)
                    for key in (
                        "available",
                        "version",
                        "version_source",
                    )
                },
                "mps_built": environment_report.get("mps_built"),
                "mps_available": environment_report.get("mps_available"),
                "cuda_available": environment_report.get("cuda_available"),
                "default_device": environment_report.get("default_device"),
            },
            "main_packages": {
                "mlx": package_version("mlx"),
                "mlx-audio": package_version("mlx-audio"),
                "transformers": package_version("transformers"),
            },
        },
        "dataset": dataset_identity(data_dir),
        "validation_text_sha256": sha256_text(args.validation_text),
        "neutral_instruction_sha256": sha256_text(
            args.neutral_instruction
        ),
        "expressive_instruction_sha256": sha256_text(
            args.expressive_instruction
        ),
        "model_probe": model_probe,
        "target_probe": {
            "module_count": targets.get("module_count"),
            "target_suffixes": targets.get("target_suffixes"),
            "actual_module_names_sha256": sha256_text(
                "\n".join(targets.get("actual_module_names", []))
            ),
        },
        "training": {
            "mode": training_metrics.get("mode"),
            "lora_rank": args.lora_rank,
            "lora_alpha": args.lora_alpha,
            "trainable_parameters": training_metrics.get(
                "trainable_parameters"
            ),
            "total_talker_parameters": training_metrics.get(
                "total_talker_parameters"
            ),
            "trainable_percent": training_metrics.get(
                "trainable_percent"
            ),
            "steps_completed": training_metrics.get("steps_completed"),
            "training_seconds": training_metrics.get("training_seconds"),
            "step_metrics": step_metrics,
            "reference_audio": {
                key: training_metrics.get("reference_audio", {}).get(key)
                for key in (
                    "sample_rate",
                    "channels",
                    "frames",
                    "duration_seconds",
                    "sha256",
                )
            },
            "adapter_model_sha256": sha256_file(
                adapter_dir / "adapter_model.safetensors"
            ),
            "artifact_manifest_sha256": sha256_file(
                adapter_dir / "sidecar_artifact.json"
            ),
        },
        "pytorch_adapter_inference": {
            key: value
            for key, value in pytorch_inference.items()
            if key
            not in {
                "output_path",
                "orchestration_elapsed_seconds",
            }
        },
        "merge": {
            "device": merge.get("device"),
            "model_load_seconds": merge.get("model_load_seconds"),
            "merge_total_seconds": merge.get("merge_total_seconds"),
            "size_bytes": merge.get("size_bytes"),
            "production_assignment_supported": merge.get(
                "production_assignment_supported"
            ),
        },
        "mlx_export": {
            key: value
            for key, value in mlx_export.items()
            if key not in {"output_dir", "orchestration_elapsed_seconds"}
        },
        "shared_runtime_lora_supported": False,
        "experimental_sidecar_training_supported": True,
        "direct_pytorch_inference_performant": bool(
            pytorch_inference.get("real_time_factor", float("inf")) <= 1.05
        ),
        "merged_mlx_inference_technically_validated": bool(
            mlx_export.get("technical_validation_passed") is True
        ),
        "production_assignment_supported": False,
        "quality_review": {
            "manual_audio_review_required": True,
            "manual_audio_review_status": "pending",
            "multi_sample_multi_epoch_validation_required": True,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if args.cleanup_large_intermediates:
        shutil.rmtree(merged_dir, ignore_errors=True)
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--data-dir", required=True)
    result.add_argument("--work-dir", required=True)
    result.add_argument("--output", required=True)
    result.add_argument("--model-name", default=DEFAULT_MODEL)
    result.add_argument(
        "--device",
        choices=("mps", "cuda", "cpu", "auto"),
        default="mps",
    )
    result.add_argument("--epochs", type=int, default=1)
    result.add_argument("--max-steps", type=int, default=1)
    result.add_argument("--max-samples", type=int, default=1)
    result.add_argument("--lora-rank", type=int, default=8)
    result.add_argument("--lora-alpha", type=int, default=16)
    result.add_argument("--learning-rate", type=float, default=5e-6)
    result.add_argument("--max-audio-seconds", type=float, default=30.0)
    result.add_argument("--q-group-size", type=int, default=64)
    result.add_argument("--q-bits", type=int, default=8)
    result.add_argument("--max-tokens", type=int, default=600)
    result.add_argument("--timeout", type=float, default=300.0)
    result.add_argument("--local-files-only", action="store_true")
    result.add_argument("--cleanup-large-intermediates", action="store_true")
    result.add_argument(
        "--validation-text",
        default=(
            "You have exactly one chance to tell me the truth before this "
            "becomes considerably more unpleasant."
        ),
    )
    result.add_argument(
        "--neutral-instruction",
        default=(
            "Calm, measured, conversational delivery. Preserve the original "
            "speaker identity and accent."
        ),
    )
    result.add_argument(
        "--expressive-instruction",
        default=(
            "Controlled anger, hard consonants, restrained intensity, "
            "slightly faster pace. Preserve the original speaker identity "
            "and accent."
        ),
    )
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        value = run(args)
        print(json.dumps(value, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
