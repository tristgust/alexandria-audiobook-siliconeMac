#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

BENCHMARKS_ROOT = Path(__file__).resolve().parent
if str(BENCHMARKS_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS_ROOT))

import prepare_three_voice_combined_bank_benchmark as base

ROUND_ID = "alexandria_three_voice_final_bank_generation_benchmark_v1"
BANK_ROUND_ID = "alexandria_three_voice_validated_reference_bank_v3"
EXPECTED_REFERENCE_COUNT = 31
EXPORT_FILENAME = "alexandria_three_voice_final_bank_benchmark_review.json"
REVIEW_PORT = 8795

ROUTES: tuple[dict[str, Any], ...] = (
    {
        "route_id": "narrator_anger_control",
        "target": "narrator",
        "target_label": "Narrator",
        "function": "explosive_anger",
        "function_label": "Explosive anger · positive control",
        "bank_clip_id": "narrator_ud_explosive_indignation",
        "legacy_kind": "narrator_style",
        "legacy_key": "wounded_rage",
        "target_text": "After everything I did for you, this is how you chose to repay me.",
        "alpha": 0.75,
    },
    {
        "route_id": "narrator_joy_tuned",
        "target": "narrator",
        "target_label": "Narrator",
        "function": "joy",
        "function_label": "Ecstatic joy · tuned strength",
        "bank_clip_id": "narrator_ud_ecstatic_bucket_affection",
        "legacy_kind": "narrator_style",
        "legacy_key": "exuberant_joy",
        "target_text": "Yes! That's it! You did it, Stanley, you actually did it!",
        "alpha": 0.50,
    },
    {
        "route_id": "benny_fatalistic_dread",
        "target": "benny",
        "target_label": "Benny",
        "function": "credible_fear",
        "function_label": "Fatalistic dread",
        "bank_clip_id": "benny_hesitation_fatalistic_dread",
        "legacy_kind": "audiodrama_reference",
        "legacy_key": "benny-urgent_fear.wav",
        "legacy_reference_text": "I'm trapped in a pyramid. Yes, a pyramid. My guide's dead.",
        "target_text": "It wasn't chance. They knew exactly where we'd be, and they were waiting.",
        "alpha": 0.40,
    },
    {
        "route_id": "benny_sardonic_concern",
        "target": "benny",
        "target_label": "Benny",
        "function": "sardonic_conversation",
        "function_label": "Sardonic concern",
        "bank_clip_id": "benny_criminal_sardonic_concern",
        "legacy_kind": "audiodrama_reference",
        "legacy_key": "benny-sardonic_conversation.wav",
        "legacy_reference_text": "Until then, we're acting normal. And normal for me is digging up stuff.",
        "target_text": "Right, because ancient alien machinery always comes with a clearly labelled off switch.",
        "alpha": 0.30,
    },
    {
        "route_id": "doctor_playful_identity",
        "target": "doctor",
        "target_label": "Seventh Doctor",
        "function": "ordinary_identity",
        "function_label": "Playful eccentricity",
        "bank_clip_id": "doctor_acf_playful_introduction",
        "legacy_kind": "audiodrama_reference",
        "legacy_key": "doctor-playful_eccentricity.wav",
        "legacy_reference_text": "Ace, have you no sense of occasion?",
        "target_text": "Oh, wonderful. A locked door, a missing key, and precisely no time to think.",
        "alpha": 0.25,
    },
    {
        "route_id": "doctor_dismissive_contempt",
        "target": "doctor",
        "target_label": "Seventh Doctor",
        "function": "dismissive_contempt",
        "function_label": "Dismissive contempt",
        "bank_clip_id": "doctor_acf_dismissive_contempt",
        "legacy_kind": "audiodrama_reference",
        "legacy_key": "doctor-dry_wit.wav",
        "legacy_reference_text": "Oh, well, that sorts that out. I've got to give myself more warning.",
        "target_text": "Oh, splendid. Another petty little tyrant with a machine he barely understands.",
        "alpha": 0.30,
    },
)

OPEN_GAPS = {
    "narrator": {"grief_or_regret"},
    "benny": {"grief", "explosive_anger"},
    "doctor": {"compassion", "urgency", "weariness"},
}


class FinalBankBenchmarkError(base.CombinedBankBenchmarkError):
    pass


def configure_base() -> None:
    base.ROUND_ID = ROUND_ID
    base.BANK_ROUND_ID = BANK_ROUND_ID
    base.ROUTES = ROUTES
    base.OPEN_GAPS = OPEN_GAPS
    base.TARGET_ORDER = ("narrator", "benny", "doctor")


def validated_bank_index(bank: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if bank.get("round_id") != BANK_ROUND_ID:
        raise FinalBankBenchmarkError(
            f"Unexpected final bank round_id: {bank.get('round_id')!r}; expected {BANK_ROUND_ID!r}."
        )
    if int(bank.get("reference_count") or 0) != EXPECTED_REFERENCE_COUNT:
        raise FinalBankBenchmarkError(
            f"Final bank must contain {EXPECTED_REFERENCE_COUNT} references, got {bank.get('reference_count')!r}."
        )
    rows = bank.get("references")
    if not isinstance(rows, list) or len(rows) != EXPECTED_REFERENCE_COUNT:
        raise FinalBankBenchmarkError("Final bank references are missing or incomplete.")
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        clip_id = str(row.get("clip_id") or "")
        if not clip_id or clip_id in indexed:
            raise FinalBankBenchmarkError(f"Invalid or duplicate final-bank clip_id: {clip_id!r}")
        audio = Path(str(row.get("audio_path") or ""))
        if not audio.is_file() or base.sha256_file(audio) != row.get("audio_sha256"):
            raise FinalBankBenchmarkError(f"Final-bank audio validation failed: {clip_id}")
        if not str(row.get("reference_status") or "").startswith("approved"):
            raise FinalBankBenchmarkError(f"Final-bank reference is not approved: {clip_id}")
        if row.get("production_promotion_allowed") is not False:
            raise FinalBankBenchmarkError(f"Final-bank reference permits production promotion: {clip_id}")
        indexed[clip_id] = row
    return indexed


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    configure_base()
    bank_path = Path(args.bank).expanduser().resolve()
    bank = base.load_json(bank_path)
    bank_refs = validated_bank_index(bank)
    narrator_matrix_path = Path(args.legacy_narrator_matrix).expanduser().resolve()
    narrator_styles = base.legacy_narrator_styles(base.load_json(narrator_matrix_path))
    legacy_root = Path(args.legacy_audiodrama_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    if output_root.exists() and args.force:
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    identity_sources = {
        "narrator": Path(args.narrator_identity).expanduser().resolve(),
        "benny": Path(args.benny_identity).expanduser().resolve(),
        "doctor": Path(args.doctor_identity).expanduser().resolve(),
    }
    identity_paths: dict[str, Path] = {}
    for target, source in identity_sources.items():
        target_path = output_root / "identity" / f"{target}.wav"
        base.normalize_audio(source, target_path)
        identity_paths[target] = target_path

    routes: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    for route in ROUTES:
        if route["function"] in OPEN_GAPS.get(route["target"], set()):
            raise FinalBankBenchmarkError(f"Route targets an open coverage gap: {route['route_id']}")
        bank_row = bank_refs.get(route["bank_clip_id"])
        if bank_row is None:
            raise FinalBankBenchmarkError(f"Final-bank clip is missing: {route['bank_clip_id']}")
        if bank_row.get("target") != route["target"]:
            raise FinalBankBenchmarkError(f"Target mismatch for {route['bank_clip_id']}")
        bank_source = Path(str(bank_row["audio_path"]))
        bank_reference = output_root / "references" / "validated_bank" / f"{route['route_id']}.wav"
        base.normalize_audio(bank_source, bank_reference)
        bank_text = str(bank_row.get("selected_transcript") or "")
        if not bank_text:
            raise FinalBankBenchmarkError(f"Final-bank transcript is missing: {route['bank_clip_id']}")

        if route["legacy_kind"] == "narrator_style":
            legacy_row = narrator_styles.get(route["legacy_key"])
            if legacy_row is None:
                raise FinalBankBenchmarkError(f"Legacy Narrator style is missing: {route['legacy_key']}")
            legacy_source = Path(str(legacy_row.get("emotion_audio") or "")).expanduser().resolve()
            legacy_text = str(legacy_row.get("emotion_text") or "")
        else:
            legacy_source = (legacy_root / route["legacy_key"]).resolve()
            legacy_text = str(route.get("legacy_reference_text") or "")
        legacy_reference = output_root / "references" / "legacy" / f"{route['route_id']}.wav"
        base.normalize_audio(legacy_source, legacy_reference)

        route_row = {
            **route,
            "identity_audio": str(identity_paths[route["target"]]),
            "identity_audio_sha256": base.sha256_file(identity_paths[route["target"]]),
            "bank_reference_audio": str(bank_reference),
            "bank_reference_audio_sha256": base.sha256_file(bank_reference),
            "bank_reference_text": bank_text,
            "bank_reference_primary_emotion": str(bank_row.get("primary_emotion") or ""),
            "bank_reference_dramatic_function": str(bank_row.get("dramatic_function") or ""),
            "bank_reference_status": str(bank_row.get("reference_status") or ""),
            "legacy_reference_audio": str(legacy_reference),
            "legacy_reference_audio_sha256": base.sha256_file(legacy_reference),
            "legacy_reference_text": legacy_text,
        }
        routes.append(route_row)
        for prompt_role, reference_audio, reference_text in (
            ("combined_bank", bank_reference, bank_text),
            ("legacy_reference", legacy_reference, legacy_text),
        ):
            sample_id = base.fingerprint(
                {
                    "round_id": ROUND_ID,
                    "route_id": route["route_id"],
                    "prompt_role": prompt_role,
                    "identity_sha256": route_row["identity_audio_sha256"],
                    "reference_sha256": base.sha256_file(reference_audio),
                    "target_text": route["target_text"],
                    "alpha": route["alpha"],
                }
            )
            samples.append(
                {
                    "sample_id": sample_id,
                    "route_id": route["route_id"],
                    "target": route["target"],
                    "target_label": route["target_label"],
                    "function": route["function"],
                    "function_label": route["function_label"],
                    "target_text": route["target_text"],
                    "prompt_role": prompt_role,
                    "alpha": float(route["alpha"]),
                    "identity_audio": route_row["identity_audio"],
                    "identity_audio_sha256": route_row["identity_audio_sha256"],
                    "prompt_audio": str(reference_audio),
                    "prompt_audio_sha256": base.sha256_file(reference_audio),
                    "prompt_text": reference_text,
                    "bank_target_audio": route_row["bank_reference_audio"],
                    "bank_target_audio_sha256": route_row["bank_reference_audio_sha256"],
                    "bank_target_text": bank_text,
                }
            )

    matrix = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "created_at": base.now_iso(),
        "combined_bank": {
            "path": str(bank_path),
            "sha256": base.sha256_file(bank_path),
            "round_id": bank["round_id"],
            "reference_count": bank["reference_count"],
            "reference_counts_by_target": bank["reference_counts_by_target"],
        },
        "route_count": len(routes),
        "sample_count": len(samples),
        "target_order": ["narrator", "benny", "doctor"],
        "routes": routes,
        "samples": samples,
        "comparison_contract": {
            "same_identity_prompt": True,
            "same_runtime": True,
            "same_target_text": True,
            "same_emotion_alpha": True,
            "only_performance_prompt_changes": True,
            "open_gap_functions_excluded": True,
            "all_bank_prompts_human_validated": True,
            "final_bank_reference_count": EXPECTED_REFERENCE_COUNT,
        },
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }
    matrix_path = output_root / "matrix.json"
    matrix_path.write_text(json.dumps(matrix, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"route_count": len(routes), "sample_count": len(samples), "matrix": str(matrix_path)}


def customize_review(output_root: Path) -> None:
    review_root = output_root / "review"
    index_path = review_root / "index.html"
    app_path = review_root / "app.js"
    index_text = index_path.read_text(encoding="utf-8")
    replacements = {
        "Combined Reference Bank — Generation Benchmark": "Final 31-Reference Bank — Generation Benchmark",
        "Combined Reference Bank Benchmark": "Final 31-Reference Bank Benchmark",
        "Combined Bank Benchmark": "Final Bank Benchmark",
        "combined reference bank": "final validated reference bank",
        "combined-bank": "validated-bank",
    }
    for old, new in replacements.items():
        index_text = index_text.replace(old, new)
    index_path.write_text(index_text, encoding="utf-8")
    app_text = app_path.read_text(encoding="utf-8").replace(
        "alexandria_three_voice_combined_bank_benchmark_review.json", EXPORT_FILENAME
    )
    app_path.write_text(app_text, encoding="utf-8")
    (output_root / "START_HERE.txt").write_text(
        f'cd "{review_root}"\npython3 serve_review.py --bind 127.0.0.1 --port {REVIEW_PORT}\n\n'
        f'Then open http://127.0.0.1:{REVIEW_PORT}/\n',
        encoding="utf-8",
    )


def package(args: argparse.Namespace) -> dict[str, Any]:
    configure_base()
    result = base.package(args)
    output_root = Path(args.output_root).expanduser().resolve()
    customize_review(output_root)
    manifest_path = output_root / "review" / "manifest.json"
    manifest = base.load_json(manifest_path)
    manifest["final_validated_bank_reference_count"] = EXPECTED_REFERENCE_COUNT
    manifest["export_filename"] = EXPORT_FILENAME
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return result


def validate(args: argparse.Namespace) -> dict[str, Any]:
    configure_base()
    result = base.validate(args)
    output_root = Path(args.output_root).expanduser().resolve()
    matrix = base.load_json(output_root / "matrix.json")
    manifest = base.load_json(output_root / "review" / "manifest.json")
    answer_key = base.load_json(output_root / "answer-key.json")
    failures: list[str] = []
    if matrix.get("combined_bank", {}).get("reference_count") != EXPECTED_REFERENCE_COUNT:
        failures.append("final_bank_reference_count")
    if manifest.get("final_validated_bank_reference_count") != EXPECTED_REFERENCE_COUNT:
        failures.append("manifest_reference_count")
    if manifest.get("export_filename") != EXPORT_FILENAME:
        failures.append("export_filename")
    if len(answer_key) != len(ROUTES):
        failures.append("answer_key_count")
    for route in matrix.get("routes") or []:
        if not str(route.get("bank_reference_status") or "").startswith("approved"):
            failures.append(f"unapproved:{route.get('route_id')}")
        if not route.get("bank_reference_text"):
            failures.append(f"missing_transcript:{route.get('route_id')}")
    if failures:
        raise FinalBankBenchmarkError(f"Final benchmark validation failed: {failures}")
    return {**result, "final_bank_reference_count": EXPECTED_REFERENCE_COUNT}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark the final validated 31-reference three-voice bank against the strongest prior references."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--bank", required=True)
    prepare_parser.add_argument("--output-root", required=True)
    prepare_parser.add_argument("--narrator-identity", required=True)
    prepare_parser.add_argument("--benny-identity", required=True)
    prepare_parser.add_argument("--doctor-identity", required=True)
    prepare_parser.add_argument("--legacy-narrator-matrix", required=True)
    prepare_parser.add_argument("--legacy-audiodrama-root", required=True)
    prepare_parser.add_argument("--force", action="store_true")
    generate_parser = sub.add_parser("generate")
    generate_parser.add_argument("--runtime-root", required=True)
    generate_parser.add_argument("--output-root", required=True)
    generate_parser.add_argument("--force", action="store_true")
    package_parser = sub.add_parser("package")
    package_parser.add_argument("--output-root", required=True)
    package_parser.add_argument("--whisper-model", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--output-root", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    configure_base()
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "prepare":
            result = prepare(args)
        elif args.command == "generate":
            result = base.generate(args)
        elif args.command == "package":
            result = package(args)
        else:
            result = validate(args)
    except (
        FinalBankBenchmarkError,
        base.CombinedBankBenchmarkError,
        base.ReferenceBankError,
        subprocess.CalledProcessError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
