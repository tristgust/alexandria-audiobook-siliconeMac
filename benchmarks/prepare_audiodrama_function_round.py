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
from prepare_same_speaker_performance_validation import SameSpeakerError

ROUND_ID = "alexandria_audiodrama_function_round_v1"
TARGET_ORDER = ("narrator", "benny", "doctor")
ASSET_ROOT = Path(__file__).with_name("lazy_voice_followup_assets")
RANGE_SERVER = Path(__file__).with_name("range_http_server.py")

ROUTES: tuple[dict[str, Any], ...] = (
    {
        "target_key": "narrator",
        "target_label": "Narrator",
        "mode": "reflective_conversation",
        "mode_label": "Reflective conversation",
        "function": "conversation",
        "target_text": "Perhaps the answer was never hidden at all; perhaps Stanley had simply been asking the wrong question.",
        "reference_text": "Wouldn't wherever we end up be our destination, even if there's no story there?",
        "source": "narrator_context",
        "source_name": "correction-4ee56800e27fb05d.wav",
        "speaker_strategy": "self",
        "alpha": 0.30,
    },
    {
        "target_key": "narrator",
        "target_label": "Narrator",
        "mode": "warm_relief",
        "mode_label": "Warm relief",
        "function": "warmth_vulnerability",
        "target_text": "There you are. For a moment, Stanley, I genuinely thought I had lost you.",
        "reference_text": "Oh, thank God you lived. You had me worried there for a moment.",
        "source": "narrator_context",
        "source_name": "supplement-03eaf09f4252287e.wav",
        "speaker_strategy": "self",
        "alpha": 0.30,
    },
    {
        "target_key": "narrator",
        "target_label": "Narrator",
        "mode": "dry_amusement",
        "mode_label": "Dry amusement",
        "function": "comic_amused",
        "target_text": "Well, this is going beautifully. By which I mean it has already become a complete disaster.",
        "reference_text": "Okay, I'm over it now. What do you think? Are you sick of this gag yet?",
        "source": "narrator_context",
        "source_name": "correction-ce07cc750653d3d8.wav",
        "speaker_strategy": "self",
        "alpha": 0.30,
    },
    {
        "target_key": "narrator",
        "target_label": "Narrator",
        "mode": "restrained_concern",
        "mode_label": "Restrained concern",
        "function": "urgent_afraid",
        "target_text": "Stanley, stop. Something is wrong with the lift, and I need you to step away from the doors.",
        "reference_text": "Don't go anywhere. I can't follow you there. I can't help you.",
        "source": "narrator_context",
        "source_name": "correction-ff3107a98817296b.wav",
        "speaker_strategy": "self",
        "alpha": 0.20,
    },
    {
        "target_key": "narrator",
        "target_label": "Narrator",
        "mode": "firm_authority",
        "mode_label": "Firm authority",
        "function": "confrontation_authority",
        "target_text": "You have made your choice, Stanley. Now you will remain here and face what follows.",
        "reference_text": "You'd like to know where your co-workers are? A moment of solace before you're obliterated.",
        "source": "narrator_context",
        "source_name": "supplement-7653ebeed8096728.wav",
        "speaker_strategy": "self",
        "alpha": 0.45,
    },
    {
        "target_key": "benny",
        "target_label": "Benny",
        "mode": "sardonic_conversation",
        "mode_label": "Sardonic conversation",
        "function": "conversation",
        "target_text": "Right, because ancient alien machinery always comes with a clearly labelled off switch.",
        "reference_text": "Until then, we're acting normal. And normal for me is digging up stuff.",
        "source": "benny_file",
        "source_name": "bennyVoice1.mp3",
        "start": 16.20,
        "end": 21.95,
        "speaker_strategy": "canonical",
        "alpha": 0.30,
    },
    {
        "target_key": "benny",
        "target_label": "Benny",
        "mode": "vulnerable_honesty",
        "mode_label": "Vulnerable honesty",
        "function": "warmth_vulnerability",
        "target_text": "I thought I could handle this. I was wrong, and I need you to stay with me.",
        "reference_text": "I'm trapped in a pyramid. Yes, a pyramid. My guide's dead.",
        "source": "benny_file",
        "source_name": "bennyVoice3.mp3",
        "start": 8.30,
        "end": 17.95,
        "speaker_strategy": "canonical",
        "alpha": 0.15,
    },
    {
        "target_key": "benny",
        "target_label": "Benny",
        "mode": "delighted_curiosity",
        "mode_label": "Delighted curiosity",
        "function": "comic_amused",
        "target_text": "Oh, that's clever. Horribly dangerous, obviously, but undeniably clever.",
        "reference_text": "This alone has made the trip worthwhile, but who knows what I'll find inside the tomb itself.",
        "source": "benny_file",
        "source_name": "bennyVoice4.mp3",
        "start": 12.60,
        "end": 18.10,
        "speaker_strategy": "self",
        "alpha": 0.60,
    },
    {
        "target_key": "benny",
        "target_label": "Benny",
        "mode": "urgent_fear",
        "mode_label": "Urgent fear",
        "function": "urgent_afraid",
        "target_text": "Move! The ceiling is coming down, and we have seconds at most.",
        "reference_text": "I'm trapped in a pyramid. Yes, a pyramid. My guide's dead.",
        "source": "benny_file",
        "source_name": "bennyVoice3.mp3",
        "start": 8.30,
        "end": 17.95,
        "speaker_strategy": "canonical",
        "alpha": 0.30,
    },
    {
        "target_key": "benny",
        "target_label": "Benny",
        "mode": "controlled_anger",
        "mode_label": "Controlled anger",
        "function": "confrontation_authority",
        "target_text": "No. You don't get to threaten my people and walk away as though nothing happened.",
        "reference_text": "But we can't keep running and hiding. We decided to fight back. But we have to choose our moment.",
        "source": "benny_file",
        "source_name": "bennyVoice1.mp3",
        "start": 5.00,
        "end": 12.10,
        "speaker_strategy": "canonical",
        "alpha": 0.60,
    },
    {
        "target_key": "doctor",
        "target_label": "Doctor",
        "mode": "playful_eccentricity",
        "mode_label": "Playful eccentricity",
        "function": "conversation",
        "target_text": "Oh, wonderful. A locked door, a missing key, and precisely no time to think.",
        "reference_text": "Ace, have you no sense of occasion?",
        "source": "doctor_nuclear",
        "start": 0.00,
        "end": 1.75,
        "speaker_strategy": "self",
        "alpha": 0.20,
        "alternate_strategy": "character_bank",
    },
    {
        "target_key": "doctor",
        "target_label": "Doctor",
        "mode": "quiet_compassion",
        "mode_label": "Quiet compassion",
        "function": "warmth_vulnerability",
        "target_text": "I'm sorry. I know what this cost you, and I wish I could change it.",
        "reference_text": "I'm sorry, Morgaine. It's over.",
        "source": "doctor_nuclear",
        "start": 229.10,
        "end": 231.85,
        "speaker_strategy": "self",
        "alpha": 0.20,
        "alternate_strategy": "character_bank",
    },
    {
        "target_key": "doctor",
        "target_label": "Doctor",
        "mode": "dry_wit",
        "mode_label": "Dry wit",
        "function": "comic_amused",
        "target_text": "Yes, of course. The universe is ending, and the instructions are in another language.",
        "reference_text": "Oh, well, that sorts that out. I've got to give myself more warning.",
        "source": "doctor_composite_dry",
        "speaker_strategy": "character_bank",
        "alpha": 0.10,
        "alternate_strategy": "self",
    },
    {
        "target_key": "doctor",
        "target_label": "Doctor",
        "mode": "urgent_command",
        "mode_label": "Urgent command",
        "function": "urgent_afraid",
        "target_text": "Ace, get everyone out. Now. Do not stop for anything.",
        "reference_text": "Brigadier, you and Ace, see to this ship.",
        "source": "doctor_nuclear",
        "start": 51.00,
        "end": 53.20,
        "speaker_strategy": "self",
        "alpha": 0.25,
        "alternate_strategy": "character_bank",
    },
    {
        "target_key": "doctor",
        "target_label": "Doctor",
        "mode": "grave_warning",
        "mode_label": "Grave warning",
        "function": "confrontation_authority",
        "target_text": "If you activate that device, everyone in this city dies. Put it down.",
        "reference_text": "If this missile explodes, millions will die. You will die.",
        "source": "doctor_nuclear",
        "start": 94.90,
        "end": 99.95,
        "speaker_strategy": "self",
        "alpha": 0.30,
        "alternate_strategy": "character_bank",
    },
)

COVERAGE_LEDGER = {
    "schema_version": 1,
    "purpose": "audiodrama_voice_function_coverage",
    "characters": {
        "narrator": {
            "approved_existing": ["neutral", "panic", "smug_menace", "wounded_pleading"],
            "rejected_existing": ["wounded_rage", "exuberant_joy"],
            "under_test": [r["mode"] for r in ROUTES if r["target_key"] == "narrator"],
        },
        "benny": {
            "approved_existing": ["neutral", "determined_resolve", "emergency_distress", "excited_discovery"],
            "rejected_existing": [],
            "under_test": [r["mode"] for r in ROUTES if r["target_key"] == "benny"],
        },
        "doctor": {
            "approved_existing": ["cold_existential_dismissal"],
            "conditional_existing": ["dark_warning"],
            "rejected_existing": ["protective_authority"],
            "under_test": [r["mode"] for r in ROUTES if r["target_key"] == "doctor"],
        },
    },
}


class AudiodramaRoundError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fingerprint(value: Any, length: int = 16) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def normalize_audio(source: Path, output: Path, start: float | None = None, end: float | None = None) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.wav")
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
    if mono.size < int(sample_rate * 0.7):
        raise AudiodramaRoundError(f"Reference is too short: {source}")
    peak = float(np.max(np.abs(mono)))
    if peak > 0:
        mono *= min(1.0, 0.70 / peak)
    sf.write(output, mono, 24000, subtype="PCM_16")


def concatenate_segments(source: Path, segments: list[tuple[float, float]], output: Path) -> None:
    pieces: list[np.ndarray] = []
    silence = np.zeros(int(24000 * 0.12), dtype=np.float32)
    for index, (start, end) in enumerate(segments):
        temporary = output.with_name(f"{output.stem}-{index}.wav")
        normalize_audio(source, temporary, start, end)
        audio, sample_rate = sf.read(temporary, dtype="float32", always_2d=True)
        temporary.unlink(missing_ok=True)
        if sample_rate != 24000:
            raise AudiodramaRoundError("Composite segment sample rate mismatch")
        if index:
            pieces.append(silence)
        pieces.append(np.mean(audio, axis=1, dtype=np.float32))
    merged = np.concatenate(pieces)
    peak = float(np.max(np.abs(merged)))
    if peak > 0:
        merged *= min(1.0, 0.70 / peak)
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, merged, 24000, subtype="PCM_16")


def resolve_source(route: dict[str, Any], args: argparse.Namespace) -> tuple[Path, float | None, float | None]:
    kind = route["source"]
    if kind == "narrator_context":
        source = Path(args.narrator_context_root) / "review" / "audio" / route["source_name"]
    elif kind == "benny_file":
        source = Path(args.benny_root) / route["source_name"]
    elif kind in {"doctor_nuclear", "doctor_composite_dry"}:
        source = Path(args.doctor_nuclear_audio)
    else:
        raise AudiodramaRoundError(f"Unsupported source kind: {kind}")
    source = source.expanduser().resolve()
    if not source.is_file():
        raise AudiodramaRoundError(f"Source is missing: {source}")
    return source, route.get("start"), route.get("end")


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    refs = output_root / "references"
    refs.mkdir(parents=True, exist_ok=True)
    canonical_root = Path(args.canonical_reference_root).expanduser().resolve()
    doctor_bank_root = Path(args.doctor_bank_root).expanduser().resolve()
    canonical_sources = {
        "narrator": canonical_root / "narrator" / "conditioning.wav",
        "benny": canonical_root / "benny" / "conditioning.wav",
        "doctor": doctor_bank_root / "banks" / "doctor_core_identity.wav",
    }
    canonical: dict[str, Path] = {}
    for target, source in canonical_sources.items():
        if not source.is_file():
            raise AudiodramaRoundError(f"Canonical source is missing: {source}")
        target_path = refs / f"canonical-{target}.wav"
        normalize_audio(source, target_path)
        canonical[target] = target_path
    doctor_character = refs / "doctor-character-bank.wav"
    normalize_audio(doctor_bank_root / "banks" / "doctor_core_identity.wav", doctor_character)

    routes: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    for route in ROUTES:
        source, start, end = resolve_source(route, args)
        reference = refs / f"{route['target_key']}-{route['mode']}.wav"
        if route["source"] == "doctor_composite_dry":
            concatenate_segments(source, [(44.65, 45.95), (49.20, 50.98)], reference)
        else:
            normalize_audio(source, reference, start, end)
        route_row = {
            **route,
            "source_audio": str(source),
            "source_audio_sha256": sha256_file(source),
            "reference_audio": str(reference),
            "reference_audio_sha256": sha256_file(reference),
            "canonical_identity_audio": str(canonical[route["target_key"]]),
            "canonical_identity_sha256": sha256_file(canonical[route["target_key"]]),
        }
        routes.append(route_row)
        strategies = [route["speaker_strategy"]]
        if route.get("alternate_strategy"):
            strategies.append(route["alternate_strategy"])
        for strategy in strategies:
            if strategy == "self":
                speaker = reference
            elif strategy == "canonical":
                speaker = canonical[route["target_key"]]
            elif strategy == "character_bank":
                speaker = doctor_character
            else:
                raise AudiodramaRoundError(f"Unsupported strategy: {strategy}")
            sample_id = fingerprint(
                {
                    "round": ROUND_ID,
                    "target": route["target_key"],
                    "mode": route["mode"],
                    "strategy": strategy,
                    "alpha": route["alpha"],
                    "speaker": sha256_file(speaker),
                    "reference": sha256_file(reference),
                    "text": route["target_text"],
                }
            )
            samples.append(
                {
                    "sample_id": sample_id,
                    "target_key": route["target_key"],
                    "target_label": route["target_label"],
                    "mode": route["mode"],
                    "mode_label": route["mode_label"],
                    "function": route["function"],
                    "target_text": route["target_text"],
                    "reference_text": route["reference_text"],
                    "speaker_strategy": strategy,
                    "alpha": float(route["alpha"]),
                    "speaker_audio": str(speaker),
                    "speaker_audio_sha256": sha256_file(speaker),
                    "reference_audio": str(reference),
                    "reference_audio_sha256": sha256_file(reference),
                    "canonical_identity_audio": str(canonical[route["target_key"]]),
                    "canonical_identity_sha256": sha256_file(canonical[route["target_key"]]),
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
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "matrix.json").write_text(json.dumps(matrix, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ledger = {**COVERAGE_LEDGER, "created_at": now_iso(), "round_id": ROUND_ID}
    (output_root / "coverage-ledger.json").write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"route_count": len(routes), "sample_count": len(samples), "matrix": str(output_root / "matrix.json")}


def cosine(left: np.ndarray, right: np.ndarray) -> float:
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    return float(np.dot(left, right) / denominator) if denominator > 0 else 0.0


def generate(args: argparse.Namespace) -> dict[str, Any]:
    import torch
    import torchaudio

    runtime = runtime_paths(Path(args.runtime_root).expanduser().resolve())
    output_root = Path(args.output_root).expanduser().resolve()
    matrix = json.loads((output_root / "matrix.json").read_text(encoding="utf-8"))
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
        return original_cfm(mu, x_lens, prompt, style, f0, 8, temperature=temperature, inference_cfg_rate=inference_cfg_rate)
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
        feat = torchaudio.compliance.kaldi.fbank(audio.to(model.device), num_mel_bins=80, dither=0, sample_frequency=16000)
        feat = feat - feat.mean(dim=0, keepdim=True)
        with torch.inference_mode():
            embedding = model.campplus_model(feat.unsqueeze(0)).float()
        return embedding.detach().cpu().numpy().reshape(-1)

    embedding_cache: dict[str, np.ndarray] = {}
    metric_cache: dict[str, dict[str, Any]] = {}
    for sample in matrix["samples"]:
        for key in ("speaker_audio", "reference_audio", "canonical_identity_audio"):
            path = Path(sample[key])
            digest = sha256_file(path)
            if digest not in embedding_cache:
                embedding_cache[digest] = speaker_embedding(path)
            if digest not in metric_cache:
                words = len(sample["reference_text"].split()) if key == "reference_audio" else 2
                metric_cache[digest] = acoustic_metrics(path, words)

    results = []
    generated_root = output_root / "generated"
    receipt_root = output_root / "generation-receipts"
    for sample in matrix["samples"]:
        output = generated_root / sample["target_key"] / sample["mode"] / f"{sample['speaker_strategy']}-alpha-{sample['alpha']:.2f}.wav"
        receipt_path = receipt_root / sample["target_key"] / sample["mode"] / f"{sample['speaker_strategy']}-alpha-{sample['alpha']:.2f}.json"
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
        model.infer(
            spk_audio_prompt=sample["speaker_audio"],
            text=sample["target_text"],
            output_path=str(output),
            emo_audio_prompt=sample["reference_audio"],
            emo_alpha=sample["alpha"],
            use_random=False,
            verbose=False,
            num_beams=1,
            max_mel_tokens=700,
        )
        generated_embedding = speaker_embedding(output)
        canonical_embedding = embedding_cache[sha256_file(Path(sample["canonical_identity_audio"]))]
        style_embedding = embedding_cache[sha256_file(Path(sample["reference_audio"]))]
        output_metrics = acoustic_metrics(output, len(sample["target_text"].split()))
        reference_metrics = metric_cache[sha256_file(Path(sample["reference_audio"]))]
        canonical_cosine = cosine(generated_embedding, canonical_embedding)
        style_cosine = cosine(generated_embedding, style_embedding)
        acoustic_match = float(np.mean([
            ratio_similarity(float(output_metrics["pitch_median_hz"]), float(reference_metrics["pitch_median_hz"])),
            ratio_similarity(float(output_metrics["pitch_p90_hz"] - output_metrics["pitch_p10_hz"]), float(reference_metrics["pitch_p90_hz"] - reference_metrics["pitch_p10_hz"])),
            ratio_similarity(float(output_metrics["words_per_second"]), float(reference_metrics["words_per_second"])),
            ratio_similarity(10 ** (float(output_metrics["rms_dbfs"]) / 20.0), 10 ** (float(reference_metrics["rms_dbfs"]) / 20.0)),
        ]))
        score = canonical_cosine * 4 + style_cosine * 4 + acoustic_match * 2
        receipt = {
            **sample,
            "audio_path": str(output),
            "audio_sha256": sha256_file(output),
            "generation_seconds": round(time.perf_counter() - started, 4),
            "canonical_identity_cosine": round(canonical_cosine, 6),
            "style_reference_cosine": round(style_cosine, 6),
            "acoustic_match": round(acoustic_match, 6),
            "technical_score_without_asr": round(score, 6),
            "acoustic_metrics": output_metrics,
            "reference_metrics": reference_metrics,
            "manual_listening_required": True,
            "production_promotion_allowed": False,
        }
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        results.append(receipt)
    summary = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "created_at": now_iso(),
        "sample_count": len(results),
        "samples": results,
        "runtime": {"device": "mps", "greedy_generation": True, "diffusion_steps": 8, "pre_clamp_scale": 0.70},
        "production_promotion_allowed": False,
    }
    (output_root / "generation-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return {"sample_count": len(results), "summary": str(output_root / "generation-summary.json")}


def technical_pass(row: dict[str, Any], similarity: float, final_word: bool) -> bool:
    return (
        similarity >= 0.92
        and final_word
        and row["canonical_identity_cosine"] >= 0.62
        and row["style_reference_cosine"] >= 0.68
        and row["acoustic_match"] >= 0.48
        and not row["acoustic_metrics"]["pitch_trajectory_anomaly"]
        and float(row["acoustic_metrics"]["clipping_fraction"]) < 0.001
    )


def convert_mp3(source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(source), "-ac", "1", "-ar", "24000", "-b:a", "192k", str(output)], check=True)


def package(args: argparse.Namespace) -> dict[str, Any]:
    import mlx_whisper

    output_root = Path(args.output_root).expanduser().resolve()
    whisper_model = Path(args.whisper_model).expanduser().resolve()
    summary = json.loads((output_root / "generation-summary.json").read_text(encoding="utf-8"))
    analyzed = []
    for row in summary["samples"]:
        result = mlx_whisper.transcribe(row["audio_path"], path_or_hf_repo=str(whisper_model), language="en", word_timestamps=False, condition_on_previous_text=False, verbose=False)
        transcript = str(result.get("text") or "").strip()
        similarity = text_similarity(row["target_text"], transcript)
        expected = re.findall(r"[a-z0-9']+", row["target_text"].casefold())
        actual = re.findall(r"[a-z0-9']+", transcript.casefold())
        final_word = bool(expected and actual and expected[-1] == actual[-1])
        passed = technical_pass(row, similarity, final_word)
        analyzed.append({**row, "automatic_transcript": transcript, "text_similarity": round(similarity, 6), "final_word_matches": final_word, "technical_pass": passed, "selection_score": round(row["technical_score_without_asr"] + similarity * 3 + (0.75 if passed else -0.75), 6)})

    winners = []
    excluded = []
    for route in ROUTES:
        candidates = [r for r in analyzed if r["target_key"] == route["target_key"] and r["mode"] == route["mode"]]
        passing = [r for r in candidates if r["technical_pass"]]
        pool = passing or candidates
        winner = max(pool, key=lambda r: r["selection_score"])
        if passing:
            winners.append(winner)
        else:
            excluded.append({"target_key": route["target_key"], "mode": route["mode"], "best": winner, "reason": "no_candidate_passed_automatic_gate"})

    review_root = output_root / "review"
    if review_root.exists():
        shutil.rmtree(review_root)
    for name in ("generated", "references", "targets"):
        (review_root / "audio" / name).mkdir(parents=True, exist_ok=True)
    for asset in ("index.html", "app.js", "styles.css"):
        shutil.copy2(ASSET_ROOT / asset, review_root / asset)
    shutil.copy2(RANGE_SERVER, review_root / "serve_review.py")

    index = (review_root / "index.html").read_text(encoding="utf-8")
    index = index.replace("Targeted Voice Follow-up", "Audiodrama Function Coverage").replace("Follow-up", "Function test").replace("Six unresolved routes only.", "Everyday dramatic functions across all three characters.")
    (review_root / "index.html").write_text(index, encoding="utf-8")
    app = (review_root / "app.js").read_text(encoding="utf-8")
    app = app.replace("alexandria:lazy-voice-followup:", "alexandria:audiodrama-functions:").replace("alexandria_targeted_voice_followup_review.json", "alexandria_audiodrama_function_review.json").replace("Follow-up ${currentIndex + 1}", "Function ${currentIndex + 1}")
    (review_root / "app.js").write_text(app, encoding="utf-8")

    public_rows = []
    copied_targets: set[str] = set()
    for ordinal, row in enumerate(winners, 1):
        target_name = f"{row['target_key']}.mp3"
        reference_name = f"{row['target_key']}-{row['mode']}.mp3"
        generated_name = f"{row['sample_id']}.mp3"
        if target_name not in copied_targets:
            convert_mp3(Path(row["canonical_identity_audio"]), review_root / "audio" / "targets" / target_name)
            copied_targets.add(target_name)
        convert_mp3(Path(row["reference_audio"]), review_root / "audio" / "references" / reference_name)
        convert_mp3(Path(row["audio_path"]), review_root / "audio" / "generated" / generated_name)
        public_rows.append({
            "sample_id": row["sample_id"],
            "ordinal": ordinal,
            "target_key": row["target_key"],
            "target_label": row["target_label"],
            "mode": row["mode"],
            "mode_label": row["mode_label"],
            "short_label": row["function"].replace("_", " ").title(),
            "purpose": "audiodrama_function",
            "purpose_label": f"Dramatic function · {row['function'].replace('_', ' ')}",
            "expected_text": row["target_text"],
            "target_audio": f"audio/targets/{target_name}",
            "reference_audio": f"audio/references/{reference_name}",
            "generated_audio": f"audio/generated/{generated_name}",
            "technical_pass": row["technical_pass"],
            "automatic_transcript": row["automatic_transcript"],
        })
    public = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "title": "Audiodrama Function Coverage",
        "created_at": now_iso(),
        "candidate_count": len(public_rows),
        "target_order": list(TARGET_ORDER),
        "rows": public_rows,
        "production_promotion_allowed": False,
    }
    (review_root / "data.js").write_text("window.LAZY_VOICE_FOLLOWUP_DATA = " + json.dumps(public, ensure_ascii=False) + ";\n", encoding="utf-8")
    (review_root / "manifest.json").write_text(json.dumps({"schema_version": 1, "round_id": ROUND_ID, "candidate_count": len(public_rows), "excluded_count": len(excluded), "lazy_audio_loading": True, "range_requests_required": True, "model_names_exposed": False, "production_promotion_allowed": False}, indent=2) + "\n", encoding="utf-8")
    (output_root / "answer-key.json").write_text(json.dumps(winners, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output_root / "analysis.json").write_text(json.dumps({"schema_version": 1, "round_id": ROUND_ID, "sample_count": len(analyzed), "winner_count": len(winners), "excluded": excluded, "samples": analyzed}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output_root / "START_HERE.txt").write_text(f"Audiodrama Function Coverage\n============================\n\ncd \"{review_root}\"\npython3 serve_review.py --bind 127.0.0.1 --port 8783\n\nThen open http://127.0.0.1:8783/\n", encoding="utf-8")
    return {"review": str(review_root / "index.html"), "generated_count": len(analyzed), "winner_count": len(winners), "excluded_count": len(excluded)}


def validate(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    review_root = output_root / "review"
    prefix = "window.LAZY_VOICE_FOLLOWUP_DATA = "
    text = (review_root / "data.js").read_text(encoding="utf-8").strip()
    public = json.loads(text[len(prefix):].rstrip(";"))
    missing = []
    bad_audio = []
    for row in public["rows"]:
        for key in ("target_audio", "reference_audio", "generated_audio"):
            path = review_root / row[key]
            if not path.is_file():
                missing.append(f"{row['sample_id']}:{key}")
                continue
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,sample_rate,channels", "-of", "json", str(path)], capture_output=True, text=True, check=True)
            streams = json.loads(probe.stdout).get("streams", [])
            if not streams or streams[0].get("channels") != 1:
                bad_audio.append(f"{row['sample_id']}:{key}")
    if missing or bad_audio:
        raise AudiodramaRoundError(f"Validation failed: missing={missing}, bad_audio={bad_audio}")
    return {"round_id": ROUND_ID, "candidate_count": len(public["rows"]), "missing_count": len(missing), "bad_audio_count": len(bad_audio), "review": str(review_root / "index.html")}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build audiodrama dramatic-function coverage for Narrator, Benny, and Doctor.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--output-root", required=True)
    p.add_argument("--narrator-context-root", required=True)
    p.add_argument("--benny-root", required=True)
    p.add_argument("--doctor-nuclear-audio", required=True)
    p.add_argument("--doctor-bank-root", required=True)
    p.add_argument("--canonical-reference-root", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--runtime-root", required=True)
    g.add_argument("--output-root", required=True)
    g.add_argument("--force", action="store_true")
    q = sub.add_parser("package")
    q.add_argument("--output-root", required=True)
    q.add_argument("--whisper-model", required=True)
    v = sub.add_parser("validate")
    v.add_argument("--output-root", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "prepare": result = prepare(args)
        elif args.command == "generate": result = generate(args)
        elif args.command == "package": result = package(args)
        else: result = validate(args)
    except (AudiodramaRoundError, SameSpeakerError, ReferenceBankError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
