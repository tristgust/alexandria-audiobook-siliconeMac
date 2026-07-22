from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import soundfile as sf

from delivery_prosody import DELIVERY_PROSODY_VERSION
from generation_state import fingerprint_value
from mlx_backend import MLXBackend


CONTROLLED_CLONE_BACKEND = "qwen3_instruction_controlled"
CONTROLLED_CLONE_MODEL = MLXBackend.CLONE_MODEL
LEGACY_CONTROLLED_CLONE_BACKEND = "voxcpm2_controlled"
_ALLOWED_AUDIO_SUFFIXES = {".wav", ".flac", ".mp3", ".m4a", ".ogg"}
_PREVIEW_LOCK = threading.RLock()


class ControlledClonePreviewError(RuntimeError):
    pass


class ControlledClonePreviewValidationError(ControlledClonePreviewError):
    pass


class ControlledClonePreviewUnavailableError(ControlledClonePreviewError):
    pass


def _require_text(
    value: Any,
    label: str,
    *,
    max_length: int,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ControlledClonePreviewValidationError(
            f"{label} must be non-empty text."
        )
    text = value.strip()
    if len(text) > max_length:
        raise ControlledClonePreviewValidationError(
            f"{label} must be no longer than {max_length} characters."
        )
    return text


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_reference_audio(
    *,
    root_dir: str | Path,
    ref_audio: str,
) -> tuple[Path, str]:
    root = Path(root_dir).expanduser().resolve()
    text = _require_text(ref_audio, "Reference audio", max_length=1024)
    relative = Path(text)
    if relative.is_absolute() or ".." in relative.parts:
        raise ControlledClonePreviewValidationError(
            "Reference audio must be a project-relative path."
        )
    candidate = (root / relative).resolve()
    allowed_roots = (
        (root / "clone_voices").resolve(),
        (root / "designed_voices").resolve(),
        (root / "voice_training_projects").resolve(),
    )
    if not any(candidate.is_relative_to(allowed) for allowed in allowed_roots):
        raise ControlledClonePreviewValidationError(
            "Reference audio must come from an approved project voice or "
            "voice-training directory."
        )
    if candidate.suffix.casefold() not in _ALLOWED_AUDIO_SUFFIXES:
        raise ControlledClonePreviewValidationError(
            "Reference audio format is unsupported."
        )
    if not candidate.is_file():
        raise ControlledClonePreviewValidationError(
            "Reference audio does not exist."
        )
    return candidate, candidate.relative_to(root).as_posix()


def _validate_settings(
    *,
    temperature: float,
    top_k: int,
    top_p: float,
    repetition_penalty: float,
    max_tokens: int,
) -> tuple[float, int, float, float, int]:
    try:
        resolved_temperature = float(temperature)
        resolved_top_p = float(top_p)
        resolved_repetition = float(repetition_penalty)
    except (TypeError, ValueError) as exc:
        raise ControlledClonePreviewValidationError(
            "Instruction-clone sampling settings must be numeric."
        ) from exc
    if not 0.05 <= resolved_temperature <= 2.0:
        raise ControlledClonePreviewValidationError(
            "temperature must be between 0.05 and 2.0."
        )
    if isinstance(top_k, bool) or not isinstance(top_k, int):
        raise ControlledClonePreviewValidationError(
            "top_k must be an integer."
        )
    if not 1 <= top_k <= 200:
        raise ControlledClonePreviewValidationError(
            "top_k must be between 1 and 200."
        )
    if not 0.05 <= resolved_top_p <= 1.0:
        raise ControlledClonePreviewValidationError(
            "top_p must be between 0.05 and 1.0."
        )
    if not 1.5 <= resolved_repetition <= 3.0:
        raise ControlledClonePreviewValidationError(
            "repetition_penalty must be between 1.5 and 3.0."
        )
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
        raise ControlledClonePreviewValidationError(
            "max_tokens must be an integer."
        )
    if not 128 <= max_tokens <= 4096:
        raise ControlledClonePreviewValidationError(
            "max_tokens must be between 128 and 4096."
        )
    return (
        resolved_temperature,
        top_k,
        resolved_top_p,
        resolved_repetition,
        max_tokens,
    )


def _validate_seed(seed: int | str) -> int:
    if isinstance(seed, bool):
        raise ControlledClonePreviewValidationError(
            "seed must be -1 or a non-negative integer."
        )
    try:
        value = int(seed)
    except (TypeError, ValueError) as exc:
        raise ControlledClonePreviewValidationError(
            "seed must be -1 or a non-negative integer."
        ) from exc
    if value < -1:
        raise ControlledClonePreviewValidationError(
            "seed must be -1 or a non-negative integer."
        )
    return value


def _build_configuration_fingerprint(
    *,
    reference_audio_sha256: str,
    reference_text: str,
    character_style: str,
    temperature: float,
    top_k: int,
    top_p: float,
    repetition_penalty: float,
    max_tokens: int,
    seed: int,
) -> str:
    return fingerprint_value(
        {
            "schema_version": 3,
            "backend": CONTROLLED_CLONE_BACKEND,
            "model": CONTROLLED_CLONE_MODEL,
            "delivery_prosody_version": DELIVERY_PROSODY_VERSION,
            "reference_audio_sha256": reference_audio_sha256,
            "reference_text": reference_text,
            "character_style": character_style,
            "temperature": temperature,
            "top_k": top_k,
            "top_p": top_p,
            "repetition_penalty": repetition_penalty,
            "max_tokens": max_tokens,
            "seed": seed,
        }
    )


def build_controlled_clone_configuration_fingerprint(
    *,
    root_dir: str | Path,
    ref_audio: str,
    ref_text: str,
    character_style: str = "",
    temperature: float = 0.75,
    top_k: int = 50,
    top_p: float = 0.95,
    repetition_penalty: float = 1.5,
    max_tokens: int = 2000,
    seed: int | str = -1,
) -> str:
    reference, _ = _resolve_reference_audio(
        root_dir=root_dir,
        ref_audio=ref_audio,
    )
    reference_text = _require_text(
        ref_text,
        "Reference transcript",
        max_length=12000,
    )
    if not isinstance(character_style, str):
        raise ControlledClonePreviewValidationError(
            "Character style must be text."
        )
    persistent_style = character_style.strip()
    if len(persistent_style) > 1200:
        raise ControlledClonePreviewValidationError(
            "Character style must be no longer than 1200 characters."
        )
    (
        resolved_temperature,
        resolved_top_k,
        resolved_top_p,
        resolved_repetition,
        token_limit,
    ) = _validate_settings(
        temperature=temperature,
        top_k=top_k,
        top_p=top_p,
        repetition_penalty=repetition_penalty,
        max_tokens=max_tokens,
    )
    resolved_seed = _validate_seed(seed)
    return _build_configuration_fingerprint(
        reference_audio_sha256=_sha256_file(reference),
        reference_text=reference_text,
        character_style=persistent_style,
        temperature=resolved_temperature,
        top_k=resolved_top_k,
        top_p=resolved_top_p,
        repetition_penalty=resolved_repetition,
        max_tokens=token_limit,
        seed=resolved_seed,
    )


def build_preview_fingerprint(
    *,
    reference_audio_sha256: str,
    reference_text: str,
    text: str,
    instruct: str,
    character_style: str,
    temperature: float,
    top_k: int,
    top_p: float,
    repetition_penalty: float,
    max_tokens: int,
    seed: int = -1,
) -> str:
    return fingerprint_value(
        {
            "schema_version": 3,
            "backend": CONTROLLED_CLONE_BACKEND,
            "model": CONTROLLED_CLONE_MODEL,
            "delivery_prosody_version": DELIVERY_PROSODY_VERSION,
            "reference_audio_sha256": reference_audio_sha256,
            "reference_text": reference_text,
            "text": text,
            "instruct": instruct,
            "character_style": character_style,
            "temperature": temperature,
            "top_k": top_k,
            "top_p": top_p,
            "repetition_penalty": repetition_penalty,
            "max_tokens": max_tokens,
            "seed": seed,
        }
    )


def generate_controlled_clone_preview(
    *,
    root_dir: str | Path,
    ref_audio: str,
    ref_text: str,
    text: str,
    instruct: str,
    character_style: str = "",
    temperature: float = 0.75,
    top_k: int = 50,
    top_p: float = 0.95,
    repetition_penalty: float = 1.5,
    max_tokens: int = 2000,
    seed: int | str = -1,
    generator: Callable[..., bool],
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    reference, reference_relative = _resolve_reference_audio(
        root_dir=root,
        ref_audio=ref_audio,
    )
    reference_text = _require_text(
        ref_text,
        "Reference transcript",
        max_length=12000,
    )
    preview_text = _require_text(text, "Preview text", max_length=1200)
    delivery = _require_text(
        instruct,
        "Delivery instruction",
        max_length=1200,
    )
    if not isinstance(character_style, str):
        raise ControlledClonePreviewValidationError(
            "Character style must be text."
        )
    persistent_style = character_style.strip()
    if len(persistent_style) > 1200:
        raise ControlledClonePreviewValidationError(
            "Character style must be no longer than 1200 characters."
        )
    (
        resolved_temperature,
        resolved_top_k,
        resolved_top_p,
        resolved_repetition,
        token_limit,
    ) = _validate_settings(
        temperature=temperature,
        top_k=top_k,
        top_p=top_p,
        repetition_penalty=repetition_penalty,
        max_tokens=max_tokens,
    )
    resolved_seed = _validate_seed(seed)
    combined_instruction = " ".join(
        item for item in (delivery, persistent_style) if item
    )
    reference_hash = _sha256_file(reference)
    configuration_fingerprint = _build_configuration_fingerprint(
        reference_audio_sha256=reference_hash,
        reference_text=reference_text,
        character_style=persistent_style,
        temperature=resolved_temperature,
        top_k=resolved_top_k,
        top_p=resolved_top_p,
        repetition_penalty=resolved_repetition,
        max_tokens=token_limit,
        seed=resolved_seed,
    )
    preview_fingerprint = build_preview_fingerprint(
        reference_audio_sha256=reference_hash,
        reference_text=reference_text,
        text=preview_text,
        instruct=delivery,
        character_style=persistent_style,
        temperature=resolved_temperature,
        top_k=resolved_top_k,
        top_p=resolved_top_p,
        repetition_penalty=resolved_repetition,
        max_tokens=token_limit,
        seed=resolved_seed,
    )
    preview_dir = root / "clone_voices" / "previews"
    filename = f"controlled_{preview_fingerprint[:24]}.wav"
    target = preview_dir / filename
    temporary = preview_dir / (
        f".{filename}.{uuid.uuid4().hex}.tmp.wav"
    )

    with _PREVIEW_LOCK:
        preview_dir.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        try:
            generated = generator(
                text=preview_text,
                ref_audio=str(reference),
                ref_text=reference_text,
                instruct=combined_instruction,
                output_path=str(temporary),
                temperature=resolved_temperature,
                top_k=resolved_top_k,
                top_p=resolved_top_p,
                repetition_penalty=resolved_repetition,
                max_tokens=token_limit,
                seed=resolved_seed,
                request_label="preview",
            )
            if generated is not True or not temporary.is_file():
                raise ControlledClonePreviewUnavailableError(
                    "Controlled clone preview generation returned no audio."
                )
            info = sf.info(str(temporary))
            duration = float(info.duration)
            if duration <= 0:
                raise ControlledClonePreviewUnavailableError(
                    "Controlled clone preview audio is empty."
                )
            os.replace(temporary, target)
        except ControlledClonePreviewError:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise
        except Exception as exc:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise ControlledClonePreviewUnavailableError(
                f"Controlled clone preview failed: {exc}"
            ) from exc
        elapsed = time.perf_counter() - started

    return {
        "status": "generated",
        "backend": CONTROLLED_CLONE_BACKEND,
        "model": CONTROLLED_CLONE_MODEL,
        "audio_url": f"/clone_voices/previews/{filename}",
        "preview_fingerprint": preview_fingerprint,
        "configuration_fingerprint": configuration_fingerprint,
        "reference_file": reference_relative,
        "elapsed_seconds": elapsed,
        "audio_duration_seconds": duration,
        "real_time_factor": elapsed / duration,
        "settings": {
            "temperature": resolved_temperature,
            "top_k": resolved_top_k,
            "top_p": resolved_top_p,
            "repetition_penalty": resolved_repetition,
            "max_tokens": token_limit,
            "seed": resolved_seed,
        },
        "production_configuration_changed": False,
        "production_audio_changed": False,
        "requires_listen_confirmation": True,
    }
