#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import shutil
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf

APPLIED_ROUND_ID = "alexandria_three_voice_source_repair_review_applied_v1"
SALVAGE_ROUND_ID = "alexandria_three_voice_final_salvage_v1"
REVIEW_ROUND_ID = "alexandria_three_voice_final_salvage_review_v1"
ASSET_ROOT = Path(__file__).with_name("three_voice_final_salvage_assets")
RANGE_SERVER = Path(__file__).with_name("range_http_server.py")

SEPARATION_MODELS = {
    "bs317": {
        "filename": "model_bs_roformer_ep_317_sdr_12.9755.ckpt",
        "output_suffix": "model_bs_roformer_ep_317_sdr_12",
        "architecture": "BS-RoFormer",
    },
    "fv4": {
        "filename": "mel_band_roformer_vocals_fv4_gabox.ckpt",
        "output_suffix": "mel_band_roformer_vocals_fv4_gabox",
        "architecture": "MelBand RoFormer FV4",
    },
    "mdx": {
        "filename": "UVR-MDX-NET-Voc_FT.onnx",
        "output_suffix": "UVR-MDX-NET-Voc_FT",
        "architecture": "UVR MDX-Net Voc FT",
    },
}

SEPARATION_CARDS = {
    "benny_shock_grief": {"cache_base": "benny_shock_grief"},
    "doctor_indomitable_determination": {"cache_base": "doctor_indomitable"},
    "doctor_analytical_authority": {"cache_base": "doctor_analytical_authority"},
    "narrator_official_rallying_determination": {"cache_base": "narrator_rallying"},
}

BOUNDARY_SPECS = {
    "narrator_ud_warm_reconciliation": {
        "start": 14280.02,
        "end": 14296.00,
        "transcript": "If you're still with me, why don't we just reset the game, and we'll try to get back to what The Stanley Parable is really about. No frills. No gimmicks. Just you and me having a great time together like always. What do you say, friend?",
        "reason": "Starts immediately before 'If' and keeps 0.28 seconds after the final consonant in 'friend' without reaching the next line.",
    },
    "narrator_ud_creative_insecurity": {
        "start": 4878.54,
        "end": 4888.14,
        "transcript": "Where did I mess up the joke? Should I have paused for longer? Or spoken quicker? Comedic timing is so difficult. I wish I were better at it.",
        "reason": "Preserves the repaired opening and ends after 'it' but before the following 'but' artifact.",
    },
    "narrator_ud_contemptuous_disbelief": {
        "start": 7518.76,
        "end": 7527.16,
        "transcript": "Are you hallucinating? This is a tractor! It's an enormous machine that tills the earth! I thought this was a gimme. How on earth did you manage to screw it up?",
        "reason": "Keeps a 0.36-second tail after 'up' while stopping well before 'Absolutely incredible.'",
    },
    "narrator_ud_bittersweet_nostalgia": {
        "start": 9179.86,
        "end": 9183.82,
        "transcript": "We were so innocent. We'll never be like that again, Stanley.",
        "reason": "Keeps the complete final syllable of 'Stanley' and stops before the next 'Oh yes' line.",
    },
    "narrator_ud_separation_panic": {
        "start": 8915.62,
        "end": 8924.44,
        "transcript": "No, no, no! I'm not done! I'm not ready to move on! Stop the loading screen! Isn't there some way we can stay here? Keep enjoying these figurines?",
        "reason": "Starts at the first 'No' and keeps the full end of 'figurines' without reaching 'Let's just go backwards.'",
    },
}


class SalvageError(RuntimeError):
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
        raise SalvageError(f"JSON file is missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SalvageError(f"Invalid JSON in {path}: {exc}") from exc


def normalize_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.casefold())


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
    if audio.size == 0:
        raise SalvageError(f"Empty audio: {path}")
    mono = audio.mean(axis=1, dtype=np.float32)
    peak = float(np.max(np.abs(mono)))
    rms = float(np.sqrt(np.mean(np.square(mono), dtype=np.float64)))
    clipping = int(np.count_nonzero(np.abs(mono) >= 0.999))
    if audio.shape[1] >= 2:
        mid = (audio[:, 0] + audio[:, 1]) * 0.5
        side = (audio[:, 0] - audio[:, 1]) * 0.5
        mid_side_db = 20.0 * np.log10(
            (float(np.sqrt(np.mean(np.square(mid), dtype=np.float64))) + 1e-9)
            / (float(np.sqrt(np.mean(np.square(side), dtype=np.float64))) + 1e-9)
        )
    else:
        mid_side_db = 99.0
    return {
        "sample_rate": int(sample_rate),
        "channels": int(audio.shape[1]),
        "duration_seconds": round(mono.size / sample_rate, 6),
        "peak": round(peak, 6),
        "rms": round(rms, 6),
        "clipping_sample_count": clipping,
        "mid_side_db": round(float(mid_side_db), 4),
    }


def run_ffmpeg(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise SalvageError(result.stderr.strip() or f"ffmpeg failed: {command}")


def extract_original_stereo(row: dict[str, Any], output: Path) -> None:
    source = Path(str(row.get("source_audio") or ""))
    if not source.is_file():
        raise SalvageError(f"Source audio missing for {row.get('clip_id')}: {source}")
    output.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "ffmpeg", "-v", "error", "-y",
        "-ss", f"{float(row['selected_start_seconds']):.3f}",
        "-to", f"{float(row['selected_end_seconds']):.3f}",
        "-i", str(source), "-ar", "44100", "-c:a", "pcm_s16le", str(output),
    ])


def extract_boundary(source: Path, start: float, end: float, output: Path) -> None:
    temporary = output.with_suffix(".tmp.wav")
    output.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-i", str(source), "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(temporary),
    ])
    audio, sample_rate = sf.read(temporary, dtype="float32", always_2d=True)
    temporary.unlink(missing_ok=True)
    mono = audio.mean(axis=1, dtype=np.float32)
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    if peak > 0.86:
        mono *= 0.86 / peak
    sf.write(output, mono, sample_rate, subtype="PCM_16")


def evaluate(path: Path, expected: str, whisper_model: Path) -> dict[str, Any]:
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


def blind_order(clip_id: str) -> list[str]:
    return sorted(SEPARATION_MODELS, key=lambda key: hashlib.sha256(f"{clip_id}:{key}".encode()).hexdigest())


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    applied_path = Path(args.applied).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    separation_cache = Path(args.separation_cache).expanduser().resolve()
    model_dir = Path(args.model_dir).expanduser().resolve()
    whisper_model = Path(args.whisper_model).expanduser().resolve()
    if not whisper_model.is_dir():
        raise SalvageError(f"Whisper model is missing: {whisper_model}")
    applied = load_json(applied_path)
    if applied.get("round_id") != APPLIED_ROUND_ID:
        raise SalvageError("Applied repair review has an unexpected round_id.")
    if output_root.exists() and args.force:
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    separation_index = {row["clip_id"]: row for row in applied.get("source_separation_queue") or []}
    boundary_index = {row["clip_id"]: row for row in applied.get("boundary_repair_queue") or []}
    if set(SEPARATION_CARDS) - set(separation_index):
        raise SalvageError(f"Missing separation rows: {sorted(set(SEPARATION_CARDS)-set(separation_index))}")
    if set(BOUNDARY_SPECS) - set(boundary_index):
        raise SalvageError(f"Missing boundary rows: {sorted(set(BOUNDARY_SPECS)-set(boundary_index))}")

    model_receipts = {}
    for key, model in SEPARATION_MODELS.items():
        path = model_dir / model["filename"]
        if not path.is_file():
            raise SalvageError(f"Separation model missing: {path}")
        model_receipts[key] = {
            **model,
            "path": str(path),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }

    rows: list[dict[str, Any]] = []
    for clip_id, spec in SEPARATION_CARDS.items():
        source_row = separation_index[clip_id]
        card_root = output_root / "separation" / clip_id
        original = card_root / "original.wav"
        extract_original_stereo(source_row, original)
        original_evaluation = evaluate(original, source_row["selected_transcript"], whisper_model)
        candidates = []
        order = blind_order(clip_id)
        for ordinal, model_key in enumerate(order):
            model = SEPARATION_MODELS[model_key]
            cache_path = separation_cache / model_key / f"{spec['cache_base']}_(Vocals)_{model['output_suffix']}.wav"
            if not cache_path.is_file():
                raise SalvageError(f"Separated candidate missing: {cache_path}")
            destination = card_root / f"candidate_{ordinal + 1}.wav"
            shutil.copy2(cache_path, destination)
            evaluation = evaluate(destination, source_row["selected_transcript"], whisper_model)
            candidates.append({
                "candidate_label": chr(ord("A") + ordinal),
                "model_key": model_key,
                "model_filename": model["filename"],
                "audio_path": str(destination),
                **evaluation,
            })
        rows.append({
            "card_id": f"separation:{clip_id}",
            "card_type": "source_separation",
            "clip_id": clip_id,
            "target": source_row["target"],
            "target_label": source_row["target_label"],
            "source_title": source_row["source_title"],
            "selected_transcript": source_row["selected_transcript"],
            "primary_emotion": source_row["primary_emotion"],
            "dramatic_function": source_row["dramatic_function"],
            "review_notes": source_row.get("review_notes"),
            "original": original_evaluation,
            "candidates": candidates,
            "production_promotion_allowed": False,
        })

    for clip_id, spec in BOUNDARY_SPECS.items():
        source_row = boundary_index[clip_id]
        source = Path(str(source_row.get("source_audio") or ""))
        if not source.is_file():
            raise SalvageError(f"Boundary source missing: {source}")
        destination = output_root / "boundary" / f"{clip_id}.wav"
        extract_boundary(source, spec["start"], spec["end"], destination)
        evaluation = evaluate(destination, spec["transcript"], whisper_model)
        rows.append({
            "card_id": f"boundary:{clip_id}",
            "card_type": "boundary_final",
            "clip_id": clip_id,
            "target": source_row["target"],
            "target_label": source_row["target_label"],
            "source_title": source_row["source_title"],
            "selected_transcript": spec["transcript"],
            "primary_emotion": source_row["primary_emotion"],
            "dramatic_function": source_row["dramatic_function"],
            "review_notes": source_row.get("review_notes"),
            "boundary_reason": spec["reason"],
            "absolute_start_seconds": spec["start"],
            "absolute_end_seconds": spec["end"],
            "previous_audio_path": source_row["repaired_audio_path"],
            "previous_audio_sha256": source_row["repaired_audio_sha256"],
            "final": evaluation,
            "production_promotion_allowed": False,
        })

    payload = {
        "schema_version": 1,
        "round_id": SALVAGE_ROUND_ID,
        "created_at": now_iso(),
        "source_applied_ledger": str(applied_path),
        "source_applied_ledger_sha256": sha256_file(applied_path),
        "card_count": len(rows),
        "card_type_counts": dict(sorted(Counter(row["card_type"] for row in rows).items())),
        "technical_pass_count": sum(
            all(candidate["technical_pass"] for candidate in row["candidates"])
            if row["card_type"] == "source_separation" else bool(row["final"]["technical_pass"])
            for row in rows
        ),
        "separation_models": model_receipts,
        "rows": rows,
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }
    manifest = output_root / "salvage-manifest.json"
    manifest.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"card_count": len(rows), "card_type_counts": payload["card_type_counts"], "manifest": str(manifest)}


def validate_manifest(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("round_id") != SALVAGE_ROUND_ID:
        raise SalvageError("Salvage manifest has an unexpected round_id.")
    rows = payload.get("rows")
    if not isinstance(rows, list) or len(rows) != payload.get("card_count"):
        raise SalvageError("Salvage card count mismatch.")
    failures = []
    seen = set()
    for row in rows:
        card_id = row.get("card_id")
        if card_id in seen:
            failures.append(f"duplicate:{card_id}")
        seen.add(card_id)
        if row.get("production_promotion_allowed") is not False:
            failures.append(f"promotion:{card_id}")
        if row.get("card_type") == "source_separation":
            if len(row.get("candidates") or []) != 3:
                failures.append(f"candidate_count:{card_id}")
            paths = [row.get("original", {}).get("audio_path")] + [item.get("audio_path") for item in row.get("candidates") or []]
        else:
            paths = [row.get("previous_audio_path"), row.get("final", {}).get("audio_path")]
        for value in paths:
            path = Path(str(value or ""))
            if not path.is_file():
                failures.append(f"missing:{card_id}:{path}")
    if payload.get("automatic_production_assignment") is not False or payload.get("production_promotion_allowed") is not False:
        failures.append("global_promotion")
    if failures:
        raise SalvageError(f"Salvage validation failed: {failures}")
    return {
        "card_count": len(rows),
        "card_type_counts": dict(sorted(Counter(row["card_type"] for row in rows).items())),
        "failure_count": 0,
    }


def encode_mp3(source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "ffmpeg", "-v", "error", "-y", "-i", str(source),
        "-ar", "48000", "-c:a", "libmp3lame", "-b:a", "192k", str(output),
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
        common = {
            "card_id": row["card_id"],
            "ordinal": ordinal,
            "card_type": row["card_type"],
            "clip_id": row["clip_id"],
            "target": row["target"],
            "target_label": row["target_label"],
            "source_title": row["source_title"],
            "selected_transcript": row["selected_transcript"],
            "primary_emotion": row["primary_emotion"],
            "dramatic_function": row["dramatic_function"],
            "review_notes": row.get("review_notes"),
        }
        if row["card_type"] == "source_separation":
            original_mp3 = review / "audio" / f"{row['clip_id']}_original.mp3"
            encode_mp3(Path(row["original"]["audio_path"]), original_mp3)
            public_candidates = []
            for candidate in row["candidates"]:
                candidate_mp3 = review / "audio" / f"{row['clip_id']}_{candidate['candidate_label']}.mp3"
                encode_mp3(Path(candidate["audio_path"]), candidate_mp3)
                public_candidates.append({
                    "candidate_label": candidate["candidate_label"],
                    "audio": f"audio/{candidate_mp3.name}",
                    "technical_pass": candidate["technical_pass"],
                    "verification_similarity": candidate["verification_similarity"],
                })
            public_rows.append({
                **common,
                "original_audio": f"audio/{original_mp3.name}",
                "candidates": public_candidates,
            })
        else:
            previous_mp3 = review / "audio" / f"{row['clip_id']}_previous.mp3"
            final_mp3 = review / "audio" / f"{row['clip_id']}_final.mp3"
            encode_mp3(Path(row["previous_audio_path"]), previous_mp3)
            encode_mp3(Path(row["final"]["audio_path"]), final_mp3)
            public_rows.append({
                **common,
                "boundary_reason": row["boundary_reason"],
                "previous_audio": f"audio/{previous_mp3.name}",
                "final_audio": f"audio/{final_mp3.name}",
                "technical_pass": row["final"]["technical_pass"],
                "verification_similarity": row["final"]["verification_similarity"],
            })
        answer_rows.append(row)

    public = {
        "schema_version": 1,
        "round_id": REVIEW_ROUND_ID,
        "title": "Final Three-Voice Salvage Gate",
        "card_count": len(public_rows),
        "card_type_counts": dict(sorted(Counter(row["card_type"] for row in public_rows).items())),
        "rows": public_rows,
    }
    (review / "data.js").write_text("window.THREE_VOICE_FINAL_SALVAGE_DATA = " + json.dumps(public, ensure_ascii=False) + ";\n", encoding="utf-8")
    review_manifest = {
        "schema_version": 1,
        "round_id": REVIEW_ROUND_ID,
        "card_count": len(public_rows),
        "card_type_counts": public["card_type_counts"],
        "model_names_blinded": True,
        "maximum_simultaneous_audio_elements": 4,
        "range_server_included": True,
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }
    (review / "manifest.json").write_text(json.dumps(review_manifest, indent=2) + "\n", encoding="utf-8")
    (output_root / "answer-key.json").write_text(json.dumps(answer_rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output_root / "START_HERE.txt").write_text(
        f'cd "{review}"\npython3 serve_review.py --bind 127.0.0.1 --port 8789\n\nThen open http://127.0.0.1:8789/\n',
        encoding="utf-8",
    )
    return {"card_count": len(public_rows), "review": str(review / "index.html")}


def validate_package(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.output_root).expanduser().resolve()
    review = root / "review"
    prefix = "window.THREE_VOICE_FINAL_SALVAGE_DATA = "
    text = (review / "data.js").read_text(encoding="utf-8").strip()
    if not text.startswith(prefix):
        raise SalvageError("Review data.js prefix is invalid.")
    data = json.loads(text[len(prefix):].rstrip(";"))
    failures = []
    for row in data["rows"]:
        if row["card_type"] == "source_separation":
            paths = [row["original_audio"]] + [item["audio"] for item in row["candidates"]]
            if len(row["candidates"]) != 3:
                failures.append(f"candidates:{row['card_id']}")
        else:
            paths = [row["previous_audio"], row["final_audio"]]
        for relative in paths:
            path = review / relative
            if not path.is_file():
                failures.append(f"missing:{row['card_id']}:{relative}")
    if failures:
        raise SalvageError(f"Review package validation failed: {failures}")
    return {"card_count": len(data["rows"]), "failure_count": 0, "review": str(review / "index.html")}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the final small source-separation and boundary salvage gate.")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--applied", required=True)
    prepare_parser.add_argument("--output-root", required=True)
    prepare_parser.add_argument("--separation-cache", required=True)
    prepare_parser.add_argument("--model-dir", required=True)
    prepare_parser.add_argument("--whisper-model", required=True)
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
            result = validate_manifest(load_json(Path(args.manifest).expanduser().resolve()))
        elif args.command == "package":
            result = package(args)
        else:
            result = validate_package(args)
    except (SalvageError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
