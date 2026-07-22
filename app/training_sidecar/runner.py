#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

from qwen_training import (
    DEFAULT_MODEL,
    SidecarTrainingError,
    enumerate_lora_targets,
    infer_lora_adapter,
    load_model_bundle,
    merge_lora_adapter,
    resolve_device,
    run_training,
)


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _sox_report() -> dict:
    configured = os.environ.get("ALEXANDRIA_SIDECAR_SOX")
    candidate = configured if configured and Path(configured).is_file() else None
    executable = candidate or shutil.which("sox")
    if not executable:
        return {
            "available": False,
            "executable": None,
            "version": None,
            "error": "The SoX executable is not available on PATH.",
        }
    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:
        return {
            "available": False,
            "executable": executable,
            "version": None,
            "error": str(exc),
        }
    text = (completed.stdout or completed.stderr or "").strip()
    package_version = None
    version_source = "binary"
    if completed.returncode == 0 and not re.search(r"\d+\.\d+", text):
        metadata_dir = Path(executable).resolve().parents[1] / "conda-meta"
        for metadata_path in sorted(metadata_dir.glob("sox-*.json")):
            try:
                metadata = json.loads(
                    metadata_path.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            value = metadata.get("version")
            if isinstance(value, str) and value.strip():
                package_version = value.strip()
                version_source = "conda_metadata"
                break
    version = package_version or text or None
    return {
        "available": completed.returncode == 0,
        "executable": executable,
        "version": version,
        "binary_output": text or None,
        "version_source": version_source,
        "error": None if completed.returncode == 0 else text,
    }


def environment_report() -> dict:
    import torch

    packages = {
        name: package_version(name)
        for name in (
            "qwen-tts",
            "transformers",
            "torch",
            "torchaudio",
            "peft",
            "accelerate",
            "librosa",
            "soundfile",
            "sox",
        )
    }
    sox = _sox_report()
    package_ready = all(packages.values())
    return {
        "status": (
            "ready"
            if package_ready and sox["available"]
            else "incomplete"
        ),
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": packages,
        "sox_binary": sox,
        "mps_built": torch.backends.mps.is_built(),
        "mps_available": torch.backends.mps.is_available(),
        "cuda_available": torch.cuda.is_available(),
        "default_device": resolve_device("auto"),
    }


def model_probe(args) -> dict:
    bundle = load_model_bundle(
        model_name=args.model_name,
        device=args.device,
        local_files_only=args.local_files_only,
    )
    hf_model = bundle["hf_model"]
    talker = hf_model.talker
    return {
        "status": "loaded",
        "model_name": args.model_name,
        "device": bundle["device"],
        "dtype": str(bundle["dtype"]),
        "load_seconds": bundle["load_seconds"],
        "total_parameters": sum(
            parameter.numel() for parameter in hf_model.parameters()
        ),
        "talker_parameters": sum(
            parameter.numel() for parameter in talker.parameters()
        ),
    }


def target_probe(args) -> dict:
    bundle = load_model_bundle(
        model_name=args.model_name,
        device=args.device,
        local_files_only=args.local_files_only,
    )
    targets = enumerate_lora_targets(
        bundle["hf_model"].talker,
        profile=args.lora_target_profile,
    )
    return {
        "status": "inspected",
        "model_name": args.model_name,
        "device": bundle["device"],
        "load_seconds": bundle["load_seconds"],
        **targets,
    }


def infer_command(args) -> dict:
    return infer_lora_adapter(
        adapter_dir=args.adapter_dir,
        output_path=args.output_path,
        text=args.text,
        instruction=args.instruction,
        model_name=args.model_name,
        device=args.device,
        language=args.language,
        max_new_tokens=args.max_new_tokens,
        local_files_only=args.local_files_only,
    )


def merge_command(args) -> dict:
    return merge_lora_adapter(
        adapter_dir=args.adapter_dir,
        output_dir=args.output_dir,
        model_name=args.model_name,
        device=args.device,
        local_files_only=args.local_files_only,
    )


def train_command(args) -> dict:
    return run_training(
        mode=args.mode,
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        model_name=args.model_name,
        device=args.device,
        epochs=args.epochs,
        max_steps=args.max_steps,
        learning_rate=args.learning_rate,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        language=args.language,
        max_audio_seconds=args.max_audio_seconds,
        max_samples=args.max_samples,
        local_files_only=args.local_files_only,
        lora_rank=args.lora_rank,
        lora_alpha=args.lora_alpha,
        lora_target_profile=args.lora_target_profile,
        validation_fraction=args.validation_fraction,
        seed=args.seed,
        instruction_mode=args.instruction_mode,
        resume_from=args.resume_from,
        checkpoint_every_epoch=args.checkpoint_every_epoch,
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Isolated experimental PyTorch Qwen3-TTS training sidecar. "
            "This runner never assigns production voices."
        )
    )
    subparsers = result.add_subparsers(dest="command", required=True)
    subparsers.add_parser("environment")

    for name in ("model-probe", "inspect-targets"):
        child = subparsers.add_parser(name)
        child.add_argument("--model-name", default=DEFAULT_MODEL)
        child.add_argument(
            "--device",
            choices=("auto", "mps", "cuda", "cpu"),
            default="auto",
        )
        child.add_argument("--local-files-only", action="store_true")
        if name == "inspect-targets":
            child.add_argument(
                "--lora-target-profile",
                choices=("attention", "attention_mlp"),
                default="attention_mlp",
            )

    train = subparsers.add_parser("train")
    train.add_argument("--mode", choices=("sft", "lora"), required=True)
    train.add_argument("--data-dir", required=True)
    train.add_argument("--output-dir", required=True)
    train.add_argument("--model-name", default=DEFAULT_MODEL)
    train.add_argument(
        "--device",
        choices=("auto", "mps", "cuda", "cpu"),
        default="auto",
    )
    train.add_argument("--epochs", type=int, default=1)
    train.add_argument("--max-steps", type=int)
    train.add_argument("--learning-rate", type=float, default=5e-6)
    train.add_argument("--gradient-accumulation-steps", type=int, default=1)
    train.add_argument("--language", default="english")
    train.add_argument("--max-audio-seconds", type=float, default=30.0)
    train.add_argument("--max-samples", type=int)
    train.add_argument("--local-files-only", action="store_true")
    train.add_argument("--lora-rank", type=int, default=32)
    train.add_argument("--lora-alpha", type=int, default=128)
    train.add_argument(
        "--lora-target-profile",
        choices=("attention", "attention_mlp"),
        default="attention_mlp",
    )
    train.add_argument("--validation-fraction", type=float, default=0.1)
    train.add_argument("--seed", type=int, default=1337)
    train.add_argument(
        "--instruction-mode",
        choices=("identity_only", "per_record"),
        default="identity_only",
    )
    train.add_argument("--resume-from")
    train.add_argument(
        "--no-checkpoint-every-epoch",
        dest="checkpoint_every_epoch",
        action="store_false",
    )
    train.set_defaults(checkpoint_every_epoch=True)

    infer = subparsers.add_parser("infer-adapter")
    infer.add_argument("--adapter-dir", required=True)
    infer.add_argument("--output-path", required=True)
    infer.add_argument("--text", required=True)
    infer.add_argument("--instruction")
    infer.add_argument("--model-name", default=DEFAULT_MODEL)
    infer.add_argument(
        "--device",
        choices=("auto", "mps", "cuda", "cpu"),
        default="auto",
    )
    infer.add_argument("--language", default="English")
    infer.add_argument("--max-new-tokens", type=int, default=600)
    infer.add_argument("--local-files-only", action="store_true")

    merge = subparsers.add_parser("merge-adapter")
    merge.add_argument("--adapter-dir", required=True)
    merge.add_argument("--output-dir", required=True)
    merge.add_argument("--model-name", default=DEFAULT_MODEL)
    merge.add_argument(
        "--device",
        choices=("auto", "mps", "cuda", "cpu"),
        default="auto",
    )
    merge.add_argument("--local-files-only", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    started = time.perf_counter()
    try:
        if args.command == "environment":
            value = environment_report()
        elif args.command == "model-probe":
            value = model_probe(args)
        elif args.command == "inspect-targets":
            value = target_probe(args)
        elif args.command == "infer-adapter":
            value = infer_command(args)
        elif args.command == "merge-adapter":
            value = merge_command(args)
        else:
            value = train_command(args)
        value = {
            **value,
            "runner_elapsed_seconds": time.perf_counter() - started,
        }
        print(json.dumps(value, ensure_ascii=False))
        return 0
    except Exception as exc:
        error = {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "runner_elapsed_seconds": time.perf_counter() - started,
        }
        print(json.dumps(error, ensure_ascii=False))
        traceback.print_exc(file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
