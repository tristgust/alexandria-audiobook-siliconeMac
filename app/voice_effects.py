from __future__ import annotations

import os
from pathlib import Path
import tempfile
from typing import Any

import numpy as np
from scipy.signal import butter, sosfilt
import soundfile as sf

from experimental_prompt_routing import sha256_file


VOICE_EFFECT_SCHEMA_VERSION = 1
ALLOWED_VOICE_EFFECT_CHAINS = frozenset(
    {
        "powerless_alien_modulation_v1",
        "under_sergeant_intercom_v1",
        "securitybot_synthetic_v1",
        "computer_modulation_v1",
    }
)


class VoiceEffectError(RuntimeError):
    pass


def validate_voice_effect_chain(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise VoiceEffectError("Voice effect chain must be non-empty text or null.")
    chain = value.strip()
    if chain not in ALLOWED_VOICE_EFFECT_CHAINS:
        raise VoiceEffectError(f"Unsupported Voice effect chain: {chain!r}.")
    return chain


def _bandpass(audio: np.ndarray, rate: int, low: float, high: float) -> np.ndarray:
    nyquist = max(1.0, rate / 2.0)
    low_value = max(20.0, min(low, nyquist * 0.8)) / nyquist
    high_value = max(low + 20.0, min(high, nyquist * 0.95)) / nyquist
    sos = butter(3, [low_value, high_value], btype="bandpass", output="sos")
    return np.asarray(sosfilt(sos, audio), dtype=np.float32)


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


def _process(
    audio: np.ndarray,
    rate: int,
    chain: str,
) -> tuple[np.ndarray, dict[str, Any]]:
    time_axis = np.arange(audio.size, dtype=np.float32) / float(rate)
    if chain == "powerless_alien_modulation_v1":
        output = _bandpass(audio, rate, 170.0, 5200.0)
        output = _delayed_mix(output, rate, 10.0, 0.22)
        output *= 1.0 + 0.10 * np.sin(2.0 * np.pi * 6.5 * time_axis)
        output = np.tanh(output * 1.18) / np.tanh(1.18)
        parameters = {
            "bandpass_hz": [170.0, 5200.0],
            "chorus_delay_ms": 10.0,
            "chorus_mix": 0.22,
            "amplitude_modulation_hz": 6.5,
            "amplitude_modulation_depth": 0.10,
        }
    elif chain == "under_sergeant_intercom_v1":
        output = _bandpass(audio, rate, 300.0, 3600.0)
        output = np.tanh(output * 1.30) / np.tanh(1.30)
        parameters = {
            "bandpass_hz": [300.0, 3600.0],
            "soft_saturation": 1.30,
        }
    elif chain == "securitybot_synthetic_v1":
        output = _bandpass(audio, rate, 280.0, 4600.0)
        output *= 1.0 + 0.06 * np.sin(2.0 * np.pi * 18.0 * time_axis)
        parameters = {
            "bandpass_hz": [280.0, 4600.0],
            "amplitude_modulation_hz": 18.0,
            "amplitude_modulation_depth": 0.06,
        }
    elif chain == "computer_modulation_v1":
        output = _bandpass(audio, rate, 260.0, 4800.0)
        output = _delayed_mix(output, rate, 5.0, 0.16)
        output *= 1.0 + 0.16 * np.sin(2.0 * np.pi * 31.0 * time_axis)
        output = np.tanh(output * 1.12) / np.tanh(1.12)
        parameters = {
            "bandpass_hz": [260.0, 4800.0],
            "chorus_delay_ms": 5.0,
            "chorus_mix": 0.16,
            "amplitude_modulation_hz": 31.0,
            "amplitude_modulation_depth": 0.16,
        }
    else:  # pragma: no cover - validate_voice_effect_chain closes this path.
        raise VoiceEffectError(f"Unsupported Voice effect chain: {chain!r}.")
    peak = float(np.max(np.abs(output))) if output.size else 0.0
    target_peak = 10.0 ** (-1.0 / 20.0)
    if peak > target_peak:
        output = output * (target_peak / peak)
    return np.asarray(output, dtype=np.float32), parameters


def apply_voice_effect_chain(
    audio_path: str | Path,
    effect_chain: str | None,
) -> dict[str, Any] | None:
    chain = validate_voice_effect_chain(effect_chain)
    if chain is None:
        return None
    path = Path(audio_path).expanduser().resolve()
    if not path.is_file():
        raise VoiceEffectError(f"Voice effect input is missing: {path}")
    source_sha256 = sha256_file(path)
    try:
        audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    except Exception as exc:
        raise VoiceEffectError(f"Voice effect input is unreadable: {exc}") from exc
    mono = np.mean(audio, axis=1, dtype=np.float32)
    if mono.size == 0 or not np.all(np.isfinite(mono)):
        raise VoiceEffectError("Voice effect input contains no valid audio.")
    output, parameters = _process(mono, int(rate), chain)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".effect.tmp.wav",
        dir=path.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        sf.write(str(temporary), output, int(rate), subtype="PCM_16")
        if temporary.stat().st_size < 512:
            raise VoiceEffectError("Voice effect output is empty or invalid.")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "schema_version": VOICE_EFFECT_SCHEMA_VERSION,
        "chain": chain,
        "parameters": parameters,
        "source_sha256": source_sha256,
        "output_sha256": sha256_file(path),
    }
