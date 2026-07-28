from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from generation_state import atomic_json_write, fingerprint_value


PLAN_FILENAME = "roster_import_enrichment.json"
PLAN_SCHEMA_VERSION = 1


class RosterEnrichmentError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self)}


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized or None


def plan_path(root_dir: str | Path) -> Path:
    return Path(root_dir).expanduser().resolve() / PLAN_FILENAME


def _normalized_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    candidate_id = _text(value.get("candidate_id"))
    draft_fingerprint = _text(value.get("draft_fingerprint"))
    if not candidate_id or not draft_fingerprint:
        raise RosterEnrichmentError(
            "roster_enrichment_plan_invalid",
            "Roster enrichment plan is missing its candidate or roster draft fingerprint.",
        )
    options = value.get("options")
    if not isinstance(options, Mapping):
        raise RosterEnrichmentError(
            "roster_enrichment_plan_invalid",
            "Roster enrichment plan options must contain an object.",
        )
    state = _text(value.get("state")) or "pending_roster_approval"
    if state not in {
        "pending_roster_approval",
        "ready",
        "running",
        "complete",
        "partial",
        "failed",
    }:
        raise RosterEnrichmentError(
            "roster_enrichment_plan_invalid",
            f"Unsupported roster enrichment state: {state}.",
        )
    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "draft_fingerprint": draft_fingerprint,
        "relationships_included": True,
        "options": {
            "create_designed_voice_profiles": bool(
                options.get("create_designed_voice_profiles", True)
            ),
            "discover_visual_details": bool(
                options.get("discover_visual_details", True)
            ),
        },
        "state": state,
        "created_at_utc": _text(value.get("created_at_utc")),
        "started_at_utc": _text(value.get("started_at_utc")),
        "finished_at_utc": _text(value.get("finished_at_utc")),
        "approved_roster_fingerprint": _text(
            value.get("approved_roster_fingerprint")
        ),
        "steps": dict(value.get("steps") or {}),
        "error": _text(value.get("error")),
    }
    plan["plan_fingerprint"] = fingerprint_value(
        {
            key: item
            for key, item in plan.items()
            if key != "plan_fingerprint"
        }
    )
    return plan


def save_plan(
    *,
    root_dir: str | Path,
    candidate_id: str,
    draft_fingerprint: str,
    create_designed_voice_profiles: bool,
    discover_visual_details: bool,
    created_at_utc: str,
) -> dict[str, Any]:
    plan = _normalized_plan(
        {
            "candidate_id": candidate_id,
            "draft_fingerprint": draft_fingerprint,
            "relationships_included": True,
            "options": {
                "create_designed_voice_profiles": create_designed_voice_profiles,
                "discover_visual_details": discover_visual_details,
            },
            "state": "pending_roster_approval",
            "created_at_utc": created_at_utc,
            "steps": {
                "relationships": {
                    "state": "included_in_roster_draft",
                    "required": True,
                },
                "designed_voice_profiles": {
                    "state": (
                        "pending_roster_approval"
                        if create_designed_voice_profiles
                        else "not_selected"
                    )
                },
                "visual_details": {
                    "state": (
                        "pending_roster_approval"
                        if discover_visual_details
                        else "not_selected"
                    )
                },
            },
        }
    )
    atomic_json_write(plan, plan_path(root_dir))
    return plan


def load_plan(root_dir: str | Path) -> dict[str, Any] | None:
    path = plan_path(root_dir)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RosterEnrichmentError(
            "roster_enrichment_plan_invalid",
            f"Roster enrichment plan could not be read: {exc}",
        ) from exc
    if not isinstance(value, Mapping):
        raise RosterEnrichmentError(
            "roster_enrichment_plan_invalid",
            "Roster enrichment plan must contain an object.",
        )
    return _normalized_plan(value)


def update_plan(
    *,
    root_dir: str | Path,
    changes: Mapping[str, Any],
) -> dict[str, Any]:
    current = load_plan(root_dir)
    if current is None:
        raise RosterEnrichmentError(
            "roster_enrichment_plan_missing",
            "No pending roster enrichment plan exists.",
        )
    merged = {
        **current,
        **dict(changes),
        "options": {
            **dict(current.get("options") or {}),
            **dict(changes.get("options") or {}),
        },
        "steps": {
            **dict(current.get("steps") or {}),
            **dict(changes.get("steps") or {}),
        },
    }
    normalized = _normalized_plan(merged)
    atomic_json_write(normalized, plan_path(root_dir))
    return normalized


def clear_plan(root_dir: str | Path) -> bool:
    path = plan_path(root_dir)
    if not path.exists():
        return False
    path.unlink()
    return True
