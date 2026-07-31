#!/usr/bin/env python3
"""Build a blind identity-salvage round for unresolved Original Sin roles.

This round does not repeat source-separation inference. It derives new bounded
candidates from previously extracted same-source variants by deterministic
alignment, robust waveform consensus, low-quantile spectral consensus,
de-echoing, spectral gating, and conservative transient suppression.

The output is research-only. It does not change project audio, chunks.json,
voice_config.json, or production routing.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import shutil
import sys
from typing import Any, Mapping

import numpy as np
from scipy.signal import (
    butter,
    correlate,
    correlation_lags,
    istft,
    medfilt,
    resample_poly,
    sosfilt,
    stft,
)
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from model_registry import model_cache_status  # noqa: E402
from transcription_evaluator import (  # noqa: E402
    evaluate_transcriptions,
    normalized_words,
)


ROUND_ID = "alexandria_original_sin_overlap_identity_salvage_round_v6"
DEFAULT_PROJECT = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665"
)
DEFAULT_WORKFLOW = (
    DEFAULT_PROJECT
    / "external_workflows"
    / "big_finish_overlap_reference_v1"
)
DEFAULT_OUTPUT = DEFAULT_WORKFLOW / "overlap_identity_salvage_round_v6"
TARGET_RATE = 44100
MAX_ACCEPTABLE_WER = 0.25


CHARACTER_SPECS: tuple[dict[str, Any], ...] = (
    {
        "character_id": "doc_dantalion",
        "character": "Doc Dantalion",
        "book_speaker": "DOC DANTALION",
        "answer_key": "reference_cleanliness_round_v1/private/answer-key.json",
        "candidate_ids": [
            "051d2d8f04a015fa",
            "521502618d493dcb",
            "1b3f56062121451c",
        ],
        "context_candidate_id": "051d2d8f04a015fa",
        "transcript": (
            "Then, my friend, you didn't pay enough. Let me guess, you want "
            "the memories back."
        ),
        "known_blocker": "echo and room/scene contamination",
        "variants": (
            "robust_waveform_consensus",
            "low_quantile_spectral_consensus",
            "deecho_spectral_consensus",
        ),
    },
    {
        "character_id": "homeless_forsaken",
        "character": "Homeless Forsaken",
        "book_speaker": "HOMELESS FORSAKEN",
        "answer_key": "reference_repair_round_v2/private/answer-key.json",
        "candidate_ids": [
            "3932d1942197febd",
            "5b4e9db29072b2f8",
            "b264207697acb6ee",
        ],
        "context_candidate_id": "3932d1942197febd",
        "transcript": "I can't. I'm dying.",
        "known_blocker": "footsteps, background noise, and boundary damage",
        "variants": (
            "robust_waveform_consensus",
            "low_quantile_spectral_consensus",
            "spectral_gate_transient_control",
        ),
    },
    {
        "character_id": "shythe_shahid",
        "character": "Shythe Shahid",
        "book_speaker": "SHYTHE SHAHID",
        "answer_key": "reference_final_repair_round_v4/private/answer-key.json",
        "candidate_ids": [
            "d88d8f587dc2455a",
            "ea469b0ce3611aea",
            "52d397de429074f9",
        ],
        "context_candidate_id": "d88d8f587dc2455a",
        "transcript": "I'm Shahid Shahid. This is Empire Today.",
        "known_blocker": "residual music and broadcast-scene contamination",
        "variants": (
            "robust_waveform_consensus",
            "low_quantile_spectral_consensus",
            "music_suppressed_consensus",
        ),
    },
)


TRANSCRIPTION_ALIASES = {
    "doc_dantalion": {
        "really": "merely",
        "have": "will",
        "one": "will",
    },
    "shythe_shahid": {
        "shait": "shahid",
        "shaheed": "shahid",
        "shite": "shahid",
    },
}


class IdentitySalvageError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise IdentitySalvageError(f"{label} is missing: {path}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IdentitySalvageError(f"{label} is invalid: {exc}") from exc


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audio_record(path: Path) -> dict[str, Any]:
    info = sf.info(str(path))
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "sample_rate": int(info.samplerate),
        "frames": int(info.frames),
        "duration_seconds": float(info.duration),
        "format": info.format,
    }


def candidate_identifier(
    character_id: str,
    treatment: str,
    source_hashes: list[str],
) -> str:
    payload = ":".join((ROUND_ID, character_id, treatment, *source_hashes))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def source_rows(
    workflow: Path,
    spec: Mapping[str, Any],
) -> list[dict[str, Any]]:
    answer = read_json(workflow / str(spec["answer_key"]), "Source answer key")
    candidates = answer.get("candidates")
    if not isinstance(candidates, Mapping):
        raise IdentitySalvageError("Source answer key has no candidates.")
    rows: list[dict[str, Any]] = []
    for candidate_id in spec["candidate_ids"]:
        value = candidates.get(candidate_id)
        if not isinstance(value, Mapping):
            raise IdentitySalvageError(f"Source candidate is missing: {candidate_id}")
        path = Path(str(value.get("path") or "")).expanduser().resolve()
        expected = str((value.get("metrics") or {}).get("sha256") or "")
        if not path.is_file() or sha256_file(path) != expected:
            raise IdentitySalvageError(
                f"Source candidate is missing or changed: {candidate_id}"
            )
        rows.append(
            {
                "candidate_id": candidate_id,
                "path": path,
                "sha256": expected,
                "treatment": value.get("treatment") or value.get("variant"),
                "automatic_transcript": value.get("automatic_transcript"),
            }
        )
    return rows


def read_mono(path: Path, target_rate: int = TARGET_RATE) -> np.ndarray:
    audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = np.mean(audio, axis=1, dtype=np.float32)
    if int(rate) != int(target_rate):
        divisor = int(np.gcd(int(rate), int(target_rate)))
        mono = resample_poly(
            mono,
            int(target_rate) // divisor,
            int(rate) // divisor,
        ).astype(np.float32)
    if mono.size == 0 or not np.all(np.isfinite(mono)):
        raise IdentitySalvageError(f"Unreadable source audio: {path}")
    return mono


def alignment_lag(reference: np.ndarray, candidate: np.ndarray, rate: int) -> tuple[int, float]:
    max_shift = int(round(0.18 * rate))
    left = reference - np.mean(reference)
    right = candidate - np.mean(candidate)
    values = correlate(right, left, mode="full", method="fft")
    lags = correlation_lags(right.size, left.size, mode="full")
    allowed = np.abs(lags) <= max_shift
    if not np.any(allowed):
        return 0, 1.0
    allowed_values = values[allowed]
    allowed_lags = lags[allowed]
    index = int(np.argmax(np.abs(allowed_values)))
    correlation = float(allowed_values[index])
    return int(allowed_lags[index]), (-1.0 if correlation < 0.0 else 1.0)


def aligned_sources(paths: list[Path]) -> tuple[list[np.ndarray], dict[str, Any]]:
    arrays = [read_mono(path) for path in paths]
    reference = arrays[0]
    offsets = [0]
    polarities = [1.0]
    for candidate in arrays[1:]:
        lag, polarity = alignment_lag(reference, candidate, TARGET_RATE)
        offsets.append(lag)
        polarities.append(polarity)

    starts = [max(0, -lag) for lag in offsets]
    ends = [
        min(reference.size, array.size - lag)
        for array, lag in zip(arrays, offsets)
    ]
    common_start = max(starts)
    common_end = min(ends)
    if common_end - common_start < int(0.75 * TARGET_RATE):
        raise IdentitySalvageError("Same-source variants do not have a safe common window.")
    aligned = []
    for array, lag, polarity in zip(arrays, offsets, polarities):
        source_start = common_start + lag
        source_end = common_end + lag
        value = np.asarray(array[source_start:source_end] * polarity, dtype=np.float32)
        aligned.append(value)

    rms_values = [float(np.sqrt(np.mean(value * value) + 1e-12)) for value in aligned]
    target_rms = float(np.median(rms_values))
    normalized = [
        np.asarray(value * np.clip(target_rms / max(rms, 1e-8), 0.5, 2.0), dtype=np.float32)
        for value, rms in zip(aligned, rms_values)
    ]
    return normalized, {
        "sample_rate": TARGET_RATE,
        "offset_samples": offsets,
        "polarity": polarities,
        "common_start_sample": common_start,
        "common_end_sample": common_end,
        "source_rms": rms_values,
        "normalized_target_rms": target_rms,
    }


def peak_limit(audio: np.ndarray, target_dbfs: float = -1.0) -> np.ndarray:
    value = np.asarray(audio, dtype=np.float32)
    peak = float(np.max(np.abs(value))) if value.size else 0.0
    target = 10.0 ** (target_dbfs / 20.0)
    if peak > target:
        value = value * (target / peak)
    return np.asarray(value, dtype=np.float32)


def waveform_consensus(arrays: list[np.ndarray]) -> np.ndarray:
    return peak_limit(np.median(np.stack(arrays, axis=0), axis=0))


def spectral_consensus(
    arrays: list[np.ndarray],
    *,
    quantile: float,
    reference_index: int = 1,
) -> tuple[np.ndarray, dict[str, Any]]:
    spectra = []
    frequencies = None
    times = None
    for audio in arrays:
        frequencies, times, spectrum = stft(
            audio,
            fs=TARGET_RATE,
            nperseg=1024,
            noverlap=768,
            boundary="zeros",
        )
        spectra.append(spectrum)
    magnitudes = np.stack([np.abs(value) for value in spectra], axis=0)
    consensus = np.quantile(magnitudes, quantile, axis=0)
    phase = np.exp(1j * np.angle(spectra[min(reference_index, len(spectra) - 1)]))
    _, output = istft(
        consensus * phase,
        fs=TARGET_RATE,
        nperseg=1024,
        noverlap=768,
        input_onesided=True,
        boundary=True,
    )
    output = output[: arrays[0].size]
    return peak_limit(output), {
        "stft_window": 1024,
        "stft_overlap": 768,
        "magnitude_quantile": quantile,
        "phase_reference_index": reference_index,
    }


def spectral_gate(
    audio: np.ndarray,
    *,
    noise_quantile: float = 0.18,
    subtraction: float = 1.15,
    floor: float = 0.12,
) -> tuple[np.ndarray, dict[str, Any]]:
    _, _, spectrum = stft(
        audio,
        fs=TARGET_RATE,
        nperseg=1024,
        noverlap=768,
        boundary="zeros",
    )
    magnitude = np.abs(spectrum)
    noise = np.quantile(magnitude, noise_quantile, axis=1, keepdims=True)
    gain = np.clip(
        (magnitude - subtraction * noise) / np.maximum(magnitude, 1e-8),
        floor,
        1.0,
    )
    _, output = istft(
        spectrum * gain,
        fs=TARGET_RATE,
        nperseg=1024,
        noverlap=768,
        input_onesided=True,
        boundary=True,
    )
    return peak_limit(output[: audio.size]), {
        "noise_quantile": noise_quantile,
        "subtraction": subtraction,
        "gain_floor": floor,
    }


def deecho(audio: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    # Estimate the strongest short room reflection after removing the slow
    # envelope. The subtraction is deliberately capped to avoid hollowing the
    # identity signal.
    highpass = butter(2, 120.0 / (TARGET_RATE / 2.0), btype="highpass", output="sos")
    filtered = sosfilt(highpass, audio)
    minimum = int(round(0.018 * TARGET_RATE))
    maximum = int(round(0.095 * TARGET_RATE))
    correlations = []
    denominator = float(np.dot(filtered, filtered) + 1e-9)
    for lag in range(minimum, maximum + 1):
        correlations.append(float(np.dot(filtered[lag:], filtered[:-lag]) / denominator))
    lag = minimum + int(np.argmax(np.abs(correlations)))
    strength = float(np.clip(abs(correlations[lag - minimum]), 0.08, 0.32))
    output = np.array(audio, copy=True)
    output[lag:] -= strength * audio[:-lag]
    return peak_limit(output), {
        "estimated_delay_samples": lag,
        "estimated_delay_ms": 1000.0 * lag / TARGET_RATE,
        "subtraction_strength": strength,
    }


def suppress_low_frequency_transients(audio: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    lowpass = butter(3, 280.0 / (TARGET_RATE / 2.0), btype="lowpass", output="sos")
    low = sosfilt(lowpass, audio).astype(np.float32)
    high = np.asarray(audio - low, dtype=np.float32)
    envelope = np.abs(low)
    kernel = int(round(0.09 * TARGET_RATE)) | 1
    local = medfilt(envelope, kernel_size=kernel)
    threshold = np.maximum(3.0 * local, 0.015)
    gain = np.minimum(1.0, threshold / np.maximum(envelope, 1e-8))
    output = high + low * gain
    return peak_limit(output), {
        "lowpass_hz": 280.0,
        "median_window_ms": 90.0,
        "transient_ratio_cap": 3.0,
    }


def build_variant(
    treatment: str,
    arrays: list[np.ndarray],
) -> tuple[np.ndarray, dict[str, Any]]:
    if treatment == "robust_waveform_consensus":
        return waveform_consensus(arrays), {"operation": treatment}
    if treatment == "low_quantile_spectral_consensus":
        output, receipt = spectral_consensus(arrays, quantile=0.35)
        return output, {"operation": treatment, **receipt}
    if treatment == "deecho_spectral_consensus":
        consensus, first = spectral_consensus(arrays, quantile=0.40)
        output, second = deecho(consensus)
        return output, {"operation": treatment, "spectral": first, "deecho": second}
    if treatment == "spectral_gate_transient_control":
        consensus = waveform_consensus(arrays)
        gated, first = spectral_gate(consensus, subtraction=1.10, floor=0.15)
        output, second = suppress_low_frequency_transients(gated)
        return output, {"operation": treatment, "spectral_gate": first, "transient_control": second}
    if treatment == "music_suppressed_consensus":
        consensus, first = spectral_consensus(arrays, quantile=0.25)
        output, second = spectral_gate(
            consensus,
            noise_quantile=0.22,
            subtraction=1.25,
            floor=0.10,
        )
        return output, {"operation": treatment, "spectral_consensus": first, "spectral_gate": second}
    raise IdentitySalvageError(f"Unknown salvage treatment: {treatment}")


def adjusted_wer(character_id: str, expected: str, transcript: str) -> float:
    aliases = TRANSCRIPTION_ALIASES.get(character_id, {})
    reference = normalized_words(expected)
    heard: list[str] = []
    for word in normalized_words(transcript):
        heard.extend(str(aliases.get(word, word)).split())
    if not reference:
        return 0.0 if not heard else 1.0
    previous = list(range(len(heard) + 1))
    for row_index, expected_word in enumerate(reference, start=1):
        current = [row_index]
        for column, heard_word in enumerate(heard, start=1):
            current.append(
                min(
                    previous[column] + 1,
                    current[column - 1] + 1,
                    previous[column - 1] + (expected_word != heard_word),
                )
            )
        previous = current
    return previous[-1] / len(reference)


def attach_transcriptions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    evaluation = evaluate_transcriptions(
        {
            "model_status": model_cache_status("mlx_whisper_base"),
            "outputs": [
                {
                    "sample_id": row["candidate_id"],
                    "path": row["audio_path"],
                    "text": row["transcript"],
                }
                for row in rows
            ],
        }
    )
    measurements = evaluation.get("measurements") or {}
    for row in rows:
        result = copy.deepcopy(measurements.get(row["candidate_id"]) or {})
        heard = result.get("transcript")
        if isinstance(heard, str):
            result["raw_word_error_rate"] = result.get("word_error_rate")
            result["word_error_rate"] = adjusted_wer(
                str(row["character_id"]),
                str(row["transcript"]),
                heard,
            )
            result["alias_policy_applied"] = bool(
                TRANSCRIPTION_ALIASES.get(str(row["character_id"]))
            )
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


def safe_copy(source: Path, destination: Path, expected_sha256: str) -> None:
    if not source.is_file() or sha256_file(source) != expected_sha256:
        raise IdentitySalvageError(f"Context source is missing or changed: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    if sha256_file(destination) != expected_sha256:
        raise IdentitySalvageError(f"Context copy changed: {destination}")


def build_review(
    output: Path,
    characters: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    omissions: list[dict[str, Any]],
) -> None:
    review = output / "review"
    review.mkdir(parents=True, exist_ok=True)
    by_character: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        by_character.setdefault(str(row["character_id"]), []).append(row)
    randomizer = random.Random(2026080106)
    public_characters = []
    for character in characters:
        rows = list(by_character.get(str(character["character_id"]), []))
        randomizer.shuffle(rows)
        public_characters.append(
            {
                "character_id": character["character_id"],
                "character": character["character"],
                "transcript": character["transcript"],
                "known_blocker": character["known_blocker"],
                "context_audio": character["context_audio"],
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
        "characters": public_characters,
        "objective_omission_count": len(omissions),
    }
    (review / "data.js").write_text(
        "window.ALEXANDRIA_IDENTITY_SALVAGE = "
        + json.dumps(public, indent=2, ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )
    (review / "index.html").write_text(
        """<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Original Sin identity salvage</title><link rel='icon' href='data:,'><style>:root{font-family:Inter,system-ui,sans-serif;color:#29251f;background:#f1eee7}*{box-sizing:border-box}body{margin:0}header,main{max-width:1050px;margin:auto;padding:28px 20px}header{border-bottom:1px solid #d4ccbf}h1,h2,h3{font-family:Georgia,serif}.character{margin:42px 0}.candidate,.context{background:#fffdf8;border:1px solid #d5cdbf;border-radius:10px;padding:18px;margin:16px 0}.context{background:#fff4d6}.meta{font-size:13px;color:#6d655b}.ratings{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:12px 0}label{display:grid;gap:5px;font-size:13px;font-weight:650}select,textarea{font:inherit;padding:8px;border:1px solid #b9afa2;border-radius:5px;background:white}textarea{width:100%;min-height:72px}.checks,.decision{display:flex;gap:14px;flex-wrap:wrap;margin:12px 0}.checks label,.decision label{display:flex;align-items:center;gap:7px;border:1px solid #b9afa2;border-radius:6px;padding:8px;background:white}audio{width:100%}button{padding:10px 14px;border:0;border-radius:6px;background:#315c55;color:white;font-weight:700}@media(max-width:820px){.ratings{grid-template-columns:1fr 1fr}}@media(max-width:480px){.ratings{grid-template-columns:1fr}}</style></head><body><header><p>Alexandria source-quality gate</p><h1>Original Sin unresolved identity salvage</h1><p>Listen to the contaminated source-context extraction only to identify the performer. It is not a promotable option. Then score the blind cleaned candidates. Written notes override pass. A candidate cannot pass with missing words, identity damage, audible music, echo, footsteps, adjacent voices, or separator artifacts.</p><button id='export'>Export review</button> <span id='progress'></span></header><main id='app'></main><script src='data.js'></script><script src='app.js'></script></body></html>""",
        encoding="utf-8",
    )
    (review / "app.js").write_text(
        """(()=>{'use strict';const d=window.ALEXANDRIA_IDENTITY_SALVAGE,key='alexandria-identity-salvage:'+d.round_id;let saved={};try{saved=JSON.parse(localStorage.getItem(key)||'{}')}catch(_){saved={}}const app=document.querySelector('#app'),scale='<option value="">—</option>'+[1,2,3,4,5].map(v=>`<option>${v}</option>`).join('');for(const m of d.characters){const section=document.createElement('section');section.className='character';section.innerHTML=`<h2>${m.character}</h2><p><strong>Exact transcript:</strong> ${m.transcript}</p><p class="meta"><strong>Known blocker:</strong> ${m.known_blocker}</p><article class="context"><strong>Source-context extraction — identity reference only, not eligible</strong><audio controls preload="none" src="${m.context_audio}"></audio></article><h3>Blind salvage candidates</h3>`;app.appendChild(section);if(!m.candidates.length){section.insertAdjacentHTML('beforeend','<p>No candidate survived objective text screening.</p>');continue}for(const c of m.candidates){const el=document.createElement('article');el.className='candidate';el.innerHTML=`<p class="meta">Candidate ${c.display_id}</p><audio controls preload="none" src="${c.audio}"></audio><div class="ratings"><label>Identity preservation<select data-id="${c.candidate_id}" data-name="identity">${scale}</select></label><label>Cleanliness<select data-id="${c.candidate_id}" data-name="cleanliness">${scale}</select></label><label>Naturalness<select data-id="${c.candidate_id}" data-name="naturalness">${scale}</select></label><label>Intelligibility<select data-id="${c.candidate_id}" data-name="intelligibility">${scale}</select></label><label>Contamination removal<select data-id="${c.candidate_id}" data-name="contamination">${scale}</select></label></div><div class="checks"><label><input type="radio" name="complete-${c.candidate_id}" value="complete" data-id="${c.candidate_id}" data-name="completeness">Entire line present</label><label><input type="radio" name="complete-${c.candidate_id}" value="incomplete" data-id="${c.candidate_id}" data-name="completeness">Cut off / incomplete</label></div><div class="decision"><label><input type="radio" name="decision-${c.candidate_id}" value="pass" data-id="${c.candidate_id}" data-name="decision">Pass as identity source</label><label><input type="radio" name="decision-${c.candidate_id}" value="fail" data-id="${c.candidate_id}" data-name="decision">Fail</label></div><label>Notes<textarea data-id="${c.candidate_id}" data-name="notes"></textarea></label>`;section.appendChild(el)}}for(const e of document.querySelectorAll('[data-id]')){const x=saved[e.dataset.id]||{},v=x[e.dataset.name];if(e.type==='radio')e.checked=v===e.value;else if(v!=null)e.value=v;e.addEventListener(e.tagName==='TEXTAREA'?'input':'change',event=>{const t=event.target,id=t.dataset.id,n=t.dataset.name;saved[id]=saved[id]||{};saved[id][n]=t.type==='radio'?t.value:t.value;localStorage.setItem(key,JSON.stringify(saved));progress()})}function progress(){const total=d.characters.reduce((n,m)=>n+m.candidates.length,0),done=Object.values(saved).filter(x=>x.identity&&x.cleanliness&&x.naturalness&&x.intelligibility&&x.contamination&&x.completeness&&x.decision).length;document.querySelector('#progress').textContent=`${done} of ${total} reviewed`}document.querySelector('#export').onclick=()=>{const payload={schema_version:1,round_id:d.round_id,exported_at:new Date().toISOString(),results:saved};const a=document.createElement('a'),b=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'});a.href=URL.createObjectURL(b);a.download=d.round_id+'-tristan.json';a.click()};progress()})();""",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow-root", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    workflow = args.workflow_root.expanduser().resolve()
    output = args.output_root.expanduser().resolve()
    if output.exists():
        if not args.replace:
            raise IdentitySalvageError(
                f"Output exists; pass --replace to rebuild: {output}"
            )
        shutil.rmtree(output)
    output.mkdir(parents=True)

    candidates: list[dict[str, Any]] = []
    character_records: list[dict[str, Any]] = []
    for spec in CHARACTER_SPECS:
        rows = source_rows(workflow, spec)
        arrays, alignment = aligned_sources([row["path"] for row in rows])
        source_hashes = [str(row["sha256"]) for row in rows]

        context = next(
            row for row in rows if row["candidate_id"] == spec["context_candidate_id"]
        )
        context_relative = Path("context") / f"{spec['character_id']}{context['path'].suffix}"
        safe_copy(context["path"], output / context_relative, context["sha256"])
        character_records.append(
            {
                "character_id": spec["character_id"],
                "character": spec["character"],
                "book_speaker": spec["book_speaker"],
                "transcript": spec["transcript"],
                "known_blocker": spec["known_blocker"],
                "context_audio": "../" + context_relative.as_posix(),
                "context_sha256": context["sha256"],
                "source_candidates": [
                    {
                        **row,
                        "path": str(row["path"]),
                    }
                    for row in rows
                ],
                "alignment": alignment,
            }
        )
        for treatment in spec["variants"]:
            identifier = candidate_identifier(
                str(spec["character_id"]),
                str(treatment),
                source_hashes,
            )
            path = output / "private" / "audio" / f"{identifier}.wav"
            audio, processing = build_variant(str(treatment), arrays)
            path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(str(path), audio, TARGET_RATE, subtype="PCM_16")
            candidates.append(
                {
                    "candidate_id": identifier,
                    "character_id": spec["character_id"],
                    "character": spec["character"],
                    "book_speaker": spec["book_speaker"],
                    "transcript": spec["transcript"],
                    "treatment": treatment,
                    "source_candidate_ids": list(spec["candidate_ids"]),
                    "source_audio_sha256": source_hashes,
                    "alignment": alignment,
                    "processing": processing,
                    "audio_path": str(path),
                    "audio_relative": path.relative_to(output).as_posix(),
                    "audio": audio_record(path),
                }
            )

    evaluation = attach_transcriptions(candidates)
    accepted = [row for row in candidates if transcription_passed(row)]
    omissions = [
        {
            "candidate_id": row["candidate_id"],
            "character_id": row["character_id"],
            "treatment": row["treatment"],
            "reason": "objective_transcription_gate_failed",
            "transcription": row.get("transcription"),
        }
        for row in candidates
        if not transcription_passed(row)
    ]
    answer = {
        "schema_version": 1,
        "round_id": ROUND_ID,
        "generated_at_utc": utc_now(),
        "character_count": len(character_records),
        "planned_candidate_count": len(candidates),
        "candidate_count": len(accepted),
        "objective_omission_count": len(omissions),
        "characters": character_records,
        "candidates": {row["candidate_id"]: row for row in accepted},
        "omissions": omissions,
        "transcription_evaluation": evaluation,
        "review_contract": {
            "source_context_not_eligible": True,
            "candidate_methods_hidden": True,
            "entire_line_required": True,
            "identity_and_contamination_scores_required": True,
            "written_notes_override_pass": True,
        },
        "repeated_source_separation_inference": False,
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
            "character_count": len(character_records),
            "planned_candidate_count": len(candidates),
            "candidate_count": len(accepted),
            "objective_omission_count": len(omissions),
            "repeated_source_separation_inference": False,
            "production_routing_changed": False,
            "project_audio_changed": False,
            "voice_config_changed": False,
        },
    )
    build_review(output, character_records, accepted, omissions)
    print(
        json.dumps(
            {
                "round_id": ROUND_ID,
                "output": str(output),
                "review": str(output / "review" / "index.html"),
                "character_count": len(character_records),
                "planned_candidate_count": len(candidates),
                "candidate_count": len(accepted),
                "objective_omission_count": len(omissions),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
