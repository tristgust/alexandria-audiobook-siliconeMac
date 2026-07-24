#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from prepare_audiodrama_function_round import convert_mp3
from prepare_narrator_indextts2_reference_bank import sha256_file
from prepare_same_speaker_performance_validation import SameSpeakerError

ROUND_ID = "alexandria_audiodrama_function_final_review_v1"
ASSET_ROOT = Path(__file__).with_name("lazy_voice_followup_assets")
RANGE_SERVER = Path(__file__).with_name("range_http_server.py")
TARGET_ORDER = ("narrator", "benny", "doctor")
FUNCTION_ORDER = (
    "conversation",
    "warmth_vulnerability",
    "comic_amused",
    "urgent_afraid",
    "confrontation_authority",
)
EXPECTED_ROUTES = tuple(
    (target, function_name)
    for target in TARGET_ORDER
    for function_name in FUNCTION_ORDER
)
EXPERIMENTAL_DOCTOR_MODES = (
    "playful_eccentricity",
    "quiet_compassion",
    "urgent_command",
    "grave_warning",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_rows(path: Path) -> list[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise SameSpeakerError(f"Expected a row list: {path}")
    return rows


def best_experimental_rows(main_root: Path, salvage_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for root in (main_root, salvage_root):
        analysis = json.loads((root / "analysis.json").read_text(encoding="utf-8"))
        rows.extend(
            row
            for row in analysis["samples"]
            if row["target_key"] == "doctor" and row["mode"] in EXPERIMENTAL_DOCTOR_MODES
        )
    winners = []
    for mode in EXPERIMENTAL_DOCTOR_MODES:
        candidates = [row for row in rows if row["mode"] == mode]
        if not candidates:
            raise SameSpeakerError(f"No experimental Doctor candidates found for {mode}")
        winner = max(candidates, key=lambda row: float(row["selection_score"]))
        winners.append(
            {
                **winner,
                "technical_pass": False,
                "experimental_tradeoff": True,
                "experimental_reason": (
                    "Automatic gate failed because identity and delivery could not both clear threshold. "
                    "Human review may still accept this exact compromise."
                ),
            }
        )
    return winners


def route_key(row: dict[str, Any]) -> tuple[str, str]:
    return (row["target_key"], row["function"])


def package(args: argparse.Namespace) -> dict[str, Any]:
    main_root = Path(args.main_root).expanduser().resolve()
    salvage_root = Path(args.salvage_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()

    qualified = read_rows(main_root / "answer-key.json") + read_rows(salvage_root / "answer-key.json")
    experimental = best_experimental_rows(main_root, salvage_root)
    by_route: dict[tuple[str, str], dict[str, Any]] = {}
    for row in qualified + experimental:
        key = route_key(row)
        existing = by_route.get(key)
        if existing is None or (not existing.get("technical_pass") and row.get("technical_pass")):
            by_route[key] = row
    missing = [key for key in EXPECTED_ROUTES if key not in by_route]
    extra = sorted(set(by_route) - set(EXPECTED_ROUTES))
    if missing or extra:
        raise SameSpeakerError(f"Audiodrama route contract mismatch: missing={missing}, extra={extra}")
    winners = [by_route[key] for key in EXPECTED_ROUTES]

    review_root = output_root / "review"
    if review_root.exists():
        shutil.rmtree(review_root)
    for folder in ("generated", "references", "targets"):
        (review_root / "audio" / folder).mkdir(parents=True, exist_ok=True)
    for asset in ("index.html", "app.js", "styles.css"):
        shutil.copy2(ASSET_ROOT / asset, review_root / asset)
    shutil.copy2(RANGE_SERVER, review_root / "serve_review.py")

    index = (review_root / "index.html").read_text(encoding="utf-8")
    index = (
        index.replace("<title>Targeted same-speaker follow-up</title>", "<title>Audiodrama Function Coverage</title>")
        .replace("<h1>Targeted same-speaker follow-up</h1>", "<h1>Audiodrama Function Coverage</h1>")
        .replace(
            "Only unresolved or repaired routes are included. One card and three audio files load at a time.",
            "Fifteen reusable dramatic functions: five each for Narrator, Benny, and Doctor. One card and three audio files load at a time.",
        )
        .replace("0 / 6 complete", "0 / 15 complete")
        .replace("aria-label=\"Follow-up routes\"", "aria-label=\"Audiodrama function routes\"")
        .replace(
            "<strong>Rating rule:</strong> every score uses <strong>5 as best</strong>. For a route to pass, character identity and the requested delivery must both work. Use the direct audio link when the built-in player misbehaves.",
            "<strong>Rating rule:</strong> every score uses <strong>5 as best</strong>. For a route to pass, character identity and the requested delivery must both work. Doctor cards marked Experimental did not clear the automatic identity-and-delivery gate; judge those compromises carefully.",
        )
    )
    (review_root / "index.html").write_text(index, encoding="utf-8")
    app = (review_root / "app.js").read_text(encoding="utf-8")
    app = (
        app.replace("alexandria:lazy-voice-followup:", "alexandria:audiodrama-function-final:")
        .replace(
            "alexandria_targeted_voice_followup_review.json",
            "alexandria_audiodrama_function_review.json",
        )
        .replace("Follow-up ${currentIndex + 1}", "Function ${currentIndex + 1}")
    )
    (review_root / "app.js").write_text(app, encoding="utf-8")

    copied_targets: set[str] = set()
    public_rows = []
    answer_rows = []
    coverage_rows = []
    for ordinal, row in enumerate(winners, 1):
        target_name = f"{row['target_key']}.mp3"
        reference_name = f"{row['target_key']}-{row['mode']}.mp3"
        generated_name = f"{row['sample_id']}.mp3"
        target_source = Path(row["canonical_identity_audio"])
        reference_source = Path(row["reference_audio"])
        generated_source = Path(row["audio_path"])
        for source in (target_source, reference_source, generated_source):
            if not source.is_file():
                raise SameSpeakerError(f"Audiodrama source is missing: {source}")
        if target_name not in copied_targets:
            convert_mp3(target_source, review_root / "audio" / "targets" / target_name)
            copied_targets.add(target_name)
        convert_mp3(reference_source, review_root / "audio" / "references" / reference_name)
        convert_mp3(generated_source, review_root / "audio" / "generated" / generated_name)
        experimental_flag = bool(row.get("experimental_tradeoff"))
        purpose_label = (
            "Experimental tradeoff · automatic gate failed"
            if experimental_flag
            else "Qualified audiodrama function"
        )
        public_rows.append(
            {
                "sample_id": row["sample_id"],
                "ordinal": ordinal,
                "target_key": row["target_key"],
                "target_label": row["target_label"],
                "mode": row["mode"],
                "mode_label": row["mode_label"] + (" · Experimental" if experimental_flag else ""),
                "short_label": row["function"].replace("_", " ").title(),
                "purpose": "experimental_tradeoff" if experimental_flag else "audiodrama_function",
                "purpose_label": purpose_label,
                "expected_text": row["target_text"],
                "target_audio": f"audio/targets/{target_name}",
                "reference_audio": f"audio/references/{reference_name}",
                "generated_audio": f"audio/generated/{generated_name}",
                "technical_pass": bool(row.get("technical_pass")),
                "automatic_transcript": row["automatic_transcript"],
            }
        )
        answer_rows.append(row)
        coverage_rows.append(
            {
                "target_key": row["target_key"],
                "function": row["function"],
                "mode": row["mode"],
                "sample_id": row["sample_id"],
                "status": "qualified_candidate" if row.get("technical_pass") else "experimental_tradeoff",
                "production_promotion_allowed": False,
            }
        )

    public = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "title": "Audiodrama Function Coverage",
        "created_at": now_iso(),
        "candidate_count": len(public_rows),
        "target_order": list(TARGET_ORDER),
        "rows": public_rows,
        "production_promotion_allowed": False,
    }
    (review_root / "data.js").write_text(
        "window.LAZY_VOICE_FOLLOWUP_DATA = " + json.dumps(public, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "candidate_count": len(public_rows),
        "qualified_count": sum(bool(row.get("technical_pass")) for row in winners),
        "experimental_count": sum(bool(row.get("experimental_tradeoff")) for row in winners),
        "lazy_audio_loading": True,
        "range_requests_required": True,
        "answer_key_outside_review_root": True,
        "model_names_exposed": False,
        "production_promotion_allowed": False,
    }
    (review_root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (output_root / "answer-key.json").write_text(json.dumps(answer_rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    coverage = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "created_at": now_iso(),
        "function_order": list(FUNCTION_ORDER),
        "routes": coverage_rows,
        "existing_approved_extremes": {
            "narrator": ["neutral", "panic", "smug_menace", "wounded_pleading"],
            "benny": ["neutral", "determined_resolve", "emergency_distress", "excited_discovery"],
            "doctor": ["cold_existential_dismissal", "dark_warning_conditional"],
        },
        "known_rejections": {
            "narrator": ["wounded_rage", "exuberant_joy"],
            "benny": [],
            "doctor": ["protective_authority"],
        },
        "production_promotion_allowed": False,
    }
    (output_root / "coverage-ledger.json").write_text(json.dumps(coverage, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output_root / "START_HERE.txt").write_text(
        "Audiodrama Function Coverage\n"
        "============================\n\n"
        f"cd \"{review_root}\"\n"
        "python3 serve_review.py --bind 127.0.0.1 --port 8784\n\n"
        "Then open http://127.0.0.1:8784/\n",
        encoding="utf-8",
    )
    return {
        "review": str(review_root / "index.html"),
        "candidate_count": len(public_rows),
        "qualified_count": manifest["qualified_count"],
        "experimental_count": manifest["experimental_count"],
    }


def validate(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    review_root = output_root / "review"
    prefix = "window.LAZY_VOICE_FOLLOWUP_DATA = "
    data_text = (review_root / "data.js").read_text(encoding="utf-8").strip()
    public = json.loads(data_text[len(prefix) :].rstrip(";"))
    answers = {
        row["sample_id"]: row
        for row in json.loads((output_root / "answer-key.json").read_text(encoding="utf-8"))
    }
    missing = []
    bad_audio = []
    for row in public["rows"]:
        for key in ("target_audio", "reference_audio", "generated_audio"):
            path = review_root / row[key]
            if not path.is_file():
                missing.append(f"{row['sample_id']}:{key}")
                continue
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,sample_rate,channels", "-of", "json", str(path)],
                capture_output=True,
                text=True,
                check=True,
            )
            stream = (json.loads(probe.stdout).get("streams") or [{}])[0]
            if stream.get("codec_name") != "mp3" or stream.get("channels") != 1:
                bad_audio.append(f"{row['sample_id']}:{key}")
        if row["sample_id"] not in answers:
            missing.append(f"{row['sample_id']}:answer")
    route_order = [(row["target_key"], row["short_label"].casefold().replace(" ", "_")) for row in public["rows"]]
    expected = list(EXPECTED_ROUTES)
    if route_order != expected:
        raise SameSpeakerError(f"Public audiodrama route order changed: {route_order}")
    visible = (review_root / "index.html").read_text(encoding="utf-8")
    if re.search(r"IndexTTS2|VoxCPM|Fish S2|Qwen", visible, re.IGNORECASE):
        raise SameSpeakerError("Model name leaked into audiodrama review")
    if missing or bad_audio:
        raise SameSpeakerError(f"Audiodrama validation failed: missing={missing}, bad_audio={bad_audio}")
    return {
        "round_id": ROUND_ID,
        "candidate_count": len(public["rows"]),
        "missing_count": len(missing),
        "bad_audio_count": len(bad_audio),
        "review": str(review_root / "index.html"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Package final audiodrama dramatic-function review.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("package")
    p.add_argument("--main-root", required=True)
    p.add_argument("--salvage-root", required=True)
    p.add_argument("--output-root", required=True)
    v = sub.add_parser("validate")
    v.add_argument("--output-root", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        result = package(args) if args.command == "package" else validate(args)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
