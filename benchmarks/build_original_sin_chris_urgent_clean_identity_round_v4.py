#!/usr/bin/env python3
"""Build a final clean-identity Chris urgent-authority comparison."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from tts import TTSEngine
from benchmarks.build_original_sin_direct_substitution_round import encode_proxy, probe_audio
from benchmarks.build_original_sin_overlap_reference_round import WHISPER_MODEL_KEY, metrics, sha256_file, transcribe, utc_now, write_json
from benchmarks.build_original_sin_overlap_reference_repair_round import project_hashes
import benchmarks.build_original_sin_unseen_expression_round as expression_base
from benchmarks.original_sin_overlap_word_alignment import normalized_words, transcript_comparison
from model_registry import resolve_model_path


ROUND_ID = "alexandria_original_sin_chris_urgent_clean_identity_v4"
DEFAULT_PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")
DEFAULT_PLAN = Path(__file__).with_name("original_sin_chris_urgent_clean_identity_plan_v4.json")
CANONICAL_SOURCE = Path("/Users/tristan/pinokio/api/alexandria-audiobook.git")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def qwen_voice(anchor: dict, seed: int) -> dict:
    return {
        "type": "clone",
        "voice": "Ryan",
        "seed": str(seed),
        "ref_audio": str(anchor["path"]),
        "ref_text": anchor["text"],
        "clone_backend": "qwen3_instruction_controlled",
        "instruction_clone_temperature": 0.75,
        "instruction_clone_top_k": 50,
        "instruction_clone_top_p": 0.95,
        "instruction_clone_repetition_penalty": 1.5,
        "instruction_clone_max_tokens": 2000,
    }


def speed_and_gain(path: Path, speed: float = 1.08, gain: float = 1.12) -> None:
    audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    target_frames = max(1, round(len(audio) / speed))
    changed = resample(audio, target_frames, axis=0).astype(np.float32)
    changed *= gain
    peak = float(np.max(np.abs(changed)))
    if peak > 0.89:
        changed *= 0.89 / peak
    sf.write(str(path), changed, rate, subtype="PCM_16")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    project = args.project_root.expanduser().resolve()
    plan = read_json(DEFAULT_PLAN)
    if plan.get("round_id") != ROUND_ID or len(plan.get("routes") or []) != 4:
        raise RuntimeError("Chris clean-identity plan mismatch")
    chunks = read_json(project / "chunks.json")
    chunk = chunks[int(plan["chunk_id"])]
    if chunk.get("speaker") != plan["book_speaker"] or normalized_words(chunk.get("text")) != normalized_words(plan["text"]):
        raise RuntimeError("Chris target chunk mismatch")
    transcript = read_json(project / "external_workflows/big_finish_overlap_reference_v1/private/transcript.json")["segments"]
    adaptation = " ".join(normalized_words(" ".join(str(row.get("text") or "") for row in transcript)))
    if " ".join(normalized_words(plan["text"])) in adaptation:
        raise RuntimeError("Chris target line occurs in adaptation")
    current = read_json(CANONICAL_SOURCE / "voice_config.json")["CHRIS"]
    anchor_path = (CANONICAL_SOURCE / current["ref_audio"]).resolve()
    anchor = {"path": anchor_path, "text": current["ref_text"], "sha256": sha256_file(anchor_path), "candidate_id": "current_identity:CHRIS", "round_id": "current_alexandria_identity"}
    output = project / "external_workflows/big_finish_overlap_reference_v1/chris_urgent_clean_identity_round_v4"
    if output.exists():
        if not args.replace:
            raise RuntimeError(f"Output exists: {output}")
        shutil.rmtree(output)
    before = project_hashes(project)
    whisper = str(resolve_model_path(WHISPER_MODEL_KEY, local_files_only=True))
    engine = TTSEngine({"tts": {"mode": "local", "language": "English", "device": "auto"}})
    private = output / "private/audio"; private.mkdir(parents=True, exist_ok=True)
    candidates = []
    for route in plan["routes"]:
        cid = hashlib.sha256(f"{ROUND_ID}:{route}:{anchor['sha256']}".encode()).hexdigest()[:16]
        wav = private / f"{cid}.wav"
        if route.startswith("qwen"):
            instruction = plan["instruction"] + (" Deliver even faster and louder, as an immediate command." if route.endswith("fast") else "")
            ok = engine.generate_voice(plan["text"], instruction, plan["book_speaker"], {plan["book_speaker"]: qwen_voice(anchor, int(plan["seed"]))}, str(wav))
            if not ok:
                continue
            backend = "qwen3_instruction_controlled"
            receipt = {}
        else:
            instruction = plan["instruction"] + (" Shout with clipped speed and immediate authority." if route.endswith("fast") else "")
            receipt = expression_base.fish_inline_generate(anchor=anchor, text=plan["text"], instruction=instruction, output=wav)
            backend = "fish_s2.1_pro_free_inline_zero_shot"
        if route.endswith("fast"):
            speed_and_gain(wav)
        observed = transcribe(wav, whisper)
        source_check = transcript_comparison([plan["text"]], observed, {})
        if source_check["word_error_rate"] != 0.0 or not source_check["first_word_present"] or not source_check["last_word_present"]:
            continue
        proxy = private / f"{cid}.mp3"; encode_proxy(wav, proxy, bitrate="192k")
        proxy_check = transcript_comparison([plan["text"]], transcribe(proxy, whisper), {})
        probe = probe_audio(proxy)
        if proxy_check["word_error_rate"] != 0.0 or not proxy_check["first_word_present"] or not proxy_check["last_word_present"] or probe["codec_name"] != "mp3" or probe["sample_rate"] != 44100 or probe["channels"] != 2:
            continue
        candidates.append({
            "candidate_id": cid,
            "route_key": route,
            "actual_backend": backend,
            "fallback_used": False,
            "receipt": receipt,
            "wav_path": wav,
            "wav_metrics": metrics(wav),
            "source_objective": source_check,
            "proxy_path": proxy,
            "proxy_sha256": sha256_file(proxy),
            "proxy_probe": probe,
            "proxy_objective": proxy_check,
        })
    if len(candidates) < 2:
        raise RuntimeError("Fewer than two Chris clean-identity candidates survived")
    groups = [{
        "group": "chris_urgent_authority_clean_identity",
        "character": plan["character"],
        "book_speaker": plan["book_speaker"],
        "chunk_id": int(plan["chunk_id"]),
        "mode": plan["mode"],
        "text": plan["text"],
        "instruction": plan["instruction"],
        "anchor": {**anchor, "path": str(anchor["path"])},
        "candidates": candidates,
    }]
    expression_base.ROUND_ID = ROUND_ID
    expression_base.build_review(output, groups)
    after = project_hashes(project)
    if before != after:
        raise RuntimeError("Protected project hashes changed")
    write_json(output / "generation-summary.json", {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "generated_at": utc_now(),
        "planned_candidate_count": 4,
        "candidate_count": len(candidates),
        "protected_project_hashes_before": before,
        "protected_project_hashes_after": after,
        "production_changes": False,
        "output_root": str(output),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
