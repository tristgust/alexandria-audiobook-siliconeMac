#!/usr/bin/env python3
"""Build the next blind repair round for unresolved overlap-character modes.

The round is non-installing. It combines approved adaptation references,
existing character-bank references, alternate model paths, and stronger
Computer-only post-processing. Production routing and project audio remain
unchanged until a later fail-closed review and explicit promotion.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import sys
from typing import Any, Mapping

import numpy as np
from scipy.signal import butter, sosfilt
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
BENCHMARKS = ROOT / "benchmarks"
for value in (APP, BENCHMARKS):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

import build_original_sin_overlap_character_coverage_round_v3 as coverage_v3  # noqa: E402
from build_original_sin_noncore_quasi_emotive_round_v1 import (  # noqa: E402
    audio_record,
    current_identity_reference,
    normalized_words,
    read_json,
    sha256_file,
    write_json,
)
from model_registry import model_cache_status  # noqa: E402
from responsive_voice_backend import (  # noqa: E402
    ResponsiveBackendUnavailable,
    ResponsiveVoiceBackend,
    ResponsiveVoiceBackendError,
)
from transcription_evaluator import evaluate_transcriptions  # noqa: E402
from tts import TTSEngine  # noqa: E402


ROUND_ID = "alexandria_original_sin_overlap_character_repairs_round_v4"
DEFAULT_PROJECT = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665"
)
DEFAULT_CONFIG = Path(
    "/Users/tristan/pinokio/api/alexandria-audiobook.git/config.json"
)
DEFAULT_OUTPUT = (
    DEFAULT_PROJECT
    / "external_workflows"
    / "big_finish_overlap_reference_v1"
    / "overlap_character_repairs_round_v4"
)
DEFAULT_PROFILE = DEFAULT_PROJECT / "production_prompt_routes/approved_adaptation/profile.json"
DEFAULT_SALVAGE_ANSWER = (
    DEFAULT_PROJECT
    / "external_workflows/big_finish_overlap_reference_v1/overlap_identity_salvage_round_v6/private/answer-key.json"
)
DEFAULT_V3_ANSWER = (
    DEFAULT_PROJECT
    / "external_workflows/big_finish_overlap_reference_v1/overlap_character_coverage_round_v3/private/answer-key.json"
)
INDEXTTS2_ROOT = Path("/Users/tristan/pinokio/cache/alexandria-evaluation/indextts2")
PRIMARY_SEED = 130363
RETRY_SEED = 130464
MAX_ACCEPTABLE_WER = 0.25

QWEN = coverage_v3.QWEN
VOX = coverage_v3.VOX
FISH = coverage_v3.FISH
INDEX = coverage_v3.INDEX


MODE_SPECS = (
    {
        "mode_id": "doctor_urgent_discovery_repair",
        "character": "The Doctor",
        "book_speaker": "DOCTOR",
        "voice_key": "THE DOCTOR",
        "target_chunk_ids": [4602],
        "title": "The Doctor — urgent discovery repair",
        "review_instruction": "Sudden delighted urgency with the exact Doctor identity; do not reward generic shouting.",
        "qwen_reference_routes": [
            "doctor_indomitable_determination",
            "approved_adaptation_17769426b8ffb17a",
        ],
        "specialists": [
            {"backend": FISH, "performance_route": "ordinary_identity"},
            {"backend": INDEX, "performance_route": "doctor_indomitable_determination"},
            {"backend": INDEX, "performance_route": "approved_adaptation_17769426b8ffb17a"},
        ],
    },
    {
        "mode_id": "doctor_weary_moral_gravity_repair",
        "character": "The Doctor",
        "book_speaker": "DOCTOR",
        "voice_key": "THE DOCTOR",
        "target_chunk_ids": [4445],
        "title": "The Doctor — weary moral gravity repair",
        "review_instruction": "Ancient moral gravity and rhetorical shape with natural weariness, not a soulless recital.",
        "qwen_reference_routes": [
            "ordinary_identity",
            "approved_adaptation_3e626dedeb7b88b0",
        ],
        "specialists": [
            {"backend": FISH, "performance_route": "ordinary_identity"},
            {"backend": INDEX, "performance_route": "doctor_acf_fond_reminiscence"},
            {"backend": INDEX, "performance_route": "approved_adaptation_3e626dedeb7b88b0"},
        ],
    },
    {
        "mode_id": "roz_dry_banter_repair",
        "character": "Roz Forrester",
        "book_speaker": "ROZ FORRESTER",
        "voice_key": "ROZ FORRESTER",
        "target_chunk_ids": [478],
        "target_instruct": "Dry streetwise teasing with clipped timing and guarded amusement.",
        "title": "Roz — dry banter repair",
        "review_instruction": "Dry authority and amusement without losing Roz's edge or becoming broadly warm.",
        "qwen_identity": True,
        "specialists": [
            {"backend": VOX, "performance_route": "identity"},
            {"backend": FISH, "performance_route": "identity"},
            {"backend": INDEX, "performance_route": "identity"},
        ],
    },
    {
        "mode_id": "computer_processing_repair",
        "character": "Computer",
        "book_speaker": "COMPUTER",
        "voice_key": "COMPUTER",
        "target_text": "Search complete. The requested information is classified.",
        "target_instruct": "Flat synthetic delivery with exact diction and neutral pacing.",
        "title": "Computer — processing repair",
        "review_instruction": "The underlying voice already matched. Judge whether the new processing now matches the adaptation Computer without harming words.",
        "postprocess_sources": ["da6c367d964ea6c9", "56da202533b9f6d6"],
        "effect_variants": [
            "computer_terminal_v3",
            "computer_dual_tone_v3",
            "computer_clean_vocoder_v3",
        ],
    },
    {
        "mode_id": "dantalion_dry_sardonic",
        "character": "Doc Dantalion",
        "book_speaker": "DOC DANTALION",
        "voice_key": "DOC DANTALION",
        "target_chunk_ids": [2623],
        "title": "Doc Dantalion — dry sardonic explanation",
        "review_instruction": "Dry intelligence, weary amusement, and exact salvaged identity without echo.",
        "salvaged_identity": True,
        "models": [QWEN, VOX, FISH, INDEX],
    },
    {
        "mode_id": "dantalion_sharp_irritation",
        "character": "Doc Dantalion",
        "book_speaker": "DOC DANTALION",
        "voice_key": "DOC DANTALION",
        "target_chunk_ids": [3500],
        "title": "Doc Dantalion — sharp sardonic irritation",
        "review_instruction": "Sudden force and weary irritation while retaining the same dry, intelligent identity.",
        "salvaged_identity": True,
        "models": [QWEN, VOX, FISH, INDEX],
    },
)

FISH_TAGS = {
    "doctor_urgent_discovery_repair": "sudden delighted urgency with sharp recognition and eccentric Doctor energy",
    "doctor_weary_moral_gravity_repair": "ancient weary moral gravity with controlled rhetorical emphasis",
    "roz_dry_banter_repair": "dry streetwise teasing with clipped guarded amusement",
    "dantalion_dry_sardonic": "dry sardonic intelligence with weary amusement",
    "dantalion_sharp_irritation": "sharp sardonic irritation with controlled force",
}

INDEX_STRENGTH = {
    "doctor_urgent_discovery_repair": 0.90,
    "doctor_weary_moral_gravity_repair": 0.72,
    "roz_dry_banter_repair": 0.65,
    "dantalion_dry_sardonic": 0.70,
    "dantalion_sharp_irritation": 0.90,
}


class RepairRoundError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def route_reference(project: Path, voice: Mapping[str, Any], route_key: str) -> dict[str, Any]:
    if route_key == "identity":
        return current_identity_reference(project, voice)
    routes = ((voice.get("experimental_prompt_routing") or {}).get("routes") or {})
    route = routes.get(route_key)
    if not isinstance(route, Mapping):
        raise RepairRoundError(f"Voice route is missing: {route_key}")
    source = (project / str(route.get("ref_audio") or "")).resolve()
    expected = str(route.get("ref_audio_sha256") or "")
    if not source.is_file() or sha256_file(source) != expected:
        raise RepairRoundError(f"Voice route changed: {route_key}")
    return {
        "audio_path": source,
        "audio_sha256": expected,
        "reference_text": str(route.get("ref_text") or "").strip(),
        "candidate_id": route_key,
        "chunk_id": None,
    }


def salvage_reference(answer: Mapping[str, Any]) -> dict[str, Any]:
    row = (answer.get("candidates") or {}).get("89773ee3454a2cbf")
    if not isinstance(row, Mapping):
        raise RepairRoundError("Approved Doc Dantalion salvage candidate is missing.")
    source = Path(str(row["audio_path"])).resolve()
    expected = str(row["audio"]["sha256"])
    if not source.is_file() or sha256_file(source) != expected:
        raise RepairRoundError("Doc Dantalion salvage audio changed.")
    return {
        "audio_path": source,
        "audio_sha256": expected,
        "reference_text": str(row["transcript"]),
        "candidate_id": "89773ee3454a2cbf",
        "chunk_id": None,
    }


def _bandpass(audio: np.ndarray, rate: int, low: float, high: float) -> np.ndarray:
    nyquist = rate / 2.0
    sos = butter(3, [low / nyquist, min(high / nyquist, 0.95)], btype="bandpass", output="sos")
    return np.asarray(sosfilt(sos, audio), dtype=np.float32)


def _delay(audio: np.ndarray, rate: int, milliseconds: float, mix: float) -> np.ndarray:
    samples = max(1, int(round(rate * milliseconds / 1000.0)))
    shifted = np.zeros_like(audio)
    shifted[samples:] = audio[:-samples]
    return np.asarray((1.0 - mix) * audio + mix * shifted, dtype=np.float32)


def computer_effect(source: Path, destination: Path, variant: str) -> dict[str, Any]:
    audio, rate = sf.read(str(source), dtype="float32", always_2d=True)
    mono = np.mean(audio, axis=1, dtype=np.float32)
    t = np.arange(mono.size, dtype=np.float32) / float(rate)
    if variant == "computer_terminal_v3":
        output = _bandpass(mono, rate, 280.0, 4300.0)
        output = _delay(output, rate, 6.0, 0.24)
        output *= 1.0 + 0.28 * np.sin(2.0 * np.pi * 41.0 * t)
        output = np.tanh(output * 1.24) / np.tanh(1.24)
    elif variant == "computer_dual_tone_v3":
        output = _bandpass(mono, rate, 300.0, 4600.0)
        output = _delay(output, rate, 9.0, 0.18)
        output *= 1.0 + 0.20 * np.sin(2.0 * np.pi * 29.0 * t)
        output *= 1.0 + 0.09 * np.sin(2.0 * np.pi * 73.0 * t)
    elif variant == "computer_clean_vocoder_v3":
        output = _bandpass(mono, rate, 230.0, 5000.0)
        output = _delay(output, rate, 4.0, 0.14)
        output *= 1.0 + 0.22 * np.sin(2.0 * np.pi * 34.0 * t)
        output = np.tanh(output * 1.12) / np.tanh(1.12)
    else:
        raise RepairRoundError(f"Unknown Computer effect: {variant}")
    peak = float(np.max(np.abs(output))) if output.size else 0.0
    target = 10.0 ** (-1.0 / 20.0)
    if peak > target:
        output *= target / peak
    destination.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(destination), output, int(rate), subtype="PCM_16")
    return {
        "variant": variant,
        "source_sha256": sha256_file(source),
        "output_sha256": sha256_file(destination),
    }


def candidate_id(*values: str) -> str:
    return hashlib.sha256(":".join((ROUND_ID, *values)).encode("utf-8")).hexdigest()[:16]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--config-path", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    project = args.project_root.expanduser().resolve()
    output = args.output_root.expanduser().resolve()
    if output.exists():
        if not args.replace:
            raise RepairRoundError(f"Output exists; pass --replace: {output}")
        shutil.rmtree(output)
    output.mkdir(parents=True)
    os.environ.setdefault("ALEXANDRIA_INDEXTTS2_ROOT", str(INDEXTTS2_ROOT))
    chunks = read_json(project / "chunks.json", "Project chunks")
    voices = read_json(project / "voice_config.json", "Voice configuration")
    config = read_json(args.config_path, "Alexandria configuration") if args.config_path.is_file() else {}
    transcript = read_json(project / "external_workflows/big_finish_overlap_reference_v1/private/transcript.json", "Adaptation transcript")
    salvage = read_json(DEFAULT_SALVAGE_ANSWER, "Salvage answer")
    v3_answer = read_json(DEFAULT_V3_ANSWER, "V3 answer")
    transcript_text = " ".join(normalized_words(str(transcript.get("text") or "")))
    coverage_v3.ROUND_ID = ROUND_ID
    coverage_v3.FISH_TAGS.update(FISH_TAGS)
    coverage_v3.INDEX_STRENGTH.update(INDEX_STRENGTH)

    modes: list[dict[str, Any]] = []
    generation_specs: list[dict[str, Any]] = []
    postprocess_specs: list[dict[str, Any]] = []
    doc_identity = salvage_reference(salvage)
    for raw in MODE_SPECS:
        mode = copy.deepcopy(raw)
        if mode["mode_id"] == "computer_processing_repair":
            target = {
                "text": mode["target_text"],
                "instruct": mode["target_instruct"],
                "chunk_ids": [],
            }
            voice = voices["COMPUTER"]
            identity = current_identity_reference(project, voice)
            performance = coverage_v3.profile_reference(project, read_json(DEFAULT_PROFILE, "Profile"), "d3a4830b0a3bea41")
            mode["target_text"] = target["text"]
            mode["target_instruct"] = target["instruct"]
            mode["public_references"] = coverage_v3.public_references(output=output, mode=mode, identity=identity, performance=performance)
            modes.append(mode)
            for source_id in mode["postprocess_sources"]:
                source_row = v3_answer["candidates"][source_id]
                raw_source = Path(source_row["audio_path"]).parent.parent / "raw" / f"{source_id}.wav"
                if not raw_source.is_file():
                    raise RepairRoundError(f"Computer raw source missing: {raw_source}")
                for variant in mode["effect_variants"]:
                    postprocess_specs.append({"mode": mode, "source_id": source_id, "source_row": source_row, "source": raw_source, "variant": variant})
            continue

        target = coverage_v3.target_record(mode, chunks)
        normalized_target = " ".join(normalized_words(target["text"]))
        if normalized_target in transcript_text:
            raise RepairRoundError(f"Target is present in adaptation: {mode['mode_id']}")
        voice = voices.get(mode["voice_key"], {})
        identity = doc_identity if mode.get("salvaged_identity") else current_identity_reference(project, voice)
        references = [identity]
        if mode.get("qwen_reference_routes"):
            references.extend(route_reference(project, voice, key) for key in mode["qwen_reference_routes"])
        mode["target_text"] = target["text"]
        mode["target_instruct"] = target["instruct"]
        # Show all distinct approved references used anywhere in this mode.
        public = []
        seen = set()
        for index, ref in enumerate(references):
            if ref["audio_sha256"] in seen:
                continue
            seen.add(ref["audio_sha256"])
            relative = Path("references") / mode["mode_id"] / f"reference_{index}{Path(ref['audio_path']).suffix}"
            coverage_v3.safe_copy(Path(ref["audio_path"]), output / relative, ref["audio_sha256"])
            public.append({"kind": "identity_or_delivery", "label": "Approved character reference", "audio": "../" + relative.as_posix(), "transcript": ref["reference_text"], "audio_sha256": ref["audio_sha256"]})
        mode["public_references"] = public
        modes.append(mode)
        if mode.get("qwen_identity"):
            generation_specs.append({"kind": "qwen", "mode": mode, "target": target, "voice": voice, "identity": identity, "performance": identity})
        for route_key in mode.get("qwen_reference_routes", []):
            ref = route_reference(project, voice, route_key)
            generation_specs.append({"kind": "qwen", "mode": mode, "target": target, "voice": voice, "identity": identity, "performance": ref})
        for backend_name in mode.get("models", []):
            generation_specs.append({"kind": "qwen" if backend_name == QWEN else "specialist", "backend": backend_name, "mode": mode, "target": target, "voice": voice, "identity": identity, "performance": identity})
        for specialist in mode.get("specialists", []):
            route_key = specialist["performance_route"]
            performance = identity if route_key == "identity" else route_reference(project, voice, route_key)
            generation_specs.append({"kind": "specialist", "backend": specialist["backend"], "mode": mode, "target": target, "voice": voice, "identity": identity, "performance": performance})

    attempts: list[dict[str, Any]] = []
    omissions: list[dict[str, Any]] = []
    engine = TTSEngine(config)
    responsive = ResponsiveVoiceBackend()
    try:
        for spec in generation_specs:
            try:
                if spec["kind"] == "qwen":
                    row = coverage_v3.qwen_candidate(engine=engine, output=output, mode=spec["mode"], target=spec["target"], voice=spec["voice"], reference=spec["performance"], seed=PRIMARY_SEED)
                else:
                    row = coverage_v3.specialist_candidate(backend=responsive, output=output, mode=spec["mode"], target=spec["target"], identity=spec["identity"], performance=spec["performance"], backend_name=spec["backend"], seed=PRIMARY_SEED)
                row["reference_variant_sha256"] = spec["performance"]["audio_sha256"]
                attempts.append(row)
            except Exception as first:
                try:
                    if spec["kind"] == "qwen":
                        row = coverage_v3.qwen_candidate(engine=engine, output=output, mode=spec["mode"], target=spec["target"], voice=spec["voice"], reference=spec["performance"], seed=RETRY_SEED)
                    else:
                        row = coverage_v3.specialist_candidate(backend=responsive, output=output, mode=spec["mode"], target=spec["target"], identity=spec["identity"], performance=spec["performance"], backend_name=spec["backend"], seed=RETRY_SEED)
                    row["generation_retry_of"] = str(first)
                    row["reference_variant_sha256"] = spec["performance"]["audio_sha256"]
                    attempts.append(row)
                except Exception as retry:
                    omissions.append({"mode_id": spec["mode"]["mode_id"], "backend": spec.get("backend", QWEN), "reason": "generation_failed_after_retry", "primary_error": str(first), "retry_error": str(retry)})
    finally:
        responsive.close()

    for spec in postprocess_specs:
        identifier = candidate_id(spec["mode"]["mode_id"], spec["source_id"], spec["variant"])
        final = output / "private/audio" / f"{identifier}.wav"
        processing = computer_effect(spec["source"], final, spec["variant"])
        attempts.append({"candidate_id": identifier, "mode_id": spec["mode"]["mode_id"], "backend": f"postprocess_{spec['source_row']['backend']}", "backend_source": "v3_raw_candidate_postprocess_repair", "source_candidate_id": spec["source_id"], "seed": spec["source_row"].get("seed"), "text": spec["mode"]["target_text"], "instruct": spec["mode"]["target_instruct"], "reference_audio_sha256": spec["source_row"].get("reference_audio_sha256"), "audio_path": str(final), "audio_relative": final.relative_to(output).as_posix(), "audio": audio_record(final), "effect_processing": processing, "generation_receipt": None})

    evaluation = coverage_v3.attach_transcriptions(attempts)
    accepted = [row for row in attempts if coverage_v3.transcription_passed(row)]
    for row in attempts:
        if row not in accepted:
            omissions.append({"mode_id": row["mode_id"], "backend": row["backend"], "reason": "final_transcription_gate_failed", "candidate_id": row["candidate_id"], "transcription": row.get("transcription")})
    answer = {"schema_version": 1, "round_id": ROUND_ID, "generated_at_utc": utc_now(), "mode_count": len(modes), "planned_candidate_count": len(generation_specs) + len(postprocess_specs), "candidate_count": len(accepted), "objective_omission_count": len(omissions), "modes": modes, "candidates": {row["candidate_id"]: row for row in accepted}, "omissions": omissions, "transcription_evaluation": evaluation, "review_contract": {"model_and_reference_identity_hidden": True, "approved_reference_audio_visible": True, "entire_line_required": True, "all_five_scores_required": True, "written_notes_override_pass": True}, "production_routing_changed": False, "project_audio_changed": False, "voice_config_changed": False}
    write_json(output / "private/answer-key.json", answer)
    write_json(output / "generation-summary.json", {"schema_version": 1, "round_id": ROUND_ID, "mode_count": len(modes), "planned_candidate_count": answer["planned_candidate_count"], "candidate_count": len(accepted), "objective_omission_count": len(omissions), "production_routing_changed": False, "project_audio_changed": False, "voice_config_changed": False})
    coverage_v3.build_review(output=output, modes=modes, candidates=accepted, omissions=omissions)
    print(json.dumps({"round_id": ROUND_ID, "review": str(output / 'review/index.html'), "mode_count": len(modes), "candidate_count": len(accepted), "objective_omission_count": len(omissions)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
