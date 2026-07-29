from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from fish_cloud_credentials import (
    FishCredentialError,
    apply_fish_api_key_update,
    fish_credential_status,
)
from generation_state import fingerprint_value
from llm_config import (
    DEFAULT_API_KEY,
    DEFAULT_BACKEND,
    DEFAULT_BASE_URL,
    DEFAULT_CONTEXT_LENGTH,
    DEFAULT_CORRECTIVE_RETRY,
    DEFAULT_KEEP_ALIVE,
    DEFAULT_MODEL_NAME,
    DEFAULT_STRUCTURED_OUTPUT,
    DEFAULT_THINKING,
    DEFAULT_TIMEOUT,
)


SETTINGS_SCHEMA_VERSION = 1
MAX_CONFIG_BYTES = 8 * 1024 * 1024
KEEP_ALIVE_RE = re.compile(r"^-1$|^0$|^[1-9][0-9]*(?:ms|s|m|h)$")
LANGUAGE_RE = re.compile(r"[^<>\x00-\x1f\x7f]{1,80}")
BACKENDS = frozenset({"auto", "ollama", "openai"})
TTS_MODES = frozenset({"local", "external"})
FISH_MODELS = frozenset({"s2.1-pro-free", "s2-pro"})
FISH_API_KEY_MODES = frozenset({"preserve", "replace", "clear"})
MOTION_PREFERENCES = frozenset({"system", "reduced", "full"})
CONTRAST_PREFERENCES = frozenset({"system", "more", "standard"})
DENSITY_PREFERENCES = frozenset({"comfortable", "compact"})


class ApplicationSettingsError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.context = copy.deepcopy(dict(context or {}))

    def as_detail(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "message": str(self),
        }
        if self.context:
            result["context"] = copy.deepcopy(self.context)
        return result


def _mapping(value: Any) -> dict[str, Any]:
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _config_target(path: str | Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def _read_config(path: str | Path) -> dict[str, Any]:
    target = _config_target(path)
    if target.is_symlink():
        raise ApplicationSettingsError(
            "settings_config_unsafe",
            "The Alexandria configuration must be a regular file.",
        )
    if not target.exists():
        return {}
    if not target.is_file():
        raise ApplicationSettingsError(
            "settings_config_unsafe",
            "The Alexandria configuration path is not a regular file.",
        )
    try:
        if target.stat().st_size > MAX_CONFIG_BYTES:
            raise ApplicationSettingsError(
                "settings_config_too_large",
                "The Alexandria configuration exceeds the supported size limit.",
            )
        value = json.loads(target.read_text(encoding="utf-8"))
    except ApplicationSettingsError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApplicationSettingsError(
            "settings_config_unreadable",
            f"The Alexandria configuration could not be read: {exc}",
        ) from exc
    if not isinstance(value, Mapping):
        raise ApplicationSettingsError(
            "settings_config_invalid",
            "The Alexandria configuration must contain a JSON object.",
        )
    return copy.deepcopy(dict(value))


def _write_config(path: str | Path, value: Mapping[str, Any]) -> None:
    target = _config_target(path)
    if target.is_symlink():
        raise ApplicationSettingsError(
            "settings_config_unsafe",
            "The Alexandria configuration must be a regular file.",
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    rendered = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )
    if len(rendered) > MAX_CONFIG_BYTES:
        raise ApplicationSettingsError(
            "settings_config_too_large",
            "The Alexandria configuration exceeds the supported size limit.",
        )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def _text(
    value: Any,
    *,
    field: str,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ApplicationSettingsError(
            "settings_field_invalid",
            f"{field} must be text.",
            status_code=422,
            context={"field": field},
        )
    normalized = " ".join(value.split())
    if not normalized and not allow_empty:
        raise ApplicationSettingsError(
            "settings_field_required",
            f"{field} is required.",
            status_code=422,
            context={"field": field},
        )
    if len(normalized) > maximum:
        raise ApplicationSettingsError(
            "settings_field_too_long",
            f"{field} exceeds {maximum} characters.",
            status_code=422,
            context={"field": field, "maximum": maximum},
        )
    return normalized


def _language(value: Any, *, field: str) -> str:
    result = _text(value, field=field, maximum=80)
    if not LANGUAGE_RE.fullmatch(result):
        raise ApplicationSettingsError(
            "settings_language_invalid",
            f"{field} contains unsupported characters.",
            status_code=422,
            context={"field": field},
        )
    return result


def _boolean(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise ApplicationSettingsError(
            "settings_field_invalid",
            f"{field} must be true or false.",
            status_code=422,
            context={"field": field},
        )
    return value


def _integer(
    value: Any,
    *,
    field: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApplicationSettingsError(
            "settings_field_invalid",
            f"{field} must be a whole number.",
            status_code=422,
            context={"field": field},
        )
    if value < minimum or value > maximum:
        raise ApplicationSettingsError(
            "settings_field_out_of_range",
            f"{field} must be between {minimum} and {maximum}.",
            status_code=422,
            context={
                "field": field,
                "minimum": minimum,
                "maximum": maximum,
            },
        )
    return value


def _number(
    value: Any,
    *,
    field: str,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ApplicationSettingsError(
            "settings_field_invalid",
            f"{field} must be numeric.",
            status_code=422,
            context={"field": field},
        )
    result = float(value)
    if result < minimum or result > maximum:
        raise ApplicationSettingsError(
            "settings_field_out_of_range",
            f"{field} must be between {minimum:g} and {maximum:g}.",
            status_code=422,
            context={
                "field": field,
                "minimum": minimum,
                "maximum": maximum,
            },
        )
    return result


def _enum(value: Any, *, field: str, allowed: frozenset[str]) -> str:
    result = _text(value, field=field, maximum=80)
    if result not in allowed:
        raise ApplicationSettingsError(
            "settings_field_invalid",
            f"{field} has an unsupported value.",
            status_code=422,
            context={"field": field, "allowed": sorted(allowed)},
        )
    return result


def _url(value: Any, *, field: str, allow_empty: bool = False) -> str:
    result = _text(
        value,
        field=field,
        maximum=500,
        allow_empty=allow_empty,
    )
    if not result and allow_empty:
        return ""
    parsed = urlparse(result)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ApplicationSettingsError(
            "settings_url_invalid",
            f"{field} must be an HTTP or HTTPS URL.",
            status_code=422,
            context={"field": field},
        )
    if parsed.username or parsed.password:
        raise ApplicationSettingsError(
            "settings_url_credentials_forbidden",
            f"{field} must not contain embedded credentials.",
            status_code=422,
            context={"field": field},
        )
    return result.rstrip("/")


def _keep_alive(value: Any) -> int | str:
    if isinstance(value, bool):
        raise ApplicationSettingsError(
            "settings_keep_alive_invalid",
            "Keep alive must be -1, 0, or a duration such as 10m.",
            status_code=422,
            context={"field": "provider.keep_alive"},
        )
    if isinstance(value, int):
        if value in {-1, 0} or value > 0:
            return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if KEEP_ALIVE_RE.fullmatch(normalized):
            return int(normalized) if normalized in {"-1", "0"} else normalized
    raise ApplicationSettingsError(
        "settings_keep_alive_invalid",
        "Keep alive must be -1, 0, or a duration such as 10m.",
        status_code=422,
        context={"field": "provider.keep_alive"},
    )


def _default_application() -> dict[str, Any]:
    return {
        "preferences": {
            "default_source_language": "English",
            "default_output_language": "English",
            "confirm_before_destructive": True,
            "remember_last_project": True,
        },
        "accessibility": {
            "motion": "system",
            "contrast": "system",
            "density": "comfortable",
            "status_announcements": True,
        },
        "storage": {
            "rollback_retention_days": 30,
            "intermediate_retention_days": 7,
            "maximum_backup_gib": 10.0,
            "cleanup_mode": "manual_only",
        },
    }


def _normalized_application(value: Any) -> dict[str, Any]:
    source = _mapping(value)
    defaults = _default_application()
    preferences_source = _mapping(source.get("preferences"))
    accessibility_source = _mapping(source.get("accessibility"))
    storage_source = _mapping(source.get("storage"))
    return {
        **{
            key: copy.deepcopy(item)
            for key, item in source.items()
            if key not in {"preferences", "accessibility", "storage"}
        },
        "preferences": {
            "default_source_language": _language(
                preferences_source.get(
                    "default_source_language",
                    defaults["preferences"]["default_source_language"],
                ),
                field="preferences.default_source_language",
            ),
            "default_output_language": _language(
                preferences_source.get(
                    "default_output_language",
                    defaults["preferences"]["default_output_language"],
                ),
                field="preferences.default_output_language",
            ),
            "confirm_before_destructive": _boolean(
                preferences_source.get(
                    "confirm_before_destructive",
                    defaults["preferences"]["confirm_before_destructive"],
                ),
                field="preferences.confirm_before_destructive",
            ),
            "remember_last_project": _boolean(
                preferences_source.get(
                    "remember_last_project",
                    defaults["preferences"]["remember_last_project"],
                ),
                field="preferences.remember_last_project",
            ),
        },
        "accessibility": {
            "motion": _enum(
                accessibility_source.get(
                    "motion",
                    defaults["accessibility"]["motion"],
                ),
                field="accessibility.motion",
                allowed=MOTION_PREFERENCES,
            ),
            "contrast": _enum(
                accessibility_source.get(
                    "contrast",
                    defaults["accessibility"]["contrast"],
                ),
                field="accessibility.contrast",
                allowed=CONTRAST_PREFERENCES,
            ),
            "density": _enum(
                accessibility_source.get(
                    "density",
                    defaults["accessibility"]["density"],
                ),
                field="accessibility.density",
                allowed=DENSITY_PREFERENCES,
            ),
            "status_announcements": _boolean(
                accessibility_source.get(
                    "status_announcements",
                    defaults["accessibility"]["status_announcements"],
                ),
                field="accessibility.status_announcements",
            ),
        },
        "storage": {
            "rollback_retention_days": _integer(
                storage_source.get(
                    "rollback_retention_days",
                    defaults["storage"]["rollback_retention_days"],
                ),
                field="storage.rollback_retention_days",
                minimum=1,
                maximum=365,
            ),
            "intermediate_retention_days": _integer(
                storage_source.get(
                    "intermediate_retention_days",
                    defaults["storage"]["intermediate_retention_days"],
                ),
                field="storage.intermediate_retention_days",
                minimum=1,
                maximum=90,
            ),
            "maximum_backup_gib": _number(
                storage_source.get(
                    "maximum_backup_gib",
                    defaults["storage"]["maximum_backup_gib"],
                ),
                field="storage.maximum_backup_gib",
                minimum=1,
                maximum=1000,
            ),
            "cleanup_mode": "manual_only",
        },
    }


def _normalized_provider(value: Any, *, existing_api_key: str) -> tuple[dict[str, Any], str]:
    source = _mapping(value)
    backend = _enum(
        source.get("backend", DEFAULT_BACKEND),
        field="provider.backend",
        allowed=BACKENDS,
    )
    base_url = _url(
        source.get("base_url", DEFAULT_BASE_URL),
        field="provider.base_url",
    )
    model_name = _text(
        source.get("model_name", DEFAULT_MODEL_NAME),
        field="provider.model_name",
        maximum=200,
    )
    api_key_mode = _enum(
        source.get("api_key_mode", "preserve"),
        field="provider.api_key_mode",
        allowed=frozenset({"preserve", "replace", "clear"}),
    )
    if api_key_mode == "replace":
        api_key = _text(
            source.get("api_key", ""),
            field="provider.api_key",
            maximum=1000,
        )
    elif api_key_mode == "clear":
        if backend == "openai":
            raise ApplicationSettingsError(
                "settings_api_key_required",
                "OpenAI-compatible providers require an API key.",
                status_code=422,
                context={"field": "provider.api_key"},
            )
        api_key = ""
    else:
        api_key = existing_api_key
    if backend == "ollama" and urlparse(base_url).hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ApplicationSettingsError(
            "settings_ollama_url_not_local",
            "Native Ollama must use a local host URL.",
            status_code=422,
            context={"field": "provider.base_url"},
        )
    provider = {
        "backend": backend,
        "base_url": base_url,
        "model_name": model_name,
        "context_length": _integer(
            source.get("context_length", DEFAULT_CONTEXT_LENGTH),
            field="provider.context_length",
            minimum=1024,
            maximum=262144,
        ),
        "keep_alive": _keep_alive(
            source.get("keep_alive", DEFAULT_KEEP_ALIVE)
        ),
        "timeout": _integer(
            source.get("timeout", DEFAULT_TIMEOUT),
            field="provider.timeout",
            minimum=1,
            maximum=7200,
        ),
        "thinking": _boolean(
            source.get("thinking", DEFAULT_THINKING),
            field="provider.thinking",
        ),
        "structured_output": _boolean(
            source.get("structured_output", DEFAULT_STRUCTURED_OUTPUT),
            field="provider.structured_output",
        ),
        "corrective_retry": _boolean(
            source.get("corrective_retry", DEFAULT_CORRECTIVE_RETRY),
            field="provider.corrective_retry",
        ),
    }
    if not provider["structured_output"]:
        raise ApplicationSettingsError(
            "settings_structured_output_required",
            "Structured output is required by Alexandria's workflow contracts.",
            status_code=422,
            context={"field": "provider.structured_output"},
        )
    return provider, api_key


def _normalized_speech(
    value: Any,
    *,
    existing_fish_api_key_configured: bool,
) -> tuple[dict[str, Any], str, str]:
    source = _mapping(value)
    mode = _enum(
        source.get("mode", "local"),
        field="speech.mode",
        allowed=TTS_MODES,
    )
    url = _url(
        source.get("url", "http://127.0.0.1:7860"),
        field="speech.url",
        allow_empty=mode == "local",
    )
    if mode == "external" and not url:
        raise ApplicationSettingsError(
            "settings_tts_url_required",
            "External speech mode requires a server URL.",
            status_code=422,
            context={"field": "speech.url"},
        )
    fish_key_mode = _enum(
        source.get("fish_api_key_mode", "preserve"),
        field="speech.fish_api_key_mode",
        allowed=FISH_API_KEY_MODES,
    )
    fish_key = ""
    fish_key_configured = existing_fish_api_key_configured
    if fish_key_mode == "replace":
        fish_key = _text(
            source.get("fish_api_key", ""),
            field="speech.fish_api_key",
            maximum=1000,
        )
        fish_key_configured = True
    elif fish_key_mode == "clear":
        fish_key_configured = False
    fish_enabled = _boolean(
        source.get("fish_cloud_enabled", False),
        field="speech.fish_cloud_enabled",
    )
    if fish_enabled and not fish_key_configured:
        raise ApplicationSettingsError(
            "settings_fish_api_key_required",
            "Fish cloud speech requires a configured API key.",
            status_code=422,
            context={"field": "speech.fish_api_key"},
        )
    candidate_count = _integer(
        source.get("fish_candidate_count", 2),
        field="speech.fish_candidate_count",
        minimum=2,
        maximum=6,
    )
    difficult_count = _integer(
        source.get("fish_difficult_candidate_count", 6),
        field="speech.fish_difficult_candidate_count",
        minimum=2,
        maximum=8,
    )
    if difficult_count < candidate_count:
        raise ApplicationSettingsError(
            "settings_fish_candidate_count_invalid",
            "Difficult-line candidate count cannot be lower than the normal candidate count.",
            status_code=422,
            context={"field": "speech.fish_difficult_candidate_count"},
        )
    return (
        {
            "mode": mode,
            "url": url,
            "language": _language(
                source.get("language", "Auto"),
                field="speech.language",
            ),
            "parallel_workers": _integer(
                source.get("parallel_workers", 2),
                field="speech.parallel_workers",
                minimum=1,
                maximum=16,
            ),
            "pause_between_speakers_ms": _integer(
                source.get("pause_between_speakers_ms", 500),
                field="speech.pause_between_speakers_ms",
                minimum=0,
                maximum=5000,
            ),
            "pause_same_speaker_ms": _integer(
                source.get("pause_same_speaker_ms", 250),
                field="speech.pause_same_speaker_ms",
                minimum=0,
                maximum=5000,
            ),
            "fish_cloud_enabled": fish_enabled,
            "fish_model": _enum(
                source.get("fish_model", "s2.1-pro-free"),
                field="speech.fish_model",
                allowed=FISH_MODELS,
            ),
            "fish_candidate_count": candidate_count,
            "fish_difficult_candidate_count": difficult_count,
            "fish_text_wer_limit": _number(
                source.get("fish_text_wer_limit", 0.08),
                field="speech.fish_text_wer_limit",
                minimum=0.0,
                maximum=0.5,
            ),
            "fish_timeout_seconds": _integer(
                source.get("fish_timeout_seconds", 240),
                field="speech.fish_timeout_seconds",
                minimum=30,
                maximum=600,
            ),
            # This is a non-secret capability marker. The credential itself is
            # stored only in Keychain, an environment variable, or process memory.
            "fish_api_key_configured": fish_key_configured,
        },
        fish_key_mode,
        fish_key,
    )


def _settings_from_config(config: Mapping[str, Any]) -> dict[str, Any]:
    llm = _mapping(config.get("llm"))
    tts = _mapping(config.get("tts"))
    application = _normalized_application(config.get("application"))
    fish_status = fish_credential_status()
    fish_configured = bool(
        fish_status.configured
        or tts.get("fish_api_key_configured", False)
    )
    fish_source = (
        fish_status.source
        if fish_status.configured
        else "keychain"
        if tts.get("fish_api_key_configured", False)
        else "none"
    )
    provider = {
        "backend": llm.get("backend", DEFAULT_BACKEND),
        "base_url": llm.get("base_url", DEFAULT_BASE_URL),
        "model_name": llm.get("model_name", DEFAULT_MODEL_NAME),
        "context_length": llm.get("context_length", DEFAULT_CONTEXT_LENGTH),
        "keep_alive": llm.get("keep_alive", DEFAULT_KEEP_ALIVE),
        "timeout": llm.get("timeout", DEFAULT_TIMEOUT),
        "thinking": llm.get("thinking", DEFAULT_THINKING),
        "structured_output": llm.get(
            "structured_output",
            DEFAULT_STRUCTURED_OUTPUT,
        ),
        "corrective_retry": llm.get(
            "corrective_retry",
            DEFAULT_CORRECTIVE_RETRY,
        ),
        "api_key_configured": bool(str(llm.get("api_key", DEFAULT_API_KEY)).strip()),
        "api_key_mode": "preserve",
        "api_key": "",
    }
    speech = {
        "mode": tts.get("mode", "local"),
        "url": tts.get("url", "http://127.0.0.1:7860"),
        "language": tts.get("language", "Auto"),
        "parallel_workers": tts.get("parallel_workers", 2),
        "pause_between_speakers_ms": tts.get(
            "pause_between_speakers_ms",
            500,
        ),
        "pause_same_speaker_ms": tts.get(
            "pause_same_speaker_ms",
            250,
        ),
        "fish_cloud_enabled": bool(
            tts.get("fish_cloud_enabled", False)
        ),
        "fish_model": tts.get("fish_model", "s2.1-pro-free"),
        "fish_candidate_count": tts.get("fish_candidate_count", 2),
        "fish_difficult_candidate_count": tts.get(
            "fish_difficult_candidate_count",
            6,
        ),
        "fish_text_wer_limit": tts.get("fish_text_wer_limit", 0.08),
        "fish_timeout_seconds": tts.get("fish_timeout_seconds", 240),
        "fish_api_key_configured": fish_configured,
        "fish_api_key_source": fish_source,
        "fish_api_key_persistent": bool(
            fish_status.persistent
            or tts.get("fish_api_key_configured", False)
        ),
        "fish_api_key_mode": "preserve",
        "fish_api_key": "",
    }
    return {
        "preferences": application["preferences"],
        "provider": provider,
        "speech": speech,
        "accessibility": application["accessibility"],
        "storage": {
            **application["storage"],
            "enforcement_status": "policy_saved_not_enforced",
            "enforcement_message": (
                "Retention values are saved now. Guarded cleanup enforcement is "
                "implemented separately in Maintenance and audio-safety work."
            ),
        },
    }


def settings_fingerprint(config: Mapping[str, Any]) -> str:
    return fingerprint_value(config)


def get_application_settings(*, config_path: str | Path) -> dict[str, Any]:
    config = _read_config(config_path)
    return {
        "schema_version": SETTINGS_SCHEMA_VERSION,
        "config_exists": _config_target(config_path).exists(),
        "config_fingerprint": settings_fingerprint(config),
        "settings": _settings_from_config(config),
        "advanced_destinations": {
            "stage_profiles": {
                "destination": "more",
                "context": {
                    "tool": "maintenance",
                    "mode": "llm-profiles",
                    "return": "#/settings",
                },
            },
            "runtime_diagnostics": {
                "destination": "more",
                "context": {
                    "tool": "maintenance",
                    "mode": "runtime",
                    "return": "#/settings",
                },
            },
            "model_cache": {
                "destination": "more",
                "context": {
                    "tool": "model-cache",
                    "return": "#/settings",
                },
            },
            "advanced_generation": {
                "destination": "more",
                "context": {
                    "tool": "maintenance",
                    "mode": "advanced-generation",
                    "return": "#/settings",
                },
            },
        },
        "diagnostics_in_normal_settings": False,
        "repair_actions_in_normal_settings": False,
    }


def update_application_settings(
    *,
    config_path: str | Path,
    expected_config_fingerprint: str,
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    current = _read_config(config_path)
    actual_fingerprint = settings_fingerprint(current)
    if expected_config_fingerprint != actual_fingerprint:
        raise ApplicationSettingsError(
            "settings_config_conflict",
            "Settings changed since this view was loaded. Reload and try again.",
            status_code=409,
            context={"actual_config_fingerprint": actual_fingerprint},
        )
    if not isinstance(settings, Mapping):
        raise ApplicationSettingsError(
            "settings_payload_invalid",
            "Settings must contain a JSON object.",
            status_code=422,
        )
    incoming = dict(settings)
    required = {
        "preferences",
        "provider",
        "speech",
        "accessibility",
        "storage",
    }
    missing = sorted(required - set(incoming))
    unexpected = sorted(set(incoming) - required)
    if missing or unexpected:
        raise ApplicationSettingsError(
            "settings_payload_invalid",
            "Settings payload fields do not match the supported contract.",
            status_code=422,
            context={"missing": missing, "unexpected": unexpected},
        )
    llm = _mapping(current.get("llm"))
    existing_api_key = str(llm.get("api_key", DEFAULT_API_KEY))
    provider, api_key = _normalized_provider(
        incoming["provider"],
        existing_api_key=existing_api_key,
    )
    current_fish_status = fish_credential_status()
    speech, fish_key_mode, fish_key = _normalized_speech(
        incoming["speech"],
        existing_fish_api_key_configured=current_fish_status.configured,
    )
    application = _normalized_application(
        {
            "preferences": incoming["preferences"],
            "accessibility": incoming["accessibility"],
            "storage": incoming["storage"],
        }
    )
    updated = copy.deepcopy(current)
    updated_llm = _mapping(updated.get("llm"))
    updated_llm.update(provider)
    updated_llm["api_key"] = api_key
    updated["llm"] = updated_llm
    updated_tts = _mapping(updated.get("tts"))
    updated_tts.update(speech)
    updated["tts"] = updated_tts
    existing_application = _mapping(updated.get("application"))
    existing_application.update(application)
    updated["application"] = existing_application
    _write_config(config_path, updated)
    try:
        apply_fish_api_key_update(
            fish_key_mode,
            fish_key,
        )
    except FishCredentialError as exc:
        # Keep configuration and credential state transactional. A failed
        # Keychain operation must not leave Fish enabled without its key.
        _write_config(config_path, current)
        raise ApplicationSettingsError(
            "settings_fish_api_key_update_failed",
            str(exc),
            status_code=422,
            context={"field": "speech.fish_api_key"},
        ) from exc
    return get_application_settings(config_path=config_path)
