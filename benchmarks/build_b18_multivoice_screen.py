#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
BENCHMARKS = ROOT / "benchmarks"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))

from huggingface_hub import snapshot_download  # noqa: E402
from fish_cloud_tts import (  # noqa: E402
    FishCloudBackend,
    SpeakerSimilarityScorer,
    audio_features,
    terminal_text_matches,
)
from mlx_backend import MLXBackend  # noqa: E402
from model_registry import engine_record_payload, model_spec, resolve_model_path  # noqa: E402
from recurring_voice_routing import resolve_recurring_voice_route  # noqa: E402
from responsive_voice_backend import ResponsiveVoiceBackend  # noqa: E402
from transcription_evaluator import evaluate_transcriptions  # noqa: E402
from voice_effects import apply_voice_effect_chain  # noqa: E402


ROUND_ID = "b18_multivoice_archetype_screen_20260803"
DEFAULT_PROJECT = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Alexandria"
    / "Projects"
    / "original-sin--e6286665"
)
DEFAULT_OUTPUT = ROOT / "training_sidecar_runtime" / ROUND_ID
SEED = 130363
QWEN_ENGINE = engine_record_payload("qwen3_instruction_controlled")["engine_id"]
LOCAL_MODELS = {
    "fish_s2_pro_local": (
        "mlx-community/fish-audio-s2-pro",
        "eccd57bf5c1ebc13cb2f993df867f4e49931a36a",
    ),
    "moss_local_v15": (
        "OpenMOSS-Team/MOSS-TTS-Local-Transformer-v1.5",
        "be7766a6735b98bd793f7c79fb720b4d0f5d13b8",
    ),
    "moss_nano": (
        "mlx-community/MOSS-TTS-Nano-100M",
        "229a9c51bb0ffff6fd0dbe53b5bf0c441e438a79",
    ),
}

SPEAKERS: tuple[dict[str, Any], ...] = (
    {
        "speaker_key": "THE DOCTOR",
        "display_name": "The Doctor",
        "archetype": "eccentric dry authority",
        "source_chunk_id": 63,
        "text": "I left him my scarf, but it clashes with his plumage.",
        "instruction": (
            "Dryly amused, wry and eccentric, with clipped precision and "
            "understated authority."
        ),
    },
    {
        "speaker_key": "BERNICE",
        "display_name": "Benny",
        "archetype": "wry warmth and sardonic concern",
        "source_chunk_id": 74,
        "text": (
            "The only sight I want to see at the moment is the inside of a "
            "tumbler of whisky. Let’s go."
        ),
        "instruction": (
            "Sardonic concern with wry intelligence, quick emotional shifts, "
            "and guarded warmth."
        ),
    },
    {
        "speaker_key": "CHRIS CWEJ",
        "display_name": "Chris Cwej",
        "archetype": "urgent youthful authority",
        "source_chunk_id": 5113,
        "text": (
            "Look, if you were shooting straight, I wouldn’t be in the way!"
        ),
        "instruction": (
            "Urgent authority and protective command with strong projection, "
            "earnest intensity, and immediate control."
        ),
    },
    {
        "speaker_key": "ROZ FORRESTER",
        "display_name": "Roz Forrester",
        "archetype": "dry streetwise sarcasm",
        "source_chunk_id": 608,
        "text": (
            "We can’t assume anything until we’ve seen the scan. Yeah. Right. "
            "Dream on, kid."
        ),
        "instruction": (
            "Dry banter and professional sarcasm with clipped timing, "
            "restrained impatience, and guarded amusement."
        ),
    },
    {
        "speaker_key": "COMPUTER",
        "display_name": "Computer",
        "archetype": "processed formal machine voice",
        "source_chunk_id": 1261,
        "text": (
            "Information concerning the prisoner of war identified by that "
            "number is classified."
        ),
        "instruction": (
            "Formal system response and computer announcement: flat synthetic "
            "delivery, exact diction, and neutral timing."
        ),
        "effect_isolation": "computer_terminal_v3",
    },
    {
        "speaker_key": "TOBIAS VAUGHN",
        "display_name": "Tobias Vaughn",
        "archetype": "cultivated concealed menace",
        "source_chunk_id": 4616,
        "text": (
            "Come to me, Doctor. I’ve opened the way for you. I’ve made it "
            "easy. Don’t disappoint me now."
        ),
        "instruction": (
            "Cultivated menace with polished control, chilling patience, a "
            "subtle threat, and no melodrama."
        ),
    },
    {
        "speaker_key": "POWERLESS FRIENDLESS",
        "display_name": "Powerless Friendless (Hith)",
        "archetype": "nonhuman panic with alien modulation",
        "source_chunk_id": 3342,
        "text": "They’ll spot me instantly. I’m a Hith! We’re at war!",
        "instruction": (
            "Panicked urgency with exposed fear, strained projection, alien "
            "vulnerability, and intelligible words."
        ),
        "effect_isolation": "powerless_alien_modulation_v1",
    },
)

METHODS = (
    "current_route",
    "qwen_controlled_identity",
    "fish_s2_pro_local",
    "fish_s21_pro_free",
    "moss_local_v15",
    "moss_nano",
)


class MultiVoiceScreenError(RuntimeError):
    pass


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _resolve_project_asset(project: Path, value: Any, label: str) -> Path:
    text = str(value or "").strip()
    if not text:
        raise MultiVoiceScreenError(f"{label} is missing.")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = project / candidate
    resolved = candidate.resolve()
    if not resolved.is_file():
        raise MultiVoiceScreenError(f"{label} does not exist: {resolved}")
    return resolved


def _combined_instruction(voice: dict[str, Any], instruction: str) -> str:
    style = str(
        voice.get("character_style")
        or voice.get("default_style")
        or ""
    ).strip()
    return " ".join(part for part in (instruction.strip(), style) if part)


def _candidate_path(output: Path, speaker_key: str, method: str) -> Path:
    safe = "_".join(speaker_key.casefold().split())
    return output / "candidates" / safe / f"{method}.wav"


def _run_mlx_cli(
    *,
    model_path: Path,
    method: str,
    text: str,
    reference_audio: Path,
    reference_text: str,
    output_path: Path,
    instruction: str,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{method}-",
        dir=output_path.parent,
    ) as temporary:
        temporary_root = Path(temporary)
        generation_text = (
            f"[{instruction}] {text}"
            if method == "fish_s2_pro_local"
            else text
        )
        command = [
            str(ROOT / "app" / "env" / "bin" / "python"),
            "-m",
            "mlx_audio.tts.generate",
            "--model",
            str(model_path),
            "--text",
            generation_text,
            "--ref_audio",
            str(reference_audio),
            "--ref_text",
            reference_text,
            "--lang_code",
            "en",
            "--max_tokens",
            "500",
            "--output_path",
            str(temporary_root),
            "--file_prefix",
            "candidate",
        ]
        env = dict(os.environ)
        env["HF_HUB_OFFLINE"] = "1"
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=360,
            check=False,
        )
        generated = temporary_root / "candidate_000.wav"
        if completed.returncode != 0 or not generated.is_file():
            raise MultiVoiceScreenError(
                f"{method} generation failed: "
                f"{(completed.stderr or completed.stdout)[-3000:]}"
            )
        os.replace(generated, output_path)
        return {
            "command": command,
            "stdout_tail": completed.stdout[-3000:],
            "stderr_tail": completed.stderr[-3000:],
        }


def _generate_current_route(
    *,
    project: Path,
    voice: dict[str, Any],
    speaker: dict[str, Any],
    output_path: Path,
    qwen: MLXBackend,
    responsive: ResponsiveVoiceBackend,
) -> dict[str, Any]:
    route = resolve_recurring_voice_route(
        voice_data=voice,
        instruction=speaker["instruction"],
        project_root=project,
        verify_audio=True,
    )
    if route is None:
        raise MultiVoiceScreenError(
            f"{speaker['speaker_key']} has no active responsive route."
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if route["backend"] == QWEN_ENGINE:
        reference_audio = Path(
            route.get("performance_audio_path")
            or route["identity_audio_path"]
        )
        reference_text = str(
            route.get("performance_text")
            or route["identity_text"]
        )
        qwen.generate_instruction_controlled_clone(
            text=speaker["text"],
            ref_audio=str(reference_audio),
            ref_text=reference_text,
            instruct=_combined_instruction(voice, speaker["instruction"]),
            output_path=str(output_path),
            temperature=float(voice.get("instruction_clone_temperature", 0.75)),
            top_k=int(voice.get("instruction_clone_top_k", 50)),
            top_p=float(voice.get("instruction_clone_top_p", 0.95)),
            repetition_penalty=float(
                voice.get("instruction_clone_repetition_penalty", 1.5)
            ),
            max_tokens=int(voice.get("instruction_clone_max_tokens", 2000)),
            seed=int(voice.get("seed", SEED)),
            request_label=f"multivoice:{speaker['speaker_key']}:current",
        )
        receipt: dict[str, Any] = {
            "backend": QWEN_ENGINE,
            "repair_strategy": "direct_qwen_route",
        }
    else:
        if not responsive.backend_available(str(route["backend"])):
            raise MultiVoiceScreenError(
                f"Current route backend unavailable: {route['backend']}"
            )
        receipt = responsive.generate(
            route=route,
            text=speaker["text"],
            output_path=output_path,
            seed=int(voice.get("seed", SEED)),
        )
    effect_receipt = apply_voice_effect_chain(
        output_path,
        route.get("effect_chain"),
    )
    return {
        "route_key": route["route_key"],
        "route_backend": route["backend"],
        "route_mapping_reason": route["mapping_reason"],
        "route_evidence_round_id": route["evidence_round_id"],
        "effect_chain": route.get("effect_chain"),
        "effect_receipt": effect_receipt,
        "backend_receipt": receipt,
    }


def _generate_candidate(
    *,
    project: Path,
    voice: dict[str, Any],
    speaker: dict[str, Any],
    method: str,
    output: Path,
    qwen: MLXBackend,
    responsive: ResponsiveVoiceBackend,
    model_paths: dict[str, Path],
) -> dict[str, Any]:
    target = _candidate_path(output, speaker["speaker_key"], method)
    if target.is_file():
        return {"status": "reused", "output_path": str(target)}
    reference_audio = _resolve_project_asset(
        project,
        voice.get("ref_audio"),
        f"{speaker['speaker_key']} reference audio",
    )
    reference_text = str(voice.get("ref_text") or "").strip()
    if not reference_text:
        raise MultiVoiceScreenError(
            f"{speaker['speaker_key']} reference transcript is missing."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    if method == "current_route":
        receipt = _generate_current_route(
            project=project,
            voice=voice,
            speaker=speaker,
            output_path=target,
            qwen=qwen,
            responsive=responsive,
        )
    elif method == "qwen_controlled_identity":
        qwen.generate_instruction_controlled_clone(
            text=speaker["text"],
            ref_audio=str(reference_audio),
            ref_text=reference_text,
            instruct=_combined_instruction(voice, speaker["instruction"]),
            output_path=str(target),
            temperature=float(voice.get("instruction_clone_temperature", 0.75)),
            top_k=int(voice.get("instruction_clone_top_k", 50)),
            top_p=float(voice.get("instruction_clone_top_p", 0.95)),
            repetition_penalty=float(
                voice.get("instruction_clone_repetition_penalty", 1.5)
            ),
            max_tokens=int(voice.get("instruction_clone_max_tokens", 2000)),
            seed=int(voice.get("seed", SEED)),
            request_label=f"multivoice:{speaker['speaker_key']}:qwen",
        )
        receipt = {"backend": QWEN_ENGINE}
    elif method == "fish_s21_pro_free":
        backend = FishCloudBackend(
            model="s2.1-pro-free",
            candidate_count=2,
            difficult_candidate_count=2,
        )
        result = backend.generate(
            text=speaker["text"],
            instruction=speaker["instruction"],
            speaker=speaker["speaker_key"],
            reference_audio=reference_audio,
            reference_text=reference_text,
            output_path=target,
            require_delivery_evidence=False,
        )
        receipt = {
            "backend": "fish_s2_pro_cloud",
            "model": "s2.1-pro-free",
            "result": {
                "output_path": result.output_path,
                "selected": result.selected.as_dict(),
                "candidates": [item.as_dict() for item in result.candidates],
                "style": result.style,
                "reference_fingerprint": result.reference_fingerprint,
                "reference_model_reused": result.reference_model_reused,
            },
        }
    else:
        receipt = _run_mlx_cli(
            model_path=model_paths[method],
            method=method,
            text=speaker["text"],
            reference_audio=reference_audio,
            reference_text=reference_text,
            output_path=target,
            instruction=speaker["instruction"],
        )
    if not target.is_file():
        raise MultiVoiceScreenError(f"Candidate output is missing: {target}")
    return {
        "status": "generated",
        "output_path": str(target),
        "output_sha256": sha256_file(target),
        "receipt": receipt,
    }


def generate_round(
    *,
    project_root: Path,
    output_root: Path,
    speaker_keys: set[str] | None = None,
    reset_speaker_keys: set[str] | None = None,
) -> dict[str, Any]:
    project = project_root.expanduser().resolve()
    output = output_root.expanduser().resolve()
    voice_config = json.loads((project / "voice_config.json").read_text(encoding="utf-8"))
    model_paths = {
        method: Path(
            snapshot_download(
                repo_id,
                revision=revision,
                local_files_only=True,
            )
        ).resolve()
        for method, (repo_id, revision) in LOCAL_MODELS.items()
    }
    qwen = MLXBackend(language="English")
    responsive = ResponsiveVoiceBackend(model_residency=qwen._memory)
    manifest_path = output / "generation_manifest.json"
    prior_rows: dict[str, dict[str, Any]] = {}
    if manifest_path.is_file():
        prior = json.loads(manifest_path.read_text(encoding="utf-8"))
        prior_rows = {
            str(row["candidate_id"]): row
            for row in prior.get("rows", [])
            if isinstance(row, dict) and row.get("candidate_id")
        }
    selected_speakers = [
        speaker
        for speaker in SPEAKERS
        if speaker_keys is None or speaker["speaker_key"] in speaker_keys
    ]
    if speaker_keys is not None:
        missing = speaker_keys - {
            speaker["speaker_key"] for speaker in selected_speakers
        }
        if missing:
            raise MultiVoiceScreenError(
                "Unknown speaker key(s): " + ", ".join(sorted(missing))
            )
    known_speaker_keys = {speaker["speaker_key"] for speaker in SPEAKERS}
    reset_keys = reset_speaker_keys or set()
    unknown_reset = reset_keys - known_speaker_keys
    if unknown_reset:
        raise MultiVoiceScreenError(
            "Unknown reset speaker key(s): "
            + ", ".join(sorted(unknown_reset))
        )
    for speaker_key in reset_keys:
        safe = "_".join(speaker_key.casefold().split())
        shutil.rmtree(output / "candidates" / safe, ignore_errors=True)
        prior_rows = {
            candidate_id: row
            for candidate_id, row in prior_rows.items()
            if row.get("speaker_key") != speaker_key
        }
    rows: dict[str, dict[str, Any]] = dict(prior_rows)
    try:
        for speaker in selected_speakers:
            voice = voice_config.get(speaker["speaker_key"])
            if not isinstance(voice, dict):
                raise MultiVoiceScreenError(
                    f"Missing Voice configuration: {speaker['speaker_key']}"
                )
            for method in METHODS:
                candidate_id = (
                    "_".join(speaker["speaker_key"].casefold().split())
                    + "__"
                    + method
                )
                try:
                    generation = _generate_candidate(
                        project=project,
                        voice=voice,
                        speaker=speaker,
                        method=method,
                        output=output,
                        qwen=qwen,
                        responsive=responsive,
                        model_paths=model_paths,
                    )
                    status = "generated"
                    error = None
                except Exception as exc:
                    generation = None
                    status = "failed"
                    error = f"{type(exc).__name__}: {exc}"
                rows[candidate_id] = {
                        "candidate_id": candidate_id,
                        "speaker_key": speaker["speaker_key"],
                        "display_name": speaker["display_name"],
                        "archetype": speaker["archetype"],
                        "source_chunk_id": speaker["source_chunk_id"],
                        "text": speaker["text"],
                        "instruction": speaker["instruction"],
                        "method": method,
                        "status": status,
                        "error": error,
                        "generation": generation,
                    }
            effect = speaker.get("effect_isolation")
            if effect:
                source = _candidate_path(
                    output,
                    speaker["speaker_key"],
                    "qwen_controlled_identity",
                )
                method = f"qwen_controlled_identity__{effect}"
                target = _candidate_path(output, speaker["speaker_key"], method)
                candidate_id = (
                    "_".join(speaker["speaker_key"].casefold().split())
                    + "__"
                    + method
                )
                try:
                    if not target.is_file():
                        shutil.copy2(source, target)
                        effect_receipt = apply_voice_effect_chain(target, str(effect))
                    else:
                        effect_receipt = None
                    status = "generated"
                    error = None
                    generation = {
                        "status": "generated",
                        "output_path": str(target),
                        "output_sha256": sha256_file(target),
                        "effect_chain": effect,
                        "effect_receipt": effect_receipt,
                    }
                except Exception as exc:
                    status = "failed"
                    error = f"{type(exc).__name__}: {exc}"
                    generation = None
                rows[candidate_id] = {
                        "candidate_id": candidate_id,
                        "speaker_key": speaker["speaker_key"],
                        "display_name": speaker["display_name"],
                        "archetype": speaker["archetype"],
                        "source_chunk_id": speaker["source_chunk_id"],
                        "text": speaker["text"],
                        "instruction": speaker["instruction"],
                        "method": method,
                        "status": status,
                        "error": error,
                        "generation": generation,
                    }
    finally:
        responsive.close()
    manifest = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "project_root": str(project),
        "output_root": str(output),
        "seed": SEED,
        "speaker_count": len(SPEAKERS),
        "candidate_count": len(rows),
        "rows": sorted(
            rows.values(),
            key=lambda row: (row["speaker_key"], row["method"]),
        ),
        "production_promotion_allowed": False,
        "live_project_mutated": False,
    }
    _atomic_json(output / "generation_manifest.json", manifest)
    return manifest


def evaluate_round(*, project_root: Path, output_root: Path) -> dict[str, Any]:
    project = project_root.expanduser().resolve()
    output = output_root.expanduser().resolve()
    generation = json.loads(
        (output / "generation_manifest.json").read_text(encoding="utf-8")
    )
    voice_config = json.loads((project / "voice_config.json").read_text(encoding="utf-8"))
    model = model_spec("mlx_whisper_base")
    snapshot = Path(resolve_model_path(model.repo_id, local_files_only=True))
    transcription_inputs = []
    for row in generation["rows"]:
        path = Path(str((row.get("generation") or {}).get("output_path") or ""))
        if row["status"] == "generated" and path.is_file():
            transcription_inputs.append(
                {
                    "sample_id": row["candidate_id"],
                    "path": str(path),
                    "text": row["text"],
                }
            )
    transcription = evaluate_transcriptions(
        {
            "model_status": {
                "cached": True,
                "revision": model.revision,
                "snapshot_path": str(snapshot),
            },
            "outputs": transcription_inputs,
        }
    )
    _atomic_json(output / "transcription_evaluation.json", transcription)
    scorer = SpeakerSimilarityScorer()
    objective_rows = []
    for row in generation["rows"]:
        candidate = copy.deepcopy(row)
        generated_path = Path(
            str((row.get("generation") or {}).get("output_path") or "")
        )
        measurement = transcription.get("measurements", {}).get(
            row["candidate_id"],
            {},
        )
        if row["status"] != "generated" or not generated_path.is_file():
            candidate.update(
                {
                    "eligible": False,
                    "exclusion_reason": "generation_failed",
                    "objective": None,
                }
            )
            objective_rows.append(candidate)
            continue
        voice = voice_config[row["speaker_key"]]
        reference = _resolve_project_asset(
            project,
            voice.get("ref_audio"),
            f"{row['speaker_key']} reference audio",
        )
        identity_score, identity_mode = scorer.score(reference, generated_path)
        features = audio_features(generated_path, row["text"])
        wer = measurement.get("word_error_rate")
        transcript = measurement.get("transcript")
        text_passed = (
            isinstance(wer, (int, float))
            and float(wer) <= 0.08
            and terminal_text_matches(row["text"], str(transcript or ""))
        )
        candidate.update(
            {
                "eligible": bool(text_passed),
                "exclusion_reason": (
                    None if text_passed else "authored_text_integrity_failed"
                ),
                "objective": {
                    "output_path": str(generated_path),
                    "output_sha256": sha256_file(generated_path),
                    "reference_path": str(reference),
                    "reference_sha256": sha256_file(reference),
                    "transcript": transcript,
                    "word_error_rate": wer,
                    "text_passed": bool(text_passed),
                    "identity_score": identity_score,
                    "identity_mode": identity_mode,
                    "features": asdict(features),
                },
            }
        )
        objective_rows.append(candidate)
    summary = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "row_count": len(objective_rows),
        "eligible_count": sum(row["eligible"] for row in objective_rows),
        "excluded_count": sum(not row["eligible"] for row in objective_rows),
        "rows": objective_rows,
        "production_promotion_allowed": False,
    }
    _atomic_json(output / "objective_summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=("generate", "evaluate", "all"),
        nargs="?",
        default="all",
    )
    parser.add_argument("--project-root", default=str(DEFAULT_PROJECT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--speaker",
        action="append",
        default=[],
        help="Generate only this exact speaker key; may be repeated.",
    )
    parser.add_argument(
        "--reset-speaker",
        action="append",
        default=[],
        help=(
            "Delete prior candidates and manifest rows for this exact speaker "
            "before generation; may be repeated."
        ),
    )
    args = parser.parse_args()
    project = Path(args.project_root)
    output = Path(args.output_root)
    result: dict[str, Any] = {}
    if args.command in {"generate", "all"}:
        result["generation"] = generate_round(
            project_root=project,
            output_root=output,
            speaker_keys=set(args.speaker) or None,
            reset_speaker_keys=set(args.reset_speaker) or None,
        )
    if args.command in {"evaluate", "all"}:
        result["objective"] = evaluate_round(
            project_root=project,
            output_root=output,
        )
    print(
        json.dumps(
            {
                "round_id": ROUND_ID,
                "generated_count": len(
                    (result.get("generation") or {}).get("rows", [])
                ),
                "eligible_count": (
                    (result.get("objective") or {}).get("eligible_count")
                ),
                "output_root": str(output.expanduser().resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
