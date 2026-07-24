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

from prepare_narrator_indextts2_reference_bank import sha256_file

ROUND_ID = "alexandria_targeted_voice_followup_v1"
ASSET_ROOT = Path(__file__).with_name("lazy_voice_followup_assets")
RANGE_SERVER = Path(__file__).with_name("range_http_server.py")

SELECTIONS: tuple[dict[str, Any], ...] = (
    {
        "source": "expanded",
        "target_key": "narrator",
        "mode": "wounded_rage",
        "speaker_strategy": "self",
        "alpha": 0.60,
        "mode_label": "Wounded rage · stronger delivery",
        "short_label": "Rage repair",
        "purpose": "delivery_repair",
        "purpose_label": "Stronger delivery alternative",
    },
    {
        "source": "expanded",
        "target_key": "narrator",
        "mode": "exuberant_joy",
        "speaker_strategy": "self",
        "alpha": 0.60,
        "mode_label": "Exuberant joy · playback repair",
        "short_label": "Joy playback",
        "purpose": "playback_repair",
        "purpose_label": "Same candidate, repackaged for reliable playback",
    },
    {
        "source": "benny_self",
        "target_key": "benny",
        "mode": "excited_discovery",
        "speaker_strategy": None,
        "alpha": 0.60,
        "mode_label": "Excited discovery · soul repair",
        "short_label": "Discovery soul",
        "purpose": "delivery_repair",
        "purpose_label": "Authentic performance also controls speaker identity",
    },
    {
        "source": "doctor_followup",
        "target_key": "doctor",
        "mode": "cold_existential_dismissal",
        "speaker_strategy": "character_bank",
        "alpha": 0.20,
        "mode_label": "Cold existential dismissal · identity repair",
        "short_label": "Cold identity",
        "purpose": "identity_repair",
        "purpose_label": "Lower transfer strength with in-character identity bank",
    },
    {
        "source": "doctor_followup",
        "target_key": "doctor",
        "mode": "dry_sarcasm",
        "speaker_strategy": "character_bank",
        "alpha": 0.10,
        "mode_label": "Dry sarcasm · cleanliness repair",
        "short_label": "Sarcasm cleanup",
        "purpose": "cleanliness_repair",
        "purpose_label": "Lower transfer strength to reduce vocal weirdness",
    },
    {
        "source": "doctor_followup",
        "target_key": "doctor",
        "mode": "protective_authority_repair",
        "speaker_strategy": "character_bank",
        "alpha": 0.45,
        "mode_label": "Protective authority · identity tradeoff",
        "short_label": "Protective identity",
        "purpose": "identity_repair_experimental",
        "purpose_label": "Near-threshold identity-first alternative",
    },
)


class FollowupError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_analysis(root: Path) -> list[dict[str, Any]]:
    path = root / "analysis.json"
    if not path.is_file():
        raise FollowupError(f"Analysis is missing: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("samples")
    if not isinstance(rows, list):
        raise FollowupError(f"Analysis samples are invalid: {path}")
    return rows


def select_row(rows: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    matches = []
    for row in rows:
        if row.get("target_key") != spec["target_key"] or row.get("mode") != spec["mode"]:
            continue
        if abs(float(row.get("alpha", -1)) - float(spec["alpha"])) > 1e-9:
            continue
        expected_strategy = spec.get("speaker_strategy")
        if expected_strategy is not None and row.get("speaker_strategy") != expected_strategy:
            continue
        matches.append(row)
    if len(matches) != 1:
        raise FollowupError(f"Expected one source row for {spec}; found {len(matches)}")
    return matches[0]


def encode_mp3(source: Path, output: Path) -> None:
    if not source.is_file():
        raise FollowupError(f"Audio source is missing: {source}")
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-map_metadata",
            "-1",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(output),
        ],
        check=True,
    )


def probe_audio(path: Path) -> dict[str, Any]:
    process = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,sample_rate,channels:format=duration,size",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(process.stdout)
    stream = data.get("streams", [{}])[0]
    format_data = data.get("format", {})
    return {
        "codec_name": stream.get("codec_name"),
        "sample_rate": int(stream.get("sample_rate") or 0),
        "channels": int(stream.get("channels") or 0),
        "duration_seconds": float(format_data.get("duration") or 0.0),
        "size_bytes": int(format_data.get("size") or 0),
    }


def package(args: argparse.Namespace) -> dict[str, Any]:
    roots = {
        "expanded": Path(args.expanded_root).expanduser().resolve(),
        "benny_self": Path(args.benny_self_root).expanduser().resolve(),
        "doctor_followup": Path(args.doctor_followup_root).expanduser().resolve(),
    }
    output_root = Path(args.output_root).expanduser().resolve()
    analyses = {key: load_analysis(root) for key, root in roots.items()}
    selected = []
    for spec in SELECTIONS:
        source_row = select_row(analyses[spec["source"]], spec)
        selected.append({**source_row, **spec})

    review_root = output_root / "review"
    if review_root.exists():
        shutil.rmtree(review_root)
    (review_root / "audio" / "targets").mkdir(parents=True)
    (review_root / "audio" / "references").mkdir(parents=True)
    (review_root / "audio" / "generated").mkdir(parents=True)
    for asset in ("index.html", "app.js", "styles.css"):
        shutil.copy2(ASSET_ROOT / asset, review_root / asset)
    shutil.copy2(RANGE_SERVER, review_root / "serve_review.py")

    public_rows = []
    answer_rows = []
    packaged_targets: dict[str, str] = {}
    for ordinal, row in enumerate(selected, 1):
        target_source = Path(row["canonical_identity_audio"]).resolve()
        reference_source = Path(row["reference_audio"]).resolve()
        generated_source = Path(row["audio_path"]).resolve()
        target_name = f"{row['target_key']}.mp3"
        reference_name = f"{row['target_key']}-{row['mode']}.mp3"
        generated_name = f"{row['sample_id']}.mp3"
        target_output = review_root / "audio" / "targets" / target_name
        reference_output = review_root / "audio" / "references" / reference_name
        generated_output = review_root / "audio" / "generated" / generated_name
        if row["target_key"] not in packaged_targets:
            encode_mp3(target_source, target_output)
            packaged_targets[row["target_key"]] = target_name
        encode_mp3(reference_source, reference_output)
        encode_mp3(generated_source, generated_output)
        target_probe = probe_audio(target_output)
        reference_probe = probe_audio(reference_output)
        generated_probe = probe_audio(generated_output)
        public_rows.append(
            {
                "sample_id": row["sample_id"],
                "ordinal": ordinal,
                "target_key": row["target_key"],
                "target_label": row["target_label"],
                "mode": row["mode"],
                "mode_label": row["mode_label"],
                "short_label": row["short_label"],
                "purpose": row["purpose"],
                "purpose_label": row["purpose_label"],
                "expected_text": row["target_text"],
                "target_audio": f"audio/targets/{target_name}",
                "reference_audio": f"audio/references/{reference_name}",
                "generated_audio": f"audio/generated/{generated_name}",
                "technical_pass": bool(row.get("technical_pass")),
                "automatic_transcript": row.get("automatic_transcript", ""),
            }
        )
        answer_rows.append(
            {
                **row,
                "packaged_target_audio": str(target_output),
                "packaged_target_sha256": sha256_file(target_output),
                "packaged_reference_audio": str(reference_output),
                "packaged_reference_sha256": sha256_file(reference_output),
                "packaged_generated_audio": str(generated_output),
                "packaged_generated_sha256": sha256_file(generated_output),
                "packaged_target_probe": target_probe,
                "packaged_reference_probe": reference_probe,
                "packaged_generated_probe": generated_probe,
            }
        )

    public = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "title": "Targeted same-speaker follow-up",
        "created_at": now_iso(),
        "candidate_count": len(public_rows),
        "rows": public_rows,
        "lazy_audio_loading": True,
        "browser_audio_format": "mp3_192kbps_mono_48khz",
        "production_promotion_allowed": False,
    }
    (review_root / "data.js").write_text(
        "window.LAZY_VOICE_FOLLOWUP_DATA = " + json.dumps(public, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    (review_root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "round_id": ROUND_ID,
                "candidate_count": len(public_rows),
                "lazy_audio_loading": True,
                "maximum_simultaneous_audio_elements": 3,
                "range_server_included": True,
                "browser_audio_format": "mp3_192kbps_mono_48khz",
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
        json.dumps(answer_rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    decisions = {
        "schema_version": 1,
        "source_round_id": "alexandria_expanded_same_speaker_round_v1",
        "accepted_without_repeat": [
            {"target_key": "narrator", "mode": "wounded_pleading", "reason": "identity_5_delivery_5_clear_and_approved"},
            {"target_key": "benny", "mode": "determined_resolve", "reason": "all_scores_5_clear_and_approved"},
            {"target_key": "benny", "mode": "emergency_distress_repair", "reason": "all_scores_5_controls_incomplete"},
        ],
        "followup_routes": [
            {"target_key": row["target_key"], "mode": row["mode"], "purpose": row["purpose"]}
            for row in public_rows
        ],
        "rejected_experiment": {
            "name": "doctor_actor_interview_as_speaker_prompt",
            "reason": "preserved_actor_interview_identity_but_lost_in_character_doctor_identity",
        },
    }
    (output_root / "decision-summary.json").write_text(
        json.dumps(decisions, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_root / "START_HERE.txt").write_text(
        "Targeted same-speaker follow-up\n"
        "===============================\n\n"
        f"cd \"{review_root}\"\n"
        "python3 serve_review.py --bind 127.0.0.1 --port 8782\n\n"
        "Then open http://127.0.0.1:8782/\n\n"
        "Do not use python3 -m http.server for this package; it does not honor byte-range requests.\n",
        encoding="utf-8",
    )
    return {
        "review": str(review_root / "index.html"),
        "candidate_count": len(public_rows),
        "lazy_audio_loading": True,
        "range_server": str(review_root / "serve_review.py"),
    }


def validate(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    review_root = output_root / "review"
    prefix = "window.LAZY_VOICE_FOLLOWUP_DATA = "
    text = (review_root / "data.js").read_text(encoding="utf-8").strip()
    public = json.loads(text[len(prefix) :].rstrip(";"))
    answers = {
        row["sample_id"]: row
        for row in json.loads((output_root / "answer-key.json").read_text(encoding="utf-8"))
    }
    missing = []
    bad_hash = []
    bad_audio = []
    for row in public["rows"]:
        answer = answers[row["sample_id"]]
        checks = (
            (review_root / row["target_audio"], answer["packaged_target_sha256"]),
            (review_root / row["reference_audio"], answer["packaged_reference_sha256"]),
            (review_root / row["generated_audio"], answer["packaged_generated_sha256"]),
        )
        for path, expected_hash in checks:
            if not path.is_file():
                missing.append(str(path))
                continue
            if sha256_file(path) != expected_hash:
                bad_hash.append(str(path))
            probe = probe_audio(path)
            if (
                probe["codec_name"] != "mp3"
                or probe["sample_rate"] != 48000
                or probe["channels"] != 1
                or probe["duration_seconds"] < 0.8
            ):
                bad_audio.append({"path": str(path), "probe": probe})
    if not (review_root / "serve_review.py").is_file():
        missing.append(str(review_root / "serve_review.py"))
    body = (review_root / "index.html").read_text(encoding="utf-8")
    if re.search(r"OpenVoice|Seed-VC|IndexTTS2", body, re.IGNORECASE):
        raise FollowupError("Model name leaked into public follow-up review")
    if missing or bad_hash or bad_audio:
        raise FollowupError(
            f"Validation failed: missing={missing}, bad_hash={bad_hash}, bad_audio={bad_audio}"
        )
    return {
        "round_id": ROUND_ID,
        "candidate_count": len(public["rows"]),
        "missing_count": len(missing),
        "bad_hash_count": len(bad_hash),
        "bad_audio_count": len(bad_audio),
        "review": str(review_root / "index.html"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Package a lazy-loading targeted voice follow-up.")
    sub = parser.add_subparsers(dest="command", required=True)
    package_parser = sub.add_parser("package")
    package_parser.add_argument("--expanded-root", required=True)
    package_parser.add_argument("--benny-self-root", required=True)
    package_parser.add_argument("--doctor-followup-root", required=True)
    package_parser.add_argument("--output-root", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--output-root", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        result = package(args) if args.command == "package" else validate(args)
    except (FollowupError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
