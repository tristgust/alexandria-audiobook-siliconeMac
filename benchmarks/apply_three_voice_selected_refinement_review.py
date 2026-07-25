#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import soundfile as sf

PRIOR_ROUND_ID = "alexandria_three_voice_final_salvage_applied_v1"
REFINEMENT_ROUND_ID = "alexandria_three_voice_selected_refinements_v1"
REVIEW_ROUND_ID = "alexandria_three_voice_selected_refinements_review_v1"
HISTORICAL_ROUND_ID = "alexandria_transcript_guided_source_isolation_v1"
FINAL_ROUND_ID = "alexandria_three_voice_combined_reference_bank_v1"

ALLOWED_DECISIONS = {"use_refined", "use_selected", "reject"}


class FinalBankError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    if not path.is_file():
        raise FinalBankError(f"JSON file is missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FinalBankError(f"Invalid JSON in {path}: {exc}") from exc


def require_round(payload: Any, expected: str, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise FinalBankError(f"{label} must be a JSON object.")
    if payload.get("round_id") != expected:
        raise FinalBankError(
            f"Unexpected {label} round_id: {payload.get('round_id')!r}; expected {expected!r}."
        )
    return payload


def rows_by_id(rows: Any, *, key: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        raise FinalBankError(f"{label} must contain a non-empty list.")
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise FinalBankError(f"Every {label} row must be an object.")
        value = row.get(key)
        if not isinstance(value, str) or not value.strip():
            raise FinalBankError(f"Every {label} row requires {key}.")
        value = value.strip()
        if value in indexed:
            raise FinalBankError(f"Duplicate {label} {key}: {value}")
        indexed[value] = row
    return indexed


def validated_audio(
    path_value: Any,
    expected_hash: Any,
    label: str,
    *,
    require_bank_format: bool = False,
) -> Path:
    path = Path(str(path_value or "")).expanduser().resolve()
    if not path.is_file():
        raise FinalBankError(f"{label} audio is missing: {path}")
    actual = sha256_file(path)
    if actual != expected_hash:
        raise FinalBankError(f"{label} audio hash mismatch: {path}")
    if require_bank_format:
        info = sf.info(path)
        if info.samplerate != 24000 or info.channels != 1 or info.subtype != "PCM_16":
            raise FinalBankError(
                f"{label} audio must be 24 kHz mono PCM_16; got "
                f"{info.samplerate} Hz, {info.channels} channels, {info.subtype}: {path}"
            )
    return path


def copy_reference_audio(source: Path, output_root: Path, target: str, clip_id: str) -> tuple[Path, str]:
    destination = output_root / "audio" / target / f"{clip_id}.wav"
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise FinalBankError(result.stderr.strip() or f"Failed to normalize {source}")
    validated_audio(destination, sha256_file(destination), f"normalized:{clip_id}", require_bank_format=True)
    digest = sha256_file(destination)
    return destination, digest


def slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return normalized or "uncategorized"


def source_atlas_by_id(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise FinalBankError("Source atlas must be a JSON object.")
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise FinalBankError("Source atlas rows are missing.")
    return rows_by_id(rows, key="clip_id", label="source atlas")


def canonical_new_reference(
    row: dict[str, Any],
    *,
    source_path: Path,
    destination: Path,
    destination_hash: str,
    provenance: str,
) -> dict[str, Any]:
    return {
        "clip_id": row["clip_id"],
        "target": row.get("target"),
        "target_label": row.get("target_label"),
        "source_title": row.get("source_title"),
        "source_kind": row.get("source_kind"),
        "youtube_id": row.get("youtube_id"),
        "selected_transcript": row.get("selected_transcript"),
        "primary_emotion": row.get("primary_emotion"),
        "secondary_emotion": row.get("secondary_emotion"),
        "dramatic_function": row.get("dramatic_function"),
        "intensity_1_to_5": row.get("intensity_1_to_5"),
        "coverage_family": row.get("coverage_gap") or slug(str(row.get("primary_emotion") or "")),
        "speaker_certainty": row.get("speaker_certainty"),
        "source_audio_sha256": row.get("source_audio_sha256"),
        "source_reference_audio_sha256": sha256_file(source_path),
        "audio_path": str(destination),
        "audio_sha256": destination_hash,
        "reference_status": "approved_source_reference_final",
        "provenance": provenance,
        "production_promotion_allowed": False,
    }


def canonical_historical_reference(
    row: dict[str, Any],
    *,
    source_path: Path,
    destination: Path,
    destination_hash: str,
) -> dict[str, Any]:
    return {
        "clip_id": row["clip_id"],
        "target": row.get("target"),
        "target_label": row.get("target_label"),
        "source_title": row.get("source_key"),
        "source_kind": "historical_transcript_guided_source",
        "youtube_id": None,
        "selected_transcript": row.get("transcript"),
        "primary_emotion": row.get("primary_emotion"),
        "secondary_emotion": row.get("secondary_emotion"),
        "dramatic_function": row.get("dramatic_function"),
        "intensity_1_to_5": row.get("intensity_1_to_5"),
        "coverage_family": slug(str(row.get("primary_emotion") or "")),
        "speaker_certainty": "historically_reviewed",
        "source_audio_sha256": row.get("source_sha256"),
        "source_reference_audio_sha256": sha256_file(source_path),
        "audio_path": str(destination),
        "audio_sha256": destination_hash,
        "reference_status": "approved_source_reference_historical",
        "provenance": "historical_transcript_guided_bank",
        "production_promotion_allowed": False,
    }


def gap_assessment(references: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {row["clip_id"]: row for row in references}
    requirements: dict[str, dict[str, list[str]]] = {
        "narrator": {
            "joy": ["narrator_ud_ecstatic_bucket_affection", "narrator_ud_manic_victory"],
            "explosive_anger": ["narrator_ud_explosive_indignation"],
            "shame": ["narrator_ud_shame_and_guilt"],
            "loneliness": ["narrator_skip_lonely_deprivation"],
            "abandonment": ["narrator_skip_abandonment_terror"],
            "grief_or_regret": ["narrator_skip_regret_and_grief"],
        },
        "benny": {
            "credible_fear": [
                "benny_hesitation_fatalistic_dread",
                "benny_hesitation_fearful_vigilance",
            ],
            "grief": ["benny_shock_grief"],
            "explosive_anger": ["benny_explosive_frustration"],
            "soft_intimacy": [
                "benny_hesitation_protective_reassurance",
                "benny_diary_buoyant_confidence",
            ],
        },
        "doctor": {
            "compassion": ["doctor_gentle_concern", "doctor_gentle_contrition"],
            "ordinary_identity": [
                "doctor_acf_fond_reminiscence",
                "doctor_acf_playful_introduction",
                "doctor_comic_disorientation",
            ],
            "urgency": ["doctor_acf_emergency_command"],
            "authority": ["doctor_indomitable_determination"],
            "weariness": ["doctor_weary_mortality"],
        },
    }
    result: dict[str, Any] = {}
    for target, target_requirements in requirements.items():
        rows = []
        for function, candidate_ids in target_requirements.items():
            matching = [clip_id for clip_id in candidate_ids if clip_id in by_id]
            rows.append(
                {
                    "function": function,
                    "status": "covered" if matching else "open_gap",
                    "matching_clip_ids": matching,
                    "candidate_clip_ids_considered": candidate_ids,
                }
            )
        result[target] = {
            "covered_count": sum(row["status"] == "covered" for row in rows),
            "requirement_count": len(rows),
            "requirements": rows,
        }
    return result


def build_bank(
    *,
    prior_payload: dict[str, Any],
    refinement_payload: dict[str, Any],
    review_payload: dict[str, Any],
    historical_payload: dict[str, Any],
    source_atlas_payload: dict[str, Any],
    output_root: Path,
    source_paths: dict[str, Path],
) -> dict[str, Any]:
    prior = require_round(prior_payload, PRIOR_ROUND_ID, "prior salvage ledger")
    refinement = require_round(refinement_payload, REFINEMENT_ROUND_ID, "refinement manifest")
    review = require_round(review_payload, REVIEW_ROUND_ID, "refinement review")
    historical = require_round(historical_payload, HISTORICAL_ROUND_ID, "historical bank")
    atlas = source_atlas_by_id(source_atlas_payload)

    prior_references = prior.get("validated_references")
    if not isinstance(prior_references, list):
        raise FinalBankError("Prior salvage ledger has no validated_references list.")
    if len(prior_references) != int(prior.get("validated_reference_count") or 0):
        raise FinalBankError("Prior validated reference count does not match its list.")

    refinement_rows = rows_by_id(refinement.get("rows"), key="clip_id", label="refinement manifest")
    review_rows = rows_by_id(review.get("rows"), key="clip_id", label="refinement review")
    if set(refinement_rows) != set(review_rows):
        raise FinalBankError(
            f"Refinement/review mismatch: manifest={sorted(refinement_rows)}, review={sorted(review_rows)}"
        )

    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    references: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = list(prior.get("rejected_sources") or [])
    final_decisions: list[dict[str, Any]] = []

    for row in prior_references:
        clip_id = str(row.get("clip_id") or "")
        target = str(row.get("target") or "")
        source = validated_audio(row.get("audio_path"), row.get("audio_sha256"), f"prior:{clip_id}")
        destination, digest = copy_reference_audio(source, output_root, target, clip_id)
        references.append(
            canonical_new_reference(
                row,
                source_path=source,
                destination=destination,
                destination_hash=digest,
                provenance=str(row.get("provenance") or row.get("disposition") or "new_source_atlas_review"),
            )
        )

    for clip_id, reviewed in review_rows.items():
        manifest_row = refinement_rows[clip_id]
        decision = reviewed.get("decision")
        if decision not in ALLOWED_DECISIONS:
            raise FinalBankError(f"Invalid final decision for {clip_id}: {decision!r}")
        for key in ("target", "selected_transcript", "primary_emotion", "refinement_type"):
            if reviewed.get(key) != manifest_row.get(key):
                raise FinalBankError(
                    f"Review changed stable field {key} for {clip_id}: "
                    f"{reviewed.get(key)!r} != {manifest_row.get(key)!r}"
                )

        if decision == "reject":
            rejections.append(
                {
                    "clip_id": clip_id,
                    "target": manifest_row.get("target"),
                    "source_title": manifest_row.get("source_title"),
                    "rejection_reason": "rejected_after_selected_refinement",
                    "review_updated_at": reviewed.get("updated_at"),
                }
            )
            final_decisions.append({"clip_id": clip_id, "decision": decision, "included": False})
            continue

        if decision == "use_refined":
            source = validated_audio(
                manifest_row.get("refined_audio_path"),
                manifest_row.get("refined_audio_sha256"),
                f"refined:{clip_id}",
            )
            provenance = f"selected_refinement:{manifest_row.get('refinement_type')}"
        else:
            source = validated_audio(
                manifest_row.get("selected_audio_path"),
                manifest_row.get("selected_audio_sha256"),
                f"selected:{clip_id}",
            )
            provenance = "selected_separation_candidate_without_refinement"

        atlas_row = atlas.get(clip_id)
        if atlas_row is None:
            raise FinalBankError(f"Source atlas metadata is missing for {clip_id}")
        target = str(manifest_row.get("target") or "")
        destination, digest = copy_reference_audio(source, output_root, target, clip_id)
        merged = {
            **atlas_row,
            **manifest_row,
            "audio_path": str(source),
            "audio_sha256": sha256_file(source),
            "selected_transcript": manifest_row.get("selected_transcript") or atlas_row.get("expected_text"),
        }
        references.append(
            canonical_new_reference(
                merged,
                source_path=source,
                destination=destination,
                destination_hash=digest,
                provenance=provenance,
            )
        )
        final_decisions.append({"clip_id": clip_id, "decision": decision, "included": True})

    historical_rows = historical.get("accepted_candidates")
    if not isinstance(historical_rows, list):
        raise FinalBankError("Historical bank accepted_candidates are missing.")
    if len(historical_rows) != int(historical.get("accepted_count") or 0):
        raise FinalBankError("Historical accepted count does not match its list.")
    for row in historical_rows:
        clip_id = str(row.get("clip_id") or "")
        target = str(row.get("target") or "")
        source = validated_audio(row.get("audio_path"), row.get("audio_sha256"), f"historical:{clip_id}")
        destination, digest = copy_reference_audio(source, output_root, target, clip_id)
        references.append(
            canonical_historical_reference(
                row,
                source_path=source,
                destination=destination,
                destination_hash=digest,
            )
        )

    clip_ids = [row["clip_id"] for row in references]
    if len(clip_ids) != len(set(clip_ids)):
        duplicates = sorted({clip_id for clip_id in clip_ids if clip_ids.count(clip_id) > 1})
        raise FinalBankError(f"Final bank contains duplicate clip IDs: {duplicates}")
    references.sort(key=lambda row: (str(row.get("target")), str(row.get("clip_id"))))
    counts = Counter(str(row.get("target")) for row in references)
    coverage = gap_assessment(references)

    payload = {
        "schema_version": 1,
        "round_id": FINAL_ROUND_ID,
        "created_at": now_iso(),
        "reference_count": len(references),
        "reference_counts_by_target": dict(sorted(counts.items())),
        "new_source_reference_count": sum(row["provenance"] != "historical_transcript_guided_bank" for row in references),
        "historical_reference_count": sum(row["provenance"] == "historical_transcript_guided_bank" for row in references),
        "coverage_assessment": coverage,
        "references": references,
        "rejected_sources": rejections,
        "final_refinement_decisions": final_decisions,
        "source_artifacts": {
            name: {"path": str(path), "sha256": sha256_file(path)}
            for name, path in source_paths.items()
        },
        "bank_status": "validated_research_reference_bank",
        "ready_for_targeted_generation_benchmark": True,
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }
    bank_path = output_root / "three-voice-combined-reference-bank.json"
    bank_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    open_gaps = {
        target: [row["function"] for row in detail["requirements"] if row["status"] == "open_gap"]
        for target, detail in coverage.items()
    }
    report = {
        "schema_version": 1,
        "round_id": FINAL_ROUND_ID,
        "reference_count": len(references),
        "reference_counts_by_target": dict(sorted(counts.items())),
        "open_gaps": open_gaps,
        "coverage_assessment": coverage,
        "recommendation": (
            "Stop broad source-cleanup attempts. Use the validated bank for a bounded generation benchmark; "
            "mine only the named open gaps from genuinely clean source material."
        ),
        "production_promotion_allowed": False,
    }
    report_path = output_root / "coverage-report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def validate_bank(payload: Any) -> dict[str, Any]:
    bank = require_round(payload, FINAL_ROUND_ID, "final bank")
    rows = bank.get("references")
    if not isinstance(rows, list) or not rows:
        raise FinalBankError("Final bank has no references.")
    failures: list[str] = []
    seen: set[str] = set()
    counts: Counter[str] = Counter()
    for row in rows:
        clip_id = row.get("clip_id")
        if not isinstance(clip_id, str) or not clip_id:
            failures.append("missing_clip_id")
            continue
        if clip_id in seen:
            failures.append(f"duplicate:{clip_id}")
        seen.add(clip_id)
        counts[str(row.get("target"))] += 1
        try:
            validated_audio(
                row.get("audio_path"),
                row.get("audio_sha256"),
                f"bank:{clip_id}",
                require_bank_format=True,
            )
        except FinalBankError as exc:
            failures.append(str(exc))
        for key in (
            "target",
            "target_label",
            "selected_transcript",
            "primary_emotion",
            "dramatic_function",
            "coverage_family",
            "reference_status",
            "provenance",
        ):
            if row.get(key) in (None, ""):
                failures.append(f"field:{clip_id}:{key}")
        if row.get("production_promotion_allowed") is not False:
            failures.append(f"promotion:{clip_id}")
    if len(rows) != int(bank.get("reference_count") or 0):
        failures.append("reference_count")
    if dict(sorted(counts.items())) != bank.get("reference_counts_by_target"):
        failures.append("reference_counts_by_target")
    if bank.get("automatic_production_assignment") is not False:
        failures.append("automatic_production_assignment")
    if bank.get("production_promotion_allowed") is not False:
        failures.append("production_promotion_allowed")
    if failures:
        raise FinalBankError(f"Final bank validation failed: {failures}")
    open_gaps = {
        target: [row["function"] for row in detail["requirements"] if row["status"] == "open_gap"]
        for target, detail in bank.get("coverage_assessment", {}).items()
    }
    return {
        "reference_count": len(rows),
        "reference_counts_by_target": dict(sorted(counts.items())),
        "new_source_reference_count": bank.get("new_source_reference_count"),
        "historical_reference_count": bank.get("historical_reference_count"),
        "open_gaps": open_gaps,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply the final two refinement decisions and materialize the combined three-voice bank."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--prior-ledger", required=True)
    build.add_argument("--refinement-manifest", required=True)
    build.add_argument("--review", required=True)
    build.add_argument("--historical-bank", required=True)
    build.add_argument("--source-atlas", required=True)
    build.add_argument("--output-root", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--bank", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "build":
            paths = {
                "prior_salvage_ledger": Path(args.prior_ledger).expanduser().resolve(),
                "refinement_manifest": Path(args.refinement_manifest).expanduser().resolve(),
                "final_refinement_review": Path(args.review).expanduser().resolve(),
                "historical_transcript_guided_bank": Path(args.historical_bank).expanduser().resolve(),
                "source_atlas": Path(args.source_atlas).expanduser().resolve(),
            }
            payload = build_bank(
                prior_payload=load_json(paths["prior_salvage_ledger"]),
                refinement_payload=load_json(paths["refinement_manifest"]),
                review_payload=load_json(paths["final_refinement_review"]),
                historical_payload=load_json(paths["historical_transcript_guided_bank"]),
                source_atlas_payload=load_json(paths["source_atlas"]),
                output_root=Path(args.output_root).expanduser().resolve(),
                source_paths=paths,
            )
            result = {
                **validate_bank(payload),
                "output": str(Path(args.output_root).expanduser().resolve() / "three-voice-combined-reference-bank.json"),
            }
        else:
            result = validate_bank(load_json(Path(args.bank).expanduser().resolve()))
    except (FinalBankError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
