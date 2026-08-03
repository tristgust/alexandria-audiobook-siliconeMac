from __future__ import annotations

import copy
import inspect
import json
import os
import secrets
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from audio_artifacts import (
    AudioArtifactError,
    sha256_file,
    validate_audio_file,
)
from chapter_assembly import (
    CHAPTER_MODES,
    ChapterAssemblyError,
    build_chapters,
)
from export_publication import (
    export_cover_status,
    materialized_export_cover,
    resolve_export_cover,
    resolve_publication_metadata,
)
from generation_state import atomic_json_write, fingerprint_value


SCHEMA_VERSION = 1
SUPPORTED_FORMATS = frozenset({"mp3", "m4b", "audacity"})
KNOWN_FORMATS = frozenset(
    {"mp3", "m4b", "audacity", "chapter_separated"}
)
OUTPUT_FILENAMES = {
    "mp3": "cloned_audiobook.mp3",
    "m4b": "audiobook.m4b",
    "audacity": "audacity_export.zip",
}
RECEIPT_FILENAME = "export_build.json"
HISTORY_DIRNAME = "export_build_history"


class ExportAggregateError(RuntimeError):
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


class _ExportBuildCancelled(RuntimeError):
    pass


class _ExportBuildStale(RuntimeError):
    pass


def utc_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise ExportAggregateError(
            status_code=409,
            code="export_artifact_invalid",
            detail=f"{path.name} is not a safe regular file.",
            context={"filename": path.name},
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ExportAggregateError(
            status_code=409,
            code="export_artifact_invalid_json",
            detail=f"{path.name} is invalid JSON: {exc}",
            context={"filename": path.name},
        ) from exc


def _normalized_metadata(value: Mapping[str, Any] | None) -> dict[str, str]:
    source = _mapping(value)
    return {
        "title": str(source.get("title") or "").strip(),
        "author": str(source.get("author") or "").strip(),
        "narrator": str(source.get("narrator") or "").strip(),
        "year": str(source.get("year") or "").strip(),
        "description": str(source.get("description") or "").strip(),
    }


def _normalized_formats(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        item = str(value or "").strip().casefold()
        if item and item not in result:
            result.append(item)
    return result


def _blocker(
    *,
    code: str,
    title: str,
    explanation: str,
    target_id: str,
    native_destination: str = "export",
) -> dict[str, Any]:
    return {
        "code": code,
        "title": title,
        "explanation": explanation,
        "native_destination": native_destination,
        "target_id": target_id,
        "blocking": True,
    }


def build_export_chapters(
    chunks: list[Mapping[str, Any]],
    *,
    config: Mapping[str, Any],
    mode: str,
) -> list[dict[str, Any]]:
    try:
        return build_chapters(
            chunks,
            config=config,
            mode=mode,
        )
    except ChapterAssemblyError as exc:
        raise ExportAggregateError(
            status_code=422,
            code="export_chapter_mode_invalid",
            detail=str(exc),
            context=exc.context or {"chapter_mode": mode},
        ) from exc


def _output_record(
    *,
    root: Path,
    format_name: str,
    receipt: Mapping[str, Any] | None,
    dependency_fingerprint: str | None,
    file_hasher: Callable[[str | Path], str],
) -> dict[str, Any]:
    filename = OUTPUT_FILENAMES[format_name]
    path = root / filename
    receipt_outputs = _mapping(_mapping(receipt).get("outputs"))
    recorded = _mapping(receipt_outputs.get(format_name))
    exists = path.is_file()
    state = "missing"
    actual_hash = None
    actual_size = None
    if exists:
        try:
            actual_size = path.stat().st_size
            actual_hash = file_hasher(path)
        except (OSError, AudioArtifactError):
            state = "invalid"
        else:
            recorded_hash = _text(recorded.get("sha256"))
            recorded_size = recorded.get("size_bytes")
            if not recorded:
                state = "legacy_unverified"
            elif recorded_hash != actual_hash or recorded_size != actual_size:
                state = "invalid"
            elif _text(_mapping(receipt).get("dependency_fingerprint")) != dependency_fingerprint:
                state = "stale"
            else:
                state = "current"
    return {
        "format": format_name,
        "filename": filename,
        "state": state,
        "exists": exists,
        "download_url": {
            "mp3": "/api/audiobook",
            "m4b": "/api/audiobook_m4b",
            "audacity": "/api/export_audacity",
        }[format_name]
        if exists
        else None,
        "playback_url": (
            "/api/audiobook"
            if format_name == "mp3" and state == "current"
            else "/api/audiobook_m4b"
            if format_name == "m4b" and state == "current"
            else None
        ),
        "sha256": actual_hash,
        "size_bytes": actual_size,
        "duration_ms": recorded.get("duration_ms"),
        "built_at_utc": recorded.get("built_at_utc"),
        "technical_details": {
            "relative_path": filename,
            "recorded_sha256": recorded.get("sha256"),
            "recorded_size_bytes": recorded.get("size_bytes"),
        },
    }


def build_export_plan(
    *,
    produce: Mapping[str, Any],
    metadata: Mapping[str, Any] | None,
    formats: Iterable[Any],
    chapter_mode: str,
    config: Mapping[str, Any] | None = None,
    cover_sha256: str | None = None,
) -> dict[str, Any]:
    metadata_value = _normalized_metadata(metadata)
    format_values = _normalized_formats(formats)
    if not format_values:
        format_values = ["mp3"]
    unknown = [item for item in format_values if item not in KNOWN_FORMATS]
    unavailable = [item for item in format_values if item not in SUPPORTED_FORMATS]
    chunks = [
        item
        for item in _list(produce.get("chunks"))
        if isinstance(item, Mapping)
    ]
    chapters = build_export_chapters(
        chunks,
        config=_mapping(config),
        mode=chapter_mode,
    )
    blockers: list[dict[str, Any]] = []
    if _mapping(produce.get("summary")).get("complete") is not True:
        blockers.append(
            _blocker(
                code="export_produce_incomplete",
                title="Produce is incomplete",
                explanation="Finish or repair every required audio chunk before Export.",
                native_destination="produce",
                target_id="produce:blockers",
            )
        )
    for field in ("title", "author"):
        if not metadata_value[field]:
            blockers.append(
                _blocker(
                    code="export_metadata_missing",
                    title=f"{field.title()} is required",
                    explanation=f"Enter the audiobook {field} before building outputs.",
                    target_id=f"export:metadata:{field}",
                )
            )
    if unknown:
        blockers.append(
            _blocker(
                code="export_format_unknown",
                title="Unknown export format",
                explanation="Remove unsupported format identifiers from the build plan.",
                target_id="export:formats",
            )
        )
    if unavailable:
        blockers.append(
            _blocker(
                code="export_format_unavailable",
                title="Selected export format is unavailable",
                explanation=(
                    "The current backend does not implement: "
                    + ", ".join(unavailable)
                    + "."
                ),
                target_id="export:formats",
            )
        )
    if "m4b" in format_values and not chapters:
        blockers.append(
            _blocker(
                code="export_chapters_required",
                title="M4B chapters are unavailable",
                explanation="Choose Smart or Per chunk chapters before building M4B.",
                target_id="export:chapters",
            )
        )
    mastering = copy.deepcopy(dict(_mapping(produce.get("mastering"))))
    dependency = {
        "produce": dict(_mapping(produce.get("fingerprints"))),
        "mastering": mastering,
        "metadata": metadata_value,
        "formats": format_values,
        "chapter_mode": chapter_mode,
        "chapters": chapters,
        "cover_sha256": cover_sha256,
    }
    dependency_fingerprint = fingerprint_value(dependency)
    plan_seed = {
        "schema_version": SCHEMA_VERSION,
        "dependency_fingerprint": dependency_fingerprint,
        "outputs": format_values,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "metadata": metadata_value,
        "formats": format_values,
        "chapter_mode": chapter_mode,
        "chapters": chapters,
        "mastering": mastering,
        "cover_sha256": cover_sha256,
        "dependency_fingerprint": dependency_fingerprint,
        "plan_fingerprint": fingerprint_value(plan_seed),
        "blockers": blockers,
        "safe_to_execute": not blockers,
        "output_filenames": {
            item: OUTPUT_FILENAMES[item]
            for item in format_values
            if item in SUPPORTED_FORMATS
        },
    }


def inspect_export_project(
    *,
    root_dir: str | Path,
    produce: Mapping[str, Any],
    process: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
    file_hasher: Callable[[str | Path], str] = sha256_file,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    receipt = _read_json(root / RECEIPT_FILENAME)
    if receipt is not None and not isinstance(receipt, Mapping):
        raise ExportAggregateError(
            status_code=409,
            code="export_receipt_invalid",
            detail="export_build.json must contain a JSON object.",
        )
    metadata = resolve_publication_metadata(
        root_dir=root,
        receipt_metadata=_mapping(_mapping(receipt).get("metadata")),
        config=config,
    )
    formats = _list(_mapping(receipt).get("formats")) or ["mp3"]
    chapter_mode = _text(_mapping(receipt).get("chapter_mode")) or "smart"
    cover = resolve_export_cover(root)
    cover_sha256 = cover.sha256 if cover else None
    plan = build_export_plan(
        produce=produce,
        metadata=metadata,
        formats=formats,
        chapter_mode=chapter_mode,
        config=config,
        cover_sha256=cover_sha256,
    )
    dependency_fingerprint = plan["dependency_fingerprint"]
    outputs = {
        name: _output_record(
            root=root,
            format_name=name,
            receipt=receipt,
            dependency_fingerprint=dependency_fingerprint,
            file_hasher=file_hasher,
        )
        for name in sorted(SUPPORTED_FORMATS)
    }
    selected_outputs = [
        outputs[name] for name in plan["formats"] if name in outputs
    ]
    current_selected = bool(selected_outputs) and all(
        item["state"] == "current" for item in selected_outputs
    )
    process_value = dict(process or {})
    if process_value.get("running"):
        state = "running"
    elif plan["blockers"]:
        state = "blocked"
    elif current_selected:
        state = "complete"
    elif any(item["state"] == "invalid" for item in selected_outputs):
        state = "failed"
    elif any(item["state"] in {"stale", "legacy_unverified"} for item in selected_outputs):
        state = "stale"
    else:
        state = "ready"
    primary_action = (
        {
            "id": "cancel_export_build",
            "label": "Cancel build",
            "endpoint": "/api/export/cancel",
            "method": "POST",
        }
        if process_value.get("running")
        else {
            "id": "build_export",
            "label": "Build audiobook",
            "endpoint": "/api/export/build",
            "method": "POST",
        }
        if not plan["blockers"] and not current_selected
        else None
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "state": state,
        "metadata": plan["metadata"],
        "formats": plan["formats"],
        "chapter_mode": plan["chapter_mode"],
        "chapters": plan["chapters"],
        "cover": export_cover_status(cover),
        "outputs": outputs,
        "selected_outputs": selected_outputs,
        "summary": {
            "selected_format_count": len(selected_outputs),
            "current_output_count": sum(
                item["state"] == "current" for item in selected_outputs
            ),
            "chapter_count": len(plan["chapters"]),
            "blocker_count": len(plan["blockers"]),
            "complete": state == "complete",
        },
        "blockers": plan["blockers"],
        "process": process_value,
        "primary_action": primary_action,
        "plan": plan,
        "receipt": copy.deepcopy(dict(receipt)) if isinstance(receipt, Mapping) else None,
        "player": next(
            (
                {
                    "format": item["format"],
                    "url": item["playback_url"],
                    "duration_ms": item["duration_ms"],
                }
                for item in selected_outputs
                if item["playback_url"]
            ),
            None,
        ),
        "fingerprints": {
            "dependencies": dependency_fingerprint,
            "plan": plan["plan_fingerprint"],
            "receipt": fingerprint_value(dict(receipt))
            if isinstance(receipt, Mapping)
            else None,
        },
        "technical_details": {
            "project_path": str(root),
            "receipt_relative_path": RECEIPT_FILENAME,
        },
    }


def _validate_audacity(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ExportAggregateError(
            status_code=409,
            code="export_output_invalid",
            detail="Audacity export is missing or empty.",
        )
    try:
        with zipfile.ZipFile(path, "r") as archive:
            corrupt = archive.testzip()
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        raise ExportAggregateError(
            status_code=409,
            code="export_output_invalid",
            detail=f"Audacity export is invalid: {exc}",
        ) from exc
    if corrupt is not None or not {"project.lof", "labels.txt"}.issubset(names):
        raise ExportAggregateError(
            status_code=409,
            code="export_output_invalid",
            detail="Audacity export failed archive validation.",
        )
    return {
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "duration_ms": None,
    }


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _restore_previous(
    root: Path,
    previous: Mapping[str, Mapping[str, Any]],
    *,
    pending_directory: Path | None = None,
) -> None:
    for format_name, record in previous.items():
        target = root / OUTPUT_FILENAMES[format_name]
        if record.get("exists"):
            backup = root / str(record["backup_relative_path"])
            if not backup.is_file() and pending_directory is not None:
                backup = (
                    pending_directory
                    / "previous"
                    / OUTPUT_FILENAMES[format_name]
                )
            _atomic_copy(backup, target)
        else:
            try:
                target.unlink()
            except FileNotFoundError:
                pass


def execute_export_build(
    *,
    root_dir: str | Path,
    project_manager: Any,
    plan: Mapping[str, Any],
    cancel_check: Callable[[], bool] | None = None,
    publication_check: Callable[[], None] | None = None,
    publication_gate: Callable[
        [Callable[[], None], dict[str, Any]], str | None
    ]
    | None = None,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    audio_validator: Callable[..., Mapping[str, Any]] = validate_audio_file,
    commit_replace: Callable[[str | Path, str | Path], Any] = os.replace,
    at_utc: str | None = None,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    if plan.get("safe_to_execute") is not True:
        raise ExportAggregateError(
            status_code=409,
            code="export_plan_blocked",
            detail="Resolve Export blockers before building outputs.",
            context={"blockers": copy.deepcopy(plan.get("blockers") or [])},
        )
    formats = [str(item) for item in _list(plan.get("formats"))]
    if any(item not in SUPPORTED_FORMATS for item in formats):
        raise ExportAggregateError(
            status_code=422,
            code="export_format_unavailable",
            detail="The build plan contains an unavailable format.",
        )
    cover = resolve_export_cover(root)
    cover_sha256 = cover.sha256 if cover else None
    if cover_sha256 != plan.get("cover_sha256"):
        raise ExportAggregateError(
            status_code=409,
            code="export_dependencies_changed",
            detail="The publication cover changed after this export plan was prepared.",
            context={
                "planned_cover_sha256": plan.get("cover_sha256"),
                "current_cover_sha256": cover_sha256,
            },
        )
    build_id = "export_" + secrets.token_hex(12)
    pending = root / HISTORY_DIRNAME / f".{build_id}.pending"
    final_history = root / HISTORY_DIRNAME / build_id
    pending.mkdir(parents=True, exist_ok=False)
    built_dir = pending / "built"
    previous_dir = pending / "previous"
    built_dir.mkdir()
    previous_dir.mkdir()
    now = at_utc or utc_timestamp()
    built_records: dict[str, dict[str, Any]] = {}
    previous_records: dict[str, dict[str, Any]] = {}
    receipt_path = root / RECEIPT_FILENAME
    previous_receipt = receipt_path.read_bytes() if receipt_path.is_file() else None

    def canceled() -> bool:
        return bool(cancel_check and cancel_check())

    def ensure_publishable(*, check_cancel: bool = True) -> None:
        if check_cancel and canceled():
            raise _ExportBuildCancelled()
        if publication_check is not None:
            publication_check()

    def progress(**fields: Any) -> None:
        if progress_callback:
            progress_callback(fields)

    progress(
        phase="preparing_export",
        phase_label="Preparing Export",
        completed_count=0,
        total_count=len(formats),
        overall_percent=2,
        progress_message="Creating a protected Export transaction.",
    )

    try:
        for format_name in formats:
            if canceled():
                return {
                    "status": "cancelled",
                    "build_id": build_id,
                    "committed": False,
                }
            target = built_dir / OUTPUT_FILENAMES[format_name]
            if format_name == "mp3":
                success, message = project_manager.merge_audio(
                    output_path=target
                )
            elif format_name == "m4b":
                with materialized_export_cover(cover, directory=pending) as cover_path:
                    metadata = {
                        **dict(_mapping(plan.get("metadata"))),
                        "cover_path": str(cover_path) if cover_path else "",
                    }
                    merge_m4b = project_manager.merge_m4b
                    merge_kwargs = {
                        "per_chunk_chapters": (
                            plan.get("chapter_mode") == "per_chunk"
                        ),
                        "metadata": metadata,
                        "output_path": target,
                    }
                    try:
                        parameters = inspect.signature(merge_m4b).parameters
                    except (TypeError, ValueError):
                        parameters = {}
                    if "cancel_check" in parameters:
                        merge_kwargs["cancel_check"] = cancel_check
                    if "progress_callback" in parameters:
                        merge_kwargs["progress_callback"] = progress_callback
                    success, message = merge_m4b(**merge_kwargs)
            else:
                success, message = project_manager.export_audacity(
                    output_path=target
                )
            if canceled():
                return {
                    "status": "cancelled",
                    "build_id": build_id,
                    "committed": False,
                }
            if not success:
                raise ExportAggregateError(
                    status_code=409,
                    code="export_build_failed",
                    detail=str(message),
                    context={"format": format_name},
                )
            progress(
                phase="validating_output",
                phase_label="Validating finished audiobook",
                completed_count=0,
                total_count=1,
                overall_percent=97,
                progress_message="Verifying the generated output before commit.",
            )
            if format_name in {"mp3", "m4b"}:
                try:
                    validation = dict(
                        audio_validator(
                            target,
                            format_hint=(
                                "mp3" if format_name == "mp3" else "mp4"
                            ),
                        )
                    )
                except (AudioArtifactError, OSError) as exc:
                    raise ExportAggregateError(
                        status_code=409,
                        code="export_output_invalid",
                        detail=str(exc),
                        context={"format": format_name},
                    ) from exc
            else:
                validation = _validate_audacity(target)
            built_records[format_name] = {
                "format": format_name,
                "filename": OUTPUT_FILENAMES[format_name],
                "sha256": validation["sha256"],
                "size_bytes": validation["size_bytes"],
                "duration_ms": validation.get("duration_ms"),
                "built_at_utc": now,
            }

        ensure_publishable()

        for format_name in formats:
            canonical = root / OUTPUT_FILENAMES[format_name]
            if canonical.is_file():
                backup = previous_dir / canonical.name
                _atomic_copy(canonical, backup)
                previous_records[format_name] = {
                    "exists": True,
                    "sha256": sha256_file(canonical),
                    "size_bytes": canonical.stat().st_size,
                    "backup_relative_path": (
                        final_history
                        / "previous"
                        / canonical.name
                    ).relative_to(root).as_posix(),
                }
            else:
                previous_records[format_name] = {
                    "exists": False,
                    "sha256": None,
                    "size_bytes": None,
                    "backup_relative_path": None,
                }

        progress(
            phase="committing_export",
            phase_label="Saving verified output",
            completed_count=0,
            total_count=len(formats),
            overall_percent=99,
            progress_message="Committing the verified audiobook atomically.",
        )
        receipt: dict[str, Any] = {}
        publication_result = {
            "status": "complete",
            "build_id": build_id,
            "receipt_fingerprint": None,
        }

        def commit_transaction(*, scheduler_joined: bool = False) -> None:
            nonlocal receipt
            committed: list[str] = []
            try:
                for format_name in formats:
                    ensure_publishable(check_cancel=not scheduler_joined)
                    source = built_dir / OUTPUT_FILENAMES[format_name]
                    canonical = root / OUTPUT_FILENAMES[format_name]
                    commit_replace(source, canonical)
                    committed.append(format_name)
                    if sha256_file(canonical) != built_records[format_name]["sha256"]:
                        raise ExportAggregateError(
                            status_code=409,
                            code="export_commit_hash_mismatch",
                            detail=(
                                f"Committed {format_name} output changed during "
                                "replacement."
                            ),
                        )
                ensure_publishable(check_cancel=not scheduler_joined)
                receipt = {
                    "schema_version": SCHEMA_VERSION,
                    "status": "complete",
                    "build_id": build_id,
                    "built_at_utc": now,
                    "dependency_fingerprint": plan["dependency_fingerprint"],
                    "plan_fingerprint": plan["plan_fingerprint"],
                    "metadata": copy.deepcopy(dict(_mapping(plan.get("metadata")))),
                    "formats": formats,
                    "chapter_mode": plan.get("chapter_mode"),
                    "chapters": copy.deepcopy(_list(plan.get("chapters"))),
                    "cover_sha256": plan.get("cover_sha256"),
                    "outputs": copy.deepcopy(built_records),
                    "previous_outputs": copy.deepcopy(previous_records),
                }
                publication_result["receipt_fingerprint"] = fingerprint_value(receipt)
                atomic_json_write(receipt, receipt_path)
                atomic_json_write(receipt, pending / "receipt.json")
                os.replace(pending, final_history)
            except Exception:
                _restore_previous(
                    root,
                    previous_records,
                    pending_directory=pending,
                )
                if previous_receipt is None:
                    try:
                        receipt_path.unlink()
                    except FileNotFoundError:
                        pass
                else:
                    handle, temp_name = tempfile.mkstemp(
                        prefix=".export-receipt.",
                        suffix=".tmp",
                        dir=root,
                    )
                    os.close(handle)
                    temporary = Path(temp_name)
                    try:
                        temporary.write_bytes(previous_receipt)
                        os.replace(temporary, receipt_path)
                    finally:
                        try:
                            temporary.unlink()
                        except FileNotFoundError:
                            pass
                raise

        if publication_gate is None:
            commit_transaction()
        else:
            publication_state = publication_gate(
                lambda: commit_transaction(scheduler_joined=True),
                publication_result,
            )
            if publication_state == "cancelled":
                raise _ExportBuildCancelled()
            if publication_state == "stale":
                raise _ExportBuildStale()
            if publication_state != "succeeded":
                raise ExportAggregateError(
                    status_code=409,
                    code="export_publication_not_authorized",
                    detail="The scheduler did not authorize Export publication.",
                    context={"scheduler_state": publication_state},
                )
        progress(
            phase="complete",
            phase_label="Audiobook ready",
            completed_count=len(formats),
            total_count=len(formats),
            overall_percent=100,
            progress_message="The verified audiobook is ready.",
        )
        return {
            "status": "complete",
            "build_id": build_id,
            "committed": True,
            "receipt": receipt,
        }
    except _ExportBuildCancelled:
        return {
            "status": "cancelled",
            "build_id": build_id,
            "committed": False,
        }
    except _ExportBuildStale:
        return {
            "status": "stale",
            "build_id": build_id,
            "committed": False,
        }
    finally:
        if pending.exists():
            shutil.rmtree(pending, ignore_errors=True)
