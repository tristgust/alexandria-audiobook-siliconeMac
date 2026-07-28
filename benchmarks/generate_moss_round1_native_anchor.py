#!/usr/bin/env python3
"""Generate and register the fixed-seed MOSS-TTS Round 1 native anchor."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import mlx.core as mx
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))

from generate_multimodel_round1_native_anchors import (  # noqa: E402
    ANCHOR_TEXT,
    metrics,
    sha256_file,
    sha256_text,
)
from run_multimodel_round1_mlx import (  # noqa: E402
    collect_results,
    disable_optional_sklearn,
    exact_snapshot,
    load_model,
)

DEFAULT_EVIDENCE = ROOT / ".omo" / "evidence" / "b17-t05-multimodel-round1"
MODEL_REPO = "OpenMOSS-Team/MOSS-TTS-Local-Transformer-v1.5"
MODEL_REVISION = "be7766a6735b98bd793f7c79fb720b4d0f5d13b8"
TOKENIZER_REPO = "OpenMOSS-Team/MOSS-Audio-Tokenizer-v2"
TOKENIZER_REVISION = "f6e20e543b33d2c252a7ef71bdf8aa71e5ff9169"
NATIVE_INSTRUCTION = (
    "A clear adult English audiobook voice with a balanced midrange, natural "
    "pacing, precise diction, and restrained warmth."
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--seed", type=int, default=7003)
    parser.add_argument("--reuse-existing", action="store_true")
    args = parser.parse_args()

    disable_optional_sklearn()
    evidence_root = Path(args.evidence_root).expanduser().resolve()
    native_root = evidence_root / "references" / "native"
    native_root.mkdir(parents=True, exist_ok=True)
    output = native_root / "moss_alder.wav"

    model, model_snapshot = load_model(MODEL_REPO, MODEL_REVISION)
    tokenizer_snapshot = exact_snapshot(TOKENIZER_REPO, TOKENIZER_REVISION)
    started = time.perf_counter()
    if not (args.reuse_existing and output.is_file()):
        mx.random.seed(args.seed)
        audio, sample_rate = collect_results(
            model,
            model.generate(
                text=ANCHOR_TEXT,
                mode="generation",
                instruction=NATIVE_INSTRUCTION,
                language="English",
                max_tokens=4096,
                audio_tokenizer_source=str(tokenizer_snapshot),
                audio_temperature=1.7,
                audio_top_p=0.8,
                audio_top_k=25,
                n_vq_for_inference=12,
                stream=False,
            ),
        )
        sf.write(output, audio, sample_rate)

    record = {
        "identity_key": "native_moss_alder",
        "review_name": "Alder",
        "model_key": "moss_tts_local_v15",
        "kind": "fixed_seed_reference_less_anchor",
        "audio_file": output.name,
        "audio_sha256": sha256_file(output),
        "transcript": ANCHOR_TEXT,
        "transcript_sha256": sha256_text(ANCHOR_TEXT),
        "native_instruction": NATIVE_INSTRUCTION,
        "native_instruction_sha256": sha256_text(NATIVE_INSTRUCTION),
        "seed": args.seed,
        "generation_seconds": time.perf_counter() - started,
        "model_repo": MODEL_REPO,
        "model_revision": MODEL_REVISION,
        "model_snapshot": str(model_snapshot),
        "tokenizer_repo": TOKENIZER_REPO,
        "tokenizer_revision": TOKENIZER_REVISION,
        "tokenizer_snapshot": str(tokenizer_snapshot),
        "audio": metrics(output),
    }

    manifest_path = native_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = [
        item
        for item in manifest.get("records", [])
        if item.get("identity_key") != record["identity_key"]
    ]
    records.append(record)
    manifest["records"] = records
    manifest["record_count"] = len(records)
    manifest["pending_native_anchors"] = [
        item
        for item in manifest.get("pending_native_anchors", [])
        if item != record["identity_key"]
    ]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"manifest": str(manifest_path), "record": record}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
