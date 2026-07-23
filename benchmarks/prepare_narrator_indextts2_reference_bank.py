#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf

ROUND_ID = "alexandria_narrator_indextts2_reference_bank_v1"
ALPHAS = (0.35, 0.60, 0.85)
ASSET_ROOT = Path(__file__).with_name("narrator_indextts2_reference_assets")

STYLE_SPECS: dict[str, dict[str, str]] = {
    "neutral": {
        "label": "Neutral narration",
        "target_text": "The envelope rested beside the lamp, exactly where she had left it.",
        "emotion_sample_id": "7804fbd626371f77",
        "emotion_text": "Coming to a staircase, Stanley walked upstairs to his boss's office.",
        "emotion_scene": "Ordinary story narration",
    },
    "pleading": {
        "label": "Wounded pleading",
        "target_text": "Please don't leave me here. I don't know what happens if you go.",
        "emotion_sample_id": "d5adebb5f24a9ca4",
        "emotion_text": "I wanted us to be happy here, Stanley. Maybe you're just getting a kick out of it. I don't know anymore. I just wanted us to get along.",
        "emotion_scene": "Zending — plea to stop",
    },
    "panic": {
        "label": "Genuine panic",
        "target_text": "The door would not open, the smoke was getting thicker, and there was nowhere left to run.",
        "emotion_sample_id": "d9692d8c004cd7fe",
        "emotion_text": "No, wait. Stanley, where are you? Don't go anywhere. I can't follow you there. I can't help you. No, just stay there. I'll find a way to get you out.",
        "emotion_scene": "Playtest Ending — Stanley jumps out of reach",
    },
    "wounded_rage": {
        "label": "Wounded rage",
        "target_text": "After everything I did for you, this is how you chose to repay me.",
        "emotion_sample_id": "6d16e628ab8c0825",
        "emotion_text": "I'm here. I'm still here. Here in this pile of rubbish. With you. You, who thought you were so clever. Now look where we are. My entire game is destroyed.",
        "emotion_scene": "Incorrect Ending — destroyed game",
    },
    "smug_menace": {
        "label": "Smug menace",
        "target_text": "Take your time, Stanley. The clock is only counting down to your death.",
        "emotion_sample_id": "7653ebeed8096728",
        "emotion_text": "You'd like to know where your co-workers are? A moment of solace before you're obliterated. All right, I'm in a good mood. You're going to die anyway. I'll tell you exactly what happened to them.",
        "emotion_scene": "Countdown Ending — co-worker revelation",
    },
    "exuberant_joy": {
        "label": "Exuberant joy",
        "target_text": "Yes! That's it! You did it, Stanley, you actually did it!",
        "emotion_sample_id": "34d0f3167d261758",
        "emotion_text": "Yes! We did it! Oh, wow! That felt amazing! Oh! You really earned it, Stanley!",
        "emotion_scene": "Office achievement — completed click challenge",
    },
}


class ReferenceBankError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    source: Path
    python: Path
    model: Path
    aux: Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(value: Any, length: int = 16) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def normalize_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", value.casefold()))


def text_similarity(expected: str, actual: str) -> float:
    return SequenceMatcher(None, normalize_text(expected), normalize_text(actual)).ratio()


def runtime_paths(root: Path) -> RuntimePaths:
    receipt_path = root / "restore_receipt.json"
    if not receipt_path.is_file():
        raise ReferenceBankError(f"IndexTTS2 restore receipt is missing: {receipt_path}")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    source = Path(receipt["source"]["path"]).resolve()
    model = Path(receipt["model"]["path"]).resolve()
    python = root / "env" / "bin" / "python"
    aux = Path(receipt["flat_auxiliary"]).resolve()
    for path in (source, model, python, aux):
        if not path.exists():
            raise ReferenceBankError(f"Pinned IndexTTS2 runtime path is missing: {path}")
    return RuntimePaths(root=root, source=source, python=python, model=model, aux=aux)


def load_audio(path: Path) -> tuple[np.ndarray, int]:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = np.mean(audio, axis=1, dtype=np.float32)
    if mono.size == 0:
        raise ReferenceBankError(f"Audio is empty: {path}")
    return mono, int(sample_rate)


def frame_rms(audio: np.ndarray, frame: int = 1024, hop: int = 256) -> np.ndarray:
    if len(audio) < frame:
        return np.array([float(np.sqrt(np.mean(audio * audio)))], dtype=np.float32)
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
        confidence = float(correlation[lag] / max(correlation[0], 1e-9))
        values.append(sample_rate / lag if confidence >= 0.28 else float("nan"))
    return np.asarray(values, dtype=np.float32)


def acoustic_metrics(path: Path, word_count: int) -> dict[str, float | list[float] | bool]:
    audio, sample_rate = load_audio(path)
    duration = len(audio) / sample_rate
    rms = float(np.sqrt(np.mean(audio * audio)))
    peak = float(np.max(np.abs(audio)))
    pitches = pitch_track(audio, sample_rate)
    finite = pitches[np.isfinite(pitches)]
    pitch_median = float(np.median(finite)) if finite.size else 0.0
    pitch_p10 = float(np.percentile(finite, 10)) if finite.size else 0.0
    pitch_p90 = float(np.percentile(finite, 90)) if finite.size else 0.0
    thirds: list[float] = []
    for chunk in np.array_split(pitches, 3):
        chunk = chunk[np.isfinite(chunk)]
        thirds.append(float(np.median(chunk)) if chunk.size else 0.0)
    nonzero = [value for value in thirds if value > 0]
    trajectory_ratio = (
        thirds[-1] / thirds[0]
        if thirds[0] > 0 and thirds[-1] > 0
        else 1.0
    )
    frame_values = frame_rms(audio)
    tail_frames = max(1, int(round(0.08 * sample_rate)))
    tail_rms = float(np.sqrt(np.mean(audio[-tail_frames:] ** 2)))
    return {
        "duration_seconds": duration,
        "sample_rate": float(sample_rate),
        "rms_dbfs": 20.0 * math.log10(max(rms, 1e-9)),
        "peak_dbfs": 20.0 * math.log10(max(peak, 1e-9)),
        "words_per_second": word_count / max(duration, 0.01),
        "pitch_median_hz": pitch_median,
        "pitch_p10_hz": pitch_p10,
        "pitch_p90_hz": pitch_p90,
        "pitch_thirds_hz": thirds,
        "pitch_end_start_ratio": trajectory_ratio,
        "pitch_trajectory_anomaly": trajectory_ratio > 1.75 or trajectory_ratio < 0.45,
        "voiced_pitch_fraction": float(finite.size / max(1, pitches.size)),
        "tail_rms_dbfs": 20.0 * math.log10(max(tail_rms, 1e-9)),
        "clipping_fraction": float(np.mean(np.abs(audio) >= 0.999)),
        "dynamic_db": float(
            20.0
            * math.log10(
                max(float(np.percentile(frame_values, 90)), 1e-8)
                / max(float(np.percentile(frame_values, 20)), 1e-8)
            )
        ),
    }


def ratio_similarity(left: float, right: float, *, floor: float = 1e-6) -> float:
    if left <= floor or right <= floor:
        return 0.0
    return math.exp(-abs(math.log(left / right)))


def bank_paths(prior_triage: Path, context_root: Path) -> dict[str, Path]:
    paths = {
        "neutral": prior_triage / "review" / "audio" / "7804fbd626371f77.wav",
        "pleading": context_root / "review" / "audio" / "supplement-d5adebb5f24a9ca4.wav",
        "panic": context_root / "review" / "audio" / "supplement-d9692d8c004cd7fe.wav",
        "wounded_rage": context_root / "review" / "audio" / "supplement-6d16e628ab8c0825.wav",
        "smug_menace": context_root / "review" / "audio" / "supplement-7653ebeed8096728.wav",
        "exuberant_joy": context_root / "review" / "audio" / "supplement-34d0f3167d261758.wav",
    }
    for path in paths.values():
        if not path.is_file():
            raise ReferenceBankError(f"Approved performance reference is missing: {path}")
    return paths


def write_matrix(
    *,
    output_root: Path,
    identity_audio: Path,
    emotion_paths: dict[str, Path],
) -> Path:
    matrix = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "created_at": now_iso(),
        "identity": {
            "audio": str(identity_audio),
            "sha256": sha256_file(identity_audio),
            "label": "Narrator neutral identity anchor",
        },
        "styles": [],
        "samples": [],
    }
    for style_key, spec in STYLE_SPECS.items():
        emotion_audio = emotion_paths[style_key]
        matrix["styles"].append(
            {
                "key": style_key,
                **spec,
                "emotion_audio": str(emotion_audio),
                "emotion_audio_sha256": sha256_file(emotion_audio),
            }
        )
        for alpha in ALPHAS:
            sample_id = fingerprint(
                {
                    "round": ROUND_ID,
                    "style": style_key,
                    "alpha": alpha,
                    "identity": matrix["identity"]["sha256"],
                    "emotion": sha256_file(emotion_audio),
                    "text": spec["target_text"],
                }
            )
            matrix["samples"].append(
                {
                    "sample_id": sample_id,
                    "style": style_key,
                    "alpha": alpha,
                    "identity_audio": str(identity_audio),
                    "emotion_audio": str(emotion_audio),
                    "target_text": spec["target_text"],
                }
            )
    path = output_root / "matrix.json"
    path.write_text(json.dumps(matrix, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def run_generation(args: argparse.Namespace) -> dict[str, Any]:
    import torch
    import torchaudio
    import torch.nn.functional as torch_functional

    runtime = runtime_paths(Path(args.runtime_root).expanduser().resolve())
    output_root = Path(args.output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    identity_audio = Path(args.identity_audio).expanduser().resolve()
    if not identity_audio.is_file():
        raise ReferenceBankError(f"Identity anchor is missing: {identity_audio}")
    emotion_paths = bank_paths(
        Path(args.prior_triage).expanduser().resolve(),
        Path(args.context_root).expanduser().resolve(),
    )
    matrix_path = write_matrix(
        output_root=output_root,
        identity_audio=identity_audio,
        emotion_paths=emotion_paths,
    )
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))

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
            mu,
            x_lens,
            prompt,
            style,
            f0,
            8,
            temperature=temperature,
            inference_cfg_rate=inference_cfg_rate,
        )

    model.s2mel.models["cfm"].inference = short_cfm

    def speaker_embedding(path: Path) -> np.ndarray:
        audio, sample_rate = torchaudio.load(str(path))
        if audio.shape[0] > 1:
            audio = audio.mean(dim=0, keepdim=True)
        if sample_rate != 16000:
            audio = torchaudio.transforms.Resample(sample_rate, 16000)(audio)
        feat = torchaudio.compliance.kaldi.fbank(
            audio.to(model.device),
            num_mel_bins=80,
            dither=0,
            sample_frequency=16000,
        )
        feat = feat - feat.mean(dim=0, keepdim=True)
        with torch.inference_mode():
            embedding = model.campplus_model(feat.unsqueeze(0)).float()
        return embedding.detach().cpu().numpy().reshape(-1)

    def cosine(left: np.ndarray, right: np.ndarray) -> float:
        denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
        return float(np.dot(left, right) / denominator) if denominator > 0 else 0.0

    identity_embedding = speaker_embedding(identity_audio)
    emotion_embeddings = {
        key: speaker_embedding(path) for key, path in emotion_paths.items()
    }
    emotion_metrics = {
        key: acoustic_metrics(path, len(STYLE_SPECS[key]["emotion_text"].split()))
        for key, path in emotion_paths.items()
    }

    generation_root = output_root / "generated"
    receipt_root = output_root / "generation-receipts"
    results = []
    for sample in matrix["samples"]:
        style = sample["style"]
        alpha = float(sample["alpha"])
        output = generation_root / style / f"alpha_{alpha:.2f}.wav"
        receipt_path = receipt_root / style / f"alpha_{alpha:.2f}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        if output.is_file() and receipt_path.is_file() and not args.force:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("audio_sha256") == sha256_file(output):
                results.append(receipt)
                continue
        random.seed(20260723)
        np.random.seed(20260723)
        torch.manual_seed(20260723)
        started = time.perf_counter()
        returned = model.infer(
            spk_audio_prompt=str(identity_audio),
            text=sample["target_text"],
            output_path=str(output),
            emo_audio_prompt=sample["emotion_audio"],
            emo_alpha=alpha,
            use_random=False,
            verbose=False,
            num_beams=1,
            max_mel_tokens=700,
        )
        if not output.is_file():
            raise ReferenceBankError(f"IndexTTS2 did not create {output}; returned {returned!r}")
        generated_embedding = speaker_embedding(output)
        metrics = acoustic_metrics(output, len(sample["target_text"].split()))
        reference_metrics = emotion_metrics[style]
        identity_cosine = cosine(generated_embedding, identity_embedding)
        emotion_speaker_cosine = cosine(generated_embedding, emotion_embeddings[style])
        acoustic_match = float(
            np.mean(
                [
                    ratio_similarity(float(metrics["pitch_median_hz"]), float(reference_metrics["pitch_median_hz"])),
                    ratio_similarity(float(metrics["pitch_p90_hz"] - metrics["pitch_p10_hz"]), float(reference_metrics["pitch_p90_hz"] - reference_metrics["pitch_p10_hz"])),
                    ratio_similarity(float(metrics["words_per_second"]), float(reference_metrics["words_per_second"])),
                    ratio_similarity(10 ** (float(metrics["rms_dbfs"]) / 20.0), 10 ** (float(reference_metrics["rms_dbfs"]) / 20.0)),
                ]
            )
        )
        technical_score = (
            identity_cosine * 5.0
            + emotion_speaker_cosine * 1.5
            + acoustic_match * 2.0
            + (0.5 if not metrics["pitch_trajectory_anomaly"] else -2.0)
            + (0.5 if float(metrics["clipping_fraction"]) < 0.0005 else -1.0)
            - abs(alpha - 0.60) * 0.15
        )
        receipt = {
            "schema_version": 1,
            "round_id": ROUND_ID,
            "sample_id": sample["sample_id"],
            "style": style,
            "style_label": STYLE_SPECS[style]["label"],
            "alpha": alpha,
            "target_text": sample["target_text"],
            "identity_audio_sha256": sha256_file(identity_audio),
            "emotion_audio_sha256": sha256_file(Path(sample["emotion_audio"])),
            "audio_path": str(output),
            "audio_sha256": sha256_file(output),
            "generation_seconds": round(time.perf_counter() - started, 4),
            "identity_cosine": round(identity_cosine, 6),
            "emotion_speaker_cosine": round(emotion_speaker_cosine, 6),
            "acoustic_match": round(acoustic_match, 6),
            "technical_score_without_asr": round(technical_score, 6),
            "acoustic_metrics": metrics,
            "emotion_reference_metrics": reference_metrics,
            "manual_listening_required": True,
            "production_promotion_allowed": False,
        }
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        results.append(receipt)

    model = None
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()
    summary = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "created_at": now_iso(),
        "matrix": str(matrix_path),
        "runtime": {
            "source_commit": json.loads((runtime.root / "restore_receipt.json").read_text())["source"]["actual_commit"],
            "model_revision": json.loads((runtime.root / "restore_receipt.json").read_text())["model"]["revision"],
            "device": "mps",
            "use_random": False,
            "greedy_generation": True,
            "diffusion_steps": 8,
        },
        "sample_count": len(results),
        "samples": results,
    }
    (output_root / "generation-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return {"sample_count": len(results), "summary": str(output_root / "generation-summary.json")}


def analyze_and_package(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    summary = json.loads((output_root / "generation-summary.json").read_text(encoding="utf-8"))
    whisper_model = Path(args.whisper_model).expanduser().resolve()
    if not whisper_model.is_dir():
        raise ReferenceBankError(f"Whisper model is missing: {whisper_model}")
    try:
        import mlx_whisper
    except ImportError as exc:
        raise ReferenceBankError("mlx-whisper is required for automatic text screening") from exc

    analyzed = []
    for receipt in summary["samples"]:
        audio = Path(receipt["audio_path"])
        asr = mlx_whisper.transcribe(
            str(audio),
            path_or_hf_repo=str(whisper_model),
            language="en",
            word_timestamps=False,
            condition_on_previous_text=False,
            verbose=False,
        )
        transcript = str(asr.get("text") or "").strip()
        similarity = text_similarity(receipt["target_text"], transcript)
        exact_tail = normalize_text(receipt["target_text"]).split()[-1:] == normalize_text(transcript).split()[-1:]
        technical_pass = (
            similarity >= 0.92
            and exact_tail
            and not receipt["acoustic_metrics"]["pitch_trajectory_anomaly"]
            and float(receipt["acoustic_metrics"]["clipping_fraction"]) < 0.001
            and receipt["identity_cosine"] >= 0.75
        )
        score = (
            receipt["technical_score_without_asr"]
            + similarity * 3.0
            + (0.5 if exact_tail else -2.0)
            + (0.5 if technical_pass else -0.5)
        )
        analyzed.append(
            {
                **receipt,
                "automatic_transcript": transcript,
                "text_similarity": round(similarity, 6),
                "final_word_matches": exact_tail,
                "technical_pass": technical_pass,
                "selection_score": round(score, 6),
            }
        )

    winners = []
    for style in STYLE_SPECS:
        candidates = [row for row in analyzed if row["style"] == style]
        passing = [row for row in candidates if row["technical_pass"]]
        pool = passing or candidates
        winner = max(pool, key=lambda row: row["selection_score"])
        winners.append(winner)

    review_root = output_root / "review"
    if review_root.exists():
        shutil.rmtree(review_root)
    (review_root / "audio").mkdir(parents=True)
    (review_root / "references").mkdir(parents=True)
    identity_audio = Path(args.identity_audio).expanduser().resolve()
    shutil.copy2(identity_audio, review_root / "references" / "identity.wav")
    public_rows = []
    answer_rows = []
    for ordinal, winner in enumerate(winners, 1):
        style = winner["style"]
        generated_name = f"{style}.wav"
        emotion_name = f"{style}-reference.wav"
        shutil.copy2(winner["audio_path"], review_root / "audio" / generated_name)
        emotion_source = next(
            Path(row["emotion_audio"])
            for row in json.loads((output_root / "matrix.json").read_text())["samples"]
            if row["style"] == style
        )
        shutil.copy2(emotion_source, review_root / "references" / emotion_name)
        public_rows.append(
            {
                "sample_id": winner["sample_id"],
                "ordinal": ordinal,
                "style": style,
                "style_label": winner["style_label"],
                "target_text": winner["target_text"],
                "emotion_scene": STYLE_SPECS[style]["emotion_scene"],
                "emotion_reference_text": STYLE_SPECS[style]["emotion_text"],
                "audio": f"audio/{generated_name}",
                "emotion_audio": f"references/{emotion_name}",
                "identity_audio": "references/identity.wav",
                "automatic_transcript": winner["automatic_transcript"],
                "technical_pass": winner["technical_pass"],
            }
        )
        answer_rows.append(winner)

    for asset in ("index.html", "styles.css", "app.js"):
        shutil.copy2(ASSET_ROOT / asset, review_root / asset)
    public = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "title": "Narrator IndexTTS2 — Reference Performance Validation",
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
                "answer_key_outside_review_root": True,
                "production_promotion_allowed": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (output_root / "answer-key.json").write_text(
        json.dumps(answer_rows, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (output_root / "analysis.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "round_id": ROUND_ID,
                "sample_count": len(analyzed),
                "technical_pass_count": sum(row["technical_pass"] for row in analyzed),
                "samples": analyzed,
                "winners": [row["sample_id"] for row in winners],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (output_root / "START_HERE.txt").write_text(
        "Narrator IndexTTS2 Reference Performance Validation\n"
        "===================================================\n\n"
        f"cd \"{review_root}\"\n"
        "python3 -m http.server 8774 --bind 127.0.0.1\n\n"
        "Then open http://127.0.0.1:8774/\n",
        encoding="utf-8",
    )
    return {
        "review": str(review_root / "index.html"),
        "generated_count": len(analyzed),
        "winner_count": len(winners),
        "technical_pass_count": sum(row["technical_pass"] for row in analyzed),
    }


def validate(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    review_root = output_root / "review"
    public_text = (review_root / "data.js").read_text(encoding="utf-8")
    prefix = "window.NARRATOR_INDEXTTS2_REFERENCE_DATA = "
    public = json.loads(public_text[len(prefix) :].rstrip().rstrip(";"))
    answer = json.loads((output_root / "answer-key.json").read_text(encoding="utf-8"))
    missing = []
    bad_hash = []
    for row in public["rows"]:
        audio = review_root / row["audio"]
        emotion = review_root / row["emotion_audio"]
        if not audio.is_file() or not emotion.is_file():
            missing.append(row["sample_id"])
        answer_row = next(item for item in answer if item["sample_id"] == row["sample_id"])
        if audio.is_file() and sha256_file(audio) != answer_row["audio_sha256"]:
            bad_hash.append(row["sample_id"])
    if missing or bad_hash:
        raise ReferenceBankError(f"Validation failed: missing={missing}, bad_hash={bad_hash}")
    return {
        "round_id": ROUND_ID,
        "candidate_count": len(public["rows"]),
        "missing_count": len(missing),
        "bad_hash_count": len(bad_hash),
        "review": str(review_root / "index.html"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the Narrator IndexTTS2 identity-plus-performance reference experiment.")
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("generate")
    generate.add_argument("--runtime-root", required=True)
    generate.add_argument("--identity-audio", required=True)
    generate.add_argument("--prior-triage", required=True)
    generate.add_argument("--context-root", required=True)
    generate.add_argument("--output-root", required=True)
    generate.add_argument("--force", action="store_true")
    package = sub.add_parser("package")
    package.add_argument("--output-root", required=True)
    package.add_argument("--identity-audio", required=True)
    package.add_argument("--whisper-model", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--output-root", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "generate":
            result = run_generation(args)
        elif args.command == "package":
            result = analyze_and_package(args)
        else:
            result = validate(args)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
