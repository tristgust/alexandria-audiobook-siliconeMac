from __future__ import annotations

import importlib.util
import hashlib
import json
import platform
import shutil
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Final


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


@dataclass(frozen=True)
class EngineComponentRecord(ModelSpec):
    def as_model_spec(self) -> ModelSpec:
        return ModelSpec(**asdict(self))


_COMPONENT_RECORDS = (
    EngineComponentRecord(
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
    EngineComponentRecord(
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
    EngineComponentRecord(
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
    EngineComponentRecord(
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
    EngineComponentRecord(
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
    EngineComponentRecord(
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
    EngineComponentRecord(
        key="pytorch_scrappylabs_narrator",
        repo_id="scrappylabs/narrator-tts",
        revision="82b3a6f6bc4a9087169d61417aa77b2615d7e0a3",
        runtime="qwen-tts-pytorch",
        purpose="Experimental expressive narrator CustomVoice source",
        estimated_size_bytes=4_520_218_951,
        required_paths=QWEN_TTS_REQUIRED_PATHS,
        installation_class="optional_evaluation",
        consumers=("community_qwen_candidates.scrappylabs_narrator",),
    ),
    EngineComponentRecord(
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
    EngineComponentRecord(
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
    EngineComponentRecord(
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

_MODEL_SPECS = tuple(record.as_model_spec() for record in _COMPONENT_RECORDS)
_COMPONENT_RECORDS_BY_ID = {record.key: record for record in _COMPONENT_RECORDS}

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


ENGINE_COMPONENT_RECORD_SCHEMA_VERSION = 1


class EngineRecordValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _artifact_role(path: str) -> str:
    if path.startswith("speech_tokenizer/"):
        return "codec"
    if "tokenizer" in path or path in {"merges.txt", "vocab.json"}:
        return "tokenizer"
    if path == "preprocessor_config.json":
        return "preprocessor"
    return "model"


def _serialization(path: str) -> str:
    if path.endswith(".safetensors"):
        return "safetensors"
    if path.endswith(".json"):
        return "json"
    if path.endswith(".npz"):
        return "numpy_npz"
    return "text"


def _loader(runtime: str) -> str:
    return {
        "mlx-audio": "mlx_audio.load_model",
        "mlx-whisper": "mlx_whisper.load_models",
        "qwen-tts-pytorch": "qwen_tts.Qwen3TTSModel.from_pretrained",
    }[runtime]


def _component_build_id(
    *,
    source_id: str,
    revision: str,
    runtime: str,
    loader: str,
    role: str,
    required_paths: tuple[str, ...],
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "source_id": source_id,
                "revision": revision,
                "runtime": runtime,
                "loader": loader,
                "role": role,
                "required_paths": list(required_paths),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _primary_paths(spec: EngineComponentRecord) -> tuple[str, ...]:
    if spec.required_paths == QWEN_TTS_REQUIRED_PATHS:
        return ("config.json", "model.safetensors")
    if spec.key == "mlx_controlled_clone":
        return ("config.json", "model.safetensors")
    return spec.required_paths


def _component_payload(spec: EngineComponentRecord) -> dict[str, Any]:
    loader = _loader(spec.runtime)
    required_paths = _primary_paths(spec)
    return {
        "component_id": spec.key,
        "source_id": spec.repo_id,
        "revision": spec.revision,
        "build_id": _component_build_id(
            source_id=spec.repo_id,
            revision=spec.revision,
            runtime=spec.runtime,
            loader=loader,
            role="model",
            required_paths=required_paths,
        ),
        "runtime": spec.runtime,
        "loader": loader,
        "role": "model",
        "purpose": spec.purpose,
        "estimated_size_bytes": spec.estimated_size_bytes,
        "required_paths": list(required_paths),
        "artifacts": [
            {
                "path": path,
                "role": _artifact_role(path),
                "serialization": _serialization(path),
            }
            for path in required_paths
        ],
        "installation_class": spec.installation_class,
        "consumers": list(spec.consumers),
        "required_by_default": spec.required_by_default,
    }


def _supporting_component_payload(
    spec: EngineComponentRecord,
    *,
    suffix: str,
    role: str,
    required_paths: tuple[str, ...],
) -> dict[str, Any]:
    loader = _loader(spec.runtime)
    component_id = f"{spec.key}.{suffix}"
    return {
        "component_id": component_id,
        "source_id": spec.repo_id,
        "revision": spec.revision,
        "build_id": _component_build_id(
            source_id=spec.repo_id,
            revision=spec.revision,
            runtime=spec.runtime,
            loader=loader,
            role=role,
            required_paths=required_paths,
        ),
        "runtime": spec.runtime,
        "loader": loader,
        "role": role,
        "purpose": f"{spec.purpose} {role} assets",
        "estimated_size_bytes": 0,
        "required_paths": list(required_paths),
        "artifacts": [
            {
                "path": path,
                "role": role,
                "serialization": _serialization(path),
            }
            for path in required_paths
        ],
        "installation_class": spec.installation_class,
        "consumers": list(spec.consumers),
        "required_by_default": spec.required_by_default,
    }


def _supporting_components(spec: EngineComponentRecord) -> tuple[dict[str, Any], ...]:
    if spec.required_paths == QWEN_TTS_REQUIRED_PATHS:
        return (
            _supporting_component_payload(
                spec,
                suffix="text_tokenizer",
                role="tokenizer",
                required_paths=("tokenizer_config.json", "merges.txt", "vocab.json"),
            ),
            _supporting_component_payload(
                spec,
                suffix="speech_tokenizer",
                role="codec",
                required_paths=(
                    "speech_tokenizer/config.json",
                    "speech_tokenizer/configuration.json",
                    "speech_tokenizer/model.safetensors",
                    "speech_tokenizer/preprocessor_config.json",
                ),
            ),
            _supporting_component_payload(
                spec,
                suffix="generation",
                role="auxiliary",
                required_paths=("generation_config.json", "preprocessor_config.json"),
            ),
        )
    if spec.key == "mlx_controlled_clone":
        return (
            _supporting_component_payload(
                spec,
                suffix="tokenizer",
                role="tokenizer",
                required_paths=(
                    "tokenizer.json",
                    "tokenizer_config.json",
                    "special_tokens_map.json",
                ),
            ),
        )
    return ()


_SUPPORTING_COMPONENTS = tuple(
    component
    for spec in _COMPONENT_RECORDS
    for component in _supporting_components(spec)
)
_SUPPORTING_COMPONENTS_BY_ID = {
    component["component_id"]: component for component in _SUPPORTING_COMPONENTS
}


def _engine_component_ids(primary_id: str) -> tuple[str, ...]:
    return (primary_id,) + tuple(
        component["component_id"]
        for component in _supporting_components(_COMPONENT_RECORDS_BY_ID[primary_id])
    )


_SYNTHESIS_WINDOWS: dict[str, dict[str, Any]] = {
    "qwen3_custom": {"family": "qwen3", "max_chars": 96, "max_words": None, "minimum_words": 2, "seam_mode": "silence_gap", "seam_ms": 100, "split_priority": ["paragraph", "sentence", "word", "character"]},
    "qwen3_base": {"family": "qwen3", "max_chars": 180, "max_words": 14, "minimum_words": 2, "seam_mode": "silence_gap", "seam_ms": 100, "split_priority": ["paragraph", "sentence", "clause", "word", "character"]},
    "qwen3_instruction_controlled": {"family": "qwen3", "max_chars": 220, "max_words": None, "minimum_words": 2, "seam_mode": "crossfade", "seam_ms": 12, "split_priority": ["paragraph", "sentence", "clause", "word", "character"]},
    "qwen3_lora": {"family": "qwen3", "max_chars": 220, "max_words": None, "minimum_words": 2, "seam_mode": "crossfade", "seam_ms": 12, "split_priority": ["paragraph", "sentence", "clause", "word", "character"]},
    "qwen3_voice_design": {"family": "qwen3", "max_chars": 220, "max_words": None, "minimum_words": 2, "seam_mode": "crossfade", "seam_ms": 12, "split_priority": ["paragraph", "sentence", "clause", "word", "character"]},
    "community_qwen": {"family": "qwen3", "max_chars": 220, "max_words": None, "minimum_words": 2, "seam_mode": "crossfade", "seam_ms": 12, "split_priority": ["paragraph", "sentence", "clause", "word", "character"]},
    "voxcpm2_controlled": {"family": "voxcpm2", "max_chars": 180, "max_words": None, "minimum_words": 2, "seam_mode": "discard_overlap", "seam_ms": 20, "split_priority": ["paragraph", "sentence", "clause", "word", "character"]},
    "fish_s21_cloud": {"family": "fish", "max_chars": 500, "max_words": None, "minimum_words": 1, "seam_mode": "none", "seam_ms": 0, "split_priority": ["paragraph", "sentence", "clause", "word", "character"]},
    "responsive_router": {"family": "routed", "max_chars": 500, "max_words": None, "minimum_words": 1, "seam_mode": "none", "seam_ms": 0, "split_priority": ["paragraph", "sentence", "clause", "word", "character"]},
    "external_generic": {"family": "external", "max_chars": 240, "max_words": None, "minimum_words": 2, "seam_mode": "crossfade", "seam_ms": 10, "split_priority": ["paragraph", "sentence", "clause", "word", "character"]},
}

_ENGINE_COMPONENTS = {
    "qwen3_custom": _engine_component_ids("mlx_custom_voice"),
    "qwen3_base": _engine_component_ids("mlx_clone"),
    "qwen3_instruction_controlled": _engine_component_ids("mlx_clone"),
    "qwen3_lora": _engine_component_ids("pytorch_qwen_base"),
    "qwen3_voice_design": _engine_component_ids("mlx_voice_design"),
    "community_qwen": _engine_component_ids("pytorch_scrappylabs_narrator"),
    "voxcpm2_controlled": _engine_component_ids("mlx_controlled_clone"),
    "fish_s21_cloud": (),
    "responsive_router": (),
    "external_generic": (),
}

_ENGINE_CONSUMERS = {
    "qwen3_custom": ("mlx_backend", "tts"),
    "qwen3_base": ("mlx_backend", "tts"),
    "qwen3_instruction_controlled": (
        "cast_aggregate",
        "controlled_clone_preview",
        "mlx_backend",
        "tts",
        "voice_backend_capabilities",
        "voice_library",
    ),
    "qwen3_lora": ("tts",),
    "qwen3_voice_design": ("mlx_backend", "tts"),
    "community_qwen": ("mlx_backend", "tts"),
    "voxcpm2_controlled": (
        "cast_aggregate",
        "controlled_clone_preview",
        "mlx_backend",
        "responsive_voice_backend",
        "tts",
        "voice_backend_capabilities",
        "voice_library",
    ),
    "fish_s21_cloud": ("tts",),
    "responsive_router": ("tts",),
    "external_generic": ("tts",),
}


def _engine_revision(engine_id: str) -> str:
    components = _ENGINE_COMPONENTS[engine_id]
    if components:
        return _COMPONENT_RECORDS_BY_ID[components[0]].revision
    return hashlib.sha1(("alexandria:" + engine_id).encode("utf-8")).hexdigest()


def _base_engine_payload(engine_id: str) -> dict[str, Any]:
    migrated = engine_id in {"qwen3_base", "voxcpm2_controlled"}
    supported = engine_id != "external_generic"
    instruction_supported = engine_id == "qwen3_instruction_controlled"
    voice_methods = {
        "qwen3_custom": ["built_in"],
        "qwen3_base": ["clone"],
        "qwen3_instruction_controlled": ["controlled_clone"],
        "qwen3_lora": ["lora"],
        "qwen3_voice_design": ["design"],
        "community_qwen": ["built_in"],
        "voxcpm2_controlled": ["controlled_clone"],
        "fish_s21_cloud": ["clone"],
        "responsive_router": ["clone"],
        "external_generic": ["built_in", "clone"],
    }[engine_id]
    expected_memory = sum(
        MODEL_REGISTRY[component_id].estimated_loaded_memory_bytes
        for component_id in _ENGINE_COMPONENTS[engine_id]
        if component_id in MODEL_REGISTRY
    )
    return {
        "schema_version": ENGINE_COMPONENT_RECORD_SCHEMA_VERSION,
        "engine_id": engine_id,
        "engine_revision": _engine_revision(engine_id),
        "adapter_revision": None,
        "migration_state": "migrated" if migrated else "legacy_passthrough",
        "provider": "local" if _ENGINE_COMPONENTS[engine_id] else "external",
        "supported": supported,
        "readiness": "qualified" if supported else "unsupported",
        "platforms": ["darwin"],
        "devices": ["mps"],
        "languages": ["English"],
        "sample_rates": [24000],
        "modes": ["local"] if _ENGINE_COMPONENTS[engine_id] else ["external"],
        "voice_methods": voice_methods,
        "preprocessing": ["normalize_reference"],
        "instruction": {
            "supported": instruction_supported,
            "mode": "per_record" if instruction_supported else "identity_only",
            "contract": "alexandria_qwen_instruction_propagation_v1",
            "formatter": "qwen_chat_user_v1",
            "placement": "instruction_embedding_then_original_icl_prefill",
        },
        "synthesis_window": {
            "schema_version": 1,
            "backend_id": engine_id,
            **_SYNTHESIS_WINDOWS[engine_id],
        },
        "determinism": {"seed_policy": "request_seed", "streaming": False},
        "concurrency": {"policy": "serialized"},
        "lifecycle": {"policy": "local_on_demand"},
        "expected_memory_bytes": expected_memory,
        "offline": {
            "local_only": bool(_ENGINE_COMPONENTS[engine_id]),
            "cache_policy": "pinned_snapshot",
            "acquisition_policy": "explicit_maintenance",
            "repair_policy": "explicit_repair",
        },
        "component_ids": list(_ENGINE_COMPONENTS[engine_id]),
        "consumers": list(_ENGINE_CONSUMERS[engine_id]),
    }


def engine_ids_for_voice_method(method: str) -> tuple[str, ...]:
    return tuple(
        engine_id
        for engine_id in sorted(_ENGINE_COMPONENTS)
        if method in _base_engine_payload(engine_id)["voice_methods"]
    )


def engine_ids_for_consumer(consumer: str) -> tuple[str, ...]:
    return tuple(
        engine_id
        for engine_id in sorted(_ENGINE_CONSUMERS)
        if consumer in _ENGINE_CONSUMERS[engine_id]
    )


def component_record_payload(component_id: str) -> dict[str, Any]:
    try:
        if component_id in _COMPONENT_RECORDS_BY_ID:
            payload = _component_payload(_COMPONENT_RECORDS_BY_ID[component_id])
        else:
            payload = _SUPPORTING_COMPONENTS_BY_ID[component_id]
        return json.loads(json.dumps(payload))
    except KeyError as exc:
        raise ModelRegistryError(f"Unregistered component: {component_id!r}.") from exc


def engine_record_payload(engine_id: str) -> dict[str, Any]:
    if engine_id not in _ENGINE_COMPONENTS:
        raise ModelRegistryError(f"Unregistered engine: {engine_id!r}.")
    payload = _base_engine_payload(engine_id)
    payload["components"] = [
        component_record_payload(component_id)
        for component_id in payload["component_ids"]
    ]
    return payload


STANDARD_CLONE_ENGINE_ID: Final = engine_record_payload("qwen3_base")[
    "engine_id"
]
INSTRUCTION_CONTROLLED_ENGINE_ID: Final = engine_record_payload(
    "qwen3_instruction_controlled"
)["engine_id"]
LEGACY_CONTROLLED_CLONE_ENGINE_ID: Final = engine_record_payload(
    "voxcpm2_controlled"
)["engine_id"]
FISH_CLOUD_ENGINE_ID: Final = engine_record_payload("fish_s21_cloud")[
    "engine_id"
]
RESPONSIVE_ROUTER_ENGINE_ID: Final = engine_record_payload(
    "responsive_router"
)["engine_id"]
EXTERNAL_GENERIC_ENGINE_ID: Final = engine_record_payload(
    "external_generic"
)["engine_id"]
CUSTOM_VOICE_ENGINE_ID: Final = engine_record_payload("qwen3_custom")[
    "engine_id"
]
LORA_ENGINE_ID: Final = engine_record_payload("qwen3_lora")["engine_id"]
VOICE_DESIGN_ENGINE_ID: Final = engine_record_payload("qwen3_voice_design")[
    "engine_id"
]
COMMUNITY_QWEN_ENGINE_ID: Final = engine_record_payload("community_qwen")[
    "engine_id"
]

# Persisted voice-config protocol identity. It selects the authoritative
# ``responsive_router`` engine but is not itself a separate engine capability.
RESPONSIVE_ROUTER_SELECTION_ID: Final = "alexandria_responsive_router"

class CloneBackendSelectionId(str, Enum):
    STANDARD = STANDARD_CLONE_ENGINE_ID
    INSTRUCTION_CONTROLLED = INSTRUCTION_CONTROLLED_ENGINE_ID
    LEGACY_CONTROLLED = LEGACY_CONTROLLED_CLONE_ENGINE_ID
    FISH_CLOUD = FISH_CLOUD_ENGINE_ID
    RESPONSIVE_ROUTER = RESPONSIVE_ROUTER_SELECTION_ID

    def __str__(self) -> str:
        return self.value


def synthesis_window_record_payloads() -> dict[str, dict[str, Any]]:
    return {
        engine_id: engine_record_payload(engine_id)["synthesis_window"]
        for engine_id in sorted(_ENGINE_COMPONENTS)
    }


def instruction_record_payload() -> dict[str, Any]:
    declaration = engine_record_payload("qwen3_instruction_controlled")["instruction"]
    return {"schema_version": 1, **declaration, "modes": ["identity_only", "per_record"]}


def engine_component_record_payload() -> dict[str, Any]:
    return {
        "schema_version": ENGINE_COMPONENT_RECORD_SCHEMA_VERSION,
        "components": [
            component_record_payload(key)
            for key in sorted(
                set(_COMPONENT_RECORDS_BY_ID) | set(_SUPPORTING_COMPONENTS_BY_ID)
            )
        ],
        "engines": [engine_record_payload(key) for key in sorted(_ENGINE_COMPONENTS)],
    }


def engine_record_fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def migrate_legacy_component_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EngineRecordValidationError(
            "invalid_record", "Component record must be an object."
        )
    if "component_id" in value:
        component_id = value.get("component_id")
        if component_id not in (
            set(_COMPONENT_RECORDS_BY_ID) | set(_SUPPORTING_COMPONENTS_BY_ID)
        ):
            raise EngineRecordValidationError(
                "unknown_component", "Component record names an unknown component."
            )
        expected = component_record_payload(component_id)
        if set(value) != set(expected):
            raise EngineRecordValidationError(
                "unknown_field", "Component record fields do not match the schema."
            )
        if value != expected:
            raise EngineRecordValidationError(
                "record_drift", "Component record differs from the authoritative declaration."
            )
        return json.loads(json.dumps(expected))

    component_id = value.get("key")
    if component_id not in _COMPONENT_RECORDS_BY_ID:
        raise EngineRecordValidationError(
            "unknown_component", "Legacy component names an unknown component."
        )
    legacy = _COMPONENT_RECORDS_BY_ID[component_id].as_dict()
    if set(value) != set(legacy):
        raise EngineRecordValidationError(
            "unknown_field", "Legacy component fields do not match the shipped schema."
        )
    if value != legacy:
        raise EngineRecordValidationError(
            "record_drift", "Legacy component differs from the shipped declaration."
        )
    return component_record_payload(component_id)


def migrate_legacy_engine_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EngineRecordValidationError("invalid_record", "Engine record must be an object.")

    if "engine_id" not in value:
        engine_id = value.get("backend_id")
        if engine_id not in {"qwen3_base", "voxcpm2_controlled"}:
            raise EngineRecordValidationError(
                "unknown_engine", "Legacy engine names an unsupported engine."
            )
        expected_window = engine_record_payload(engine_id)["synthesis_window"]
        if set(value) != set(expected_window):
            raise EngineRecordValidationError(
                "unknown_field", "Legacy engine fields do not match the shipped schema."
            )
        if value != expected_window:
            raise EngineRecordValidationError(
                "record_drift", "Legacy engine differs from the shipped declaration."
            )
        return engine_record_payload(engine_id)

    engine_id = value.get("engine_id")
    if engine_id not in _ENGINE_COMPONENTS:
        raise EngineRecordValidationError("unknown_engine", "Engine record names an unknown engine.")
    expected = engine_record_payload(engine_id)
    if set(value) != set(expected):
        raise EngineRecordValidationError("unknown_field", "Engine record fields do not match the schema.")
    if value.get("readiness") == "ready" and not value.get("supported"):
        raise EngineRecordValidationError("unsupported_ready", "An unsupported engine cannot be ready.")
    if value.get("synthesis_window") != expected["synthesis_window"]:
        raise EngineRecordValidationError("synthesis_mismatch", "Synthesis policy differs from the record.")
    instruction = value.get("instruction", {})
    if instruction.get("mode") == "per_record" and not instruction.get("supported"):
        raise EngineRecordValidationError("instruction_mismatch", "Per-record instructions require support.")
    if value.get("instruction") != expected["instruction"]:
        raise EngineRecordValidationError("instruction_mismatch", "Instruction policy differs from the record.")
    if value.get("offline", {}).get("local_only") != expected["offline"]["local_only"]:
        raise EngineRecordValidationError("offline_mismatch", "Offline policy differs from the record.")
    if value != expected:
        raise EngineRecordValidationError("record_drift", "Engine record differs from the authoritative declaration.")
    return json.loads(json.dumps(expected))


def validate_engine_component_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema_version", "components", "engines"}:
        raise EngineRecordValidationError("unknown_field", "Capability record fields do not match the schema.")
    if value["schema_version"] != ENGINE_COMPONENT_RECORD_SCHEMA_VERSION:
        raise EngineRecordValidationError("unsupported_schema", "Capability record schema is unsupported.")
    if not isinstance(value["components"], list) or not isinstance(value["engines"], list):
        raise EngineRecordValidationError("invalid_record", "Capability record collections must be lists.")
    if any(not isinstance(item, dict) for item in value["components"] + value["engines"]):
        raise EngineRecordValidationError("invalid_record", "Capability declarations must be objects.")
    component_ids = [item.get("component_id") for item in value["components"]]
    if len(component_ids) != len(set(component_ids)):
        raise EngineRecordValidationError("duplicate_component_id", "Component identifiers must be unique.")
    for component in value["components"]:
        component_id = component.get("component_id")
        if component_id not in (
            set(_COMPONENT_RECORDS_BY_ID) | set(_SUPPORTING_COMPONENTS_BY_ID)
        ):
            raise EngineRecordValidationError("unknown_component", "Capability record names an unknown component.")
        expected_component = component_record_payload(component_id)
        if set(component) != set(expected_component):
            raise EngineRecordValidationError("unknown_field", "Component fields do not match the schema.")
        artifacts = component.get("artifacts")
        if not isinstance(artifacts, list) or any(not isinstance(item, dict) for item in artifacts):
            raise EngineRecordValidationError("invalid_record", "Component artifacts must be objects.")
        paths = [item.get("path") for item in artifacts]
        if len(paths) != len(set(paths)):
            raise EngineRecordValidationError("duplicate_artifact_path", "Component artifact paths must be unique.")
        expected_artifact_fields = set(expected_component["artifacts"][0])
        if any(set(item) != expected_artifact_fields for item in artifacts):
            raise EngineRecordValidationError("unknown_field", "Artifact fields do not match the schema.")
    engine_ids = [item.get("engine_id") for item in value["engines"]]
    if len(engine_ids) != len(set(engine_ids)):
        raise EngineRecordValidationError("duplicate_engine_id", "Engine identifiers must be unique.")
    for engine in value["engines"]:
        migrate_legacy_engine_record(engine)
    if value != engine_component_record_payload():
        raise EngineRecordValidationError("record_drift", "Capability record differs from the authoritative declaration.")
    return json.loads(json.dumps(value))


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
