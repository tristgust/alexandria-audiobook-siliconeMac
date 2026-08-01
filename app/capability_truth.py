from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from model_registry import ModelSpec, registered_models


CAPABILITY_MODEL_BINDINGS = {
    "clone": "mlx_clone",
    "custom_voice": "mlx_custom_voice",
    "voice_design": "mlx_voice_design",
    "controlled_clone_voxcpm2": "mlx_controlled_clone",
    "pytorch_base_model_cached": "pytorch_qwen_base",
}

RUNTIME_SOURCE_BINDINGS = {
    "mlx_clone": ("app/mlx_backend.py", '"clone": "mlx_clone"'),
    "mlx_custom_voice": ("app/mlx_backend.py", '"custom": "mlx_custom_voice"'),
    "mlx_voice_design": ("app/mlx_backend.py", '"design": "mlx_voice_design"'),
    "mlx_controlled_clone": ("app/mlx_backend.py", '"expressive_clone": "mlx_controlled_clone"'),
    "mlx_whisper_large_v3_turbo": ("app/alexandria_preparer.py", 'mlx_whisper_large_v3_turbo'),
    "mlx_whisper_base": ("benchmarks/transcription_evaluator.py", 'EVALUATOR_MODEL_KEY = "mlx_whisper_base"'),
    "pytorch_scrappylabs_narrator": (
        "app/community_qwen_candidates.py",
        '"model_key": "pytorch_scrappylabs_narrator"',
    ),
    "pytorch_qwen_custom_voice": ("app/tts.py", 'pytorch_qwen_custom_voice'),
    "pytorch_qwen_voice_design": ("app/tts.py", 'pytorch_qwen_voice_design'),
    "pytorch_qwen_base": ("app/tts.py", 'pytorch_qwen_base'),
}


class CapabilityTruthError(RuntimeError):
    def __init__(self, issues: list[dict[str, Any]]):
        super().__init__(
            "Capability truth audit failed: "
            + ", ".join(sorted({str(item["kind"]) for item in issues}))
        )
        self.issues = issues


def _issue(kind: str, message: str, **context: Any) -> dict[str, Any]:
    return {"kind": kind, "message": message, "context": context}


def audit_capability_truth(
    *,
    repository_root: str | Path,
    capabilities: dict[str, Any],
    model_statuses: Iterable[dict[str, Any]],
    specs: Iterable[ModelSpec] | None = None,
) -> dict[str, Any]:
    root = Path(repository_root)
    registry = tuple(specs or registered_models())
    by_key = {item.key: item for item in registry}
    statuses = {
        item.get("model", {}).get("key"): item
        for item in model_statuses
        if isinstance(item, dict)
    }
    issues: list[dict[str, Any]] = []

    for spec in registry:
        if not spec.required_paths:
            issues.append(_issue("omission", "Registered model has no required-file manifest.", model_key=spec.key))
        binding = RUNTIME_SOURCE_BINDINGS.get(spec.key)
        if binding is None:
            issues.append(_issue("orphan", "Registered model has no runtime consumer binding.", model_key=spec.key))
            continue
        path, marker = binding
        try:
            source = (root / path).read_text(encoding="utf-8")
        except OSError:
            issues.append(_issue("orphan", "Runtime consumer source is unavailable.", model_key=spec.key, path=path))
            continue
        if marker not in source:
            issues.append(_issue("orphan", "Runtime consumer no longer references the registered model.", model_key=spec.key, path=path))

    for key in statuses:
        if key not in by_key:
            issues.append(_issue("phantom", "Status reports an unregistered model.", model_key=key))
    for key in by_key:
        if key not in statuses:
            issues.append(_issue("omission", "Registered model is absent from status output.", model_key=key))

    environment = capabilities.get("environment") or {}
    mlx_claims = environment.get("mlx_models_cached") or {}
    for claim, model_key in CAPABILITY_MODEL_BINDINGS.items():
        claimed = (
            environment.get(claim)
            if claim == "pytorch_base_model_cached"
            else mlx_claims.get(claim)
        )
        if claimed is None:
            issues.append(_issue("omission", "Capability payload omitted a declared cache claim.", claim=claim, model_key=model_key))
            continue
        status = statuses.get(model_key)
        actual = bool(status and status.get("cached"))
        if bool(claimed) != actual:
            issues.append(_issue("commission", "Capability cache claim disagrees with exact registry status.", claim=claim, model_key=model_key, claimed=bool(claimed), actual=actual))

    expressive = capabilities.get("expressive_clone") or {}
    if expressive.get("supported"):
        if not expressive.get("model_cached"):
            issues.append(_issue("unsupported_ready", "Expressive clone is marked supported without a cached model."))
        if expressive.get("per_line_instruction_supported") is not True:
            issues.append(_issue("unsupported_ready", "Expressive clone is marked supported without accepted instruction control."))
        acceptance = expressive.get("acceptance") or {}
        if acceptance.get("manual_audio_review_status") != "approved":
            issues.append(_issue("unsupported_ready", "Expressive clone is marked supported without approved listening evidence."))

    if capabilities.get("training_action_enabled") and not capabilities.get("lora_training_supported"):
        issues.append(_issue("unsupported_ready", "Training action is enabled while training support is false."))
    if capabilities.get("lora_inference_supported"):
        sidecar = capabilities.get("experimental_lora_sidecar") or {}
        if not sidecar.get("merged_mlx_inference_technically_validated") or not sidecar.get("installed_artifact_count"):
            issues.append(_issue("unsupported_ready", "LoRA inference is marked supported without a validated installed artifact."))

    interface_path = root / "app/static/specialists/model_cache.js"
    try:
        interface_source = interface_path.read_text(encoding="utf-8")
    except OSError:
        interface_source = ""
    for marker in ("required_by_default", "missing_required_paths", "data-maintenance-model-action"):
        if marker not in interface_source:
            issues.append(_issue("omission", "Canonical Maintenance omits required model truth.", marker=marker))
    if "snapshot_path" in interface_source or "cache_dir" in interface_source:
        issues.append(_issue("commission", "Canonical Maintenance exposes hidden cache internals."))

    result = {
        "schema_version": 1,
        "passed": not issues,
        "registry_count": len(registry),
        "status_count": len(statuses),
        "issues": issues,
    }
    if issues:
        raise CapabilityTruthError(issues)
    return result
