#!/usr/bin/env python3
"""Objectively screen and package the Original Sin v2 repair shortlist."""
from __future__ import annotations

import argparse
import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any


V1_ROUND_ID = "alexandria_original_sin_overlap_reference_cleanliness_v1"
V2_ROUND_ID = "alexandria_original_sin_overlap_reference_repair_v2"
SHORTLIST_ROUND_ID = "alexandria_original_sin_overlap_reference_repair_shortlist_v2"
EXPECTED_V2_CANDIDATES = 33
EXPECTED_V2_ELIGIBLE = 19
EXPECTED_SHORTLIST_CANDIDATES = 20
CHARACTER_ORDER = (
    "Bernice Summerfield",
    "The Doctor",
    "Chris Cwej",
    "Beltempest",
    "Under-Sergeant",
    "Computer",
    "Doc Dantalion",
    "Homeless Forsaken",
    "Evan Claple",
    "Shythe Shahid",
    "Tobias Vaughn / Robot",
)


class RepairScreenError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RepairScreenError(f"Expected JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def objective_eligible(candidate: dict[str, Any], *, require_last_word: bool) -> bool:
    try:
        wer = float(candidate["word_error_rate"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RepairScreenError("Candidate has no valid word_error_rate") from exc
    if wer != 0.0 or candidate.get("first_word_present") is not True:
        return False
    if require_last_word and candidate.get("last_word_present") is not True:
        return False
    return True


def build_screen(v2_key: dict[str, Any], v1_key: dict[str, Any]) -> dict[str, Any]:
    if v2_key.get("round_id") != V2_ROUND_ID:
        raise RepairScreenError("v2 answer-key round_id mismatch")
    if v1_key.get("round_id") != V1_ROUND_ID:
        raise RepairScreenError("v1 answer-key round_id mismatch")
    v2_candidates = v2_key.get("candidates")
    v1_candidates = v1_key.get("candidates")
    if not isinstance(v2_candidates, dict) or not isinstance(v1_candidates, dict):
        raise RepairScreenError("Answer-key candidates must be objects")
    if len(v2_candidates) != EXPECTED_V2_CANDIDATES:
        raise RepairScreenError(
            f"Expected {EXPECTED_V2_CANDIDATES} v2 candidates; found {len(v2_candidates)}"
        )

    rows: list[dict[str, Any]] = []
    shortlist: list[dict[str, Any]] = []
    for candidate_id, candidate in v2_candidates.items():
        eligible = objective_eligible(candidate, require_last_word=True)
        row = {
            "candidate_id": candidate_id,
            "source_round_id": V2_ROUND_ID,
            "character": str(candidate.get("character") or ""),
            "treatment": str(candidate.get("treatment") or ""),
            "automatic_transcript": str(candidate.get("automatic_transcript") or ""),
            "word_error_rate": float(candidate["word_error_rate"]),
            "first_word_present": candidate.get("first_word_present") is True,
            "last_word_present": candidate.get("last_word_present") is True,
            "objective_eligible": eligible,
            "shortlisted": eligible,
        }
        rows.append(row)
        if eligible:
            shortlist.append(
                {
                    "candidate_id": candidate_id,
                    "source_round_id": V2_ROUND_ID,
                    "candidate": candidate,
                }
            )

    if len(shortlist) != EXPECTED_V2_ELIGIBLE:
        raise RepairScreenError(
            f"Expected {EXPECTED_V2_ELIGIBLE} objectively eligible v2 candidates; found {len(shortlist)}"
        )

    prior_under = [
        (candidate_id, candidate)
        for candidate_id, candidate in v1_candidates.items()
        if candidate.get("character") == "Under-Sergeant"
        and candidate.get("variant") == "mel_roformer_vocal"
        and objective_eligible(candidate, require_last_word=False)
    ]
    if len(prior_under) != 1:
        raise RepairScreenError(
            f"Expected one exact prior Under-Sergeant Mel-RoFormer candidate; found {len(prior_under)}"
        )
    prior_id, prior_candidate = prior_under[0]
    carried = dict(prior_candidate)
    carried["treatment"] = str(carried.pop("variant"))
    carried["review_context"] = (
        "This is the only objectively exact Under-Sergeant candidate from v1. "
        "Judge whether the radio/speaker coloration is scene-specific enough to restrict it to performance reference use."
    )
    shortlist.append(
        {
            "candidate_id": prior_id,
            "source_round_id": V1_ROUND_ID,
            "candidate": carried,
        }
    )
    rows.append(
        {
            "candidate_id": prior_id,
            "source_round_id": V1_ROUND_ID,
            "character": "Under-Sergeant",
            "treatment": "mel_roformer_vocal",
            "automatic_transcript": str(carried.get("automatic_transcript") or ""),
            "word_error_rate": float(carried["word_error_rate"]),
            "first_word_present": carried.get("first_word_present") is True,
            "last_word_present": None,
            "objective_eligible": True,
            "shortlisted": True,
            "carried_from_prior_round": True,
        }
    )

    if len(shortlist) != EXPECTED_SHORTLIST_CANDIDATES:
        raise RepairScreenError(
            f"Expected {EXPECTED_SHORTLIST_CANDIDATES} shortlist candidates; found {len(shortlist)}"
        )

    by_character: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in shortlist:
        by_character[str(entry["candidate"].get("character") or "")].append(entry)
    decisions = []
    for character in CHARACTER_ORDER:
        candidates = by_character.get(character, [])
        if character == "Homeless Forsaken":
            outcome = "requires a replacement source or new extraction"
        elif candidates:
            outcome = "ready for blind repair review"
        else:
            raise RepairScreenError(f"No shortlist candidate or explicit disposition for {character}")
        decisions.append(
            {
                "character": character,
                "outcome": outcome,
                "shortlist_candidate_ids": [entry["candidate_id"] for entry in candidates],
            }
        )

    return {
        "schema_version": 1,
        "round_id": SHORTLIST_ROUND_ID,
        "v2_candidate_count": len(v2_candidates),
        "v2_objective_eligible_count": EXPECTED_V2_ELIGIBLE,
        "prior_candidate_count": 1,
        "shortlist_candidate_count": len(shortlist),
        "objective_rejected_v2_count": len(v2_candidates) - EXPECTED_V2_ELIGIBLE,
        "production_changes": False,
        "project_voice_config_changed": False,
        "project_chunks_changed": False,
        "character_decisions": decisions,
        "candidates": sorted(
            rows,
            key=lambda row: (
                CHARACTER_ORDER.index(row["character"]),
                row["source_round_id"],
                row["candidate_id"],
            ),
        ),
        "_shortlist_entries": shortlist,
    }


def package_review(
    *,
    report: dict[str, Any],
    output: Path,
    source_index: Path,
) -> None:
    if output.exists():
        shutil.rmtree(output)
    review_audio = output / "review" / "audio"
    review_audio.mkdir(parents=True, exist_ok=True)
    private_candidates: dict[str, Any] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in report["_shortlist_entries"]:
        candidate_id = entry["candidate_id"]
        candidate = entry["candidate"]
        source = Path(str(candidate["path"])).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        destination = review_audio / f"{candidate_id}.wav"
        shutil.copy2(source, destination)
        public = {"id": candidate_id, "audio": f"audio/{candidate_id}.wav"}
        grouped[str(candidate["character"])].append(public)
        private_candidates[candidate_id] = {
            **candidate,
            "path": str(source),
            "source_round_id": entry["source_round_id"],
        }

    contexts = {
        entry["candidate"]["character"]: entry["candidate"].get("review_context")
        for entry in report["_shortlist_entries"]
        if entry["candidate"].get("review_context")
    }
    transcripts = {
        entry["candidate"]["character"]: str(
            entry["candidate"].get("transcript")
            or entry["candidate"].get("automatic_transcript")
            or ""
        )
        for entry in report["_shortlist_entries"]
    }
    groups = []
    for character in CHARACTER_ORDER:
        if character == "Homeless Forsaken":
            continue
        groups.append(
            {
                "character": character,
                "transcript": transcripts[character],
                "review_context": contexts.get(character),
                "candidates": grouped[character],
            }
        )
    payload = {"schema_version": 1, "round_id": SHORTLIST_ROUND_ID, "groups": groups}
    (output / "review" / "data.js").write_text(
        "window.ORIGINAL_SIN_REPAIR_ROUND = " + json.dumps(payload, indent=2, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    shutil.copy2(source_index, output / "review" / "index.html")
    write_json(
        output / "private" / "answer-key.json",
        {
            "schema_version": 1,
            "round_id": SHORTLIST_ROUND_ID,
            "candidates": private_candidates,
            "production_changes": False,
        },
    )
    public_report = {key: value for key, value in report.items() if key != "_shortlist_entries"}
    write_json(output / "objective-screen.json", public_report)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Original Sin overlap reference repair v2 objective screen",
        "",
        f"Round: `{report['round_id']}`",
        "",
        f"V2 candidates: {report['v2_candidate_count']}; objectively eligible: {report['v2_objective_eligible_count']}; carried prior exact candidate: {report['prior_candidate_count']}; blind shortlist: {report['shortlist_candidate_count']}.",
        "",
        "No Alexandria Voice assignment or chunk audio was changed.",
        "",
        "## Character dispositions",
        "",
        "| Character | Outcome | Shortlist candidates |",
        "|---|---|---|",
    ]
    for decision in report["character_decisions"]:
        ids = ", ".join(f"`{candidate_id}`" for candidate_id in decision["shortlist_candidate_ids"]) or "—"
        lines.append(f"| {decision['character']} | {decision['outcome']} | {ids} |")
    lines.extend(
        [
            "",
            "## Objective screen",
            "",
            "| Candidate | Character | Source round | Treatment | WER | First | Last | Eligible | Shortlisted |",
            "|---|---|---|---|---:|---|---|---|---|",
        ]
    )
    for row in report["candidates"]:
        last = "n/a" if row["last_word_present"] is None else ("pass" if row["last_word_present"] else "fail")
        lines.append(
            f"| `{row['candidate_id']}` | {row['character']} | `{row['source_round_id']}` | `{row['treatment']}` | {row['word_error_rate']:.3f} | {'pass' if row['first_word_present'] else 'fail'} | {last} | {'yes' if row['objective_eligible'] else 'no'} | {'yes' if row['shortlisted'] else 'no'} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v2-answer-key", type=Path, required=True)
    parser.add_argument("--v1-answer-key", type=Path, required=True)
    parser.add_argument("--source-index", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-markdown", type=Path, required=True)
    args = parser.parse_args()
    report = build_screen(read_json(args.v2_answer_key), read_json(args.v1_answer_key))
    package_review(
        report=report,
        output=args.output_root.expanduser().resolve(),
        source_index=args.source_index.expanduser().resolve(),
    )
    public_report = {key: value for key, value in report.items() if key != "_shortlist_entries"}
    write_json(args.report_json, public_report)
    args.report_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.report_markdown.write_text(render_markdown(public_report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
