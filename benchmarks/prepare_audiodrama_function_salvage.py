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

from prepare_audiodrama_function_round import (
    ASSET_ROOT,
    RANGE_SERVER,
    AudiodramaRoundError,
    convert_mp3,
    cosine,
    normalize_audio,
    technical_pass,
)
from prepare_narrator_indextts2_reference_bank import (
    acoustic_metrics,
    ratio_similarity,
    runtime_paths,
    sha256_file,
    text_similarity,
)

ROUND_ID = "alexandria_audiodrama_function_salvage_v1"
TARGET_MODES = (
    ("narrator", "restrained_concern"),
    ("doctor", "playful_eccentricity"),
    ("doctor", "quiet_compassion"),
    ("doctor", "urgent_command"),
    ("doctor", "grave_warning"),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fingerprint(value: Any, length: int = 16) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def concatenate(paths: list[Path], output: Path, silence_seconds: float = 0.12) -> None:
    pieces: list[np.ndarray] = []
    silence = np.zeros(int(24000 * silence_seconds), dtype=np.float32)
    for index, path in enumerate(paths):
        audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
        if sample_rate != 24000:
            raise AudiodramaRoundError(f"Composite source is not 24 kHz: {path}")
        if index:
            pieces.append(silence)
        pieces.append(np.mean(audio, axis=1, dtype=np.float32))
    merged = np.concatenate(pieces)
    peak = float(np.max(np.abs(merged)))
    if peak > 0:
        merged *= min(1.0, 0.70 / peak)
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, merged, 24000, subtype="PCM_16")


def compact_doctor_identity(doctor_bank_root: Path, output: Path, mode: str) -> Path:
    clips = doctor_bank_root / "clips"
    if mode == "grave_warning":
        names = ["sample_0206.wav", "sample_0207.wav", "sample_0208.wav"]
    else:
        names = ["sample_0208.wav"]
    paths = [clips / name for name in names]
    for path in paths:
        if not path.is_file():
            raise AudiodramaRoundError(f"Doctor identity clip is missing: {path}")
    concatenate(paths, output)
    return output


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    main_root = Path(args.main_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    doctor_bank_root = Path(args.doctor_bank_root).expanduser().resolve()
    main_matrix = json.loads((main_root / "matrix.json").read_text(encoding="utf-8"))
    route_by_key = {(r["target_key"], r["mode"]): r for r in main_matrix["routes"]}
    refs = output_root / "references"
    refs.mkdir(parents=True, exist_ok=True)
    samples = []
    routes = []
    for target_key, mode in TARGET_MODES:
        route = route_by_key[(target_key, mode)]
        reference = Path(route["reference_audio"])
        canonical = Path(route["canonical_identity_audio"])
        if not reference.is_file() or not canonical.is_file():
            raise AudiodramaRoundError(f"Salvage source missing for {target_key}/{mode}")
        route_copy = {**route}
        routes.append(route_copy)
        candidates: list[tuple[str, Path, float]] = []
        if target_key == "narrator":
            candidates.append(("canonical", canonical, 0.15))
            composite = refs / "narrator-restrained-concern-composite.wav"
            concatenate([canonical, reference], composite)
            candidates.append(("identity_style_composite", composite, 0.15))
        else:
            compact = refs / f"doctor-{mode}-compact-identity.wav"
            compact_doctor_identity(doctor_bank_root, compact, mode)
            composite = refs / f"doctor-{mode}-composite.wav"
            concatenate([compact, reference], composite)
            if mode == "playful_eccentricity":
                alphas = (0.15, 0.25)
            elif mode == "quiet_compassion":
                alphas = (0.15, 0.25)
            elif mode == "urgent_command":
                alphas = (0.20, 0.35)
            else:
                alphas = (0.20, 0.35)
            for alpha in alphas:
                candidates.append(("identity_style_composite", composite, alpha))
        for strategy, speaker, alpha in candidates:
            sample_id = fingerprint({
                "round": ROUND_ID,
                "target": target_key,
                "mode": mode,
                "strategy": strategy,
                "alpha": alpha,
                "speaker": sha256_file(speaker),
                "reference": sha256_file(reference),
                "text": route["target_text"],
            })
            samples.append({
                "sample_id": sample_id,
                "target_key": target_key,
                "target_label": route["target_label"],
                "mode": mode,
                "mode_label": route["mode_label"],
                "function": route["function"],
                "target_text": route["target_text"],
                "reference_text": route["reference_text"],
                "speaker_strategy": strategy,
                "alpha": alpha,
                "speaker_audio": str(speaker),
                "speaker_audio_sha256": sha256_file(speaker),
                "reference_audio": str(reference),
                "reference_audio_sha256": sha256_file(reference),
                "canonical_identity_audio": str(canonical),
                "canonical_identity_sha256": sha256_file(canonical),
            })
    matrix = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "created_at": now_iso(),
        "route_count": len(routes),
        "sample_count": len(samples),
        "routes": routes,
        "samples": samples,
        "production_promotion_allowed": False,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "matrix.json").write_text(json.dumps(matrix, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"route_count": len(routes), "sample_count": len(samples), "matrix": str(output_root / "matrix.json")}


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

    model = IndexTTS2(
        cfg_path=str(runtime.model / "config.yaml"),
        model_dir=str(runtime.model),
        use_fp16=False,
        device="mps",
        use_cuda_kernel=False,
        use_deepspeed=False,
        use_accel=False,
        use_torch_compile=False,
        aux_paths={
            "w2v_bert": str(runtime.aux / "w2v-bert-2.0"),
            "semantic_codec": str(runtime.aux / "semantic_codec" / "model.safetensors"),
            "campplus": str(runtime.aux / "campplus_cn_common.bin"),
            "bigvgan": str(runtime.aux / "bigvgan"),
        },
    )
    original_inference = model.gpt.inference_speech
    def greedy(*args, **kwargs):
        kwargs["do_sample"] = False
        kwargs["num_beams"] = 1
        return original_inference(*args, **kwargs)
    model.gpt.inference_speech = greedy
    original_cfm = model.s2mel.models["cfm"].inference
    def short_cfm(mu, x_lens, prompt, style, f0, n_timesteps, temperature=1.0, inference_cfg_rate=0.5):
        return original_cfm(mu, x_lens, prompt, style, f0, 8, temperature=temperature, inference_cfg_rate=inference_cfg_rate)
    model.s2mel.models["cfm"].inference = short_cfm
    original_bigvgan = model.bigvgan
    model.bigvgan = lambda *a, **k: original_bigvgan(*a, **k) * 0.70

    def embedding(path: Path) -> np.ndarray:
        audio, sample_rate = torchaudio.load(str(path))
        if audio.shape[0] > 1:
            audio = audio.mean(dim=0, keepdim=True)
        if sample_rate != 16000:
            audio = torchaudio.transforms.Resample(sample_rate, 16000)(audio)
        feat = torchaudio.compliance.kaldi.fbank(audio.to(model.device), num_mel_bins=80, dither=0, sample_frequency=16000)
        feat = feat - feat.mean(dim=0, keepdim=True)
        with torch.inference_mode():
            value = model.campplus_model(feat.unsqueeze(0)).float()
        return value.detach().cpu().numpy().reshape(-1)

    embeddings: dict[str, np.ndarray] = {}
    metrics: dict[str, dict[str, Any]] = {}
    for sample in matrix["samples"]:
        for key in ("speaker_audio", "reference_audio", "canonical_identity_audio"):
            path = Path(sample[key])
            digest = sha256_file(path)
            if digest not in embeddings:
                embeddings[digest] = embedding(path)
            if digest not in metrics:
                words = len(sample["reference_text"].split()) if key == "reference_audio" else 2
                metrics[digest] = acoustic_metrics(path, words)

    results = []
    for sample in matrix["samples"]:
        output = output_root / "generated" / sample["target_key"] / sample["mode"] / f"{sample['speaker_strategy']}-alpha-{sample['alpha']:.2f}.wav"
        receipt_path = output_root / "generation-receipts" / sample["target_key"] / sample["mode"] / f"{sample['speaker_strategy']}-alpha-{sample['alpha']:.2f}.json"
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
        generated = embedding(output)
        canonical = embeddings[sha256_file(Path(sample["canonical_identity_audio"]))]
        style = embeddings[sha256_file(Path(sample["reference_audio"]))]
        out_metrics = acoustic_metrics(output, len(sample["target_text"].split()))
        ref_metrics = metrics[sha256_file(Path(sample["reference_audio"]))]
        canonical_cosine = cosine(generated, canonical)
        style_cosine = cosine(generated, style)
        acoustic_match = float(np.mean([
            ratio_similarity(float(out_metrics["pitch_median_hz"]), float(ref_metrics["pitch_median_hz"])),
            ratio_similarity(float(out_metrics["pitch_p90_hz"] - out_metrics["pitch_p10_hz"]), float(ref_metrics["pitch_p90_hz"] - ref_metrics["pitch_p10_hz"])),
            ratio_similarity(float(out_metrics["words_per_second"]), float(ref_metrics["words_per_second"])),
            ratio_similarity(10 ** (float(out_metrics["rms_dbfs"]) / 20.0), 10 ** (float(ref_metrics["rms_dbfs"]) / 20.0)),
        ]))
        receipt = {
            **sample,
            "audio_path": str(output),
            "audio_sha256": sha256_file(output),
            "generation_seconds": round(time.perf_counter() - started, 4),
            "canonical_identity_cosine": round(canonical_cosine, 6),
            "style_reference_cosine": round(style_cosine, 6),
            "acoustic_match": round(acoustic_match, 6),
            "technical_score_without_asr": round(canonical_cosine * 4 + style_cosine * 4 + acoustic_match * 2, 6),
            "acoustic_metrics": out_metrics,
            "reference_metrics": ref_metrics,
            "manual_listening_required": True,
            "production_promotion_allowed": False,
        }
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        results.append(receipt)
    summary = {"schema_version": 1, "round_id": ROUND_ID, "created_at": now_iso(), "sample_count": len(results), "samples": results, "production_promotion_allowed": False}
    (output_root / "generation-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return {"sample_count": len(results), "summary": str(output_root / "generation-summary.json")}


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
    for target_key, mode in TARGET_MODES:
        candidates = [r for r in analyzed if r["target_key"] == target_key and r["mode"] == mode]
        passing = [r for r in candidates if r["technical_pass"]]
        pool = passing or candidates
        winner = max(pool, key=lambda r: r["selection_score"])
        if passing:
            winners.append(winner)
        else:
            excluded.append({"target_key": target_key, "mode": mode, "best": winner, "reason": "no_candidate_passed_automatic_gate"})
    (output_root / "answer-key.json").write_text(json.dumps(winners, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output_root / "analysis.json").write_text(json.dumps({"schema_version": 1, "round_id": ROUND_ID, "sample_count": len(analyzed), "winner_count": len(winners), "excluded": excluded, "samples": analyzed}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"winner_count": len(winners), "excluded_count": len(excluded)}


def validate(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    winners = json.loads((output_root / "answer-key.json").read_text(encoding="utf-8"))
    missing = [r["sample_id"] for r in winners if not Path(r["audio_path"]).is_file()]
    if missing:
        raise AudiodramaRoundError(f"Missing salvage audio: {missing}")
    return {"winner_count": len(winners), "missing_count": len(missing)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Salvage audiodrama function routes with compact identity-style prompts.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--main-root", required=True)
    p.add_argument("--doctor-bank-root", required=True)
    p.add_argument("--output-root", required=True)
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
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
