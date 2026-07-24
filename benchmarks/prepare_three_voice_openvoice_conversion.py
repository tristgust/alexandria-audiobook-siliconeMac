#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf

ROUND_ID = "alexandria_three_voice_openvoice_conversion_v1"
ASSET_ROOT = Path(__file__).with_name("three_voice_openvoice_assets")
TARGET_ORDER = ("narrator", "benny", "doctor")
MODE_ORDER = ("calm", "pleading", "angry")
ANCHOR_STRATEGIES = ("conditioning", "source", "combined")
MODE_LABELS = {
    "calm": "Calm reassurance",
    "pleading": "Wounded pleading",
    "angry": "Controlled anger",
}
DONOR_SAMPLE_IDS = {
    "calm": "d4f81f89d250626b",
    "pleading": "e3e1a4136ce098fb",
    "angry": "69139e1777b30993",
}
REVIEW_FIELDS = (
    "identity_1_to_5",
    "delivery_1_to_5",
    "naturalness_1_to_5",
    "artifact_severity_1_to_5",
    "spoken_text_matches_expected",
    "performance_is_preserved",
    "approve_for_candidate",
    "notes",
)


class ConversionError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(value: Any, length: int = 16) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def normalize_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", str(value).casefold()))


def text_similarity(expected: str, actual: str) -> float:
    return SequenceMatcher(None, normalize_text(expected), normalize_text(actual)).ratio()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_audio(path: Path) -> tuple[np.ndarray, int]:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = np.mean(audio, axis=1, dtype=np.float32)
    if not mono.size:
        raise ConversionError(f"Audio is empty: {path}")
    return mono, int(sample_rate)


def frame_rms(audio: np.ndarray, frame: int = 1024, hop: int = 256) -> np.ndarray:
    if len(audio) < frame:
        return np.asarray([float(np.sqrt(np.mean(audio * audio)))], dtype=np.float32)
    return np.asarray(
        [
            float(np.sqrt(np.mean(audio[start : start + frame] ** 2)))
            for start in range(0, len(audio) - frame + 1, hop)
        ],
        dtype=np.float32,
    )


def pitch_track(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    frame = max(512, int(round(sample_rate * 0.04)))
    hop = max(128, int(round(sample_rate * 0.01)))
    low_lag = max(1, int(sample_rate / 500.0))
    high_lag = max(low_lag + 1, int(sample_rate / 55.0))
    values: list[float] = []
    window = np.hanning(frame).astype(np.float32)
    for start in range(0, max(1, len(audio) - frame + 1), hop):
        chunk = audio[start : start + frame]
        if len(chunk) < frame:
            chunk = np.pad(chunk, (0, frame - len(chunk)))
        chunk = (chunk - float(np.mean(chunk))) * window
        rms = float(np.sqrt(np.mean(chunk * chunk)))
        if rms < 0.006:
            values.append(float("nan"))
            continue
        correlation = np.correlate(chunk, chunk, mode="full")[frame - 1 :]
        upper = min(high_lag, len(correlation) - 1)
        if upper <= low_lag:
            values.append(float("nan"))
            continue
        region = correlation[low_lag : upper + 1]
        lag = int(np.argmax(region)) + low_lag
        confidence = float(correlation[lag] / max(float(correlation[0]), 1e-9))
        values.append(sample_rate / lag if confidence >= 0.28 else float("nan"))
    return np.asarray(values, dtype=np.float32)


def audio_metrics(path: Path, word_count: int) -> dict[str, Any]:
    audio, sample_rate = load_audio(path)
    duration = len(audio) / sample_rate
    rms = float(np.sqrt(np.mean(audio * audio)))
    peak = float(np.max(np.abs(audio)))
    pitches = pitch_track(audio, sample_rate)
    finite = pitches[np.isfinite(pitches)]
    thirds: list[float] = []
    for part in np.array_split(pitches, 3):
        part = part[np.isfinite(part)]
        thirds.append(float(np.median(part)) if part.size else 0.0)
    ratio = thirds[-1] / thirds[0] if thirds[0] > 0 and thirds[-1] > 0 else 1.0
    values = frame_rms(audio)
    tail = audio[-max(1, int(round(sample_rate * 0.08))) :]
    tail_rms = float(np.sqrt(np.mean(tail * tail)))
    return {
        "duration_seconds": duration,
        "sample_rate": sample_rate,
        "channels": 1,
        "rms_dbfs": 20.0 * math.log10(max(rms, 1e-9)),
        "peak_dbfs": 20.0 * math.log10(max(peak, 1e-9)),
        "words_per_second": word_count / max(duration, 0.01),
        "pitch_median_hz": float(np.median(finite)) if finite.size else 0.0,
        "pitch_p10_hz": float(np.percentile(finite, 10)) if finite.size else 0.0,
        "pitch_p90_hz": float(np.percentile(finite, 90)) if finite.size else 0.0,
        "pitch_thirds_hz": thirds,
        "pitch_end_start_ratio": ratio,
        "pitch_trajectory_anomaly": ratio > 1.75 or ratio < 0.45,
        "voiced_pitch_fraction": float(finite.size / max(1, pitches.size)),
        "tail_rms_dbfs": 20.0 * math.log10(max(tail_rms, 1e-9)),
        "clipping_fraction": float(np.mean(np.abs(audio) >= 0.999)),
        "dynamic_db": 20.0
        * math.log10(
            max(float(np.percentile(values, 90)), 1e-8)
            / max(float(np.percentile(values, 20)), 1e-8)
        ),
    }


def normalized_pitch_shape(metrics: dict[str, Any]) -> np.ndarray:
    values = np.asarray(metrics["pitch_thirds_hz"], dtype=np.float32)
    positive = values[values > 0]
    if positive.size == 0:
        return np.ones(3, dtype=np.float32)
    return values / max(float(np.median(positive)), 1e-6)


def pitch_shape_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    distance = float(np.mean(np.abs(normalized_pitch_shape(left) - normalized_pitch_shape(right))))
    return max(0.0, 1.0 - distance / 1.25)


def cosine_torch(left: Any, right: Any) -> float:
    import torch

    a = left.detach().float().reshape(-1)
    b = right.detach().float().reshape(-1)
    return float(torch.nn.functional.cosine_similarity(a, b, dim=0).item())


def identity_rows(manifest_path: Path) -> dict[str, dict[str, Any]]:
    manifest = load_json(manifest_path)
    result: dict[str, dict[str, Any]] = {}
    for item in manifest.get("identities") or []:
        key = str(item.get("identity_key") or "")
        if key not in TARGET_ORDER:
            continue
        conditioning_audio = (manifest_path.parent / item["conditioning_file"]).resolve()
        source_audio = (manifest_path.parent / item["source_file"]).resolve()
        for audio in (conditioning_audio, source_audio):
            if not audio.is_file():
                raise ConversionError(f"Identity reference is missing: {audio}")
        result[key] = {
            "key": key,
            "label": item.get("label") or key.title(),
            "audio": conditioning_audio,
            "conditioning_audio": conditioning_audio,
            "source_audio": source_audio,
            "transcript": item.get("conditioning_transcript") or "",
        }
    if set(result) != set(TARGET_ORDER):
        raise ConversionError(f"Identity manifest is incomplete: {sorted(result)}")
    return result


def donor_rows(review_path: Path) -> dict[str, dict[str, Any]]:
    payload = load_json(review_path)
    rows = payload if isinstance(payload, list) else payload.get("rows") or payload.get("samples") or []
    by_id = {str(row.get("sample_id")): row for row in rows}
    result: dict[str, dict[str, Any]] = {}
    for mode, sample_id in DONOR_SAMPLE_IDS.items():
        row = by_id.get(sample_id)
        if row is None:
            raise ConversionError(f"Donor sample is missing: {sample_id}")
        audio = (review_path.parent / row["file"]).resolve()
        if not audio.is_file():
            raise ConversionError(f"Donor audio is missing: {audio}")
        result[mode] = {
            "mode": mode,
            "label": MODE_LABELS[mode],
            "sample_id": sample_id,
            "audio": audio,
            "text": row.get("expected_text") or "",
            "automatic_transcript": row.get("automatic_transcript"),
            "word_error_rate": row.get("word_error_rate"),
        }
    return result


def chunk_embeddings(converter: Any, audio_path: Path, output_root: Path, sample_id: str) -> list[Any]:
    audio, sample_rate = load_audio(audio_path)
    embeddings = []
    with tempfile.TemporaryDirectory(prefix=f"openvoice-chunks-{sample_id}-", dir=str(output_root)) as temporary:
        temp_root = Path(temporary)
        for index, chunk in enumerate(np.array_split(audio, 3)):
            if len(chunk) < max(2048, int(sample_rate * 0.35)):
                continue
            path = temp_root / f"chunk_{index}.wav"
            sf.write(path, chunk, sample_rate, subtype="PCM_16")
            embeddings.append(converter.extract_se(str(path)))
    return embeddings


def run_conversion(args: argparse.Namespace) -> dict[str, Any]:
    openvoice_app = Path(args.openvoice_app).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    identity_manifest = Path(args.identity_manifest).expanduser().resolve()
    donor_review = Path(args.donor_review).expanduser().resolve()
    config = openvoice_app / "checkpoints" / "converter" / "config.json"
    checkpoint = openvoice_app / "checkpoints" / "converter" / "checkpoint.pth"
    for path in (openvoice_app, config, checkpoint, identity_manifest, donor_review):
        if not path.exists():
            raise ConversionError(f"Required path is missing: {path}")

    sys.path.insert(0, str(openvoice_app))
    import torch
    from OpenVoice.api import OpenVoiceBaseClass, ToneColorConverter

    device = args.device
    if device == "auto":
        device = "mps" if torch.backends.mps.is_available() else "cpu"
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    output_root.mkdir(parents=True, exist_ok=True)
    generated_root = output_root / "generated"
    receipt_root = output_root / "generation-receipts"
    generated_root.mkdir(parents=True, exist_ok=True)
    receipt_root.mkdir(parents=True, exist_ok=True)

    targets = identity_rows(identity_manifest)
    donors = donor_rows(donor_review)
    # The downloaded OpenVoice2 fork reads enable_watermark from **kwargs but
    # mistakenly forwards the same keyword to OpenVoiceBaseClass, whose
    # constructor does not accept it. Initialize the base explicitly so the
    # evaluation can disable watermarking without patching the external app.
    converter = ToneColorConverter.__new__(ToneColorConverter)
    OpenVoiceBaseClass.__init__(converter, str(config), device=device)
    converter.watermark_model = None
    converter.load_ckpt(str(checkpoint))

    canonical_target_embeddings = {
        key: converter.extract_se(str(item["conditioning_audio"]))
        for key, item in targets.items()
    }
    strategy_target_embeddings: dict[tuple[str, str], Any] = {}
    for key, item in targets.items():
        strategy_target_embeddings[(key, "conditioning")] = canonical_target_embeddings[key]
        strategy_target_embeddings[(key, "source")] = converter.extract_se(str(item["source_audio"]))
        strategy_target_embeddings[(key, "combined")] = converter.extract_se(
            [str(item["conditioning_audio"]), str(item["source_audio"])]
        )
    donor_embeddings = {key: converter.extract_se(str(item["audio"])) for key, item in donors.items()}
    donor_metrics = {key: audio_metrics(item["audio"], len(item["text"].split())) for key, item in donors.items()}

    requested_strategies = tuple(
        value.strip()
        for value in str(args.anchor_strategies).split(",")
        if value.strip()
    )
    if not requested_strategies or any(value not in ANCHOR_STRATEGIES for value in requested_strategies):
        raise ConversionError(
            "anchor strategies must be a comma-separated subset of: "
            + ", ".join(ANCHOR_STRATEGIES)
        )

    records = []
    for anchor_strategy in requested_strategies:
        for target_key in TARGET_ORDER:
            target = targets[target_key]
            conversion_embedding = strategy_target_embeddings[(target_key, anchor_strategy)]
            canonical_embedding = canonical_target_embeddings[target_key]
            for mode in MODE_ORDER:
                donor = donors[mode]
                sample_id = fingerprint(
                    {
                        "round": ROUND_ID,
                        "target": target_key,
                        "mode": mode,
                        "tau": args.tau,
                        "anchor_strategy": anchor_strategy,
                    }
                )
                output = generated_root / f"{sample_id}.wav"
                receipt_path = receipt_root / f"{sample_id}.json"
                if output.is_file() and receipt_path.is_file() and not args.force:
                    receipt = load_json(receipt_path)
                    if receipt.get("audio_sha256") == sha256_file(output):
                        records.append(receipt)
                        continue
                started = time.perf_counter()
                converter.convert(
                    audio_src_path=str(donor["audio"]),
                    src_se=donor_embeddings[mode],
                    tgt_se=conversion_embedding,
                    output_path=str(output),
                    tau=float(args.tau),
                    message="alexandria-evaluation",
                )
                if not output.is_file():
                    raise ConversionError(f"OpenVoice did not create {output}")
                output_embedding = converter.extract_se(str(output))
                thirds = chunk_embeddings(converter, output, output_root, sample_id)
                whole_identity = cosine_torch(output_embedding, canonical_embedding)
                third_identity = [cosine_torch(value, canonical_embedding) for value in thirds]
                donor_retention = cosine_torch(output_embedding, donor_embeddings[mode])
                metrics = audio_metrics(output, len(donor["text"].split()))
                shape_similarity = pitch_shape_similarity(metrics, donor_metrics[mode])
                anchor_files = (
                    [target["conditioning_audio"]]
                    if anchor_strategy == "conditioning"
                    else [target["source_audio"]]
                    if anchor_strategy == "source"
                    else [target["conditioning_audio"], target["source_audio"]]
                )
                receipt = {
                    "schema_version": 1,
                    "round_id": ROUND_ID,
                    "sample_id": sample_id,
                    "target_key": target_key,
                    "target_label": target["label"],
                    "mode": mode,
                    "mode_label": donor["label"],
                    "anchor_strategy": anchor_strategy,
                    "target_anchor_files": [str(path) for path in anchor_files],
                    "target_anchor_sha256": [sha256_file(path) for path in anchor_files],
                    "expected_text": donor["text"],
                    "target_reference": str(target["conditioning_audio"]),
                    "target_reference_sha256": sha256_file(target["conditioning_audio"]),
                    "donor_audio": str(donor["audio"]),
                    "donor_audio_sha256": sha256_file(donor["audio"]),
                    "audio_path": str(output),
                    "audio_sha256": sha256_file(output),
                    "tau": float(args.tau),
                    "device": device,
                    "generation_seconds": round(time.perf_counter() - started, 4),
                    "whole_identity_cosine": round(whole_identity, 6),
                    "third_identity_cosines": [round(value, 6) for value in third_identity],
                    "minimum_third_identity_cosine": round(min(third_identity) if third_identity else whole_identity, 6),
                    "donor_timbre_retention_cosine": round(donor_retention, 6),
                    "pitch_shape_similarity_to_donor": round(shape_similarity, 6),
                    "audio_metrics": metrics,
                    "donor_metrics": donor_metrics[mode],
                    "manual_listening_required": True,
                    "production_promotion_allowed": False,
                }
                write_json(receipt_path, receipt)
                records.append(receipt)

    summary = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "created_at": now_iso(),
        "openvoice_app": str(openvoice_app),
        "converter_checkpoint_sha256": sha256_file(checkpoint),
        "device": device,
        "tau": float(args.tau),
        "anchor_strategies": list(requested_strategies),
        "sample_count": len(records),
        "samples": records,
        "production_promotion_allowed": False,
    }
    write_json(output_root / "generation-summary.json", summary)
    return {"sample_count": len(records), "summary": str(output_root / "generation-summary.json")}


def package_review(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    whisper_model = Path(args.whisper_model).expanduser().resolve()
    summary_path = output_root / "generation-summary.json"
    if not summary_path.is_file():
        raise ConversionError(f"Generation summary is missing: {summary_path}")
    if not whisper_model.is_dir():
        raise ConversionError(f"Whisper model is missing: {whisper_model}")
    import mlx_whisper

    summary = load_json(summary_path)
    analyzed = []
    for row in summary["samples"]:
        audio = Path(row["audio_path"])
        asr = mlx_whisper.transcribe(
            str(audio),
            path_or_hf_repo=str(whisper_model),
            language="en",
            word_timestamps=False,
            condition_on_previous_text=False,
            verbose=False,
        )
        transcript = str(asr.get("text") or "").strip()
        similarity = text_similarity(row["expected_text"], transcript)
        expected_words = normalize_text(row["expected_text"]).split()
        actual_words = normalize_text(transcript).split()
        final_word_matches = bool(expected_words and actual_words and expected_words[-1] == actual_words[-1])
        duration_ratio = row["audio_metrics"]["duration_seconds"] / max(row["donor_metrics"]["duration_seconds"], 0.01)
        hard_pass = (
            similarity >= 0.92
            and final_word_matches
            and row["whole_identity_cosine"] >= float(args.identity_floor)
            and row["minimum_third_identity_cosine"] >= float(args.third_identity_floor)
            and row["pitch_shape_similarity_to_donor"] >= 0.55
            and not row["audio_metrics"]["pitch_trajectory_anomaly"]
            and row["audio_metrics"]["clipping_fraction"] < 0.001
            and 0.82 <= duration_ratio <= 1.18
        )
        selection_score = (
            float(row["whole_identity_cosine"]) * 4.0
            + float(row["minimum_third_identity_cosine"]) * 3.0
            + float(row["pitch_shape_similarity_to_donor"]) * 2.0
            + similarity * 2.0
            + (0.75 if hard_pass else -0.5)
            - abs(duration_ratio - 1.0)
        )
        analyzed.append(
            {
                **row,
                "automatic_transcript": transcript,
                "text_similarity": round(similarity, 6),
                "final_word_matches": final_word_matches,
                "duration_ratio_to_donor": round(duration_ratio, 6),
                "technical_pass": hard_pass,
                "selection_score": round(selection_score, 6),
            }
        )

    winners = []
    for target_key in TARGET_ORDER:
        for mode in MODE_ORDER:
            candidates = [
                row
                for row in analyzed
                if row["target_key"] == target_key and row["mode"] == mode
            ]
            if not candidates:
                raise ConversionError(f"No candidates for {target_key}/{mode}")
            passing = [row for row in candidates if row["technical_pass"]]
            winners.append(max(passing or candidates, key=lambda row: row["selection_score"]))

    review_root = output_root / "review"
    if review_root.exists():
        shutil.rmtree(review_root)
    (review_root / "audio").mkdir(parents=True)
    (review_root / "targets").mkdir(parents=True)
    (review_root / "donors").mkdir(parents=True)
    public_rows = []
    answer_rows = []
    copied_targets: set[str] = set()
    copied_donors: set[str] = set()
    for ordinal, row in enumerate(winners, 1):
        sample_id = row["sample_id"]
        target_name = f"{row['target_key']}.wav"
        donor_name = f"{row['mode']}.wav"
        output_name = f"{sample_id}.wav"
        if target_name not in copied_targets:
            shutil.copy2(row["target_reference"], review_root / "targets" / target_name)
            copied_targets.add(target_name)
        if donor_name not in copied_donors:
            shutil.copy2(row["donor_audio"], review_root / "donors" / donor_name)
            copied_donors.add(donor_name)
        shutil.copy2(row["audio_path"], review_root / "audio" / output_name)
        public_rows.append(
            {
                "sample_id": sample_id,
                "ordinal": ordinal,
                "target_key": row["target_key"],
                "target_label": row["target_label"],
                "mode": row["mode"],
                "mode_label": row["mode_label"],
                "expected_text": row["expected_text"],
                "target_audio": f"targets/{target_name}",
                "donor_audio": f"donors/{donor_name}",
                "converted_audio": f"audio/{output_name}",
                "technical_pass": row["technical_pass"],
                "automatic_transcript": row["automatic_transcript"],
            }
        )
        answer_rows.append(row)

    for asset in ("index.html", "styles.css", "app.js"):
        shutil.copy2(ASSET_ROOT / asset, review_root / asset)
    public = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "title": "Three-voice performance conversion proof",
        "created_at": now_iso(),
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
    write_json(
        output_root / "analysis.json",
        {
            "schema_version": 1,
            "round_id": ROUND_ID,
            "sample_count": len(analyzed),
            "technical_pass_count": sum(row["technical_pass"] for row in analyzed),
            "winner_technical_pass_count": sum(row["technical_pass"] for row in winners),
            "samples": analyzed,
            "winners": [row["sample_id"] for row in winners],
        },
    )
    (output_root / "START_HERE.txt").write_text(
        "Three-voice OpenVoice performance conversion proof\n"
        "===============================================\n\n"
        f"cd \"{review_root}\"\n"
        "python3 -m http.server 8776 --bind 127.0.0.1\n\n"
        "Then open http://127.0.0.1:8776/\n",
        encoding="utf-8",
    )
    return {
        "review": str(review_root / "index.html"),
        "candidate_count": len(public_rows),
        "technical_pass_count": sum(row["technical_pass"] for row in winners),
        "analyzed_count": len(analyzed),
    }


def validate(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    review_root = output_root / "review"
    prefix = "window.THREE_VOICE_OPENVOICE_DATA = "
    data_text = (review_root / "data.js").read_text(encoding="utf-8").strip()
    data = json.loads(data_text[len(prefix) :].rstrip(";"))
    answers = {row["sample_id"]: row for row in load_json(output_root / "answer-key.json")}
    missing = []
    hash_errors = []
    for row in data["rows"]:
        audio = review_root / row["converted_audio"]
        target = review_root / row["target_audio"]
        donor = review_root / row["donor_audio"]
        if not audio.is_file() or not target.is_file() or not donor.is_file():
            missing.append(row["sample_id"])
            continue
        if sha256_file(audio) != answers[row["sample_id"]]["audio_sha256"]:
            hash_errors.append(row["sample_id"])
    if missing or hash_errors:
        raise ConversionError(f"Validation failed: missing={missing}, hash_errors={hash_errors}")
    return {
        "round_id": ROUND_ID,
        "candidate_count": len(data["rows"]),
        "missing_count": len(missing),
        "hash_error_count": len(hash_errors),
        "review": str(review_root / "index.html"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a bounded three-character OpenVoice performance conversion proof.")
    sub = parser.add_subparsers(dest="command", required=True)
    convert = sub.add_parser("convert")
    convert.add_argument("--openvoice-app", required=True)
    convert.add_argument("--identity-manifest", required=True)
    convert.add_argument("--donor-review", required=True)
    convert.add_argument("--output-root", required=True)
    convert.add_argument("--device", default="auto", choices=("auto", "mps", "cpu"))
    convert.add_argument("--tau", type=float, default=0.3)
    convert.add_argument(
        "--anchor-strategies",
        default=",".join(ANCHOR_STRATEGIES),
        help="Comma-separated target embedding strategies.",
    )
    convert.add_argument("--force", action="store_true")
    package = sub.add_parser("package")
    package.add_argument("--output-root", required=True)
    package.add_argument("--whisper-model", required=True)
    package.add_argument("--identity-floor", type=float, default=0.70)
    package.add_argument("--third-identity-floor", type=float, default=0.62)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--output-root", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "convert":
            result = run_conversion(args)
        elif args.command == "package":
            result = package_review(args)
        else:
            result = validate(args)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
