#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, Iterable

ROUND_ID = "alexandria_narrator_benny_seedvc_validation_v1"
ASSET_ROOT = Path(__file__).with_name("three_voice_openvoice_assets")
TARGET_ORDER = ("narrator", "benny")
MODE_ORDER = ("calm", "pleading", "angry")


class PackageError(RuntimeError):
    pass


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def doctor_diagnosis(doctor_root: Path) -> dict[str, Any]:
    analysis = load_json(doctor_root / "analysis.json")
    samples = analysis.get("samples") or []
    best = {}
    for mode in MODE_ORDER:
        candidates = [row for row in samples if row.get("mode") == mode]
        if not candidates:
            continue
        winner = max(
            candidates,
            key=lambda row: (
                float(row.get("minimum_third_identity_cosine") or 0.0),
                float(row.get("whole_identity_cosine") or 0.0),
            ),
        )
        best[mode] = {
            "target_anchor": winner.get("target_anchor"),
            "whole_identity_cosine": winner.get("whole_identity_cosine"),
            "minimum_third_identity_cosine": winner.get("minimum_third_identity_cosine"),
            "third_identity_cosines": winner.get("third_identity_cosines"),
            "pitch_shape_similarity_to_donor": winner.get("pitch_shape_similarity_to_donor"),
            "text_similarity": winner.get("text_similarity"),
            "technical_pass": winner.get("technical_pass"),
        }
    return {
        "schema_version": 1,
        "status": "identity_anchor_insufficient",
        "production_route_approved": False,
        "tested_register_anchor_count": len({row.get("target_anchor") for row in samples}),
        "tested_sample_count": len(samples),
        "technical_pass_count": sum(bool(row.get("technical_pass")) for row in samples),
        "best_by_mode": best,
        "diagnosis": (
            "Seed-VC preserves the donor text, duration, and pitch shape, but the available "
            "Doctor recordings do not produce stable target identity across all thirds of a line. "
            "A cleaner or larger authentic Doctor identity corpus is required before expressive "
            "conversion can be promoted."
        ),
        "next_requirement": (
            "Acquire or prepare at least 60-120 seconds of clean, single-speaker Doctor audio "
            "covering calm, vulnerable, and forceful registers, then build a dedicated identity "
            "adapter or multi-reference target encoder."
        ),
    }


def package(args: argparse.Namespace) -> dict[str, Any]:
    seedvc_root = Path(args.seedvc_root).expanduser().resolve()
    doctor_root = Path(args.doctor_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    for path in (seedvc_root / "analysis.json", doctor_root / "analysis.json"):
        if not path.is_file():
            raise PackageError(f"Required analysis is missing: {path}")

    analysis = load_json(seedvc_root / "analysis.json")
    rows = [
        row
        for row in analysis.get("samples") or []
        if row.get("target_key") in TARGET_ORDER and bool(row.get("technical_pass"))
    ]
    rows.sort(key=lambda row: (TARGET_ORDER.index(row["target_key"]), MODE_ORDER.index(row["mode"])))
    expected = {(target, mode) for target in TARGET_ORDER for mode in MODE_ORDER}
    actual = {(row["target_key"], row["mode"]) for row in rows}
    if actual != expected or len(rows) != 6:
        raise PackageError(f"Expected six passing Narrator/Benny routes, found {sorted(actual)}")

    review_root = output_root / "review"
    if review_root.exists():
        shutil.rmtree(review_root)
    (review_root / "audio").mkdir(parents=True)
    (review_root / "targets").mkdir(parents=True)
    (review_root / "donors").mkdir(parents=True)
    copied_targets: set[str] = set()
    copied_donors: set[str] = set()
    public_rows = []
    answer_rows = []
    for ordinal, row in enumerate(rows, 1):
        target_name = f"{row['target_key']}.wav"
        donor_name = f"{row['mode']}.wav"
        audio_name = f"{row['sample_id']}.wav"
        if target_name not in copied_targets:
            shutil.copy2(row["target_reference"], review_root / "targets" / target_name)
            copied_targets.add(target_name)
        if donor_name not in copied_donors:
            shutil.copy2(row["donor_audio"], review_root / "donors" / donor_name)
            copied_donors.add(donor_name)
        shutil.copy2(row["audio_path"], review_root / "audio" / audio_name)
        public_rows.append(
            {
                "sample_id": row["sample_id"],
                "ordinal": ordinal,
                "target_key": row["target_key"],
                "target_label": row["target_label"],
                "mode": row["mode"],
                "mode_label": row["mode_label"],
                "expected_text": row["expected_text"],
                "target_audio": f"targets/{target_name}",
                "donor_audio": f"donors/{donor_name}",
                "converted_audio": f"audio/{audio_name}",
                "technical_pass": True,
                "automatic_transcript": row["automatic_transcript"],
            }
        )
        answer_rows.append(row)

    for asset in ("index.html", "styles.css", "app.js"):
        shutil.copy2(ASSET_ROOT / asset, review_root / asset)
    index_path = review_root / "index.html"
    index_path.write_text(
        index_path.read_text(encoding="utf-8")
        .replace("Nine bounded tests.", "Six bounded tests.")
        .replace("Narrator, Benny, or Doctor", "Narrator or Benny")
        .replace("OpenVoice changes the speaker", "Seed-VC changes the speaker")
        .replace("0 / 9 complete", "0 / 6 complete"),
        encoding="utf-8",
    )
    app_path = review_root / "app.js"
    app_path.write_text(
        app_path.read_text(encoding="utf-8")
        .replace(
            "alexandria:three-voice-openvoice:",
            "alexandria:narrator-benny-seedvc:",
        )
        .replace(
            "alexandria_three_voice_openvoice_conversion_review.json",
            "alexandria_narrator_benny_seedvc_conversion_review.json",
        ),
        encoding="utf-8",
    )
    public = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "title": "Narrator and Benny Seed-VC performance conversion validation",
        "candidate_count": len(public_rows),
        "target_order": TARGET_ORDER,
        "mode_order": MODE_ORDER,
        "rows": public_rows,
        "production_promotion_allowed": False,
    }
    (review_root / "data.js").write_text(
        "window.THREE_VOICE_OPENVOICE_DATA = " + json.dumps(public, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    write_json(
        review_root / "manifest.json",
        {
            "schema_version": 1,
            "round_id": ROUND_ID,
            "candidate_count": len(public_rows),
            "target_count": len(TARGET_ORDER),
            "mode_count": len(MODE_ORDER),
            "answer_key_outside_review_root": True,
            "production_promotion_allowed": False,
        },
    )
    write_json(output_root / "answer-key.json", answer_rows)
    diagnosis = doctor_diagnosis(doctor_root)
    write_json(output_root / "doctor-diagnosis.json", diagnosis)
    (output_root / "START_HERE.txt").write_text(
        "Narrator and Benny Seed-VC validation\n"
        "======================================\n\n"
        f"cd \"{review_root}\"\n"
        "python3 -m http.server 8778 --bind 127.0.0.1\n\n"
        "Then open http://127.0.0.1:8778/\n\n"
        "The Doctor is intentionally excluded: none of twelve register-matched "
        "Doctor conversions passed stable identity checks.\n",
        encoding="utf-8",
    )
    return {
        "review": str(review_root / "index.html"),
        "candidate_count": len(public_rows),
        "doctor_status": diagnosis["status"],
    }


def validate(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    review_root = output_root / "review"
    prefix = "window.THREE_VOICE_OPENVOICE_DATA = "
    text = (review_root / "data.js").read_text(encoding="utf-8").strip()
    data = json.loads(text[len(prefix) :].rstrip(";"))
    answers = {row["sample_id"]: row for row in load_json(output_root / "answer-key.json")}
    missing = []
    bad_hash = []
    for row in data["rows"]:
        audio = review_root / row["converted_audio"]
        target = review_root / row["target_audio"]
        donor = review_root / row["donor_audio"]
        if not audio.is_file() or not target.is_file() or not donor.is_file():
            missing.append(row["sample_id"])
            continue
        if sha256_file(audio) != answers[row["sample_id"]]["audio_sha256"]:
            bad_hash.append(row["sample_id"])
    diagnosis = load_json(output_root / "doctor-diagnosis.json")
    if missing or bad_hash or diagnosis.get("production_route_approved") is not False:
        raise PackageError(
            f"Validation failed: missing={missing}, bad_hash={bad_hash}, doctor={diagnosis}"
        )
    return {
        "round_id": ROUND_ID,
        "candidate_count": len(data["rows"]),
        "missing_count": len(missing),
        "bad_hash_count": len(bad_hash),
        "doctor_status": diagnosis["status"],
        "review": str(review_root / "index.html"),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Package the passing Narrator/Benny Seed-VC proof routes.")
    sub = result.add_subparsers(dest="command", required=True)
    package_parser = sub.add_parser("package")
    package_parser.add_argument("--seedvc-root", required=True)
    package_parser.add_argument("--doctor-root", required=True)
    package_parser.add_argument("--output-root", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--output-root", required=True)
    return result


def main(argv: Iterable[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    try:
        value = package(args) if args.command == "package" else validate(args)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
