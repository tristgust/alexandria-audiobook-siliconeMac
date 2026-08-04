#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
ROUND_ID = "b22_doctor_fallback_repair_20260804"
DEFAULT_PROJECT = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Alexandria"
    / "Projects"
    / "original-sin--e6286665"
)
DEFAULT_REVIEW = (
    ROOT / "benchmarks" / "b22_doctor_fallback_repair_20260804_user_decision.json"
)
DEFAULT_ANSWER_KEY = Path(
    "/Users/tristan/Downloads/b22_doctor_fallback_repair_20260804/"
    "answer-keys/answer-key.json"
)
DEFAULT_OUTPUT = (
    ROOT / "benchmarks" / "b22_doctor_fallback_repair_20260804_decision.json"
)
PROJECT_DECISION_FILENAME = "voice_route_listening_decisions.json"
EVIDENCE_PATH = ".omo/evidence/b22-doctor-fallback-repair-20260804.json"
ANCHOR_REFERENCE_ROUTES = {
    "b18_neutral_identity_anchor": "neutral",
    "b18_current_route_anchor": "ordinary_identity",
}


class DoctorFallbackAdjudicationError(RuntimeError):
    pass


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DoctorFallbackAdjudicationError(f"Could not read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise DoctorFallbackAdjudicationError(f"{label} must contain an object.")
    return value


def _validate_review(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {"schema_version", "round_id", "completed_at", "selection", "notes"}
    if set(value) != expected or value.get("schema_version") != 1:
        raise DoctorFallbackAdjudicationError("Doctor fallback review has an invalid schema.")
    if value.get("round_id") != ROUND_ID:
        raise DoctorFallbackAdjudicationError("Doctor fallback review has the wrong round ID.")
    selection = str(value.get("selection") or "").strip()
    if not selection:
        raise DoctorFallbackAdjudicationError("Doctor fallback review has no selection.")
    return {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "completed_at": str(value["completed_at"]),
        "selection": selection,
        "notes": str(value.get("notes") or ""),
    }


def adjudicate(
    *,
    project_root: str | Path,
    review_path: str | Path,
    answer_key_path: str | Path,
    uploaded_review_sha256: str | None = None,
) -> dict[str, Any]:
    project = Path(project_root).expanduser().resolve()
    review_file = Path(review_path).expanduser().resolve()
    answer_file = Path(answer_key_path).expanduser().resolve()
    review = _validate_review(_read_json(review_file, "Doctor fallback review"))
    answer_key = _read_json(answer_file, "Doctor fallback answer key")
    if answer_key.get("round_id") != ROUND_ID:
        raise DoctorFallbackAdjudicationError("Doctor fallback answer key has the wrong round ID.")

    answers = answer_key.get("answers")
    if not isinstance(answers, list):
        raise DoctorFallbackAdjudicationError("Doctor fallback answer key has no answers.")
    answer_by_sample = {
        str(row.get("sample_id")): dict(row)
        for row in answers
        if isinstance(row, Mapping) and row.get("sample_id")
    }
    selection = review["selection"]
    current_decisions = _read_json(
        project / PROJECT_DECISION_FILENAME,
        "Current Voice listening decisions",
    )
    project_document = copy.deepcopy(current_decisions)
    project_document.update(
        {
            "round_id": ROUND_ID,
            "completed_at": review["completed_at"],
            "review_sha256": uploaded_review_sha256 or sha256_file(review_file),
            "answer_key_sha256": sha256_file(answer_file),
            "evidence_path": EVIDENCE_PATH,
        }
    )

    if selection.casefold() == "none":
        project_document["decisions"]["THE DOCTOR"] = {
            "status": "return_to_preparation",
            "primary_method": None,
            "primary_candidate_id": None,
            "summary": (
                "The focused Doctor fallback review selected None. Existing specific Doctor "
                "routes remain valid, but the dry/eccentric fallback remains in preparation."
            ),
            "production_action": "preserve_prior_routes",
            "preserve_prior_routes": True,
            "route_key": "ordinary_identity",
            "approval_tier": None,
            "evidence_sample_ids": [],
            "unresolved_requirements": [
                "Prepare another Doctor dry/eccentric fallback reference only if explicitly requested."
            ],
        }
        return {
            "schema_version": 1,
            "round_id": ROUND_ID,
            "selection": "none",
            "selected_answer": None,
            "uploaded_review_sha256": uploaded_review_sha256,
            "stored_review_sha256": sha256_file(review_file),
            "answer_key_sha256": sha256_file(answer_file),
            "route_updates": [],
            "project_decision_document": project_document,
            "production_route_change": False,
        }

    selected = answer_by_sample.get(selection)
    if selected is None:
        raise DoctorFallbackAdjudicationError(
            f"Unknown Doctor fallback selection: {selection!r}."
        )
    method = str(selected.get("method") or "")
    reference_route_key = (
        ANCHOR_REFERENCE_ROUTES.get(method)
        or str(selected.get("route_key") or "").strip()
    )
    if not reference_route_key:
        raise DoctorFallbackAdjudicationError(
            f"Selected Doctor candidate has no reference route: {selection}."
        )

    voice_config = _read_json(project / "voice_config.json", "Voice configuration")
    doctor = voice_config.get("THE DOCTOR")
    if not isinstance(doctor, Mapping):
        raise DoctorFallbackAdjudicationError("THE DOCTOR Voice configuration is missing.")
    policy = doctor.get("responsive_backend_routing")
    routes = policy.get("routes") if isinstance(policy, Mapping) else None
    if not isinstance(routes, Mapping):
        raise DoctorFallbackAdjudicationError("THE DOCTOR routing policy is missing.")
    reference_route = routes.get(reference_route_key)
    if not isinstance(reference_route, Mapping):
        raise DoctorFallbackAdjudicationError(
            f"Selected Doctor reference route is missing: {reference_route_key}."
        )
    if (
        reference_route.get("backend") != "qwen3_instruction_controlled"
        or reference_route.get("approval_tier") != "strict"
        or not reference_route.get("production_promotion_allowed")
    ):
        raise DoctorFallbackAdjudicationError(
            f"Selected Doctor reference route is not strict-approved: {reference_route_key}."
        )

    project_document["decisions"]["THE DOCTOR"] = {
        "status": "approved",
        "primary_method": method,
        "primary_candidate_id": selection,
        "summary": (
            f"The focused best-or-none review selected {selection}. The winning render used "
            f"the strict-approved {reference_route_key} Doctor identity reference, which now "
            "replaces the rejected playful fallback reference for dry/eccentric dialogue."
        ),
        "production_action": "replace_route",
        "preserve_prior_routes": True,
        "route_key": "ordinary_identity",
        "approval_tier": "strict",
        "evidence_sample_ids": [selection],
        "unresolved_requirements": [],
    }
    route_update = {
        "voice": "THE DOCTOR",
        "route_key": "ordinary_identity",
        "expected_configuration_fingerprint": str(
            doctor.get("responsive_backend_configuration_fingerprint") or ""
        ),
        "backend": "qwen3_instruction_controlled",
        "identity_audio": str(reference_route["identity_audio"]),
        "identity_audio_sha256": str(reference_route["identity_audio_sha256"]),
        "identity_text": str(reference_route["identity_text"]),
        "control": {},
        "effect_chain": None,
        "approval_tier": "strict",
        "clear_performance_reference": True,
    }
    return {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "selection": selection,
        "selected_answer": selected,
        "selected_reference_route": reference_route_key,
        "uploaded_review_sha256": uploaded_review_sha256,
        "stored_review_sha256": sha256_file(review_file),
        "answer_key_sha256": sha256_file(answer_file),
        "route_updates": [route_update],
        "project_decision_document": project_document,
        "production_route_change": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=str(DEFAULT_PROJECT))
    parser.add_argument("--review", default=str(DEFAULT_REVIEW))
    parser.add_argument("--answer-key", default=str(DEFAULT_ANSWER_KEY))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--uploaded-review-sha256")
    args = parser.parse_args()
    result = adjudicate(
        project_root=args.project_root,
        review_path=args.review,
        answer_key_path=args.answer_key,
        uploaded_review_sha256=args.uploaded_review_sha256,
    )
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(output),
        "selection": result["selection"],
        "selected_reference_route": result.get("selected_reference_route"),
        "production_route_change": result["production_route_change"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
