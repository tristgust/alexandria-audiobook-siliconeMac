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
from llm_schemas import (
    ContractValidationError,
    get_schema,
    validate_contract,
)


HANDOFF_SCHEMA_VERSION = 1
EXPECTED_MEMBERS = frozenset(
    {
        "manifest.json",
        "prompt.md",
        "input.json",
        "schema.json",
    }
)
DEFAULT_RESULT_FILENAME = "result.json"
MAX_MEMBER_BYTES = 24 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 48 * 1024 * 1024
MAX_RESULT_BYTES = 24 * 1024 * 1024
MAX_JSON_DEPTH = 32
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SAFE_BUNDLE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")


_TASK_INPUT_CONTRACTS: dict[str, dict[str, frozenset[str]]] = {
    "script_generation": {
        "required": frozenset({"source_text"}),
        "allowed": frozenset(
            {
                "source_text",
                "part_number",
                "part_count",
                "previous_entries",
                "source_context",
                "generation_constraints",
            }
        ),
    },
    "script_review": {
        "required": frozenset({"entries"}),
        "allowed": frozenset(
            {
                "entries",
                "context_before",
                "context_after",
                "review_constraints",
            }
        ),
    },
    "roster_discovery": {
        "required": frozenset({"source_passage"}),
        "allowed": frozenset(
            {
                "source_passage",
                "passage_number",
                "passage_count",
                "existing_observations",
            }
        ),
    },
    "roster_reconciliation": {
        "required": frozenset({"observations"}),
        "allowed": frozenset(
            {
                "observations",
                "source_summary",
                "existing_roster",
            }
        ),
    },
    "persona_generation": {
        "required": frozenset({"speaker", "sample_lines"}),
        "allowed": frozenset(
            {
                "speaker",
                "sample_lines",
                "narrator_context",
                "roster_entry",
                "advanced",
            }
        ),
    },
    "visual_discovery": {
        "required": frozenset({"roster_entry", "source_passage"}),
        "allowed": frozenset(
            {
                "roster_entry",
                "source_passage",
                "existing_dossier",
                "passage_number",
                "passage_count",
            }
        ),
    },
}

_TASK_CONTRACTS = {
    "script_generation": "script",
    "script_review": "review",
    "roster_discovery": "roster_discovery",
    "roster_reconciliation": "roster_reconciliation",
    "persona_generation": "persona",
    "visual_discovery": "visual_discovery",
}

_SENSITIVE_KEY_FRAGMENTS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "access_token",
        "refresh_token",
        "password",
        "secret",
        "cookie",
        "token_path",
        "hf_token",
        "openai_key",
    }
)


class ChatGPTHandoffError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class HandoffValidationError(ChatGPTHandoffError):
    pass


class HandoffConflictError(ChatGPTHandoffError):
    pass


def utc_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HandoffValidationError(
            "invalid_text",
            f"{field} must be non-empty text.",
        )
    if _contains_unsafe_control(value):
        raise HandoffValidationError(
            "unsafe_text",
            f"{field} contains an unsupported control character.",
        )
    return value.strip()


def _contains_unsafe_control(value: str) -> bool:
    return any(
        (
            ord(character) < 32
            and character not in {"\t", "\n", "\r"}
        )
        or 0xD800 <= ord(character) <= 0xDFFF
        for character in value
    )


def _validate_fingerprint(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise HandoffValidationError(
            "invalid_fingerprint",
            f"{field} must be a lowercase SHA-256 fingerprint.",
        )
    return value


def _safe_json_value(value: Any, *, path: str = "$", depth: int = 0) -> Any:
    if depth > MAX_JSON_DEPTH:
        raise HandoffValidationError(
            "json_too_deep",
            f"{path} exceeds the maximum JSON nesting depth.",
        )
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise HandoffValidationError(
                "invalid_number",
                f"{path} contains a non-finite number.",
            )
        return value
    if isinstance(value, str):
        if _contains_unsafe_control(value):
            raise HandoffValidationError(
                "unsafe_text",
                f"{path} contains an unsupported control character.",
            )
        return value
    if isinstance(value, list):
        return [
            _safe_json_value(item, path=f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise HandoffValidationError(
                    "invalid_json_key",
                    f"{path} contains a non-text or empty key.",
                )
            folded = key.casefold().replace("-", "_").replace(" ", "_")
            if any(fragment in folded for fragment in _SENSITIVE_KEY_FRAGMENTS):
                raise HandoffValidationError(
                    "sensitive_field",
                    f"{path}.{key} is not permitted in a ChatGPT handoff.",
                )
            normalized[key] = _safe_json_value(
                item,
                path=f"{path}.{key}",
                depth=depth + 1,
            )
        return normalized
    raise HandoffValidationError(
        "non_json_value",
        f"{path} contains a value that cannot be represented in JSON.",
    )


def _task_contract(task_type: str) -> dict[str, frozenset[str]]:
    normalized = _require_text(task_type, "task_type")
    contract = _TASK_INPUT_CONTRACTS.get(normalized)
    if contract is None:
        raise HandoffValidationError(
            "unsupported_task",
            f"Unsupported ChatGPT handoff task: {normalized!r}.",
        )
    return contract


def _validate_task_input(task_type: str, value: Any) -> dict[str, Any]:
    contract = _task_contract(task_type)
    if not isinstance(value, dict):
        raise HandoffValidationError(
            "invalid_input",
            "input_payload must be a JSON object.",
        )
    normalized = _safe_json_value(value, path="input_payload")
    keys = set(normalized)
    missing = sorted(contract["required"] - keys)
    unexpected = sorted(keys - contract["allowed"])
    if missing:
        raise HandoffValidationError(
            "missing_input_fields",
            "Missing required handoff input field(s): " + ", ".join(missing) + ".",
        )
    if unexpected:
        raise HandoffValidationError(
            "unexpected_input_fields",
            "Unexpected handoff input field(s): " + ", ".join(unexpected) + ".",
        )
    for key in contract["required"]:
        required_value = normalized[key]
        if required_value in (None, "", [], {}):
            raise HandoffValidationError(
                "empty_input_field",
                f"Required handoff input field {key!r} is empty.",
            )
    return normalized


def _validate_output_schema(task_type: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HandoffValidationError(
            "invalid_schema",
            "output_schema must be a JSON object.",
        )
    normalized = _safe_json_value(value, path="output_schema")
    schema_type = normalized.get("type")
    if schema_type not in {"array", "object"}:
        raise HandoffValidationError(
            "invalid_schema",
            "output_schema must declare a root type of object or array.",
        )
    contract = _TASK_CONTRACTS[task_type]
    canonical = get_schema(contract)
    if fingerprint_value(normalized) != fingerprint_value(canonical):
        raise HandoffValidationError(
            "schema_contract_mismatch",
            f"output_schema does not match Alexandria's {contract!r} stage contract.",
        )
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


def _safe_bundle_name(task_type: str, bundle_name: str | None, created_at_utc: str) -> str:
    if bundle_name is None:
        timestamp = re.sub(r"[^0-9]", "", created_at_utc)[:14]
        return f"{task_type}-{timestamp or 'handoff'}.zip"
    name = _require_text(bundle_name, "bundle_name")
    if Path(name).name != name or not SAFE_BUNDLE_NAME_PATTERN.fullmatch(name):
        raise HandoffValidationError(
            "unsafe_bundle_name",
            (
                "bundle_name must be a confined filename using letters, "
                "numbers, dot, dash, or underscore."
            ),
        )
    return name if name.endswith(".zip") else f"{name}.zip"


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    return info


def _prompt_document(task_type: str, stage_prompt: str, expected_output_filename: str) -> str:
    return (
        "# Alexandria ChatGPT handoff\n\n"
        f"Task: `{task_type}`\n\n"
        "Read `input.json` and `schema.json`. Complete only the requested "
        "structured task. Preserve supplied source wording and identifiers "
        "exactly unless the task instructions explicitly permit a change. "
        "Do not add commentary, markdown fences, or fields outside the schema.\n\n"
        "Return only valid JSON matching `schema.json` and save it as "
        f"`{expected_output_filename}`.\n\n"
        "## Task instructions\n\n"
        f"{stage_prompt.strip()}\n"
    )


def create_handoff_bundle(
    *,
    output_dir: str | Path,
    task_type: str,
    stage_prompt: str,
    input_payload: dict[str, Any],
    output_schema: dict[str, Any],
    application_version: str,
    source_fingerprint: str | None = None,
    artifact_fingerprints: dict[str, str] | None = None,
    expected_output_filename: str = DEFAULT_RESULT_FILENAME,
    bundle_name: str | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    normalized_task = _require_text(task_type, "task_type")
    normalized_input = _validate_task_input(normalized_task, input_payload)
    normalized_schema = _validate_output_schema(normalized_task, output_schema)
    normalized_prompt = _require_text(stage_prompt, "stage_prompt")
    normalized_version = _require_text(application_version, "application_version")
    result_filename = _require_text(expected_output_filename, "expected_output_filename")
    if Path(result_filename).name != result_filename or not result_filename.endswith(".json"):
        raise HandoffValidationError(
            "unsafe_result_filename",
            "expected_output_filename must be a confined JSON filename.",
        )
    normalized_source = None
    if source_fingerprint is not None:
        normalized_source = _validate_fingerprint(source_fingerprint, "source_fingerprint")
    normalized_artifacts: dict[str, str] = {}
    for name, fingerprint in (artifact_fingerprints or {}).items():
        safe_name = _require_text(name, "artifact_fingerprint name")
        if "/" in safe_name or "\\" in safe_name:
            raise HandoffValidationError(
                "unsafe_artifact_name",
                "Artifact fingerprint names must not contain path separators.",
            )
        normalized_artifacts[safe_name] = _validate_fingerprint(
            fingerprint,
            f"artifact_fingerprints.{safe_name}",
        )

    created = _require_text(
        created_at_utc or utc_timestamp(),
        "created_at_utc",
    )
    prompt_document = _prompt_document(
        normalized_task,
        normalized_prompt,
        result_filename,
    )
    manifest_seed = {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "task_type": normalized_task,
        "contract": _TASK_CONTRACTS[normalized_task],
        "application_version": normalized_version,
        "created_at_utc": created,
        "source_fingerprint": normalized_source,
        "artifact_fingerprints": normalized_artifacts,
        "input_fingerprint": fingerprint_value(normalized_input),
        "schema_fingerprint": fingerprint_value(normalized_schema),
        "prompt_fingerprint": fingerprint_text(prompt_document),
        "expected_output_filename": result_filename,
    }
    manifest = {
        **manifest_seed,
        "handoff_id": "handoff_" + fingerprint_value(manifest_seed)[:24],
        "members": sorted(EXPECTED_MEMBERS),
    }
    payloads = {
        "manifest.json": _json_bytes(manifest),
        "prompt.md": prompt_document.encode("utf-8"),
        "input.json": _json_bytes(normalized_input),
        "schema.json": _json_bytes(normalized_schema),
    }
    if any(len(payload) > MAX_MEMBER_BYTES for payload in payloads.values()):
        raise HandoffValidationError(
            "handoff_too_large",
            "A handoff member exceeds the supported size limit.",
        )
    if sum(len(payload) for payload in payloads.values()) > MAX_TOTAL_UNCOMPRESSED_BYTES:
        raise HandoffValidationError(
            "handoff_too_large",
            "The handoff exceeds the supported total size limit.",
        )

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    filename = _safe_bundle_name(normalized_task, bundle_name, created)
    target = directory / filename
    temporary = target.with_name(target.name + ".tmp")
    try:
        with zipfile.ZipFile(temporary, mode="w") as archive:
            for name in sorted(payloads):
                archive.writestr(_zip_info(name), payloads[name])
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


def _validate_member_name(name: str) -> None:
    pure = PurePosixPath(name)
    if (
        not name
        or pure.is_absolute()
        or ".." in pure.parts
        or "\\" in name
        or name.startswith("/")
    ):
        raise HandoffValidationError(
            "unsafe_archive_member",
            f"Unsafe archive member path: {name!r}.",
        )


def _read_zip_members(path: str | Path) -> dict[str, bytes]:
    target = Path(path)
    if not target.is_file():
        raise HandoffValidationError(
            "bundle_missing",
            f"Handoff bundle was not found: {target}.",
        )
    members: dict[str, bytes] = {}
    total_size = 0
    try:
        with zipfile.ZipFile(target, mode="r") as archive:
            for info in archive.infolist():
                _validate_member_name(info.filename)
                if info.filename in members:
                    raise HandoffValidationError(
                        "duplicate_archive_member",
                        f"Duplicate archive member: {info.filename}.",
                    )
                if info.is_dir():
                    raise HandoffValidationError(
                        "unexpected_archive_member",
                        "Handoff bundles may not contain directories.",
                    )
                file_mode = (info.external_attr >> 16) & 0o170000
                if file_mode == stat.S_IFLNK:
                    raise HandoffValidationError(
                        "archive_symlink",
                        f"Archive member {info.filename!r} is a symbolic link.",
                    )
                if info.flag_bits & 0x1:
                    raise HandoffValidationError(
                        "encrypted_archive_member",
                        "Encrypted handoff members are not supported.",
                    )
                if info.file_size > MAX_MEMBER_BYTES:
                    raise HandoffValidationError(
                        "handoff_too_large",
                        f"Archive member {info.filename!r} exceeds the size limit.",
                    )
                total_size += info.file_size
                if total_size > MAX_TOTAL_UNCOMPRESSED_BYTES:
                    raise HandoffValidationError(
                        "handoff_too_large",
                        "The handoff exceeds the supported total size limit.",
                    )
                if (
                    info.file_size > 1024 * 1024
                    and info.compress_size > 0
                    and info.file_size / info.compress_size > 200
                ):
                    raise HandoffValidationError(
                        "suspicious_compression_ratio",
                        f"Archive member {info.filename!r} has an unsafe compression ratio.",
                    )
                payload = archive.read(info)
                if len(payload) > MAX_MEMBER_BYTES:
                    raise HandoffValidationError(
                        "handoff_too_large",
                        f"Archive member {info.filename!r} exceeds the size limit.",
                    )
                members[info.filename] = payload
    except zipfile.BadZipFile as exc:
        raise HandoffValidationError(
            "invalid_bundle",
            "The handoff bundle is not a valid ZIP archive.",
        ) from exc
    if set(members) != EXPECTED_MEMBERS:
        missing = sorted(EXPECTED_MEMBERS - set(members))
        unexpected = sorted(set(members) - EXPECTED_MEMBERS)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise HandoffValidationError(
            "invalid_bundle_members",
            "Invalid handoff bundle members: " + "; ".join(details) + ".",
        )
    return members


def _decode_utf8(payload: bytes, name: str) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HandoffValidationError(
            "invalid_encoding",
            f"{name} must be UTF-8 text.",
        ) from exc


def _parse_json(payload: bytes, name: str) -> Any:
    try:
        return json.loads(_decode_utf8(payload, name))
    except json.JSONDecodeError as exc:
        raise HandoffValidationError(
            "invalid_json",
            f"{name} does not contain valid JSON.",
        ) from exc


def inspect_handoff_bundle(path: str | Path) -> dict[str, Any]:
    members = _read_zip_members(path)
    manifest = _parse_json(members["manifest.json"], "manifest.json")
    input_payload = _parse_json(members["input.json"], "input.json")
    output_schema = _parse_json(members["schema.json"], "schema.json")
    prompt_document = _decode_utf8(members["prompt.md"], "prompt.md")
    if not isinstance(manifest, dict):
        raise HandoffValidationError(
            "invalid_manifest",
            "manifest.json must contain a JSON object.",
        )
    if manifest.get("schema_version") != HANDOFF_SCHEMA_VERSION:
        raise HandoffValidationError(
            "unsupported_manifest_schema",
            "Unsupported ChatGPT handoff manifest schema.",
        )
    task_type = _require_text(
        manifest.get("task_type"),
        "manifest.task_type",
    )
    _require_text(
        manifest.get("application_version"),
        "manifest.application_version",
    )
    _require_text(
        manifest.get("created_at_utc"),
        "manifest.created_at_utc",
    )
    normalized_input = _validate_task_input(task_type, input_payload)
    normalized_schema = _validate_output_schema(task_type, output_schema)
    if manifest.get("contract") != _TASK_CONTRACTS[task_type]:
        raise HandoffValidationError(
            "invalid_manifest",
            "The manifest contract does not match its task type.",
        )
    if manifest.get("members") != sorted(EXPECTED_MEMBERS):
        raise HandoffValidationError(
            "invalid_manifest",
            "The manifest member list does not match the bundle.",
        )
    source_fingerprint = manifest.get("source_fingerprint")
    if source_fingerprint is not None:
        _validate_fingerprint(source_fingerprint, "manifest.source_fingerprint")
    artifact_fingerprints = manifest.get("artifact_fingerprints")
    if not isinstance(artifact_fingerprints, dict):
        raise HandoffValidationError(
            "invalid_manifest",
            "manifest.artifact_fingerprints must be an object.",
        )
    for name, fingerprint in artifact_fingerprints.items():
        _require_text(name, "manifest artifact name")
        _validate_fingerprint(fingerprint, f"manifest.artifact_fingerprints.{name}")
    expected_output_filename = _require_text(
        manifest.get("expected_output_filename"),
        "manifest.expected_output_filename",
    )
    if (
        Path(expected_output_filename).name
        != expected_output_filename
        or not expected_output_filename.endswith(".json")
    ):
        raise HandoffValidationError(
            "invalid_manifest",
            "The manifest expected output filename is unsafe.",
        )
    fingerprint_checks = {
        "input_fingerprint": fingerprint_value(normalized_input),
        "schema_fingerprint": fingerprint_value(normalized_schema),
        "prompt_fingerprint": fingerprint_text(prompt_document),
    }
    for field, actual in fingerprint_checks.items():
        expected = manifest.get(field)
        _validate_fingerprint(expected, f"manifest.{field}")
        if expected != actual:
            raise HandoffValidationError(
                "bundle_fingerprint_mismatch",
                f"{field} does not match the bundled content.",
            )
    manifest_seed = {
        key: copy.deepcopy(value)
        for key, value in manifest.items()
        if key not in {"handoff_id", "members"}
    }
    expected_handoff_id = "handoff_" + fingerprint_value(manifest_seed)[:24]
    if manifest.get("handoff_id") != expected_handoff_id:
        raise HandoffValidationError(
            "bundle_fingerprint_mismatch",
            "The handoff identifier does not match the manifest.",
        )
    return {
        "manifest": copy.deepcopy(manifest),
        "prompt": prompt_document,
        "input": normalized_input,
        "schema": normalized_schema,
    }


def _read_result_json(path: str | Path) -> Any:
    target = Path(path)
    if not target.is_file():
        raise HandoffValidationError(
            "result_missing",
            f"ChatGPT result was not found: {target}.",
        )
    if target.stat().st_size > MAX_RESULT_BYTES:
        raise HandoffValidationError(
            "result_too_large",
            "The ChatGPT result exceeds the supported size limit.",
        )
    try:
        with target.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except UnicodeDecodeError as exc:
        raise HandoffValidationError(
            "invalid_encoding",
            "The ChatGPT result must be UTF-8 JSON.",
        ) from exc
    except json.JSONDecodeError as exc:
        raise HandoffValidationError(
            "invalid_result_json",
            "The ChatGPT result is not valid JSON.",
        ) from exc
    return _safe_json_value(value, path="result")


def validate_handoff_result(
    *,
    bundle_path: str | Path,
    result_path: str | Path,
    current_source_fingerprint: str | None = None,
    current_artifact_fingerprints: dict[str, str] | None = None,
) -> dict[str, Any]:
    handoff = inspect_handoff_bundle(bundle_path)
    manifest = handoff["manifest"]
    expected_source = manifest.get("source_fingerprint")
    if expected_source is not None:
        if current_source_fingerprint is None:
            raise HandoffConflictError(
                "source_fingerprint_required",
                "The current source fingerprint is required before importing this result.",
            )
        current_source = _validate_fingerprint(
            current_source_fingerprint,
            "current_source_fingerprint",
        )
        if current_source != expected_source:
            raise HandoffConflictError(
                "stale_source",
                "The selected source changed after this handoff was exported.",
            )
    current_artifacts = current_artifact_fingerprints or {}
    for name, expected in manifest["artifact_fingerprints"].items():
        current = current_artifacts.get(name)
        if current is None:
            raise HandoffConflictError(
                "artifact_fingerprint_required",
                f"Current fingerprint for artifact {name!r} is required.",
            )
        _validate_fingerprint(current, f"current_artifact_fingerprints.{name}")
        if current != expected:
            raise HandoffConflictError(
                "stale_artifact",
                f"Artifact {name!r} changed after this handoff was exported.",
            )
    result = _read_result_json(result_path)
    if isinstance(result, list):
        root_type = "array"
    elif isinstance(result, dict):
        root_type = "object"
    else:
        root_type = type(result).__name__
    declared_type = handoff["schema"].get("type")
    if root_type != declared_type:
        raise HandoffValidationError(
            "result_root_type_mismatch",
            (
                f"The result root type is {root_type}, but the handoff schema "
                f"requires {declared_type}."
            ),
        )
    try:
        normalized_result = validate_contract(manifest["contract"], result)
    except ContractValidationError as exc:
        raise HandoffValidationError(
            "stage_contract_validation_failed",
            f"The ChatGPT result failed the {manifest['contract']!r} stage contract: {exc}",
        ) from exc
    item_count = len(normalized_result) if isinstance(normalized_result, (list, dict)) else None
    return {
        "handoff_id": manifest["handoff_id"],
        "task_type": manifest["task_type"],
        "expected_output_filename": manifest["expected_output_filename"],
        "result_filename": Path(result_path).name,
        "result": copy.deepcopy(normalized_result),
        "result_fingerprint": fingerprint_value(normalized_result),
        "review": {
            "root_type": root_type,
            "item_count": item_count,
            "source_fingerprint_verified": expected_source is not None,
            "artifact_fingerprints_verified": sorted(manifest["artifact_fingerprints"]),
        },
    }
