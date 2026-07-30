#!/usr/bin/env python3
"""Prepare a 12-sample clean-reference urgency-control retest for Chris."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".omo/evidence/chris-roz-multimodel-round1-v1"
REFERENCE_BANK = Path(
    "/Users/tristan/.devspace/worktrees/alexandria-audiobook.git-f3df5335/"
    ".omo/evidence/chris-roz-cleanup-v1/reference-bank.json"
)
DEFAULT_OUTPUT = ROOT / ".omo/evidence/chris-urgency-control-retest-v1"
ROUND_ID = "alexandria_chris_urgency_control_retest_v1"
TARGET = "Get behind me now. Whatever comes through that door, it is not reaching you."


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def stable_id(*parts: Any, length: int = 20) -> str:
    return hashlib.sha256("\x1f".join(map(str, parts)).encode()).hexdigest()[:length]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    output = Path(args.output_root).expanduser().resolve()
    source_internal = read_json(SOURCE / "private/internal-manifest.json")
    clean = SOURCE / "private/references/identity/chris/clean_actor/reference.wav"
    clean_text = next(
        spec["reference"]["transcript"]
        for spec in source_internal["sample_specs"]
        if spec["identity_key"] == "chris" and spec["reference_tier"] == "clean_actor"
    )
    reference_target = output / "private/references/chris/clean_actor/reference.wav"
    reference_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(clean, reference_target)
    reference = {
        "reference_key": "chris:clean_actor",
        "identity_key": "chris",
        "identity_label": "Chris Cwej",
        "tier": "clean_actor",
        "candidate_id": "chris_identity_ooc_01",
        "audio_file": str(reference_target.relative_to(output)),
        "audio_sha256": sha256_file(reference_target),
        "transcript": clean_text,
        "transcript_sha256": sha256_text(clean_text),
    }

    performance_ids = ("chris_canonical_urgent_exposition", "chris_dread_protective")
    bank = read_json(REFERENCE_BANK)
    bank_rows = {
        str(row["candidate_id"]): row
        for row in bank["performance_bank"]["chris"]
    }
    performance = {}
    for candidate_id in performance_ids:
        source_row = bank_rows[candidate_id]
        source = Path(str(source_row["audio_path"]))
        target = output / f"private/references/performance/{candidate_id}.wav"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        performance[candidate_id] = {
            "candidate_id": candidate_id,
            "audio_file": str(target.relative_to(output)),
            "audio_sha256": sha256_file(target),
        }

    models = {row["key"]: row for row in source_internal["models"]}
    specs = []
    fish_variants = [
        ("untagged", TARGET),
        ("rich_scope", f"[urgent protective command throughout, fast controlled resolve, danger sustained through every phrase] {TARGET}"),
        ("full_scope", f"[Speak the entire line as an immediate protective command. Keep urgency and controlled danger audible from the first word through the final word; do not relax after 'now'. Do not shout.] {TARGET}"),
        ("punctuated_scope", "[urgent protective command throughout, clipped precision, sustained controlled danger] Get behind me—now. Whatever comes through that door, it is not reaching you."),
    ]
    vox_variants = [
        ("baseline", "Begin with an immediate protective command, then sustain urgent resolve and controlled danger without shouting.", 2.0, 10),
        ("strong_instruction", "Command them behind you immediately. Sustain fast, forceful protective urgency through every phrase, with credible danger and no relaxation. Do not shout.", 2.0, 10),
        ("strong_cfg", "Command them behind you immediately. Sustain fast, forceful protective urgency through every phrase, with credible danger and no relaxation. Do not shout.", 2.5, 10),
        ("strong_cfg_steps", "Command them behind you immediately. Sustain fast, forceful protective urgency through every phrase, with credible danger and no relaxation. Do not shout.", 3.0, 15),
    ]
    index_variants = [
        ("urgent_a065", "chris_canonical_urgent_exposition", 0.65),
        ("urgent_a100", "chris_canonical_urgent_exposition", 1.0),
        ("protective_a085", "chris_dread_protective", 0.85),
        ("protective_a100", "chris_dread_protective", 1.0),
    ]

    for variant, prompt in fish_variants:
        model_key = "fish_s2_pro_cloud"
        cell = stable_id(ROUND_ID, model_key, variant)
        specs.append({
            "round_id": ROUND_ID, "sample_id": f"curg_{cell}", "blind_id": stable_id("blind", cell, length=16),
            "model_key": model_key, "model_label": models[model_key]["label"], "identity_key": "chris", "identity_label": "Chris Cwej",
            "reference_tier": "clean_actor", "reference": reference, "style": "urgent_authority", "style_label": "Urgent protective authority",
            "group": "urgency", "target_text": TARGET, "target_text_sha256": sha256_text(TARGET),
            "instruction": "Sustain unmistakable protective urgency through the entire line.", "instruction_sha256": sha256_text("Sustain unmistakable protective urgency through the entire line."),
            "fish_prompt_mode": variant, "fish_prompt": prompt, "fish_prompt_sha256": sha256_text(prompt),
            "emotion_reference": reference, "index_alpha": 0.0, "repeat": 1, "seed": 13000 + len(specs),
            "output_file": f"outputs/{model_key}/{variant}.wav", "result_file": f"outputs/{model_key}/{variant}.json", "status": "pending_generation",
            "control": {"prompt_mode": variant, "temperature": 0.7, "top_p": 0.7, "format": "wav", "sample_rate": 44100, "latency": "normal", "repetition_penalty": 1.2, "condition_on_previous_chunks": True, "request_pause_seconds": 0.25, "max_attempts": 4},
            "production_promotion_allowed": False,
        })
    for variant, instruction, cfg, steps in vox_variants:
        model_key = "voxcpm2_controllable_clone"
        cell = stable_id(ROUND_ID, model_key, variant)
        specs.append({
            "round_id": ROUND_ID, "sample_id": f"curg_{cell}", "blind_id": stable_id("blind", cell, length=16),
            "model_key": model_key, "model_label": models[model_key]["label"], "identity_key": "chris", "identity_label": "Chris Cwej",
            "reference_tier": "clean_actor", "reference": reference, "style": "urgent_authority", "style_label": "Urgent protective authority",
            "group": "urgency", "target_text": TARGET, "target_text_sha256": sha256_text(TARGET),
            "instruction": instruction, "instruction_sha256": sha256_text(instruction), "fish_prompt_mode": None, "fish_prompt": TARGET, "fish_prompt_sha256": sha256_text(TARGET),
            "emotion_reference": reference, "index_alpha": 0.0, "repeat": 1, "seed": 13000 + len(specs),
            "output_file": f"outputs/{model_key}/{variant}.wav", "result_file": f"outputs/{model_key}/{variant}.json", "status": "pending_generation",
            "control": {"instruct": instruction, "cfg_value": cfg, "inference_timesteps": steps, "warmup_patches": 1, "max_tokens": 1800},
            "production_promotion_allowed": False,
        })
    for variant, emotion_id, alpha in index_variants:
        model_key = "indextts2_matched_control"
        emotion = performance[emotion_id]
        cell = stable_id(ROUND_ID, model_key, variant)
        instruction = "Sustain unmistakable protective urgency through the entire line."
        specs.append({
            "round_id": ROUND_ID, "sample_id": f"curg_{cell}", "blind_id": stable_id("blind", cell, length=16),
            "model_key": model_key, "model_label": models[model_key]["label"], "identity_key": "chris", "identity_label": "Chris Cwej",
            "reference_tier": "clean_actor", "reference": reference, "style": "urgent_authority", "style_label": "Urgent protective authority",
            "group": "urgency", "target_text": TARGET, "target_text_sha256": sha256_text(TARGET),
            "instruction": instruction, "instruction_sha256": sha256_text(instruction), "fish_prompt_mode": None, "fish_prompt": TARGET, "fish_prompt_sha256": sha256_text(TARGET),
            "emotion_reference": emotion, "index_alpha": alpha, "repeat": 1, "seed": 13000 + len(specs),
            "output_file": f"outputs/{model_key}/{variant}.wav", "result_file": f"outputs/{model_key}/{variant}.json", "status": "pending_generation",
            "control": {"mechanism": "same_character_emotion_reference", "emotion_strength": alpha, "device": "mps", "use_fp16": False, "num_beams": 1, "greedy": True, "diffusion_steps": 8, "max_mel_tokens": 600},
            "production_promotion_allowed": False,
        })

    internal = {"schema_version": 1, "round_id": ROUND_ID, "purpose": "chris_clean_reference_urgency_control_retest", "models": list(models.values()), "sample_specs": specs, "tnia_miller_included": False, "production_promotion_allowed": False}
    write_json(output / "private/internal-manifest.json", internal)
    index_samples = []
    for spec in specs:
        if spec["model_key"] != "indextts2_matched_control":
            continue
        emotion = spec["emotion_reference"]
        index_samples.append({
            "sample_id": spec["sample_id"], "blind_id": spec["blind_id"], "group": "urgency", "identity_key": "chris", "identity_label": "Chris Cwej",
            "style": "urgent_authority", "selection_kind": "clean_reference_urgency_control_retest", "source_selection_sample_id": reference["candidate_id"],
            "source_instruction_sha256": spec["instruction_sha256"], "source_seed": spec["seed"], "seed": spec["seed"],
            "reference_audio": str(reference_target), "reference_audio_sha256": reference["audio_sha256"],
            "emotion_audio_prompt": str(output / emotion["audio_file"]), "emotion_audio_sha256": emotion["audio_sha256"],
            "emotion_strength": spec["index_alpha"], "emotion_strength_origin": "urgency_control_retest", "text": TARGET,
            "output_file": str(output / spec["output_file"]), "result_file": str(output / spec["result_file"]), "generation": {"max_mel_tokens": 600},
        })
    write_json(output / "private/indextts2-manifest.json", {"schema_version": 1, "round_id": ROUND_ID, "runtime_profile": {"persistent_worker_count": 2, "use_fp16": False, "device": "mps", "greedy": True, "num_beams": 1, "diffusion_steps": 8}, "samples": index_samples, "tnia_miller_included": False, "production_promotion_allowed": False})
    write_json(output / "manifest.json", {"schema_version": 1, "round_id": ROUND_ID, "sample_count": len(specs), "model_counts": {key: sum(s["model_key"] == key for s in specs) for key in models}, "production_promotion_allowed": False})
    print(json.dumps({"output": str(output), "samples": len(specs), "index_samples": len(index_samples)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
