from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from utils import atomic_json_write


STAGE_LOG_SCHEMA_VERSION = 1
DEFAULT_MAX_ENTRIES = 1000
DEFAULT_STATUS_LIMIT = 200
_LOG_LOCK = threading.RLock()


class StageLogError(ValueError):
    """A persisted stage log could not satisfy the stage-log contract."""


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_stage(stage: str) -> str:
    if not isinstance(stage, str) or not stage.strip():
        raise StageLogError("Stage log name is required.")
    value = stage.strip()
    if not all(character.isalnum() or character in {"_", "-"} for character in value):
        raise StageLogError("Stage log name contains unsupported characters.")
    return value


def _empty_document(stage: str) -> dict[str, Any]:
    return {
        "schema_version": STAGE_LOG_SCHEMA_VERSION,
        "stage": stage,
        "entries": [],
        "updated_at": None,
    }


def _validate_document(document: Any, *, stage: str) -> dict[str, Any]:
    if not isinstance(document, Mapping):
        raise StageLogError("Stage log must contain a JSON object.")
    if document.get("schema_version") != STAGE_LOG_SCHEMA_VERSION:
        raise StageLogError("Stage log schema version is unsupported.")
    if document.get("stage") != stage:
        raise StageLogError("Stage log belongs to another stage.")
    raw_entries = document.get("entries")
    if not isinstance(raw_entries, list):
        raise StageLogError("Stage log entries must be a JSON array.")

    entries: list[dict[str, str]] = []
    for index, raw_entry in enumerate(raw_entries):
        if not isinstance(raw_entry, Mapping):
            raise StageLogError(f"Stage log entry {index + 1} must be an object.")
        message = raw_entry.get("message")
        timestamp = raw_entry.get("timestamp")
        level = raw_entry.get("level", "info")
        if not isinstance(message, str) or not message.strip():
            raise StageLogError(f"Stage log entry {index + 1} has no message.")
        if not isinstance(timestamp, str) or not timestamp.strip():
            raise StageLogError(f"Stage log entry {index + 1} has no timestamp.")
        if not isinstance(level, str) or not level.strip():
            raise StageLogError(f"Stage log entry {index + 1} has no level.")
        entries.append(
            {
                "timestamp": timestamp.strip(),
                "level": level.strip(),
                "message": message.strip(),
            }
        )

    return {
        "schema_version": STAGE_LOG_SCHEMA_VERSION,
        "stage": stage,
        "entries": entries,
        "updated_at": (
            document.get("updated_at")
            if isinstance(document.get("updated_at"), str)
            else (entries[-1]["timestamp"] if entries else None)
        ),
    }


def _read_document(path: str | Path, *, stage: str) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return _empty_document(stage)
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise StageLogError(f"Could not read stage log: {exc}") from exc
    return _validate_document(document, stage=stage)


def reset_stage_log(path: str | Path, *, stage: str) -> dict[str, Any]:
    stage_name = _safe_stage(stage)
    target = Path(path)
    with _LOG_LOCK:
        target.parent.mkdir(parents=True, exist_ok=True)
        document = _empty_document(stage_name)
        atomic_json_write(document, str(target))
        return document


def append_stage_log(
    path: str | Path,
    *,
    stage: str,
    message: str,
    level: str = "info",
    timestamp: str | None = None,
    max_entries: int = DEFAULT_MAX_ENTRIES,
) -> dict[str, Any]:
    stage_name = _safe_stage(stage)
    if not isinstance(message, str) or not message.strip():
        raise StageLogError("Stage log message is required.")
    if not isinstance(level, str) or not level.strip():
        raise StageLogError("Stage log level is required.")
    if max_entries < 1:
        raise StageLogError("Stage log entry limit must be positive.")

    target = Path(path)
    with _LOG_LOCK:
        target.parent.mkdir(parents=True, exist_ok=True)
        document = _read_document(target, stage=stage_name)
        entry = {
            "timestamp": timestamp or _timestamp(),
            "level": level.strip(),
            "message": message.strip(),
        }
        entries = list(document["entries"])
        entries.append(entry)
        if len(entries) > max_entries:
            entries = entries[-max_entries:]
        updated = {
            "schema_version": STAGE_LOG_SCHEMA_VERSION,
            "stage": stage_name,
            "entries": entries,
            "updated_at": entry["timestamp"],
        }
        atomic_json_write(updated, str(target))
        return updated


def read_stage_log(
    path: str | Path,
    *,
    stage: str,
    limit: int = DEFAULT_STATUS_LIMIT,
) -> dict[str, Any]:
    stage_name = _safe_stage(stage)
    if limit < 1:
        raise StageLogError("Stage log status limit must be positive.")
    target = Path(path)
    with _LOG_LOCK:
        exists = target.exists()
        try:
            document = _read_document(target, stage=stage_name)
        except StageLogError as exc:
            return {
                "exists": exists,
                "stage": stage_name,
                "entries": [],
                "lines": [],
                "line_count": 0,
                "truncated": False,
                "updated_at": None,
                "error": str(exc),
            }

    entries = list(document["entries"])
    visible = entries[-limit:]
    return {
        "exists": exists,
        "stage": stage_name,
        "entries": visible,
        "lines": [entry["message"] for entry in visible],
        "line_count": len(entries),
        "truncated": len(entries) > limit,
        "updated_at": document.get("updated_at"),
        "error": None,
    }
