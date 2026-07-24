#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from prepare_narrator_indextts2_reference_bank import sha256_file
from prepare_same_speaker_performance_validation import SameSpeakerError, patch_assets

ROUND_ID = "alexandria_same_speaker_final_validation_v1"
EXPECTED_ROUTES = (
    ("narrator", "panic"),
    ("narrator", "smug_menace"),
    ("benny", "emergency_distress"),
    ("benny", "excited_discovery"),
    ("doctor", "protective_authority"),
    ("doctor", "dark_warning"),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_rows(root: Path) -> list[dict[str, Any]]:
    path = root / "answer-key.json"
    if not path.is_file():
        raise SameSpeakerError(f"Answer key is missing: {path}")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise SameSpeakerError(f"Answer key must contain a list: {path}")
    return rows


def package(args: argparse.Namespace) -> dict[str, Any]:
    base_root = Path(args.base_root).expanduser().resolve()
    doctor_root = Path(args.doctor_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    rows = load_rows(base_root) + load_rows(doctor_root)
    by_route = {(row["target_key"], row["mode"]): row for row in rows}
    missing_routes = [route for route in EXPECTED_ROUTES if route not in by_route]
    extra_routes = sorted(set(by_route) - set(EXPECTED_ROUTES))
    if missing_routes or extra_routes:
        raise SameSpeakerError(
            f"Final route contract mismatch: missing={missing_routes}, extra={extra_routes}"
        )
    winners = [by_route[route] for route in EXPECTED_ROUTES]

    review_root = output_root / "review"
    if review_root.exists():
        shutil.rmtree(review_root)
    (review_root / "audio").mkdir(parents=True)
    (review_root / "targets").mkdir(parents=True)
    (review_root / "donors").mkdir(parents=True)
    patch_assets(review_root)

    public_rows = []
    copied_targets: set[str] = set()
    copied_refs: set[str] = set()
    for ordinal, row in enumerate(winners, 1):
        target_name = f"{row['target_key']}.wav"
        reference_name = f"{row['target_key']}-{row['mode']}.wav"
        generated_name = f"{row['sample_id']}.wav"
        target = Path(row["canonical_identity_audio"])
        reference = Path(row["reference_audio"])
        generated = Path(row["audio_path"])
        for source in (target, reference, generated):
            if not source.is_file():
                raise SameSpeakerError(f"Final review source is missing: {source}")
        if target_name not in copied_targets:
            shutil.copy2(target, review_root / "targets" / target_name)
            copied_targets.add(target_name)
        if reference_name not in copied_refs:
            shutil.copy2(reference, review_root / "donors" / reference_name)
            copied_refs.add(reference_name)
        shutil.copy2(generated, review_root / "audio" / generated_name)
        public_rows.append(
            {
                "sample_id": row["sample_id"],
                "ordinal": ordinal,
                "target_key": row["target_key"],
                "target_label": row["target_label"],
                "mode": row["mode"],
                "mode_label": row["mode_label"],
                "expected_text": row["target_text"],
                "target_audio": f"targets/{target_name}",
                "donor_audio": f"donors/{reference_name}",
                "converted_audio": f"audio/{generated_name}",
                "technical_pass": True,
                "automatic_transcript": row["automatic_transcript"],
            }
        )

    public = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "title": "Same-speaker performance validation",
        "created_at": now_iso(),
        "candidate_count": len(public_rows),
        "target_order": ["narrator", "benny", "doctor"],
        "rows": public_rows,
        "production_promotion_allowed": False,
    }
    (review_root / "data.js").write_text(
        "window.THREE_VOICE_OPENVOICE_DATA = " + json.dumps(public, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    (review_root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "round_id": ROUND_ID,
                "candidate_count": len(public_rows),
                "target_count": 3,
                "route_count_per_target": 2,
                "answer_key_outside_review_root": True,
                "model_names_exposed": False,
                "production_promotion_allowed": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (output_root / "answer-key.json").write_text(
        json.dumps(winners, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_root / "analysis.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "round_id": ROUND_ID,
                "candidate_count": len(winners),
                "routes": [list(route) for route in EXPECTED_ROUTES],
                "source_roots": {"base": str(base_root), "doctor": str(doctor_root)},
                "all_candidates_passed_automatic_gate": True,
                "manual_listening_required": True,
                "production_promotion_allowed": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (output_root / "START_HERE.txt").write_text(
        "Same-speaker performance validation\n"
        "===================================\n\n"
        f"cd \"{review_root}\"\n"
        "python3 -m http.server 8780 --bind 127.0.0.1\n\n"
        "Then open http://127.0.0.1:8780/\n",
        encoding="utf-8",
    )
    return {"review": str(review_root / "index.html"), "candidate_count": len(winners)}


def validate(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    review_root = output_root / "review"
    prefix = "window.THREE_VOICE_OPENVOICE_DATA = "
    text = (review_root / "data.js").read_text(encoding="utf-8").strip()
    public = json.loads(text[len(prefix) :].rstrip(";"))
    answers = {
        row["sample_id"]: row
        for row in json.loads((output_root / "answer-key.json").read_text(encoding="utf-8"))
    }
    missing = []
    bad_hash = []
    for row in public["rows"]:
        audio = review_root / row["converted_audio"]
        target = review_root / row["target_audio"]
        reference = review_root / row["donor_audio"]
        if not audio.is_file() or not target.is_file() or not reference.is_file():
            missing.append(row["sample_id"])
            continue
        if sha256_file(audio) != answers[row["sample_id"]]["audio_sha256"]:
            bad_hash.append(row["sample_id"])
    routes = [(row["target_key"], row["mode"]) for row in public["rows"]]
    if tuple(routes) != EXPECTED_ROUTES:
        raise SameSpeakerError(f"Public route order changed: {routes}")
    visible = (review_root / "index.html").read_text(encoding="utf-8")
    if re.search(r"OpenVoice|Seed-VC|IndexTTS2", visible, re.IGNORECASE):
        raise SameSpeakerError("Model name leaked into final public review")
    if missing or bad_hash:
        raise SameSpeakerError(f"Final review validation failed: missing={missing}, bad_hash={bad_hash}")
    return {
        "round_id": ROUND_ID,
        "candidate_count": len(public["rows"]),
        "missing_count": len(missing),
        "bad_hash_count": len(bad_hash),
        "review": str(review_root / "index.html"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Package the final bounded same-speaker review.")
    sub = parser.add_subparsers(dest="command", required=True)
    package_parser = sub.add_parser("package")
    package_parser.add_argument("--base-root", required=True)
    package_parser.add_argument("--doctor-root", required=True)
    package_parser.add_argument("--output-root", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--output-root", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        result = package(args) if args.command == "package" else validate(args)
    except SameSpeakerError as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
