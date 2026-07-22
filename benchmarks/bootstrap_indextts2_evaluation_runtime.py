#!/usr/bin/env python3
"""Restore the pinned IndexTTS2 evaluation source and model cache.

This utility is evaluation-only. It writes outside the repository by default,
verifies exact source/model revisions, emits a machine-readable receipt, and
never changes Alexandria's production registry or Voice assignments.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

SOURCE_REPO = "https://github.com/index-tts/index-tts.git"
SOURCE_COMMIT = "13495845e3028f0bb6ca1462ad22aa0e76349e40"
MODEL_REPO = "IndexTeam/IndexTTS-2"
MODEL_REVISION = "740dcaff396282ffb241903d150ac011cd4b1ede"
AUXILIARY_MODELS = {
    "w2v_bert": {
        "repo": "facebook/w2v-bert-2.0",
        "revision": "da985ba0987f70aaeb84a80f2851cfac8c697a7b",
    },
    "semantic_codec": {
        "repo": "amphion/MaskGCT",
        "revision": "265c6cef07625665d0c28d2faafb1415562379dc",
        "file": "semantic_codec/model.safetensors",
    },
    "campplus": {
        "repo": "funasr/campplus",
        "revision": "e4b6ede7ce16997aff4ae69fbca1f0175e2afede",
        "file": "campplus_cn_common.bin",
    },
    "bigvgan": {
        "repo": "nvidia/bigvgan_v2_22khz_80band_256x",
        "revision": "633ff708ed5b74903e86ff1298cf4a98e921c513",
        "files": ["config.json", "bigvgan_generator.pt"],
    },
}
DEFAULT_ROOT = Path.home() / "pinokio" / "cache" / "alexandria-evaluation" / "indextts2"


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> str:
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Command failed ({completed.returncode}): {' '.join(command)}\n"
            f"{completed.stdout[-8000:]}"
        )
    return completed.stdout.strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def restore_source(root: Path) -> Path:
    source_dir = root / "source"
    if not source_dir.exists():
        source_dir.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--no-checkout", SOURCE_REPO, str(source_dir)])
    if not (source_dir / ".git").is_dir():
        raise RuntimeError(f"Existing source path is not a Git checkout: {source_dir}")
    run(["git", "fetch", "--depth", "1", "origin", SOURCE_COMMIT], cwd=source_dir)
    run(["git", "checkout", "--detach", SOURCE_COMMIT], cwd=source_dir)
    actual = run(["git", "rev-parse", "HEAD"], cwd=source_dir)
    if actual != SOURCE_COMMIT:
        raise RuntimeError(f"Source revision mismatch: expected {SOURCE_COMMIT}, got {actual}")
    return source_dir


def restore_model(root: Path) -> Path:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError("huggingface_hub is required for model restoration") from exc

    snapshot = snapshot_download(
        repo_id=MODEL_REPO,
        revision=MODEL_REVISION,
        cache_dir=str(root / "huggingface"),
        local_files_only=False,
    )
    snapshot_path = Path(snapshot).resolve()
    required = ["config.yaml", "gpt.pth", "s2mel.pth", "bpe.model"]
    missing = [name for name in required if not (snapshot_path / name).is_file()]
    if missing:
        raise RuntimeError(f"Pinned model snapshot is missing required files: {missing}")
    return snapshot_path


def restore_auxiliary(root: Path) -> dict[str, Path]:
    from huggingface_hub import hf_hub_download, snapshot_download

    aux_root = root / "auxiliary"
    aux_root.mkdir(parents=True, exist_ok=True)
    w2v = snapshot_download(
        repo_id=AUXILIARY_MODELS["w2v_bert"]["repo"],
        revision=AUXILIARY_MODELS["w2v_bert"]["revision"],
        cache_dir=str(root / "huggingface"),
        local_files_only=False,
    )
    w2v_path = Path(w2v).resolve()

    semantic_path = Path(
        hf_hub_download(
            repo_id=AUXILIARY_MODELS["semantic_codec"]["repo"],
            revision=AUXILIARY_MODELS["semantic_codec"]["revision"],
            filename=AUXILIARY_MODELS["semantic_codec"]["file"],
            cache_dir=str(root / "huggingface"),
        )
    ).resolve()
    campplus_path = Path(
        hf_hub_download(
            repo_id=AUXILIARY_MODELS["campplus"]["repo"],
            revision=AUXILIARY_MODELS["campplus"]["revision"],
            filename=AUXILIARY_MODELS["campplus"]["file"],
            cache_dir=str(root / "huggingface"),
        )
    ).resolve()
    bigvgan_snapshot = Path(
        snapshot_download(
            repo_id=AUXILIARY_MODELS["bigvgan"]["repo"],
            revision=AUXILIARY_MODELS["bigvgan"]["revision"],
            allow_patterns=AUXILIARY_MODELS["bigvgan"]["files"],
            cache_dir=str(root / "huggingface"),
        )
    ).resolve()
    for name in AUXILIARY_MODELS["bigvgan"]["files"]:
        if not (bigvgan_snapshot / name).is_file():
            raise RuntimeError(f"Pinned BigVGAN snapshot is missing {name}")
    return {
        "w2v_bert": w2v_path,
        "semantic_codec": semantic_path,
        "campplus": campplus_path,
        "bigvgan": bigvgan_snapshot,
    }


def replace_symlink(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.exists():
        if link.is_dir() and not link.is_symlink():
            shutil.rmtree(link)
        else:
            link.unlink()
    link.symlink_to(target)


def create_flat_auxiliary(root: Path, auxiliary: dict[str, Path]) -> Path:
    flat = root / "aux-flat"
    flat.mkdir(parents=True, exist_ok=True)
    replace_symlink(flat / "w2v-bert-2.0", auxiliary["w2v_bert"])
    replace_symlink(
        flat / "semantic_codec" / "model.safetensors",
        auxiliary["semantic_codec"],
    )
    replace_symlink(flat / "campplus_cn_common.bin", auxiliary["campplus"])
    replace_symlink(flat / "bigvgan", auxiliary["bigvgan"])
    return flat


def restore_environment(root: Path, source_dir: Path) -> Path:
    env_dir = root / "env"
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required to create the pinned IndexTTS2 environment")
    if not (env_dir / "bin" / "python").is_file():
        run([uv, "venv", "--python", "3.11", str(env_dir)])
    run(
        [uv, "sync", "--frozen", "--no-dev", "--active"],
        cwd=source_dir,
        env={**os.environ, "VIRTUAL_ENV": str(env_dir)},
    )
    return env_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--skip-environment", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    source_dir = restore_source(root)
    model_dir = None if args.source_only else restore_model(root)
    auxiliary = {} if args.source_only else restore_auxiliary(root)
    flat_auxiliary = None if args.source_only else create_flat_auxiliary(root, auxiliary)
    environment = (
        None
        if args.source_only or args.skip_environment
        else restore_environment(root, source_dir)
    )

    receipt: dict[str, Any] = {
        "schema_version": 1,
        "purpose": "pinned_indextts2_evaluation_runtime_restore",
        "root": str(root),
        "source": {
            "repo": SOURCE_REPO,
            "expected_commit": SOURCE_COMMIT,
            "actual_commit": run(["git", "rev-parse", "HEAD"], cwd=source_dir),
            "path": str(source_dir),
        },
        "model": None,
        "auxiliary": None,
        "flat_auxiliary": str(flat_auxiliary) if flat_auxiliary is not None else None,
        "environment": str(environment) if environment is not None else None,
        "production_registry_changed": False,
        "voice_assignment_changed": False,
        "live_project_audio_changed": False,
    }
    if model_dir is not None:
        receipt["model"] = {
            "repo": MODEL_REPO,
            "revision": MODEL_REVISION,
            "path": str(model_dir),
            "config_sha256": sha256_file(model_dir / "config.yaml"),
            "gpt_sha256": sha256_file(model_dir / "gpt.pth"),
            "s2mel_sha256": sha256_file(model_dir / "s2mel.pth"),
            "bpe_sha256": sha256_file(model_dir / "bpe.model"),
        }
        receipt["auxiliary"] = {
            key: {
                "repo": AUXILIARY_MODELS[key]["repo"],
                "revision": AUXILIARY_MODELS[key]["revision"],
                "path": str(path),
            }
            for key, path in auxiliary.items()
        }

    receipt_path = root / "restore_receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
