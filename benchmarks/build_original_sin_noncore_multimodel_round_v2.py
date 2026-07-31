#!/usr/bin/env python3
"""Build the cross-model blind acceptance round for non-core Original Sin Voices.

The round compares only technically compatible engines, shows the approved
adaptation identity and delivery references beside the blind candidates, runs
objective text screening, and records all model/effect provenance privately.
It does not modify project audio, Voice configuration, or production routing.
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

from build_original_sin_noncore_quasi_emotive_round_v1 import (  # noqa: E402
    MODE_SPECS,
    TRANSCRIPTION_ALIAS_POLICY,
    _replace_phrases,
    _word_error_rate_from_words,
    audio_record,
    current_identity_reference,
    locked_reference,
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


ROUND_ID = "alexandria_original_sin_noncore_multimodel_round_v2"
DEFAULT_PROJECT = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665"
)
DEFAULT_OUTPUT = (
    DEFAULT_PROJECT
    / "external_workflows"
    / "big_finish_overlap_reference_v1"
    / "noncore_multimodel_round_v2"
)
DEFAULT_V1_ANSWER = (
    DEFAULT_PROJECT
    / "external_workflows"
    / "big_finish_overlap_reference_v1"
    / "noncore_quasi_emotive_round_v1"
    / "private"
    / "answer-key.json"
)
DEFAULT_V1_DECISION = Path(
    "benchmarks/original_sin_noncore_quasi_emotive_round_v1_decision.json"
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

MODEL_MATRIX: dict[str, tuple[str, ...]] = {
    "beltempest_interrogative_impatience": (QWEN, VOX, INDEX),
    "beltempest_military_volatility": (QWEN, VOX, INDEX),
    "beltempest_weary_resignation": (QWEN, FISH, INDEX),
    "beltempest_urgent_command": (QWEN, VOX, INDEX),
    "tobias_cultivated_menace": (QWEN, VOX, INDEX),
    "tobias_polished_probe": (VOX, FISH, INDEX),
    "zebulon_nervous_analysis": (QWEN, VOX, INDEX),
    "zebulon_intense_questioning": (QWEN, VOX, INDEX),
    "hater_wounded_fury": (VOX, FISH, INDEX),
    "karvellis_amplified_command": (QWEN, VOX, FISH),
    "lubineki_rough_jovial": (QWEN, VOX, FISH),
    "powerless_panicked_urgency": (VOX, FISH, INDEX),
    "rashid_tired_authority": (VOX, FISH, INDEX),
    "under_sergeant_military_menace": (QWEN, VOX, INDEX),
    "bot_synthetic_neutral": (QWEN, VOX),
    "computer_interrupted_system": (VOX, FISH, INDEX),
}

EFFECT_CHAINS = {
    "powerless_panicked_urgency": "powerless_alien_modulation_v1",
    "under_sergeant_military_menace": "under_sergeant_intercom_v1",
    "bot_synthetic_neutral": "securitybot_synthetic_v1",
    "computer_interrupted_system": "computer_modulation_v1",
}

TARGET_CHUNK_OVERRIDES = {
    # The v1 Hater line is dominated by adaptation-specific proper names and
    # tests recognizer vocabulary more than Voice identity or wounded challenge.
    "hater_wounded_fury": 3803,
}

RESEARCH_ADMISSION_MAX_WER = {
    # These two synthetic lines contain several proper-name/tokenization cases
    # covered by the final round's narrow alias policy.
    "bot_synthetic_neutral": 0.40,
    "computer_interrupted_system": 0.35,
}

FISH_TAGS = {
    "beltempest_weary_resignation": (
        "Begin with a soft audible sigh, then speak with weary military "
        "resignation, softened pacing, and restrained authority."
    ),
    "tobias_polished_probe": (
        "Speak with polished conversational calm and a subtle cultivated threat "
        "underneath; never broad or theatrical."
    ),
    "hater_wounded_fury": (
        "Speak with thunderous alien formality, wounded pride, and commanding fury."
    ),
    "karvellis_amplified_command": (
        "Speak as a hard amplified command: clipped, urgent, penetrating, and cold."
    ),
    "lubineki_rough_jovial": (
        "Speak with rough jovial confidence, blunt humour, and alert concern."
    ),
    "powerless_panicked_urgency": (
        "Speak with exposed panic, urgent projection, and alien vulnerability."
    ),
    "rashid_tired_authority": (
        "Speak with tired bureaucratic authority, dry bluntness, and the exact "
        "accent of the reference."
    ),
    "computer_interrupted_system": (
        "Speak as a precise modulated computer system interrupted mid-sentence."
    ),
}

INDEX_STRENGTH = {
    "beltempest_interrogative_impatience": 0.80,
    "beltempest_military_volatility": 0.90,
    "beltempest_weary_resignation": 0.75,
    "beltempest_urgent_command": 1.00,
    "tobias_cultivated_menace": 0.90,
    "tobias_polished_probe": 0.70,
    "zebulon_nervous_analysis": 0.90,
    "zebulon_intense_questioning": 1.00,
    "hater_wounded_fury": 1.00,
    "powerless_panicked_urgency": 1.00,
    "rashid_tired_authority": 0.80,
    "under_sergeant_military_menace": 0.90,
    "computer_interrupted_system": 0.70,
}

TRANSCRIPTION_ALIAS_POLICY_V2 = {
    **TRANSCRIPTION_ALIAS_POLICY,
    "bot_synthetic_neutral": {
        "token_aliases": {
            "forrestor": "forrester",
            "forresta": "forrester",
            "rosling": "roslyn",
            "rosalind": "roslyn",
            "5": "five",
            "500": "five",
            "town": "undertown",
        },
        "phrase_aliases": {
            ("a", "judicator"): ("adjudicator",),
            ("space", "port"): ("spaceport",),
        },
    },
    "beltempest_military_volatility": {
        "token_aliases": {"landsconnect": "landsknecht", "offices": "officers"},
        "phrase_aliases": {},
    },
    "hater_wounded_fury": {
        "token_aliases": {"daf": "daph", "yili": "yilli", "skelski": "skel'ske"},
        "phrase_aliases": {},
    },
    "karvellis_amplified_command": {
        "token_aliases": {"5": "five"},
        "phrase_aliases": {},
    },
    "lubineki_rough_jovial": {
        "token_aliases": {"forresta": "forrester"},
        "phrase_aliases": {},
    },
    "under_sergeant_military_menace": {
        "token_aliases": {"target": "targets"},
        "phrase_aliases": {},
    },
}


class MultimodelRoundError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def candidate_identifier(
    *, mode_id: str, backend: str, reference_sha256: str, seed: int,
    effect_chain: str | None,
) -> str:
    value = ":".join(
        (ROUND_ID, mode_id, backend, reference_sha256, str(seed), effect_chain or "none")
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def safe_copy(source: Path, destination: Path, expected_sha256: str) -> None:
    if not source.is_file() or sha256_file(source) != expected_sha256:
        raise MultimodelRoundError(f"Source is missing or changed: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    if sha256_file(destination) != expected_sha256:
        raise MultimodelRoundError(f"Copied source changed: {destination}")


def bandpass(audio: np.ndarray, rate: int, low: float, high: float) -> np.ndarray:
    nyquist = max(1.0, rate / 2.0)
    low_value = max(20.0, min(low, nyquist * 0.8)) / nyquist
    high_value = max(low + 20.0, min(high, nyquist * 0.95)) / nyquist
    sos = butter(3, [low_value, high_value], btype="bandpass", output="sos")
    return np.asarray(sosfilt(sos, audio), dtype=np.float32)


def delayed_mix(audio: np.ndarray, rate: int, delay_ms: float, amount: float) -> np.ndarray:
    delay = max(1, int(round(rate * delay_ms / 1000.0)))
    shifted = np.zeros_like(audio)
    shifted[delay:] = audio[:-delay]
    return np.asarray((1.0 - amount) * audio + amount * shifted, dtype=np.float32)


def apply_effect_chain(source: Path, destination: Path, chain: str | None) -> dict[str, Any] | None:
    if chain is None:
        if source != destination:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        return None
    audio, rate = sf.read(str(source), dtype="float32", always_2d=True)
    mono = np.mean(audio, axis=1, dtype=np.float32)
    time_axis = np.arange(mono.size, dtype=np.float32) / float(rate)
    parameters: dict[str, Any]
    if chain == "powerless_alien_modulation_v1":
        output = bandpass(mono, rate, 170.0, 5200.0)
        output = delayed_mix(output, rate, 10.0, 0.22)
        output *= 1.0 + 0.10 * np.sin(2.0 * np.pi * 6.5 * time_axis)
        output = np.tanh(output * 1.18) / np.tanh(1.18)
        parameters = {"bandpass_hz": [170.0, 5200.0], "chorus_delay_ms": 10.0, "chorus_mix": 0.22, "amplitude_modulation_hz": 6.5, "amplitude_modulation_depth": 0.10}
    elif chain == "under_sergeant_intercom_v1":
        output = bandpass(mono, rate, 300.0, 3600.0)
        output = np.tanh(output * 1.30) / np.tanh(1.30)
        parameters = {"bandpass_hz": [300.0, 3600.0], "soft_saturation": 1.30}
    elif chain == "securitybot_synthetic_v1":
        output = bandpass(mono, rate, 280.0, 4600.0)
        output *= 1.0 + 0.06 * np.sin(2.0 * np.pi * 18.0 * time_axis)
        parameters = {"bandpass_hz": [280.0, 4600.0], "amplitude_modulation_hz": 18.0, "amplitude_modulation_depth": 0.06}
    elif chain == "computer_modulation_v1":
        output = bandpass(mono, rate, 260.0, 4800.0)
        output = delayed_mix(output, rate, 5.0, 0.16)
        output *= 1.0 + 0.16 * np.sin(2.0 * np.pi * 31.0 * time_axis)
        output = np.tanh(output * 1.12) / np.tanh(1.12)
        parameters = {"bandpass_hz": [260.0, 4800.0], "chorus_delay_ms": 5.0, "chorus_mix": 0.16, "amplitude_modulation_hz": 31.0, "amplitude_modulation_depth": 0.16}
    else:
        raise MultimodelRoundError(f"Unknown effect chain: {chain}")
    peak = float(np.max(np.abs(output))) if output.size else 0.0
    target_peak = 10.0 ** (-1.0 / 20.0)
    if peak > target_peak:
        output = output * (target_peak / peak)
    destination.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(destination), output, int(rate), subtype="PCM_16")
    return {"chain": chain, "parameters": parameters, "source_sha256": sha256_file(source), "output_sha256": sha256_file(destination)}


def adjusted_word_error_rate(mode_id: str, expected: str, transcript: str) -> float | None:
    policy = TRANSCRIPTION_ALIAS_POLICY_V2.get(mode_id)
    if policy is None:
        return None
    expected_words = normalized_words(expected)
    aliases = dict(policy.get("token_aliases") or {})
    heard = [aliases.get(word, word) for word in normalized_words(transcript)]
    heard = _replace_phrases(heard, dict(policy.get("phrase_aliases") or {}))
    return _word_error_rate_from_words(expected_words, heard)


def evaluate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    evaluation = evaluate_transcriptions(
        {
            "model_status": model_cache_status("mlx_whisper_base"),
            "outputs": [
                {"sample_id": row["candidate_id"], "path": row["audio_path"], "text": row["text"]}
                for row in rows
            ],
        }
    )
    measurements = evaluation.get("measurements") or {}
    for row in rows:
        result = copy.deepcopy(measurements.get(row["candidate_id"]) or {})
        transcript = result.get("transcript")
        if isinstance(transcript, str):
            adjusted = adjusted_word_error_rate(str(row["mode_id"]), str(row["text"]), transcript)
            if adjusted is not None:
                result["raw_word_error_rate"] = result.get("word_error_rate")
                result["word_error_rate"] = adjusted
                result["alias_policy_applied"] = row["mode_id"]
        row["transcription"] = result
    return evaluation


def transcription_passed(row: Mapping[str, Any]) -> bool:
    value = row.get("transcription")
    if not isinstance(value, Mapping):
        return False
    wer = value.get("word_error_rate")
    return isinstance(wer, (int, float)) and not isinstance(wer, bool) and float(wer) <= MAX_ACCEPTABLE_WER


def mode_references(*, project: Path, output: Path, mode: Mapping[str, Any], voice: Mapping[str, Any], reference_chunk: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    identity = current_identity_reference(project, voice)
    performance = locked_reference(reference_chunk)
    public: list[dict[str, Any]] = []
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
        public.append({"kind": kind, "label": "Approved adaptation identity reference" if kind == "identity" else "Approved adaptation delivery reference", "audio": "../" + relative.as_posix(), "transcript": reference["reference_text"], "audio_sha256": fingerprint})
    return identity, performance, public


def qwen_reuse_candidate(*, output: Path, mode: Mapping[str, Any], candidate_id: str, v1_answer: Mapping[str, Any], effect_chain: str | None) -> dict[str, Any]:
    row = (v1_answer.get("candidates") or {}).get(candidate_id)
    if not isinstance(row, Mapping):
        raise MultimodelRoundError(f"Reusable Qwen candidate is missing: {candidate_id}")
    source = Path(str(row.get("audio_path") or "")).expanduser().resolve()
    expected = str((row.get("audio") or {}).get("sha256") or "")
    identifier = candidate_identifier(mode_id=str(mode["mode_id"]), backend=QWEN, reference_sha256=str(row.get("reference_audio_sha256") or expected), seed=int(row.get("seed") or PRIMARY_SEED), effect_chain=effect_chain)
    raw = output / "private" / "raw" / f"{identifier}.wav"
    final = output / "private" / "audio" / f"{identifier}.wav"
    safe_copy(source, raw, expected)
    effects = apply_effect_chain(raw, final, effect_chain)
    return {"candidate_id": identifier, "mode_id": mode["mode_id"], "backend": QWEN, "backend_source": "v1_diagnostic_reuse", "source_candidate_id": candidate_id, "seed": row.get("seed"), "text": mode["target_text"], "instruct": mode["target_instruct"], "reference_audio_sha256": row.get("reference_audio_sha256"), "audio_path": str(final), "audio_relative": final.relative_to(output).as_posix(), "audio": audio_record(final), "effect_processing": effects, "generation_receipt": None}


def specialist_candidate(*, backend: ResponsiveVoiceBackend, output: Path, mode: Mapping[str, Any], identity: Mapping[str, Any], performance: Mapping[str, Any], backend_name: str, seed: int, effect_chain: str | None) -> dict[str, Any]:
    reference_sha = str(performance["audio_sha256"]) if backend_name in {FISH, INDEX} else str(identity["audio_sha256"])
    identifier = candidate_identifier(mode_id=str(mode["mode_id"]), backend=backend_name, reference_sha256=reference_sha, seed=seed, effect_chain=effect_chain)
    raw = output / "private" / "raw" / f"{identifier}.wav"
    final = output / "private" / "audio" / f"{identifier}.wav"
    raw.parent.mkdir(parents=True, exist_ok=True)
    text = str(mode["target_text"])
    instruction = " ".join(value.strip() for value in (str(mode["target_instruct"]), str(mode["review_instruction"])) if value.strip())
    research_max_wer = float(
        RESEARCH_ADMISSION_MAX_WER.get(str(mode["mode_id"]), 0.30)
    )
    if backend_name == VOX:
        receipt = backend.generate(route={"backend": VOX, "identity_audio_path": str(identity["audio_path"]), "identity_text": identity["reference_text"], "control": {"instruction": instruction, "cfg_value": 2.0, "inference_timesteps": 10, "warmup_patches": 0, "max_tokens": 1800}, "verification": {"maximum_word_error_rate": research_max_wer, "require_first_word": True}}, text=text, output_path=raw, seed=seed)
    elif backend_name == INDEX:
        receipt = backend.generate(route={"backend": INDEX, "identity_audio_path": str(identity["audio_path"]), "identity_text": identity["reference_text"], "performance_audio_path": str(performance["audio_path"]), "control": {"emotion_strength": float(INDEX_STRENGTH.get(str(mode["mode_id"]), 0.85)), "diffusion_steps": 8, "num_beams": 1, "greedy": True, "max_mel_tokens": 600}, "verification": {"maximum_word_error_rate": research_max_wer, "require_first_word": True}}, text=text, output_path=raw, seed=seed)
    elif backend_name == FISH:
        receipt = backend.fish.generate_zero_shot(text=text, reference_audio=identity["audio_path"], reference_text=identity["reference_text"], control={"api_model_header": "s2.1-pro-free", "prompt_mode": "full_alexandria_tag", "tag": FISH_TAGS.get(str(mode["mode_id"]), instruction), "temperature": 0.7, "top_p": 0.7, "repetition_penalty": 1.2, "verification_maximum_word_error_rate": research_max_wer, "verification_require_first_word": True}, output_path=raw)
    else:
        raise MultimodelRoundError(f"Unsupported v2 backend: {backend_name}")
    if not raw.is_file():
        raise MultimodelRoundError(f"{backend_name} created no candidate for {mode['mode_id']}.")
    effects = apply_effect_chain(raw, final, effect_chain)
    return {"candidate_id": identifier, "mode_id": mode["mode_id"], "backend": backend_name, "backend_source": "fresh_multimodel_generation", "seed": seed, "text": text, "instruct": mode["target_instruct"], "reference_audio_sha256": reference_sha, "identity_audio_sha256": identity["audio_sha256"], "performance_audio_sha256": performance["audio_sha256"], "audio_path": str(final), "audio_relative": final.relative_to(output).as_posix(), "audio": audio_record(final), "effect_processing": effects, "generation_receipt": receipt}


def build_review(*, output: Path, modes: list[dict[str, Any]], candidates: list[dict[str, Any]], omissions: list[dict[str, Any]]) -> None:
    review = output / "review"
    review.mkdir(parents=True, exist_ok=True)
    by_mode: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        by_mode.setdefault(str(row["mode_id"]), []).append(row)
    randomizer = random.Random(202607312)
    public_modes = []
    for mode in modes:
        rows = list(by_mode.get(str(mode["mode_id"]), []))
        randomizer.shuffle(rows)
        labels = [chr(ord("A") + index) for index in range(len(rows))]
        public_modes.append({"mode_id": mode["mode_id"], "title": mode["title"], "instruction": mode["review_instruction"], "speaker": mode["speaker"], "text": mode["target_text"], "delivery_direction": mode["target_instruct"], "references": mode["public_references"], "candidates": [{"candidate_id": row["candidate_id"], "display_id": labels[index], "audio": "../" + row["audio_relative"]} for index, row in enumerate(rows)]})
    public = {"schema_version": 1, "round_id": ROUND_ID, "modes": public_modes, "objective_omission_count": len(omissions)}
    (review / "data.js").write_text("window.ALEXANDRIA_NONCORE_MULTIMODEL = " + json.dumps(public, indent=2, ensure_ascii=False) + ";\n", encoding="utf-8")
    (review / "index.html").write_text("""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Non-core Voice multimodel review</title><link rel='icon' href='data:,'><style>:root{font-family:Inter,system-ui,sans-serif;color:#29251f;background:#f1eee7}*{box-sizing:border-box}body{margin:0}header,main{max-width:1080px;margin:auto;padding:28px 20px}header{border-bottom:1px solid #d4ccbf}h1,h2,h3{font-family:Georgia,serif}.mode{margin:40px 0}.candidate,.reference{background:#fffdf8;border:1px solid #d5cdbf;border-radius:10px;padding:18px;margin:16px 0}.reference{background:#e9eee9}.reference-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.meta{font-size:13px;color:#6d655b}.ratings{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:12px 0}label{display:grid;gap:5px;font-size:13px;font-weight:650}select,textarea{font:inherit;padding:8px;border:1px solid #b9afa2;border-radius:5px;background:white}textarea{width:100%;min-height:72px}.checks,.decision{display:flex;gap:14px;flex-wrap:wrap;margin:12px 0}.checks label,.decision label{display:flex;align-items:center;gap:7px;border:1px solid #b9afa2;border-radius:6px;padding:8px;background:white}audio{width:100%}button{padding:10px 14px;border:0;border-radius:6px;background:#315c55;color:white;font-weight:700}.line{border-left:3px solid #c9bda9;padding-left:14px}@media(max-width:820px){.ratings{grid-template-columns:1fr 1fr}.reference-grid{grid-template-columns:1fr}}@media(max-width:480px){.ratings{grid-template-columns:1fr}}</style></head><body><header><p>Alexandria cross-model Voice acceptance</p><h1>Non-core quasi-emotive multimodel review</h1><p>Model identities are hidden. Listen to the approved adaptation references first, then score every generated candidate. Written notes override pass buttons. A candidate cannot pass if the entire line is missing or required character processing/effects are absent.</p><button id='export'>Export review</button> <span id='progress'></span></header><main id='app'></main><script src='data.js'></script><script src='app.js'></script></body></html>""", encoding="utf-8")
    (review / "app.js").write_text("""(()=>{'use strict';const d=window.ALEXANDRIA_NONCORE_MULTIMODEL,key='alexandria-noncore-multimodel:'+d.round_id;let saved={};try{saved=JSON.parse(localStorage.getItem(key)||'{}')}catch(_){saved={}}const app=document.querySelector('#app'),scale='<option value="">—</option>'+[1,2,3,4,5].map(v=>`<option>${v}</option>`).join('');for(const m of d.modes){const section=document.createElement('section');section.className='mode';section.innerHTML=`<h2>${m.title}</h2><p>${m.instruction}</p><div class="line"><p><strong>Unseen line:</strong> ${m.text}</p><p class="meta"><strong>Direction:</strong> ${m.delivery_direction}</p></div><h3>Approved adaptation references</h3>`;const refs=document.createElement('div');refs.className='reference-grid';for(const r of m.references){const el=document.createElement('article');el.className='reference';el.innerHTML=`<strong>${r.label}</strong><audio controls preload="none" src="${r.audio}"></audio><p class="meta">${r.transcript}</p>`;refs.appendChild(el)}section.appendChild(refs);section.insertAdjacentHTML('beforeend','<h3>Blind generated candidates</h3>');app.appendChild(section);if(!m.candidates.length){section.insertAdjacentHTML('beforeend','<p>No candidate survived objective screening.</p>');continue}for(const c of m.candidates){const el=document.createElement('article');el.className='candidate';el.innerHTML=`<p class="meta">Candidate ${c.display_id}</p><audio controls preload="none" src="${c.audio}"></audio><div class="ratings"><label>Identity<select data-id="${c.candidate_id}" data-name="identity">${scale}</select></label><label>Delivery fit<select data-id="${c.candidate_id}" data-name="delivery">${scale}</select></label><label>Naturalness<select data-id="${c.candidate_id}" data-name="naturalness">${scale}</select></label><label>Intelligibility<select data-id="${c.candidate_id}" data-name="intelligibility">${scale}</select></label><label>Effects / processing<select data-id="${c.candidate_id}" data-name="effects">${scale}</select></label></div><div class="checks"><label><input type="radio" name="complete-${c.candidate_id}" value="complete" data-id="${c.candidate_id}" data-name="completeness">Entire line present</label><label><input type="radio" name="complete-${c.candidate_id}" value="incomplete" data-id="${c.candidate_id}" data-name="completeness">Cut off / incomplete</label></div><div class="decision"><label><input type="radio" name="decision-${c.candidate_id}" value="pass" data-id="${c.candidate_id}" data-name="decision">Pass</label><label><input type="radio" name="decision-${c.candidate_id}" value="fail" data-id="${c.candidate_id}" data-name="decision">Fail</label></div><label>Notes<textarea data-id="${c.candidate_id}" data-name="notes"></textarea></label>`;section.appendChild(el)}}for(const e of document.querySelectorAll('[data-id]')){const x=saved[e.dataset.id]||{},v=x[e.dataset.name];if(e.type==='radio')e.checked=v===e.value;else if(v!=null)e.value=v;e.addEventListener(e.tagName==='TEXTAREA'?'input':'change',event=>{const t=event.target,id=t.dataset.id,n=t.dataset.name;saved[id]=saved[id]||{};saved[id][n]=t.type==='radio'?t.value:t.value;localStorage.setItem(key,JSON.stringify(saved));progress()})}function progress(){const total=d.modes.reduce((n,m)=>n+m.candidates.length,0),done=Object.values(saved).filter(x=>x.identity&&x.delivery&&x.naturalness&&x.intelligibility&&x.effects&&x.completeness&&x.decision).length;document.querySelector('#progress').textContent=`${done} of ${total} reviewed`}document.querySelector('#export').onclick=()=>{const payload={schema_version:1,round_id:d.round_id,exported_at:new Date().toISOString(),results:saved};const a=document.createElement('a'),b=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'});a.href=URL.createObjectURL(b);a.download=d.round_id+'-tristan.json';a.click()};progress()})();""", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--v1-answer", type=Path, default=DEFAULT_V1_ANSWER)
    parser.add_argument("--v1-decision", type=Path, default=DEFAULT_V1_DECISION)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    project = args.project_root.expanduser().resolve()
    output = args.output_root.expanduser().resolve()
    if output.exists():
        if args.replace and args.resume:
            raise MultimodelRoundError("Choose either --replace or --resume, not both.")
        if not args.replace and not args.resume:
            raise MultimodelRoundError(f"Output already exists; pass --replace to rebuild: {output}")
        if args.replace:
            shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("ALEXANDRIA_INDEXTTS2_ROOT", str(INDEXTTS2_ROOT))
    chunks = read_json(project / "chunks.json", "Project chunks")
    voices = read_json(project / "voice_config.json", "Voice configuration")
    v1_answer = read_json(args.v1_answer.expanduser().resolve(), "v1 answer key")
    v1_decision = read_json(args.v1_decision.expanduser().resolve(), "v1 decision")
    previous_answer: Mapping[str, Any] = {}
    previous_answer_path = output / "private" / "answer-key.json"
    if args.resume and previous_answer_path.is_file():
        loaded_previous = read_json(previous_answer_path, "Previous v2 answer key")
        if (
            not isinstance(loaded_previous, Mapping)
            or loaded_previous.get("round_id") != ROUND_ID
        ):
            raise MultimodelRoundError("Previous v2 answer key is incompatible.")
        previous_answer = loaded_previous
    if not isinstance(chunks, list) or not isinstance(voices, Mapping):
        raise MultimodelRoundError("Project chunks or Voice configuration is invalid.")
    reuse = v1_decision.get("reusable_qwen_candidates")
    if not isinstance(reuse, Mapping):
        raise MultimodelRoundError("v1 decision has no reusable Qwen candidates.")
    by_id = {item.get("id", index): item for index, item in enumerate(chunks) if isinstance(item, Mapping)}
    modes: list[dict[str, Any]] = []
    specs: list[dict[str, Any]] = []
    for raw_mode in MODE_SPECS:
        mode = dict(raw_mode)
        mode_id = str(mode["mode_id"])
        target_chunk_id = TARGET_CHUNK_OVERRIDES.get(
            mode_id,
            int(mode["target_chunk_id"]),
        )
        target = by_id.get(target_chunk_id)
        reference_chunk = by_id.get(mode["reference_chunk_id"])
        voice = voices.get(mode["speaker"])
        if not isinstance(target, Mapping) or not isinstance(reference_chunk, Mapping):
            raise MultimodelRoundError(f"Mode chunks are missing: {mode_id}")
        if not isinstance(voice, Mapping):
            raise MultimodelRoundError(f"Voice is missing: {mode['speaker']}")
        identity, performance, public_refs = mode_references(project=project, output=output, mode=mode, voice=voice, reference_chunk=reference_chunk)
        mode.update({"target_text": str(target.get("text") or ""), "target_instruct": str(target.get("instruct") or ""), "public_references": public_refs, "planned_backends": list(MODEL_MATRIX[mode_id])})
        mode["target_chunk_id"] = target_chunk_id
        modes.append(mode)
        for backend_name in MODEL_MATRIX[mode_id]:
            if backend_name == QWEN and mode_id not in reuse:
                raise MultimodelRoundError(f"Model matrix requests blocked Qwen evidence for {mode_id}.")
            specs.append({"mode": mode, "identity": identity, "performance": performance, "backend": backend_name, "qwen_candidate_id": reuse.get(mode_id), "effect_chain": EFFECT_CHAINS.get(mode_id)})
    backend = ResponsiveVoiceBackend()
    attempts: list[dict[str, Any]] = []
    omissions: list[dict[str, Any]] = []
    previous_by_slot = {
        (str(row.get("mode_id")), str(row.get("backend"))): copy.deepcopy(dict(row))
        for row in (previous_answer.get("candidates") or {}).values()
        if isinstance(row, Mapping)
    }
    try:
        availability = {VOX: backend.backend_available(VOX), FISH: backend.backend_available("fish_s2_pro_cloud"), INDEX: backend.backend_available(INDEX), QWEN: True}
        for spec in specs:
            model = str(spec["backend"])
            mode = spec["mode"]
            prior = previous_by_slot.get((str(mode["mode_id"]), model))
            if prior is not None:
                prior_audio = Path(str(prior.get("audio_path") or "")).expanduser().resolve()
                expected_sha = str((prior.get("audio") or {}).get("sha256") or "")
                if not prior_audio.is_file() or sha256_file(prior_audio) != expected_sha:
                    raise MultimodelRoundError(
                        f"Previous candidate changed for {mode['mode_id']} {model}."
                    )
                prior["resumed_from_previous_build"] = True
                attempts.append(prior)
                continue
            if not availability.get(model, False):
                omissions.append({"mode_id": mode["mode_id"], "backend": model, "reason": "backend_unavailable"})
                continue
            try:
                if model == QWEN:
                    row = qwen_reuse_candidate(output=output, mode=mode, candidate_id=str(spec["qwen_candidate_id"]), v1_answer=v1_answer, effect_chain=spec["effect_chain"])
                else:
                    row = specialist_candidate(backend=backend, output=output, mode=mode, identity=spec["identity"], performance=spec["performance"], backend_name=model, seed=PRIMARY_SEED, effect_chain=spec["effect_chain"])
                attempts.append(row)
            except (ResponsiveBackendUnavailable, ResponsiveVoiceBackendError, MultimodelRoundError) as exc:
                if model == QWEN:
                    omissions.append({"mode_id": mode["mode_id"], "backend": model, "reason": "generation_failed", "error": str(exc)})
                    continue
                try:
                    row = specialist_candidate(backend=backend, output=output, mode=mode, identity=spec["identity"], performance=spec["performance"], backend_name=model, seed=RETRY_SEED, effect_chain=spec["effect_chain"])
                    row["generation_retry_of"] = str(exc)
                    attempts.append(row)
                except (ResponsiveBackendUnavailable, ResponsiveVoiceBackendError, MultimodelRoundError) as retry_exc:
                    omissions.append({"mode_id": mode["mode_id"], "backend": model, "reason": "generation_failed_after_retry", "primary_error": str(exc), "retry_error": str(retry_exc)})
        primary_evaluation = evaluate_rows(attempts)
    finally:
        backend.close()
    accepted: list[dict[str, Any]] = []
    for row in attempts:
        if transcription_passed(row):
            accepted.append(row)
        else:
            omissions.append({"mode_id": row["mode_id"], "backend": row["backend"], "reason": "final_transcription_gate_failed", "candidate_id": row["candidate_id"], "transcription": row.get("transcription")})
    answer = {"schema_version": 1, "round_id": ROUND_ID, "generated_at_utc": utc_now(), "project_root": str(project), "planned_candidate_count": len(specs), "candidate_count": len(accepted), "objective_omission_count": len(omissions), "mode_count": len(modes), "backend_availability": availability, "model_matrix": {key: list(value) for key, value in MODEL_MATRIX.items()}, "effect_chains": EFFECT_CHAINS, "max_acceptable_word_error_rate": MAX_ACCEPTABLE_WER, "modes": modes, "candidates": {row["candidate_id"]: row for row in accepted}, "omissions": omissions, "transcription_evaluation": primary_evaluation, "review_contract": {"model_identity_hidden": True, "approved_reference_audio_visible": True, "entire_line_required": True, "identity_effects_score_required": True, "written_notes_override_pass": True}, "production_routing_changed": False, "project_audio_changed": False, "voice_config_changed": False}
    write_json(output / "private" / "answer-key.json", answer)
    write_json(output / "generation-summary.json", {"schema_version": 1, "round_id": ROUND_ID, "generated_at_utc": answer["generated_at_utc"], "mode_count": len(modes), "planned_candidate_count": len(specs), "candidate_count": len(accepted), "objective_omission_count": len(omissions), "backend_counts": {backend_name: sum(row["backend"] == backend_name for row in accepted) for backend_name in (QWEN, VOX, FISH, INDEX)}, "all_retained_candidates_passed_transcription_gate": all(transcription_passed(row) for row in accepted), "production_routing_changed": False, "project_audio_changed": False, "voice_config_changed": False})
    build_review(output=output, modes=modes, candidates=accepted, omissions=omissions)
    print(json.dumps({"round_id": ROUND_ID, "output": str(output), "review": str(output / "review" / "index.html"), "mode_count": len(modes), "planned_candidate_count": len(specs), "candidate_count": len(accepted), "objective_omission_count": len(omissions), "backend_counts": {backend_name: sum(row["backend"] == backend_name for row in accepted) for backend_name in (QWEN, VOX, FISH, INDEX)}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
