#!/usr/bin/env python3
"""Build the bounded 24-sample IndexTTS2 winner-validation manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SALVAGE_ROOT = ROOT / ".omo" / "evidence" / "b17-t05-reference-transfer-salvage"
FIVE_LANE_ROOT = ROOT / ".omo" / "evidence" / "b17-t05-four-voice-emotion-matrix"
DEFAULT_OUTPUT = SALVAGE_ROOT / "winner-validation" / "manifest.json"

VOICE_REFERENCES = {
    "narrator": {
        "label": "Narrator production reference",
        "identity_label": "Narrator identity",
        "path": ROOT / "clone_voices" / "narratorvoicelines_-_01_1784553553.mp3",
    },
    "benny": {
        "label": "Benny production reference",
        "identity_label": "Benny identity",
        "path": ROOT / "clone_voices" / "bennyvoice1_1784053953.mp3",
    },
    "doctor": {
        "label": "Doctor production reference",
        "identity_label": "Doctor identity",
        "path": ROOT / "clone_voices" / "dw7voice1_1784300409.mp3",
    },
}

ACTING_REFERENCE_STRENGTHS = {
    "fear": 0.70,
    "panic": 0.75,
    "contempt": 0.65,
    "relief": 0.60,
    "urgent": 0.70,
}

STYLE_ORDER = [
    "fear",
    "panic",
    "contempt",
    "relief",
    "urgent",
    "calm",
    "pleading",
    "shout",
]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def selected_sources(human: dict[str, Any]) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for style in ACTING_REFERENCE_STRENGTHS:
        record = human["selected_acting_references"][style]["winner"]
        if record is None:
            raise ValueError(f"Acting-reference winner missing for {style}")
        selected[style] = {
            "selection_kind": "new_acting_reference",
            "source_sample_id": record["source_sample_id"],
            "text": next(
                item["text"]
                for item in read_json(
                    SALVAGE_ROOT / "qwen-reference-candidates" / "manifest.json"
                )["samples"]
                if item["sample_id"] == record["source_sample_id"]
            ),
            "emotion_audio_prompt": ROOT / record["audio_file"],
            "emotion_audio_sha256": record["audio_sha256"],
            "emotion_strength": ACTING_REFERENCE_STRENGTHS[style],
            "emotion_strength_origin": "prior_bounded_five_lane_style_strength",
            "source_instruction_sha256": record["instruction_sha256"],
            "source_seed": record["seed"],
        }

    transfer_manifest = read_json(
        SALVAGE_ROOT / "generic-ryan-strength-matrix" / "manifest.json"
    )
    transfer_by_id = {item["sample_id"]: item for item in transfer_manifest["samples"]}
    for style in ("calm", "pleading", "shout"):
        record = human["selected_transfer_strengths"][style]["winner"]
        if record is None:
            raise ValueError(f"Transfer-strength winner missing for {style}")
        source = transfer_by_id[record["source_sample_id"]]
        selected[style] = {
            "selection_kind": "accepted_transfer_strength",
            "source_sample_id": record["source_sample_id"],
            "text": source["text"],
            "emotion_audio_prompt": Path(source["emotion_audio_prompt"]).resolve(),
            "emotion_audio_sha256": record["emotion_reference_sha256"],
            "emotion_strength": record["emotion_strength"],
            "emotion_strength_origin": "targeted_generic_ryan_human_strength_selection",
            "source_instruction_sha256": record["direction_sha256"],
            "source_seed": record["seed"],
        }
    return selected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--human-summary",
        default=str(SALVAGE_ROOT / "human_review_summary.json"),
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--seed", type=int, default=5701)
    args = parser.parse_args()

    human_path = Path(args.human_summary).expanduser().resolve()
    human = read_json(human_path)
    if not human["acceptance"]["all_uploaded_samples_unblinded"]:
        raise ValueError("Human salvage review is not complete.")
    if human["acceptance"]["production_promotion_allowed"]:
        raise ValueError("Winner validation must remain non-production.")

    selected = selected_sources(human)
    samples = []
    speaker_metadata = {}
    for speaker, metadata in VOICE_REFERENCES.items():
        reference_path = metadata["path"].resolve()
        if not reference_path.is_file():
            raise FileNotFoundError(reference_path)
        speaker_metadata[speaker] = {
            "identity_label": metadata["identity_label"],
            "reference_label": metadata["label"],
            "reference_audio": str(reference_path),
            "reference_audio_sha256": sha256_bytes(reference_path),
        }
        for style_index, style in enumerate(STYLE_ORDER):
            source = selected[style]
            emotion_audio = Path(source["emotion_audio_prompt"]).resolve()
            if not emotion_audio.is_file():
                raise FileNotFoundError(emotion_audio)
            actual_hash = sha256_bytes(emotion_audio)
            if actual_hash != source["emotion_audio_sha256"]:
                raise ValueError(
                    f"Emotion reference hash mismatch for {style}: "
                    f"{actual_hash} != {source['emotion_audio_sha256']}"
                )
            seed = args.seed + style_index
            sample_id = f"winner_validation_{speaker}_{style}_{seed}"
            samples.append(
                {
                    "sample_id": sample_id,
                    "speaker": speaker,
                    "identity_label": metadata["identity_label"],
                    "reference_audio": str(reference_path),
                    "reference_label": metadata["label"],
                    "reference_audio_sha256": speaker_metadata[speaker][
                        "reference_audio_sha256"
                    ],
                    "style": style,
                    "text": source["text"],
                    "expected_text_sha256": sha256_text(source["text"]),
                    "direction": f"{style} reference",
                    "emotion_audio_prompt": str(emotion_audio),
                    "emotion_audio_sha256": actual_hash,
                    "emotion_strength": source["emotion_strength"],
                    "emotion_strength_origin": source["emotion_strength_origin"],
                    "selection_kind": source["selection_kind"],
                    "source_selection_sample_id": source["source_sample_id"],
                    "source_instruction_sha256": source["source_instruction_sha256"],
                    "source_seed": source["source_seed"],
                    "seed": seed,
                    "generation": {"num_beams": 1, "max_mel_tokens": 600},
                }
            )

    expected_count = len(STYLE_ORDER) * len(VOICE_REFERENCES)
    if len(samples) != expected_count or expected_count != 24:
        raise AssertionError(f"Expected 24 validation samples, found {len(samples)}")

    manifest = {
        "schema_version": 1,
        "purpose": "bounded_cross_speaker_validation_of_human_selected_acting_references_and_transfer_strengths",
        "human_review_summary": str(human_path),
        "human_review_summary_sha256": sha256_bytes(human_path),
        "styles": STYLE_ORDER,
        "speakers": list(VOICE_REFERENCES),
        "speaker_metadata": speaker_metadata,
        "sample_count": len(samples),
        "samples": samples,
        "runtime_profile": {
            "candidate": "IndexTTS2",
            "device": "mps",
            "use_fp16": False,
            "mps_fast_math": True,
            "mps_prefer_metal": True,
            "num_beams": 1,
            "greedy_generation": True,
            "diffusion_steps": 8,
            "persistent_worker_count": 2,
        },
        "generic_ryan_regenerated": False,
        "broad_combinatorial_matrix_generated": False,
        "persistent_repository_local_evaluation_cache_required": True,
        "temporary_paths_allowed": False,
        "manual_blinded_review_required": True,
        "license_review_complete": False,
        "production_promotion_allowed": False,
        "production_registry_changed": False,
        "voice_assignment_changed": False,
        "live_project_audio_changed": False,
    }
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output_path),
                "sample_count": len(samples),
                "styles": STYLE_ORDER,
                "speakers": list(VOICE_REFERENCES),
                "generic_ryan_regenerated": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
