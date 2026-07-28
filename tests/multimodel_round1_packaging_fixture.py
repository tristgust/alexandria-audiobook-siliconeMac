"""Small deterministic evidence fixture for Round 1 package tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TypeAlias
import wave


ROUND_ID = "alexandria_multimodel_expressive_clone_round1_v1"
JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class FixtureContractError(RuntimeError):
    pass


def canonical_hash(value: dict[str, JsonValue]) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def write_wav(path: Path, value: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8_000)
        handle.writeframes(value.to_bytes(2, "little", signed=True) * 80)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_fixture(
    evidence: Path,
) -> tuple[list[dict[str, JsonValue]], list[dict[str, JsonValue]]]:
    models: list[dict[str, JsonValue]] = [
        {"key": "model_alpha", "label": "Secret Alpha"},
        {"key": "model_beta", "label": "Secret Beta"},
    ]
    reference_specs = (
        ("sample_internal_1", "blind_one", "model_alpha", "narrator", "Narrator", "supplied_recording_clone", 100),
        ("sample_internal_2", "blind_two", "model_beta", "native_beta_voice", "Beta Star", "model_beta_native_voice", 200),
    )
    samples: list[dict[str, JsonValue]] = []
    for index, spec in enumerate(reference_specs):
        sample_id, blind_id, model_key, identity_key, review_name, kind, value = spec
        reference_path = evidence / "references" / f"reference-{index}.wav"
        write_wav(reference_path, value)
        target_text = f"Target sentence {index}."
        target_sha = hashlib.sha256(target_text.encode()).hexdigest()
        control: dict[str, JsonValue] = {
            "requested_instruction": f"Style {index}",
            "target_text_sha256": target_sha,
            "strength": index,
        }
        sample: dict[str, JsonValue] = {
            "sample_id": sample_id,
            "blind_id": blind_id,
            "model_key": model_key,
            "model_label": next(item["label"] for item in models if item["key"] == model_key),
            "identity_key": identity_key,
            "identity_review_name": review_name,
            "identity_kind": kind,
            "style": "neutral",
            "style_label": "Neutral",
            "group": "baseline",
            "target_text": target_text,
            "target_text_sha256": target_sha,
            "reference": {
                "source_file": reference_path.name,
                "source_sha256": sha256_file(reference_path),
                "conditioning_file": reference_path.name,
                "conditioning_sha256": sha256_file(reference_path),
                "conditioning_transcript": "Reference words.",
                "conditioning_transcript_sha256": hashlib.sha256(b"Reference words.").hexdigest(),
            },
            "control": control,
            "seed": 6200 + index,
            "output_file": f"outputs/{model_key}/{sample_id}.wav",
            "result_file": f"outputs/{model_key}/{sample_id}.json",
            "status": "pending_generation",
        }
        samples.append(sample)
    internal = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "groups": {"baseline": {"key": "baseline", "label": "Baseline"}},
        "styles": [{"key": "neutral", "label": "Neutral", "group": "baseline", "target_text": "Target", "instruction": "Style"}],
        "model_contract": {"models": models},
        "sample_specs": samples,
        "blocked_cells": [],
    }
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "round1_internal_manifest.json").write_text(
        json.dumps(internal), encoding="utf-8"
    )
    for sample in samples:
        output = evidence / str(sample["output_file"])
        write_wav(output, 1_000 + int(sample["seed"]))
        model = next(item for item in models if item["key"] == sample["model_key"])
        reference = sample["reference"]
        if not isinstance(reference, dict):
            raise FixtureContractError("fixture reference must be an object")
        relevant = {
            "round": ROUND_ID,
            "sample_id": sample["sample_id"],
            "model": model,
            "identity_key": sample["identity_key"],
            "style": sample["style"],
            "target_text_sha256": sample["target_text_sha256"],
            "reference": {
                key: reference.get(key)
                for key in (
                    "conditioning_sha256",
                    "conditioning_transcript_sha256",
                    "acted_emotion_reference_sha256",
                )
            },
            "control": sample["control"],
            "seed": sample["seed"],
        }
        receipt = {
            "sample_id": sample["sample_id"],
            "blind_id": sample["blind_id"],
            "sample_fingerprint": canonical_hash(relevant),
            "model_key": sample["model_key"],
            "target_text_sha256": sample["target_text_sha256"],
            "control": sample["control"],
            "audio_file": sample["output_file"],
            "audio_sha256": sha256_file(output),
        }
        result = evidence / str(sample["result_file"])
        result.write_text(json.dumps(receipt), encoding="utf-8")
    return samples, models
