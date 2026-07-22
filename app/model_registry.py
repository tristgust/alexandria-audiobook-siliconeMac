from __future__ import annotations

import importlib.util
import platform
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


MODEL_REGISTRY_SCHEMA_VERSION = 1
QWEN_TTS_REQUIRED_PATHS = (
    "config.json",
    "generation_config.json",
    "model.safetensors",
    "preprocessor_config.json",
    "tokenizer_config.json",
    "merges.txt",
    "vocab.json",
    "speech_tokenizer/config.json",
    "speech_tokenizer/configuration.json",
    "speech_tokenizer/model.safetensors",
    "speech_tokenizer/preprocessor_config.json",
)


@dataclass(frozen=True)
class ModelSpec:
    key: str
    repo_id: str
    revision: str
    runtime: str
    purpose: str
    estimated_size_bytes: int
    required_paths: tuple[str, ...]
    installation_class: str
    consumers: tuple[str, ...]
    required_by_default: bool = False

    @property
    def cache_name(self) -> str:
        return "models--" + self.repo_id.replace("/", "--")

    @property
    def dependency_modules(self) -> tuple[str, ...]:
        return {
            "mlx-audio": ("mlx", "mlx_audio"),
            "mlx-whisper": ("mlx", "mlx_whisper"),
            "qwen-tts-pytorch": ("torch", "qwen_tts"),
        }[self.runtime]

    @property
    def estimated_cold_memory_bytes(self) -> int:
        return max(256 * 1024 * 1024, self.estimated_size_bytes // 10)

    @property
    def estimated_loaded_memory_bytes(self) -> int:
        return self.estimated_size_bytes + max(
            768 * 1024 * 1024,
            self.estimated_size_bytes // 3,
        )

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["required_paths"] = list(self.required_paths)
        value["consumers"] = list(self.consumers)
        value["cache_name"] = self.cache_name
        value["dependency_modules"] = list(self.dependency_modules)
        value["estimated_cold_memory_bytes"] = self.estimated_cold_memory_bytes
        value["estimated_loaded_memory_bytes"] = self.estimated_loaded_memory_bytes
        return value


_MODEL_SPECS = (
    ModelSpec(
        key="mlx_clone",
        repo_id="mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
        revision="e7dd0585652209fa0d7783659aad4e8a324de11c",
        runtime="mlx-audio",
        purpose="Standard supplied-clip voice cloning",
        estimated_size_bytes=3_104_156_243,
        required_paths=QWEN_TTS_REQUIRED_PATHS,
        installation_class="core",
        consumers=("mlx_backend.clone", "voice_backend_capabilities"),
        required_by_default=True,
    ),
    ModelSpec(
        key="mlx_custom_voice",
        repo_id="mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit",
        revision="41d3337e8b7f2843a75841595fc14e4b9a7a4b96",
        runtime="mlx-audio",
        purpose="Built-in Qwen CustomVoice synthesis",
        estimated_size_bytes=3_080_138_901,
        required_paths=QWEN_TTS_REQUIRED_PATHS,
        installation_class="core",
        consumers=("mlx_backend.custom_voice", "voice_backend_capabilities"),
        required_by_default=True,
    ),
    ModelSpec(
        key="mlx_voice_design",
        repo_id="mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit",
        revision="f90d617701d9f7f4ca499291e0b57f2b3c2fd2ee",
        runtime="mlx-audio",
        purpose="Qwen VoiceDesign preview and synthesis",
        estimated_size_bytes=3_080_138_280,
        required_paths=QWEN_TTS_REQUIRED_PATHS,
        installation_class="core",
        consumers=("mlx_backend.voice_design", "voice_backend_capabilities"),
        required_by_default=True,
    ),
    ModelSpec(
        key="mlx_controlled_clone",
        repo_id="mlx-community/VoxCPM2-4bit",
        revision="dc9e5c187858da5f4a13dc4c247e297339216381",
        runtime="mlx-audio",
        purpose="Instruction-controlled supplied-clip cloning",
        estimated_size_bytes=2_300_904_017,
        required_paths=(
            "config.json",
            "model.safetensors",
            "tokenizer.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
        ),
        installation_class="optional_legacy",
        consumers=(
            "mlx_backend.controlled_clone_comparison",
            "expressive_clone_candidates.voxcpm2_baseline",
            "voice_backend_capabilities",
        ),
    ),
    ModelSpec(
        key="mlx_whisper_large_v3_turbo",
        repo_id="mlx-community/whisper-large-v3-turbo",
        revision="a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb",
        runtime="mlx-whisper",
        purpose="Audio-preparer transcription",
        estimated_size_bytes=1_613_979_758,
        required_paths=(
            "config.json",
            "weights.safetensors",
        ),
        installation_class="optional_feature",
        consumers=("alexandria_preparer.transcription",),
    ),
    ModelSpec(
        key="mlx_whisper_base",
        repo_id="mlx-community/whisper-base-mlx",
        revision="1e3e249fb8d01c655324bd6841b1deadffd6d04c",
        runtime="mlx-whisper",
        purpose="Lightweight transcription compatibility model",
        estimated_size_bytes=143_724_466,
        required_paths=(
            "config.json",
            "weights.npz",
        ),
        installation_class="optional_evaluation",
        consumers=("benchmarks.transcription_evaluator",),
    ),
    ModelSpec(
        key="pytorch_qwen_custom_voice",
        repo_id="Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        revision="0c0e3051f131929182e2c023b9537f8b1c68adfe",
        runtime="qwen-tts-pytorch",
        purpose="PyTorch CustomVoice compatibility synthesis",
        estimated_size_bytes=4_520_218_951,
        required_paths=QWEN_TTS_REQUIRED_PATHS,
        installation_class="optional_compatibility",
        consumers=("tts.pytorch_custom_voice",),
    ),
    ModelSpec(
        key="pytorch_qwen_voice_design",
        repo_id="Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
        revision="5ecdb67327fd37bb2e042aab12ff7391903235d3",
        runtime="qwen-tts-pytorch",
        purpose="PyTorch VoiceDesign compatibility synthesis",
        estimated_size_bytes=4_520_163_832,
        required_paths=QWEN_TTS_REQUIRED_PATHS,
        installation_class="optional_compatibility",
        consumers=("tts.pytorch_voice_design",),
    ),
    ModelSpec(
        key="pytorch_qwen_base",
        repo_id="Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        revision="fd4b254389122332181a7c3db7f27e918eec64e3",
        runtime="qwen-tts-pytorch",
        purpose="Isolated SFT and PEFT LoRA training",
        estimated_size_bytes=4_544_170_364,
        required_paths=QWEN_TTS_REQUIRED_PATHS,
        installation_class="optional_training",
        consumers=(
            "tts.pytorch_clone",
            "train_lora",
            "training_sidecar.qwen_training",
            "instruction_dataset",
        ),
    ),
)

_VALID_INSTALLATION_CLASSES = frozenset(
    {
        "core",
        "optional_feature",
        "optional_compatibility",
        "optional_evaluation",
        "optional_legacy",
        "optional_training",
    }
)
for _spec in _MODEL_SPECS:
    if _spec.installation_class not in _VALID_INSTALLATION_CLASSES:
        raise RuntimeError(
            f"Invalid installation class for model {_spec.key!r}: "
            f"{_spec.installation_class!r}."
        )
    if not _spec.consumers or any(not item.strip() for item in _spec.consumers):
        raise RuntimeError(f"Model {_spec.key!r} has no declared consumers.")
    if _spec.required_by_default != (_spec.installation_class == "core"):
        raise RuntimeError(
            f"Model {_spec.key!r} default requirement does not match its "
            "installation class."
        )

MODEL_REGISTRY = {spec.key: spec for spec in _MODEL_SPECS}
_MODEL_KEYS_BY_REPO = {spec.repo_id: spec.key for spec in _MODEL_SPECS}


class ModelRegistryError(ValueError):
    pass


class ModelCacheOperationError(RuntimeError):
    def __init__(self, code: str, message: str, *, model_key: str):
        super().__init__(message)
        self.code = code
        self.model_key = model_key


def registered_models() -> tuple[ModelSpec, ...]:
    return _MODEL_SPECS


def model_spec(identifier: str) -> ModelSpec:
    value = str(identifier or "").strip()
    key = value if value in MODEL_REGISTRY else _MODEL_KEYS_BY_REPO.get(value)
    if key is None:
        raise ModelRegistryError(f"Unregistered Alexandria model: {identifier!r}.")
    return MODEL_REGISTRY[key]


def is_registered_model(identifier: str) -> bool:
    try:
        model_spec(identifier)
    except ModelRegistryError:
        return False
    return True


def _canonical_cache_root(cache_dir: str | Path | None) -> Path:
    if cache_dir is not None:
        return Path(cache_dir).expanduser().resolve()
    from hf_access import shared_huggingface_cache_dir

    return shared_huggingface_cache_dir()


def resolve_model_path(
    identifier: str,
    *,
    local_files_only: bool = True,
    token: bool | str | None = None,
    cache_dir: str | Path | None = None,
) -> Path:
    spec = model_spec(identifier)
    target_root = _canonical_cache_root(cache_dir)
    if local_files_only:
        status = model_cache_status(spec.key, cache_dir=target_root)
        if not status["cached"]:
            repair = status["state"] == "incomplete"
            raise ModelCacheOperationError(
                (
                    "model_cache_repair_required"
                    if repair
                    else "model_cache_download_required"
                ),
                (
                    f"The pinned model '{spec.repo_id}' is "
                    f"{'incomplete' if repair else 'not cached'}. Open Maintenance → "
                    f"Local model cache and choose {'Repair' if repair else 'Download'} "
                    "before starting synthesis or preparation."
                ),
                model_key=spec.key,
            )
    from hf_access import snapshot_download_with_public_fallback

    return snapshot_download_with_public_fallback(
        spec.repo_id,
        revision=spec.revision,
        token=token,
        cache_dir=target_root,
        local_files_only=local_files_only,
        required_paths=spec.required_paths,
        include_fallback_roots=False,
    )


def _dependency_status(spec: ModelSpec) -> dict[str, Any]:
    modules = {
        name: importlib.util.find_spec(name) is not None
        for name in spec.dependency_modules
    }
    return {
        "modules": modules,
        "ready": all(modules.values()),
        "missing": [name for name, ready in modules.items() if not ready],
    }


def _memory_snapshot() -> dict[str, Any]:
    try:
        import psutil

        memory = psutil.virtual_memory()
        return {
            "available": True,
            "total_bytes": int(memory.total),
            "available_bytes": int(memory.available),
            "used_bytes": int(memory.used),
            "platform": platform.system().lower(),
            "architecture": platform.machine().lower(),
        }
    except Exception as exc:
        return {
            "available": False,
            "total_bytes": None,
            "available_bytes": None,
            "used_bytes": None,
            "platform": platform.system().lower(),
            "architecture": platform.machine().lower(),
            "error": str(exc),
        }


def model_cache_status(
    identifier: str,
    *,
    cache_dir: str | Path | None = None,
) -> dict[str, Any]:
    spec = model_spec(identifier)
    from hf_access import cached_snapshot_status

    target_root = _canonical_cache_root(cache_dir)
    status = cached_snapshot_status(
        spec.repo_id,
        revision=spec.revision,
        cache_dir=target_root,
        required_paths=spec.required_paths,
        include_fallback_roots=False,
    )
    dependencies = _dependency_status(spec)
    return {
        "schema_version": MODEL_REGISTRY_SCHEMA_VERSION,
        "model": spec.as_dict(),
        "dependencies": dependencies,
        **status,
        "repair_required": status["state"] == "incomplete",
        "action": (
            "repair"
            if status["state"] == "incomplete"
            else ("download" if status["state"] == "missing" else None)
        ),
    }


def model_registry_status(
    *,
    cache_dir: str | Path | None = None,
    loaded_model_keys: tuple[str, ...] | list[str] | None = None,
    minimum_headroom_bytes: int = 512 * 1024 * 1024,
) -> dict[str, Any]:
    loaded = frozenset(loaded_model_keys or ())
    unknown_loaded = sorted(loaded - set(MODEL_REGISTRY))
    if unknown_loaded:
        raise ModelRegistryError(
            "Unknown loaded model key(s): " + ", ".join(unknown_loaded) + "."
        )
    memory = _memory_snapshot()
    models = []
    for spec in registered_models():
        item = model_cache_status(spec.key, cache_dir=cache_dir)
        required_memory = (
            spec.estimated_loaded_memory_bytes
            + max(0, int(minimum_headroom_bytes))
        )
        reasons = []
        if not item["cached"]:
            reasons.append(
                "model snapshot is incomplete"
                if item["state"] == "incomplete"
                else "model snapshot is missing"
            )
        if not item["dependencies"]["ready"]:
            reasons.append(
                "missing dependencies: "
                + ", ".join(item["dependencies"]["missing"])
            )
        available_bytes = memory.get("available_bytes")
        if available_bytes is None:
            reasons.append("unified-memory availability is unavailable")
        elif available_bytes < required_memory:
            reasons.append(
                f"requires {required_memory} available bytes including headroom; "
                f"{available_bytes} are currently available"
            )
        item["loaded"] = spec.key in loaded
        item["loaded_identity"] = (
            {
                "model_key": spec.key,
                "repo_id": spec.repo_id,
                "revision": spec.revision,
                "snapshot_path": item.get("snapshot_path"),
            }
            if item["loaded"]
            else None
        )
        item["memory"] = {
            "estimated_cold_bytes": spec.estimated_cold_memory_bytes,
            "estimated_loaded_bytes": spec.estimated_loaded_memory_bytes,
            "minimum_headroom_bytes": max(0, int(minimum_headroom_bytes)),
            "required_available_bytes": required_memory,
        }
        item["current_machine_eligible"] = not reasons
        item["ineligibility_reasons"] = reasons
        models.append(item)
    return {
        "schema_version": MODEL_REGISTRY_SCHEMA_VERSION,
        "models": models,
        "memory": memory,
        "loaded_models": [
            item["loaded_identity"]
            for item in models
            if item["loaded_identity"] is not None
        ],
        "cached_count": sum(item["cached"] for item in models),
        "missing_count": sum(item["state"] == "missing" for item in models),
        "incomplete_count": sum(
            item["state"] == "incomplete" for item in models
        ),
        "cached_size_bytes": sum(item["size_bytes"] for item in models),
        "estimated_total_bytes": sum(
            item["model"]["estimated_size_bytes"] for item in models
        ),
        "required_count": sum(
            item["model"]["required_by_default"] for item in models
        ),
        "required_missing_count": sum(
            item["model"]["required_by_default"]
            and item["state"] == "missing"
            for item in models
        ),
        "required_incomplete_count": sum(
            item["model"]["required_by_default"]
            and item["state"] == "incomplete"
            for item in models
        ),
    }


def _disk_usage_path(path: Path) -> Path:
    candidate = path.expanduser().resolve()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def download_or_repair_model(
    identifier: str,
    *,
    repair: bool = False,
    token: bool | str | None = None,
    cache_dir: str | Path | None = None,
    minimum_headroom_bytes: int = 512 * 1024 * 1024,
) -> dict[str, Any]:
    spec = model_spec(identifier)
    before = model_cache_status(spec.key, cache_dir=cache_dir)
    if before["cached"] and not repair:
        return {
            **before,
            "operation": "already_cached",
            "downloaded": False,
            "repaired": False,
        }

    from hf_access import snapshot_download_with_public_fallback

    target_root = _canonical_cache_root(cache_dir)
    required_free = spec.estimated_size_bytes + max(
        0,
        int(minimum_headroom_bytes),
    )
    free_bytes = shutil.disk_usage(_disk_usage_path(target_root)).free
    if free_bytes < required_free:
        raise ModelCacheOperationError(
            "insufficient_model_cache_space",
            (
                f"Downloading '{spec.repo_id}' requires approximately "
                f"{required_free} free bytes including safety headroom, but "
                f"only {free_bytes} bytes are available at '{target_root}'."
            ),
            model_key=spec.key,
        )

    snapshot_download_with_public_fallback(
        spec.repo_id,
        revision=spec.revision,
        token=token,
        cache_dir=target_root,
        force_download=bool(repair),
        required_paths=spec.required_paths,
        include_fallback_roots=False,
    )
    after = model_cache_status(spec.key, cache_dir=target_root)
    if not after["cached"]:
        raise ModelCacheOperationError(
            "model_cache_repair_incomplete",
            (
                f"The pinned snapshot for '{spec.repo_id}' did not pass "
                "Alexandria's required-file validation after download."
            ),
            model_key=spec.key,
        )
    return {
        **after,
        "operation": "repaired" if repair else "downloaded",
        "downloaded": True,
        "repaired": bool(repair),
    }


def model_registry_payload() -> dict[str, Any]:
    return {
        "schema_version": MODEL_REGISTRY_SCHEMA_VERSION,
        "models": [spec.as_dict() for spec in registered_models()],
    }
