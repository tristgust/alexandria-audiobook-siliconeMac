#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import shutil
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf

APPLIED_ROUND_ID = "alexandria_three_voice_source_atlas_applied_v1"
REPAIR_ROUND_ID = "alexandria_three_voice_source_repairs_v1"
REVIEW_ROUND_ID = "alexandria_three_voice_source_repairs_review_v1"
RANGE_SERVER = Path(__file__).with_name("range_http_server.py")
ASSET_ROOT = Path(__file__).with_name("three_voice_source_repair_assets")


class RepairError(RuntimeError):
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
        raise RepairError(f"JSON file is missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RepairError(f"Invalid JSON in {path}: {exc}") from exc


def normalize_words(text: str) -> list[str]:
    return [token for token in "".join(ch.lower() if ch.isalnum() or ch == "'" else " " for ch in text).split() if token]


def transcript_similarity(expected: str, observed: str) -> float:
    return difflib.SequenceMatcher(None, normalize_words(expected), normalize_words(observed)).ratio()


def ffprobe(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_name,sample_rate,channels,channel_layout:format=duration",
            "-of", "json", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    stream = (payload.get("streams") or [{}])[0]
    return {
        "codec_name": stream.get("codec_name"),
        "sample_rate": int(stream.get("sample_rate") or 0),
        "channels": int(stream.get("channels") or 0),
        "channel_layout": stream.get("channel_layout"),
        "duration": float((payload.get("format") or {}).get("duration") or 0.0),
    }


def transcribe(path: Path, whisper_model: Path) -> str:
    import mlx_whisper

    result = mlx_whisper.transcribe(
        str(path),
        path_or_hf_repo=str(whisper_model),
        language="en",
        condition_on_previous_text=False,
        word_timestamps=False,
        verbose=False,
    )
    return str(result.get("text") or "").strip()


def audio_metrics(path: Path) -> dict[str, Any]:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = audio.mean(axis=1, dtype=np.float32)
    if mono.size == 0:
        raise RepairError(f"Empty audio: {path}")
    peak = float(np.max(np.abs(mono)))
    rms = float(np.sqrt(np.mean(np.square(mono), dtype=np.float64)))
    clipping = int(np.count_nonzero(np.abs(mono) >= 0.999))
    return {
        "sample_rate": int(sample_rate),
        "channels": int(audio.shape[1]),
        "duration_seconds": round(mono.size / sample_rate, 6),
        "peak": round(peak, 6),
        "rms": round(rms, 6),
        "clipping_sample_count": clipping,
    }


def run_ffmpeg(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RepairError(result.stderr.strip() or f"ffmpeg failed: {command}")


def encode_review_mp3(source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "ffmpeg", "-v", "error", "-y", "-i", str(source),
        "-ac", "1", "-ar", "48000", "-c:a", "libmp3lame", "-b:a", "192k", str(output),
    ])


def mild_cleanup(source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    filters = (
        "highpass=f=65,lowpass=f=10500,"
        "afftdn=nr=5:nf=-48:tn=1:gs=5,"
        "loudnorm=I=-21:LRA=11:TP=-2"
    )
    run_ffmpeg([
        "ffmpeg", "-v", "error", "-y", "-i", str(source),
        "-af", filters, "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(output),
    ])


def dialogue_cleanup(source: Path, start: float, end: float, output: Path) -> bool:
    info = ffprobe(source)
    if info["channels"] < 2:
        return False
    output.parent.mkdir(parents=True, exist_ok=True)
    filters = (
        "dialoguenhance=original=0.25:enhance=2.1:voice=12,"
        "pan=mono|c0=c2,highpass=f=65,lowpass=f=10500,"
        "afftdn=nr=3:nf=-50:tn=1:gs=4,"
        "loudnorm=I=-21:LRA=11:TP=-2"
    )
    try:
        run_ffmpeg([
            "ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, start):.3f}",
            "-to", f"{end:.3f}", "-i", str(source), "-af", filters,
            "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(output),
        ])
    except RepairError:
        output.unlink(missing_ok=True)
        return False
    return output.is_file()


def transcribe_source_words(
    source: Path,
    start: float,
    end: float,
    whisper_model: Path,
) -> list[dict[str, Any]]:
    import mlx_whisper

    result = mlx_whisper.transcribe(
        str(source),
        path_or_hf_repo=str(whisper_model),
        language="en",
        word_timestamps=True,
        condition_on_previous_text=False,
        clip_timestamps=f"{start},{end}",
        verbose=False,
    )
    words: list[dict[str, Any]] = []
    for segment in result.get("segments", []):
        for word in segment.get("words", []):
            token = str(word.get("word") or "").strip()
            normalized = normalize_words(token)
            if not normalized:
                continue
            words.append(
                {
                    "word": token,
                    "normalized": normalized[0],
                    "start": float(word.get("start", 0.0)),
                    "end": float(word.get("end", 0.0)),
                }
            )
    return words


def best_word_span(words: list[dict[str, Any]], expected: str) -> tuple[int, int, float]:
    expected_words = normalize_words(expected)
    actual_words = [item["normalized"] for item in words]
    if not expected_words or not actual_words:
        raise RepairError("Cannot resolve a boundary from empty words.")
    best = (-1, -1, -1.0)
    low = max(1, int(len(expected_words) * 0.70))
    high = min(len(actual_words), int(len(expected_words) * 1.30) + 2)
    for size in range(low, high + 1):
        for start in range(0, len(actual_words) - size + 1):
            end = start + size
            score = difflib.SequenceMatcher(None, expected_words, actual_words[start:end]).ratio()
            score -= abs(size - len(expected_words)) / max(len(expected_words), 1) * 0.04
            if score > best[2]:
                best = (start, end, score)
    if best[2] < 0.68:
        raise RepairError(f"Boundary transcript match is too weak: {best[2]:.3f}")
    return best


def repair_boundary(
    row: dict[str, Any],
    output: Path,
    boundary: str,
    whisper_model: Path,
) -> tuple[dict[str, Any], str]:
    source = Path(str(row.get("source_audio") or ""))
    if not source.is_file():
        raise RepairError(f"Original source audio is missing for {row.get('clip_id')}: {source}")
    current_start = float(row.get("selected_start_seconds") or 0.0)
    current_end = float(row.get("selected_end_seconds") or 0.0)
    expected = str(row.get("selected_transcript") or "")
    context_start = max(0.0, current_start - 1.5)
    context_end = current_end + 1.5
    words = transcribe_source_words(source, context_start, context_end, whisper_model)
    start_index, end_index, match = best_word_span(words, expected)
    selected = words[start_index:end_index]
    first_word_start = float(selected[0]["start"])
    last_word_end = float(selected[-1]["end"])
    repaired_transcript = expected

    if boundary == "too_early":
        start = first_word_start
        end = current_end
    elif boundary == "too_late":
        start = max(0.0, min(current_start - 0.45, first_word_start - 0.20))
        end = current_end
    elif boundary == "ends_too_early":
        start = current_start
        end = max(current_end + 0.45, last_word_end + 0.20)
    elif boundary == "ends_too_late":
        start = current_start
        end = last_word_end + 0.08
        if end >= current_end - 0.05:
            if row.get("clip_id") == "narrator_ud_contemptuous_disbelief":
                prior_sentence = expected.rsplit(" Absolutely incredible!", 1)[0].strip()
                prior_start, prior_end, prior_match = best_word_span(words, prior_sentence)
                prior_words = words[prior_start:prior_end]
                end = float(prior_words[-1]["end"]) + 0.08
                repaired_transcript = prior_sentence
                match = min(match, prior_match)
            else:
                end = max(start + 0.45, current_end - 0.15)
    else:
        raise RepairError(f"Unsupported boundary decision: {boundary}")

    if end - start < 0.45:
        raise RepairError(f"Boundary repair made {row.get('clip_id')} too short")
    output.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-i", str(source), "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(output),
    ])
    detail = {
        "absolute_start_seconds": round(start, 3),
        "absolute_end_seconds": round(end, 3),
        "source_first_word_start_seconds": round(first_word_start, 3),
        "source_last_word_end_seconds": round(last_word_end, 3),
        "source_match": round(match, 6),
        "original_start_seconds": round(current_start, 3),
        "original_end_seconds": round(current_end, 3),
        "start_delta_seconds": round(start - current_start, 3),
        "end_delta_seconds": round(end - current_end, 3),
    }
    return detail, repaired_transcript


def evaluate_variant(path: Path, expected: str, whisper_model: Path) -> dict[str, Any]:
    observed = transcribe(path, whisper_model)
    similarity = transcript_similarity(expected, observed)
    metrics = audio_metrics(path)
    return {
        "audio_path": str(path),
        "audio_sha256": sha256_file(path),
        "verification_transcript": observed,
        "verification_similarity": round(similarity, 6),
        "metrics": metrics,
        "technical_pass": similarity >= 0.72 and metrics["clipping_sample_count"] == 0 and metrics["peak"] <= 0.99,
    }


def choose_cleanup(row: dict[str, Any], root: Path, whisper_model: Path) -> tuple[Path, dict[str, Any]]:
    clip_id = row["clip_id"]
    original = Path(row["audio_path"])
    variants_root = root / "variants" / row["target"] / clip_id
    mild = variants_root / "mild.wav"
    mild_cleanup(original, mild)
    variants: dict[str, dict[str, Any]] = {"mild": evaluate_variant(mild, row["selected_transcript"], whisper_model)}

    dialogue = variants_root / "dialogue.wav"
    source = Path(str(row.get("source_audio") or ""))
    if source.is_file() and dialogue_cleanup(
        source,
        float(row.get("selected_start_seconds") or 0.0),
        float(row.get("selected_end_seconds") or 0.0),
        dialogue,
    ):
        variants["dialogue"] = evaluate_variant(dialogue, row["selected_transcript"], whisper_model)

    passing = [(name, item) for name, item in variants.items() if item["technical_pass"]]
    if not passing:
        best_name, best = max(variants.items(), key=lambda item: item[1]["verification_similarity"])
        return Path(best["audio_path"]), {
            "selected_variant": best_name,
            "selection_reason": "no_variant_passed_hard_gate",
            "variants": variants,
            "technical_pass": False,
        }

    mild_score = variants["mild"]["verification_similarity"]
    dialogue_item = variants.get("dialogue")
    if dialogue_item and dialogue_item["technical_pass"] and dialogue_item["verification_similarity"] >= mild_score - 0.04:
        selected_name = "dialogue"
        reason = "dialogue_enhancement_preserved_transcript"
    else:
        selected_name = max(passing, key=lambda item: item[1]["verification_similarity"])[0]
        reason = "highest_transcript_fidelity"
    selected = variants[selected_name]
    return Path(selected["audio_path"]), {
        "selected_variant": selected_name,
        "selection_reason": reason,
        "variants": variants,
        "technical_pass": selected["technical_pass"],
    }


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    applied_path = Path(args.applied).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    whisper_model = Path(args.whisper_model).expanduser().resolve()
    if not whisper_model.is_dir():
        raise RepairError(f"Whisper model is missing: {whisper_model}")
    applied = load_json(applied_path)
    if applied.get("round_id") != APPLIED_ROUND_ID:
        raise RepairError(f"Unexpected applied ledger round_id: {applied.get('round_id')}")
    if output_root.exists() and args.force:
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    cleanup_rows = list(applied.get("cleanup_queue") or [])
    boundary_rows = list(applied.get("boundary_repair_queue") or [])
    pending_rows = list(applied.get("incomplete_review_queue") or [])

    for row in cleanup_rows:
        try:
            repaired, selection = choose_cleanup(row, output_root, whisper_model)
            rows.append({
                **row,
                "repair_type": "cleanup",
                "repair_reason": "reviewer_approved_after_cleanup",
                "original_audio_path": row["audio_path"],
                "original_audio_sha256": row["audio_sha256"],
                "repaired_audio_path": str(repaired),
                "repaired_audio_sha256": sha256_file(repaired),
                "repair_details": selection,
                "technical_pass": bool(selection["technical_pass"]),
                "production_promotion_allowed": False,
            })
        except Exception as exc:
            failures.append({"clip_id": row.get("clip_id"), "repair_type": "cleanup", "error_type": type(exc).__name__, "error": str(exc)})

    for row in boundary_rows:
        try:
            boundary = str(row.get("boundary_decision") or "")
            repaired = output_root / "repairs" / row["target"] / f"{row['clip_id']}.wav"
            detail, repaired_transcript = repair_boundary(row, repaired, boundary, whisper_model)
            if row.get("audio_cleanliness_decision") == "usable_with_cleanup":
                cleaned = repaired.with_name(repaired.stem + "-clean.wav")
                mild_cleanup(repaired, cleaned)
                repaired = cleaned
                detail["cleanup_also_applied"] = True
            evaluation = evaluate_variant(repaired, repaired_transcript, whisper_model)
            rows.append({
                **row,
                "repair_type": "boundary",
                "repair_reason": f"reviewer_marked_{boundary}",
                "original_audio_path": row["audio_path"],
                "original_audio_sha256": row["audio_sha256"],
                "repaired_audio_path": str(repaired),
                "repaired_audio_sha256": sha256_file(repaired),
                "repaired_transcript": repaired_transcript,
                "repair_details": {"boundary_adjustment": detail, "evaluation": evaluation},
                "technical_pass": bool(evaluation["technical_pass"]),
                "production_promotion_allowed": False,
            })
        except Exception as exc:
            failures.append({"clip_id": row.get("clip_id"), "repair_type": "boundary", "error_type": type(exc).__name__, "error": str(exc)})

    for row in pending_rows:
        try:
            repaired, selection = choose_cleanup(row, output_root, whisper_model) if row.get("audio_cleanliness_decision") == "usable_with_cleanup" else (Path(row["audio_path"]), {"selected_variant": "original", "technical_pass": True})
            rows.append({
                **row,
                "repair_type": "decision_only",
                "repair_reason": "review_decision_missing",
                "original_audio_path": row["audio_path"],
                "original_audio_sha256": row["audio_sha256"],
                "repaired_audio_path": str(repaired),
                "repaired_audio_sha256": sha256_file(repaired),
                "repair_details": selection,
                "technical_pass": bool(selection.get("technical_pass", True)),
                "production_promotion_allowed": False,
            })
        except Exception as exc:
            failures.append({"clip_id": row.get("clip_id"), "repair_type": "decision_only", "error_type": type(exc).__name__, "error": str(exc)})

    payload = {
        "schema_version": 1,
        "round_id": REPAIR_ROUND_ID,
        "created_at": now_iso(),
        "source_applied_ledger": str(applied_path),
        "source_applied_ledger_sha256": sha256_file(applied_path),
        "candidate_count": len(rows),
        "expected_candidate_count": len(cleanup_rows) + len(boundary_rows) + len(pending_rows),
        "repair_type_counts": dict(sorted(Counter(row["repair_type"] for row in rows).items())),
        "technical_pass_count": sum(row["technical_pass"] for row in rows),
        "failure_count": len(failures),
        "failures": failures,
        "rows": rows,
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }
    manifest = output_root / "repair-manifest.json"
    manifest.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if failures and not args.allow_failures:
        raise RepairError(f"{len(failures)} repairs failed; see {manifest}")
    return {"candidate_count": len(rows), "technical_pass_count": payload["technical_pass_count"], "failure_count": len(failures), "manifest": str(manifest)}


def validate_repair(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("round_id") != REPAIR_ROUND_ID:
        raise RepairError("Repair manifest has an unexpected round_id.")
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise RepairError("Repair manifest rows are missing.")
    failures: list[str] = []
    seen: set[str] = set()
    for row in rows:
        clip_id = row.get("clip_id")
        if clip_id in seen:
            failures.append(f"duplicate:{clip_id}")
        seen.add(clip_id)
        for key in ("original_audio_path", "repaired_audio_path"):
            path = Path(str(row.get(key) or ""))
            if not path.is_file():
                failures.append(f"missing:{clip_id}:{key}")
        repaired = Path(str(row.get("repaired_audio_path") or ""))
        if repaired.is_file() and sha256_file(repaired) != row.get("repaired_audio_sha256"):
            failures.append(f"hash:{clip_id}")
        if row.get("production_promotion_allowed") is not False:
            failures.append(f"promotion:{clip_id}")
    if len(rows) != int(payload.get("candidate_count") or 0):
        failures.append("candidate_count")
    if failures:
        raise RepairError(f"Repair validation failed: {failures}")
    return {
        "candidate_count": len(rows),
        "technical_pass_count": sum(bool(row.get("technical_pass")) for row in rows),
        "failure_count": len(payload.get("failures") or []),
        "repair_type_counts": dict(sorted(Counter(str(row.get("repair_type")) for row in rows).items())),
    }


def package(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = Path(args.manifest).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    payload = load_json(manifest_path)
    validate_repair(payload)
    review = output_root / "review"
    if review.exists():
        shutil.rmtree(review)
    (review / "audio" / "original").mkdir(parents=True)
    (review / "audio" / "repaired").mkdir(parents=True)
    for name in ("index.html", "styles.css", "app.js"):
        shutil.copy2(ASSET_ROOT / name, review / name)
    shutil.copy2(RANGE_SERVER, review / "serve_review.py")

    public_rows: list[dict[str, Any]] = []
    answer_rows: list[dict[str, Any]] = []
    for ordinal, row in enumerate(payload["rows"], start=1):
        original = Path(row["original_audio_path"])
        repaired = Path(row["repaired_audio_path"])
        original_mp3 = review / "audio" / "original" / f"{row['clip_id']}.mp3"
        repaired_mp3 = review / "audio" / "repaired" / f"{row['clip_id']}.mp3"
        encode_review_mp3(original, original_mp3)
        encode_review_mp3(repaired, repaired_mp3)
        evaluation = row.get("repair_details", {}).get("evaluation") or {}
        if row["repair_type"] == "cleanup":
            selected_name = row.get("repair_details", {}).get("selected_variant")
            variants = row.get("repair_details", {}).get("variants") or {}
            evaluation = variants.get(selected_name) or evaluation
        public_rows.append({
            "clip_id": row["clip_id"],
            "ordinal": ordinal,
            "target": row["target"],
            "target_label": row["target_label"],
            "repair_type": row["repair_type"],
            "repair_reason": row["repair_reason"],
            "source_title": row["source_title"],
            "original_transcript": row["selected_transcript"],
            "selected_transcript": row.get("repaired_transcript") or row["selected_transcript"],
            "primary_emotion": row["primary_emotion"],
            "secondary_emotion": row["secondary_emotion"],
            "dramatic_function": row["dramatic_function"],
            "coverage_gap": row.get("coverage_gap"),
            "prior_boundary_decision": row.get("boundary_decision"),
            "prior_cleanliness_decision": row.get("audio_cleanliness_decision"),
            "prior_reference_decision": row.get("reference_decision"),
            "technical_pass": bool(row.get("technical_pass")),
            "verification_transcript": evaluation.get("verification_transcript"),
            "verification_similarity": evaluation.get("verification_similarity"),
            "original_audio": f"audio/original/{row['clip_id']}.mp3",
            "repaired_audio": f"audio/repaired/{row['clip_id']}.mp3",
        })
        answer_rows.append({
            **row,
            "review_original_audio_sha256": sha256_file(original_mp3),
            "review_repaired_audio_sha256": sha256_file(repaired_mp3),
        })

    public = {
        "schema_version": 1,
        "round_id": REVIEW_ROUND_ID,
        "title": "Three-Voice Source Repair Review",
        "candidate_count": len(public_rows),
        "target_counts": dict(sorted(Counter(row["target"] for row in public_rows).items())),
        "repair_type_counts": dict(sorted(Counter(row["repair_type"] for row in public_rows).items())),
        "rows": public_rows,
    }
    (review / "data.js").write_text("window.THREE_VOICE_SOURCE_REPAIR_DATA = " + json.dumps(public, ensure_ascii=False) + ";\n", encoding="utf-8")
    review_manifest = {
        "schema_version": 1,
        "round_id": REVIEW_ROUND_ID,
        "candidate_count": len(public_rows),
        "target_counts": public["target_counts"],
        "repair_type_counts": public["repair_type_counts"],
        "original_and_repaired_comparison": True,
        "maximum_simultaneous_audio_elements": 2,
        "range_server_included": True,
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }
    (review / "manifest.json").write_text(json.dumps(review_manifest, indent=2) + "\n", encoding="utf-8")
    (output_root / "answer-key.json").write_text(json.dumps(answer_rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output_root / "START_HERE.txt").write_text(
        f'cd "{review}"\npython3 serve_review.py --bind 127.0.0.1 --port 8788\n\nThen open http://127.0.0.1:8788/\n',
        encoding="utf-8",
    )
    return {"candidate_count": len(public_rows), "review": str(review / "index.html")}


def validate_package(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.output_root).expanduser().resolve()
    review = root / "review"
    prefix = "window.THREE_VOICE_SOURCE_REPAIR_DATA = "
    text = (review / "data.js").read_text(encoding="utf-8").strip()
    if not text.startswith(prefix):
        raise RepairError("Review data.js has an unexpected prefix.")
    data = json.loads(text[len(prefix):].rstrip(";"))
    failures: list[str] = []
    for row in data["rows"]:
        for key in ("original_audio", "repaired_audio"):
            path = review / row[key]
            if not path.is_file():
                failures.append(f"missing:{row['clip_id']}:{key}")
                continue
            info = ffprobe(path)
            if info["codec_name"] != "mp3" or info["channels"] != 1:
                failures.append(f"format:{row['clip_id']}:{key}")
        if not row.get("selected_transcript") or not row.get("primary_emotion"):
            failures.append(f"metadata:{row['clip_id']}")
    if failures:
        raise RepairError(f"Review package validation failed: {failures}")
    return {"candidate_count": len(data["rows"]), "failure_count": 0, "review": str(review / "index.html")}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare and package source-audio repairs for the three-voice atlas.")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--applied", required=True)
    prepare_parser.add_argument("--output-root", required=True)
    prepare_parser.add_argument("--whisper-model", required=True)
    prepare_parser.add_argument("--allow-failures", action="store_true")
    prepare_parser.add_argument("--force", action="store_true")
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--manifest", required=True)
    package_parser = sub.add_parser("package")
    package_parser.add_argument("--manifest", required=True)
    package_parser.add_argument("--output-root", required=True)
    package_validate_parser = sub.add_parser("validate-package")
    package_validate_parser.add_argument("--output-root", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "prepare":
            result = prepare(args)
        elif args.command == "validate":
            result = validate_repair(load_json(Path(args.manifest).expanduser().resolve()))
        elif args.command == "package":
            result = package(args)
        else:
            result = validate_package(args)
    except (RepairError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
