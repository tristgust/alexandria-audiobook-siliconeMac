#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROUND_ID = "alexandria_narrator_indextts2_reference_followup_v1"
ASSET_ROOT = Path(__file__).with_name("narrator_indextts2_reference_assets")

FOLLOWUP_SELECTIONS = {
    "panic": "7e0e08b60c0c5ff5",
    "wounded_rage": "622dff12b69a74bb",
    "smug_menace": "7ebf0d515c80cc38",
    "exuberant_joy": "a35707535dc5d8c2",
}

APPROVED_STYLES = {"neutral", "pleading"}
PROVISIONAL_STYLES = {"exuberant_joy"}

FOLLOWUP_CONTEXT = {
    "panic": {
        "scene": "Playtest Ending — Stanley jumps out of reach",
        "reference_text": (
            "No, wait. Stanley, where are you? Don't go anywhere. I can't follow "
            "you there. I can't help you. No, just stay there. I'll find a way "
            "to get you out."
        ),
    },
    "wounded_rage": {
        "scene": "Incorrect Ending — destroyed game",
        "reference_text": (
            "I'm here. I'm still here. Here in this pile of rubbish. With you. "
            "You, who thought you were so clever. Now look where we are. My "
            "entire game is destroyed."
        ),
    },
    "smug_menace": {
        "scene": "Countdown Ending — co-worker revelation",
        "reference_text": (
            "You'd like to know where your co-workers are? A moment of solace "
            "before you're obliterated. All right, I'm in a good mood. You're "
            "going to die anyway. I'll tell you exactly what happened to them."
        ),
    },
    "exuberant_joy": {
        "scene": "Office achievement — completed click challenge",
        "reference_text": (
            "Yes! We did it! Oh, wow! That felt amazing! Oh! You really earned "
            "it, Stanley!"
        ),
    },
}


class FollowupError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    if not path.is_file():
        raise FollowupError(f"Required JSON is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def transcode_pcm(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FollowupError(f"Audio source is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-af",
            "volume=-3dB",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0 or not destination.is_file():
        raise FollowupError(process.stderr.strip() or f"ffmpeg failed for {source}")


def route_decisions(review: dict[str, Any], answer_key: list[dict[str, Any]]) -> dict[str, Any]:
    answers = {row["sample_id"]: row for row in answer_key}
    decisions = []
    for row in review.get("rows", []):
        source = answers.get(row.get("sample_id"))
        if source is None:
            continue
        style = row.get("style")
        delivery = row.get("delivery_1_to_5")
        approved = row.get("approve_for_candidate") is True
        if style in APPROVED_STYLES and approved and (delivery or 0) >= 4:
            disposition = "approved"
        elif style in PROVISIONAL_STYLES and approved:
            disposition = "provisional_identity_only"
        elif style == "wounded_rage" and not isinstance(delivery, int):
            disposition = "unresolved_playback"
        else:
            disposition = "rejected_delivery"
        decisions.append(
            {
                "sample_id": row.get("sample_id"),
                "style": style,
                "alpha": source.get("alpha"),
                "disposition": disposition,
                "identity_1_to_5": row.get("identity_1_to_5"),
                "delivery_1_to_5": delivery,
                "naturalness_1_to_5": row.get("naturalness_1_to_5"),
                "artifact_severity_1_to_5": row.get("artifact_severity_1_to_5"),
                "requested_mode_is_clear": row.get("requested_mode_is_clear"),
                "approve_for_candidate": row.get("approve_for_candidate"),
                "notes": row.get("notes", ""),
            }
        )
    return {
        "schema_version": 1,
        "source_round_id": review.get("round_id"),
        "created_at": now_iso(),
        "decisions": decisions,
        "approved_styles": [row["style"] for row in decisions if row["disposition"] == "approved"],
        "followup_styles": list(FOLLOWUP_SELECTIONS),
        "production_promotion_allowed": False,
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    source_root = Path(args.source_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    review_path = Path(args.review).expanduser().resolve()
    identity_source = Path(args.identity_audio).expanduser().resolve()

    analysis = load_json(source_root / "analysis.json")
    answer_key = load_json(source_root / "answer-key.json")
    matrix = load_json(source_root / "matrix.json")
    review = load_json(review_path)
    if review.get("round_id") != "alexandria_narrator_indextts2_reference_bank_v1":
        raise FollowupError("The supplied review belongs to a different round.")

    analyzed = {row["sample_id"]: row for row in analysis["samples"]}
    matrix_by_style = {}
    for row in matrix["samples"]:
        matrix_by_style.setdefault(row["style"], row)

    if output_root.exists():
        shutil.rmtree(output_root)
    review_root = output_root / "review"
    (review_root / "audio").mkdir(parents=True)
    (review_root / "references").mkdir(parents=True)

    transcode_pcm(identity_source, review_root / "references" / "identity.wav")
    public_rows = []
    private_rows = []
    for ordinal, (style, sample_id) in enumerate(FOLLOWUP_SELECTIONS.items(), 1):
        source = analyzed.get(sample_id)
        if source is None:
            raise FollowupError(f"Follow-up source was not found: {sample_id}")
        spec = matrix_by_style.get(style)
        if spec is None:
            raise FollowupError(f"Matrix reference was not found for {style}")
        generated_target = review_root / "audio" / f"{style}.wav"
        emotion_target = review_root / "references" / f"{style}-reference.wav"
        transcode_pcm(Path(source["audio_path"]), generated_target)
        transcode_pcm(Path(spec["emotion_audio"]), emotion_target)
        followup_id = hashlib.sha256(
            f"{ROUND_ID}|{style}|{sample_id}|pcm24k".encode("utf-8")
        ).hexdigest()[:16]
        public_rows.append(
            {
                "sample_id": followup_id,
                "ordinal": ordinal,
                "style": style,
                "style_label": source["style_label"],
                "target_text": source["target_text"],
                "emotion_scene": FOLLOWUP_CONTEXT[style]["scene"],
                "emotion_reference_text": FOLLOWUP_CONTEXT[style]["reference_text"],
                "audio": f"audio/{style}.wav",
                "emotion_audio": f"references/{style}-reference.wav",
                "identity_audio": "references/identity.wav",
                "automatic_transcript": source["automatic_transcript"],
                "technical_pass": source["technical_pass"],
            }
        )
        private_rows.append(
            {
                **source,
                "followup_sample_id": followup_id,
                "source_sample_id": sample_id,
                "packaged_audio_sha256": sha256_file(generated_target),
                "packaged_emotion_sha256": sha256_file(emotion_target),
                "packaged_sample_rate": 24000,
                "packaged_format": "pcm_s16le_mono",
            }
        )

    for asset in ("index.html", "styles.css", "app.js"):
        shutil.copy2(ASSET_ROOT / asset, review_root / asset)
    index = review_root / "index.html"
    index.write_text(
        index.read_text(encoding="utf-8")
        .replace("Narrator IndexTTS2 Reference Validation", "Narrator IndexTTS2 Strong-Transfer Follow-up")
        .replace("Narrator reference-performance validation", "Narrator strong-transfer follow-up")
        .replace(
            "Six generated lines. Compare each result with the neutral identity anchor and the real Narrator performance used to guide delivery.",
            "Four repaired, stronger-transfer candidates. Neutral and pleading have already been accepted; review only the unresolved expressive routes.",
        )
        .replace("0 / 6 complete", "0 / 4 complete"),
        encoding="utf-8",
    )
    app = review_root / "app.js"
    app.write_text(
        app.read_text(encoding="utf-8").replace(
            "alexandria_narrator_indextts2_reference_validation.json",
            "alexandria_narrator_indextts2_followup_validation.json",
        ),
        encoding="utf-8",
    )
    public = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "title": "Narrator IndexTTS2 Strong-Transfer Follow-up",
        "created_at": now_iso(),
        "candidate_count": len(public_rows),
        "rows": public_rows,
    }
    (review_root / "data.js").write_text(
        "window.NARRATOR_INDEXTTS2_REFERENCE_DATA = "
        + json.dumps(public, ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )
    (review_root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "round_id": ROUND_ID,
                "candidate_count": len(public_rows),
                "reencoded_pcm_24khz": True,
                "answer_key_outside_review_root": True,
                "production_promotion_allowed": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (output_root / "answer-key.json").write_text(
        json.dumps(private_rows, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    decisions = route_decisions(review, answer_key)
    decisions["review_sha256"] = sha256_file(review_path)
    (output_root / "route-decisions.json").write_text(
        json.dumps(decisions, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (output_root / "START_HERE.txt").write_text(
        "Narrator IndexTTS2 Strong-Transfer Follow-up\n"
        "=============================================\n\n"
        f"cd \"{review_root}\"\n"
        "python3 -m http.server 8775 --bind 127.0.0.1\n\n"
        "Then open http://127.0.0.1:8775/\n",
        encoding="utf-8",
    )
    return {
        "review": str(review_root / "index.html"),
        "candidate_count": len(public_rows),
        "approved_styles": decisions["approved_styles"],
        "followup_styles": decisions["followup_styles"],
    }


def validate(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    review_root = output_root / "review"
    prefix = "window.NARRATOR_INDEXTTS2_REFERENCE_DATA = "
    text = (review_root / "data.js").read_text(encoding="utf-8").strip()
    public = json.loads(text[len(prefix) :].rstrip(";"))
    answer = {row["followup_sample_id"]: row for row in load_json(output_root / "answer-key.json")}
    missing = []
    hash_errors = []
    for row in public["rows"]:
        audio = review_root / row["audio"]
        emotion = review_root / row["emotion_audio"]
        source = answer[row["sample_id"]]
        if not audio.is_file() or not emotion.is_file():
            missing.append(row["sample_id"])
            continue
        if sha256_file(audio) != source["packaged_audio_sha256"]:
            hash_errors.append(row["sample_id"])
    if missing or hash_errors:
        raise FollowupError(f"Validation failed: missing={missing}, hash_errors={hash_errors}")
    return {
        "round_id": ROUND_ID,
        "candidate_count": len(public["rows"]),
        "missing_count": len(missing),
        "hash_error_count": len(hash_errors),
        "review": str(review_root / "index.html"),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Build a minimal strong-transfer IndexTTS2 follow-up.")
    sub = result.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--source-root", required=True)
    build_parser.add_argument("--review", required=True)
    build_parser.add_argument("--identity-audio", required=True)
    build_parser.add_argument("--output-root", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--output-root", required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        value = build(args) if args.command == "build" else validate(args)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(value, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
