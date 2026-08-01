#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
OLD_BRANCH = "research/original-sin-adaptation-overlaps"
EXPECTED_OLD_HEAD = "56c9aa3e794932d5a13736b6b05087eb3a91d350"
DEFAULT_PROJECT_ROOT = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665"
)
DEFAULT_RECEIPT = ROOT / "benchmarks/original_sin_adaptation_followup_reconciliation_v1.json"
BELTEMPEST_ANCHOR_ID = "52c386b56c630e95"
BELTEMPEST_ANCHOR_SHA256 = (
    "3e059188da8566f7283e8d39ef3fdb445d6639a466541ca061dd5a9649896c44"
)
BELTEMPEST_MODES = (
    "beltempest_interrogative_impatience",
    "beltempest_military_volatility",
    "beltempest_weary_resignation",
    "beltempest_urgent_command",
)

USER_REVIEW_LINEAGE = (
    (
        "alexandria_original_sin_overlap_reference_cleanliness_v1-tristan(1)(1).json",
        "benchmarks/original_sin_overlap_reference_cleanliness_review.json",
        "alexandria_original_sin_overlap_reference_cleanliness_v1",
    ),
    (
        "alexandria_original_sin_direct_substitution_pilot_v1-tristan.json",
        "benchmarks/original_sin_direct_substitution_review_v1.json",
        "alexandria_original_sin_direct_substitution_pilot_v1",
    ),
    (
        "alexandria_original_sin_overlap_reference_repair_shortlist_v2-tristan.json",
        "benchmarks/original_sin_overlap_reference_repair_review_v2.json",
        "alexandria_original_sin_overlap_reference_repair_shortlist_v2",
    ),
    (
        "alexandria_original_sin_overlap_reference_repair_v3-tristan.json",
        "benchmarks/original_sin_overlap_reference_repair_review_v3.json",
        "alexandria_original_sin_overlap_reference_repair_v3",
    ),
    (
        "alexandria_original_sin_direct_substitution_repair_v2-tristan.json",
        "benchmarks/original_sin_direct_substitution_repair_review_v2.json",
        "alexandria_original_sin_direct_substitution_repair_v2",
    ),
    (
        "alexandria_original_sin_direct_substitution_final_repair_v3-tristan.json",
        "benchmarks/original_sin_direct_substitution_final_repair_review_v3.json",
        "alexandria_original_sin_direct_substitution_final_repair_v3",
    ),
    (
        "alexandria_original_sin_overlap_reference_final_repair_v4-tristan.json",
        "benchmarks/original_sin_overlap_reference_final_repair_review_v4.json",
        "alexandria_original_sin_overlap_reference_final_repair_v4",
    ),
    (
        "alexandria_original_sin_unseen_expression_v1-tristan.json",
        "benchmarks/original_sin_unseen_expression_review_v1.json",
        "alexandria_original_sin_unseen_expression_v1",
    ),
    (
        "alexandria_original_sin_direct_substitution_boundary_repair_v4-tristan.json",
        "benchmarks/original_sin_direct_substitution_boundary_repair_review_v4.json",
        "alexandria_original_sin_direct_substitution_boundary_repair_v4",
    ),
    (
        "alexandria_original_sin_overlap_reference_boundary_repair_v5-tristan.json",
        "benchmarks/original_sin_overlap_reference_boundary_repair_review_v5.json",
        "alexandria_original_sin_overlap_reference_boundary_repair_v5",
    ),
)


class ReconciliationError(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReconciliationError(f"Could not read {path}: {exc}") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_text(spec: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "show", spec], cwd=ROOT, text=True
        )
    except subprocess.CalledProcessError as exc:
        raise ReconciliationError(f"Could not read Git object {spec}.") from exc


def git_json(path: str) -> Mapping[str, Any]:
    try:
        value = json.loads(git_text(f"{OLD_BRANCH}:{path}"))
    except json.JSONDecodeError as exc:
        raise ReconciliationError(f"Invalid JSON in {OLD_BRANCH}:{path}.") from exc
    if not isinstance(value, Mapping):
        raise ReconciliationError(f"Expected an object in {OLD_BRANCH}:{path}.")
    return value


def git_oid(spec: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", spec], cwd=ROOT, text=True
        ).strip()
    except subprocess.CalledProcessError as exc:
        raise ReconciliationError(f"Could not resolve Git object {spec}.") from exc


def chunk_rows(value: Any) -> list[Mapping[str, Any]]:
    rows = value if isinstance(value, list) else value.get("chunks", [])
    if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
        raise ReconciliationError("chunks.json has an invalid shape.")
    return rows


def build_receipt(project_root: Path) -> dict[str, Any]:
    old_head = git_oid(OLD_BRANCH)
    if old_head != EXPECTED_OLD_HEAD:
        raise ReconciliationError(
            f"Retired research branch moved: expected {EXPECTED_OLD_HEAD}, got {old_head}."
        )

    lineage = []
    for supplied_name, canonical_path, round_id in USER_REVIEW_LINEAGE:
        review = git_json(canonical_path)
        if review.get("round_id") != round_id:
            raise ReconciliationError(f"Round mismatch in {canonical_path}.")
        lineage.append(
            {
                "supplied_filename": supplied_name,
                "canonical_review_path": canonical_path,
                "round_id": round_id,
                "git_blob_oid": git_oid(f"{OLD_BRANCH}:{canonical_path}"),
                "review_exported_at": review.get("exported_at")
                or review.get("review_exported_at"),
                "status": "durably_ingested_on_preserved_research_branch",
            }
        )

    direct_manifest = git_json(
        "benchmarks/original_sin_overlap_complete_promotion_manifest_v2.json"
    )
    strict_ledger = git_json(
        "benchmarks/original_sin_strict_direct_overlap_ledger_v2.json"
    )
    boundary_decision = git_json(
        "benchmarks/original_sin_boundary_and_expression_decision.json"
    )
    boundary_review = git_json(
        "benchmarks/original_sin_overlap_reference_boundary_repair_review_v5.json"
    )

    coverage = read_json(
        ROOT / "benchmarks/original_sin_overlap_character_coverage_ledger_v4.json"
    )
    multimodel = read_json(
        ROOT / "benchmarks/original_sin_noncore_multimodel_round_v2_decision.json"
    )
    if coverage.get("coverage_program_status") != (
        "complete_pending_production_promotion_and_bot_speaker_remap"
    ):
        raise ReconciliationError("The tracked coverage ledger is not closed.")
    if coverage.get("summary_after_final_v5", {}).get("pending_generation_review") != 0:
        raise ReconciliationError("The tracked coverage ledger still has pending review.")

    workflow_root = project_root / "external_workflows/big_finish_overlap_reference_v1"
    boundary_answer = read_json(
        workflow_root / "reference_boundary_repair_round_v5/private/answer-key.json"
    )
    anchor = boundary_answer.get("candidates", {}).get(BELTEMPEST_ANCHOR_ID)
    if not isinstance(anchor, Mapping):
        raise ReconciliationError("Beltempest anchor is absent from the boundary answer key.")
    anchor_review = boundary_review.get("results", {}).get(BELTEMPEST_ANCHOR_ID)
    if not isinstance(anchor_review, Mapping) or anchor_review.get("decision") != "pass":
        raise ReconciliationError("Beltempest anchor lacks a passing user review.")
    if anchor.get("metrics", {}).get("sha256") != BELTEMPEST_ANCHOR_SHA256:
        raise ReconciliationError("Beltempest anchor hash changed.")
    anchor_path = Path(str(anchor["path"]))
    if sha256_file(anchor_path) != BELTEMPEST_ANCHOR_SHA256:
        raise ReconciliationError("Beltempest anchor bytes do not match the evidence hash.")

    transcript = read_json(workflow_root / "private/transcript.json")
    source_path = Path(str(transcript["source_path"]))
    source_hash = sha256_file(source_path)
    source = anchor.get("source", {})
    if source_hash != source.get("media_sha256"):
        raise ReconciliationError("Adaptation source media hash changed.")

    voice_config_path = project_root / "voice_config.json"
    voice_config = read_json(voice_config_path)
    beltempest = voice_config.get("BELTEMPEST")
    if not isinstance(beltempest, Mapping):
        raise ReconciliationError("Production BELTEMPEST Voice is missing.")
    routes = beltempest.get("responsive_backend_routing", {}).get("routes", {})
    if not isinstance(routes, Mapping):
        raise ReconciliationError("Beltempest responsive routes are missing.")
    neutral = routes.get("neutral")
    if not isinstance(neutral, Mapping):
        raise ReconciliationError("Beltempest neutral route is missing.")
    if neutral.get("identity_audio_sha256") != BELTEMPEST_ANCHOR_SHA256:
        raise ReconciliationError("An expressive reference replaced the neutral anchor.")

    selected_modes = multimodel.get("selected", {})
    mode_rows = []
    for mode_id in BELTEMPEST_MODES:
        route = routes.get(mode_id)
        selected = selected_modes.get(mode_id)
        if not isinstance(route, Mapping) or not isinstance(selected, Mapping):
            raise ReconciliationError(f"Beltempest mode is incomplete: {mode_id}.")
        if route.get("identity_audio_sha256") != BELTEMPEST_ANCHOR_SHA256:
            raise ReconciliationError(f"Beltempest mode changed identity anchor: {mode_id}.")
        performance_hash = route.get("performance_audio_sha256")
        if not performance_hash or performance_hash == BELTEMPEST_ANCHOR_SHA256:
            raise ReconciliationError(f"Beltempest mode lacks separated delivery evidence: {mode_id}.")
        mode_rows.append(
            {
                "mode_id": mode_id,
                "candidate_id": selected.get("candidate_id"),
                "backend": selected.get("backend"),
                "approval_tier": selected.get("approval_tier"),
                "scores": selected.get("scores"),
                "identity_audio_sha256": route.get("identity_audio_sha256"),
                "performance_audio_sha256": performance_hash,
                "performance_text": route.get("performance_text"),
            }
        )

    chunks_path = project_root / "chunks.json"
    chunks = chunk_rows(read_json(chunks_path))
    locked = [row for row in chunks if row.get("approved_audio_lock")]
    if len(locked) != 84:
        raise ReconciliationError(f"Expected 84 approved locks, found {len(locked)}.")
    if {
        row.get("generation_provenance", {}).get("source") for row in locked
    } != {"approved_adaptation_import"}:
        raise ReconciliationError("A locked direct performance lost adaptation provenance.")

    securitybot_ids = (491, 493, 495, 497, 501, 503, 618, 622, 634)
    tobias_ids = (1341, 3669, 3674, 3676, 3680, 3682, 3684)
    by_id = {int(row["id"]): row for row in chunks if str(row.get("id", "")).isdigit()}
    if any(by_id[chunk_id].get("speaker") != "SECURITYBOT" for chunk_id in securitybot_ids):
        raise ReconciliationError("Securitybot BOT split is not installed.")
    if any(by_id[chunk_id].get("speaker") != "TOBIAS VAUGHN" for chunk_id in tobias_ids):
        raise ReconciliationError("Tobias BOT split is not installed.")

    completion_path = project_root / "original_sin_overlap_completion_pack.json"
    completion = read_json(completion_path)
    if completion.get("status") != "installed" or completion.get("route_count") != 20:
        raise ReconciliationError("Final overlap completion pack is not installed.")

    beltempest_decision = next(
        row
        for row in boundary_decision["reference_round"]["character_decisions"]
        if row.get("character") == "Beltempest"
    )
    if beltempest_decision.get("selected_candidate_id") != BELTEMPEST_ANCHOR_ID:
        raise ReconciliationError("Beltempest decision no longer selects the approved anchor.")

    required_remaining = {
        row["character"]: max(0, row["required_mode_count"] - len(row["accepted_modes"]))
        for row in coverage["characters"]
        if row["required_mode_count"] > len(row["accepted_modes"])
    }
    if required_remaining:
        raise ReconciliationError(f"Required expressive deficits remain: {required_remaining}")

    return {
        "schema_version": 1,
        "reconciliation_id": "alexandria_original_sin_adaptation_followup_v1",
        "research_branch": OLD_BRANCH,
        "research_branch_head": old_head,
        "current_integration_base": git_oid("alexandria/b19-t10-integration-20260801"),
        "user_evidence_lineage": lineage,
        "source_audio": {
            "release": "Big Finish Doctor Who: Original Sin",
            "source_file": str(source_path),
            "source_file_sha256": source_hash,
            "source_duration_context": "single full-cast adaptation source",
            "cast_credit": {
                "character": "Beltempest",
                "actor": "Andrew French",
                "release_source": "https://www.bigfinish.com/releases/v/doctor-who-original-sin-1231",
                "role_credit_source": "https://thetimescales.com/Story/story.php?audioid=2269",
            },
        },
        "beltempest": {
            "conclusion": "existing_reference_is_genuinely_usable",
            "materially_different_clean_replacement_required": False,
            "neutral_anchor": {
                "candidate_id": BELTEMPEST_ANCHOR_ID,
                "transcript": anchor.get("transcript"),
                "source_start_seconds": source.get("source_start_seconds"),
                "source_end_seconds": source.get("source_end_seconds"),
                "source_segment": source.get("segment_start"),
                "processing": {
                    "treatment": anchor.get("treatment"),
                    "extraction_model": anchor.get("extraction_model"),
                    "extraction_revision": anchor.get("extraction_revision"),
                },
                "audio_sha256": BELTEMPEST_ANCHOR_SHA256,
                "duration_seconds": anchor.get("metrics", {}).get("duration_seconds"),
                "sample_rate": anchor.get("metrics", {}).get("sample_rate"),
                "word_error_rate": anchor.get("word_error_rate"),
                "first_word_present": anchor.get("first_word_present"),
                "last_word_present": anchor.get("last_word_present"),
                "human_review": {
                    "decision": anchor_review.get("decision"),
                    "isolation": int(anchor_review["isolation"]),
                    "naturalness": int(anchor_review["naturalness"]),
                    "identity": int(anchor_review["identity"]),
                    "usefulness": int(anchor_review["usefulness"]),
                },
            },
            "neutral_anchor_preserved_separately": True,
            "selected_expressive_modes": mode_rows,
        },
        "direct_substitution": {
            "matching_contract": "exact adaptation dialogue only; no paraphrases",
            "strict_overlap_status": direct_manifest["strict_overlap_expansion_status"],
            "book_chunk_matches": direct_manifest["strict_overlap_book_chunk_match_count"],
            "unique_quotations": direct_manifest["strict_overlap_unique_quotation_count"],
            "resolved_bindings": direct_manifest["strict_overlap_resolved_binding_count"],
            "excluded_bindings": direct_manifest["strict_overlap_excluded_binding_count"],
            "untouched_bound_rows": direct_manifest["strict_overlap_untouched_bound_count"],
            "strict_clean_installed": direct_manifest["strict_clean_direct_substitution_count"],
            "restricted_installed": direct_manifest["restricted_direct_substitution_count"],
            "installed_lock_count": len(locked),
            "all_locked_provenance": "approved_adaptation_import",
            "terminal_rejected_chunk_ids": direct_manifest["terminal_rejected_chunk_ids"],
            "strict_ledger_git_blob_oid": git_oid(
                f"{OLD_BRANCH}:benchmarks/original_sin_strict_direct_overlap_ledger_v2.json"
            ),
            "ledger_resolved_bindings": strict_ledger["resolved_binding_count"],
        },
        "character_coverage": {
            "roster_count": coverage["roster_count"],
            "pending_generation_review": coverage["summary_after_final_v5"][
                "pending_generation_review"
            ],
            "remaining_required_modes_by_character": required_remaining,
            "tracked_ledger_status_before_install": coverage["coverage_program_status"],
            "runtime_completion_status": completion["status"],
            "installed_route_count": completion["route_count"],
            "bot_split_installed": True,
            "historical_optional_unsupported_modes": [
                "doctor_urgent_discovery",
                "dantalion_sharp_irritation",
                "bot_synthetic_neutral",
                "computer_interrupted_system",
            ],
        },
        "runtime_proof": {
            "completion_operation_id": completion["operation_id"],
            "completion_pack_sha256": sha256_file(completion_path),
            "chunks_sha256": sha256_file(chunks_path),
            "voice_config_sha256": sha256_file(voice_config_path),
            "approved_direct_audio_lock_count": len(locked),
            "approved_locked_audio_preserved": completion[
                "approved_locked_audio_preserved"
            ],
            "rollback_available": completion["rollback_available"],
        },
        "blind_review_package": {
            "status": "none_required",
            "path": None,
            "reason": (
                "All required character modes are closed, the selected generated routes "
                "already passed Tristan's blind reviews, and the final guarded promotion "
                "plus BOT split is installed. Creating another round would duplicate closed work."
            ),
        },
        "next_decision": {
            "required_now": False,
            "decision": None,
            "optional_reopen_only": (
                "Tristan may explicitly reopen one of the historical unsupported optional "
                "modes, but no unresolved required listening decision remains."
            ),
        },
        "boundaries_observed": {
            "b16_t06_or_t07_modified": False,
            "production_audio_mutated_by_this_reconciliation": False,
            "training_run": False,
            "model_downloaded": False,
            "license_accepted": False,
            "prior_evidence_deleted": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(os.environ.get("ALEXANDRIA_ORIGINAL_SIN_PROJECT_ROOT", DEFAULT_PROJECT_ROOT)),
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    actual = build_receipt(args.project_root)
    if args.check:
        expected = read_json(DEFAULT_RECEIPT)
        if actual != expected:
            raise ReconciliationError("Committed reconciliation receipt is stale.")
        print(json.dumps({"status": "ok", "receipt": str(DEFAULT_RECEIPT)}, indent=2))
        return 0
    print(json.dumps(actual, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
