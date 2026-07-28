from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from character_roster import CharacterRosterError, read_character_roster
from external_stage_transfers import (
    ExternalStageTransferConflictError,
    ExternalStageTransferValidationError,
    transfer_structured_result_candidate,
)
from external_workflows import (
    ExternalWorkflowConflictError,
    ExternalWorkflowValidationError,
    get_structured_result_candidate,
    mark_structured_result_transferred,
    store_derived_structured_result_candidate,
)
from generation_state import atomic_json_write, fingerprint_text, fingerprint_value
from visual_discovery import (
    VisualDiscoveryError,
    checkpoint_visual_passage,
    checkpoint_visual_reconciliation,
    load_visual_discovery_state,
    new_visual_discovery_state,
    normalize_visual_passage_result,
)


PACKAGE_DIR = Path("external_workflows") / "cast_dossier_packages"
VOICE_DOSSIER_FILENAME = "cast_voice_dossiers.json"


class CastDossierPackageError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.details = copy.deepcopy(details or {})

    def as_detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "details": copy.deepcopy(self.details),
        }


def _package_path(root: Path, parent_candidate_id: str) -> Path:
    return root / PACKAGE_DIR / f"{parent_candidate_id}.json"


def _read_package(root: Path, parent_candidate_id: str) -> dict[str, Any]:
    path = _package_path(root, parent_candidate_id)
    if not path.is_file():
        raise CastDossierPackageError(
            "cast_dossier_package_not_found",
            "The Complete Cast dossier package was not found.",
            status_code=404,
        )
    try:
        import json

        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise CastDossierPackageError(
            "cast_dossier_package_invalid",
            f"The Complete Cast dossier package is unreadable: {exc}",
        ) from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or value.get("parent_candidate_id") != parent_candidate_id
    ):
        raise CastDossierPackageError(
            "cast_dossier_package_invalid",
            "The Complete Cast dossier package record is invalid.",
        )
    return value


def get_cast_dossier_package(
    *,
    root_dir: str | Path,
    parent_candidate_id: str,
) -> dict[str, Any]:
    return _read_package(Path(root_dir), parent_candidate_id)


def split_complete_cast_dossier_candidate(
    *,
    root_dir: str | Path,
    parent: dict[str, Any],
) -> dict[str, Any]:
    root = Path(root_dir)
    if parent.get("task_type") != "complete_cast_dossier":
        raise CastDossierPackageError(
            "complete_cast_dossier_required",
            "This result is not a Complete Cast dossier.",
            status_code=422,
        )
    parent_id = str(parent.get("candidate_id") or "").strip()
    if not parent_id:
        raise CastDossierPackageError(
            "complete_cast_dossier_required",
            "The Complete Cast dossier has no stored candidate identifier.",
            status_code=422,
        )
    existing_path = _package_path(root, parent_id)
    if existing_path.exists():
        package = _read_package(root, parent_id)
        return {
            "parent": get_structured_result_candidate(
                root_dir=root,
                candidate_id=parent_id,
            ),
            "package": package,
            "roster_candidate": (
                get_structured_result_candidate(
                    root_dir=root,
                    candidate_id=package["components"]["roster_candidate_id"],
                )
                if package["components"].get("roster_candidate_id")
                else None
            ),
        }

    result = parent.get("result") or {}
    sections = copy.deepcopy(result.get("selected_sections") or {})
    roster_child = None
    persona_child = None
    if sections.get("roster_and_relationships"):
        roster_child = store_derived_structured_result_candidate(
            root_dir=root,
            parent=parent,
            task_type="roster_discovery",
            result=result["roster"],
        )
    if sections.get("voice_personas_and_designs"):
        voice_result = result.get("voice_dossiers") or {}
        parent_artifacts = copy.deepcopy(
            (parent.get("snapshot") or {}).get("artifact_fingerprints") or {}
        )
        parent_artifacts.pop("character_roster", None)
        parent_artifacts.pop("character_roster_draft", None)
        persona_child = store_derived_structured_result_candidate(
            root_dir=root,
            parent=parent,
            task_type="persona_catalog_generation",
            artifact_fingerprints=parent_artifacts,
            result={
                "personas": [
                    {
                        "speaker": item["speaker"],
                        "description": item["designed_voice_description"],
                        "ref_text": item["ref_text"],
                    }
                    for item in voice_result.get("voices") or []
                ],
                "warnings": list(voice_result.get("warnings") or []),
            },
        )
    package = {
        "schema_version": 1,
        "parent_candidate_id": parent_id,
        "parent_result_fingerprint": parent["result_fingerprint"],
        "source_fingerprint": (parent.get("snapshot") or {}).get(
            "source_fingerprint"
        ),
        "selected_sections": sections,
        "status": "awaiting_roster_review" if roster_child else "ready_for_activation",
        "components": {
            "roster_candidate_id": (
                roster_child.get("candidate_id") if roster_child else None
            ),
            "persona_candidate_id": (
                persona_child.get("candidate_id") if persona_child else None
            ),
            "visual_included": bool(sections.get("visual_dossiers")),
        },
        "voice_dossiers": copy.deepcopy(result.get("voice_dossiers")),
        "visual_observations": copy.deepcopy(result.get("visual_observations")),
        "visual_dossiers": copy.deepcopy(result.get("visual_dossiers")),
        "warnings": copy.deepcopy(result.get("warnings") or []),
        "applications": {},
    }
    atomic_json_write(package, existing_path)
    try:
        transferred = mark_structured_result_transferred(
            root_dir=root,
            candidate_id=parent_id,
            expected_result_fingerprint=parent["result_fingerprint"],
            application={
                "status": "native_review_ready",
                "destination": "cast_dossier_review",
                "tab": "characters",
                "stage": "complete_cast_dossier",
                "package_path": str(existing_path.relative_to(root)),
                "components": copy.deepcopy(package["components"]),
            },
        )
    except ExternalWorkflowConflictError:
        transferred = get_structured_result_candidate(
            root_dir=root,
            candidate_id=parent_id,
        )
    return {
        "parent": transferred,
        "package": package,
        "roster_candidate": roster_child,
    }


def package_for_roster_draft(
    *,
    root_dir: str | Path,
    draft_fingerprint: str,
) -> tuple[dict[str, Any], str] | None:
    fingerprint = str(draft_fingerprint or "").strip()
    if not fingerprint:
        return None
    root = Path(root_dir)
    package_root = root / PACKAGE_DIR
    if not package_root.is_dir():
        return None
    for path in sorted(
        package_root.glob("*.json"),
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    ):
        try:
            package = _read_package(root, path.stem)
            roster_id = str(
                (package.get("components") or {}).get("roster_candidate_id")
                or ""
            ).strip()
            if not roster_id:
                continue
            candidate = get_structured_result_candidate(
                root_dir=root,
                candidate_id=roster_id,
            )
        except (
            CastDossierPackageError,
            ExternalWorkflowValidationError,
            OSError,
        ):
            continue
        application = candidate.get("application") or {}
        if (
            candidate.get("status") == "transferred"
            and str(application.get("draft_fingerprint") or "").strip()
            == fingerprint
        ):
            return package, roster_id
    return None


def package_for_roster_candidate(
    *,
    root_dir: str | Path,
    roster_candidate: dict[str, Any],
) -> dict[str, Any] | None:
    review = roster_candidate.get("review") or {}
    parent_id = str(review.get("derived_from_candidate_id") or "").strip()
    if not parent_id:
        return None
    try:
        parent = get_structured_result_candidate(
            root_dir=root_dir,
            candidate_id=parent_id,
        )
    except ExternalWorkflowValidationError:
        return None
    if parent.get("task_type") != "complete_cast_dossier":
        return None
    return get_cast_dossier_package(
        root_dir=root_dir,
        parent_candidate_id=parent_id,
    )


def _identity_keys(value: Any) -> tuple[str, ...]:
    raw = str(value or "").strip().casefold()
    if not raw:
        return ()
    normalized = " ".join(
        "".join(
            character if character.isalnum() else " "
            for character in raw
        ).split()
    )
    keys = [f"exact:{raw}"]
    if normalized:
        keys.append(f"normalized:{normalized}")
        if normalized.startswith("the "):
            keys.append(f"normalized:{normalized[4:]}")
    return tuple(dict.fromkeys(keys))


def _identity_index(roster: dict[str, Any]) -> tuple[dict[str, str], set[str]]:
    values: dict[str, set[str]] = {}
    resolved_values: dict[str, set[str]] = {}
    primary_values: dict[str, set[str]] = {}
    resolved_primary_values: dict[str, set[str]] = {}
    for entry in roster.get("entries") or []:
        entry_id = str(entry.get("id") or "").strip()
        if not entry_id:
            continue
        resolved = entry.get("resolution_status") == "resolved"
        primary_labels = [
            entry_id,
            entry.get("canonical_name"),
            entry.get("display_name"),
            entry.get("speaker_label"),
        ]
        secondary_labels = [
            *(entry.get("aliases") or []),
            *(entry.get("nicknames") or []),
            *(entry.get("titles") or []),
        ]
        for raw in primary_labels:
            for label in _identity_keys(raw):
                values.setdefault(label, set()).add(entry_id)
                primary_values.setdefault(label, set()).add(entry_id)
                if resolved:
                    resolved_values.setdefault(label, set()).add(entry_id)
                    resolved_primary_values.setdefault(label, set()).add(entry_id)
        for raw in secondary_labels:
            for label in _identity_keys(raw):
                values.setdefault(label, set()).add(entry_id)
                if resolved:
                    resolved_values.setdefault(label, set()).add(entry_id)
    unique: dict[str, str] = {}
    ambiguous: set[str] = set()
    for label, ids in values.items():
        resolved_primary_ids = resolved_primary_values.get(label) or set()
        primary_ids = primary_values.get(label) or set()
        resolved_ids = resolved_values.get(label) or set()
        if len(resolved_primary_ids) == 1:
            unique[label] = next(iter(resolved_primary_ids))
        elif len(resolved_primary_ids) > 1:
            ambiguous.add(label)
        elif len(primary_ids) == 1:
            unique[label] = next(iter(primary_ids))
        elif len(primary_ids) > 1:
            ambiguous.add(label)
        elif len(resolved_ids) == 1:
            unique[label] = next(iter(resolved_ids))
        elif len(resolved_ids) > 1:
            ambiguous.add(label)
        elif len(ids) == 1:
            unique[label] = next(iter(ids))
        else:
            ambiguous.add(label)
    return unique, ambiguous


def _resolve_identity(
    label: str,
    *,
    index: dict[str, str],
    ambiguous: set[str],
) -> str:
    for key in _identity_keys(label):
        if key in index:
            return index[key]
        if key in ambiguous:
            raise CastDossierPackageError(
                "cast_dossier_identity_ambiguous",
                f"Complete Cast identity {label!r} matches more than one approved roster entry.",
            )
    raise CastDossierPackageError(
        "cast_dossier_identity_missing",
        f"Complete Cast identity {label!r} is absent from the approved roster.",
    )


def inspect_visual_identity_review(
    *,
    package: dict[str, Any],
    roster: dict[str, Any],
    roster_entities: list[dict[str, Any]],
) -> dict[str, Any]:
    """Describe unresolved visual identities without deciding for the operator."""
    index, ambiguous = _identity_index(roster)
    approved_entries = [
        {
            "id": str(entry.get("id") or "").strip(),
            "canonical_name": str(entry.get("canonical_name") or "").strip(),
            "display_name": str(
                entry.get("display_name")
                or entry.get("canonical_name")
                or entry.get("id")
                or ""
            ).strip(),
        }
        for entry in roster.get("entries") or []
        if str(entry.get("id") or "").strip()
        and entry.get("resolution_status") == "resolved"
    ]
    approved_entries.sort(
        key=lambda entry: (
            entry["display_name"].casefold(),
            entry["canonical_name"].casefold(),
            entry["id"],
        )
    )

    entities_by_seed = {
        str(entity.get("identity_seed") or "").strip(): entity
        for entity in roster_entities
        if str(entity.get("identity_seed") or "").strip()
    }
    excluded_labels: set[str] = set()
    for entity in roster.get("excluded_entities") or []:
        excluded_labels.update(_identity_keys(entity.get("name")))

    visual_keys = {
        str(item.get("character_id") or "").strip()
        for item in (package.get("visual_observations") or {}).get(
            "observations"
        ) or []
    } | {
        str(item.get("character_id") or "").strip()
        for item in (package.get("visual_dossiers") or {}).get("characters")
        or []
    }
    issues = []
    for identity_key in sorted(value for value in visual_keys if value):
        try:
            _resolve_identity(identity_key, index=index, ambiguous=ambiguous)
            continue
        except CastDossierPackageError:
            pass

        entity = entities_by_seed.get(identity_key) or {}
        labels = [
            entity.get("canonical_name"),
            entity.get("display_name"),
            *(entity.get("aliases") or []),
            *(entity.get("nicknames") or []),
            *(entity.get("titles") or []),
        ]
        candidate_ids: set[str] = set()
        for label in labels:
            try:
                candidate_ids.add(
                    _resolve_identity(label, index=index, ambiguous=ambiguous)
                )
            except CastDossierPackageError:
                continue
        suggested_id = (
            next(iter(candidate_ids)) if len(candidate_ids) == 1 else None
        )
        suggested_entry = next(
            (
                entry
                for entry in approved_entries
                if entry["id"] == suggested_id
            ),
            None,
        )
        entity_keys = set(_identity_keys(identity_key))
        for label in labels:
            entity_keys.update(_identity_keys(label))
        label = str(
            entity.get("display_name")
            or entity.get("canonical_name")
            or identity_key.replace("_", " ")
        ).strip()
        issues.append({
            "identity_key": identity_key,
            "label": label,
            "suggested_entry_id": suggested_id,
            "suggested_entry_name": (
                suggested_entry["display_name"] if suggested_entry else None
            ),
            "excluded_during_roster_review": bool(
                entity_keys & excluded_labels
            ),
        })
    return {
        "required": bool(issues),
        "issues": issues,
        "approved_entries": approved_entries,
    }


def _save_voice_dossiers(
    *,
    root: Path,
    package: dict[str, Any],
    roster: dict[str, Any],
) -> dict[str, Any]:
    voice_section = package.get("voice_dossiers") or {}
    index, ambiguous = _identity_index(roster)
    voices = []
    for dossier in voice_section.get("voices") or []:
        character_id = _resolve_identity(
            dossier["speaker"],
            index=index,
            ambiguous=ambiguous,
        )
        voices.append({
            **copy.deepcopy(dossier),
            "character_id": character_id,
        })
    document = {
        "schema_version": 1,
        "source_fingerprint": package.get("source_fingerprint"),
        "roster_fingerprint": roster["roster_fingerprint"],
        "parent_candidate_id": package["parent_candidate_id"],
        "voices": voices,
        "warnings": copy.deepcopy(voice_section.get("warnings") or []),
        "document_fingerprint": None,
    }
    document["document_fingerprint"] = fingerprint_value({
        key: value
        for key, value in document.items()
        if key != "document_fingerprint"
    })
    atomic_json_write(document, root / VOICE_DOSSIER_FILENAME)
    return document


def _import_visual_package(
    *,
    root: Path,
    package: dict[str, Any],
    source_snapshot: dict[str, Any],
    source_text: str,
    roster: dict[str, Any],
    visual_state_path: str | Path,
    identity_crosswalk: dict[str, str] | None = None,
    excluded_identity_keys: set[str] | None = None,
) -> dict[str, Any]:
    state_path = Path(visual_state_path)
    existing = load_visual_discovery_state(state_path)
    if existing is not None:
        raise CastDossierPackageError(
            "visual_work_in_progress",
            "Visual dossiers already have saved work. Finish or discard that review before importing the package visuals.",
        )
    index, ambiguous = _identity_index(roster)
    raw_visuals = package.get("visual_observations") or {}
    raw_dossiers = package.get("visual_dossiers") or {}
    crosswalk = {
        str(source).strip(): str(target).strip()
        for source, target in (identity_crosswalk or {}).items()
        if str(source).strip() and str(target).strip()
    }
    excluded = {
        str(value).strip()
        for value in (excluded_identity_keys or set())
        if str(value).strip()
    }
    overlap = sorted(set(crosswalk) & excluded)
    if overlap:
        raise CastDossierPackageError(
            "cast_dossier_identity_decision_conflict",
            "A visual identity cannot be both mapped and excluded.",
            details={"identity_keys": overlap},
        )
    available_identity_keys = {
        str(item.get("character_id") or "").strip()
        for item in raw_visuals.get("observations") or []
    } | {
        str(item.get("character_id") or "").strip()
        for item in raw_dossiers.get("characters") or []
    }
    unknown_decisions = sorted(
        (set(crosswalk) | excluded) - available_identity_keys
    )
    if unknown_decisions:
        raise CastDossierPackageError(
            "cast_dossier_identity_decision_unknown",
            "A visual identity decision does not belong to this Complete Cast package.",
            details={"identity_keys": unknown_decisions},
        )

    def resolve_visual_identity(label: str) -> str:
        return _resolve_identity(
            crosswalk.get(label, label),
            index=index,
            ambiguous=ambiguous,
        )

    prepared = []
    source_ids = []
    for item in raw_visuals.get("observations") or []:
        if item["character_id"] in excluded:
            continue
        source_ids.append(item["observation_id"])
        prepared.append({
            **{
                key: copy.deepcopy(value)
                for key, value in item.items()
                if key != "observation_id"
            },
            "character_id": resolve_visual_identity(
                item["character_id"],
            ),
        })
    passage = {
        "index": 1,
        "start_char": 0,
        "end_char": len(source_text),
        "text": source_text,
        "fingerprint": fingerprint_text(source_text),
    }
    dossier_character_ids = {
        resolve_visual_identity(
            character["character_id"],
        )
        for character in raw_dossiers.get("characters") or []
        if character["character_id"] not in excluded
    }
    character_ids = sorted(
        {item["character_id"] for item in prepared}
        | dossier_character_ids
    )
    observations, warnings = normalize_visual_passage_result(
        {
            "observations": prepared,
            "warnings": list(raw_visuals.get("warnings") or []),
        },
        passage=passage,
        source_text=source_text,
        allowed_character_ids=set(character_ids),
    )
    observation_map = {
        source_id: normalized["observation_id"]
        for source_id, normalized in zip(source_ids, observations)
    }
    generation_identity = {
        "model_name": "Ordinary ChatGPT Complete Cast dossier",
        "backend": "external_chatgpt",
        "passage_size": max(100, len(source_text)),
        "overlap_chars": 0,
        "temperature": 0.0,
        "max_tokens": 0,
        "seed": None,
        "parent_candidate_id": package["parent_candidate_id"],
        "result_fingerprint": package["parent_result_fingerprint"],
    }
    state = new_visual_discovery_state(
        source=source_snapshot,
        roster_fingerprint=roster["roster_fingerprint"],
        character_ids=character_ids,
        generation_identity=generation_identity,
        passages=[passage],
    )
    state = checkpoint_visual_passage(
        state=state,
        path=state_path,
        passage=passage,
        observations=observations,
        warnings=warnings,
    )
    reconciliation = copy.deepcopy(raw_dossiers)
    reconciliation["characters"] = [
        character
        for character in reconciliation.get("characters") or []
        if character["character_id"] not in excluded
    ]
    for character in reconciliation["characters"]:
        character["character_id"] = resolve_visual_identity(
            character["character_id"],
        )
        for facts in character.get("profile", {}).values():
            for fact in facts:
                fact["observation_ids"] = [
                    observation_map[value]
                    for value in fact.get("observation_ids") or []
                ]
        for variant in character.get("variants") or []:
            variant["observation_ids"] = [
                observation_map[value]
                for value in variant.get("observation_ids") or []
            ]
        for conflict in character.get("conflicts") or []:
            conflict["observation_ids"] = [
                observation_map[value]
                for value in conflict.get("observation_ids") or []
            ]
    state = checkpoint_visual_reconciliation(
        state=state,
        path=state_path,
        reconciliation=reconciliation,
    )
    return {
        "status": "native_review_ready",
        "destination": "visual_dossiers",
        "character_count": len(reconciliation.get("characters") or []),
        "observation_count": len(observations),
        "state_fingerprint": fingerprint_value(state),
        "identity_crosswalk": copy.deepcopy(crosswalk),
        "excluded_identity_keys": sorted(excluded),
    }


def activate_complete_cast_dossier(
    *,
    root_dir: str | Path,
    parent_candidate_id: str,
    expected_roster_fingerprint: str,
    source_snapshot: dict[str, Any],
    source_text: str,
    approved_roster_path: str | Path,
    voice_training_projects_root: str | Path,
    visual_state_path: str | Path,
    import_voice_dossiers: bool = True,
    import_visual_dossiers: bool = True,
    identity_crosswalk: dict[str, str] | None = None,
    excluded_visual_identity_keys: set[str] | None = None,
) -> dict[str, Any]:
    root = Path(root_dir)
    package = _read_package(root, parent_candidate_id)
    if package.get("source_fingerprint") != source_snapshot.get("fingerprint"):
        raise CastDossierPackageError(
            "stale_cast_dossier_source",
            "The selected source changed after the Complete Cast dossier was exported.",
        )
    try:
        roster = read_character_roster(
            approved_roster_path,
            source_text=source_text,
            expected_status="approved",
        )
    except (FileNotFoundError, CharacterRosterError) as exc:
        raise CastDossierPackageError(
            "cast_dossier_roster_required",
            "Approve the imported Character roster before activating the remaining Cast dossier sections.",
        ) from exc
    if roster["roster_fingerprint"] != expected_roster_fingerprint:
        raise CastDossierPackageError(
            "stale_cast_dossier_roster",
            "The approved Character roster changed before the remaining dossier sections were activated.",
        )
    applications = copy.deepcopy(package.get("applications") or {})
    if (
        import_voice_dossiers
        and package["selected_sections"].get("voice_personas_and_designs")
        and "voice_dossiers" not in applications
    ):
        persona_id = package["components"].get("persona_candidate_id")
        if not persona_id:
            raise CastDossierPackageError(
                "cast_dossier_voice_candidate_missing",
                "The Complete Cast package has no Voice dossier candidate.",
            )
        try:
            persona = get_structured_result_candidate(
                root_dir=root,
                candidate_id=persona_id,
            )
            if persona.get("status") == "transferred":
                transferred = persona
            else:
                transferred = transfer_structured_result_candidate(
                    root_dir=root,
                    candidate_id=persona_id,
                    expected_result_fingerprint=persona["result_fingerprint"],
                    source_snapshot=source_snapshot,
                    source_text=source_text,
                    roster_state_path=root / "character_roster_state.json",
                    roster_draft_path=root / "character_roster.draft.json",
                    approved_roster_path=approved_roster_path,
                    voice_training_projects_root=voice_training_projects_root,
                    visual_state_path=visual_state_path,
                    replace_persona_draft=False,
                    persona_catalog_decision=True,
                    replace_persona_speakers=set(),
                )
        except (
            ExternalWorkflowConflictError,
            ExternalWorkflowValidationError,
            ExternalStageTransferConflictError,
            ExternalStageTransferValidationError,
        ) as exc:
            raise CastDossierPackageError(
                getattr(exc, "code", "cast_dossier_voice_import_failed"),
                str(exc),
                details=getattr(exc, "details", None),
            ) from exc
        document = _save_voice_dossiers(
            root=root,
            package=package,
            roster=roster,
        )
        applications["voice_dossiers"] = {
            "candidate_id": persona_id,
            "application": copy.deepcopy(transferred.get("application")),
            "dossier_fingerprint": document["document_fingerprint"],
        }
    if (
        import_visual_dossiers
        and package["selected_sections"].get("visual_dossiers")
        and "visual_dossiers" not in applications
    ):
        try:
            applications["visual_dossiers"] = _import_visual_package(
                root=root,
                package=package,
                source_snapshot=source_snapshot,
                source_text=source_text,
                roster=roster,
                visual_state_path=visual_state_path,
                identity_crosswalk=identity_crosswalk,
                excluded_identity_keys=excluded_visual_identity_keys,
            )
        except (VisualDiscoveryError, KeyError) as exc:
            raise CastDossierPackageError(
                "cast_dossier_visual_import_failed",
                str(exc),
            ) from exc
    updated = {
        **package,
        "status": "complete",
        "approved_roster_fingerprint": roster["roster_fingerprint"],
        "applications": applications,
    }
    atomic_json_write(updated, _package_path(root, parent_candidate_id))
    return {
        "status": "complete",
        "package": updated,
        "routing": {
            "status": "review_ready",
            "native_destination": "cast",
            "message": (
                "The selected ChatGPT Cast dossier sections entered their native "
                "Alexandria reviews. Nothing was approved automatically beyond the "
                "explicit roster approval."
            ),
        },
    }
