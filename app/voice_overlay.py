from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

from pydub import AudioSegment


VOICE_OVERLAY_SCHEMA_VERSION = 1


class VoiceOverlayError(ValueError):
    pass


def _number(value: Any, *, field: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise VoiceOverlayError(f"{field} must be a number.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise VoiceOverlayError(f"{field} must be a number.") from exc
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise VoiceOverlayError(
            f"{field} must be between {minimum:g} and {maximum:g}."
        )
    return round(number, 4)


def normalize_voice_overlay(value: Mapping[str, Any] | None) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    direction = " ".join(str(source.get("direction") or "").split()).strip()
    if len(direction) > 2000:
        raise VoiceOverlayError("Voice overlay direction is too long.")
    return {
        "schema_version": VOICE_OVERLAY_SCHEMA_VERSION,
        "direction": direction,
        "pitch_semitones": _number(
            source.get("pitch_semitones", 0),
            field="Pitch shift",
            minimum=-12,
            maximum=12,
        ),
        "pace_percent": _number(
            source.get("pace_percent", 100),
            field="Pace",
            minimum=50,
            maximum=200,
        ),
        "level_db": _number(
            source.get("level_db", 0),
            field="Level",
            minimum=-24,
            maximum=12,
        ),
    }


def voice_overlay_fingerprint(value: Mapping[str, Any] | None) -> str:
    normalized = normalize_voice_overlay(value)
    return hashlib.sha256(
        json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def voice_overlay_is_neutral(value: Mapping[str, Any] | None) -> bool:
    overlay = normalize_voice_overlay(value)
    return (
        not overlay["direction"]
        and overlay["pitch_semitones"] == 0
        and overlay["pace_percent"] == 100
        and overlay["level_db"] == 0
    )


def apply_voice_overlay_instruction(
    instruction: str,
    value: Mapping[str, Any] | None,
) -> str:
    overlay = normalize_voice_overlay(value)
    direction = overlay["direction"]
    base = " ".join(str(instruction or "").split()).strip()
    if not direction:
        return base
    suffix = f"Character-specific Voice direction: {direction}"
    return f"{base} {suffix}".strip()


def apply_voice_overlay_audio(
    path: str | Path,
    value: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if value is None:
        return {}
    overlay = normalize_voice_overlay(value)
    acoustic_neutral = (
        overlay["pitch_semitones"] == 0
        and overlay["pace_percent"] == 100
        and overlay["level_db"] == 0
    )
    if acoustic_neutral:
        return {
            "voice_overlay_applied": False,
            "voice_overlay": overlay,
            "voice_overlay_fingerprint": voice_overlay_fingerprint(overlay),
        }
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        raise VoiceOverlayError("Generated audio is missing before Voice adjustment.")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise VoiceOverlayError("FFmpeg is required for Voice pitch and pace adjustments.")
    try:
        with target.open("rb") as source_file:
            sample_rate = int(AudioSegment.from_file(source_file).frame_rate)
    except Exception as exc:
        raise VoiceOverlayError("Generated audio could not be inspected for Voice adjustment.") from exc

    filters: list[str] = []
    semitones = float(overlay["pitch_semitones"])
    if semitones:
        ratio = 2 ** (semitones / 12.0)
        filters.extend(
            [
                f"asetrate={max(1, int(round(sample_rate * ratio)))}",
                f"aresample={sample_rate}",
                f"atempo={1.0 / ratio:.8f}",
            ]
        )
    pace = float(overlay["pace_percent"]) / 100.0
    if pace != 1.0:
        filters.append(f"atempo={pace:.8f}")
    level = float(overlay["level_db"])
    if level:
        filters.append(f"volume={level:.4f}dB")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.stem}.voice-overlay-",
        suffix=".wav",
        dir=target.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        completed = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(target),
                "-filter:a",
                ",".join(filters),
                "-c:a",
                "pcm_s16le",
                str(temporary),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0 or not temporary.is_file() or temporary.stat().st_size <= 44:
            message = " ".join(completed.stderr.split())[-500:]
            raise VoiceOverlayError(
                "Voice adjustment failed." + (f" {message}" if message else "")
            )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "voice_overlay_applied": True,
        "voice_overlay": overlay,
        "voice_overlay_fingerprint": voice_overlay_fingerprint(overlay),
    }
