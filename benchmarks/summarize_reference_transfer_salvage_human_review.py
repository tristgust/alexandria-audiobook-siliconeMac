#!/usr/bin/env python3
"""Unblind the targeted reference/transfer salvage review into durable evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE_ROOT = (
    ROOT / ".omo" / "evidence" / "b17-t05-reference-transfer-salvage"
)
FIVE_LANE_ROOT = ROOT / ".omo" / "evidence" / "b17-t05-four-voice-emotion-matrix"

ACTING_SELECTIONS: dict[str, dict[str, Any]] = {
    "fear": {
        "winner": "fear_acting_v2_5302",
        "approved_alternatives": ["fear_acting_v3_5303", "fear_acting_v1_5301"],
        "disposition": "winner_with_two_useful_approved_alternatives",
    },
    "panic": {
        "winner": "panic_acting_v2_5305",
        "approved_alternatives": [],
        "disposition": "clear_winner",
    },
    "disgust": {
        "winner": None,
        "approved_alternatives": [],
        "disposition": "acting_source_failure_all_candidates_rejected",
    },
    "contempt": {
        "winner": "contempt_acting_v3_5312",
        "approved_alternatives": [],
        "disposition": "winner_with_human_confirmed_text_despite_automatic_word_inflection",
    },
    "relief": {
        "winner": "relief_acting_v2_5314",
        "approved_alternatives": ["relief_acting_v3_5315"],
        "disposition": "winner_with_slightly_excessive_laughter_limit",
    },
    "urgent": {
        "winner": "urgent_acting_v3_5318",
        "approved_alternatives": [],
        "disposition": "winner_other_candidates_insufficiently_urgent",
    },
}

TRANSFER_SELECTIONS: dict[str, dict[str, Any]] = {
    "calm": {
        "winner_strength": 0.70,
        "approved_alternatives": [0.85, 1.00],
        "disposition": "all_equivalent_perfect_select_least_aggressive_strength",
    },
    "pleading": {
        "winner_strength": 1.00,
        "approved_alternatives": [0.85, 0.70],
        "disposition": "highest_delivery_score_wins",
    },
    "whisper": {
        "winner_strength": None,
        "approved_alternatives": [],
        "disposition": "reject_all_quieter_speech_not_whisper",
    },
    "sarcastic": {
        "winner_strength": None,
        "approved_alternatives": [],
        "disposition": "reject_all_as_proof_of_sarcasm_possible_enthusiasm_only",
    },
    "shout": {
        "winner_strength": 1.00,
        "approved_alternatives": [0.85],
        "disposition": "perfect_delivery_naturalness_and_identity",
    },
}

REJECTION_REASONS = {
    "panic_acting_v1_5304": "weaker panic; approved=false and explicitly pales beside the winner",
    "panic_acting_v3_5306": (
        "text/artifact failure: repeated nonverbal laughter, WER about 0.47, weak identity, "
        "delivery, and naturalness; retain only as a nonverbal-generation research example"
    ),
    "disgust_acting_v1_5307": "acting-source failure; clean identity but delivery 1.2/5",
    "disgust_acting_v2_5308": "acting-source failure; clean identity but delivery 1/5",
    "disgust_acting_v3_5309": "acting-source failure; clean identity but delivery 1/5",
    "contempt_acting_v1_5310": "insufficient contempt; delivery 3/5 and not approved",
    "contempt_acting_v2_5311": "insufficient contempt; delivery 3/5 and not approved",
    "relief_acting_v1_5313": "weak relief and naturalness; added nonlexical interjection",
    "urgent_acting_v1_5316": "insufficient urgency; delivery 3/5 and not approved",
    "urgent_acting_v2_5317": "insufficient urgency; delivery 2/5 and not approved",
    "generic_ryan_whisper_0p70_5507": "quiet start, explicitly not a whisper",
    "generic_ryan_whisper_0p85_5508": "explicitly not a whisper",
    "generic_ryan_whisper_1p00_5509": "quiet speech, explicitly not a whisper",
    "generic_ryan_sarcastic_0p70_5510": "ambiguous enthusiasm rather than demonstrated sarcasm",
    "generic_ryan_sarcastic_0p85_5511": "ambiguous enthusiasm rather than demonstrated sarcasm",
    "generic_ryan_sarcastic_1p00_5512": "ambiguous enthusiasm rather than demonstrated sarcasm",
    "generic_ryan_shout_0p70_5513": "not approved despite strong numeric delivery score",
}


def sha256_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_hash(value: str, *, label: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"Invalid sha256 for {label}: {value!r}")


def collect_scores(uploaded: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    input_files = []
    score_by_id: dict[str, dict[str, Any]] = {}
    for item in uploaded.get("inputs") or []:
        filename = str(item.get("filename") or "").strip()
        digest = str(item.get("sha256") or "").strip()
        rows = list(item.get("rows") or [])
        validate_hash(digest, label=filename)
        if item.get("row_count") != len(rows):
            raise ValueError(f"Row count mismatch for {filename}")
        input_files.append({"filename": filename, "sha256": digest, "row_count": len(rows)})
        for row in rows:
            sample_id = str(row.get("sample_id") or "").strip()
            if not sample_id or sample_id in score_by_id:
                raise ValueError(f"Duplicate or missing uploaded sample_id: {sample_id!r}")
            score_by_id[sample_id] = {**row, "input_filename": filename, "input_sha256": digest}
    return input_files, score_by_id


def acting_rows(evidence_root: Path, score_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    candidate_manifest = read_json(evidence_root / "qwen-reference-candidates" / "manifest.json")
    candidate_root = evidence_root / "qwen-reference-candidates"
    candidates = {row["sample_id"]: row for row in candidate_manifest["samples"]}
    output = []
    for style in sorted(ACTING_SELECTIONS):
        answer_key = read_json(evidence_root / "acting-review" / "pages" / style / "answer_key.json")
        evaluation = read_json(evidence_root / "acting-review" / "pages" / style / "evaluation.json")
        speaker = evaluation["speaker_evaluation"]["measurements"]
        transcription = evaluation["transcription_evaluation"]["measurements"]
        for mapping in answer_key:
            blind_id = mapping["sample_id"]
            source_id = mapping["source_sample_id"]
            score = score_by_id.pop(blind_id)
            candidate = candidates[source_id]
            audio_path = (candidate_root / candidate["audio_file"]).resolve()
            transcript = transcription[source_id]
            selection = ACTING_SELECTIONS[style]
            selected_role = (
                "winner"
                if source_id == selection["winner"]
                else "approved_alternative"
                if source_id in selection["approved_alternatives"]
                else "rejected"
            )
            output.append(
                {
                    "review_sample_id": blind_id,
                    "source_sample_id": source_id,
                    "style": style,
                    "prompt_variant": candidate["variant"],
                    "seed": candidate["seed"],
                    "instruction": candidate["instruction"],
                    "instruction_sha256": sha256_text(candidate["instruction"]),
                    "expected_text_sha256": evaluation["expected_text_sha256_by_sample"][source_id],
                    "audio_sha256": sha256_bytes(audio_path),
                    "audio_file": str(audio_path.relative_to(ROOT)),
                    "automatic_transcript": transcript["transcript"],
                    "automatic_transcript_sha256": transcript["transcript_sha256"],
                    "word_error_rate": transcript["word_error_rate"],
                    "speaker_cosine_to_ryan_reference": speaker[source_id][
                        "speaker_cosine_to_primary_reference"
                    ],
                    "human_score": score,
                    "selected_role": selected_role,
                    "rejection_reason": REJECTION_REASONS.get(source_id),
                }
            )
    return output


def transfer_rows(evidence_root: Path, score_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    matrix_manifest = read_json(evidence_root / "generic-ryan-strength-matrix" / "manifest.json")
    samples = {row["sample_id"]: row for row in matrix_manifest["samples"]}
    output = []
    for style in sorted(TRANSFER_SELECTIONS):
        answer_key = read_json(evidence_root / "transfer-review" / "pages" / style / "answer_key.json")
        evaluation = read_json(evidence_root / "transfer-review" / "pages" / style / "evaluation.json")
        speaker = evaluation["speaker_evaluation"]["measurements"]
        transcription = evaluation["transcription_evaluation"]["measurements"]
        for mapping in answer_key:
            blind_id = mapping["sample_id"]
            source_id = mapping["source_sample_id"]
            score = score_by_id.pop(blind_id)
            sample = samples[source_id]
            result_path = (
                evidence_root / "generic-ryan-strength-matrix" / "outputs" / source_id / "result.json"
            )
            result = read_json(result_path)
            audio_path = result_path.parent / "audio.wav"
            emotion_audio = Path(sample["emotion_audio_prompt"]).resolve()
            transcript = transcription[source_id]
            selection = TRANSFER_SELECTIONS[style]
            strength = float(sample["emotion_strength"])
            selected_role = (
                "winner"
                if selection["winner_strength"] is not None
                and abs(strength - float(selection["winner_strength"])) < 1e-9
                else "approved_alternative"
                if any(abs(strength - float(value)) < 1e-9 for value in selection["approved_alternatives"])
                else "rejected"
            )
            output.append(
                {
                    "review_sample_id": blind_id,
                    "source_sample_id": source_id,
                    "style": style,
                    "emotion_strength": strength,
                    "seed": sample["seed"],
                    "direction": sample["direction"],
                    "direction_sha256": sha256_text(sample["direction"]),
                    "expected_text_sha256": evaluation["expected_text_sha256_by_sample"][source_id],
                    "speaker_reference_sha256": result["reference_audio_sha256"],
                    "emotion_reference_sha256": sha256_bytes(emotion_audio),
                    "emotion_reference_file": str(emotion_audio.relative_to(ROOT)),
                    "audio_sha256": sha256_bytes(audio_path),
                    "audio_file": str(audio_path.relative_to(ROOT)),
                    "automatic_transcript": transcript["transcript"],
                    "automatic_transcript_sha256": transcript["transcript_sha256"],
                    "word_error_rate": transcript["word_error_rate"],
                    "speaker_cosine_to_ryan_reference": speaker[source_id][
                        "speaker_cosine_to_primary_reference"
                    ],
                    "runtime_controls": result["runtime_controls"],
                    "generation_controls": result["generation_controls"],
                    "human_score": score,
                    "selected_role": selected_role,
                    "rejection_reason": REJECTION_REASONS.get(source_id),
                }
            )
    return output


def doctor_relief_row(score_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    blind_id = "c712ca1bb47a58a6"
    score = score_by_id.pop(blind_id)
    answer_key = read_json(FIVE_LANE_ROOT / "review" / "answer-keys" / "relief.json")
    mapping = next(row for row in answer_key if row["sample_id"] == blind_id)
    source_id = mapping["source_sample_id"]
    result_path = FIVE_LANE_ROOT / "indextts2" / "doctor" / source_id / "result.json"
    result = read_json(result_path)
    audio_path = result_path.parent / "audio.wav"
    evaluation = read_json(FIVE_LANE_ROOT / "lane-evaluations" / "doctor" / "evaluation.json")
    transcript = evaluation["transcription_evaluation"]["measurements"][source_id]
    speaker = evaluation["speaker_evaluation"]["measurements"][source_id]
    return {
        "review_sample_id": blind_id,
        "source_sample_id": source_id,
        "style": "relief",
        "speaker": "doctor",
        "seed": result["seed"],
        "emotion_strength": result["emotion_strength"],
        "audio_sha256": sha256_bytes(audio_path),
        "audio_file": str(audio_path.relative_to(ROOT)),
        "automatic_transcript": transcript["transcript"],
        "automatic_transcript_sha256": transcript["transcript_sha256"],
        "word_error_rate": transcript["word_error_rate"],
        "speaker_cosine_to_primary_reference": speaker["speaker_cosine_to_primary_reference"],
        "human_score": score,
        "disposition": "restricted_pass",
        "reason": "approved and natural, but described as fine rather than a strong relief result",
    }


def compact_selection(
    rows: list[dict[str, Any]], selections: dict[str, dict[str, Any]], *, strength: bool
) -> dict[str, Any]:
    by_source = {row["source_sample_id"]: row for row in rows}
    by_style: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_style.setdefault(row["style"], []).append(row)
    output = {}
    for style, selection in selections.items():
        style_rows = by_style[style]
        if strength:
            winner_value = selection["winner_strength"]
            winner = next(
                (
                    row
                    for row in style_rows
                    if winner_value is not None
                    and abs(row["emotion_strength"] - float(winner_value)) < 1e-9
                ),
                None,
            )
            alternatives = [
                next(row for row in style_rows if abs(row["emotion_strength"] - float(value)) < 1e-9)
                for value in selection["approved_alternatives"]
            ]
        else:
            winner = by_source.get(selection["winner"])
            alternatives = [by_source[value] for value in selection["approved_alternatives"]]
        output[style] = {
            **selection,
            "winner": winner,
            "approved_alternative_records": alternatives,
            "rejected_source_sample_ids": [
                row["source_sample_id"] for row in style_rows if row["selected_role"] == "rejected"
            ],
        }
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE_ROOT))
    parser.add_argument("--output")
    args = parser.parse_args()

    evidence_root = Path(args.evidence_root).expanduser().resolve()
    uploaded_path = evidence_root / "uploaded_human_scores.json"
    uploaded = read_json(uploaded_path)
    input_files, score_by_id = collect_scores(uploaded)
    if len(score_by_id) != 34:
        raise ValueError(f"Expected 34 uploaded rows, found {len(score_by_id)}")

    acting = acting_rows(evidence_root, score_by_id)
    transfer = transfer_rows(evidence_root, score_by_id)
    doctor = doctor_relief_row(score_by_id)
    if score_by_id:
        raise ValueError(f"Unmatched uploaded sample IDs: {sorted(score_by_id)}")

    summary = {
        "schema_version": 1,
        "purpose": "unblinded_targeted_acting_reference_and_transfer_strength_human_evidence",
        "source": {
            "uploaded_score_manifest": str(uploaded_path.relative_to(ROOT)),
            "uploaded_score_manifest_sha256": sha256_bytes(uploaded_path),
            "input_files": input_files,
            "input_file_count": len(input_files),
            "raw_score_row_count": 34,
            "complete_score_row_count": 34,
            "unblinding_method": "match_each_sample_id_to_repository_answer_keys_not_input_filename",
        },
        "sample_level_scores": {
            "acting_reference": acting,
            "transfer_strength": transfer,
            "doctor_relief": doctor,
        },
        "selected_acting_references": compact_selection(
            acting, ACTING_SELECTIONS, strength=False
        ),
        "selected_transfer_strengths": compact_selection(
            transfer, TRANSFER_SELECTIONS, strength=True
        ),
        "rejected_candidates": [
            {
                "source_sample_id": source_id,
                "reason": reason,
            }
            for source_id, reason in sorted(REJECTION_REASONS.items())
        ],
        "doctor_relief_disposition": {
            "source_sample_id": doctor["source_sample_id"],
            "review_sample_id": doctor["review_sample_id"],
            "disposition": "restricted_pass",
            "human_score": doctor["human_score"],
            "reason": doctor["reason"],
        },
        "failure_classification": {
            "acting_source_failure": {
                "styles": ["disgust"],
                "details": (
                    "All three disgust candidates preserved identity and naturalness but scored only "
                    "1.0-1.2 for delivery. This failure precedes IndexTTS2 transfer."
                ),
            },
            "indextts2_transfer_failure": {
                "styles": ["whisper", "sarcastic"],
                "details": (
                    "Every whisper strength was quiet speech rather than whispering; every sarcastic "
                    "strength remained ambiguous between enthusiasm and sarcasm. Prior five-lane evidence "
                    "also places disgust and broad relief failure inside transfer after usable source acting."
                ),
            },
            "speaker_specific_compatibility": {
                "known_from_five_lane_evidence": {
                    "doctor": ["whisper", "sarcasm"],
                    "benny": ["urgent"],
                    "narrator": ["calm", "friendly"],
                    "variable": ["grief", "pleading"],
                },
                "winner_validation_status": "pending_bounded_24_sample_review",
            },
            "text_or_artifact_failure": {
                "panic_acting_v3_5306": (
                    "Repeated laughter/nonverbal material and WER about 0.47; not a text-faithful reference."
                ),
                "contempt_acting_v3_5312": (
                    "Automatic transcript inflected believed as believe; human text-match confirmation is affirmative."
                ),
                "relief_acting_v1_5313": "Added a nonlexical interjection.",
                "relief_acting_v3_5315": "Minor initial-word omission; retained only as an approved alternative.",
                "policy": "Record interjections, contractions, omissions, and inflections without silent normalization.",
            },
        },
        "winner_validation": {
            "selected_styles": [
                "fear",
                "panic",
                "contempt",
                "relief",
                "urgent",
                "calm",
                "pleading",
                "shout",
            ],
            "speakers": ["narrator", "benny", "doctor"],
            "planned_sample_count": 24,
            "generic_ryan_regeneration_required": False,
            "status": "ready_for_bounded_generation",
        },
        "acceptance": {
            "doctor_relief_score_complete": True,
            "acting_reference_scores_complete": True,
            "transfer_strength_scores_complete": True,
            "all_uploaded_samples_unblinded": True,
            "license_review_complete": False,
            "production_promotion_allowed": False,
            "production_registry_changed": False,
            "voice_assignment_changed": False,
            "live_project_audio_changed": False,
        },
    }

    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else evidence_root / "human_review_summary.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output_path),
                "acting_rows": len(acting),
                "transfer_rows": len(transfer),
                "doctor_rows": 1,
                "unmatched_rows": 0,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
