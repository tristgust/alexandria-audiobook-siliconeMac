#!/usr/bin/env python3
"""Build four matched IndexTTS2 manifests from the durable Qwen control bank."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = (
    ROOT / ".omo" / "evidence" / "b17-t05-four-voice-emotion-matrix"
)

STRENGTHS = {
    "sad": 0.65,
    "fear": 0.70,
    "angry": 0.70,
    "happy": 0.65,
    "excited": 0.65,
    "friendly": 0.60,
    "surprised": 0.65,
    "whisper": 0.75,
    "shout": 0.75,
    "disgust": 0.70,
    "contempt": 0.65,
    "grief": 0.70,
    "panic": 0.75,
    "relief": 0.60,
    "tender": 0.60,
    "pleading": 0.70,
    "sarcastic": 0.65,
    "calm": 0.55,
    "urgent": 0.70,
    "exhausted": 0.65,
    "authoritative": 0.65,
}

VOICE_REFERENCES = {
    "narrator": {
        "label": "Narrator production reference",
        "path": ROOT / "clone_voices" / "narratorvoicelines_-_01_1784553553.mp3",
        "identity_label": "Narrator identity",
    },
    "benny": {
        "label": "Benny production reference",
        "path": ROOT / "clone_voices" / "bennyvoice1_1784053953.mp3",
        "identity_label": "Benny identity",
    },
    "doctor": {
        "label": "Doctor production reference",
        "path": ROOT / "clone_voices" / "dw7voice1_1784300409.mp3",
        "identity_label": "Doctor identity",
    },
    "generic_ryan": {
        "label": "Generic Ryan upper-bound control reference",
        "path": DEFAULT_EVIDENCE / "qwen-control" / "audio" / "ryan_neutral.wav",
        "identity_label": "Ryan identity",
    },
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE))
    parser.add_argument(
        "--control-manifest",
        default=str(DEFAULT_EVIDENCE / "qwen-control" / "manifest.json"),
    )
    parser.add_argument("--seed", type=int, default=4901)
    args = parser.parse_args()

    evidence_root = Path(args.evidence_root).expanduser().resolve()
    control_manifest_path = Path(args.control_manifest).expanduser().resolve()
    control = json.loads(control_manifest_path.read_text(encoding="utf-8"))
    control_root = control_manifest_path.parent
    samples_by_style = {item["style"]: item for item in control["samples"]}
    expected_styles = [
        "neutral",
        "sad",
        "fear",
        "angry",
        "happy",
        "excited",
        "friendly",
        "surprised",
        "whisper",
        "shout",
        "disgust",
        "contempt",
        "grief",
        "panic",
        "relief",
        "tender",
        "pleading",
        "sarcastic",
        "calm",
        "urgent",
        "exhausted",
        "authoritative",
    ]
    if sorted(samples_by_style) != sorted(expected_styles):
        raise ValueError(
            f"Control bank styles differ from expected set: {sorted(samples_by_style)}"
        )

    manifests_dir = evidence_root / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    outputs = {}
    for lane_index, (lane, voice) in enumerate(VOICE_REFERENCES.items()):
        reference = Path(voice["path"]).expanduser().resolve()
        if not reference.is_file():
            raise FileNotFoundError(reference)
        matrix_samples = []
        for style_index, style in enumerate(expected_styles):
            control_sample = samples_by_style[style]
            item = {
                "sample_id": f"{lane}_{style}_{args.seed + style_index}",
                "reference_audio": str(reference),
                "reference_label": voice["label"],
                "text": control_sample["text"],
                "line_label": f"matched {style} line",
                "direction": "identity" if style == "neutral" else f"{style} reference",
                "emotion_strength": None if style == "neutral" else STRENGTHS[style],
                "seed": args.seed + lane_index * 100 + style_index,
                "generation": {"num_beams": 1},
            }
            if style != "neutral":
                emotion_audio = (control_root / control_sample["audio_file"]).resolve()
                if not emotion_audio.is_file():
                    raise FileNotFoundError(emotion_audio)
                item["emotion_audio_prompt"] = str(emotion_audio)
                item["emotion_label"] = f"Qwen Ryan {style} control"
            matrix_samples.append(item)

        manifest = {
            "schema_version": 1,
            "purpose": "four_voice_same_model_emotion_transfer_matrix",
            "lane": lane,
            "identity_label": voice["identity_label"],
            "control_bank_manifest": str(control_manifest_path),
            "samples": matrix_samples,
            "production_promotion_allowed": False,
            "production_registry_changed": False,
            "voice_assignment_changed": False,
            "live_project_audio_changed": False,
        }
        path = manifests_dir / f"{lane}.json"
        path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        outputs[lane] = str(path)

    index = {
        "schema_version": 1,
        "purpose": "four_voice_same_model_emotion_transfer_matrix",
        "lanes": outputs,
        "styles": expected_styles,
        "qwen_control_lane": str(control_manifest_path),
        "index_model_lane_count": 4,
        "production_promotion_allowed": False,
    }
    (manifests_dir / "index.json").write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(index, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
