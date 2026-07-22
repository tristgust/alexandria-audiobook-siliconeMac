from __future__ import annotations

import copy
import gc
import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

from model_registry import ModelRegistryError, model_spec


MODEL_MEMORY_POLICY_SCHEMA_VERSION = 1
DEFAULT_MINIMUM_HEADROOM_BYTES = 512 * 1024 * 1024
DEFAULT_IDLE_UNLOAD_SECONDS = 15 * 60
MAX_IDLE_UNLOAD_SECONDS = 24 * 60 * 60
T = TypeVar("T")


class ModelMemoryError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = copy.deepcopy(details or {})


def default_model_memory_policy_path() -> Path:
    override = os.environ.get("ALEXANDRIA_MODEL_MEMORY_POLICY")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".config" / "alexandria" / "model-memory.json").resolve()


def default_model_memory_policy() -> dict[str, Any]:
    return {
        "schema_version": MODEL_MEMORY_POLICY_SCHEMA_VERSION,
        "minimum_headroom_bytes": DEFAULT_MINIMUM_HEADROOM_BYTES,
        "idle_unload_seconds": DEFAULT_IDLE_UNLOAD_SECONDS,
        "release_and_retry_on_oom": True,
    }


def normalize_model_memory_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ModelMemoryError("model_memory_policy_invalid", "Model memory policy must be an object.")
    defaults = default_model_memory_policy()
    schema_version = value.get("schema_version", MODEL_MEMORY_POLICY_SCHEMA_VERSION)
    if schema_version != MODEL_MEMORY_POLICY_SCHEMA_VERSION:
        raise ModelMemoryError("model_memory_policy_schema_unsupported", "Unsupported model memory policy schema.")
    headroom = value.get("minimum_headroom_bytes", defaults["minimum_headroom_bytes"])
    idle = value.get("idle_unload_seconds", defaults["idle_unload_seconds"])
    retry = value.get("release_and_retry_on_oom", defaults["release_and_retry_on_oom"])
    if isinstance(headroom, bool) or not isinstance(headroom, int) or not 0 <= headroom <= 64 * 1024**3:
        raise ModelMemoryError("model_memory_policy_invalid", "minimum_headroom_bytes must be between 0 and 64 GiB.")
    if isinstance(idle, bool) or not isinstance(idle, int) or not 0 <= idle <= MAX_IDLE_UNLOAD_SECONDS:
        raise ModelMemoryError("model_memory_policy_invalid", "idle_unload_seconds must be between 0 and 86400.")
    if not isinstance(retry, bool):
        raise ModelMemoryError("model_memory_policy_invalid", "release_and_retry_on_oom must be boolean.")
    return {
        "schema_version": MODEL_MEMORY_POLICY_SCHEMA_VERSION,
        "minimum_headroom_bytes": headroom,
        "idle_unload_seconds": idle,
        "release_and_retry_on_oom": retry,
    }


def read_model_memory_policy(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return default_model_memory_policy()
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelMemoryError("model_memory_policy_unreadable", f"Could not read model memory policy: {exc}") from exc
    return normalize_model_memory_policy(value)


def write_model_memory_policy(path: str | Path, value: Any) -> dict[str, Any]:
    normalized = normalize_model_memory_policy(value)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(normalized, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
    return copy.deepcopy(normalized)


def memory_snapshot() -> dict[str, int]:
    try:
        import psutil

        memory = psutil.virtual_memory()
    except Exception as exc:
        raise ModelMemoryError("model_memory_unavailable", f"Could not inspect unified memory: {exc}") from exc
    return {
        "total_bytes": int(memory.total),
        "available_bytes": int(memory.available),
        "used_bytes": int(memory.used),
    }


def admission_status(
    model_key: str,
    *,
    policy: dict[str, Any] | None = None,
    snapshot: dict[str, int] | None = None,
) -> dict[str, Any]:
    try:
        spec = model_spec(model_key)
    except ModelRegistryError as exc:
        raise ModelMemoryError("model_memory_unknown_model", str(exc)) from exc
    normalized = normalize_model_memory_policy(policy or default_model_memory_policy())
    current = copy.deepcopy(snapshot or memory_snapshot())
    required = spec.estimated_loaded_memory_bytes + normalized["minimum_headroom_bytes"]
    available = int(current["available_bytes"])
    admitted = available >= required
    return {
        "model_key": spec.key,
        "repo_id": spec.repo_id,
        "revision": spec.revision,
        "estimated_loaded_memory_bytes": spec.estimated_loaded_memory_bytes,
        "minimum_headroom_bytes": normalized["minimum_headroom_bytes"],
        "required_available_bytes": required,
        "available_bytes": available,
        "admitted": admitted,
        "reason": None if admitted else (
            f"Model {spec.key} requires {required} available bytes including headroom; "
            f"{available} are currently available."
        ),
    }


def require_admission(model_key: str, *, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    result = admission_status(model_key, policy=policy)
    if not result["admitted"]:
        raise ModelMemoryError("model_memory_admission_denied", result["reason"], details=result)
    return result


def is_recognized_allocation_failure(error: BaseException) -> bool:
    text = " ".join(str(item) for item in (error, getattr(error, "__cause__", None)) if item).casefold()
    markers = (
        "out of memory",
        "memory allocation",
        "failed to allocate",
        "metal heap",
        "resource exhausted",
        "mps backend out of memory",
    )
    return isinstance(error, MemoryError) or any(marker in text for marker in markers)


class ModelMemoryCoordinator:
    def __init__(self, *, policy_path: str | Path | None = None) -> None:
        self._lock = threading.RLock()
        self._active_jobs = 0
        self._last_activity_monotonic = time.monotonic()
        self._policy_path = (
            Path(policy_path).expanduser().resolve()
            if policy_path is not None
            else default_model_memory_policy_path()
        )

    @property
    def active_jobs(self) -> int:
        with self._lock:
            return self._active_jobs

    def policy(self) -> dict[str, Any]:
        return read_model_memory_policy(self._policy_path)

    def update_policy(self, value: Any) -> dict[str, Any]:
        return write_model_memory_policy(self._policy_path, value)

    @contextmanager
    def job(self) -> Iterator[None]:
        with self._lock:
            self._active_jobs += 1
            self._last_activity_monotonic = time.monotonic()
        try:
            yield
        finally:
            with self._lock:
                self._active_jobs -= 1
                self._last_activity_monotonic = time.monotonic()

    def release(self, release_callback: Callable[[], Any], *, reason: str, allow_active: bool = False) -> dict[str, Any]:
        with self._lock:
            if self._active_jobs and not allow_active:
                raise ModelMemoryError(
                    "model_memory_active_jobs",
                    "Models cannot be released while synthesis jobs are active.",
                    details={"active_jobs": self._active_jobs},
                )
            released = release_callback()
            gc.collect()
            self._last_activity_monotonic = time.monotonic()
            return {
                "released": bool(released),
                "reason": reason,
                "active_jobs": self._active_jobs,
            }

    def release_if_idle(self, release_callback: Callable[[], Any], *, now: float | None = None) -> dict[str, Any]:
        policy = self.policy()
        idle_seconds = policy["idle_unload_seconds"]
        with self._lock:
            elapsed = (time.monotonic() if now is None else now) - self._last_activity_monotonic
            if self._active_jobs or idle_seconds == 0 or elapsed < idle_seconds:
                return {"released": False, "reason": "not_idle", "idle_seconds": max(0.0, elapsed)}
        return self.release(release_callback, reason="idle_policy")

    def run_with_oom_retry(
        self,
        model_key: str,
        operation: Callable[[], T],
        release_callback: Callable[[], Any],
    ) -> T:
        policy = self.policy()
        require_admission(model_key, policy=policy)
        try:
            return operation()
        except Exception as exc:
            if not policy["release_and_retry_on_oom"] or not is_recognized_allocation_failure(exc):
                raise
            self.release(release_callback, reason="allocation_failure", allow_active=True)
            require_admission(model_key, policy=policy)
            try:
                return operation()
            except Exception as retry_error:
                if is_recognized_allocation_failure(retry_error):
                    raise ModelMemoryError(
                        "model_memory_retry_exhausted",
                        "Model allocation failed after one release-and-retry attempt.",
                        details={"model_key": model_key},
                    ) from retry_error
                raise
