#!/usr/bin/env python3
"""Generate and register the Chatterbox Multilingual V3 native anchor.

Run with the isolated Python 3.11 Chatterbox environment and the pinned official
source tree on PYTHONPATH. The macOS evaluation path uses two explicit,
recorded compatibility shims:
- checkpoint tensors are staged through CPU before modules move to MPS;
- Perth watermarking is unavailable on macOS, so evaluation audio is left
  unwatermarked instead of failing generation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import torch

DEFAULT_EVIDENCE = Path(
    "/Users/tristan/.devspace/worktrees/"
    "alexandria-audiobook.git-78fc5814/.omo/evidence/"
    "b17-t05-multimodel-round1"
)
DEFAULT_SOURCE = Path(
    "/Users/tristan/pinokio/cache/alexandria-evaluation/"
    "chatterbox-v3/source"
)
MODEL_REPO = "ResembleAI/chatterbox"
MODEL_REVISION = "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"
SOURCE_COMMIT = "5de7a54aa4e5e2baadb0182dde554908b48b85c2"
T3_MODEL = "v3"
ANCHOR_TEXT = (
    "The old clock marked the hour while the quiet library settled around us."
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def exact_snapshot() -> Path:
    snapshot = (
        Path.home()
        / ".cache"
        / "huggingface"
        / "hub"
        / "models--ResembleAI--chatterbox"
        / "snapshots"
        / MODEL_REVISION
    ).resolve()
    required = (
        "ve.pt",
        "t3_mtl23ls_v3.safetensors",
        "s3gen.pt",
        "grapheme_mtl_merged_expanded_v1.json",
        "conds.pt",
        "Cangjie5_TC.json",
    )
    missing = [name for name in required if not (snapshot / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Chatterbox V3 snapshot is incomplete: {missing}")
    return snapshot


def repository_head(source: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    head = completed.stdout.strip()
    if completed.returncode != 0 or not head:
        raise RuntimeError("Could not verify the pinned Chatterbox source commit.")
    return head


def load_model(snapshot: Path):
    import perth
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    class NoopWatermarker:
        def apply_watermark(self, wav: Any, sample_rate: int) -> np.ndarray:
            del sample_rate
            return np.asarray(wav, dtype=np.float32)

    perth.PerthImplicitWatermarker = NoopWatermarker
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    if device.type != "mps":
        raise RuntimeError("Chatterbox V3 Round 1 requires the Apple-Silicon MPS path.")

    original_load = torch.load

    def cpu_staged_load(*args: Any, **kwargs: Any):
        if kwargs.get("map_location") is None:
            kwargs["map_location"] = torch.device("cpu")
        return original_load(*args, **kwargs)

    torch.load = cpu_staged_load
    try:
        model = ChatterboxMultilingualTTS.from_local(
            snapshot,
            device,
            t3_model=T3_MODEL,
        )
    finally:
        torch.load = original_load
    return model, device


def audio_metrics(path: Path) -> dict[str, Any]:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    return {
        "duration_seconds": len(audio) / int(sample_rate),
        "sample_rate": int(sample_rate),
        "channels": int(audio.shape[1]),
        "peak": float(np.max(np.abs(audio))) if audio.size else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--source-root", default=str(DEFAULT_SOURCE))
    parser.add_argument("--seed", type=int, default=7004)
    parser.add_argument("--reuse-existing", action="store_true")
    args = parser.parse_args()

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    evidence_root = Path(args.evidence_root).expanduser().resolve()
    source_root = Path(args.source_root).expanduser().resolve()
    head = repository_head(source_root)
    if head != SOURCE_COMMIT:
        raise RuntimeError(
            f"Chatterbox source commit changed: expected {SOURCE_COMMIT}, found {head}."
        )
    snapshot = exact_snapshot()
    native_root = evidence_root / "references" / "native"
    native_root.mkdir(parents=True, exist_ok=True)
    output = native_root / "chatterbox_linden.wav"

    started = time.perf_counter()
    load_seconds = 0.0
    generation_seconds = 0.0
    if not (args.reuse_existing and output.is_file()):
        load_started = time.perf_counter()
        model, device = load_model(snapshot)
        load_seconds = time.perf_counter() - load_started
        torch.manual_seed(args.seed)
        generated_at = time.perf_counter()
        wav = model.generate(
            ANCHOR_TEXT,
            language_id="en",
            audio_prompt_path=None,
            exaggeration=0.5,
            cfg_weight=0.5,
            temperature=0.8,
            repetition_penalty=1.2,
            min_p=0.05,
            top_p=1.0,
        )
        generation_seconds = time.perf_counter() - generated_at
        audio = wav.detach().cpu().numpy().reshape(-1).astype(np.float32)
        sf.write(output, audio, int(model.sr))
    else:
        device = torch.device("mps")

    record = {
        "identity_key": "native_chatterbox_linden",
        "review_name": "Linden",
        "model_key": "chatterbox_multilingual_v3",
        "kind": "official_builtin_conditionals_anchor",
        "audio_file": output.name,
        "audio_sha256": sha256_file(output),
        "transcript": ANCHOR_TEXT,
        "transcript_sha256": sha256_text(ANCHOR_TEXT),
        "seed": args.seed,
        "generation_seconds": generation_seconds,
        "total_seconds": time.perf_counter() - started,
        "load_seconds": load_seconds,
        "model_repo": MODEL_REPO,
        "model_revision": MODEL_REVISION,
        "model_snapshot": str(snapshot),
        "source_repository": str(source_root),
        "source_commit": SOURCE_COMMIT,
        "t3_model": T3_MODEL,
        "device": str(device),
        "controls": {
            "language_id": "en",
            "exaggeration": 0.5,
            "cfg_weight": 0.5,
            "temperature": 0.8,
            "repetition_penalty": 1.2,
            "min_p": 0.05,
            "top_p": 1.0,
        },
        "cpu_staged_checkpoint_load": True,
        "watermark_applied": False,
        "watermark_reason": "perth_backend_unavailable_on_macos",
        "audio": audio_metrics(output),
    }

    manifest_path = native_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = [
        item
        for item in manifest.get("records", [])
        if item.get("identity_key") != record["identity_key"]
    ]
    records.append(record)
    manifest["records"] = records
    manifest["record_count"] = len(records)
    manifest["pending_native_anchors"] = [
        item
        for item in manifest.get("pending_native_anchors", [])
        if item != record["identity_key"]
    ]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"manifest": str(manifest_path), "record": record}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
