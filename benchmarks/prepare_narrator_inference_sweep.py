from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import math
import os
import re
import shutil
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

import librosa
import mlx_whisper
import numpy as np
import soundfile as sf

from mlx_backend import MLXBackend


ROUND_ID = "alexandria_narrator_inference_sweep_v1"
ASSET_ROOT = Path(__file__).with_name("narrator_rescue_review_assets")
TEMPERATURES = (0.5, 0.7, 0.9)
SEEDS = (20260723, 20260724, 20260725)
CANDIDATES_PER_STYLE = 4
REVIEW_FIELDS = [
    "identity_1_to_5",
    "delivery_1_to_5",
    "naturalness_1_to_5",
    "artifact_severity_1_to_5",
    "spoken_text_matches_expected",
    "requested_mode_is_clear",
    "approve_for_comparison",
    "flag_for_follow_up",
    "notes",
]


class SweepError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(value: str, length: int = 16) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def normalized_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", str(value).casefold()))


def final_word(value: str) -> str:
    tokens = normalized_text(value).split()
    return tokens[-1] if tokens else ""


def word_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def text_similarity(expected: str, observed: str) -> float:
    expected_norm = normalized_text(expected)
    observed_norm = normalized_text(observed)
    sequence = SequenceMatcher(None, expected_norm, observed_norm).ratio()
    expected_tokens = set(expected_norm.split())
    observed_tokens = set(observed_norm.split())
    coverage = (
        len(expected_tokens & observed_tokens) / max(1, len(expected_tokens))
    )
    return 0.65 * sequence + 0.35 * coverage


def load_round2_data(round2_root: Path) -> dict[str, Any]:
    data_path = round2_root / "review" / "data.js"
    prefix = "window.ALEXANDRIA_NARRATOR_RESCUE_DATA = "
    text = data_path.read_text(encoding="utf-8").strip()
    if not text.startswith(prefix):
        raise SweepError("Round 2 public data has an unsupported format.")
    return json.loads(text[len(prefix) :].rstrip(";"))


def copy_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise SweepError(f"Missing source file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def identity_only_pitch_by_style(round2_root: Path) -> dict[str, float]:
    answer_key = read_json(round2_root / "answer-key.json")
    result: dict[str, float] = {}
    for row in answer_key:
        if row.get("candidate_key") != "qwen_lora_narrator_attention_r8":
            continue
        path = round2_root / "review" / "audio" / f"{row['sample_id']}.wav"
        result[row["style"]] = pitch_metrics(path)["median_f0_hz"]
    return result


def pitch_metrics(path: Path) -> dict[str, float]:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = np.mean(audio, axis=1, dtype=np.float32)
    f0, _, _ = librosa.pyin(
        mono,
        fmin=65.0,
        fmax=500.0,
        sr=sample_rate,
        frame_length=1024,
        hop_length=256,
    )
    values = f0[np.isfinite(f0)]
    if values.size == 0:
        return {"median_f0_hz": 0.0, "p90_f0_hz": 0.0, "voiced_ratio": 0.0}
    return {
        "median_f0_hz": float(np.median(values)),
        "p90_f0_hz": float(np.percentile(values, 90)),
        "voiced_ratio": float(values.size / max(1, f0.size)),
    }


def waveform_metrics(path: Path) -> dict[str, float]:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = np.mean(audio, axis=1, dtype=np.float32)
    duration = len(mono) / float(sample_rate)
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(mono)))) if mono.size else 0.0
    tail_count = min(len(mono), int(round(sample_rate * 0.2)))
    tail = mono[-tail_count:] if tail_count else mono
    tail_rms = float(np.sqrt(np.mean(np.square(tail)))) if tail.size else 0.0
    clip_fraction = float(np.mean(np.abs(mono) >= 0.995)) if mono.size else 1.0
    return {
        "sample_rate": int(sample_rate),
        "channels": 1,
        "duration_seconds": duration,
        "peak_dbfs": 20.0 * math.log10(max(peak, 1e-8)),
        "rms_dbfs": 20.0 * math.log10(max(rms, 1e-8)),
        "tail_rms_dbfs": 20.0 * math.log10(max(tail_rms, 1e-8)),
        "clip_fraction": clip_fraction,
    }


def transcribe(path: Path, whisper_model: Path) -> str:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        result = mlx_whisper.transcribe(
            str(path),
            path_or_hf_repo=str(whisper_model),
            language="en",
            word_timestamps=False,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
            logprob_threshold=-1.0,
            compression_ratio_threshold=2.4,
            verbose=False,
        )
    return str(result.get("text") or "").strip()


def generate_grid(
    *,
    styles: list[dict[str, Any]],
    model_path: Path,
    output_root: Path,
    force: bool,
) -> list[dict[str, Any]]:
    ref_audio = model_path / "ref_sample.wav"
    ref_text_path = model_path / "ref_sample.txt"
    if not ref_audio.is_file() or not ref_text_path.is_file():
        raise SweepError("The exported model is missing its reference contract.")
    ref_text = ref_text_path.read_text(encoding="utf-8").strip()
    backend = MLXBackend(language="English")
    receipts: list[dict[str, Any]] = []
    for style in styles:
        style_key = style["key"]
        for temperature in TEMPERATURES:
            for seed in SEEDS:
                key = f"{style_key}-t{temperature:.1f}-s{seed}"
                audio_path = output_root / "generated" / f"{key}.wav"
                receipt_path = output_root / "generation-receipts" / f"{key}.json"
                if audio_path.is_file() and receipt_path.is_file() and not force:
                    receipt = read_json(receipt_path)
                    if receipt.get("audio_sha256") == sha256_file(audio_path):
                        receipts.append(receipt)
                        continue
                audio_path.parent.mkdir(parents=True, exist_ok=True)
                started = time.perf_counter()
                ok = backend.generate_merged_lora_clone(
                    text=style["target_text"],
                    ref_audio=str(ref_audio),
                    ref_text=ref_text,
                    instruct=style["instruction"],
                    model_path=str(model_path),
                    output_path=str(audio_path),
                    temperature=temperature,
                    top_k=50,
                    top_p=0.95,
                    repetition_penalty=1.5,
                    max_tokens=2400,
                    seed=seed,
                )
                if not ok or not audio_path.is_file():
                    raise SweepError(f"Generation failed for {key}.")
                receipt = {
                    "style": style_key,
                    "style_label": style["label"],
                    "target_text": style["target_text"],
                    "instruction": style["instruction"],
                    "temperature": temperature,
                    "seed": seed,
                    "audio_path": str(audio_path),
                    "audio_sha256": sha256_file(audio_path),
                    "elapsed_seconds": round(time.perf_counter() - started, 4),
                }
                write_json(receipt_path, receipt)
                receipts.append(receipt)
    backend.release_models_manually()
    return receipts


def analyze_grid(
    *,
    rows: list[dict[str, Any]],
    whisper_model: Path,
    baseline_pitch: dict[str, float],
    output_root: Path,
    force: bool,
) -> list[dict[str, Any]]:
    analyzed: list[dict[str, Any]] = []
    for row in rows:
        path = Path(row["audio_path"])
        analysis_path = output_root / "analysis" / (
            f"{row['style']}-t{row['temperature']:.1f}-s{row['seed']}.json"
        )
        if analysis_path.is_file() and not force:
            existing = read_json(analysis_path)
            if existing.get("audio_sha256") == row["audio_sha256"]:
                analyzed.append(existing)
                continue
        observed = transcribe(path, whisper_model)
        waveform = waveform_metrics(path)
        pitch = pitch_metrics(path)
        expected_last = final_word(row["target_text"])
        observed_last = final_word(observed)
        similarity = text_similarity(row["target_text"], observed)
        end_similarity = word_similarity(expected_last, observed_last)
        reference_pitch = max(1.0, baseline_pitch.get(row["style"], 100.0))
        pitch_ratio = pitch["median_f0_hz"] / reference_pitch
        severe_pitch_anomaly = (
            pitch["median_f0_hz"] <= 0
            or pitch_ratio < 0.4
            or pitch_ratio > 3.0
            or (
                row["style"] == "neutral"
                and (pitch_ratio < 0.6 or pitch_ratio > 1.55)
            )
        )
        text_pass = similarity >= 0.80
        end_pass = end_similarity >= 0.55
        clipping_pass = waveform["clip_fraction"] <= 0.002
        hard_pass = text_pass and end_pass and clipping_pass and not severe_pitch_anomaly
        pitch_score = max(0.0, 1.0 - min(abs(math.log2(max(pitch_ratio, 1e-4))) / 2.0, 1.0))
        score = (
            similarity * 5.0
            + end_similarity * 1.5
            + pitch_score
            + (1.0 if clipping_pass else 0.0)
            + min(waveform["duration_seconds"] / 5.0, 1.0) * 0.5
        )
        result = {
            **row,
            "observed_text": observed,
            "text_similarity": round(similarity, 4),
            "expected_final_word": expected_last,
            "observed_final_word": observed_last,
            "final_word_similarity": round(end_similarity, 4),
            "baseline_pitch_hz": round(reference_pitch, 3),
            "pitch_ratio_to_identity_baseline": round(pitch_ratio, 4),
            "severe_pitch_anomaly": severe_pitch_anomaly,
            "text_pass": text_pass,
            "end_pass": end_pass,
            "clipping_pass": clipping_pass,
            "hard_pass": hard_pass,
            "screening_score": round(score, 4),
            "waveform": {key: round(value, 6) for key, value in waveform.items()},
            "pitch": {key: round(value, 4) for key, value in pitch.items()},
        }
        write_json(analysis_path, result)
        analyzed.append(result)
    return analyzed


def choose_review_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for style in sorted({row["style"] for row in rows}):
        style_rows = [row for row in rows if row["style"] == style]
        chosen_ids: set[tuple[float, int]] = set()
        for temperature in TEMPERATURES:
            options = [row for row in style_rows if row["temperature"] == temperature]
            options.sort(key=lambda item: (item["hard_pass"], item["screening_score"]), reverse=True)
            winner = options[0]
            selected.append(winner)
            chosen_ids.add((winner["temperature"], winner["seed"]))
        pass_counts = {
            temperature: sum(
                row["hard_pass"]
                for row in style_rows
                if row["temperature"] == temperature
            )
            for temperature in TEMPERATURES
        }
        reliable_temperature = max(
            TEMPERATURES,
            key=lambda temperature: (
                pass_counts[temperature],
                max(
                    row["screening_score"]
                    for row in style_rows
                    if row["temperature"] == temperature
                ),
            ),
        )
        remaining = [
            row
            for row in style_rows
            if (row["temperature"], row["seed"]) not in chosen_ids
        ]
        remaining.sort(
            key=lambda item: (
                item["temperature"] == reliable_temperature,
                item["hard_pass"],
                item["screening_score"],
            ),
            reverse=True,
        )
        selected.append(remaining[0])
    return selected


def package_review(
    *,
    styles: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    all_rows: list[dict[str, Any]],
    round2_root: Path,
    output_root: Path,
) -> dict[str, Any]:
    review_root = output_root / "review"
    if review_root.exists():
        shutil.rmtree(review_root)
    (review_root / "audio").mkdir(parents=True)
    (review_root / "reference-audio").mkdir(parents=True)
    for asset in ("index.html", "styles.css", "app.js"):
        copy_file(ASSET_ROOT / asset, review_root / asset)
    app_path = review_root / "app.js"
    app_path.write_text(
        app_path.read_text(encoding="utf-8")
        .replace(
            "alexandria:narrator-rescue:round2:",
            "alexandria:narrator-inference:sweep:",
        )
        .replace(
            "alexandria_narrator_rescue_round2_",
            "alexandria_narrator_inference_sweep_",
        ),
        encoding="utf-8",
    )
    copy_file(
        round2_root / "review" / "reference-audio" / "narrator-original.mp3",
        review_root / "reference-audio" / "narrator-original.mp3",
    )
    copy_file(
        round2_root / "review" / "reference-audio" / "narrator-conditioning.wav",
        review_root / "reference-audio" / "narrator-conditioning.wav",
    )
    style_by_key = {style["key"]: style for style in styles}
    public_samples: list[dict[str, Any]] = []
    answer_rows: list[dict[str, Any]] = []
    for style_key in style_by_key:
        candidates = [row for row in selected if row["style"] == style_key]
        candidates.sort(
            key=lambda row: hashlib.sha256(
                f"{ROUND_ID}|order|{style_key}|{row['temperature']}|{row['seed']}".encode()
            ).hexdigest()
        )
        for row in candidates:
            sample_id = fingerprint(
                f"{ROUND_ID}|{style_key}|{row['temperature']}|{row['seed']}|{row['audio_sha256']}"
            )
            target = review_root / "audio" / f"{sample_id}.wav"
            copy_file(Path(row["audio_path"]), target)
            public_samples.append(
                {
                    "sample_id": sample_id,
                    "style": style_key,
                    "style_label": style_by_key[style_key]["label"],
                    "expected_identity": "Narrator",
                    "target_text": row["target_text"],
                    "requested_instruction": row["instruction"],
                    "audio": f"audio/{sample_id}.wav",
                    "audio_sha256": sha256_file(target),
                    "status": "ready",
                }
            )
            answer_rows.append(
                {
                    "sample_id": sample_id,
                    "style": style_key,
                    "temperature": row["temperature"],
                    "seed": row["seed"],
                    "audio_sha256": row["audio_sha256"],
                    "hard_pass": row["hard_pass"],
                    "screening_score": row["screening_score"],
                    "observed_text": row["observed_text"],
                    "text_similarity": row["text_similarity"],
                    "final_word_similarity": row["final_word_similarity"],
                    "pitch_ratio_to_identity_baseline": row[
                        "pitch_ratio_to_identity_baseline"
                    ],
                    "severe_pitch_anomaly": row["severe_pitch_anomaly"],
                    "waveform": row["waveform"],
                    "pitch": row["pitch"],
                }
            )
    original_data = load_round2_data(round2_root)
    public_data = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "title": "Alexandria Narrator — Deterministic Inference Sweep",
        "identity": original_data["identity"],
        "styles": styles,
        "style_order": [style["key"] for style in styles],
        "samples": public_samples,
        "review_fields": REVIEW_FIELDS,
        "candidate_count": len(public_samples),
        "production_promotion_allowed": False,
    }
    (review_root / "data.js").write_text(
        "window.ALEXANDRIA_NARRATOR_RESCUE_DATA = "
        + json.dumps(public_data, ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )
    write_json(output_root / "answer-key.json", answer_rows)
    pass_summary = {
        style_key: {
            f"{temperature:.1f}": {
                "pass_count": sum(
                    row["hard_pass"]
                    for row in all_rows
                    if row["style"] == style_key
                    and row["temperature"] == temperature
                ),
                "sample_count": len(SEEDS),
            }
            for temperature in TEMPERATURES
        }
        for style_key in style_by_key
    }
    write_json(
        output_root / "sweep-summary.json",
        {
            "schema_version": 1,
            "round_id": ROUND_ID,
            "created_at": now_iso(),
            "temperatures": TEMPERATURES,
            "seeds": SEEDS,
            "generated_count": len(all_rows),
            "selected_count": len(public_samples),
            "automatic_pass_counts": pass_summary,
        },
    )
    manifest = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "review": "index.html",
        "style_count": len(styles),
        "candidate_count": len(public_samples),
        "candidate_counts_by_style": {
            style["key"]: sum(
                sample["style"] == style["key"] for sample in public_samples
            )
            for style in styles
        },
        "parameter_names_public": False,
        "answer_key_outside_review_root": True,
        "autosave": True,
        "production_promotion_allowed": False,
    }
    write_json(review_root / "manifest.json", manifest)
    (review_root / "START_HERE.txt").write_text(
        "Alexandria Narrator Deterministic Inference Sweep\n"
        "=================================================\n\n"
        "This review contains four instruction-trained LoRA candidates per style.\n"
        "Generation settings are intentionally hidden until deblinding.\n\n"
        "Run:\n"
        "  python3 -m http.server 8773 --bind 127.0.0.1\n\n"
        "Open:\n"
        "  http://127.0.0.1:8773/\n",
        encoding="utf-8",
    )
    return {
        "review": str(review_root / "index.html"),
        "generated_count": len(all_rows),
        "selected_count": len(public_samples),
        "automatic_pass_counts": pass_summary,
    }


def validate(output_root: Path) -> dict[str, Any]:
    review_root = output_root / "review"
    manifest = read_json(review_root / "manifest.json")
    answer_key = read_json(output_root / "answer-key.json")
    missing: list[str] = []
    bad_hash: list[str] = []
    for row in answer_key:
        audio = review_root / "audio" / f"{row['sample_id']}.wav"
        if not audio.is_file():
            missing.append(row["sample_id"])
        elif sha256_file(audio) != row["audio_sha256"]:
            bad_hash.append(row["sample_id"])
    public_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in review_root.iterdir()
        if path.is_file()
    )
    leaks = [
        token
        for token in ("temperature", "seed", "answer-key.json", "Qwen3-TTS")
        if token.casefold() in public_text.casefold()
    ]
    if missing or bad_hash or leaks:
        raise SweepError(
            f"Validation failed: missing={missing}, bad_hash={bad_hash}, leaks={leaks}"
        )
    return {
        "round_id": manifest["round_id"],
        "candidate_count": manifest["candidate_count"],
        "style_count": manifest["style_count"],
        "missing_count": len(missing),
        "bad_hash_count": len(bad_hash),
        "parameter_leak_count": len(leaks),
        "review": str(review_root / "index.html"),
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    round2_root = Path(args.round2_root).expanduser().resolve()
    model_path = Path(args.model_path).expanduser().resolve()
    whisper_model = Path(args.whisper_model).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    round2_data = load_round2_data(round2_root)
    styles = round2_data["styles"]
    baseline_pitch = identity_only_pitch_by_style(round2_root)
    generated = generate_grid(
        styles=styles,
        model_path=model_path,
        output_root=output_root,
        force=args.force_generation,
    )
    analyzed = analyze_grid(
        rows=generated,
        whisper_model=whisper_model,
        baseline_pitch=baseline_pitch,
        output_root=output_root,
        force=args.force_analysis,
    )
    selected = choose_review_rows(analyzed)
    result = package_review(
        styles=styles,
        selected=selected,
        all_rows=analyzed,
        round2_root=round2_root,
        output_root=output_root,
    )
    return {**result, "validation": validate(output_root)}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Generate, screen, and package a deterministic Narrator inference sweep."
    )
    sub = result.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--round2-root", required=True)
    build_parser.add_argument("--model-path", required=True)
    build_parser.add_argument("--whisper-model", required=True)
    build_parser.add_argument("--output-root", required=True)
    build_parser.add_argument("--force-generation", action="store_true")
    build_parser.add_argument("--force-analysis", action="store_true")
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--output-root", required=True)
    return result


def main(argv: Iterable[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "build":
            result = build(args)
        else:
            result = validate(Path(args.output_root).expanduser().resolve())
    except (SweepError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}")
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
