from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class VoiceAliasError(ValueError):
    """A safe, user-facing voice-alias validation failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        speaker: str | None = None,
        target: str | None = None,
        chain: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.speaker = speaker
        self.target = target
        self.chain = tuple(chain or ())

    def detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.speaker is not None:
            detail["speaker"] = self.speaker
        if self.target is not None:
            detail["target"] = self.target
        if self.chain:
            detail["chain"] = list(self.chain)
        return detail


@dataclass(frozen=True)
class VoiceAliasResolution:
    speaker: str
    alias_of: str | None
    chain: tuple[str, ...]
    resolved_target: str
    resolved_type: str
    resolved_source: str

    @property
    def is_alias(self) -> bool:
        return self.alias_of is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "is_alias": self.is_alias,
            "alias_of": self.alias_of,
            "chain": list(self.chain),
            "resolved_target": self.resolved_target,
            "resolved_type": self.resolved_type,
            "resolved_source": self.resolved_source,
        }


def _require_voice_config(
    voice_config: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    if not isinstance(voice_config, Mapping):
        raise VoiceAliasError(
            "voice_config_invalid",
            "Voice configuration must be a JSON object.",
        )
    return voice_config


def _require_entry(
    speaker: str,
    entry: Any,
) -> Mapping[str, Any]:
    if not isinstance(entry, Mapping):
        raise VoiceAliasError(
            "voice_config_entry_invalid",
            f"Voice configuration for '{speaker}' must be an object.",
            speaker=speaker,
        )
    return entry


def _alias_target(
    speaker: str,
    entry: Mapping[str, Any],
) -> str | None:
    if "alias_of" in entry:
        raw_target = entry.get("alias_of")
    else:
        raw_target = entry.get("alias")

    if raw_target is None:
        return None
    if not isinstance(raw_target, str):
        raise VoiceAliasError(
            "alias_target_invalid",
            f"Alias target for '{speaker}' must be a speaker name.",
            speaker=speaker,
        )

    target = raw_target.strip()
    return target or None


def describe_voice_source(config: Mapping[str, Any]) -> str:
    voice_type = str(config.get("type") or "custom")
    if voice_type == "custom":
        return str(config.get("voice") or "Ryan")
    if voice_type == "clone":
        reference = str(config.get("ref_audio") or "").strip()
        return Path(reference).name if reference else "Supplied reference"
    if voice_type in {"lora", "builtin_lora"}:
        return str(
            config.get("adapter_id")
            or config.get("adapter_path")
            or "Adapter not selected"
        )
    if voice_type == "design":
        return str(config.get("description") or "Designed voice")
    return voice_type


def resolve_voice_alias(
    speaker: str,
    voice_config: Mapping[str, Any] | None,
) -> VoiceAliasResolution:
    config = _require_voice_config(voice_config)
    if not isinstance(speaker, str) or not speaker.strip():
        raise VoiceAliasError(
            "speaker_invalid",
            "Speaker name is required for alias resolution.",
        )

    original = speaker.strip()
    current = original
    chain = [current]
    seen = {current}
    first_alias: str | None = None

    while True:
        if current not in config:
            if current == original:
                resolved_config: Mapping[str, Any] = {}
                break
            raise VoiceAliasError(
                "alias_target_missing",
                f"Alias target '{current}' for '{original}' does not exist.",
                speaker=original,
                target=current,
                chain=chain,
            )

        entry = _require_entry(current, config[current])
        target = _alias_target(current, entry)
        if target is None:
            resolved_config = entry
            break
        if first_alias is None:
            first_alias = target
        if target == current:
            raise VoiceAliasError(
                "alias_self_reference",
                f"'{current}' cannot alias itself.",
                speaker=current,
                target=target,
                chain=chain + [target],
            )
        if target in seen:
            raise VoiceAliasError(
                "alias_cycle",
                f"Voice alias cycle detected: {' → '.join(chain + [target])}.",
                speaker=original,
                target=target,
                chain=chain + [target],
            )
        if target not in config:
            raise VoiceAliasError(
                "alias_target_missing",
                f"Alias target '{target}' for '{original}' does not exist.",
                speaker=original,
                target=target,
                chain=chain + [target],
            )
        _require_entry(target, config[target])
        current = target
        chain.append(current)
        seen.add(current)

    resolved_type = str(resolved_config.get("type") or "custom")
    return VoiceAliasResolution(
        speaker=original,
        alias_of=first_alias,
        chain=tuple(chain),
        resolved_target=current,
        resolved_type=resolved_type,
        resolved_source=describe_voice_source(resolved_config),
    )


def validate_voice_aliases(
    voice_config: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    config = _require_voice_config(voice_config)
    diagnostics: dict[str, dict[str, Any]] = {}
    for speaker, entry in config.items():
        if not isinstance(speaker, str) or not speaker.strip():
            raise VoiceAliasError(
                "speaker_invalid",
                "Every voice configuration key must be a non-empty speaker name.",
            )
        _require_entry(speaker, entry)
        diagnostics[speaker] = resolve_voice_alias(speaker, config).as_dict()
    return diagnostics


def merge_voice_config_updates(
    current_config: Mapping[str, Any] | None,
    updates: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    current = _require_voice_config(
        current_config if current_config is not None else {}
    )
    if not isinstance(updates, Mapping):
        raise VoiceAliasError(
            "voice_config_update_invalid",
            "Voice configuration update must be a JSON object.",
        )

    candidate: dict[str, Any] = copy.deepcopy(dict(current))
    for raw_name, raw_update in updates.items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise VoiceAliasError(
                "speaker_invalid",
                "Every voice configuration key must be a non-empty speaker name.",
            )
        voice_name = raw_name.strip()
        update = _require_entry(voice_name, raw_update)
        existing = candidate.get(voice_name, {})
        if existing is None:
            existing = {}
        existing = _require_entry(voice_name, existing)
        merged = copy.deepcopy(dict(existing))

        for field, value in update.items():
            if field == "alias_of":
                continue
            merged[field] = copy.deepcopy(value)

        if "alias_of" in update:
            raw_alias = update.get("alias_of")
            if raw_alias is None or (
                isinstance(raw_alias, str) and not raw_alias.strip()
            ):
                merged.pop("alias_of", None)
                merged.pop("alias", None)
            elif not isinstance(raw_alias, str):
                raise VoiceAliasError(
                    "alias_target_invalid",
                    f"Alias target for '{voice_name}' must be a speaker name.",
                    speaker=voice_name,
                )
            else:
                merged["alias_of"] = raw_alias.strip()
                merged.pop("alias", None)

        candidate[voice_name] = merged

    diagnostics = validate_voice_aliases(candidate)
    return candidate, diagnostics
