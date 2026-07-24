#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf

from prepare_narrator_indextts2_reference_bank import (
    acoustic_metrics,
    ratio_similarity,
    runtime_paths,
    sha256_file,
    text_similarity,
)

ROUND_ID = "alexandria_doctor_voxcpm_audiodrama_rescue_v1"
TARGET_MODES = (
    "playful_eccentricity",
    "quiet_compassion",
    "urgent_command",
    "grave_warning",
)
CFG_VALUES = (1.5, 2.5)
INSTRUCTIONS = {
    "playful_eccentricity": (
        "Speak with playful eccentricity and quick, bright intelligence. Keep the Seventh Doctor's light Scottish burr, mercurial rhythm, and dry wit; lively but not childish or manic."
    ),
    "quiet_compassion": (
        "Speak with quiet compassion and sincere regret. Keep the Seventh Doctor's light Scottish identity and precise diction; intimate, restrained, and emotionally present without becoming generic or sentimental."
    ),
    "urgent_command": (
        "Speak with focused urgent command. Keep the Seventh Doctor's clipped Scottish delivery and calculating authority; fast and decisive, protective rather than panicked, never booming."
    ),
    "grave_warning": (
        "Speak with grave moral warning and controlled intensity. Keep the Seventh Doctor's light Scottish burr and unusual stresses; steel beneath restraint, no melodramatic shouting."
    ),
}


class VoxRescueError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fingerprint(value: Any, length: int = 16) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    main_root = Path(args.main_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    matrix = json.loads((main_root / "matrix.json").read_text(encoding="utf-8"))
    route_by_mode = {
        row["mode"]: row
        for row in matrix["routes"]
        if row["target_key"] == "doctor" and row["mode"] in TARGET_MODES
    }
    missing = [mode for mode in TARGET_MODES if mode not in route_by_mode]
    if missing:
        raise VoxRescueError(f"Missing Doctor routes in main matrix: {missing}")
    routes = []
    samples = []
    for mode in TARGET_MODES:
        route = route_by_mode[mode]
        reference = Path(route["reference_audio"]).resolve()
        canonical = Path(route["canonical_identity_audio"]).resolve()
        for path in (reference, canonical):
            if not path.is_file():
                raise VoxRescueError(f"Required audio is missing: {path}")
        routes.append(route)
        for cfg in CFG_VALUES:
            sample_id = fingerprint(
                {
                    "round": ROUND_ID,
                    "mode": mode,
                    "cfg": cfg,
                    "reference": sha256_file(reference),
                    "text": route["target_text"],
                    "instruction": INSTRUCTIONS[mode],
                }
            )
            samples.append(
                {
                    "sample_id": sample_id,
                    "target_key": "doctor",
                    "target_label": "Doctor",
                    "mode": mode,
                    "mode_label": route["mode_label"],
                    "function": route["function"],
                    "target_text": route["target_text"],
                    "reference_text": route["reference_text"],
                    "reference_audio": str(reference),
                    "reference_audio_sha256": sha256_file(reference),
                    "canonical_identity_audio": str(canonical),
                    "canonical_identity_sha256": sha256_file(canonical),
                    "instruction": INSTRUCTIONS[mode],
                    "cfg_value": cfg,
                    "inference_timesteps": 10,
                    "warmup_patches": 1,
                    "backend_key": "voxcpm2_research_rescue",
                }
            )
    output_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "created_at": now_iso(),
        "route_count": len(routes),
        "sample_count": len(samples),
        "routes": routes,
        "samples": samples,
        "production_promotion_allowed": False,
    }
    path = output_root / "matrix.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"route_count": len(routes), "sample_count": len(samples), "matrix": str(path)}


def generate(args: argparse.Namespace) -> dict[str, Any]:
    # Keep MLX-only imports inside this command so the same file can run in the
    # isolated PyTorch IndexTTS2 environment during identity analysis.
    import mlx.core as mx

    from run_multimodel_round1_mlx import (
        collect_results,
        disable_optional_sklearn,
        load_model,
        prepared_reference_wav,
    )

    disable_optional_sklearn()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    output_root = Path(args.output_root).expanduser().resolve()
    matrix = json.loads((output_root / "matrix.json").read_text(encoding="utf-8"))
    model, snapshot = load_model(
        "mlx-community/VoxCPM2-4bit",
        "dc9e5c187858da5f4a13dc4c247e297339216381",
    )
    results = []
    for sample in matrix["samples"]:
        cfg = float(sample["cfg_value"])
        output = output_root / "generated" / sample["mode"] / f"cfg-{cfg:.1f}.wav"
        receipt_path = output_root / "generation-receipts" / sample["mode"] / f"cfg-{cfg:.1f}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        if output.is_file() and receipt_path.is_file() and not args.force:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("audio_sha256") == sha256_file(output):
                results.append(receipt)
                continue
        reference = prepared_reference_wav(
            output_root / "prepared-references",
            Path(sample["reference_audio"]),
            sample_rate=int(getattr(model, "_encode_sample_rate", 16000)),
        )
        mx.random.seed(20260724)
        random.seed(20260724)
        np.random.seed(20260724)
        started = time.perf_counter()
        generated = model.generate(
            text=sample["target_text"],
            ref_audio=str(reference),
            ref_text=sample["reference_text"],
            instruct=sample["instruction"],
            cfg_value=cfg,
            inference_timesteps=int(sample["inference_timesteps"]),
            warmup_patches=int(sample["warmup_patches"]),
            max_tokens=1800,
        )
        audio, sample_rate = collect_results(model, generated)
        mono = np.asarray(audio, dtype=np.float32).reshape(-1)
        if mono.size == 0:
            raise VoxRescueError(f"VoxCPM2 returned empty audio for {sample['sample_id']}")
        peak = float(np.max(np.abs(mono)))
        if peak > 0:
            mono *= min(1.0, 0.70 / peak)
        sf.write(output, mono, int(sample_rate), subtype="PCM_16")
        metrics = acoustic_metrics(output, len(sample["target_text"].split()))
        receipt = {
            **sample,
            "audio_path": str(output),
            "audio_sha256": sha256_file(output),
            "model_snapshot": str(snapshot),
            "generation_seconds": round(time.perf_counter() - started, 4),
            "acoustic_metrics": metrics,
            "manual_listening_required": True,
            "production_promotion_allowed": False,
        }
        receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        results.append(receipt)
    summary = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "created_at": now_iso(),
        "sample_count": len(results),
        "samples": results,
        "production_promotion_allowed": False,
    }
    path = output_root / "generation-summary.json"
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"sample_count": len(results), "summary": str(path)}


def analyze_identity(args: argparse.Namespace) -> dict[str, Any]:
    import torch
    import torchaudio

    output_root = Path(args.output_root).expanduser().resolve()
    runtime = runtime_paths(Path(args.runtime_root).expanduser().resolve())
    summary = json.loads((output_root / "generation-summary.json").read_text(encoding="utf-8"))
    round_id = str(summary.get("round_id") or ROUND_ID)
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
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

    def embedding(path: Path) -> np.ndarray:
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
            value = model.campplus_model(feat.unsqueeze(0)).float()
        return value.detach().cpu().numpy().reshape(-1)

    def cosine(left: np.ndarray, right: np.ndarray) -> float:
        denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
        return float(np.dot(left, right) / denominator) if denominator > 0 else 0.0

    cache: dict[str, np.ndarray] = {}
    analyzed = []
    for row in summary["samples"]:
        paths = {
            "generated": Path(row["audio_path"]),
            "canonical": Path(row["canonical_identity_audio"]),
            "reference": Path(row["reference_audio"]),
        }
        for path in paths.values():
            digest = sha256_file(path)
            if digest not in cache:
                cache[digest] = embedding(path)
        generated = cache[sha256_file(paths["generated"])]
        canonical = cache[sha256_file(paths["canonical"])]
        reference = cache[sha256_file(paths["reference"])]
        out_metrics = row["acoustic_metrics"]
        ref_metrics = acoustic_metrics(paths["reference"], len(row["reference_text"].split()))
        acoustic_match = float(
            np.mean(
                [
                    ratio_similarity(float(out_metrics["pitch_median_hz"]), float(ref_metrics["pitch_median_hz"])),
                    ratio_similarity(
                        float(out_metrics["pitch_p90_hz"] - out_metrics["pitch_p10_hz"]),
                        float(ref_metrics["pitch_p90_hz"] - ref_metrics["pitch_p10_hz"]),
                    ),
                    ratio_similarity(float(out_metrics["words_per_second"]), float(ref_metrics["words_per_second"])),
                    ratio_similarity(
                        10 ** (float(out_metrics["rms_dbfs"]) / 20.0),
                        10 ** (float(ref_metrics["rms_dbfs"]) / 20.0),
                    ),
                ]
            )
        )
        analyzed.append(
            {
                **row,
                "canonical_identity_cosine": round(cosine(generated, canonical), 6),
                "style_reference_cosine": round(cosine(generated, reference), 6),
                "acoustic_match": round(acoustic_match, 6),
                "reference_metrics": ref_metrics,
            }
        )
    path = output_root / "identity-analysis.json"
    path.write_text(json.dumps({"schema_version": 1, "round_id": round_id, "samples": analyzed}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"sample_count": len(analyzed), "analysis": str(path)}


def package(args: argparse.Namespace) -> dict[str, Any]:
    import mlx_whisper

    output_root = Path(args.output_root).expanduser().resolve()
    whisper_model = Path(args.whisper_model).expanduser().resolve()
    identity = json.loads((output_root / "identity-analysis.json").read_text(encoding="utf-8"))
    round_id = str(identity.get("round_id") or ROUND_ID)
    analyzed = []
    for row in identity["samples"]:
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
        expected = re.findall(r"[a-z0-9']+", row["target_text"].casefold())
        actual = re.findall(r"[a-z0-9']+", transcript.casefold())
        final_word = bool(expected and actual and expected[-1] == actual[-1])
        passed = (
            similarity >= 0.92
            and final_word
            and row["canonical_identity_cosine"] >= 0.62
            and row["style_reference_cosine"] >= 0.62
            and row["acoustic_match"] >= 0.45
            and not row["acoustic_metrics"]["pitch_trajectory_anomaly"]
            and float(row["acoustic_metrics"]["clipping_fraction"]) < 0.001
        )
        score = (
            row["canonical_identity_cosine"] * 4
            + row["style_reference_cosine"] * 3
            + row["acoustic_match"] * 2
            + similarity * 3
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
    for mode in TARGET_MODES:
        candidates = [row for row in analyzed if row["mode"] == mode]
        passing = [row for row in candidates if row["technical_pass"]]
        pool = passing or candidates
        winner = max(pool, key=lambda row: row["selection_score"])
        if passing:
            winners.append(winner)
        else:
            excluded.append({"mode": mode, "reason": "no_candidate_passed_automatic_gate", "best": winner})
    (output_root / "answer-key.json").write_text(json.dumps(winners, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output_root / "analysis.json").write_text(json.dumps({"schema_version": 1, "round_id": round_id, "sample_count": len(analyzed), "winner_count": len(winners), "excluded": excluded, "samples": analyzed}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"winner_count": len(winners), "excluded_count": len(excluded)}


def validate(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root).expanduser().resolve()
    winners = json.loads((output_root / "answer-key.json").read_text(encoding="utf-8"))
    missing = [row["sample_id"] for row in winners if not Path(row["audio_path"]).is_file()]
    if missing:
        raise VoxRescueError(f"Missing VoxCPM2 rescue outputs: {missing}")
    return {"winner_count": len(winners), "missing_count": len(missing)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VoxCPM2 rescue for missing Doctor audiodrama functions.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--main-root", required=True)
    p.add_argument("--output-root", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--output-root", required=True)
    g.add_argument("--force", action="store_true")
    i = sub.add_parser("identity")
    i.add_argument("--runtime-root", required=True)
    i.add_argument("--output-root", required=True)
    q = sub.add_parser("package")
    q.add_argument("--output-root", required=True)
    q.add_argument("--whisper-model", required=True)
    v = sub.add_parser("validate")
    v.add_argument("--output-root", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "prepare":
            result = prepare(args)
        elif args.command == "generate":
            result = generate(args)
        elif args.command == "identity":
            result = analyze_identity(args)
        elif args.command == "package":
            result = package(args)
        else:
            result = validate(args)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
