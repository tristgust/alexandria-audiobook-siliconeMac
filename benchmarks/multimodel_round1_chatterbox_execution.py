"""In-process execution for pinned Chatterbox Round 1 samples."""

from __future__ import annotations

import atexit
import gc
import io
import json
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from multimodel_round1_chatterbox_cache_policy import (
    AudioMetrics,
    ChatterboxError,
    ChatterboxModel,
    DeviceHandle,
    FULL_CONDITIONALS_REUSE_POLICY,
    MODEL_REPO,
    MODEL_REVISION,
    ROUND_ID,
    RUNTIME_CONTROLS,
    SOURCE_COMMIT,
    T3_MODEL,
    ConditionalsCacheProbe,
    MetalLease,
    TorchRuntime,
)
from multimodel_round1_paths import (
    ContainedPath,
    contained_path,
    parse_artifact_paths,
    safe_atomic_copy,
    safe_atomic_write_bytes,
    safe_atomic_write_text,
    safe_sha256_file,
)
from multimodel_round1_runtime import (
    PROJECTED_SAMPLE_BYTES,
    wav_is_decodable,
)


@dataclass(frozen=True, slots=True)
class ChatterboxExecutionRequest:
    evidence_root: Path
    source_root: Path
    snapshot: Path
    samples: list[dict[str, Any]]
    reused: list[dict[str, Any]]
    groups: tuple[str, ...]
    quarantined_sample_ids: frozenset[str]
    disk_receipt: Path
    mps_high_watermark_ratio: str
    mps_low_watermark_ratio: str
    load_model: Callable[
        [Path, Path], tuple[ChatterboxModel, DeviceHandle, TorchRuntime]
    ]
    sample_fingerprint: Callable[[dict[str, Any]], str]
    resolve_reference: Callable[[Path, dict[str, Any]], ContainedPath]
    audio_metrics: Callable[[ContainedPath, str], AudioMetrics]
    peak_rss_gib: Callable[[], float]
    release_mps_cache: Callable[[TorchRuntime], None]
    require_disk_headroom: Callable[..., dict[str, Any]]
    acquire_metal_lock: Callable[..., MetalLease]


def execute_chatterbox_generation(request: ChatterboxExecutionRequest) -> int:
    import numpy as np
    import soundfile as sf

    lease = request.acquire_metal_lock(
        request.evidence_root / ".metal-generation.lock",
        purpose="round1-generation:chatterbox_multilingual_v3",
    )
    atexit.register(lease.close)
    load_started = time.perf_counter()
    model, _, torch = request.load_model(request.snapshot, request.source_root)
    load_seconds = time.perf_counter() - load_started
    cache_probe = ConditionalsCacheProbe()
    completed: list[dict[str, Any]] = list(request.reused)
    failures: list[dict[str, Any]] = []

    for index, sample in enumerate(request.samples, start=1):
        try:
            request.require_disk_headroom(
                request.evidence_root,
                projected_bytes=(len(request.samples) - index + 1)
                * PROJECTED_SAMPLE_BYTES,
                receipt_path=request.disk_receipt,
                stage="chatterbox_multilingual_v3:before-sample",
                sample_id=sample["sample_id"],
            )
            artifacts = parse_artifact_paths(
                request.evidence_root,
                str(sample["output_file"]),
                str(sample["result_file"]),
            )
            reference = request.resolve_reference(request.evidence_root, sample)
            control = sample["control"]
            cache_key_seen = cache_probe.observe(
                str(sample["reference"]["conditioning_sha256"])
            )
            with tempfile.TemporaryDirectory() as directory:
                staged_reference = contained_path(
                    Path(directory), f"reference{reference.literal.suffix}"
                )
                safe_atomic_copy(reference, staged_reference)
                conditioning_started = time.perf_counter()
                model.prepare_conditionals(
                    str(staged_reference.literal),
                    exaggeration=float(control["exaggeration"]),
                )
                conditioning_seconds = time.perf_counter() - conditioning_started
            torch.manual_seed(int(sample["seed"]))
            started = time.perf_counter()
            wav = model.generate(
                sample["target_text"],
                language_id=str(control.get("language_id") or "en"),
                audio_prompt_path=None,
                exaggeration=float(control["exaggeration"]),
                cfg_weight=float(control["cfg_weight"]),
                temperature=RUNTIME_CONTROLS["temperature"],
                repetition_penalty=RUNTIME_CONTROLS["repetition_penalty"],
                min_p=RUNTIME_CONTROLS["min_p"],
                top_p=RUNTIME_CONTROLS["top_p"],
            )
            generation_seconds = time.perf_counter() - started
            audio = wav.detach().cpu().numpy().reshape(-1).astype(np.float32)
            request.require_disk_headroom(
                request.evidence_root,
                projected_bytes=PROJECTED_SAMPLE_BYTES,
                receipt_path=request.disk_receipt,
                stage="chatterbox_multilingual_v3:before-write",
                sample_id=sample["sample_id"],
            )
            encoded = io.BytesIO()
            sf.write(encoded, audio, int(model.sr), format="WAV")
            with tempfile.TemporaryDirectory() as directory:
                staged_output = contained_path(Path(directory), "output.wav")
                safe_atomic_write_bytes(staged_output, encoded.getvalue())
                if not wav_is_decodable(staged_output.literal, root=Path(directory)):
                    raise ChatterboxError("invalid_audio", str(sample["sample_id"]))
                metrics = request.audio_metrics(staged_output, sample["target_text"])
                audio_sha256 = safe_sha256_file(staged_output)
                safe_atomic_copy(staged_output, artifacts.output)
            runtime = {
                **RUNTIME_CONTROLS,
                "language_id": str(control.get("language_id") or "en"),
                "exaggeration": float(control["exaggeration"]),
                "cfg_weight": float(control["cfg_weight"]),
                "semantic_instruction_directly_consumed": False,
                "numeric_control_proxy": True,
                "source_hardcoded_max_new_tokens": 1000,
                "mps_high_watermark_ratio": request.mps_high_watermark_ratio,
                "mps_low_watermark_ratio": request.mps_low_watermark_ratio,
            }
            record = {
                "schema_version": 1,
                "round_id": ROUND_ID,
                "sample_id": sample["sample_id"],
                "blind_id": sample["blind_id"],
                "sample_fingerprint": request.sample_fingerprint(sample),
                "model_key": "chatterbox_multilingual_v3",
                "model_label": sample["model_label"],
                "model_repo": MODEL_REPO,
                "model_revision": MODEL_REVISION,
                "model_snapshot": str(request.snapshot),
                "source_repository": str(request.source_root),
                "source_commit": SOURCE_COMMIT,
                "t3_model": T3_MODEL,
                "python_executable": sys.executable,
                "identity_key": sample["identity_key"],
                "style": sample["style"],
                "group": sample["group"],
                "target_text_sha256": sample["target_text_sha256"],
                "reference_audio_sha256": sample["reference"].get("conditioning_sha256"),
                "reference_text_sha256": sample["reference"].get("conditioning_transcript_sha256"),
                "control": control,
                "seed": sample["seed"],
                "load_seconds_shared": load_seconds,
                "conditioning_seconds": conditioning_seconds,
                "conditionals_cache_hit": False,
                "conditionals_cache_key_seen_before": cache_key_seen,
                "conditionals_cache_reuse_policy": FULL_CONDITIONALS_REUSE_POLICY,
                "generation_seconds": generation_seconds,
                "real_time_factor": generation_seconds / metrics["duration_seconds"],
                "peak_rss_gib": request.peak_rss_gib(),
                "audio_file": str(artifacts.output.relative),
                "audio_sha256": audio_sha256,
                "audio": metrics,
                "runtime_controls": runtime,
                "post_generation_prosody_applied": False,
                "manual_listening_required": True,
                "production_promotion_allowed": False,
            }
            safe_atomic_write_text(
                artifacts.result,
                json.dumps(record, indent=2, ensure_ascii=False) + "\n",
            )
            completed.append(record)
            progress = {
                "index": index, "count": len(request.samples),
                "sample_id": sample["sample_id"], "rtf": record["real_time_factor"],
                "cache_hit": False, "cache_key_seen_before": cache_key_seen,
            }
            print(json.dumps(progress), flush=True)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            failure = {
                "sample_id": sample["sample_id"], "error_type": type(exc).__name__,
                "error": str(exc)[:3000],
            }
            failures.append(failure)
            print(json.dumps({"failure": failure}), flush=True)
            break
        finally:
            request.release_mps_cache(torch)

    del model
    gc.collect()
    torch.mps.empty_cache()
    lease.close()
    atexit.unregister(lease.close)
    summary = {
        "schema_version": 1,
        "model_key": "chatterbox_multilingual_v3",
        "groups": sorted(set(request.groups)),
        "requested_sample_count": len(request.samples) + len(request.reused),
        "complete_count": len(completed),
        "generated_count": len(completed) - len(request.reused),
        "reused_count": len(request.reused),
        "failure_count": len(failures),
        "failures": failures,
        "load_seconds": load_seconds,
        "model_repo": MODEL_REPO,
        "model_revision": MODEL_REVISION,
        "source_commit": SOURCE_COMMIT,
        "t3_model": T3_MODEL,
        "conditionals_cache_reuse_policy": FULL_CONDITIONALS_REUSE_POLICY,
        "watermark_applied": False,
        "quarantined_sample_ids": sorted(request.quarantined_sample_ids),
        "production_promotion_allowed": False,
    }
    group_slug = "-".join(sorted(set(request.groups)))
    slug = "chatterbox_multilingual_v3" + (f"-{group_slug}" if group_slug else "-selected")
    summary_target = contained_path(
        request.evidence_root, f"generation-summaries/{slug}.json"
    )
    safe_atomic_write_text(
        summary_target, json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
    )
    print(json.dumps(summary, indent=2))
    return 1 if failures else 0
