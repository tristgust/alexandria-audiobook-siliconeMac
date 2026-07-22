#!/usr/bin/env python3
"""Build twelve-style supplemental IndexTTS2 manifests for expanded coverage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = ROOT / ".omo" / "evidence" / "b17-t05-four-voice-emotion-matrix"
SUPPLEMENTAL_STYLES = {
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
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE))
    args = parser.parse_args()
    root = Path(args.evidence_root).expanduser().resolve()
    source_dir = root / "manifests"
    output_dir = root / "supplemental-manifests"
    output_dir.mkdir(parents=True, exist_ok=True)

    outputs = {}
    for lane in ["narrator", "benny", "doctor", "generic_ryan"]:
        manifest = json.loads((source_dir / f"{lane}.json").read_text(encoding="utf-8"))
        samples = []
        for item in manifest["samples"]:
            style = "neutral" if item["direction"] == "identity" else item["direction"].split()[0]
            if style in SUPPLEMENTAL_STYLES:
                samples.append(item)
        if len(samples) != len(SUPPLEMENTAL_STYLES):
            raise ValueError(f"Expected {len(SUPPLEMENTAL_STYLES)} supplemental samples for {lane}, got {len(samples)}")
        manifest["purpose"] = "four_voice_same_model_emotion_transfer_supplemental_matrix"
        manifest["samples"] = samples
        path = output_dir / f"{lane}.json"
        path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        outputs[lane] = str(path)

    index = {
        "schema_version": 1,
        "purpose": "four_voice_same_model_emotion_transfer_supplemental_matrix",
        "styles": sorted(SUPPLEMENTAL_STYLES),
        "lanes": outputs,
        "sample_count_per_lane": len(SUPPLEMENTAL_STYLES),
        "production_promotion_allowed": False,
    }
    (output_dir / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(index, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
