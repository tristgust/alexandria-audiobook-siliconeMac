from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from audio_invalidation import apply_project_audio_invalidation
from generation_state import atomic_json_write, fingerprint_value
from voice_training_projects import (
    read_voice_training_project,
    voice_training_project_path,
)


BANK_SCHEMA_VERSION = 2
BANK_FILENAME = "reference_bank.json"
BANK_AUDIO_DIRNAME = "reference_bank_audio"
COMPARISON_DIRNAME = "reference_bank_comparisons"
_BANK_LOCK = threading.RLock()
_CHARACTER_ID_RE = re.compile(r"character_[0-9a-f]{20}")
_STYLE_KEY_RE = re.compile(r"[a-z][a-z0-9_]{1,39}")
_REFERENCE_ID_RE = re.compile(r"reference_[0-9a-f]{24}")


def _atomic_restore(path: Path, value: bytes | None) -> None:
    if value is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".restore.tmp")
    try:
        temporary.write_bytes(value)
        temporary.replace(path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


STYLE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "neutral": {
        "label": "Neutral",
        "keywords": (),
        "instruction": "Natural neutral delivery with stable identity and clear pacing.",
    },
    "warmth": {
        "label": "Warmth",
        "keywords": ("warm", "kind", "gentle", "fond", "tender", "reassuring"),
        "instruction": "Warm, open, reassuring delivery without changing the voice identity.",
    },
    "anger": {
        "label": "Anger",
        "keywords": ("angry", "anger", "furious", "irritated", "rage", "hostile", "sharp"),
        "instruction": "Controlled anger with firm pressure and stable voice identity.",
    },
    "fear": {
        "label": "Fear",
        "keywords": ("fear", "afraid", "terrified", "panic", "nervous", "uneasy", "frightened"),
        "instruction": "Audible fear and tension while preserving pronunciation and identity.",
    },
    "urgency": {
        "label": "Urgency",
        "keywords": ("urgent", "urgency", "hurry", "quickly", "immediate", "desperate", "rushed"),
        "instruction": "Urgent forward momentum with clear words and controlled speed.",
    },
    "fatigue": {
        "label": "Fatigue",
        "keywords": ("tired", "fatigue", "weary", "exhausted", "drained", "sleepy"),
        "instruction": "Tired, low-energy delivery without slurring or identity drift.",
    },
    "grief": {
        "label": "Grief",
        "keywords": ("grief", "grieving", "sad", "sorrow", "mourning", "heartbroken", "tearful"),
        "instruction": "Restrained grief with emotional weight and intelligible phrasing.",
    },
    "amusement": {
        "label": "Amusement",
        "keywords": ("amused", "amusement", "funny", "wry", "playful", "laugh", "humor", "humour"),
        "instruction": "Dry or light amusement with the same core character identity.",
    },
    "authority": {
        "label": "Authority",
        "keywords": ("authoritative", "authority", "command", "commanding", "firm", "decisive", "stern"),
        "instruction": "Calm authority, decisive phrasing, and stable controlled projection.",
    },
    "softness": {
        "label": "Softness",
        "keywords": ("soft", "quiet", "whisper", "hushed", "low", "intimate", "delicate"),
        "instruction": "Soft, close delivery that remains audible and clearly articulated.",
    },
    "excitement": {
        "label": "Excitement",
        "keywords": ("excited", "excitement", "thrilled", "eager", "animated", "delighted", "elated"),
        "instruction": "Heightened excitement with energetic rhythm and stable timbre.",
    },
}

REQUIRED_STYLE_KEYS = tuple(STYLE_DEFINITIONS)
COMPARISON_MODES = (
    "reference_bank_clone",
    "single_reference_clone",
    "direct_voice_design",
)


class ExpressiveReferenceBankError(RuntimeError):
    pass


class ExpressiveReferenceBankValidationError(ExpressiveReferenceBankError):
    pass


class ExpressiveReferenceBankConflictError(ExpressiveReferenceBankError):
    pass


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ExpressiveReferenceBankValidationError(f"{label} must be text.")
    normalized = value.strip()
    if not normalized and not allow_empty:
        raise ExpressiveReferenceBankValidationError(f"{label} must not be empty.")
    return normalized


def _sha(value: Any, label: str) -> str:
    text = _text(value, label)
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise ExpressiveReferenceBankValidationError(
            f"{label} must be a lowercase SHA-256 fingerprint."
        )
    return text


def _character_id(value: Any) -> str:
    text = _text(value, "Character ID")
    if not _CHARACTER_ID_RE.fullmatch(text):
        raise ExpressiveReferenceBankValidationError("Character ID is invalid.")
    return text


def _style_key(value: Any) -> str:
    text = _text(value, "Style key").casefold().replace("-", "_").replace(" ", "_")
    if not _STYLE_KEY_RE.fullmatch(text):
        raise ExpressiveReferenceBankValidationError("Style key is invalid.")
    return text


def _relative_path(value: Any, label: str) -> str:
    text = _text(value, label)
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise ExpressiveReferenceBankValidationError(
            f"{label} must be a safe project-relative path."
        )
    return path.as_posix()


def _validate_identity_source(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExpressiveReferenceBankValidationError(
            "Reference bank.identity_source must be an object."
        )
    expected = {
        "kind",
        "source_clip_id",
        "source_file_id",
        "exact_transcript",
        "audio_path",
        "audio_sha256",
        "permission_basis",
        "selected_reference_fingerprint",
    }
    if set(value) != expected:
        raise ExpressiveReferenceBankValidationError(
            "Reference bank.identity_source has unexpected fields."
        )
    kind = _text(value["kind"], "Identity source.kind")
    if kind != "owned_recording":
        raise ExpressiveReferenceBankValidationError(
            "Reference bank identity must come from an owned recording."
        )
    return {
        "kind": kind,
        "source_clip_id": _text(
            value["source_clip_id"],
            "Identity source.source_clip_id",
        ),
        "source_file_id": _text(
            value["source_file_id"],
            "Identity source.source_file_id",
        ),
        "exact_transcript": _text(
            value["exact_transcript"],
            "Identity source.exact_transcript",
        ),
        "audio_path": _relative_path(
            value["audio_path"],
            "Identity source.audio_path",
        ),
        "audio_sha256": _sha(
            value["audio_sha256"],
            "Identity source.audio_sha256",
        ),
        "permission_basis": _text(
            value["permission_basis"],
            "Identity source.permission_basis",
        ),
        "selected_reference_fingerprint": _sha(
            value["selected_reference_fingerprint"],
            "Identity source.selected_reference_fingerprint",
        ),
    }


def reference_bank_path(projects_root: str | Path, character_id: str) -> Path:
    safe = _character_id(character_id)
    return Path(projects_root) / safe / BANK_FILENAME


def reference_audio_directory(projects_root: str | Path, character_id: str) -> Path:
    safe = _character_id(character_id)
    return Path(projects_root) / safe / BANK_AUDIO_DIRNAME


def comparison_directory(projects_root: str | Path, character_id: str) -> Path:
    safe = _character_id(character_id)
    return Path(projects_root) / safe / COMPARISON_DIRNAME


def compute_bank_fingerprint(bank: dict[str, Any]) -> str:
    return fingerprint_value(
        {key: value for key, value in bank.items() if key != "bank_fingerprint"}
    )


def _validate_review(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExpressiveReferenceBankValidationError(f"{label} must be an object.")
    expected = {
        "source_identity_retention_passed",
        "identity_drift_passed",
        "emotion_match_passed",
        "pronunciation_passed",
        "pace_passed",
        "approved",
        "notes",
        "reviewed_at_utc",
    }
    if set(value) != expected:
        raise ExpressiveReferenceBankValidationError(f"{label} has unexpected fields.")
    for key in (
        "source_identity_retention_passed",
        "identity_drift_passed",
        "emotion_match_passed",
        "pronunciation_passed",
        "pace_passed",
        "approved",
    ):
        if not isinstance(value[key], bool):
            raise ExpressiveReferenceBankValidationError(f"{label}.{key} must be boolean.")
    notes = _text(value["notes"], f"{label}.notes", allow_empty=True)
    reviewed_at = value["reviewed_at_utc"]
    if reviewed_at is not None:
        reviewed_at = _text(reviewed_at, f"{label}.reviewed_at_utc")
    passed = all(
        value[key]
        for key in (
            "source_identity_retention_passed",
            "identity_drift_passed",
            "emotion_match_passed",
            "pronunciation_passed",
            "pace_passed",
        )
    )
    if value["approved"] != passed or (value["approved"] and reviewed_at is None):
        raise ExpressiveReferenceBankValidationError(
            f"{label}.approved must reflect all quality checks and review time."
        )
    return {
        **{key: bool(value[key]) for key in expected if key not in {"notes", "reviewed_at_utc"}},
        "notes": notes,
        "reviewed_at_utc": reviewed_at,
    }


def _validate_reference(value: Any, index: int) -> dict[str, Any]:
    label = f"Reference {index}"
    if not isinstance(value, dict):
        raise ExpressiveReferenceBankValidationError(f"{label} must be an object.")
    expected = {
        "reference_id",
        "style_key",
        "label",
        "instruction",
        "reference_text",
        "seed",
        "audio_path",
        "audio_sha256",
        "generation_backend",
        "model",
        "source_kind",
        "source_clip_id",
        "generated_at_utc",
        "review",
    }
    if set(value) != expected:
        raise ExpressiveReferenceBankValidationError(f"{label} has unexpected fields.")
    reference_id = _text(value["reference_id"], f"{label}.reference_id")
    if not _REFERENCE_ID_RE.fullmatch(reference_id):
        raise ExpressiveReferenceBankValidationError(f"{label}.reference_id is invalid.")
    seed = value["seed"]
    if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool) or seed < 0):
        raise ExpressiveReferenceBankValidationError(f"{label}.seed must be non-negative or null.")
    source_kind = _text(value["source_kind"], f"{label}.source_kind")
    if source_kind not in {
        "owned_recording",
        "controlled_clone_experimental",
        "qwen_icl_instruction_experimental",
    }:
        raise ExpressiveReferenceBankValidationError(
            f"{label}.source_kind is unsupported."
        )
    source_clip_id = _text(
        value["source_clip_id"],
        f"{label}.source_clip_id",
    )
    return {
        "reference_id": reference_id,
        "style_key": _style_key(value["style_key"]),
        "label": _text(value["label"], f"{label}.label"),
        "instruction": _text(value["instruction"], f"{label}.instruction"),
        "reference_text": _text(value["reference_text"], f"{label}.reference_text"),
        "seed": seed,
        "audio_path": _relative_path(value["audio_path"], f"{label}.audio_path"),
        "audio_sha256": _sha(value["audio_sha256"], f"{label}.audio_sha256"),
        "generation_backend": _text(value["generation_backend"], f"{label}.generation_backend"),
        "model": _text(value["model"], f"{label}.model"),
        "source_kind": source_kind,
        "source_clip_id": source_clip_id,
        "generated_at_utc": _text(value["generated_at_utc"], f"{label}.generated_at_utc"),
        "review": _validate_review(value["review"], f"{label}.review"),
    }


def _validate_comparison(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExpressiveReferenceBankValidationError("Comparison must be an object.")
    expected = {
        "status",
        "test_lines",
        "outputs",
        "source_identity_retention_passed",
        "identity_consistency_passed",
        "emotion_match_passed",
        "pronunciation_passed",
        "pace_passed",
        "long_form_drift_passed",
        "notes",
        "reviewed_at_utc",
    }
    if set(value) != expected:
        raise ExpressiveReferenceBankValidationError("Comparison has unexpected fields.")
    status = _text(value["status"], "Comparison.status")
    if status not in {"not_started", "generated", "approved", "rejected"}:
        raise ExpressiveReferenceBankValidationError("Comparison.status is unsupported.")
    lines = value["test_lines"]
    outputs = value["outputs"]
    if not isinstance(lines, list) or not all(isinstance(item, str) and item.strip() for item in lines):
        raise ExpressiveReferenceBankValidationError("Comparison.test_lines must be text entries.")
    if not isinstance(outputs, list):
        raise ExpressiveReferenceBankValidationError("Comparison.outputs must be a list.")
    normalized_outputs = []
    for index, item in enumerate(outputs):
        if not isinstance(item, dict) or set(item) != {
            "mode",
            "style_key",
            "line_index",
            "audio_path",
            "audio_sha256",
            "identity_role",
        }:
            raise ExpressiveReferenceBankValidationError(
                f"Comparison output {index} is invalid."
            )
        mode = _text(item["mode"], f"Comparison output {index}.mode")
        if mode not in COMPARISON_MODES:
            raise ExpressiveReferenceBankValidationError(
                f"Comparison output {index}.mode is unsupported."
            )
        line_index = item["line_index"]
        if not isinstance(line_index, int) or isinstance(line_index, bool) or not (0 <= line_index < len(lines)):
            raise ExpressiveReferenceBankValidationError(
                f"Comparison output {index}.line_index is invalid."
            )
        style = item["style_key"]
        if style is not None:
            style = _style_key(style)
        identity_role = _text(
            item["identity_role"],
            f"Comparison output {index}.identity_role",
        )
        expected_role = (
            "external_experimental_comparator"
            if mode == "direct_voice_design"
            else "owned_identity_candidate"
        )
        if identity_role != expected_role:
            raise ExpressiveReferenceBankValidationError(
                f"Comparison output {index}.identity_role is invalid for {mode}."
            )
        normalized_outputs.append(
            {
                "mode": mode,
                "style_key": style,
                "line_index": line_index,
                "audio_path": _relative_path(item["audio_path"], f"Comparison output {index}.audio_path"),
                "audio_sha256": _sha(item["audio_sha256"], f"Comparison output {index}.audio_sha256"),
                "identity_role": identity_role,
            }
        )
    checks = {}
    for key in (
        "source_identity_retention_passed",
        "identity_consistency_passed",
        "emotion_match_passed",
        "pronunciation_passed",
        "pace_passed",
        "long_form_drift_passed",
    ):
        if value[key] is not None and not isinstance(value[key], bool):
            raise ExpressiveReferenceBankValidationError(f"Comparison.{key} must be boolean or null.")
        checks[key] = value[key]
    notes = _text(value["notes"], "Comparison.notes", allow_empty=True)
    reviewed_at = value["reviewed_at_utc"]
    if reviewed_at is not None:
        reviewed_at = _text(reviewed_at, "Comparison.reviewed_at_utc")
    passed = all(checks[key] is True for key in checks)
    if status == "approved" and (not passed or reviewed_at is None):
        raise ExpressiveReferenceBankValidationError(
            "Approved comparison requires every quality check and review time."
        )
    if status == "rejected" and reviewed_at is None:
        raise ExpressiveReferenceBankValidationError(
            "Rejected comparison requires review time."
        )
    return {
        "status": status,
        "test_lines": [item.strip() for item in lines],
        "outputs": normalized_outputs,
        **checks,
        "notes": notes,
        "reviewed_at_utc": reviewed_at,
    }


def validate_reference_bank(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExpressiveReferenceBankValidationError("Reference bank must be an object.")
    expected = {
        "schema_version",
        "status",
        "character",
        "persona_fingerprint",
        "identity_source",
        "identity_seed",
        "required_style_keys",
        "neutral_style_key",
        "references",
        "comparison",
        "production_assignment",
        "created_at_utc",
        "updated_at_utc",
        "bank_fingerprint",
    }
    if set(value) != expected or value["schema_version"] != BANK_SCHEMA_VERSION:
        raise ExpressiveReferenceBankValidationError("Reference bank schema is unsupported.")
    status = _text(value["status"], "Reference bank.status")
    if status not in {"draft", "approved"}:
        raise ExpressiveReferenceBankValidationError("Reference bank.status is unsupported.")
    character = value["character"]
    if not isinstance(character, dict) or set(character) != {
        "id",
        "canonical_name",
        "display_name",
        "source_fingerprint",
        "roster_fingerprint",
    }:
        raise ExpressiveReferenceBankValidationError("Reference bank.character is invalid.")
    normalized_character = {
        "id": _character_id(character["id"]),
        "canonical_name": _text(character["canonical_name"], "Reference bank.character.canonical_name"),
        "display_name": _text(character["display_name"], "Reference bank.character.display_name"),
        "source_fingerprint": _sha(character["source_fingerprint"], "Reference bank.character.source_fingerprint"),
        "roster_fingerprint": _sha(character["roster_fingerprint"], "Reference bank.character.roster_fingerprint"),
    }
    identity_seed = value["identity_seed"]
    if identity_seed is not None and (
        not isinstance(identity_seed, int)
        or isinstance(identity_seed, bool)
        or identity_seed < 0
    ):
        raise ExpressiveReferenceBankValidationError(
            "Reference bank.identity_seed must be non-negative or null."
        )
    required = value["required_style_keys"]
    if not isinstance(required, list) or not required:
        raise ExpressiveReferenceBankValidationError("Reference bank.required_style_keys is invalid.")
    required_styles = [_style_key(item) for item in required]
    if len(required_styles) != len(set(required_styles)):
        raise ExpressiveReferenceBankValidationError("Required style keys must be unique.")
    neutral = _style_key(value["neutral_style_key"])
    if neutral not in required_styles:
        raise ExpressiveReferenceBankValidationError("Neutral style must be required.")
    references = value["references"]
    if not isinstance(references, list):
        raise ExpressiveReferenceBankValidationError("Reference bank.references must be a list.")
    normalized_references = [_validate_reference(item, index) for index, item in enumerate(references)]
    reference_ids = [item["reference_id"] for item in normalized_references]
    if len(reference_ids) != len(set(reference_ids)):
        raise ExpressiveReferenceBankValidationError("Reference IDs must be unique.")
    style_keys = [item["style_key"] for item in normalized_references]
    if len(style_keys) != len(set(style_keys)):
        raise ExpressiveReferenceBankValidationError("Only one reference per style is allowed.")
    assignment = value["production_assignment"]
    if not isinstance(assignment, dict) or set(assignment) != {"status", "voice_name", "assigned_at_utc"}:
        raise ExpressiveReferenceBankValidationError("Production assignment is invalid.")
    assignment_status = _text(assignment["status"], "Production assignment.status")
    if assignment_status not in {"unassigned", "assigned"}:
        raise ExpressiveReferenceBankValidationError("Production assignment.status is unsupported.")
    voice_name = assignment["voice_name"]
    assigned_at = assignment["assigned_at_utc"]
    if assignment_status == "unassigned":
        if voice_name is not None or assigned_at is not None:
            raise ExpressiveReferenceBankValidationError("Unassigned bank cannot carry assignment metadata.")
    else:
        voice_name = _text(voice_name, "Production assignment.voice_name")
        assigned_at = _text(assigned_at, "Production assignment.assigned_at_utc")
    normalized = {
        "schema_version": BANK_SCHEMA_VERSION,
        "status": status,
        "character": normalized_character,
        "persona_fingerprint": _sha(value["persona_fingerprint"], "Reference bank.persona_fingerprint"),
        "identity_source": _validate_identity_source(
            value["identity_source"]
        ),
        "identity_seed": identity_seed,
        "required_style_keys": required_styles,
        "neutral_style_key": neutral,
        "references": normalized_references,
        "comparison": _validate_comparison(value["comparison"]),
        "production_assignment": {
            "status": assignment_status,
            "voice_name": voice_name,
            "assigned_at_utc": assigned_at,
        },
        "created_at_utc": _text(value["created_at_utc"], "Reference bank.created_at_utc"),
        "updated_at_utc": _text(value["updated_at_utc"], "Reference bank.updated_at_utc"),
        "bank_fingerprint": _sha(value["bank_fingerprint"], "Reference bank.bank_fingerprint"),
    }
    expected_fingerprint = compute_bank_fingerprint(normalized)
    if normalized["bank_fingerprint"] != expected_fingerprint:
        raise ExpressiveReferenceBankValidationError("Reference bank fingerprint is stale.")
    approved_by_style = {
        item["style_key"]: item
        for item in normalized_references
        if item["review"]["approved"]
    }
    if status == "approved":
        missing = [key for key in required_styles if key not in approved_by_style]
        if missing:
            raise ExpressiveReferenceBankValidationError(
                "Approved reference bank is missing approved styles: " + ", ".join(missing)
            )
        if normalized["comparison"]["status"] != "approved":
            raise ExpressiveReferenceBankValidationError(
                "Approved reference bank requires approved comparison review."
            )
    if assignment_status == "assigned" and status != "approved":
        raise ExpressiveReferenceBankValidationError("Only an approved bank may be assigned.")
    return normalized


def read_reference_bank(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExpressiveReferenceBankValidationError(
            f"Reference bank could not be read: {exc}"
        ) from exc
    return validate_reference_bank(value)


def save_reference_bank(bank: dict[str, Any], path: str | Path) -> dict[str, Any]:
    normalized = validate_reference_bank(bank)
    atomic_json_write(normalized, path)
    return normalized


def _included_owned_clip(
    project: dict[str, Any],
    *,
    source_clip_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    dataset = project.get("dataset_project")
    recordings = project.get("existing_recordings")
    if (
        not isinstance(dataset, dict)
        or dataset.get("source_kind") != "existing_recordings"
        or dataset.get("status") not in {"approved", "exported"}
        or dataset.get("dataset_fingerprint") is None
        or not isinstance(recordings, dict)
        or recordings.get("status") not in {"approved", "exported"}
    ):
        raise ExpressiveReferenceBankValidationError(
            "Expressive reference banks require an approved existing-recordings dataset."
        )
    clip = next(
        (
            item
            for item in recordings["clips"]
            if item["clip_id"] == source_clip_id
        ),
        None,
    )
    if (
        clip is None
        or source_clip_id not in dataset["clip_ids"]
        or clip["inclusion_decision"] != "included"
        or not clip["transcript_corrected"]
        or clip["duplicate_status"] != "unique"
        or clip["contamination_status"] != "clean"
    ):
        raise ExpressiveReferenceBankValidationError(
            "The selected recording clip is not an included, reviewed, clean same-speaker recording."
        )
    source_file = next(
        (
            item
            for item in recordings["files"]
            if item["file_id"] == clip["source_file_id"]
        ),
        None,
    )
    if source_file is None:
        raise ExpressiveReferenceBankValidationError(
            "The selected recording clip has no source-file provenance."
        )
    return clip, source_file


def _owned_reference_clip(
    project: dict[str, Any],
    *,
    source_clip_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    dataset = project.get("dataset_project")
    recordings = project.get("existing_recordings")
    selected = project.get("selected_reference_sample")
    if (
        not isinstance(dataset, dict)
        or dataset.get("source_kind") != "existing_recordings"
        or dataset.get("status") not in {"approved", "exported"}
        or dataset.get("dataset_fingerprint") is None
        or not isinstance(recordings, dict)
        or recordings.get("status") not in {"approved", "exported"}
    ):
        raise ExpressiveReferenceBankValidationError(
            "Expressive reference banks require an approved existing-recordings dataset."
        )
    if (
        not isinstance(selected, dict)
        or selected.get("source_kind") != "existing_recordings"
        or selected.get("clip_id") is None
        or selected.get("clip_id") not in dataset["clip_ids"]
    ):
        raise ExpressiveReferenceBankValidationError(
            "Select an approved owned-recording clip as the project reference first."
        )
    selected_clip_id = selected["clip_id"]
    if source_clip_id is not None and source_clip_id != selected_clip_id:
        raise ExpressiveReferenceBankValidationError(
            "The requested identity clip must match the approved selected reference."
        )
    clip, source_file = _included_owned_clip(
        project,
        source_clip_id=selected_clip_id,
    )
    return clip, source_file, selected


def build_reference_bank(
    *,
    voice_training_project: dict[str, Any],
    identity_source: dict[str, Any],
    identity_seed: int | None = None,
    created_at_utc: str | None = None,
    required_style_keys: list[str] | None = None,
) -> dict[str, Any]:
    project = copy.deepcopy(voice_training_project)
    persona = project["desired_base_persona"]
    if persona["approval_status"] != "approved" or persona["approved_fingerprint"] is None:
        raise ExpressiveReferenceBankValidationError(
            "Expressive reference banks require an approved desired persona."
        )
    normalized_source = _validate_identity_source(identity_source)
    if identity_seed is not None and (
        not isinstance(identity_seed, int)
        or isinstance(identity_seed, bool)
        or identity_seed < 0
    ):
        raise ExpressiveReferenceBankValidationError(
            "Identity seed must be non-negative or null."
        )
    required = required_style_keys or list(REQUIRED_STYLE_KEYS)
    timestamp = created_at_utc or utc_timestamp()
    identity_reference = {
        "reference_id": "reference_"
        + fingerprint_value(
            {
                "character_id": project["character"]["id"],
                "style_key": "neutral",
                "source_clip_id": normalized_source["source_clip_id"],
                "audio_sha256": normalized_source["audio_sha256"],
            }
        )[:24],
        "style_key": "neutral",
        "label": STYLE_DEFINITIONS["neutral"]["label"],
        "instruction": STYLE_DEFINITIONS["neutral"]["instruction"],
        "reference_text": normalized_source["exact_transcript"],
        "seed": None,
        "audio_path": normalized_source["audio_path"],
        "audio_sha256": normalized_source["audio_sha256"],
        "generation_backend": "owned-recording",
        "model": "none",
        "source_kind": "owned_recording",
        "source_clip_id": normalized_source["source_clip_id"],
        "generated_at_utc": timestamp,
        "review": {
            "source_identity_retention_passed": True,
            "identity_drift_passed": True,
            "emotion_match_passed": False,
            "pronunciation_passed": True,
            "pace_passed": False,
            "approved": False,
            "notes": "Canonical supplied identity reference; review its delivery style before approval.",
            "reviewed_at_utc": None,
        },
    }
    bank = {
        "schema_version": BANK_SCHEMA_VERSION,
        "status": "draft",
        "character": {
            "id": project["character"]["id"],
            "canonical_name": project["character"]["canonical_name"],
            "display_name": project["character"]["display_name"],
            "source_fingerprint": project["character"]["source_fingerprint"],
            "roster_fingerprint": project["character"]["roster_fingerprint"],
        },
        "persona_fingerprint": persona["approved_fingerprint"],
        "identity_source": normalized_source,
        "identity_seed": identity_seed,
        "required_style_keys": required,
        "neutral_style_key": "neutral",
        "references": [identity_reference],
        "comparison": {
            "status": "not_started",
            "test_lines": [],
            "outputs": [],
            "source_identity_retention_passed": None,
            "identity_consistency_passed": None,
            "emotion_match_passed": None,
            "pronunciation_passed": None,
            "pace_passed": None,
            "long_form_drift_passed": None,
            "notes": "",
            "reviewed_at_utc": None,
        },
        "production_assignment": {
            "status": "unassigned",
            "voice_name": None,
            "assigned_at_utc": None,
        },
        "created_at_utc": timestamp,
        "updated_at_utc": timestamp,
        "bank_fingerprint": "0" * 64,
    }
    bank["bank_fingerprint"] = compute_bank_fingerprint(bank)
    return validate_reference_bank(bank)


def create_reference_bank_file(
    *,
    projects_root: str | Path,
    character_id: str,
    identity_seed: int | None = None,
    source_clip_id: str | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    with _BANK_LOCK:
        project_path = voice_training_project_path(projects_root, character_id)
        bank_path = reference_bank_path(projects_root, character_id)
        if bank_path.exists():
            raise ExpressiveReferenceBankConflictError(
                "An expressive reference bank already exists for this character."
            )
        project = read_voice_training_project(project_path)
        clip, source_file, selected = _owned_reference_clip(
            project,
            source_clip_id=source_clip_id,
        )
        project_dir = project_path.parent.resolve()
        audio = (project_dir / clip["audio_path"]).resolve()
        try:
            audio.relative_to(project_dir)
        except ValueError as exc:
            raise ExpressiveReferenceBankValidationError(
                "The selected identity audio escaped its character project."
            ) from exc
        if not audio.is_file() or sha256_file(audio) != clip["audio_sha256"]:
            raise ExpressiveReferenceBankValidationError(
                "The selected identity audio is missing or changed."
            )
        identity_source = {
            "kind": "owned_recording",
            "source_clip_id": clip["clip_id"],
            "source_file_id": clip["source_file_id"],
            "exact_transcript": clip["transcript"],
            "audio_path": audio.relative_to(project_dir).as_posix(),
            "audio_sha256": clip["audio_sha256"],
            "permission_basis": source_file["permission_basis"],
            "selected_reference_fingerprint": fingerprint_value(selected),
        }
        bank = build_reference_bank(
            voice_training_project=project,
            identity_source=identity_source,
            identity_seed=identity_seed,
            created_at_utc=created_at_utc,
        )
        bank_path.parent.mkdir(parents=True, exist_ok=True)
        return save_reference_bank(bank, bank_path)


def _check_bank_ownership(
    bank: dict[str, Any],
    project: dict[str, Any],
) -> None:
    character = bank["character"]
    project_character = project["character"]
    for key in ("id", "source_fingerprint", "roster_fingerprint"):
        if character[key] != project_character[key]:
            raise ExpressiveReferenceBankConflictError(
                "Reference bank belongs to another character, source, or approved roster."
            )
    persona = project["desired_base_persona"]
    if (
        persona["approval_status"] != "approved"
        or bank["persona_fingerprint"] != persona["approved_fingerprint"]
    ):
        raise ExpressiveReferenceBankConflictError(
            "Reference bank belongs to another or stale approved persona."
        )
    try:
        clip, source_file, selected = _owned_reference_clip(
            project,
            source_clip_id=bank["identity_source"]["source_clip_id"],
        )
    except ExpressiveReferenceBankValidationError as exc:
        raise ExpressiveReferenceBankConflictError(str(exc)) from exc
    identity_source = bank["identity_source"]
    if (
        identity_source["source_file_id"] != clip["source_file_id"]
        or identity_source["exact_transcript"]
        != clip["transcript"]
        or identity_source["audio_path"] != clip["audio_path"]
        or identity_source["audio_sha256"] != clip["audio_sha256"]
        or identity_source["permission_basis"]
        != source_file["permission_basis"]
        or identity_source["selected_reference_fingerprint"]
        != fingerprint_value(selected)
    ):
        raise ExpressiveReferenceBankConflictError(
            "Reference bank identity source is stale or no longer matches "
            "the approved owned recording reference."
        )


def _new_reference(
    *,
    bank: dict[str, Any],
    style_key: str,
    instruction: str,
    reference_text: str,
    seed: int | None,
    audio_path: str,
    audio_sha256: str,
    generation_backend: str,
    model: str,
    source_kind: str,
    source_clip_id: str | None,
    generated_at_utc: str,
) -> dict[str, Any]:
    style = _style_key(style_key)
    identity = {
        "character_id": bank["character"]["id"],
        "persona_fingerprint": bank["persona_fingerprint"],
        "style_key": style,
        "audio_sha256": audio_sha256,
        "seed": seed,
    }
    return {
        "reference_id": "reference_" + fingerprint_value(identity)[:24],
        "style_key": style,
        "label": STYLE_DEFINITIONS.get(style, {}).get("label", style.replace("_", " ").title()),
        "instruction": instruction,
        "reference_text": reference_text,
        "seed": seed,
        "audio_path": audio_path,
        "audio_sha256": audio_sha256,
        "generation_backend": generation_backend,
        "model": model,
        "source_kind": source_kind,
        "source_clip_id": source_clip_id,
        "generated_at_utc": generated_at_utc,
        "review": {
            "source_identity_retention_passed": False,
            "identity_drift_passed": False,
            "emotion_match_passed": False,
            "pronunciation_passed": False,
            "pace_passed": False,
            "approved": False,
            "notes": "",
            "reviewed_at_utc": None,
        },
    }


def add_reference(
    bank: dict[str, Any],
    *,
    expected_fingerprint: str,
    style_key: str,
    instruction: str,
    reference_text: str,
    seed: int | None,
    audio_path: str,
    audio_sha256: str,
    generation_backend: str,
    model: str,
    source_kind: str = "qwen_icl_instruction_experimental",
    source_clip_id: str | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    normalized = validate_reference_bank(bank)
    if expected_fingerprint != normalized["bank_fingerprint"]:
        raise ExpressiveReferenceBankConflictError("Reference bank changed after it was loaded.")
    if normalized["status"] == "approved":
        raise ExpressiveReferenceBankConflictError("Approved reference bank is immutable until explicitly returned to draft.")
    style = _style_key(style_key)
    reference = _new_reference(
        bank=normalized,
        style_key=style,
        instruction=_text(instruction, "Reference instruction"),
        reference_text=_text(reference_text, "Reference text"),
        seed=seed,
        audio_path=_relative_path(audio_path, "Reference audio path"),
        audio_sha256=_sha(audio_sha256, "Reference audio fingerprint"),
        generation_backend=_text(generation_backend, "Reference generation backend"),
        model=_text(model, "Reference model"),
        source_kind=source_kind,
        source_clip_id=source_clip_id,
        generated_at_utc=generated_at_utc or utc_timestamp(),
    )
    working = copy.deepcopy(normalized)
    working["references"] = [
        item for item in working["references"] if item["style_key"] != style
    ] + [reference]
    working["comparison"] = {
        "status": "not_started",
        "test_lines": [],
        "outputs": [],
        "source_identity_retention_passed": None,
        "identity_consistency_passed": None,
        "emotion_match_passed": None,
        "pronunciation_passed": None,
        "pace_passed": None,
        "long_form_drift_passed": None,
        "notes": "",
        "reviewed_at_utc": None,
    }
    working["updated_at_utc"] = utc_timestamp()
    working["bank_fingerprint"] = compute_bank_fingerprint(working)
    return validate_reference_bank(working)


def add_owned_recording_reference(
    bank: dict[str, Any],
    *,
    voice_training_project: dict[str, Any],
    project_dir: str | Path,
    expected_fingerprint: str,
    style_key: str,
    source_clip_id: str,
    instruction: str | None = None,
    imported_at_utc: str | None = None,
) -> dict[str, Any]:
    normalized = validate_reference_bank(bank)
    _check_bank_ownership(normalized, voice_training_project)
    clip, _source_file = _included_owned_clip(
        voice_training_project,
        source_clip_id=source_clip_id,
    )
    root = Path(project_dir).expanduser().resolve()
    audio = (root / clip["audio_path"]).resolve()
    try:
        audio.relative_to(root)
    except ValueError as exc:
        raise ExpressiveReferenceBankValidationError(
            "The owned recording clip escaped its character project."
        ) from exc
    if not audio.is_file() or sha256_file(audio) != clip["audio_sha256"]:
        raise ExpressiveReferenceBankValidationError(
            "The owned recording clip is missing or changed."
        )
    style = _style_key(style_key)
    definition = STYLE_DEFINITIONS.get(style)
    if definition is None:
        raise ExpressiveReferenceBankValidationError(
            f"Unsupported expressive reference style: {style_key!r}."
        )
    return add_reference(
        normalized,
        expected_fingerprint=expected_fingerprint,
        style_key=style,
        instruction=instruction or definition["instruction"],
        reference_text=clip["corrected_transcript"],
        seed=None,
        audio_path=audio.relative_to(root).as_posix(),
        audio_sha256=clip["audio_sha256"],
        generation_backend="owned-recording",
        model="none",
        source_kind="owned_recording",
        source_clip_id=clip["clip_id"],
        generated_at_utc=imported_at_utc or utc_timestamp(),
    )


def review_reference(
    bank: dict[str, Any],
    *,
    expected_fingerprint: str,
    reference_id: str,
    source_identity_retention_passed: bool,
    identity_drift_passed: bool,
    emotion_match_passed: bool,
    pronunciation_passed: bool,
    pace_passed: bool,
    notes: str = "",
    reviewed_at_utc: str | None = None,
) -> dict[str, Any]:
    normalized = validate_reference_bank(bank)
    if expected_fingerprint != normalized["bank_fingerprint"]:
        raise ExpressiveReferenceBankConflictError("Reference bank changed after it was loaded.")
    found = False
    working = copy.deepcopy(normalized)
    for item in working["references"]:
        if item["reference_id"] != reference_id:
            continue
        found = True
        approved = all(
            (
                source_identity_retention_passed,
                identity_drift_passed,
                emotion_match_passed,
                pronunciation_passed,
                pace_passed,
            )
        )
        item["review"] = {
            "source_identity_retention_passed": bool(
                source_identity_retention_passed
            ),
            "identity_drift_passed": bool(identity_drift_passed),
            "emotion_match_passed": bool(emotion_match_passed),
            "pronunciation_passed": bool(pronunciation_passed),
            "pace_passed": bool(pace_passed),
            "approved": approved,
            "notes": _text(notes, "Reference review notes", allow_empty=True),
            "reviewed_at_utc": reviewed_at_utc or utc_timestamp(),
        }
        break
    if not found:
        raise ExpressiveReferenceBankValidationError("Reference was not found.")
    working["comparison"]["status"] = "not_started"
    working["updated_at_utc"] = utc_timestamp()
    working["bank_fingerprint"] = compute_bank_fingerprint(working)
    return validate_reference_bank(working)


def record_comparison_outputs(
    bank: dict[str, Any],
    *,
    expected_fingerprint: str,
    test_lines: list[str],
    outputs: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized = validate_reference_bank(bank)
    if expected_fingerprint != normalized["bank_fingerprint"]:
        raise ExpressiveReferenceBankConflictError("Reference bank changed after it was loaded.")
    working = copy.deepcopy(normalized)
    working["comparison"] = {
        "status": "generated",
        "test_lines": test_lines,
        "outputs": outputs,
        "source_identity_retention_passed": None,
        "identity_consistency_passed": None,
        "emotion_match_passed": None,
        "pronunciation_passed": None,
        "pace_passed": None,
        "long_form_drift_passed": None,
        "notes": "",
        "reviewed_at_utc": None,
    }
    working["updated_at_utc"] = utc_timestamp()
    working["bank_fingerprint"] = compute_bank_fingerprint(working)
    return validate_reference_bank(working)


def review_comparison(
    bank: dict[str, Any],
    *,
    expected_fingerprint: str,
    source_identity_retention_passed: bool,
    identity_consistency_passed: bool,
    emotion_match_passed: bool,
    pronunciation_passed: bool,
    pace_passed: bool,
    long_form_drift_passed: bool,
    notes: str = "",
    reviewed_at_utc: str | None = None,
) -> dict[str, Any]:
    normalized = validate_reference_bank(bank)
    if expected_fingerprint != normalized["bank_fingerprint"]:
        raise ExpressiveReferenceBankConflictError("Reference bank changed after it was loaded.")
    if normalized["comparison"]["status"] not in {"generated", "approved", "rejected"}:
        raise ExpressiveReferenceBankValidationError("Generate comparison audio before review.")
    passed = all(
        (
            source_identity_retention_passed,
            identity_consistency_passed,
            emotion_match_passed,
            pronunciation_passed,
            pace_passed,
            long_form_drift_passed,
        )
    )
    working = copy.deepcopy(normalized)
    working["comparison"].update(
        {
            "status": "approved" if passed else "rejected",
            "source_identity_retention_passed": bool(
                source_identity_retention_passed
            ),
            "identity_consistency_passed": bool(identity_consistency_passed),
            "emotion_match_passed": bool(emotion_match_passed),
            "pronunciation_passed": bool(pronunciation_passed),
            "pace_passed": bool(pace_passed),
            "long_form_drift_passed": bool(long_form_drift_passed),
            "notes": _text(notes, "Comparison notes", allow_empty=True),
            "reviewed_at_utc": reviewed_at_utc or utc_timestamp(),
        }
    )
    working["updated_at_utc"] = utc_timestamp()
    working["bank_fingerprint"] = compute_bank_fingerprint(working)
    return validate_reference_bank(working)


def approve_reference_bank(
    bank: dict[str, Any],
    *,
    expected_fingerprint: str,
) -> dict[str, Any]:
    normalized = validate_reference_bank(bank)
    if expected_fingerprint != normalized["bank_fingerprint"]:
        raise ExpressiveReferenceBankConflictError("Reference bank changed after it was loaded.")
    working = copy.deepcopy(normalized)
    working["status"] = "approved"
    working["updated_at_utc"] = utc_timestamp()
    working["bank_fingerprint"] = compute_bank_fingerprint(working)
    return validate_reference_bank(working)


def return_reference_bank_to_draft(
    bank: dict[str, Any],
    *,
    expected_fingerprint: str,
) -> dict[str, Any]:
    normalized = validate_reference_bank(bank)
    if expected_fingerprint != normalized["bank_fingerprint"]:
        raise ExpressiveReferenceBankConflictError("Reference bank changed after it was loaded.")
    working = copy.deepcopy(normalized)
    working["status"] = "draft"
    working["production_assignment"] = {
        "status": "unassigned",
        "voice_name": None,
        "assigned_at_utc": None,
    }
    working["updated_at_utc"] = utc_timestamp()
    working["bank_fingerprint"] = compute_bank_fingerprint(working)
    return validate_reference_bank(working)


def map_instruction_to_style(
    instruction: str,
    *,
    available_styles: list[str] | tuple[str, ...] = REQUIRED_STYLE_KEYS,
    neutral_style_key: str = "neutral",
) -> dict[str, Any]:
    available = [_style_key(item) for item in available_styles]
    neutral = _style_key(neutral_style_key)
    if neutral not in available:
        raise ExpressiveReferenceBankValidationError("Neutral style is unavailable.")
    text = instruction if isinstance(instruction, str) else str(instruction or "")
    explicit = re.search(r"\[\s*style\s*:\s*([a-zA-Z0-9 _-]+)\s*\]", text)
    if explicit:
        requested = _style_key(explicit.group(1))
        if requested in available:
            return {"style_key": requested, "reason": "explicit_override", "score": 10_000}
    tokens = set(re.findall(r"[a-z]+", text.casefold()))
    scores = {}
    for style in available:
        keywords = STYLE_DEFINITIONS.get(style, {}).get("keywords", ())
        scores[style] = sum(1 for keyword in keywords if keyword.casefold() in tokens or keyword.casefold() in text.casefold())
    best_score = max(scores.values(), default=0)
    if best_score <= 0:
        return {"style_key": neutral, "reason": "neutral_fallback", "score": 0}
    best = sorted(style for style, score in scores.items() if score == best_score)
    if len(best) != 1:
        return {"style_key": neutral, "reason": "ambiguous_fallback", "score": best_score}
    return {"style_key": best[0], "reason": "keyword_match", "score": best_score}


def select_reference_for_instruction(
    *,
    bank_path: str | Path,
    instruction: str,
    project_root: str | Path,
    verify_audio: bool = True,
    require_bank_approved: bool = True,
) -> dict[str, Any]:
    bank = read_reference_bank(bank_path)
    if require_bank_approved and bank["status"] != "approved":
        raise ExpressiveReferenceBankConflictError("Reference bank is not approved.")
    approved = {
        item["style_key"]: item
        for item in bank["references"]
        if item["review"]["approved"]
    }
    mapping = map_instruction_to_style(
        instruction,
        available_styles=list(approved),
        neutral_style_key=bank["neutral_style_key"],
    )
    reference = approved.get(mapping["style_key"])
    if reference is None:
        reference = approved[bank["neutral_style_key"]]
        mapping = {"style_key": bank["neutral_style_key"], "reason": "neutral_fallback", "score": 0}
    bank_file = Path(bank_path).expanduser().resolve()
    audio = (bank_file.parent / reference["audio_path"]).resolve()
    try:
        audio.relative_to(bank_file.parent.resolve())
    except ValueError as exc:
        raise ExpressiveReferenceBankValidationError("Reference audio escaped its character project.") from exc
    if verify_audio:
        if not audio.is_file():
            raise ExpressiveReferenceBankValidationError("Reference audio file is missing.")
        if sha256_file(audio) != reference["audio_sha256"]:
            raise ExpressiveReferenceBankValidationError("Reference audio fingerprint does not match.")
    return {
        "style_key": mapping["style_key"],
        "mapping_reason": mapping["reason"],
        "reference_id": reference["reference_id"],
        "ref_audio": str(audio),
        "ref_text": reference["reference_text"],
        "instruction": reference["instruction"],
        "bank_fingerprint": bank["bank_fingerprint"],
        "character_id": bank["character"]["id"],
    }


def assign_reference_bank_to_voice_config(
    *,
    bank_path: str | Path,
    voice_config_path: str | Path,
    project_root: str | Path,
    expected_fingerprint: str,
    voice_name: str | None = None,
    assigned_at_utc: str | None = None,
) -> dict[str, Any]:
    with _BANK_LOCK:
        bank_target = Path(bank_path).expanduser().resolve()
        root = Path(project_root).expanduser().resolve()
        try:
            bank_target.relative_to(root)
        except ValueError as exc:
            raise ExpressiveReferenceBankValidationError(
                "Reference bank must remain inside the project root."
            ) from exc
        bank = read_reference_bank(bank_target)
        if expected_fingerprint != bank["bank_fingerprint"]:
            raise ExpressiveReferenceBankConflictError(
                "Reference bank changed after it was loaded."
            )
        if bank["status"] != "approved" or bank["comparison"]["status"] != "approved":
            raise ExpressiveReferenceBankConflictError(
                "Only an approved and compared reference bank may be assigned."
            )
        name = _text(
            voice_name
            or bank["character"]["canonical_name"],
            "Production voice name",
        )
        approved = {
            item["style_key"]: item
            for item in bank["references"]
            if item["review"]["approved"]
        }
        neutral = approved[bank["neutral_style_key"]]
        neutral_audio = (bank_target.parent / neutral["audio_path"]).resolve()
        try:
            neutral_audio.relative_to(root)
        except ValueError as exc:
            raise ExpressiveReferenceBankValidationError(
                "Neutral reference audio must remain inside the project root."
            ) from exc
        if not neutral_audio.is_file() or sha256_file(neutral_audio) != neutral["audio_sha256"]:
            raise ExpressiveReferenceBankValidationError(
                "Neutral reference audio is missing or changed."
            )
        config_target = Path(voice_config_path).expanduser().resolve()
        try:
            config_target.relative_to(root)
        except ValueError as exc:
            raise ExpressiveReferenceBankValidationError(
                "Voice configuration must remain inside the project root."
            ) from exc
        if config_target.exists():
            try:
                config = json.loads(config_target.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ExpressiveReferenceBankValidationError(
                    f"Voice configuration could not be read: {exc}"
                ) from exc
            if not isinstance(config, dict):
                raise ExpressiveReferenceBankValidationError(
                    "Voice configuration root must be an object."
                )
        else:
            config = {}
        before_config = config_target.read_bytes() if config_target.exists() else None
        before_bank = bank_target.read_bytes()
        working_bank = copy.deepcopy(bank)
        timestamp = assigned_at_utc or utc_timestamp()
        working_bank["production_assignment"] = {
            "status": "assigned",
            "voice_name": name,
            "assigned_at_utc": timestamp,
        }
        working_bank["updated_at_utc"] = timestamp
        working_bank["bank_fingerprint"] = compute_bank_fingerprint(working_bank)
        validated_bank = validate_reference_bank(working_bank)
        entry = copy.deepcopy(config.get(name, {}))
        entry.update(
            {
                "type": "clone",
                "reference_bank_path": bank_target.relative_to(root).as_posix(),
                "reference_bank_character_id": bank["character"]["id"],
                "reference_bank_fingerprint": validated_bank["bank_fingerprint"],
                "reference_bank_identity_source": bank["identity_source"]["kind"],
                "reference_bank_source_clip_id": bank["identity_source"]["source_clip_id"],
                "ref_audio": neutral_audio.relative_to(root).as_posix(),
                "ref_text": neutral["reference_text"],
            }
        )
        config[name] = entry
        operation_id = "audio_dependency_" + fingerprint_value(
            {
                "operation": "reference_bank_assignment",
                "character_id": bank["character"]["id"],
                "bank_fingerprint": validated_bank["bank_fingerprint"],
                "assigned_at_utc": timestamp,
            }
        )[:24]
        try:
            atomic_json_write(config, config_target)
            atomic_json_write(validated_bank, bank_target)
            invalidation = apply_project_audio_invalidation(
                project_root=root,
                operation_id=operation_id,
                operation="reference_bank_assignment",
                at_utc=timestamp,
                speakers={name, bank["character"]["canonical_name"]},
                reason="production reference bank assignment changed",
                dependency_before={
                    config_target: before_config,
                    bank_target: before_bank,
                },
            )
        except Exception:
            if before_config is None:
                try:
                    config_target.unlink()
                except FileNotFoundError:
                    pass
            else:
                config_target.parent.mkdir(parents=True, exist_ok=True)
                config_target.write_bytes(before_config)
            bank_target.write_bytes(before_bank)
            raise
        return {
            "bank": read_reference_bank(bank_target),
            "voice_name": name,
            "voice_config": json.loads(config_target.read_text(encoding="utf-8")),
            "audio_invalidation": invalidation,
        }


def clear_reference_bank_assignment(
    *,
    bank_path: str | Path,
    voice_config_path: str | Path,
    project_root: str | Path,
    expected_fingerprint: str,
) -> dict[str, Any]:
    with _BANK_LOCK:
        bank_target = Path(bank_path).expanduser().resolve()
        root = Path(project_root).expanduser().resolve()
        bank = read_reference_bank(bank_target)
        if expected_fingerprint != bank["bank_fingerprint"]:
            raise ExpressiveReferenceBankConflictError(
                "Reference bank changed after it was loaded."
            )
        assignment = bank["production_assignment"]
        name = assignment["voice_name"]
        config_target = Path(voice_config_path).expanduser().resolve()
        config = {}
        if config_target.exists():
            value = json.loads(config_target.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ExpressiveReferenceBankValidationError(
                    "Voice configuration root must be an object."
                )
            config = value
        if name and isinstance(config.get(name), dict):
            entry = copy.deepcopy(config[name])
            for key in (
                "reference_bank_path",
                "reference_bank_character_id",
                "reference_bank_fingerprint",
                "reference_bank_identity_source",
                "reference_bank_source_clip_id",
                "selected_reference_style",
                "selected_reference_id",
            ):
                entry.pop(key, None)
            config[name] = entry
        before_config = config_target.read_bytes() if config_target.exists() else None
        before_bank = bank_target.read_bytes()
        working = copy.deepcopy(bank)
        working["production_assignment"] = {
            "status": "unassigned",
            "voice_name": None,
            "assigned_at_utc": None,
        }
        timestamp = utc_timestamp()
        working["updated_at_utc"] = timestamp
        working["bank_fingerprint"] = compute_bank_fingerprint(working)
        validated = validate_reference_bank(working)
        operation_id = "audio_dependency_" + fingerprint_value(
            {
                "operation": "reference_bank_unassignment",
                "character_id": bank["character"]["id"],
                "bank_fingerprint": validated["bank_fingerprint"],
                "at_utc": timestamp,
            }
        )[:24]
        try:
            atomic_json_write(config, config_target)
            atomic_json_write(validated, bank_target)
            invalidation = apply_project_audio_invalidation(
                project_root=root,
                operation_id=operation_id,
                operation="reference_bank_unassignment",
                at_utc=timestamp,
                speakers={name, bank["character"]["canonical_name"]},
                reason="production reference bank assignment removed",
                dependency_before={
                    config_target: before_config,
                    bank_target: before_bank,
                },
            )
        except Exception:
            _atomic_restore(config_target, before_config)
            _atomic_restore(bank_target, before_bank)
            raise
        return {
            "bank": read_reference_bank(bank_target),
            "voice_config": json.loads(config_target.read_text(encoding="utf-8")),
            "audio_invalidation": invalidation,
        }


def mutate_reference_bank_file(
    *,
    projects_root: str | Path,
    character_id: str,
    expected_fingerprint: str,
    action: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    with _BANK_LOCK:
        project_path = voice_training_project_path(projects_root, character_id)
        bank_path = reference_bank_path(projects_root, character_id)
        project = read_voice_training_project(project_path)
        bank = read_reference_bank(bank_path)
        _check_bank_ownership(bank, project)
        if action == "add_reference":
            updated = add_reference(
                bank,
                expected_fingerprint=expected_fingerprint,
                **payload,
            )
        elif action == "add_owned_recording_reference":
            updated = add_owned_recording_reference(
                bank,
                voice_training_project=project,
                project_dir=project_path.parent,
                expected_fingerprint=expected_fingerprint,
                **payload,
            )
        elif action == "review_reference":
            updated = review_reference(bank, expected_fingerprint=expected_fingerprint, **payload)
        elif action == "record_comparison_outputs":
            updated = record_comparison_outputs(bank, expected_fingerprint=expected_fingerprint, **payload)
        elif action == "review_comparison":
            updated = review_comparison(bank, expected_fingerprint=expected_fingerprint, **payload)
        elif action == "approve_bank":
            updated = approve_reference_bank(bank, expected_fingerprint=expected_fingerprint)
        elif action == "return_to_draft":
            updated = return_reference_bank_to_draft(bank, expected_fingerprint=expected_fingerprint)
        else:
            raise ExpressiveReferenceBankValidationError(
                f"Unsupported expressive reference bank action: {action!r}."
            )
        return save_reference_bank(updated, bank_path)


def build_reference_bank_status(
    *,
    approved_roster: dict[str, Any] | None,
    projects_root: str | Path,
) -> dict[str, Any]:
    if approved_roster is None:
        return {
            "available": False,
            "reason": "No annotated script or approved character roster exists.",
            "entries": [],
        }
    entries = []
    for character in approved_roster["entries"]:
        if (
            character["speaking_status"] not in {"speaker", "narrator"}
            or character["resolution_status"] != "resolved"
        ):
            continue
        path = reference_bank_path(projects_root, character["id"])
        if not path.exists():
            entries.append(
                {
                    "character_id": character["id"],
                    "canonical_name": character["canonical_name"],
                    "display_name": character["display_name"],
                    "status": "absent",
                    "approved_style_count": 0,
                    "required_style_count": len(REQUIRED_STYLE_KEYS),
                    "comparison_status": "not_started",
                    "assigned": False,
                    "identity_source_kind": None,
                    "source_clip_id": None,
                    "error": None,
                }
            )
            continue
        try:
            bank = read_reference_bank(path)
            entries.append(
                {
                    "character_id": character["id"],
                    "canonical_name": character["canonical_name"],
                    "display_name": character["display_name"],
                    "status": bank["status"],
                    "approved_style_count": sum(
                        item["review"]["approved"] for item in bank["references"]
                    ),
                    "required_style_count": len(bank["required_style_keys"]),
                    "comparison_status": bank["comparison"]["status"],
                    "assigned": bank["production_assignment"]["status"] == "assigned",
                    "identity_source_kind": bank["identity_source"]["kind"],
                    "source_clip_id": bank["identity_source"]["source_clip_id"],
                    "error": None,
                }
            )
        except ExpressiveReferenceBankError as exc:
            entries.append(
                {
                    "character_id": character["id"],
                    "canonical_name": character["canonical_name"],
                    "display_name": character["display_name"],
                    "status": "invalid",
                    "approved_style_count": 0,
                    "required_style_count": len(REQUIRED_STYLE_KEYS),
                    "comparison_status": "unknown",
                    "assigned": False,
                    "identity_source_kind": None,
                    "source_clip_id": None,
                    "error": str(exc),
                }
            )
    return {
        "available": True,
        "reason": None,
        "style_definitions": copy.deepcopy(STYLE_DEFINITIONS),
        "entries": entries,
    }
