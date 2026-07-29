#!/usr/bin/env python3
"""Evaluate and package the focused Chris/Roz multimodel blind round.

Objective evaluation is resumable by batch. Public packaging copies only blind
candidate audio and the fixed clean-actor identity anchors. Model names,
reference tiers, repeats, controls, seeds, and source sample IDs remain solely
in the private answer key.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
from typing import Any, Iterable, Mapping

import mlx_whisper
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
import torch
from speechbrain.inference.classifiers import EncoderClassifier

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = ROOT / ".omo/evidence/chris-roz-multimodel-round1-v1"
ASSET_ROOT = ROOT / "benchmarks/multimodel_review_assets"
ECAPA_MODEL = Path(
    "/Users/tristan/.devspace/worktrees/"
    "alexandria-audiobook.git-c2133ab9/.omo/evidence/"
    "cwej-roz-voice-evaluation/private/models/"
    "spkrec-ecapa-voxceleb-0f99f2d0"
)
WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"
ASSET_FILES = (
    "index.html",
    "styles.css",
    "review-core.js",
    "review-content.js",
    "review-navigation.js",
    "review-io.js",
    "app.js",
)


class PackageError(RuntimeError):
    """Raised when objective evidence or blind packaging is incomplete."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    if not path.is_file():
        raise PackageError(f"Required JSON file is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_value(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "twenty": "20", "thirty": "30", "forty": "40",
    "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80",
    "ninety": "90",
}


def normalized_words(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9']+", text.casefold().replace("%", " percent"))
    return [_NUMBER_WORDS.get(token, token) for token in tokens]


def word_error_rate(expected: str, observed: str) -> float:
    left = normalized_words(expected)
    right = normalized_words(observed)
    previous = list(range(len(right) + 1))
    for index, left_word in enumerate(left, start=1):
        current = [index]
        for right_index, right_word in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_word != right_word),
                )
            )
        previous = current
    return previous[-1] / max(1, len(left))


def audio_metrics(path: Path) -> dict[str, Any]:
    audio, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = np.mean(audio, axis=1)
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(mono), dtype=np.float64))) if mono.size else 0.0
    frame = max(1, int(sample_rate * 0.02))
    hop = max(1, frame // 2)
    if mono.size >= frame:
        windows = np.lib.stride_tricks.sliding_window_view(mono, frame)[::hop]
        frame_rms = np.sqrt(np.mean(np.square(windows), axis=1) + 1e-12)
        silence_fraction = float(np.mean(frame_rms < 10 ** (-45.0 / 20.0)))
    else:
        silence_fraction = float(rms < 10 ** (-45.0 / 20.0))
    return {
        "duration_seconds": float(len(mono) / sample_rate),
        "sample_rate": int(sample_rate),
        "channels": int(audio.shape[1]),
        "frames": int(audio.shape[0]),
        "rms_dbfs": 20.0 * math.log10(max(rms, 1e-9)),
        "peak_dbfs": 20.0 * math.log10(max(peak, 1e-9)),
        "clipping_fraction": float(np.mean(np.abs(mono) >= 0.995)) if mono.size else 0.0,
        "silence_fraction": silence_fraction,
    }


def load_16k(path: Path) -> torch.Tensor:
    audio, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = np.mean(audio, axis=1)
    if sample_rate != 16000:
        mono = resample_poly(mono, 16000, sample_rate).astype(np.float32)
    return torch.from_numpy(mono)[None, :]


def embedding(model: EncoderClassifier, path: Path) -> np.ndarray:
    with torch.no_grad():
        vector = model.encode_batch(load_16k(path)).squeeze().cpu().numpy()
    vector = np.asarray(vector, dtype=np.float32).reshape(-1)
    return vector / (np.linalg.norm(vector) + 1e-12)


def result_audio(evidence: Path, spec: Mapping[str, Any]) -> tuple[Path, dict[str, Any], Path]:
    result_path = evidence / str(spec["result_file"])
    receipt = read_json(result_path)
    if receipt.get("sample_id") != spec["sample_id"]:
        raise PackageError(f"Generation receipt belongs to the wrong sample: {result_path}")
    value = receipt.get("audio_file") or receipt.get("output_file") or spec["output_file"]
    audio = Path(str(value)).expanduser()
    if not audio.is_absolute():
        audio = evidence / audio
    audio = audio.resolve()
    expected = str(receipt.get("audio_sha256") or "")
    if not audio.is_file() or not expected or sha256_file(audio) != expected:
        raise PackageError(f"Generated audio is missing or hash-invalid: {spec['sample_id']}")
    return audio, receipt, result_path


class ObjectiveRuntime:
    def __init__(self, evidence: Path, internal: Mapping[str, Any]) -> None:
        self.evidence = evidence
        self.internal = internal
        self.speaker_model = EncoderClassifier.from_hparams(source=str(ECAPA_MODEL))
        self.anchor_embeddings: dict[str, np.ndarray] = {}
        for identity in ("chris", "roz"):
            reference = internal["references"][f"{identity}:clean_actor"]
            path = evidence / reference["audio_file"]
            if sha256_file(path) != reference["audio_sha256"]:
                raise PackageError(f"Clean actor anchor changed: {identity}")
            self.anchor_embeddings[identity] = embedding(self.speaker_model, path)

    def evaluate(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        audio, receipt, result_path = result_audio(self.evidence, spec)
        audio_hash = sha256_file(audio)
        fingerprint = sha256_value(
            {
                "round_id": self.internal["round_id"],
                "sample_id": spec["sample_id"],
                "audio_sha256": audio_hash,
                "target_text_sha256": spec["target_text_sha256"],
                "identity_anchor_sha256": self.internal["references"][f"{spec['identity_key']}:clean_actor"]["audio_sha256"],
                "whisper_model": WHISPER_MODEL,
                "speaker_model": "speechbrain/spkrec-ecapa-voxceleb@0f99f2d0",
            }
        )
        output = self.evidence / "private/objective" / f"{spec['sample_id']}.json"
        if output.is_file():
            prior = read_json(output)
            if prior.get("fingerprint") == fingerprint and prior.get("audio_sha256") == audio_hash:
                return prior
        transcript_result = mlx_whisper.transcribe(
            str(audio),
            path_or_hf_repo=WHISPER_MODEL,
            language="en",
            condition_on_previous_text=False,
            word_timestamps=False,
            verbose=False,
        )
        transcript = str(transcript_result.get("text") or "").strip()
        output_embedding = embedding(self.speaker_model, audio)
        record = {
            "schema_version": 1,
            "round_id": self.internal["round_id"],
            "sample_id": spec["sample_id"],
            "blind_id": spec["blind_id"],
            "fingerprint": fingerprint,
            "model_key": spec["model_key"],
            "identity_key": spec["identity_key"],
            "reference_tier": spec["reference_tier"],
            "style": spec["style"],
            "repeat": spec["repeat"],
            "audio_file": str(audio),
            "audio_sha256": audio_hash,
            "target_text_sha256": spec["target_text_sha256"],
            "automatic_transcript": transcript,
            "automatic_transcript_sha256": hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
            "word_error_rate": word_error_rate(str(spec["target_text"]), transcript),
            "exact_normalized_text": normalized_words(str(spec["target_text"])) == normalized_words(transcript),
            "speaker_reference_sha256": self.internal["references"][f"{spec['identity_key']}:clean_actor"]["audio_sha256"],
            "speaker_cosine_to_expected_identity": float(np.dot(self.anchor_embeddings[str(spec["identity_key"])], output_embedding)),
            "audio_diagnostics": audio_metrics(audio),
            "generation_receipt": str(result_path),
            "generation_receipt_sha256": sha256_file(result_path),
            "sample_fingerprint": receipt.get("sample_fingerprint"),
            "whisper_model": WHISPER_MODEL,
            "speaker_model": "speechbrain/spkrec-ecapa-voxceleb@0f99f2d0",
            "manual_blind_review_required": True,
            "production_promotion_allowed": False,
            "completed_at": utc_now(),
        }
        write_json(output, record)
        return record


def objective_records(evidence: Path, internal: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    missing = []
    for spec in internal["sample_specs"]:
        path = evidence / "private/objective" / f"{spec['sample_id']}.json"
        if not path.is_file():
            missing.append(spec["sample_id"])
            continue
        row = read_json(path)
        audio, _, _ = result_audio(evidence, spec)
        if row.get("audio_sha256") != sha256_file(audio):
            missing.append(spec["sample_id"])
            continue
        result[str(spec["sample_id"])] = row
    if missing:
        raise PackageError(f"Objective measurements are incomplete: {len(missing)} missing or stale")
    return result


def copy_public(source: Path, target: Path, expected_sha: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or sha256_file(target) != expected_sha:
        shutil.copy2(source, target)
    if sha256_file(target) != expected_sha:
        raise PackageError(f"Public copy hash changed: {target}")


def package_review(evidence: Path, internal: Mapping[str, Any]) -> dict[str, Any]:
    objectives = objective_records(evidence, internal)
    review = evidence / "review"
    if review.exists():
        shutil.rmtree(review)
    review.mkdir(parents=True)
    for name in ASSET_FILES:
        shutil.copy2(ASSET_ROOT / name, review / name)
    index = (review / "index.html").read_text(encoding="utf-8")
    index = index.replace("Alexandria Round 1 Blind Review", "Chris and Roz multimodel blind review")
    index = index.replace("Multimodel expressive-clone blind review", "Chris and Roz multimodel blind review")
    index = index.replace(
        "Round 1 · grouped by performance, never by model · candidates remixed on every style",
        "96 candidates · model and reference tier hidden · grouped by delivery",
    )
    (review / "index.html").write_text(index, encoding="utf-8")

    public_identities: dict[str, Any] = {}
    for identity, label in (("chris", "Chris Cwej"), ("roz", "Roz Forrester")):
        reference = internal["references"][f"{identity}:clean_actor"]
        source = evidence / reference["audio_file"]
        relative = Path("reference-audio") / f"{reference['audio_sha256']}.wav"
        copy_public(source, review / relative, reference["audio_sha256"])
        public_identities[identity] = {
            "identity_key": identity,
            "review_name": label,
            "kind": "fixed_identity_anchor",
            "conditioning_transcript": reference["transcript"],
            "conditioning_transcript_sha256": reference["transcript_sha256"],
            "original_audio": str(relative),
            "conditioning_audio": str(relative),
        }

    style_definitions = {
        "neutral": {
            "key": "neutral", "label": "Neutral", "group": "baseline",
            "target_text": "Character-specific neutral test lines.",
            "instruction": "Natural, controlled, character-appropriate delivery with clear diction and stable identity.",
        },
        "dry_humour": {
            "key": "dry_humour", "label": "Dry humour", "group": "subtext",
            "target_text": "Character-specific dry-humour test lines.",
            "instruction": "Dry, understated humour with precise ironic timing and no broad comedy.",
        },
        "urgent_authority": {
            "key": "urgent_authority", "label": "Urgent authority", "group": "threat",
            "target_text": "Character-specific urgent-authority test lines.",
            "instruction": "Sustained urgent command or protective authority with control, precision, and credible danger.",
        },
        "vulnerability": {
            "key": "vulnerability", "label": "Restrained vulnerability", "group": "vulnerability",
            "target_text": "Character-specific vulnerability and concern test lines.",
            "instruction": "Restrained vulnerability or concern beneath the character's normal control.",
        },
    }
    groups = {
        "baseline": {"key": "baseline", "label": "Baseline", "description": "Neutral identity and reference-tier stability.", "styles": ["neutral"]},
        "subtext": {"key": "subtext", "label": "Subtext and humour", "description": "Dry timing and ironic subtext.", "styles": ["dry_humour"]},
        "threat": {"key": "threat", "label": "Urgency and command", "description": "Protective or tactical authority under pressure.", "styles": ["urgent_authority"]},
        "vulnerability": {"key": "vulnerability", "label": "Vulnerability and concern", "description": "Emotion held beneath professional or personal control.", "styles": ["vulnerability"]},
    }

    public_samples = []
    answer_samples: dict[str, Any] = {}
    for spec in internal["sample_specs"]:
        audio, receipt, result_path = result_audio(evidence, spec)
        objective = objectives[str(spec["sample_id"])]
        relative = Path("audio") / f"{spec['blind_id']}.wav"
        copy_public(audio, review / relative, objective["audio_sha256"])
        public_samples.append(
            {
                "sample_id": spec["blind_id"],
                "group": spec["group"],
                "style": spec["style"],
                "style_label": spec["style_label"],
                "identity_key": spec["identity_key"],
                "identity_reference_key": spec["identity_key"],
                "expected_identity": spec["identity_label"],
                "review_section_key": spec["identity_key"],
                "review_section_label": spec["identity_label"],
                "target_text": spec["target_text"],
                "requested_instruction": style_definitions[spec["style"]]["instruction"],
                "status": "ready",
                "structurally_generated": True,
                "review_eligible": True,
                "diagnostic_hold_reason": None,
                "audio": str(relative),
                "audio_sha256": objective["audio_sha256"],
                "automatic_transcript": objective["automatic_transcript"],
                "word_error_rate": objective["word_error_rate"],
                "speaker_cosine": objective["speaker_cosine_to_expected_identity"],
                "audio_diagnostics": objective["audio_diagnostics"],
            }
        )
        answer_samples[spec["blind_id"]] = {
            "sample_id": spec["blind_id"],
            "source_sample_id": spec["sample_id"],
            "model_key": spec["model_key"],
            "model_label": spec["model_label"],
            "identity_key": spec["identity_key"],
            "expected_identity": spec["identity_label"],
            "reference_tier": spec["reference_tier"],
            "reference": spec["reference"],
            "style": spec["style"],
            "group": spec["group"],
            "repeat": spec["repeat"],
            "seed": spec["seed"],
            "instruction": spec["instruction"],
            "control": spec.get("control"),
            "emotion_reference": spec.get("emotion_reference"),
            "generation_receipt": str(result_path),
            "generation_receipt_sha256": sha256_file(result_path),
            "generation_sample_fingerprint": receipt.get("sample_fingerprint"),
            "source_audio": str(audio),
            "source_audio_sha256": objective["audio_sha256"],
            "public_audio": str(relative),
            "objective": objective,
            "production_promotion_allowed": False,
        }

    public_samples.sort(key=lambda row: (row["group"], row["style"], row["identity_key"], row["sample_id"]))
    public = {
        "schema_version": 1,
        "round_id": internal["round_id"],
        "title": "Chris and Roz multimodel blind review",
        "groups": groups,
        "styles": [style_definitions[key] for key in ("neutral", "dry_humour", "urgent_authority", "vulnerability")],
        "identities": public_identities,
        "samples": public_samples,
        "blocked_coverage": [],
        "model_identity_hidden": True,
        "answer_key_separate": True,
        "production_promotion_allowed": False,
    }
    (review / "data.js").write_text(
        "window.ALEXANDRIA_ROUND1_DATA = " + json.dumps(public, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )
    answer = {
        "schema_version": 1,
        "round_id": internal["round_id"],
        "generated_at": utc_now(),
        "samples": answer_samples,
        "model_counts": dict(Counter(row["model_key"] for row in answer_samples.values())),
        "reference_tier_counts": dict(Counter(row["reference_tier"] for row in answer_samples.values())),
        "tnia_miller_included": False,
        "production_promotion_allowed": False,
    }
    write_json(evidence / "private/answer-key.json", answer)

    public_text = (review / "data.js").read_text(encoding="utf-8").casefold()
    forbidden = [
        "fish_s2_pro_cloud", "voxcpm2_controllable_clone", "indextts2_matched_control",
        "clean_actor", "canonical_cleaned", "reference_tier", "model_key", "model_label",
        "tnia", "source_sample_id", "generation_receipt",
    ]
    leaked = [token for token in forbidden if token in public_text]
    if leaked:
        raise PackageError(f"Public blind data leaks private fields: {leaked}")
    private_paths = [path for path in review.rglob("*") if "private" in path.relative_to(review).parts]
    if private_paths:
        raise PackageError(f"Private paths leaked into review root: {private_paths}")

    exact_count = sum(row["exact_normalized_text"] for row in objectives.values())
    wers = [float(row["word_error_rate"]) for row in objectives.values()]
    cosines = [float(row["speaker_cosine_to_expected_identity"]) for row in objectives.values()]
    manifest = {
        "schema_version": 1,
        "round_id": internal["round_id"],
        "generated_at": utc_now(),
        "group_count": len(groups),
        "style_count": len(style_definitions),
        "planned_sample_count": len(internal["sample_specs"]),
        "generated_sample_count": len(public_samples),
        "review_eligible_sample_count": len(public_samples),
        "exact_transcript_count": exact_count,
        "nonzero_wer_count": len(wers) - exact_count,
        "max_word_error_rate": max(wers),
        "speaker_cosine_range": [min(cosines), max(cosines)],
        "model_identity_hidden": True,
        "reference_tier_hidden": True,
        "public_assets_only": True,
        "tnia_miller_included": False,
        "answer_key": "../private/answer-key.json",
        "production_mutation": False,
    }
    write_json(review / "manifest.json", {key: value for key, value in manifest.items() if key != "answer_key"})
    write_json(evidence / "manifest.json", {**manifest, "generation_complete": True})
    write_json(
        evidence / "private/objective-summary.json",
        {
            "schema_version": 1,
            "round_id": internal["round_id"],
            "sample_count": len(objectives),
            "exact_transcript_count": exact_count,
            "nonzero_wer_count": len(wers) - exact_count,
            "max_word_error_rate": max(wers),
            "speaker_cosine_range": [min(cosines), max(cosines)],
            "model_counts": dict(Counter(row["model_key"] for row in objectives.values())),
            "reference_tier_counts": dict(Counter(row["reference_tier"] for row in objectives.values())),
            "manual_blind_review_required": True,
            "production_promotion_allowed": False,
        },
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--batch-index", type=int)
    parser.add_argument("--batch-size", type=int, default=24)
    parser.add_argument("--package-only", action="store_true")
    args = parser.parse_args()

    evidence = args.evidence_root.expanduser().resolve()
    internal = read_json(evidence / "private/internal-manifest.json")
    if internal.get("tnia_miller_included") is not False:
        raise PackageError("T'Nia must remain absent from objective evaluation and packaging.")
    if not args.package_only:
        specs = list(internal["sample_specs"])
        if args.batch_index is not None:
            start = args.batch_index * args.batch_size
            specs = specs[start : start + args.batch_size]
        runtime = ObjectiveRuntime(evidence, internal)
        for index, spec in enumerate(specs, start=1):
            row = runtime.evaluate(spec)
            print(
                json.dumps(
                    {
                        "index": index,
                        "count": len(specs),
                        "sample_id": spec["sample_id"],
                        "wer": row["word_error_rate"],
                        "cosine": row["speaker_cosine_to_expected_identity"],
                    }
                ),
                flush=True,
            )
    if args.package_only or args.batch_index is None:
        manifest = package_review(evidence, internal)
        print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
