from __future__ import annotations

import copy
import gc
import json
import os
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

from model_registry import (
    ModelRegistryError,
    component_record_payload,
    model_spec,
)


MODEL_MEMORY_POLICY_SCHEMA_VERSION = 1
DEFAULT_MINIMUM_HEADROOM_BYTES = 512 * 1024 * 1024
DEFAULT_IDLE_UNLOAD_SECONDS = 15 * 60
MAX_IDLE_UNLOAD_SECONDS = 24 * 60 * 60
T = TypeVar("T")
MAX_RESIDENCY_EVENTS = 64


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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_memory_snapshot() -> dict[str, Any]:
    try:
        return {"available": True, **memory_snapshot()}
    except ModelMemoryError as exc:
        return {
            "available": False,
            "error": str(exc),
            "error_code": exc.code,
        }


def _normalize_owner(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return {"label": text} if text else None
    if not isinstance(value, dict):
        raise ModelMemoryError(
            "model_residency_owner_invalid",
            "Model residency owner must be a string or object.",
        )
    result: dict[str, Any] = {}
    for key in ("job_id", "domain", "operation", "request_id", "label"):
        item = value.get(key)
        if item is None:
            continue
        text = str(item).strip()
        if text:
            result[key] = text[:240]
    return result or None


def _component_identity(
    component_id: str,
    *,
    identity: dict[str, Any] | None = None,
    estimated_loaded_memory_bytes: int | None = None,
) -> dict[str, Any]:
    component = str(component_id or "").strip()
    if not component:
        raise ModelMemoryError(
            "model_residency_component_invalid",
            "A model component ID is required.",
        )
    if identity is None:
        try:
            record = component_record_payload(component)
        except (ModelRegistryError, KeyError) as exc:
            raise ModelMemoryError(
                "model_residency_component_unknown",
                f"Unregistered model component: {component!r}.",
            ) from exc
    elif isinstance(identity, dict):
        record = copy.deepcopy(identity)
    else:
        raise ModelMemoryError(
            "model_residency_identity_invalid",
            "Model component identity must be an object.",
        )
    record_component = str(record.get("component_id") or component).strip()
    if record_component != component:
        raise ModelMemoryError(
            "model_residency_identity_invalid",
            "Model component identity does not match the requested component.",
        )
    for field in ("source_id", "revision", "build_id", "runtime"):
        if not str(record.get(field) or "").strip():
            raise ModelMemoryError(
                "model_residency_identity_invalid",
                f"Model component identity is missing {field}.",
            )
    if estimated_loaded_memory_bytes is None:
        try:
            estimate = model_spec(component).estimated_loaded_memory_bytes
        except ModelRegistryError:
            estimate = int(record.get("estimated_loaded_memory_bytes") or 0)
    else:
        estimate = estimated_loaded_memory_bytes
    if isinstance(estimate, bool) or not isinstance(estimate, int) or estimate < 0:
        raise ModelMemoryError(
            "model_residency_identity_invalid",
            "estimated_loaded_memory_bytes must be a non-negative integer.",
        )
    return {
        "component_id": component,
        "source_id": str(record["source_id"]),
        "revision": str(record["revision"]),
        "build_id": str(record["build_id"]),
        "runtime": str(record["runtime"]),
        "estimated_loaded_memory_bytes": estimate,
    }


class ModelMemoryCoordinator:
    def __init__(self, *, policy_path: str | Path | None = None) -> None:
        self._lock = threading.RLock()
        self._transition_lock = threading.RLock()
        self._leases: dict[str, dict[str, Any]] = {}
        self._operations: dict[str, dict[str, Any]] = {}
        self._operation_order: list[str] = []
        self._residents: dict[str, dict[str, Any]] = {}
        self._resident_callbacks: dict[str, dict[str, Callable[[], Any] | None]] = {}
        self._events: list[dict[str, Any]] = []
        self._current_transition: dict[str, Any] | None = None
        self._planned_eviction: dict[str, Any] | None = None
        self._last_release: dict[str, Any] | None = None
        self._last_activity_monotonic = time.monotonic()
        self._policy_path = (
            Path(policy_path).expanduser().resolve()
            if policy_path is not None
            else default_model_memory_policy_path()
        )

    @property
    def active_jobs(self) -> int:
        with self._lock:
            return len(self._leases)

    def policy(self) -> dict[str, Any]:
        return read_model_memory_policy(self._policy_path)

    def update_policy(self, value: Any) -> dict[str, Any]:
        return write_model_memory_policy(self._policy_path, value)

    def _event(self, event: str, **details: Any) -> None:
        with self._lock:
            self._events.append(
                {
                    "event": event,
                    "at": _utc_now(),
                    **copy.deepcopy(details),
                }
            )
            if len(self._events) > MAX_RESIDENCY_EVENTS:
                del self._events[:-MAX_RESIDENCY_EVENTS]

    def _current_owner_locked(self) -> dict[str, Any] | None:
        while self._operation_order:
            token = self._operation_order[-1]
            operation = self._operations.get(token)
            if operation is not None:
                return copy.deepcopy(operation["owner"])
            self._operation_order.pop()
        return None

    def begin_operation(self, owner: Any) -> str:
        normalized = _normalize_owner(owner)
        token = f"operation_{uuid.uuid4().hex}"
        record = {
            "operation_token": token,
            "owner": normalized or {"label": "unspecified"},
            "started_at": _utc_now(),
        }
        with self._lock:
            self._operations[token] = record
            self._operation_order.append(token)
        self._event("operation_started", owner=record["owner"])
        return token

    def end_operation(self, operation_token: str) -> None:
        token = str(operation_token or "")
        with self._lock:
            record = self._operations.pop(token, None)
            self._operation_order = [
                item for item in self._operation_order if item != token
            ]
        if record is not None:
            self._event("operation_finished", owner=record["owner"])

    @contextmanager
    def operation(self, owner: Any) -> Iterator[dict[str, Any]]:
        token = self.begin_operation(owner)
        with self._lock:
            record = copy.deepcopy(self._operations[token])
        try:
            yield copy.deepcopy(record)
        finally:
            self.end_operation(token)

    @contextmanager
    def job(
        self,
        component_ids: Iterator[str] | list[str] | tuple[str, ...] = (),
        *,
        owner: Any = None,
        label: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        lease = self._acquire_lease(
            component_ids,
            owner=owner,
            label=label,
        )
        try:
            yield copy.deepcopy(lease)
        finally:
            self._release_lease(lease["lease_id"])

    def _acquire_lease(
        self,
        component_ids: Iterator[str] | list[str] | tuple[str, ...] = (),
        *,
        owner: Any = None,
        label: str | None = None,
    ) -> dict[str, Any]:
        components = tuple(
            dict.fromkeys(
                str(item).strip()
                for item in component_ids
                if str(item).strip()
            )
        )
        with self._lock:
            normalized_owner = _normalize_owner(owner) or self._current_owner_locked()
        lease_id = f"lease_{uuid.uuid4().hex}"
        lease = {
            "lease_id": lease_id,
            "component_ids": list(components),
            "owner": normalized_owner,
            "label": str(label or "model work")[:240],
            "acquired_at": _utc_now(),
        }
        with self._lock:
            self._leases[lease_id] = lease
            now = _utc_now()
            for resident in self._residents.values():
                if not components or resident["component_id"] in components:
                    resident["last_used_at"] = now
            self._last_activity_monotonic = time.monotonic()
        self._event(
            "lease_acquired",
            lease_id=lease_id,
            component_ids=list(components),
            owner=normalized_owner,
        )
        return copy.deepcopy(lease)

    def _release_lease(self, lease_id: str) -> None:
        with self._lock:
            removed = self._leases.pop(lease_id, None)
            self._last_activity_monotonic = time.monotonic()
        if removed is not None:
            self._event("lease_released", lease_id=lease_id)

    @contextmanager
    def prepared_job(
        self,
        component_ids: Iterator[str] | list[str] | tuple[str, ...],
        prepare: Callable[[], T],
        *,
        owner: Any = None,
        label: str | None = None,
    ) -> Iterator[T]:
        """Prepare resident state and acquire its lease without a transition gap."""
        with self._transition_lock:
            prepared = prepare()
            lease = self._acquire_lease(
                component_ids,
                owner=owner,
                label=label,
            )
        try:
            yield prepared
        finally:
            self._release_lease(lease["lease_id"])

    def _lease_blockers_locked(self, resident: dict[str, Any]) -> list[dict[str, Any]]:
        blockers = []
        for lease in self._leases.values():
            components = lease["component_ids"]
            if not components or resident["component_id"] in components:
                blockers.append(copy.deepcopy(lease))
        return blockers

    def register_resident(
        self,
        *,
        slot_id: str,
        component_id: str,
        release_callback: Callable[[], Any],
        synchronize_callback: Callable[[], Any] | None = None,
        engine_id: str | None = None,
        device: str | None = None,
        adapter_revision: str | None = None,
        identity: dict[str, Any] | None = None,
        estimated_loaded_memory_bytes: int | None = None,
    ) -> dict[str, Any]:
        slot = str(slot_id or "").strip()
        if not slot:
            raise ModelMemoryError(
                "model_residency_slot_invalid",
                "A model residency slot ID is required.",
            )
        if not callable(release_callback):
            raise ModelMemoryError(
                "model_residency_release_invalid",
                "A model residency release callback is required.",
            )
        component = _component_identity(
            component_id,
            identity=identity,
            estimated_loaded_memory_bytes=estimated_loaded_memory_bytes,
        )
        now = _utc_now()
        resident = {
            "slot_id": slot,
            **component,
            "engine_id": str(engine_id or "").strip() or None,
            "device": str(device or "").strip() or None,
            "adapter_revision": str(adapter_revision or "").strip() or None,
            "state": "resident",
            "loaded_at": now,
            "last_used_at": now,
            "last_error": None,
        }
        with self._lock:
            if slot in self._residents:
                raise ModelMemoryError(
                    "model_residency_slot_occupied",
                    f"Model residency slot {slot!r} is already occupied.",
                    details={"slot_id": slot},
                )
            self._residents[slot] = resident
            self._resident_callbacks[slot] = {
                "release": release_callback,
                "synchronize": synchronize_callback,
            }
            self._last_activity_monotonic = time.monotonic()
        self._event(
            "resident_loaded",
            slot_id=slot,
            component_id=component["component_id"],
            build_id=component["build_id"],
            device=resident["device"],
        )
        return copy.deepcopy(resident)

    def forget_resident(self, slot_id: str, *, reason: str) -> dict[str, Any] | None:
        slot = str(slot_id or "").strip()
        with self._lock:
            resident = self._residents.pop(slot, None)
            self._resident_callbacks.pop(slot, None)
            self._last_activity_monotonic = time.monotonic()
        if resident is not None:
            self._event(
                "resident_forgotten",
                slot_id=slot,
                component_id=resident["component_id"],
                reason=reason,
            )
        return copy.deepcopy(resident) if resident is not None else None

    def _admission_for_component(
        self,
        component_id: str,
        *,
        identity: dict[str, Any] | None = None,
        estimated_loaded_memory_bytes: int | None = None,
        snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        component = _component_identity(
            component_id,
            identity=identity,
            estimated_loaded_memory_bytes=estimated_loaded_memory_bytes,
        )
        policy = self.policy()
        current = copy.deepcopy(snapshot or memory_snapshot())
        required = (
            component["estimated_loaded_memory_bytes"]
            + policy["minimum_headroom_bytes"]
        )
        available = int(current["available_bytes"])
        return {
            **component,
            "minimum_headroom_bytes": policy["minimum_headroom_bytes"],
            "required_available_bytes": required,
            "available_bytes": available,
            "admitted": available >= required,
        }

    def _release_slots_transition(
        self,
        slot_ids: list[str],
        *,
        reason: str,
        allow_active: bool = False,
    ) -> dict[str, Any]:
        slots = list(dict.fromkeys(str(item) for item in slot_ids if str(item)))
        with self._lock:
            residents = [
                copy.deepcopy(self._residents[item])
                for item in slots
                if item in self._residents
            ]
            blockers = [
                {
                    "slot_id": resident["slot_id"],
                    "component_id": resident["component_id"],
                    "leases": self._lease_blockers_locked(resident),
                }
                for resident in residents
                if self._lease_blockers_locked(resident)
            ]
            operation_blocker = (
                self._current_owner_locked()
                if reason in {"manual", "idle_policy"}
                else None
            )
            if operation_blocker and not allow_active:
                raise ModelMemoryError(
                    "model_residency_active_operation",
                    "Models cannot be released while scheduler-owned model work is active.",
                    details={"current_owner": operation_blocker},
                )
            if blockers and not allow_active:
                raise ModelMemoryError(
                    "model_residency_active_lease",
                    "Leased model residents cannot be released.",
                    details={"blockers": blockers},
                )
            transition = {
                "transition_id": f"transition_{uuid.uuid4().hex}",
                "kind": "release",
                "reason": str(reason),
                "slot_ids": [item["slot_id"] for item in residents],
                "started_at": _utc_now(),
            }
            self._current_transition = transition
            for resident in residents:
                self._residents[resident["slot_id"]]["state"] = "releasing"
                self._residents[resident["slot_id"]]["last_error"] = None
        before = _safe_memory_snapshot()
        released: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for resident in residents:
            slot = resident["slot_id"]
            with self._lock:
                callbacks = copy.copy(self._resident_callbacks.get(slot) or {})
            try:
                synchronize = callbacks.get("synchronize")
                if callable(synchronize):
                    synchronize()
                release_callback = callbacks.get("release")
                if not callable(release_callback):
                    raise RuntimeError("Model resident has no release callback.")
                release_callback()
                gc.collect()
                with self._lock:
                    self._residents.pop(slot, None)
                    self._resident_callbacks.pop(slot, None)
                released.append(resident)
                self._event(
                    "resident_released",
                    slot_id=slot,
                    component_id=resident["component_id"],
                    reason=reason,
                )
            except Exception as exc:
                failure = {
                    "slot_id": slot,
                    "component_id": resident["component_id"],
                    "error": f"{type(exc).__name__}: {exc}",
                }
                failures.append(failure)
                with self._lock:
                    if slot in self._residents:
                        self._residents[slot]["state"] = "release_failed"
                        self._residents[slot]["last_error"] = failure["error"]
                self._event("resident_release_failed", **failure, reason=reason)
        after = _safe_memory_snapshot()
        measured_release = None
        if before.get("available") and after.get("available"):
            measured_release = max(
                0,
                int(after["available_bytes"]) - int(before["available_bytes"]),
            )
        result = {
            "released": bool(released),
            "reason": str(reason),
            "released_slots": [item["slot_id"] for item in released],
            "released_components": [item["component_id"] for item in released],
            "failures": failures,
            "active_jobs": self.active_jobs,
            "memory_before": before,
            "memory_after": after,
            "measured_available_bytes_recovered": measured_release,
            "finished_at": _utc_now(),
        }
        with self._lock:
            self._current_transition = None
            self._last_release = copy.deepcopy(result)
            self._last_activity_monotonic = time.monotonic()
        if failures:
            raise ModelMemoryError(
                "model_residency_release_failed",
                "One or more model residents could not be released.",
                details=result,
            )
        return result

    def release_residents(
        self,
        *,
        reason: str,
        slot_ids: list[str] | tuple[str, ...] | None = None,
        allow_active: bool = False,
    ) -> dict[str, Any]:
        with self._transition_lock:
            with self._lock:
                selected = (
                    list(slot_ids)
                    if slot_ids is not None
                    else sorted(self._residents)
                )
            return self._release_slots_transition(
                selected,
                reason=reason,
                allow_active=allow_active,
            )

    def _evict_for_admission(
        self,
        component_id: str,
        *,
        identity: dict[str, Any] | None = None,
        estimated_loaded_memory_bytes: int | None = None,
        exclude_slots: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        admission = self._admission_for_component(
            component_id,
            identity=identity,
            estimated_loaded_memory_bytes=estimated_loaded_memory_bytes,
        )
        if admission["admitted"]:
            return admission
        deficit = admission["required_available_bytes"] - admission["available_bytes"]
        with self._lock:
            candidates = []
            blocked = []
            for resident in self._residents.values():
                if resident["slot_id"] in exclude_slots:
                    continue
                leases = self._lease_blockers_locked(resident)
                if leases:
                    blocked.append(
                        {
                            "slot_id": resident["slot_id"],
                            "component_id": resident["component_id"],
                            "leases": leases,
                        }
                    )
                else:
                    candidates.append(copy.deepcopy(resident))
            candidates.sort(
                key=lambda item: (item["last_used_at"], item["slot_id"])
            )
        selected: list[str] = []
        estimated = 0
        for resident in candidates:
            selected.append(resident["slot_id"])
            estimated += int(resident["estimated_loaded_memory_bytes"])
            if estimated >= deficit:
                break
        plan = {
            "plan_id": f"eviction_{uuid.uuid4().hex}",
            "component_id": component_id,
            "required_available_bytes": admission["required_available_bytes"],
            "available_bytes": admission["available_bytes"],
            "deficit_bytes": deficit,
            "selected_slots": selected,
            "blocked_residents": blocked,
            "status": "planned" if selected else "blocked",
            "created_at": _utc_now(),
        }
        with self._lock:
            self._planned_eviction = copy.deepcopy(plan)
        self._event("eviction_planned", **plan)
        if not selected:
            raise ModelMemoryError(
                "model_residency_admission_blocked",
                "No idle model residents can be evicted to satisfy memory admission.",
                details={"admission": admission, "eviction": plan},
            )
        try:
            release_result = self._release_slots_transition(
                selected,
                reason="memory_pressure",
            )
        except ModelMemoryError:
            with self._lock:
                if self._planned_eviction is not None:
                    self._planned_eviction["status"] = "failed"
                    self._planned_eviction["finished_at"] = _utc_now()
            raise
        refreshed = self._admission_for_component(
            component_id,
            identity=identity,
            estimated_loaded_memory_bytes=estimated_loaded_memory_bytes,
        )
        with self._lock:
            if self._planned_eviction is not None:
                self._planned_eviction.update(
                    {
                        "status": "completed" if refreshed["admitted"] else "insufficient",
                        "finished_at": _utc_now(),
                        "release": copy.deepcopy(release_result),
                    }
                )
        if not refreshed["admitted"]:
            raise ModelMemoryError(
                "model_residency_admission_denied",
                "Memory admission is still denied after evicting idle residents.",
                details={
                    "admission": refreshed,
                    "eviction": copy.deepcopy(self._planned_eviction),
                },
            )
        return refreshed

    def load_resident(
        self,
        *,
        slot_id: str,
        component_id: str,
        load_callback: Callable[[], T],
        install_callback: Callable[[T], Any],
        release_callback: Callable[[], Any],
        synchronize_callback: Callable[[], Any] | None = None,
        engine_id: str | None = None,
        device: str | None = None,
        adapter_revision: str | None = None,
        identity: dict[str, Any] | None = None,
        estimated_loaded_memory_bytes: int | None = None,
    ) -> T:
        slot = str(slot_id or "").strip()
        with self._transition_lock:
            with self._lock:
                occupied = slot in self._residents
            if occupied:
                self._release_slots_transition([slot], reason="replacement")
            self._evict_for_admission(
                component_id,
                identity=identity,
                estimated_loaded_memory_bytes=estimated_loaded_memory_bytes,
                exclude_slots=(slot,),
            )
            transition = {
                "transition_id": f"transition_{uuid.uuid4().hex}",
                "kind": "load",
                "slot_id": slot,
                "component_id": component_id,
                "started_at": _utc_now(),
            }
            with self._lock:
                self._current_transition = transition
            self._event("resident_load_started", **transition)
            installed = False
            try:
                loaded = load_callback()
                install_callback(loaded)
                installed = True
                self.register_resident(
                    slot_id=slot,
                    component_id=component_id,
                    release_callback=release_callback,
                    synchronize_callback=synchronize_callback,
                    engine_id=engine_id,
                    device=device,
                    adapter_revision=adapter_revision,
                    identity=identity,
                    estimated_loaded_memory_bytes=estimated_loaded_memory_bytes,
                )
                return loaded
            except Exception as exc:
                if installed:
                    try:
                        release_callback()
                    except Exception:
                        pass
                self._event(
                    "resident_load_failed",
                    slot_id=slot,
                    component_id=component_id,
                    error=f"{type(exc).__name__}: {exc}",
                )
                raise
            finally:
                with self._lock:
                    self._current_transition = None

    def status(self) -> dict[str, Any]:
        memory = _safe_memory_snapshot()
        with self._lock:
            residents = []
            blockers = []
            for value in sorted(
                self._residents.values(),
                key=lambda item: item["slot_id"],
            ):
                resident = copy.deepcopy(value)
                leases = self._lease_blockers_locked(value)
                resident["active_lease_count"] = len(leases)
                resident["lease_ids"] = [item["lease_id"] for item in leases]
                resident["owners"] = [
                    item["owner"] for item in leases if item.get("owner")
                ]
                residents.append(resident)
                if leases:
                    blockers.append(
                        {
                            "slot_id": resident["slot_id"],
                            "component_id": resident["component_id"],
                            "reason": "active_lease",
                            "lease_ids": resident["lease_ids"],
                        }
                    )
                elif resident["state"] == "release_failed":
                    blockers.append(
                        {
                            "slot_id": resident["slot_id"],
                            "component_id": resident["component_id"],
                            "reason": "release_failed",
                            "error": resident["last_error"],
                        }
                    )
            current_owner = self._current_owner_locked()
            return {
                "schema_version": 2,
                "policy": self.policy(),
                "memory": memory,
                "active_jobs": len(self._leases),
                "current_owner": current_owner,
                "leases": copy.deepcopy(
                    sorted(self._leases.values(), key=lambda item: item["lease_id"])
                ),
                "residents": residents,
                "loaded_model_keys": sorted(
                    {item["component_id"] for item in residents}
                ),
                "planned_eviction": copy.deepcopy(self._planned_eviction),
                "current_transition": copy.deepcopy(self._current_transition),
                "blockers": blockers,
                "last_release": copy.deepcopy(self._last_release),
                "events": copy.deepcopy(self._events[-20:]),
            }

    def release(self, release_callback: Callable[[], Any], *, reason: str, allow_active: bool = False) -> dict[str, Any]:
        with self._transition_lock:
            with self._lock:
                active_jobs = len(self._leases)
            if active_jobs and not allow_active:
                raise ModelMemoryError(
                    "model_memory_active_jobs",
                    "Models cannot be released while synthesis jobs are active.",
                    details={"active_jobs": active_jobs},
                )
            before = _safe_memory_snapshot()
            released = release_callback()
            gc.collect()
            after = _safe_memory_snapshot()
            measured_release = None
            if before.get("available") and after.get("available"):
                measured_release = max(
                    0,
                    int(after["available_bytes"]) - int(before["available_bytes"]),
                )
            self._last_activity_monotonic = time.monotonic()
            result = {
                "released": bool(released),
                "reason": reason,
                "active_jobs": active_jobs,
                "memory_before": before,
                "memory_after": after,
                "measured_available_bytes_recovered": measured_release,
            }
            with self._lock:
                self._last_release = copy.deepcopy(result)
            return result

    def release_if_idle(self, release_callback: Callable[[], Any], *, now: float | None = None) -> dict[str, Any]:
        policy = self.policy()
        idle_seconds = policy["idle_unload_seconds"]
        with self._lock:
            elapsed = (time.monotonic() if now is None else now) - self._last_activity_monotonic
            if self._leases or idle_seconds == 0 or elapsed < idle_seconds:
                return {"released": False, "reason": "not_idle", "idle_seconds": max(0.0, elapsed)}
        return self.release(release_callback, reason="idle_policy")

    def release_residents_if_idle(
        self,
        *,
        slot_ids: list[str] | tuple[str, ...] | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        policy = self.policy()
        idle_seconds = policy["idle_unload_seconds"]
        with self._lock:
            elapsed = (time.monotonic() if now is None else now) - self._last_activity_monotonic
            if self._leases or idle_seconds == 0 or elapsed < idle_seconds:
                return {
                    "released": False,
                    "reason": "not_idle",
                    "idle_seconds": max(0.0, elapsed),
                }
        return self.release_residents(
            reason="idle_policy",
            slot_ids=slot_ids,
        )

    def run_with_oom_retry(
        self,
        model_key: str,
        operation: Callable[[], T],
        release_callback: Callable[[], Any],
    ) -> T:
        policy = self.policy()
        self._evict_for_admission(model_key)
        try:
            return operation()
        except Exception as exc:
            if not policy["release_and_retry_on_oom"] or not is_recognized_allocation_failure(exc):
                raise
            with self._lock:
                idle_slots = [
                    item["slot_id"]
                    for item in self._residents.values()
                    if not self._lease_blockers_locked(item)
                ]
            if idle_slots:
                self.release_residents(
                    reason="allocation_failure",
                    slot_ids=idle_slots,
                )
            else:
                self.release(
                    release_callback,
                    reason="allocation_failure",
                    allow_active=False,
                )
            self._evict_for_admission(model_key)
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
