#!/usr/bin/env python3
"""Evaluate one cumulative Round 1 review group.

The evaluator is local-only and model-blind in its public output. It computes:
- pinned Whisper Base transcript and WER against each target line;
- Qwen speaker-embedding cosine against the correct identity reference for each
  sample, including style-specific Ryan and model-native anchors;
- compact onset/silence diagnostics useful for spotting clicks or empty audio.
"""

from __future__ import annotations

import argparse
import atexit
import json
from pathlib import Path
from typing import Any

import mlx.core as mx
import numpy as np

from multimodel_round1_paths import (  # noqa: E402
    SafeIdentifier,
    contained_path,
    contained_path_from_full,
    safe_read_text,
    safe_sha256_file,
)
from multimodel_round1_runtime import (  # noqa: E402
    PROJECTED_SAMPLE_BYTES,
    acquire_metal_lock,
    atomic_write_json,
    require_disk_headroom,
    validate_sample_references,
    wav_is_decodable,
)
from multimodel_round1_objective_metrics import (  # noqa: E402
    ObjectiveIntegrityError,
    WHISPER_REPO,
    WHISPER_REVISION,
    WHISPER_VERSION,
    audio_diagnostics,
    generation_artifacts,
    load_whisper,
    read_existing_measurements,
    reference_path,
    select_generated_samples,
    speaker_embedding,
    word_error_rate,
)
from run_multimodel_round1_mlx import (  # noqa: E402
    disable_optional_sklearn,
    exact_snapshot,
    load_model,
    sha256_text,
)

DEFAULT_EVIDENCE = Path(__file__).resolve().parents[1] / ".omo/evidence/b17-t05-multimodel-round1"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--group", required=True)
    parser.add_argument("--model", action="append")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    disable_optional_sklearn()
    evidence_root = Path(args.evidence_root).expanduser().resolve()
    group = SafeIdentifier(args.group)
    manifest_path = contained_path(evidence_root, "round1_internal_manifest.json")
    manifest = json.loads(safe_read_text(manifest_path))
    samples = select_generated_samples(
        evidence_root,
        manifest["sample_specs"],
        str(group),
        set(args.model) if args.model else None,
    )
    if not samples:
        raise ObjectiveIntegrityError(
            f"No generated samples found for group {args.group!r}."
        )

    for sample in samples:
        validate_sample_references(evidence_root, sample)
        artifacts = generation_artifacts(evidence_root, sample)
        receipt = json.loads(safe_read_text(artifacts.result))
        if receipt.get("audio_sha256") != safe_sha256_file(artifacts.output):
            raise ObjectiveIntegrityError(
                f"Generation audio hash mismatch: {sample['sample_id']}"
            )
        if not wav_is_decodable(artifacts.output.literal, root=evidence_root):
            raise ObjectiveIntegrityError(
                f"Generation WAV is invalid: {sample['sample_id']}"
            )

    output_path = evidence_root / "objective" / f"{group}.json"
    output_target = contained_path(evidence_root, f"objective/{group}.json")
    existing = read_existing_measurements(output_target, force=args.force)

    disk_receipt = evidence_root / "recovery" / "disk-headroom.jsonl"
    require_disk_headroom(
        evidence_root,
        projected_bytes=PROJECTED_SAMPLE_BYTES,
        receipt_path=disk_receipt,
        stage=f"objective:{args.group}:before-model-load",
    )
    metal_lease = acquire_metal_lock(
        evidence_root / ".metal-generation.lock",
        purpose=f"round1-objective:{args.group}",
    )
    atexit.register(metal_lease.close)
    whisper = load_whisper()
    whisper_snapshot = exact_snapshot(WHISPER_REPO, WHISPER_REVISION)
    speaker_model, speaker_snapshot = load_model(
        "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
        "e7dd0585652209fa0d7783659aad4e8a324de11c",
    )
    reference_embeddings: dict[str, np.ndarray] = {}
    measurements: dict[str, Any] = dict(existing)

    for index, sample in enumerate(samples, start=1):
        artifacts = generation_artifacts(evidence_root, sample)
        receipt = json.loads(safe_read_text(artifacts.result))
        output = artifacts.output.literal
        current_audio_sha = safe_sha256_file(artifacts.output)
        prior = measurements.get(sample["sample_id"])
        if (
            not args.force
            and prior
            and prior.get("audio_sha256") == current_audio_sha
            and prior.get("target_text_sha256") == sample["target_text_sha256"]
        ):
            continue

        transcript_result = whisper.transcribe(
            str(output),
            path_or_hf_repo=str(whisper_snapshot),
            language="en",
            word_timestamps=False,
            condition_on_previous_text=False,
            verbose=False,
        )
        transcript = str(transcript_result.get("text") or "").strip()
        ref = reference_path(evidence_root, sample)
        reference_hash = safe_sha256_file(
            contained_path_from_full(evidence_root, ref)
        )
        if reference_hash not in reference_embeddings:
            reference_embeddings[reference_hash] = speaker_embedding(
                speaker_model, evidence_root, ref
            )
        output_embedding = speaker_embedding(speaker_model, evidence_root, output)
        measurements[sample["sample_id"]] = {
            "sample_id": sample["sample_id"],
            "blind_id": sample["blind_id"],
            "model_key": sample["model_key"],
            "identity_key": sample["identity_key"],
            "style": sample["style"],
            "audio_file": sample["output_file"],
            "audio_sha256": current_audio_sha,
            "target_text_sha256": sample["target_text_sha256"],
            "automatic_transcript": transcript,
            "automatic_transcript_sha256": sha256_text(transcript),
            "word_error_rate": word_error_rate(sample["target_text"], transcript),
            "speaker_reference_sha256": reference_hash,
            "speaker_cosine_to_expected_identity": float(
                np.dot(reference_embeddings[reference_hash], output_embedding)
            ),
            "audio_diagnostics": audio_diagnostics(evidence_root, output),
            "generation_receipt_sha256": safe_sha256_file(artifacts.result),
            "sample_fingerprint": receipt["sample_fingerprint"],
        }
        require_disk_headroom(
            evidence_root,
            projected_bytes=PROJECTED_SAMPLE_BYTES,
            receipt_path=disk_receipt,
            stage=f"objective:{args.group}:before-checkpoint",
            sample_id=sample["sample_id"],
        )
        atomic_write_json(
            output_path,
            {
                "schema_version": 1,
                "round_id": manifest["round_id"],
                "group": args.group,
                "model_filter": args.model,
                "in_progress": True,
                "measurement_count": len(measurements),
                "measurements": measurements,
                "production_promotion_allowed": False,
            },
            root=evidence_root,
        )
        print(
            json.dumps(
                {
                    "index": index,
                    "count": len(samples),
                    "sample_id": sample["sample_id"],
                    "wer": measurements[sample["sample_id"]]["word_error_rate"],
                    "cosine": measurements[sample["sample_id"]][
                        "speaker_cosine_to_expected_identity"
                    ],
                }
            ),
            flush=True,
        )

    measurements = {item["sample_id"]: measurements[item["sample_id"]] for item in samples}
    selected = list(measurements.values())
    wers = [float(item["word_error_rate"]) for item in selected]
    cosines = [float(item["speaker_cosine_to_expected_identity"]) for item in selected]
    payload = {
        "schema_version": 1,
        "round_id": manifest["round_id"],
        "group": args.group,
        "model_filter": args.model,
        "sample_count": len(selected),
        "perfect_transcript_count": sum(value == 0.0 for value in wers),
        "nonzero_wer_count": sum(value > 0.0 for value in wers),
        "max_word_error_rate": max(wers),
        "speaker_cosine_range": [min(cosines), max(cosines)],
        "whisper": {
            "repo": WHISPER_REPO,
            "revision": WHISPER_REVISION,
            "runtime": f"mlx-whisper=={WHISPER_VERSION}",
            "snapshot": str(whisper_snapshot),
        },
        "speaker_evaluator": {
            "repo": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
            "revision": "e7dd0585652209fa0d7783659aad4e8a324de11c",
            "snapshot": str(speaker_snapshot),
            "reference_group_count": len(reference_embeddings),
        },
        "measurements": measurements,
        "manual_blinded_review_required": True,
        "production_promotion_allowed": False,
    }
    require_disk_headroom(
        evidence_root,
        projected_bytes=PROJECTED_SAMPLE_BYTES,
        receipt_path=disk_receipt,
        stage=f"objective:{args.group}:before-final-write",
    )
    atomic_write_json(output_path, payload, root=evidence_root)
    reference_embeddings.clear()
    del speaker_model
    mx.clear_cache()
    metal_lease.close()
    atexit.unregister(metal_lease.close)
    print(
        json.dumps(
            {
                "output": str(output_path),
                "sample_count": payload["sample_count"],
                "perfect_transcript_count": payload["perfect_transcript_count"],
                "speaker_cosine_range": payload["speaker_cosine_range"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
