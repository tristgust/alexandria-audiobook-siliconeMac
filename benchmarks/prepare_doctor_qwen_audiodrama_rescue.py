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

ROUND_ID = "alexandria_doctor_qwen_audiodrama_rescue_v1"


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
        sample["backend_key"] = "qwen3_base_reference_rescue"
        sample["temperature"] = 0.65 if float(sample["cfg_value"]) < 2.0 else 0.85
        sample["top_k"] = 50
        sample["top_p"] = 0.95
        sample["repetition_penalty"] = 1.5
        sample["sample_id"] = fingerprint(
            {
                "round": ROUND_ID,
                "mode": sample["mode"],
                "temperature": sample["temperature"],
                "reference": sample["reference_audio_sha256"],
                "text": sample["target_text"],
            }
        )
    matrix_path.write_text(json.dumps(matrix, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {**result, "round_id": ROUND_ID}


def generate(args: argparse.Namespace) -> dict[str, Any]:
    import mlx.core as mx

    from run_multimodel_round1_mlx import collect_results, disable_optional_sklearn, load_model

    disable_optional_sklearn()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    output_root = Path(args.output_root).expanduser().resolve()
    matrix = json.loads((output_root / "matrix.json").read_text(encoding="utf-8"))
    model, snapshot = load_model(
        "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
        "e7dd0585652209fa0d7783659aad4e8a324de11c",
    )
    results = []
    for sample in matrix["samples"]:
        variant = "stable" if float(sample["temperature"]) < 0.8 else "expressive"
        output = output_root / "generated" / sample["mode"] / f"{variant}.wav"
        receipt_path = output_root / "generation-receipts" / sample["mode"] / f"{variant}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        if output.is_file() and receipt_path.is_file() and not args.force:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("audio_sha256") == sha256_file(output):
                results.append(receipt)
                continue
        mx.random.seed(20260724)
        random.seed(20260724)
        np.random.seed(20260724)
        generated = model.generate(
            text=sample["target_text"],
            ref_audio=sample["reference_audio"],
            ref_text=sample["reference_text"],
            lang_code="English",
            temperature=float(sample["temperature"]),
            top_k=int(sample["top_k"]),
            top_p=float(sample["top_p"]),
            repetition_penalty=float(sample["repetition_penalty"]),
            max_tokens=1800,
            verbose=False,
        )
        mono, sample_rate = collect_results(model, generated)
        mono = np.asarray(mono, dtype=np.float32).reshape(-1)
        if mono.size == 0:
            raise RuntimeError(f"Qwen returned empty audio for {sample['sample_id']}")
        peak = float(np.max(np.abs(mono)))
        if peak > 0:
            mono *= min(1.0, 0.70 / peak)
        sf.write(output, mono, int(sample_rate), subtype="PCM_16")
        receipt = {
            **sample,
            "variant": variant,
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
    parser = argparse.ArgumentParser(description="Qwen Base reference rescue for missing Doctor audiodrama functions.")
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
