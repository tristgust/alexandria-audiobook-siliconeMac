#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "benchmarks/original_sin_overlap_character_coverage_ledger_v1.json"
COVERAGE_DECISION = ROOT / "benchmarks/original_sin_overlap_character_coverage_round_v3_decision.json"
SALVAGE_DECISION = ROOT / "benchmarks/original_sin_overlap_identity_salvage_round_v6_decision.json"
OUTPUT = ROOT / "benchmarks/original_sin_overlap_character_coverage_ledger_v2.json"


MODE_TO_CHARACTER = {
    "doctor_wry_deflection": "The Doctor",
    "doctor_hushed_vulnerability": "The Doctor",
    "bernice_quiet_defiance": "Bernice Summerfield",
    "bernice_bittersweet_nostalgia": "Bernice Summerfield",
    "roz_survivor_reflection": "Roz Forrester",
    "roz_defeated_grief": "Roz Forrester",
    "chris_exposed_vulnerability": "Chris Cwej",
    "powerless_wounded_accusation": "Powerless Friendless",
    "hater_grave_statecraft": "Hater of Humans",
    "evan_broadcast_authority": "Evan Claple",
    "securitybot_identity_repair": "Securitybot",
    "tobias_robot_cold_control": "Tobias Vaughn / Robot",
}


class LedgerError(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    source = read_json(SOURCE)
    coverage = read_json(COVERAGE_DECISION)
    salvage = read_json(SALVAGE_DECISION)
    result = copy.deepcopy(source)
    result["ledger_id"] = "alexandria_original_sin_overlap_character_coverage_v2"
    result["supersedes_ledger_id"] = source["ledger_id"]
    by_name = {row["character"]: row for row in result["characters"]}

    for mode_id, selected in coverage["selected"].items():
        character = MODE_TO_CHARACTER[mode_id]
        row = by_name[character]
        if mode_id not in row["accepted_modes"]:
            row["accepted_modes"].append(mode_id)
        row.setdefault("accepted_mode_tiers", {})[mode_id] = selected["approval_tier"]
        row["pending_v3_modes"] = [
            value for value in row.get("pending_v3_modes", []) if value != mode_id
        ]

    repair_modes = set(coverage["repair_required_modes"])
    for row in result["characters"]:
        row["pending_v3_modes"] = [
            value for value in row.get("pending_v3_modes", []) if value in repair_modes
        ]

    doctor = by_name["Doc Dantalion"]
    doc_selected = salvage["selected"]["DOC DANTALION"]
    doctor["identity_status"] = "approved_salvaged_identity"
    doctor["identity_candidate_id"] = doc_selected["candidate_id"]
    doctor["identity_audio_sha256"] = doc_selected["audio_sha256"]
    doctor["coverage_status"] = "pending_generated_mode_review"
    doctor["next_required_step"] = "Run two blind generated delivery modes using the approved salvaged identity."

    shythe = by_name["Shythe Shahid"]
    shythe["identity_status"] = "pending_completion_scores"
    shythe["identity_candidate_id"] = "5ad130953556d32b"
    shythe["coverage_status"] = "pending_identity_completion_review"
    shythe["next_required_step"] = "Complete cleanliness, naturalness, intelligibility, and contamination scores, then run one blind generated delivery mode if approved."

    homeless = by_name["Homeless Forsaken"]
    homeless["identity_status"] = "identity_transfer_test_required"
    homeless["coverage_status"] = "blocked_identity_source"
    homeless["next_required_step"] = "Blind-test generated identity transfer from the least-contaminated complete adaptation extraction and the existing designed Voice."

    tobias = by_name["Tobias Vaughn / Robot"]
    tobias["speaker_split"]["status"] = "approved_pending_production_remap"
    security = by_name["Securitybot"]
    security["speaker_split"]["status"] = "approved_pending_production_remap"

    for row in result["characters"]:
        accepted = len(row.get("accepted_modes", []))
        required = int(row["required_mode_count"])
        if row["character"] in {"Tobias Vaughn / Robot", "Securitybot"}:
            row["coverage_status"] = (
                "covered_pending_speaker_remap"
                if accepted >= required
                else "pending_generation_review_and_speaker_split"
            )
        elif row["character"] in {"Doc Dantalion", "Shythe Shahid", "Homeless Forsaken"}:
            continue
        elif accepted >= required:
            restricted = any(
                tier == "restricted_user_accepted"
                for tier in row.get("accepted_mode_tiers", {}).values()
            )
            row["coverage_status"] = "covered_restricted" if restricted else "covered"
        else:
            row["coverage_status"] = "pending_generation_review"

    summary = {
        "covered": 9,
        "covered_restricted": 2,
        "covered_pending_speaker_remap": 2,
        "pending_generation_review": 4,
        "pending_identity_completion_review": 1,
        "blocked_identity_source": 1,
        "total": 19,
    }
    if sum(value for key, value in summary.items() if key != "total") != 19:
        raise LedgerError("Coverage summary does not account for the roster.")
    result["summary_after_v3_review"] = summary
    result["active_review_rounds"] = {
        "generated_mode_repairs": "alexandria_original_sin_overlap_character_repairs_round_v4",
        "shythe_identity_completion": "alexandria_original_sin_shythe_identity_completion_round_v7",
        "homeless_identity_transfer": "alexandria_original_sin_homeless_identity_transfer_round_v1",
    }
    result["production_changes"] = False
    OUTPUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "summary": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
