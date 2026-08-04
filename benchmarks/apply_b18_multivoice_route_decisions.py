#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
import sys

if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from audio_invalidation import (  # noqa: E402
    AudioInvalidationError,
    apply_speaker_audio_dependency_change,
    undo_project_audio_invalidation,
)
from generation_state import fingerprint_value  # noqa: E402
from recurring_voice_routing import (  # noqa: E402
    routing_fingerprint,
    validate_recurring_voice_routing,
)
from voice_route_listening_decisions import (  # noqa: E402
    FILENAME as DECISION_FILENAME,
    decision_fingerprint,
    normalize_voice_route_listening_decisions,
)


DEFAULT_PROJECT = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Alexandria"
    / "Projects"
    / "original-sin--e6286665"
)
DEFAULT_DECISION = (
    ROOT / "benchmarks" / "b18_multivoice_archetype_screen_20260803_decision.json"
)
EVIDENCE_ROUND_ID = "b18_multivoice_archetype_screen_20260803_human_review"


class MultiVoiceRouteApplicationError(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MultiVoiceRouteApplicationError(f"Could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MultiVoiceRouteApplicationError(f"{path} must contain an object.")
    return value


def _route_matches(route: Mapping[str, Any], update: Mapping[str, Any]) -> bool:
    expected = {
        "backend": update["backend"],
        "identity_audio": update["identity_audio"],
        "identity_audio_sha256": update["identity_audio_sha256"],
        "identity_text": update["identity_text"],
        "performance_audio": None,
        "performance_audio_sha256": None,
        "performance_text": None,
        "control": update["control"],
        "effect_chain": update.get("effect_chain"),
        "approval_tier": update["approval_tier"],
        "production_promotion_allowed": True,
    }
    return all(route.get(key) == value for key, value in expected.items())


def prepare_application(
    *,
    project_root: str | Path,
    decision_value: Mapping[str, Any],
    verify_audio: bool = True,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    voice_config_path = root / "voice_config.json"
    current = _read_json(voice_config_path)
    route_updates = decision_value.get("route_updates")
    if not isinstance(route_updates, list) or not route_updates:
        raise MultiVoiceRouteApplicationError("Decision file has no route updates.")
    project_document = normalize_voice_route_listening_decisions(
        decision_value.get("project_decision_document")
    )
    route_evidence_round_id = str(
        decision_value.get("route_evidence_round_id")
        or project_document.get("round_id")
        or EVIDENCE_ROUND_ID
    ).strip()
    if not route_evidence_round_id:
        raise MultiVoiceRouteApplicationError(
            "Decision file has no route evidence round ID."
        )
    updated = copy.deepcopy(current)
    changed_voices: list[str] = []
    applied_updates: list[dict[str, Any]] = []
    for raw_update in route_updates:
        if not isinstance(raw_update, Mapping):
            raise MultiVoiceRouteApplicationError("Route update must be an object.")
        voice_name = str(raw_update.get("voice") or "").strip()
        route_key = str(raw_update.get("route_key") or "").strip()
        voice = updated.get(voice_name)
        if not isinstance(voice, dict):
            raise MultiVoiceRouteApplicationError(f"Voice {voice_name!r} is missing.")
        raw_policy = voice.get("responsive_backend_routing")
        if not isinstance(raw_policy, dict):
            raise MultiVoiceRouteApplicationError(
                f"Voice {voice_name!r} has no responsive routing policy."
            )
        existing_fingerprint = str(
            voice.get("responsive_backend_configuration_fingerprint") or ""
        )
        policy = validate_recurring_voice_routing(
            raw_policy,
            project_root=root,
            verify_audio=verify_audio,
        )
        calculated_fingerprint = routing_fingerprint(policy)
        if existing_fingerprint != calculated_fingerprint:
            raise MultiVoiceRouteApplicationError(
                f"Voice {voice_name!r} has a stale routing fingerprint."
            )
        route = policy["routes"].get(route_key)
        if not isinstance(route, dict):
            raise MultiVoiceRouteApplicationError(
                f"Voice route {voice_name}/{route_key} is missing."
            )
        if _route_matches(route, raw_update):
            applied_updates.append(
                {"voice": voice_name, "route_key": route_key, "status": "already_applied"}
            )
            continue
        expected_fingerprint = str(
            raw_update.get("expected_configuration_fingerprint") or ""
        )
        if calculated_fingerprint != expected_fingerprint:
            raise MultiVoiceRouteApplicationError(
                f"Voice {voice_name!r} changed after review; expected {expected_fingerprint}, "
                f"got {calculated_fingerprint}."
            )
        replacement = copy.deepcopy(route)
        replacement.update(
            {
                "backend": str(raw_update["backend"]),
                "identity_audio": str(raw_update["identity_audio"]),
                "identity_audio_sha256": str(raw_update["identity_audio_sha256"]),
                "identity_text": str(raw_update["identity_text"]),
                "performance_audio": None,
                "performance_audio_sha256": None,
                "performance_text": None,
                "control": copy.deepcopy(dict(raw_update["control"])),
                "effect_chain": (
                    str(raw_update["effect_chain"])
                    if raw_update.get("effect_chain") is not None
                    else None
                ),
                "approval_tier": str(raw_update["approval_tier"]),
                "production_promotion_allowed": True,
            }
        )
        policy["routes"][route_key] = replacement
        policy["evidence_round_id"] = route_evidence_round_id
        policy = validate_recurring_voice_routing(
            policy,
            project_root=root,
            verify_audio=verify_audio,
        )
        voice["responsive_backend_routing"] = policy
        voice["responsive_backend_configuration_fingerprint"] = routing_fingerprint(policy)
        changed_voices.append(voice_name)
        applied_updates.append(
            {"voice": voice_name, "route_key": route_key, "status": "prepared"}
        )
    decision_path = root / DECISION_FILENAME
    existing_decision = _read_json(decision_path) if decision_path.is_file() else None
    decision_changed = existing_decision != project_document
    return {
        "project_root": str(root),
        "before": current,
        "after": updated,
        "decision_document": project_document,
        "decision_path": str(decision_path),
        "decision_changed": decision_changed,
        "changed_voices": sorted(changed_voices),
        "route_updates": applied_updates,
        "already_applied": not changed_voices and not decision_changed,
        "decision_fingerprint": decision_fingerprint(project_document),
        "route_evidence_round_id": route_evidence_round_id,
    }


def apply_decisions(
    *,
    project_root: str | Path,
    decision_path: str | Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    decision_file = Path(decision_path).expanduser().resolve()
    decision_value = _read_json(decision_file)
    prepared = prepare_application(
        project_root=root,
        decision_value=decision_value,
        verify_audio=True,
    )
    if dry_run or prepared["already_applied"]:
        return {
            "status": "dry_run" if dry_run else "already_applied",
            **{key: prepared[key] for key in (
                "changed_voices",
                "route_updates",
                "decision_changed",
                "decision_fingerprint",
            )},
        }
    operation_id = "b18_multivoice_routes_" + prepared["decision_fingerprint"][:24]
    operation_record = root / "audio_invalidation_history" / operation_id / "operation.json"
    if operation_record.exists():
        raise MultiVoiceRouteApplicationError(
            "The operation receipt already exists but the current project does not match it."
        )
    changes: dict[Path, Any] = {
        root / "voice_config.json": prepared["after"],
        root / DECISION_FILENAME: prepared["decision_document"],
    }
    try:
        receipt = apply_speaker_audio_dependency_change(
            project_root=root,
            operation_id=operation_id,
            operation="b18_multivoice_route_decisions",
            at_utc=str(prepared["decision_document"]["completed_at"]),
            speakers=prepared["changed_voices"],
            reason="Blind listening changed the reviewed production Voice route.",
            changes=changes,
            dependency_kind="production_voice",
            note=(
                "Per-speaker B18 listening decisions were applied. Approved audio locks were "
                "preserved; only unlocked generated audio for changed Voices was invalidated."
            ),
            record_metadata={
                "decision_fingerprint": prepared["decision_fingerprint"],
                "decision_source": str(decision_file),
                "route_updates": prepared["route_updates"],
                "universal_backend_selected": False,
            },
        )
    except AudioInvalidationError as exc:
        raise MultiVoiceRouteApplicationError(f"{exc.code}: {exc}") from exc
    return {
        "status": "applied",
        "operation_id": operation_id,
        "changed_voices": prepared["changed_voices"],
        "route_updates": prepared["route_updates"],
        "decision_fingerprint": prepared["decision_fingerprint"],
        "affected_chunk_ids": list(receipt.get("affected_chunk_ids") or []),
        "invalidated_count": len(
            (receipt.get("audio_invalidation") or {}).get("invalidated_chunks") or []
        ),
        "undo_available": bool(receipt.get("files")),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=str(DEFAULT_PROJECT))
    parser.add_argument("--decision", default=str(DEFAULT_DECISION))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--undo")
    args = parser.parse_args()
    if args.undo:
        try:
            result = undo_project_audio_invalidation(
                project_root=args.project_root,
                operation_id=args.undo,
                undone_at_utc="2026-08-03T20:17:02.040Z",
            )
        except AudioInvalidationError as exc:
            raise MultiVoiceRouteApplicationError(f"{exc.code}: {exc}") from exc
    else:
        result = apply_decisions(
            project_root=args.project_root,
            decision_path=args.decision,
            dry_run=args.dry_run,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
