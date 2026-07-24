#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, Iterable

ROUND_ID = "alexandria_three_voice_seedvc_final_validation_v1"
ASSET_ROOT = Path(__file__).with_name("three_voice_openvoice_assets")
TARGET_ORDER = ("narrator", "benny", "doctor")
MODE_ORDER = ("calm", "pleading", "angry")
EXPECTED_ROUTES = {
    ("narrator", "calm"),
    ("narrator", "pleading"),
    ("narrator", "angry"),
    ("benny", "calm"),
    ("benny", "pleading"),
    ("benny", "angry"),
    ("doctor", "pleading"),
    ("doctor", "angry"),
}


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


def passing_narrator_benny(root: Path) -> list[dict[str, Any]]:
    analysis = load_json(root / "analysis.json")
    rows = [
        row
        for row in analysis.get("samples") or []
        if row.get("target_key") in {"narrator", "benny"}
        and bool(row.get("technical_pass"))
    ]
    expected = {
        (target, mode)
        for target in ("narrator", "benny")
        for mode in MODE_ORDER
    }
    actual = {(row["target_key"], row["mode"]) for row in rows}
    if actual != expected or len(rows) != 6:
        raise PackageError(
            "Narrator/Benny analysis does not contain exactly six passing routes: "
            f"{sorted(actual)}"
        )
    return rows


def passing_doctor(root: Path) -> list[dict[str, Any]]:
    analysis = load_json(root / "analysis.json")
    by_id = {row["sample_id"]: row for row in analysis.get("samples") or []}
    winners = [by_id[sample_id] for sample_id in analysis.get("winners") or []]
    rows = [
        row
        for row in winners
        if row.get("target_key") == "doctor"
        and row.get("mode") in {"pleading", "angry"}
        and bool(row.get("technical_pass"))
    ]
    actual = {(row["target_key"], row["mode"]) for row in rows}
    expected = {("doctor", "pleading"), ("doctor", "angry")}
    if actual != expected or len(rows) != 2:
        raise PackageError(
            "Doctor analysis does not contain the two approved routes: "
            f"{sorted(actual)}"
        )
    return rows


def calm_diagnosis(calm_root: Path, doctor_root: Path) -> dict[str, Any]:
    calm_analysis = load_json(calm_root / "analysis.json")
    doctor_analysis = load_json(doctor_root / "analysis.json")
    calm_candidates = [
        row
        for row in [
            *(calm_analysis.get("samples") or []),
            *(doctor_analysis.get("samples") or []),
        ]
        if row.get("target_key") == "doctor" and row.get("mode") == "calm"
    ]
    if not calm_candidates:
        raise PackageError("No Doctor calm candidates were recorded.")
    best = max(
        calm_candidates,
        key=lambda row: (
            float(row.get("minimum_third_identity_cosine") or 0.0),
            float(row.get("whole_identity_cosine") or 0.0),
        ),
    )
    return {
        "schema_version": 1,
        "target_key": "doctor",
        "mode": "calm",
        "status": "not_approved",
        "production_route_approved": False,
        "candidate_count": len(calm_candidates),
        "best_target_anchor": best.get("target_anchor"),
        "best_whole_identity_cosine": best.get("whole_identity_cosine"),
        "best_minimum_third_identity_cosine": best.get(
            "minimum_third_identity_cosine"
        ),
        "best_third_identity_cosines": best.get("third_identity_cosines"),
        "best_pitch_shape_similarity_to_donor": best.get(
            "pitch_shape_similarity_to_donor"
        ),
        "best_text_similarity": best.get("text_similarity"),
        "reason": (
            "Every Doctor calm conversion preserved the text and calm performance, "
            "but the final or weakest third of the line fell below the same identity "
            "stability threshold passed by Doctor pleading and anger. The route is "
            "excluded rather than shown as a weak candidate."
        ),
    }


def package(args: argparse.Namespace) -> dict[str, Any]:
    narrator_benny_root = Path(args.narrator_benny_root).expanduser().resolve()
    doctor_root = Path(args.doctor_root).expanduser().resolve()
    doctor_calm_root = Path(args.doctor_calm_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    for path in (
        narrator_benny_root / "analysis.json",
        doctor_root / "analysis.json",
        doctor_calm_root / "analysis.json",
    ):
        if not path.is_file():
            raise PackageError(f"Required analysis is missing: {path}")

    rows = [
        *passing_narrator_benny(narrator_benny_root),
        *passing_doctor(doctor_root),
    ]
    rows.sort(
        key=lambda row: (
            TARGET_ORDER.index(row["target_key"]),
            MODE_ORDER.index(row["mode"]),
        )
    )
    actual = {(row["target_key"], row["mode"]) for row in rows}
    if actual != EXPECTED_ROUTES or len(rows) != 8:
        raise PackageError(f"Final route set changed: {sorted(actual)}")

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
        .replace("Nine bounded tests.", "Eight bounded tests.")
        .replace(
            "OpenVoice changes the speaker",
            "A voice-conversion stage changes the speaker",
        )
        .replace("0 / 9 complete", "0 / 8 complete"),
        encoding="utf-8",
    )
    app_path = review_root / "app.js"
    app_path.write_text(
        app_path.read_text(encoding="utf-8")
        .replace(
            "alexandria:three-voice-openvoice:",
            "alexandria:three-voice-performance-final:",
        )
        .replace(
            "alexandria_three_voice_openvoice_conversion_review.json",
            "alexandria_three_voice_performance_conversion_review.json",
        ),
        encoding="utf-8",
    )
    public = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "title": "Three-voice performance conversion validation",
        "candidate_count": len(public_rows),
        "target_order": TARGET_ORDER,
        "mode_order": MODE_ORDER,
        "rows": public_rows,
        "excluded_routes": [
            {
                "target_key": "doctor",
                "mode": "calm",
                "reason": "Failed stable identity checks and is intentionally omitted.",
            }
        ],
        "production_promotion_allowed": False,
    }
    (review_root / "data.js").write_text(
        "window.THREE_VOICE_OPENVOICE_DATA = "
        + json.dumps(public, ensure_ascii=False)
        + ";\n",
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
            "excluded_route_count": 1,
            "answer_key_outside_review_root": True,
            "production_promotion_allowed": False,
        },
    )
    write_json(output_root / "answer-key.json", answer_rows)
    diagnosis = calm_diagnosis(doctor_calm_root, doctor_root)
    write_json(output_root / "doctor-calm-diagnosis.json", diagnosis)
    (output_root / "START_HERE.txt").write_text(
        "Three-voice performance conversion validation\n"
        "====================================\n\n"
        f"cd \"{review_root}\"\n"
        "python3 -m http.server 8779 --bind 127.0.0.1\n\n"
        "Then open http://127.0.0.1:8779/\n\n"
        "Eight routes are shown. Doctor calm is deliberately excluded because "
        "it did not maintain stable identity through the entire line.\n",
        encoding="utf-8",
    )
    return {
        "review": str(review_root / "index.html"),
        "candidate_count": len(public_rows),
        "excluded_route_count": 1,
        "doctor_calm_status": diagnosis["status"],
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
    diagnosis = load_json(output_root / "doctor-calm-diagnosis.json")
    routes = {(row["target_key"], row["mode"]) for row in data["rows"]}
    if (
        missing
        or bad_hash
        or routes != EXPECTED_ROUTES
        or diagnosis.get("production_route_approved") is not False
    ):
        raise PackageError(
            "Validation failed: "
            f"missing={missing}, bad_hash={bad_hash}, routes={sorted(routes)}, "
            f"diagnosis={diagnosis}"
        )
    return {
        "round_id": ROUND_ID,
        "candidate_count": len(data["rows"]),
        "missing_count": len(missing),
        "bad_hash_count": len(bad_hash),
        "excluded_route_count": len(data["excluded_routes"]),
        "doctor_calm_status": diagnosis["status"],
        "review": str(review_root / "index.html"),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Package the final passing Seed-VC routes for all three characters."
    )
    sub = result.add_subparsers(dest="command", required=True)
    package_parser = sub.add_parser("package")
    package_parser.add_argument("--narrator-benny-root", required=True)
    package_parser.add_argument("--doctor-root", required=True)
    package_parser.add_argument("--doctor-calm-root", required=True)
    package_parser.add_argument("--output-root", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--output-root", required=True)
    return result


def main(argv: Iterable[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    try:
        value = package(args) if args.command == "package" else validate(args)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
        )
        return 2
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
