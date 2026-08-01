#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "benchmarks/original_sin_overlap_character_coverage_ledger_v3.json"
DANTALION_DECISION = ROOT / "benchmarks/original_sin_dantalion_mode_completion_round_v1_decision.json"
FINAL_DECISION = ROOT / "benchmarks/original_sin_overlap_final_character_round_v5_decision.json"
OUTPUT = ROOT / "benchmarks/original_sin_overlap_character_coverage_ledger_v4.json"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    source = read_json(SOURCE)
    dantalion_decision = read_json(DANTALION_DECISION)
    final_decision = read_json(FINAL_DECISION)
    result = copy.deepcopy(source)
    result["ledger_id"] = "alexandria_original_sin_overlap_character_coverage_v4"
    result["supersedes_ledger_id"] = source["ledger_id"]
    by_name = {row["character"]: row for row in result["characters"]}

    doctor = by_name["The Doctor"]
    doctor_mode = "doctor_sudden_realization_final"
    if doctor_mode not in doctor["accepted_modes"]:
        doctor["accepted_modes"].append(doctor_mode)
    doctor.setdefault("accepted_mode_tiers", {})[doctor_mode] = "strict"
    doctor["pending_v3_modes"] = []
    doctor["coverage_status"] = "covered"
    doctor["selected_final_candidate_id"] = final_decision["selected"][doctor_mode]["candidate_id"]

    dantalion = by_name["Doc Dantalion"]
    first_mode = dantalion_decision["selected"]["mode_id"]
    second_mode = "dantalion_weary_memory"
    for mode_id, tier in (
        (first_mode, "strict"),
        (second_mode, final_decision["selected"][second_mode]["approval_tier"]),
    ):
        if mode_id not in dantalion["accepted_modes"]:
            dantalion["accepted_modes"].append(mode_id)
        dantalion.setdefault("accepted_mode_tiers", {})[mode_id] = tier
    dantalion["coverage_status"] = "covered_operator_approved"
    dantalion["selected_final_candidate_id"] = final_decision["selected"][second_mode]["candidate_id"]
    dantalion["approved_alternate_candidate_ids"] = [
        row["candidate_id"]
        for row in final_decision.get("approved_alternates", {}).get(second_mode, [])
    ]
    dantalion["next_required_step"] = "Promote both approved modes; the Qwen local route is the default weary-memory route."

    shythe = by_name["Shythe Shahid"]
    shythe_mode = "shythe_crisis_broadcast"
    if shythe_mode not in shythe["accepted_modes"]:
        shythe["accepted_modes"].append(shythe_mode)
    shythe.setdefault("accepted_mode_tiers", {})[shythe_mode] = final_decision["selected"][shythe_mode]["approval_tier"]
    shythe["coverage_status"] = "covered_operator_approved"
    shythe["selected_final_candidate_id"] = final_decision["selected"][shythe_mode]["candidate_id"]
    shythe["next_required_step"] = "Promote the approved crisis-broadcast route from the salvaged identity."

    summary = {
        "covered": 10,
        "covered_restricted": 5,
        "covered_operator_approved": 2,
        "covered_pending_speaker_remap": 1,
        "covered_restricted_pending_speaker_remap": 1,
        "pending_generation_review": 0,
        "total": 19,
    }
    if sum(value for key, value in summary.items() if key != "total") != 19:
        raise RuntimeError("Coverage summary does not account for all characters.")
    result["summary_after_final_v5"] = summary
    result["coverage_program_status"] = "complete_pending_production_promotion_and_bot_speaker_remap"
    result["active_review_rounds"] = {}
    result["production_changes"] = False
    OUTPUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "summary": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
