from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlencode

from cast_aggregate import CastAggregateError, inspect_cast_project
from community_qwen_packs import (
    CommunityQwenPackError,
    list_qwen_packs,
    resolve_qvoice_pack,
    resolve_qvoice_preview,
)
from generation_state import fingerprint_value
from library_inventory import LibraryInventoryError, inspect_library_inventory
from recurring_voice_routing import ROUTED_CLONE_BACKEND
from voice_aliases import VoiceAliasError, validate_voice_aliases
from voice_backend_capabilities import build_voice_backend_capabilities


VOICE_LIBRARY_SCHEMA_VERSION = 1
BUILT_IN_VOICES = (
    "Ryan",
    "Aiden",
    "Vivian",
    "Serena",
    "Dylan",
    "Eric",
    "Uncle_Fu",
    "Andy",
    "Ono_Anna",
)
METHOD_ORDER = (
    "built_in",
    "designed",
    "supplied_recording",
    "instruction_controlled",
    "adapter",
    "alias",
)
METHOD_LABELS = {
    "built_in": "Built-in Voice",
    "designed": "Designed Voice",
    "supplied_recording": "Supplied recording",
    "instruction_controlled": "Instruction-controlled clone",
    "adapter": "Voice adapter",
    "alias": "Voice alias",
}
METHOD_DESCRIPTIONS = {
    "built_in": "Pinned built-in Qwen speakers. Assignment remains in Cast.",
    "designed": "Reusable designed Voices created through Voice Lab.",
    "supplied_recording": "Standard supplied-recording identity clone. Line delivery instructions are not sent to the clone model.",
    "instruction_controlled": "Experimental Qwen supplied-recording path with an explicit instruction channel. Preview and listening approval are required before assignment.",
    "adapter": "Experimental trained or merged Voice adapter. Technical completion does not imply production approval.",
    "alias": "A stable Script-label alias that resolves to another authoritative Cast Voice configuration.",
}
ARTIFACT_METHODS = {
    "designed_voice": "designed",
    "clone_reference": "supplied_recording",
    "lora_adapter": "adapter",
}


class VoiceLibraryError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

    def as_detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self)}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256(
        "\x1f".join(str(item or "") for item in parts).encode("utf-8")
    ).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _route(
    destination: str,
    *,
    project_id: str | None,
    character_id: str | None = None,
    source: str | None = None,
    tool: str | None = None,
    mode: str | None = None,
    return_route: str | None = None,
) -> dict[str, Any]:
    context: dict[str, str] = {}
    for key, value in (
        ("project", project_id),
        ("character", character_id),
        ("source", source),
        ("tool", tool),
        ("mode", mode),
        ("return", return_route),
    ):
        if value:
            context[key] = value
    query = urlencode(context)
    return {
        "destination": destination,
        "context": context,
        "hash": f"#/{destination}" + (f"?{query}" if query else ""),
    }


def _load_voice_config(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "voice_config.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VoiceLibraryError(
            "voice_library_config_invalid",
            f"voice_config.json could not be read: {exc}",
        ) from exc
    if not isinstance(value, Mapping):
        raise VoiceLibraryError(
            "voice_library_config_invalid",
            "voice_config.json must contain an object.",
        )
    return {
        str(key): dict(item)
        for key, item in value.items()
        if isinstance(key, str) and isinstance(item, Mapping)
    }


def _load_designed_manifest(root: Path) -> list[dict[str, Any]]:
    path = root / "designed_voices" / "manifest.json"
    if not path.is_file():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _safe_asset_path(root: Path, value: Any) -> Path | None:
    text = _text(value)
    if not text:
        return None
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if not resolved.is_relative_to(root) or not resolved.is_file():
        return None
    return resolved


def _saved_clone_name(configuration_key: str, reference: str) -> str:
    key = " ".join(configuration_key.replace("_", " ").split()).title()
    stem = Path(reference).stem.casefold()
    if "benny" in stem:
        return "Benny / Bernice"
    if configuration_key.strip().upper() == "THE DOCTOR":
        return "Seventh Doctor"
    if configuration_key.strip().upper() == "THE TENTH DOCTOR":
        return "Tenth Doctor"
    if configuration_key.strip().upper() == "ROMANA":
        return "Romana"
    if configuration_key.strip().upper() == "DOCTOR":
        return "Doctor (legacy clone)"
    if configuration_key.strip().upper() == "NARRATOR":
        return "Narrator"
    return key


def _assignment_usage(cast: Mapping[str, Any], project_id: str | None) -> list[dict[str, Any]]:
    result = []
    for character in cast.get("characters", []):
        if not isinstance(character, Mapping):
            continue
        voice = _mapping(character.get("voice"))
        script = _mapping(character.get("script_connection"))
        character_id = _text(character.get("character_id"))
        result.append(
            {
                "character_id": character_id,
                "character_name": _text(character.get("display_name")) or "Unnamed character",
                "script_label": _text(script.get("resolved_script_voice_label")),
                "configuration_key": _text(voice.get("configuration_key")),
                "production_method": _text(voice.get("selected_production_method")),
                "backend": _text(voice.get("selected_backend")),
                "selected_voice": _text(voice.get("selected_voice")),
                "valid": voice.get("valid") is True,
                "preview_status": _text(_mapping(voice.get("preview")).get("status")),
                "adapter_id": _text(_mapping(voice.get("adapter")).get("id")),
                "alias_target": _text(_mapping(voice.get("alias")).get("target")),
                "library_voice_id": _text(voice.get("library_voice_id")),
                "cast_route": _route(
                    "cast",
                    project_id=project_id,
                    character_id=character_id,
                    source="voice-library",
                    return_route="#/voices",
                ),
            }
        )
    return result


def _uses_built_in(usage: Mapping[str, Any], voice_name: str) -> bool:
    return (
        usage.get("production_method") in {"custom", "builtin", "built_in"}
        and usage.get("selected_voice") == voice_name
    )


def _uses_standard_clone(usage: Mapping[str, Any], config: Mapping[str, Any]) -> bool:
    return (
        usage.get("production_method") == "clone"
        and str(config.get("clone_backend") or "qwen3_base") == "qwen3_base"
    )


def _uses_controlled_clone(usage: Mapping[str, Any], config: Mapping[str, Any]) -> bool:
    return (
        usage.get("production_method") == "clone"
        and str(config.get("clone_backend") or "qwen3_base")
        in {
            "qwen3_instruction_controlled",
            "voxcpm2_controlled",
            ROUTED_CLONE_BACKEND,
        }
    )


def _artifact_preview_url(artifact: Mapping[str, Any]) -> str | None:
    technical = _mapping(artifact.get("technical_details"))
    path = _text(technical.get("relative_path"))
    if not path:
        records = technical.get("file_records")
        if isinstance(records, list):
            for record in records:
                candidate = _text(_mapping(record).get("path"))
                if candidate and Path(candidate).suffix.casefold() in {
                    ".wav",
                    ".mp3",
                    ".flac",
                    ".m4a",
                    ".ogg",
                }:
                    path = candidate
                    break
    if not path or Path(path).suffix.casefold() not in {
        ".wav",
        ".mp3",
        ".flac",
        ".m4a",
        ".ogg",
    }:
        return None
    return "/" + path.lstrip("/")


def _resource(
    *,
    method: str,
    key: str,
    name: str,
    state: str,
    description: str,
    usages: list[dict[str, Any]],
    project_id: str | None,
    capability: Mapping[str, Any],
    source_artifact: Mapping[str, Any] | None = None,
    preview_url: str | None = None,
    native_route: Mapping[str, Any] | None = None,
    technical_details: Mapping[str, Any] | None = None,
    assignment: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    item = {
        "voice_id": _stable_id("voice", method, key),
        "key": key,
        "name": name,
        "method": method,
        "method_label": METHOD_LABELS[method],
        "state": state,
        "description": description,
        "usage": usages,
        "usage_count": len(usages),
        "assigned": bool(usages),
        "capability": dict(capability),
        "preview": {
            "available": bool(preview_url),
            "url": preview_url,
            "title": name,
            "context": METHOD_LABELS[method],
        },
        "native_route": dict(native_route or _route("voices", project_id=project_id)),
        "assignment_route": (
            usages[0]["cast_route"]
            if len(usages) == 1
            else _route("cast", project_id=project_id, source="voice-library", return_route="#/voices")
        ),
        "assignment": dict(assignment or {"supported": False}),
        "assignment_mutation_supported": bool(
            _mapping(assignment).get("supported") is True
        ),
        "source_artifact_id": (
            source_artifact.get("artifact_id") if source_artifact else None
        ),
        "technical_details": dict(technical_details or {}),
    }
    item["fingerprint"] = fingerprint_value(item)
    return item


def _method_capabilities(capabilities: Mapping[str, Any]) -> list[dict[str, Any]]:
    environment = _mapping(capabilities.get("environment"))
    caches = _mapping(environment.get("mlx_models_cached"))
    expressive = _mapping(capabilities.get("expressive_clone"))
    methods = {
        "built_in": {
            "state": "available" if caches.get("custom_voice") else "model_required",
            "production_supported": bool(caches.get("custom_voice")),
            "preview_supported": bool(caches.get("custom_voice")),
            "instruction_supported": True,
            "message": "Keeps the built-in speaker and persistent description while applying each Script line direction.",
        },
        "designed": {
            "state": "available" if caches.get("voice_design") else "model_required",
            "production_supported": bool(caches.get("voice_design")),
            "preview_supported": bool(caches.get("voice_design")),
            "instruction_supported": True,
            "message": "Uses a persistent designed-Voice description.",
        },
        "supplied_recording": {
            "state": "available" if caches.get("clone") else "model_required",
            "production_supported": bool(caches.get("clone")),
            "preview_supported": bool(caches.get("clone")),
            "instruction_supported": False,
            "message": "Retains supplied identity; line instructions are not sent to the clone model.",
        },
        "instruction_controlled": {
            "state": _text(expressive.get("status")) or "experimental_preview",
            "production_supported": expressive.get("supported") is True,
            "preview_supported": expressive.get("experimental_preview_available") is True,
            "instruction_supported": expressive.get("instruction_channel_present") is True,
            "message": _text(expressive.get("warning"))
            or "Experimental preview and manual review are required.",
        },
        "adapter": {
            "state": "review_required",
            "production_supported": False,
            "preview_supported": True,
            "instruction_supported": False,
            "message": "Technical completion does not imply listening or production approval.",
        },
        "alias": {
            "state": "available",
            "production_supported": True,
            "preview_supported": False,
            "instruction_supported": False,
            "message": "Resolves to another authoritative Cast Voice configuration.",
        },
    }
    return [
        {
            "method": method,
            "label": METHOD_LABELS[method],
            "description": METHOD_DESCRIPTIONS[method],
            **methods[method],
        }
        for method in METHOD_ORDER
    ]


def resolve_voice_library_assignment(
    *,
    voice_id: str,
    reusable_root_dir: str | Path | None,
) -> dict[str, Any]:
    requested = _text(voice_id)
    if not requested:
        raise VoiceLibraryError(
            "voice_library_voice_required",
            "Choose a Voice before saving the Cast assignment.",
        )
    for voice_name in BUILT_IN_VOICES:
        candidate_id = _stable_id("voice", "built_in", voice_name)
        if candidate_id == requested:
            return {
                "voice_id": candidate_id,
                "kind": "built_in",
                "name": voice_name.replace("_", " "),
                "configuration": {
                    "type": "custom",
                    "voice": voice_name,
                    "library_voice_id": candidate_id,
                },
                "assets": [],
            }

    if reusable_root_dir is None:
        raise VoiceLibraryError(
            "voice_library_voice_not_found",
            "The selected reusable Voice is no longer available.",
        )
    reusable_root = Path(reusable_root_dir).expanduser().resolve()
    if not reusable_root.is_dir() or reusable_root.is_symlink():
        raise VoiceLibraryError(
            "voice_library_reusable_root_invalid",
            "The reusable Voice collection is unavailable or unsafe.",
        )
    try:
        community_packs = list_qwen_packs(reusable_root=reusable_root)
    except CommunityQwenPackError as exc:
        raise VoiceLibraryError(exc.code, str(exc)) from exc
    for item in community_packs:
        pack_id = _text(item.get("pack_id"))
        if not pack_id:
            continue
        candidate_id = _stable_id(
            "voice",
            "instruction_controlled",
            f"community:{pack_id}",
        )
        if candidate_id != requested:
            continue
        try:
            pack, source = resolve_qvoice_pack(
                pack_id=pack_id,
                reusable_root=reusable_root,
                require_approved=True,
            )
        except CommunityQwenPackError as exc:
            raise VoiceLibraryError(exc.code, str(exc)) from exc
        relative = source.relative_to(reusable_root).as_posix()
        description = _text(pack.get("persistent_description")) or ""
        return {
            "voice_id": candidate_id,
            "kind": "community_qvoice",
            "name": _text(pack.get("name")) or pack_id,
            "configuration": {
                "type": "community_qvoice",
                "voice": _text(pack.get("name")) or pack_id,
                "description": description,
                "character_style": description,
                "community_pack_id": pack_id,
                "community_pack_path": relative,
                "community_pack_sha256": pack.get("sha256"),
                "community_pack_approval_fingerprint": pack.get(
                    "approval_fingerprint"
                ),
                "library_voice_id": candidate_id,
            },
            "assets": [{"relative_path": relative, "source_path": source}],
            "preview_path": (
                reusable_root / str(pack.get("preview"))
                if _text(pack.get("preview"))
                else None
            ),
        }
    for item in _load_designed_manifest(reusable_root):
        item_id = _text(item.get("id"))
        filename = _text(item.get("filename"))
        candidate_id = _stable_id("voice", "designed", f"reusable:{item_id}")
        preview = _safe_asset_path(reusable_root, f"designed_voices/{filename}")
        if candidate_id != requested or preview is None:
            continue
        description = _text(item.get("description"))
        return {
            "voice_id": candidate_id,
            "kind": "reusable_designed",
            "name": _text(item.get("name")) or item_id,
            "configuration": {
                "type": "design",
                "voice": None,
                "description": description,
                "character_style": description,
                "library_voice_id": candidate_id,
            },
            "assets": [{
                "relative_path": f"designed_voices/{filename}",
                "source_path": str(preview),
            }],
        }
    reusable_config = _load_voice_config(reusable_root)
    for configuration_key, source_value in sorted(reusable_config.items()):
        if source_value.get("type") != "clone" or source_value.get("alias_of"):
            continue
        backend = str(source_value.get("clone_backend") or "qwen3_base")
        controlled = backend in {
            "qwen3_instruction_controlled",
            "voxcpm2_controlled",
            ROUTED_CLONE_BACKEND,
        }
        method = "instruction_controlled" if controlled else "supplied_recording"
        candidate_id = _stable_id(
            "voice",
            method,
            f"reusable:{configuration_key}",
        )
        if candidate_id != requested:
            continue
        if backend == "voxcpm2_controlled":
            raise VoiceLibraryError(
                "voice_library_legacy_clone_blocked",
                "This saved VoxCPM2 Voice cannot be assigned to production.",
            )
        approval_field = (
            "responsive_backend_configuration_fingerprint"
            if backend == ROUTED_CLONE_BACKEND
            else "controlled_clone_configuration_fingerprint"
        )
        if controlled and not _text(source_value.get(approval_field)):
            raise VoiceLibraryError(
                "voice_library_clone_review_required",
                "This controlled clone needs a completed listening review before assignment.",
            )

        configuration = copy.deepcopy(dict(source_value))
        configuration.pop("alias_of", None)
        configuration["library_voice_id"] = candidate_id
        assets: list[dict[str, Any]] = []
        seen: set[str] = set()

        def bind_asset(container: dict[str, Any], field: str) -> None:
            raw = _text(container.get(field))
            if not raw:
                return
            source = _safe_asset_path(reusable_root, raw)
            if source is None:
                raise VoiceLibraryError(
                    "voice_library_asset_missing",
                    f"The saved Voice is missing required asset {Path(raw).name}.",
                )
            relative = source.relative_to(reusable_root).as_posix()
            container[field] = relative
            if relative not in seen:
                assets.append({"relative_path": relative, "source_path": source})
                seen.add(relative)

        bind_asset(configuration, "ref_audio")
        if not _text(configuration.get("ref_text")):
            raise VoiceLibraryError(
                "voice_library_transcript_missing",
                "The saved Voice has no exact reference transcript.",
            )
        reference_bank = _text(configuration.get("reference_bank_path"))
        if reference_bank:
            bind_asset(configuration, "reference_bank_path")
        routing = configuration.get("experimental_prompt_routing")
        if isinstance(routing, Mapping):
            routing_copy = copy.deepcopy(dict(routing))
            routes = routing_copy.get("routes")
            if isinstance(routes, Mapping):
                normalized_routes: dict[str, Any] = {}
                for route_key, route_value in routes.items():
                    route_copy = (
                        copy.deepcopy(dict(route_value))
                        if isinstance(route_value, Mapping)
                        else route_value
                    )
                    if isinstance(route_copy, dict):
                        bind_asset(route_copy, "ref_audio")
                    normalized_routes[str(route_key)] = route_copy
                routing_copy["routes"] = normalized_routes
            configuration["experimental_prompt_routing"] = routing_copy
        responsive_routing = configuration.get("responsive_backend_routing")
        if isinstance(responsive_routing, Mapping):
            responsive_copy = copy.deepcopy(dict(responsive_routing))
            responsive_routes = responsive_copy.get("routes")
            if isinstance(responsive_routes, Mapping):
                normalized_routes: dict[str, Any] = {}
                for route_key, route_value in responsive_routes.items():
                    route_copy = (
                        copy.deepcopy(dict(route_value))
                        if isinstance(route_value, Mapping)
                        else route_value
                    )
                    if isinstance(route_copy, dict):
                        bind_asset(route_copy, "identity_audio")
                        bind_asset(route_copy, "performance_audio")
                    normalized_routes[str(route_key)] = route_copy
                responsive_copy["routes"] = normalized_routes
            configuration["responsive_backend_routing"] = responsive_copy
        return {
            "voice_id": candidate_id,
            "kind": "reusable_clone",
            "name": _saved_clone_name(
                configuration_key,
                str(configuration.get("ref_audio") or ""),
            ),
            "source_configuration_key": configuration_key,
            "configuration": configuration,
            "assets": assets,
        }
    raise VoiceLibraryError(
        "voice_library_voice_not_found",
        "The selected Voice is no longer available.",
    )


def resolve_voice_library_preview(
    *,
    voice_id: str,
    reusable_root_dir: str | Path | None,
) -> Path:
    assignment = resolve_voice_library_assignment(
        voice_id=voice_id,
        reusable_root_dir=reusable_root_dir,
    )
    if assignment["kind"] == "reusable_designed":
        return Path(assignment["assets"][0]["source_path"])
    if assignment["kind"] == "community_qvoice":
        root = Path(reusable_root_dir).expanduser().resolve()
        pack_id = _text(
            assignment.get("configuration", {}).get("community_pack_id")
        )
        try:
            pack, _ = resolve_qvoice_pack(
                pack_id=pack_id,
                reusable_root=root,
                require_approved=True,
            )
            return resolve_qvoice_preview(item=pack, reusable_root=root)
        except CommunityQwenPackError as exc:
            raise VoiceLibraryError(exc.code, str(exc)) from exc
    if assignment["kind"] != "reusable_clone":
        raise VoiceLibraryError(
            "voice_library_preview_unavailable",
            "This Voice does not have a reusable preview clip.",
        )
    reference = _text(assignment["configuration"].get("ref_audio"))
    for asset in assignment["assets"]:
        if asset.get("relative_path") == reference:
            return Path(asset["source_path"])
    raise VoiceLibraryError(
        "voice_library_preview_unavailable",
        "The saved Voice preview clip is unavailable.",
    )


def build_voice_library(
    *,
    root_dir: str | Path,
    project_id: str | None = None,
    return_route: str | None = "#/voices",
    reusable_root_dir: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise VoiceLibraryError(
            "voice_library_root_invalid",
            "The active project root is unavailable or unsafe.",
        )
    config = _load_voice_config(root)
    reusable_root = (
        Path(reusable_root_dir).expanduser().resolve()
        if reusable_root_dir is not None
        else None
    )
    reusable_config = (
        _load_voice_config(reusable_root)
        if reusable_root is not None
        and reusable_root.is_dir()
        and not reusable_root.is_symlink()
        and reusable_root != root
        else {}
    )
    try:
        cast = inspect_cast_project(root_dir=root)
        inventory = inspect_library_inventory(
            root_dir=root,
            project_id=project_id,
            return_route=return_route,
        )
    except (CastAggregateError, LibraryInventoryError) as exc:
        raise VoiceLibraryError(
            "voice_library_source_invalid",
            f"Voice Library could not read the active project: {exc}",
        ) from exc
    capabilities = build_voice_backend_capabilities(root_dir=root)
    method_capabilities = _method_capabilities(capabilities)
    capability_by_method = {
        item["method"]: item for item in method_capabilities
    }
    assignments = _assignment_usage(cast, project_id)
    resources: list[dict[str, Any]] = []

    for voice_name in BUILT_IN_VOICES:
        usages = [item for item in assignments if _uses_built_in(item, voice_name)]
        resources.append(
            _resource(
                method="built_in",
                key=voice_name,
                name=voice_name.replace("_", " "),
                state=capability_by_method["built_in"]["state"],
                description="Pinned built-in Qwen speaker.",
                usages=usages,
                project_id=project_id,
                capability=capability_by_method["built_in"],
                native_route=_route(
                    "more",
                    project_id=project_id,
                    tool="voice-designer",
                    mode="builtin-preview",
                    source=voice_name,
                    return_route=return_route,
                ),
                technical_details={"speaker_key": voice_name, "scope": "built_in"},
                assignment={
                    "supported": capability_by_method["built_in"]["production_supported"] is True,
                    "kind": "built_in",
                    "production_method": "custom",
                    "label": f"Use {voice_name.replace('_', ' ')}",
                },
            )
        )

    for configuration_key, value in sorted(reusable_config.items()):
        if value.get("type") != "clone" or value.get("alias_of"):
            continue
        reference = _text(value.get("ref_audio"))
        transcript = _text(value.get("ref_text"))
        reference_path = (
            _safe_asset_path(reusable_root, reference)
            if reusable_root is not None
            else None
        )
        if not reference or not transcript or reference_path is None:
            continue
        backend = str(value.get("clone_backend") or "qwen3_base")
        controlled = backend in {
            "qwen3_instruction_controlled",
            "voxcpm2_controlled",
            ROUTED_CLONE_BACKEND,
        }
        method = "instruction_controlled" if controlled else "supplied_recording"
        approval_field = (
            "responsive_backend_configuration_fingerprint"
            if backend == ROUTED_CLONE_BACKEND
            else "controlled_clone_configuration_fingerprint"
        )
        approved = bool(_text(value.get(approval_field))) if controlled else True
        capability = dict(capability_by_method[method])
        if controlled and approved and backend in {
            "qwen3_instruction_controlled",
            ROUTED_CLONE_BACKEND,
        }:
            capability.update(
                {
                    "state": "approved",
                    "production_supported": True,
                    "preview_supported": True,
                    "instruction_supported": True,
                    "message": (
                        "This exact saved clone has a matching listening-approval "
                        "fingerprint. Editing it requires a new preview."
                    ),
                }
            )
        resource_key = f"reusable:{configuration_key}"
        voice_id = _stable_id("voice", method, resource_key)
        usages = [
            item
            for item in assignments
            if item.get("library_voice_id") == voice_id
        ]
        name = _saved_clone_name(configuration_key, reference)
        resource = _resource(
            method=method,
            key=resource_key,
            name=name,
            state=(
                "approved"
                if controlled and approved and backend in {
                    "qwen3_instruction_controlled",
                    ROUTED_CLONE_BACKEND,
                }
                else "legacy_blocked"
                if backend == "voxcpm2_controlled"
                else capability.get("state") or "available"
            ),
            description=(
                "Saved instruction-controlled clone."
                if controlled
                else "Saved supplied-recording clone."
            ),
            usages=usages,
            project_id=project_id,
            capability=capability,
            preview_url=f"/api/voice-library/{voice_id}/preview",
            native_route=_route(
                "voices",
                project_id=project_id,
                source=voice_id,
                return_route=return_route,
            ),
            technical_details={
                "scope": "reusable",
                "source_configuration_key": configuration_key,
                "backend": backend,
                "reference_filename": reference_path.name,
                "listening_approval_present": approved,
            },
            assignment={
                "supported": bool(
                    capability.get("production_supported") is True
                    and backend != "voxcpm2_controlled"
                ),
                "kind": "reusable_clone",
                "production_method": "clone",
                "label": f"Use {name}",
                "requires_new_preview_if_edited": controlled,
            },
        )
        resources.append(resource)

    if reusable_root is not None and reusable_root.is_dir():
        try:
            community_packs = list_qwen_packs(reusable_root=reusable_root)
        except CommunityQwenPackError as exc:
            raise VoiceLibraryError(exc.code, str(exc)) from exc
        for item in community_packs:
            pack_id = _text(item.get("pack_id"))
            if not pack_id:
                continue
            resource_key = f"community:{pack_id}"
            voice_id = _stable_id("voice", "instruction_controlled", resource_key)
            approved = (
                item.get("state") == "approved"
                and item.get("production_supported") is True
                and item.get("approval_fingerprint") == item.get("preview_fingerprint")
            )
            capability = {
                "state": "approved" if approved else "review_required",
                "production_supported": approved,
                "preview_supported": True,
                "instruction_supported": True,
                "message": (
                    "This imported Qwen Voice passed its exact listening review."
                    if approved
                    else "Generate, listen to, and approve a preview before Cast assignment."
                ),
            }
            usages = [
                value
                for value in assignments
                if value.get("library_voice_id") == voice_id
            ]
            resources.append(
                _resource(
                    method="instruction_controlled",
                    key=resource_key,
                    name=_text(item.get("name")) or pack_id,
                    state="approved" if approved else "review_required",
                    description="Imported Qwen community Voice with per-line direction control.",
                    usages=usages,
                    project_id=project_id,
                    capability=capability,
                    preview_url=(
                        f"/api/community-qwen-packs/{pack_id}/preview"
                        if _text(item.get("preview"))
                        else None
                    ),
                    native_route=_route(
                        "voices",
                        project_id=project_id,
                        source=pack_id,
                        return_route=return_route,
                    ),
                    technical_details={
                        "scope": "reusable",
                        "community_pack_id": pack_id,
                        "family": item.get("family"),
                        "prompt_mode": item.get("prompt_mode"),
                        "language": item.get("language"),
                        "license_name": item.get("license_name"),
                        "license_is_informational": True,
                        "sections": item.get("sections"),
                    },
                    assignment={
                        "supported": approved,
                        "kind": "community_qvoice",
                        "production_method": "community_qvoice",
                        "label": f"Use {_text(item.get('name')) or pack_id}",
                        "requires_new_preview_if_edited": True,
                    },
                )
            )

    if reusable_root is not None and reusable_root.is_dir() and reusable_root != root:
        designed_capability = capability_by_method["designed"]
        for item in _load_designed_manifest(reusable_root):
            item_id = _text(item.get("id"))
            filename = _text(item.get("filename"))
            preview = _safe_asset_path(reusable_root, f"designed_voices/{filename}")
            if not item_id or preview is None:
                continue
            resource_key = f"reusable:{item_id}"
            voice_id = _stable_id("voice", "designed", resource_key)
            usages = [entry for entry in assignments if entry.get("library_voice_id") == voice_id]
            name = _text(item.get("name")) or item_id
            resources.append(_resource(
                method="designed",
                key=resource_key,
                name=name,
                state=designed_capability.get("state") or "available",
                description=_text(item.get("description")) or METHOD_DESCRIPTIONS["designed"],
                usages=usages,
                project_id=project_id,
                capability=designed_capability,
                preview_url=f"/api/voice-library/{voice_id}/preview",
                technical_details={"scope": "reusable", "source_voice_id": item_id},
                assignment={
                    "supported": designed_capability.get("production_supported") is True,
                    "kind": "reusable_designed",
                    "production_method": "design",
                    "label": f"Use {name}",
                },
            ))

    artifacts = inventory.get("artifacts", [])
    config_by_reference: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for configuration_key, value in config.items():
        reference = _text(value.get("ref_audio"))
        if reference:
            config_by_reference.setdefault(Path(reference).name.casefold(), []).append(
                (configuration_key, value)
            )

    for artifact in artifacts:
        if not isinstance(artifact, Mapping) or artifact.get("kind") not in ARTIFACT_METHODS:
            continue
        method = ARTIFACT_METHODS[str(artifact["kind"])]
        artifact_key = _text(artifact.get("key")) or _text(artifact.get("artifact_id")) or "voice"
        artifact_name = _text(artifact.get("name")) or artifact_key
        usage_candidates = assignments
        if method == "designed":
            usages = [
                item
                for item in usage_candidates
                if item.get("production_method") == "design"
                and item.get("selected_voice") in {artifact_key, artifact_name}
            ]
        elif method == "adapter":
            usages = [
                item
                for item in usage_candidates
                if item.get("production_method") in {"lora", "builtin_lora"}
                and item.get("adapter_id") in {artifact_key, artifact_name}
            ]
        else:
            aliases = {
                Path(value).name.casefold()
                for value in _mapping(artifact.get("technical_details")).get(
                    "identity_aliases", []
                )
                if isinstance(value, str)
            }
            matches: list[tuple[str, dict[str, Any]]] = []
            for alias in aliases | {artifact_key.casefold(), artifact_name.casefold()}:
                matches.extend(config_by_reference.get(alias, []))
            matched_keys = {item[0] for item in matches}
            usages = [
                item
                for item in usage_candidates
                if item.get("configuration_key") in matched_keys
                and _uses_standard_clone(item, config.get(item.get("configuration_key"), {}))
            ]
        preview_url = _artifact_preview_url(artifact)
        resources.append(
            _resource(
                method=method,
                key=artifact_key,
                name=artifact_name,
                state=_text(artifact.get("state")) or "unknown",
                description=METHOD_DESCRIPTIONS[method],
                usages=usages,
                project_id=project_id,
                capability=capability_by_method[method],
                source_artifact=artifact,
                preview_url=preview_url,
                native_route=_mapping(artifact.get("native_route")),
                technical_details={
                    "scope": "project",
                    "size_bytes": artifact.get("size_bytes"),
                    "file_count": artifact.get("file_count"),
                    "modified_at_utc": artifact.get("modified_at_utc"),
                    "provenance": artifact.get("provenance"),
                    "metadata_error": artifact.get("metadata_error"),
                },
            )
        )

    for configuration_key, value in sorted(config.items()):
        if value.get("type") != "clone":
            continue
        backend = str(value.get("clone_backend") or "qwen3_base")
        if backend not in {
            "qwen3_instruction_controlled",
            "voxcpm2_controlled",
            ROUTED_CLONE_BACKEND,
        }:
            continue
        usages = [
            item
            for item in assignments
            if item.get("configuration_key") == configuration_key
            and _uses_controlled_clone(item, value)
        ]
        approval_field = (
            "responsive_backend_configuration_fingerprint"
            if backend == ROUTED_CLONE_BACKEND
            else "controlled_clone_configuration_fingerprint"
        )
        reviewed_responsive = bool(
            backend == ROUTED_CLONE_BACKEND and _text(value.get(approval_field))
        )
        state = (
            "legacy_blocked"
            if backend == "voxcpm2_controlled"
            else "approved"
            if reviewed_responsive
            else capability_by_method["instruction_controlled"]["state"]
        )
        resources.append(
            _resource(
                method="instruction_controlled",
                key=configuration_key,
                name=configuration_key,
                state=state,
                description=(
                    "Legacy VoxCPM2 assignment; production synthesis is blocked."
                    if backend == "voxcpm2_controlled"
                    else "Reviewed recurring Voice with model-specific delivery routing."
                    if backend == ROUTED_CLONE_BACKEND
                    else METHOD_DESCRIPTIONS["instruction_controlled"]
                ),
                usages=usages,
                project_id=project_id,
                capability=capability_by_method["instruction_controlled"],
                native_route=_route(
                    "cast",
                    project_id=project_id,
                    character_id=usages[0]["character_id"] if len(usages) == 1 else None,
                    source="voice-library",
                    return_route=return_route,
                ),
                technical_details={
                    "backend": backend,
                    "reference_audio_configured": bool(_text(value.get("ref_audio"))),
                    "reference_transcript_configured": bool(_text(value.get("ref_text"))),
                    "identity_description_configured": bool(
                        _text(value.get("character_style") or value.get("default_style"))
                    ),
                    "approval_fingerprint_present": bool(
                        value.get(approval_field)
                    ),
                },
            )
        )

    try:
        alias_validation = validate_voice_aliases(config)
    except VoiceAliasError as exc:
        alias_validation = {
            "valid": False,
            "error": str(exc),
            "aliases": [],
        }
    alias_rows = [
        {"alias": key, **dict(row)}
        for key, row in alias_validation.items()
        if isinstance(row, Mapping) and row.get("is_alias") is True
    ] if isinstance(alias_validation, Mapping) else []
    for row in alias_rows:
        alias = _text(row.get("alias")) or "Alias"
        target = _text(row.get("resolved_target") or row.get("alias_of")) or "Unknown target"
        usages = [
            item
            for item in assignments
            if item.get("configuration_key") == alias
            or item.get("alias_target") == target
        ]
        resources.append(
            _resource(
                method="alias",
                key=alias,
                name=alias,
                state="available" if row.get("valid") is not False else "invalid",
                description=f"Resolves to {target} without duplicating its Voice configuration.",
                usages=usages,
                project_id=project_id,
                capability=capability_by_method["alias"],
                native_route=_route(
                    "cast",
                    project_id=project_id,
                    character_id=usages[0]["character_id"] if len(usages) == 1 else None,
                    source="voice-library",
                    return_route=return_route,
                ),
                technical_details={
                    "target": target,
                    "resolution_chain": row.get("chain"),
                    "resolved_type": row.get("resolved_type"),
                    "resolved_source": row.get("resolved_source"),
                },
            )
        )

    resources.sort(
        key=lambda item: (
            METHOD_ORDER.index(item["method"]),
            item["name"].casefold(),
            item["voice_id"],
        )
    )
    methods_present = sorted(
        {item["method"] for item in resources},
        key=METHOD_ORDER.index,
    )
    states = sorted({str(item["state"]) for item in resources})
    summary = {
        "voice_count": len(resources),
        "assigned_voice_count": sum(item["assigned"] for item in resources),
        "assignment_count": sum(item["usage_count"] for item in resources),
        "invalid_voice_count": sum(
            item["state"] in {"invalid", "legacy_blocked"}
            for item in resources
        ),
        "method_counts": {
            method: sum(item["method"] == method for item in resources)
            for method in METHOD_ORDER
        },
        "cast_character_count": len(assignments),
        "cast_blocker_count": _mapping(cast.get("summary")).get("blocker_count", 0),
    }
    result = {
        "schema_version": VOICE_LIBRARY_SCHEMA_VERSION,
        "project_id": project_id,
        "summary": summary,
        "methods": method_capabilities,
        "filters": {
            "methods": methods_present,
            "states": states,
        },
        "voices": resources,
        "assignment_mutation_supported": any(
            item.get("assignment_mutation_supported") is True
            for item in resources
        ),
        "cast_is_authoritative": True,
        "fingerprint": fingerprint_value(
            {
                "summary": summary,
                "methods": method_capabilities,
                "voices": [item["fingerprint"] for item in resources],
            }
        ),
    }
    return result
