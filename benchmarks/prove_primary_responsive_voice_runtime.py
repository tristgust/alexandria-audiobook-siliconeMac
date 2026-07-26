#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable

import soundfile as sf

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from experimental_prompt_routing import resolve_experimental_prompt_override, sha256_file
from production_prompt_routes import (
    BENNY_SOURCE_SHA256,
    DOCTOR_SOURCE_SHA256,
    build_primary_responsive_voice_config,
)
from tts import TTSEngine


ROUND_ID = "alexandria_primary_responsive_voice_runtime_proof_v1"
SAMPLES: tuple[dict[str, str], ...] = (
    {
        "sample_id": "narrator_wounded_anger",
        "speaker": "NARRATOR",
        "text": "After everything I did for you, this is how you chose to repay me.",
        "instruction": (
            "Wounded anger; emotionally raw but controlled, with pressure building "
            "through the final phrase."
        ),
    },
    {
        "sample_id": "benny_fatalistic_dread",
        "speaker": "BERNICE",
        "text": "Who's going to save us this time?",
        "instruction": (
            "Anxious and searching; hesitant pace, let the question expose the fear."
        ),
    },
    {
        "sample_id": "doctor_playful_identity",
        "speaker": "THE DOCTOR",
        "text": "Oh, wonderful. A locked door, a missing key, and precisely no time to think.",
        "instruction": (
            "Dryly amused; conversational pace, underplay the punch line while "
            "keeping the eccentric calculation underneath."
        ),
    },
)


class RuntimeProofError(RuntimeError):
    pass


def read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeProofError(f"{label} could not be read: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeProofError(f"{label} must be a JSON object.")
    return value


def copy_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise RuntimeProofError(f"Required source file is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def prepare_project(
    *,
    source_root: Path,
    output_root: Path,
    benny_prompt_source: Path,
    doctor_prompt_source: Path,
) -> dict[str, Any]:
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)
    source_config = read_json_object(source_root / "voice_config.json", "Source voice config")
    selected = {}
    for speaker in ("NARRATOR", "BERNICE", "THE DOCTOR"):
        voice = source_config.get(speaker)
        if not isinstance(voice, dict):
            raise RuntimeProofError(f"Source voice {speaker!r} is missing.")
        relative = Path(str(voice.get("ref_audio") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeProofError(f"Source voice {speaker!r} has an unsafe reference path.")
        copy_file(source_root / relative, output_root / relative)
        selected[speaker] = voice

    if sha256_file(benny_prompt_source) != BENNY_SOURCE_SHA256:
        raise RuntimeProofError("Benny prompt hash does not match the reviewed source.")
    if sha256_file(doctor_prompt_source) != DOCTOR_SOURCE_SHA256:
        raise RuntimeProofError("Doctor prompt hash does not match the reviewed source.")
    copy_file(
        benny_prompt_source,
        output_root / "production_prompt_routes" / "benny_credible_fear.wav",
    )
    copy_file(
        doctor_prompt_source,
        output_root / "production_prompt_routes" / "doctor_playful_identity.wav",
    )
    config = build_primary_responsive_voice_config(
        project_root=output_root,
        voice_config=selected,
        approved_at_utc="2026-07-26T06:00:00Z",
    )
    return config


def apply_seed(config: dict[str, Any], seed: int) -> dict[str, Any]:
    seeded = json.loads(json.dumps(config))
    for voice in seeded.values():
        if isinstance(voice, dict):
            voice["seed"] = str(seed)
    return seeded


def generate(args: argparse.Namespace) -> dict[str, Any]:
    source_root = Path(args.source_project_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    config = prepare_project(
        source_root=source_root,
        output_root=output_root,
        benny_prompt_source=Path(args.benny_prompt_source).expanduser().resolve(),
        doctor_prompt_source=Path(args.doctor_prompt_source).expanduser().resolve(),
    )
    config = apply_seed(config, int(args.seed))
    (output_root / "voice_config.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    engine = TTSEngine(
        {
            "tts": {
                "mode": "local",
                "language": "English",
                "device": "auto",
            }
        }
    )
    generated = []
    for sample in SAMPLES:
        output = output_root / f"proof_{sample['sample_id']}.wav"
        route = resolve_experimental_prompt_override(
            voice_data=config[sample["speaker"]],
            instruction=sample["instruction"],
            project_root=output_root,
        )
        success = engine.generate_voice(
            sample["text"],
            sample["instruction"],
            sample["speaker"],
            config,
            str(output),
        )
        if not success or not output.is_file():
            raise RuntimeProofError(f"Generation failed for {sample['sample_id']}.")
        info = sf.info(output)
        if info.frames <= 0 or info.channels != 1:
            raise RuntimeProofError(f"Generated audio is invalid for {sample['sample_id']}.")
        generated.append(
            {
                **sample,
                "audio_path": str(output),
                "audio_sha256": sha256_file(output),
                "sample_rate": info.samplerate,
                "channels": info.channels,
                "duration_seconds": round(info.duration, 6),
                "generation_seed": int(args.seed),
                "clone_backend": config[sample["speaker"]]["clone_backend"],
                "selected_prompt_route": route["route_key"] if route else None,
                "selected_prompt_role": route["prompt_role"] if route else None,
                "production_promotion_allowed": (
                    bool(route["production_promotion_allowed"]) if route else True
                ),
            }
        )
    manifest = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "sample_count": len(generated),
        "all_instruction_controlled": all(
            row["clone_backend"] == "qwen3_instruction_controlled"
            for row in generated
        ),
        "all_export_eligible": all(
            row["production_promotion_allowed"] for row in generated
        ),
        "samples": generated,
    }
    (output_root / "runtime-proof.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the real MLX instruction-controlled primary voice production proof."
    )
    parser.add_argument("--source-project-root", required=True)
    parser.add_argument("--benny-prompt-source", required=True)
    parser.add_argument("--doctor-prompt-source", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--seed", type=int, default=104729)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        result = generate(args)
    except (RuntimeProofError, OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
