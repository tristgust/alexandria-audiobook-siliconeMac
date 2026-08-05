from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Iterable

from model_registry import (
    ModelSpec,
    engine_component_record_payload,
    engine_record_fingerprint,
    engine_record_payload,
    model_spec,
    registered_models,
    resolve_model_path,
)


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
    "mlx_fish_s2_pro": (
        "app/responsive_voice_models.py",
        'LOCAL_FISH_S2_PRO_MODEL_KEY = "mlx_fish_s2_pro"',
    ),
    "mlx_whisper_large_v3_turbo": ("app/alexandria_preparer.py", 'mlx_whisper_large_v3_turbo'),
    "mlx_whisper_base": ("benchmarks/transcription_evaluator.py", 'EVALUATOR_MODEL_KEY = "mlx_whisper_base"'),
    "pytorch_scrappylabs_narrator": (
        "app/community_qwen_candidates.py",
        '"model_key": "pytorch_scrappylabs_narrator"',
    ),
    "pytorch_qwen_custom_voice": ("app/tts.py", 'pytorch_qwen_custom_voice'),
    "pytorch_qwen_voice_design": ("app/tts.py", 'pytorch_qwen_voice_design'),
    "pytorch_qwen_base": ("app/tts.py", 'pytorch_qwen_base'),
    "pytorch_stable_audio_open_small": (
        "app/sound_effects.py",
        'SOUND_EFFECT_MODEL_KEY = "pytorch_stable_audio_open_small"',
    ),
    "pytorch_t5_base_sound_effects": (
        "app/sound_effects.py",
        'SOUND_EFFECT_TEXT_ENCODER_KEY = "pytorch_t5_base_sound_effects"',
    ),
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


def _engine_ids_for_runtime_values(
    expected: dict[str, dict[str, Any]],
    values: Iterable[str],
) -> set[str]:
    identifiers = {str(value) for value in values}
    matches: set[str] = set()
    unmatched = set(identifiers)
    for engine_id, record in expected.items():
        if engine_id in identifiers:
            matches.add(engine_id)
            unmatched.discard(engine_id)
            continue
        component_identifiers = {
            identifier
            for component in record["components"]
            for identifier in (
                component["component_id"],
                component["source_id"],
            )
        }
        matched_components = identifiers & component_identifiers
        if matched_components:
            matches.add(engine_id)
            unmatched.difference_update(matched_components)
    return matches | unmatched


def _production_engine_bindings(
    expected: dict[str, dict[str, Any]],
) -> tuple[dict[str, set[str]], dict[str, dict[str, Any]], dict[str, bool]]:
    import cast_aggregate
    import controlled_clone_preview
    import instruction_propagation
    import mlx_backend
    import produce_aggregate
    import recurring_voice_routing
    import responsive_voice_backend
    import synthesis_windows
    import tts
    import voice_backend_capabilities
    import voice_library

    root = Path(__file__).resolve().parents[1]
    backend_capabilities = voice_backend_capabilities.build_voice_backend_capabilities(
        root_dir=root
    )
    capability_windows = backend_capabilities["synthesis_windows"]["catalog"]
    live_windows = synthesis_windows.synthesis_window_catalog()

    tts_consumes_windows = (
        tts.plan_synthesis_segments is synthesis_windows.plan_synthesis_segments
    )
    mlx_sources = {
        mlx_backend.MLXBackend.CUSTOM_MODEL,
        mlx_backend.MLXBackend.CLONE_MODEL,
        mlx_backend.MLXBackend.DESIGN_MODEL,
        mlx_backend.MLXBackend.EXPRESSIVE_CLONE_MODEL,
    }
    mlx_engines = _engine_ids_for_runtime_values(expected, mlx_sources)
    if callable(getattr(mlx_backend.MLXBackend, "generate_community_qwen_pack", None)):
        community_source = model_spec("pytorch_scrappylabs_narrator").repo_id
        mlx_engines.update(
            _engine_ids_for_runtime_values(expected, {community_source})
        )

    expressive = backend_capabilities["expressive_clone"]
    cast_engine_bindings = (
        cast_aggregate.CONTROLLED_CLONE_BACKENDS
        | cast_aggregate.LEGACY_CONTROLLED_CLONE_BACKENDS
    ) - cast_aggregate.CONTROLLED_CLONE_COMPATIBILITY_ALIASES
    declarations = {
        "cast_aggregate": _engine_ids_for_runtime_values(
            expected,
            cast_engine_bindings,
        ),
        "controlled_clone_preview": _engine_ids_for_runtime_values(
            expected,
            {
                controlled_clone_preview.CONTROLLED_CLONE_BACKEND,
                controlled_clone_preview.LEGACY_CONTROLLED_CLONE_BACKEND,
            },
        ),
        "mlx_backend": mlx_engines,
        "responsive_voice_backend": _engine_ids_for_runtime_values(
            expected,
            {responsive_voice_backend.VOXCPM2_MODEL_KEY},
        ),
        "tts": set(live_windows) if tts_consumes_windows else set(),
        "voice_backend_capabilities": _engine_ids_for_runtime_values(
            expected,
            {expressive["backend"], expressive["legacy_backend"]},
        ),
        "voice_library": _engine_ids_for_runtime_values(
            expected,
            voice_library.CONTROLLED_CLONE_ENGINE_IDS,
        ),
    }

    controlled_instruction_engines = _engine_ids_for_runtime_values(
        expected,
        {
            tts.INSTRUCTION_CONTROLLED_BACKEND,
            voice_library.INSTRUCTION_CONTROLLED_ENGINE_ID,
            voice_backend_capabilities.CONTROLLED_CLONE_BACKEND,
        },
    )
    resolver_signature = inspect.signature(resolve_model_path)
    local_only_default = (
        resolver_signature.parameters["local_files_only"].default is True
    )
    resolver_source = inspect.getsource(resolve_model_path)
    actual: dict[str, dict[str, Any]] = {}
    for engine_id, synthesis_window in live_windows.items():
        if engine_id not in expected:
            continue
        instruction_supported = engine_id in controlled_instruction_engines
        synthesis_projection = {
            field: synthesis_window.get(field)
            for field in expected[engine_id]["synthesis_window"]
        }
        actual[engine_id] = {
            "synthesis_window": synthesis_projection,
            "instruction": {
                "supported": instruction_supported,
                "mode": "per_record" if instruction_supported else "identity_only",
                "contract": instruction_propagation.INSTRUCTION_PROPAGATION_CONTRACT,
                "formatter": instruction_propagation.INSTRUCTION_FORMATTER,
                "placement": instruction_propagation.INSTRUCTION_PLACEMENT,
            },
            "offline": {
                "local_only": local_only_default
                if expected[engine_id]["components"]
                else False,
                **(
                    {
                        "cache_policy": (
                            "pinned_snapshot"
                            if "revision=spec.revision" in resolver_source
                            else None
                        ),
                        "acquisition_policy": (
                            "explicit_maintenance"
                            if "model_cache_download_required" in resolver_source
                            else None
                        ),
                        "repair_policy": (
                            "explicit_repair"
                            if "model_cache_repair_required" in resolver_source
                            else None
                        ),
                    }
                    if expected[engine_id]["components"]
                    else {}
                ),
            },
        }
    surface_checks = {
        "backend_capability_synthesis_projection": (
            capability_windows == live_windows
        ),
        "produce_cast_projection": (
            produce_aggregate.inspect_cast_project
            is cast_aggregate.inspect_cast_project
        ),
        "cast_compatibility_alias_projection": (
            cast_aggregate.CONTROLLED_CLONE_COMPATIBILITY_ALIASES
            <= cast_aggregate.CONTROLLED_CLONE_BACKENDS
        ),
        "responsive_router_projection": (
            synthesis_windows.resolve_synthesis_backend_id(
                {
                    "type": "clone",
                    "clone_backend": recurring_voice_routing.ROUTED_CLONE_BACKEND,
                },
                mode="local",
                use_mlx=True,
            )
            in expected
        ),
    }
    return declarations, actual, surface_checks


def _projection_matches(reference: Any, projection: Any) -> bool:
    if isinstance(projection, dict):
        return isinstance(reference, dict) and all(
            field in reference
            and _projection_matches(reference[field], projected_value)
            for field, projected_value in projection.items()
        )
    return projection == reference


def audit_engine_record_truth() -> dict[str, Any]:
    catalog = engine_component_record_payload()
    expected = {
        item["engine_id"]: engine_record_payload(item["engine_id"])
        for item in catalog["engines"]
    }
    declared_consumers, actual, surface_checks = _production_engine_bindings(
        expected
    )
    expected_consumers: dict[str, set[str]] = {}
    for engine_id, record in expected.items():
        for consumer in record["consumers"]:
            expected_consumers.setdefault(consumer, set()).add(engine_id)
    issues: list[dict[str, Any]] = []
    for consumer in sorted(set(expected_consumers) | set(declared_consumers)):
        missing = expected_consumers.get(consumer, set()) - declared_consumers.get(
            consumer, set()
        )
        extra = declared_consumers.get(consumer, set()) - expected_consumers.get(
            consumer, set()
        )
        if missing:
            issues.append(
                _issue(
                    "registry_consumer_missing",
                    "A declared engine consumer is missing record engines.",
                    consumer=consumer,
                    engine_ids=sorted(missing),
                )
            )
        if extra:
            issues.append(
                _issue(
                    "registry_consumer_extra",
                    "A declared engine consumer names extra record engines.",
                    consumer=consumer,
                    engine_ids=sorted(extra),
                )
            )
    for engine_id in sorted(expected):
        declaration = actual.get(engine_id)
        if declaration is None:
            issues.append(
                _issue("omission", "Engine record is absent.", engine_id=engine_id)
            )
            continue
        reference = expected[engine_id]
        if declaration.get("readiness") == "ready" and not declaration.get(
            "supported"
        ):
            issues.append(
                _issue(
                    "unsupported_ready",
                    "An unsupported engine is declared ready.",
                    engine_id=engine_id,
                )
            )
        comparisons = (
            ("engine_revision", "engine_revision"),
            ("synthesis_window", "synthesis_mismatch"),
            ("instruction", "instruction_mismatch"),
            ("offline", "offline_mismatch"),
        )
        for field, kind in comparisons:
            if field in declaration and not _projection_matches(
                reference[field], declaration[field]
            ):
                issues.append(
                    _issue(
                        kind,
                        "Engine projection differs from the authoritative record.",
                        engine_id=engine_id,
                        field=field,
                    )
                )
    for surface, passed in surface_checks.items():
        if not passed:
            issues.append(
                _issue(
                    "production_binding_missing",
                    "A production engine projection is disconnected.",
                    surface=surface,
                )
            )
    result = {
        "schema_version": 1,
        "passed": not issues,
        "record_fingerprint": engine_record_fingerprint(catalog),
        "engine_count": len(expected),
        "consumer_count": len(expected_consumers),
        "surface_count": len(surface_checks),
        "issues": issues,
    }
    if issues:
        raise CapabilityTruthError(issues)
    return result


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
