from __future__ import annotations

import copy
import json
import math
import os
import re
import stat
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from generation_state import fingerprint_text, fingerprint_value
from llm_schemas import ContractValidationError, validate_contract
from script_audit import audit_script_chunk


IMPORT_BUNDLE_SCHEMA_VERSION = 1
IMPORT_BUNDLE_TYPE = "alexandria_annotated_script"
SCRIPT_MEMBER = "annotated_script.json"
METADATA_MEMBER = "annotated_script.meta.json"
VOICE_CONFIG_MEMBER = "voice_config.json"
MANIFEST_MEMBER = "manifest.json"
REQUIRED_BUNDLE_MEMBERS = frozenset({MANIFEST_MEMBER, SCRIPT_MEMBER})
OPTIONAL_BUNDLE_MEMBERS = frozenset({METADATA_MEMBER, VOICE_CONFIG_MEMBER})
ALLOWED_BUNDLE_MEMBERS = REQUIRED_BUNDLE_MEMBERS | OPTIONAL_BUNDLE_MEMBERS
MAX_ARCHIVE_MEMBER_BYTES = 96 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 192 * 1024 * 1024
MAX_DIRECT_JSON_BYTES = 96 * 1024 * 1024
MAX_SCRIPT_ENTRIES = 500_000
MAX_SCRIPT_CHARACTERS = 250_000_000
MAX_JSON_DEPTH = 32
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SAFE_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
CHECKPOINT_STATES = frozenset(
    {
        "none",
        "resumable",
        "finalization_only",
        "running",
        "invalid",
        "corrupt",
        "incompatible",
        "unknown",
    }
)
CHECKPOINT_DECISIONS = frozenset({"keep", "discard", "cancel"})


class AnnotatedScriptImportError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = copy.deepcopy(details or {})


class AnnotatedScriptImportValidationError(AnnotatedScriptImportError):
    pass


class AnnotatedScriptImportConflictError(AnnotatedScriptImportError):
    pass


def utc_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _contains_unsafe_control(value: str) -> bool:
    return any(
        (
            ord(character) < 32
            and character not in {"\t", "\n", "\r"}
        )
        or 0xD800 <= ord(character) <= 0xDFFF
        for character in value
    )


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnnotatedScriptImportValidationError(
            "invalid_text",
            f"{field} must be non-empty text.",
        )
    if _contains_unsafe_control(value):
        raise AnnotatedScriptImportValidationError(
            "unsafe_text",
            f"{field} contains an unsupported control character.",
        )
    return value.strip()


def _safe_json_value(
    value: Any,
    *,
    path: str,
    depth: int = 0,
) -> Any:
    if depth > MAX_JSON_DEPTH:
        raise AnnotatedScriptImportValidationError(
            "json_too_deep",
            f"{path} exceeds the maximum JSON nesting depth.",
        )
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AnnotatedScriptImportValidationError(
                "invalid_number",
                f"{path} contains a non-finite number.",
            )
        return value
    if isinstance(value, str):
        if _contains_unsafe_control(value):
            raise AnnotatedScriptImportValidationError(
                "unsafe_text",
                f"{path} contains an unsupported control character.",
            )
        return value
    if isinstance(value, list):
        return [
            _safe_json_value(
                item,
                path=f"{path}[{index}]",
                depth=depth + 1,
            )
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise AnnotatedScriptImportValidationError(
                    "invalid_json_key",
                    f"{path} contains a non-text or empty key.",
                )
            if _contains_unsafe_control(key):
                raise AnnotatedScriptImportValidationError(
                    "unsafe_text",
                    f"{path} contains an unsafe key.",
                )
            normalized[key] = _safe_json_value(
                item,
                path=f"{path}.{key}",
                depth=depth + 1,
            )
        return normalized
    raise AnnotatedScriptImportValidationError(
        "non_json_value",
        f"{path} contains a value that cannot be represented in JSON.",
    )


def _validate_fingerprint(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise AnnotatedScriptImportValidationError(
            "invalid_fingerprint",
            f"{field} must be a lowercase SHA-256 fingerprint.",
        )
    return value


def _speaker_labels(entries: list[dict[str, str]]) -> list[str]:
    return sorted({entry["speaker"] for entry in entries})


def _validate_entries(value: Any) -> list[dict[str, str]]:
    if isinstance(value, list) and not value:
        raise AnnotatedScriptImportValidationError(
            "empty_script",
            "The annotated script must contain at least one entry.",
        )
    try:
        normalized = validate_contract("script", value)
    except ContractValidationError as exc:
        raise AnnotatedScriptImportValidationError(
            "invalid_script_contract",
            f"The annotated script does not match Alexandria's script contract: {exc}",
        ) from exc
    if not isinstance(normalized, list):
        raise AnnotatedScriptImportValidationError(
            "invalid_script_contract",
            "The annotated script contract did not return a JSON array.",
        )
    if len(normalized) > MAX_SCRIPT_ENTRIES:
        raise AnnotatedScriptImportValidationError(
            "script_too_large",
            "The annotated script contains too many entries.",
        )
    if normalized != value:
        raise AnnotatedScriptImportValidationError(
            "noncanonical_script",
            "The annotated script contains values that would require silent normalization.",
        )
    character_count = 0
    for index, entry in enumerate(normalized):
        speaker = entry["speaker"]
        text = entry["text"]
        instruct = entry["instruct"]
        if speaker != speaker.upper() or not any(
            character.isalpha()
            for character in speaker
        ):
            raise AnnotatedScriptImportValidationError(
                "invalid_speaker_label",
                f"Entry {index} speaker labels must be uppercase canonical text.",
            )
        if len(speaker) > 128:
            raise AnnotatedScriptImportValidationError(
                "invalid_speaker_label",
                f"Entry {index} speaker label is too long.",
            )
        for field, field_value in (
            ("speaker", speaker),
            ("text", text),
            ("instruct", instruct),
        ):
            if _contains_unsafe_control(field_value):
                raise AnnotatedScriptImportValidationError(
                    "unsafe_text",
                    f"Entry {index} {field} contains an unsupported control character.",
                )
        character_count += len(text)
        if character_count > MAX_SCRIPT_CHARACTERS:
            raise AnnotatedScriptImportValidationError(
                "script_too_large",
                "The annotated script contains too much spoken text.",
            )
    return copy.deepcopy(normalized)


def _validate_metadata(
    value: Any,
    *,
    entries: list[dict[str, str]],
    source_text: str | None,
) -> tuple[dict[str, Any] | None, list[str]]:
    if value is None:
        return None, []
    if not isinstance(value, dict):
        raise AnnotatedScriptImportValidationError(
            "invalid_metadata",
            "annotated_script.meta.json must contain a JSON object.",
        )
    metadata = _safe_json_value(
        value,
        path="metadata",
    )
    if metadata.get("schema_version") != 1:
        raise AnnotatedScriptImportValidationError(
            "unsupported_metadata_schema",
            "Unsupported annotated-script metadata schema.",
        )
    result = metadata.get("result")
    if not isinstance(result, dict):
        raise AnnotatedScriptImportValidationError(
            "invalid_metadata",
            "Metadata result information is missing.",
        )
    expected_script_fingerprint = fingerprint_value(entries)
    if result.get("script_fingerprint") != expected_script_fingerprint:
        raise AnnotatedScriptImportValidationError(
            "metadata_script_mismatch",
            "Metadata script fingerprint does not match the imported script.",
        )
    if result.get("entry_count") != len(entries):
        raise AnnotatedScriptImportValidationError(
            "metadata_script_mismatch",
            "Metadata entry count does not match the imported script.",
        )
    if result.get("speaker_labels") != _speaker_labels(entries):
        raise AnnotatedScriptImportValidationError(
            "metadata_script_mismatch",
            "Metadata speaker labels do not match the imported script.",
        )
    warnings: list[str] = []
    source = metadata.get("source")
    if source is not None and not isinstance(source, dict):
        raise AnnotatedScriptImportValidationError(
            "invalid_metadata",
            "Metadata source information must be a JSON object.",
        )
    if source_text is not None:
        if not isinstance(source, dict):
            raise AnnotatedScriptImportValidationError(
                "metadata_source_missing",
                "Metadata source information is required when verifying against a source.",
            )
        expected_source_fingerprint = fingerprint_text(source_text)
        if source.get("fingerprint") != expected_source_fingerprint:
            raise AnnotatedScriptImportValidationError(
                "metadata_source_mismatch",
                "Metadata source fingerprint does not match the selected source.",
            )
        if source.get("character_count") != len(source_text):
            raise AnnotatedScriptImportValidationError(
                "metadata_source_mismatch",
                "Metadata source character count does not match the selected source.",
            )
    elif isinstance(source, dict) and source.get("fingerprint"):
        _validate_fingerprint(
            source.get("fingerprint"),
            "metadata.source.fingerprint",
        )
        warnings.append(
            "The bundle claims source provenance, but no current source was available to verify it."
        )
    return metadata, warnings


def _script_summary(
    entries: list[dict[str, str]],
) -> dict[str, Any]:
    speaker_labels = _speaker_labels(entries)
    return {
        "entry_count": len(entries),
        "speaker_count": len(speaker_labels),
        "speaker_labels": speaker_labels,
        "character_count": sum(
            len(entry["text"])
            for entry in entries
        ),
        "narrator_entry_count": sum(
            entry["speaker"] == "NARRATOR"
            for entry in entries
        ),
        "directed_entry_count": sum(
            bool(entry["instruct"])
            for entry in entries
        ),
    }


def _import_consequences(
    *,
    metadata: dict[str, Any] | None,
    voice_config: dict[str, Any] | None,
    checkpoint_status: str,
    generated_audio_count: int,
) -> dict[str, bool]:
    return {
        "replace_script": True,
        "remove_unrelated_metadata": metadata is None,
        "replace_voice_config": voice_config is not None,
        "rebuild_chunks": True,
        "mark_generated_audio_stale": generated_audio_count > 0,
        "checkpoint_decision_required": checkpoint_status != "none",
    }


def _validate_voice_config(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise AnnotatedScriptImportValidationError(
            "invalid_voice_config",
            "voice_config.json must contain a JSON object.",
        )
    safe_value = _safe_json_value(
        value,
        path="voice_config",
    )
    normalized: dict[str, Any] = {}
    for speaker, config in safe_value.items():
        label = _require_text(speaker, "voice_config speaker")
        if not isinstance(config, dict):
            raise AnnotatedScriptImportValidationError(
                "invalid_voice_config",
                f"Voice configuration for {label!r} must be a JSON object.",
            )
        normalized[label] = copy.deepcopy(config)
    return normalized


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _safe_bundle_filename(name: str | None, created_at_utc: str) -> str:
    if name is None:
        timestamp = re.sub(r"[^0-9]", "", created_at_utc)[:14]
        return f"annotated-script-{timestamp or 'bundle'}.zip"
    normalized = _require_text(name, "bundle_name")
    if (
        Path(normalized).name != normalized
        or not SAFE_FILENAME_PATTERN.fullmatch(normalized)
    ):
        raise AnnotatedScriptImportValidationError(
            "unsafe_bundle_name",
            (
                "bundle_name must be a confined filename using letters, "
                "numbers, dot, dash, or underscore."
            ),
        )
    return normalized if normalized.endswith(".zip") else f"{normalized}.zip"


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    return info


def create_annotated_script_bundle(
    *,
    output_dir: str | Path,
    entries: list[dict[str, str]],
    application_version: str,
    metadata: dict[str, Any] | None = None,
    voice_config: dict[str, Any] | None = None,
    source_fingerprint: str | None = None,
    bundle_name: str | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    normalized_entries = _validate_entries(entries)
    normalized_metadata, _ = _validate_metadata(
        metadata,
        entries=normalized_entries,
        source_text=None,
    )
    normalized_voice = _validate_voice_config(voice_config)
    version = _require_text(application_version, "application_version")
    source_claim = None
    if source_fingerprint is not None:
        source_claim = _validate_fingerprint(
            source_fingerprint,
            "source_fingerprint",
        )
    metadata_source = (
        normalized_metadata.get("source")
        if normalized_metadata is not None
        else None
    )
    metadata_source_fingerprint = (
        metadata_source.get("fingerprint")
        if isinstance(metadata_source, dict)
        else None
    )
    if metadata_source_fingerprint is not None:
        metadata_source_fingerprint = _validate_fingerprint(
            metadata_source_fingerprint,
            "metadata.source.fingerprint",
        )
        if source_claim is None:
            source_claim = metadata_source_fingerprint
        elif source_claim != metadata_source_fingerprint:
            raise AnnotatedScriptImportValidationError(
                "bundle_source_mismatch",
                "The requested bundle source fingerprint does not match its metadata.",
            )
    created = _require_text(
        created_at_utc or utc_timestamp(),
        "created_at_utc",
    )
    members = {SCRIPT_MEMBER}
    if normalized_metadata is not None:
        members.add(METADATA_MEMBER)
    if normalized_voice is not None:
        members.add(VOICE_CONFIG_MEMBER)
    final_members = sorted({MANIFEST_MEMBER, *members})
    manifest_seed = {
        "schema_version": IMPORT_BUNDLE_SCHEMA_VERSION,
        "bundle_type": IMPORT_BUNDLE_TYPE,
        "application_version": version,
        "created_at_utc": created,
        "members": final_members,
        "source_fingerprint": source_claim,
        "script_fingerprint": fingerprint_value(normalized_entries),
        "metadata_fingerprint": (
            fingerprint_value(normalized_metadata)
            if normalized_metadata is not None
            else None
        ),
        "voice_config_fingerprint": (
            fingerprint_value(normalized_voice)
            if normalized_voice is not None
            else None
        ),
    }
    manifest = {
        **manifest_seed,
        "bundle_id": "script_bundle_" + fingerprint_value(manifest_seed)[:24],
    }
    payloads = {
        MANIFEST_MEMBER: _json_bytes(manifest),
        SCRIPT_MEMBER: _json_bytes(normalized_entries),
    }
    if normalized_metadata is not None:
        payloads[METADATA_MEMBER] = _json_bytes(normalized_metadata)
    if normalized_voice is not None:
        payloads[VOICE_CONFIG_MEMBER] = _json_bytes(normalized_voice)
    for member, payload in payloads.items():
        if len(payload) > MAX_ARCHIVE_MEMBER_BYTES:
            raise AnnotatedScriptImportValidationError(
                "bundle_too_large",
                f"Bundle member {member!r} exceeds the size limit.",
            )
    if sum(len(payload) for payload in payloads.values()) > MAX_ARCHIVE_TOTAL_BYTES:
        raise AnnotatedScriptImportValidationError(
            "bundle_too_large",
            "The annotated-script bundle exceeds the total size limit.",
        )
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    filename = _safe_bundle_filename(bundle_name, created)
    target = directory / filename
    temporary = target.with_name(target.name + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w") as archive:
            for member in sorted(payloads):
                archive.writestr(_zip_info(member), payloads[member])
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return {
        "path": str(target),
        "filename": filename,
        "manifest": copy.deepcopy(manifest),
        "size_bytes": target.stat().st_size,
    }


def _validate_archive_member_name(name: str) -> None:
    pure = PurePosixPath(name)
    if (
        not name
        or pure.is_absolute()
        or ".." in pure.parts
        or "\\" in name
        or name.startswith("/")
    ):
        raise AnnotatedScriptImportValidationError(
            "unsafe_archive_member",
            f"Unsafe archive member path: {name!r}.",
        )


def _read_bundle_members(path: Path) -> dict[str, bytes]:
    members: dict[str, bytes] = {}
    total = 0
    try:
        with zipfile.ZipFile(path, "r") as archive:
            for info in archive.infolist():
                _validate_archive_member_name(info.filename)
                if info.filename in members:
                    raise AnnotatedScriptImportValidationError(
                        "duplicate_archive_member",
                        f"Duplicate archive member: {info.filename}.",
                    )
                if info.is_dir():
                    raise AnnotatedScriptImportValidationError(
                        "unexpected_archive_member",
                        "Annotated-script bundles may not contain directories.",
                    )
                file_mode = (info.external_attr >> 16) & 0o170000
                if file_mode == stat.S_IFLNK:
                    raise AnnotatedScriptImportValidationError(
                        "archive_symlink",
                        f"Archive member {info.filename!r} is a symbolic link.",
                    )
                if info.flag_bits & 0x1:
                    raise AnnotatedScriptImportValidationError(
                        "encrypted_archive_member",
                        "Encrypted annotated-script members are not supported.",
                    )
                if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                    raise AnnotatedScriptImportValidationError(
                        "bundle_too_large",
                        f"Archive member {info.filename!r} exceeds the size limit.",
                    )
                total += info.file_size
                if total > MAX_ARCHIVE_TOTAL_BYTES:
                    raise AnnotatedScriptImportValidationError(
                        "bundle_too_large",
                        "The annotated-script bundle exceeds the total size limit.",
                    )
                if (
                    info.file_size > 1024 * 1024
                    and info.compress_size > 0
                    and info.file_size / info.compress_size > 200
                ):
                    raise AnnotatedScriptImportValidationError(
                        "suspicious_compression_ratio",
                        f"Archive member {info.filename!r} has an unsafe compression ratio.",
                    )
                payload = archive.read(info)
                if len(payload) > MAX_ARCHIVE_MEMBER_BYTES:
                    raise AnnotatedScriptImportValidationError(
                        "bundle_too_large",
                        f"Archive member {info.filename!r} exceeds the size limit.",
                    )
                members[info.filename] = payload
    except zipfile.BadZipFile as exc:
        raise AnnotatedScriptImportValidationError(
            "invalid_bundle",
            "The annotated-script bundle is not a valid ZIP archive.",
        ) from exc
    actual = set(members)
    if not REQUIRED_BUNDLE_MEMBERS.issubset(actual):
        missing = sorted(REQUIRED_BUNDLE_MEMBERS - actual)
        raise AnnotatedScriptImportValidationError(
            "missing_bundle_member",
            "The annotated-script bundle is missing: " + ", ".join(missing) + ".",
        )
    unexpected = sorted(actual - ALLOWED_BUNDLE_MEMBERS)
    if unexpected:
        raise AnnotatedScriptImportValidationError(
            "unexpected_bundle_member",
            "The annotated-script bundle contains unexpected members: "
            + ", ".join(unexpected)
            + ".",
        )
    return members


def _parse_json_bytes(payload: bytes, name: str) -> Any:
    try:
        return json.loads(payload.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise AnnotatedScriptImportValidationError(
            "invalid_encoding",
            f"{name} must be UTF-8 JSON.",
        ) from exc
    except json.JSONDecodeError as exc:
        raise AnnotatedScriptImportValidationError(
            "invalid_json",
            f"{name} does not contain valid JSON.",
        ) from exc


def _read_direct_json(path: Path) -> Any:
    if path.stat().st_size > MAX_DIRECT_JSON_BYTES:
        raise AnnotatedScriptImportValidationError(
            "script_too_large",
            "The annotated-script JSON exceeds the supported size limit.",
        )
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except UnicodeDecodeError as exc:
        raise AnnotatedScriptImportValidationError(
            "invalid_encoding",
            "The annotated-script file must be UTF-8 JSON.",
        ) from exc
    except json.JSONDecodeError as exc:
        raise AnnotatedScriptImportValidationError(
            "invalid_json",
            "The annotated-script file does not contain valid JSON.",
        ) from exc


def _read_import_payload(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.is_file():
        raise AnnotatedScriptImportValidationError(
            "import_missing",
            f"Annotated-script import was not found: {target}.",
        )
    suffix = target.suffix.casefold()
    if suffix == ".json":
        return {
            "format": "json",
            "filename": target.name,
            "entries": _read_direct_json(target),
            "metadata": None,
            "voice_config": None,
            "manifest": None,
        }
    if suffix != ".zip":
        raise AnnotatedScriptImportValidationError(
            "unsupported_import_format",
            "Annotated-script imports must be a JSON array or Alexandria ZIP bundle.",
        )
    members = _read_bundle_members(target)
    manifest = _parse_json_bytes(members[MANIFEST_MEMBER], MANIFEST_MEMBER)
    if not isinstance(manifest, dict):
        raise AnnotatedScriptImportValidationError(
            "invalid_bundle_manifest",
            "manifest.json must contain a JSON object.",
        )
    manifest = _safe_json_value(
        manifest,
        path="manifest",
    )
    if manifest.get("schema_version") != IMPORT_BUNDLE_SCHEMA_VERSION:
        raise AnnotatedScriptImportValidationError(
            "unsupported_bundle_schema",
            "Unsupported annotated-script bundle schema.",
        )
    if manifest.get("bundle_type") != IMPORT_BUNDLE_TYPE:
        raise AnnotatedScriptImportValidationError(
            "invalid_bundle_manifest",
            "The ZIP is not an Alexandria annotated-script bundle.",
        )
    _require_text(
        manifest.get("application_version"),
        "manifest.application_version",
    )
    _require_text(
        manifest.get("created_at_utc"),
        "manifest.created_at_utc",
    )
    if manifest.get("members") != sorted(members):
        raise AnnotatedScriptImportValidationError(
            "invalid_bundle_manifest",
            "The manifest member list does not match the bundle.",
        )
    entries = _parse_json_bytes(members[SCRIPT_MEMBER], SCRIPT_MEMBER)
    metadata = (
        _parse_json_bytes(members[METADATA_MEMBER], METADATA_MEMBER)
        if METADATA_MEMBER in members
        else None
    )
    voice_config = (
        _parse_json_bytes(members[VOICE_CONFIG_MEMBER], VOICE_CONFIG_MEMBER)
        if VOICE_CONFIG_MEMBER in members
        else None
    )
    fingerprint_fields = {
        "script_fingerprint": fingerprint_value(entries),
        "metadata_fingerprint": (
            fingerprint_value(metadata)
            if metadata is not None
            else None
        ),
        "voice_config_fingerprint": (
            fingerprint_value(voice_config)
            if voice_config is not None
            else None
        ),
    }
    for field, actual in fingerprint_fields.items():
        if manifest.get(field) != actual:
            raise AnnotatedScriptImportValidationError(
                "bundle_fingerprint_mismatch",
                f"{field} does not match the bundled content.",
            )
    source_claim = manifest.get("source_fingerprint")
    if source_claim is not None:
        _validate_fingerprint(source_claim, "manifest.source_fingerprint")
    manifest_seed = {
        key: copy.deepcopy(value)
        for key, value in manifest.items()
        if key != "bundle_id"
    }
    expected_id = "script_bundle_" + fingerprint_value(manifest_seed)[:24]
    if manifest.get("bundle_id") != expected_id:
        raise AnnotatedScriptImportValidationError(
            "bundle_fingerprint_mismatch",
            "The bundle identifier does not match the manifest.",
        )
    return {
        "format": "zip",
        "filename": target.name,
        "entries": entries,
        "metadata": metadata,
        "voice_config": voice_config,
        "manifest": manifest,
    }


def inspect_annotated_script_import(
    *,
    import_path: str | Path,
    source_text: str | None = None,
    current_script_fingerprint: str | None = None,
    checkpoint_status: str = "none",
    generated_audio_count: int = 0,
) -> dict[str, Any]:
    if source_text is not None and not isinstance(source_text, str):
        raise AnnotatedScriptImportValidationError(
            "invalid_source",
            "source_text must be text when supplied.",
        )
    if current_script_fingerprint is not None:
        _validate_fingerprint(
            current_script_fingerprint,
            "current_script_fingerprint",
        )
    if checkpoint_status not in CHECKPOINT_STATES:
        raise AnnotatedScriptImportValidationError(
            "invalid_checkpoint_status",
            f"Unsupported checkpoint status: {checkpoint_status!r}.",
        )
    if (
        not isinstance(generated_audio_count, int)
        or isinstance(generated_audio_count, bool)
        or generated_audio_count < 0
    ):
        raise AnnotatedScriptImportValidationError(
            "invalid_audio_count",
            "generated_audio_count must be a non-negative integer.",
        )
    payload = _read_import_payload(import_path)
    entries = _validate_entries(payload["entries"])
    metadata, metadata_warnings = _validate_metadata(
        payload["metadata"],
        entries=entries,
        source_text=source_text,
    )
    voice_config = _validate_voice_config(payload["voice_config"])
    warnings = list(metadata_warnings)
    manifest = payload["manifest"]
    source_claim = (
        manifest.get("source_fingerprint")
        if isinstance(manifest, dict)
        else None
    )
    if source_text is not None:
        source_fingerprint = fingerprint_text(source_text)
        if source_claim is not None and source_claim != source_fingerprint:
            raise AnnotatedScriptImportValidationError(
                "bundle_source_mismatch",
                "The bundle source fingerprint does not match the selected source.",
            )
        audit = audit_script_chunk(source_text, entries)
        if not audit.passed:
            raise AnnotatedScriptImportValidationError(
                "source_fidelity_failed",
                "The imported script failed Alexandria's source-fidelity audit.",
                details={"audit": audit.to_dict()},
            )
        provenance_status = "verified"
        provenance_label = "Imported — source fidelity verified"
        audit_summary = audit.to_dict()
    else:
        source_fingerprint = None
        provenance_status = "unverified"
        provenance_label = "Imported — source fidelity not verified"
        audit_summary = None
        warnings.append(
            "No selected source was available, so source fidelity was not verified."
        )
    summary = _script_summary(entries)
    snapshot = {
        "current_script_fingerprint": current_script_fingerprint,
        "checkpoint_status": checkpoint_status,
        "generated_audio_count": generated_audio_count,
    }
    consequences = _import_consequences(
        metadata=metadata,
        voice_config=voice_config,
        checkpoint_status=checkpoint_status,
        generated_audio_count=generated_audio_count,
    )
    candidate = {
        "schema_version": 1,
        "format": payload["format"],
        "filename": payload["filename"],
        "entries": entries,
        "metadata": metadata,
        "voice_config": voice_config,
        "manifest": copy.deepcopy(manifest),
        "summary": summary,
        "provenance": {
            "status": provenance_status,
            "label": provenance_label,
            "source_fingerprint": source_fingerprint,
            "bundle_source_claim": source_claim,
            "audit": audit_summary,
        },
        "warnings": warnings,
        "snapshot": snapshot,
        "consequences": consequences,
    }
    candidate["import_fingerprint"] = fingerprint_value(candidate)
    return candidate


def _revalidate_candidate(candidate: Any) -> dict[str, Any]:
    if not isinstance(candidate, dict) or candidate.get("schema_version") != 1:
        raise AnnotatedScriptImportValidationError(
            "invalid_candidate",
            "The annotated-script import candidate is invalid.",
        )
    protected = copy.deepcopy(candidate)
    supplied_fingerprint = protected.pop("import_fingerprint", None)
    try:
        expected_fingerprint = fingerprint_value(protected)
    except (TypeError, ValueError) as exc:
        raise AnnotatedScriptImportValidationError(
            "invalid_candidate",
            "The import candidate contains a non-JSON value.",
        ) from exc
    if supplied_fingerprint != expected_fingerprint:
        raise AnnotatedScriptImportValidationError(
            "candidate_fingerprint_mismatch",
            "The import candidate changed after inspection.",
        )
    import_format = candidate.get("format")
    if import_format not in {"json", "zip"}:
        raise AnnotatedScriptImportValidationError(
            "invalid_candidate",
            "The import candidate format is invalid.",
        )
    filename = _require_text(
        candidate.get("filename"),
        "candidate.filename",
    )
    if Path(filename).name != filename:
        raise AnnotatedScriptImportValidationError(
            "invalid_candidate",
            "The import candidate filename is not confined.",
        )
    entries = _validate_entries(candidate.get("entries"))
    metadata, _ = _validate_metadata(
        candidate.get("metadata"),
        entries=entries,
        source_text=None,
    )
    voice_config = _validate_voice_config(candidate.get("voice_config"))
    provenance = candidate.get("provenance")
    if not isinstance(provenance, dict):
        raise AnnotatedScriptImportValidationError(
            "invalid_candidate",
            "The import candidate provenance is missing.",
        )
    provenance_status = provenance.get("status")
    source_fingerprint = provenance.get("source_fingerprint")
    audit = provenance.get("audit")
    if provenance_status == "verified":
        _validate_fingerprint(
            source_fingerprint,
            "candidate.provenance.source_fingerprint",
        )
        if not isinstance(audit, dict) or audit.get("passed") is not True:
            raise AnnotatedScriptImportValidationError(
                "invalid_candidate",
                "Verified import provenance requires a passing fidelity audit.",
            )
        expected_label = "Imported — source fidelity verified"
    elif provenance_status == "unverified":
        if source_fingerprint is not None or audit is not None:
            raise AnnotatedScriptImportValidationError(
                "invalid_candidate",
                "Unverified import provenance cannot claim a source audit.",
            )
        expected_label = "Imported — source fidelity not verified"
    else:
        raise AnnotatedScriptImportValidationError(
            "invalid_candidate",
            "The import candidate provenance status is invalid.",
        )
    if provenance.get("label") != expected_label:
        raise AnnotatedScriptImportValidationError(
            "invalid_candidate",
            "The import candidate provenance label is invalid.",
        )
    source_claim = provenance.get("bundle_source_claim")
    if source_claim is not None:
        _validate_fingerprint(
            source_claim,
            "candidate.provenance.bundle_source_claim",
        )
    warnings = candidate.get("warnings")
    if (
        not isinstance(warnings, list)
        or not all(
            isinstance(warning, str)
            and warning
            and not _contains_unsafe_control(warning)
            for warning in warnings
        )
    ):
        raise AnnotatedScriptImportValidationError(
            "invalid_candidate",
            "The import candidate warnings are invalid.",
        )
    snapshot = candidate.get("snapshot")
    if not isinstance(snapshot, dict):
        raise AnnotatedScriptImportValidationError(
            "invalid_candidate",
            "The import candidate project snapshot is missing.",
        )
    current_fingerprint = snapshot.get("current_script_fingerprint")
    if current_fingerprint is not None:
        _validate_fingerprint(
            current_fingerprint,
            "candidate.snapshot.current_script_fingerprint",
        )
    checkpoint_status = snapshot.get("checkpoint_status")
    if checkpoint_status not in CHECKPOINT_STATES:
        raise AnnotatedScriptImportValidationError(
            "invalid_candidate",
            "The import candidate checkpoint status is invalid.",
        )
    generated_audio_count = snapshot.get("generated_audio_count")
    if (
        not isinstance(generated_audio_count, int)
        or isinstance(generated_audio_count, bool)
        or generated_audio_count < 0
    ):
        raise AnnotatedScriptImportValidationError(
            "invalid_candidate",
            "The import candidate generated-audio count is invalid.",
        )
    expected_summary = _script_summary(entries)
    if candidate.get("summary") != expected_summary:
        raise AnnotatedScriptImportValidationError(
            "invalid_candidate",
            "The import candidate summary does not match its script.",
        )
    expected_consequences = _import_consequences(
        metadata=metadata,
        voice_config=voice_config,
        checkpoint_status=checkpoint_status,
        generated_audio_count=generated_audio_count,
    )
    if candidate.get("consequences") != expected_consequences:
        raise AnnotatedScriptImportValidationError(
            "invalid_candidate",
            "The import candidate consequences do not match its project snapshot.",
        )
    return {
        **copy.deepcopy(candidate),
        "entries": entries,
        "metadata": metadata,
        "voice_config": voice_config,
        "summary": expected_summary,
        "consequences": expected_consequences,
    }


def build_annotated_script_import_plan(
    *,
    candidate: dict[str, Any],
    current_script_fingerprint: str | None,
    checkpoint_status: str,
    checkpoint_decision: str | None = None,
) -> dict[str, Any]:
    normalized = _revalidate_candidate(candidate)
    if current_script_fingerprint is not None:
        _validate_fingerprint(
            current_script_fingerprint,
            "current_script_fingerprint",
        )
    snapshot = normalized["snapshot"]
    if snapshot.get("current_script_fingerprint") != current_script_fingerprint:
        raise AnnotatedScriptImportConflictError(
            "current_script_changed",
            "The current annotated script changed after the import was inspected.",
        )
    if checkpoint_status not in CHECKPOINT_STATES:
        raise AnnotatedScriptImportValidationError(
            "invalid_checkpoint_status",
            f"Unsupported checkpoint status: {checkpoint_status!r}.",
        )
    if snapshot.get("checkpoint_status") != checkpoint_status:
        raise AnnotatedScriptImportConflictError(
            "checkpoint_changed",
            "The generation checkpoint changed after the import was inspected.",
        )
    if checkpoint_status == "none":
        if checkpoint_decision not in {None, "keep"}:
            raise AnnotatedScriptImportValidationError(
                "unnecessary_checkpoint_decision",
                "There is no generation checkpoint to discard or cancel.",
            )
        decision = "keep"
    else:
        if checkpoint_decision is None:
            raise AnnotatedScriptImportConflictError(
                "checkpoint_decision_required",
                "Choose whether to keep, discard, or cancel the existing generation checkpoint.",
            )
        if checkpoint_decision not in CHECKPOINT_DECISIONS:
            raise AnnotatedScriptImportValidationError(
                "invalid_checkpoint_decision",
                "checkpoint_decision must be keep, discard, or cancel.",
            )
        decision = checkpoint_decision
    if decision == "cancel":
        return {
            "status": "cancelled",
            "import_fingerprint": normalized["import_fingerprint"],
            "actions": [],
            "warnings": copy.deepcopy(normalized["warnings"]),
        }
    backup_files = [
        "annotated_script.json",
        "annotated_script.meta.json",
        "voice_config.json",
        "chunks.json",
        "audio_validity.json",
    ]
    if checkpoint_status != "none":
        backup_files.append("generation_state.json")
    actions = [
        {
            "action": "backup",
            "files": backup_files,
            "atomic_rollback_required": True,
        },
        {
            "action": "replace_script",
            "entry_count": normalized["summary"]["entry_count"],
            "script_fingerprint": fingerprint_value(normalized["entries"]),
        },
        {
            "action": (
                "replace_metadata"
                if normalized["metadata"] is not None
                else "remove_metadata"
            ),
        },
        {
            "action": (
                "replace_voice_config"
                if normalized["voice_config"] is not None
                else "preserve_voice_config"
            ),
        },
        {
            "action": "rebuild_chunks",
            "initial_status": "pending",
            "preserve_audio_files": True,
            "mark_prior_audio_stale": (
                normalized["snapshot"]["generated_audio_count"] > 0
            ),
        },
        {
            "action": "checkpoint",
            "decision": decision,
            "status": checkpoint_status,
        },
    ]
    warnings = copy.deepcopy(normalized["warnings"])
    if decision == "keep" and checkpoint_status != "none":
        warnings.append(
            "The existing generation checkpoint will be retained and may be "
            "incompatible with the imported script."
        )
    if normalized["voice_config"] is None:
        warnings.append(
            "The existing voice configuration will be preserved; newly imported "
            "speakers may still require voice assignment."
        )
    plan_seed = {
        "import_fingerprint": normalized["import_fingerprint"],
        "current_script_fingerprint": current_script_fingerprint,
        "checkpoint_status": checkpoint_status,
        "checkpoint_decision": decision,
        "actions": actions,
    }
    return {
        "status": "ready",
        "plan_id": "script_import_" + fingerprint_value(plan_seed)[:24],
        "import_fingerprint": normalized["import_fingerprint"],
        "summary": copy.deepcopy(normalized["summary"]),
        "provenance": copy.deepcopy(normalized["provenance"]),
        "actions": actions,
        "warnings": warnings,
        "consequences": copy.deepcopy(normalized["consequences"]),
    }
