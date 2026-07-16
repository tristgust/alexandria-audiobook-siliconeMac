"""Compatibility exports for canonical visual discovery state.

All Phase 18D discovery behavior is owned by `visual_discovery.py`. This module
exists only so interrupted local imports fail safely while the product and tests
use the canonical names.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from visual_discovery import (
    VisualDiscoveryEvidenceError,
    VisualDiscoveryError,
    VisualDiscoveryMismatchError,
    VisualDiscoveryStateError,
    build_visual_identity,
    checkpoint_visual_passage,
    clear_visual_discovery_state,
    completed_visual_observations,
    inspect_visual_discovery_state,
    load_visual_discovery_state,
    new_visual_discovery_state,
    normalize_visual_passage_result,
    prepare_visual_discovery_state,
    validate_visual_discovery_state,
)

PersonaVisualDiscoveryError = VisualDiscoveryError
PersonaVisualDiscoveryCorruptError = VisualDiscoveryStateError
PersonaVisualDiscoveryMismatchError = VisualDiscoveryMismatchError
PersonaVisualEvidenceError = VisualDiscoveryEvidenceError

build_visual_generation_identity = build_visual_identity
checkpoint_persona_visual_unit = checkpoint_visual_passage
clear_persona_visual_state = clear_visual_discovery_state
completed_visual_data = completed_visual_observations
load_persona_visual_state = load_visual_discovery_state
new_persona_visual_state = new_visual_discovery_state
normalize_visual_result = normalize_visual_passage_result
prepare_persona_visual_state = prepare_visual_discovery_state
validate_persona_visual_state = validate_visual_discovery_state


def selected_entry_records(
    approved_roster: dict[str, Any],
    entry_ids: list[str],
) -> list[dict[str, Any]]:
    by_id = {
        entry["id"]: entry
        for entry in approved_roster.get("entries", [])
    }
    unknown = sorted(set(entry_ids) - set(by_id))
    if unknown:
        raise PersonaVisualDiscoveryError(
            "Selected approved roster entries were not found: "
            + ", ".join(unknown)
        )
    return [by_id[entry_id] for entry_id in entry_ids]


def inspect_persona_visual_state(
    path: str | Path,
    *,
    source_fingerprint: str | None = None,
    roster_fingerprint: str | None = None,
) -> dict[str, Any]:
    current_source = (
        {"fingerprint": source_fingerprint}
        if source_fingerprint is not None
        else None
    )
    status = inspect_visual_discovery_state(
        path,
        current_source=current_source,
        roster_fingerprint=roster_fingerprint,
    )
    return {
        **status,
        "completed_units": status["completed_passages"],
        "total_units": status["total_passages"],
        "selected_entry_ids": status["character_ids"],
    }
