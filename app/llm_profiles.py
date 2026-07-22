from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Mapping

from generation_state import fingerprint_value
from llm_config import DEFAULT_MODEL_NAME


PROFILE_SCHEMA_VERSION = 1
PROFILE_STAGES = (
    "script",
    "review",
    "persona",
    "roster",
    "visual_discovery",
    "visual_compilation",
    "dataset_text",
    "transcript_cleanup",
)
PROFILE_STAGE_SET = frozenset(PROFILE_STAGES)
RUNTIME_OVERRIDE_KEYS = frozenset(
    {
        "base_url",
        "api_key",
        "model_name",
        "backend",
        "context_length",
        "keep_alive",
        "thinking",
        "structured_output",
        "corrective_retry",
        "timeout",
    }
)
EVIDENCE_KEYS = frozenset(
    {
        "benchmark_id",
        "compared_models",
        "quality_comparison_passed",
        "fidelity_validation_passed",
        "runtime_measurement_completed",
        "regression_tests_passed",
        "approved_at_utc",
        "notes",
    }
)


class LLMProfileError(RuntimeError):
    pass


class LLMProfileValidationError(LLMProfileError):
    pass


class LLMProfileConflictError(LLMProfileError):
    pass


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LLMProfileValidationError(
            f"{label} must be non-empty text."
        )
    return value.strip()


def _require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise LLMProfileValidationError(
            f"{label} must be boolean."
        )
    return value


def _require_stage(stage: Any) -> str:
    value = _require_text(stage, "LLM profile stage")
    if value not in PROFILE_STAGE_SET:
        raise LLMProfileValidationError(
            f"Unsupported LLM profile stage: {value!r}."
        )
    return value


def _require_timestamp(value: Any, label: str) -> str:
    text = _require_text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LLMProfileValidationError(
            f"{label} must be an ISO-8601 timestamp."
        ) from exc
    if parsed.tzinfo is None:
        raise LLMProfileValidationError(
            f"{label} must include a timezone."
        )
    return text


def _require_string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise LLMProfileValidationError(
            f"{label} must be a JSON array."
        )
    result = [_require_text(item, f"{label} item") for item in value]
    if len(result) != len(set(result)):
        raise LLMProfileValidationError(
            f"{label} must not contain duplicates."
        )
    return result


def validate_profile_evidence(
    value: Any,
    *,
    base_model: str,
    target_model: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise LLMProfileValidationError(
            "A model-changing stage profile requires evidence."
        )
    missing = sorted(EVIDENCE_KEYS - set(value))
    extra = sorted(set(value) - EVIDENCE_KEYS)
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise LLMProfileValidationError(
            "Profile evidence has " + "; ".join(details) + "."
        )
    compared = _require_string_list(
        value["compared_models"],
        "Profile evidence compared_models",
    )
    if base_model not in compared or target_model not in compared:
        raise LLMProfileValidationError(
            "Profile evidence must compare the inherited and target models."
        )
    gates = (
        "quality_comparison_passed",
        "fidelity_validation_passed",
        "runtime_measurement_completed",
        "regression_tests_passed",
    )
    normalized = {
        "benchmark_id": _require_text(
            value["benchmark_id"],
            "Profile evidence benchmark_id",
        ),
        "compared_models": compared,
        **{
            gate: _require_bool(
                value[gate],
                f"Profile evidence {gate}",
            )
            for gate in gates
        },
        "approved_at_utc": _require_timestamp(
            value["approved_at_utc"],
            "Profile evidence approved_at_utc",
        ),
        "notes": _require_string_list(
            value["notes"],
            "Profile evidence notes",
        ),
    }
    failed = [gate for gate in gates if not normalized[gate]]
    if failed:
        raise LLMProfileValidationError(
            "Profile evidence gates must all pass: "
            + ", ".join(failed)
            + "."
        )
    return normalized


def validate_stage_profile(
    value: Any,
    *,
    stage: str,
    base_model: str,
) -> dict[str, Any]:
    safe_stage = _require_stage(stage)
    if not isinstance(value, Mapping):
        raise LLMProfileValidationError(
            f"LLM profile {safe_stage!r} must be a JSON object."
        )
    schema_version = value.get("schema_version", PROFILE_SCHEMA_VERSION)
    if schema_version != PROFILE_SCHEMA_VERSION:
        raise LLMProfileValidationError(
            f"Unsupported LLM profile schema version for {safe_stage}."
        )
    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        raise LLMProfileValidationError(
            f"LLM profile {safe_stage}.enabled must be boolean."
        )
    overrides_value = value.get("overrides", {})
    if not isinstance(overrides_value, Mapping):
        raise LLMProfileValidationError(
            f"LLM profile {safe_stage}.overrides must be a JSON object."
        )
    unknown_overrides = sorted(
        set(overrides_value) - RUNTIME_OVERRIDE_KEYS
    )
    if unknown_overrides:
        raise LLMProfileValidationError(
            f"LLM profile {safe_stage} has unsupported overrides: "
            + ", ".join(unknown_overrides)
            + "."
        )
    overrides = copy.deepcopy(dict(overrides_value))
    if "model_name" in overrides:
        overrides["model_name"] = _require_text(
            overrides["model_name"],
            f"LLM profile {safe_stage}.overrides.model_name",
        )
    target_model = overrides.get("model_name", base_model)
    evidence_value = value.get("evidence")
    if target_model != base_model:
        evidence = validate_profile_evidence(
            evidence_value,
            base_model=base_model,
            target_model=target_model,
        )
    elif evidence_value is None:
        evidence = None
    else:
        evidence = validate_profile_evidence(
            evidence_value,
            base_model=base_model,
            target_model=target_model,
        )
    notes = _require_string_list(
        value.get("notes", []),
        f"LLM profile {safe_stage}.notes",
    )
    known = {
        "schema_version",
        "enabled",
        "overrides",
        "evidence",
        "notes",
    }
    preserved = {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key not in known
    }
    return {
        **preserved,
        "schema_version": PROFILE_SCHEMA_VERSION,
        "enabled": enabled,
        "overrides": overrides,
        "evidence": evidence,
        "notes": notes,
    }


def _llm_section(config: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(config, Mapping):
        return {}
    section = config.get("llm", {})
    return copy.deepcopy(dict(section)) if isinstance(section, Mapping) else {}


def profiles_fingerprint(config: Mapping[str, Any] | None) -> str:
    llm = _llm_section(config)
    profiles = llm.get("profiles", {})
    return fingerprint_value(profiles if isinstance(profiles, Mapping) else {})


def config_for_llm_stage(
    config: Mapping[str, Any] | None,
    *,
    stage: str,
) -> dict[str, Any]:
    safe_stage = _require_stage(stage)
    original = copy.deepcopy(dict(config)) if isinstance(config, Mapping) else {}
    llm = _llm_section(original)
    base_model = _require_text(
        llm.get("model_name", DEFAULT_MODEL_NAME),
        "Global LLM model_name",
    )
    profiles = llm.get("profiles", {})
    if profiles is None:
        profiles = {}
    if not isinstance(profiles, Mapping):
        raise LLMProfileValidationError(
            "llm.profiles must be a JSON object."
        )
    raw_profile = profiles.get(safe_stage)
    if raw_profile is None:
        original["llm"] = llm
        return original
    profile = validate_stage_profile(
        raw_profile,
        stage=safe_stage,
        base_model=base_model,
    )
    if profile["enabled"]:
        for key, value in profile["overrides"].items():
            llm[key] = copy.deepcopy(value)
    llm["profiles"] = copy.deepcopy(dict(profiles))
    original["llm"] = llm
    return original


def update_stage_profile(
    config: Mapping[str, Any] | None,
    *,
    stage: str,
    profile: Mapping[str, Any],
    expected_profiles_fingerprint: str,
) -> dict[str, Any]:
    safe_stage = _require_stage(stage)
    current_fingerprint = profiles_fingerprint(config)
    if current_fingerprint != expected_profiles_fingerprint:
        raise LLMProfileConflictError(
            "LLM profiles changed after this edit was loaded. Refresh and retry."
        )
    updated = copy.deepcopy(dict(config)) if isinstance(config, Mapping) else {}
    llm = _llm_section(updated)
    base_model = _require_text(
        llm.get("model_name", DEFAULT_MODEL_NAME),
        "Global LLM model_name",
    )
    normalized = validate_stage_profile(
        profile,
        stage=safe_stage,
        base_model=base_model,
    )
    profiles = llm.get("profiles", {})
    if not isinstance(profiles, Mapping):
        profiles = {}
    profiles = copy.deepcopy(dict(profiles))
    profiles[safe_stage] = normalized
    llm["profiles"] = profiles
    updated["llm"] = llm
    return updated


def remove_stage_profile(
    config: Mapping[str, Any] | None,
    *,
    stage: str,
    expected_profiles_fingerprint: str,
) -> dict[str, Any]:
    safe_stage = _require_stage(stage)
    current_fingerprint = profiles_fingerprint(config)
    if current_fingerprint != expected_profiles_fingerprint:
        raise LLMProfileConflictError(
            "LLM profiles changed after this edit was loaded. Refresh and retry."
        )
    updated = copy.deepcopy(dict(config)) if isinstance(config, Mapping) else {}
    llm = _llm_section(updated)
    profiles = llm.get("profiles", {})
    if isinstance(profiles, Mapping):
        profiles = copy.deepcopy(dict(profiles))
        profiles.pop(safe_stage, None)
        llm["profiles"] = profiles
    updated["llm"] = llm
    return updated


def build_profiles_status(
    config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    llm = _llm_section(config)
    base_model = _require_text(
        llm.get("model_name", DEFAULT_MODEL_NAME),
        "Global LLM model_name",
    )
    profiles = llm.get("profiles", {})
    if profiles is None:
        profiles = {}
    if not isinstance(profiles, Mapping):
        raise LLMProfileValidationError(
            "llm.profiles must be a JSON object."
        )
    entries = []
    for stage in PROFILE_STAGES:
        raw = profiles.get(stage)
        profile = (
            validate_stage_profile(
                raw,
                stage=stage,
                base_model=base_model,
            )
            if raw is not None
            else None
        )
        effective = config_for_llm_stage(config, stage=stage)
        effective_llm = _llm_section(effective)
        target_model = effective_llm.get("model_name", base_model)
        entries.append(
            {
                "stage": stage,
                "configured": profile is not None,
                "enabled": profile["enabled"] if profile else False,
                "inherits_global": (
                    profile is None
                    or not profile["enabled"]
                    or not profile["overrides"]
                ),
                "effective_model": target_model,
                "model_changed": target_model != base_model,
                "evidence_complete": (
                    profile is not None
                    and profile["evidence"] is not None
                ),
                "overrides": (
                    copy.deepcopy(profile["overrides"])
                    if profile
                    else {}
                ),
                "notes": list(profile["notes"]) if profile else [],
            }
        )
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "global_model": base_model,
        "profiles_fingerprint": profiles_fingerprint(config),
        "stages": entries,
    }
