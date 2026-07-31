#!/usr/bin/env python3
"""Build the complete, non-installing Original Sin overlap promotion manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROJECT = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665"
)
WORKFLOW = PROJECT / "external_workflows/big_finish_overlap_reference_v1"
PILOT = ROOT / "benchmarks/original_sin_overlap_production_promotion_manifest_v1.json"
LEDGER = ROOT / "benchmarks/original_sin_strict_direct_overlap_ledger_v2.json"
DEFAULT_OUTPUT = ROOT / "benchmarks/original_sin_overlap_complete_promotion_manifest_v2.json"

DECISION_PATHS = [
    ROOT / "benchmarks/original_sin_direct_overlap_expansion_batch_001_decision.json",
    ROOT / "benchmarks/original_sin_direct_overlap_expansion_followup_decision.json",
    ROOT / "benchmarks/original_sin_direct_overlap_expansion_rounds_003_decision.json",
    ROOT / "benchmarks/original_sin_direct_overlap_expansion_rounds_004_decision.json",
    ROOT / "benchmarks/original_sin_direct_overlap_expansion_rounds_005_decision.json",
    ROOT / "benchmarks/original_sin_direct_overlap_expansion_rounds_006_decision.json",
    ROOT / "benchmarks/original_sin_direct_overlap_expansion_rounds_007_decision.json",
    ROOT / "benchmarks/original_sin_direct_overlap_expansion_rounds_008_decision.json",
    ROOT / "benchmarks/original_sin_direct_overlap_boundary_repair_v9_decision.json",
]

REFERENCE_BANK_EVIDENCE = {
    1247: {
        "reference_bank_tier": "reference_only",
        "delivery_tags": ["computer_identity", "classified_information_delivery"],
        "reason": "Human review retained the line as useful reference-bank evidence but not as a direct chunk replacement.",
    },
    3209: {
        "reference_bank_tier": "reference_only",
        "delivery_tags": ["protective_resolve"],
        "reason": "Human review retained the adaptation performance as Roz reference-bank evidence only.",
    },
    561: {
        "reference_bank_tier": "direct_and_reference",
        "delivery_tags": ["rolled_r", "pitch_variation", "rhetorical_emphasis"],
        "reason": "User explicitly praised the rolled R and pitch variation and approved the line for the Doctor reference bank.",
    },
    2398: {
        "reference_bank_tier": "direct_and_reference",
        "delivery_tags": ["recognition", "expressive_delivery"],
        "reason": "User explicitly marked the clean Doctor line as worthwhile reference-bank evidence.",
    },
    5462: {
        "reference_bank_tier": "direct_and_reference",
        "delivery_tags": ["general_expressive_delivery"],
        "reason": "User explicitly marked the clean repaired Doctor line as useful for the character reference bank.",
    },
    1731: {
        "reference_bank_tier": "direct_and_reference",
        "delivery_tags": ["rolled_r", "age_authority"],
        "reason": "User explicitly approved the line for the Doctor bank and called out its strong rolled-R delivery.",
    },
    1939: {
        "reference_bank_tier": "direct_and_reference",
        "delivery_tags": ["bernice_general_expressive_delivery"],
        "reason": "User explicitly approved the clean line for direct placement and Bernice reference-bank use.",
    },
    4443: {
        "reference_bank_tier": "direct_and_reference",
        "delivery_tags": ["general_expressive_delivery"],
        "reason": "User explicitly marked the terminal repaired Doctor line as useful for the character reference bank.",
    },
}


class CompleteManifestError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CompleteManifestError(f"Expected JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def decision_rounds(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload.get("chunk_decisions"), list):
        return [payload]
    return [
        value
        for value in payload.values()
        if isinstance(value, dict) and isinstance(value.get("chunk_decisions"), list)
    ]


def selected_decisions() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in DECISION_PATHS:
        payload = read_json(path)
        for round_payload in decision_rounds(payload):
            for row in round_payload["chunk_decisions"]:
                candidate_id = row.get("selected_candidate_id")
                if not candidate_id:
                    continue
                selected = dict(row)
                selected["decision_path"] = str(path)
                selected["decision_round_id"] = str(round_payload.get("round_id") or payload.get("round_id") or "")
                rows.append(selected)
    if len(rows) != 80:
        raise CompleteManifestError(f"Expected 80 selected expansion candidates, found {len(rows)}")
    if len({int(row["chunk_id"]) for row in rows}) != len(rows):
        raise CompleteManifestError("Selected expansion chunk IDs are not unique")
    if len({str(row["selected_candidate_id"]) for row in rows}) != len(rows):
        raise CompleteManifestError("Selected expansion candidate IDs are not unique")
    return rows


def answer_key_index(required_ids: set[str]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for path in WORKFLOW.rglob("answer-key.json"):
        payload = read_json(path)
        candidates = payload.get("candidates")
        if not isinstance(candidates, dict):
            continue
        for candidate_id, row in candidates.items():
            if candidate_id not in required_ids:
                continue
            if candidate_id in index:
                raise CompleteManifestError(f"Duplicate answer-key candidate ID: {candidate_id}")
            index[candidate_id] = {
                "answer_key_path": str(path),
                "source_round_id": str(payload.get("round_id") or ""),
                "candidate": row,
            }
    missing = required_ids - set(index)
    if missing:
        raise CompleteManifestError(
            f"Selected candidates are missing from answer keys: {sorted(missing)}"
        )
    return index


def candidate_entry(
    selected: dict[str, Any],
    indexed: dict[str, Any],
    *,
    direct_placement_tier: str | None,
) -> dict[str, Any]:
    candidate_id = str(selected["selected_candidate_id"])
    source = indexed.get(candidate_id)
    if source is None:
        raise CompleteManifestError(f"Candidate is missing from answer keys: {candidate_id}")
    row = source["candidate"]
    wav_path = Path(str(row["wav_path"]))
    proxy_path = Path(str(row["proxy_path"]))
    wav_sha256 = str(row["wav_metrics"]["sha256"])
    proxy_sha256 = str(row["proxy_sha256"])
    if not wav_path.is_file() or sha256_file(wav_path) != wav_sha256:
        raise CompleteManifestError(f"WAV hash mismatch: {candidate_id}")
    if not proxy_path.is_file() or sha256_file(proxy_path) != proxy_sha256:
        raise CompleteManifestError(f"Proxy hash mismatch: {candidate_id}")
    chunk_id = int(selected["chunk_id"])
    if int(row["chunk_id"]) != chunk_id:
        raise CompleteManifestError(f"Chunk mismatch for candidate: {candidate_id}")
    return {
        "candidate_id": candidate_id,
        "source_round_id": source["source_round_id"],
        "answer_key_path": source["answer_key_path"],
        "decision_path": selected["decision_path"],
        "decision_round_id": selected["decision_round_id"],
        "character": str(row["character"]),
        "book_speaker": str(row["book_speaker"]),
        "chunk_id": chunk_id,
        "transcript": str(row["transcript"]),
        "treatment": str(row["treatment"]),
        "audio_path": str(wav_path),
        "audio_sha256": wav_sha256,
        "proxy_path": str(proxy_path),
        "proxy_sha256": proxy_sha256,
        "direct_placement_tier": direct_placement_tier,
        "reference_bank_eligible": chunk_id in REFERENCE_BANK_EVIDENCE,
        "proposed_action": (
            "install_exact_adaptation_chunk_audio"
            if direct_placement_tier == "strict_clean"
            else "install_restricted_exact_adaptation_chunk_audio_only_if_explicitly_included"
            if direct_placement_tier == "restricted_user_accepted_artifacts"
            else "add_reference_bank_evidence"
        ),
    }


def build_manifest() -> dict[str, Any]:
    pilot = read_json(PILOT)
    ledger = read_json(LEDGER)
    selected = selected_decisions()
    index = answer_key_index(
        {str(row["selected_candidate_id"]) for row in selected}
    )

    expansion_direct: list[dict[str, Any]] = []
    reference_only: list[dict[str, Any]] = []
    selected_by_chunk: dict[int, dict[str, Any]] = {}
    for row in selected:
        chunk_id = int(row["chunk_id"])
        outcome = str(row.get("outcome") or "")
        tier = row.get("direct_placement_tier")
        if outcome == "reference-bank evidence only":
            entry = candidate_entry(row, index, direct_placement_tier=None)
            reference_only.append(entry)
            selected_by_chunk[chunk_id] = entry
            continue
        if tier is None:
            if "exact-line substitution eligible" not in outcome:
                raise CompleteManifestError(f"Unclassified selected outcome: {chunk_id}: {outcome}")
            tier = "strict_clean"
        if tier not in {"strict_clean", "restricted_user_accepted_artifacts"}:
            raise CompleteManifestError(f"Unsupported direct tier: {chunk_id}: {tier}")
        entry = candidate_entry(row, index, direct_placement_tier=str(tier))
        expansion_direct.append(entry)
        selected_by_chunk[chunk_id] = entry

    if len(expansion_direct) != 78 or len(reference_only) != 2:
        raise CompleteManifestError(
            f"Expansion split drifted: direct={len(expansion_direct)}, reference_only={len(reference_only)}"
        )
    strict_expansion = [row for row in expansion_direct if row["direct_placement_tier"] == "strict_clean"]
    restricted_expansion = [
        row for row in expansion_direct
        if row["direct_placement_tier"] == "restricted_user_accepted_artifacts"
    ]
    if len(strict_expansion) != 75 or len(restricted_expansion) != 3:
        raise CompleteManifestError(
            f"Direct tiers drifted: strict={len(strict_expansion)}, restricted={len(restricted_expansion)}"
        )

    pilot_direct: list[dict[str, Any]] = []
    for row in pilot["direct_substitutions"]:
        entry = dict(row)
        entry["direct_placement_tier"] = "strict_clean"
        entry["reference_bank_eligible"] = False
        pilot_direct.append(entry)
    direct_substitutions = sorted(
        pilot_direct + expansion_direct,
        key=lambda row: int(row["chunk_id"]),
    )
    if len(direct_substitutions) != 84:
        raise CompleteManifestError("Complete direct-substitution count must be 84")
    if len({int(row["chunk_id"]) for row in direct_substitutions}) != 84:
        raise CompleteManifestError("Complete direct-substitution chunks are not unique")
    if len({str(row["candidate_id"]) for row in direct_substitutions}) != 84:
        raise CompleteManifestError("Complete direct-substitution candidates are not unique")

    reference_bank_evidence: list[dict[str, Any]] = []
    for chunk_id, disposition in REFERENCE_BANK_EVIDENCE.items():
        source = selected_by_chunk.get(chunk_id)
        if source is None:
            raise CompleteManifestError(f"Reference-bank chunk has no selected candidate: {chunk_id}")
        entry = dict(source)
        entry.update(disposition)
        entry["proposed_action"] = "add_direct_adaptation_reference_bank_evidence"
        reference_bank_evidence.append(entry)
    reference_bank_evidence.sort(key=lambda row: int(row["chunk_id"]))

    protected = {
        "voice_config.json": sha256_file(PROJECT / "voice_config.json"),
        "chunks.json": sha256_file(PROJECT / "chunks.json"),
    }
    if protected != pilot["protected_project_hashes_after"]:
        raise CompleteManifestError("Protected project hashes drifted from the reviewed baseline")

    terminal = read_json(
        ROOT / "benchmarks/original_sin_direct_overlap_boundary_repair_v9_decision.json"
    )
    terminal_rejected = [
        int(row["chunk_id"])
        for row in terminal["chunk_decisions"]
        if row["direct_placement_tier"] == "rejected_terminal"
    ]

    return {
        "schema_version": 2,
        "promotion_id": "alexandria_original_sin_overlap_complete_promotion_v2",
        "supersedes_promotion_id": pilot["promotion_id"],
        "supersedes_manifest": str(PILOT),
        "project_root": str(PROJECT),
        "protected_project_hashes_before": protected,
        "protected_project_hashes_after": protected,
        "strict_overlap_expansion_status": "completed_and_fully_dispositioned",
        "strict_overlap_book_chunk_match_count": int(ledger["book_chunk_match_count"]),
        "strict_overlap_unique_quotation_count": int(ledger["unique_quotation_count"]),
        "strict_overlap_resolved_binding_count": int(ledger["resolved_binding_count"]),
        "strict_overlap_excluded_binding_count": int(ledger["excluded_binding_count"]),
        "strict_overlap_untouched_bound_count": 0,
        "identity_anchor_count": len(pilot["identity_anchors"]),
        "adaptation_performance_reference_count": len(pilot["adaptation_performance_references"]),
        "expressive_mode_count": len(pilot["expressive_modes"]),
        "strict_clean_direct_substitution_count": 81,
        "restricted_direct_substitution_count": 3,
        "direct_substitution_count": 84,
        "reference_bank_evidence_count": len(reference_bank_evidence),
        "reference_only_evidence_count": len(reference_only),
        "unresolved_character_count": len(pilot["unresolved_characters"]),
        "identity_anchors": pilot["identity_anchors"],
        "adaptation_performance_references": pilot["adaptation_performance_references"],
        "expressive_modes": pilot["expressive_modes"],
        "direct_substitutions": direct_substitutions,
        "restricted_direct_substitutions": restricted_expansion,
        "reference_bank_evidence": reference_bank_evidence,
        "reference_only_evidence": reference_only,
        "terminal_rejected_chunk_ids": sorted(terminal_rejected),
        "unresolved_characters": pilot["unresolved_characters"],
        "installation_authorized": False,
        "restricted_tier_default_inclusion": False,
        "requires_explicit_restricted_tier_confirmation": True,
        "requires_separate_promotion_receipt": True,
        "required_promotion_controls": [
            *pilot["required_promotion_controls"],
            "explicit include/exclude decision for each restricted direct-placement-only chunk",
            "copy every selected WAV and production proxy into project-owned immutable asset paths",
            "verify source and copied hashes before binding",
            "record reference-bank mode tags and provenance separately from direct chunk bindings",
            "run a production-context blind listen before final export eligibility",
        ],
        "production_changes": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    write_json(args.output, build_manifest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
