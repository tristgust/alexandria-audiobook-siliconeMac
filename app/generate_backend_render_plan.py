from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from backend_render_plan import (
    BackendRenderPlanError,
    apply_backend_render_plan,
    build_task_chunks,
    chunks_fingerprint,
    normalize_backend_render_plan,
    task_guidance,
)
from fish_inline_cues import FishInlineCueError, validate_plan
from generation_state import fingerprint_value
from llm_config import build_runtime_client
from utils import atomic_json_write


STATE_SCHEMA_VERSION = 1
DEFAULT_BATCH_SIZE = 96
DEFAULT_MAX_BATCH_CHARS = 60000
DEFAULT_MAX_TOKENS = 32768


class LocalRenderPlanError(RuntimeError):
    pass


def _read_json(path: Path, *, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LocalRenderPlanError(f"{label} is missing: {path}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalRenderPlanError(f"Could not read {label}: {exc}") from exc


def _load_config(path: Path) -> dict[str, Any]:
    value = _read_json(path, label="Alexandria configuration")
    if not isinstance(value, dict):
        raise LocalRenderPlanError("Alexandria configuration must be a JSON object.")
    return value


def _batch_task_chunks(
    values: Sequence[Mapping[str, Any]],
    *,
    batch_size: int,
    max_chars: int,
) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    for raw in values:
        item = copy.deepcopy(dict(raw))
        size = len(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
        if current and (
            len(current) >= batch_size
            or current_chars + size > max_chars
        ):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(item)
        current_chars += size
    if current:
        batches.append(current)
    return batches


def _state_seed(
    *,
    script_fingerprint: str,
    chunks_fingerprint_value: str,
    batches: Sequence[Sequence[Mapping[str, Any]]],
    runtime_identity: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "script_fingerprint": script_fingerprint,
        "chunks_fingerprint": chunks_fingerprint_value,
        "batch_layout": [
            [int(item["index"]) for item in batch]
            for batch in batches
        ],
        "runtime_identity": copy.deepcopy(dict(runtime_identity)),
        "completed_batches": {},
        "warnings": [],
    }


def _load_or_create_state(
    path: Path,
    *,
    seed: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        value = None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        value = None
    if not isinstance(value, dict):
        state = copy.deepcopy(dict(seed))
        atomic_json_write(state, path)
        return state
    identity_keys = (
        "schema_version",
        "script_fingerprint",
        "chunks_fingerprint",
        "batch_layout",
        "runtime_identity",
    )
    if any(value.get(key) != seed.get(key) for key in identity_keys):
        state = copy.deepcopy(dict(seed))
        atomic_json_write(state, path)
        return state
    completed = value.get("completed_batches")
    if not isinstance(completed, dict):
        value["completed_batches"] = {}
    warnings = value.get("warnings")
    if not isinstance(warnings, list):
        value["warnings"] = []
    return value


def _system_prompt() -> str:
    return (
        "You are Alexandria's backend delivery planner. Return only JSON matching "
        "the supplied backend_render_plan schema. Do not rewrite, omit, add, or "
        "return spoken text. Copy every identifier and fingerprint exactly. For "
        "Qwen, write one concise whole-line actor direction. Derive the Fish S2.1 "
        "direction and cues from that same Qwen performance intent, translating it "
        "without inventing a different reading. Use one shorter acoustically concrete "
        "global direction and add sparse inline "
        "cues only when a local delivery change is useful. Phrase anchors must be "
        "exact case-sensitive substrings of canonical text. Preserve punctuation, "
        "dialogue-tag attachment, interruptions, resumptions, and authored pauses. "
        "Do not repeat stable Voice identity or use literary-analysis prose."
    )


def _user_prompt(
    *,
    script_fingerprint: str,
    chunks_fingerprint_value: str,
    chunks: Sequence[Mapping[str, Any]],
) -> str:
    payload = {
        "schema_version": 1,
        "script_fingerprint": script_fingerprint,
        "chunks_fingerprint": chunks_fingerprint_value,
        "backend_guidance": task_guidance(),
        "chunks": list(chunks),
        "requirements": {
            "coverage": "Return every supplied chunk exactly once in the same order.",
            "canonical_text": "Do not return or modify spoken text.",
            "qwen": "Use direct, concise, whole-line performance direction without tags.",
            "fish": "Translate the Qwen performance intent into a concise global direction and zero or more sparse exact-phrase cues without changing the intended reading.",
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _validate_batch_result(
    value: Any,
    *,
    expected_chunks: Sequence[Mapping[str, Any]],
    script_fingerprint: str,
    chunks_fingerprint_value: str,
) -> dict[str, Any]:
    try:
        normalized = normalize_backend_render_plan(
            value,
            expected_script_fingerprint=script_fingerprint,
            expected_chunks_fingerprint=chunks_fingerprint_value,
        )
    except BackendRenderPlanError as exc:
        raise LocalRenderPlanError(str(exc)) from exc
    expected = {int(item["index"]): item for item in expected_chunks}
    actual = {int(item["index"]): item for item in normalized["entries"]}
    if list(actual) != list(expected):
        raise LocalRenderPlanError(
            "Local render-plan batch did not return every requested chunk in order."
        )
    for index, task_chunk in expected.items():
        entry = actual[index]
        for field in ("chunk_id", "speaker", "text_sha256"):
            if entry[field] != task_chunk[field]:
                raise LocalRenderPlanError(
                    f"Local render-plan batch changed {field} for chunk {index}."
                )
        if entry["fish_cues"]:
            try:
                validate_plan(
                    str(task_chunk["text"]),
                    {
                        "schema_version": 1,
                        "text_sha256": entry["text_sha256"],
                        "cues": entry["fish_cues"],
                    },
                )
            except FishInlineCueError as exc:
                raise LocalRenderPlanError(
                    f"Local render-plan batch returned an invalid Fish cue for "
                    f"chunk {index}: {exc}"
                ) from exc
    return normalized


def generate_local_backend_render_plan(
    *,
    root_dir: str | Path,
    config_path: str | Path,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batch_chars: int = DEFAULT_MAX_BATCH_CHARS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser().resolve()
    config = _load_config(Path(config_path).expanduser().resolve())
    script = _read_json(root / "annotated_script.json", label="accepted Script")
    chunks = _read_json(root / "chunks.json", label="synthesis chunks")
    if not isinstance(script, list) or not script:
        raise LocalRenderPlanError("Accepted Script must be a non-empty JSON array.")
    if not isinstance(chunks, list) or not chunks:
        raise LocalRenderPlanError("Synthesis chunks must be a non-empty JSON array.")
    if any(not isinstance(chunk, dict) for chunk in chunks):
        raise LocalRenderPlanError("Every synthesis chunk must be a JSON object.")

    script_fingerprint = fingerprint_value(script)
    chunks_fingerprint_value = chunks_fingerprint(chunks)
    task_chunks = build_task_chunks(chunks)
    if not task_chunks:
        raise LocalRenderPlanError("No spoken synthesis chunks are available to plan.")
    batches = _batch_task_chunks(
        task_chunks,
        batch_size=max(1, int(batch_size)),
        max_chars=max(1000, int(max_batch_chars)),
    )
    runtime = build_runtime_client(config, stage="script")
    runtime_identity = {
        "backend": runtime.backend,
        "model_name": runtime.model_name,
        "thinking": bool(runtime.thinking),
        "structured_output": bool(runtime.structured_output),
        "corrective_retry": bool(runtime.corrective_retry),
        "context_length": runtime.context_length,
    }
    state_path = root / "backend_render_plan_state.json"
    seed = _state_seed(
        script_fingerprint=script_fingerprint,
        chunks_fingerprint_value=chunks_fingerprint_value,
        batches=batches,
        runtime_identity=runtime_identity,
    )
    state = _load_or_create_state(state_path, seed=seed)
    completed = state["completed_batches"]
    print(
        "Backend delivery planning: "
        f"{len(task_chunks)} chunks in {len(batches)} resumable batches."
    )
    print(
        "Planner runtime: "
        f"{runtime.model_name} via {runtime.backend}; "
        f"structured JSON={'on' if runtime.structured_output else 'off'}."
    )
    preloaded, preload_message = runtime.preload()
    print(preload_message)
    if not preloaded:
        print("Continuing without explicit model preload.")

    for batch_index, batch in enumerate(batches):
        key = str(batch_index)
        existing = completed.get(key)
        if isinstance(existing, dict) and isinstance(existing.get("entries"), list):
            print(
                f"Resuming: batch {batch_index + 1}/{len(batches)} already complete "
                f"({len(existing['entries'])} chunks)."
            )
            continue
        print(
            f"Planning batch {batch_index + 1}/{len(batches)} "
            f"({len(batch)} chunks; indices {batch[0]['index']}–{batch[-1]['index']})."
        )
        completion = runtime.complete_json(
            messages=[
                {"role": "system", "content": _system_prompt()},
                {
                    "role": "user",
                    "content": _user_prompt(
                        script_fingerprint=script_fingerprint,
                        chunks_fingerprint_value=chunks_fingerprint_value,
                        chunks=batch,
                    ),
                },
            ],
            contract="backend_render_plan",
            temperature=0.2,
            max_tokens=max(2048, int(max_tokens)),
            top_p=0.8,
            top_k=20,
            min_p=0.0,
            presence_penalty=0.0,
        )
        normalized = _validate_batch_result(
            completion.data,
            expected_chunks=batch,
            script_fingerprint=script_fingerprint,
            chunks_fingerprint_value=chunks_fingerprint_value,
        )
        completed[key] = {
            "entries": normalized["entries"],
            "warnings": normalized["warnings"],
            "metrics": copy.deepcopy(completion.metrics),
        }
        state["warnings"].extend(normalized["warnings"])
        atomic_json_write(state, state_path)
        print(
            f"Checkpointed delivery-plan batch {batch_index + 1}/{len(batches)}."
        )

    merged_entries: list[dict[str, Any]] = []
    merged_warnings: list[str] = []
    for batch_index in range(len(batches)):
        record = completed.get(str(batch_index))
        if not isinstance(record, dict) or not isinstance(record.get("entries"), list):
            raise LocalRenderPlanError(
                f"Backend delivery-plan checkpoint is missing batch {batch_index + 1}."
            )
        merged_entries.extend(record["entries"])
        merged_warnings.extend(record.get("warnings") or [])
    plan = {
        "schema_version": 1,
        "script_fingerprint": script_fingerprint,
        "chunks_fingerprint": chunks_fingerprint_value,
        "entries": merged_entries,
        "warnings": list(dict.fromkeys(merged_warnings)),
    }
    normalize_backend_render_plan(
        plan,
        chunks=chunks,
        expected_script_fingerprint=script_fingerprint,
        expected_chunks_fingerprint=chunks_fingerprint_value,
    )
    result = apply_backend_render_plan(
        root_dir=root,
        value=plan,
        expected_script_fingerprint=script_fingerprint,
        expected_chunks_fingerprint=chunks_fingerprint_value,
        at_utc=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat().replace("+00:00", "Z"),
        origin={
            "type": "local_llm",
            **runtime_identity,
        },
    )
    try:
        state_path.unlink()
    except FileNotFoundError:
        pass
    print(
        "Backend delivery plan complete: "
        f"{result['chunk_count']} chunks; "
        f"{result['fish_inline_chunk_count']} with Fish inline cues."
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate Alexandria's Qwen/Fish backend delivery plan locally."
    )
    parser.add_argument("--root-dir", required=True)
    parser.add_argument("--config-path", required=True)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--max-batch-chars",
        type=int,
        default=DEFAULT_MAX_BATCH_CHARS,
    )
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    args = parser.parse_args(argv)
    try:
        generate_local_backend_render_plan(
            root_dir=args.root_dir,
            config_path=args.config_path,
            batch_size=args.batch_size,
            max_batch_chars=args.max_batch_chars,
            max_tokens=args.max_tokens,
        )
    except Exception as exc:
        print(f"Backend delivery planning failed: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
