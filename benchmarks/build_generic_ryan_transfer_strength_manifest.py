#!/usr/bin/env python3
"""Build a same-voice IndexTTS2 transfer-strength matrix for good references."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = (
    ROOT / ".omo" / "evidence" / "b17-t05-reference-transfer-salvage"
    / "generic-ryan-strength-matrix"
)
CONTROL_MANIFEST = (
    ROOT / ".omo" / "evidence" / "b17-t05-four-voice-emotion-matrix"
    / "qwen-control" / "manifest.json"
)
GENERIC_REFERENCE = (
    ROOT / ".omo" / "evidence" / "b17-t05-four-voice-emotion-matrix"
    / "qwen-control" / "audio" / "ryan_neutral.wav"
)
STYLES = ["calm", "pleading", "whisper", "sarcastic", "shout"]
STRENGTHS = [0.70, 0.85, 1.00]


def label_strength(value: float) -> str:
    return f"{value:.2f}".replace(".", "p")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(DEFAULT_ROOT))
    parser.add_argument("--seed", type=int, default=5501)
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    control = json.loads(CONTROL_MANIFEST.read_text(encoding="utf-8"))
    control_root = CONTROL_MANIFEST.parent
    by_style = {item["style"]: item for item in control["samples"]}
    if not GENERIC_REFERENCE.is_file():
        raise FileNotFoundError(GENERIC_REFERENCE)

    samples = []
    index = 0
    for style in STYLES:
        source = by_style[style]
        emotion_audio = (control_root / source["audio_file"]).resolve()
        if not emotion_audio.is_file():
            raise FileNotFoundError(emotion_audio)
        for strength in STRENGTHS:
            seed = args.seed + index
            index += 1
            samples.append({
                "sample_id": f"generic_ryan_{style}_{label_strength(strength)}_{seed}",
                "reference_audio": str(GENERIC_REFERENCE.resolve()),
                "reference_label": "Generic Ryan same-voice transfer reference",
                "text": source["text"],
                "line_label": f"matched {style} line",
                "direction": f"{style} reference",
                "emotion_audio_prompt": str(emotion_audio),
                "emotion_label": f"Qwen Ryan {style} control",
                "emotion_strength": strength,
                "seed": seed,
                "generation": {"num_beams": 1},
            })

    manifest = {
        "schema_version": 1,
        "purpose": "generic_ryan_same_voice_transfer_strength_matrix",
        "identity_label": "Ryan identity",
        "styles": STYLES,
        "strengths": STRENGTHS,
        "samples": samples,
        "manual_blinded_review_required": True,
        "production_promotion_allowed": False,
        "production_registry_changed": False,
        "voice_assignment_changed": False,
        "live_project_audio_changed": False,
    }
    path = output_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "manifest": str(path),
        "style_count": len(STYLES),
        "strength_count": len(STRENGTHS),
        "sample_count": len(samples),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
