#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "benchmarks/original_sin_overlap_character_coverage_ledger_v2.json"
REPAIR_DECISION = ROOT / "benchmarks/original_sin_overlap_character_repairs_round_v4_decision.json"
HOMELESS_DECISION = ROOT / "benchmarks/original_sin_homeless_identity_transfer_round_v1_decision.json"
SHYTHE_DECISION = ROOT / "benchmarks/original_sin_shythe_identity_completion_round_v7_decision.json"
OUTPUT = ROOT / "benchmarks/original_sin_overlap_character_coverage_ledger_v3.json"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    source = read_json(SOURCE)
    repairs = read_json(REPAIR_DECISION)
    homeless = read_json(HOMELESS_DECISION)
    shythe = read_json(SHYTHE_DECISION)
    result = copy.deepcopy(source)
    result["ledger_id"] = "alexandria_original_sin_overlap_character_coverage_v3"
    result["supersedes_ledger_id"] = source["ledger_id"]
    by_name = {row["character"]: row for row in result["characters"]}

    doctor = by_name["The Doctor"]
    doctor["accepted_modes"].append("doctor_weary_moral_gravity")
    doctor.setdefault("accepted_mode_tiers", {})["doctor_weary_moral_gravity"] = "strict"
    doctor["pending_v3_modes"] = ["doctor_urgent_discovery"]
    doctor["coverage_status"] = "pending_generation_review"

    roz = by_name["Roz Forrester"]
    roz["accepted_modes"].append("roz_dry_banter")
    roz.setdefault("accepted_mode_tiers", {})["roz_dry_banter"] = "restricted_user_accepted"
    roz["pending_v3_modes"] = []
    roz["coverage_status"] = "covered_restricted"

    computer = by_name["Computer"]
    computer["accepted_modes"].append("computer_formal_system_response")
    computer.setdefault("accepted_mode_tiers", {})["computer_formal_system_response"] = "strict"
    computer["pending_v3_modes"] = []
    computer["coverage_status"] = "covered"
    computer["selected_processing_candidate_id"] = repairs["selected"]["computer_processing_repair"]["candidate_id"]

    shythe_row = by_name["Shythe Shahid"]
    shythe_row["identity_status"] = "approved_salvaged_identity"
    shythe_row["identity_candidate_id"] = shythe["selected"]["candidate_id"]
    shythe_row["identity_audio_sha256"] = shythe["selected"]["audio_sha256"]
    shythe_row["coverage_status"] = "pending_generated_mode_review"
    shythe_row["next_required_step"] = "Run one blind generated crisis-broadcast mode using the approved identity."

    homeless_row = by_name["Homeless Forsaken"]
    homeless_row["identity_status"] = "approved_generated_identity_transfer"
    homeless_row["accepted_modes"] = ["homeless_identity_transfer"]
    homeless_row["accepted_mode_tiers"] = {"homeless_identity_transfer": "restricted_user_accepted"}
    homeless_row["coverage_status"] = "covered_restricted"
    homeless_row["selected_candidate_id"] = homeless["selected"]["candidate_id"]
    homeless_row["source_audio_status"] = homeless["source_audio_status"]
    homeless_row["next_required_step"] = "Promote the clean generated transfer as a derived identity route; do not install the contaminated source audio."

    under = by_name["Under-Sergeant"]
    under["coverage_status"] = "covered_restricted"
    under.setdefault("accepted_mode_tiers", {})["under_sergeant_military_menace"] = "restricted_user_accepted"
    powerless = by_name["Powerless Friendless"]
    powerless["coverage_status"] = "covered_restricted"
    powerless.setdefault("accepted_mode_tiers", {})["powerless_panicked_urgency"] = "restricted_user_accepted"
    security = by_name["Securitybot"]
    security["coverage_status"] = "covered_restricted_pending_speaker_remap"

    dantalion = by_name["Doc Dantalion"]
    dantalion["coverage_status"] = "pending_generated_mode_review"
    dantalion["pending_completion_candidate_id"] = repairs["pending_completion_review"]["dantalion_dry_sardonic"]["candidate_id"]
    dantalion["next_required_step"] = "Complete scores for the dry sardonic candidate and approve one distinct weary-memory generated mode."

    summary = {
        "covered": 9,
        "covered_restricted": 5,
        "covered_pending_speaker_remap": 1,
        "covered_restricted_pending_speaker_remap": 1,
        "pending_generation_review": 3,
        "total": 19,
    }
    if sum(value for key, value in summary.items() if key != "total") != 19:
        raise RuntimeError("Coverage summary does not account for all characters.")
    result["summary_after_repair_v4"] = summary
    result["active_review_rounds"] = {
        "final_generated_modes": "alexandria_original_sin_overlap_final_character_round_v5",
        "dantalion_score_completion": "alexandria_original_sin_dantalion_mode_completion_round_v1",
    }
    result["production_changes"] = False
    OUTPUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "summary": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
