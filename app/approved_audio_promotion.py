from __future__ import annotations

import copy
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from approved_audio import approved_audio_lock_fields
from audio_artifacts import (
    install_verified_audio,
    restore_operation_audio,
    sha256_file,
)
from audio_generation_provenance import approved_import_provenance
from audio_invalidation import apply_project_audio_invalidation
from audio_takes import (
    build_take_record,
    chunk_key as audio_take_chunk_key,
    new_take_id,
    register_take,
    registry_path as audio_take_registry_path,
    take_directory,
    take_filename_base,
)
from experimental_prompt_routing import (
    PROMPT_ROUTING_SCHEMA_VERSION,
    validate_experimental_prompt_routing,
)
from generation_state import atomic_json_write, fingerprint_value
from model_registry import INSTRUCTION_CONTROLLED_ENGINE_ID
from production_prompt_routes import PRODUCTION_GENERATION_SEED, _upgrade_voice
from voice_aliases import validate_voice_aliases


PROMOTION_SCHEMA_VERSION = 1
HISTORY_DIRNAME = "approved_audio_promotion_history"
VOICE_EVIDENCE_ROOT = Path("production_prompt_routes/approved_adaptation")
IDENTITY_EVIDENCE_ROOT = Path("clone_voices/approved_adaptation")

VOICE_KEY_BY_BOOK_SPEAKER = {
    "BERNICE": "BERNICE",
    "DOCTOR": "THE DOCTOR",
    "ROZ FORRESTER": "ROZ FORRESTER",
    "CHRIS CWEJ": "CHRIS CWEJ",
    "BELTEMPEST": "BELTEMPEST",
    "TOBIAS VAUGHN": "TOBIAS VAUGHN",
    "ZEBULON PRYCE": "ZEBULON PRYCE",
    "COMPUTER": "COMPUTER",
    "RASHID": "RASHID",
    "POWERLESS FRIENDLESS": "POWERLESS FRIENDLESS",
    "BOT": "BOT",
    "UNDER-SERGEANT": "UNDER-SERGEANT",
    "EVAN CLAPLE": "EVAN CLAPLE",
}

VOICE_ALIASES_BY_TARGET = {
    "THE DOCTOR": ("DOCTOR", "SEVENTH DOCTOR", "THE SEVENTH DOCTOR"),
    "BERNICE": ("BENNY", "BERNICE SUMMERFIELD", "NARRATOR (BENNY)"),
}

REFERENCE_KEYWORDS_BY_CHUNK = {
    561: [
        "rhetorical emphasis",
        "emphatic",
        "pitch variation",
        "rolled r",
        "moral observation",
        "wry authority",
    ],
    1247: [
        "classified information",
        "computer announcement",
        "formal machine delivery",
        "official computer",
    ],
    1731: [
        "age authority",
        "ancient authority",
        "rolled r",
        "confident age",
        "authoritative",
    ],
    1939: [
        "wry disbelief",
        "skeptical",
        "sceptical",
        "dry incredulity",
        "conversational disbelief",
    ],
    2398: [
        "recognition",
        "knowing recognition",
        "grim recognition",
        "daleks",
    ],
    3209: [
        "protective resolve",
        "protective",
        "determined",
        "fierce resolve",
        "refusing loss",
    ],
    4443: [
        "reflective warning",
        "moral reflection",
        "quietly probing",
        "expressive reflection",
    ],
    5462: [
        "theatrical entrance",
        "dramatic announcement",
        "playful authority",
        "expressive announcement",
    ],
}


class ApprovedAudioPromotionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ApprovedAudioPromotionError(
            "approved_audio_file_missing",
            f"{label} does not exist: {path}",
        ) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApprovedAudioPromotionError(
            "approved_audio_json_invalid",
            f"{label} could not be read: {exc}",
        ) from exc


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ApprovedAudioPromotionError(
            "approved_audio_manifest_invalid",
            f"{label} must contain text.",
        )
    return value.strip()


def _normalized_words(value: Any) -> list[str]:
    normalized = str(value or "").casefold().replace("’", "'")
    return re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", normalized)


def _safe_name(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "").strip())
    text = re.sub(r"_+", "_", text).strip("_")
    return text.casefold() or "voice"


def _atomic_bytes(path: Path, value: bytes | None) -> None:
    if value is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def _copy_exact(source: Path, destination: Path, expected_sha256: str) -> None:
    if not source.is_file() or sha256_file(source) != expected_sha256:
        raise ApprovedAudioPromotionError(
            "approved_audio_source_hash_mismatch",
            f"Reviewed evidence is missing or changed: {source}",
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copyfile(source, temporary)
        if sha256_file(temporary) != expected_sha256:
            raise ApprovedAudioPromotionError(
                "approved_audio_copy_hash_mismatch",
                f"Reviewed evidence changed while copying: {source}",
            )
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _voice_key(book_speaker: str) -> str:
    return VOICE_KEY_BY_BOOK_SPEAKER.get(book_speaker, book_speaker)


def _select_direct_rows(
    manifest: Mapping[str, Any],
    *,
    include_restricted: bool,
) -> list[dict[str, Any]]:
    rows = manifest.get("direct_substitutions")
    if not isinstance(rows, list):
        raise ApprovedAudioPromotionError(
            "approved_audio_manifest_invalid",
            "Complete promotion manifest has no direct_substitutions list.",
        )
    selected: list[dict[str, Any]] = []
    seen_chunks: set[int] = set()
    seen_candidates: set[str] = set()
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise ApprovedAudioPromotionError(
                "approved_audio_manifest_invalid",
                "Every direct substitution must be an object.",
            )
        row = dict(raw)
        chunk_id = row.get("chunk_id")
        candidate_id = row.get("candidate_id")
        tier = row.get("direct_placement_tier")
        if not isinstance(chunk_id, int) or isinstance(chunk_id, bool):
            raise ApprovedAudioPromotionError(
                "approved_audio_manifest_invalid",
                "Every direct substitution must identify an integer chunk.",
            )
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise ApprovedAudioPromotionError(
                "approved_audio_manifest_invalid",
                f"Chunk {chunk_id} has no candidate ID.",
            )
        if tier not in {"strict_clean", "restricted_user_accepted_artifacts"}:
            raise ApprovedAudioPromotionError(
                "approved_audio_manifest_invalid",
                f"Chunk {chunk_id} has an unsupported placement tier.",
            )
        if tier == "restricted_user_accepted_artifacts" and not include_restricted:
            continue
        if chunk_id in seen_chunks or candidate_id in seen_candidates:
            raise ApprovedAudioPromotionError(
                "approved_audio_manifest_duplicate",
                "The complete promotion manifest repeats a selected chunk or candidate.",
            )
        seen_chunks.add(chunk_id)
        seen_candidates.add(candidate_id)
        selected.append(row)
    return selected


def _reviewed_direct_source(row: Mapping[str, Any]) -> tuple[Path, str]:
    proxy_path = row.get("proxy_path")
    proxy_sha = row.get("proxy_sha256")
    if isinstance(proxy_path, str) and proxy_path.strip() and isinstance(proxy_sha, str):
        return Path(proxy_path).expanduser().resolve(), proxy_sha.strip()
    return (
        Path(_text(row.get("audio_path"), "Direct audio path")).expanduser().resolve(),
        _text(row.get("audio_sha256"), "Direct audio fingerprint"),
    )


def _snapshot_paths(
    *,
    root: Path,
    operation_dir: Path,
    paths: Iterable[Path],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for raw in paths:
        path = raw.expanduser().resolve()
        try:
            relative = path.relative_to(root)
        except ValueError as exc:
            raise ApprovedAudioPromotionError(
                "approved_audio_snapshot_unsafe",
                f"Promotion attempted to snapshot a path outside the project: {path}",
            ) from exc
        if path in seen:
            continue
        seen.add(path)
        backup = operation_dir / "rollback" / relative
        existed = path.is_file()
        if existed:
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, backup)
            if sha256_file(backup) != sha256_file(path):
                raise ApprovedAudioPromotionError(
                    "approved_audio_snapshot_hash_mismatch",
                    f"Promotion snapshot verification failed for {relative}.",
                )
        records.append(
            {
                "path": relative.as_posix(),
                "existed": existed,
                "sha256": sha256_file(path) if existed else None,
                "backup_path": (
                    backup.relative_to(operation_dir).as_posix() if existed else None
                ),
            }
        )
    return records


def _restore_snapshots(
    *,
    root: Path,
    operation_dir: Path,
    records: Iterable[Mapping[str, Any]],
) -> None:
    for record in records:
        relative = Path(str(record.get("path") or ""))
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ApprovedAudioPromotionError(
                "approved_audio_rollback_unsafe",
                "Rollback receipt contains a path outside the project.",
            ) from exc
        if record.get("existed"):
            backup_value = record.get("backup_path")
            if not isinstance(backup_value, str):
                raise ApprovedAudioPromotionError(
                    "approved_audio_rollback_invalid",
                    f"Rollback backup is missing for {relative}.",
                )
            backup = (operation_dir / backup_value).resolve()
            if (
                not backup.is_file()
                or sha256_file(backup) != record.get("sha256")
            ):
                raise ApprovedAudioPromotionError(
                    "approved_audio_rollback_snapshot_invalid",
                    f"Rollback snapshot is missing or changed for {relative}.",
                )
            _atomic_bytes(target, backup.read_bytes())
        else:
            _atomic_bytes(target, None)


def _record_after_snapshots(
    *,
    root: Path,
    records: list[dict[str, Any]],
) -> None:
    for record in records:
        target = (root / Path(record["path"])).resolve()
        exists = target.is_file()
        record["after_existed"] = exists
        record["after_sha256"] = sha256_file(target) if exists else None


def _validate_after_snapshots(
    *,
    root: Path,
    records: Iterable[Mapping[str, Any]],
) -> None:
    for record in records:
        target = (root / Path(str(record.get("path") or ""))).resolve()
        expected_exists = bool(record.get("after_existed"))
        current_exists = target.is_file()
        current_sha = sha256_file(target) if current_exists else None
        if (
            current_exists != expected_exists
            or current_sha != record.get("after_sha256")
        ):
            raise ApprovedAudioPromotionError(
                "approved_audio_rollback_conflict",
                f"Cannot roll back because {record.get('path')} changed after promotion.",
            )


def _restore_invalidation_audio(
    *,
    root: Path,
    operation_id: str,
    snapshots: Iterable[Mapping[str, Any]],
    require_no_new_audio: bool,
) -> None:
    operation_dir = root / "audio_invalidation_history" / operation_id
    record_path = operation_dir / "operation.json"
    if not record_path.is_file():
        return
    record = _read_json(record_path, "Audio invalidation record")
    backups = list(record.get("audio_backups") or [])
    if require_no_new_audio:
        snapshot_paths = {
            str(item.get("path") or "") for item in snapshots
        }
        for backup in backups:
            original_path = str(backup.get("original_path") or "")
            if original_path in snapshot_paths:
                continue
            original = (root / original_path).resolve()
            if original.exists():
                raise ApprovedAudioPromotionError(
                    "approved_audio_rollback_conflict",
                    f"Cannot roll back because newer audio exists at {original_path}.",
                )
    restore_operation_audio(
        root_dir=root,
        records=backups,
        require_original_absent=False,
        consume_backups=False,
    )


def _validate_invalidation_rollback(
    *,
    root: Path,
    operation_id: str,
    snapshots: Iterable[Mapping[str, Any]],
) -> None:
    operation_dir = root / "audio_invalidation_history" / operation_id
    record_path = operation_dir / "operation.json"
    if not record_path.is_file():
        return
    record = _read_json(record_path, "Audio invalidation record")
    snapshot_paths = {str(item.get("path") or "") for item in snapshots}
    for backup in record.get("audio_backups") or []:
        original_path = str(backup.get("original_path") or "")
        backup_path = str(backup.get("backup_path") or "")
        expected_sha = str(backup.get("sha256") or "")
        original = (root / original_path).resolve()
        saved = (root / backup_path).resolve()
        for path in (original, saved):
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ApprovedAudioPromotionError(
                    "approved_audio_rollback_invalid",
                    "Audio invalidation rollback path escaped the project.",
                ) from exc
        if not saved.is_file() or sha256_file(saved) != expected_sha:
            raise ApprovedAudioPromotionError(
                "approved_audio_rollback_backup_invalid",
                f"Audio invalidation backup is missing or changed for {original_path}.",
            )
        if original_path not in snapshot_paths and original.exists():
            raise ApprovedAudioPromotionError(
                "approved_audio_rollback_conflict",
                f"Cannot roll back because newer audio exists at {original_path}.",
            )


def _rollback_partial_promotion(
    *,
    root: Path,
    operation_dir: Path,
    operation_id: str,
    snapshots: Iterable[Mapping[str, Any]],
) -> None:
    _restore_snapshots(
        root=root,
        operation_dir=operation_dir,
        records=snapshots,
    )
    _restore_invalidation_audio(
        root=root,
        operation_id=operation_id,
        snapshots=snapshots,
        require_no_new_audio=False,
    )
    shutil.rmtree(root / "audio_invalidation_history" / operation_id, ignore_errors=True)
    shutil.rmtree(operation_dir, ignore_errors=True)


def _identity_and_reference_destinations(
    *,
    root: Path,
    manifest: Mapping[str, Any],
    voice_config: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Path]]:
    identities: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    destinations: list[Path] = []
    identity_speakers: set[str] = set()
    for raw in manifest.get("identity_anchors") or []:
        if not isinstance(raw, Mapping):
            continue
        book_speaker = _text(raw.get("book_speaker"), "Identity book speaker")
        identity_speakers.add(book_speaker)
        voice_key = _voice_key(book_speaker)
        source = Path(_text(raw.get("audio_path"), "Identity audio path")).expanduser().resolve()
        audio_sha = _text(raw.get("audio_sha256"), "Identity audio fingerprint")
        suffix = source.suffix.casefold() or ".wav"
        relative = (
            IDENTITY_EVIDENCE_ROOT
            / _safe_name(voice_key)
            / f"{_text(raw.get('candidate_id'), 'Identity candidate')}{suffix}"
        )
        destinations.append(root / relative)
        identities.append(
            {
                **dict(raw),
                "voice_key": voice_key,
                "source": source,
                "audio_sha256": audio_sha,
                "relative_audio": relative.as_posix(),
                "identity_basis": "approved_identity_anchor",
            }
        )

    direct_speakers = {
        str(item.get("book_speaker") or "").strip()
        for item in manifest.get("direct_substitutions") or []
        if isinstance(item, Mapping)
        and str(item.get("book_speaker") or "").strip()
    }
    existing_identity_voices = {
        str(key)
        for key, value in voice_config.items()
        if isinstance(value, Mapping)
        and not value.get("alias_of")
        and value.get("type") == "clone"
        and str(value.get("ref_audio") or "").strip()
        and str(value.get("ref_text") or "").strip()
    }
    performance_by_speaker: dict[str, list[Mapping[str, Any]]] = {}
    for raw in manifest.get("adaptation_performance_references") or []:
        if not isinstance(raw, Mapping):
            continue
        speaker = str(raw.get("book_speaker") or "").strip()
        if speaker:
            performance_by_speaker.setdefault(speaker, []).append(raw)
    strict_direct_by_speaker: dict[str, list[Mapping[str, Any]]] = {}
    for raw in manifest.get("direct_substitutions") or []:
        if (
            not isinstance(raw, Mapping)
            or raw.get("direct_placement_tier") != "strict_clean"
        ):
            continue
        speaker = str(raw.get("book_speaker") or "").strip()
        if speaker:
            strict_direct_by_speaker.setdefault(speaker, []).append(raw)

    for book_speaker in sorted(direct_speakers):
        voice_key = _voice_key(book_speaker)
        if (
            book_speaker in identity_speakers
            or voice_key in existing_identity_voices
        ):
            continue
        performance = performance_by_speaker.get(book_speaker, [])
        if performance:
            raw = sorted(
                performance,
                key=lambda item: str(item.get("candidate_id") or ""),
            )[0]
            basis = "approved_adaptation_performance_reference"
        else:
            strict = strict_direct_by_speaker.get(book_speaker, [])
            if not strict:
                raise ApprovedAudioPromotionError(
                    "approved_audio_voice_evidence_missing",
                    f"Approved speaker {book_speaker!r} has no configured Voice, "
                    "identity anchor, performance reference, or strict-clean "
                    "direct performance that can seed one.",
                )
            raw = sorted(
                strict,
                key=lambda item: (
                    -len(_normalized_words(item.get("transcript"))),
                    int(item.get("chunk_id") or 0),
                    str(item.get("candidate_id") or ""),
                ),
            )[0]
            basis = "strict_clean_direct_performance_fallback"
        source = Path(
            _text(raw.get("audio_path"), "Fallback identity audio path")
        ).expanduser().resolve()
        audio_sha = _text(
            raw.get("audio_sha256"),
            "Fallback identity audio fingerprint",
        )
        suffix = source.suffix.casefold() or ".wav"
        relative = (
            IDENTITY_EVIDENCE_ROOT
            / _safe_name(voice_key)
            / f"{_text(raw.get('candidate_id'), 'Fallback identity candidate')}{suffix}"
        )
        destinations.append(root / relative)
        identities.append(
            {
                **dict(raw),
                "voice_key": voice_key,
                "source": source,
                "audio_sha256": audio_sha,
                "relative_audio": relative.as_posix(),
                "identity_basis": basis,
            }
        )
        identity_speakers.add(book_speaker)
    for raw in manifest.get("reference_bank_evidence") or []:
        if not isinstance(raw, Mapping):
            continue
        book_speaker = _text(raw.get("book_speaker"), "Reference book speaker")
        voice_key = _voice_key(book_speaker)
        source = Path(_text(raw.get("audio_path"), "Reference audio path")).expanduser().resolve()
        audio_sha = _text(raw.get("audio_sha256"), "Reference audio fingerprint")
        suffix = source.suffix.casefold() or ".wav"
        candidate_id = _text(raw.get("candidate_id"), "Reference candidate")
        relative = (
            VOICE_EVIDENCE_ROOT
            / "expressive"
            / _safe_name(voice_key)
            / f"{candidate_id}{suffix}"
        )
        destinations.append(root / relative)
        references.append(
            {
                **dict(raw),
                "voice_key": voice_key,
                "source": source,
                "audio_sha256": audio_sha,
                "relative_audio": relative.as_posix(),
            }
        )
    return identities, references, destinations


def _route_keywords(reference: Mapping[str, Any]) -> list[str]:
    chunk_id = reference.get("chunk_id")
    configured = REFERENCE_KEYWORDS_BY_CHUNK.get(chunk_id)
    if configured:
        return configured
    result = []
    for raw in reference.get("delivery_tags") or []:
        value = str(raw or "").casefold().replace("_", " ").strip()
        if len(value) >= 2 and value not in result:
            result.append(value)
    return result or ["approved adaptation delivery"]


def _voice_style_guidance(root: Path) -> dict[str, dict[str, Any]]:
    guidance: dict[str, dict[str, Any]] = {}
    projects_root = root / "voice_training_projects"
    if not projects_root.is_dir():
        return guidance
    for path in sorted(projects_root.glob("*/project.json")):
        try:
            project = _read_json(path, "Voice training project")
        except ApprovedAudioPromotionError:
            continue
        if not isinstance(project, Mapping):
            continue
        character = project.get("character")
        persona = project.get("desired_base_persona")
        if not isinstance(character, Mapping) or not isinstance(persona, Mapping):
            continue
        canonical = str(character.get("canonical_name") or "").strip()
        description = str(persona.get("description") or "").strip()
        if not canonical or not description:
            continue
        voice_key = _voice_key(canonical)
        guidance[voice_key] = {
            "voice_key": voice_key,
            "description": description,
            "source_path": path.relative_to(root).as_posix(),
            "approval_status": str(
                persona.get("approval_status") or "unknown"
            ).strip(),
            "source_kind": "voice_training_desired_base_persona_description",
        }
    return guidance


def _voice_policy(
    *,
    root: Path,
    voice: Mapping[str, Any],
    promotion_id: str,
    references: list[dict[str, Any]],
    approved_at_utc: str,
) -> dict[str, Any] | None:
    existing = voice.get("experimental_prompt_routing")
    if existing is None:
        routes: dict[str, Any] = {}
    else:
        routes = copy.deepcopy(
            validate_experimental_prompt_routing(
                existing,
                project_root=root,
                verify_audio=True,
            )["routes"]
        )
    for reference in references:
        candidate_id = _text(reference.get("candidate_id"), "Reference candidate")
        route_key = f"approved_adaptation_{candidate_id}"
        evidence = {
            "status": "production_opt_in",
            "prompt_role": "validated_bank",
            "reference_key": candidate_id,
            "validated_bank_clip_id": candidate_id,
            "ref_audio": reference["relative_audio"],
            "ref_audio_sha256": reference["audio_sha256"],
            "ref_text": _text(reference.get("transcript"), "Reference transcript"),
            "production_promotion_allowed": True,
            "instruction_keywords": _route_keywords(reference),
            "approval_basis": "operator_approved_after_listening",
            "operator_approved_at_utc": approved_at_utc,
        }
        current = routes.get(route_key)
        if current is not None and current != evidence:
            raise ApprovedAudioPromotionError(
                "approved_audio_route_conflict",
                f"Voice route {route_key} already exists with different evidence.",
            )
        routes[route_key] = evidence
    if not routes:
        return None
    return validate_experimental_prompt_routing(
        {
            "schema_version": PROMPT_ROUTING_SCHEMA_VERSION,
            "enabled": True,
            "scope": "production_opt_in",
            "general_routing": "instruction_keywords",
            "production_promotion_allowed": True,
            "evidence_round_id": promotion_id,
            "routes": routes,
        },
        project_root=root,
        verify_audio=True,
    )


def _promote_voice_evidence(
    *,
    root: Path,
    manifest: Mapping[str, Any],
    voice_config: Mapping[str, Any],
    promotion_id: str,
    approved_at_utc: str,
) -> tuple[dict[str, Any], dict[str, Any], list[Path], set[str]]:
    identities, references, destinations = _identity_and_reference_destinations(
        root=root,
        manifest=manifest,
        voice_config=voice_config,
    )
    for item in identities + references:
        _copy_exact(item["source"], root / item["relative_audio"], item["audio_sha256"])
    identity_by_voice = {item["voice_key"]: item for item in identities}
    references_by_voice: dict[str, list[dict[str, Any]]] = {}
    for reference in references:
        references_by_voice.setdefault(reference["voice_key"], []).append(reference)

    config = copy.deepcopy(dict(voice_config))
    style_guidance = _voice_style_guidance(root)
    changed_voices = set(identity_by_voice) | set(references_by_voice)
    for voice_key in sorted(changed_voices):
        existing = config.get(voice_key)
        if existing is None:
            source_voice: dict[str, Any] = {
                "type": "clone",
                "voice": "Ryan",
                "character_style": "",
                "default_style": "",
                "seed": str(PRODUCTION_GENERATION_SEED),
                "clone_backend": INSTRUCTION_CONTROLLED_ENGINE_ID,
                "instruction_clone_temperature": 0.75,
                "instruction_clone_top_k": 50,
                "instruction_clone_top_p": 0.95,
                "instruction_clone_repetition_penalty": 1.5,
                "instruction_clone_max_tokens": 2000,
            }
        elif not isinstance(existing, Mapping) or existing.get("alias_of"):
            raise ApprovedAudioPromotionError(
                "approved_audio_voice_conflict",
                f"Voice {voice_key!r} is not an independent configurable Voice.",
            )
        else:
            source_voice = copy.deepcopy(dict(existing))
        guidance = style_guidance.get(voice_key)
        if guidance is not None and not str(
            source_voice.get("character_style")
            or source_voice.get("default_style")
            or ""
        ).strip():
            source_voice["character_style"] = guidance["description"]
        identity = identity_by_voice.get(voice_key)
        if identity is not None:
            source_voice.update(
                {
                    "type": "clone",
                    "ref_audio": identity["relative_audio"],
                    "ref_text": _text(identity.get("transcript"), "Identity transcript"),
                    "clone_backend": INSTRUCTION_CONTROLLED_ENGINE_ID,
                }
            )
        if source_voice.get("type") != "clone":
            raise ApprovedAudioPromotionError(
                "approved_audio_voice_type_invalid",
                f"Voice {voice_key!r} must be a clone before adaptation evidence can refine it.",
            )
        policy = _voice_policy(
            root=root,
            voice=source_voice,
            promotion_id=promotion_id,
            references=references_by_voice.get(voice_key, []),
            approved_at_utc=approved_at_utc,
        )
        config[voice_key] = _upgrade_voice(
            root=root,
            voice_name=voice_key,
            source=source_voice,
            policy=policy,
        )
        if guidance is not None:
            config[voice_key].update(
                {
                    "approved_adaptation_style_source": guidance["source_kind"],
                    "approved_adaptation_style_source_path": guidance[
                        "source_path"
                    ],
                    "approved_adaptation_style_approval_status": guidance[
                        "approval_status"
                    ],
                }
            )
    validate_voice_aliases(config)

    unresolved_voice_keys = sorted(
        {
            _voice_key(str(item.get("book_speaker") or "").strip())
            for item in manifest.get("direct_substitutions") or []
            if isinstance(item, Mapping)
            and (
                not isinstance(
                    config.get(
                        _voice_key(
                            str(item.get("book_speaker") or "").strip()
                        )
                    ),
                    Mapping,
                )
                or config[
                    _voice_key(str(item.get("book_speaker") or "").strip())
                ].get("type")
                != "clone"
                or not str(
                    config[
                        _voice_key(str(item.get("book_speaker") or "").strip())
                    ].get("ref_audio")
                    or ""
                ).strip()
                or not str(
                    config[
                        _voice_key(str(item.get("book_speaker") or "").strip())
                    ].get("ref_text")
                    or ""
                ).strip()
            )
        }
    )
    if unresolved_voice_keys:
        raise ApprovedAudioPromotionError(
            "approved_audio_voice_configuration_incomplete",
            "Approved adaptation speakers still lack usable clone Voices: "
            + ", ".join(unresolved_voice_keys),
        )

    profile = {
        "schema_version": 1,
        "promotion_id": promotion_id,
        "approved_at_utc": approved_at_utc,
        "identity_anchors": [
            {
                key: value
                for key, value in item.items()
                if key not in {"source"}
            }
            for item in identities
        ],
        "expressive_references": [
            {
                key: value
                for key, value in item.items()
                if key not in {"source"}
            }
            for item in references
        ],
        "adaptation_performance_references": copy.deepcopy(
            list(manifest.get("adaptation_performance_references") or [])
        ),
        "voice_style_guidance": [
            copy.deepcopy(style_guidance[key])
            for key in sorted(changed_voices)
            if key in style_guidance
        ],
        "direct_alignment_evidence": [
            copy.deepcopy(dict(item))
            for item in manifest.get("direct_substitutions") or []
            if isinstance(item, Mapping)
            and item.get("direct_placement_tier") == "strict_clean"
        ],
        "restricted_direct_candidates_excluded": [
            item.get("candidate_id")
            for item in manifest.get("restricted_direct_substitutions") or []
            if isinstance(item, Mapping)
        ],
    }
    profile["profile_fingerprint"] = fingerprint_value(profile)
    profile_path = root / VOICE_EVIDENCE_ROOT / "profile.json"
    profile_relative = profile_path.relative_to(root).as_posix()
    alignment_counts: dict[str, int] = {}
    for item in profile["direct_alignment_evidence"]:
        voice_key = _voice_key(str(item.get("book_speaker") or "").strip())
        alignment_counts[voice_key] = alignment_counts.get(voice_key, 0) + 1
    for voice_key in sorted(changed_voices):
        voice = config.get(voice_key)
        if not isinstance(voice, dict):
            continue
        identity = identity_by_voice.get(voice_key)
        voice.update(
            {
                "approved_adaptation_profile_path": profile_relative,
                "approved_adaptation_profile_fingerprint": profile[
                    "profile_fingerprint"
                ],
                "approved_adaptation_identity_candidate_id": (
                    identity.get("candidate_id") if identity else None
                ),
                "approved_adaptation_identity_basis": (
                    identity.get("identity_basis") if identity else None
                ),
                "approved_adaptation_alignment_count": alignment_counts.get(
                    voice_key,
                    0,
                ),
                "approved_adaptation_expressive_reference_count": len(
                    references_by_voice.get(voice_key, [])
                ),
            }
        )
    atomic_json_write(profile, profile_path)
    destinations.append(profile_path)
    aliases = {
        alias
        for target in changed_voices
        for alias in VOICE_ALIASES_BY_TARGET.get(target, ())
    }
    return config, profile, destinations, changed_voices | aliases


def promote_approved_adaptation_audio(
    *,
    project_root: str | Path,
    manifest_path: str | Path,
    confirm_installation: bool,
    include_restricted: bool = False,
    promote_voice_evidence: bool = True,
    installed_at_utc: str | None = None,
) -> dict[str, Any]:
    if confirm_installation is not True:
        raise ApprovedAudioPromotionError(
            "approved_audio_confirmation_required",
            "Approved adaptation audio promotion requires explicit confirmation.",
        )
    root = Path(project_root).expanduser().resolve()
    manifest_source = Path(manifest_path).expanduser().resolve()
    manifest = _read_json(manifest_source, "Complete promotion manifest")
    if (
        not isinstance(manifest, Mapping)
        or manifest.get("schema_version") not in {1, 2}
    ):
        raise ApprovedAudioPromotionError(
            "approved_audio_manifest_invalid",
            "Complete promotion manifest schema is unsupported.",
        )
    promotion_id = _text(manifest.get("promotion_id"), "Promotion ID")
    manifest_project = manifest.get("project_root")
    if manifest.get("schema_version") == 2:
        if not isinstance(manifest_project, str) or not manifest_project.strip():
            raise ApprovedAudioPromotionError(
                "approved_audio_manifest_invalid",
                "Complete promotion manifest does not identify its project.",
            )
        if Path(manifest_project).expanduser().resolve() != root:
            raise ApprovedAudioPromotionError(
                "approved_audio_manifest_project_mismatch",
                "Complete promotion manifest belongs to another Alexandria project.",
            )
    if manifest.get("strict_overlap_expansion_status") != "completed_and_fully_dispositioned":
        raise ApprovedAudioPromotionError(
            "approved_audio_manifest_incomplete",
            "Strict overlap research is not fully dispositioned.",
        )
    chunks_path = root / "chunks.json"
    voice_config_path = root / "voice_config.json"
    audio_validity_path = root / "audio_validity.json"
    chunks = _read_json(chunks_path, "Project chunks")
    voice_config = _read_json(voice_config_path, "Voice configuration")
    if not isinstance(chunks, list) or not isinstance(voice_config, Mapping):
        raise ApprovedAudioPromotionError(
            "approved_audio_project_invalid",
            "Project chunks or Voice configuration has an unsupported shape.",
        )
    protected_hashes = manifest.get("protected_project_hashes_before")
    if manifest.get("schema_version") == 2:
        if not isinstance(protected_hashes, Mapping):
            raise ApprovedAudioPromotionError(
                "approved_audio_manifest_invalid",
                "Complete promotion manifest has no protected project hashes.",
            )
        current_hashes = {
            "chunks.json": sha256_file(chunks_path),
            "voice_config.json": sha256_file(voice_config_path),
        }
        mismatches = {
            name: {
                "expected": protected_hashes.get(name),
                "actual": actual,
            }
            for name, actual in current_hashes.items()
            if protected_hashes.get(name) != actual
        }
        if mismatches:
            raise ApprovedAudioPromotionError(
                "approved_audio_project_changed",
                "Protected Original Sin project state changed after review: "
                + ", ".join(sorted(mismatches)),
            )
    direct_rows = manifest.get("direct_substitutions")
    if not isinstance(direct_rows, list):
        raise ApprovedAudioPromotionError(
            "approved_audio_manifest_invalid",
            "Complete promotion manifest has no direct substitutions.",
        )
    recorded_direct_count = manifest.get("direct_substitution_count")
    if (
        recorded_direct_count is not None
        and recorded_direct_count != len(direct_rows)
    ):
        raise ApprovedAudioPromotionError(
            "approved_audio_manifest_count_mismatch",
            "Complete promotion manifest direct-substitution count is stale.",
        )
    selected = _select_direct_rows(
        manifest,
        include_restricted=include_restricted,
    )
    by_id = {
        item.get("id", index): (index, item)
        for index, item in enumerate(chunks)
        if isinstance(item, dict)
    }
    installed_at = installed_at_utc or utc_timestamp()
    operation_id = "approved_audio_" + fingerprint_value(
        {
            "promotion_id": promotion_id,
            "project_root": str(root),
            "installed_at_utc": installed_at,
            "include_restricted": include_restricted,
            "promote_voice_evidence": promote_voice_evidence,
        }
    )[:24]
    operation_dir = root / HISTORY_DIRNAME / operation_id
    if operation_dir.exists():
        raise ApprovedAudioPromotionError(
            "approved_audio_operation_exists",
            f"Promotion operation already exists: {operation_id}",
        )
    project_manifest = operation_dir / "manifest.json"

    identities, references, voice_destinations = _identity_and_reference_destinations(
        root=root,
        manifest=manifest,
        voice_config=voice_config,
    )
    del identities, references
    profile_path = root / VOICE_EVIDENCE_ROOT / "profile.json"
    snapshot_paths: list[Path] = [
        chunks_path,
        voice_config_path,
        audio_validity_path,
        audio_take_registry_path(root),
        profile_path,
        *voice_destinations,
    ]
    direct_preflight: list[dict[str, Any]] = []
    for row in selected:
        chunk_id = int(row["chunk_id"])
        located = by_id.get(chunk_id)
        if located is None:
            raise ApprovedAudioPromotionError(
                "approved_audio_chunk_missing",
                f"Approved chunk {chunk_id} does not exist in the project.",
            )
        index, chunk = located
        if _normalized_words(chunk.get("text")) != _normalized_words(row.get("transcript")):
            raise ApprovedAudioPromotionError(
                "approved_audio_text_mismatch",
                f"Approved transcript no longer matches chunk {chunk_id}.",
            )
        if str(chunk.get("speaker") or "").casefold() != str(
            row.get("book_speaker") or ""
        ).casefold():
            raise ApprovedAudioPromotionError(
                "approved_audio_speaker_mismatch",
                f"Approved speaker no longer matches chunk {chunk_id}.",
            )
        source, source_sha = _reviewed_direct_source(row)
        if not source.is_file() or sha256_file(source) != source_sha:
            raise ApprovedAudioPromotionError(
                "approved_audio_source_hash_mismatch",
                f"Reviewed source is missing or changed for chunk {chunk_id}.",
            )
        take_id = new_take_id(kind="raw")
        chunk_key_value = audio_take_chunk_key(chunk, index)
        take_dir = take_directory(root, chunk_key_value)
        filename_base = take_filename_base(take_id)
        suffix = source.suffix.casefold()
        canonical = take_dir / f"{filename_base}{suffix}"
        alternate = take_dir / (
            f"{filename_base}.wav" if suffix == ".mp3" else f"{filename_base}.mp3"
        )
        snapshot_paths.extend((canonical, alternate))
        previous_path = chunk.get("audio_path") or chunk.get("stale_audio_path")
        if isinstance(previous_path, str) and previous_path.strip():
            snapshot_paths.append(root / previous_path)
        direct_preflight.append(
            {
                "row": row,
                "index": index,
                "chunk": chunk,
                "source": source,
                "source_sha256": source_sha,
                "filename_base": filename_base,
                "take_id": take_id,
                "chunk_key": chunk_key_value,
                "take_dir": take_dir,
            }
        )

    try:
        operation_dir.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(manifest_source, project_manifest)
        snapshots = _snapshot_paths(
            root=root,
            operation_dir=operation_dir,
            paths=snapshot_paths,
        )
    except Exception:
        shutil.rmtree(operation_dir, ignore_errors=True)
        raise
    before_hashes = {
        "chunks.json": sha256_file(chunks_path),
        "voice_config.json": sha256_file(voice_config_path),
        "audio_validity.json": (
            sha256_file(audio_validity_path) if audio_validity_path.is_file() else None
        ),
    }
    dependency_before = {
        path.expanduser().resolve(): (
            path.expanduser().resolve().read_bytes()
            if path.expanduser().resolve().is_file()
            else None
        )
        for path in [voice_config_path, profile_path, *voice_destinations]
    }
    installed_rows: list[dict[str, Any]] = []
    voice_profile: dict[str, Any] | None = None
    changed_speakers: set[str] = set()
    try:
        effective_voice_config = copy.deepcopy(dict(voice_config))
        if promote_voice_evidence:
            (
                effective_voice_config,
                voice_profile,
                _created_voice_paths,
                changed_speakers,
            ) = _promote_voice_evidence(
                root=root,
                manifest=manifest,
                voice_config=effective_voice_config,
                promotion_id=promotion_id,
                approved_at_utc=installed_at,
            )
            atomic_json_write(effective_voice_config, voice_config_path)
            apply_project_audio_invalidation(
                project_root=root,
                operation_id=operation_id,
                operation="approved_adaptation_voice_evidence_promotion",
                at_utc=installed_at,
                speakers=changed_speakers,
                reason=(
                    "Production Voice identity and expressive evidence changed to "
                    "operator-approved adaptation performances."
                ),
                dependency_before=dependency_before,
            )
            chunks = _read_json(chunks_path, "Invalidated project chunks")
            by_id = {
                item.get("id", index): (index, item)
                for index, item in enumerate(chunks)
                if isinstance(item, dict)
            }

        for preflight in direct_preflight:
            row = preflight["row"]
            chunk_id = int(row["chunk_id"])
            index, chunk = by_id[chunk_id]
            lock_fields = approved_audio_lock_fields(
                chunk=chunk,
                promotion_id=promotion_id,
                candidate_id=_text(row.get("candidate_id"), "Candidate ID"),
                source_round_id=(
                    str(row.get("source_round_id")).strip()
                    if row.get("source_round_id")
                    else None
                ),
                direct_placement_tier=_text(
                    row.get("direct_placement_tier"),
                    "Direct placement tier",
                ),
                source_audio_path=str(preflight["source"]),
                source_audio_sha256=preflight["source_sha256"],
                manifest_path=project_manifest.relative_to(root).as_posix(),
                installed_at_utc=installed_at,
                reference_bank_eligible=bool(row.get("reference_bank_eligible")),
            )
            binding = lock_fields["approved_audio_lock"]["binding_fingerprint"]
            artifact = install_verified_audio(
                root_dir=root,
                voicelines_dir=preflight["take_dir"],
                source_audio_path=preflight["source"],
                filename_base=preflight["filename_base"],
                binding_fingerprint=binding,
                expected_sha256=preflight["source_sha256"],
                previous_audio_path=(
                    chunk.get("audio_path") or chunk.get("stale_audio_path")
                ),
                text=str(chunk.get("text") or ""),
            )
            generation_provenance = approved_import_provenance(
                promotion_id=promotion_id,
                candidate_id=_text(row.get("candidate_id"), "Candidate ID"),
                source_round_id=(
                    str(row.get("source_round_id")).strip()
                    if row.get("source_round_id")
                    else None
                ),
                direct_placement_tier=_text(
                    row.get("direct_placement_tier"),
                    "Direct placement tier",
                ),
            )
            chunk_fields = {
                "status": "done",
                "error": None,
                "error_code": None,
                **artifact,
                **lock_fields,
                "generation_provenance": generation_provenance,
                "generated_at_utc": installed_at,
                "audio_research_only": False,
                "audio_production_prompt_approved": True,
                "production_promotion_allowed": True,
                "review_required": False,
                "review_flag": False,
                "listening_required": False,
                "listening_state": "approved",
            }
            take_record = build_take_record(
                take_id=preflight["take_id"],
                chunk_key_value=preflight["chunk_key"],
                chunk_index=index,
                kind="raw",
                source_take_id=None,
                root_take_id=preflight["take_id"],
                artifact={
                    "relative_path": artifact["audio_path"],
                    "sha256": artifact["audio_sha256"],
                    "size_bytes": artifact["audio_size_bytes"],
                    "duration_ms": artifact["audio_duration_ms"],
                    "format": artifact["audio_format"],
                    "sample_rate": artifact.get("audio_sample_rate"),
                    "sample_count": artifact.get("audio_sample_count"),
                    "channels": artifact.get("audio_channels"),
                    "installed_sample_width": artifact.get(
                        "audio_sample_width"
                    ),
                },
                authored={
                    "text": str(chunk.get("text") or ""),
                    "text_fingerprint": fingerprint_value(
                        str(chunk.get("text") or "")
                    ),
                    "speaker": str(chunk.get("speaker") or ""),
                    "resolved_speaker": str(chunk.get("speaker") or ""),
                    "direction": str(chunk.get("instruct") or ""),
                    "effective_direction": str(chunk.get("instruct") or ""),
                    "pause_after_ms": chunk.get("pause_after"),
                },
                voice={
                    "resolved_speaker": str(chunk.get("speaker") or ""),
                    "configuration": copy.deepcopy(
                        effective_voice_config.get(chunk.get("speaker"), {})
                    ),
                    "binding_fingerprint": binding,
                    "approved_audio_lock": copy.deepcopy(
                        lock_fields["approved_audio_lock"]
                    ),
                    "approved_audio_origin": copy.deepcopy(
                        lock_fields["approved_audio_origin"]
                    ),
                },
                generation={
                    "audio_fingerprint": binding,
                    "request_id": operation_id,
                    "request_fingerprint": fingerprint_value(
                        {
                            "operation_id": operation_id,
                            "chunk_id": chunk_id,
                            "candidate_id": row.get("candidate_id"),
                        }
                    ),
                    "provenance": generation_provenance,
                    "source_audio_path": str(preflight["source"]),
                    "source_audio_sha256": preflight["source_sha256"],
                    "chunk_audio_fields": copy.deepcopy(chunk_fields),
                },
                synthesis={
                    "source_kind": "approved_adaptation_performance",
                    "direct_placement_tier": row.get(
                        "direct_placement_tier"
                    ),
                    "segment_count": 1,
                    "original_sample_count": artifact.get(
                        "audio_sample_count"
                    ),
                    "sample_rate": artifact.get("audio_sample_rate"),
                },
                review={
                    "state": "approved",
                    "review_required": False,
                    "listening_required": False,
                    "promotion_id": promotion_id,
                    "candidate_id": row.get("candidate_id"),
                },
                created_at_utc=installed_at,
            )
            registered_take, take_registry = register_take(
                root,
                chunks=chunks,
                record=take_record,
            )
            chunk_fields.update(
                {
                    "current_take_id": registered_take["take_id"],
                    "take_record_fingerprint": registered_take[
                        "record_fingerprint"
                    ],
                    "take_registry_fingerprint": take_registry[
                        "registry_fingerprint"
                    ],
                    "stale_audio_path": None,
                }
            )
            chunk.update(chunk_fields)
            installed_rows.append(
                {
                    "chunk_id": chunk_id,
                    "index": index,
                    "speaker": chunk.get("speaker"),
                    "candidate_id": row.get("candidate_id"),
                    "direct_placement_tier": row.get("direct_placement_tier"),
                    "audio_path": artifact["audio_path"],
                    "audio_sha256": artifact["audio_sha256"],
                    "binding_fingerprint": binding,
                    "take_id": registered_take["take_id"],
                }
            )
        atomic_json_write(chunks, chunks_path)
    except Exception:
        _rollback_partial_promotion(
            root=root,
            operation_dir=operation_dir,
            operation_id=operation_id,
            snapshots=snapshots,
        )
        raise

    try:
        _record_after_snapshots(root=root, records=snapshots)
        after_hashes = {
            "chunks.json": sha256_file(chunks_path),
            "voice_config.json": sha256_file(voice_config_path),
            "audio_validity.json": (
                sha256_file(audio_validity_path)
                if audio_validity_path.is_file()
                else None
            ),
        }
        invalidation_dir = root / "audio_invalidation_history" / operation_id
        receipt = {
            "schema_version": PROMOTION_SCHEMA_VERSION,
            "operation_id": operation_id,
            "promotion_id": promotion_id,
            "status": "promoted",
            "project_root": str(root),
            "manifest_path": project_manifest.relative_to(root).as_posix(),
            "installed_at_utc": installed_at,
            "include_restricted": bool(include_restricted),
            "promote_voice_evidence": bool(promote_voice_evidence),
            "strict_clean_count": sum(
                row["direct_placement_tier"] == "strict_clean"
                for row in installed_rows
            ),
            "restricted_count": sum(
                row["direct_placement_tier"]
                == "restricted_user_accepted_artifacts"
                for row in installed_rows
            ),
            "installed_chunk_count": len(installed_rows),
            "installed_chunks": installed_rows,
            "changed_voice_keys": sorted(changed_speakers),
            "voice_evidence_profile": voice_profile,
            "before_hashes": before_hashes,
            "after_hashes": after_hashes,
            "rollback_snapshots": snapshots,
            "audio_invalidation_operation": (
                f"audio_invalidation_history/{operation_id}/operation.json"
                if invalidation_dir.exists()
                else None
            ),
            "production_changes": True,
        }
        receipt["receipt_fingerprint"] = fingerprint_value(receipt)
        atomic_json_write(receipt, operation_dir / "receipt.json")
        return receipt
    except Exception:
        _rollback_partial_promotion(
            root=root,
            operation_dir=operation_dir,
            operation_id=operation_id,
            snapshots=snapshots,
        )
        raise


def rollback_approved_adaptation_audio(
    *,
    project_root: str | Path,
    receipt_path: str | Path,
    confirm_rollback: bool,
) -> dict[str, Any]:
    if confirm_rollback is not True:
        raise ApprovedAudioPromotionError(
            "approved_audio_rollback_confirmation_required",
            "Approved audio rollback requires explicit confirmation.",
        )
    root = Path(project_root).expanduser().resolve()
    receipt_target = Path(receipt_path).expanduser().resolve()
    receipt = _read_json(receipt_target, "Approved audio promotion receipt")
    if not isinstance(receipt, Mapping) or receipt.get("schema_version") != 1:
        raise ApprovedAudioPromotionError(
            "approved_audio_rollback_invalid",
            "Approved audio promotion receipt schema is unsupported.",
        )
    if Path(str(receipt.get("project_root") or "")).expanduser().resolve() != root:
        raise ApprovedAudioPromotionError(
            "approved_audio_rollback_project_mismatch",
            "Promotion receipt belongs to another project.",
        )
    if receipt.get("status") != "promoted":
        raise ApprovedAudioPromotionError(
            "approved_audio_rollback_unavailable",
            "Approved audio promotion is not in a rollback-eligible state.",
        )
    expected_receipt_fingerprint = fingerprint_value(
        {
            key: value
            for key, value in receipt.items()
            if key != "receipt_fingerprint"
        }
    )
    if receipt.get("receipt_fingerprint") != expected_receipt_fingerprint:
        raise ApprovedAudioPromotionError(
            "approved_audio_rollback_receipt_invalid",
            "Approved audio promotion receipt fingerprint is invalid.",
        )
    operation_dir = receipt_target.parent
    snapshots = list(receipt.get("rollback_snapshots") or [])
    _validate_after_snapshots(root=root, records=snapshots)
    operation_id = _text(receipt.get("operation_id"), "Operation ID")
    _validate_invalidation_rollback(
        root=root,
        operation_id=operation_id,
        snapshots=snapshots,
    )
    _restore_snapshots(
        root=root,
        operation_dir=operation_dir,
        records=snapshots,
    )
    _restore_invalidation_audio(
        root=root,
        operation_id=operation_id,
        snapshots=snapshots,
        require_no_new_audio=False,
    )
    shutil.rmtree(root / "audio_invalidation_history" / operation_id, ignore_errors=True)
    rolled_back = {
        **dict(receipt),
        "status": "rolled_back",
        "rolled_back_at_utc": utc_timestamp(),
        "production_changes": False,
    }
    rolled_back["receipt_fingerprint"] = fingerprint_value(
        {key: value for key, value in rolled_back.items() if key != "receipt_fingerprint"}
    )
    atomic_json_write(rolled_back, receipt_target)
    return rolled_back
