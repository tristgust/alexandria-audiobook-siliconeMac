#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf

BENCHMARKS_ROOT = Path(__file__).resolve().parent
if str(BENCHMARKS_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS_ROOT))

import prepare_three_voice_combined_bank_benchmark as generation_base
import prepare_three_voice_final_bank_benchmark as final_base

ROUND_ID = "alexandria_three_voice_paired_seed_reliability_v1"
EXPORT_FILENAME = "alexandria_three_voice_paired_seed_reliability_review.json"
REVIEW_PORT = 8796
EXPECTED_REFERENCE_COUNT = 31

ROUTE_GROUP_IDS = (
    "narrator_anger_control",
    "benny_fatalistic_dread",
    "doctor_playful_identity",
)
RUNS: tuple[dict[str, Any], ...] = (
    {"run_id": "run_1", "run_label": "Run 1 of 3", "generation_seed": 104729, "repeat_of": None},
    {"run_id": "run_2", "run_label": "Run 2 of 3", "generation_seed": 130363, "repeat_of": None},
    {"run_id": "run_3", "run_label": "Run 3 of 3", "generation_seed": 104729, "repeat_of": "run_1"},
)


class PairedSeedReliabilityError(final_base.FinalBankBenchmarkError):
    pass


def route_templates() -> dict[str, dict[str, Any]]:
    indexed = {str(row["route_id"]): dict(row) for row in final_base.ROUTES}
    missing = set(ROUTE_GROUP_IDS) - set(indexed)
    if missing:
        raise PairedSeedReliabilityError(f"Missing final-bank route templates: {sorted(missing)}")
    return {key: indexed[key] for key in ROUTE_GROUP_IDS}


def expanded_routes() -> tuple[dict[str, Any], ...]:
    templates = route_templates()
    rows: list[dict[str, Any]] = []
    # Interleave targets by run so the hidden repeat is separated from run 1.
    for run in RUNS:
        for group_id in ROUTE_GROUP_IDS:
            template = templates[group_id]
            rows.append(
                {
                    **template,
                    "route_id": f"{group_id}__{run['run_id']}",
                    "route_group_id": group_id,
                    "run_id": run["run_id"],
                    "run_label": run["run_label"],
                    "generation_seed": int(run["generation_seed"]),
                    "repeat_of_run_id": run["repeat_of"],
                    "function_label": f"{template['function_label']} · {run['run_label']}",
                }
            )
    return tuple(rows)


ROUTES = expanded_routes()


def configure_upstream() -> None:
    final_base.ROUND_ID = ROUND_ID
    final_base.EXPORT_FILENAME = EXPORT_FILENAME
    final_base.REVIEW_PORT = REVIEW_PORT
    final_base.ROUTES = ROUTES
    final_base.configure_base()


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    configure_upstream()
    result = final_base.prepare(args)
    output_root = Path(args.output_root).expanduser().resolve()
    matrix_path = output_root / "matrix.json"
    matrix = generation_base.load_json(matrix_path)
    route_index = {row["route_id"]: row for row in matrix["routes"]}
    for route in matrix["routes"]:
        configured = next(row for row in ROUTES if row["route_id"] == route["route_id"])
        for key in (
            "route_group_id",
            "run_id",
            "run_label",
            "generation_seed",
            "repeat_of_run_id",
        ):
            route[key] = configured.get(key)
    for sample in matrix["samples"]:
        route = route_index[sample["route_id"]]
        seed = int(route["generation_seed"])
        # generation_base.generate uses the first eight hex characters as the seed.
        # Keep the suffix unique while forcing both prompt roles in a pair to share a seed.
        suffix = generation_base.fingerprint(
            {
                "round_id": ROUND_ID,
                "route_id": sample["route_id"],
                "prompt_role": sample["prompt_role"],
                "prompt_audio_sha256": sample["prompt_audio_sha256"],
            },
            8,
        )
        sample["sample_id"] = f"{seed:08x}{suffix}"
        sample["generation_seed"] = seed
        sample["route_group_id"] = route["route_group_id"]
        sample["run_id"] = route["run_id"]
        sample["repeat_of_run_id"] = route.get("repeat_of_run_id")
    matrix.update(
        {
            "route_group_count": len(ROUTE_GROUP_IDS),
            "runs_per_route_group": len(RUNS),
            "unique_seed_count_per_route_group": 2,
            "hidden_fixed_seed_repeat_count": len(ROUTE_GROUP_IDS),
            "paired_generation_seed": True,
            "same_seed_within_prompt_pair": True,
            "seed_source": "explicit_matrix_generation_seed",
        }
    )
    matrix["comparison_contract"].update(
        {
            "same_generation_seed_within_pair": True,
            "fixed_seed_repeat_included": True,
            "prompt_role_excluded_from_seed": True,
        }
    )
    matrix_path.write_text(json.dumps(matrix, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        **result,
        "route_group_count": len(ROUTE_GROUP_IDS),
        "runs_per_route_group": len(RUNS),
        "paired_generation_seed": True,
    }


def _pcm(path: Path) -> tuple[np.ndarray, int]:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = audio.mean(axis=1, dtype=np.float32)
    return mono, int(sample_rate)


def _compare_audio(left_path: Path, right_path: Path) -> dict[str, Any]:
    left, left_rate = _pcm(left_path)
    right, right_rate = _pcm(right_path)
    minimum = min(left.size, right.size)
    if minimum:
        delta = left[:minimum] - right[:minimum]
        max_abs = float(np.max(np.abs(delta)))
        rms_delta = float(np.sqrt(np.mean(np.square(delta), dtype=np.float64)))
        denominator = float(np.linalg.norm(left[:minimum]) * np.linalg.norm(right[:minimum]))
        cosine = float(np.dot(left[:minimum], right[:minimum]) / denominator) if denominator > 0 else 0.0
    else:
        max_abs = math.inf
        rms_delta = math.inf
        cosine = 0.0
    return {
        "left_audio_path": str(left_path),
        "right_audio_path": str(right_path),
        "left_audio_sha256": generation_base.sha256_file(left_path),
        "right_audio_sha256": generation_base.sha256_file(right_path),
        "exact_file_hash_match": generation_base.sha256_file(left_path) == generation_base.sha256_file(right_path),
        "exact_pcm_match": left_rate == right_rate and left.shape == right.shape and bool(np.array_equal(left, right)),
        "left_sample_rate": left_rate,
        "right_sample_rate": right_rate,
        "left_sample_count": int(left.size),
        "right_sample_count": int(right.size),
        "duration_difference_seconds": round(abs(left.size / left_rate - right.size / right_rate), 6),
        "max_abs_pcm_difference": None if not math.isfinite(max_abs) else round(max_abs, 9),
        "rms_pcm_difference": None if not math.isfinite(rms_delta) else round(rms_delta, 9),
        "overlap_pcm_cosine": round(cosine, 9),
    }


def analyze_repeats(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    matrix = generation_base.load_json(output_root / "matrix.json")
    summary = generation_base.load_json(output_root / "generation-summary.json")
    samples = {
        (row["route_group_id"], row["run_id"], row["prompt_role"]): row
        for row in summary["samples"]
    }
    comparisons: list[dict[str, Any]] = []
    for group_id in ROUTE_GROUP_IDS:
        for prompt_role in ("combined_bank", "legacy_reference"):
            first = samples[(group_id, "run_1", prompt_role)]
            repeat = samples[(group_id, "run_3", prompt_role)]
            comparisons.append(
                {
                    "route_group_id": group_id,
                    "prompt_role": prompt_role,
                    "generation_seed": first["generation_seed"],
                    **_compare_audio(Path(first["audio_path"]), Path(repeat["audio_path"])),
                }
            )
    exact_hash_count = sum(bool(row["exact_file_hash_match"]) for row in comparisons)
    exact_pcm_count = sum(bool(row["exact_pcm_match"]) for row in comparisons)
    payload = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "created_at": generation_base.now_iso(),
        "comparison_count": len(comparisons),
        "exact_file_hash_match_count": exact_hash_count,
        "exact_pcm_match_count": exact_pcm_count,
        "fixed_seed_runtime_reproducible": exact_pcm_count == len(comparisons),
        "conclusion": (
            "All fixed-seed repeats were PCM-identical; observed generation differences are seed-dependent."
            if exact_pcm_count == len(comparisons)
            else "At least one fixed-seed repeat differed; runtime nondeterminism contributes in addition to seed variance."
        ),
        "comparisons": comparisons,
        "source_matrix": {
            "path": str(output_root / "matrix.json"),
            "sha256": generation_base.sha256_file(output_root / "matrix.json"),
            "same_seed_within_prompt_pair": matrix.get("same_seed_within_prompt_pair"),
        },
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }
    path = output_root / "repeatability-analysis.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "comparison_count": len(comparisons),
        "exact_pcm_match_count": exact_pcm_count,
        "fixed_seed_runtime_reproducible": payload["fixed_seed_runtime_reproducible"],
        "analysis": str(path),
    }


def customize_review(output_root: Path) -> None:
    review_root = output_root / "review"
    index_path = review_root / "index.html"
    index = index_path.read_text(encoding="utf-8")
    replacements = {
        "Final 31-Reference Bank — Generation Benchmark": "Paired-Seed Prompt Reliability",
        "Final 31-Reference Bank Benchmark": "Paired-Seed Prompt Reliability",
        "Final Bank Benchmark": "Paired-Seed Reliability",
        "For each line, choose the generated candidate that best matches the authentic performance target while preserving the character’s identity, accent, pacing, and clean natural speech.": (
            "Each target line appears in three blinded runs. Within every A/B pair, the identity, text, alpha, runtime, and generation seed are identical; only the performance prompt changes."
        ),
    }
    for old, new in replacements.items():
        index = index.replace(old, new)
    index_path.write_text(index, encoding="utf-8")
    app_path = review_root / "app.js"
    app = app_path.read_text(encoding="utf-8")
    app = app.replace(final_base.EXPORT_FILENAME, EXPORT_FILENAME)
    app_path.write_text(app, encoding="utf-8")
    (output_root / "START_HERE.txt").write_text(
        f'cd "{review_root}"\npython3 serve_review.py --bind 127.0.0.1 --port {REVIEW_PORT}\n\n'
        f'Then open http://127.0.0.1:{REVIEW_PORT}/\n',
        encoding="utf-8",
    )


def package(args: argparse.Namespace) -> dict[str, Any]:
    configure_upstream()
    result = final_base.package(args)
    output_root = Path(args.output_root).expanduser().resolve()
    customize_review(output_root)
    manifest_path = output_root / "review" / "manifest.json"
    manifest = generation_base.load_json(manifest_path)
    manifest.update(
        {
            "export_filename": EXPORT_FILENAME,
            "paired_generation_seed": True,
            "same_seed_within_prompt_pair": True,
            "route_group_count": len(ROUTE_GROUP_IDS),
            "runs_per_route_group": len(RUNS),
            "hidden_fixed_seed_repeat_count": len(ROUTE_GROUP_IDS),
            "repeat_relationship_exposed": False,
        }
    )
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {
        **result,
        "route_group_count": len(ROUTE_GROUP_IDS),
        "runs_per_route_group": len(RUNS),
    }


def validate(args: argparse.Namespace) -> dict[str, Any]:
    configure_upstream()
    result = final_base.validate(args)
    output_root = Path(args.output_root).expanduser().resolve()
    matrix = generation_base.load_json(output_root / "matrix.json")
    manifest = generation_base.load_json(output_root / "review" / "manifest.json")
    public_prefix = "window.THREE_VOICE_BANK_BENCHMARK_DATA = "
    public_text = (output_root / "review" / "data.js").read_text(encoding="utf-8").strip()
    public = json.loads(public_text[len(public_prefix):].rstrip(";"))
    failures: list[str] = []
    if len(matrix.get("routes") or []) != 9 or len(matrix.get("samples") or []) != 18:
        failures.append("matrix_size")
    by_route: dict[str, list[dict[str, Any]]] = {}
    for sample in matrix.get("samples") or []:
        by_route.setdefault(sample["route_id"], []).append(sample)
    for route_id, samples in by_route.items():
        if len(samples) != 2:
            failures.append(f"pair_count:{route_id}")
            continue
        if len({int(row["generation_seed"]) for row in samples}) != 1:
            failures.append(f"unpaired_seed:{route_id}")
        if len({str(row["sample_id"])[:8] for row in samples}) != 1:
            failures.append(f"seed_prefix:{route_id}")
    route_rows = {row["route_id"]: row for row in matrix.get("routes") or []}
    for group_id in ROUTE_GROUP_IDS:
        run_1 = route_rows[f"{group_id}__run_1"]
        run_3 = route_rows[f"{group_id}__run_3"]
        if run_1["generation_seed"] != run_3["generation_seed"]:
            failures.append(f"repeat_seed:{group_id}")
        stable_keys = (
            "target_text",
            "alpha",
            "identity_audio_sha256",
            "bank_reference_audio_sha256",
            "legacy_reference_audio_sha256",
        )
        if any(run_1[key] != run_3[key] for key in stable_keys):
            failures.append(f"repeat_inputs:{group_id}")
    if manifest.get("candidate_count") != 9 or manifest.get("paired_generation_seed") is not True:
        failures.append("manifest")
    if manifest.get("repeat_relationship_exposed") is not False:
        failures.append("repeat_exposure")
    public_blob = json.dumps(public, sort_keys=True)
    if "repeat_of" in public_blob or "generation_seed" in public_blob:
        failures.append("public_seed_leak")
    repeat_path = output_root / "repeatability-analysis.json"
    if repeat_path.is_file():
        repeat = generation_base.load_json(repeat_path)
        if repeat.get("comparison_count") != 6:
            failures.append("repeat_analysis_count")
    if failures:
        raise PairedSeedReliabilityError(f"Paired-seed reliability validation failed: {failures}")
    return {
        **result,
        "route_group_count": len(ROUTE_GROUP_IDS),
        "runs_per_route_group": len(RUNS),
        "sample_count": 18,
        "paired_generation_seed": True,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a paired-seed prompt reliability study with hidden fixed-seed repeats."
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
    repeat_parser = sub.add_parser("analyze-repeats")
    repeat_parser.add_argument("--output-root", required=True)
    package_parser = sub.add_parser("package")
    package_parser.add_argument("--output-root", required=True)
    package_parser.add_argument("--whisper-model", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--output-root", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    configure_upstream()
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "prepare":
            result = prepare(args)
        elif args.command == "generate":
            result = generation_base.generate(args)
        elif args.command == "analyze-repeats":
            result = analyze_repeats(args)
        elif args.command == "package":
            result = package(args)
        else:
            result = validate(args)
    except (
        PairedSeedReliabilityError,
        final_base.FinalBankBenchmarkError,
        generation_base.CombinedBankBenchmarkError,
        generation_base.ReferenceBankError,
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
