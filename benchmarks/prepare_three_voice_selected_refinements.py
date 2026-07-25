#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf

APPLIED_ROUND_ID = "alexandria_three_voice_final_salvage_applied_v1"
REFINEMENT_ROUND_ID = "alexandria_three_voice_selected_refinements_v1"
REVIEW_ROUND_ID = "alexandria_three_voice_selected_refinements_review_v1"
RANGE_SERVER = Path(__file__).with_name("range_http_server.py")
ASSET_ROOT = Path(__file__).with_name("three_voice_selected_refinement_assets")

BENNY_CLIP_ID = "benny_shock_grief"
DOCTOR_CLIP_ID = "doctor_indomitable_determination"
DOCTOR_MODEL_FILENAME = "mel_band_roformer_vocals_fv4_gabox.ckpt"
DOCTOR_SOURCE_START = 58.98
DOCTOR_SOURCE_END = 75.22


class RefinementError(RuntimeError):
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
        raise RefinementError(f"JSON file is missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RefinementError(f"Invalid JSON in {path}: {exc}") from exc


def run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RefinementError(result.stderr.strip() or result.stdout.strip() or f"Command failed: {command}")


def normalize_words(text: str) -> list[str]:
    cleaned = "".join(character.lower() if character.isalnum() or character == "'" else " " for character in text)
    return [token for token in cleaned.split() if token]


def transcript_similarity(expected: str, observed: str) -> float:
    return difflib.SequenceMatcher(None, normalize_words(expected), normalize_words(observed)).ratio()


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
        raise RefinementError(f"Empty audio: {path}")
    peak = float(np.max(np.abs(mono)))
    rms = float(np.sqrt(np.mean(np.square(mono), dtype=np.float64)))
    return {
        "sample_rate": int(sample_rate),
        "channels": int(audio.shape[1]),
        "duration_seconds": round(mono.size / sample_rate, 6),
        "peak": round(peak, 6),
        "rms": round(rms, 6),
        "clipping_sample_count": int(np.count_nonzero(np.abs(mono) >= 0.999)),
    }


def evaluate(path: Path, expected: str, whisper_model: Path, *, floor: float) -> dict[str, Any]:
    observed = transcribe(path, whisper_model)
    similarity = transcript_similarity(expected, observed)
    metrics = audio_metrics(path)
    return {
        "audio_path": str(path),
        "audio_sha256": sha256_file(path),
        "verification_transcript": observed,
        "verification_similarity": round(similarity, 6),
        "metrics": metrics,
        "technical_pass": (
            similarity >= floor
            and metrics["sample_rate"] == 24000
            and metrics["channels"] == 1
            and metrics["clipping_sample_count"] == 0
            and metrics["peak"] <= 0.99
        ),
    }


def encode_consistent(source: Path, output: Path, *, fade_in: float = 0.0, fade_out: float = 0.0) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    duration = audio_metrics(source)["duration_seconds"]
    filters = []
    if fade_in > 0:
        filters.append(f"afade=t=in:st=0:d={fade_in:.3f}")
    if fade_out > 0:
        filters.append(f"afade=t=out:st={max(0.0, duration - fade_out):.3f}:d={fade_out:.3f}")
    command = ["ffmpeg", "-v", "error", "-y", "-i", str(source)]
    if filters:
        command.extend(["-af", ",".join(filters)])
    command.extend(["-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(output)])
    run(command)


def raised_segment_mask(length: int, sample_rate: int, segments: list[tuple[float, float]], fade_seconds: float) -> np.ndarray:
    mask = np.zeros(length, dtype=np.float32)
    fade = max(1, int(sample_rate * fade_seconds))
    for start_seconds, end_seconds in segments:
        start = max(0, int(round(start_seconds * sample_rate)))
        end = min(length, int(round(end_seconds * sample_rate)))
        if end <= start:
            continue
        mask[start:end] = 1.0
        fade_in_end = min(end, start + fade)
        if fade_in_end > start:
            mask[start:fade_in_end] = np.maximum(
                mask[start:fade_in_end],
                np.linspace(0.0, 1.0, fade_in_end - start, endpoint=False, dtype=np.float32),
            )
        fade_out_start = max(start, end - fade)
        if end > fade_out_start:
            mask[fade_out_start:end] = np.minimum(
                mask[fade_out_start:end],
                np.linspace(1.0, 0.0, end - fade_out_start, endpoint=False, dtype=np.float32),
            )
    return mask


def refine_benny(selected: Path, output_root: Path) -> tuple[Path, dict[str, Any]]:
    audio, sample_rate = sf.read(selected, dtype="float32", always_2d=True)
    # Source-level word timestamps place the three spoken phrases at these
    # relative intervals. Effects left by the vocal separator occupy the gaps.
    speech_segments = [(0.40, 3.06), (3.58, 4.96), (5.16, 7.94)]
    mask = raised_segment_mask(audio.shape[0], sample_rate, speech_segments, 0.045)
    gated = audio * mask[:, None]
    trim_start = int(round(0.36 * sample_rate))
    trim_end = min(audio.shape[0], int(round(8.04 * sample_rate)))
    gated = gated[trim_start:trim_end]
    intermediate = output_root / "working" / "benny_shock_grief_gap_suppressed.wav"
    intermediate.parent.mkdir(parents=True, exist_ok=True)
    sf.write(intermediate, gated, sample_rate, subtype="PCM_16")
    output = output_root / "refined" / "benny" / f"{BENNY_CLIP_ID}.wav"
    encode_consistent(intermediate, output, fade_in=0.025, fade_out=0.05)
    return output, {
        "method": "word_timed_interphrase_effect_suppression",
        "selected_candidate_start_seconds": 0.36,
        "selected_candidate_end_seconds": 8.04,
        "speech_segments_seconds": speech_segments,
        "gap_gain": 0.0,
        "crossfade_seconds": 0.045,
        "reason": "Reviewer selected FV4 but heard page-turn-like transients in the silent gaps.",
    }


def find_vocal_output(output_dir: Path) -> Path:
    candidates = sorted(output_dir.glob("*Vocals*.wav"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    if not candidates:
        raise RefinementError(f"Audio separator produced no vocal stem in {output_dir}")
    return candidates[0]


def refine_doctor(
    atlas_payload: Any,
    output_root: Path,
    *,
    separator_command: Path,
    model_dir: Path,
) -> tuple[Path, dict[str, Any]]:
    rows = atlas_payload.get("rows") if isinstance(atlas_payload, dict) else None
    if not isinstance(rows, list):
        raise RefinementError("Source atlas rows are missing.")
    row = next((item for item in rows if item.get("clip_id") == DOCTOR_CLIP_ID), None)
    if row is None:
        raise RefinementError(f"Source atlas is missing {DOCTOR_CLIP_ID}")
    source = Path(str(row.get("source_audio") or ""))
    if not source.is_file():
        raise RefinementError(f"Doctor source audio is missing: {source}")
    stereo = output_root / "working" / "doctor_indomitable_complete_entrance.wav"
    stereo.parent.mkdir(parents=True, exist_ok=True)
    run([
        "ffmpeg", "-v", "error", "-y",
        "-ss", f"{DOCTOR_SOURCE_START:.3f}", "-to", f"{DOCTOR_SOURCE_END:.3f}",
        "-i", str(source), "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(stereo),
    ])
    separation_dir = output_root / "working" / "doctor_fv4_separation"
    if separation_dir.exists():
        shutil.rmtree(separation_dir)
    separation_dir.mkdir(parents=True)
    run([
        str(separator_command),
        "-m", DOCTOR_MODEL_FILENAME,
        "--model_file_dir", str(model_dir),
        "--output_dir", str(separation_dir),
        "--output_format", "WAV",
        "--single_stem", "Vocals",
        "--normalization", "0.9",
        str(stereo),
    ])
    vocal = find_vocal_output(separation_dir)
    output = output_root / "refined" / "doctor" / f"{DOCTOR_CLIP_ID}.wav"
    # The repaired source now includes 80 ms of clean pre-roll before the full
    # opening word. A 55 ms fade suppresses the transition without touching it.
    encode_consistent(vocal, output, fade_in=0.055, fade_out=0.08)
    return output, {
        "method": "source_recut_then_fv4_vocal_separation",
        "model_filename": DOCTOR_MODEL_FILENAME,
        "source_start_seconds": DOCTOR_SOURCE_START,
        "source_end_seconds": DOCTOR_SOURCE_END,
        "opening_word_source_start_seconds": 59.06,
        "pre_roll_seconds": round(59.06 - DOCTOR_SOURCE_START, 3),
        "fade_in_seconds": 0.055,
        "reason": "The prior atlas started about 0.48 seconds inside the opening word, creating a damaged entrance.",
    }


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    applied_path = Path(args.applied).expanduser().resolve()
    atlas_path = Path(args.atlas).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    whisper_model = Path(args.whisper_model).expanduser().resolve()
    separator_command = Path(args.separator_command).expanduser().resolve()
    model_dir = Path(args.model_dir).expanduser().resolve()
    if not whisper_model.is_dir():
        raise RefinementError(f"Whisper model is missing: {whisper_model}")
    if not separator_command.is_file():
        raise RefinementError(f"Audio separator command is missing: {separator_command}")
    if not model_dir.is_dir():
        raise RefinementError(f"Audio separator model directory is missing: {model_dir}")
    applied = load_json(applied_path)
    atlas = load_json(atlas_path)
    if applied.get("round_id") != APPLIED_ROUND_ID:
        raise RefinementError(f"Unexpected applied-ledger round_id: {applied.get('round_id')}")
    queue = applied.get("refinement_queue")
    if not isinstance(queue, list) or {row.get("clip_id") for row in queue} != {BENNY_CLIP_ID, DOCTOR_CLIP_ID}:
        raise RefinementError("Applied ledger does not contain the expected two-card refinement queue.")
    by_id = {row["clip_id"]: row for row in queue}
    if output_root.exists() and args.force:
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    rows = []
    benny_selected = Path(by_id[BENNY_CLIP_ID]["audio_path"])
    benny_output, benny_details = refine_benny(benny_selected, output_root)
    benny_evaluation = evaluate(benny_output, by_id[BENNY_CLIP_ID]["selected_transcript"], whisper_model, floor=0.85)
    rows.append({
        **by_id[BENNY_CLIP_ID],
        "refinement_type": "interphrase_transient_suppression",
        "selected_audio_path": str(benny_selected),
        "selected_audio_sha256": sha256_file(benny_selected),
        "refined_audio_path": str(benny_output),
        "refined_audio_sha256": sha256_file(benny_output),
        "refinement_details": benny_details,
        "evaluation": benny_evaluation,
        "technical_pass": benny_evaluation["technical_pass"],
        "production_promotion_allowed": False,
    })

    doctor_selected = Path(by_id[DOCTOR_CLIP_ID]["audio_path"])
    doctor_output, doctor_details = refine_doctor(
        atlas,
        output_root,
        separator_command=separator_command,
        model_dir=model_dir,
    )
    doctor_evaluation = evaluate(doctor_output, by_id[DOCTOR_CLIP_ID]["selected_transcript"], whisper_model, floor=0.88)
    rows.append({
        **by_id[DOCTOR_CLIP_ID],
        "refinement_type": "complete_entrance_reseparation",
        "selected_audio_path": str(doctor_selected),
        "selected_audio_sha256": sha256_file(doctor_selected),
        "refined_audio_path": str(doctor_output),
        "refined_audio_sha256": sha256_file(doctor_output),
        "refinement_details": doctor_details,
        "evaluation": doctor_evaluation,
        "technical_pass": doctor_evaluation["technical_pass"],
        "production_promotion_allowed": False,
    })

    payload = {
        "schema_version": 1,
        "round_id": REFINEMENT_ROUND_ID,
        "created_at": now_iso(),
        "applied_salvage_ledger": str(applied_path),
        "applied_salvage_ledger_sha256": sha256_file(applied_path),
        "source_atlas": str(atlas_path),
        "source_atlas_sha256": sha256_file(atlas_path),
        "candidate_count": len(rows),
        "technical_pass_count": sum(bool(row["technical_pass"]) for row in rows),
        "rows": rows,
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }
    manifest = output_root / "refinement-manifest.json"
    manifest.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "candidate_count": len(rows),
        "technical_pass_count": payload["technical_pass_count"],
        "manifest": str(manifest),
    }


def validate_manifest(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("round_id") != REFINEMENT_ROUND_ID:
        raise RefinementError("Refinement manifest has an unexpected round_id.")
    rows = payload.get("rows")
    if not isinstance(rows, list) or len(rows) != 2:
        raise RefinementError("Refinement manifest must contain exactly two rows.")
    if {row.get("clip_id") for row in rows} != {BENNY_CLIP_ID, DOCTOR_CLIP_ID}:
        raise RefinementError("Refinement manifest contains unexpected clip IDs.")
    for row in rows:
        for path_key, hash_key in (
            ("selected_audio_path", "selected_audio_sha256"),
            ("refined_audio_path", "refined_audio_sha256"),
        ):
            path = Path(str(row.get(path_key) or ""))
            if not path.is_file() or sha256_file(path) != row.get(hash_key):
                raise RefinementError(f"Audio validation failed for {row.get('clip_id')}:{path_key}")
        if row.get("technical_pass") is not True:
            raise RefinementError(f"Technical checks failed for {row.get('clip_id')}")
        if row.get("production_promotion_allowed") is not False:
            raise RefinementError("Refined audio may not auto-promote to production.")
    if payload.get("production_promotion_allowed") is not False:
        raise RefinementError("Refinement manifest may not auto-promote to production.")
    return {
        "candidate_count": len(rows),
        "technical_pass_count": sum(bool(row.get("technical_pass")) for row in rows),
    }


def encode_review_mp3(source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    run([
        "ffmpeg", "-v", "error", "-y", "-i", str(source),
        "-ac", "1", "-ar", "48000", "-c:a", "libmp3lame", "-b:a", "192k", str(output),
    ])


def package(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = Path(args.manifest).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    payload = load_json(manifest_path)
    validate_manifest(payload)
    review = output_root / "review"
    if review.exists():
        shutil.rmtree(review)
    (review / "audio").mkdir(parents=True)
    for name in ("index.html", "styles.css", "app.js"):
        shutil.copy2(ASSET_ROOT / name, review / name)
    shutil.copy2(RANGE_SERVER, review / "serve_review.py")

    public_rows = []
    answer_rows = []
    for ordinal, row in enumerate(payload["rows"], start=1):
        selected = Path(row["selected_audio_path"])
        refined = Path(row["refined_audio_path"])
        selected_mp3 = review / "audio" / f"{row['clip_id']}_selected.mp3"
        refined_mp3 = review / "audio" / f"{row['clip_id']}_refined.mp3"
        encode_review_mp3(selected, selected_mp3)
        encode_review_mp3(refined, refined_mp3)
        public_rows.append({
            "clip_id": row["clip_id"],
            "ordinal": ordinal,
            "target": row["target"],
            "target_label": row["target_label"],
            "source_title": row["source_title"],
            "selected_transcript": row["selected_transcript"],
            "primary_emotion": row["primary_emotion"],
            "dramatic_function": row["dramatic_function"],
            "review_notes": row.get("review_notes"),
            "refinement_type": row["refinement_type"],
            "technical_pass": row["technical_pass"],
            "verification_transcript": row["evaluation"]["verification_transcript"],
            "verification_similarity": row["evaluation"]["verification_similarity"],
            "selected_audio": f"audio/{row['clip_id']}_selected.mp3",
            "refined_audio": f"audio/{row['clip_id']}_refined.mp3",
        })
        answer_rows.append({
            **row,
            "review_selected_audio_sha256": sha256_file(selected_mp3),
            "review_refined_audio_sha256": sha256_file(refined_mp3),
        })

    public = {
        "schema_version": 1,
        "round_id": REVIEW_ROUND_ID,
        "candidate_count": len(public_rows),
        "rows": public_rows,
    }
    (review / "data.js").write_text(
        "window.THREE_VOICE_SELECTED_REFINEMENT_DATA = " + json.dumps(public, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    review_manifest = {
        "schema_version": 1,
        "round_id": REVIEW_ROUND_ID,
        "candidate_count": len(public_rows),
        "selected_vs_refined_comparison": True,
        "maximum_simultaneous_audio_elements": 2,
        "range_server_included": True,
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }
    (review / "manifest.json").write_text(json.dumps(review_manifest, indent=2) + "\n", encoding="utf-8")
    (output_root / "answer-key.json").write_text(json.dumps(answer_rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output_root / "START_HERE.txt").write_text(
        f'cd "{review}"\npython3 serve_review.py --bind 127.0.0.1 --port 8790\n\nThen open http://127.0.0.1:8790/\n',
        encoding="utf-8",
    )
    return {"candidate_count": len(public_rows), "review": str(review / "index.html")}


def validate_package(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.output_root).expanduser().resolve()
    review = root / "review"
    prefix = "window.THREE_VOICE_SELECTED_REFINEMENT_DATA = "
    text = (review / "data.js").read_text(encoding="utf-8").strip()
    if not text.startswith(prefix):
        raise RefinementError("Review data.js has an unexpected prefix.")
    data = json.loads(text[len(prefix):].rstrip(";"))
    if len(data.get("rows") or []) != 2:
        raise RefinementError("Review package must contain exactly two rows.")
    failures = []
    for row in data["rows"]:
        for key in ("selected_audio", "refined_audio"):
            path = review / row[key]
            if not path.is_file():
                failures.append(f"missing:{row['clip_id']}:{key}")
        if not row.get("selected_transcript") or not row.get("primary_emotion"):
            failures.append(f"metadata:{row['clip_id']}")
    if failures:
        raise RefinementError(f"Review package validation failed: {failures}")
    return {"candidate_count": len(data["rows"]), "failure_count": 0, "review": str(review / "index.html")}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare the two reviewer-selected source refinements.")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--applied", required=True)
    prepare_parser.add_argument("--atlas", required=True)
    prepare_parser.add_argument("--output-root", required=True)
    prepare_parser.add_argument("--whisper-model", required=True)
    prepare_parser.add_argument("--separator-command", required=True)
    prepare_parser.add_argument("--model-dir", required=True)
    prepare_parser.add_argument("--force", action="store_true")
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--manifest", required=True)
    package_parser = sub.add_parser("package")
    package_parser.add_argument("--manifest", required=True)
    package_parser.add_argument("--output-root", required=True)
    validate_package_parser = sub.add_parser("validate-package")
    validate_package_parser.add_argument("--output-root", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "prepare":
            result = prepare(args)
        elif args.command == "validate":
            result = validate_manifest(load_json(Path(args.manifest).expanduser().resolve()))
        elif args.command == "package":
            result = package(args)
        else:
            result = validate_package(args)
    except RefinementError as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
