#!/usr/bin/env python3
"""Resume pinned Chatterbox Multilingual V3 Round 1 generation safely."""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import resource
import sys
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

from multimodel_round1_chatterbox_cache_policy import (
    AudioMetrics,
    ChatterboxError,
    ChatterboxModel,
    DeviceHandle,
    FULL_CONDITIONALS_REUSE_POLICY as FULL_CONDITIONALS_REUSE_POLICY,
    MODEL_REPO as MODEL_REPO,
    MODEL_REVISION as MODEL_REVISION,
    ROUND_ID as ROUND_ID,
    RUNTIME_CONTROLS as RUNTIME_CONTROLS,
    SOURCE_COMMIT as SOURCE_COMMIT,
    T3_MODEL as T3_MODEL,
    TorchRuntime,
    chatterbox_sample_fingerprint as chatterbox_sample_fingerprint,
    legacy_cache_revalidation_status as legacy_cache_revalidation_status,
    partition_chatterbox_samples as partition_chatterbox_samples,
    repository_head as repository_head,
)
from multimodel_round1_chatterbox_execution import (
    ChatterboxExecutionRequest,
    execute_chatterbox_generation,
)
from multimodel_round1_paths import (
    ContainedPath,
    contained_path,
    safe_read_bytes,
    safe_read_text,
)
from multimodel_round1_runtime import (
    PROJECTED_SAMPLE_BYTES,
    acquire_metal_lock,
    require_disk_headroom,
    validate_sample_references,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = ROOT / ".omo" / "evidence" / "b17-t05-multimodel-round1"
DEFAULT_SOURCE = Path(
    "/Users/tristan/pinokio/cache/alexandria-evaluation/chatterbox-v3/source"
)
MPS_HIGH_WATERMARK_RATIO = "0.45"
MPS_LOW_WATERMARK_RATIO = "0.40"
QUARANTINED_SAMPLES = {"r1_a7001c75d9fabc63a421"}


def configure_mps_safety_environment(environment: MutableMapping[str, str]) -> None:
    environment["PYTORCH_MPS_HIGH_WATERMARK_RATIO"] = MPS_HIGH_WATERMARK_RATIO
    environment["PYTORCH_MPS_LOW_WATERMARK_RATIO"] = MPS_LOW_WATERMARK_RATIO


def release_sample_mps_cache(torch_module: TorchRuntime) -> None:
    if hasattr(torch_module.mps, "synchronize"):
        torch_module.mps.synchronize()
    torch_module.mps.empty_cache()


def cleanup_sample_partials(*paths: Path) -> None:
    for path in paths:
        path.unlink(missing_ok=True)


def load_v3(
    snapshot: Path, source_root: Path
) -> tuple[ChatterboxModel, DeviceHandle, TorchRuntime]:
    import numpy as np
    import perth
    import torch

    source_package = source_root / "src"
    if str(source_package) not in sys.path:
        sys.path.insert(0, str(source_package))
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    class NoopWatermarker:
        def apply_watermark(self, wav: Any, sample_rate: int) -> np.ndarray:
            return np.asarray(wav, dtype=np.float32)

    perth.PerthImplicitWatermarker = NoopWatermarker
    if not torch.backends.mps.is_available():
        raise ChatterboxError("mps_unavailable", MODEL_REPO)
    device = torch.device("mps")
    original_load = torch.load

    def cpu_staged_load(*args: Any, **kwargs: Any):
        if kwargs.get("map_location") is None:
            kwargs["map_location"] = torch.device("cpu")
        return original_load(*args, **kwargs)

    torch.load = cpu_staged_load
    try:
        model = ChatterboxMultilingualTTS.from_local(
            snapshot, device, t3_model=T3_MODEL
        )
    finally:
        torch.load = original_load
    return model, device, torch


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


def audio_metrics(path: ContainedPath, text: str) -> AudioMetrics:
    import numpy as np
    import soundfile as sf

    audio, sample_rate = sf.read(
        io.BytesIO(safe_read_bytes(path)), dtype="float32", always_2d=True
    )
    mono = audio.mean(axis=1)
    duration = len(mono) / int(sample_rate)
    rms = float(np.sqrt(np.mean(mono * mono))) if len(mono) else 0.0
    peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
    return {
        "duration_seconds": duration,
        "sample_rate": int(sample_rate),
        "channels": int(audio.shape[1]),
        "rms_dbfs": 20.0 * math.log10(max(rms, 1e-12)),
        "peak_dbfs": 20.0 * math.log10(max(peak, 1e-12)),
        "words_per_second": len(text.split()) / duration if duration else None,
    }


def peak_rss_gib() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024**3)


def resolve_reference(evidence_root: Path, sample: dict[str, Any]) -> ContainedPath:
    value = sample["reference"].get("conditioning_file")
    if not value:
        raise ChatterboxError("reference_audio_missing", str(sample["sample_id"]))
    return contained_path(evidence_root / "references", str(value))


def selected_samples(
    manifest: dict[str, Any], args: argparse.Namespace
) -> list[dict[str, Any]]:
    styles = set(args.style or [])
    identities = set(args.identity or [])
    groups = set(args.group or [])
    skipped = QUARANTINED_SAMPLES | set(args.skip_sample or [])
    return [
        item
        for item in manifest["sample_specs"]
        if item["model_key"] == "chatterbox_multilingual_v3"
        and item["status"] == "pending_generation"
        and (not groups or item["group"] in groups)
        and (not styles or item["style"] in styles)
        and (not identities or item["identity_key"] in identities)
        and item["sample_id"] not in skipped
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--source-root", default=str(DEFAULT_SOURCE))
    parser.add_argument("--group", action="append")
    parser.add_argument("--style", action="append")
    parser.add_argument("--identity", action="append")
    parser.add_argument("--skip-sample", action="append")
    parser.add_argument("--max-samples", type=int)
    parser.add_argument("--reuse-existing", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()

    configure_mps_safety_environment(os.environ)
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    evidence_root = Path(args.evidence_root).expanduser().resolve()
    source_root = Path(args.source_root).expanduser().resolve()
    head = repository_head(source_root)
    if head != SOURCE_COMMIT:
        raise ChatterboxError("source_commit_changed", head)
    snapshot = exact_snapshot()
    manifest = json.loads(
        safe_read_text(contained_path(evidence_root, "round1_internal_manifest.json"))
    )
    candidates = selected_samples(manifest, args)
    for sample in candidates:
        validate_sample_references(evidence_root, sample)
    samples, reused = partition_chatterbox_samples(
        evidence_root,
        candidates,
        reuse_existing=args.reuse_existing,
        max_samples=args.max_samples,
    )
    disk_receipt = evidence_root / "recovery" / "disk-headroom.jsonl"
    disk = require_disk_headroom(
        evidence_root,
        projected_bytes=len(samples) * PROJECTED_SAMPLE_BYTES,
        receipt_path=disk_receipt,
        stage="chatterbox_multilingual_v3:before-model-load",
    )
    if args.preflight_only:
        lease = acquire_metal_lock(
            evidence_root / ".metal-generation.lock",
            purpose="round1-generation:chatterbox-preflight",
        )
        lease.close()
        print(
            json.dumps(
                {
                    "ok": True,
                    "candidate_count": len(candidates),
                    "reused_count": len(reused),
                    "pending_after_bound": len(samples),
                    "quarantined_sample_ids": sorted(QUARANTINED_SAMPLES),
                    "source_commit": head,
                    "model_revision": MODEL_REVISION,
                    "snapshot": str(snapshot),
                    "disk": disk,
                    "model_loaded": False,
                },
                indent=2,
            )
        )
        return 0
    if not samples:
        print(json.dumps({"model_key": "chatterbox_multilingual_v3", "reused": len(reused)}))
        return 0

    request = ChatterboxExecutionRequest(
        evidence_root=evidence_root,
        source_root=source_root,
        snapshot=snapshot,
        samples=samples,
        reused=reused,
        groups=tuple(args.group or ()),
        quarantined_sample_ids=frozenset(QUARANTINED_SAMPLES),
        disk_receipt=disk_receipt,
        mps_high_watermark_ratio=MPS_HIGH_WATERMARK_RATIO,
        mps_low_watermark_ratio=MPS_LOW_WATERMARK_RATIO,
        load_model=load_v3,
        sample_fingerprint=chatterbox_sample_fingerprint,
        resolve_reference=resolve_reference,
        audio_metrics=audio_metrics,
        peak_rss_gib=peak_rss_gib,
        release_mps_cache=release_sample_mps_cache,
        require_disk_headroom=require_disk_headroom,
        acquire_metal_lock=acquire_metal_lock,
    )
    return execute_chatterbox_generation(request)


if __name__ == "__main__":
    raise SystemExit(main())
