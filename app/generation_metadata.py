from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from generation_state import (
    atomic_json_write,
    clear_generation_state,
    fingerprint_value,
)


SCHEMA_VERSION = 1


class GenerationMetadataError(RuntimeError):
    pass


def utc_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _speaker_labels(
    entries: list[dict[str, Any]],
) -> list[str]:
    labels = set()

    for entry in entries:
        label = (
            entry.get("speaker")
            or entry.get("type")
            or "UNKNOWN"
        )

        if not isinstance(label, str):
            label = str(label)

        labels.add(
            label.strip() or "UNKNOWN"
        )

    return sorted(labels)


def build_generation_metadata(
    *,
    source_path: str | Path,
    source_fingerprint: str,
    source_character_count: int,
    source_chunk_count: int,
    generation_fingerprint: str,
    generation_identity: dict[str, Any],
    entries: list[dict[str, Any]],
    resumed: bool,
    previously_completed_chunks: int,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    if (
        not isinstance(entries, list)
        or not all(
            isinstance(entry, dict)
            for entry in entries
        )
    ):
        raise GenerationMetadataError(
            "Completed script entries must be "
            "a list of objects."
        )

    if (
        not isinstance(
            source_character_count,
            int,
        )
        or isinstance(
            source_character_count,
            bool,
        )
        or source_character_count < 0
    ):
        raise GenerationMetadataError(
            "Source character count must be "
            "a non-negative integer."
        )

    if (
        not isinstance(
            source_chunk_count,
            int,
        )
        or isinstance(
            source_chunk_count,
            bool,
        )
        or source_chunk_count < 0
    ):
        raise GenerationMetadataError(
            "Source chunk count must be "
            "a non-negative integer."
        )

    if (
        not isinstance(
            previously_completed_chunks,
            int,
        )
        or isinstance(
            previously_completed_chunks,
            bool,
        )
        or previously_completed_chunks < 0
    ):
        raise GenerationMetadataError(
            "Previously completed chunk count "
            "must be a non-negative integer."
        )

    if (
        previously_completed_chunks
        > source_chunk_count
    ):
        raise GenerationMetadataError(
            "Previously completed chunk count "
            "cannot exceed source chunk count."
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": (
            generated_at_utc
            if generated_at_utc is not None
            else utc_timestamp()
        ),
        "source": {
            "basename": Path(
                source_path
            ).name,
            "fingerprint": (
                source_fingerprint
            ),
            "character_count": (
                source_character_count
            ),
            "chunk_count": (
                source_chunk_count
            ),
        },
        "generation": {
            "fingerprint": (
                generation_fingerprint
            ),
            "effective_identity": (
                copy.deepcopy(
                    generation_identity
                )
            ),
        },
        "result": {
            "script_fingerprint": (
                fingerprint_value(
                    entries
                )
            ),
            "entry_count": len(entries),
            "speaker_labels": (
                _speaker_labels(
                    entries
                )
            ),
        },
        "resume": {
            "resumed": bool(resumed),
            "previously_completed_chunks": (
                previously_completed_chunks
            ),
        },
    }


def _read_json(
    path: str | Path,
) -> Any:
    target = Path(path)

    try:
        with target.open(
            "r",
            encoding="utf-8",
        ) as handle:
            return json.load(handle)
    except (
        OSError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        raise GenerationMetadataError(
            "Finalized JSON could not be "
            f"verified at {target}: {exc}"
        ) from exc


def finalize_generation_outputs(
    *,
    entries: list[dict[str, Any]],
    metadata: dict[str, Any],
    script_path: str | Path,
    metadata_path: str | Path,
    state_path: str | Path,
) -> None:
    atomic_json_write(
        entries,
        script_path,
    )

    try:
        atomic_json_write(
            metadata,
            metadata_path,
        )

        written_script = _read_json(
            script_path
        )
        written_metadata = _read_json(
            metadata_path
        )

        if (
            fingerprint_value(
                written_script
            )
            != fingerprint_value(
                entries
            )
        ):
            raise GenerationMetadataError(
                "Final script verification failed."
            )

        if (
            fingerprint_value(
                written_metadata
            )
            != fingerprint_value(
                metadata
            )
        ):
            raise GenerationMetadataError(
                "Final metadata verification failed."
            )
    except BaseException:
        try:
            Path(
                metadata_path
            ).unlink()
        except OSError:
            pass
        raise

    clear_generation_state(
        state_path
    )
