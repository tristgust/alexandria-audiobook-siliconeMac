"""Compatibility exports for the canonical character visual contract.

Phase 18D originally produced a second `persona_visual` implementation while
`character_visuals.py` was already committed as the contract and storage owner.
Keep this module as a narrow import bridge for interrupted callers; all behavior
lives in `character_visuals.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from character_visuals import (
    PROFILE_BUCKETS,
    CharacterVisualError,
    CharacterVisualValidationError,
    build_visual_dossier,
    inspect_visual_dossier,
    load_persona_reference,
    persona_reference_path,
    persona_reference_targets,
    sanitize_character_filename,
    validate_visual_dossier,
    write_visual_dossier,
)

PROFILE_CATEGORIES = PROFILE_BUCKETS
PersonaVisualError = CharacterVisualError
PersonaVisualValidationError = CharacterVisualValidationError


def persona_ref_filename(name: str) -> str:
    return sanitize_character_filename(name) + ".json"


def persona_ref_path(directory: str | Path, name: str) -> Path:
    return persona_reference_path(directory, name)


def persona_ref_targets(
    *,
    persona_refs_dir: str | Path,
    selected_entries: list[dict[str, Any]],
    all_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Path]:
    def normalize(entry: dict[str, Any]) -> dict[str, str]:
        return {
            "entry_id": entry["entry_id"],
            "character_name": (
                entry.get("character_name")
                or entry.get("canonical_name")
                or entry.get("display_name")
                or ""
            ),
        }

    return persona_reference_targets(
        persona_refs_dir=persona_refs_dir,
        selected_entries=[normalize(item) for item in selected_entries],
        all_entries=(
            [normalize(item) for item in all_entries]
            if all_entries is not None
            else None
        ),
    )


def load_persona_ref(
    path: str | Path,
    *,
    source_text: str | None = None,
    expected_source_fingerprint: str | None = None,
    expected_roster_fingerprint: str | None = None,
    expected_entry_id: str | None = None,
) -> dict[str, Any]:
    reference = load_persona_reference(path)
    if (
        expected_entry_id is not None
        and reference.get("roster_entry_id") not in {
            None,
            expected_entry_id,
        }
    ):
        raise PersonaVisualValidationError(
            "Persona reference belongs to a different roster entry."
        )
    if (
        expected_source_fingerprint is not None
        and reference.get("visual_source_fingerprint") not in {
            None,
            expected_source_fingerprint,
        }
    ):
        raise PersonaVisualValidationError(
            "Visual dossier belongs to a different source."
        )
    if (
        expected_roster_fingerprint is not None
        and reference.get("visual_roster_fingerprint") not in {
            None,
            expected_roster_fingerprint,
        }
    ):
        raise PersonaVisualValidationError(
            "Visual dossier belongs to a different approved roster."
        )
    if "visual" in reference:
        reference = {
            **reference,
            "visual": validate_visual_dossier(
                reference["visual"],
                source_text=source_text,
            ),
        }
    return reference


def inspect_persona_visual(
    path: str | Path,
    *,
    source_text: str | None = None,
    expected_source_fingerprint: str | None = None,
    expected_roster_fingerprint: str | None = None,
    expected_entry_id: str | None = None,
) -> dict[str, Any]:
    return inspect_visual_dossier(
        persona_ref_path=path,
        source_text=source_text,
        expected_entry_id=expected_entry_id,
        expected_source_fingerprint=expected_source_fingerprint,
        expected_roster_fingerprint=expected_roster_fingerprint,
    )


compile_visual_dossier = build_visual_dossier
save_visual_dossier = write_visual_dossier
validate_persona_ref = load_persona_ref
