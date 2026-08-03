#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
ROUND_ID = "b18_multivoice_archetype_screen_20260803"
DEFAULT_REVIEW = (
    ROOT
    / ".omo"
    / "evidence"
    / "b18-multivoice-archetype-screen-20260803"
    / "completed-review.json"
)
DEFAULT_ANSWER_KEY = (
    ROOT
    / ".omo"
    / "evidence"
    / "b18-multivoice-archetype-screen-20260803"
    / "answer-keys"
    / "answer-key.json"
)
DEFAULT_DECISION = (
    ROOT / "benchmarks" / "b18_multivoice_archetype_screen_20260803_decision.json"
)
DEFAULT_EVIDENCE = (
    ROOT
    / ".omo"
    / "evidence"
    / "b18-multivoice-archetype-screen-20260803"
    / "human-review-adjudication.json"
)
UPLOADED_REVIEW_SHA256 = (
    "e761e8c1c60f67eefed620e713fb4b7a0b910ba8c1d6b0d99e62ff8ad3fdad35"
)


class MultiVoiceAdjudicationError(RuntimeError):
    pass


SPEAKER_DECISIONS: dict[str, dict[str, Any]] = {
    "THE DOCTOR": {
        "status": "return_to_preparation",
        "primary_method": None,
        "primary_candidate_id": None,
        "summary": (
            "No tested dry-eccentric candidate exceeded middling identity and delivery. "
            "The current route also carried an audible artifact, so this signature lane "
            "returns to reference and prompt preparation without revoking earlier accepted routes."
        ),
        "production_action": "preserve_prior_routes",
        "preserve_prior_routes": True,
        "route_key": "ordinary_identity",
        "approval_tier": None,
        "evidence_sample_ids": ["DOC01", "DOC02", "DOC03"],
        "unresolved_requirements": [
            "Prepare a stronger Doctor identity/reference set for dry eccentric dialogue.",
            "Repeat the lane with artifact-free controlled and prompt-bank candidates.",
        ],
    },
    "BERNICE": {
        "status": "return_to_preparation",
        "primary_method": None,
        "primary_candidate_id": None,
        "summary": (
            "Naturalness was often strong, but no artifact-free candidate retained more "
            "than moderate Benny identity. Existing specifically approved routes remain; "
            "this generalized sardonic-concern lane is not promoted."
        ),
        "production_action": "preserve_prior_routes",
        "preserve_prior_routes": True,
        "route_key": "benny_criminal_sardonic_concern",
        "approval_tier": None,
        "evidence_sample_ids": ["BEN01", "BEN02", "BEN03", "BEN04", "BEN05"],
        "unresolved_requirements": [
            "Repair Benny identity retention without losing naturalness.",
            "Re-test the sardonic-concern lane with the approved reference bank.",
        ],
    },
    "CHRIS CWEJ": {
        "status": "restricted",
        "primary_method": "current_route",
        "primary_candidate_id": "chris_cwej__current_route",
        "summary": (
            "The current route and generic Qwen candidate were byte-identical and retained "
            "perfect identity with strong naturalness. Blind delivery scores differed by one "
            "point, so the existing Qwen route remains accepted with a delivery-tuning restriction."
        ),
        "production_action": "keep_current",
        "preserve_prior_routes": True,
        "route_key": "neutral",
        "approval_tier": None,
        "evidence_sample_ids": ["CHR01", "CHR04"],
        "unresolved_requirements": [
            "Add a dedicated urgent-authority Qwen route or keyword match before broad promotion."
        ],
    },
    "ROZ FORRESTER": {
        "status": "approved",
        "primary_method": "fish_s2_pro_local",
        "primary_candidate_id": "roz_forrester__fish_s2_pro_local",
        "summary": (
            "Local Fish S2 Pro was the strongest blind take at 5 identity, 4 delivery, "
            "and 5 naturalness. The existing hosted Fish dry-banter route also passed at "
            "4/5/4 and remains the integrated production route until local Fish routing exists."
        ),
        "production_action": "keep_current",
        "preserve_prior_routes": True,
        "route_key": "roz_dry_banter",
        "approval_tier": "restricted_user_accepted",
        "evidence_sample_ids": ["ROZ02", "ROZ04"],
        "unresolved_requirements": [
            "Treat local Fish S2 Pro as the preferred noncommercial integration candidate."
        ],
    },
    "COMPUTER": {
        "status": "approved",
        "primary_method": "qwen_controlled_identity__computer_terminal_v3",
        "primary_candidate_id": (
            "computer__qwen_controlled_identity__computer_terminal_v3"
        ),
        "summary": (
            "Qwen plus computer_terminal_v3 received 5/5/5 and was called very good. "
            "The current Fish-plus-effect route also scored 5/5/5 but its processing was "
            "described as slightly off, so the production route moves to Qwen plus the same chain."
        ),
        "production_action": "replace_route",
        "preserve_prior_routes": True,
        "route_key": "computer_formal_system_response",
        "approval_tier": "strict",
        "evidence_sample_ids": ["COM02", "COM05"],
        "unresolved_requirements": [],
    },
    "TOBIAS VAUGHN": {
        "status": "approved",
        "primary_method": "current_route",
        "primary_candidate_id": "tobias_vaughn__current_route",
        "summary": (
            "The existing IndexTTS2 cultivated-menace route scored 5/5/5. Every Qwen "
            "or Fish alternative scored 1/1/1, so the specialist route remains authoritative."
        ),
        "production_action": "keep_current",
        "preserve_prior_routes": True,
        "route_key": "tobias_cultivated_menace",
        "approval_tier": "strict",
        "evidence_sample_ids": ["TOB01", "TOB02", "TOB03", "TOB04"],
        "unresolved_requirements": [],
    },
    "POWERLESS FRIENDLESS": {
        "status": "restricted",
        "primary_method": (
            "qwen_controlled_identity__powerless_alien_modulation_v1"
        ),
        "primary_candidate_id": (
            "powerless_friendless__qwen_controlled_identity__powerless_alien_modulation_v1"
        ),
        "summary": (
            "Raw Qwen and hosted Fish both scored 5/5/5 but were explicitly missing the "
            "required alien processing. The current IndexTTS2-plus-effect route sounded "
            "completely wrong at 2/2/3. Qwen plus the existing chain scored 4/4/4 and "
            "becomes the restricted interim route while the alien processing is redesigned."
        ),
        "production_action": "replace_route",
        "preserve_prior_routes": True,
        "route_key": "powerless_panicked_urgency",
        "approval_tier": "restricted_user_accepted",
        "evidence_sample_ids": ["HIT01", "HIT02", "HIT03", "HIT04"],
        "unresolved_requirements": [
            "Redesign and blind-test the Hith post-processing chain.",
            "Retain Qwen identity and panic delivery while increasing audible alien character."
        ],
    },
}


CANDIDATE_DISPOSITIONS = {
    "DOC01": "evaluation_only",
    "DOC02": "evaluation_only",
    "DOC03": "rejected_artifact",
    "BEN01": "evaluation_only_identity_weak",
    "BEN02": "rejected_artifact",
    "BEN03": "rejected_artifact",
    "BEN04": "evaluation_only_identity_weak",
    "BEN05": "evaluation_only_identity_weak",
    "CHR01": "production_accepted_restricted",
    "CHR02": "rejected_identity",
    "CHR03": "rejected",
    "CHR04": "production_accepted_restricted",
    "CHR05": "rejected_artifact",
    "ROZ01": "restricted_alternate",
    "ROZ02": "evaluation_preferred_local_integration_pending",
    "ROZ03": "rejected",
    "ROZ04": "production_accepted",
    "ROZ05": "restricted_alternate",
    "COM01": "underlying_voice_approved_processing_required",
    "COM02": "restricted_processing_tuning",
    "COM03": "underlying_voice_approved_processing_required",
    "COM04": "underlying_voice_approved_processing_required",
    "COM05": "production_accepted",
    "COM06": "underlying_voice_approved_processing_required",
    "TOB01": "rejected",
    "TOB02": "rejected",
    "TOB03": "rejected",
    "TOB04": "production_accepted",
    "HIT01": "underlying_voice_approved_processing_required",
    "HIT02": "rejected",
    "HIT03": "underlying_voice_approved_processing_required",
    "HIT04": "production_accepted_restricted_processing_repair",
}


ROUTE_UPDATES = [
    {
        "voice": "COMPUTER",
        "route_key": "computer_formal_system_response",
        "expected_configuration_fingerprint": (
            "8fadce5ab52958d471e17e3a04f4b4d86e54e1929561ba74bfa9a9f29a2a5068"
        ),
        "backend": "qwen3_instruction_controlled",
        "identity_audio": "clone_voices/approved_adaptation/computer/048a5ca161610aad.wav",
        "identity_audio_sha256": "4012a34bf801e68824b3451a3adbdcb52908e6a6f7fc9194d8020fb3f222393b",
        "identity_text": "Information concerning the prisoner of war identified by that number is classified.",
        "control": {},
        "effect_chain": "computer_terminal_v3",
        "approval_tier": "strict",
        "clear_performance_reference": True,
    },
    {
        "voice": "POWERLESS FRIENDLESS",
        "route_key": "powerless_panicked_urgency",
        "expected_configuration_fingerprint": (
            "52867ce5904b586af249a26c1440608c6a39b64a58019dcf504bcc53c2885a3d"
        ),
        "backend": "qwen3_instruction_controlled",
        "identity_audio": "clone_voices/approved_adaptation/powerless_friendless/98968650c708e630.wav",
        "identity_audio_sha256": "31a4c37f744ea4c413dff7ea9ccb4caa9fe08f1f3ac84f63b7f16328acb885a9",
        "identity_text": "I thought you wanted to fly it away in hyperspace.",
        "control": {},
        "effect_chain": "powerless_alien_modulation_v1",
        "approval_tier": "restricted_user_accepted",
        "clear_performance_reference": True,
    },
]


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MultiVoiceAdjudicationError(f"Could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MultiVoiceAdjudicationError(f"{path} must contain an object.")
    return value


def adjudicate(
    *,
    review_path: str | Path,
    answer_key_path: str | Path,
    review_source_sha256: str | None = None,
) -> dict[str, Any]:
    review_file = Path(review_path).expanduser().resolve()
    answer_file = Path(answer_key_path).expanduser().resolve()
    review = _read_json(review_file)
    answer_key = _read_json(answer_file)
    if review.get("round_id") != ROUND_ID or answer_key.get("round_id") != ROUND_ID:
        raise MultiVoiceAdjudicationError("Review and answer key must match the B18 round.")
    ratings = review.get("ratings")
    answers = answer_key.get("answers")
    if not isinstance(ratings, dict) or not isinstance(answers, list):
        raise MultiVoiceAdjudicationError("Review ratings or answer key entries are missing.")
    answer_by_id = {
        str(row["sample_id"]): row
        for row in answers
        if isinstance(row, Mapping) and row.get("sample_id")
    }
    if set(ratings) != set(answer_by_id):
        missing = sorted(set(answer_by_id) - set(ratings))
        extra = sorted(set(ratings) - set(answer_by_id))
        raise MultiVoiceAdjudicationError(
            f"Review is incomplete or mismatched; missing={missing}, extra={extra}."
        )
    if set(CANDIDATE_DISPOSITIONS) != set(answer_by_id):
        raise MultiVoiceAdjudicationError(
            "Candidate disposition register does not match the answer key."
        )

    candidate_rows: list[dict[str, Any]] = []
    hashes: dict[str, list[str]] = defaultdict(list)
    for sample_id in sorted(answer_by_id):
        answer = answer_by_id[sample_id]
        rating = ratings[sample_id]
        required = {
            "identity",
            "delivery",
            "naturalness",
            "text_match",
            "artifact_free",
        }
        if not isinstance(rating, Mapping) or not required <= set(rating):
            raise MultiVoiceAdjudicationError(
                f"Review sample {sample_id} is missing required fields."
            )
        scores = [int(rating[key]) for key in ("identity", "delivery", "naturalness")]
        if any(score < 1 or score > 5 for score in scores):
            raise MultiVoiceAdjudicationError(
                f"Review sample {sample_id} has an invalid score."
            )
        source_sha = str(answer.get("source_sha256") or "")
        hashes[source_sha].append(sample_id)
        candidate_rows.append(
            {
                "sample_id": sample_id,
                "speaker_key": str(answer["speaker_key"]),
                "candidate_id": str(answer["candidate_id"]),
                "method": str(answer["method"]),
                "source_sha256": source_sha,
                "ratings": {
                    "identity": scores[0],
                    "delivery": scores[1],
                    "naturalness": scores[2],
                    "text_match": bool(rating["text_match"]),
                    "artifact_free": bool(rating["artifact_free"]),
                    "notes": str(rating.get("notes") or ""),
                    "score_total": sum(scores),
                },
                "disposition": CANDIDATE_DISPOSITIONS[sample_id],
            }
        )

    duplicate_groups = [
        {"source_sha256": fingerprint, "sample_ids": sorted(sample_ids)}
        for fingerprint, sample_ids in sorted(hashes.items())
        if fingerprint and len(sample_ids) > 1
    ]
    decisions = json.loads(json.dumps(SPEAKER_DECISIONS))
    stored_review_sha256 = sha256_file(review_file)
    review_evidence_sha256 = review_source_sha256 or stored_review_sha256
    project_document = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "completed_at": str(review.get("completed_at") or ""),
        "review_sha256": review_evidence_sha256,
        "answer_key_sha256": sha256_file(answer_file),
        "evidence_path": (
            ".omo/evidence/b18-multivoice-archetype-screen-20260803/"
            "human-review-adjudication.json"
        ),
        "decisions": decisions,
    }
    return {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "review_completed_at": review.get("completed_at"),
        "review_sha256": review_evidence_sha256,
        "stored_review_sha256": stored_review_sha256,
        "answer_key_sha256": sha256_file(answer_file),
        "candidate_count": len(candidate_rows),
        "speaker_count": len(decisions),
        "candidate_decisions": candidate_rows,
        "duplicate_audio_groups": duplicate_groups,
        "speaker_decisions": decisions,
        "route_updates": json.loads(json.dumps(ROUTE_UPDATES)),
        "project_decision_document": project_document,
        "production_promotion_is_per_speaker": True,
        "universal_backend_selected": False,
    }


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", default=str(DEFAULT_REVIEW))
    parser.add_argument("--answer-key", default=str(DEFAULT_ANSWER_KEY))
    parser.add_argument("--decision-output", default=str(DEFAULT_DECISION))
    parser.add_argument("--evidence-output", default=str(DEFAULT_EVIDENCE))
    parser.add_argument(
        "--review-source-sha256",
        default=UPLOADED_REVIEW_SHA256,
    )
    args = parser.parse_args()
    result = adjudicate(
        review_path=args.review,
        answer_key_path=args.answer_key,
        review_source_sha256=args.review_source_sha256,
    )
    _atomic_write(Path(args.decision_output).expanduser().resolve(), result)
    _atomic_write(Path(args.evidence_output).expanduser().resolve(), result)
    print(
        json.dumps(
            {
                "round_id": ROUND_ID,
                "candidate_count": result["candidate_count"],
                "speaker_count": result["speaker_count"],
                "route_update_count": len(result["route_updates"]),
                "universal_backend_selected": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
