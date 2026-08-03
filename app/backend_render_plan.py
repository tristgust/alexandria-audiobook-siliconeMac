from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from dialogue_continuity import (
    effective_delivery_instruction,
    resolve_spoken_continuity,
)
from fish_inline_cues import FishInlineCueError, text_sha256, validate_plan
from generation_state import fingerprint_value
from utils import atomic_json_write


SCHEMA_VERSION = 1
PLAN_FILENAME = "backend_render_plan.json"
MAX_INSTRUCTION_LENGTH = 1200
MAX_FISH_DIRECTION_LENGTH = 600
MAX_CUES_PER_CHUNK = 8
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class BackendRenderPlanError(ValueError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = copy.deepcopy(details or {})


def _text(value: Any, label: str, *, allow_empty: bool = False, limit: int | None = None) -> str:
    if not isinstance(value, str):
        raise BackendRenderPlanError(
            "backend_render_plan_text_invalid",
            f"{label} must be text.",
        )
    normalized = " ".join(value.strip().split())
    if not normalized and not allow_empty:
        raise BackendRenderPlanError(
            "backend_render_plan_text_required",
            f"{label} must not be empty.",
        )
    if limit is not None and len(normalized) > limit:
        raise BackendRenderPlanError(
            "backend_render_plan_text_too_long",
            f"{label} must be {limit} characters or fewer.",
        )
    return normalized


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise BackendRenderPlanError(
            "backend_render_plan_hash_invalid",
            f"{label} must be a lowercase SHA-256 fingerprint.",
        )
    return value


def _warnings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise BackendRenderPlanError(
            "backend_render_plan_warnings_invalid",
            f"{label} must be an array.",
        )
    normalized: list[str] = []
    for index, item in enumerate(value):
        normalized.append(
            _text(
                item,
                f"{label}[{index}]",
                limit=500,
            )
        )
    return normalized


def _normalize_cue_shape(value: Any, *, entry_index: int, cue_index: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BackendRenderPlanError(
            "backend_render_plan_cue_invalid",
            f"Render entry {entry_index} Fish cue {cue_index + 1} must be an object.",
        )
    allowed = {"anchor", "tag", "kind", "phrase", "occurrence"}
    extra = set(value) - allowed
    if extra:
        raise BackendRenderPlanError(
            "backend_render_plan_cue_fields_invalid",
            f"Render entry {entry_index} Fish cue {cue_index + 1} has unsupported fields: {sorted(extra)}.",
        )
    anchor = _text(value.get("anchor"), "Fish cue anchor")
    if anchor not in {"start", "before_phrase", "after_phrase", "end"}:
        raise BackendRenderPlanError(
            "backend_render_plan_cue_anchor_invalid",
            f"Render entry {entry_index} Fish cue {cue_index + 1} has unsupported anchor {anchor!r}.",
        )
    kind = _text(value.get("kind", "delivery"), "Fish cue kind")
    if kind not in {"delivery", "reaction", "reset"}:
        raise BackendRenderPlanError(
            "backend_render_plan_cue_kind_invalid",
            f"Render entry {entry_index} Fish cue {cue_index + 1} has unsupported kind {kind!r}.",
        )
    tag = _text(value.get("tag"), "Fish cue tag", limit=180)
    normalized: dict[str, Any] = {
        "anchor": anchor,
        "tag": tag,
        "kind": kind,
    }
    if anchor in {"before_phrase", "after_phrase"}:
        phrase = value.get("phrase")
        if not isinstance(phrase, str) or not phrase:
            raise BackendRenderPlanError(
                "backend_render_plan_cue_phrase_required",
                f"Render entry {entry_index} Fish cue {cue_index + 1} requires an exact phrase.",
            )
        occurrence = value.get("occurrence", 1)
        if not isinstance(occurrence, int) or isinstance(occurrence, bool) or occurrence < 1:
            raise BackendRenderPlanError(
                "backend_render_plan_cue_occurrence_invalid",
                f"Render entry {entry_index} Fish cue {cue_index + 1} occurrence must be a positive integer.",
            )
        normalized["phrase"] = phrase
        normalized["occurrence"] = occurrence
    elif value.get("phrase") not in (None, "") or "occurrence" in value:
        raise BackendRenderPlanError(
            "backend_render_plan_cue_phrase_unexpected",
            f"Render entry {entry_index} Fish cue {cue_index + 1} cannot use a phrase with anchor {anchor!r}.",
        )
    return normalized


def normalize_backend_render_plan(
    value: Any,
    *,
    chunks: Sequence[Mapping[str, Any]] | None = None,
    expected_script_fingerprint: str | None = None,
    expected_chunks_fingerprint: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BackendRenderPlanError(
            "backend_render_plan_invalid",
            "Backend render plan result must be an object.",
        )
    expected_keys = {
        "schema_version",
        "script_fingerprint",
        "chunks_fingerprint",
        "entries",
        "warnings",
    }
    if set(value) != expected_keys:
        raise BackendRenderPlanError(
            "backend_render_plan_fields_invalid",
            "Backend render plan has invalid fields.",
            details={
                "missing": sorted(expected_keys - set(value)),
                "unexpected": sorted(set(value) - expected_keys),
            },
        )
    if value.get("schema_version") != SCHEMA_VERSION:
        raise BackendRenderPlanError(
            "backend_render_plan_schema_unsupported",
            f"Backend render plan schema must be {SCHEMA_VERSION}.",
        )
    script_fingerprint = _sha256(
        value.get("script_fingerprint"),
        "script_fingerprint",
    )
    chunks_fingerprint = _sha256(
        value.get("chunks_fingerprint"),
        "chunks_fingerprint",
    )
    if expected_script_fingerprint and script_fingerprint != expected_script_fingerprint:
        raise BackendRenderPlanError(
            "backend_render_plan_script_changed",
            "The render plan does not match the accepted Script fingerprint.",
        )
    if expected_chunks_fingerprint and chunks_fingerprint != expected_chunks_fingerprint:
        raise BackendRenderPlanError(
            "backend_render_plan_chunks_changed",
            "The render plan does not match the synthesis chunks fingerprint.",
        )
    raw_entries = value.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise BackendRenderPlanError(
            "backend_render_plan_entries_required",
            "Backend render plan requires at least one chunk entry.",
        )

    normalized_entries: list[dict[str, Any]] = []
    seen_indices: set[int] = set()
    for position, raw_entry in enumerate(raw_entries):
        if not isinstance(raw_entry, Mapping):
            raise BackendRenderPlanError(
                "backend_render_plan_entry_invalid",
                f"Render entry {position} must be an object.",
            )
        entry_keys = {
            "index",
            "chunk_id",
            "speaker",
            "text_sha256",
            "qwen_instruction",
            "fish_direction",
            "fish_cues",
            "warnings",
        }
        if set(raw_entry) != entry_keys:
            raise BackendRenderPlanError(
                "backend_render_plan_entry_fields_invalid",
                f"Render entry {position} has invalid fields.",
                details={
                    "missing": sorted(entry_keys - set(raw_entry)),
                    "unexpected": sorted(set(raw_entry) - entry_keys),
                },
            )
        index = raw_entry.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise BackendRenderPlanError(
                "backend_render_plan_index_invalid",
                f"Render entry {position} index must be a non-negative integer.",
            )
        if index in seen_indices:
            raise BackendRenderPlanError(
                "backend_render_plan_index_duplicate",
                f"Render entry index {index} appears more than once.",
            )
        seen_indices.add(index)
        raw_cues = raw_entry.get("fish_cues")
        if not isinstance(raw_cues, list) or len(raw_cues) > MAX_CUES_PER_CHUNK:
            raise BackendRenderPlanError(
                "backend_render_plan_cues_invalid",
                f"Render entry {index} fish_cues must contain at most {MAX_CUES_PER_CHUNK} cues.",
            )
        normalized_entries.append(
            {
                "index": index,
                "chunk_id": _text(raw_entry.get("chunk_id"), f"Render entry {index} chunk_id"),
                "speaker": _text(raw_entry.get("speaker"), f"Render entry {index} speaker"),
                "text_sha256": _sha256(
                    raw_entry.get("text_sha256"),
                    f"Render entry {index} text_sha256",
                ),
                "qwen_instruction": _text(
                    raw_entry.get("qwen_instruction"),
                    f"Render entry {index} qwen_instruction",
                    limit=MAX_INSTRUCTION_LENGTH,
                ),
                "fish_direction": _text(
                    raw_entry.get("fish_direction"),
                    f"Render entry {index} fish_direction",
                    limit=MAX_FISH_DIRECTION_LENGTH,
                ),
                "fish_cues": [
                    _normalize_cue_shape(cue, entry_index=index, cue_index=cue_index)
                    for cue_index, cue in enumerate(raw_cues)
                ],
                "warnings": _warnings(
                    raw_entry.get("warnings"),
                    f"Render entry {index} warnings",
                ),
            }
        )

    if chunks is not None:
        expected: dict[int, Mapping[str, Any]] = {
            index: chunk
            for index, chunk in enumerate(chunks)
            if str(chunk.get("text") or "").strip()
        }
        if set(seen_indices) != set(expected):
            raise BackendRenderPlanError(
                "backend_render_plan_coverage_invalid",
                "Backend render plan must cover every non-empty synthesis chunk exactly once.",
                details={
                    "missing_indices": sorted(set(expected) - seen_indices)[:100],
                    "unexpected_indices": sorted(seen_indices - set(expected))[:100],
                },
            )
        for entry in normalized_entries:
            chunk = expected[entry["index"]]
            expected_chunk_id = f"chunk:{chunk.get('id', entry['index'])}"
            if entry["chunk_id"] != expected_chunk_id:
                raise BackendRenderPlanError(
                    "backend_render_plan_chunk_id_changed",
                    f"Render entry {entry['index']} no longer matches its chunk ID.",
                )
            if entry["speaker"] != str(chunk.get("speaker") or ""):
                raise BackendRenderPlanError(
                    "backend_render_plan_speaker_changed",
                    f"Render entry {entry['index']} no longer matches its speaker.",
                )
            text = str(chunk.get("text") or "")
            if entry["text_sha256"] != text_sha256(text):
                raise BackendRenderPlanError(
                    "backend_render_plan_text_changed",
                    f"Render entry {entry['index']} no longer matches its canonical text.",
                )
            if entry["fish_cues"]:
                try:
                    validate_plan(
                        text,
                        {
                            "schema_version": 1,
                            "text_sha256": entry["text_sha256"],
                            "cues": entry["fish_cues"],
                        },
                    )
                except FishInlineCueError as exc:
                    raise BackendRenderPlanError(
                        exc.code,
                        f"Render entry {entry['index']} has an invalid Fish cue plan: {exc}",
                    ) from exc

    return {
        "schema_version": SCHEMA_VERSION,
        "script_fingerprint": script_fingerprint,
        "chunks_fingerprint": chunks_fingerprint,
        "entries": sorted(normalized_entries, key=lambda item: item["index"]),
        "warnings": _warnings(value.get("warnings"), "warnings"),
    }


def canonical_chunks_snapshot(
    chunks: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    snapshot: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        text = str(chunk.get("text") or "")
        if not text.strip():
            continue
        value = {
            "index": index,
            "id": chunk.get("id", index),
            "speaker": str(chunk.get("speaker") or ""),
            "text": text,
            "instruct": str(chunk.get("instruct") or ""),
        }
        if chunk.get("pause_after") is not None:
            value["pause_after"] = int(chunk.get("pause_after"))
        snapshot.append(value)
    return snapshot


def chunks_fingerprint(chunks: Sequence[Mapping[str, Any]]) -> str:
    return fingerprint_value(canonical_chunks_snapshot(chunks))


def build_task_chunks(
    chunks: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        text = str(chunk.get("text") or "")
        if not text.strip():
            continue
        continuity = resolve_spoken_continuity(chunks, index)
        previous = chunks[index - 1] if index > 0 else None
        following = chunks[index + 1] if index + 1 < len(chunks) else None

        def neighbor(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
            if not isinstance(value, Mapping) or not str(value.get("text") or "").strip():
                return None
            return {
                "speaker": str(value.get("speaker") or ""),
                "text": str(value.get("text") or ""),
                "instruct": str(value.get("instruct") or ""),
            }

        values.append(
            {
                "index": index,
                "chunk_id": f"chunk:{chunk.get('id', index)}",
                "speaker": str(chunk.get("speaker") or ""),
                "text": text,
                "text_sha256": text_sha256(text),
                "canonical_instruction": str(chunk.get("instruct") or ""),
                "effective_instruction": effective_delivery_instruction(
                    chunk.get("instruct", ""),
                    continuity,
                ),
                "spoken_continuity": continuity,
                "authored_pause_after_ms": chunk.get("pause_after"),
                "previous_chunk": neighbor(previous),
                "next_chunk": neighbor(following),
            }
        )
    return values


def plan_fingerprint(plan: Mapping[str, Any]) -> str:
    return fingerprint_value(normalize_backend_render_plan(plan))


def applied_binding_fields(chunk: Mapping[str, Any]) -> dict[str, Any] | None:
    if not (
        chunk.get("backend_render_plan_binding_enabled") is True
        or chunk.get("backend_render_plan_applied") is not None
    ):
        return None
    return {
        "plan_fingerprint": chunk.get("backend_render_plan_fingerprint"),
        "qwen_instruction": chunk.get("qwen_render_instruction"),
        "fish_direction": chunk.get("fish_render_instruction"),
        "fish_inline_plan": chunk.get("fish_render_plan"),
    }


def application_record(chunk: Mapping[str, Any]) -> dict[str, Any] | None:
    plan_id = chunk.get("backend_render_plan_fingerprint")
    if not plan_id:
        return None
    fish_plan = chunk.get("fish_render_plan")
    return {
        "plan_fingerprint": plan_id,
        "qwen_instruction_sha256": hashlib.sha256(
            str(chunk.get("qwen_render_instruction") or "").encode("utf-8")
        ).hexdigest(),
        "fish_direction_sha256": hashlib.sha256(
            str(chunk.get("fish_render_instruction") or "").encode("utf-8")
        ).hexdigest(),
        "fish_inline_plan_fingerprint": (
            fingerprint_value(fish_plan) if fish_plan is not None else None
        ),
    }


def _restore_bytes(path: Path, content: bytes | None) -> None:
    if content is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".backend-render-plan-rollback")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def apply_backend_render_plan(
    *,
    root_dir: str | Path,
    value: Mapping[str, Any],
    expected_script_fingerprint: str,
    expected_chunks_fingerprint: str,
    at_utc: str,
    origin: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    script_path = root / "annotated_script.json"
    chunks_path = root / "chunks.json"
    plan_path = root / PLAN_FILENAME
    try:
        script = json.loads(script_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BackendRenderPlanError(
            "backend_render_plan_script_missing",
            "The accepted Script is required before applying a backend render plan.",
        ) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackendRenderPlanError(
            "backend_render_plan_script_invalid",
            f"Could not read the accepted Script: {exc}",
        ) from exc
    if not isinstance(script, list):
        raise BackendRenderPlanError(
            "backend_render_plan_script_invalid",
            "annotated_script.json must contain an array.",
        )
    if fingerprint_value(script) != expected_script_fingerprint:
        raise BackendRenderPlanError(
            "backend_render_plan_script_changed",
            "The accepted Script changed before the render plan was applied.",
        )
    try:
        chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BackendRenderPlanError(
            "backend_render_plan_chunks_missing",
            "Synthesis chunks are required before applying a backend render plan.",
        ) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackendRenderPlanError(
            "backend_render_plan_chunks_invalid",
            f"Could not read synthesis chunks: {exc}",
        ) from exc
    if not isinstance(chunks, list) or any(not isinstance(chunk, dict) for chunk in chunks):
        raise BackendRenderPlanError(
            "backend_render_plan_chunks_invalid",
            "chunks.json must contain an array of chunk objects.",
        )
    current_chunks_fingerprint = chunks_fingerprint(chunks)
    if current_chunks_fingerprint != expected_chunks_fingerprint:
        raise BackendRenderPlanError(
            "backend_render_plan_chunks_changed",
            "Synthesis chunks changed before the render plan was applied.",
        )
    normalized = normalize_backend_render_plan(
        value,
        chunks=chunks,
        expected_script_fingerprint=expected_script_fingerprint,
        expected_chunks_fingerprint=expected_chunks_fingerprint,
    )
    fingerprint = fingerprint_value(normalized)
    entries = {entry["index"]: entry for entry in normalized["entries"]}
    updated_chunks = copy.deepcopy(chunks)
    for index, chunk in enumerate(updated_chunks):
        entry = entries.get(index)
        if entry is None:
            continue
        chunk["backend_render_plan_fingerprint"] = fingerprint
        chunk["qwen_render_instruction"] = entry["qwen_instruction"]
        chunk["fish_render_instruction"] = entry["fish_direction"]
        if entry["fish_cues"]:
            chunk["fish_render_plan"] = {
                "schema_version": 1,
                "text_sha256": entry["text_sha256"],
                "cues": copy.deepcopy(entry["fish_cues"]),
            }
        else:
            chunk.pop("fish_render_plan", None)
        if entry["warnings"]:
            chunk["backend_render_plan_warnings"] = copy.deepcopy(entry["warnings"])
        else:
            chunk.pop("backend_render_plan_warnings", None)

    persisted = {
        **copy.deepcopy(normalized),
        "plan_fingerprint": fingerprint,
        "applied_at_utc": at_utc,
        "chunk_count": len(normalized["entries"]),
        "origin": copy.deepcopy(dict(origin or {})),
    }
    before_chunks = chunks_path.read_bytes()
    before_plan = plan_path.read_bytes() if plan_path.exists() else None
    try:
        atomic_json_write(persisted, plan_path)
        atomic_json_write(updated_chunks, chunks_path)
    except Exception:
        _restore_bytes(chunks_path, before_chunks)
        _restore_bytes(plan_path, before_plan)
        raise
    return {
        "status": "applied",
        "destination": "produce",
        "plan_fingerprint": fingerprint,
        "chunk_count": len(normalized["entries"]),
        "fish_inline_chunk_count": sum(
            bool(entry["fish_cues"]) for entry in normalized["entries"]
        ),
        "warning_count": len(normalized["warnings"])
        + sum(len(entry["warnings"]) for entry in normalized["entries"]),
        "path": PLAN_FILENAME,
    }


def _core_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value.get(key))
        for key in (
            "schema_version",
            "script_fingerprint",
            "chunks_fingerprint",
            "entries",
            "warnings",
        )
    }


def inspect_backend_render_plan(root_dir: str | Path) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    script_path = root / "annotated_script.json"
    chunks_path = root / "chunks.json"
    plan_path = root / PLAN_FILENAME

    def read_json(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return {"_read_error": f"{type(exc).__name__}: {exc}"}

    script = read_json(script_path)
    chunks = read_json(chunks_path)
    current_script_fingerprint = (
        fingerprint_value(script) if isinstance(script, list) else None
    )
    current_chunks_fingerprint = (
        chunks_fingerprint(chunks) if isinstance(chunks, list) else None
    )
    base = {
        "schema_version": 1,
        "state": "missing",
        "available": bool(
            current_script_fingerprint and current_chunks_fingerprint
        ),
        "current": False,
        "path": PLAN_FILENAME,
        "current_script_fingerprint": current_script_fingerprint,
        "current_chunks_fingerprint": current_chunks_fingerprint,
        "plan_fingerprint": None,
        "script_fingerprint": None,
        "chunks_fingerprint": None,
        "chunk_count": 0,
        "fish_inline_chunk_count": 0,
        "fish_inline_cue_count": 0,
        "warning_count": 0,
        "applied_to_audio_count": 0,
        "not_yet_regenerated_count": 0,
        "applied_at_utc": None,
        "origin": {},
        "error": None,
    }
    if not isinstance(script, list) or not isinstance(chunks, list):
        base["state"] = "unavailable"
        if isinstance(script, Mapping) and script.get("_read_error"):
            base["error"] = script["_read_error"]
        elif isinstance(chunks, Mapping) and chunks.get("_read_error"):
            base["error"] = chunks["_read_error"]
        return base
    persisted = read_json(plan_path)
    if persisted is None:
        return base
    if not isinstance(persisted, Mapping) or persisted.get("_read_error"):
        base["state"] = "invalid"
        base["error"] = (
            persisted.get("_read_error")
            if isinstance(persisted, Mapping)
            else "backend_render_plan.json must contain an object."
        )
        return base
    core = _core_plan(persisted)
    base.update(
        {
            "plan_fingerprint": persisted.get("plan_fingerprint"),
            "script_fingerprint": core.get("script_fingerprint"),
            "chunks_fingerprint": core.get("chunks_fingerprint"),
            "applied_at_utc": persisted.get("applied_at_utc"),
            "origin": copy.deepcopy(
                persisted.get("origin")
                if isinstance(persisted.get("origin"), Mapping)
                else {}
            ),
        }
    )
    if (
        core.get("script_fingerprint") != current_script_fingerprint
        or core.get("chunks_fingerprint") != current_chunks_fingerprint
    ):
        base["state"] = "stale"
        base["error"] = (
            "The accepted Script or synthesis chunk structure changed after this "
            "delivery plan was created."
        )
        return base
    try:
        normalized = normalize_backend_render_plan(
            core,
            chunks=chunks,
            expected_script_fingerprint=current_script_fingerprint,
            expected_chunks_fingerprint=current_chunks_fingerprint,
        )
    except BackendRenderPlanError as exc:
        base["state"] = "invalid"
        base["error"] = str(exc)
        return base
    fingerprint = fingerprint_value(normalized)
    entries = normalized["entries"]
    applied_count = sum(
        isinstance(chunk.get("backend_render_plan_applied"), Mapping)
        and chunk["backend_render_plan_applied"].get("plan_fingerprint")
        == fingerprint
        for chunk in chunks
    )
    base.update(
        {
            "state": "current",
            "current": True,
            "plan_fingerprint": fingerprint,
            "chunk_count": len(entries),
            "fish_inline_chunk_count": sum(
                bool(entry.get("fish_cues")) for entry in entries
            ),
            "fish_inline_cue_count": sum(
                len(entry.get("fish_cues") or []) for entry in entries
            ),
            "warning_count": len(normalized.get("warnings") or [])
            + sum(len(entry.get("warnings") or []) for entry in entries),
            "applied_to_audio_count": applied_count,
            "not_yet_regenerated_count": max(0, len(entries) - applied_count),
        }
    )
    return base


def task_guidance() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "qwen": {
            "target": "Qwen3-TTS instruction-controlled generation",
            "principles": [
                "Use one concise whole-line instruction in direct actor language.",
                "State immediate objective, emotion, pacing, emphasis, and restraint only when audible.",
                "Preserve punctuation and spoken continuity; do not repeat stable Voice identity.",
                "Avoid markup, bracket tags, literary analysis, camera direction, or multiple conflicting alternatives.",
            ],
        },
        "fish": {
            "target": "Fish Audio S2.1 Pro Free",
            "principles": [
                "Translate the accepted Qwen performance intent into a short acoustically concrete Fish direction without inventing a different reading.",
                "Use sparse bracket cues immediately before the exact phrase they affect.",
                "Prefer simple documented or well-established cues such as whispering, shouting, sigh, gasp, long pause, emphasis, voice breaking, and steady voice.",
                "Use a reset cue after a temporary delivery change when the remainder should return to normal.",
                "Do not over-tag; omit inline cues when one global direction is enough.",
                "End-position cues are reactions only; never use an end tag as an abstract direction with no following speech.",
            ],
            "community_caveats": [
                "Treat community examples as heuristics rather than guaranteed syntax.",
                "Instruction response varies by reference Voice; concise local cues and expressive reference audio generally require listening validation.",
                "Do not assume a legal cue produced the requested performance; Alexandria still validates text, identity, delivery, and audio integrity.",
            ],
            "cue_contract": {
                "anchors": ["start", "before_phrase", "after_phrase", "end"],
                "kinds": ["delivery", "reaction", "reset"],
                "maximum_cues_per_chunk": MAX_CUES_PER_CHUNK,
                "phrase_anchors": "Exact, case-sensitive substrings from the canonical chunk text.",
            },
        },
    }
