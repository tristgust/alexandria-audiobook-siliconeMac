#!/usr/bin/env python3
"""Summarize the durable five-lane emotion-transfer experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = (
    ROOT / ".omo" / "evidence" / "b17-t05-four-voice-emotion-matrix"
)
DEFAULT_RUNTIME = (
    Path.home() / "pinokio" / "cache" / "alexandria-evaluation" / "indextts2"
)
LANES = ["qwen_direct", "generic_ryan", "narrator", "benny", "doctor"]
INDEX_LANES = ["generic_ryan", "narrator", "benny", "doctor"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME))
    args = parser.parse_args()
    evidence_root = Path(args.evidence_root).expanduser().resolve()
    runtime_root = Path(args.runtime_root).expanduser().resolve()

    runtime_receipt = json.loads(
        (runtime_root / "restore_receipt.json").read_text(encoding="utf-8")
    )
    control = json.loads(
        (evidence_root / "qwen-control" / "manifest.json").read_text(encoding="utf-8")
    )
    review = json.loads(
        (evidence_root / "review" / "manifest.json").read_text(encoding="utf-8")
    )

    lane_summary: dict[str, Any] = {}
    nonzero_wer = []
    total_samples = 0
    perfect_transcripts = 0
    for lane in LANES:
        evaluation = json.loads(
            (evidence_root / "lane-evaluations" / lane / "evaluation.json").read_text(
                encoding="utf-8"
            )
        )
        trans = evaluation["transcription_evaluation"]["measurements"]
        speaker = evaluation["speaker_evaluation"]["measurements"]
        wers = [float(item["word_error_rate"]) for item in trans.values()]
        cosines = [
            float(item["speaker_cosine_to_primary_reference"])
            for item in speaker.values()
        ]
        total_samples += len(wers)
        perfect_transcripts += sum(value == 0.0 for value in wers)
        for sample_id, measurement in trans.items():
            wer = float(measurement["word_error_rate"])
            if wer != 0.0:
                nonzero_wer.append(
                    {
                        "lane": lane,
                        "sample_id": sample_id,
                        "word_error_rate": wer,
                        "transcript_sha256": measurement.get("transcript_sha256"),
                    }
                )
        lane_summary[lane] = {
            "sample_count": len(wers),
            "perfect_transcript_count": sum(value == 0.0 for value in wers),
            "max_word_error_rate": max(wers),
            "speaker_cosine_min": min(cosines),
            "speaker_cosine_max": max(cosines),
            "speaker_cosine_mean": sum(cosines) / len(cosines),
        }

    for lane in INDEX_LANES:
        review_manifest = json.loads(
            (evidence_root / "review-manifests" / f"{lane}.json").read_text(
                encoding="utf-8"
            )
        )
        receipts = []
        for sample in review_manifest["samples"]:
            receipt_path = (
                evidence_root
                / "indextts2"
                / lane
                / sample["sample_id"]
                / "result.json"
            )
            if not receipt_path.is_file():
                raise FileNotFoundError(receipt_path)
            receipts.append(json.loads(receipt_path.read_text(encoding="utf-8")))
        rtfs = [float(item["real_time_factor"]) for item in receipts]
        loads = [float(item["shared_model_load_seconds"]) for item in receipts]
        controls = {json.dumps(item["runtime_controls"], sort_keys=True) for item in receipts}
        if len(controls) != 1:
            raise ValueError(f"Mixed runtime controls in {lane}: {sorted(controls)}")
        full_matrix_path = evidence_root / "indextts2" / lane / "matrix_result.json"
        full_matrix = (
            json.loads(full_matrix_path.read_text(encoding="utf-8"))
            if full_matrix_path.is_file()
            else None
        )
        if full_matrix and full_matrix.get("sample_count") == len(receipts):
            lane_summary[lane]["mean_real_time_factor"] = full_matrix[
                "mean_real_time_factor"
            ]
            lane_summary[lane]["shared_model_load_seconds"] = full_matrix[
                "shared_model_load_seconds"
            ]
            lane_summary[lane]["runtime_source"] = "uncontended_full_matrix_summary"
        else:
            lane_summary[lane]["mean_real_time_factor"] = sum(rtfs) / len(rtfs)
            lane_summary[lane]["shared_model_load_seconds"] = sum(loads) / len(loads)
            lane_summary[lane]["runtime_source"] = "individual_sample_receipts"
        lane_summary[lane]["runtime_controls"] = receipts[0]["runtime_controls"]

    qwen_rtfs = [float(item["real_time_factor"]) for item in control["samples"]]
    lane_summary["qwen_direct"]["mean_real_time_factor"] = sum(qwen_rtfs) / len(
        qwen_rtfs
    )
    lane_summary["qwen_direct"]["runtime_controls"] = {
        "model": "mlx_custom_voice",
        "voice": control["voice"],
    }

    receipt_copy = evidence_root / "runtime_restore_receipt.json"
    receipt_copy.write_text(
        json.dumps(runtime_receipt, indent=2) + "\n", encoding="utf-8"
    )

    summary = {
        "schema_version": 1,
        "purpose": "durable_five_lane_same_model_emotion_transfer_evaluation",
        "experimental_design": {
            "direct_non_cloned_control": "qwen_direct",
            "same_model_same_voice_upper_bound": "generic_ryan",
            "cross_identity_transfer_lanes": ["narrator", "benny", "doctor"],
            "shared_emotion_reference_source": "durable Qwen Ryan control bank",
            "index_model_lane_count": 4,
            "total_lane_count": 5,
            "style_count": len(review["styles"]),
        },
        "runtime": {
            "source": runtime_receipt["source"],
            "model": runtime_receipt["model"],
            "auxiliary": runtime_receipt["auxiliary"],
            "environment": runtime_receipt["environment"],
            "receipt_sha256": sha256_file(receipt_copy),
        },
        "control_model": control["model"],
        "sample_count": total_samples,
        "perfect_transcript_count": perfect_transcripts,
        "nonzero_wer_count": len(nonzero_wer),
        "nonzero_wer_samples": nonzero_wer,
        "lane_summary": lane_summary,
        "review": {
            "root_alias": str(evidence_root / "review.html"),
            "hub": str(evidence_root / "review" / "index.html"),
            "page_count": review["page_count"],
            "sample_count": review["sample_count"],
            "candidate_lane_hidden": review["candidate_lane_hidden"],
            "expected_identity_visible": review["expected_identity_visible"],
            "all_audio_copied_into_review_folder": review[
                "all_audio_copied_into_review_folder"
            ],
            "temporary_paths_required": review["temporary_paths_required"],
            "review_tree_sha256": tree_hash(evidence_root / "review"),
        },
        "acceptance": {
            "manual_scores_complete": False,
            "license_review_complete": False,
            "production_promotion_allowed": False,
            "production_registry_changed": False,
            "voice_assignment_changed": False,
            "live_project_audio_changed": False,
        },
    }
    output = evidence_root / "objective_summary.json"
    output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "sample_count": total_samples,
                "perfect_transcript_count": perfect_transcripts,
                "review_tree_sha256": summary["review"]["review_tree_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
