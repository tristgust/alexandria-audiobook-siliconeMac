from __future__ import annotations

import copy
import gc
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import platform
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from model_registry import model_cache_status, model_spec, resolve_model_path


SOUND_EFFECT_SCHEMA_VERSION = 2
SOUND_EFFECT_BACKEND_ID = "stable_audio_open_small"
SOUND_EFFECT_MODEL_KEY = "pytorch_stable_audio_open_small"
SOUND_EFFECT_TEXT_ENCODER_KEY = "pytorch_t5_base_sound_effects"
SOUND_EFFECT_TOOLS_VERSION = "0.0.20"
SOUND_EFFECT_TOOLS_REVISION = "3241adba4fc2a85cf5b29d9eb68d42f40a28e820"
SOUND_EFFECT_SAMPLE_RATE = 44_100
SOUND_EFFECT_CHANNELS = 2
SOUND_EFFECT_DEFAULT_DURATION_SECONDS = 3.5
SOUND_EFFECT_MIN_DURATION_SECONDS = 0.5
SOUND_EFFECT_MAX_DURATION_SECONDS = 11.0
SOUND_EFFECT_DEFAULT_STEPS = 8
SOUND_EFFECT_DEFAULT_CFG_SCALE = 1.0
SOUND_EFFECT_DEFAULT_SAMPLER = "pingpong"
SOUND_EFFECT_RESIDENCY_SLOT = "sound_effects.stable_audio_open_small"
SOUND_EFFECT_LICENSE = "Stability AI Community License"
SOUND_EFFECT_BACKEND_MESSAGE = (
    "Stable Audio Open Small is not ready. Install its pinned model, local T5 "
    "encoder, and Stable Audio Tools dependencies before generating Sound effects."
)


class SoundEffectConfigurationError(ValueError):
    pass


class SoundEffectBackendError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _bounded_float(
    value: Any,
    *,
    default: float,
    minimum: float,
    maximum: float,
    label: str,
) -> float:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise SoundEffectConfigurationError(
            f"{label} must be between {minimum:g} and {maximum:g}."
        )
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SoundEffectConfigurationError(
            f"{label} must be between {minimum:g} and {maximum:g}."
        ) from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise SoundEffectConfigurationError(
            f"{label} must be between {minimum:g} and {maximum:g}."
        )
    return result


def _bounded_integer(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
    label: str,
) -> int:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise SoundEffectConfigurationError(
            f"{label} must be between {minimum} and {maximum}."
        )
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise SoundEffectConfigurationError(
            f"{label} must be between {minimum} and {maximum}."
        ) from exc
    if not minimum <= result <= maximum:
        raise SoundEffectConfigurationError(
            f"{label} must be between {minimum} and {maximum}."
        )
    return result


def normalize_sound_effect_configuration(
    value: Mapping[str, Any] | None,
) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    definition = " ".join(
        str(
            source.get("sound_effect_definition")
            or source.get("definition")
            or source.get("description")
            or ""
        ).split()
    ).strip()
    if not definition:
        raise SoundEffectConfigurationError(
            "Describe the non-speech sound this character should produce."
        )
    if len(definition) > 4000:
        raise SoundEffectConfigurationError(
            "The sound-effect definition is too long."
        )
    duration = _bounded_float(
        source.get("sound_effect_duration_seconds"),
        default=SOUND_EFFECT_DEFAULT_DURATION_SECONDS,
        minimum=SOUND_EFFECT_MIN_DURATION_SECONDS,
        maximum=SOUND_EFFECT_MAX_DURATION_SECONDS,
        label="Sound-effect duration",
    )
    steps = _bounded_integer(
        source.get("sound_effect_steps"),
        default=SOUND_EFFECT_DEFAULT_STEPS,
        minimum=1,
        maximum=100,
        label="Sound-effect sampling steps",
    )
    cfg_scale = _bounded_float(
        source.get("sound_effect_cfg_scale"),
        default=SOUND_EFFECT_DEFAULT_CFG_SCALE,
        minimum=0.0,
        maximum=20.0,
        label="Sound-effect guidance scale",
    )
    return {
        "type": "sound_effect",
        "voice": None,
        "sound_effect_schema_version": SOUND_EFFECT_SCHEMA_VERSION,
        "sound_effect_definition": definition,
        "sound_effect_backend": SOUND_EFFECT_BACKEND_ID,
        "sound_effect_duration_seconds": duration,
        "sound_effect_steps": steps,
        "sound_effect_cfg_scale": cfg_scale,
        "description": definition,
        "character_style": "",
    }


def build_sound_effect_request(
    *,
    voice_data: Mapping[str, Any],
    chunk: Mapping[str, Any],
    seed: int,
) -> dict[str, Any]:
    normalized = normalize_sound_effect_configuration(voice_data)
    direction = " ".join(
        str(
            chunk.get("effective_instruct")
            or chunk.get("instruct")
            or ""
        ).split()
    ).strip()
    authored_event = " ".join(str(chunk.get("text") or "").split()).strip()
    prompt_parts = [normalized["sound_effect_definition"]]
    if direction:
        prompt_parts.append(f"Immediate event: {direction}")
    elif authored_event:
        prompt_parts.append(f"Immediate event: {authored_event}")
    prompt_parts.append(
        "Natural non-speech sound effect only; no music, no narration, and no human speech."
    )
    prompt = ". ".join(part.rstrip(" .") for part in prompt_parts if part) + "."
    settings = {
        "backend_id": SOUND_EFFECT_BACKEND_ID,
        "model_key": SOUND_EFFECT_MODEL_KEY,
        "model_revision": model_spec(SOUND_EFFECT_MODEL_KEY).revision,
        "text_encoder_key": SOUND_EFFECT_TEXT_ENCODER_KEY,
        "text_encoder_revision": model_spec(SOUND_EFFECT_TEXT_ENCODER_KEY).revision,
        "tools_version": SOUND_EFFECT_TOOLS_VERSION,
        "tools_revision": SOUND_EFFECT_TOOLS_REVISION,
        "duration_seconds": normalized["sound_effect_duration_seconds"],
        "steps": normalized["sound_effect_steps"],
        "cfg_scale": normalized["sound_effect_cfg_scale"],
        "sampler": SOUND_EFFECT_DEFAULT_SAMPLER,
        "sample_rate": SOUND_EFFECT_SAMPLE_RATE,
        "channels": SOUND_EFFECT_CHANNELS,
        "seed": int(seed),
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            {"prompt": prompt, "settings": settings},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "prompt": prompt,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "request_fingerprint": fingerprint,
        "settings": settings,
    }


def _dependencies_ready() -> tuple[bool, list[str]]:
    required = (
        "torch",
        "torchaudio",
        "stable_audio_tools",
        "soundfile",
    )
    missing = [name for name in required if importlib.util.find_spec(name) is None]
    return not missing, missing


def sound_effect_backend_status() -> dict[str, Any]:
    try:
        model = model_cache_status(SOUND_EFFECT_MODEL_KEY)
        text_encoder = model_cache_status(SOUND_EFFECT_TEXT_ENCODER_KEY)
    except Exception as exc:
        return {
            "available": False,
            "backend_id": SOUND_EFFECT_BACKEND_ID,
            "schema_version": SOUND_EFFECT_SCHEMA_VERSION,
            "state": "inspection_failed",
            "message": (
                "Alexandria could not inspect the Sound effect model cache: "
                f"{type(exc).__name__}."
            ),
            "model": None,
            "text_encoder": None,
            "missing_dependencies": [],
            "license": SOUND_EFFECT_LICENSE,
        }
    dependencies_ready, missing_dependencies = _dependencies_ready()
    available = bool(
        model.get("cached")
        and text_encoder.get("cached")
        and dependencies_ready
    )
    if available:
        state = "ready"
        message = (
            "Stable Audio Open Small is ready for local non-speech generation."
        )
    elif not model.get("cached"):
        state = "model_missing"
        message = (
            "Stable Audio Open Small is not cached. Import the licensed model "
            "files before generating Sound effects."
        )
    elif not text_encoder.get("cached"):
        state = "text_encoder_missing"
        message = (
            "The pinned local T5 text encoder is missing. Download or repair it "
            "in Maintenance → Local model cache."
        )
    else:
        state = "dependencies_missing"
        message = (
            "Stable Audio Open Small is cached, but its Python runtime is missing: "
            + ", ".join(missing_dependencies)
            + "."
        )
    device = "cpu"
    try:
        import torch

        if platform.system() == "Darwin" and torch.backends.mps.is_available():
            device = "mps"
    except Exception:
        pass
    return {
        "available": available,
        "backend_id": SOUND_EFFECT_BACKEND_ID,
        "schema_version": SOUND_EFFECT_SCHEMA_VERSION,
        "state": state,
        "message": message,
        "device": device,
        "sample_rate": SOUND_EFFECT_SAMPLE_RATE,
        "channels": SOUND_EFFECT_CHANNELS,
        "maximum_duration_seconds": SOUND_EFFECT_MAX_DURATION_SECONDS,
        "model": model,
        "text_encoder": text_encoder,
        "missing_dependencies": missing_dependencies,
        "license": SOUND_EFFECT_LICENSE,
    }


def sound_effect_generation_error() -> str:
    return str(sound_effect_backend_status().get("message") or SOUND_EFFECT_BACKEND_MESSAGE)


class StableAudioOpenSmallBackend:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._model = None
        self._config: dict[str, Any] | None = None
        self._device = "cpu"
        self._loaded_at = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def synchronize(self) -> None:
        try:
            import torch

            if self._device == "mps" and torch.backends.mps.is_available():
                torch.mps.synchronize()
        except Exception:
            pass

    def unload(self) -> bool:
        with self._lock:
            model = self._model
            self._model = None
            self._config = None
            self._loaded_at = None
            self._device = "cpu"
            if model is None:
                return False
            try:
                model.to("cpu")
            except Exception:
                pass
            del model
            gc.collect()
            try:
                import torch

                if torch.backends.mps.is_available():
                    torch.mps.empty_cache()
            except Exception:
                pass
            return True

    @staticmethod
    def _device_order() -> tuple[str, ...]:
        try:
            import torch

            if platform.system() == "Darwin" and torch.backends.mps.is_available():
                return ("mps", "cpu")
        except Exception:
            pass
        return ("cpu",)

    @staticmethod
    def _mps_fallback_allowed(error: BaseException) -> bool:
        text = str(error).casefold()
        return any(
            marker in text
            for marker in (
                "mps backend",
                "not implemented for mps",
                "mps does not support",
                "out of memory",
                "metal",
            )
        )

    def _load(self) -> None:
        if self._model is not None:
            return
        status = sound_effect_backend_status()
        if status.get("available") is not True:
            raise SoundEffectBackendError(
                "sound_effect_backend_unavailable",
                str(status.get("message") or SOUND_EFFECT_BACKEND_MESSAGE),
            )
        try:
            import torch
            from stable_audio_tools.models.factory import create_model_from_config
            from stable_audio_tools.models.utils import load_ckpt_state_dict
        except Exception as exc:
            raise SoundEffectBackendError(
                "sound_effect_runtime_unavailable",
                "Stable Audio Tools could not be imported for Sound effect generation.",
            ) from exc
        model_root = resolve_model_path(SOUND_EFFECT_MODEL_KEY)
        text_encoder_root = resolve_model_path(SOUND_EFFECT_TEXT_ENCODER_KEY)
        config = json.loads((model_root / "model_config.json").read_text(encoding="utf-8"))
        conditioning = (
            config.get("model", {})
            .get("conditioning", {})
            .get("configs", [])
        )
        for item in conditioning:
            if isinstance(item, dict) and item.get("type") == "t5":
                item.setdefault("config", {})["model_path"] = str(text_encoder_root)
        model = create_model_from_config(config)
        state = load_ckpt_state_dict(str(model_root / "model.safetensors"))
        missing, unexpected = model.load_state_dict(state, strict=False)
        if missing or unexpected:
            raise SoundEffectBackendError(
                "sound_effect_model_mismatch",
                "Stable Audio Open Small weights do not match the pinned model configuration.",
            )
        device = self._device_order()[0]
        try:
            model = model.to(device).eval()
        except Exception as exc:
            if device != "mps" or not self._mps_fallback_allowed(exc):
                raise
            device = "cpu"
            model = model.to(device).eval()
        self._model = model
        self._config = config
        self._device = device
        self._loaded_at = time.time()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    @staticmethod
    def _write_validated_audio(
        *,
        audio,
        sample_rate: int,
        duration_seconds: float,
        output_path: str | Path,
    ) -> dict[str, Any]:
        import numpy as np
        import soundfile as sf

        array = audio.detach().float().cpu().numpy()
        if array.ndim != 2 or array.shape[0] != SOUND_EFFECT_CHANNELS:
            raise SoundEffectBackendError(
                "sound_effect_output_shape_invalid",
                "Stable Audio Open Small returned an invalid stereo audio shape.",
            )
        if not np.isfinite(array).all():
            raise SoundEffectBackendError(
                "sound_effect_output_nonfinite",
                "Stable Audio Open Small returned non-finite audio samples.",
            )
        peak = float(np.max(np.abs(array))) if array.size else 0.0
        if peak <= 1e-6:
            raise SoundEffectBackendError(
                "sound_effect_output_silent",
                "Stable Audio Open Small returned silent audio.",
            )
        array = np.clip(array / peak * 0.95, -1.0, 1.0)
        fade = min(max(1, int(sample_rate * 0.01)), array.shape[1] // 2)
        if fade:
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
            array[:, :fade] *= ramp
            array[:, -fade:] *= ramp[::-1]
        target = Path(output_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.sound-effect.tmp.wav")
        try:
            sf.write(temporary, array.T, sample_rate, subtype="PCM_16")
            info = sf.info(temporary)
            actual_duration = info.frames / info.samplerate
            if (
                info.channels != SOUND_EFFECT_CHANNELS
                or info.samplerate != SOUND_EFFECT_SAMPLE_RATE
                or info.frames <= 0
                or abs(actual_duration - duration_seconds) > 0.2
            ):
                raise SoundEffectBackendError(
                    "sound_effect_output_validation_failed",
                    "Generated Sound effect did not satisfy its duration and audio format contract.",
                )
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return {
            "sample_rate": int(info.samplerate),
            "sample_count": int(info.frames),
            "channels": int(info.channels),
            "duration_seconds": round(actual_duration, 6),
            "pre_normalization_peak": peak,
        }

    def generate(
        self,
        *,
        request: Mapping[str, Any],
        output_path: str | Path,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        model_residency: Any = None,
        owner: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if model_residency is not None:
            residents = model_residency.status().get("residents", [])
            resident = any(
                item.get("slot_id") == SOUND_EFFECT_RESIDENCY_SLOT
                for item in residents
            )
            if not resident:
                estimated = (
                    model_spec(SOUND_EFFECT_MODEL_KEY).estimated_loaded_memory_bytes
                    + model_spec(
                        SOUND_EFFECT_TEXT_ENCODER_KEY
                    ).estimated_loaded_memory_bytes
                )

                def load_backend():
                    with self._lock:
                        self._load()
                    return self

                model_residency.load_resident(
                    slot_id=SOUND_EFFECT_RESIDENCY_SLOT,
                    component_id=SOUND_EFFECT_MODEL_KEY,
                    load_callback=load_backend,
                    install_callback=lambda _loaded: None,
                    release_callback=self.unload,
                    synchronize_callback=self.synchronize,
                    engine_id=SOUND_EFFECT_BACKEND_ID,
                    device=self._device_order()[0],
                    estimated_loaded_memory_bytes=estimated,
                )
            with model_residency.job(
                (SOUND_EFFECT_MODEL_KEY,),
                owner=dict(owner or {}),
                label="Stable Audio Sound effect generation",
            ):
                return self._generate_locked(
                    request=request,
                    output_path=output_path,
                    progress_callback=progress_callback,
                )
        return self._generate_locked(
            request=request,
            output_path=output_path,
            progress_callback=progress_callback,
        )

    def _generate_locked(
        self,
        *,
        request: Mapping[str, Any],
        output_path: str | Path,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._load()
            assert self._model is not None and self._config is not None
            try:
                import torch
                from stable_audio_tools.inference.generation import (
                    generate_diffusion_cond,
                )
            except Exception as exc:
                raise SoundEffectBackendError(
                    "sound_effect_runtime_unavailable",
                    "Stable Audio Tools could not start Sound effect generation.",
                ) from exc
            settings = dict(request.get("settings") or {})
            duration = float(settings["duration_seconds"])
            attempts = []
            started = time.perf_counter()
            devices = (self._device,) + tuple(
                item for item in self._device_order() if item != self._device
            )
            last_error = None
            for device in devices:
                if progress_callback is not None:
                    progress_callback(
                        {
                            "stage": "generating_sound_effect",
                            "device": device,
                            "exact": False,
                            "fraction": None,
                        }
                    )
                attempt_started = time.perf_counter()
                try:
                    self._model = self._model.to(device).eval()
                    with torch.inference_mode():
                        output = generate_diffusion_cond(
                            self._model,
                            steps=int(settings["steps"]),
                            cfg_scale=float(settings["cfg_scale"]),
                            conditioning=[
                                {
                                    "prompt": str(request["prompt"]),
                                    "seconds_total": duration,
                                }
                            ],
                            sample_size=int(self._config["sample_size"]),
                            seed=int(settings["seed"]),
                            device=device,
                            sampler_type=str(settings["sampler"]),
                            adapt_duration_to_conditioning=True,
                            duration_padding_sec=0,
                        )
                    validation = self._write_validated_audio(
                        audio=output[0],
                        sample_rate=int(self._config["sample_rate"]),
                        duration_seconds=duration,
                        output_path=output_path,
                    )
                    self._device = device
                    attempts.append(
                        {
                            "device": device,
                            "succeeded": True,
                            "seconds": round(time.perf_counter() - attempt_started, 6),
                        }
                    )
                    return {
                        "schema_version": 1,
                        "backend_id": SOUND_EFFECT_BACKEND_ID,
                        "model_key": SOUND_EFFECT_MODEL_KEY,
                        "model_revision": model_spec(SOUND_EFFECT_MODEL_KEY).revision,
                        "text_encoder_key": SOUND_EFFECT_TEXT_ENCODER_KEY,
                        "text_encoder_revision": model_spec(
                            SOUND_EFFECT_TEXT_ENCODER_KEY
                        ).revision,
                        "tools_version": importlib.metadata.version(
                            "stable-audio-tools"
                        ),
                        "tools_revision": SOUND_EFFECT_TOOLS_REVISION,
                        "device": device,
                        "prompt_sha256": request["prompt_sha256"],
                        "request_fingerprint": request["request_fingerprint"],
                        "seed": int(settings["seed"]),
                        "duration_requested_seconds": duration,
                        "steps": int(settings["steps"]),
                        "cfg_scale": float(settings["cfg_scale"]),
                        "sampler": str(settings["sampler"]),
                        "generation_seconds": round(
                            time.perf_counter() - started,
                            6,
                        ),
                        "attempts": attempts,
                        **validation,
                    }
                except Exception as exc:
                    last_error = exc
                    attempts.append(
                        {
                            "device": device,
                            "succeeded": False,
                            "seconds": round(time.perf_counter() - attempt_started, 6),
                            "error_type": type(exc).__name__,
                        }
                    )
                    if device != "mps" or not self._mps_fallback_allowed(exc):
                        break
            if isinstance(last_error, SoundEffectBackendError):
                raise last_error
            raise SoundEffectBackendError(
                "sound_effect_generation_failed",
                "Stable Audio Open Small could not generate this Sound effect.",
            ) from last_error


_BACKEND = StableAudioOpenSmallBackend()


def generate_sound_effect_audio(
    *,
    request: Mapping[str, Any],
    output_path: str | Path,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    model_residency: Any = None,
    owner: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return _BACKEND.generate(
        request=copy.deepcopy(dict(request)),
        output_path=output_path,
        progress_callback=progress_callback,
        model_residency=model_residency,
        owner=owner,
    )
