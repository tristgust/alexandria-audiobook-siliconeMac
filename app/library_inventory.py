from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlencode

from generation_state import fingerprint_value


SCHEMA_VERSION = 1
SAFE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_ARTIFACT_FILES = 20_000
MAX_REFERENCE_FILES = 8_000

ARTIFACT_KINDS = frozenset(
    {
        "source_book",
        "production_audio",
        "export_output",
        "designed_voice",
        "clone_reference",
        "owned_recording",
        "expressive_reference_bank",
        "voice_preparation_project",
        "preparer_output",
        "dataset_builder_project",
        "lora_dataset",
        "lora_adapter",
    }
)

DELETE_ROUTES = {
    "designed_voice": "/api/voice_design/{voice_id}",
    "clone_reference": "/api/clone_voices/{voice_id}",
    "dataset_builder_project": "/api/dataset_builder/{name}",
    "lora_dataset": "/api/lora/datasets/{dataset_id}",
    "lora_adapter": "/api/lora/models/{adapter_id}",
}

NATIVE_DESTINATIONS = {
    "source_book": "script",
    "production_audio": "produce",
    "export_output": "export",
}

EXPORT_OUTPUT_FILENAMES = {
    "mp3": "cloned_audiobook.mp3",
    "m4b": "audiobook.m4b",
    "audacity": "audacity_export.zip",
}

VOICE_LAB_TOOLS = {
    "designed_voice": ("voice-designer", "library"),
    "clone_reference": ("voice-designer", "clone-reference"),
    "owned_recording": ("audio-preparer", "owned-recording"),
    "expressive_reference_bank": ("voice-training", "reference-bank"),
    "voice_preparation_project": ("voice-training", "preparation"),
    "preparer_output": ("audio-preparer", "output"),
    "dataset_builder_project": ("dataset-builder", "project"),
    "lora_dataset": ("dataset-builder", "dataset"),
    "lora_adapter": ("voice-training", "adapter"),
}

CURRENT_REFERENCE_PATHS = (
    "voice_config.json",
    "character_roster.json",
    "character_roster.draft.json",
    "audio_validity.json",
    "export_build.json",
    "roster_import_enrichment.json",
)

CURRENT_REFERENCE_DIRECTORIES = (
    "voice_training_projects",
    "dataset_builder",
    "lora_models",
)

HISTORY_REFERENCE_DIRECTORIES = (
    "external_workflows",
    "speaker_management_history",
    "character_roster_history",
    "export_build_history",
    "migration_backups",
)


class LibraryInventoryError(RuntimeError):
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
        self.context = copy.deepcopy(dict(context or {}))

    def as_detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.detail,
            "context": copy.deepcopy(self.context),
        }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _safe_key(value: str, *, label: str = "artifact key") -> str:
    text = str(value or "").strip()
    if not SAFE_KEY_PATTERN.fullmatch(text):
        raise LibraryInventoryError(
            status_code=409,
            code="library_artifact_key_invalid",
            detail=f"{label.title()} is invalid.",
            context={"value": text},
        )
    return text


def _relative(root: Path, path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise LibraryInventoryError(
            status_code=409,
            code="library_artifact_path_unsafe",
            detail="A Library artifact escaped the project root.",
            context={"path": str(resolved)},
        ) from exc


def _read_json(path: Path) -> tuple[Any, str | None]:
    if not path.exists():
        return None, None
    if not path.is_file() or path.is_symlink():
        return None, f"{path.name} is not a safe regular file."
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            return None, f"{path.name} exceeds the Library metadata limit."
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return None, f"{path.name} is invalid JSON: {exc}"


def _safe_metadata(value: Any) -> dict[str, Any]:
    source = _mapping(value)
    allowed = (
        "schema_version",
        "status",
        "created_at_utc",
        "updated_at_utc",
        "description",
        "name",
        "character_id",
        "speaker",
        "dataset_id",
        "adapter_id",
        "source_kind",
        "approval_status",
        "validation_status",
        "production_assignment_supported",
        "manual_audio_review_status",
        "language",
        "sample_count",
        "title",
        "author",
        "source_filename",
        "source_type",
        "source_language",
        "output_language",
        "total_chunks",
        "audio_file_count",
        "current_chunk_count",
        "pending_chunk_count",
        "stale_chunk_count",
        "failed_chunk_count",
        "format",
        "duration_ms",
        "built_at_utc",
    )
    result: dict[str, Any] = {}
    for key in allowed:
        value = source.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            if value not in (None, ""):
                result[key] = value
    return result


def _latest_timestamp(paths: Iterable[Path]) -> tuple[int, str | None]:
    latest_ns = 0
    for path in paths:
        try:
            latest_ns = max(latest_ns, path.stat().st_mtime_ns)
        except OSError:
            continue
    if latest_ns <= 0:
        return 0, None
    timestamp = datetime.fromtimestamp(
        latest_ns / 1_000_000_000,
        tz=timezone.utc,
    ).isoformat().replace("+00:00", "Z")
    return latest_ns, timestamp


def _artifact_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    result: list[Path] = []
    if not path.is_dir() or path.is_symlink():
        return result
    for candidate in sorted(path.rglob("*")):
        if candidate.is_symlink():
            continue
        if candidate.is_file():
            result.append(candidate)
            if len(result) >= MAX_ARTIFACT_FILES:
                break
    return result


def _file_summary(root: Path, files: list[Path]) -> dict[str, Any]:
    records = []
    total = 0
    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        total += stat.st_size
        records.append(
            {
                "path": _relative(root, path),
                "size_bytes": stat.st_size,
                "modified_ns": stat.st_mtime_ns,
            }
        )
    records.sort(key=lambda item: item["path"])
    return {
        "file_count": len(records),
        "size_bytes": total,
        "records": records,
    }


def _artifact_id(kind: str, key: str) -> str:
    return "library_" + fingerprint_value(
        {"kind": kind, "key": key}
    )[:24]


def _native_artifact_route(
    *,
    kind: str,
    artifact_id: str,
    project_id: str | None,
    character_id: str | None,
    return_route: str | None,
) -> dict[str, Any]:
    if kind in VOICE_LAB_TOOLS:
        return _voice_lab_route(
            kind=kind,
            artifact_id=artifact_id,
            project_id=project_id,
            character_id=character_id,
            return_route=return_route,
        )
    destination = NATIVE_DESTINATIONS[kind]
    context = {"source": artifact_id}
    if project_id:
        context["project"] = project_id
    if character_id:
        context["character"] = character_id
    if return_route:
        context["return"] = return_route
    query = urlencode(context)
    return {
        "destination": destination,
        "context": context,
        "hash": f"#/{destination}" + (f"?{query}" if query else ""),
    }


def _voice_lab_route(
    *,
    kind: str,
    artifact_id: str,
    project_id: str | None,
    character_id: str | None,
    return_route: str | None,
) -> dict[str, Any]:
    tool, mode = VOICE_LAB_TOOLS[kind]
    context = {
        "tool": tool,
        "mode": mode,
        "source": artifact_id,
    }
    if project_id:
        context["project"] = project_id
    if character_id:
        context["character"] = character_id
    if return_route:
        context["return"] = return_route
    return {
        "destination": "more",
        "tool": tool,
        "mode": mode,
        "context": context,
        "hash": "#/more?" + urlencode(context),
    }


def _artifact(
    *,
    root: Path,
    kind: str,
    key: str,
    name: str,
    path: Path,
    metadata_path: Path | None = None,
    metadata_value: Any = None,
    metadata_error: str | None = None,
    character_id: str | None = None,
    project_id: str | None = None,
    return_route: str | None = None,
    aliases: Iterable[str] = (),
    state: str | None = None,
) -> dict[str, Any]:
    if kind not in ARTIFACT_KINDS:
        raise LibraryInventoryError(
            status_code=500,
            code="library_artifact_kind_invalid",
            detail=f"Unsupported Library artifact kind: {kind}",
        )
    raw_key = str(key or "").strip()
    route_key = raw_key if SAFE_KEY_PATTERN.fullmatch(raw_key) else None
    safe_key = route_key or (
        "artifact_" + fingerprint_value(
            {"kind": kind, "source_key": raw_key}
        )[:24]
    )
    files = _artifact_files(path)
    summary = _file_summary(root, files)
    latest_ns, modified_at = _latest_timestamp(files or [path])
    relative_path = _relative(root, path)
    artifact_id = _artifact_id(kind, safe_key)
    if state is None:
        if metadata_error:
            state = "invalid"
        elif not path.exists():
            state = "missing"
        elif not files and path.is_dir():
            state = "empty"
        else:
            state = "available"
    metadata_summary = _safe_metadata(metadata_value)
    identity_aliases = {
        safe_key,
        raw_key,
        relative_path,
        path.name,
        path.stem,
        *(str(value).strip() for value in aliases if str(value).strip()),
    }
    for field in ("adapter_id", "dataset_id", "character_id", "name"):
        value = _text(_mapping(metadata_value).get(field))
        if value:
            identity_aliases.add(value)
    return {
        "artifact_id": artifact_id,
        "kind": kind,
        "key": safe_key,
        "source_key": raw_key,
        "name": name,
        "state": state,
        "size_bytes": summary["size_bytes"],
        "file_count": summary["file_count"],
        "modified_at_utc": modified_at,
        "modified_ns": latest_ns,
        "character_id": character_id
        or _text(_mapping(metadata_value).get("character_id")),
        "provenance": metadata_summary,
        "metadata_error": metadata_error,
        "usage": [],
        "dependency_count": 0,
        "blocking_dependency_count": 0,
        "delete": {
            "supported": kind in DELETE_ROUTES and route_key is not None,
            "route_key": route_key,
            "endpoint_template": DELETE_ROUTES.get(kind),
            "blocked": True,
            "reason": "Library dependencies have not been inspected yet.",
        },
        "native_route": _native_artifact_route(
            kind=kind,
            artifact_id=artifact_id,
            project_id=project_id,
            character_id=character_id,
            return_route=return_route,
        ),
        "voice_lab": (
            _voice_lab_route(
                kind=kind,
                artifact_id=artifact_id,
                project_id=project_id,
                character_id=character_id,
                return_route=return_route,
            )
            if kind in VOICE_LAB_TOOLS
            else None
        ),
        "technical_details": {
            "relative_path": relative_path,
            "metadata_relative_path": (
                _relative(root, metadata_path)
                if metadata_path is not None and metadata_path.exists()
                else None
            ),
            "file_records": summary["records"],
            "identity_aliases": sorted(identity_aliases),
        },
    }


def _group_files_by_stem(directory: Path) -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = {}
    if not directory.is_dir():
        return result
    for path in sorted(directory.iterdir()):
        if (
            path.name.startswith(".")
            or path.is_symlink()
            or not path.is_file()
        ):
            continue
        result.setdefault(path.stem, []).append(path)
    return result


def _scan_manifest_audio_directory(
    root: Path,
    *,
    directory_name: str,
    kind: str,
    missing_audio_message: str,
    **context: Any,
) -> list[dict[str, Any]]:
    directory = root / directory_name
    if not directory.is_dir():
        return []
    groups = _group_files_by_stem(directory)
    manifest_path = directory / "manifest.json"
    manifest_value, manifest_error = _read_json(manifest_path)
    if manifest_path.exists() and manifest_error is None and not isinstance(
        manifest_value, list
    ):
        manifest_error = "manifest.json must contain a JSON array."
    manifest_entries = manifest_value if isinstance(manifest_value, list) else []
    audio_extensions = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
    artifacts: list[dict[str, Any]] = []
    consumed: set[Path] = set()

    for index, raw_entry in enumerate(manifest_entries):
        entry = _mapping(raw_entry)
        filename = _text(entry.get("filename"))
        key = (
            _text(entry.get("id"))
            or (Path(filename).stem if filename else None)
            or f"manifest-entry-{index + 1}"
        )
        entry_error = None
        candidate = None
        if filename:
            if Path(filename).name != filename or filename in {".", ".."}:
                entry_error = "Manifest audio filename is unsafe."
            else:
                candidate = directory / filename
        matching_files = list(groups.get(candidate.stem, [])) if candidate else []
        if candidate and candidate.is_file() and candidate not in matching_files:
            matching_files.append(candidate)
        audio_files = [
            path
            for path in matching_files
            if path.suffix.casefold() in audio_extensions
        ]
        if not audio_files:
            entry_error = entry_error or missing_audio_message
        representative = audio_files[0] if audio_files else manifest_path
        aliases = [str(key)]
        for path in matching_files:
            aliases.extend([path.name, _relative(root, path)])
            consumed.add(path.resolve())
        if filename:
            aliases.extend([filename, f"{directory_name}/{filename}"])
        artifact = _artifact(
            root=root,
            kind=kind,
            key=str(key),
            name=_text(entry.get("name")) or str(key),
            path=representative,
            metadata_path=manifest_path,
            metadata_value=dict(entry),
            metadata_error=entry_error,
            aliases=aliases,
            state="invalid" if entry_error else None,
            **context,
        )
        artifact["technical_details"]["group_files"] = [
            _relative(root, path) for path in matching_files
        ]
        artifact["technical_details"]["manifest_index"] = index
        artifacts.append(artifact)

    for stem, files in groups.items():
        if stem == "manifest":
            continue
        remaining = [path for path in files if path.resolve() not in consumed]
        if not remaining:
            continue
        metadata_path = next(
            (path for path in remaining if path.suffix.casefold() == ".json"),
            None,
        )
        metadata_value, metadata_error = (
            _read_json(metadata_path)
            if metadata_path is not None
            else (None, manifest_error)
        )
        audio_files = [
            path
            for path in remaining
            if path.suffix.casefold() in audio_extensions
        ]
        representative = metadata_path or (
            audio_files[0] if audio_files else remaining[0]
        )
        state = None
        if not audio_files:
            state = "invalid"
            metadata_error = metadata_error or missing_audio_message
        artifact_key = (
            f"orphan-{stem}" if manifest_path.exists() else stem
        )
        artifact = _artifact(
            root=root,
            kind=kind,
            key=artifact_key,
            name=_text(_mapping(metadata_value).get("name")) or stem,
            path=representative,
            metadata_path=metadata_path,
            metadata_value=metadata_value,
            metadata_error=metadata_error,
            aliases=[
                *(path.name for path in remaining),
                *(_relative(root, path) for path in remaining),
            ],
            state=state,
            **context,
        )
        artifact["technical_details"]["group_files"] = [
            _relative(root, path) for path in remaining
        ]
        artifact["technical_details"]["orphaned_from_manifest"] = bool(
            manifest_path.exists()
        )
        artifact["delete"].update(
            {
                "supported": False,
                "route_key": None,
            }
        )
        artifacts.append(artifact)
    return artifacts


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_source_metadata(root: Path) -> tuple[dict[str, Any], Path | None, str | None]:
    manifest_path = root / "alexandria-project.json"
    state_path = root / "state.json"
    manifest, manifest_error = _read_json(manifest_path)
    state, state_error = _read_json(state_path)
    manifest_value = _mapping(manifest)
    source = dict(_mapping(manifest_value.get("source")))
    state_value = _mapping(state)
    source_path_text = _text(source.get("original_relative_path")) or _text(
        state_value.get("input_file_path")
    )
    if not source_path_text:
        return {}, manifest_path if manifest_path.exists() else state_path if state_path.exists() else None, manifest_error or state_error
    candidate = Path(source_path_text).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    source_error = manifest_error or state_error
    try:
        candidate.relative_to(root)
    except ValueError:
        source_error = "The source book path is outside the active project root."
    if candidate.is_symlink():
        source_error = "The source book path is a symbolic link."
    metadata = {
        "title": _text(source.get("title")) or _text(state_value.get("book_title")),
        "author": _text(source.get("author")) or _text(state_value.get("author")),
        "source_filename": _text(source.get("original_filename")) or candidate.name,
        "source_type": _text(source.get("type")) or candidate.suffix.lstrip(".").casefold() or "text",
        "source_language": _text(source.get("source_language")) or _text(state_value.get("source_language")),
        "output_language": _text(source.get("output_language")) or _text(state_value.get("output_language")),
        "created_at_utc": _text(manifest_value.get("created_at_utc")),
        "updated_at_utc": _text(manifest_value.get("updated_at_utc")),
    }
    metadata = {key: value for key, value in metadata.items() if value not in (None, "")}
    return metadata, candidate, source_error


def _scan_source_book(root: Path, **context: Any) -> list[dict[str, Any]]:
    metadata, source_path, source_error = _project_source_metadata(root)
    if source_path is None:
        return []
    source_safe = False
    try:
        source_path.relative_to(root)
        source_safe = source_path.is_file() and not source_path.is_symlink()
    except ValueError:
        source_safe = False
    metadata_path = (
        root / "alexandria-project.json"
        if (root / "alexandria-project.json").is_file()
        else root / "state.json"
    )
    representative = source_path if source_safe else metadata_path
    state = None
    metadata_error = source_error
    if not source_safe:
        state = "invalid" if source_path.exists() or source_error else "missing"
        metadata_error = metadata_error or "The source book file is missing."
    artifact = _artifact(
        root=root,
        kind="source_book",
        key=_text(context.get("project_id")) or source_path.name,
        name=_text(metadata.get("title")) or source_path.name,
        path=representative,
        metadata_path=metadata_path if metadata_path.is_file() else None,
        metadata_value=metadata,
        metadata_error=metadata_error,
        aliases=[source_path.name, _text(metadata.get("source_filename")) or ""],
        state=state,
        **context,
    )
    artifact["delete"].update({"supported": False, "route_key": None})
    artifact["technical_details"]["source_filename"] = source_path.name
    artifact["technical_details"]["source_path_valid"] = source_safe
    return [artifact]


def _scan_production_audio(root: Path, **context: Any) -> list[dict[str, Any]]:
    chunks_path = root / "chunks.json"
    validity_path = root / "audio_validity.json"
    voicelines = root / "voicelines"
    if not chunks_path.exists() and not validity_path.exists() and not voicelines.exists():
        return []
    chunks, chunks_error = _read_json(chunks_path)
    validity, validity_error = _read_json(validity_path)
    chunk_rows = chunks if isinstance(chunks, list) else []
    if chunks_path.exists() and chunks_error is None and not isinstance(chunks, list):
        chunks_error = "chunks.json must contain a JSON array."
    statuses = [str(_mapping(item).get("status") or "pending").casefold() for item in chunk_rows]
    audio_files = [
        path
        for path in _artifact_files(voicelines)
        if path.suffix.casefold() in {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
    ]
    metadata = {
        "total_chunks": len(chunk_rows),
        "audio_file_count": len(audio_files),
        "current_chunk_count": sum(value in {"complete", "current", "done"} for value in statuses),
        "pending_chunk_count": sum(value in {"pending", "ready", "ready_to_generate"} for value in statuses),
        "stale_chunk_count": sum(value == "stale" for value in statuses),
        "failed_chunk_count": sum(value == "failed" for value in statuses),
        "updated_at_utc": _text(_mapping(validity).get("updated_at_utc")),
    }
    metadata_error = chunks_error or validity_error
    if metadata_error:
        state = "invalid"
    elif bool(_mapping(validity).get("stale")) or metadata["stale_chunk_count"]:
        state = "stale"
    elif not audio_files:
        state = "empty"
    else:
        state = "available"
    artifact = _artifact(
        root=root,
        kind="production_audio",
        key=_text(context.get("project_id")) or "production-audio",
        name="Production audio",
        path=voicelines,
        metadata_path=validity_path if validity_path.is_file() else chunks_path if chunks_path.is_file() else None,
        metadata_value=metadata,
        metadata_error=metadata_error,
        aliases=["voicelines", "chunks.json", "audio_validity.json"],
        state=state,
        **context,
    )
    summary = _file_summary(root, audio_files)
    artifact["size_bytes"] = summary["size_bytes"]
    artifact["file_count"] = summary["file_count"]
    artifact["modified_at_utc"] = _latest_timestamp(audio_files or [chunks_path, validity_path])[1]
    artifact["technical_details"]["file_records"] = summary["records"]
    artifact["technical_details"]["relative_path"] = "voicelines"
    artifact["delete"].update({"supported": False, "route_key": None})
    return [artifact]


def _scan_export_outputs(root: Path, **context: Any) -> list[dict[str, Any]]:
    receipt_path = root / "export_build.json"
    receipt, receipt_error = _read_json(receipt_path)
    if receipt_path.exists() and receipt_error is None and not isinstance(receipt, Mapping):
        receipt_error = "export_build.json must contain a JSON object."
    receipt_value = _mapping(receipt)
    receipt_outputs = _mapping(receipt_value.get("outputs"))
    formats = set(receipt_outputs)
    formats.update(
        format_name
        for format_name, filename in EXPORT_OUTPUT_FILENAMES.items()
        if (root / filename).exists()
    )
    artifacts = []
    for format_name in sorted(formats):
        filename = EXPORT_OUTPUT_FILENAMES.get(format_name)
        if not filename:
            continue
        output_path = root / filename
        record = dict(_mapping(receipt_outputs.get(format_name)))
        metadata_error = receipt_error
        if not output_path.is_file() or output_path.is_symlink():
            state = "invalid"
            metadata_error = metadata_error or "The export receipt references a missing output file."
        elif record.get("sha256"):
            try:
                matches = _sha256_file(output_path) == str(record["sha256"])
            except OSError as exc:
                matches = False
                metadata_error = str(exc)
            state = "available" if matches else "invalid"
            if not matches:
                metadata_error = metadata_error or "The export output hash does not match its build receipt."
        else:
            state = "legacy_unverified"
        metadata = {
            "format": format_name,
            "duration_ms": record.get("duration_ms"),
            "built_at_utc": record.get("built_at_utc") or receipt_value.get("built_at_utc"),
            "validation_status": "verified" if state == "available" else state,
        }
        artifact = _artifact(
            root=root,
            kind="export_output",
            key=format_name,
            name={
                "mp3": "MP3 audiobook",
                "m4b": "M4B audiobook",
                "audacity": "Audacity project package",
            }.get(format_name, filename),
            path=output_path,
            metadata_path=receipt_path if receipt_path.is_file() else None,
            metadata_value=metadata,
            metadata_error=metadata_error,
            aliases=[filename, format_name],
            state=state,
            **context,
        )
        artifact["delete"].update({"supported": False, "route_key": None})
        artifacts.append(artifact)
    return artifacts


def _scan_designed_voices(
    root: Path,
    **context: Any,
) -> list[dict[str, Any]]:
    return _scan_manifest_audio_directory(
        root,
        directory_name="designed_voices",
        kind="designed_voice",
        missing_audio_message="Designed voice audio is missing.",
        **context,
    )


def _scan_clone_references(
    root: Path,
    **context: Any,
) -> list[dict[str, Any]]:
    return _scan_manifest_audio_directory(
        root,
        directory_name="clone_voices",
        kind="clone_reference",
        missing_audio_message="Clone reference audio is missing.",
        **context,
    )


def _read_jsonl_summary(path: Path) -> tuple[Any, str | None]:
    if not path.is_file() or path.is_symlink():
        return None, f"{path.name} is not a safe regular file."
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            return None, f"{path.name} exceeds the Library metadata limit."
        sample_count = 0
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, Mapping):
                return None, (
                    f"{path.name} line {line_number} must contain a JSON object."
                )
            sample_count += 1
        return {
            "status": "ready" if sample_count else "empty",
            "sample_count": sample_count,
        }, None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return None, f"{path.name} is invalid JSONL: {exc}"


def _scan_preparer_outputs(
    root: Path,
    **context: Any,
) -> list[dict[str, Any]]:
    directory = root / "preparer_output"
    if not directory.is_dir():
        return []
    artifacts = []
    for path in sorted(directory.iterdir()):
        if (
            path.name.startswith(".")
            or path.is_symlink()
            or not path.is_file()
        ):
            continue
        metadata_value = None
        metadata_error = None
        if path.suffix.casefold() == ".json":
            metadata_value, metadata_error = _read_json(path)
        artifacts.append(
            _artifact(
                root=root,
                kind="preparer_output",
                key=path.name,
                name=path.name,
                path=path,
                metadata_path=path if path.suffix.casefold() == ".json" else None,
                metadata_value=metadata_value,
                metadata_error=metadata_error,
                **context,
            )
        )
    return artifacts


def _scan_top_level_projects(
    root: Path,
    *,
    directory_name: str,
    kind: str,
    metadata_candidates: tuple[str, ...],
    **context: Any,
) -> list[dict[str, Any]]:
    directory = root / directory_name
    if not directory.is_dir():
        return []
    artifacts = []
    for path in sorted(directory.iterdir()):
        if (
            path.name.startswith(".")
            or path.is_symlink()
            or not path.is_dir()
        ):
            continue
        key = path.name
        metadata_path = next(
            (
                path / name
                for name in metadata_candidates
                if (path / name).is_file()
            ),
            None,
        )
        metadata_value, metadata_error = (
            _read_jsonl_summary(metadata_path)
            if metadata_path is not None
            and metadata_path.suffix.casefold() == ".jsonl"
            else _read_json(metadata_path)
            if metadata_path is not None
            else (None, None)
        )
        state = None
        if path.is_dir() and metadata_candidates and metadata_path is None:
            state = "invalid"
            metadata_error = (
                "Expected Library metadata is missing: "
                + ", ".join(metadata_candidates)
                + "."
            )
        artifacts.append(
            _artifact(
                root=root,
                kind=kind,
                key=key,
                name=_text(_mapping(metadata_value).get("name")) or key,
                path=path,
                metadata_path=metadata_path,
                metadata_value=metadata_value,
                metadata_error=metadata_error,
                aliases=[path.stem],
                **context,
                state=state,
            )
        )
    return artifacts


def _find_bank_metadata(project_dir: Path) -> list[Path]:
    result = []
    for pattern in (
        "reference_bank.json",
        "expressive_reference_bank.json",
        "*reference*bank*.json",
    ):
        result.extend(project_dir.glob(pattern))
    return sorted(
        {
            path.resolve()
            for path in result
            if path.is_file() and not path.is_symlink()
        }
    )


def _scan_voice_projects(
    root: Path,
    **context: Any,
) -> list[dict[str, Any]]:
    directory = root / "voice_training_projects"
    if not directory.is_dir():
        return []
    artifacts = []
    for project_dir in sorted(directory.iterdir()):
        if project_dir.is_symlink() or not project_dir.is_dir():
            continue
        project_path = project_dir / "project.json"
        project_value, project_error = _read_json(project_path)
        character_id = (
            _text(_mapping(project_value).get("character_id"))
            or project_dir.name
        )
        project_context = {
            **context,
            "character_id": character_id,
        }
        if project_path.exists():
            artifacts.append(
                _artifact(
                    root=root,
                    kind="voice_preparation_project",
                    key=project_dir.name,
                    name=(
                        _text(
                            _mapping(_mapping(project_value).get("character")).get(
                                "display_name"
                            )
                        )
                        or _text(_mapping(project_value).get("name"))
                        or project_dir.name
                    ),
                    path=project_dir,
                    metadata_path=project_path,
                    metadata_value=project_value,
                    metadata_error=project_error,
                    **project_context,
                )
            )
        for bank_path in _find_bank_metadata(project_dir):
            bank_value, bank_error = _read_json(bank_path)
            artifacts.append(
                _artifact(
                    root=root,
                    kind="expressive_reference_bank",
                    key=f"{project_dir.name}-{bank_path.stem}",
                    name=(
                        _text(_mapping(bank_value).get("name"))
                        or f"{project_dir.name} reference bank"
                    ),
                    path=bank_path,
                    metadata_path=bank_path,
                    metadata_value=bank_value,
                    metadata_error=bank_error,
                    aliases=[project_dir.name, bank_path.name],
                    **project_context,
                )
            )
        source_root = project_dir / "recordings" / "source"
        if source_root.is_dir():
            for recording in sorted(source_root.iterdir()):
                if recording.is_symlink() or not recording.is_file():
                    continue
                artifacts.append(
                    _artifact(
                        root=root,
                        kind="owned_recording",
                        key=f"{project_dir.name}-{recording.name}",
                        name=recording.name,
                        path=recording,
                        aliases=[project_dir.name, recording.name],
                        **project_context,
                    )
                )
    return artifacts


def _walk_text_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        text = value.strip()
        if text:
            yield text
        return
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _walk_text_values(item)
        return
    if isinstance(value, list):
        for item in value:
            yield from _walk_text_values(item)


def _reference_files(root: Path) -> list[tuple[Path, str]]:
    result: list[tuple[Path, str]] = []
    for relative in CURRENT_REFERENCE_PATHS:
        path = root / relative
        if path.is_file() and not path.is_symlink():
            result.append((path, "current"))
    for directory_name in CURRENT_REFERENCE_DIRECTORIES:
        directory = root / directory_name
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.json")):
            if path.is_file() and not path.is_symlink():
                result.append((path, "current"))
                if len(result) >= MAX_REFERENCE_FILES:
                    return result
    for directory_name in HISTORY_REFERENCE_DIRECTORIES:
        directory = root / directory_name
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.json")):
            if path.is_file() and not path.is_symlink():
                result.append((path, "history"))
                if len(result) >= MAX_REFERENCE_FILES:
                    return result
    return result


def _dependency_index(root: Path) -> list[dict[str, Any]]:
    references = []
    for path, scope in _reference_files(root):
        value, error = _read_json(path)
        if error or value is None:
            continue
        relative = _relative(root, path)
        character_id = None
        if isinstance(value, Mapping):
            character_id = _text(value.get("character_id"))
            if not character_id:
                character_id = _text(
                    _mapping(value.get("character")).get("id")
                )
        for text in _walk_text_values(value):
            references.append(
                {
                    "value": text.replace("\\", "/"),
                    "scope": scope,
                    "source_relative_path": relative,
                    "character_id": character_id,
                }
            )
    return references


def _dependency_lookup(
    references: list[dict[str, Any]],
) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    exact: dict[str, list[int]] = {}
    descendants: dict[str, list[int]] = {}
    for index, reference in enumerate(references):
        value = str(reference.get("value") or "").strip().replace("\\", "/")
        folded = value.casefold()
        if not folded:
            continue
        exact.setdefault(folded, []).append(index)
        for position, character in enumerate(folded):
            if character != "/":
                continue
            prefix = folded[:position]
            if "/" in prefix:
                descendants.setdefault(prefix, []).append(index)
    return exact, descendants


def _apply_dependencies(
    root: Path,
    artifacts: list[dict[str, Any]],
) -> None:
    references = _dependency_index(root)
    exact_references, descendant_references = _dependency_lookup(references)
    for artifact in artifacts:
        aliases = set(
            artifact["technical_details"].get("identity_aliases") or []
        )
        if artifact["kind"] in {
            "designed_voice",
            "clone_reference",
            "owned_recording",
            "preparer_output",
        }:
            aliases = {
                alias
                for alias in aliases
                if "/" in str(alias)
                or Path(str(alias)).suffix.casefold()
                in {
                    ".wav",
                    ".mp3",
                    ".flac",
                    ".ogg",
                    ".m4a",
                    ".zip",
                    ".json",
                }
            }
        relative_path = str(
            artifact["technical_details"].get("relative_path") or ""
        )
        usage = []
        seen = set()
        matching_indices: set[int] = set()
        for alias in aliases:
            normalized_alias = str(alias).strip().replace("\\", "/").casefold()
            if not normalized_alias:
                continue
            matching_indices.update(exact_references.get(normalized_alias, ()))
            if "/" in normalized_alias:
                matching_indices.update(
                    descendant_references.get(normalized_alias, ())
                )
        for index in sorted(matching_indices):
            reference = references[index]
            source_path = reference["source_relative_path"]
            if source_path == relative_path or source_path.startswith(
                relative_path.rstrip("/") + "/"
            ):
                continue
            key = (
                reference["scope"],
                source_path,
                reference.get("character_id"),
            )
            if key in seen:
                continue
            seen.add(key)
            scope = reference["scope"]
            usage.append(
                {
                    "scope": scope,
                    "source": source_path,
                    "character_id": reference.get("character_id"),
                    "blocking": True,
                    "native_destination": (
                        "cast"
                        if source_path == "voice_config.json"
                        or source_path.startswith("voice_training_projects/")
                        else "more"
                    ),
                    "target_id": (
                        f"cast:character:{reference['character_id']}"
                        if reference.get("character_id")
                        else "more:library-dependencies"
                    ),
                }
            )
        usage.sort(
            key=lambda item: (
                item["scope"] != "current",
                item["source"],
                item.get("character_id") or "",
            )
        )
        artifact["usage"] = usage
        artifact["dependency_count"] = len(usage)
        artifact["blocking_dependency_count"] = sum(
            item["blocking"] is True for item in usage
        )
        supported = artifact["delete"]["supported"]
        blocked = (
            not supported
            or artifact["state"] not in {"available", "empty"}
            or artifact["blocking_dependency_count"] > 0
        )
        artifact["delete"].update(
            {
                "blocked": blocked,
                "reason": (
                    "This artifact has no authoritative delete route."
                    if not supported
                    else "Repair the artifact before deletion."
                    if artifact["state"] not in {"available", "empty"}
                    else (
                        f"{artifact['blocking_dependency_count']} dependency"
                        + (
                            " blocks deletion."
                            if artifact["blocking_dependency_count"] == 1
                            else " dependencies block deletion."
                        )
                    )
                    if artifact["blocking_dependency_count"]
                    else None
                ),
            }
        )


def _apply_workflow_usage(root: Path, artifacts: list[dict[str, Any]]) -> None:
    for artifact in artifacts:
        additions = []
        if artifact["kind"] == "source_book" and (root / "annotated_script.json").is_file():
            additions.append(
                {
                    "scope": "current",
                    "source": "annotated_script.json",
                    "character_id": None,
                    "blocking": False,
                    "native_destination": "script",
                    "target_id": "script:source",
                }
            )
        elif artifact["kind"] == "production_audio":
            additions.append(
                {
                    "scope": "current",
                    "source": "chunks.json",
                    "character_id": None,
                    "blocking": False,
                    "native_destination": "produce",
                    "target_id": "produce:audio",
                }
            )
            if (root / "export_build.json").is_file():
                additions.append(
                    {
                        "scope": "current",
                        "source": "export_build.json",
                        "character_id": None,
                        "blocking": False,
                        "native_destination": "export",
                        "target_id": "export:build",
                    }
                )
        elif artifact["kind"] == "export_output":
            additions.append(
                {
                    "scope": "current",
                    "source": "export_build.json",
                    "character_id": None,
                    "blocking": False,
                    "native_destination": "export",
                    "target_id": "export:output",
                }
            )
        if not additions:
            continue
        existing = {
            (item.get("scope"), item.get("source"), item.get("target_id"))
            for item in artifact["usage"]
        }
        artifact["usage"].extend(
            item
            for item in additions
            if (item["scope"], item["source"], item["target_id"]) not in existing
        )
        artifact["usage"].sort(
            key=lambda item: (
                item["scope"] != "current",
                item["source"],
                item.get("target_id") or "",
            )
        )
        artifact["dependency_count"] = len(artifact["usage"])
        artifact["blocking_dependency_count"] = sum(
            item.get("blocking") is True for item in artifact["usage"]
        )


def _artifact_fingerprint(artifact: Mapping[str, Any]) -> str:
    return fingerprint_value(
        {
            "artifact_id": artifact.get("artifact_id"),
            "kind": artifact.get("kind"),
            "key": artifact.get("key"),
            "source_key": artifact.get("source_key"),
            "state": artifact.get("state"),
            "size_bytes": artifact.get("size_bytes"),
            "file_count": artifact.get("file_count"),
            "modified_ns": artifact.get("modified_ns"),
            "provenance": artifact.get("provenance"),
            "metadata_error": artifact.get("metadata_error"),
            "usage": artifact.get("usage"),
            "delete": artifact.get("delete"),
        }
    )


def inspect_library_inventory(
    *,
    root_dir: str | Path,
    kind: str | None = None,
    state: str | None = None,
    search: str | None = None,
    project_id: str | None = None,
    character_id: str | None = None,
    return_route: str | None = None,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    if kind is not None and kind not in ARTIFACT_KINDS:
        raise LibraryInventoryError(
            status_code=422,
            code="library_kind_invalid",
            detail="The requested Library artifact kind is invalid.",
            context={"kind": kind},
        )
    context = {
        "project_id": _text(project_id),
        "character_id": _text(character_id),
        "return_route": _text(return_route) or "#/library",
    }
    artifacts: list[dict[str, Any]] = []
    artifacts.extend(_scan_source_book(root, **context))
    artifacts.extend(_scan_production_audio(root, **context))
    artifacts.extend(_scan_export_outputs(root, **context))
    artifacts.extend(_scan_designed_voices(root, **context))
    artifacts.extend(_scan_clone_references(root, **context))
    artifacts.extend(_scan_preparer_outputs(root, **context))
    artifacts.extend(
        _scan_top_level_projects(
            root,
            directory_name="dataset_builder",
            kind="dataset_builder_project",
            metadata_candidates=("state.json",),
            **context,
        )
    )
    artifacts.extend(
        _scan_top_level_projects(
            root,
            directory_name="lora_datasets",
            kind="lora_dataset",
            metadata_candidates=(
                "metadata.jsonl",
                "metadata.json",
                "manifest.json",
                "state.json",
            ),
            **context,
        )
    )
    artifacts.extend(
        _scan_top_level_projects(
            root,
            directory_name="lora_models",
            kind="lora_adapter",
            metadata_candidates=(
                "training_meta.json",
                "mlx_export_manifest.json",
                "manifest.json",
            ),
            **context,
        )
    )
    artifacts.extend(_scan_voice_projects(root, **context))
    ids = [artifact["artifact_id"] for artifact in artifacts]
    if len(ids) != len(set(ids)):
        raise LibraryInventoryError(
            status_code=409,
            code="library_artifact_id_collision",
            detail="Library artifact identity collision detected.",
        )
    _apply_dependencies(root, artifacts)
    _apply_workflow_usage(root, artifacts)
    for artifact in artifacts:
        artifact["fingerprint"] = _artifact_fingerprint(artifact)
    artifacts.sort(
        key=lambda item: (
            item["kind"],
            _normalized(item["name"]),
            item["artifact_id"],
        )
    )
    all_artifacts = artifacts
    query = _normalized(search)
    visible = []
    for artifact in all_artifacts:
        if kind is not None and artifact["kind"] != kind:
            continue
        if state is not None and artifact["state"] != state:
            continue
        if query:
            searchable = " ".join(
                [
                    artifact["name"],
                    artifact["kind"],
                    artifact["key"],
                    str(artifact.get("character_id") or ""),
                    " ".join(
                        str(value)
                        for value in artifact["provenance"].values()
                    ),
                ]
            )
            if query not in _normalized(searchable):
                continue
        visible.append(artifact)
    kind_counts = {
        artifact_kind: sum(
            item["kind"] == artifact_kind for item in all_artifacts
        )
        for artifact_kind in sorted(ARTIFACT_KINDS)
    }
    state_values = sorted({item["state"] for item in all_artifacts})
    state_counts = {
        item_state: sum(
            item["state"] == item_state for item in all_artifacts
        )
        for item_state in state_values
    }
    inventory_fingerprint = fingerprint_value(
        [
            {
                "artifact_id": item["artifact_id"],
                "fingerprint": item["fingerprint"],
            }
            for item in all_artifacts
        ]
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "inventory_fingerprint": inventory_fingerprint,
        "summary": {
            "artifact_count": len(all_artifacts),
            "visible_count": len(visible),
            "total_size_bytes": sum(
                item["size_bytes"] for item in all_artifacts
            ),
            "referenced_count": sum(
                item["dependency_count"] > 0 for item in all_artifacts
            ),
            "deletable_count": sum(
                item["delete"]["supported"]
                and not item["delete"]["blocked"]
                for item in all_artifacts
            ),
            "invalid_count": sum(
                item["state"] == "invalid" for item in all_artifacts
            ),
        },
        "filters": {
            "kind": kind,
            "state": state,
            "search": search,
            "available_kinds": sorted(ARTIFACT_KINDS),
            "available_states": state_values,
            "kind_counts": kind_counts,
            "state_counts": state_counts,
        },
        "artifacts": visible,
        "context": {
            "project_id": context["project_id"],
            "character_id": context["character_id"],
            "return_route": context["return_route"],
        },
        "technical_details": {
            "project_path": str(root),
            "reference_file_limit": MAX_REFERENCE_FILES,
            "artifact_file_limit": MAX_ARTIFACT_FILES,
        },
    }


def get_library_artifact(
    inventory: Mapping[str, Any],
    artifact_id: str,
) -> dict[str, Any]:
    wanted = str(artifact_id or "").strip()
    for item in _list(inventory.get("artifacts")):
        if isinstance(item, Mapping) and item.get("artifact_id") == wanted:
            return copy.deepcopy(dict(item))
    raise LibraryInventoryError(
        status_code=404,
        code="library_artifact_not_found",
        detail="The requested Library artifact was not found.",
        context={"artifact_id": wanted},
    )


def build_library_delete_impact(
    *,
    inventory: Mapping[str, Any],
    artifact_id: str,
) -> dict[str, Any]:
    artifact = get_library_artifact(inventory, artifact_id)
    supported = artifact["delete"]["supported"] is True
    blocked = artifact["delete"]["blocked"] is True
    endpoint_template = artifact["delete"].get("endpoint_template")
    endpoint = (
        endpoint_template.format(
            voice_id=artifact["delete"]["route_key"],
            name=artifact["delete"]["route_key"],
            dataset_id=artifact["delete"]["route_key"],
            adapter_id=artifact["delete"]["route_key"],
        )
        if supported and endpoint_template
        else None
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_id": artifact["artifact_id"],
        "artifact_fingerprint": artifact["fingerprint"],
        "inventory_fingerprint": inventory.get("inventory_fingerprint"),
        "kind": artifact["kind"],
        "key": artifact["key"],
        "source_key": artifact.get("source_key"),
        "name": artifact["name"],
        "state": artifact["state"],
        "supported": supported,
        "blocked": blocked,
        "blockers": copy.deepcopy(artifact["usage"]),
        "reason": artifact["delete"].get("reason"),
        "confirm_name": artifact["name"],
        "delete_endpoint": endpoint,
        "delete_method": "DELETE" if endpoint else None,
        "safe_to_delete": supported and not blocked,
    }


def validate_library_delete_request(
    *,
    inventory: Mapping[str, Any],
    artifact_id: str,
    expected_inventory_fingerprint: str,
    expected_artifact_fingerprint: str,
    confirm_name: str,
) -> dict[str, Any]:
    if inventory.get("inventory_fingerprint") != expected_inventory_fingerprint:
        raise LibraryInventoryError(
            status_code=409,
            code="library_inventory_changed",
            detail="Library inventory changed after deletion was reviewed.",
            context={
                "current_inventory_fingerprint": inventory.get(
                    "inventory_fingerprint"
                )
            },
        )
    impact = build_library_delete_impact(
        inventory=inventory,
        artifact_id=artifact_id,
    )
    if impact["artifact_fingerprint"] != expected_artifact_fingerprint:
        raise LibraryInventoryError(
            status_code=409,
            code="library_artifact_changed",
            detail="The Library artifact changed after deletion was reviewed.",
            context={
                "current_artifact_fingerprint": impact[
                    "artifact_fingerprint"
                ]
            },
        )
    if confirm_name != impact["confirm_name"]:
        raise LibraryInventoryError(
            status_code=422,
            code="library_delete_confirmation_mismatch",
            detail="Type the exact Library artifact name to confirm deletion.",
        )
    if not impact["supported"]:
        raise LibraryInventoryError(
            status_code=409,
            code="library_delete_unsupported",
            detail=impact["reason"]
            or "This Library artifact has no authoritative delete route.",
        )
    if impact["blocked"]:
        raise LibraryInventoryError(
            status_code=409,
            code="library_delete_blocked",
            detail=impact["reason"]
            or "Library dependencies block deletion.",
            context={"blockers": impact["blockers"]},
        )
    return impact
