from __future__ import annotations

import copy
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import secrets
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit


AUTOMATION_SCHEMA_VERSION = 1
DEFAULT_REVIEW_TTL_SECONDS = 10 * 60
MAX_REVIEW_TTL_SECONDS = 60 * 60
MIN_BEARER_TOKEN_CHARS = 48
_SAFE_CREDENTIAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
_SAFE_TICKET_ID = re.compile(r"^review_[0-9a-f]{32}$")
_SAFE_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{15,191}$")
_STATE_LOCKS_GUARD = threading.Lock()
_STATE_LOCKS: dict[str, threading.RLock] = {}
_AUTH_FAILURES_LOCK = threading.Lock()
_AUTH_FAILURES: dict[str, list[float]] = {}
AUTH_FAILURE_WINDOW_SECONDS = 60
MAX_AUTH_FAILURES_PER_WINDOW = 8

KNOWN_AUTOMATION_SCOPES = frozenset(
    {
        "automation:discover",
        "state:read",
        "work:read",
        "work:cancel",
        "tasks:read",
        "tasks:export",
        "tasks:import",
        "operations:produce",
        "operations:export",
    }
)


class AutomationApiError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = int(status_code)
        self.details = copy.deepcopy(dict(details or {}))


@dataclass(frozen=True)
class AutomationPrincipal:
    credential_id: str
    scopes: frozenset[str]
    token_fingerprint: str
    credential_path: Path


def _state_thread_lock(root: Path) -> threading.RLock:
    key = str(root)
    with _STATE_LOCKS_GUARD:
        return _STATE_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _automation_state_lock(root: Path):
    _ensure_private_directory(root)
    lock_path = root / ".lock"
    with _state_thread_lock(root), lock_path.open("a+b") as handle:
        try:
            lock_path.chmod(0o600)
        except OSError as exc:
            raise AutomationApiError(
                "automation_storage_permissions_invalid",
                "Automation state lock permissions could not be restricted.",
                status_code=500,
            ) from exc
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise AutomationApiError(
            "automation_record_invalid",
            "Automation security record contains an invalid timestamp.",
            status_code=500,
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonical_json_fingerprint(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AutomationApiError(
            "automation_request_invalid",
            "Automation request data is not canonical JSON.",
            status_code=422,
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def default_automation_root() -> Path:
    override = os.environ.get("ALEXANDRIA_AUTOMATION_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".config" / "alexandria" / "automation").resolve()


def default_credential_path() -> Path:
    override = os.environ.get("ALEXANDRIA_AUTOMATION_CREDENTIAL")
    if override:
        return Path(override).expanduser().resolve()
    return default_automation_root() / "credential.json"


def automation_state_root(credential_path: str | Path | None = None) -> Path:
    credential = (
        Path(credential_path).expanduser().resolve()
        if credential_path is not None
        else default_credential_path()
    )
    override = os.environ.get("ALEXANDRIA_AUTOMATION_STATE_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return credential.parent / "state"


def _ensure_private_directory(path: Path) -> None:
    if path.exists() and (not path.is_dir() or path.is_symlink()):
        raise AutomationApiError(
            "automation_storage_invalid",
            "Automation security directory is not a safe local directory.",
            status_code=500,
            details={"path_name": path.name},
        )
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError as exc:
        raise AutomationApiError(
            "automation_storage_permissions_invalid",
            "Automation security directory permissions could not be restricted.",
            status_code=500,
        ) from exc


def _atomic_private_json(path: Path, value: Mapping[str, Any]) -> None:
    _ensure_private_directory(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        path.chmod(0o600)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def _read_private_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise AutomationApiError(
            "automation_credential_missing",
            "Alexandria local automation is not provisioned.",
            status_code=503,
        )
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise AutomationApiError(
            "automation_credential_permissions_invalid",
            "Automation credential permissions are too broad.",
            status_code=503,
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AutomationApiError(
            "automation_credential_invalid",
            "Automation credential could not be read safely.",
            status_code=503,
        ) from exc
    if not isinstance(value, dict):
        raise AutomationApiError(
            "automation_credential_invalid",
            "Automation credential must be a JSON object.",
            status_code=503,
        )
    return value


def provision_automation_credential(
    *,
    path: str | Path | None = None,
    scopes: Iterable[str] = KNOWN_AUTOMATION_SCOPES,
    token: str | None = None,
    credential_id: str | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    target = (
        Path(path).expanduser().resolve()
        if path is not None
        else default_credential_path()
    )
    if target.exists() and not replace:
        raise AutomationApiError(
            "automation_credential_exists",
            "Automation credential already exists.",
            status_code=409,
        )
    selected_scopes = sorted(set(str(item) for item in scopes))
    unknown = sorted(set(selected_scopes) - KNOWN_AUTOMATION_SCOPES)
    if unknown:
        raise AutomationApiError(
            "automation_scope_invalid",
            "Automation credential requested unsupported scopes.",
            status_code=422,
            details={"scopes": unknown},
        )
    resolved_token = token or secrets.token_urlsafe(48)
    if len(resolved_token) < MIN_BEARER_TOKEN_CHARS or any(
        character.isspace() for character in resolved_token
    ):
        raise AutomationApiError(
            "automation_token_invalid",
            "Automation bearer token is too short or malformed.",
            status_code=422,
        )
    resolved_id = credential_id or f"credential_{secrets.token_hex(12)}"
    if not _SAFE_CREDENTIAL_ID.fullmatch(resolved_id):
        raise AutomationApiError(
            "automation_credential_id_invalid",
            "Automation credential ID is invalid.",
            status_code=422,
        )
    record = {
        "schema_version": AUTOMATION_SCHEMA_VERSION,
        "credential_id": resolved_id,
        "token": resolved_token,
        "token_fingerprint": hashlib.sha256(
            resolved_token.encode("utf-8")
        ).hexdigest(),
        "scopes": selected_scopes,
        "created_at_utc": _utc_text(),
        "enabled": True,
    }
    _atomic_private_json(target, record)
    return {**copy.deepcopy(record), "credential_path": str(target)}


def load_automation_credential(
    path: str | Path | None = None,
) -> tuple[dict[str, Any], Path]:
    target = (
        Path(path).expanduser().resolve()
        if path is not None
        else default_credential_path()
    )
    value = _read_private_json(target)
    if value.get("schema_version") != AUTOMATION_SCHEMA_VERSION:
        raise AutomationApiError(
            "automation_credential_invalid",
            "Automation credential schema is unsupported.",
            status_code=503,
        )
    credential_id = str(value.get("credential_id") or "")
    token = str(value.get("token") or "")
    scopes = value.get("scopes")
    if (
        not _SAFE_CREDENTIAL_ID.fullmatch(credential_id)
        or len(token) < MIN_BEARER_TOKEN_CHARS
        or not isinstance(scopes, list)
        or any(not isinstance(item, str) for item in scopes)
        or set(scopes) - KNOWN_AUTOMATION_SCOPES
        or value.get("enabled") is not True
    ):
        raise AutomationApiError(
            "automation_credential_invalid",
            "Automation credential is incomplete or disabled.",
            status_code=503,
        )
    expected = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if value.get("token_fingerprint") != expected:
        raise AutomationApiError(
            "automation_credential_invalid",
            "Automation credential fingerprint does not match its token.",
            status_code=503,
        )
    return copy.deepcopy(value), target


def public_automation_credential(
    path: str | Path | None = None,
) -> dict[str, Any]:
    credential, target = load_automation_credential(path)
    return {
        "schema_version": AUTOMATION_SCHEMA_VERSION,
        "provisioned": True,
        "credential_id": credential["credential_id"],
        "token_fingerprint": credential["token_fingerprint"],
        "scopes": list(credential["scopes"]),
        "credential_filename": target.name,
        "browser_origin_policy": "rejected",
        "network_policy": "loopback_only",
    }


def _validate_loopback_client(client_host: str | None) -> None:
    if not client_host:
        raise AutomationApiError(
            "automation_client_unavailable",
            "Automation client address is unavailable.",
            status_code=403,
        )
    try:
        address = ipaddress.ip_address(client_host)
    except ValueError as exc:
        raise AutomationApiError(
            "automation_loopback_required",
            "Automation accepts only direct loopback clients.",
            status_code=403,
        ) from exc
    if not address.is_loopback:
        raise AutomationApiError(
            "automation_loopback_required",
            "Automation accepts only direct loopback clients.",
            status_code=403,
        )


def _validate_host_header(host_header: str | None) -> None:
    value = str(host_header or "")
    if (
        not value
        or value != value.strip()
        or any(ord(character) < 32 for character in value)
        or any(character in value for character in (",", "@", "/", "\\"))
    ):
        raise AutomationApiError(
            "automation_host_invalid",
            "Automation Host header is invalid.",
            status_code=400,
        )
    try:
        parsed = urlsplit(f"//{value}")
        hostname = (parsed.hostname or "").casefold()
        _ = parsed.port
    except ValueError as exc:
        raise AutomationApiError(
            "automation_host_invalid",
            "Automation Host header is invalid.",
            status_code=400,
        ) from exc
    if hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise AutomationApiError(
            "automation_host_rejected",
            "Automation Host must resolve explicitly to loopback.",
            status_code=403,
        )


def _check_auth_rate_limit(client_host: str) -> None:
    now = time.monotonic()
    cutoff = now - AUTH_FAILURE_WINDOW_SECONDS
    with _AUTH_FAILURES_LOCK:
        recent = [
            value
            for value in _AUTH_FAILURES.get(client_host, [])
            if value >= cutoff
        ]
        if recent:
            _AUTH_FAILURES[client_host] = recent
        else:
            _AUTH_FAILURES.pop(client_host, None)
        if len(recent) >= MAX_AUTH_FAILURES_PER_WINDOW:
            raise AutomationApiError(
                "automation_authentication_rate_limited",
                "Too many failed automation authentication attempts.",
                status_code=429,
            )


def _record_auth_failure(client_host: str) -> None:
    now = time.monotonic()
    cutoff = now - AUTH_FAILURE_WINDOW_SECONDS
    with _AUTH_FAILURES_LOCK:
        recent = [
            value
            for value in _AUTH_FAILURES.get(client_host, [])
            if value >= cutoff
        ]
        recent.append(now)
        _AUTH_FAILURES[client_host] = recent[-MAX_AUTH_FAILURES_PER_WINDOW:]


def _clear_auth_failures(client_host: str) -> None:
    with _AUTH_FAILURES_LOCK:
        _AUTH_FAILURES.pop(client_host, None)


def authorize_automation_request(
    *,
    client_host: str | None,
    host_header: str | None,
    origin_header: str | None,
    authorization_header: str | None,
    required_scopes: Iterable[str] = (),
    credential_path: str | Path | None = None,
    forwarded_headers_present: bool = False,
) -> AutomationPrincipal:
    _validate_loopback_client(client_host)
    _validate_host_header(host_header)
    if forwarded_headers_present:
        raise AutomationApiError(
            "automation_forwarded_request_rejected",
            "Automation does not trust forwarded client or Host headers.",
            status_code=403,
        )
    if origin_header is not None:
        raise AutomationApiError(
            "automation_browser_origin_rejected",
            "Browser-origin automation requests are not accepted.",
            status_code=403,
        )
    _check_auth_rate_limit(str(client_host))
    raw_authorization = str(authorization_header or "")
    if not raw_authorization.startswith("Bearer "):
        _record_auth_failure(str(client_host))
        raise AutomationApiError(
            "automation_authentication_required",
            "A local automation bearer credential is required.",
            status_code=401,
        )
    candidate = raw_authorization[7:]
    if not candidate or candidate != candidate.strip() or " " in candidate:
        _record_auth_failure(str(client_host))
        raise AutomationApiError(
            "automation_authentication_invalid",
            "Automation bearer credential is malformed.",
            status_code=401,
        )
    credential, target = load_automation_credential(credential_path)
    if not secrets.compare_digest(candidate, credential["token"]):
        _record_auth_failure(str(client_host))
        raise AutomationApiError(
            "automation_authentication_invalid",
            "Automation bearer credential is invalid.",
            status_code=401,
        )
    selected = frozenset(str(item) for item in credential["scopes"])
    required = frozenset(str(item) for item in required_scopes)
    missing = sorted(required - selected)
    if missing:
        raise AutomationApiError(
            "automation_scope_required",
            "Automation credential lacks a required scope.",
            status_code=403,
            details={"required_scopes": missing},
        )
    _clear_auth_failures(str(client_host))
    return AutomationPrincipal(
        credential_id=credential["credential_id"],
        scopes=selected,
        token_fingerprint=credential["token_fingerprint"],
        credential_path=target,
    )


def _review_path(root: Path, ticket_id: str) -> Path:
    if not _SAFE_TICKET_ID.fullmatch(ticket_id):
        raise AutomationApiError(
            "automation_review_token_invalid",
            "Automation review token is invalid.",
            status_code=401,
        )
    return root / "reviews" / f"{ticket_id}.json"


def _idempotency_path(root: Path, credential_id: str, key: str) -> Path:
    if not _SAFE_IDEMPOTENCY_KEY.fullmatch(key):
        raise AutomationApiError(
            "automation_idempotency_key_invalid",
            "Idempotency-Key must contain 16 to 192 safe characters.",
            status_code=422,
        )
    digest = hashlib.sha256(f"{credential_id}:{key}".encode("utf-8")).hexdigest()
    return root / "idempotency" / f"{digest}.json"


def create_review_ticket(
    *,
    principal: AutomationPrincipal,
    operation: str,
    required_scope: str,
    request_payload: Mapping[str, Any],
    reviewed_payload: Mapping[str, Any],
    staged_files: list[dict[str, Any]] | None = None,
    ttl_seconds: int = DEFAULT_REVIEW_TTL_SECONDS,
) -> dict[str, Any]:
    if required_scope not in principal.scopes:
        raise AutomationApiError(
            "automation_scope_required",
            "Automation credential lacks the reviewed operation scope.",
            status_code=403,
            details={"required_scopes": [required_scope]},
        )
    ttl = int(ttl_seconds)
    if not 1 <= ttl <= MAX_REVIEW_TTL_SECONDS:
        raise AutomationApiError(
            "automation_review_ttl_invalid",
            "Automation review lifetime is invalid.",
            status_code=422,
        )
    ticket_id = f"review_{secrets.token_hex(16)}"
    secret = secrets.token_urlsafe(32)
    token = f"{ticket_id}.{secret}"
    now = _utc_now()
    expires = now + timedelta(seconds=ttl)
    state_root = automation_state_root(principal.credential_path)
    record = {
        "schema_version": AUTOMATION_SCHEMA_VERSION,
        "ticket_id": ticket_id,
        "token_hash": hashlib.sha256(secret.encode("utf-8")).hexdigest(),
        "credential_id": principal.credential_id,
        "operation": str(operation),
        "required_scope": str(required_scope),
        "request_payload": copy.deepcopy(dict(request_payload)),
        "request_fingerprint": canonical_json_fingerprint(request_payload),
        "reviewed_payload": copy.deepcopy(dict(reviewed_payload)),
        "reviewed_fingerprint": canonical_json_fingerprint(reviewed_payload),
        "staged_files": copy.deepcopy(list(staged_files or [])),
        "created_at_utc": _utc_text(now),
        "expires_at_utc": _utc_text(expires),
        "consumed_at_utc": None,
        "consumed": False,
    }
    with _automation_state_lock(state_root):
        _atomic_private_json(_review_path(state_root, ticket_id), record)
    return {
        "schema_version": AUTOMATION_SCHEMA_VERSION,
        "review_token": token,
        "operation": record["operation"],
        "required_scope": record["required_scope"],
        "request_fingerprint": record["request_fingerprint"],
        "reviewed_fingerprint": record["reviewed_fingerprint"],
        "expires_at_utc": record["expires_at_utc"],
    }


def inspect_review_ticket(
    review_token: str,
    *,
    principal: AutomationPrincipal,
) -> tuple[dict[str, Any], Path, str]:
    value = str(review_token or "")
    if "." not in value:
        raise AutomationApiError(
            "automation_review_token_invalid",
            "Automation review token is invalid.",
            status_code=401,
        )
    ticket_id, secret = value.split(".", 1)
    state_root = automation_state_root(principal.credential_path)
    path = _review_path(state_root, ticket_id)
    record = _read_private_json(path)
    if record.get("schema_version") != AUTOMATION_SCHEMA_VERSION:
        raise AutomationApiError(
            "automation_review_token_invalid",
            "Automation review record schema is unsupported.",
            status_code=401,
        )
    expected_hash = str(record.get("token_hash") or "")
    actual_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    if not secrets.compare_digest(expected_hash, actual_hash):
        raise AutomationApiError(
            "automation_review_token_invalid",
            "Automation review token is invalid.",
            status_code=401,
        )
    if record.get("credential_id") != principal.credential_id:
        raise AutomationApiError(
            "automation_review_credential_mismatch",
            "Automation review belongs to another credential.",
            status_code=403,
        )
    if _parse_utc(record.get("expires_at_utc")) <= _utc_now():
        cleanup_staged_files(record.get("staged_files") or [])
        path.unlink(missing_ok=True)
        raise AutomationApiError(
            "automation_review_expired",
            "Automation review has expired.",
            status_code=409,
        )
    return record, path, ticket_id


def consume_review_ticket(
    *,
    principal: AutomationPrincipal,
    review_token: str,
    idempotency_key: str,
    operation: str,
    required_scope: str,
    request_payload: Mapping[str, Any],
) -> dict[str, Any]:
    if required_scope not in principal.scopes:
        raise AutomationApiError(
            "automation_scope_required",
            "Automation credential lacks the operation scope.",
            status_code=403,
            details={"required_scopes": [required_scope]},
        )
    state_root = automation_state_root(principal.credential_path)
    with _automation_state_lock(state_root):
        record, path, ticket_id = inspect_review_ticket(
            review_token,
            principal=principal,
        )
        if record.get("operation") != operation or record.get("required_scope") != required_scope:
            raise AutomationApiError(
                "automation_review_operation_mismatch",
                "Automation review token does not authorize this operation.",
                status_code=409,
            )
        current_fingerprint = canonical_json_fingerprint(request_payload)
        if current_fingerprint != record.get("request_fingerprint"):
            raise AutomationApiError(
                "automation_review_request_changed",
                "Automation request changed after review.",
                status_code=409,
                details={"reviewed_request_fingerprint": record.get("request_fingerprint")},
            )
        if record.get("consumed") is True:
            raise AutomationApiError(
                "automation_review_replay_rejected",
                "Automation review token was already consumed.",
                status_code=409,
            )
        idempotency_path = _idempotency_path(
            state_root,
            principal.credential_id,
            idempotency_key,
        )
        if idempotency_path.exists():
            previous = _read_private_json(idempotency_path)
            raise AutomationApiError(
                "automation_idempotency_replay_rejected",
                "Idempotency-Key was already used.",
                status_code=409,
                details={
                    "previous_operation": previous.get("operation"),
                    "previous_request_fingerprint": previous.get("request_fingerprint"),
                },
            )
        consumed_at = _utc_text()
        consumed = {
            **record,
            "consumed": True,
            "consumed_at_utc": consumed_at,
            "idempotency_key_fingerprint": hashlib.sha256(
                idempotency_key.encode("utf-8")
            ).hexdigest(),
        }
        _atomic_private_json(path, consumed)
        idempotency_record = {
            "schema_version": AUTOMATION_SCHEMA_VERSION,
            "credential_id": principal.credential_id,
            "ticket_id": ticket_id,
            "operation": operation,
            "request_fingerprint": current_fingerprint,
            "started_at_utc": consumed_at,
            "finished_at_utc": None,
            "status": "started",
            "result_fingerprint": None,
        }
        _atomic_private_json(idempotency_path, idempotency_record)
    return {
        "ticket": copy.deepcopy(consumed),
        "ticket_path": path,
        "idempotency_path": idempotency_path,
    }


def finish_consumed_operation(
    consumed: Mapping[str, Any],
    *,
    status: str,
    result: Any = None,
) -> None:
    path = Path(str(consumed["idempotency_path"])).expanduser().resolve()
    state_root = path.parent.parent
    with _automation_state_lock(state_root):
        record = _read_private_json(path)
        record.update(
            {
                "status": str(status),
                "finished_at_utc": _utc_text(),
                "result_fingerprint": (
                    canonical_json_fingerprint(result) if result is not None else None
                ),
            }
        )
        _atomic_private_json(path, record)


def new_staging_path(
    *,
    principal: AutomationPrincipal,
    suffix: str,
) -> Path:
    if not re.fullmatch(r"\.[a-z0-9]{1,12}", suffix.casefold()):
        raise AutomationApiError(
            "automation_staging_suffix_invalid",
            "Automation staging file suffix is invalid.",
            status_code=422,
        )
    root = automation_state_root(principal.credential_path) / "staging"
    _ensure_private_directory(root)
    descriptor, name = tempfile.mkstemp(
        prefix="upload_",
        suffix=suffix.casefold(),
        dir=root,
    )
    os.fchmod(descriptor, 0o600)
    os.close(descriptor)
    return Path(name).resolve()


def staged_file_record(path: str | Path, *, original_name: str) -> dict[str, Any]:
    target = Path(path).expanduser().resolve()
    if not target.is_file() or target.is_symlink():
        raise AutomationApiError(
            "automation_staged_file_invalid",
            "Automation staged file is missing or unsafe.",
            status_code=409,
        )
    return {
        "path": str(target),
        "original_name": Path(original_name).name,
        "size_bytes": target.stat().st_size,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }


def verify_staged_file(record: Mapping[str, Any]) -> Path:
    target = Path(str(record.get("path") or "")).expanduser().resolve()
    if not target.is_file() or target.is_symlink():
        raise AutomationApiError(
            "automation_staged_file_invalid",
            "Automation staged file is missing or unsafe.",
            status_code=409,
        )
    if target.stat().st_size != record.get("size_bytes"):
        raise AutomationApiError(
            "automation_staged_file_changed",
            "Automation staged file changed after review.",
            status_code=409,
        )
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest != record.get("sha256"):
        raise AutomationApiError(
            "automation_staged_file_changed",
            "Automation staged file changed after review.",
            status_code=409,
        )
    return target


def cleanup_staged_files(records: Iterable[Mapping[str, Any]]) -> None:
    for record in records:
        try:
            Path(str(record.get("path") or "")).unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Provision Alexandria's local automation bearer credential."
    )
    parser.add_argument("command", choices=("provision", "status"))
    parser.add_argument("--replace", action="store_true")
    parser.add_argument(
        "--scope",
        action="append",
        dest="scopes",
        choices=sorted(KNOWN_AUTOMATION_SCOPES),
    )
    args = parser.parse_args()
    if args.command == "provision":
        created = provision_automation_credential(
            scopes=args.scopes or KNOWN_AUTOMATION_SCOPES,
            replace=args.replace,
        )
        print(
            json.dumps(
                {
                    "status": "provisioned",
                    "credential_path": created["credential_path"],
                    "credential_id": created["credential_id"],
                    "token_fingerprint": created["token_fingerprint"],
                    "scopes": created["scopes"],
                    "secret_output": False,
                },
                sort_keys=True,
            )
        )
    else:
        print(json.dumps(public_automation_credential(), sort_keys=True))
