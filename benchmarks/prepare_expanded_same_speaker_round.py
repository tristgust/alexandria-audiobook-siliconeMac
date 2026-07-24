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
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf

from prepare_narrator_indextts2_reference_bank import (
    ReferenceBankError,
    acoustic_metrics,
    ratio_similarity,
    runtime_paths,
    sha256_file,
    text_similarity,
)

ROUND_ID = "alexandria_expanded_same_speaker_round_v1"
ASSET_ROOT = Path(__file__).with_name("expanded_same_speaker_assets")
TARGET_ORDER = ("narrator", "benny", "doctor")

SPECS: tuple[dict[str, Any], ...] = (
    {
        "target_key": "narrator",
        "target_label": "Narrator",
        "mode": "wounded_pleading",
        "mode_label": "Wounded pleading",
        "target_text": "I thought we could make this work, Stanley. I honestly believed you might listen.",
        "reference_text": "I wanted us to be happy here, Stanley. I really did. I wish I still thought that was possible.",
        "source_kind": "narrator_context",
        "source_name": "supplement-d5adebb5f24a9ca4.wav",
        "start_seconds": 0.0,
        "end_seconds": 6.55,
        "speaker_strategies": ("self",),
        "alphas": (0.30, 0.60),
        "purpose": "new_range",
    },
    {
        "target_key": "narrator",
        "target_label": "Narrator",
        "mode": "wounded_rage",
        "mode_label": "Wounded rage",
        "target_text": "You knew exactly what this would do, Stanley, and you chose to do it anyway.",
        "reference_text": "You. Who thought you were so clever. Now look where we are. My entire game is destroyed.",
        "source_kind": "narrator_context",
        "source_name": "supplement-6d16e628ab8c0825.wav",
        "start_seconds": 5.35,
        "end_seconds": 11.70,
        "speaker_strategies": ("self",),
        "alphas": (0.30, 0.60),
        "purpose": "new_range",
    },
    {
        "target_key": "narrator",
        "target_label": "Narrator",
        "mode": "exuberant_joy",
        "mode_label": "Exuberant joy",
        "target_text": "Yes! There it is, Stanley! You found it. You actually found it!",
        "reference_text": "Yes! We did it! Oh wow, that felt amazing.",
        "source_kind": "narrator_context",
        "source_name": "supplement-34d0f3167d261758.wav",
        "start_seconds": 1.10,
        "end_seconds": 5.55,
        "speaker_strategies": ("self",),
        "alphas": (0.30, 0.60),
        "purpose": "new_range",
    },
    {
        "target_key": "benny",
        "target_label": "Benny",
        "mode": "determined_resolve",
        "mode_label": "Determined resolve",
        "target_text": "We're done running. We choose the moment, we make the plan, and then we fight back.",
        "reference_text": "But we can't keep running and hiding. We decided to fight back. But we have to choose our moment. Wait until Brax shows his hand and makes his move.",
        "source_kind": "benny_download",
        "source_name": "bennyVoice1.mp3",
        "start_seconds": 5.00,
        "end_seconds": 15.80,
        "speaker_strategies": ("canonical",),
        "alphas": (0.30, 0.60),
        "purpose": "new_range",
    },
    {
        "target_key": "benny",
        "target_label": "Benny",
        "mode": "emergency_distress_repair",
        "mode_label": "Emergency distress · clean-voice repair",
        "target_text": "This is Bernice Summerfield. I'm trapped below the excavation site, and the chamber is collapsing.",
        "reference_text": "I'm trapped in a pyramid. Yes, a pyramid, roughly four kilometres southeast of colony sector five. My guide's dead.",
        "source_kind": "benny_download",
        "source_name": "bennyVoice3.mp3",
        "start_seconds": 8.30,
        "end_seconds": 17.95,
        "speaker_strategies": ("canonical",),
        "alphas": (0.30, 0.60),
        "purpose": "repair",
    },
    {
        "target_key": "benny",
        "target_label": "Benny",
        "mode": "excited_discovery_repair",
        "mode_label": "Excited discovery · playback repair",
        "target_text": "This is extraordinary. These markings predate the colony by thousands of years.",
        "reference_text": "A previously undiscovered civilization. This alone has made the trip worthwhile, but who knows what I'll find inside the tomb itself.",
        "source_kind": "benny_download",
        "source_name": "bennyVoice4.mp3",
        "start_seconds": 9.78,
        "end_seconds": 18.10,
        "speaker_strategies": ("canonical",),
        "alphas": (0.30, 0.60),
        "purpose": "repair",
    },
    {
        "target_key": "doctor",
        "target_label": "Doctor",
        "mode": "cold_existential_dismissal",
        "mode_label": "Cold existential dismissal",
        "target_text": "You are an echo pretending to be a man. Nothing more.",
        "reference_text": "You're not real. You never were. You never will be. You exist in this instant.",
        "source_kind": "doctor_upload",
        "source_name": "dw7voice2.wav",
        "start_seconds": 0.0,
        "end_seconds": 6.10,
        "speaker_strategies": ("self", "character_bank"),
        "alphas": (0.55,),
        "purpose": "new_upload",
    },
    {
        "target_key": "doctor",
        "target_label": "Doctor",
        "mode": "dry_sarcasm",
        "mode_label": "Dry sarcasm",
        "target_text": "Oh, brilliant. Another impossible machine with no instructions and a very large red button.",
        "reference_text": "She always puts you down, tells you how stupid you are. I can see what she means. I might as well be talking to a door.",
        "source_kind": "doctor_bank",
        "source_name": "doctor_dry_irritated.wav",
        "start_seconds": None,
        "end_seconds": None,
        "speaker_strategies": ("self", "character_bank"),
        "alphas": (0.30,),
        "purpose": "new_range",
    },
    {
        "target_key": "doctor",
        "target_label": "Doctor",
        "mode": "protective_authority_repair",
        "mode_label": "Protective authority · identity repair",
        "target_text": "Stay behind me. Whatever happens, do not let go of my hand.",
        "reference_text": "I'm the Doctor, and I take care of my friends.",
        "source_kind": "doctor_clip",
        "source_name": "sample_0208.wav",
        "start_seconds": None,
        "end_seconds": None,
        "speaker_strategies": ("self", "character_bank"),
        "alphas": (0.15,),
        "purpose": "repair",
    },
)


class ExpandedRoundError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fingerprint(value: Any, length: int = 16) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def normalize_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", value.casefold()))


def resolve_source(
    spec: dict[str, Any],
    *,
    narrator_context_root: Path,
    benny_root: Path,
    doctor_bank_root: Path,
    doctor_upload_root: Path,
) -> Path:
    kind = spec["source_kind"]
    if kind == "narrator_context":
        path = narrator_context_root / "review" / "audio" / spec["source_name"]
    elif kind == "benny_download":
        path = benny_root / spec["source_name"]
    elif kind == "doctor_upload":
        path = doctor_upload_root / "clips" / spec["source_name"]
    elif kind == "doctor_bank":
        path = doctor_bank_root / "banks" / spec["source_name"]
    elif kind == "doctor_clip":
        path = doctor_bank_root / "clips" / spec["source_name"]
    else:
        raise ExpandedRoundError(f"Unsupported source kind: {kind}")
    path = path.expanduser().resolve()
    if not path.is_file():
        raise ExpandedRoundError(f"Reference source is missing: {path}")
    return path


def normalize_audio(source: Path, output: Path, start: float | None, end: float | None) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".temporary.wav")
    command = ["ffmpeg", "-v", "error", "-y"]
    if start is not None:
        command += ["-ss", f"{start:.3f}"]
    if end is not None:
        command += ["-to", f"{end:.3f}"]
    command += ["-i", str(source), "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(temporary)]
    subprocess.run(command, check=True)
    audio, sample_rate = sf.read(temporary, dtype="float32", always_2d=True)
    temporary.unlink(missing_ok=True)
    mono = np.mean(audio, axis=1, dtype=np.float32)
    if mono.size < int(sample_rate * 0.8):
        raise ExpandedRoundError(f"Audio is too short after normalization: {source}")
    peak = float(np.max(np.abs(mono)))
    if peak > 0:
        mono *= min(1.0, 0.70 / peak)
    sf.write(output, mono, 24000, subtype="PCM_16")


def concatenate_audio(sources: list[tuple[Path, float | None, float | None]], output: Path) -> None:
    pieces: list[np.ndarray] = []
    silence = np.zeros(int(24000 * 0.14), dtype=np.float32)
    temporary_root = output.parent / ".temporary-pieces"
    temporary_root.mkdir(parents=True, exist_ok=True)
    for index, (source, start, end) in enumerate(sources):
        piece = temporary_root / f"piece-{index:02d}.wav"
        normalize_audio(source, piece, start, end)
        audio, rate = sf.read(piece, dtype="float32", always_2d=True)
        if rate != 24000:
            raise ExpandedRoundError(f"Unexpected temporary sample rate: {piece}")
        if index:
            pieces.append(silence)
        pieces.append(np.mean(audio, axis=1, dtype=np.float32))
    merged = np.concatenate(pieces)
    peak = float(np.max(np.abs(merged)))
    if peak > 0:
        merged *= min(1.0, 0.70 / peak)
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, merged, 24000, subtype="PCM_16")
    shutil.rmtree(temporary_root)


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    narrator_context = Path(args.narrator_context_root).expanduser().resolve()
    benny_root = Path(args.benny_root).expanduser().resolve()
    doctor_bank = Path(args.doctor_bank_root).expanduser().resolve()
    doctor_upload = Path(args.doctor_upload_root).expanduser().resolve()
    canonical_root = Path(args.canonical_reference_root).expanduser().resolve()
    canonical = {
        "narrator": canonical_root / "narrator" / "conditioning.wav",
        "benny": canonical_root / "benny" / "conditioning.wav",
        "doctor": doctor_bank / "banks" / "doctor_core_identity.wav",
    }
    doctor_character_bank = doctor_bank / "banks" / "doctor_calm_authoritative.wav"
    for key, path in {**canonical, "doctor_character_bank": doctor_character_bank}.items():
        if not path.is_file():
            raise ExpandedRoundError(f"Required identity source is missing for {key}: {path}")

    references_root = output_root / "references"
    references_root.mkdir(parents=True, exist_ok=True)
    canonical_normalized: dict[str, Path] = {}
    for key, source in canonical.items():
        target = references_root / f"canonical-{key}.wav"
        normalize_audio(source, target, None, None)
        canonical_normalized[key] = target
    doctor_character_normalized = references_root / "doctor-character-bank.wav"
    normalize_audio(doctor_character_bank, doctor_character_normalized, None, None)

    actor_source_3 = doctor_upload / "clips" / "dw7voice3.wav"
    actor_source_4 = doctor_upload / "clips" / "dw7voice4.wav"
    for source in (actor_source_3, actor_source_4):
        if not source.is_file():
            raise ExpandedRoundError(f"Recovered Doctor actor identity clip is missing: {source}")
    doctor_actor_identity = references_root / "doctor-actor-identity.wav"
    concatenate_audio(
        [
            (actor_source_3, 12.90, 21.50),
            (actor_source_4, 0.0, 9.90),
        ],
        doctor_actor_identity,
    )

    routes = []
    samples = []
    for spec in SPECS:
        source = resolve_source(
            spec,
            narrator_context_root=narrator_context,
            benny_root=benny_root,
            doctor_bank_root=doctor_bank,
            doctor_upload_root=doctor_upload,
        )
        reference = references_root / f"{spec['target_key']}-{spec['mode']}.wav"
        normalize_audio(source, reference, spec["start_seconds"], spec["end_seconds"])
        route = {
            **spec,
            "source_audio": str(source),
            "source_audio_sha256": sha256_file(source),
            "reference_audio": str(reference),
            "reference_audio_sha256": sha256_file(reference),
            "canonical_identity_audio": str(canonical_normalized[spec["target_key"]]),
            "canonical_identity_sha256": sha256_file(canonical_normalized[spec["target_key"]]),
            "doctor_actor_identity_audio": str(doctor_actor_identity) if spec["target_key"] == "doctor" else None,
            "doctor_actor_identity_sha256": sha256_file(doctor_actor_identity) if spec["target_key"] == "doctor" else None,
        }
        routes.append(route)
        for strategy in spec["speaker_strategies"]:
            if strategy == "self":
                speaker = reference
            elif strategy == "canonical":
                speaker = canonical_normalized[spec["target_key"]]
            elif strategy == "character_bank":
                speaker = doctor_character_normalized
            else:
                raise ExpandedRoundError(f"Unsupported speaker strategy: {strategy}")
            for alpha in spec["alphas"]:
                sample_id = fingerprint(
                    {
                        "round": ROUND_ID,
                        "target": spec["target_key"],
                        "mode": spec["mode"],
                        "strategy": strategy,
                        "alpha": alpha,
                        "speaker": sha256_file(speaker),
                        "reference": sha256_file(reference),
                        "text": spec["target_text"],
                    }
                )
                samples.append(
                    {
                        "sample_id": sample_id,
                        "target_key": spec["target_key"],
                        "target_label": spec["target_label"],
                        "mode": spec["mode"],
                        "mode_label": spec["mode_label"],
                        "purpose": spec["purpose"],
                        "target_text": spec["target_text"],
                        "reference_text": spec["reference_text"],
                        "speaker_strategy": strategy,
                        "alpha": float(alpha),
                        "speaker_audio": str(speaker),
                        "speaker_audio_sha256": sha256_file(speaker),
                        "reference_audio": str(reference),
                        "reference_audio_sha256": sha256_file(reference),
                        "canonical_identity_audio": route["canonical_identity_audio"],
                        "canonical_identity_sha256": route["canonical_identity_sha256"],
                        "doctor_actor_identity_audio": route["doctor_actor_identity_audio"],
                        "doctor_actor_identity_sha256": route["doctor_actor_identity_sha256"],
                    }
                )
    matrix = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "created_at": now_iso(),
        "route_count": len(routes),
        "sample_count": len(samples),
        "target_order": list(TARGET_ORDER),
        "routes": routes,
        "samples": samples,
        "production_promotion_allowed": False,
    }
    path = output_root / "matrix.json"
    path.write_text(json.dumps(matrix, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "route_count": len(routes),
        "sample_count": len(samples),
        "doctor_actor_identity": str(doctor_actor_identity),
        "matrix": str(path),
    }


def cosine(left: np.ndarray, right: np.ndarray) -> float:
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    return float(np.dot(left, right) / denominator) if denominator > 0 else 0.0


def generate(args: argparse.Namespace) -> dict[str, Any]:
    import torch
    import torchaudio

    runtime = runtime_paths(Path(args.runtime_root).expanduser().resolve())
    output_root = Path(args.output_root).expanduser().resolve()
    matrix_path = output_root / "matrix.json"
    if not matrix_path.is_file():
        raise ExpandedRoundError(f"Prepare the matrix first: {matrix_path}")
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
            audio.to(model.device),
            num_mel_bins=80,
            dither=0,
            sample_frequency=16000,
        )
        feat = feat - feat.mean(dim=0, keepdim=True)
        with torch.inference_mode():
            embedding = model.campplus_model(feat.unsqueeze(0)).float()
        return embedding.detach().cpu().numpy().reshape(-1)

    embedding_cache: dict[str, np.ndarray] = {}
    metric_cache: dict[str, dict[str, Any]] = {}
    for sample in matrix["samples"]:
        for key in (
            "speaker_audio",
            "reference_audio",
            "canonical_identity_audio",
            "doctor_actor_identity_audio",
        ):
            value = sample.get(key)
            if not value:
                continue
            path = Path(value)
            digest = sha256_file(path)
            if digest not in embedding_cache:
                embedding_cache[digest] = speaker_embedding(path)
            if digest not in metric_cache:
                words = len(sample["reference_text"].split()) if key == "reference_audio" else 2
                metric_cache[digest] = acoustic_metrics(path, words)

    generated_root = output_root / "generated"
    receipt_root = output_root / "generation-receipts"
    results = []
    for sample in matrix["samples"]:
        output = (
            generated_root
            / sample["target_key"]
            / sample["mode"]
            / f"{sample['speaker_strategy']}-alpha-{sample['alpha']:.2f}.wav"
        )
        receipt_path = (
            receipt_root
            / sample["target_key"]
            / sample["mode"]
            / f"{sample['speaker_strategy']}-alpha-{sample['alpha']:.2f}.json"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        if output.is_file() and receipt_path.is_file() and not args.force:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("audio_sha256") == sha256_file(output):
                results.append(receipt)
                continue

        random.seed(20260724)
        np.random.seed(20260724)
        torch.manual_seed(20260724)
        started = time.perf_counter()
        returned = model.infer(
            spk_audio_prompt=sample["speaker_audio"],
            text=sample["target_text"],
            output_path=str(output),
            emo_audio_prompt=sample["reference_audio"],
            emo_alpha=float(sample["alpha"]),
            use_random=False,
            verbose=False,
            num_beams=1,
            max_mel_tokens=700,
        )
        if not output.is_file():
            raise ExpandedRoundError(f"Generation did not create {output}; returned {returned!r}")

        generated_embedding = speaker_embedding(output)
        generated_metrics = acoustic_metrics(output, len(sample["target_text"].split()))
        canonical_embedding = embedding_cache[sample["canonical_identity_sha256"]]
        reference_embedding = embedding_cache[sample["reference_audio_sha256"]]
        speaker_prompt_embedding = embedding_cache[sample["speaker_audio_sha256"]]
        reference_metrics = metric_cache[sample["reference_audio_sha256"]]
        canonical_cosine = cosine(generated_embedding, canonical_embedding)
        reference_cosine = cosine(generated_embedding, reference_embedding)
        speaker_prompt_cosine = cosine(generated_embedding, speaker_prompt_embedding)
        doctor_actor_cosine = None
        if sample.get("doctor_actor_identity_sha256"):
            doctor_actor_cosine = cosine(
                generated_embedding,
                embedding_cache[sample["doctor_actor_identity_sha256"]],
            )
        acoustic_match = float(
            np.mean(
                [
                    ratio_similarity(
                        float(generated_metrics["pitch_median_hz"]),
                        float(reference_metrics["pitch_median_hz"]),
                    ),
                    ratio_similarity(
                        float(generated_metrics["pitch_p90_hz"] - generated_metrics["pitch_p10_hz"]),
                        float(reference_metrics["pitch_p90_hz"] - reference_metrics["pitch_p10_hz"]),
                    ),
                    ratio_similarity(
                        float(generated_metrics["words_per_second"]),
                        float(reference_metrics["words_per_second"]),
                    ),
                    ratio_similarity(
                        10 ** (float(generated_metrics["rms_dbfs"]) / 20.0),
                        10 ** (float(reference_metrics["rms_dbfs"]) / 20.0),
                    ),
                ]
            )
        )
        score = (
            canonical_cosine * 3.5
            + reference_cosine * 4.0
            + speaker_prompt_cosine * 1.5
            + acoustic_match * 2.0
            + (doctor_actor_cosine * 0.75 if doctor_actor_cosine is not None else 0.0)
            + (0.5 if not generated_metrics["pitch_trajectory_anomaly"] else -2.0)
            + (0.5 if float(generated_metrics["clipping_fraction"]) < 0.001 else -1.5)
        )
        receipt = {
            **sample,
            "schema_version": 1,
            "round_id": ROUND_ID,
            "audio_path": str(output),
            "audio_sha256": sha256_file(output),
            "generation_seconds": round(time.perf_counter() - started, 4),
            "canonical_identity_cosine": round(canonical_cosine, 6),
            "style_reference_cosine": round(reference_cosine, 6),
            "speaker_prompt_cosine": round(speaker_prompt_cosine, 6),
            "doctor_actor_identity_cosine": round(doctor_actor_cosine, 6) if doctor_actor_cosine is not None else None,
            "acoustic_match": round(acoustic_match, 6),
            "technical_score_without_asr": round(score, 6),
            "acoustic_metrics": generated_metrics,
            "reference_metrics": reference_metrics,
            "manual_listening_required": True,
            "production_promotion_allowed": False,
        }
        receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        results.append(receipt)

    model = None
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()
    summary = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "created_at": now_iso(),
        "sample_count": len(results),
        "runtime": {
            "device": "mps",
            "greedy_generation": True,
            "use_random": False,
            "diffusion_steps": 8,
            "pre_int16_vocoder_scale": 0.70,
        },
        "samples": results,
        "production_promotion_allowed": False,
    }
    path = output_root / "generation-summary.json"
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"sample_count": len(results), "summary": str(path)}


def reencode_review_audio(source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(output),
        ],
        check=True,
    )


def technical_pass(row: dict[str, Any], similarity: float, final_word: bool) -> bool:
    if similarity < 0.92 or not final_word:
        return False
    if row["acoustic_metrics"]["pitch_trajectory_anomaly"]:
        return False
    if float(row["acoustic_metrics"]["clipping_fraction"]) >= 0.001:
        return False
    if row["acoustic_match"] < 0.50:
        return False
    if row["target_key"] == "doctor":
        # Interview speech is useful as a secondary tie-breaker, but the Doctor's
        # performed character voice can legitimately diverge from the actor's
        # present-day conversational register. Do not reject an authentic
        # in-character match solely for low interview-voice similarity.
        return (
            row["canonical_identity_cosine"] >= 0.64
            and row["style_reference_cosine"] >= 0.69
        )
    return (
        row["style_reference_cosine"] >= 0.78
        or (
            row["style_reference_cosine"] >= 0.72
            and row["canonical_identity_cosine"] >= 0.72
        )
    )


def package(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    whisper_model = Path(args.whisper_model).expanduser().resolve()
    if not whisper_model.is_dir():
        raise ExpandedRoundError(f"Whisper model is missing: {whisper_model}")
    summary_path = output_root / "generation-summary.json"
    matrix_path = output_root / "matrix.json"
    if not summary_path.is_file() or not matrix_path.is_file():
        raise ExpandedRoundError("Generation summary or matrix is missing")
    import mlx_whisper

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    analyzed = []
    for row in summary["samples"]:
        result = mlx_whisper.transcribe(
            row["audio_path"],
            path_or_hf_repo=str(whisper_model),
            language="en",
            word_timestamps=False,
            condition_on_previous_text=False,
            verbose=False,
        )
        transcript = str(result.get("text") or "").strip()
        similarity = text_similarity(row["target_text"], transcript)
        expected_words = normalize_text(row["target_text"]).split()
        actual_words = normalize_text(transcript).split()
        final_word = bool(expected_words and actual_words and expected_words[-1] == actual_words[-1])
        passed = technical_pass(row, similarity, final_word)
        score = (
            row["technical_score_without_asr"]
            + similarity * 3.0
            + (0.5 if final_word else -2.0)
            + (0.75 if passed else -0.75)
        )
        analyzed.append(
            {
                **row,
                "automatic_transcript": transcript,
                "text_similarity": round(similarity, 6),
                "final_word_matches": final_word,
                "technical_pass": passed,
                "selection_score": round(score, 6),
            }
        )

    winners = []
    excluded = []
    for route in matrix["routes"]:
        candidates = [
            row
            for row in analyzed
            if row["target_key"] == route["target_key"] and row["mode"] == route["mode"]
        ]
        passing = [row for row in candidates if row["technical_pass"]]
        if not passing:
            excluded.append(
                {
                    "target_key": route["target_key"],
                    "mode": route["mode"],
                    "reason": "no_candidate_passed_automatic_gate",
                    "best": max(candidates, key=lambda row: row["selection_score"]),
                }
            )
            continue
        winners.append(max(passing, key=lambda row: row["selection_score"]))

    review_root = output_root / "review"
    if review_root.exists():
        shutil.rmtree(review_root)
    (review_root / "audio").mkdir(parents=True)
    (review_root / "targets").mkdir(parents=True)
    (review_root / "references").mkdir(parents=True)
    for asset in ("index.html", "app.js", "styles.css"):
        shutil.copy2(ASSET_ROOT / asset, review_root / asset)

    public_rows = []
    answer_rows = []
    target_files: dict[str, str] = {}
    for ordinal, winner in enumerate(winners, 1):
        target_key = winner["target_key"]
        target_name = f"{target_key}.wav"
        if target_key not in target_files:
            target_output = review_root / "targets" / target_name
            reencode_review_audio(Path(winner["canonical_identity_audio"]), target_output)
            target_files[target_key] = f"targets/{target_name}"
        reference_name = f"{target_key}-{winner['mode']}.wav"
        generated_name = f"{winner['sample_id']}.wav"
        reference_output = review_root / "references" / reference_name
        generated_output = review_root / "audio" / generated_name
        reencode_review_audio(Path(winner["reference_audio"]), reference_output)
        reencode_review_audio(Path(winner["audio_path"]), generated_output)
        answer = {
            **winner,
            "packaged_target_sha256": sha256_file(review_root / target_files[target_key]),
            "packaged_reference_sha256": sha256_file(reference_output),
            "packaged_audio_sha256": sha256_file(generated_output),
            "packaged_sample_rate": 24000,
            "packaged_format": "pcm_s16le_mono",
        }
        answer_rows.append(answer)
        public_rows.append(
            {
                "sample_id": winner["sample_id"],
                "ordinal": ordinal,
                "target_key": target_key,
                "target_label": winner["target_label"],
                "mode": winner["mode"],
                "mode_label": winner["mode_label"],
                "purpose": winner["purpose"],
                "expected_text": winner["target_text"],
                "target_audio": target_files[target_key],
                "reference_audio": f"references/{reference_name}",
                "generated_audio": f"audio/{generated_name}",
                "automatic_transcript": winner["automatic_transcript"],
                "technical_pass": winner["technical_pass"],
            }
        )

    public = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "title": "Expanded same-speaker performance validation",
        "created_at": now_iso(),
        "candidate_count": len(public_rows),
        "target_order": list(TARGET_ORDER),
        "rows": public_rows,
        "production_promotion_allowed": False,
    }
    (review_root / "data.js").write_text(
        "window.EXPANDED_SAME_SPEAKER_DATA = " + json.dumps(public, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    (review_root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "round_id": ROUND_ID,
                "candidate_count": len(public_rows),
                "excluded_count": len(excluded),
                "answer_key_outside_review_root": True,
                "model_names_exposed": False,
                "all_review_audio_pcm_24khz_mono": True,
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
    (output_root / "analysis.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "round_id": ROUND_ID,
                "sample_count": len(analyzed),
                "technical_pass_count": sum(row["technical_pass"] for row in analyzed),
                "winner_count": len(winners),
                "excluded": excluded,
                "samples": analyzed,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (output_root / "START_HERE.txt").write_text(
        "Expanded same-speaker performance validation\n"
        "============================================\n\n"
        f"cd \"{review_root}\"\n"
        "python3 -m http.server 8781 --bind 127.0.0.1\n\n"
        "Then open http://127.0.0.1:8781/\n",
        encoding="utf-8",
    )
    return {
        "review": str(review_root / "index.html"),
        "generated_count": len(analyzed),
        "winner_count": len(winners),
        "excluded_count": len(excluded),
    }


def validate(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    review_root = output_root / "review"
    prefix = "window.EXPANDED_SAME_SPEAKER_DATA = "
    data_text = (review_root / "data.js").read_text(encoding="utf-8").strip()
    public = json.loads(data_text[len(prefix) :].rstrip(";"))
    answers = {
        row["sample_id"]: row
        for row in json.loads((output_root / "answer-key.json").read_text(encoding="utf-8"))
    }
    missing = []
    bad_hash = []
    bad_audio = []
    for row in public["rows"]:
        generated = review_root / row["generated_audio"]
        target = review_root / row["target_audio"]
        reference = review_root / row["reference_audio"]
        if not generated.is_file() or not target.is_file() or not reference.is_file():
            missing.append(row["sample_id"])
            continue
        answer = answers[row["sample_id"]]
        if sha256_file(generated) != answer["packaged_audio_sha256"]:
            bad_hash.append(row["sample_id"])
        for path in (generated, target, reference):
            info = sf.info(path)
            if info.samplerate != 24000 or info.channels != 1 or info.duration < 0.8:
                bad_audio.append(str(path))
    visible = (review_root / "index.html").read_text(encoding="utf-8")
    if re.search(r"OpenVoice|Seed-VC|SeedVC|IndexTTS2", visible, re.IGNORECASE):
        raise ExpandedRoundError("Model name leaked into public review")
    if missing or bad_hash or bad_audio:
        raise ExpandedRoundError(
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
    parser = argparse.ArgumentParser(description="Build the expanded same-speaker performance round.")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--output-root", required=True)
    prepare_parser.add_argument("--narrator-context-root", required=True)
    prepare_parser.add_argument("--benny-root", required=True)
    prepare_parser.add_argument("--doctor-bank-root", required=True)
    prepare_parser.add_argument("--doctor-upload-root", required=True)
    prepare_parser.add_argument("--canonical-reference-root", required=True)
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
    except (ExpandedRoundError, ReferenceBankError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
