from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


class GenerationStateError(RuntimeError):
    pass


class GenerationStateCorruptError(
    GenerationStateError
):
    pass


class GenerationStateMismatchError(
    GenerationStateError
):
    pass


def canonical_json_bytes(
    value: Any,
) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def fingerprint_value(
    value: Any,
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(value)
    ).hexdigest()


def fingerprint_text(
    text: str,
) -> str:
    return hashlib.sha256(
        str(text).encode("utf-8")
    ).hexdigest()


def atomic_json_write(
    value: Any,
    path: str | Path,
) -> None:
    target = Path(path)
    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    temporary = target.with_name(
        target.name + ".tmp"
    )

    try:
        with temporary.open(
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(
                value,
                handle,
                indent=2,
                ensure_ascii=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(
                handle.fileno()
            )

        os.replace(
            temporary,
            target,
        )
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def new_generation_state(
    *,
    source_fingerprint: str,
    generation_fingerprint: str,
    chunk_fingerprints: list[str],
    generation_identity: dict[str, Any] | None = None,
    source: dict[str, Any] | None = None,
    auditor_contract_version: int | None = None,
) -> dict[str, Any]:
    state = {
        "schema_version": SCHEMA_VERSION,
        "source_fingerprint": source_fingerprint,
        "generation_fingerprint": (
            generation_fingerprint
        ),
        "total_chunks": len(
            chunk_fingerprints
        ),
        "chunk_fingerprints": list(
            chunk_fingerprints
        ),
        "completed_chunks": [],
    }

    if generation_identity is not None:
        state["generation_identity"] = copy.deepcopy(
            generation_identity
        )

    if source is not None:
        state["source"] = copy.deepcopy(source)

    if auditor_contract_version is not None:
        state["auditor_contract_version"] = (
            auditor_contract_version
        )

    return state


def _require_text(
    state: dict[str, Any],
    key: str,
) -> str:
    value = state.get(key)

    if not isinstance(value, str) or not value:
        raise GenerationStateCorruptError(
            f"Generation state field "
            f"{key!r} must be non-empty text."
        )

    return value


def validate_generation_state(
    value: Any,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GenerationStateCorruptError(
            "Generation state must be a JSON object."
        )

    if value.get(
        "schema_version"
    ) != SCHEMA_VERSION:
        raise GenerationStateCorruptError(
            "Unsupported generation state schema."
        )

    _require_text(
        value,
        "source_fingerprint",
    )
    _require_text(
        value,
        "generation_fingerprint",
    )

    total_chunks = value.get(
        "total_chunks"
    )

    if (
        not isinstance(total_chunks, int)
        or isinstance(total_chunks, bool)
        or total_chunks < 0
    ):
        raise GenerationStateCorruptError(
            "Generation state total_chunks "
            "must be a non-negative integer."
        )

    chunk_fingerprints = value.get(
        "chunk_fingerprints"
    )

    if (
        not isinstance(
            chunk_fingerprints,
            list,
        )
        or len(
            chunk_fingerprints
        ) != total_chunks
        or not all(
            isinstance(item, str)
            and bool(item)
            for item
            in chunk_fingerprints
        )
    ):
        raise GenerationStateCorruptError(
            "Generation state chunk fingerprints "
            "are invalid."
        )

    completed = value.get(
        "completed_chunks"
    )

    if not isinstance(
        completed,
        list,
    ):
        raise GenerationStateCorruptError(
            "Generation state completed_chunks "
            "must be a list."
        )

    if len(completed) > total_chunks:
        raise GenerationStateCorruptError(
            "Generation state has more completed "
            "chunks than source chunks."
        )

    for expected_index, item in enumerate(
        completed,
        start=1,
    ):
        if not isinstance(item, dict):
            raise GenerationStateCorruptError(
                "Completed chunk records "
                "must be objects."
            )

        if item.get(
            "index"
        ) != expected_index:
            raise GenerationStateCorruptError(
                "Completed chunks must be "
                "contiguous and ordered."
            )

        chunk_fingerprint = item.get(
            "chunk_fingerprint"
        )

        if (
            not isinstance(
                chunk_fingerprint,
                str,
            )
            or not chunk_fingerprint
            or chunk_fingerprint
            != chunk_fingerprints[
                expected_index - 1
            ]
        ):
            raise GenerationStateCorruptError(
                "Completed chunk fingerprint "
                "does not match its source chunk."
            )

        entries = item.get(
            "entries"
        )

        if (
            not isinstance(entries, list)
            or not entries
            or not all(
                isinstance(entry, dict)
                for entry in entries
            )
        ):
            raise GenerationStateCorruptError(
                "Completed chunk entries "
                "must be a non-empty list "
                "of objects."
            )

    generation_identity = value.get(
        "generation_identity"
    )

    if (
        generation_identity is not None
        and not isinstance(
            generation_identity,
            dict,
        )
    ):
        raise GenerationStateCorruptError(
            "Generation state generation_identity "
            "must be an object when present."
        )

    source = value.get("source")

    if (
        source is not None
        and not isinstance(source, dict)
    ):
        raise GenerationStateCorruptError(
            "Generation state source must be "
            "an object when present."
        )

    auditor_contract_version = value.get(
        "auditor_contract_version"
    )

    if (
        auditor_contract_version is not None
        and (
            not isinstance(
                auditor_contract_version,
                int,
            )
            or isinstance(
                auditor_contract_version,
                bool,
            )
            or auditor_contract_version < 0
        )
    ):
        raise GenerationStateCorruptError(
            "Generation state auditor_contract_version "
            "must be a non-negative integer when present."
        )

    return value


def load_generation_state(
    path: str | Path,
) -> dict[str, Any] | None:
    target = Path(path)

    if not target.exists():
        return None

    try:
        value = json.loads(
            target.read_text(
                encoding="utf-8"
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        raise GenerationStateCorruptError(
            "Generation state could not be read: "
            f"{exc}"
        ) from exc

    return validate_generation_state(
        value
    )


def prepare_generation_state(
    *,
    path: str | Path,
    source_fingerprint: str,
    generation_fingerprint: str,
    chunk_fingerprints: list[str],
    generation_identity: dict[str, Any] | None = None,
    source: dict[str, Any] | None = None,
    auditor_contract_version: int | None = None,
) -> dict[str, Any]:
    existing = load_generation_state(path)

    if existing is None:
        state = new_generation_state(
            source_fingerprint=(
                source_fingerprint
            ),
            generation_fingerprint=(
                generation_fingerprint
            ),
            chunk_fingerprints=(
                chunk_fingerprints
            ),
            generation_identity=(
                generation_identity
            ),
            source=source,
            auditor_contract_version=(
                auditor_contract_version
            ),
        )
        atomic_json_write(state, path)
        return state

    mismatches = []

    if (
        existing["source_fingerprint"]
        != source_fingerprint
    ):
        mismatches.append("source")

    if (
        existing["generation_fingerprint"]
        != generation_fingerprint
    ):
        mismatches.append(
            "generation configuration"
        )

    if (
        existing["chunk_fingerprints"]
        != chunk_fingerprints
    ):
        mismatches.append("chunk layout")

    if mismatches:
        raise GenerationStateMismatchError(
            "Existing generation state does not "
            "match the current "
            + ", ".join(mismatches)
            + ". Remove generation_state.json "
            "before starting a different run."
        )

    return existing


def completed_entries(
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    validate_generation_state(
        state
    )
    entries = []

    for item in state[
        "completed_chunks"
    ]:
        entries.extend(
            item["entries"]
        )

    return entries


def checkpoint_completed_chunk(
    *,
    state: dict[str, Any],
    path: str | Path,
    index: int,
    chunk_fingerprint: str,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    validate_generation_state(
        state
    )

    expected_index = (
        len(
            state["completed_chunks"]
        )
        + 1
    )

    if index != expected_index:
        raise GenerationStateError(
            "Checkpoint index must be the next "
            "contiguous source chunk."
        )

    expected_fingerprint = state[
        "chunk_fingerprints"
    ][index - 1]

    if (
        chunk_fingerprint
        != expected_fingerprint
    ):
        raise GenerationStateMismatchError(
            "Checkpoint source chunk does not "
            "match the prepared generation state."
        )

    if (
        not isinstance(entries, list)
        or not entries
        or not all(
            isinstance(entry, dict)
            for entry in entries
        )
    ):
        raise GenerationStateError(
            "Checkpoint entries must be a "
            "non-empty list of objects."
        )

    updated = {
        **state,
        "completed_chunks": [
            *state["completed_chunks"],
            {
                "index": index,
                "chunk_fingerprint": (
                    chunk_fingerprint
                ),
                "entries": entries,
            },
        ],
    }

    validate_generation_state(
        updated
    )
    atomic_json_write(
        updated,
        path,
    )
    return updated


def clear_generation_state(
    path: str | Path,
) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass
