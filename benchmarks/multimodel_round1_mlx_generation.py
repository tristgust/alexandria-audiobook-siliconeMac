"""Pinned model loading and resumable sample generation for Round 1."""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any

from multimodel_round1_mlx_models import generate_fish, generate_qwen, generate_voxcpm
from multimodel_round1_mlx_loading import (
    SupportedModel,
    assert_never,
    load_requested_models,
)
from multimodel_round1_mlx_moss import generate_moss
from multimodel_round1_mlx_support import (
    ArtifactPathError,
    GenerationInputError,
    InvalidAudioError,
    ManifestPathError,
    MlxRunnerError,
    ReferencePathError,
    _require_mlx,
    audio_metrics,
    peak_rss_gib,
    release_sample_mlx_cache,
    resolve_reference,
    sha256_file,
    write_audio_wav,
)
from multimodel_round1_mlx_paths import artifact_paths_for_sample, safe_write_json
from multimodel_round1_runtime import (
    PROJECTED_SAMPLE_BYTES,
    require_disk_headroom,
    sample_fingerprint,
    wav_is_decodable,
)


def generate_pending_samples(
    evidence_root: Path,
    model_key: str,
    model_contract: dict[str, Any],
    samples: list[dict[str, Any]],
    reused: list[dict[str, Any]],
    loaded: dict[str, Any],
    snapshots: dict[str, str],
    load_seconds: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    completed: list[dict[str, Any]] = list(reused)
    failures: list[dict[str, Any]] = []
    disk_receipt = evidence_root / "recovery" / "disk-headroom.jsonl"
    for index, sample in enumerate(samples, start=1):
        try:
            artifacts = artifact_paths_for_sample(evidence_root, sample)
            output = artifacts.output.literal
            fingerprint = sample_fingerprint(sample, model_contract)
            require_disk_headroom(
                evidence_root,
                projected_bytes=(len(samples) - index + 1) * PROJECTED_SAMPLE_BYTES,
                receipt_path=disk_receipt,
                stage=f"{model_key}:before-sample",
                sample_id=sample["sample_id"],
            )
            reference_path, reference_text = resolve_reference(evidence_root, sample)
            cache_status = None
            _require_mlx().random.seed(int(sample["seed"]))
            started = time.perf_counter()
            match model_key:
                case SupportedModel.VOXCPM2:
                    if reference_path is None:
                        raise GenerationInputError(
                            "voxcpm2", "clone requires reference audio"
                        )
                    audio, sample_rate = generate_voxcpm(
                        loaded["main"], sample, reference_path, evidence_root
                    )
                case SupportedModel.QWEN3_TTS:
                    audio, sample_rate = generate_qwen(
                        loaded["base"],
                        loaded.get("custom"),
                        sample,
                        reference_path,
                        reference_text,
                    )
                case SupportedModel.FISH_S2_PRO:
                    if reference_path is None or not reference_text:
                        raise GenerationInputError(
                            "fish_s2_pro", "clone requires reference audio and text"
                        )
                    audio, sample_rate = generate_fish(
                        loaded["main"],
                        sample,
                        reference_path,
                        reference_text,
                        evidence_root,
                    )
                case SupportedModel.MOSS_TTS_LOCAL_V15:
                    if reference_path is None or not reference_text:
                        raise GenerationInputError(
                            "moss_tts_local_v15",
                            "clone requires reference audio and text",
                        )
                    audio, sample_rate, cache_status = generate_moss(
                        loaded["main"],
                        sample,
                        reference_path,
                        reference_text,
                        loaded["tokenizer_snapshot"],
                        evidence_root,
                        loaded["moss_reference_code_cache"],
                    )
                case unreachable:
                    assert_never(unreachable)
            generation_seconds = time.perf_counter() - started
            require_disk_headroom(
                evidence_root,
                projected_bytes=PROJECTED_SAMPLE_BYTES,
                receipt_path=disk_receipt,
                stage=f"{model_key}:before-write",
                sample_id=sample["sample_id"],
            )
            write_audio_wav(evidence_root, output, audio, sample_rate)
            if not wav_is_decodable(output, root=evidence_root):
                raise InvalidAudioError(model_key)
            metrics = audio_metrics(
                output,
                sample["target_text"],
                root=evidence_root,
            )
            duration = metrics["duration_seconds"]
            record = {
                "schema_version": 1,
                "sample_id": sample["sample_id"],
                "blind_id": sample["blind_id"],
                "sample_fingerprint": fingerprint,
                "model_key": model_key,
                "model_label": sample["model_label"],
                "model_snapshots": snapshots,
                "identity_key": sample["identity_key"],
                "style": sample["style"],
                "group": sample["group"],
                "target_text_sha256": sample["target_text_sha256"],
                "reference_audio_sha256": sample["reference"].get("conditioning_sha256"),
                "reference_text_sha256": sample["reference"].get(
                    "conditioning_transcript_sha256"
                ),
                "control": sample["control"],
                "seed": sample["seed"],
                "load_seconds_shared": load_seconds,
                "generation_seconds": generation_seconds,
                "real_time_factor": generation_seconds / duration,
                "peak_rss_gib": peak_rss_gib(),
                "audio_file": str(artifacts.output.relative),
                "audio_sha256": sha256_file(output, root=evidence_root),
                "audio": metrics,
                "reference_code_cache_status": cache_status,
                "reference_code_cache_hit": cache_status in {"memory", "disk"},
                "post_generation_prosody_applied": False,
                "production_promotion_allowed": False,
            }
            if model_key == SupportedModel.MOSS_TTS_LOCAL_V15:
                record["runtime_controls"] = {
                    "mode": "generation",
                    "language": sample["control"]["language"],
                    "max_tokens": int(sample["control"]["max_tokens"]),
                    "audio_temperature": float(sample["control"]["audio_temperature"]),
                    "audio_top_p": float(sample["control"]["audio_top_p"]),
                    "audio_top_k": int(sample["control"]["audio_top_k"]),
                    "n_vq_for_inference": int(
                        sample["control"]["n_vq_for_inference"]
                    ),
                    "stream": False,
                }
            else:
                record["runtime_controls"] = sample["control"]
            safe_write_json(
                evidence_root,
                str(artifacts.result.relative),
                record,
                kind="artifact",
            )
            completed.append(record)
            print(
                json.dumps(
                    {
                    "index": index,
                    "count": len(samples),
                    "sample_id": sample["sample_id"],
                    "identity": sample["identity_key"],
                    "style": sample["style"],
                    "rtf": record["real_time_factor"],
                    }
                ),
                flush=True,
            )
        except (ArtifactPathError, ManifestPathError, ReferencePathError):
            raise
        except (MlxRunnerError, OSError, TypeError, ValueError, RuntimeError) as exc:
            failure = {
                "sample_id": sample["sample_id"],
                "identity_key": sample["identity_key"],
                "style": sample["style"],
                "error_type": type(exc).__name__,
                "error": str(exc)[:3000],
            }
            failures.append(failure)
            print(json.dumps({"failure": failure}), flush=True)
            break
        finally:
            release_sample_mlx_cache()
    return completed, failures
