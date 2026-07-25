#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf

BENCHMARKS_ROOT = Path(__file__).resolve().parent
if str(BENCHMARKS_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS_ROOT))

from prepare_narrator_indextts2_reference_bank import (
    ReferenceBankError,
    acoustic_metrics,
    ratio_similarity,
    runtime_paths,
    sha256_file,
    text_similarity,
)

ROUND_ID = "alexandria_three_voice_combined_bank_generation_benchmark_v1"
BANK_ROUND_ID = "alexandria_three_voice_combined_reference_bank_v1"
ASSET_ROOT = Path(__file__).with_name("three_voice_combined_bank_benchmark_assets")
RANGE_SERVER = Path(__file__).with_name("range_http_server.py")
TARGET_ORDER = ("narrator", "benny", "doctor")

ROUTES: tuple[dict[str, Any], ...] = (
    {
        "route_id": "narrator_joy",
        "target": "narrator",
        "target_label": "Narrator",
        "function": "joy",
        "function_label": "Ecstatic joy",
        "bank_clip_id": "narrator_ud_ecstatic_bucket_affection",
        "legacy_kind": "narrator_style",
        "legacy_key": "exuberant_joy",
        "target_text": "Yes! That's it! You did it, Stanley, you actually did it!",
        "alpha": 0.65,
    },
    {
        "route_id": "narrator_anger",
        "target": "narrator",
        "target_label": "Narrator",
        "function": "explosive_anger",
        "function_label": "Explosive anger",
        "bank_clip_id": "narrator_ud_explosive_indignation",
        "legacy_kind": "narrator_style",
        "legacy_key": "wounded_rage",
        "target_text": "After everything I did for you, this is how you chose to repay me.",
        "alpha": 0.75,
    },
    {
        "route_id": "benny_fear",
        "target": "benny",
        "target_label": "Benny",
        "function": "credible_fear",
        "function_label": "Credible fear",
        "bank_clip_id": "benny_hesitation_fearful_vigilance",
        "legacy_kind": "audiodrama_reference",
        "legacy_key": "benny-urgent_fear.wav",
        "legacy_reference_text": "I'm trapped in a pyramid. Yes, a pyramid. My guide's dead.",
        "target_text": "Move! The ceiling is coming down, and we have seconds at most.",
        "alpha": 0.45,
    },
    {
        "route_id": "benny_reassurance",
        "target": "benny",
        "target_label": "Benny",
        "function": "soft_intimacy",
        "function_label": "Protective reassurance",
        "bank_clip_id": "benny_hesitation_protective_reassurance",
        "legacy_kind": "audiodrama_reference",
        "legacy_key": "benny-vulnerable_honesty.wav",
        "legacy_reference_text": "I'm trapped in a pyramid. Yes, a pyramid. My guide's dead.",
        "target_text": "I thought I could handle this. I was wrong, and I need you to stay with me.",
        "alpha": 0.40,
    },
    {
        "route_id": "doctor_playful_identity",
        "target": "doctor",
        "target_label": "Seventh Doctor",
        "function": "ordinary_identity",
        "function_label": "Playful eccentricity",
        "bank_clip_id": "doctor_acf_playful_introduction",
        "legacy_kind": "audiodrama_reference",
        "legacy_key": "doctor-playful_eccentricity.wav",
        "legacy_reference_text": "Ace, have you no sense of occasion?",
        "target_text": "Oh, wonderful. A locked door, a missing key, and precisely no time to think.",
        "alpha": 0.35,
    },
    {
        "route_id": "doctor_urgency",
        "target": "doctor",
        "target_label": "Seventh Doctor",
        "function": "urgency",
        "function_label": "Emergency command",
        "bank_clip_id": "doctor_acf_emergency_command",
        "legacy_kind": "audiodrama_reference",
        "legacy_key": "doctor-urgent_command.wav",
        "legacy_reference_text": "Brigadier, you and Ace, see to this ship.",
        "target_text": "Ace, get everyone out. Now. Do not stop for anything.",
        "alpha": 0.50,
    },
)

OPEN_GAPS = {
    "narrator": {"grief_or_regret"},
    "benny": {"grief", "explosive_anger"},
    "doctor": {"compassion", "weariness"},
}


class CombinedBankBenchmarkError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fingerprint(value: Any, length: int = 16) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def load_json(path: Path) -> Any:
    if not path.is_file():
        raise CombinedBankBenchmarkError(f"JSON file is missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CombinedBankBenchmarkError(f"Invalid JSON in {path}: {exc}") from exc


def normalize_audio(source: Path, output: Path) -> None:
    if not source.is_file():
        raise CombinedBankBenchmarkError(f"Audio source is missing: {source}")
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-i", str(source),
            "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(output),
        ],
        check=True,
    )
    info = sf.info(output)
    if info.samplerate != 24000 or info.channels != 1 or info.subtype != "PCM_16":
        raise CombinedBankBenchmarkError(f"Normalized audio has an invalid format: {output}")


def reference_index(bank: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if bank.get("round_id") != BANK_ROUND_ID:
        raise CombinedBankBenchmarkError(f"Unexpected combined bank round_id: {bank.get('round_id')}")
    rows = bank.get("references")
    if not isinstance(rows, list) or len(rows) != int(bank.get("reference_count") or 0):
        raise CombinedBankBenchmarkError("Combined bank references are missing or incomplete.")
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        clip_id = row.get("clip_id")
        if not isinstance(clip_id, str) or not clip_id:
            raise CombinedBankBenchmarkError("Every combined-bank reference requires clip_id.")
        if clip_id in indexed:
            raise CombinedBankBenchmarkError(f"Duplicate combined-bank clip_id: {clip_id}")
        audio = Path(str(row.get("audio_path") or ""))
        if not audio.is_file() or sha256_file(audio) != row.get("audio_sha256"):
            raise CombinedBankBenchmarkError(f"Combined-bank audio validation failed: {clip_id}")
        indexed[clip_id] = row
    return indexed


def legacy_narrator_styles(matrix: dict[str, Any]) -> dict[str, dict[str, Any]]:
    styles = matrix.get("styles")
    if not isinstance(styles, list):
        raise CombinedBankBenchmarkError("Legacy Narrator matrix has no styles list.")
    return {str(row.get("key")): row for row in styles}


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    bank_path = Path(args.bank).expanduser().resolve()
    bank = load_json(bank_path)
    bank_refs = reference_index(bank)
    narrator_matrix_path = Path(args.legacy_narrator_matrix).expanduser().resolve()
    narrator_styles = legacy_narrator_styles(load_json(narrator_matrix_path))
    legacy_root = Path(args.legacy_audiodrama_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    if output_root.exists() and args.force:
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    identity_sources = {
        "narrator": Path(args.narrator_identity).expanduser().resolve(),
        "benny": Path(args.benny_identity).expanduser().resolve(),
        "doctor": Path(args.doctor_identity).expanduser().resolve(),
    }
    identity_paths: dict[str, Path] = {}
    for target, source in identity_sources.items():
        target_path = output_root / "identity" / f"{target}.wav"
        normalize_audio(source, target_path)
        identity_paths[target] = target_path

    routes: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    for route in ROUTES:
        if route["function"] in OPEN_GAPS.get(route["target"], set()):
            raise CombinedBankBenchmarkError(f"Route incorrectly targets an open bank gap: {route['route_id']}")
        bank_row = bank_refs.get(route["bank_clip_id"])
        if bank_row is None:
            raise CombinedBankBenchmarkError(f"Bank clip is missing: {route['bank_clip_id']}")
        bank_source = Path(bank_row["audio_path"])
        bank_reference = output_root / "references" / "combined_bank" / f"{route['route_id']}.wav"
        normalize_audio(bank_source, bank_reference)

        if route["legacy_kind"] == "narrator_style":
            legacy_row = narrator_styles.get(route["legacy_key"])
            if legacy_row is None:
                raise CombinedBankBenchmarkError(f"Legacy Narrator style is missing: {route['legacy_key']}")
            legacy_source = Path(str(legacy_row.get("emotion_audio") or "")).expanduser().resolve()
            legacy_text = str(legacy_row.get("emotion_text") or "")
        else:
            legacy_source = (legacy_root / route["legacy_key"]).resolve()
            legacy_text = str(route.get("legacy_reference_text") or "")
        legacy_reference = output_root / "references" / "legacy" / f"{route['route_id']}.wav"
        normalize_audio(legacy_source, legacy_reference)

        route_row = {
            **route,
            "identity_audio": str(identity_paths[route["target"]]),
            "identity_audio_sha256": sha256_file(identity_paths[route["target"]]),
            "bank_reference_audio": str(bank_reference),
            "bank_reference_audio_sha256": sha256_file(bank_reference),
            "bank_reference_text": str(bank_row.get("transcript") or ""),
            "bank_reference_primary_emotion": str(bank_row.get("primary_emotion") or ""),
            "bank_reference_dramatic_function": str(bank_row.get("dramatic_function") or ""),
            "legacy_reference_audio": str(legacy_reference),
            "legacy_reference_audio_sha256": sha256_file(legacy_reference),
            "legacy_reference_text": legacy_text,
        }
        routes.append(route_row)
        for prompt_role, reference_audio, reference_text in (
            ("combined_bank", bank_reference, route_row["bank_reference_text"]),
            ("legacy_reference", legacy_reference, legacy_text),
        ):
            sample_id = fingerprint(
                {
                    "round_id": ROUND_ID,
                    "route_id": route["route_id"],
                    "prompt_role": prompt_role,
                    "identity_sha256": route_row["identity_audio_sha256"],
                    "reference_sha256": sha256_file(reference_audio),
                    "target_text": route["target_text"],
                    "alpha": route["alpha"],
                }
            )
            samples.append(
                {
                    "sample_id": sample_id,
                    "route_id": route["route_id"],
                    "target": route["target"],
                    "target_label": route["target_label"],
                    "function": route["function"],
                    "function_label": route["function_label"],
                    "target_text": route["target_text"],
                    "prompt_role": prompt_role,
                    "alpha": float(route["alpha"]),
                    "identity_audio": route_row["identity_audio"],
                    "identity_audio_sha256": route_row["identity_audio_sha256"],
                    "prompt_audio": str(reference_audio),
                    "prompt_audio_sha256": sha256_file(reference_audio),
                    "prompt_text": reference_text,
                    "bank_target_audio": route_row["bank_reference_audio"],
                    "bank_target_audio_sha256": route_row["bank_reference_audio_sha256"],
                    "bank_target_text": route_row["bank_reference_text"],
                }
            )

    matrix = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "created_at": now_iso(),
        "combined_bank": {
            "path": str(bank_path),
            "sha256": sha256_file(bank_path),
            "reference_count": bank.get("reference_count"),
            "reference_counts_by_target": bank.get("reference_counts_by_target"),
        },
        "route_count": len(routes),
        "sample_count": len(samples),
        "target_order": list(TARGET_ORDER),
        "routes": routes,
        "samples": samples,
        "comparison_contract": {
            "same_identity_prompt": True,
            "same_runtime": True,
            "same_target_text": True,
            "same_emotion_alpha": True,
            "only_performance_prompt_changes": True,
            "open_gap_functions_excluded": True,
        },
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }
    matrix_path = output_root / "matrix.json"
    matrix_path.write_text(json.dumps(matrix, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"route_count": len(routes), "sample_count": len(samples), "matrix": str(matrix_path)}


def cosine(left: np.ndarray, right: np.ndarray) -> float:
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    return float(np.dot(left, right) / denominator) if denominator > 0 else 0.0


def normalize_generated(source: Path) -> None:
    temporary = source.with_name(source.stem + ".normalized.wav")
    normalize_audio(source, temporary)
    temporary.replace(source)


def generate(args: argparse.Namespace) -> dict[str, Any]:
    import torch
    import torchaudio

    output_root = Path(args.output_root).expanduser().resolve()
    matrix = load_json(output_root / "matrix.json")
    runtime = runtime_paths(Path(args.runtime_root).expanduser().resolve())
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("PYTORCH_MPS_FAST_MATH", "1")
    os.environ.setdefault("PYTORCH_MPS_PREFER_METAL", "1")
    sys.path.insert(0, str(runtime.source))
    from indextts.infer_v2 import IndexTTS2

    aux_paths = {
        "w2v_bert": str(runtime.aux / "w2v-bert-2.0"),
        "semantic_codec": str(runtime.aux / "semantic_codec" / "model.safetensors"),
        "campplus": str(runtime.aux / "campplus_cn_common.bin"),
        "bigvgan": str(runtime.aux / "bigvgan"),
    }
    torch.set_float32_matmul_precision("high")
    model = IndexTTS2(
        cfg_path=str(runtime.model / "config.yaml"),
        model_dir=str(runtime.model),
        use_fp16=False,
        device="mps",
        use_cuda_kernel=False,
        use_deepspeed=False,
        use_accel=False,
        use_torch_compile=False,
        aux_paths=aux_paths,
    )
    original_inference = model.gpt.inference_speech

    def greedy_inference(*positional, **keywords):
        keywords["do_sample"] = False
        keywords["num_beams"] = 1
        return original_inference(*positional, **keywords)

    model.gpt.inference_speech = greedy_inference
    original_cfm = model.s2mel.models["cfm"].inference

    def short_cfm(mu, x_lens, prompt, style, f0, n_timesteps, temperature=1.0, inference_cfg_rate=0.5):
        return original_cfm(
            mu, x_lens, prompt, style, f0, 8,
            temperature=temperature,
            inference_cfg_rate=inference_cfg_rate,
        )

    model.s2mel.models["cfm"].inference = short_cfm
    original_bigvgan = model.bigvgan

    def safely_scaled_bigvgan(*positional, **keywords):
        return original_bigvgan(*positional, **keywords) * 0.70

    model.bigvgan = safely_scaled_bigvgan

    def speaker_embedding(path: Path) -> np.ndarray:
        audio, sample_rate = torchaudio.load(str(path))
        if audio.shape[0] > 1:
            audio = audio.mean(dim=0, keepdim=True)
        if sample_rate != 16000:
            audio = torchaudio.transforms.Resample(sample_rate, 16000)(audio)
        feat = torchaudio.compliance.kaldi.fbank(
            audio.to(model.device), num_mel_bins=80, dither=0, sample_frequency=16000
        )
        feat = feat - feat.mean(dim=0, keepdim=True)
        with torch.inference_mode():
            embedding = model.campplus_model(feat.unsqueeze(0)).float()
        return embedding.detach().cpu().numpy().reshape(-1)

    embedding_cache: dict[str, np.ndarray] = {}
    metric_cache: dict[str, dict[str, Any]] = {}
    for sample in matrix["samples"]:
        for key, words in (
            ("identity_audio", 2),
            ("prompt_audio", max(1, len(str(sample.get("prompt_text") or "").split()))),
            ("bank_target_audio", max(1, len(str(sample.get("bank_target_text") or "").split()))),
        ):
            path = Path(sample[key])
            digest = sha256_file(path)
            if digest not in embedding_cache:
                embedding_cache[digest] = speaker_embedding(path)
            if digest not in metric_cache:
                metric_cache[digest] = acoustic_metrics(path, words)

    generated_root = output_root / "generated"
    receipt_root = output_root / "generation-receipts"
    receipts: list[dict[str, Any]] = []
    for sample in matrix["samples"]:
        output = generated_root / sample["target"] / sample["route_id"] / f"{sample['prompt_role']}.wav"
        receipt_path = receipt_root / sample["target"] / sample["route_id"] / f"{sample['prompt_role']}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        if output.is_file() and receipt_path.is_file() and not args.force:
            existing = load_json(receipt_path)
            if existing.get("audio_sha256") == sha256_file(output):
                receipts.append(existing)
                continue
        seed = int(sample["sample_id"][:8], 16)
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        started = time.perf_counter()
        returned = model.infer(
            spk_audio_prompt=sample["identity_audio"],
            text=sample["target_text"],
            output_path=str(output),
            emo_audio_prompt=sample["prompt_audio"],
            emo_alpha=sample["alpha"],
            use_random=False,
            verbose=False,
            num_beams=1,
            max_mel_tokens=700,
        )
        if not output.is_file():
            raise CombinedBankBenchmarkError(f"IndexTTS2 did not create {output}; returned {returned!r}")
        normalize_generated(output)
        generated_embedding = speaker_embedding(output)
        identity_embedding = embedding_cache[sha256_file(Path(sample["identity_audio"]))]
        bank_embedding = embedding_cache[sha256_file(Path(sample["bank_target_audio"]))]
        output_metrics = acoustic_metrics(output, len(sample["target_text"].split()))
        bank_metrics = metric_cache[sha256_file(Path(sample["bank_target_audio"]))]
        identity_cosine = cosine(generated_embedding, identity_embedding)
        bank_reference_cosine = cosine(generated_embedding, bank_embedding)
        acoustic_match = float(
            np.mean(
                [
                    ratio_similarity(
                        float(output_metrics["pitch_median_hz"]),
                        float(bank_metrics["pitch_median_hz"]),
                    ),
                    ratio_similarity(
                        float(output_metrics["pitch_p90_hz"] - output_metrics["pitch_p10_hz"]),
                        float(bank_metrics["pitch_p90_hz"] - bank_metrics["pitch_p10_hz"]),
                    ),
                    ratio_similarity(
                        float(output_metrics["words_per_second"]),
                        float(bank_metrics["words_per_second"]),
                    ),
                    ratio_similarity(
                        10 ** (float(output_metrics["rms_dbfs"]) / 20.0),
                        10 ** (float(bank_metrics["rms_dbfs"]) / 20.0),
                    ),
                ]
            )
        )
        receipt = {
            **sample,
            "audio_path": str(output),
            "audio_sha256": sha256_file(output),
            "generation_seconds": round(time.perf_counter() - started, 4),
            "identity_cosine": round(identity_cosine, 6),
            "bank_reference_cosine": round(bank_reference_cosine, 6),
            "bank_acoustic_match": round(acoustic_match, 6),
            "acoustic_metrics": output_metrics,
            "bank_reference_metrics": bank_metrics,
            "manual_listening_required": True,
            "automatic_production_assignment": False,
            "production_promotion_allowed": False,
        }
        receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        receipts.append(receipt)

    summary = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "created_at": now_iso(),
        "sample_count": len(receipts),
        "samples": receipts,
        "runtime": {
            "engine": "IndexTTS2",
            "device": "mps",
            "greedy_generation": True,
            "diffusion_steps": 8,
            "pre_clamp_scale": 0.70,
        },
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }
    summary_path = output_root / "generation-summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"sample_count": len(receipts), "summary": str(summary_path)}


def technical_pass(row: dict[str, Any], transcript_similarity: float, final_word_matches: bool) -> bool:
    return (
        transcript_similarity >= 0.90
        and final_word_matches
        and float(row["identity_cosine"]) >= 0.60
        and not bool(row["acoustic_metrics"]["pitch_trajectory_anomaly"])
        and float(row["acoustic_metrics"]["clipping_fraction"]) < 0.001
    )


def convert_mp3(source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-i", str(source),
            "-ac", "1", "-ar", "24000", "-c:a", "libmp3lame", "-b:a", "192k", str(output),
        ],
        check=True,
    )


def package(args: argparse.Namespace) -> dict[str, Any]:
    import mlx_whisper

    output_root = Path(args.output_root).expanduser().resolve()
    whisper_model = Path(args.whisper_model).expanduser().resolve()
    if not whisper_model.is_dir():
        raise CombinedBankBenchmarkError(f"Whisper model is missing: {whisper_model}")
    matrix = load_json(output_root / "matrix.json")
    summary = load_json(output_root / "generation-summary.json")
    analyzed: list[dict[str, Any]] = []
    for row in summary["samples"]:
        result = mlx_whisper.transcribe(
            row["audio_path"],
            path_or_hf_repo=str(whisper_model),
            language="en",
            condition_on_previous_text=False,
            word_timestamps=False,
            verbose=False,
        )
        transcript = str(result.get("text") or "").strip()
        similarity = text_similarity(row["target_text"], transcript)
        expected_words = re.findall(r"[a-z0-9']+", row["target_text"].casefold())
        actual_words = re.findall(r"[a-z0-9']+", transcript.casefold())
        final_word_matches = bool(expected_words and actual_words and expected_words[-1] == actual_words[-1])
        passed = technical_pass(row, similarity, final_word_matches)
        analyzed.append(
            {
                **row,
                "automatic_transcript": transcript,
                "text_similarity": round(similarity, 6),
                "final_word_matches": final_word_matches,
                "technical_pass": passed,
            }
        )

    analyzed_by_route: dict[str, dict[str, dict[str, Any]]] = {}
    for row in analyzed:
        analyzed_by_route.setdefault(row["route_id"], {})[row["prompt_role"]] = row

    review_root = output_root / "review"
    if review_root.exists():
        shutil.rmtree(review_root)
    (review_root / "audio" / "identity").mkdir(parents=True)
    (review_root / "audio" / "references").mkdir(parents=True)
    (review_root / "audio" / "candidates").mkdir(parents=True)
    for asset in ("index.html", "styles.css", "app.js"):
        shutil.copy2(ASSET_ROOT / asset, review_root / asset)
    shutil.copy2(RANGE_SERVER, review_root / "serve_review.py")

    public_rows: list[dict[str, Any]] = []
    answer_rows: list[dict[str, Any]] = []
    copied_identity: set[str] = set()
    for ordinal, route in enumerate(matrix["routes"], start=1):
        candidate_rows = analyzed_by_route.get(route["route_id"], {})
        bank_candidate = candidate_rows.get("combined_bank")
        legacy_candidate = candidate_rows.get("legacy_reference")
        if bank_candidate is None or legacy_candidate is None:
            raise CombinedBankBenchmarkError(f"Generated candidates are missing for {route['route_id']}")
        bank_is_a = int(fingerprint({"round": ROUND_ID, "route": route["route_id"], "blind": "mapping"}, 8), 16) % 2 == 0
        mapping = {
            "A": bank_candidate if bank_is_a else legacy_candidate,
            "B": legacy_candidate if bank_is_a else bank_candidate,
        }
        identity_name = f"{route['target']}.mp3"
        if identity_name not in copied_identity:
            convert_mp3(Path(route["identity_audio"]), review_root / "audio" / "identity" / identity_name)
            copied_identity.add(identity_name)
        reference_name = f"{route['route_id']}-target.mp3"
        convert_mp3(Path(route["bank_reference_audio"]), review_root / "audio" / "references" / reference_name)
        candidate_public: dict[str, Any] = {}
        for label, candidate in mapping.items():
            candidate_name = f"{route['route_id']}-{label}.mp3"
            convert_mp3(Path(candidate["audio_path"]), review_root / "audio" / "candidates" / candidate_name)
            candidate_public[label] = {
                "audio": f"audio/candidates/{candidate_name}",
                "technical_pass": bool(candidate["technical_pass"]),
                "automatic_transcript": candidate["automatic_transcript"],
            }
        public_rows.append(
            {
                "route_id": route["route_id"],
                "ordinal": ordinal,
                "target": route["target"],
                "target_label": route["target_label"],
                "function": route["function"],
                "function_label": route["function_label"],
                "target_text": route["target_text"],
                "performance_reference_text": route["bank_reference_text"],
                "performance_reference_emotion": route["bank_reference_primary_emotion"],
                "performance_reference_function": route["bank_reference_dramatic_function"],
                "identity_audio": f"audio/identity/{identity_name}",
                "performance_reference_audio": f"audio/references/{reference_name}",
                "candidate_A": candidate_public["A"],
                "candidate_B": candidate_public["B"],
            }
        )
        answer_rows.append(
            {
                "route_id": route["route_id"],
                "target": route["target"],
                "function": route["function"],
                "target_text": route["target_text"],
                "candidate_mapping": {
                    "A": mapping["A"]["prompt_role"],
                    "B": mapping["B"]["prompt_role"],
                },
                "combined_bank_candidate": bank_candidate,
                "legacy_candidate": legacy_candidate,
                "performance_reference": {
                    "clip_id": route["bank_clip_id"],
                    "audio_path": route["bank_reference_audio"],
                    "audio_sha256": route["bank_reference_audio_sha256"],
                    "transcript": route["bank_reference_text"],
                    "primary_emotion": route["bank_reference_primary_emotion"],
                    "dramatic_function": route["bank_reference_dramatic_function"],
                },
                "automatic_production_assignment": False,
                "production_promotion_allowed": False,
            }
        )

    public = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "title": "Combined Reference Bank — Generation Benchmark",
        "created_at": now_iso(),
        "candidate_count": len(public_rows),
        "target_counts": dict(sorted(Counter(row["target"] for row in public_rows).items())),
        "rows": public_rows,
        "production_promotion_allowed": False,
    }
    (review_root / "data.js").write_text(
        "window.THREE_VOICE_BANK_BENCHMARK_DATA = " + json.dumps(public, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "candidate_count": len(public_rows),
        "target_counts": public["target_counts"],
        "maximum_simultaneous_audio_elements": 4,
        "lazy_audio_loading": True,
        "range_requests_required": True,
        "candidate_mapping_exposed": False,
        "model_names_exposed": False,
        "same_identity_runtime_text_and_alpha": True,
        "automatic_production_assignment": False,
        "production_promotion_allowed": False,
    }
    (review_root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (output_root / "answer-key.json").write_text(json.dumps(answer_rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output_root / "analysis.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "round_id": ROUND_ID,
                "sample_count": len(analyzed),
                "technical_pass_count": sum(bool(row["technical_pass"]) for row in analyzed),
                "samples": analyzed,
                "automatic_production_assignment": False,
                "production_promotion_allowed": False,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (output_root / "START_HERE.txt").write_text(
        f'cd "{review_root}"\npython3 serve_review.py --bind 127.0.0.1 --port 8791\n\nThen open http://127.0.0.1:8791/\n',
        encoding="utf-8",
    )
    return {
        "review": str(review_root / "index.html"),
        "candidate_count": len(public_rows),
        "generated_count": len(analyzed),
        "technical_pass_count": sum(bool(row["technical_pass"]) for row in analyzed),
    }


def validate(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    review_root = output_root / "review"
    prefix = "window.THREE_VOICE_BANK_BENCHMARK_DATA = "
    text = (review_root / "data.js").read_text(encoding="utf-8").strip()
    if not text.startswith(prefix):
        raise CombinedBankBenchmarkError("Review data.js has an unexpected prefix.")
    public = json.loads(text[len(prefix):].rstrip(";"))
    failures: list[str] = []
    if len(public.get("rows") or []) != len(ROUTES):
        failures.append("candidate_count")
    for row in public.get("rows") or []:
        if row.get("function") in OPEN_GAPS.get(row.get("target"), set()):
            failures.append(f"open_gap:{row.get('route_id')}")
        for key in ("identity_audio", "performance_reference_audio"):
            path = review_root / str(row.get(key) or "")
            if not path.is_file():
                failures.append(f"missing:{row.get('route_id')}:{key}")
        for label in ("A", "B"):
            candidate = row.get(f"candidate_{label}") or {}
            path = review_root / str(candidate.get("audio") or "")
            if not path.is_file():
                failures.append(f"missing:{row.get('route_id')}:candidate_{label}")
    manifest = load_json(review_root / "manifest.json")
    if manifest.get("candidate_mapping_exposed") is not False:
        failures.append("mapping_exposed")
    if manifest.get("model_names_exposed") is not False:
        failures.append("model_names_exposed")
    if manifest.get("production_promotion_allowed") is not False:
        failures.append("promotion")
    if failures:
        raise CombinedBankBenchmarkError(f"Review validation failed: {failures}")
    return {
        "round_id": ROUND_ID,
        "candidate_count": len(public["rows"]),
        "failure_count": 0,
        "review": str(review_root / "index.html"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark the combined three-voice reference bank against earlier performance references.")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--bank", required=True)
    prepare_parser.add_argument("--output-root", required=True)
    prepare_parser.add_argument("--narrator-identity", required=True)
    prepare_parser.add_argument("--benny-identity", required=True)
    prepare_parser.add_argument("--doctor-identity", required=True)
    prepare_parser.add_argument("--legacy-narrator-matrix", required=True)
    prepare_parser.add_argument("--legacy-audiodrama-root", required=True)
    prepare_parser.add_argument("--force", action="store_true")
    generate_parser = sub.add_parser("generate")
    generate_parser.add_argument("--runtime-root", required=True)
    generate_parser.add_argument("--output-root", required=True)
    generate_parser.add_argument("--force", action="store_true")
    package_parser = sub.add_parser("package")
    package_parser.add_argument("--output-root", required=True)
    package_parser.add_argument("--whisper-model", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--output-root", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "prepare":
            result = prepare(args)
        elif args.command == "generate":
            result = generate(args)
        elif args.command == "package":
            result = package(args)
        else:
            result = validate(args)
    except (
        CombinedBankBenchmarkError,
        ReferenceBankError,
        subprocess.CalledProcessError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
