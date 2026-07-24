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

ROUND_ID = "alexandria_same_speaker_performance_validation_v1"
ALPHAS = (0.30, 0.60)
ASSET_ROOT = Path(__file__).with_name("three_voice_openvoice_assets")

SPECS: tuple[dict[str, Any], ...] = (
    {
        "target_key": "narrator",
        "target_label": "Narrator",
        "mode": "panic",
        "mode_label": "Urgent panic",
        "target_text": "Stanley, don't move. The floor is giving way, and I can't reach you from here.",
        "reference_text": "Don't go anywhere. I can't follow you there. I can't help you.",
        "source_kind": "narrator_context",
        "source_name": "supplement-d9692d8c004cd7fe.wav",
        "start_seconds": 3.20,
        "end_seconds": 7.12,
    },
    {
        "target_key": "narrator",
        "target_label": "Narrator",
        "mode": "smug_menace",
        "mode_label": "Smug menace",
        "target_text": "Take your time, Stanley. Every second you waste makes this much more entertaining.",
        "reference_text": "A moment of solace before you're obliterated? All right, I'm in a good mood. You're going to die anyway.",
        "source_kind": "narrator_context",
        "source_name": "supplement-7653ebeed8096728.wav",
        "start_seconds": 2.74,
        "end_seconds": 8.90,
    },
    {
        "target_key": "benny",
        "target_label": "Benny",
        "mode": "emergency_distress",
        "mode_label": "Emergency distress",
        "target_text": "This is Bernice Summerfield. I'm trapped below the excavation site, and the chamber is collapsing.",
        "reference_text": "People of Mars, this is Bernice Summerfield, broadcasting on, hopefully, an emergency frequency. I'm trapped in a pyramid. Yes, a pyramid.",
        "source_kind": "benny_download",
        "source_name": "bennyVoice3.mp3",
        "start_seconds": 0.80,
        "end_seconds": 11.45,
    },
    {
        "target_key": "benny",
        "target_label": "Benny",
        "mode": "excited_discovery",
        "mode_label": "Excited discovery",
        "target_text": "This is extraordinary. These markings predate the colony by thousands of years.",
        "reference_text": "A previously undiscovered civilization. This alone has made the trip worthwhile, but who knows what I'll find inside the tomb itself.",
        "source_kind": "benny_download",
        "source_name": "bennyVoice4.mp3",
        "start_seconds": 9.78,
        "end_seconds": 18.10,
    },
    {
        "target_key": "doctor",
        "target_label": "Doctor",
        "mode": "protective_authority",
        "mode_label": "Protective authority",
        "target_text": "Stay behind me. Whatever happens, do not let go of my hand.",
        "reference_text": "I'm the Doctor, and I take care of my friends.",
        "source_kind": "doctor_clip",
        "source_name": "sample_0208.wav",
        "speaker_source_kind": "doctor_bank",
        "speaker_source_name": "doctor_calm_authoritative.wav",
        "start_seconds": None,
        "end_seconds": None,
    },
    {
        "target_key": "doctor",
        "target_label": "Doctor",
        "mode": "dark_warning",
        "mode_label": "Dark warning",
        "target_text": "You will leave them alone. That is not a request.",
        "reference_text": "You're not real. You never were.",
        "source_kind": "doctor_clip",
        "source_name": "sample_0198.wav",
        "speaker_source_kind": "doctor_bank",
        "speaker_source_name": "doctor_calm_authoritative.wav",
        "start_seconds": None,
        "end_seconds": None,
    },
)


class SameSpeakerError(RuntimeError):
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
) -> Path:
    kind = spec["source_kind"]
    if kind == "narrator_context":
        path = narrator_context_root / "review" / "audio" / spec["source_name"]
    elif kind == "benny_download":
        path = benny_root / spec["source_name"]
    elif kind == "doctor_clip":
        path = doctor_bank_root / "clips" / spec["source_name"]
    elif kind == "doctor_bank":
        path = doctor_bank_root / "banks" / spec["source_name"]
    else:
        raise SameSpeakerError(f"Unsupported source kind: {kind}")
    path = path.expanduser().resolve()
    if not path.is_file():
        raise SameSpeakerError(f"Reference source is missing: {path}")
    return path


def normalize_reference(source: Path, output: Path, start: float | None, end: float | None) -> None:
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
        raise SameSpeakerError(f"Reference is too short after trimming: {source}")
    peak = float(np.max(np.abs(mono)))
    if peak > 0:
        mono = mono * min(1.0, 0.70 / peak)
    sf.write(output, mono, 24000, subtype="PCM_16")


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    narrator_context = Path(args.narrator_context_root).expanduser().resolve()
    benny_root = Path(args.benny_root).expanduser().resolve()
    doctor_bank = Path(args.doctor_bank_root).expanduser().resolve()
    canonical_root = Path(args.canonical_reference_root).expanduser().resolve()
    canonical = {
        "narrator": canonical_root / "narrator" / "conditioning.wav",
        "benny": canonical_root / "benny" / "conditioning.wav",
        "doctor": doctor_bank / "banks" / "doctor_core_identity.wav",
    }
    for key, path in canonical.items():
        if not path.is_file():
            raise SameSpeakerError(f"Canonical identity is missing for {key}: {path}")

    references_root = output_root / "references"
    references_root.mkdir(parents=True, exist_ok=True)
    rows = []
    for spec in SPECS:
        source = resolve_source(
            spec,
            narrator_context_root=narrator_context,
            benny_root=benny_root,
            doctor_bank_root=doctor_bank,
        )
        reference = references_root / f"{spec['target_key']}-{spec['mode']}.wav"
        normalize_reference(source, reference, spec["start_seconds"], spec["end_seconds"])
        speaker_spec = {
            **spec,
            "source_kind": spec.get("speaker_source_kind", spec["source_kind"]),
            "source_name": spec.get("speaker_source_name", spec["source_name"]),
        }
        speaker_source = resolve_source(
            speaker_spec,
            narrator_context_root=narrator_context,
            benny_root=benny_root,
            doctor_bank_root=doctor_bank,
        )
        if speaker_source == source and not spec.get("speaker_source_kind"):
            speaker_audio = reference
        else:
            speaker_audio = references_root / f"{spec['target_key']}-{spec['mode']}-speaker.wav"
            normalize_reference(speaker_source, speaker_audio, None, None)
        rows.append(
            {
                **spec,
                "source_audio": str(source),
                "source_audio_sha256": sha256_file(source),
                "reference_audio": str(reference),
                "reference_audio_sha256": sha256_file(reference),
                "speaker_audio": str(speaker_audio),
                "speaker_audio_sha256": sha256_file(speaker_audio),
                "speaker_source_audio": str(speaker_source),
                "speaker_source_audio_sha256": sha256_file(speaker_source),
                "canonical_identity_audio": str(canonical[spec["target_key"]].resolve()),
                "canonical_identity_sha256": sha256_file(canonical[spec["target_key"]]),
            }
        )
    matrix = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "created_at": now_iso(),
        "alphas": list(ALPHAS),
        "reference_count": len(rows),
        "rows": rows,
        "production_promotion_allowed": False,
    }
    path = output_root / "matrix.json"
    path.write_text(json.dumps(matrix, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"reference_count": len(rows), "matrix": str(path)}


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
        raise SameSpeakerError(f"Prepare the reference matrix first: {matrix_path}")
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
            audio.to(model.device), num_mel_bins=80, dither=0, sample_frequency=16000
        )
        feat = feat - feat.mean(dim=0, keepdim=True)
        with torch.inference_mode():
            embedding = model.campplus_model(feat.unsqueeze(0)).float()
        return embedding.detach().cpu().numpy().reshape(-1)

    embeddings: dict[str, np.ndarray] = {}
    metrics: dict[str, dict[str, Any]] = {}
    for row in matrix["rows"]:
        for key in ("reference_audio", "speaker_audio", "canonical_identity_audio"):
            path = Path(row[key])
            digest = sha256_file(path)
            if digest not in embeddings:
                embeddings[digest] = speaker_embedding(path)
            if digest not in metrics:
                word_count = len((row["reference_text"] if key == "reference_audio" else "identity anchor").split())
                metrics[digest] = acoustic_metrics(path, word_count)

    generated_root = output_root / "generated"
    receipt_root = output_root / "generation-receipts"
    results = []
    for row in matrix["rows"]:
        reference = Path(row["reference_audio"])
        speaker_audio = Path(row["speaker_audio"])
        canonical = Path(row["canonical_identity_audio"])
        style_embedding = embeddings[sha256_file(reference)]
        canonical_embedding = embeddings[sha256_file(canonical)]
        reference_metrics = metrics[sha256_file(reference)]
        for alpha in matrix["alphas"]:
            sample_id = fingerprint(
                {
                    "round": ROUND_ID,
                    "target": row["target_key"],
                    "mode": row["mode"],
                    "alpha": alpha,
                    "reference": row["reference_audio_sha256"],
                    "speaker": row["speaker_audio_sha256"],
                    "text": row["target_text"],
                }
            )
            output = generated_root / row["target_key"] / row["mode"] / f"alpha_{alpha:.2f}.wav"
            receipt_path = receipt_root / row["target_key"] / row["mode"] / f"alpha_{alpha:.2f}.json"
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
                spk_audio_prompt=str(speaker_audio),
                text=row["target_text"],
                output_path=str(output),
                emo_audio_prompt=str(reference),
                emo_alpha=float(alpha),
                use_random=False,
                verbose=False,
                num_beams=1,
                max_mel_tokens=700,
            )
            if not output.is_file():
                raise SameSpeakerError(f"IndexTTS2 did not create {output}; returned {returned!r}")
            generated_embedding = speaker_embedding(output)
            output_metrics = acoustic_metrics(output, len(row["target_text"].split()))
            canonical_cosine = cosine(generated_embedding, canonical_embedding)
            style_cosine = cosine(generated_embedding, style_embedding)
            acoustic_match = float(
                np.mean(
                    [
                        ratio_similarity(float(output_metrics["pitch_median_hz"]), float(reference_metrics["pitch_median_hz"])),
                        ratio_similarity(
                            float(output_metrics["pitch_p90_hz"] - output_metrics["pitch_p10_hz"]),
                            float(reference_metrics["pitch_p90_hz"] - reference_metrics["pitch_p10_hz"]),
                        ),
                        ratio_similarity(float(output_metrics["words_per_second"]), float(reference_metrics["words_per_second"])),
                        ratio_similarity(
                            10 ** (float(output_metrics["rms_dbfs"]) / 20.0),
                            10 ** (float(reference_metrics["rms_dbfs"]) / 20.0),
                        ),
                    ]
                )
            )
            score = (
                canonical_cosine * 4.0
                + style_cosine * 4.0
                + acoustic_match * 2.0
                + (0.5 if not output_metrics["pitch_trajectory_anomaly"] else -2.0)
                + (0.5 if float(output_metrics["clipping_fraction"]) < 0.001 else -1.0)
            )
            receipt = {
                "schema_version": 1,
                "round_id": ROUND_ID,
                "sample_id": sample_id,
                "target_key": row["target_key"],
                "target_label": row["target_label"],
                "mode": row["mode"],
                "mode_label": row["mode_label"],
                "alpha": float(alpha),
                "target_text": row["target_text"],
                "reference_text": row["reference_text"],
                "reference_audio": str(reference),
                "reference_audio_sha256": sha256_file(reference),
                "speaker_audio": str(speaker_audio),
                "speaker_audio_sha256": sha256_file(speaker_audio),
                "canonical_identity_audio": str(canonical),
                "canonical_identity_sha256": sha256_file(canonical),
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

    model = None
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()
    summary = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "created_at": now_iso(),
        "runtime": {
            "device": "mps",
            "greedy_generation": True,
            "use_random": False,
            "diffusion_steps": 8,
            "same_audio_for_speaker_and_performance": True,
        },
        "sample_count": len(results),
        "samples": results,
        "production_promotion_allowed": False,
    }
    path = output_root / "generation-summary.json"
    path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return {"sample_count": len(results), "summary": str(path)}


def patch_assets(review_root: Path) -> None:
    html = (ASSET_ROOT / "index.html").read_text(encoding="utf-8")
    html = (
        html.replace("Three-voice performance conversion proof", "Same-speaker performance validation")
        .replace("Performance-to-character conversion", "Same-speaker performance anchoring")
        .replace(
            "Nine bounded tests. The donor supplies the acting; OpenVoice changes the speaker to Narrator, Benny, or Doctor.",
            "Each result uses an authentic performance from that same character for both identity and delivery. No cross-speaker donor is used.",
        )
        .replace("0 / 9 complete", "0 / 6 complete")
        .replace(
            "the converted result must still sound like the target character and preserve the donor's performance.",
            "the generated result must preserve the character's accent and identity while matching the authentic reference performance.",
        )
        .replace("Acted donor", "Authentic performance reference")
        .replace("The timing, emotion, and emphasis to preserve.", "The same character performing the target delivery family.")
        .replace("Converted result", "Generated result")
        .replace("Performance preserved", "Delivery match")
        .replace("Donor performance is recognizably preserved", "Reference delivery is recognizably preserved")
        .replace("Approve this conversion route", "Approve this same-speaker route")
    )
    app = (ASSET_ROOT / "app.js").read_text(encoding="utf-8")
    app = (
        app.replace("alexandria:three-voice-openvoice:", "alexandria:same-speaker-performance:")
        .replace("All 9", "All 6")
        .replace(
            "alexandria_three_voice_openvoice_conversion_review.json",
            "alexandria_same_speaker_performance_review.json",
        )
    )
    (review_root / "index.html").write_text(html, encoding="utf-8")
    (review_root / "app.js").write_text(app, encoding="utf-8")
    shutil.copy2(ASSET_ROOT / "styles.css", review_root / "styles.css")


def package(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    whisper_model = Path(args.whisper_model).expanduser().resolve()
    summary_path = output_root / "generation-summary.json"
    if not summary_path.is_file():
        raise SameSpeakerError(f"Generation summary is missing: {summary_path}")
    if not whisper_model.is_dir():
        raise SameSpeakerError(f"Whisper model is missing: {whisper_model}")
    import mlx_whisper

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    analyzed = []
    for row in summary["samples"]:
        asr = mlx_whisper.transcribe(
            row["audio_path"],
            path_or_hf_repo=str(whisper_model),
            language="en",
            word_timestamps=False,
            condition_on_previous_text=False,
            verbose=False,
        )
        transcript = str(asr.get("text") or "").strip()
        similarity = text_similarity(row["target_text"], transcript)
        expected_words = normalize_text(row["target_text"]).split()
        actual_words = normalize_text(transcript).split()
        final_word = bool(expected_words and actual_words and expected_words[-1] == actual_words[-1])
        technical_pass = (
            similarity >= 0.92
            and final_word
            and (
                row["style_reference_cosine"] >= 0.80
                or (
                    row["style_reference_cosine"] >= 0.74
                    and row["canonical_identity_cosine"] >= 0.72
                )
            )
            and row["acoustic_match"] >= 0.50
            and not row["acoustic_metrics"]["pitch_trajectory_anomaly"]
            and float(row["acoustic_metrics"]["clipping_fraction"]) < 0.001
        )
        score = (
            row["technical_score_without_asr"]
            + similarity * 3.0
            + (0.5 if final_word else -2.0)
            + (0.75 if technical_pass else -0.75)
        )
        analyzed.append(
            {
                **row,
                "automatic_transcript": transcript,
                "text_similarity": round(similarity, 6),
                "final_word_matches": final_word,
                "technical_pass": technical_pass,
                "selection_score": round(score, 6),
            }
        )

    winners = []
    excluded = []
    for spec in SPECS:
        candidates = [
            row
            for row in analyzed
            if row["target_key"] == spec["target_key"] and row["mode"] == spec["mode"]
        ]
        passing = [row for row in candidates if row["technical_pass"]]
        if not passing:
            excluded.append(
                {
                    "target_key": spec["target_key"],
                    "mode": spec["mode"],
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
    (review_root / "donors").mkdir(parents=True)
    public_rows = []
    copied_targets: set[str] = set()
    copied_refs: set[str] = set()
    for ordinal, winner in enumerate(winners, 1):
        target_name = f"{winner['target_key']}.wav"
        reference_name = f"{winner['target_key']}-{winner['mode']}.wav"
        generated_name = f"{winner['sample_id']}.wav"
        if target_name not in copied_targets:
            shutil.copy2(winner["canonical_identity_audio"], review_root / "targets" / target_name)
            copied_targets.add(target_name)
        if reference_name not in copied_refs:
            shutil.copy2(winner["reference_audio"], review_root / "donors" / reference_name)
            copied_refs.add(reference_name)
        shutil.copy2(winner["audio_path"], review_root / "audio" / generated_name)
        public_rows.append(
            {
                "sample_id": winner["sample_id"],
                "ordinal": ordinal,
                "target_key": winner["target_key"],
                "target_label": winner["target_label"],
                "mode": winner["mode"],
                "mode_label": winner["mode_label"],
                "expected_text": winner["target_text"],
                "target_audio": f"targets/{target_name}",
                "donor_audio": f"donors/{reference_name}",
                "converted_audio": f"audio/{generated_name}",
                "technical_pass": winner["technical_pass"],
                "automatic_transcript": winner["automatic_transcript"],
            }
        )

    patch_assets(review_root)
    public = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "title": "Same-speaker performance validation",
        "created_at": now_iso(),
        "candidate_count": len(public_rows),
        "target_order": ["narrator", "benny", "doctor"],
        "rows": public_rows,
        "production_promotion_allowed": False,
    }
    (review_root / "data.js").write_text(
        "window.THREE_VOICE_OPENVOICE_DATA = " + json.dumps(public, ensure_ascii=False) + ";\n",
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
                "production_promotion_allowed": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (output_root / "answer-key.json").write_text(
        json.dumps(winners, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
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
        "Same-speaker performance validation\n"
        "===================================\n\n"
        f"cd \"{review_root}\"\n"
        "python3 -m http.server 8780 --bind 127.0.0.1\n\n"
        "Then open http://127.0.0.1:8780/\n",
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
    prefix = "window.THREE_VOICE_OPENVOICE_DATA = "
    data_text = (review_root / "data.js").read_text(encoding="utf-8").strip()
    data = json.loads(data_text[len(prefix) :].rstrip(";"))
    answers = {row["sample_id"]: row for row in json.loads((output_root / "answer-key.json").read_text())}
    missing = []
    bad_hash = []
    for row in data["rows"]:
        audio = review_root / row["converted_audio"]
        target = review_root / row["target_audio"]
        reference = review_root / row["donor_audio"]
        if not audio.is_file() or not target.is_file() or not reference.is_file():
            missing.append(row["sample_id"])
            continue
        if sha256_file(audio) != answers[row["sample_id"]]["audio_sha256"]:
            bad_hash.append(row["sample_id"])
    body = (review_root / "index.html").read_text(encoding="utf-8")
    if re.search(r"OpenVoice|Seed-VC|IndexTTS2", body, re.IGNORECASE):
        raise SameSpeakerError("Model name leaked into the public review")
    if missing or bad_hash:
        raise SameSpeakerError(f"Validation failed: missing={missing}, bad_hash={bad_hash}")
    return {
        "round_id": ROUND_ID,
        "candidate_count": len(data["rows"]),
        "missing_count": len(missing),
        "bad_hash_count": len(bad_hash),
        "review": str(review_root / "index.html"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a bounded same-speaker performance validation.")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--output-root", required=True)
    prepare_parser.add_argument("--narrator-context-root", required=True)
    prepare_parser.add_argument("--benny-root", required=True)
    prepare_parser.add_argument("--doctor-bank-root", required=True)
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
    except (SameSpeakerError, ReferenceBankError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
