from __future__ import annotations

import importlib.util
import importlib.metadata as metadata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from hf_access import cached_snapshot_status
from model_registry import model_spec


EXPRESSIVE_CLONE_CANDIDATE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RepositoryRequirement:
    repo_id: str
    revision: str
    estimated_size_bytes: int
    required_paths: tuple[str, ...]
    purpose: str = "model"

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["required_paths"] = list(self.required_paths)
        return value


@dataclass(frozen=True)
class ExpressiveCloneCandidate:
    key: str
    label: str
    benchmark_order: int
    model_repo_id: str
    runtime_module: str
    control_mode: str
    license_id: str | None
    repository_requirements: tuple[RepositoryRequirement, ...]
    capabilities: tuple[str, ...]
    limitations: tuple[str, ...]
    comparison_only: bool = False
    required_next_blind_round: bool = False

    @property
    def estimated_download_size_bytes(self) -> int:
        return sum(
            item.estimated_size_bytes for item in self.repository_requirements
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "benchmark_order": self.benchmark_order,
            "model_repo_id": self.model_repo_id,
            "runtime_module": self.runtime_module,
            "control_mode": self.control_mode,
            "license_id": self.license_id,
            "repository_requirements": [
                item.as_dict() for item in self.repository_requirements
            ],
            "estimated_download_size_bytes": (
                self.estimated_download_size_bytes
            ),
            "capabilities": list(self.capabilities),
            "limitations": list(self.limitations),
            "comparison_only": self.comparison_only,
            "required_next_blind_round": self.required_next_blind_round,
            "evaluation_only": True,
            "production_assignment_supported": False,
            "delivery_control_validated": False,
        }


_QWEN_CLONE = model_spec("mlx_clone")
_VOXCPM2 = model_spec("mlx_controlled_clone")


_CANDIDATES = (
    ExpressiveCloneCandidate(
        key="fish_s2_pro",
        label="Fish Audio S2 Pro",
        benchmark_order=1,
        model_repo_id="mlx-community/fish-audio-s2-pro",
        runtime_module="mlx_audio.tts.models.fish_qwen3_omni",
        control_mode="inline_freeform_tags",
        license_id="other",
        repository_requirements=(
            RepositoryRequirement(
                repo_id="mlx-community/fish-audio-s2-pro",
                revision="eccd57bf5c1ebc13cb2f993df867f4e49931a36a",
                estimated_size_bytes=11_007_904_953,
                required_paths=(
                    "config.json",
                    "codec.safetensors",
                    "model.safetensors.index.json",
                    "model-00001-of-00002.safetensors",
                    "model-00002-of-00002.safetensors",
                    "tokenizer.json",
                ),
            ),
        ),
        capabilities=(
            "supplied_reference_identity",
            "exact_reference_transcript",
            "freeform_inline_delivery_tags",
            "word_or_phrase_level_control",
            "long_form_batching",
        ),
        limitations=(
            "Large local footprint for an evaluation candidate.",
            "Research-oriented upstream license requires release review.",
            "Alexandria has not measured identity, directionality, memory, or speed.",
        ),
    ),
    ExpressiveCloneCandidate(
        key="chatterbox_original",
        label="Chatterbox",
        benchmark_order=2,
        model_repo_id="mlx-community/chatterbox-4bit",
        runtime_module="mlx_audio.tts.models.chatterbox",
        control_mode="numeric_exaggeration_cfg",
        license_id="apache-2.0",
        repository_requirements=(
            RepositoryRequirement(
                repo_id="mlx-community/chatterbox-4bit",
                revision="f1d7b9696e1b6242e64eb8c4a823b6d1a50425a8",
                estimated_size_bytes=611_949_083,
                required_paths=(
                    "config.json",
                    "model.safetensors",
                    "conds.safetensors",
                    "tokenizer.json",
                ),
            ),
            RepositoryRequirement(
                repo_id="mlx-community/S3TokenizerV2",
                revision="e0c9886f0e1c35ae85b1f27277416fb19fc72bec",
                estimated_size_bytes=494_871_088,
                required_paths=(
                    "config.json",
                    "model.safetensors",
                ),
                purpose="speech tokenizer",
            ),
        ),
        capabilities=(
            "supplied_reference_identity",
            "numeric_exaggeration",
            "classifier_free_guidance",
        ),
        limitations=(
            "No arbitrary natural-language delivery instruction channel.",
            "Numeric exaggeration is only a proxy for the requested performance.",
            "Alexandria has not measured identity, directionality, memory, or speed.",
        ),
    ),
    ExpressiveCloneCandidate(
        key="chatterbox_turbo",
        label="Chatterbox-Turbo",
        benchmark_order=3,
        model_repo_id="mlx-community/chatterbox-turbo-4bit",
        runtime_module="mlx_audio.tts.models.chatterbox_turbo",
        control_mode="native_event_tags",
        license_id="apache-2.0",
        repository_requirements=(
            RepositoryRequirement(
                repo_id="mlx-community/chatterbox-turbo-4bit",
                revision="c63817725071d7b5269c7b558772d6e8cbf59cec",
                estimated_size_bytes=416_875_430,
                required_paths=(
                    "config.json",
                    "model.safetensors",
                    "conds.safetensors",
                    "tokenizer_config.json",
                    "vocab.json",
                    "merges.txt",
                ),
            ),
        ),
        capabilities=(
            "supplied_reference_identity",
            "native_paralinguistic_event_tags",
            "low_step_decoder",
        ),
        limitations=(
            "Event tags do not represent arbitrary prose stage direction.",
            "Exaggeration and CFG inputs are ignored by the Turbo runtime.",
            "Alexandria has not measured identity, directionality, memory, or speed.",
        ),
    ),
    ExpressiveCloneCandidate(
        key="tada_1b",
        label="TADA 1B MLX",
        benchmark_order=4,
        model_repo_id="HumeAI/mlx-tada-1b",
        runtime_module="mlx_audio.tts.models.tada",
        control_mode="reference_style_bank",
        license_id="llama3.2",
        repository_requirements=(
            RepositoryRequirement(
                repo_id="HumeAI/mlx-tada-1b",
                revision="b9e0e8c8f527464b9abd72c6fe3786f1f05ed1eb",
                estimated_size_bytes=4_587_636_911,
                required_paths=(
                    "model/config.json",
                    "model/weights.safetensors",
                    "aligner/weights.safetensors",
                    "decoder/weights.safetensors",
                    "encoder/weights.safetensors",
                ),
            ),
        ),
        capabilities=(
            "supplied_reference_identity",
            "exact_reference_transcript",
            "reference_style_bank",
            "duration_control",
        ),
        limitations=(
            "No arbitrary line-instruction channel is exposed by the MLX adapter.",
            "Non-neutral directions require separate approved reference clips.",
            "Llama 3.2 license terms apply.",
            "Alexandria has not measured identity, directionality, memory, or speed.",
        ),
    ),
    ExpressiveCloneCandidate(
        key="moss_tts_nano",
        label="MOSS-TTS Nano",
        benchmark_order=5,
        model_repo_id="mlx-community/MOSS-TTS-Nano-100M",
        runtime_module="mlx_audio.tts.models.moss_tts_nano",
        control_mode="reference_style_bank",
        license_id="apache-2.0",
        repository_requirements=(
            RepositoryRequirement(
                repo_id="mlx-community/MOSS-TTS-Nano-100M",
                revision="229a9c51bb0ffff6fd0dbe53b5bf0c441e438a79",
                estimated_size_bytes=285_462_910,
                required_paths=(
                    "config.json",
                    "model.safetensors",
                    "tokenizer.model",
                    "tokenizer_config.json",
                ),
            ),
            RepositoryRequirement(
                repo_id="mlx-community/MOSS-Audio-Tokenizer-Nano",
                revision="edccdfd96d5c21f1c078338a98d738b9a6bf6917",
                estimated_size_bytes=44_000_662,
                required_paths=(
                    "config.json",
                    "model.safetensors",
                ),
                purpose="audio tokenizer",
            ),
        ),
        capabilities=(
            "supplied_reference_identity",
            "reference_style_bank",
            "small_model_baseline",
            "multilingual_clone_baseline",
        ),
        limitations=(
            "No arbitrary line-instruction channel is exposed by the MLX adapter.",
            "Non-neutral directions require separate approved reference clips.",
            "Alexandria has not measured identity, directionality, memory, or speed.",
        ),
    ),
    ExpressiveCloneCandidate(
        key="moss_tts_local_v15",
        label="MOSS-TTS Local Transformer v1.5",
        benchmark_order=6,
        model_repo_id="OpenMOSS-Team/MOSS-TTS-Local-Transformer-v1.5",
        runtime_module="mlx_audio.tts.models.moss_tts",
        control_mode="instruction_and_pause_syntax",
        license_id="apache-2.0",
        repository_requirements=(
            RepositoryRequirement(
                repo_id="OpenMOSS-Team/MOSS-TTS-Local-Transformer-v1.5",
                revision="be7766a6735b98bd793f7c79fb720b4d0f5d13b8",
                estimated_size_bytes=9_116_898_371,
                required_paths=(
                    "config.json",
                    "model.safetensors",
                    "processor_config.json",
                    "tokenizer.json",
                ),
            ),
            RepositoryRequirement(
                repo_id="OpenMOSS-Team/MOSS-Audio-Tokenizer-v2",
                revision="f6e20e543b33d2c252a7ef71bdf8aa71e5ff9169",
                estimated_size_bytes=8_498_219_165,
                required_paths=(
                    "config.json",
                    "model.safetensors.index.json",
                    "model-00001-of-00003.safetensors",
                    "model-00002-of-00003.safetensors",
                    "model-00003-of-00003.safetensors",
                ),
                purpose="audio tokenizer",
            ),
        ),
        capabilities=(
            "supplied_reference_identity",
            "freeform_instruction_field",
            "explicit_pause_syntax",
            "streaming_runtime_path",
            "long_form_baseline",
        ),
        limitations=(
            "The instruction field has not been validated for arbitrary emotion control.",
            "Model plus tokenizer require a very large local footprint.",
            "Alexandria has not measured identity, directionality, memory, or speed.",
        ),
    ),
    ExpressiveCloneCandidate(
        key="qwen_icl_patch_baseline",
        label="Existing Qwen ICL patch",
        benchmark_order=7,
        model_repo_id=_QWEN_CLONE.repo_id,
        runtime_module="alexandria.mlx_backend",
        control_mode="untrained_instruction_embedding_patch",
        license_id=None,
        repository_requirements=(
            RepositoryRequirement(
                repo_id=_QWEN_CLONE.repo_id,
                revision=_QWEN_CLONE.revision,
                estimated_size_bytes=_QWEN_CLONE.estimated_size_bytes,
                required_paths=_QWEN_CLONE.required_paths,
            ),
        ),
        capabilities=(
            "supplied_reference_identity",
            "comparison_instruction_channel",
            "deterministic_seed",
        ),
        limitations=(
            "Instruction embedding injection is not a trained upstream interface.",
            "Post-generation prosody processing must be disabled for model-only comparison.",
            "Production delivery control is unaccepted.",
        ),
        comparison_only=True,
    ),
    ExpressiveCloneCandidate(
        key="voxcpm2_baseline",
        label="Existing VoxCPM2 comparison",
        benchmark_order=8,
        model_repo_id=_VOXCPM2.repo_id,
        runtime_module="alexandria.mlx_backend",
        control_mode="freeform_instruction_comparison",
        license_id=None,
        repository_requirements=(
            RepositoryRequirement(
                repo_id=_VOXCPM2.repo_id,
                revision=_VOXCPM2.revision,
                estimated_size_bytes=_VOXCPM2.estimated_size_bytes,
                required_paths=_VOXCPM2.required_paths,
            ),
        ),
        capabilities=(
            "supplied_reference_identity",
            "freeform_instruction_field",
            "deterministic_seed",
        ),
        limitations=(
            "Prior evidence did not establish reliable per-line delivery control.",
            "The next blind round must retest the strongest controllable-cloning path rather than reuse the flat baseline unchanged.",
            "Production delivery control is unaccepted.",
        ),
        comparison_only=True,
        required_next_blind_round=True,
    ),
)

CANDIDATES = {item.key: item for item in _CANDIDATES}


class ExpressiveCloneCandidateError(ValueError):
    pass


def expressive_clone_candidates() -> tuple[ExpressiveCloneCandidate, ...]:
    return _CANDIDATES


def primary_candidate_keys() -> tuple[str, ...]:
    return tuple(item.key for item in _CANDIDATES if not item.comparison_only)


def comparison_candidate_keys() -> tuple[str, ...]:
    return tuple(item.key for item in _CANDIDATES if item.comparison_only)


def required_next_blind_round_candidate_keys() -> tuple[str, ...]:
    return tuple(item.key for item in _CANDIDATES if item.required_next_blind_round)


def expressive_clone_candidate(identifier: str) -> ExpressiveCloneCandidate:
    key = str(identifier or "").strip()
    try:
        return CANDIDATES[key]
    except KeyError as exc:
        raise ExpressiveCloneCandidateError(
            f"Unknown expressive-clone candidate: {identifier!r}."
        ) from exc


def _module_available(module_name: str) -> bool:
    if module_name == "alexandria.mlx_backend":
        return True
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def expressive_clone_candidate_status(
    identifier: str,
    *,
    cache_dir: str | Path | None = None,
) -> dict[str, Any]:
    candidate = expressive_clone_candidate(identifier)
    repositories = []
    blockers = []
    for requirement in candidate.repository_requirements:
        status = cached_snapshot_status(
            requirement.repo_id,
            revision=requirement.revision,
            cache_dir=cache_dir,
            required_paths=requirement.required_paths,
        )
        repositories.append(
            {
                **requirement.as_dict(),
                "cache": status,
            }
        )
        if not status["cached"]:
            action = "repair" if status["state"] == "incomplete" else "download"
            blockers.append(
                {
                    "code": f"candidate_{action}_required",
                    "message": (
                        f"{requirement.repo_id}@{requirement.revision} is "
                        f"{status['state']} in the local Hugging Face cache."
                    ),
                    "repo_id": requirement.repo_id,
                    "revision": requirement.revision,
                    "action": action,
                }
            )

    adapter_available = _module_available(candidate.runtime_module)
    if not adapter_available:
        blockers.append(
            {
                "code": "candidate_runtime_adapter_missing",
                "message": (
                    f"The installed MLX-Audio runtime does not expose "
                    f"{candidate.runtime_module}."
                ),
                "module": candidate.runtime_module,
            }
        )

    return {
        "schema_version": EXPRESSIVE_CLONE_CANDIDATE_SCHEMA_VERSION,
        "candidate": candidate.as_dict(),
        "adapter_available": adapter_available,
        "repositories": repositories,
        "ready_for_benchmark": not blockers,
        "blockers": blockers,
        "evaluation_only": True,
        "production_assignment_supported": False,
    }


def expressive_clone_candidate_catalog(
    *,
    cache_dir: str | Path | None = None,
) -> dict[str, Any]:
    statuses = [
        expressive_clone_candidate_status(item.key, cache_dir=cache_dir)
        for item in _CANDIDATES
    ]
    return {
        "schema_version": EXPRESSIVE_CLONE_CANDIDATE_SCHEMA_VERSION,
        "mlx_audio_version": _package_version("mlx-audio"),
        "candidate_count": len(statuses),
        "primary_candidate_count": sum(
            not item["candidate"]["comparison_only"] for item in statuses
        ),
        "ready_candidate_count": sum(
            item["ready_for_benchmark"] for item in statuses
        ),
        "candidates": statuses,
        "required_next_blind_round_candidate_keys": list(
            required_next_blind_round_candidate_keys()
        ),
        "implicit_downloads_allowed": False,
        "manual_listening_required": True,
        "production_promotion_allowed": False,
    }
