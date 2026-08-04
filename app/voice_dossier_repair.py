from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from generation_state import atomic_json_write, fingerprint_value


HISTORY_DIRNAME = "voice_dossier_repair_history"
REQUIRED_VOICE_FIELDS = (
    "character_id",
    "speaker",
    "persona_summary",
    "designed_voice_description",
    "vocal_age_impression",
    "pitch",
    "weight_and_resonance",
    "texture_and_timbre",
    "accent_and_language",
    "cadence_and_rhythm",
    "energy_range",
    "emotional_range",
    "casting_guidance",
)
STRUCTURED_VOICE_FIELDS = (
    "vocal_age_impression",
    "pitch",
    "weight_and_resonance",
    "texture_and_timbre",
    "accent_and_language",
    "cadence_and_rhythm",
    "energy_range",
    "emotional_range",
    "casting_guidance",
)


class VoiceDossierRepairError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VoiceDossierRepairError(f"{label} could not be read: {exc}") from exc


def _atomic_bytes(path: Path, value: bytes | None) -> None:
    if value is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _require_sha(path: Path, expected: str, label: str) -> None:
    actual = sha256_file(path)
    if actual != str(expected or ""):
        raise VoiceDossierRepairError(
            f"{label} changed; expected {expected}, got {actual}."
        )


def _validate_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise VoiceDossierRepairError("Voice dossier repair manifest is invalid.")
    for key in (
        "project",
        "expected_cast_voice_dossiers_sha256",
        "expected_voice_config_sha256",
        "expected_source_fingerprint",
        "expected_roster_fingerprint",
    ):
        if not str(value.get(key) or "").strip():
            raise VoiceDossierRepairError(f"Voice dossier repair manifest lacks {key}.")
    voices = value.get("voices")
    if not isinstance(voices, list) or not voices:
        raise VoiceDossierRepairError("Voice dossier repair manifest has no Voices.")
    if (
        "allow_saved_dossier_updates" in value
        and not isinstance(value.get("allow_saved_dossier_updates"), bool)
    ):
        raise VoiceDossierRepairError(
            "Voice dossier repair allow_saved_dossier_updates must be boolean."
        )
    markers = [
        _normalize(marker)
        for marker in value.get("generic_markers") or []
        if _normalize(marker)
    ]
    identities: set[tuple[str, str]] = set()
    descriptions: set[str] = set()
    for index, voice in enumerate(voices):
        if not isinstance(voice, dict):
            raise VoiceDossierRepairError(
                f"Voice dossier repair entry {index} must be an object."
            )
        for key in REQUIRED_VOICE_FIELDS:
            if not str(voice.get(key) or "").strip():
                raise VoiceDossierRepairError(
                    f"Voice dossier repair entry {index} lacks {key}."
                )
        identity = (
            str(voice["character_id"]).strip(),
            _normalize(voice["speaker"]),
        )
        if identity in identities:
            raise VoiceDossierRepairError(
                f"Voice dossier repair repeats identity {voice['speaker']}."
            )
        identities.add(identity)
        description = _normalize(voice["designed_voice_description"])
        if len(description) < 120:
            raise VoiceDossierRepairError(
                f"Voice dossier repair description is too thin for {voice['speaker']}."
            )
        if description in descriptions:
            raise VoiceDossierRepairError(
                f"Voice dossier repair repeats a description for {voice['speaker']}."
            )
        descriptions.add(description)
        combined = _normalize(" ".join(
            str(item)
            for key, item in voice.items()
            if key != "uncertainties" and isinstance(item, str)
        ))
        hits = [marker for marker in markers if marker in combined]
        if hits:
            raise VoiceDossierRepairError(
                f"Voice dossier repair retains generic template language for "
                f"{voice['speaker']}: {hits}."
            )
        uncertainties = voice.get("uncertainties") or []
        if not isinstance(uncertainties, list) or not all(
            isinstance(item, str) and item.strip() for item in uncertainties
        ):
            raise VoiceDossierRepairError(
                f"Voice dossier repair uncertainties are invalid for {voice['speaker']}."
            )
    return copy.deepcopy(value)


def load_voice_dossier_repair_manifest(path: str | Path) -> dict[str, Any]:
    return _validate_manifest(
        _read_json(Path(path).expanduser().resolve(), "Voice dossier repair manifest")
    )


def _saved_identity_keys(voice_config: Mapping[str, Any]) -> set[str]:
    keys: set[str] = set()
    for key, raw in voice_config.items():
        if not isinstance(raw, Mapping):
            continue
        keys.add(_normalize(key))
        for field in (
            "character_id",
            "canonical_name",
            "display_name",
            "script_label",
            "speaker",
        ):
            normalized = _normalize(raw.get(field))
            if normalized:
                keys.add(normalized)
    return keys


def _structured(value: str) -> dict[str, Any]:
    return {
        "value": str(value).strip(),
        "basis": "casting_recommendation",
        "evidence_quotes": [],
    }


def build_repaired_voice_dossiers(
    *,
    document: Mapping[str, Any],
    voice_config: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_value = _validate_manifest(dict(manifest))
    if document.get("schema_version") != 1:
        raise VoiceDossierRepairError("Cast Voice dossier schema is unsupported.")
    if document.get("source_fingerprint") != manifest_value["expected_source_fingerprint"]:
        raise VoiceDossierRepairError("Cast Voice dossiers belong to a different source.")
    if document.get("roster_fingerprint") != manifest_value["expected_roster_fingerprint"]:
        raise VoiceDossierRepairError("Cast Voice dossiers belong to a different roster.")
    voices = document.get("voices")
    if not isinstance(voices, list):
        raise VoiceDossierRepairError("Cast Voice dossiers have no Voice list.")

    saved_keys = _saved_identity_keys(voice_config)
    allow_saved_dossier_updates = bool(
        manifest_value.get("allow_saved_dossier_updates")
    )
    target_specs = {
        str(item["character_id"]): item for item in manifest_value["voices"]
    }
    for spec in target_specs.values():
        if not allow_saved_dossier_updates and (
            _normalize(spec["speaker"]) in saved_keys
            or _normalize(spec["character_id"]) in saved_keys
        ):
            raise VoiceDossierRepairError(
                f"Refusing to rewrite saved production Voice dossier: {spec['speaker']}."
            )

    updated = copy.deepcopy(dict(document))
    updated_voices = updated["voices"]
    found: set[str] = set()
    changed: list[dict[str, Any]] = []
    for index, dossier in enumerate(updated_voices):
        if not isinstance(dossier, dict):
            raise VoiceDossierRepairError(
                f"Cast Voice dossier entry {index} is invalid."
            )
        character_id = str(dossier.get("character_id") or "").strip()
        spec = target_specs.get(character_id)
        if spec is None:
            continue
        if _normalize(dossier.get("speaker")) != _normalize(spec["speaker"]):
            raise VoiceDossierRepairError(
                f"Character identity changed for {spec['speaker']}."
            )
        found.add(character_id)
        before_description = str(dossier.get("designed_voice_description") or "")
        dossier["persona_summary"] = str(spec["persona_summary"]).strip()
        dossier["designed_voice_description"] = str(
            spec["designed_voice_description"]
        ).strip()
        for field in STRUCTURED_VOICE_FIELDS:
            dossier[field] = _structured(str(spec[field]))
        dossier["uncertainties"] = copy.deepcopy(spec.get("uncertainties") or [])
        changed.append(
            {
                "character_id": character_id,
                "speaker": str(spec["speaker"]),
                "before_description_sha256": hashlib.sha256(
                    before_description.encode("utf-8")
                ).hexdigest(),
                "after_description_sha256": hashlib.sha256(
                    dossier["designed_voice_description"].encode("utf-8")
                ).hexdigest(),
            }
        )
    missing = sorted(set(target_specs) - found)
    if missing:
        raise VoiceDossierRepairError(
            f"Cast Voice dossiers are missing target character IDs: {missing}."
        )
    updated["document_fingerprint"] = fingerprint_value(
        {
            key: value
            for key, value in updated.items()
            if key != "document_fingerprint"
        }
    )
    return updated, {
        "target_count": len(changed),
        "targets": sorted(changed, key=lambda item: item["speaker"]),
        "document_fingerprint": updated["document_fingerprint"],
    }


def _operation_id(manifest: Mapping[str, Any]) -> str:
    return "voice_dossier_repair_" + fingerprint_value(manifest)[:24]


def apply_voice_dossier_repair(
    *,
    project_root: str | Path,
    manifest_path: str | Path,
    confirm_repair: bool,
    applied_at_utc: str | None = None,
) -> dict[str, Any]:
    if confirm_repair is not True:
        raise VoiceDossierRepairError(
            "Voice dossier repair requires explicit confirmation."
        )
    root = Path(project_root).expanduser().resolve()
    manifest = load_voice_dossier_repair_manifest(manifest_path)
    if root.name != str(manifest["project"]):
        raise VoiceDossierRepairError(
            f"Voice dossier repair targets {manifest['project']}, not {root.name}."
        )
    dossier_path = root / "cast_voice_dossiers.json"
    voice_config_path = root / "voice_config.json"
    operation_id = _operation_id(manifest)
    operation_dir = root / HISTORY_DIRNAME / operation_id
    receipt_path = operation_dir / "receipt.json"
    if receipt_path.is_file():
        existing = _read_json(receipt_path, "Voice dossier repair receipt")
        if (
            isinstance(existing, dict)
            and existing.get("status") == "installed"
            and dossier_path.is_file()
            and sha256_file(dossier_path) == existing.get("after_sha256")
        ):
            if not existing.get("allow_saved_dossier_updates"):
                _require_sha(
                    voice_config_path,
                    existing["voice_config_sha256"],
                    "Voice configuration",
                )
            return {**existing, "status": "already_applied"}

    _require_sha(
        voice_config_path,
        manifest["expected_voice_config_sha256"],
        "Voice configuration",
    )

    _require_sha(
        dossier_path,
        manifest["expected_cast_voice_dossiers_sha256"],
        "Cast Voice dossiers",
    )
    before_bytes = dossier_path.read_bytes()
    before_document = _read_json(dossier_path, "Cast Voice dossiers")
    voice_config = _read_json(voice_config_path, "Voice configuration")
    if not isinstance(before_document, dict) or not isinstance(voice_config, dict):
        raise VoiceDossierRepairError("Project Voice state is invalid.")
    updated, summary = build_repaired_voice_dossiers(
        document=before_document,
        voice_config=voice_config,
        manifest=manifest,
    )
    operation_dir.mkdir(parents=True, exist_ok=True)
    before_snapshot = operation_dir / "before" / "cast_voice_dossiers.json"
    if before_snapshot.exists():
        if before_snapshot.read_bytes() != before_bytes:
            raise VoiceDossierRepairError(
                "Existing Voice dossier repair rollback snapshot does not match."
            )
    else:
        _atomic_bytes(before_snapshot, before_bytes)
    try:
        atomic_json_write(updated, dossier_path)
        after_sha = sha256_file(dossier_path)
        receipt = {
            "schema_version": 1,
            "status": "installed",
            "operation_id": operation_id,
            "applied_at_utc": applied_at_utc or utc_now(),
            "manifest_fingerprint": fingerprint_value(manifest),
            "project": manifest["project"],
            "target_count": summary["target_count"],
            "targets": summary["targets"],
            "before_sha256": manifest["expected_cast_voice_dossiers_sha256"],
            "after_sha256": after_sha,
            "before_document_fingerprint": before_document.get("document_fingerprint"),
            "after_document_fingerprint": summary["document_fingerprint"],
            "voice_config_sha256": manifest["expected_voice_config_sha256"],
            "allow_saved_dossier_updates": bool(
                manifest.get("allow_saved_dossier_updates")
            ),
            "saved_voice_config_unchanged": (
                sha256_file(voice_config_path)
                == manifest["expected_voice_config_sha256"]
            ),
            "rollback_snapshot": str(before_snapshot.relative_to(root)),
            "rollback_available": True,
        }
        receipt["receipt_fingerprint"] = fingerprint_value(receipt)
        atomic_json_write(receipt, receipt_path)
        return receipt
    except Exception:
        _atomic_bytes(dossier_path, before_bytes)
        receipt_path.unlink(missing_ok=True)
        raise


def inspect_voice_dossier_repair(
    *,
    project_root: str | Path,
    manifest_path: str | Path,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    manifest = load_voice_dossier_repair_manifest(manifest_path)
    operation_id = _operation_id(manifest)
    receipt_path = root / HISTORY_DIRNAME / operation_id / "receipt.json"
    try:
        receipt = _read_json(receipt_path, "Voice dossier repair receipt")
        if not isinstance(receipt, dict) or receipt.get("status") != "installed":
            raise VoiceDossierRepairError("Voice dossier repair is not installed.")
        _require_sha(root / "cast_voice_dossiers.json", receipt["after_sha256"], "Cast Voice dossiers")
        if not receipt.get("allow_saved_dossier_updates"):
            _require_sha(root / "voice_config.json", receipt["voice_config_sha256"], "Voice configuration")
        return {
            "ready": True,
            "operation_id": operation_id,
            "target_count": receipt["target_count"],
            "after_sha256": receipt["after_sha256"],
            "receipt_fingerprint": receipt["receipt_fingerprint"],
            "error": None,
        }
    except Exception as exc:
        return {
            "ready": False,
            "operation_id": operation_id,
            "target_count": 0,
            "after_sha256": None,
            "receipt_fingerprint": None,
            "error": str(exc),
        }


def rollback_voice_dossier_repair(
    *,
    project_root: str | Path,
    operation_id: str,
    confirm_rollback: bool,
    rolled_back_at_utc: str | None = None,
) -> dict[str, Any]:
    if confirm_rollback is not True:
        raise VoiceDossierRepairError(
            "Voice dossier repair rollback requires explicit confirmation."
        )
    root = Path(project_root).expanduser().resolve()
    operation_dir = root / HISTORY_DIRNAME / str(operation_id)
    receipt_path = operation_dir / "receipt.json"
    receipt = _read_json(receipt_path, "Voice dossier repair receipt")
    if not isinstance(receipt, dict) or receipt.get("status") != "installed":
        raise VoiceDossierRepairError("Voice dossier repair is not available for rollback.")
    dossier_path = root / "cast_voice_dossiers.json"
    _require_sha(dossier_path, receipt["after_sha256"], "Cast Voice dossiers")
    if not receipt.get("allow_saved_dossier_updates"):
        _require_sha(root / "voice_config.json", receipt["voice_config_sha256"], "Voice configuration")
    snapshot = root / str(receipt["rollback_snapshot"])
    if not snapshot.is_file():
        raise VoiceDossierRepairError("Voice dossier repair rollback snapshot is missing.")
    before_bytes = snapshot.read_bytes()
    if hashlib.sha256(before_bytes).hexdigest() != receipt["before_sha256"]:
        raise VoiceDossierRepairError("Voice dossier repair rollback snapshot changed.")
    _atomic_bytes(dossier_path, before_bytes)
    updated = {
        **receipt,
        "status": "rolled_back",
        "rolled_back_at_utc": rolled_back_at_utc or utc_now(),
        "rollback_available": False,
    }
    updated["receipt_fingerprint"] = fingerprint_value(
        {
            key: value
            for key, value in updated.items()
            if key != "receipt_fingerprint"
        }
    )
    atomic_json_write(updated, receipt_path)
    return updated
