#!/usr/bin/env python3
"""Prepare and finalize the corrected, focused Alexandria Round 1 review.

The v1 evidence is preserved untouched. This tool creates a separate evidence
root containing only the identity lanes and delivery styles that materially
inform Alexandria's expressive-clone decision. Valid v1 audio is hard-linked
when possible. Fish S2 Pro and MOSS v1.5 are deliberately left pending so they
must be regenerated with the corrected sample-rate/channel handling.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path(
    "/Users/tristan/.devspace/worktrees/alexandria-audiobook.git-78fc5814/"
    ".omo/evidence/b17-t05-multimodel-round1"
)
DEFAULT_DESTINATION = Path(
    "/Users/tristan/.devspace/worktrees/alexandria-audiobook.git-78fc5814/"
    ".omo/evidence/b17-t05-multimodel-round1-v2-usable"
)
DEFAULT_RESULTS = Path("/Users/tristan/Downloads/alexandria_round1_cumulative_all(3).json")
ROUND_ID = "alexandria_multimodel_expressive_clone_round1_v2_usable"
INVALID_V1_MODELS = {"fish_s2_pro", "moss_tts_local_v15"}
IDENTITIES = ("narrator", "benny", "doctor", "ryan_neutral", "ryan_acted")
STYLES = (
    "neutral",
    "happy",
    "tender",
    "grief",
    "panic",
    "angry",
    "menacing",
    "sarcastic",
    "whisper",
    "laughing",
)
REQUIRED_COMPLETE_FIELDS = (
    "identity_1_to_5",
    "delivery_1_to_5",
    "naturalness_1_to_5",
    "artifact_severity_1_to_5",
    "spoken_text_matches_expected",
    "requested_mode_is_clear",
    "approve_for_comparison",
)
FINAL_ASSET_FILES = (
    "index.html",
    "styles.css",
    "app.js",
    "review-core.js",
    "review-io.js",
    "review-content.js",
    "review-navigation.js",
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def link_or_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        target.unlink()
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def copy_tree_linked(source: Path, target: Path) -> None:
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        destination = target / relative
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            link_or_copy(path, destination)


def filtered_groups(groups: dict[str, Any]) -> dict[str, Any]:
    selected = set(STYLES)
    result: dict[str, Any] = {}
    for key, group in groups.items():
        style_keys = [style for style in group.get("styles", []) if style in selected]
        if not style_keys:
            continue
        result[key] = {**group, "styles": style_keys}
    return result


def prepare(source: Path, destination: Path, force: bool) -> dict[str, Any]:
    if destination.exists():
        if not force:
            raise FileExistsError(f"Destination already exists: {destination}")
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    source_manifest = read_json(source / "round1_internal_manifest.json")
    selected_styles = set(STYLES)
    selected_identities = set(IDENTITIES)
    selected_samples = [
        dict(sample)
        for sample in source_manifest["sample_specs"]
        if sample["identity_key"] in selected_identities
        and sample["style"] in selected_styles
    ]
    selected_blocked = [
        dict(cell)
        for cell in source_manifest["blocked_cells"]
        if cell["identity_key"] in selected_identities and cell["style"] in selected_styles
    ]

    copy_tree_linked(source / "references", destination / "references")

    carried_audio = 0
    pending_regeneration = 0
    for sample in selected_samples:
        if sample["model_key"] in INVALID_V1_MODELS:
            sample["status"] = "pending_generation"
            pending_regeneration += 1
            continue
        for key in ("output_file", "result_file"):
            source_file = source / sample[key]
            if not source_file.is_file():
                raise FileNotFoundError(source_file)
            link_or_copy(source_file, destination / sample[key])
        sample["status"] = "ready"
        carried_audio += 1

    model_count = len(source_manifest["model_contract"]["models"])
    expected_cells = model_count * len(IDENTITIES) * len(STYLES)
    if len(selected_samples) + len(selected_blocked) != expected_cells:
        raise RuntimeError(
            "Focused coverage accounting mismatch: "
            f"{len(selected_samples)} + {len(selected_blocked)} != {expected_cells}"
        )

    styles_by_key = {item["key"]: item for item in source_manifest["styles"]}
    manifest = {
        **source_manifest,
        "round_id": ROUND_ID,
        "purpose": "corrected_focused_multimodel_expressive_clone_blind_round1",
        "supersedes_round_id": source_manifest["round_id"],
        "groups": filtered_groups(source_manifest["groups"]),
        "styles": [styles_by_key[key] for key in STYLES],
        "identity_lanes": {
            key: source_manifest["identity_lanes"][key] for key in IDENTITIES
        },
        "native_lanes": {},
        "selected_identity_lanes": list(IDENTITIES),
        "selected_styles": list(STYLES),
        "native_voice_matrix_removed": True,
        "expected_coverage_cell_count": expected_cells,
        "sample_spec_count": len(selected_samples),
        "blocked_cell_count": len(selected_blocked),
        "sample_specs": selected_samples,
        "blocked_cells": selected_blocked,
        "invalidated_v1_model_runs": {
            "fish_s2_pro": "Reference audio was incorrectly supplied at 24 kHz to a 44.1 kHz codec.",
            "moss_tts_local_v15": "Stereo output was flattened into an interleaved mono sequence.",
        },
        "review_contract": {
            **source_manifest.get("review_contract", {}),
            "focused_style_matrix": True,
            "native_voice_matrix": False,
            "technical_canary_required_before_bulk_generation": True,
        },
    }
    write_json(destination / "round1_internal_manifest.json", manifest)
    write_json(
        destination / "v2-preparation.json",
        {
            "schema_version": 1,
            "round_id": ROUND_ID,
            "source_evidence": str(source),
            "selected_identity_lanes": list(IDENTITIES),
            "selected_styles": list(STYLES),
            "sample_spec_count": len(selected_samples),
            "blocked_cell_count": len(selected_blocked),
            "carried_forward_audio_count": carried_audio,
            "pending_regeneration_count": pending_regeneration,
            "pending_models": sorted(INVALID_V1_MODELS),
        },
    )
    return {
        "destination": str(destination),
        "sample_spec_count": len(selected_samples),
        "blocked_cell_count": len(selected_blocked),
        "carried_forward_audio_count": carried_audio,
        "pending_regeneration_count": pending_regeneration,
    }


def parse_data_js(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8").strip()
    prefix = "window.ALEXANDRIA_ROUND1_DATA = "
    if not text.startswith(prefix) or not text.endswith(";"):
        raise ValueError(f"Unexpected data.js format: {path}")
    return json.loads(text[len(prefix) : -1])


def write_data_js(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        "window.ALEXANDRIA_ROUND1_DATA = "
        + json.dumps(payload, ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )


def finalize(
    source: Path,
    destination: Path,
    cumulative_results: Path,
) -> dict[str, Any]:
    review_root = destination / "review"
    if not (review_root / "data.js").is_file():
        raise FileNotFoundError(
            "Package the v2 evidence first with package_multimodel_round1_review.py"
        )

    source_assets = source / "review-round1-complete-final"
    for filename in FINAL_ASSET_FILES:
        source_file = source_assets / filename
        if not source_file.is_file():
            raise FileNotFoundError(source_file)
        shutil.copy2(source_file, review_root / filename)

    app_path = review_root / "app.js"
    app_text = app_path.read_text(encoding="utf-8")
    default_filter = 'identityFilter: "all"'
    if default_filter not in app_text:
        raise RuntimeError("Could not locate the review identity-filter default.")
    app_path.write_text(
        app_text.replace(default_filter, 'identityFilter: "narrator"', 1),
        encoding="utf-8",
    )

    data = parse_data_js(review_root / "data.js")
    data["title"] = "Alexandria expressive-clone blind review — Corrected Round 1"
    data["test_revision"] = "focused-v2"
    data["native_voice_matrix_removed"] = True
    data["carried_forward_results_file"] = "alexandria_round1_v2_existing_results.json"
    write_data_js(review_root / "data.js", data)

    answer_by_blind_id: dict[str, dict[str, Any]] = {}
    for answer_file in (review_root / "answer-keys").glob("*.json"):
        for row in json.loads(answer_file.read_text(encoding="utf-8")):
            answer_by_blind_id[row["sample_id"]] = row

    source_results = read_json(cumulative_results)
    selected_ids = {sample["sample_id"] for sample in data["samples"]}
    rows: list[dict[str, Any]] = []
    for row in source_results.get("rows", []):
        answer = answer_by_blind_id.get(row.get("sample_id"))
        if row.get("sample_id") not in selected_ids or answer is None:
            continue
        if answer["model_key"] in INVALID_V1_MODELS:
            continue
        rows.append(row)

    complete = sum(
        1 for row in rows if all(field in row for field in REQUIRED_COMPLETE_FIELDS)
    )
    flags = sum(1 for row in rows if row.get("flag_for_follow_up") is True)
    exported_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    seed = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "export_scope": "cumulative",
        "export_key": "all",
        "exported_at": exported_at,
        "revision": int(source_results.get("revision") or 0) + 1,
        "summary": {
            "ready_sample_count": len(data["samples"]),
            "complete_sample_count": complete,
            "incomplete_sample_count": len(data["samples"]) - complete,
            "follow_up_flag_count": flags,
            "carried_forward_row_count": len(rows),
            "discarded_invalid_fish_moss_ratings": True,
        },
        "rows": rows,
    }
    seed_path = review_root / "alexandria_round1_v2_existing_results.json"
    write_json(seed_path, seed)

    manifest_path = review_root / "manifest.json"
    manifest = read_json(manifest_path)
    manifest.update(
        {
            "title": data["title"],
            "focused_round1_v2": True,
            "native_voice_matrix_removed": True,
            "native_voices_pooled_across_models": False,
            "carried_forward_results_file": seed_path.name,
            "carried_forward_result_count": len(rows),
            "invalid_v1_models_regenerated": sorted(INVALID_V1_MODELS),
        }
    )
    write_json(manifest_path, manifest)

    readme = """ALEXANDRIA CORRECTED ROUND 1\n\n1. Open index.html.\n2. Import alexandria_round1_v2_existing_results.json once.\n3. Continue reviewing incomplete samples.\n\nThe main matrix includes Narrator, Benny, Doctor, Ryan neutral, and Ryan acted.\nNative model voices were removed. Fish and MOSS audio was regenerated after correcting\nthe Fish 44.1 kHz reference path and MOSS stereo downmix.\n"""
    (review_root / "START_HERE.txt").write_text(readme, encoding="utf-8")

    return {
        "review": str(review_root / "index.html"),
        "sample_count": len(data["samples"]),
        "carried_forward_row_count": len(rows),
        "carried_forward_complete_count": complete,
        "seed_results": str(seed_path),
    }


def validate(destination: Path) -> dict[str, Any]:
    manifest = read_json(destination / "round1_internal_manifest.json")
    review_manifest = read_json(destination / "review" / "manifest.json")
    samples = manifest["sample_specs"]
    missing = []
    invalid_receipts = []
    for sample in samples:
        audio = destination / sample["output_file"]
        receipt = destination / sample["result_file"]
        if not audio.is_file() or not receipt.is_file():
            missing.append(sample["sample_id"])
            continue
        payload = read_json(receipt)
        if payload.get("blind_id") != sample["blind_id"]:
            invalid_receipts.append(sample["sample_id"])
    native = [sample["sample_id"] for sample in samples if sample["identity_key"].startswith("native_")]
    if len(samples) != 264:
        raise RuntimeError(f"Expected 264 samples, found {len(samples)}")
    if native:
        raise RuntimeError(f"Native identities remain in v2: {native[:3]}")
    if missing or invalid_receipts:
        raise RuntimeError(
            f"v2 validation failed: missing={len(missing)} invalid_receipts={len(invalid_receipts)}"
        )
    if review_manifest.get("generated_sample_count") != len(samples):
        raise RuntimeError("Review package generation count does not match v2 manifest")
    return {
        "round_id": manifest["round_id"],
        "sample_count": len(samples),
        "blocked_count": len(manifest["blocked_cells"]),
        "identity_lanes": list(IDENTITIES),
        "styles": list(STYLES),
        "missing_count": 0,
        "native_sample_count": 0,
        "review": str(destination / "review" / "index.html"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "finalize", "validate"))
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--destination", default=str(DEFAULT_DESTINATION))
    parser.add_argument("--cumulative-results", default=str(DEFAULT_RESULTS))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    destination = Path(args.destination).expanduser().resolve()
    if args.mode == "prepare":
        result = prepare(source, destination, args.force)
    elif args.mode == "finalize":
        result = finalize(
            source,
            destination,
            Path(args.cumulative_results).expanduser().resolve(),
        )
    else:
        result = validate(destination)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
