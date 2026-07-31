from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from generation_state import fingerprint_value


CAST_AGGREGATE_SCHEMA_VERSION = 1
CAST_FILTERS = frozenset(
    {
        "all",
        "needs_attention",
        "unassigned",
        "speaking_roles",
        "non_speaking",
        "ready",
    }
)
CAST_READINESS_STATES = frozenset(
    {
        "needs_identity_review",
        "needs_voice",
        "preview_recommended",
        "ready",
    }
)
CONTROLLED_CLONE_BACKENDS = frozenset(
    {
        "qwen3_instruction_controlled",
        "controlled_clone",
        "instruction_controlled_clone",
    }
)
LEGACY_CONTROLLED_CLONE_BACKENDS = frozenset(
    {"voxcpm2_controlled"}
)
NON_SPEAKING_VALUES = frozenset(
    {
        "non-speaking",
        "non_speaking",
        "nonspeaking",
        "silent",
        "mentioned",
        "visual_only",
    }
)
RESOLVED_IDENTITY_VALUES = frozenset(
    {
        "resolved",
        "approved",
        "confirmed",
        "ready",
        "complete",
    }
)


class CastAggregateError(RuntimeError):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        detail: str,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.context = dict(context or {})

    def as_detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.detail,
            "context": self.context,
        }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = "\x1f".join(str(part or "") for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()[:20]}"


def _normalized(value: Any) -> str:
    text = _text(value) or ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = text.casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9()]+", " ", text)
    return " ".join(text.split())


def _label_key(value: Any) -> str:
    text = _normalized(value)
    if text.startswith("the "):
        text = text[4:]
    return text


def _spoken_key(value: Any) -> str:
    return " ".join((_text(value) or "").split()).casefold()


def _unique_texts(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _text(value)
        if text is None:
            continue
        key = _normalized(text)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _roster_entries(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        entries = value
    else:
        root = _mapping(value)
        entries = (
            _list(root.get("entries"))
            or _list(root.get("characters"))
            or _list(root.get("roster"))
        )
    return [dict(item) for item in entries if isinstance(item, Mapping)]


def _script_entries(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _voice_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    root = dict(value)
    nested = root.get("voices")
    if isinstance(nested, Mapping):
        return {str(key): copy for key, copy in nested.items()}
    return root


def _character_id(entry: Mapping[str, Any], index: int) -> tuple[str, bool]:
    identifier = (
        _text(entry.get("id"))
        or _text(entry.get("character_id"))
        or _text(entry.get("stable_character_id"))
    )
    if identifier:
        return identifier, True
    return (
        _stable_id(
            "character",
            entry.get("canonical_name"),
            entry.get("display_name"),
            index,
        ),
        False,
    )


def _character_names(entry: Mapping[str, Any]) -> list[str]:
    return _unique_texts(
        [
            entry.get("canonical_name"),
            entry.get("display_name"),
            entry.get("name"),
            *_list(entry.get("aliases")),
            *_list(entry.get("titles")),
            *_list(entry.get("nicknames")),
        ]
    )


def _script_index(entries: list[dict[str, Any]]) -> dict[str, Any]:
    labels: list[str] = []
    by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_text: dict[str, set[str]] = defaultdict(set)
    for index, entry in enumerate(entries):
        label = _text(entry.get("speaker"))
        text = _text(entry.get("text"))
        if label is None or text is None:
            continue
        if label not in by_label:
            labels.append(label)
        item = {**entry, "script_index": index}
        by_label[label].append(item)
        by_text[_spoken_key(text)].add(label)
    by_key: dict[str, set[str]] = defaultdict(set)
    for label in labels:
        by_key[_label_key(label)].add(label)
        by_key[_normalized(label)].add(label)
    return {
        "labels": labels,
        "by_label": by_label,
        "by_text": by_text,
        "by_key": by_key,
    }


def _name_variants(name: str) -> list[tuple[str, str, float]]:
    normalized = _normalized(name)
    stripped = _label_key(name)
    tokens = [token for token in stripped.split() if token]
    variants: list[tuple[str, str, float]] = []
    if normalized:
        variants.append((normalized, "exact_name", 1.0))
    if stripped and stripped != normalized:
        variants.append((stripped, "article_normalized", 0.98))
    if len(tokens) >= 2:
        variants.append((tokens[-1], "unique_surname", 0.84))
        variants.append((tokens[0], "unique_given_name", 0.8))
    elif len(tokens) == 1:
        variants.append((tokens[0], "single_name", 0.9))
    if "(" in normalized and ")" in normalized:
        variants.append((normalized.replace(" ", ""), "parenthetical_name", 0.96))
    return variants


def resolve_script_label(
    *,
    character: Mapping[str, Any],
    script_index: Mapping[str, Any],
) -> dict[str, Any]:
    labels = list(script_index.get("labels") or [])
    by_key = _mapping(script_index.get("by_key"))
    by_text = _mapping(script_index.get("by_text"))
    candidates: dict[str, tuple[float, str]] = {}

    explicit = (
        _text(character.get("script_voice_label"))
        or _text(character.get("resolved_script_voice_label"))
        or _text(character.get("speaker_label"))
    )
    if explicit:
        exact = [label for label in labels if _normalized(label) == _normalized(explicit)]
        if len(exact) == 1:
            return {
                "resolved_label": exact[0],
                "method": "explicit",
                "confidence": 1.0,
                "ambiguous": False,
                "candidate_labels": exact,
            }

    primary_names = _unique_texts(
        [
            character.get("canonical_name"),
            character.get("display_name"),
            character.get("name"),
        ]
    )
    primary_exact = _unique_texts(
        label
        for name in primary_names
        for label in labels
        if _normalized(label) == _normalized(name)
    )
    if len(primary_exact) == 1:
        return {
            "resolved_label": primary_exact[0],
            "method": "exact_name",
            "confidence": 1.0,
            "ambiguous": False,
            "candidate_labels": primary_exact,
        }
    if len(primary_exact) > 1:
        return {
            "resolved_label": None,
            "method": "ambiguous",
            "confidence": 1.0,
            "ambiguous": True,
            "candidate_labels": primary_exact,
        }

    for name in _character_names(character):
        for key, method, confidence in _name_variants(name):
            lookup_keys = {key, key.replace(" ", "")}
            for lookup_key in lookup_keys:
                matches = set(by_key.get(lookup_key) or [])
                if not matches:
                    matches = {
                        label
                        for label in labels
                        if _label_key(label).replace(" ", "") == lookup_key
                    }
                for label in matches:
                    current = candidates.get(label)
                    if current is None or confidence > current[0]:
                        candidates[label] = (confidence, method)

    sample_labels: set[str] = set()
    for sample in [
        *_list(character.get("sample_lines")),
        *_list(character.get("representative_lines")),
    ]:
        sample_text = sample.get("text") if isinstance(sample, Mapping) else sample
        key = _spoken_key(sample_text)
        if key:
            sample_labels.update(by_text.get(key) or set())
    if len(sample_labels) == 1:
        label = next(iter(sample_labels))
        current = candidates.get(label)
        if current is None or current[0] < 0.99:
            candidates[label] = (0.99, "representative_line")
    elif len(sample_labels) > 1:
        for label in sample_labels:
            current = candidates.get(label)
            if current is None or current[0] < 0.72:
                candidates[label] = (0.72, "ambiguous_representative_lines")

    if not candidates:
        return {
            "resolved_label": None,
            "method": None,
            "confidence": 0.0,
            "ambiguous": False,
            "candidate_labels": [],
        }
    ordered = sorted(
        candidates.items(),
        key=lambda item: (-item[1][0], labels.index(item[0]) if item[0] in labels else 999999),
    )
    best_confidence = ordered[0][1][0]
    best = [item for item in ordered if abs(item[1][0] - best_confidence) < 0.000001]
    if len(best) != 1:
        return {
            "resolved_label": None,
            "method": "ambiguous",
            "confidence": best_confidence,
            "ambiguous": True,
            "candidate_labels": [item[0] for item in best],
        }
    label, (confidence, method) = best[0]
    return {
        "resolved_label": label,
        "method": method,
        "confidence": confidence,
        "ambiguous": False,
        "candidate_labels": [item[0] for item in ordered],
    }


def _identity_resolved(entry: Mapping[str, Any], stable_id_present: bool) -> bool:
    if not stable_id_present:
        return False
    if entry.get("conflict_state") not in {None, False, "none", "resolved"}:
        return False
    if _list(entry.get("unresolved_questions")):
        return False
    status = _text(entry.get("resolution_status")) or _text(entry.get("status"))
    if status is None:
        return True
    return _normalized(status).replace(" ", "_") in RESOLVED_IDENTITY_VALUES


def _is_non_speaking(entry: Mapping[str, Any], line_count: int) -> bool:
    if line_count > 0:
        return False
    explicit = _bool(entry.get("speaking"))
    if explicit is False:
        return True
    status = _normalized(entry.get("speaking_status")).replace(" ", "_")
    if status in NON_SPEAKING_VALUES:
        return True
    return explicit is not True and status not in {"speaker", "speaking", "narrator"}


def _character_record_index(value: Any) -> dict[str, dict[str, tuple[int, dict[str, Any]]]]:
    """Index nested character records once while preserving DFS match order."""
    by_identifier: dict[str, tuple[int, dict[str, Any]]] = {}
    by_name: dict[str, tuple[int, dict[str, Any]]] = {}
    position = 0

    def visit(node: Any) -> None:
        nonlocal position
        if isinstance(node, Mapping):
            current_position = position
            position += 1
            record = dict(node)
            identifier = (
                _text(node.get("character_id"))
                or _text(node.get("stable_character_id"))
                or _text(node.get("roster_entry_id"))
                or _text(node.get("id"))
            )
            if identifier:
                by_identifier.setdefault(identifier, (current_position, record))
            node_names = {
                _normalized(node.get("character_name")),
                _normalized(node.get("display_name")),
                _normalized(node.get("canonical_name")),
                _normalized(node.get("speaker")),
                _normalized(node.get("name")),
            }
            node_names.discard("")
            for name in node_names:
                by_name.setdefault(name, (current_position, record))
            for nested in node.values():
                visit(nested)
        elif isinstance(node, list):
            for nested in node:
                visit(nested)

    visit(value)
    return {
        "by_identifier": by_identifier,
        "by_name": by_name,
    }


def _find_indexed_character_record(
    index: Mapping[str, Mapping[str, tuple[int, dict[str, Any]]]],
    *,
    character_id: str,
    names: list[str],
) -> dict[str, Any]:
    candidates: list[tuple[int, dict[str, Any]]] = []
    identifier_match = _mapping(index.get("by_identifier")).get(character_id)
    if identifier_match:
        candidates.append(identifier_match)
    by_name = _mapping(index.get("by_name"))
    for name in names:
        normalized = _normalized(name)
        if normalized and normalized in by_name:
            candidates.append(by_name[normalized])
    return dict(min(candidates, key=lambda item: item[0])[1]) if candidates else {}


def _voice_entry_for_character(
    *,
    voice_config: Mapping[str, Any],
    character_id: str,
    names: list[str],
    script_label: str | None,
) -> tuple[str | None, dict[str, Any]]:
    direct_keys = [character_id, script_label, *names]
    for key in direct_keys:
        if key is None:
            continue
        value = voice_config.get(key)
        if isinstance(value, Mapping):
            return str(key), dict(value)
    normalized_targets = {
        _normalized(value)
        for value in direct_keys
        if _normalized(value)
    }
    matches = [
        (str(key), dict(value))
        for key, value in voice_config.items()
        if isinstance(value, Mapping) and _normalized(key) in normalized_targets
    ]
    if len(matches) == 1:
        return matches[0]
    return None, {}


def _display_asset(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    if "/" in text or "\\" in text:
        return Path(text).name or None
    return text


def _rooted_path(root_dir: Path | None, value: Any) -> Path | None:
    text = _text(value)
    if text is None:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute() and root_dir is not None:
        path = root_dir / path
    try:
        return path.resolve()
    except OSError:
        return path


def _public_project_audio_url(root_dir: Path | None, path: Path | None) -> str | None:
    if root_dir is None or path is None or not path.is_file():
        return None
    try:
        relative = path.resolve().relative_to(root_dir.resolve())
    except (OSError, ValueError):
        return None
    if not relative.parts or relative.parts[0] not in {
        "clone_voices",
        "designed_voices",
        "lora_models",
        "builtin_lora",
        "dataset_builder",
        "voicelines",
    }:
        return None
    return f"/{relative.as_posix()}"


def _voice_summary(voice: Mapping[str, Any]) -> str:
    method = _normalized(voice.get("selected_production_method")).replace(" ", "_")
    if method in {
        "clone",
        "supplied_recording_clone",
        "controlled_clone",
        "instruction_controlled_clone",
    }:
        clone = _mapping(voice.get("clone"))
        return (
            "Instruction-controlled clone"
            if clone.get("controlled_capability") is True
            else "Supplied-recording clone"
        )
    if method in {"design", "designed", "designed_voice", "voice_design"}:
        return "Designed Voice"
    if method in {"lora", "adapter", "trained_voice"}:
        return "Voice adapter"
    if method == "alias":
        target = _text(_mapping(voice.get("alias")).get("target"))
        return f"Alias to {target}" if target else "Voice alias"
    return (
        _text(voice.get("selected_voice"))
        or _text(voice.get("persistent_voice_description"))
        or _text(voice.get("selected_production_method"))
        or "No production Voice"
    )


def _adapter_manifest(adapter_path: Path | None) -> dict[str, Any]:
    if adapter_path is None or not adapter_path.exists():
        return {}
    candidates = [
        adapter_path / "mlx_export_manifest.json",
        adapter_path / "manifest.json",
        adapter_path / "adapter_manifest.json",
    ]
    if adapter_path.name != "mlx_model":
        candidates.insert(0, adapter_path / "mlx_model" / "mlx_export_manifest.json")
    for path in candidates:
        if not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return {"invalid": True, "manifest_filename": path.name}
        if isinstance(value, Mapping):
            return {**dict(value), "manifest_filename": path.name}
    return {}


def _public_adapter_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    if not value:
        return {}
    validation = _mapping(value.get("validation"))
    return {
        "manifest_filename": value.get("manifest_filename"),
        "production_assignment_supported": value.get(
            "production_assignment_supported"
        ),
        "experimental": value.get("experimental"),
        "model_type": value.get("model_type"),
        "base_model": _display_asset(value.get("base_model")),
        "validation": {
            "manual_audio_review_status": validation.get(
                "manual_audio_review_status"
            ),
            "inference_status": validation.get("inference_status"),
        },
        "invalid": value.get("invalid") is True,
    }


def _voice_record(
    *,
    root_dir: Path | None,
    character_id: str,
    names: list[str],
    script_label: str | None,
    voice_config: Mapping[str, Any],
    persona_record: Mapping[str, Any],
    preview_record: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    config_key, config = _voice_entry_for_character(
        voice_config=voice_config,
        character_id=character_id,
        names=names,
        script_label=script_label,
    )
    persona = dict(persona_record)
    nested_persona = _mapping(config.get("persona"))
    method = (
        _text(config.get("type"))
        or _text(config.get("voice_type"))
        or _text(config.get("method"))
        or _text(config.get("production_method"))
    )
    method_key = _normalized(method).replace(" ", "_") if method else None
    selected_voice = (
        _text(config.get("voice"))
        or _text(config.get("voice_id"))
        or _text(config.get("custom_voice"))
        or _text(config.get("designed_voice"))
    )
    backend = (
        _text(config.get("clone_backend"))
        or _text(config.get("backend"))
        or _text(config.get("tts_backend"))
    )
    description = (
        _text(config.get("description"))
        or _text(config.get("voice_description"))
        or _text(nested_persona.get("description"))
        or _text(persona.get("designed_voice_description"))
        or _text(persona.get("description"))
        or _text(persona.get("voice_description"))
    )
    representative_text = (
        _text(config.get("representative_text"))
        or _text(config.get("ref_text"))
        or _text(nested_persona.get("ref_text"))
        or _text(persona.get("representative_text"))
        or _text(persona.get("ref_text"))
    )
    reference_transcript = (
        _text(config.get("reference_transcript"))
        or _text(config.get("ref_text"))
    )
    reference_audio_value = (
        config.get("ref_audio")
        or config.get("reference_audio")
        or config.get("reference_audio_path")
    )
    reference_audio_path = _rooted_path(root_dir, reference_audio_value)
    preview_audio_path = _rooted_path(
        root_dir,
        config.get("preview_audio")
        or config.get("preview_audio_path")
        or config.get("designed_preview"),
    )
    alias_target = (
        _text(config.get("alias"))
        or _text(config.get("alias_of"))
        or _text(config.get("target_voice"))
        or _text(config.get("target"))
    )
    adapter_value = (
        config.get("adapter_path")
        or config.get("adapter")
        or config.get("adapter_id")
    )
    adapter_path = _rooted_path(root_dir, adapter_value)
    adapter_manifest = _adapter_manifest(adapter_path)
    community_pack_path = _rooted_path(
        root_dir,
        config.get("community_pack_path"),
    )
    community_pack_confined = False
    if root_dir is not None and community_pack_path is not None:
        try:
            community_pack_path.relative_to(root_dir.resolve())
            community_pack_confined = True
        except (OSError, ValueError):
            pass
    backend_key = (backend or "").casefold()
    legacy_controlled = backend_key in LEGACY_CONTROLLED_CLONE_BACKENDS
    controlled = bool(
        backend_key in CONTROLLED_CLONE_BACKENDS
        or method_key in CONTROLLED_CLONE_BACKENDS
        or config.get("controlled") is True
    )
    approval_fingerprint = (
        _text(config.get("controlled_clone_configuration_fingerprint"))
        or _text(config.get("controlled_clone_approval_fingerprint"))
        or _text(_mapping(config.get("approval_receipt")).get("configuration_fingerprint"))
    )
    preview_status = (
        _text(preview_record.get("status"))
        or _text(config.get("preview_status"))
        or "not_generated"
    )
    listened = bool(
        preview_record.get("listened") is True
        or config.get("preview_listened") is True
        or config.get("listen_approved") is True
    )
    blockers: list[dict[str, Any]] = []
    valid = True

    def blocker(code: str, title: str, explanation: str) -> None:
        nonlocal valid
        valid = False
        blockers.append(
            {
                "code": code,
                "title": title,
                "explanation": explanation,
                "native_destination": "cast",
                "target_id": character_id,
                "blocking": True,
            }
        )

    if not config:
        blocker(
            "cast_voice_configuration_missing",
            "Production Voice is not assigned",
            "Choose a production-ready Voice for this speaking character.",
        )
    elif method_key in {"custom", "builtin", "built_in", "standard", "saved_voice"}:
        if selected_voice is None:
            blocker(
                "cast_voice_selection_missing",
                "Production Voice is not selected",
                "Choose a built-in or saved Voice for this speaking character.",
            )
    elif method_key in {"clone", "supplied_recording_clone", "controlled_clone", "instruction_controlled_clone"}:
        if reference_audio_path is None or not reference_audio_path.is_file():
            blocker(
                "cast_clone_reference_audio_invalid",
                "Clone reference audio is invalid",
                "Select a readable supplied-recording reference.",
            )
        if reference_transcript is None:
            blocker(
                "cast_clone_reference_transcript_missing",
                "Exact clone transcript is missing",
                "Enter the exact words spoken in the clone reference audio.",
            )
        if legacy_controlled:
            blocker(
                "cast_legacy_controlled_clone_unsupported",
                "Legacy expressive clone is no longer supported",
                (
                    "VoxCPM2 does not provide a reliable per-line delivery-control "
                    "channel. Re-preview this supplied Voice with the Qwen "
                    "instruction-controlled clone or use the standard clone."
                ),
            )
        elif controlled and approval_fingerprint is None:
            blocker(
                "cast_controlled_clone_approval_missing",
                "Controlled-clone approval is not current",
                "Generate the bound preview, listen to it, and save with a current server receipt.",
            )
    elif method_key == "community_qvoice":
        expected_hash = _text(config.get("community_pack_sha256"))
        if (
            community_pack_path is None
            or not community_pack_confined
            or not community_pack_path.is_file()
        ):
            blocker(
                "cast_community_qvoice_pack_missing",
                "Community Qwen Voice pack is missing",
                "Reassign the approved imported Voice from the Voice library.",
            )
        elif expected_hash is None or hashlib.sha256(
            community_pack_path.read_bytes()
        ).hexdigest() != expected_hash:
            blocker(
                "cast_community_qvoice_integrity_failed",
                "Community Qwen Voice pack failed its integrity check",
                "Remove the changed artifact and import the original pack again.",
            )
        if not _text(config.get("community_pack_approval_fingerprint")):
            blocker(
                "cast_community_qvoice_approval_missing",
                "Community Qwen Voice listening review is incomplete",
                "Generate, listen to, and approve the exact preview before assignment.",
            )
        if description is None:
            blocker(
                "cast_community_qvoice_description_missing",
                "Persistent Voice description is missing",
                "Add the stable identity description used for the approved preview.",
            )
    elif method_key in {"design", "designed", "designed_voice", "voice_design"}:
        if description is None:
            blocker(
                "cast_designed_voice_missing",
                "Designed Voice definition is missing",
                "Provide a stable Voice definition. A built-in Voice name cannot substitute for a designed Voice.",
            )
    elif method_key in {"lora", "adapter", "trained_voice"}:
        production_supported = adapter_manifest.get("production_assignment_supported") is True
        manual_review = _normalized(
            _mapping(adapter_manifest.get("validation")).get("manual_audio_review_status")
        )
        if not adapter_manifest or adapter_manifest.get("invalid"):
            blocker(
                "cast_adapter_invalid",
                "Voice adapter is unavailable",
                "Select a compatible validated adapter artifact.",
            )
        elif not production_supported or manual_review not in {"approved", "complete", "passed"}:
            blocker(
                "cast_adapter_not_approved",
                "Voice adapter is not production-approved",
                "Complete inference validation and human listening approval before assignment.",
            )
    elif method_key == "alias":
        if alias_target is None:
            blocker(
                "cast_alias_target_missing",
                "Voice alias target is missing",
                "Choose the stable character or Script Voice label this alias should use.",
            )
        else:
            target = voice_config.get(alias_target)
            if not isinstance(target, Mapping):
                target = next(
                    (
                        value
                        for key, value in voice_config.items()
                        if isinstance(value, Mapping)
                        and _normalized(key) == _normalized(alias_target)
                    ),
                    None,
                )
            if not isinstance(target, Mapping):
                blocker(
                    "cast_alias_target_invalid",
                    "Voice alias target is unavailable",
                    "Choose an existing compatible production Voice target.",
                )
    elif method_key is None:
        valid = False
    else:
        blocker(
            "cast_voice_method_unsupported",
            "Production Voice method is unsupported",
            f"The saved Voice method {method!r} is not available in the unified Cast workflow.",
        )

    fingerprint = fingerprint_value(config) if config else None
    return (
        {
            "configuration_key": config_key,
            "library_voice_id": _text(config.get("library_voice_id")),
            "selected_production_method": method_key,
            "selected_backend": backend,
            "selected_voice": _display_asset(selected_voice),
            "clone": {
                "reference_source": _text(config.get("reference_source")),
                "reference_audio_url": _public_project_audio_url(root_dir, reference_audio_path),
                "exact_reference_transcript": reference_transcript,
                "reference_audio_state": (
                    "ready"
                    if reference_audio_path is not None and reference_audio_path.is_file()
                    else "missing"
                    if reference_audio_value
                    else "not_configured"
                ),
                "reference_audio_fingerprint": _text(config.get("reference_audio_fingerprint")),
                "controlled_capability": controlled,
                "controlled_approval_state": (
                    "approved" if approval_fingerprint else "required" if controlled else "not_required"
                ),
                "approval_receipt_fingerprint": approval_fingerprint,
            },
            "persistent_voice_description": description,
            "representative_text": representative_text,
            "imported_dossier": {
                key: copy.deepcopy(persona.get(key))
                for key in (
                    "persona_summary",
                    "designed_voice_description",
                    "vocal_age_impression",
                    "pitch",
                    "weight_and_resonance",
                    "texture_and_timbre",
                    "accent_and_language",
                    "cadence_and_rhythm",
                    "energy_range",
                    "emotional_range",
                    "casting_guidance",
                    "uncertainties",
                )
                if persona.get(key) not in (None, "", [], {})
            },
            "preview": {
                "status": preview_status,
                "audio_url": _public_project_audio_url(
                    root_dir,
                    preview_audio_path,
                ),
                "listened": listened,
                "approved": bool(
                    preview_record.get("approved") is True
                    or config.get("preview_approved") is True
                    or approval_fingerprint
                    or _text(config.get("community_pack_approval_fingerprint"))
                ),
                "fingerprint": _text(preview_record.get("fingerprint"))
                or _text(config.get("preview_fingerprint")),
            },
            "designed_voice_state": _text(config.get("designed_voice_state")),
            "adapter": {
                "state": (
                    "ready"
                    if method_key in {"lora", "adapter", "trained_voice"} and valid
                    else "invalid"
                    if method_key in {"lora", "adapter", "trained_voice"}
                    else "not_selected"
                ),
                "id": _text(config.get("adapter_id")),
                "manifest": _public_adapter_manifest(adapter_manifest),
            },
            "alias": {
                "state": "ready" if method_key == "alias" and valid else "invalid" if method_key == "alias" else "not_selected",
                "target": alias_target,
            },
            "saved_configuration_fingerprint": fingerprint,
            "valid": valid and bool(config),
            "blockers": blockers,
        },
        blockers,
    )


def _appearance_record(record: Mapping[str, Any]) -> dict[str, Any]:
    visual = _mapping(record.get("visual"))
    source = visual or record
    status = (
        _text(source.get("status"))
        or _text(source.get("state"))
        or ("complete" if visual else "not_started")
    )
    profile = _mapping(source.get("profile"))
    stable_traits = _list(source.get("stable_traits")) or _list(
        source.get("traits")
    )
    if not stable_traits and profile:
        stable_traits = [
            fact
            for facts in profile.values()
            for fact in _list(facts)
        ]
    return {
        "entry_id": _text(record.get("roster_entry_id"))
        or _text(record.get("character_id")),
        "status": status,
        "summary": _text(source.get("image_prompt_summary"))
        or _text(source.get("summary"))
        or _text(source.get("appearance_summary")),
        "stable_traits": stable_traits,
        "variants": _list(source.get("variants")),
        "conflicts": _list(source.get("conflicts")),
        "unknowns": _list(source.get("unknowns")),
        "evidence_available": bool(
            source.get("evidence_available")
            or _list(source.get("evidence"))
            or _list(source.get("source_evidence"))
            or stable_traits
        ),
        "operation": copy_mapping(source.get("operation")),
        "optional": True,
    }


def copy_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _advanced_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "expressive_reference_state": _text(record.get("expressive_reference_state"))
        or _text(record.get("reference_bank_state"))
        or "not_started",
        "owned_recording_preparation_state": _text(record.get("owned_recording_preparation_state"))
        or _text(record.get("preparation_state"))
        or "not_started",
        "dataset_state": _text(record.get("dataset_state")) or "not_started",
        "adapter_training_state": _text(record.get("adapter_training_state"))
        or _text(record.get("training_state"))
        or "not_started",
        "compatibility_state": _text(record.get("compatibility_state")) or "current",
        "provenance_available": bool(record.get("provenance_available") or record.get("provenance")),
        "blockers": [
            dict(item)
            for item in _list(record.get("blockers"))
            if isinstance(item, Mapping)
        ],
        "optional": True,
    }


def _identity_blocker(character_id: str, code: str, title: str, explanation: str) -> dict[str, Any]:
    return {
        "code": code,
        "title": title,
        "explanation": explanation,
        "native_destination": "cast",
        "target_id": character_id,
        "blocking": True,
    }


def _row_state(
    *,
    identity_blockers: list[dict[str, Any]],
    required_for_completion: bool,
    voice: Mapping[str, Any],
) -> str:
    if identity_blockers:
        return "needs_identity_review"
    if required_for_completion and voice.get("valid") is not True:
        return "needs_voice"
    preview = _mapping(voice.get("preview"))
    if required_for_completion and preview.get("status") in {"stale", "failed"}:
        return "preview_recommended"
    return "ready"


def build_cast_aggregate(
    *,
    roster: Any,
    script: Any,
    voice_config: Any,
    root_dir: str | Path | None = None,
    persona_state: Any = None,
    visual_state: Any = None,
    preparation_state: Any = None,
    preview_state: Any = None,
    selected_character_id: str | None = None,
    filter_key: str = "all",
    search: str | None = None,
) -> dict[str, Any]:
    if filter_key not in CAST_FILTERS:
        raise CastAggregateError(
            status_code=422,
            code="cast_filter_invalid",
            detail="The requested Cast filter is invalid.",
            context={"filter": filter_key},
        )
    roster_entries = _roster_entries(roster)
    script_entries = _script_entries(script)
    config = _voice_config(voice_config)
    root = Path(root_dir).expanduser().resolve() if root_dir is not None else None
    script_index = _script_index(script_entries)
    script_fingerprint = fingerprint_value(script_entries) if script_entries else None
    persona_index = _character_record_index(persona_state)
    visual_index = _character_record_index(visual_state)
    preparation_index = _character_record_index(preparation_state)
    preview_index = _character_record_index(preview_state)
    characters: list[dict[str, Any]] = []
    aggregate_blockers: list[dict[str, Any]] = []

    for index, entry in enumerate(roster_entries):
        character_id, stable_id_present = _character_id(entry, index)
        names = _character_names(entry)
        display_name = (
            _text(entry.get("display_name"))
            or _text(entry.get("canonical_name"))
            or _text(entry.get("name"))
            or names[0]
            if names
            else f"Character {index + 1}"
        )
        mapping = resolve_script_label(
            character=entry,
            script_index=script_index,
        )
        label = mapping["resolved_label"]
        lines = list(_mapping(script_index.get("by_label")).get(label) or []) if label else []
        non_speaking = _is_non_speaking(entry, len(lines))
        required = not non_speaking
        identity_blockers: list[dict[str, Any]] = []
        if not stable_id_present:
            identity_blockers.append(
                _identity_blocker(
                    character_id,
                    "cast_stable_character_id_missing",
                    "Stable character ID is missing",
                    "Repair or reconcile this roster entry before production.",
                )
            )
        if not _identity_resolved(entry, stable_id_present):
            identity_blockers.append(
                _identity_blocker(
                    character_id,
                    "cast_identity_unresolved",
                    "Character identity requires review",
                    "Resolve the character identity, conflict, or evidence question.",
                )
            )
        if required and mapping.get("ambiguous"):
            identity_blockers.append(
                _identity_blocker(
                    character_id,
                    "cast_script_label_ambiguous",
                    "Script Voice label is ambiguous",
                    "Choose the one Script speaker label represented by this character.",
                )
            )
        elif required and label is None:
            identity_blockers.append(
                _identity_blocker(
                    character_id,
                    "cast_script_label_missing",
                    "Script Voice label is unresolved",
                    "Map this speaking identity to one Script speaker label.",
                )
            )

        persona_record = _find_indexed_character_record(
            persona_index,
            character_id=character_id,
            names=names,
        )
        visual_record = _find_indexed_character_record(
            visual_index,
            character_id=character_id,
            names=names,
        )
        preparation_record = _find_indexed_character_record(
            preparation_index,
            character_id=character_id,
            names=names,
        )
        preview_record = _find_indexed_character_record(
            preview_index,
            character_id=character_id,
            names=names,
        )
        voice, voice_blockers = _voice_record(
            root_dir=root,
            character_id=character_id,
            names=names,
            script_label=label,
            voice_config=config,
            persona_record=persona_record,
            preview_record=preview_record,
        )
        if not required:
            voice_blockers = []
        blockers = [*identity_blockers, *voice_blockers]
        readiness = _row_state(
            identity_blockers=identity_blockers,
            required_for_completion=required,
            voice=voice,
        )
        if readiness not in CAST_READINESS_STATES:
            raise CastAggregateError(
                status_code=500,
                code="cast_readiness_invalid",
                detail=f"Derived unsupported Cast readiness state: {readiness}",
            )
        character = {
            "character_id": character_id,
            "display_name": display_name,
            "canonical_name": _text(entry.get("canonical_name")) or display_name,
            "speaking_role": "non_speaking" if non_speaking else "speaking",
            "required_for_completion": required,
            "readiness_state": readiness,
            "voice_summary": _voice_summary(voice),
            "blocker_count": len(blockers),
            "blockers": blockers,
            "next_useful_action": (
                {
                    "id": "review_character_identity",
                    "label": "Review identity",
                    "native_destination": "cast",
                    "target_id": character_id,
                }
                if identity_blockers
                else {
                    "id": "assign_character_voice",
                    "label": "Assign Voice",
                    "native_destination": "cast",
                    "target_id": character_id,
                }
                if required and voice.get("valid") is not True
                else {
                    "id": "generate_character_preview",
                    "label": "Generate preview",
                    "native_destination": "cast",
                    "target_id": character_id,
                }
                if readiness == "preview_recommended"
                else None
            ),
            "identity": {
                "stable_character_id": character_id,
                "stable_id_present": stable_id_present,
                "canonical_name": _text(entry.get("canonical_name")) or display_name,
                "display_name": display_name,
                "aliases": _unique_texts(_list(entry.get("aliases"))),
                "titles": _unique_texts(_list(entry.get("titles"))),
                "nicknames": _unique_texts(_list(entry.get("nicknames"))),
                "pronouns": entry.get("pronouns"),
                "species_or_type": entry.get("species") or entry.get("type"),
                "relationships": _list(entry.get("relationships")),
                "role": entry.get("role"),
                "speaking_state": "non_speaking" if non_speaking else "speaking",
                "source_confidence": entry.get("source_confidence") or entry.get("confidence"),
                "unresolved_questions": _list(entry.get("unresolved_questions")),
                "conflict_state": entry.get("conflict_state"),
                "source_evidence_summary": entry.get("source_evidence_summary"),
                "representative_script_lines": [
                    {
                        "script_index": item.get("script_index"),
                        "text": item.get("text"),
                        "instruct": item.get("instruct"),
                    }
                    for item in lines[:8]
                ],
            },
            "script_connection": {
                "resolved_script_voice_label": label,
                "mapping_method": mapping.get("method"),
                "mapping_confidence": mapping.get("confidence"),
                "ambiguity_state": "ambiguous" if mapping.get("ambiguous") else "resolved" if label else "unresolved",
                "candidate_labels": mapping.get("candidate_labels") or [],
                "script_line_count": len(lines),
                "representative_lines": [
                    item.get("text") for item in lines[:8]
                ],
            },
            "voice": voice,
            "character": {
                "summary": {
                    "canonical_name": _text(entry.get("canonical_name")) or display_name,
                    "display_name": display_name,
                    "aliases": _unique_texts(_list(entry.get("aliases"))),
                    "role": entry.get("role"),
                    "speaking_state": "non_speaking" if non_speaking else "speaking",
                    "species_or_type": entry.get("species") or entry.get("type"),
                    "relationships": _list(entry.get("relationships")),
                    "source_confidence": entry.get("source_confidence") or entry.get("confidence"),
                },
                "expanded": {
                    "titles": _unique_texts(_list(entry.get("titles"))),
                    "nicknames": _unique_texts(_list(entry.get("nicknames"))),
                    "pronouns": entry.get("pronouns"),
                    "source_evidence": _list(entry.get("source_evidence")) or _list(entry.get("evidence")),
                    "representative_script_lines": [
                        item.get("text") for item in lines[:12]
                    ],
                    "script_line_count": len(lines),
                    "mapping_evidence": mapping,
                    "unresolved_questions": _list(entry.get("unresolved_questions")),
                    "conflicts": _list(entry.get("conflicts")),
                    "technical_provenance": copy_mapping(entry.get("provenance")),
                },
            },
            "appearance": _appearance_record(visual_record),
            "advanced_voice_setup": _advanced_record(preparation_record),
            "fingerprints": {
                "script": script_fingerprint,
                "roster_entry": fingerprint_value(entry),
                "voice_configuration": voice.get("saved_configuration_fingerprint"),
            },
        }
        characters.append(character)
        if required:
            aggregate_blockers.extend(blockers)

    if selected_character_id is not None and not any(
        item["character_id"] == selected_character_id for item in characters
    ):
        raise CastAggregateError(
            status_code=404,
            code="cast_character_not_found",
            detail="The selected character is not present in the current Cast roster.",
            context={"character_id": selected_character_id},
        )
    selected = next(
        (
            item
            for item in characters
            if item["character_id"] == selected_character_id
        ),
        characters[0] if characters else None,
    )
    query = _normalized(search)

    def visible(item: Mapping[str, Any]) -> bool:
        if query:
            searchable = " ".join(
                [
                    str(item.get("display_name") or ""),
                    str(_mapping(item.get("script_connection")).get("resolved_script_voice_label") or ""),
                    " ".join(_mapping(item.get("identity")).get("aliases") or []),
                ]
            )
            if query not in _normalized(searchable):
                return False
        if filter_key == "needs_attention":
            return item.get("readiness_state") != "ready"
        if filter_key == "unassigned":
            return item.get("required_for_completion") and _mapping(item.get("voice")).get("valid") is not True
        if filter_key == "speaking_roles":
            return item.get("speaking_role") == "speaking"
        if filter_key == "non_speaking":
            return item.get("speaking_role") == "non_speaking"
        if filter_key == "ready":
            return item.get("readiness_state") == "ready"
        return True

    visible_characters = [item for item in characters if visible(item)]
    required_characters = [item for item in characters if item["required_for_completion"]]
    completion = bool(characters) and all(
        item["readiness_state"] == "ready" for item in required_characters
    )
    return {
        "schema_version": CAST_AGGREGATE_SCHEMA_VERSION,
        "summary": {
            "state": "complete" if completion else "blocked" if aggregate_blockers else "not_started" if not characters else "ready",
            "character_count": len(characters),
            "required_speaking_count": len(required_characters),
            "ready_required_count": sum(
                item["readiness_state"] == "ready"
                for item in required_characters
            ),
            "blocker_count": len(aggregate_blockers),
            "complete": completion,
        },
        "filters": {
            "active": filter_key,
            "counts": {
                "all": len(characters),
                "needs_attention": sum(item["readiness_state"] != "ready" for item in characters),
                "unassigned": sum(
                    item["required_for_completion"]
                    and _mapping(item.get("voice")).get("valid") is not True
                    for item in characters
                ),
                "speaking_roles": sum(item["speaking_role"] == "speaking" for item in characters),
                "non_speaking": sum(item["speaking_role"] == "non_speaking" for item in characters),
                "ready": sum(item["readiness_state"] == "ready" for item in characters),
            },
            "search": search,
        },
        "characters": visible_characters,
        "selected_character_id": selected["character_id"] if selected else None,
        "selected_character": selected,
        "selection_visible": bool(
            selected
            and any(
                item["character_id"] == selected["character_id"]
                for item in visible_characters
            )
        ),
        "blockers": aggregate_blockers,
        "fingerprints": {
            "script": script_fingerprint,
            "roster": fingerprint_value(roster_entries) if roster_entries else None,
            "voice_config": fingerprint_value(config) if config else None,
        },
        "native_endpoints_preserved": True,
    }


def filter_cast_aggregate(
    aggregate: Mapping[str, Any],
    *,
    filter_key: str = "all",
    search: str | None = None,
) -> dict[str, Any]:
    if filter_key not in CAST_FILTERS:
        raise CastAggregateError(
            status_code=422,
            code="cast_filter_invalid",
            detail="The requested Cast filter is invalid.",
            context={"filter": filter_key},
        )
    result = copy.deepcopy(dict(aggregate))
    characters = [
        item
        for item in _list(result.get("characters"))
        if isinstance(item, Mapping)
    ]
    query = _normalized(search)

    def visible(item: Mapping[str, Any]) -> bool:
        if query:
            searchable = " ".join(
                [
                    str(item.get("display_name") or ""),
                    str(
                        _mapping(item.get("script_connection")).get(
                            "resolved_script_voice_label"
                        )
                        or ""
                    ),
                    " ".join(
                        _mapping(item.get("identity")).get("aliases") or []
                    ),
                ]
            )
            if query not in _normalized(searchable):
                return False
        if filter_key == "needs_attention":
            return item.get("readiness_state") != "ready"
        if filter_key == "unassigned":
            return item.get("required_for_completion") and _mapping(
                item.get("voice")
            ).get("valid") is not True
        if filter_key == "speaking_roles":
            return item.get("speaking_role") == "speaking"
        if filter_key == "non_speaking":
            return item.get("speaking_role") == "non_speaking"
        if filter_key == "ready":
            return item.get("readiness_state") == "ready"
        return True

    visible_characters = [item for item in characters if visible(item)]
    selected = _mapping(result.get("selected_character"))
    selected_id = _text(result.get("selected_character_id"))
    filters = dict(_mapping(result.get("filters")))
    filters["active"] = filter_key
    filters["search"] = search
    result["filters"] = filters
    result["characters"] = visible_characters
    result["selection_visible"] = bool(
        selected_id
        and any(
            item.get("character_id") == selected_id
            for item in visible_characters
        )
    )
    if selected_id and selected:
        result["selected_character"] = dict(selected)
    return result


def apply_native_cast_validation(
    aggregate: Mapping[str, Any],
    native_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    result = copy.deepcopy(dict(aggregate))
    characters = [
        item
        for item in _list(result.get("characters"))
        if isinstance(item, Mapping)
    ]
    by_id = {
        str(item.get("character_id")): item
        for item in characters
        if _text(item.get("character_id"))
    }
    aggregate_blockers = [
        dict(item)
        for item in _list(result.get("blockers"))
        if isinstance(item, Mapping)
    ]

    def add_character_blocker(
        character_id: str,
        *,
        code: str,
        title: str,
        explanation: str,
        readiness_state: str,
    ) -> None:
        character = by_id.get(character_id)
        if character is None:
            aggregate_blockers.append(
                {
                    "code": code,
                    "title": title,
                    "explanation": explanation,
                    "native_destination": "cast",
                    "target_id": character_id,
                    "blocking": True,
                }
            )
            return
        blockers = [
            dict(item)
            for item in _list(character.get("blockers"))
            if isinstance(item, Mapping)
        ]
        if not any(item.get("code") == code for item in blockers):
            blockers.append(
                {
                    "code": code,
                    "title": title,
                    "explanation": explanation,
                    "native_destination": "cast",
                    "target_id": character_id,
                    "blocking": True,
                }
            )
        character["blockers"] = blockers
        character["blocker_count"] = len(blockers)
        character["readiness_state"] = readiness_state
        character["next_useful_action"] = (
            {
                "id": "review_character_identity",
                "label": "Review identity",
                "native_destination": "cast",
                "target_id": character_id,
            }
            if readiness_state == "needs_identity_review"
            else {
                "id": "assign_character_voice",
                "label": "Review Voice",
                "native_destination": "cast",
                "target_id": character_id,
            }
            if readiness_state == "needs_voice"
            else {
                "id": "generate_character_preview",
                "label": "Review preview",
                "native_destination": "cast",
                "target_id": character_id,
            }
        )
        if character.get("required_for_completion") is True:
            aggregate_blockers.extend(
                item
                for item in blockers
                if item.get("blocking") is True
                and not any(
                    existing.get("code") == item.get("code")
                    and existing.get("target_id") == item.get("target_id")
                    for existing in aggregate_blockers
                )
            )

    categories = (
        (
            "unresolved_identity_ids",
            "cast_native_identity_unresolved",
            "Character identity is not authoritatively resolved",
            "Resolve this identity through the native roster review transaction.",
            "needs_identity_review",
        ),
        (
            "ambiguous_mapping_ids",
            "cast_native_script_label_ambiguous",
            "Script Voice label is not authoritatively resolved",
            "Resolve the native Script-label mapping before assigning production audio.",
            "needs_identity_review",
        ),
        (
            "missing_voice_ids",
            "cast_native_voice_missing",
            "Production Voice is missing",
            "Assign a production Voice through the existing Voice transaction.",
            "needs_voice",
        ),
        (
            "invalid_voice_ids",
            "cast_native_voice_invalid",
            "Production Voice is invalid",
            "Repair or replace the saved production Voice configuration.",
            "needs_voice",
        ),
        (
            "invalid_clone_ids",
            "cast_native_clone_invalid",
            "Clone reference is invalid",
            "Repair the supplied recording and exact reference transcript.",
            "needs_voice",
        ),
        (
            "controlled_clone_approval_missing_ids",
            "cast_native_controlled_clone_approval_missing",
            "Controlled-clone approval is not current",
            "Generate the bound preview, listen, and save with a current server receipt.",
            "needs_voice",
        ),
        (
            "invalid_adapter_ids",
            "cast_native_adapter_invalid",
            "Adapter or alias is invalid",
            "Select a compatible production-approved adapter or valid alias target.",
            "needs_voice",
        ),
        (
            "stale_voice_ids",
            "cast_native_voice_stale",
            "Production Voice configuration is stale",
            "Review and save the current Voice configuration before production.",
            "preview_recommended",
        ),
    )
    for native_key, code, title, explanation, readiness in categories:
        for value in _list(native_evidence.get(native_key)):
            character_id = _text(value)
            if character_id:
                add_character_blocker(
                    character_id,
                    code=code,
                    title=title,
                    explanation=explanation,
                    readiness_state=readiness,
                )

    def add_aggregate_blocker(code: str, title: str, explanation: str) -> None:
        if any(item.get("code") == code for item in aggregate_blockers):
            return
        aggregate_blockers.append(
            {
                "code": code,
                "title": title,
                "explanation": explanation,
                "native_destination": "cast",
                "target_id": "cast:review",
                "blocking": True,
            }
        )

    if native_evidence.get("roster_exists") is not True:
        add_aggregate_blocker(
            "cast_native_roster_missing",
            "Character roster is missing",
            "Complete post-Script character discovery before Cast can be ready.",
        )
    if native_evidence.get("review_required") is True:
        add_aggregate_blocker(
            "cast_native_roster_review_required",
            "Character reconciliation requires review",
            "Resolve the native roster review issues before Cast can be ready.",
        )
    if native_evidence.get("roster_approved") is not True:
        add_aggregate_blocker(
            "cast_native_roster_not_approved",
            "Character roster is not approved",
            "Approve the resolved roster through the native review transaction.",
        )
    if native_evidence.get("roster_current") is not True:
        add_aggregate_blocker(
            "cast_native_roster_stale",
            "Character roster is stale or incompatible",
            "Reconcile the roster against the current source and accepted Script.",
        )
    if native_evidence.get("failed") is True:
        add_aggregate_blocker(
            "cast_native_operation_failed",
            "Cast preparation failed",
            "Inspect the native roster or Voice failure and retry safely.",
        )

    required = [
        item for item in characters if item.get("required_for_completion") is True
    ]
    native_required = int(
        native_evidence.get("required_speaking_characters") or 0
    )
    native_valid = int(native_evidence.get("valid_production_voices") or 0)
    if native_required != len(required):
        add_aggregate_blocker(
            "cast_native_required_count_mismatch",
            "Required speaking-role count is inconsistent",
            "Resolve the roster and Script-label mapping before Cast can be complete.",
        )
    if native_valid < native_required:
        add_aggregate_blocker(
            "cast_native_voice_validation_incomplete",
            "Not every required Voice passes native validation",
            "Review the character-level Voice blockers before production.",
        )

    ready_required = sum(
        item.get("readiness_state") == "ready" for item in required
    )
    complete = bool(characters) and not aggregate_blockers and all(
        item.get("readiness_state") == "ready" for item in required
    )
    process = _mapping(native_evidence.get("process"))
    summary = dict(_mapping(result.get("summary")))
    summary.update(
        {
            "state": (
                "running"
                if process.get("running") is True
                else "resumable"
                if native_evidence.get("resumable") is True
                else "failed"
                if native_evidence.get("failed") is True
                else "complete"
                if complete
                else "blocked"
                if aggregate_blockers
                else "ready"
            ),
            "required_speaking_count": len(required),
            "ready_required_count": ready_required,
            "blocker_count": len(aggregate_blockers),
            "complete": complete,
        }
    )
    result["characters"] = characters
    selected_id = _text(result.get("selected_character_id"))
    if selected_id and selected_id in by_id:
        result["selected_character"] = by_id[selected_id]
    result["blockers"] = aggregate_blockers
    result["summary"] = summary
    result["authoritative_native_validation"] = {
        "applied": True,
        "roster_exists": native_evidence.get("roster_exists"),
        "roster_approved": native_evidence.get("roster_approved"),
        "roster_current": native_evidence.get("roster_current"),
        "required_speaking_characters": native_required,
        "valid_production_voices": native_valid,
        "fingerprints": copy.deepcopy(
            dict(_mapping(native_evidence.get("fingerprints")))
        ),
    }
    filters = dict(_mapping(result.get("filters")))
    counts = dict(_mapping(filters.get("counts")))
    counts.update(
        {
            "needs_attention": sum(
                item.get("readiness_state") != "ready" for item in characters
            ),
            "unassigned": sum(
                item.get("required_for_completion") is True
                and _mapping(item.get("voice")).get("valid") is not True
                for item in characters
            ),
            "ready": sum(
                item.get("readiness_state") == "ready" for item in characters
            ),
        }
    )
    filters["counts"] = counts
    result["filters"] = filters
    return result


def _read_project_json(
    path: Path,
    *,
    required: bool = False,
    maximum_bytes: int = 256 * 1024 * 1024,
) -> Any:
    if not path.exists():
        if required:
            raise CastAggregateError(
                status_code=409,
                code="cast_artifact_missing",
                detail=f"{path.name} is missing.",
                context={"filename": path.name},
            )
        return None
    if not path.is_file() or path.is_symlink():
        raise CastAggregateError(
            status_code=409,
            code="cast_artifact_invalid",
            detail=f"{path.name} is not a safe regular file.",
            context={"filename": path.name},
        )
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise CastAggregateError(
            status_code=409,
            code="cast_artifact_unreadable",
            detail=f"Could not inspect {path.name}: {exc}",
            context={"filename": path.name},
        ) from exc
    if size > maximum_bytes:
        raise CastAggregateError(
            status_code=413,
            code="cast_artifact_too_large",
            detail=f"{path.name} exceeds the Cast read-model safety limit.",
            context={"filename": path.name, "size_bytes": size},
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise CastAggregateError(
            status_code=409,
            code="cast_artifact_invalid_json",
            detail=f"{path.name} is invalid JSON: {exc}",
            context={"filename": path.name},
        ) from exc


def _read_auxiliary_tree(
    root: Path,
    *,
    relative_paths: Iterable[str],
    maximum_files: int = 200,
    maximum_total_bytes: int = 32 * 1024 * 1024,
) -> dict[str, Any]:
    records: list[Any] = []
    warnings: list[dict[str, Any]] = []
    file_count = 0
    total_bytes = 0
    for relative in relative_paths:
        candidate = root / relative
        if not candidate.exists():
            continue
        paths = [candidate] if candidate.is_file() else sorted(candidate.rglob("*.json"))
        for path in paths:
            if file_count >= maximum_files:
                warnings.append(
                    {
                        "code": "cast_auxiliary_file_limit",
                        "title": "Some specialist Cast state was not indexed",
                        "explanation": "The optional specialist-state file limit was reached.",
                        "blocking": False,
                    }
                )
                return {"records": records, "warnings": warnings}
            if path.is_symlink() or not path.is_file():
                warnings.append(
                    {
                        "code": "cast_auxiliary_symlink_skipped",
                        "title": "Unsafe specialist state was skipped",
                        "explanation": f"Skipped {path.name} because symbolic links are not read by Cast status.",
                        "blocking": False,
                    }
                )
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > 4 * 1024 * 1024 or total_bytes + size > maximum_total_bytes:
                warnings.append(
                    {
                        "code": "cast_auxiliary_size_limit",
                        "title": "Large specialist state was skipped",
                        "explanation": f"Skipped {path.name} because it exceeds the optional Cast indexing limit.",
                        "blocking": False,
                    }
                )
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
                warnings.append(
                    {
                        "code": "cast_auxiliary_invalid_json",
                        "title": "Specialist state could not be indexed",
                        "explanation": f"Skipped invalid optional JSON file {path.name}.",
                        "blocking": False,
                    }
                )
                continue
            file_count += 1
            total_bytes += size
            records.append(value)
    return {"records": records, "warnings": warnings}


def inspect_cast_project(
    *,
    root_dir: str | Path,
    selected_character_id: str | None = None,
    filter_key: str = "all",
    search: str | None = None,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    approved_roster = root / "character_roster.json"
    draft_roster = root / "character_roster.draft.json"
    roster_path = approved_roster if approved_roster.is_file() else draft_roster
    roster = _read_project_json(roster_path) if roster_path.exists() else {"entries": []}
    script = _read_project_json(root / "annotated_script.json") or []
    voice_config = _read_project_json(root / "voice_config.json") or {}

    persona = _read_auxiliary_tree(
        root,
        relative_paths=(
            "cast_voice_dossiers.json",
            "persona_projects",
            "voice_training_projects",
            "persona_refs",
            "designed_voices",
        ),
    )
    visual = _read_auxiliary_tree(
        root,
        relative_paths=(
            "persona_visual.json",
            "persona_visual_state.json",
            "character_visual.json",
            "character_visual_dossiers",
            "persona_visual",
            "persona_refs",
        ),
    )
    preparation = _read_auxiliary_tree(
        root,
        relative_paths=(
            "voice_training_projects",
            "lora_datasets",
            "lora_models",
            "dataset_builder",
            "preparer_output",
        ),
    )
    preview = _read_auxiliary_tree(
        root,
        relative_paths=(
            "clone_previews",
            "controlled_clone_previews",
            "voice_previews",
        ),
    )
    aggregate = build_cast_aggregate(
        roster=roster,
        script=script,
        voice_config=voice_config,
        root_dir=root,
        persona_state=persona["records"],
        visual_state=visual["records"],
        preparation_state=preparation["records"],
        preview_state=preview["records"],
        selected_character_id=selected_character_id,
        filter_key=filter_key,
        search=search,
    )
    aggregate["compatibility"] = {
        "state": "advisory" if any(
            [
                persona["warnings"],
                visual["warnings"],
                preparation["warnings"],
                preview["warnings"],
            ]
        ) else "current",
        "warnings": [
            *persona["warnings"],
            *visual["warnings"],
            *preparation["warnings"],
            *preview["warnings"],
        ],
        "roster_source": "approved" if roster_path == approved_roster else "draft" if roster_path == draft_roster else "missing",
    }
    aggregate["technical_details"] = {
        "project_path": str(root),
        "roster_filename": roster_path.name,
    }
    return aggregate
