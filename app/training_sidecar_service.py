from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from generation_state import atomic_json_write, fingerprint_value
from instruction_propagation import (
    InstructionPropagationError,
    build_instruction_propagation_contract,
    validate_instruction_propagation_contract,
)
from model_registry import model_spec


SIDECAR_SCHEMA_VERSION = 1
SIDECAR_RUNTIME_DIRNAME = "training_sidecar_runtime"
SIDECAR_JOBS_DIRNAME = "jobs"
SIDECAR_IMPORTS_DIRNAME = "imports"
SIDECAR_JOB_ID_RE = re.compile(r"sidecar_[0-9a-f]{24}")
SIDECAR_IMPORT_ID_RE = re.compile(r"sidecar_import_[0-9a-f]{24}")
LORA_ARTIFACT_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{2,63}")
MLX_EXPORT_MANIFEST = "mlx_export_manifest.json"
SIDECAR_ACTIONS = {
    "setup",
    "environment",
    "model_probe",
    "inspect_targets",
    "train_sft",
    "train_lora",
    "merge_lora",
    "export_mlx",
}
SIDECAR_ARTIFACT_FORMATS = {
    "official_sft_full_checkpoint",
    "peft_lora_adapter",
    "merged_mlx_qwen_checkpoint",
}
_SIDECAR_LOCK = threading.RLock()


class TrainingSidecarError(RuntimeError):
    pass


class TrainingSidecarValidationError(TrainingSidecarError):
    pass


class TrainingSidecarConflictError(TrainingSidecarError):
    pass


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sidecar_source_dir(root_dir: str | Path) -> Path:
    return Path(root_dir).expanduser().resolve() / "app" / "training_sidecar"


def sidecar_environment_dir(root_dir: str | Path) -> Path:
    return sidecar_source_dir(root_dir) / "env"


def sidecar_python_path(root_dir: str | Path) -> Path:
    environment = sidecar_environment_dir(root_dir)
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def sidecar_sox_environment_dir(root_dir: str | Path) -> Path:
    return sidecar_source_dir(root_dir) / "sox_env"


def sidecar_sox_binary_path(root_dir: str | Path) -> Path:
    environment = sidecar_sox_environment_dir(root_dir)
    if os.name == "nt":
        return environment / "Library" / "bin" / "sox.exe"
    return environment / "bin" / "sox"


def _conda_executable(root_dir: str | Path) -> str:
    configured = os.environ.get("CONDA_EXE")
    if configured and Path(configured).is_file():
        return configured
    discovered = shutil.which("conda")
    if discovered:
        return discovered
    root = Path(root_dir).expanduser().resolve()
    pinokio_candidate = root.parent.parent / "bin" / "miniforge" / "bin" / "conda"
    if pinokio_candidate.is_file():
        return str(pinokio_candidate)
    return "conda"


def sidecar_runtime_dir(root_dir: str | Path) -> Path:
    return Path(root_dir).expanduser().resolve() / SIDECAR_RUNTIME_DIRNAME


def sidecar_jobs_dir(root_dir: str | Path) -> Path:
    return sidecar_runtime_dir(root_dir) / SIDECAR_JOBS_DIRNAME


def sidecar_imports_dir(root_dir: str | Path) -> Path:
    return sidecar_runtime_dir(root_dir) / SIDECAR_IMPORTS_DIRNAME


def sidecar_job_path(root_dir: str | Path, job_id: str) -> Path:
    if not isinstance(job_id, str) or SIDECAR_JOB_ID_RE.fullmatch(job_id) is None:
        raise TrainingSidecarValidationError("Sidecar job ID is invalid.")
    return sidecar_jobs_dir(root_dir) / f"{job_id}.json"


def _safe_relative_path(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise TrainingSidecarValidationError(f"{label} must be a path.")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise TrainingSidecarValidationError(
            f"{label} must be a safe project-relative path."
        )
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise TrainingSidecarValidationError(
            f"{label} must remain inside the project root."
        ) from exc
    return resolved


def _tail(text: str, limit: int = 12000) -> str:
    value = text if isinstance(text, str) else str(text or "")
    return value[-limit:]


def _json_from_stdout(stdout: str) -> dict[str, Any] | None:
    for line in reversed(str(stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _setup_commands(root_dir: str | Path) -> list[list[str]]:
    environment = sidecar_environment_dir(root_dir)
    sox_environment = sidecar_sox_environment_dir(root_dir)
    requirements = sidecar_source_dir(root_dir) / "requirements.txt"
    python = sidecar_python_path(root_dir)
    conda = _conda_executable(root_dir)
    return [
        [sys.executable, "-m", "venv", str(environment)],
        [str(python), "-m", "pip", "install", "--upgrade", "pip"],
        [str(python), "-m", "pip", "install", "-r", str(requirements)],
        [
            conda,
            "create",
            "--yes",
            "--prefix",
            str(sox_environment),
            "--channel",
            "conda-forge",
            "sox",
        ],
    ]


def _runner_command(
    *,
    root_dir: str | Path,
    action: str,
    payload: dict[str, Any],
) -> list[str]:
    python = sidecar_python_path(root_dir)
    runner = sidecar_source_dir(root_dir) / "runner.py"
    if not python.is_file():
        raise TrainingSidecarConflictError(
            "The isolated training sidecar environment is not installed."
        )
    if action == "environment":
        return [str(python), str(runner), "environment"]
    model_name = str(
        payload.get(
            "model_name",
            model_spec("pytorch_qwen_base").repo_id,
        )
    )
    device = str(payload.get("device", "auto"))
    local_only = bool(payload.get("local_files_only", False))
    if action in {"model_probe", "inspect_targets"}:
        command = [
            str(python),
            str(runner),
            "model-probe" if action == "model_probe" else "inspect-targets",
            "--model-name",
            model_name,
            "--device",
            device,
        ]
        if action == "inspect_targets":
            command.extend(
                [
                    "--lora-target-profile",
                    str(payload.get("lora_target_profile", "attention_mlp")),
                ]
            )
        if local_only:
            command.append("--local-files-only")
        return command
    root = Path(root_dir).expanduser().resolve()
    if action == "merge_lora":
        adapter_dir = _safe_relative_path(
            root,
            payload.get("adapter_dir"),
            "LoRA adapter path",
        )
        output_dir = _safe_relative_path(
            root,
            payload.get("output_dir"),
            "Merged checkpoint path",
        )
        command = [
            str(python),
            str(runner),
            "merge-adapter",
            "--adapter-dir",
            str(adapter_dir),
            "--output-dir",
            str(output_dir),
            "--model-name",
            model_name,
            "--device",
            device,
        ]
        if local_only:
            command.append("--local-files-only")
        return command
    if action == "export_mlx":
        merged_dir = _safe_relative_path(
            root,
            payload.get("merged_dir"),
            "Merged checkpoint path",
        )
        output_dir = _safe_relative_path(
            root,
            payload.get("output_dir"),
            "MLX output path",
        )
        exporter = sidecar_source_dir(root) / "mlx_export.py"
        command = [
            sys.executable,
            str(exporter),
            "--merged-dir",
            str(merged_dir),
            "--output-dir",
            str(output_dir),
            "--validation-text",
            str(
                payload.get(
                    "validation_text",
                    "You have exactly one chance to tell me the truth before this becomes considerably more unpleasant.",
                )
            ),
            "--neutral-instruction",
            str(
                payload.get(
                    "neutral_instruction",
                    "Calm, measured, conversational delivery. Preserve the original speaker identity and accent.",
                )
            ),
            "--expressive-instruction",
            str(
                payload.get(
                    "expressive_instruction",
                    "Controlled anger, hard consonants, restrained intensity, slightly faster pace. Preserve the original speaker identity and accent.",
                )
            ),
            "--q-group-size",
            str(int(payload.get("q_group_size", 64))),
            "--q-bits",
            str(int(payload.get("q_bits", 8))),
            "--max-tokens",
            str(int(payload.get("max_tokens", 1200))),
        ]
        if bool(payload.get("cleanup_merged", False)):
            command.append("--cleanup-merged")
        return command
    if action not in {"train_sft", "train_lora"}:
        raise TrainingSidecarValidationError(
            f"Unsupported sidecar action: {action!r}."
        )
    data_dir = _safe_relative_path(root, payload.get("data_dir"), "Dataset path")
    output_dir = _safe_relative_path(root, payload.get("output_dir"), "Output path")
    resume_from = None
    if payload.get("resume_from") is not None:
        resume_from = _safe_relative_path(
            root,
            payload.get("resume_from"),
            "Training resume checkpoint",
        )
    command = [
        str(python),
        str(runner),
        "train",
        "--mode",
        "sft" if action == "train_sft" else "lora",
        "--data-dir",
        str(data_dir),
        "--output-dir",
        str(output_dir),
        "--model-name",
        model_name,
        "--device",
        device,
        "--epochs",
        str(int(payload.get("epochs", 1))),
        "--learning-rate",
        str(float(payload.get("learning_rate", 5e-6))),
        "--gradient-accumulation-steps",
        str(int(payload.get("gradient_accumulation_steps", 1))),
        "--language",
        str(payload.get("language", "english")),
        "--max-audio-seconds",
        str(float(payload.get("max_audio_seconds", 30.0))),
        "--lora-target-profile",
        str(payload.get("lora_target_profile", "attention_mlp")),
        "--validation-fraction",
        str(float(payload.get("validation_fraction", 0.1))),
        "--seed",
        str(int(payload.get("seed", 1337))),
        "--instruction-mode",
        str(payload.get("instruction_mode", "identity_only")),
    ]
    optional_ints = {
        "max_steps": "--max-steps",
        "max_samples": "--max-samples",
        "lora_rank": "--lora-rank",
        "lora_alpha": "--lora-alpha",
    }
    for key, flag in optional_ints.items():
        value = payload.get(key)
        if value is not None:
            command.extend([flag, str(int(value))])
    if resume_from is not None:
        command.extend(["--resume-from", str(resume_from)])
    if not bool(payload.get("checkpoint_every_epoch", True)):
        command.append("--no-checkpoint-every-epoch")
    if local_only:
        command.append("--local-files-only")
    return command


def build_sidecar_status(root_dir: str | Path) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    source = sidecar_source_dir(root)
    environment = sidecar_environment_dir(root)
    python = sidecar_python_path(root)
    sox_environment = sidecar_sox_environment_dir(root)
    sox_binary = sidecar_sox_binary_path(root)
    requirements = source / "requirements.txt"
    runner = source / "runner.py"
    jobs = []
    jobs_dir = sidecar_jobs_dir(root)
    if jobs_dir.is_dir():
        for path in sorted(
            jobs_dir.glob("sidecar_*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )[:20]:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                value = {
                    "job_id": path.stem,
                    "status": "invalid",
                    "error": "Job record could not be read.",
                }
            jobs.append(value)
    imports = []
    imports_dir = sidecar_imports_dir(root)
    if imports_dir.is_dir():
        for path in sorted(imports_dir.glob("sidecar_import_*")):
            manifest = path / "import_manifest.json"
            if not manifest.is_file():
                continue
            try:
                imports.append(json.loads(manifest.read_text(encoding="utf-8")))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                imports.append(
                    {
                        "import_id": path.name,
                        "status": "invalid",
                        "error": "Import manifest could not be read.",
                    }
                )
    environment_report = None
    for job in jobs:
        if job.get("action") == "environment" and job.get("status") == "completed":
            environment_report = job.get("result")
            break
    return {
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "experimental": True,
        "production_assignment_supported": False,
        "source_available": requirements.is_file() and runner.is_file(),
        "environment_exists": environment.is_dir(),
        "python_available": python.is_file(),
        "requirements_path": requirements.relative_to(root).as_posix(),
        "environment_path": environment.relative_to(root).as_posix(),
        "sox_environment_path": sox_environment.relative_to(root).as_posix(),
        "sox_binary_path": sox_binary.relative_to(root).as_posix(),
        "sox_binary_available": sox_binary.is_file(),
        "runtime_path": sidecar_runtime_dir(root).relative_to(root).as_posix(),
        "environment_report": environment_report,
        "jobs": jobs,
        "imports": imports,
    }


def create_sidecar_job(
    *,
    root_dir: str | Path,
    action: str,
    payload: dict[str, Any] | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    if action not in SIDECAR_ACTIONS:
        raise TrainingSidecarValidationError(
            f"Unsupported sidecar action: {action!r}."
        )
    normalized_payload = copy.deepcopy(payload or {})
    identity = {
        "action": action,
        "payload": normalized_payload,
        "created_at_utc": created_at_utc or utc_timestamp(),
        "nonce": os.urandom(16).hex(),
    }
    job_id = "sidecar_" + fingerprint_value(identity)[:24]
    record = {
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "job_id": job_id,
        "action": action,
        "status": "queued",
        "payload": normalized_payload,
        "command": None,
        "created_at_utc": identity["created_at_utc"],
        "started_at_utc": None,
        "finished_at_utc": None,
        "return_code": None,
        "stdout_tail": "",
        "stderr_tail": "",
        "result": None,
        "error": None,
    }
    path = sidecar_job_path(root_dir, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(record, path)
    return record


def read_sidecar_job(
    *,
    root_dir: str | Path,
    job_id: str,
) -> dict[str, Any]:
    path = sidecar_job_path(root_dir, job_id)
    if not path.is_file():
        raise TrainingSidecarValidationError(
            f"Sidecar job {job_id!r} was not found."
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TrainingSidecarValidationError(
            f"Sidecar job could not be read: {exc}"
        ) from exc
    if not isinstance(value, dict) or value.get("job_id") != job_id:
        raise TrainingSidecarValidationError(
            "Sidecar job record is invalid."
        )
    return value


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: float | None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> subprocess.CompletedProcess:
    environment = os.environ.copy()
    sox_binary = sidecar_sox_binary_path(cwd)
    if sox_binary.is_file():
        environment["PATH"] = (
            str(sox_binary.parent)
            + os.pathsep
            + environment.get("PATH", "")
        )
        environment["ALEXANDRIA_SIDECAR_SOX"] = str(sox_binary)
        library_dir = sox_binary.parent.parent / "lib"
        if sys.platform == "darwin":
            environment["DYLD_LIBRARY_PATH"] = (
                str(library_dir)
                + os.pathsep
                + environment.get("DYLD_LIBRARY_PATH", "")
            )
        elif os.name != "nt":
            environment["LD_LIBRARY_PATH"] = (
                str(library_dir)
                + os.pathsep
                + environment.get("LD_LIBRARY_PATH", "")
            )
    return run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=environment,
    )


def execute_sidecar_job(
    *,
    root_dir: str | Path,
    job_id: str,
    timeout: float | None = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    with _SIDECAR_LOCK:
        record = read_sidecar_job(root_dir=root, job_id=job_id)
        if record["status"] != "queued":
            raise TrainingSidecarConflictError(
                "Only a queued sidecar job may start."
            )
        record["status"] = "running"
        record["started_at_utc"] = utc_timestamp()
        atomic_json_write(record, sidecar_job_path(root, job_id))
    commands: list[list[str]] = []
    completed_stdout = []
    completed_stderr = []
    return_code = 0
    command_text = []
    result_value = None
    error = None
    try:
        commands = (
            _setup_commands(root)
            if record["action"] == "setup"
            else [
                _runner_command(
                    root_dir=root,
                    action=record["action"],
                    payload=record["payload"],
                )
            ]
        )
        for command in commands:
            command_text.append(command)
            completed = _run_command(
                command,
                cwd=root,
                timeout=timeout,
                run=run,
            )
            completed_stdout.append(completed.stdout or "")
            completed_stderr.append(completed.stderr or "")
            return_code = int(completed.returncode)
            if return_code != 0:
                break
        result_value = _json_from_stdout("\n".join(completed_stdout))
        if return_code != 0:
            error = (
                (result_value or {}).get("error")
                or _tail("\n".join(completed_stderr), 2000)
                or f"Sidecar command exited with {return_code}."
            )
    except subprocess.TimeoutExpired as exc:
        return_code = -1
        error = f"Sidecar job timed out: {exc}"
        completed_stdout.append(str(exc.stdout or ""))
        completed_stderr.append(str(exc.stderr or ""))
    except Exception as exc:
        return_code = -1
        error = str(exc)
    with _SIDECAR_LOCK:
        record = read_sidecar_job(root_dir=root, job_id=job_id)
        record.update(
            {
                "status": "completed" if return_code == 0 else "failed",
                "command": command_text,
                "finished_at_utc": utc_timestamp(),
                "return_code": return_code,
                "stdout_tail": _tail("\n".join(completed_stdout)),
                "stderr_tail": _tail("\n".join(completed_stderr)),
                "result": result_value,
                "error": error,
            }
        )
        atomic_json_write(record, sidecar_job_path(root, job_id))
        return record


def _validate_mlx_export_artifact(
    *,
    artifact_dir: Path,
) -> dict[str, Any]:
    manifest_path = artifact_dir / MLX_EXPORT_MANIFEST
    if not manifest_path.is_file():
        raise TrainingSidecarValidationError(
            f"MLX export requires {MLX_EXPORT_MANIFEST}."
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TrainingSidecarValidationError(
            f"MLX export manifest could not be read: {exc}"
        ) from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise TrainingSidecarValidationError(
            "MLX export manifest schema is unsupported."
        )
    if manifest.get("artifact_format") != "merged_mlx_qwen_checkpoint":
        raise TrainingSidecarValidationError(
            "MLX export artifact format is unsupported."
        )
    if manifest.get("status") != "validated_experimental" or (
        manifest.get("technical_validation_passed") is not True
    ):
        raise TrainingSidecarValidationError(
            "MLX export has not passed technical validation."
        )
    if manifest.get("production_assignment_supported") is not False:
        raise TrainingSidecarValidationError(
            "Experimental MLX exports cannot claim production assignment."
        )
    raw_propagation = manifest.get("instruction_propagation")
    try:
        instruction_propagation = (
            validate_instruction_propagation_contract(raw_propagation)
            if raw_propagation is not None
            else build_instruction_propagation_contract(
                mode="identity_only",
                samples=[],
            )
        )
    except InstructionPropagationError as exc:
        raise TrainingSidecarValidationError(
            f"MLX export instruction propagation is invalid: {exc}"
        ) from exc
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise TrainingSidecarValidationError(
            "MLX export manifest must list artifact files."
        )
    normalized_files = []
    listed_paths = set()
    for index, item in enumerate(files):
        if not isinstance(item, dict) or set(item) != {
            "path",
            "sha256",
            "size_bytes",
        }:
            raise TrainingSidecarValidationError(
                f"MLX export file {index} is invalid."
            )
        path = _safe_relative_path(
            artifact_dir,
            item["path"],
            f"MLX export file {index}",
        )
        if not path.is_file():
            raise TrainingSidecarValidationError(
                f"MLX export file is missing: {item['path']}"
            )
        if sha256_file(path) != item["sha256"]:
            raise TrainingSidecarValidationError(
                f"MLX export file hash does not match: {item['path']}"
            )
        if path.stat().st_size != item["size_bytes"]:
            raise TrainingSidecarValidationError(
                f"MLX export file size does not match: {item['path']}"
            )
        relative = path.relative_to(artifact_dir).as_posix()
        if relative in listed_paths:
            raise TrainingSidecarValidationError(
                f"MLX export lists a duplicate file: {relative}"
            )
        listed_paths.add(relative)
        normalized_files.append({**item, "path": relative})
    required = {
        "model.safetensors",
        "config.json",
        "ref_sample.wav",
        "ref_sample.txt",
        "validation_neutral.wav",
        "validation_expressive.wav",
        "speech_tokenizer/model.safetensors",
    }
    missing = sorted(required - listed_paths)
    if missing:
        raise TrainingSidecarValidationError(
            "MLX export is missing required files: " + ", ".join(missing)
        )
    return {
        **manifest,
        "instruction_propagation": instruction_propagation,
        "files": normalized_files,
    }


def install_mlx_lora_artifact(
    *,
    root_dir: str | Path,
    source_path: str,
    adapter_id: str,
    name: str,
    dataset_id: str | None = None,
    training_metrics_path: str | None = None,
    installed_at_utc: str | None = None,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    source = _safe_relative_path(root, source_path, "MLX export path")
    if not source.is_dir():
        raise TrainingSidecarValidationError(
            "MLX export path must be a directory."
        )
    safe_id = str(adapter_id or "").strip().lower()
    if not LORA_ARTIFACT_ID_RE.fullmatch(safe_id):
        raise TrainingSidecarValidationError(
            "LoRA artifact ID must use lowercase letters, numbers, hyphens, or underscores."
        )
    display_name = str(name or "").strip()
    if not display_name:
        raise TrainingSidecarValidationError(
            "LoRA artifact name must be non-empty."
        )
    export_manifest = _validate_mlx_export_artifact(artifact_dir=source)
    export_instruction_propagation = export_manifest[
        "instruction_propagation"
    ]
    metrics = None
    if training_metrics_path is not None:
        metrics_path = _safe_relative_path(
            root,
            training_metrics_path,
            "Training metrics path",
        )
        if not metrics_path.is_file():
            raise TrainingSidecarValidationError(
                "Training metrics file does not exist."
            )
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TrainingSidecarValidationError(
                f"Training metrics could not be read: {exc}"
            ) from exc
        if not isinstance(metrics, dict) or metrics.get("mode") != "lora":
            raise TrainingSidecarValidationError(
                "Training metrics must describe a LoRA run."
            )
        source_manifest_sha = export_manifest.get(
            "source_adapter_manifest_sha256"
        )
        artifact_manifest = metrics_path.parent / "sidecar_artifact.json"
        if (
            not artifact_manifest.is_file()
            or sha256_file(artifact_manifest) != source_manifest_sha
        ):
            raise TrainingSidecarValidationError(
                "Training metrics do not match the exported adapter."
            )
        raw_training_propagation = metrics.get(
            "training_contract",
            {},
        ).get("instruction_propagation")
        try:
            training_instruction_propagation = (
                validate_instruction_propagation_contract(
                    raw_training_propagation
                )
                if raw_training_propagation is not None
                else build_instruction_propagation_contract(
                    mode="identity_only",
                    samples=[],
                )
            )
        except InstructionPropagationError as exc:
            raise TrainingSidecarValidationError(
                f"Training instruction propagation is invalid: {exc}"
            ) from exc
        if (
            training_instruction_propagation["propagation_fingerprint"]
            != export_instruction_propagation["propagation_fingerprint"]
        ):
            raise TrainingSidecarValidationError(
                "Training and MLX export instruction propagation do not match."
            )

    models_root = root / "lora_models"
    target_root = models_root / safe_id
    target_model = target_root / "mlx_model"
    manifest_path = models_root / "manifest.json"
    models_root.mkdir(parents=True, exist_ok=True)
    if target_root.exists():
        raise TrainingSidecarConflictError(
            f"LoRA artifact {safe_id!r} already exists."
        )
    manifest = []
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TrainingSidecarValidationError(
                f"LoRA model manifest could not be read: {exc}"
            ) from exc
        if not isinstance(manifest, list):
            raise TrainingSidecarValidationError(
                "LoRA model manifest must contain a JSON array."
            )
    if any(item.get("id") == safe_id for item in manifest if isinstance(item, dict)):
        raise TrainingSidecarConflictError(
            f"LoRA artifact {safe_id!r} is already registered."
        )

    identity = {
        "adapter_id": safe_id,
        "source_path": source.relative_to(root).as_posix(),
        "export_fingerprint": export_manifest.get("export_fingerprint"),
        "installed_at_utc": installed_at_utc or utc_timestamp(),
    }
    temporary_root = models_root / (
        f".{safe_id}.installing.{fingerprint_value(identity)[:12]}"
    )
    if temporary_root.exists():
        shutil.rmtree(temporary_root)
    try:
        shutil.copytree(source, temporary_root / "mlx_model")
        shutil.copy2(
            temporary_root / "mlx_model" / "ref_sample.wav",
            temporary_root / "ref_sample.wav",
        )
        shutil.copy2(
            temporary_root / "mlx_model" / "ref_sample.txt",
            temporary_root / "ref_sample.txt",
        )
        shutil.copy2(
            temporary_root / "mlx_model" / "validation_neutral.wav",
            temporary_root / "preview_sample.wav",
        )
        reference_text = (temporary_root / "ref_sample.txt").read_text(
            encoding="utf-8"
        ).strip()
        training_meta = {
            "schema_version": 1,
            "status": "validated_experimental_unassigned",
            "ref_sample_text": reference_text,
            "dataset_id": dataset_id,
            "base_model": export_manifest.get("base_model"),
            "base_model_revision": export_manifest.get("base_model_revision"),
            "export_fingerprint": export_manifest.get("export_fingerprint"),
            "instruction_propagation": export_instruction_propagation,
            "technical_validation_passed": True,
            "manual_audio_review_status": export_manifest.get(
                "validation",
                {},
            ).get("manual_audio_review_status", "pending"),
            "production_assignment_supported": False,
        }
        atomic_json_write(training_meta, temporary_root / "training_meta.json")
        os.replace(temporary_root, target_root)

        validation = export_manifest.get("validation", {})
        validation_rows = validation.get("measurements", {})
        validation_losses = (
            metrics.get("validation_metrics", [])
            if isinstance(metrics, dict)
            else []
        )
        final_validation = (
            validation_losses[-1].get("validation")
            if validation_losses
            else None
        )
        entry = {
            "id": safe_id,
            "name": display_name,
            "dataset_id": dataset_id,
            "epochs": metrics.get("epochs_completed") if isinstance(metrics, dict) else None,
            "final_loss": (
                metrics.get("validation_metrics", [])[-1].get("train_loss")
                if isinstance(metrics, dict) and metrics.get("validation_metrics")
                else None
            ),
            "validation_loss": (
                final_validation.get("loss")
                if isinstance(final_validation, dict)
                else None
            ),
            "sample_count": (
                metrics.get("dataset", {}).get("prepared_count")
                if isinstance(metrics, dict)
                else None
            ),
            "lora_r": (
                metrics.get("training_contract", {}).get("lora_rank")
                if isinstance(metrics, dict)
                else None
            ),
            "lr": (
                metrics.get("training_contract", {}).get("learning_rate")
                if isinstance(metrics, dict)
                else None
            ),
            "target_profile": (
                metrics.get("training_contract", {}).get("target_profile")
                if isinstance(metrics, dict)
                else None
            ),
            "created": datetime.fromisoformat(
                identity["installed_at_utc"].replace("Z", "+00:00")
            ).timestamp(),
            "created_at_utc": identity["installed_at_utc"],
            "experimental": True,
            "technical_validation_passed": True,
            "production_assignment_supported": False,
            "manual_audio_review_status": validation.get(
                "manual_audio_review_status",
                "pending",
            ),
            "neutral_rtf": validation_rows.get("neutral", {}).get(
                "real_time_factor"
            ),
            "expressive_rtf": validation_rows.get("expressive", {}).get(
                "real_time_factor"
            ),
            "speaker_cosine_floor": min(
                (
                    row.get("speaker_cosine_to_reference", 0.0)
                    for row in validation_rows.values()
                    if isinstance(row, dict)
                ),
                default=None,
            ),
            "export_fingerprint": export_manifest.get("export_fingerprint"),
            "base_model_revision": export_manifest.get("base_model_revision"),
            "instruction_propagation": export_instruction_propagation,
            "instruction_mode": export_instruction_propagation["mode"],
            "instruction_required_at_inference": export_instruction_propagation[
                "instruction_required_at_inference"
            ],
            "adapter_path": target_root.relative_to(root).as_posix(),
            "mlx_model_path": target_model.relative_to(root).as_posix(),
        }
        atomic_json_write([*manifest, entry], manifest_path)
    except Exception:
        shutil.rmtree(temporary_root, ignore_errors=True)
        shutil.rmtree(target_root, ignore_errors=True)
        raise
    return {
        "status": "installed_experimental_unassigned",
        "adapter_id": safe_id,
        "adapter_path": target_root.relative_to(root).as_posix(),
        "mlx_model_path": target_model.relative_to(root).as_posix(),
        "manifest_path": manifest_path.relative_to(root).as_posix(),
        "entry": entry,
        "production_assignment_supported": False,
    }


def _validate_external_artifact(
    *,
    artifact_dir: Path,
) -> dict[str, Any]:
    manifest_path = artifact_dir / "sidecar_artifact.json"
    if not manifest_path.is_file():
        raise TrainingSidecarValidationError(
            "External artifact requires sidecar_artifact.json."
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TrainingSidecarValidationError(
            f"External artifact manifest could not be read: {exc}"
        ) from exc
    expected = {
        "schema_version",
        "artifact_format",
        "status",
        "base_model",
        "training_device",
        "dataset_path",
        "created_at_utc",
        "metrics",
        "files",
        "production_assignment_supported",
    }
    allowed = expected | {"instruction_propagation"}
    if (
        not isinstance(manifest, dict)
        or not expected.issubset(manifest)
        or not set(manifest).issubset(allowed)
    ):
        raise TrainingSidecarValidationError(
            "External artifact manifest has unexpected fields."
        )
    if manifest["schema_version"] != SIDECAR_SCHEMA_VERSION:
        raise TrainingSidecarValidationError(
            "External artifact schema is unsupported."
        )
    if manifest["artifact_format"] not in SIDECAR_ARTIFACT_FORMATS:
        raise TrainingSidecarValidationError(
            "External artifact format is unsupported."
        )
    if manifest["production_assignment_supported"] is not False:
        raise TrainingSidecarValidationError(
            "Experimental artifacts cannot claim production assignment support."
        )
    raw_propagation = manifest.get("instruction_propagation")
    try:
        instruction_propagation = (
            validate_instruction_propagation_contract(raw_propagation)
            if raw_propagation is not None
            else build_instruction_propagation_contract(
                mode="identity_only",
                samples=[],
            )
        )
    except InstructionPropagationError as exc:
        raise TrainingSidecarValidationError(
            f"External artifact instruction propagation is invalid: {exc}"
        ) from exc
    if not isinstance(manifest["files"], list) or not manifest["files"]:
        raise TrainingSidecarValidationError(
            "External artifact must list files."
        )
    normalized_files = []
    for index, item in enumerate(manifest["files"]):
        if not isinstance(item, dict) or set(item) != {
            "path",
            "sha256",
            "size_bytes",
        }:
            raise TrainingSidecarValidationError(
                f"External artifact file {index} is invalid."
            )
        path = _safe_relative_path(
            artifact_dir,
            item["path"],
            f"External artifact file {index}",
        )
        if not path.is_file():
            raise TrainingSidecarValidationError(
                f"External artifact file is missing: {item['path']}"
            )
        if sha256_file(path) != item["sha256"]:
            raise TrainingSidecarValidationError(
                f"External artifact file hash does not match: {item['path']}"
            )
        if path.stat().st_size != item["size_bytes"]:
            raise TrainingSidecarValidationError(
                f"External artifact file size does not match: {item['path']}"
            )
        normalized_files.append(item)
    return {
        **manifest,
        "instruction_propagation": instruction_propagation,
        "files": normalized_files,
    }


def import_external_sidecar_artifact(
    *,
    root_dir: str | Path,
    source_path: str,
    imported_at_utc: str | None = None,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    source = _safe_relative_path(root, source_path, "External artifact path")
    if not source.is_dir():
        raise TrainingSidecarValidationError(
            "External artifact path must be a directory."
        )
    manifest = _validate_external_artifact(artifact_dir=source)
    identity = {
        "source_path": source.relative_to(root).as_posix(),
        "manifest_sha256": sha256_file(source / "sidecar_artifact.json"),
        "imported_at_utc": imported_at_utc or utc_timestamp(),
    }
    import_id = "sidecar_import_" + fingerprint_value(identity)[:24]
    target = sidecar_imports_dir(root) / import_id
    with _SIDECAR_LOCK:
        if target.exists():
            raise TrainingSidecarConflictError(
                "This external artifact is already imported."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)
        verified = _validate_external_artifact(artifact_dir=target)
        record = {
            "schema_version": SIDECAR_SCHEMA_VERSION,
            "import_id": import_id,
            "status": "imported_experimental_unassigned",
            "source_path": identity["source_path"],
            "artifact_format": verified["artifact_format"],
            "base_model": verified["base_model"],
            "training_device": verified["training_device"],
            "instruction_propagation": verified[
                "instruction_propagation"
            ],
            "imported_at_utc": identity["imported_at_utc"],
            "artifact_manifest_sha256": identity["manifest_sha256"],
            "production_assignment_supported": False,
            "target_path": target.relative_to(root).as_posix(),
        }
        atomic_json_write(record, target / "import_manifest.json")
        return record
