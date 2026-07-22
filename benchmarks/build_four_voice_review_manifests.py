#!/usr/bin/env python3
"""Build lane-wise evaluator manifests for the durable five-lane review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = (
    ROOT / ".omo" / "evidence" / "b17-t05-four-voice-emotion-matrix"
)

LANE_REFERENCES = {
    "qwen_direct": {
        "reference": DEFAULT_EVIDENCE / "qwen-control" / "audio" / "ryan_neutral.wav",
        "identity_label": "Ryan identity",
        "expected_identity": "Ryan",
    },
    "generic_ryan": {
        "reference": DEFAULT_EVIDENCE / "qwen-control" / "audio" / "ryan_neutral.wav",
        "identity_label": "Ryan identity",
        "expected_identity": "Ryan",
    },
    "narrator": {
        "reference": ROOT / "clone_voices" / "narratorvoicelines_-_01_1784553553.mp3",
        "identity_label": "Narrator identity",
        "expected_identity": "Narrator",
    },
    "benny": {
        "reference": ROOT / "clone_voices" / "bennyvoice1_1784053953.mp3",
        "identity_label": "Benny identity",
        "expected_identity": "Benny",
    },
    "doctor": {
        "reference": ROOT / "clone_voices" / "dw7voice1_1784300409.mp3",
        "identity_label": "Doctor identity",
        "expected_identity": "Doctor",
    },
}


def control_samples(evidence_root: Path) -> list[dict]:
    manifest = json.loads(
        (evidence_root / "qwen-control" / "manifest.json").read_text(encoding="utf-8")
    )
    root = evidence_root / "qwen-control"
    return [
        {
            "sample_id": f"qwen_direct_{item['style']}",
            "candidate": "qwen_direct_non_cloned_control",
            "direction": item["style"],
            "expected_identity": "Ryan",
            "expected_text": item["text"],
            "seed": item["seed"],
            "path": str((root / item["audio_file"]).resolve()),
        }
        for item in manifest["samples"]
    ]


def index_samples(evidence_root: Path, lane: str) -> list[dict]:
    full_manifest = json.loads(
        (evidence_root / "manifests" / f"{lane}.json").read_text(encoding="utf-8")
    )
    lane_root = evidence_root / "indextts2" / lane
    rows = []
    for expected in full_manifest["samples"]:
        sample_dir = lane_root / expected["sample_id"]
        receipt_path = sample_dir / "result.json"
        if not receipt_path.is_file():
            raise FileNotFoundError(receipt_path)
        item = json.loads(receipt_path.read_text(encoding="utf-8"))
        style = "neutral" if item["direction"] == "identity" else item["direction"].split()[0]
        audio_path = sample_dir / "audio.wav"
        if not audio_path.is_file():
            raise FileNotFoundError(audio_path)
        if item["target_text_sha256"] != expected.get("target_text_sha256", item["target_text_sha256"]):
            raise ValueError(f"Text fingerprint mismatch for {item['sample_id']}")
        rows.append(
            {
                "sample_id": item["sample_id"],
                "candidate": f"indextts2_{lane}",
                "direction": style,
                "expected_identity": LANE_REFERENCES[lane]["expected_identity"],
                "expected_text": item["expected_text"],
                "seed": item["seed"],
                "path": str(audio_path.resolve()),
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE))
    args = parser.parse_args()
    evidence_root = Path(args.evidence_root).expanduser().resolve()
    output_dir = evidence_root / "review-manifests"
    output_dir.mkdir(parents=True, exist_ok=True)

    manifests = {}
    for lane, identity in LANE_REFERENCES.items():
        reference = Path(identity["reference"]).expanduser().resolve()
        if not reference.is_file():
            raise FileNotFoundError(reference)
        samples = (
            control_samples(evidence_root)
            if lane == "qwen_direct"
            else index_samples(evidence_root, lane)
        )
        expected_count = len(
            json.loads(
                (evidence_root / "qwen-control" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )["samples"]
        )
        if len(samples) != expected_count:
            raise ValueError(
                f"Expected {expected_count} samples for {lane}, got {len(samples)}"
            )
        manifest = {
            "schema_version": 1,
            "purpose": "lane_evaluation_for_five_lane_emotion_review",
            "lane": lane,
            "identity_label": identity["identity_label"],
            "reference_audio": str(reference),
            "samples": samples,
            "production_promotion_allowed": False,
        }
        path = output_dir / f"{lane}.json"
        path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        manifests[lane] = str(path)

    index = {
        "schema_version": 1,
        "purpose": "lane_evaluation_for_five_lane_emotion_review",
        "manifests": manifests,
        "lane_count": len(manifests),
        "sample_count": sum(
            len(json.loads(Path(path).read_text(encoding="utf-8"))["samples"])
            for path in manifests.values()
        ),
        "production_promotion_allowed": False,
    }
    (output_dir / "index.json").write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(index, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
