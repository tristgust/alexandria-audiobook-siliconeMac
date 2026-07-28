"""Command-line orchestration for the resumable Round 1 MLX runner."""

from __future__ import annotations

import argparse
import atexit
import json
import os
from pathlib import Path
import time
from typing import Any

from multimodel_round1_mlx_generation import generate_pending_samples
from multimodel_round1_mlx_loading import load_requested_models
from multimodel_round1_mlx_paths import safe_read_json, safe_write_json
from multimodel_round1_mlx_support import (
    disable_optional_sklearn,
    release_sample_mlx_cache,
)
from multimodel_round1_runtime import (
    PROJECTED_SAMPLE_BYTES,
    acquire_metal_lock,
    partition_generation_samples,
    require_disk_headroom,
    validate_sample_references,
)


SUPPORTED_MODELS: tuple[str, ...] = (
    "voxcpm2",
    "qwen3_tts",
    "fish_s2_pro",
    "moss_tts_local_v15",
)


def _parser(default_evidence: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", default=str(default_evidence))
    parser.add_argument("--model", required=True, choices=SUPPORTED_MODELS)
    parser.add_argument("--group")
    parser.add_argument("--style", action="append")
    parser.add_argument("--identity", action="append")
    parser.add_argument("--max-samples", type=int)
    parser.add_argument("--reuse-existing", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    return parser


def _summary(
    model_key: str,
    group: str | None,
    requested: int,
    complete: int,
    generated: int,
    reused: int,
    failures: list[dict[str, Any]],
    load_seconds: float,
    snapshots: dict[str, str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "model_key": model_key,
        "group": group,
        "requested_sample_count": requested,
        "complete_count": complete,
        "generated_count": generated,
        "reused_count": reused,
        "failure_count": len(failures),
        "failures": failures,
        "load_seconds": load_seconds,
        "model_snapshots": snapshots,
        "production_promotion_allowed": False,
    }


def main(default_evidence: Path | None = None) -> int:
    root = default_evidence or Path(__file__).resolve().parents[1] / ".omo" / "evidence" / "b17-t05-multimodel-round1"
    args = _parser(root).parse_args()
    disable_optional_sklearn()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    evidence_root = Path(args.evidence_root).expanduser().absolute()
    manifest = safe_read_json(
        evidence_root,
        "round1_internal_manifest.json",
        kind="manifest",
    )
    model_contract = next(
        item
        for item in manifest["model_contract"]["models"]
        if item["key"] == args.model
    )
    samples = [
        item
        for item in manifest["sample_specs"]
        if item["model_key"] == args.model
        and item["status"] == "pending_generation"
        and (not args.group or item["group"] == args.group)
        and (not args.style or item["style"] in set(args.style))
        and (not args.identity or item["identity_key"] in set(args.identity))
    ]
    samples, reused = partition_generation_samples(
        evidence_root,
        samples,
        model_contract,
        reuse_existing=args.reuse_existing,
        max_samples=args.max_samples,
    )
    if not samples:
        summary = _summary(
            args.model,
            args.group,
            len(reused),
            len(reused),
            0,
            len(reused),
            [],
            0.0,
            {},
        )
        slug = args.model + (f"-{args.group}" if args.group else "-selected")
        safe_write_json(
            evidence_root,
            f"generation-summaries/{slug}.json",
            summary,
            kind="summary",
        )
        print(json.dumps(summary, indent=2))
        return 0

    disk_receipt = evidence_root / "recovery" / "disk-headroom.jsonl"
    disk = require_disk_headroom(
        evidence_root,
        projected_bytes=len(samples) * PROJECTED_SAMPLE_BYTES,
        receipt_path=disk_receipt,
        stage=f"{args.model}:before-model-load",
    )
    for sample in samples:
        validate_sample_references(evidence_root, sample)
    if args.preflight_only:
        lease = acquire_metal_lock(
            evidence_root / ".metal-generation.lock",
            purpose=f"round1-generation:{args.model}:preflight",
        )
        lease.close()
        print(
            json.dumps(
                {
                    "ok": True,
                    "model_key": args.model,
                    "reused_count": len(reused),
                    "pending_after_bound": len(samples),
                    "disk": disk,
                    "model_loaded": False,
                },
                indent=2,
            )
        )
        return 0

    metal_lease = acquire_metal_lock(
        evidence_root / ".metal-generation.lock",
        purpose=f"round1-generation:{args.model}",
    )
    atexit.register(metal_lease.close)
    load_started = time.perf_counter()
    loaded, snapshots = load_requested_models(args.model, samples)
    load_seconds = time.perf_counter() - load_started
    completed, failures = generate_pending_samples(
        evidence_root,
        args.model,
        model_contract,
        samples,
        reused,
        loaded,
        snapshots,
        load_seconds,
    )
    loaded.clear()
    release_sample_mlx_cache()
    metal_lease.close()
    atexit.unregister(metal_lease.close)

    summary = _summary(
        args.model,
        args.group,
        len(samples) + len(reused),
        len(completed),
        len(completed) - len(reused),
        len(reused),
        failures,
        load_seconds,
        snapshots,
    )
    slug = args.model + (f"-{args.group}" if args.group else "-selected")
    safe_write_json(
        evidence_root,
        f"generation-summaries/{slug}.json",
        summary,
        kind="summary",
    )
    print(json.dumps(summary, indent=2))
    return 1 if failures else 0
