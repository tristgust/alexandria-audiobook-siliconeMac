#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf

import prepare_doctor_voxcpm_audiodrama_rescue as shared
from prepare_narrator_indextts2_reference_bank import acoustic_metrics, sha256_file

ROUND_ID = "alexandria_doctor_fish_audiodrama_rescue_v1"
INLINE_TAGS = {
    "playful_eccentricity": None,
    "quiet_compassion": "low voice",
    "urgent_command": "urgent",
    "grave_warning": "emphasis",
}


def fingerprint(value: Any, length: int = 16) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    result = shared.prepare(args)
    output_root = Path(args.output_root).expanduser().resolve()
    matrix_path = output_root / "matrix.json"
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    matrix["round_id"] = ROUND_ID
    for sample in matrix["samples"]:
        sample["backend_key"] = "fish_s2_pro_research_rescue"
        sample["inline_tag"] = INLINE_TAGS[sample["mode"]]
        sample["temperature"] = 0.7
        sample["top_p"] = 0.7
        sample["top_k"] = 30
        sample["sample_id"] = fingerprint(
            {
                "round": ROUND_ID,
                "mode": sample["mode"],
                "cfg_source": sample["cfg_value"],
                "reference": sample["reference_audio_sha256"],
                "text": sample["target_text"],
                "instruction": sample["instruction"],
                "inline_tag": sample["inline_tag"],
            }
        )
    matrix_path.write_text(json.dumps(matrix, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {**result, "round_id": ROUND_ID}


def generate(args: argparse.Namespace) -> dict[str, Any]:
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
        "mlx-community/fish-audio-s2-pro",
        "eccd57bf5c1ebc13cb2f993df867f4e49931a36a",
    )
    reference_rate = int(getattr(model, "sample_rate", 44100))
    results = []
    for sample in matrix["samples"]:
        # Two cfg-derived rows would otherwise be duplicates for Fish. Keep the
        # first per mode and let the second use a slightly lower temperature.
        variant = "stable" if float(sample["cfg_value"]) < 2.0 else "expressive"
        output = output_root / "generated" / sample["mode"] / f"{variant}.wav"
        receipt_path = output_root / "generation-receipts" / sample["mode"] / f"{variant}.json"
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
            sample_rate=reference_rate,
        )
        audio, rate = sf.read(reference, dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        text = sample["target_text"]
        tag = sample.get("inline_tag")
        if tag:
            text = f"[{tag}] {text}"
        temperature = 0.65 if variant == "stable" else 0.8
        mx.random.seed(20260724)
        random.seed(20260724)
        np.random.seed(20260724)
        generated = model.generate(
            text=text,
            ref_audio=mx.array(audio),
            ref_text=sample["reference_text"],
            instruct=sample["instruction"],
            max_tokens=1400,
            temperature=temperature,
            top_p=0.7,
            top_k=30,
            verbose=False,
        )
        mono, sample_rate = collect_results(model, generated)
        mono = np.asarray(mono, dtype=np.float32).reshape(-1)
        peak = float(np.max(np.abs(mono))) if mono.size else 0.0
        if peak > 0:
            mono *= min(1.0, 0.70 / peak)
        sf.write(output, mono, int(sample_rate), subtype="PCM_16")
        receipt = {
            **sample,
            "variant": variant,
            "temperature": temperature,
            "audio_path": str(output),
            "audio_sha256": sha256_file(output),
            "model_snapshot": str(snapshot),
            "acoustic_metrics": acoustic_metrics(output, len(sample["target_text"].split())),
            "manual_listening_required": True,
            "production_promotion_allowed": False,
        }
        receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        results.append(receipt)
    summary = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "sample_count": len(results),
        "samples": results,
        "production_promotion_allowed": False,
    }
    path = output_root / "generation-summary.json"
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"sample_count": len(results), "summary": str(path)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fish S2 Pro rescue for missing Doctor audiodrama functions.")
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
            result = shared.analyze_identity(args)
        elif args.command == "package":
            result = shared.package(args)
        else:
            result = shared.validate(args)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
