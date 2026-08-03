from __future__ import annotations

import importlib.metadata as metadata
import json
import os
import shutil
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement
from packaging.version import Version

from fish_cloud_credentials import fish_credential_status
from instruction_propagation import (
    InstructionPropagationError,
    build_instruction_propagation_contract,
    validate_instruction_propagation_contract,
)
from model_registry import model_cache_status, model_spec


CAPABILITY_SCHEMA_VERSION = 1
PHASE22_OUTCOMES = frozenset(
    {
        "full_mlx_native_lora",
        "pytorch_mps_training_with_mlx_inference",
        "external_training_with_mlx_inference",
        "pytorch_only_lora",
        "import_only",
        "unsupported",
    }
)
STABLE_LORA_OUTCOME = "unsupported"
PHASE22_RESULT_GLOB = "*_phase22_apple_silicon.json"
CONTROLLED_CLONE_RESULT_GLOB = "*_qwen3_icl_instruction_clone.json"
LORA_SIDECAR_RESULT_GLOB = "*_mps_lora_merged_mlx.json"
CONTROLLED_CLONE_BACKEND = "qwen3_instruction_controlled"
CONTROLLED_CLONE_MODEL = model_spec("mlx_clone").repo_id
LORA_SIDECAR_ARCHITECTURE = (
    "mps_lora_training_merged_mlx_inference_experimental"
)


class VoiceBackendCapabilityError(RuntimeError):
    pass


def _fish_cloud_configuration(root_dir: str | Path) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    configured_path = os.environ.get("ALEXANDRIA_CONFIG_PATH")
    candidates = tuple(
        path
        for path in (
            Path(configured_path).expanduser().resolve()
            if configured_path
            else None,
            root / "app" / "config.json",
            root / "config.json",
        )
        if path is not None
    )
    tts: dict[str, Any] = {}
    for path in candidates:
        if not path.is_file() or path.is_symlink():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("tts"), dict):
            tts = dict(payload["tts"])
            break
    runtime_credential = fish_credential_status(check_keychain=True)
    saved_secure_marker = bool(tts.get("fish_api_key_configured", False))
    credential_configured = runtime_credential.configured
    credential_source = runtime_credential.source
    enabled = bool(tts.get("fish_cloud_enabled", False))
    return {
        "available": enabled and credential_configured,
        "enabled": enabled,
        "credential_configured": credential_configured,
        "credential_source": credential_source,
        "credential_persistent": runtime_credential.persistent,
        "saved_secure_marker_present": saved_secure_marker,
        "credential_marker_stale": bool(
            saved_secure_marker and not runtime_credential.configured
        ),
        "model": str(tts.get("fish_model", "s2.1-pro-free")),
        "automatic_candidate_selection": True,
        "exact_text_validation": True,
        "speaker_similarity_selection": True,
        "delivery_scoring": True,
        "manual_review_required": False,
        "production_default": False,
    }


def _package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _requirements(name: str) -> list[str]:
    try:
        return list(metadata.distribution(name).requires or [])
    except metadata.PackageNotFoundError:
        return []


def _requirement_for(
    package: str,
    dependency: str,
) -> Requirement | None:
    wanted = dependency.casefold().replace("_", "-")
    for raw in _requirements(package):
        try:
            requirement = Requirement(raw)
        except Exception:
            continue
        normalized = requirement.name.casefold().replace("_", "-")
        if normalized == wanted:
            return requirement
    return None


def _requirement_satisfied(
    requirement: Requirement | None,
    installed_version: str | None,
) -> bool | None:
    if requirement is None or installed_version is None:
        return None
    return Version(installed_version) in requirement.specifier


def latest_phase22_evidence_path(
    root_dir: str | Path,
) -> Path | None:
    results = Path(root_dir) / "benchmarks" / "results"
    if not results.exists():
        return None
    matches = sorted(results.glob(PHASE22_RESULT_GLOB))
    return matches[-1] if matches else None


def latest_controlled_clone_evidence_path(
    root_dir: str | Path,
) -> Path | None:
    results = Path(root_dir) / "benchmarks" / "results"
    if not results.exists():
        return None
    matches = sorted(results.glob(CONTROLLED_CLONE_RESULT_GLOB))
    return matches[-1] if matches else None


def load_controlled_clone_evidence(
    root_dir: str | Path,
) -> dict[str, Any] | None:
    path = latest_controlled_clone_evidence_path(root_dir)
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VoiceBackendCapabilityError(
            f"Controlled-clone evidence could not be read: {exc}"
        ) from exc
    if not isinstance(value, dict) or value.get("schema_version") != 2:
        raise VoiceBackendCapabilityError(
            "Controlled-clone evidence schema is unsupported."
        )
    if (
        value.get("backend") != CONTROLLED_CLONE_BACKEND
        or value.get("model") != CONTROLLED_CLONE_MODEL
    ):
        raise VoiceBackendCapabilityError(
            "Controlled-clone evidence names an unsupported backend or model."
        )
    measurements = value.get("measurements")
    acceptance = value.get("acceptance")
    if not isinstance(measurements, dict) or not isinstance(acceptance, dict):
        raise VoiceBackendCapabilityError(
            "Controlled-clone evidence is incomplete."
        )
    required_acceptance = {
        "delivery_directionality_passed",
        "speaker_identity_passed",
        "manual_audio_review_status",
    }
    if not required_acceptance.issubset(acceptance):
        raise VoiceBackendCapabilityError(
            "Controlled-clone evidence is missing directionality or listening acceptance."
        )
    return value


def latest_lora_sidecar_evidence_path(
    root_dir: str | Path,
) -> Path | None:
    results = Path(root_dir) / "benchmarks" / "results"
    if not results.exists():
        return None
    matches = sorted(results.glob(LORA_SIDECAR_RESULT_GLOB))
    return matches[-1] if matches else None


def load_lora_sidecar_evidence(
    root_dir: str | Path,
) -> dict[str, Any] | None:
    path = latest_lora_sidecar_evidence_path(root_dir)
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VoiceBackendCapabilityError(
            f"LoRA sidecar evidence could not be read: {exc}"
        ) from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise VoiceBackendCapabilityError(
            "LoRA sidecar evidence schema is unsupported."
        )
    if value.get("architecture") != LORA_SIDECAR_ARCHITECTURE:
        raise VoiceBackendCapabilityError(
            "LoRA sidecar evidence names an unsupported architecture."
        )
    required_flags = {
        "shared_runtime_lora_supported": False,
        "experimental_sidecar_training_supported": True,
        "direct_pytorch_inference_performant": False,
        "merged_mlx_inference_technically_validated": True,
        "production_assignment_supported": False,
    }
    for key, expected in required_flags.items():
        if value.get(key) is not expected:
            raise VoiceBackendCapabilityError(
                f"LoRA sidecar evidence has an invalid {key} value."
            )
    training = value.get("training")
    mlx_export = value.get("mlx_export")
    quality = value.get("quality_review")
    if not all(
        isinstance(item, dict)
        for item in (training, mlx_export, quality)
    ):
        raise VoiceBackendCapabilityError(
            "LoRA sidecar evidence is incomplete."
        )
    return value


def installed_mlx_lora_artifacts(
    root_dir: str | Path,
) -> list[dict[str, Any]]:
    root = Path(root_dir).expanduser().resolve()
    manifest_path = root / "lora_models" / "manifest.json"
    if not manifest_path.is_file():
        return []
    try:
        entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(entries, list):
        return []
    installed = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if (
            entry.get("experimental") is not True
            or entry.get("technical_validation_passed") is not True
            or entry.get("production_assignment_supported") is not False
        ):
            continue
        relative = entry.get("mlx_model_path")
        if not isinstance(relative, str) or not relative.strip():
            continue
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        inner_manifest_path = candidate / "mlx_export_manifest.json"
        if not inner_manifest_path.is_file():
            continue
        try:
            inner = json.loads(
                inner_manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if (
            not isinstance(inner, dict)
            or inner.get("artifact_format")
            != "merged_mlx_qwen_checkpoint"
            or inner.get("status") != "validated_experimental"
            or inner.get("technical_validation_passed") is not True
            or inner.get("production_assignment_supported") is not False
            or inner.get("export_fingerprint")
            != entry.get("export_fingerprint")
        ):
            continue
        try:
            entry_propagation = (
                validate_instruction_propagation_contract(
                    entry.get("instruction_propagation")
                )
                if entry.get("instruction_propagation") is not None
                else build_instruction_propagation_contract(
                    mode="identity_only",
                    samples=[],
                )
            )
            inner_propagation = (
                validate_instruction_propagation_contract(
                    inner.get("instruction_propagation")
                )
                if inner.get("instruction_propagation") is not None
                else build_instruction_propagation_contract(
                    mode="identity_only",
                    samples=[],
                )
            )
        except InstructionPropagationError:
            continue
        if (
            entry_propagation["propagation_fingerprint"]
            != inner_propagation["propagation_fingerprint"]
        ):
            continue
        if not all(
            (candidate / name).is_file()
            for name in (
                "model.safetensors",
                "config.json",
                "ref_sample.wav",
                "ref_sample.txt",
                "validation_neutral.wav",
                "validation_expressive.wav",
                "speech_tokenizer/model.safetensors",
            )
        ):
            continue
        installed.append(
            {
                "id": entry.get("id"),
                "name": entry.get("name"),
                "mlx_model_path": relative,
                "export_fingerprint": entry.get("export_fingerprint"),
                "base_model_revision": entry.get("base_model_revision"),
                "manual_audio_review_status": entry.get(
                    "manual_audio_review_status",
                    "pending",
                ),
                "neutral_rtf": entry.get("neutral_rtf"),
                "expressive_rtf": entry.get("expressive_rtf"),
                "speaker_cosine_floor": entry.get("speaker_cosine_floor"),
                "instruction_propagation": entry_propagation,
                "instruction_mode": entry_propagation["mode"],
                "instruction_required_at_inference": entry_propagation[
                    "instruction_required_at_inference"
                ],
                "production_assignment_supported": False,
            }
        )
    return installed


def load_phase22_evidence(
    root_dir: str | Path,
) -> dict[str, Any] | None:
    path = latest_phase22_evidence_path(root_dir)
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VoiceBackendCapabilityError(
            f"Phase 22 evidence could not be read: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise VoiceBackendCapabilityError(
            "Phase 22 evidence root must be a JSON object."
        )
    outcome = value.get("stable_lora_outcome")
    if outcome not in PHASE22_OUTCOMES:
        raise VoiceBackendCapabilityError(
            "Phase 22 evidence has an unsupported LoRA outcome."
        )
    return value


def build_voice_backend_capabilities(
    *,
    root_dir: str | Path,
) -> dict[str, Any]:
    transformers_version = _package_version("transformers")
    qwen_tts_version = _package_version("qwen-tts")
    mlx_audio_version = _package_version("mlx-audio")
    qwen_transformers = _requirement_for(
        "qwen-tts",
        "transformers",
    )
    mlx_transformers = _requirement_for(
        "mlx-audio",
        "transformers",
    )
    qwen_requirement_satisfied = _requirement_satisfied(
        qwen_transformers,
        transformers_version,
    )
    mlx_requirement_satisfied = _requirement_satisfied(
        mlx_transformers,
        transformers_version,
    )
    requirement_conflict = (
        qwen_transformers is not None
        and mlx_transformers is not None
        and transformers_version is not None
        and qwen_requirement_satisfied is False
        and mlx_requirement_satisfied is True
    )
    sox_available = shutil.which("sox") is not None
    pytorch_base_cached = model_cache_status(
        "pytorch_qwen_base"
    )["cached"]
    mlx_models = {
        "voice_design": model_cache_status("mlx_voice_design")["cached"],
        "clone": model_cache_status("mlx_clone")["cached"],
        "custom_voice": model_cache_status("mlx_custom_voice")["cached"],
        "controlled_clone_voxcpm2": model_cache_status(
            "mlx_controlled_clone"
        )["cached"],
    }
    blockers = []
    if requirement_conflict:
        blockers.append(
            "qwen-tts and mlx-audio require incompatible Transformers major versions in the shared environment."
        )
    elif qwen_requirement_satisfied is False:
        blockers.append(
            "The installed Transformers version does not satisfy qwen-tts."
        )
    if not sox_available:
        blockers.append(
            "SoX is not installed in the Alexandria environment."
        )
    if not pytorch_base_cached:
        blockers.append(
            "The official PyTorch Qwen3-TTS Base checkpoint is not cached locally."
        )
    blockers.append(
        "The shared runtime has no PEFT adapter-training or dynamic adapter-loading implementation."
    )
    evidence = load_phase22_evidence(root_dir)
    controlled_clone_evidence = load_controlled_clone_evidence(root_dir)
    lora_sidecar_evidence = load_lora_sidecar_evidence(root_dir)
    outcome = (
        evidence.get("stable_lora_outcome")
        if evidence is not None
        else STABLE_LORA_OUTCOME
    )
    controlled_evidence_current = bool(
        controlled_clone_evidence is not None
        and controlled_clone_evidence.get("backend")
        == CONTROLLED_CLONE_BACKEND
        and controlled_clone_evidence.get("model")
        == CONTROLLED_CLONE_MODEL
    )
    controlled_acceptance = (
        controlled_clone_evidence.get("acceptance", {})
        if controlled_evidence_current
        else {}
    )
    controlled_supported = bool(
        mlx_audio_version
        and controlled_evidence_current
        and controlled_acceptance.get("delivery_directionality_passed")
        is True
        and controlled_acceptance.get("speaker_identity_passed") is True
        and controlled_acceptance.get("manual_audio_review_status")
        == "approved"
    )
    controlled_preview_available = bool(
        mlx_audio_version
        and mlx_models["clone"]
    )
    sidecar_training = (
        lora_sidecar_evidence.get("training", {})
        if lora_sidecar_evidence is not None
        else {}
    )
    sidecar_pytorch = (
        lora_sidecar_evidence.get("pytorch_adapter_inference", {})
        if lora_sidecar_evidence is not None
        else {}
    )
    sidecar_export = (
        lora_sidecar_evidence.get("mlx_export", {})
        if lora_sidecar_evidence is not None
        else {}
    )
    sidecar_quality = (
        lora_sidecar_evidence.get("quality_review", {})
        if lora_sidecar_evidence is not None
        else {}
    )
    installed_lora_artifacts = installed_mlx_lora_artifacts(root_dir)
    exported_mlx_inference_supported = bool(
        mlx_audio_version
        and lora_sidecar_evidence is not None
        and lora_sidecar_evidence.get(
            "merged_mlx_inference_technically_validated"
        )
        is True
        and installed_lora_artifacts
    )
    if not installed_lora_artifacts:
        blockers.append(
            "No technically validated standalone MLX LoRA artifact is installed."
        )
    return {
        "schema_version": CAPABILITY_SCHEMA_VERSION,
        "stable_lora_outcome": outcome,
        "lora_training_supported": False,
        "lora_inference_supported": exported_mlx_inference_supported,
        "training_action_enabled": False,
        "reason": (
            (
                "Shared-runtime PEFT training and dynamic adapter loading remain "
                "unsupported. Technically validated standalone MLX LoRA inference "
                "is available for installed experimental artifacts; production "
                "assignment remains blocked pending dataset and listening review."
            )
            if exported_mlx_inference_supported
            else (
                "Qwen LoRA remains unsupported in the shared Apple Silicon "
                "runtime. Qwen instruction-injected clone previews are available "
                "for controlled comparison, but production delivery control is "
                "not accepted until directionality and listening review pass."
            )
        ),
        "blockers": blockers,
        "fish_s21_cloud": _fish_cloud_configuration(root_dir),
        "expressive_clone": {
            "supported": controlled_supported,
            "experimental_preview_available": controlled_preview_available,
            "status": (
                "approved" if controlled_supported else "experimental_unaccepted"
            ),
            "backend": CONTROLLED_CLONE_BACKEND,
            "model": CONTROLLED_CLONE_MODEL,
            "legacy_backend": "voxcpm2_controlled",
            "legacy_backend_supported": False,
            "uses_supplied_reference_identity": True,
            "per_line_instruction_supported": controlled_supported,
            "instruction_channel_present": controlled_preview_available,
            "production_default": False,
            "preview_and_manual_review_required": True,
            "model_cached": mlx_models["clone"],
            "measurements": (
                controlled_clone_evidence.get("measurements", {})
                if controlled_evidence_current
                else {}
            ),
            "acceptance": controlled_acceptance,
            "evidence_path": (
                str(latest_controlled_clone_evidence_path(root_dir))
                if controlled_evidence_current
                else None
            ),
            "warning": (
                None
                if controlled_supported
                else (
                    "The previous VoxCPM2 path was not a valid per-line delivery "
                    "control channel. Qwen ICL instruction injection is available "
                    "for preview comparison only until current listening evidence passes."
                )
            ),
        },
        "experimental_lora_sidecar": {
            "available": lora_sidecar_evidence is not None,
            "architecture": LORA_SIDECAR_ARCHITECTURE,
            "training_supported": bool(
                lora_sidecar_evidence
                and lora_sidecar_evidence.get(
                    "experimental_sidecar_training_supported"
                )
                is True
            ),
            "training_device": (
                lora_sidecar_evidence.get("model_probe", {}).get("device")
                if lora_sidecar_evidence is not None
                else None
            ),
            "training_status": "experimental",
            "trainable_parameters": sidecar_training.get(
                "trainable_parameters"
            ),
            "trainable_percent": sidecar_training.get(
                "trainable_percent"
            ),
            "measured_step_seconds": (
                sidecar_training.get("step_metrics", [{}])[0].get(
                    "step_seconds"
                )
                if sidecar_training.get("step_metrics")
                else None
            ),
            "measured_mps_current_allocated_gib": (
                sidecar_training.get("step_metrics", [{}])[0].get(
                    "mps_current_allocated_gib"
                )
                if sidecar_training.get("step_metrics")
                else None
            ),
            "direct_pytorch_inference_performant": bool(
                lora_sidecar_evidence
                and lora_sidecar_evidence.get(
                    "direct_pytorch_inference_performant"
                )
                is True
            ),
            "direct_pytorch_inference_rtf": sidecar_pytorch.get(
                "real_time_factor"
            ),
            "merged_mlx_inference_technically_validated": bool(
                lora_sidecar_evidence
                and lora_sidecar_evidence.get(
                    "merged_mlx_inference_technically_validated"
                )
                is True
            ),
            "inference_supported": exported_mlx_inference_supported,
            "installed_artifact_count": len(installed_lora_artifacts),
            "installed_artifacts": installed_lora_artifacts,
            "merged_mlx_validation": sidecar_export.get(
                "validation",
                {},
            ),
            "per_line_instruction_supported": bool(
                sidecar_export.get("validation", {}).get(
                    "instruction_channel_changed_output"
                )
                is True
            ),
            "production_assignment_supported": False,
            "manual_audio_review_required": bool(
                sidecar_quality.get("manual_audio_review_required", True)
            ),
            "manual_audio_review_status": sidecar_quality.get(
                "manual_audio_review_status",
                "pending",
            ),
            "multi_sample_multi_epoch_validation_required": bool(
                sidecar_quality.get(
                    "multi_sample_multi_epoch_validation_required",
                    True,
                )
            ),
            "evidence_path": (
                str(latest_lora_sidecar_evidence_path(root_dir))
                if lora_sidecar_evidence is not None
                else None
            ),
        },
        "environment": {
            "qwen_tts_version": qwen_tts_version,
            "mlx_audio_version": mlx_audio_version,
            "transformers_version": transformers_version,
            "qwen_tts_transformers_requirement": (
                str(qwen_transformers.specifier)
                if qwen_transformers is not None
                else None
            ),
            "mlx_audio_transformers_requirement": (
                str(mlx_transformers.specifier)
                if mlx_transformers is not None
                else None
            ),
            "qwen_tts_requirement_satisfied": (
                qwen_requirement_satisfied
            ),
            "mlx_audio_requirement_satisfied": (
                mlx_requirement_satisfied
            ),
            "transformers_requirement_conflict": requirement_conflict,
            "sox_available": sox_available,
            "pytorch_base_model_cached": pytorch_base_cached,
            "mlx_models_cached": mlx_models,
        },
        "measured_inference": (
            evidence.get("tts_measurements", {})
            if evidence is not None
            else {}
        ),
        "evidence_path": (
            str(latest_phase22_evidence_path(root_dir))
            if evidence is not None
            else None
        ),
    }


def require_lora_training_supported(
    *,
    root_dir: str | Path,
) -> dict[str, Any]:
    capabilities = build_voice_backend_capabilities(
        root_dir=root_dir,
    )
    if not capabilities["lora_training_supported"]:
        raise VoiceBackendCapabilityError(
            capabilities["reason"]
            + " "
            + " ".join(capabilities["blockers"])
        )
    return capabilities
