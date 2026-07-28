"""Strict receipt contracts for historical multimodel Round 1 generators."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from multimodel_round1_paths import (
    PathSafetyError,
    SafeIdentifier,
    contained_path,
    safe_read_text,
)
from multimodel_round1_runtime import (
    GenerationIntegrityError,
    canonical_json,
    sample_fingerprint,
    sha256_file,
    sha256_text,
    validate_generation_pair,
)


ROUND_ID = "alexandria_multimodel_expressive_clone_round1_v1"
CHATTER_REPO = "ResembleAI/chatterbox"
CHATTER_REVISION = "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"
CHATTER_COMMIT = "5de7a54aa4e5e2baadb0182dde554908b48b85c2"
CHATTER_FINGERPRINT_RUNTIME = {
    "device": "mps",
    "cpu_staged_checkpoint_load": True,
    "watermark_applied": False,
    "watermark_reason": "perth_backend_unavailable_on_macos",
    "temperature": 0.8,
    "repetition_penalty": 1.2,
    "min_p": 0.05,
    "top_p": 1.0,
}
INDEX_MODEL_DIR = "/Users/tristan/pinokio/cache/alexandria-evaluation/indextts2/huggingface/models--IndexTeam--IndexTTS-2/snapshots/740dcaff396282ffb241903d150ac011cd4b1ede"
INDEX_AUX_ROOT = "/Users/tristan/pinokio/cache/alexandria-evaluation/indextts2/aux-flat"
INDEX_RUNTIME = {
    "device": "mps",
    "use_fp16": False,
    "mps_fast_math": True,
    "mps_prefer_metal": True,
    "num_beams": 1,
    "greedy_generation": True,
    "diffusion_steps": 8,
}
INDEX_PROFILE = {"candidate": "IndexTTS2", **INDEX_RUNTIME, "persistent_worker_count": 2}


def _hash(value: Any) -> str:
    return sha256_text(canonical_json(value))


def _index_hash(value: Any) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return sha256_text(serialized)


def _mismatch(code: str, sample: dict[str, Any]) -> None:
    raise GenerationIntegrityError(code, str(sample["sample_id"]))


def _require_fields(
    actual: dict[str, Any], expected: dict[str, Any], sample: dict[str, Any]
) -> None:
    for key, value in expected.items():
        if actual.get(key) != value:
            _mismatch(f"receipt_{key}", sample)


def _require_model(
    model: dict[str, Any], expected: dict[str, Any], sample: dict[str, Any]
) -> None:
    for key, value in expected.items():
        if model.get(key) != value:
            _mismatch(f"model_contract_{key}", sample)


def _chatter_fingerprint(sample: dict[str, Any]) -> str:
    return _hash(
        {
            "round_id": ROUND_ID,
            "sample_id": sample["sample_id"],
            "model_repo": CHATTER_REPO,
            "model_revision": CHATTER_REVISION,
            "source_commit": CHATTER_COMMIT,
            "t3_model": "v3",
            "identity_key": sample["identity_key"],
            "style": sample["style"],
            "target_text_sha256": sample["target_text_sha256"],
            "reference_audio_sha256": sample["reference"].get("conditioning_sha256"),
            "control": sample["control"],
            "seed": sample["seed"],
            "runtime": CHATTER_FINGERPRINT_RUNTIME,
        }
    )


def _chatter_runtime(sample: dict[str, Any]) -> dict[str, Any]:
    control = sample["control"]
    return {
        "device": "mps",
        "language_id": control["language_id"],
        "exaggeration": control["exaggeration"],
        "cfg_weight": control["cfg_weight"],
        "temperature": 0.8,
        "repetition_penalty": 1.2,
        "min_p": 0.05,
        "top_p": 1.0,
        "semantic_instruction_directly_consumed": False,
        "numeric_control_proxy": True,
        "cpu_staged_checkpoint_load": True,
        "watermark_applied": False,
        "watermark_reason": "perth_backend_unavailable_on_macos",
    }


def _chatter_runtime_profiles(sample: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    original = _chatter_runtime(sample)
    current = {
        **original,
        "source_hardcoded_max_new_tokens": 1000,
        "mps_high_watermark_ratio": "0.45",
        "mps_low_watermark_ratio": "0.40",
    }
    return original, current


def _read_index_sample(evidence: Path, sample: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    try:
        group = SafeIdentifier(str(sample["group"]))
        manifest_target = contained_path(
            evidence, f"indextts2_round1_manifest_{group}.json"
        )
        manifest = json.loads(safe_read_text(manifest_target))
    except (OSError, json.JSONDecodeError, PathSafetyError) as exc:
        raise GenerationIntegrityError("index_manifest_invalid", str(sample["sample_id"])) from exc
    manifest_path = manifest_target.literal
    if manifest.get("round_id") != ROUND_ID or manifest.get("runtime_profile") != INDEX_PROFILE:
        _mismatch("index_manifest_runtime_profile", sample)
    matches = [row for row in manifest.get("samples", []) if row.get("sample_id") == sample["sample_id"]]
    if len(matches) != 1:
        _mismatch("index_manifest_sample", sample)
    active = matches[0]
    reference = sample["reference"]
    try:
        reference_audio = contained_path(
            evidence / "references", str(reference["conditioning_file"])
        ).literal
        emotion_audio = contained_path(
            evidence / "references", str(reference["acted_emotion_reference_file"])
        ).literal
        output = contained_path(evidence, str(sample["output_file"])).literal
        result = contained_path(evidence, str(sample["result_file"])).literal
    except PathSafetyError as exc:
        raise GenerationIntegrityError(
            "index_manifest_path", str(sample["sample_id"])
        ) from exc
    expected = {
        "sample_id": sample["sample_id"],
        "blind_id": sample["blind_id"],
        "model_key": "indextts2",
        "speaker": sample["identity_key"],
        "identity_key": sample["identity_key"],
        "identity_label": sample["identity_review_name"],
        "style": sample["style"],
        "group": sample["group"],
        "text": sample["target_text"],
        "reference_audio": str(reference_audio),
        "reference_audio_sha256": reference["conditioning_sha256"],
        "emotion_audio_prompt": str(emotion_audio),
        "emotion_audio_sha256": reference["acted_emotion_reference_sha256"],
        "emotion_strength": sample["control"]["emo_alpha"],
        "source_instruction_sha256": sample["control"]["requested_instruction_sha256"],
        "seed": sample["seed"],
        "control": sample["control"],
        "output_file": str(output),
        "result_file": str(result),
    }
    for key, value in expected.items():
        if active.get(key) != value:
            _mismatch(f"index_manifest_{key}", sample)
    return manifest_path, active


def _index_fingerprint(evidence: Path, sample: dict[str, Any]) -> str:
    manifest_path, active = _read_index_sample(evidence, sample)
    runtime = {
        "round_id": ROUND_ID,
        "model_dir": INDEX_MODEL_DIR,
        "aux_root": INDEX_AUX_ROOT,
        "device": "mps",
        "diffusion_steps": 8,
        "greedy": True,
    }
    return _index_hash(
        {"sample": active, "runtime": runtime, "manifest_sha256": sha256_file(manifest_path)}
    )


def expected_sample_fingerprint(
    evidence: Path, sample: dict[str, Any], model: dict[str, Any]
) -> str:
    if sample["model_key"] == "chatterbox_multilingual_v3":
        return _chatter_fingerprint(sample)
    if sample["model_key"] == "indextts2":
        return _index_fingerprint(evidence, sample)
    return sample_fingerprint(sample, model)


def validate_round1_generation_pair(
    evidence: Path, sample: dict[str, Any], model: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    model_key = sample["model_key"]
    if model_key == "chatterbox_multilingual_v3":
        _require_model(model, {"key": model_key, "model_repo": CHATTER_REPO, "revision": CHATTER_REVISION, "runtime": "official chatterbox-tts PyTorch MPS path with t3_model=v3"}, sample)
    elif model_key == "indextts2":
        _require_model(model, {"key": model_key, "model_repo": "IndexTeam/IndexTTS-2", "revision": "740dcaff396282ffb241903d150ac011cd4b1ede", "runtime": "pinned IndexTTS2 PyTorch MPS evaluation runtime"}, sample)
    fingerprint = expected_sample_fingerprint(evidence, sample, model)
    receipt, audio_sha = validate_generation_pair(
        evidence,
        sample,
        model,
        expected_fingerprint=fingerprint,
        require_control=model_key != "indextts2",
    )
    common = {
        "round_id": ROUND_ID,
        "group": sample["group"],
        "identity_key": sample["identity_key"],
        "style": sample["style"],
        "seed": sample["seed"],
    }
    if model_key == "chatterbox_multilingual_v3":
        _require_fields(receipt, common, sample)
        chatter = {
            "model_label": model["label"],
            "model_repo": CHATTER_REPO,
            "model_revision": CHATTER_REVISION,
            "source_commit": CHATTER_COMMIT,
            "t3_model": "v3",
            "reference_audio_sha256": sample["reference"]["conditioning_sha256"],
            "reference_text_sha256": sample["reference"]["conditioning_transcript_sha256"],
        }
        _require_fields(receipt, chatter, sample)
        if receipt.get("runtime_controls") not in _chatter_runtime_profiles(sample):
            _mismatch("receipt_runtime_controls", sample)
    elif model_key == "indextts2":
        _require_fields(receipt, common, sample)
        index = {
            "source_instruction_sha256": sample["control"]["requested_instruction_sha256"],
            "reference_audio_sha256": sample["reference"]["conditioning_sha256"],
            "emotion_audio_sha256": sample["reference"]["acted_emotion_reference_sha256"],
            "emotion_strength": sample["control"]["emo_alpha"],
            "runtime_controls": INDEX_RUNTIME,
        }
        _require_fields(receipt, index, sample)
    return receipt, audio_sha
