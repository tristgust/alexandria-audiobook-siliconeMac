#!/usr/bin/env python3
"""Unblind the Chris/Roz source review and preferred Fish-router retest.

The source review is intentionally partial after the user removed all T'Nia
Miller style candidates. This analyzer requires every non-T'Nia candidate to
have a review row, joins scores to the private answer keys, preserves all
rejections, and emits one post-review selection and routing summary.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
SELECTION_PATH = ROOT / "benchmarks/chris_roz_postreview_selection.json"
DEFAULT_SOURCE_EXPORT = Path.home() / "Downloads/alexandria_chris_roz_consolidated_reference_review_v2-tristan.json"
DEFAULT_FISH_EXPORT = Path.home() / "Downloads/alexandria_fish_s21_preferred_router_retest_v1-tristan.json"
DEFAULT_SOURCE_KEY = Path("/private/tmp/alexandria-chris-roz-final-reference-review-v2/private/answer-key.json")
DEFAULT_FISH_KEY = Path("/private/tmp/alexandria-fish-preferred-router-retest-v1/private/answer-key.json")
DEFAULT_OUTPUT_ROOT = ROOT / ".omo/evidence/chris-roz-postreview-v1"


class PostReviewError(ValueError):
    """Raised when a review export cannot be safely joined to its answer key."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    if not path.is_file():
        raise PostReviewError(f"Required JSON file is missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PostReviewError(f"Could not read JSON file {path}: {exc}") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rating(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise PostReviewError(f"Invalid rating value: {value!r}") from exc
    if not 1 <= result <= 5:
        raise PostReviewError(f"Rating is outside 1–5: {result}")
    return result


def candidate_descriptor(answer: Mapping[str, Any]) -> dict[str, Any]:
    kind = str(answer.get("kind") or "")
    if kind == "ecapa_large_v3_supplement":
        candidate = dict(answer["candidate"])
        return {
            "kind": kind,
            "logical_id": str(candidate["id"]),
            "identity": str(candidate.get("character") or ""),
            "speaker": str(candidate.get("speaker") or ""),
            "role": str(candidate.get("role") or ""),
            "source_key": str(candidate.get("source_key") or ""),
            "start_seconds": float(candidate["start_seconds"]),
            "end_seconds": float(candidate["end_seconds"]),
            "delivery": str(candidate.get("delivery") or ""),
            "transcript": str(candidate.get("transcript_provisional") or ""),
            "speaker_gate": answer.get("speaker_consistency", {}).get("gate", {}).get("status"),
            "transcript_gate": answer.get("transcript_gate", {}).get("status"),
        }
    if kind in {"curated_performance", "tnia_style"}:
        trim = answer.get("trim") or {}
        return {
            "kind": kind,
            "logical_id": str(answer.get("id") or ""),
            "identity": str(answer.get("identity") or ""),
            "speaker": "",
            "role": "delivery_style_only" if kind == "tnia_style" else "canonical_delivery",
            "source_key": str(answer.get("source_key") or ""),
            "start_seconds": float(trim["start_seconds"]),
            "end_seconds": float(trim["end_seconds"]),
            "delivery": str(answer.get("delivery") or ""),
            "transcript": str(answer.get("transcript") or ""),
            "speaker_gate": None,
            "transcript_gate": "pinned_asr_checked",
        }
    if kind == "identity_scan":
        preview_id = str(answer.get("preview_id") or "")
        identity = preview_id.split("-", 1)[0] if preview_id else ""
        return {
            "kind": kind,
            "logical_id": preview_id,
            "identity": identity,
            "speaker": "",
            "role": "identity",
            "source_key": str(answer.get("source_key") or ""),
            "start_seconds": float(answer["start_seconds"]),
            "end_seconds": float(answer["end_seconds"]),
            "delivery": "identity_scan",
            "transcript": str(answer.get("text") or ""),
            "speaker_gate": "wavlm_scan_pass",
            "transcript_gate": "pinned_asr_unverified_by_listener",
        }
    raise PostReviewError(f"Unknown source candidate kind: {kind!r}")


def cleanup_flags(notes: str) -> list[str]:
    normalized = notes.casefold()
    flags: list[str] = []
    tests = (
        ("background_or_sfx", ("background", "sound effects", "music")),
        ("room_or_echo", ("room out", "echoey")),
        ("start_boundary", ("starts late", "starts mid", "starts midway", "weird start")),
        ("end_boundary", ("cuts off", "finishes the last word", "finishes saying", "before the end")),
        ("multiple_speakers", ("two voices", "two different actors", "more than one voice", "somebody else has a laugh")),
        ("wrong_speaker", ("wrong person",)),
        ("out_of_character", ("out of character",)),
        ("quiet_voice", ("quiet voice",)),
    )
    for label, needles in tests:
        if any(needle in normalized for needle in needles):
            flags.append(label)
    return flags


def join_source_review(
    export_path: Path,
    key_path: Path,
    selection: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    export = read_json(export_path)
    answer = read_json(key_path)
    expected_round = str(selection["source_review_round_id"])
    if export.get("round_id") != expected_round or answer.get("round_id") != expected_round:
        raise PostReviewError("Source-review round IDs do not match the selection contract.")
    scores = export.get("scores")
    candidates = answer.get("candidates")
    if not isinstance(scores, Mapping) or not isinstance(candidates, Mapping):
        raise PostReviewError("Source review or answer key is malformed.")

    joined: list[dict[str, Any]] = []
    removed_style_rows: list[dict[str, Any]] = []
    for blind_id, raw_answer in candidates.items():
        descriptor = candidate_descriptor(raw_answer)
        is_tnia = (
            descriptor["kind"] == "tnia_style"
            or descriptor["identity"] == "tnia_style"
            or descriptor["speaker"] == "tnia_miller"
            or descriptor["role"] == "delivery_style_only"
            or descriptor["logical_id"].startswith("tnia_")
        )
        if is_tnia:
            removed_style_rows.append({"blind_id": blind_id, **descriptor})
            continue
        score = scores.get(blind_id)
        if not isinstance(score, Mapping):
            raise PostReviewError(f"Non-T'Nia candidate is missing a score row: {blind_id}")
        notes = str(score.get("notes") or "").strip()
        joined.append(
            {
                "blind_id": blind_id,
                **descriptor,
                "ratings": {
                    "identity": rating(score.get("identity")),
                    "delivery": rating(score.get("delivery")),
                    "cleanliness": rating(score.get("cleanliness")),
                    "naturalness": rating(score.get("naturalness")),
                    "usefulness": rating(score.get("usefulness")),
                },
                "retain": score.get("retain") is True,
                "notes": notes,
                "cleanup_flags": cleanup_flags(notes),
            }
        )
    unknown_scores = sorted(set(scores) - set(candidates))
    if unknown_scores:
        raise PostReviewError(f"Source export contains unknown blind IDs: {unknown_scores}")
    return joined, removed_style_rows


def join_fish_review(export_path: Path, key_path: Path, selection: Mapping[str, Any]) -> list[dict[str, Any]]:
    export = read_json(export_path)
    answer = read_json(key_path)
    expected_round = str(selection["fish_review_round_id"])
    if export.get("round_id") != expected_round or answer.get("round_id") != expected_round:
        raise PostReviewError("Fish-review round IDs do not match the selection contract.")
    scores = export.get("scores")
    samples = answer.get("samples")
    if not isinstance(scores, Mapping) or not isinstance(samples, Mapping):
        raise PostReviewError("Fish review or answer key is malformed.")
    if set(scores) != set(samples):
        raise PostReviewError(
            f"Fish export must be complete: scores={len(scores)}, answer={len(samples)}."
        )
    rows: list[dict[str, Any]] = []
    for blind_id, score in scores.items():
        source = dict(samples[blind_id])
        rows.append(
            {
                "blind_id": blind_id,
                "identity": str(source["identity"]),
                "test_key": str(source["test_key"]),
                "style": str(source["style"]),
                "prompt_mode": str(source["prompt_mode"]),
                "repeat": int(source["repeat"]),
                "ratings": {
                    "identity": rating(score.get("identity")),
                    "delivery": rating(score.get("delivery")),
                    "naturalness": rating(score.get("naturalness")),
                    "text": rating(score.get("text")),
                    "artifacts": rating(score.get("artifacts")),
                },
                "retain": score.get("retain") is True,
                "notes": str(score.get("notes") or "").strip(),
            }
        )
    return rows


def mean_present(rows: Iterable[Mapping[str, Any]], field: str) -> float | None:
    values = [row["ratings"][field] for row in rows if row["ratings"].get(field) is not None]
    return mean(values) if values else None


def fish_test_disposition(identity: str, test_key: str, rows: list[dict[str, Any]]) -> str:
    best_delivery = max((row["ratings"]["delivery"] or 0) for row in rows)
    retained = any(row["retain"] for row in rows)
    notes = " ".join(row["notes"].casefold() for row in rows)
    worst_artifacts = max((row["ratings"]["artifacts"] or 0) for row in rows)
    if identity == "narrator":
        if test_key == "narrator_calm_authority" and best_delivery >= 4:
            return "restricted_pass"
        return "rejected_delivery"
    if identity == "benny":
        if test_key == "benny_relief" and best_delivery == 5 and retained:
            return "pass"
        if test_key == "benny_urgent_warning" and "only in one word" in notes:
            return "restricted_localized_delivery"
        return "restricted_pass" if best_delivery >= 4 and retained else "review_required"
    if identity == "doctor":
        if test_key == "doctor_calm_authority" and best_delivery == 5:
            return "pass_with_whisper_quirk"
        if test_key == "doctor_dry_sarcasm" and best_delivery >= 4 and retained:
            return "pass_selected_repeat"
        if test_key == "doctor_urgent_warning" and best_delivery >= 4 and retained:
            return "restricted_pass"
        if test_key == "doctor_restrained_grief" or "monotone" in notes:
            return "rejected_delivery"
    if worst_artifacts >= 5:
        return "rejected_artifacts"
    return "review_required"


def fish_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["identity"], row["test_key"])].append(row)
    tests: list[dict[str, Any]] = []
    for (identity, test_key), members in sorted(grouped.items()):
        winner = max(
            members,
            key=lambda row: (
                row["retain"],
                row["ratings"]["delivery"] or 0,
                row["ratings"]["identity"] or 0,
                row["ratings"]["naturalness"] or 0,
                -(row["ratings"]["artifacts"] or 5),
            ),
        )
        tests.append(
            {
                "identity": identity,
                "test_key": test_key,
                "style": members[0]["style"],
                "prompt_mode": members[0]["prompt_mode"],
                "sample_count": len(members),
                "delivery_mean": mean_present(members, "delivery"),
                "identity_mean": mean_present(members, "identity"),
                "naturalness_mean": mean_present(members, "naturalness"),
                "text_mean": mean_present(members, "text"),
                "artifact_mean": mean_present(members, "artifacts"),
                "retained_count": sum(1 for row in members if row["retain"]),
                "selected_blind_id": winner["blind_id"],
                "selected_repeat": winner["repeat"],
                "disposition": fish_test_disposition(identity, test_key, members),
                "notes": [row["notes"] for row in members if row["notes"]],
            }
        )
    by_identity = []
    for identity in sorted({row["identity"] for row in rows}):
        members = [row for row in rows if row["identity"] == identity]
        by_identity.append(
            {
                "identity": identity,
                "sample_count": len(members),
                "identity_mean": mean_present(members, "identity"),
                "delivery_mean": mean_present(members, "delivery"),
                "naturalness_mean": mean_present(members, "naturalness"),
                "text_mean": mean_present(members, "text"),
                "artifact_mean": mean_present(members, "artifacts"),
                "retained_count": sum(1 for row in members if row["retain"]),
            }
        )
    return {"by_test": tests, "by_identity": by_identity}


def selected_logical_ids(selection: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    for identity in selection["identity_references"].values():
        result.add(str(identity["clean_actor_primary"]))
        alternate = identity.get("clean_actor_alternate")
        if alternate:
            result.add(str(alternate))
        result.update(map(str, identity["canonical_candidates"]))
    for rows in selection["performance_bank"].values():
        result.update(map(str, rows))
    return result


def markdown_report(report: Mapping[str, Any]) -> str:
    source = report["source_review"]
    fish = report["fish_review"]
    lines = [
        "# Chris Cwej / Roz Forrester Post-Review Summary",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Source review",
        "",
        f"- Non-T'Nia candidates scored: {source['scored_non_tnia_count']}",
        f"- Retained: {source['retained_count']}",
        f"- Not retained: {source['not_retained_count']}",
        f"- T'Nia candidates removed before scoring: {source['tnia_removed_count']}",
        "- T'Nia downstream use: disabled",
        "",
        "### Cleanup demand among retained clips",
        "",
    ]
    for flag, count in source["retained_cleanup_flag_counts"].items():
        lines.append(f"- {flag}: {count}")
    lines.extend(["", "## Fish preferred-router retest", ""])
    for row in fish["by_test"]:
        lines.append(
            f"- {row['test_key']}: {row['disposition']} "
            f"(delivery mean {row['delivery_mean']:.2f}; selected repeat {row['selected_repeat']})"
        )
    lines.extend(
        [
            "",
            "## Downstream decision",
            "",
            "- Roz remains Yasmin Bannerman only.",
            "- Repair sentence boundaries and compare conservative cleanup variants.",
            "- The next blind model round compares clean-actor and cleaned in-character identity tiers.",
            "- Fish, VoxCPM2 controllable cloning, and IndexTTS2 remain the bounded candidates.",
            "",
        ]
    )
    return "\n".join(lines)


def build_report(
    selection: Mapping[str, Any],
    source_rows: list[dict[str, Any]],
    removed_style_rows: list[dict[str, Any]],
    fish_rows: list[dict[str, Any]],
    source_export: Path,
    fish_export: Path,
) -> dict[str, Any]:
    retained = [row for row in source_rows if row["retain"]]
    selected_ids = selected_logical_ids(selection)
    missing_selection = sorted(selected_ids - {row["logical_id"] for row in source_rows})
    if missing_selection:
        raise PostReviewError(f"Selection contract references unknown candidates: {missing_selection}")
    selected_rows = [row for row in source_rows if row["logical_id"] in selected_ids]
    not_retained_selected = sorted(row["logical_id"] for row in selected_rows if not row["retain"])
    if not_retained_selected:
        raise PostReviewError(
            "Selection contract includes candidates the reviewer did not retain: "
            + ", ".join(not_retained_selected)
        )
    flag_counts = Counter(flag for row in retained for flag in row["cleanup_flags"])
    source_summary = {
        "scored_non_tnia_count": len(source_rows),
        "retained_count": len(retained),
        "not_retained_count": len(source_rows) - len(retained),
        "tnia_removed_count": len(removed_style_rows),
        "tnia_downstream_allowed": False,
        "retained_cleanup_flag_counts": dict(sorted(flag_counts.items())),
        "selected_candidate_count": len(selected_rows),
        "selected_candidates": selected_rows,
        "all_scored_candidates": source_rows,
        "removed_tnia_candidates": removed_style_rows,
    }
    return {
        "schema_version": 1,
        "selection_id": selection["selection_id"],
        "generated_at": utc_now(),
        "reviewer": selection["reviewer"],
        "input_exports": {
            "source": {"path": str(source_export), "sha256": sha256_file(source_export)},
            "fish": {"path": str(fish_export), "sha256": sha256_file(fish_export)},
        },
        "source_review": source_summary,
        "fish_review": fish_summary(fish_rows),
        "selection_contract": selection,
        "production_mutation_allowed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, default=SELECTION_PATH)
    parser.add_argument("--source-export", type=Path, default=DEFAULT_SOURCE_EXPORT)
    parser.add_argument("--fish-export", type=Path, default=DEFAULT_FISH_EXPORT)
    parser.add_argument("--source-answer-key", type=Path, default=DEFAULT_SOURCE_KEY)
    parser.add_argument("--fish-answer-key", type=Path, default=DEFAULT_FISH_KEY)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()

    selection = read_json(args.selection.expanduser().resolve())
    if selection.get("schema_version") != 1:
        raise PostReviewError("Unsupported post-review selection schema.")
    if selection.get("tnia_miller", {}).get("downstream_allowed") is not False:
        raise PostReviewError("T'Nia downstream use must remain disabled.")

    source_export = args.source_export.expanduser().resolve()
    fish_export = args.fish_export.expanduser().resolve()
    source_rows, removed_style_rows = join_source_review(
        source_export,
        args.source_answer_key.expanduser().resolve(),
        selection,
    )
    fish_rows = join_fish_review(
        fish_export,
        args.fish_answer_key.expanduser().resolve(),
        selection,
    )
    report = build_report(
        selection,
        source_rows,
        removed_style_rows,
        fish_rows,
        source_export,
        fish_export,
    )

    output_root = args.output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    json_path = output_root / "human-review-summary.json"
    markdown_path = output_root / "human-review-summary.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_path.write_text(markdown_report(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "source_scored": report["source_review"]["scored_non_tnia_count"],
                "source_retained": report["source_review"]["retained_count"],
                "tnia_removed": report["source_review"]["tnia_removed_count"],
                "selected_candidates": report["source_review"]["selected_candidate_count"],
                "fish_samples": sum(row["sample_count"] for row in report["fish_review"]["by_identity"]),
                "json": str(json_path),
                "markdown": str(markdown_path),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
