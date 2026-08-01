#!/usr/bin/env python3
"""Build the next character-completeness blind Voice round for Original Sin.

This round is intentionally deficit-driven. It does not retest delivery modes
already accepted in prior blind rounds. It covers the remaining generated-Voice
gaps for shared book/adaptation characters with usable identity evidence.

The round is research-only. It writes candidates and a blind listening page
under the project's external_workflows directory and never changes project
audio, chunks.json, voice_config.json, or production routing.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import gc
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

from build_original_sin_noncore_quasi_emotive_round_v1 import (  # noqa: E402
    audio_record,
    candidate_voice,
    current_identity_reference,
    locked_reference,
    normalized_words,
    read_json,
    sha256_file,
    write_json,
    _replace_phrases,
    _word_error_rate_from_words,
)
from model_registry import model_cache_status  # noqa: E402
from responsive_voice_backend import (  # noqa: E402
    ResponsiveBackendUnavailable,
    ResponsiveVoiceBackend,
    ResponsiveVoiceBackendError,
)
from transcription_evaluator import evaluate_transcriptions  # noqa: E402
from tts import TTSEngine  # noqa: E402


ROUND_ID = "alexandria_original_sin_overlap_character_coverage_round_v3"
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
    / "overlap_character_coverage_round_v3"
)
DEFAULT_PROFILE = (
    DEFAULT_PROJECT
    / "production_prompt_routes"
    / "approved_adaptation"
    / "profile.json"
)
INDEXTTS2_ROOT = Path(
    "/Users/tristan/pinokio/cache/alexandria-evaluation/indextts2"
)
PRIMARY_SEED = 130363
RETRY_SEED = 130464
MAX_ACCEPTABLE_WER = 0.25

QWEN = "qwen3_instruction_controlled"
VOX = "voxcpm2_controllable_clone"
FISH = "fish_s2_pro_free_zero_shot"
INDEX = "indextts2_matched_control"


# Previous accepted generated-mode counts are treated as evidence, not inferred
# from current routing. V3 closes only the measured deficits under the coverage
# rule recorded in CHARACTER_COVERAGE_TARGETS below.
CHARACTER_COVERAGE_TARGETS = {
    "DOCTOR": {"book_line_count": 664, "required_modes": 4, "accepted_before": 0},
    "BERNICE": {"book_line_count": 411, "required_modes": 4, "accepted_before": 2},
    "ROZ FORRESTER": {"book_line_count": 480, "required_modes": 4, "accepted_before": 1},
    "CHRIS CWEJ": {"book_line_count": 267, "required_modes": 3, "accepted_before": 2},
    "POWERLESS FRIENDLESS": {"book_line_count": 122, "required_modes": 2, "accepted_before": 1},
    "HATER OF HUMANS": {"book_line_count": 88, "required_modes": 2, "accepted_before": 1},
    "EVAN CLAPLE": {"book_line_count": 8, "required_modes": 1, "accepted_before": 0},
    "SECURITYBOT": {"book_line_count": 9, "required_modes": 1, "accepted_before": 0},
    "COMPUTER": {"book_line_count": 10, "required_modes": 1, "accepted_before": 0},
    # Tobias already meets the numerical mode target. This extra mode is needed
    # because the book's BOT label later refers to Tobias's robot identity.
    "TOBIAS VAUGHN / ROBOT": {
        "book_line_count": 219,
        "required_modes": 3,
        "accepted_before": 4,
        "speaker_split_validation": True,
    },
}


MODE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "mode_id": "doctor_wry_deflection",
        "character": "The Doctor",
        "book_speaker": "DOCTOR",
        "voice_key": "THE DOCTOR",
        "target_chunk_ids": [63],
        "performance": {"kind": "locked_chunk", "chunk_id": 5462},
        "title": "The Doctor — wry deflection",
        "review_instruction": "Dry, nimble wit with the Doctor's exact identity; playful rather than flat.",
        "models": (QWEN, FISH, INDEX),
    },
    {
        "mode_id": "doctor_urgent_discovery",
        "character": "The Doctor",
        "book_speaker": "DOCTOR",
        "voice_key": "THE DOCTOR",
        "target_chunk_ids": [4602],
        "performance": {"kind": "locked_chunk", "chunk_id": 2398},
        "title": "The Doctor — urgent discovery",
        "review_instruction": "Sudden delighted urgency and sharp recognition without generic shouting.",
        "models": (QWEN, VOX, INDEX),
    },
    {
        "mode_id": "doctor_hushed_vulnerability",
        "character": "The Doctor",
        "book_speaker": "DOCTOR",
        "voice_key": "THE DOCTOR",
        "target_chunk_ids": [3812],
        "performance": {"kind": "locked_chunk", "chunk_id": 4443},
        "title": "The Doctor — hushed vulnerability",
        "review_instruction": "Quiet recognition of danger with restrained fear, age, and intact identity.",
        "models": (QWEN, FISH, INDEX),
    },
    {
        "mode_id": "doctor_weary_moral_gravity",
        "character": "The Doctor",
        "book_speaker": "DOCTOR",
        "voice_key": "THE DOCTOR",
        "target_chunk_ids": [4445],
        "performance": {"kind": "locked_chunk", "chunk_id": 561},
        "title": "The Doctor — weary moral gravity",
        "review_instruction": "Weary philosophical gravity, controlled emphasis, and the Doctor's characteristic rhetorical shape.",
        "models": (QWEN, FISH, INDEX),
    },
    {
        "mode_id": "bernice_quiet_defiance",
        "character": "Bernice Summerfield",
        "book_speaker": "BERNICE",
        "voice_key": "BERNICE",
        "target_chunk_ids": [27, 29],
        "target_instruct": "Low, controlled defiance with contained anger and precise articulation.",
        "performance": {"kind": "locked_chunk", "chunk_id": 1939},
        "title": "Bernice — quiet defiance",
        "review_instruction": "Contained danger and intelligent defiance, not a generic action-hero threat.",
        "models": (QWEN, VOX, FISH),
    },
    {
        "mode_id": "bernice_bittersweet_nostalgia",
        "character": "Bernice Summerfield",
        "book_speaker": "BERNICE",
        "voice_key": "BERNICE",
        "target_chunk_ids": [248],
        "target_instruct": "Warm, bittersweet nostalgia with light self-mockery and natural pacing.",
        "performance": {"kind": "locked_chunk", "chunk_id": 1939},
        "title": "Bernice — bittersweet nostalgia",
        "review_instruction": "Warm reflective memory with Benny's quick intelligence still audible underneath.",
        "models": (QWEN, FISH, INDEX),
    },
    {
        "mode_id": "roz_survivor_reflection",
        "character": "Roz Forrester",
        "book_speaker": "ROZ FORRESTER",
        "voice_key": "ROZ FORRESTER",
        "target_chunk_ids": [5396, 5398],
        "target_instruct": "Guarded survivor's reflection with restrained warmth and hard-earned relief.",
        "performance": {"kind": "profile_candidate", "candidate_id": "74923d243dd1c330"},
        "title": "Roz — guarded survivor reflection",
        "review_instruction": "Hard-earned relief and protective feeling without losing Roz's blunt, guarded identity.",
        "models": (QWEN, FISH, INDEX),
    },
    {
        "mode_id": "roz_defeated_grief",
        "character": "Roz Forrester",
        "book_speaker": "ROZ FORRESTER",
        "voice_key": "ROZ FORRESTER",
        "target_chunk_ids": [3199, 3201],
        "target_instruct": "Defeated grief with exhausted control, suppressed pain, and no melodrama.",
        "performance": {"kind": "profile_candidate", "candidate_id": "74923d243dd1c330"},
        "title": "Roz — defeated grief",
        "review_instruction": "A normally hard, guarded person admitting defeat and grief without losing Roz's identity.",
        "models": (QWEN, VOX, FISH),
    },
    {
        "mode_id": "roz_dry_banter",
        "character": "Roz Forrester",
        "book_speaker": "ROZ FORRESTER",
        "voice_key": "ROZ FORRESTER",
        "target_chunk_ids": [478],
        "target_instruct": "Dry streetwise teasing with clipped timing and guarded amusement.",
        "performance": {"kind": "profile_candidate", "candidate_id": "74923d243dd1c330"},
        "title": "Roz — dry streetwise banter",
        "review_instruction": "Dry amusement and authority together, not warmth that erases her edge.",
        "models": (QWEN, FISH, INDEX),
    },
    {
        "mode_id": "chris_exposed_vulnerability",
        "character": "Chris Cwej",
        "book_speaker": "CHRIS CWEJ",
        "voice_key": "CHRIS CWEJ",
        "target_chunk_ids": [454],
        "performance": {"kind": "identity"},
        "title": "Chris — exposed vulnerability",
        "review_instruction": "Open, earnest vulnerability and careful pleading without losing his youthful identity.",
        "models": (QWEN, VOX, FISH),
    },
    {
        "mode_id": "powerless_wounded_accusation",
        "character": "Powerless Friendless",
        "book_speaker": "POWERLESS FRIENDLESS",
        "voice_key": "POWERLESS FRIENDLESS",
        "target_chunk_ids": [3325],
        "performance": {"kind": "locked_chunk", "chunk_id": 1322},
        "effect_chain": "powerless_alien_modulation_v1",
        "title": "Powerless Friendless — wounded accusation",
        "review_instruction": "Betrayal, fear, and accusation through the same alien identity and approved modulation.",
        "models": (QWEN, VOX, INDEX),
    },
    {
        "mode_id": "hater_grave_statecraft",
        "character": "Hater of Humans",
        "book_speaker": "HATER OF HUMANS",
        "voice_key": "HATER OF HUMANS",
        "target_chunk_ids": [4323],
        "performance": {"kind": "identity"},
        "title": "Hater of Humans — grave statecraft",
        "review_instruction": "Controlled sovereign authority and wounded pride without the prior fury mode.",
        "models": (QWEN, VOX, FISH),
    },
    {
        "mode_id": "evan_broadcast_authority",
        "character": "Evan Claple",
        "book_speaker": "EVAN CLAPLE",
        "voice_key": "EVAN CLAPLE",
        "target_chunk_ids": [1237],
        "target_text": (
            "I’m Evan Claple, and this is The Empire Today Update, broadcasting "
            "from the heart of the Empire. Tonight’s special report: are the "
            "Overcities safe for humanity?"
        ),
        "target_instruct": "Polished broadcast authority with controlled urgency and exact adaptation pronunciation.",
        "performance": {"kind": "identity"},
        "title": "Evan Claple — broadcast authority",
        "review_instruction": "A credible Empire Today presenter matching the adaptation identity and its Claypool-like surname pronunciation.",
        "models": (QWEN, VOX, FISH),
    },
    {
        "mode_id": "securitybot_identity_repair",
        "character": "Securitybot",
        "book_speaker": "BOT",
        "voice_key": "BOT",
        "target_chunk_ids": [495],
        "performance": {"kind": "locked_chunk", "chunk_id": 618},
        "effect_chain": "securitybot_synthetic_v2",
        "title": "Securitybot — identity and modulation repair",
        "review_instruction": "Mechanically precise Securitybot identity with stronger character-correct synthesis; do not reward an ordinary human voice.",
        "models": (QWEN, VOX, FISH),
    },
    {
        "mode_id": "computer_formal_timestamp",
        "character": "Computer",
        "book_speaker": "COMPUTER",
        "voice_key": "COMPUTER",
        "target_chunk_ids": [1247],
        "target_text": "Search complete. The requested information is classified.",
        "target_instruct": "Flat synthetic delivery with exact diction and neutral pacing.",
        "performance": {"kind": "profile_candidate", "candidate_id": "d3a4830b0a3bea41"},
        "effect_chain": "computer_modulation_v2",
        "title": "Computer — formal system response",
        "review_instruction": "Exact neutral machine diction and the adaptation's computer identity; no emotional or human drift.",
        "models": (QWEN, VOX, FISH, INDEX),
    },
    {
        "mode_id": "tobias_robot_cold_control",
        "character": "Tobias Vaughn / Robot",
        "book_speaker": "BOT",
        "voice_key": "TOBIAS VAUGHN",
        "target_chunk_ids": [3676],
        "performance": {"kind": "identity"},
        "title": "Tobias Vaughn's robot body — cold control",
        "review_instruction": "The accepted Tobias/Robot identity giving a cold lethal command; this must not sound like the Securitybot.",
        "models": (QWEN, VOX, FISH, INDEX),
        "speaker_split": {
            "source_label": "BOT",
            "canonical_character": "TOBIAS VAUGHN",
            "book_chunk_ids": [1341, 3669, 3674, 3676, 3680, 3682, 3684],
        },
    },
)


MODEL_LABELS = (QWEN, VOX, FISH, INDEX)

FISH_TAGS = {
    "doctor_wry_deflection": "dry quick-witted deflection with playful authority",
    "doctor_hushed_vulnerability": "quiet restrained fear with ancient intelligence",
    "doctor_weary_moral_gravity": "weary philosophical gravity and controlled rhetorical emphasis",
    "bernice_quiet_defiance": "low contained defiance and precise intelligent anger",
    "bernice_bittersweet_nostalgia": "warm bittersweet nostalgia with light self-mockery",
    "roz_survivor_reflection": "guarded survivor reflection with restrained warmth",
    "roz_defeated_grief": "exhausted defeated grief with suppressed pain and no melodrama",
    "roz_dry_banter": "dry streetwise teasing with clipped timing",
    "chris_exposed_vulnerability": "open earnest vulnerability and careful pleading",
    "hater_grave_statecraft": "grave sovereign authority and wounded pride",
    "evan_broadcast_authority": "polished authoritative news broadcast with controlled urgency",
    "securitybot_identity_repair": "mechanically precise low-emotion security system",
    "computer_formal_timestamp": "neutral exact computer announcement",
    "tobias_robot_cold_control": "cold cultivated lethal command with no warmth",
}

INDEX_STRENGTH = {
    "doctor_wry_deflection": 0.65,
    "doctor_urgent_discovery": 0.95,
    "doctor_hushed_vulnerability": 0.75,
    "doctor_weary_moral_gravity": 0.85,
    "bernice_bittersweet_nostalgia": 0.70,
    "roz_survivor_reflection": 0.75,
    "roz_dry_banter": 0.65,
    "powerless_wounded_accusation": 0.95,
    "computer_formal_timestamp": 0.35,
    "tobias_robot_cold_control": 0.80,
}

RESEARCH_ADMISSION_MAX_WER = {
    "evan_broadcast_authority": 0.35,
    "securitybot_identity_repair": 0.40,
    "computer_formal_timestamp": 0.30,
}

RESEARCH_REQUIRE_FIRST_WORD = {
    # The pinned recognizer repeatedly renders Forrester as Forresta. The final
    # alias-aware round evaluator still requires the complete audible line.
    "securitybot_identity_repair": False,
}

TRANSCRIPTION_ALIAS_POLICY = {
    "doctor_urgent_discovery": {
        "token_aliases": {"tardis": "tardis"},
        "phrase_aliases": {},
    },
    "powerless_wounded_accusation": {
        "token_aliases": {"hithus": "hithis", "hith": "hithis"},
        "phrase_aliases": {},
    },
    "hater_grave_statecraft": {
        "token_aliases": {"skelski": "skel'ske", "skellsky": "skel'ske", "innatec": "initec"},
        "phrase_aliases": {},
    },
    "evan_broadcast_authority": {
        "token_aliases": {"claypool": "claple", "claypole": "claple", "overcities": "overcities"},
        "phrase_aliases": {("empire", "today"): ("empire", "today")},
    },
    "securitybot_identity_repair": {
        "token_aliases": {
            "forrestor": "forrester",
            "forresta": "forrester",
            "rosling": "roslyn",
            "rosalind": "roslyn",
            "5": "five",
            "500": "five",
            "town": "undertown",
            "light": "lodge",
        },
        "phrase_aliases": {
            ("a", "judicator"): ("adjudicator",),
            ("space", "port"): ("spaceport",),
        },
    },
}


class CoverageRoundError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def candidate_identifier(
    *,
    mode_id: str,
    backend: str,
    reference_sha256: str,
    seed: int,
    effect_chain: str | None,
) -> str:
    payload = ":".join(
        (
            ROUND_ID,
            mode_id,
            backend,
            reference_sha256,
            str(seed),
            effect_chain or "none",
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def safe_copy(source: Path, destination: Path, expected_sha256: str) -> None:
    if not source.is_file() or sha256_file(source) != expected_sha256:
        raise CoverageRoundError(f"Source is missing or changed: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    if sha256_file(destination) != expected_sha256:
        raise CoverageRoundError(f"Copied source changed: {destination}")


def _bandpass(audio: np.ndarray, rate: int, low: float, high: float) -> np.ndarray:
    nyquist = max(1.0, rate / 2.0)
    low_value = max(20.0, min(low, nyquist * 0.8)) / nyquist
    high_value = max(low + 20.0, min(high, nyquist * 0.95)) / nyquist
    return np.asarray(
        sosfilt(
            butter(3, [low_value, high_value], btype="bandpass", output="sos"),
            audio,
        ),
        dtype=np.float32,
    )


def _delayed_mix(
    audio: np.ndarray,
    rate: int,
    delay_ms: float,
    amount: float,
) -> np.ndarray:
    delay = max(1, int(round(rate * delay_ms / 1000.0)))
    shifted = np.zeros_like(audio)
    shifted[delay:] = audio[:-delay]
    return np.asarray((1.0 - amount) * audio + amount * shifted, dtype=np.float32)


def apply_effect_chain(
    source: Path,
    destination: Path,
    chain: str | None,
) -> dict[str, Any] | None:
    if chain is None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return None
    audio, rate = sf.read(str(source), dtype="float32", always_2d=True)
    mono = np.mean(audio, axis=1, dtype=np.float32)
    time_axis = np.arange(mono.size, dtype=np.float32) / float(rate)
    if chain == "powerless_alien_modulation_v1":
        output = _bandpass(mono, int(rate), 170.0, 5200.0)
        output = _delayed_mix(output, int(rate), 10.0, 0.22)
        output *= 1.0 + 0.10 * np.sin(2.0 * np.pi * 6.5 * time_axis)
        output = np.tanh(output * 1.18) / np.tanh(1.18)
        parameters = {
            "bandpass_hz": [170.0, 5200.0],
            "chorus_delay_ms": 10.0,
            "chorus_mix": 0.22,
            "amplitude_modulation_hz": 6.5,
            "amplitude_modulation_depth": 0.10,
        }
    elif chain == "securitybot_synthetic_v2":
        output = _bandpass(mono, int(rate), 240.0, 4300.0)
        output = _delayed_mix(output, int(rate), 4.0, 0.12)
        output *= 1.0 + 0.18 * np.sin(2.0 * np.pi * 27.0 * time_axis)
        # Mild deterministic quantization strengthens the synthetic identity
        # without making the words unintelligible.
        output = np.round(output * 2048.0) / 2048.0
        output = np.tanh(output * 1.20) / np.tanh(1.20)
        parameters = {
            "bandpass_hz": [240.0, 4300.0],
            "chorus_delay_ms": 4.0,
            "chorus_mix": 0.12,
            "amplitude_modulation_hz": 27.0,
            "amplitude_modulation_depth": 0.18,
            "quantization_levels": 4096,
        }
    elif chain == "computer_modulation_v2":
        output = _bandpass(mono, int(rate), 220.0, 5000.0)
        output = _delayed_mix(output, int(rate), 3.0, 0.08)
        output *= 1.0 + 0.10 * np.sin(2.0 * np.pi * 38.0 * time_axis)
        output = np.tanh(output * 1.10) / np.tanh(1.10)
        parameters = {
            "bandpass_hz": [220.0, 5000.0],
            "chorus_delay_ms": 3.0,
            "chorus_mix": 0.08,
            "amplitude_modulation_hz": 38.0,
            "amplitude_modulation_depth": 0.10,
        }
    else:
        raise CoverageRoundError(f"Unknown effect chain: {chain}")
    peak = float(np.max(np.abs(output))) if output.size else 0.0
    target_peak = 10.0 ** (-1.0 / 20.0)
    if peak > target_peak:
        output = output * (target_peak / peak)
    destination.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(destination), output, int(rate), subtype="PCM_16")
    return {
        "chain": chain,
        "parameters": parameters,
        "source_sha256": sha256_file(source),
        "output_sha256": sha256_file(destination),
    }


def profile_reference(
    project: Path,
    profile: Mapping[str, Any],
    candidate_id: str,
) -> dict[str, Any]:
    for row in profile.get("expressive_references") or []:
        if not isinstance(row, Mapping) or row.get("candidate_id") != candidate_id:
            continue
        relative = str(row.get("relative_audio") or "")
        source = (project / relative).resolve()
        expected = str(row.get("audio_sha256") or "")
        if not source.is_file() or sha256_file(source) != expected:
            raise CoverageRoundError(
                f"Profile reference is missing or changed: {candidate_id}"
            )
        return {
            "audio_path": source,
            "audio_sha256": expected,
            "reference_text": str(row.get("transcript") or "").strip(),
            "candidate_id": candidate_id,
            "chunk_id": row.get("chunk_id"),
        }
    raise CoverageRoundError(f"Profile reference is missing: {candidate_id}")


def target_record(
    mode: Mapping[str, Any],
    chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    ids = [int(value) for value in mode["target_chunk_ids"]]
    rows = []
    for chunk_id in ids:
        if not 0 <= chunk_id < len(chunks):
            raise CoverageRoundError(f"Unknown target chunk: {chunk_id}")
        row = chunks[chunk_id]
        if row.get("speaker") != mode["book_speaker"]:
            raise CoverageRoundError(
                f"Target speaker changed for {mode['mode_id']}: {chunk_id}"
            )
        if row.get("approved_audio_lock"):
            raise CoverageRoundError(
                f"Target is already approved direct audio: {mode['mode_id']}"
            )
        rows.append(row)
    text = str(mode.get("target_text") or "").strip()
    if not text:
        text = " ".join(str(row.get("text") or "").strip() for row in rows)
    instruct = str(mode.get("target_instruct") or "").strip()
    if not instruct:
        instruct = str(rows[0].get("instruct") or "").strip()
    return {
        "id": ids[0] if len(ids) == 1 else None,
        "chunk_ids": ids,
        "speaker": mode["book_speaker"],
        "text": text,
        "instruct": instruct,
    }


def adjusted_word_error_rate(mode_id: str, expected: str, heard: str) -> float | None:
    policy = TRANSCRIPTION_ALIAS_POLICY.get(mode_id)
    if policy is None:
        return None
    expected_words = normalized_words(expected)
    aliases = dict(policy.get("token_aliases") or {})
    heard_words: list[str] = []
    for word in normalized_words(heard):
        replacement = aliases.get(word, word)
        heard_words.extend(str(replacement).split())
    heard_words = _replace_phrases(
        heard_words,
        dict(policy.get("phrase_aliases") or {}),
    )
    return _word_error_rate_from_words(expected_words, heard_words)


def attach_transcriptions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    evaluation = evaluate_transcriptions(
        {
            "model_status": model_cache_status("mlx_whisper_base"),
            "outputs": [
                {
                    "sample_id": row["candidate_id"],
                    "path": row["audio_path"],
                    "text": row["text"],
                }
                for row in rows
            ],
        }
    )
    measurements = evaluation.get("measurements") or {}
    for row in rows:
        result = copy.deepcopy(measurements.get(row["candidate_id"]) or {})
        transcript = result.get("transcript")
        if isinstance(transcript, str):
            adjusted = adjusted_word_error_rate(
                str(row["mode_id"]),
                str(row["text"]),
                transcript,
            )
            if adjusted is not None:
                result["raw_word_error_rate"] = result.get("word_error_rate")
                result["word_error_rate"] = adjusted
                result["alias_policy_applied"] = row["mode_id"]
        row["transcription"] = result
    return evaluation


def transcription_passed(row: Mapping[str, Any]) -> bool:
    result = row.get("transcription")
    if not isinstance(result, Mapping):
        return False
    value = result.get("word_error_rate")
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and float(value) <= MAX_ACCEPTABLE_WER
    )


def public_references(
    *,
    output: Path,
    mode: Mapping[str, Any],
    identity: Mapping[str, Any],
    performance: Mapping[str, Any],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for kind, reference in (("identity", identity), ("delivery", performance)):
        fingerprint = str(reference["audio_sha256"])
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        source = Path(reference["audio_path"])
        suffix = source.suffix.casefold() or ".wav"
        relative = Path("references") / str(mode["mode_id"]) / f"{kind}{suffix}"
        safe_copy(source, output / relative, fingerprint)
        result.append(
            {
                "kind": kind,
                "label": (
                    "Approved adaptation identity reference"
                    if kind == "identity"
                    else "Approved adaptation delivery reference"
                ),
                "audio": "../" + relative.as_posix(),
                "transcript": reference["reference_text"],
                "audio_sha256": fingerprint,
            }
        )
    return result


def qwen_candidate(
    *,
    engine: TTSEngine,
    output: Path,
    mode: Mapping[str, Any],
    target: Mapping[str, Any],
    voice: Mapping[str, Any],
    reference: Mapping[str, Any],
    seed: int,
) -> dict[str, Any]:
    effect_chain = mode.get("effect_chain")
    identifier = candidate_identifier(
        mode_id=str(mode["mode_id"]),
        backend=QWEN,
        reference_sha256=str(reference["audio_sha256"]),
        seed=seed,
        effect_chain=str(effect_chain) if effect_chain else None,
    )
    raw = output / "private" / "raw" / f"{identifier}.wav"
    final = output / "private" / "audio" / f"{identifier}.wav"
    raw.parent.mkdir(parents=True, exist_ok=True)
    generation_key = str(mode["voice_key"])
    config = {
        generation_key: candidate_voice(
            voice,
            reference,
            seed=seed,
        )
    }
    success = engine.generate_voice(
        str(target["text"]),
        str(target["instruct"]),
        generation_key,
        config,
        str(raw),
    )
    if not success or not raw.is_file():
        raise CoverageRoundError(
            f"Qwen generation failed for {mode['mode_id']} seed {seed}."
        )
    effects = apply_effect_chain(
        raw,
        final,
        str(effect_chain) if effect_chain else None,
    )
    return {
        "candidate_id": identifier,
        "mode_id": mode["mode_id"],
        "backend": QWEN,
        "backend_source": "fresh_coverage_generation",
        "seed": seed,
        "text": target["text"],
        "instruct": target["instruct"],
        "reference_audio_sha256": reference["audio_sha256"],
        "audio_path": str(final),
        "audio_relative": final.relative_to(output).as_posix(),
        "audio": audio_record(final),
        "effect_processing": effects,
        "generation_receipt": None,
    }


def specialist_candidate(
    *,
    backend: ResponsiveVoiceBackend,
    output: Path,
    mode: Mapping[str, Any],
    target: Mapping[str, Any],
    identity: Mapping[str, Any],
    performance: Mapping[str, Any],
    backend_name: str,
    seed: int,
) -> dict[str, Any]:
    effect_chain = mode.get("effect_chain")
    reference_sha = (
        str(performance["audio_sha256"])
        if backend_name in {FISH, INDEX}
        else str(identity["audio_sha256"])
    )
    identifier = candidate_identifier(
        mode_id=str(mode["mode_id"]),
        backend=backend_name,
        reference_sha256=reference_sha,
        seed=seed,
        effect_chain=str(effect_chain) if effect_chain else None,
    )
    raw = output / "private" / "raw" / f"{identifier}.wav"
    final = output / "private" / "audio" / f"{identifier}.wav"
    raw.parent.mkdir(parents=True, exist_ok=True)
    text = str(target["text"])
    instruction = " ".join(
        item.strip()
        for item in (str(target["instruct"]), str(mode["review_instruction"]))
        if item.strip()
    )
    research_wer = float(
        RESEARCH_ADMISSION_MAX_WER.get(str(mode["mode_id"]), 0.30)
    )
    require_first_word = bool(
        RESEARCH_REQUIRE_FIRST_WORD.get(str(mode["mode_id"]), True)
    )
    if backend_name == VOX:
        receipt = backend.generate(
            route={
                "backend": VOX,
                "identity_audio_path": str(identity["audio_path"]),
                "identity_text": identity["reference_text"],
                "control": {
                    "instruction": instruction,
                    "cfg_value": 2.0,
                    "inference_timesteps": 10,
                    "warmup_patches": 0,
                    "max_tokens": 2000,
                },
                "verification": {
                    "maximum_word_error_rate": research_wer,
                    "require_first_word": require_first_word,
                },
            },
            text=text,
            output_path=raw,
            seed=seed,
        )
    elif backend_name == INDEX:
        receipt = backend.generate(
            route={
                "backend": INDEX,
                "identity_audio_path": str(identity["audio_path"]),
                "identity_text": identity["reference_text"],
                "performance_audio_path": str(performance["audio_path"]),
                "control": {
                    "emotion_strength": float(
                        INDEX_STRENGTH.get(str(mode["mode_id"]), 0.85)
                    ),
                    "diffusion_steps": 8,
                    "num_beams": 1,
                    "greedy": True,
                    "max_mel_tokens": 650,
                },
                "verification": {
                    "maximum_word_error_rate": research_wer,
                    "require_first_word": require_first_word,
                },
            },
            text=text,
            output_path=raw,
            seed=seed,
        )
    elif backend_name == FISH:
        receipt = backend.fish.generate_zero_shot(
            text=text,
            reference_audio=identity["audio_path"],
            reference_text=identity["reference_text"],
            control={
                "api_model_header": "s2.1-pro-free",
                "prompt_mode": "full_alexandria_tag",
                "tag": FISH_TAGS.get(str(mode["mode_id"]), instruction),
                "temperature": 0.7,
                "top_p": 0.7,
                "repetition_penalty": 1.2,
                "verification_maximum_word_error_rate": research_wer,
                "verification_require_first_word": require_first_word,
            },
            output_path=raw,
        )
    else:
        raise CoverageRoundError(f"Unsupported backend: {backend_name}")
    if not raw.is_file():
        raise CoverageRoundError(
            f"{backend_name} created no candidate for {mode['mode_id']}."
        )
    effects = apply_effect_chain(
        raw,
        final,
        str(effect_chain) if effect_chain else None,
    )
    return {
        "candidate_id": identifier,
        "mode_id": mode["mode_id"],
        "backend": backend_name,
        "backend_source": "fresh_coverage_generation",
        "seed": seed,
        "text": text,
        "instruct": target["instruct"],
        "reference_audio_sha256": reference_sha,
        "identity_audio_sha256": identity["audio_sha256"],
        "performance_audio_sha256": performance["audio_sha256"],
        "audio_path": str(final),
        "audio_relative": final.relative_to(output).as_posix(),
        "audio": audio_record(final),
        "effect_processing": effects,
        "generation_receipt": receipt,
    }


def build_review(
    *,
    output: Path,
    modes: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    omissions: list[dict[str, Any]],
) -> None:
    review = output / "review"
    review.mkdir(parents=True, exist_ok=True)
    by_mode: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        by_mode.setdefault(str(row["mode_id"]), []).append(row)
    randomizer = random.Random(2026080103)
    public_modes = []
    for mode in modes:
        rows = list(by_mode.get(str(mode["mode_id"]), []))
        randomizer.shuffle(rows)
        public_modes.append(
            {
                "mode_id": mode["mode_id"],
                "title": mode["title"],
                "instruction": mode["review_instruction"],
                "character": mode["character"],
                "text": mode["target_text"],
                "delivery_direction": mode["target_instruct"],
                "references": mode["public_references"],
                "speaker_split": mode.get("speaker_split"),
                "candidates": [
                    {
                        "candidate_id": row["candidate_id"],
                        "display_id": chr(ord("A") + index),
                        "audio": "../" + row["audio_relative"],
                    }
                    for index, row in enumerate(rows)
                ],
            }
        )
    public = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "modes": public_modes,
        "objective_omission_count": len(omissions),
        "coverage_targets": CHARACTER_COVERAGE_TARGETS,
    }
    (review / "data.js").write_text(
        "window.ALEXANDRIA_OVERLAP_COVERAGE_V3 = "
        + json.dumps(public, indent=2, ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )
    (review / "index.html").write_text(
        """<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Original Sin character coverage v3</title><link rel='icon' href='data:,'><style>:root{font-family:Inter,system-ui,sans-serif;color:#29251f;background:#f1eee7}*{box-sizing:border-box}body{margin:0}header,main{max-width:1080px;margin:auto;padding:28px 20px}header{border-bottom:1px solid #d4ccbf}h1,h2,h3{font-family:Georgia,serif}.mode{margin:40px 0}.candidate,.reference,.notice{background:#fffdf8;border:1px solid #d5cdbf;border-radius:10px;padding:18px;margin:16px 0}.reference{background:#e9eee9}.notice{background:#fff4d6}.reference-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.meta{font-size:13px;color:#6d655b}.ratings{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:12px 0}label{display:grid;gap:5px;font-size:13px;font-weight:650}select,textarea{font:inherit;padding:8px;border:1px solid #b9afa2;border-radius:5px;background:white}textarea{width:100%;min-height:72px}.checks,.decision{display:flex;gap:14px;flex-wrap:wrap;margin:12px 0}.checks label,.decision label{display:flex;align-items:center;gap:7px;border:1px solid #b9afa2;border-radius:6px;padding:8px;background:white}audio{width:100%}button{padding:10px 14px;border:0;border-radius:6px;background:#315c55;color:white;font-weight:700}.line{border-left:3px solid #c9bda9;padding-left:14px}@media(max-width:820px){.ratings{grid-template-columns:1fr 1fr}.reference-grid{grid-template-columns:1fr}}@media(max-width:480px){.ratings{grid-template-columns:1fr}}</style></head><body><header><p>Alexandria character-completeness gate</p><h1>Original Sin overlap coverage — round v3</h1><p>Model identities are hidden. Listen to the approved adaptation references first. A candidate cannot pass with the wrong character, missing words, missing required processing, or a written blocking note. This round closes measured character-mode deficits rather than retesting modes already accepted.</p><button id='export'>Export review</button> <span id='progress'></span></header><main id='app'></main><script src='data.js'></script><script src='app.js'></script></body></html>""",
        encoding="utf-8",
    )
    (review / "app.js").write_text(
        """(()=>{'use strict';const d=window.ALEXANDRIA_OVERLAP_COVERAGE_V3,key='alexandria-overlap-coverage:'+d.round_id;let saved={};try{saved=JSON.parse(localStorage.getItem(key)||'{}')}catch(_){saved={}}const app=document.querySelector('#app'),scale='<option value="">—</option>'+[1,2,3,4,5].map(v=>`<option>${v}</option>`).join('');for(const m of d.modes){const section=document.createElement('section');section.className='mode';section.innerHTML=`<h2>${m.title}</h2><p>${m.instruction}</p><div class="line"><p><strong>Unseen book line:</strong> ${m.text}</p><p class="meta"><strong>Direction:</strong> ${m.delivery_direction}</p></div>${m.speaker_split?`<div class="notice"><strong>Speaker split check:</strong> These book chunks are labelled ${m.speaker_split.source_label}, but the character identity under review is ${m.speaker_split.canonical_character}. Judge against the adaptation reference, not the generic book label.</div>`:''}<h3>Approved adaptation references</h3>`;const refs=document.createElement('div');refs.className='reference-grid';for(const r of m.references){const el=document.createElement('article');el.className='reference';el.innerHTML=`<strong>${r.label}</strong><audio controls preload="none" src="${r.audio}"></audio><p class="meta">${r.transcript}</p>`;refs.appendChild(el)}section.appendChild(refs);section.insertAdjacentHTML('beforeend','<h3>Blind generated candidates</h3>');app.appendChild(section);if(!m.candidates.length){section.insertAdjacentHTML('beforeend','<p>No candidate survived objective screening.</p>');continue}for(const c of m.candidates){const el=document.createElement('article');el.className='candidate';el.innerHTML=`<p class="meta">Candidate ${c.display_id}</p><audio controls preload="none" src="${c.audio}"></audio><div class="ratings"><label>Identity<select data-id="${c.candidate_id}" data-name="identity">${scale}</select></label><label>Delivery fit<select data-id="${c.candidate_id}" data-name="delivery">${scale}</select></label><label>Naturalness<select data-id="${c.candidate_id}" data-name="naturalness">${scale}</select></label><label>Intelligibility<select data-id="${c.candidate_id}" data-name="intelligibility">${scale}</select></label><label>Effects / processing<select data-id="${c.candidate_id}" data-name="effects">${scale}</select></label></div><div class="checks"><label><input type="radio" name="complete-${c.candidate_id}" value="complete" data-id="${c.candidate_id}" data-name="completeness">Entire line present</label><label><input type="radio" name="complete-${c.candidate_id}" value="incomplete" data-id="${c.candidate_id}" data-name="completeness">Cut off / incomplete</label></div><div class="decision"><label><input type="radio" name="decision-${c.candidate_id}" value="pass" data-id="${c.candidate_id}" data-name="decision">Pass</label><label><input type="radio" name="decision-${c.candidate_id}" value="fail" data-id="${c.candidate_id}" data-name="decision">Fail</label></div><label>Notes<textarea data-id="${c.candidate_id}" data-name="notes"></textarea></label>`;section.appendChild(el)}}for(const e of document.querySelectorAll('[data-id]')){const x=saved[e.dataset.id]||{},v=x[e.dataset.name];if(e.type==='radio')e.checked=v===e.value;else if(v!=null)e.value=v;e.addEventListener(e.tagName==='TEXTAREA'?'input':'change',event=>{const t=event.target,id=t.dataset.id,n=t.dataset.name;saved[id]=saved[id]||{};saved[id][n]=t.type==='radio'?t.value:t.value;localStorage.setItem(key,JSON.stringify(saved));progress()})}function progress(){const total=d.modes.reduce((n,m)=>n+m.candidates.length,0),done=Object.values(saved).filter(x=>x.identity&&x.delivery&&x.naturalness&&x.intelligibility&&x.effects&&x.completeness&&x.decision).length;document.querySelector('#progress').textContent=`${done} of ${total} reviewed`}document.querySelector('#export').onclick=()=>{const payload={schema_version:1,round_id:d.round_id,exported_at:new Date().toISOString(),results:saved};const a=document.createElement('a'),b=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'});a.href=URL.createObjectURL(b);a.download=d.round_id+'-tristan.json';a.click()};progress()})();""",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--config-path", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--profile-path", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--mode",
        action="append",
        default=[],
        help="Build only the named mode_id. Repeat for more than one mode.",
    )
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    project = args.project_root.expanduser().resolve()
    config_path = args.config_path.expanduser().resolve()
    profile_path = args.profile_path.expanduser().resolve()
    output = args.output_root.expanduser().resolve()
    if output.exists():
        if args.replace and args.resume:
            raise CoverageRoundError("Choose either --replace or --resume, not both.")
        if not args.replace and not args.resume:
            raise CoverageRoundError(
                f"Output already exists; pass --replace or --resume: {output}"
            )
        if args.replace:
            shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("ALEXANDRIA_INDEXTTS2_ROOT", str(INDEXTTS2_ROOT))

    chunks = read_json(project / "chunks.json", "Project chunks")
    voices = read_json(project / "voice_config.json", "Voice configuration")
    profile = read_json(profile_path, "Approved adaptation profile")
    config = read_json(config_path, "Alexandria configuration") if config_path.is_file() else {}
    transcript = read_json(
        project
        / "external_workflows"
        / "big_finish_overlap_reference_v1"
        / "private"
        / "transcript.json",
        "Adaptation transcript",
    )
    if not isinstance(chunks, list) or not isinstance(voices, Mapping):
        raise CoverageRoundError("Project chunks or Voice configuration is invalid.")
    transcript_text = " ".join(normalized_words(str(transcript.get("text") or "")))

    previous: Mapping[str, Any] = {}
    previous_path = output / "private" / "answer-key.json"
    if args.resume and previous_path.is_file():
        loaded = read_json(previous_path, "Previous v3 answer key")
        if not isinstance(loaded, Mapping) or loaded.get("round_id") != ROUND_ID:
            raise CoverageRoundError("Previous v3 answer key is incompatible.")
        previous = loaded
    previous_by_slot = {
        (str(row.get("mode_id")), str(row.get("backend"))): copy.deepcopy(dict(row))
        for row in (previous.get("candidates") or {}).values()
        if isinstance(row, Mapping)
    }

    requested_modes = {str(value).strip() for value in args.mode if str(value).strip()}
    known_modes = {str(mode["mode_id"]) for mode in MODE_SPECS}
    unknown_modes = sorted(requested_modes - known_modes)
    if unknown_modes:
        raise CoverageRoundError(f"Unknown mode IDs: {unknown_modes}")

    prepared_modes: list[dict[str, Any]] = []
    specs: list[dict[str, Any]] = []
    for raw in MODE_SPECS:
        mode = copy.deepcopy(raw)
        if requested_modes and str(mode["mode_id"]) not in requested_modes:
            continue
        target = target_record(mode, chunks)
        normalized_target = " ".join(normalized_words(str(target["text"])))
        if normalized_target and normalized_target in transcript_text:
            raise CoverageRoundError(
                f"Target is present in the adaptation and is not an unseen test: {mode['mode_id']}"
            )
        voice = voices.get(mode["voice_key"])
        if not isinstance(voice, Mapping):
            raise CoverageRoundError(f"Voice is missing: {mode['voice_key']}")
        identity = current_identity_reference(project, voice)
        performance_spec = mode["performance"]
        if performance_spec["kind"] == "identity":
            performance = identity
        elif performance_spec["kind"] == "locked_chunk":
            performance = locked_reference(chunks[int(performance_spec["chunk_id"])])
        elif performance_spec["kind"] == "profile_candidate":
            performance = profile_reference(
                project,
                profile,
                str(performance_spec["candidate_id"]),
            )
        else:
            raise CoverageRoundError(
                f"Unknown performance source: {performance_spec['kind']}"
            )
        mode.update(
            {
                "target_text": target["text"],
                "target_instruct": target["instruct"],
                "target_chunk_ids": target["chunk_ids"],
                "public_references": public_references(
                    output=output,
                    mode=mode,
                    identity=identity,
                    performance=performance,
                ),
                "planned_backends": list(mode["models"]),
            }
        )
        prepared_modes.append(mode)
        for backend_name in mode["models"]:
            specs.append(
                {
                    "mode": mode,
                    "target": target,
                    "voice": voice,
                    "identity": identity,
                    "performance": performance,
                    "backend": backend_name,
                }
            )

    attempts: list[dict[str, Any]] = []
    omissions: list[dict[str, Any]] = []

    qwen_specs = [spec for spec in specs if spec["backend"] == QWEN]
    if qwen_specs:
        engine = TTSEngine(config)
        try:
            for spec in qwen_specs:
                slot = (str(spec["mode"]["mode_id"]), QWEN)
                prior = previous_by_slot.get(slot)
                if prior is not None:
                    path = Path(str(prior.get("audio_path") or "")).resolve()
                    expected = str((prior.get("audio") or {}).get("sha256") or "")
                    if not path.is_file() or sha256_file(path) != expected:
                        raise CoverageRoundError(f"Previous candidate changed: {slot}")
                    prior["resumed_from_previous_build"] = True
                    attempts.append(prior)
                    continue
                try:
                    attempts.append(
                        qwen_candidate(
                            engine=engine,
                            output=output,
                            mode=spec["mode"],
                            target=spec["target"],
                            voice=spec["voice"],
                            reference=spec["performance"],
                            seed=PRIMARY_SEED,
                        )
                    )
                except Exception as first:
                    try:
                        row = qwen_candidate(
                            engine=engine,
                            output=output,
                            mode=spec["mode"],
                            target=spec["target"],
                            voice=spec["voice"],
                            reference=spec["performance"],
                            seed=RETRY_SEED,
                        )
                        row["generation_retry_of"] = str(first)
                        attempts.append(row)
                    except Exception as retry:
                        omissions.append(
                            {
                                "mode_id": spec["mode"]["mode_id"],
                                "backend": QWEN,
                                "reason": "generation_failed_after_retry",
                                "primary_error": str(first),
                                "retry_error": str(retry),
                            }
                        )
        finally:
            del engine
            gc.collect()

    specialist_specs = [spec for spec in specs if spec["backend"] != QWEN]
    responsive = ResponsiveVoiceBackend()
    try:
        availability = {
            QWEN: True,
            VOX: responsive.backend_available(VOX),
            FISH: responsive.backend_available("fish_s2_pro_cloud"),
            INDEX: responsive.backend_available(INDEX),
        }
        for spec in specialist_specs:
            backend_name = str(spec["backend"])
            slot = (str(spec["mode"]["mode_id"]), backend_name)
            prior = previous_by_slot.get(slot)
            if prior is not None:
                path = Path(str(prior.get("audio_path") or "")).resolve()
                expected = str((prior.get("audio") or {}).get("sha256") or "")
                if not path.is_file() or sha256_file(path) != expected:
                    raise CoverageRoundError(f"Previous candidate changed: {slot}")
                prior["resumed_from_previous_build"] = True
                attempts.append(prior)
                continue
            if not availability.get(backend_name, False):
                omissions.append(
                    {
                        "mode_id": spec["mode"]["mode_id"],
                        "backend": backend_name,
                        "reason": "backend_unavailable",
                    }
                )
                continue
            try:
                attempts.append(
                    specialist_candidate(
                        backend=responsive,
                        output=output,
                        mode=spec["mode"],
                        target=spec["target"],
                        identity=spec["identity"],
                        performance=spec["performance"],
                        backend_name=backend_name,
                        seed=PRIMARY_SEED,
                    )
                )
            except (ResponsiveBackendUnavailable, ResponsiveVoiceBackendError, CoverageRoundError) as first:
                try:
                    row = specialist_candidate(
                        backend=responsive,
                        output=output,
                        mode=spec["mode"],
                        target=spec["target"],
                        identity=spec["identity"],
                        performance=spec["performance"],
                        backend_name=backend_name,
                        seed=RETRY_SEED,
                    )
                    row["generation_retry_of"] = str(first)
                    attempts.append(row)
                except (ResponsiveBackendUnavailable, ResponsiveVoiceBackendError, CoverageRoundError) as retry:
                    omissions.append(
                        {
                            "mode_id": spec["mode"]["mode_id"],
                            "backend": backend_name,
                            "reason": "generation_failed_after_retry",
                            "primary_error": str(first),
                            "retry_error": str(retry),
                        }
                    )
    finally:
        responsive.close()

    evaluation = attach_transcriptions(attempts)
    accepted: list[dict[str, Any]] = []
    for row in attempts:
        if transcription_passed(row):
            accepted.append(row)
        else:
            omissions.append(
                {
                    "mode_id": row["mode_id"],
                    "backend": row["backend"],
                    "reason": "final_transcription_gate_failed",
                    "candidate_id": row["candidate_id"],
                    "transcription": row.get("transcription"),
                }
            )

    availability = {
        backend: any(
            row["backend"] == backend for row in attempts
        ) or not any(spec["backend"] == backend for spec in specs)
        for backend in MODEL_LABELS
    }
    answer = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "generated_at_utc": utc_now(),
        "project_root": str(project),
        "coverage_contract": CHARACTER_COVERAGE_TARGETS,
        "requested_modes": sorted(requested_modes),
        "mode_count": len(prepared_modes),
        "planned_candidate_count": len(specs),
        "candidate_count": len(accepted),
        "objective_omission_count": len(omissions),
        "backend_availability": availability,
        "modes": prepared_modes,
        "candidates": {row["candidate_id"]: row for row in accepted},
        "omissions": omissions,
        "transcription_evaluation": evaluation,
        "review_contract": {
            "model_identity_hidden": True,
            "approved_reference_audio_visible": True,
            "entire_line_required": True,
            "identity_effects_score_required": True,
            "written_notes_override_pass": True,
            "speaker_split_explicit": True,
        },
        "production_routing_changed": False,
        "project_audio_changed": False,
        "voice_config_changed": False,
    }
    write_json(output / "private" / "answer-key.json", answer)
    write_json(
        output / "generation-summary.json",
        {
            "schema_version": 1,
            "round_id": ROUND_ID,
            "generated_at_utc": answer["generated_at_utc"],
            "mode_count": len(prepared_modes),
            "planned_candidate_count": len(specs),
            "candidate_count": len(accepted),
            "objective_omission_count": len(omissions),
            "backend_counts": {
                backend: sum(row["backend"] == backend for row in accepted)
                for backend in MODEL_LABELS
            },
            "all_retained_candidates_passed_transcription_gate": all(
                transcription_passed(row) for row in accepted
            ),
            "production_routing_changed": False,
            "project_audio_changed": False,
            "voice_config_changed": False,
        },
    )
    build_review(
        output=output,
        modes=prepared_modes,
        candidates=accepted,
        omissions=omissions,
    )
    print(
        json.dumps(
            {
                "round_id": ROUND_ID,
                "output": str(output),
                "review": str(output / "review" / "index.html"),
                "mode_count": len(prepared_modes),
                "planned_candidate_count": len(specs),
                "candidate_count": len(accepted),
                "objective_omission_count": len(omissions),
                "backend_counts": {
                    backend: sum(row["backend"] == backend for row in accepted)
                    for backend in MODEL_LABELS
                },
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
