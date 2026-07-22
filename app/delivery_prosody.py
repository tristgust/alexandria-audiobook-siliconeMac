from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


DELIVERY_PROSODY_VERSION = 1


class DeliveryProsodyError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeliveryProsodyProfile:
    version: int = DELIVERY_PROSODY_VERSION
    tempo: float = 1.0
    volume: float = 1.0
    pause_ms: int = 0
    pause_anchor: str | None = None
    reasons: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return (
            abs(self.tempo - 1.0) > 0.001
            or abs(self.volume - 1.0) > 0.001
            or self.pause_ms > 0
        )

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["reasons"] = list(self.reasons)
        return value


_STRONG_SLOW = (
    "very slow",
    "extremely slow",
    "drawn out",
    "funereal",
)
_SLOW = (
    "slow",
    "grieving",
    "grief",
    "mournful",
    "sorrowful",
    "tired",
    "weary",
    "hesitant",
    "fragile",
)
_MEASURED = (
    "measured",
    "deliberate",
    "unhurried",
    "careful pace",
)
_STRONG_FAST = (
    "very fast",
    "extremely fast",
    "frantic",
    "rapid fire",
)
_FAST = (
    "fast",
    "quick",
    "urgent",
    "rushed",
    "clipped",
    "brisk",
)
_VERY_SOFT = (
    "whisper",
    "very soft",
    "barely audible",
    "hushed",
)
_SOFT = (
    "soft",
    "quiet",
    "low energy",
    "fragile",
    "grieving",
    "grief",
    "tired",
    "weary",
    "gentle",
)
_VERY_LOUD = (
    "shout",
    "yell",
    "scream",
    "very loud",
)
_LOUD = (
    "loud",
    "forceful",
    "anger",
    "angry",
    "intense",
    "commanding",
    "projected",
)


def _contains_any(value: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in value for phrase in phrases)


def build_delivery_prosody_profile(instruction: str) -> DeliveryProsodyProfile:
    text = " ".join(str(instruction or "").casefold().split())
    reasons: list[str] = []
    tempo = 1.0
    volume = 1.0

    if _contains_any(text, _STRONG_SLOW):
        tempo = 0.78
        reasons.append("strong_slow_cue")
    elif _contains_any(text, _SLOW):
        tempo = 0.86
        reasons.append("slow_cue")
    elif _contains_any(text, _MEASURED):
        tempo = 0.93
        reasons.append("measured_cue")

    if _contains_any(text, _STRONG_FAST):
        tempo = 1.22
        reasons.append("strong_fast_cue")
    elif _contains_any(text, _FAST):
        tempo = max(tempo, 1.12)
        reasons.append("fast_cue")

    if _contains_any(text, _VERY_SOFT):
        volume = 0.62
        reasons.append("very_soft_cue")
    elif _contains_any(text, _SOFT):
        volume = 0.78
        reasons.append("soft_cue")

    if _contains_any(text, _VERY_LOUD):
        volume = 1.35
        reasons.append("very_loud_cue")
    elif _contains_any(text, _LOUD):
        volume = max(volume, 1.18)
        reasons.append("loud_cue")

    pause_ms = 0
    if "long pause" in text:
        pause_ms = 480
        reasons.append("long_pause_cue")
    elif "brief pause" in text or "short pause" in text:
        pause_ms = 180
        reasons.append("brief_pause_cue")
    elif re.search(r"\bpause\b", text):
        pause_ms = 300
        reasons.append("pause_cue")

    anchor_match = re.search(
        r"\bpause\s+(?:briefly\s+|long\s+)?after\s+['\"]?([a-z0-9'-]+)",
        text,
    )
    pause_anchor = anchor_match.group(1) if anchor_match else None

    return DeliveryProsodyProfile(
        tempo=round(tempo, 3),
        volume=round(volume, 3),
        pause_ms=pause_ms,
        pause_anchor=pause_anchor,
        reasons=tuple(dict.fromkeys(reasons)),
    )


def _pause_fraction(text: str, profile: DeliveryProsodyProfile) -> float | None:
    if profile.pause_ms <= 0:
        return None
    normalized = str(text or "")
    if profile.pause_anchor:
        matches = list(
            re.finditer(
                rf"\b{re.escape(profile.pause_anchor)}\b",
                normalized,
                flags=re.IGNORECASE,
            )
        )
        if matches:
            return min(0.94, max(0.06, matches[-1].end() / max(1, len(normalized))))
    punctuation = list(re.finditer(r"[,;:—-]", normalized))
    if punctuation:
        middle = min(
            punctuation,
            key=lambda match: abs(match.start() / max(1, len(normalized)) - 0.5),
        )
        return min(0.94, max(0.06, middle.end() / max(1, len(normalized))))
    return 0.5


def _nearest_low_energy_sample(
    audio: np.ndarray,
    *,
    estimated: int,
    sample_rate: int,
) -> int:
    if len(audio) == 0:
        return 0
    radius = max(1, int(sample_rate * 0.35))
    start = max(0, estimated - radius)
    end = min(len(audio), estimated + radius)
    window = max(16, int(sample_rate * 0.02))
    hop = max(8, window // 4)
    best_index = min(max(estimated, 0), len(audio))
    best_energy = float("inf")
    for index in range(start, max(start + 1, end - window + 1), hop):
        segment = audio[index : index + window]
        if len(segment) == 0:
            continue
        energy = float(np.sqrt(np.mean(np.square(segment, dtype=np.float64))))
        if energy < best_energy:
            best_energy = energy
            best_index = index + len(segment) // 2
    return best_index


def apply_delivery_prosody(
    *,
    audio_path: str | Path,
    text: str,
    instruction: str,
) -> dict[str, Any]:
    target = Path(audio_path).expanduser().resolve()
    if not target.is_file():
        raise DeliveryProsodyError(f"Generated audio does not exist: {target}")
    profile = build_delivery_prosody_profile(instruction)
    before = sf.info(target)
    if not profile.active:
        return {
            "applied": False,
            "profile": profile.as_dict(),
            "duration_before_seconds": float(before.duration),
            "duration_after_seconds": float(before.duration),
        }

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None and (
        abs(profile.tempo - 1.0) > 0.001
        or abs(profile.volume - 1.0) > 0.001
    ):
        raise DeliveryProsodyError(
            "FFmpeg is required to enforce instruction-controlled tempo and intensity."
        )

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.stem}.prosody-",
        suffix=".wav",
        dir=str(target.parent),
    )
    os.close(descriptor)
    Path(temporary_name).unlink(missing_ok=True)
    temporary = Path(temporary_name)
    try:
        filters: list[str] = []
        if abs(profile.tempo - 1.0) > 0.001:
            filters.append(f"atempo={profile.tempo:.3f}")
        if abs(profile.volume - 1.0) > 0.001:
            filters.append(f"volume={profile.volume:.3f}")
            if profile.volume > 1.0:
                filters.append("alimiter=limit=0.97")
        if filters:
            completed = subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(target),
                    "-af",
                    ",".join(filters),
                    "-c:a",
                    "pcm_s16le",
                    str(temporary),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            if completed.returncode != 0 or not temporary.is_file():
                raise DeliveryProsodyError(
                    "FFmpeg prosody enforcement failed: "
                    + (completed.stderr.strip() or "no output was produced")
                )
        else:
            shutil.copy2(target, temporary)

        if profile.pause_ms > 0:
            audio, sample_rate = sf.read(
                temporary,
                dtype="float32",
                always_2d=False,
            )
            if audio.ndim > 1:
                channels = audio.shape[1]
                mono = np.mean(audio, axis=1)
            else:
                channels = 1
                mono = audio
            fraction = _pause_fraction(text, profile)
            estimated = int(len(mono) * float(fraction or 0.5))
            insert_at = _nearest_low_energy_sample(
                mono,
                estimated=estimated,
                sample_rate=sample_rate,
            )
            pause_shape = (
                (int(sample_rate * profile.pause_ms / 1000), channels)
                if channels > 1
                else (int(sample_rate * profile.pause_ms / 1000),)
            )
            pause = np.zeros(pause_shape, dtype=np.float32)
            audio = np.concatenate(
                [audio[:insert_at], pause, audio[insert_at:]],
                axis=0,
            )
            sf.write(temporary, audio, sample_rate, subtype="PCM_16")

        after = sf.info(temporary)
        if float(after.duration) <= 0:
            raise DeliveryProsodyError("Prosody enforcement produced empty audio.")
        temporary.replace(target)
        return {
            "applied": True,
            "profile": profile.as_dict(),
            "duration_before_seconds": float(before.duration),
            "duration_after_seconds": float(after.duration),
        }
    finally:
        temporary.unlink(missing_ok=True)
