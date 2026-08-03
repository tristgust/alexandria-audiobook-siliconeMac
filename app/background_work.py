from __future__ import annotations

import copy
import os
import re
import secrets
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from generation_state import atomic_json_write, fingerprint_value


BACKGROUND_WORK_SCHEMA_VERSION = 1
BACKGROUND_WORK_DIRNAME = "background_work"
BACKGROUND_WORK_JOBS_DIRNAME = "jobs"
BACKGROUND_WORK_INDEX_FILENAME = "index.json"

ACTIVE_STATES = frozenset({"queued", "running", "cancelling"})
TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled", "stale"})
ALL_STATES = ACTIVE_STATES | TERMINAL_STATES

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_SAFE_RESOURCE = re.compile(r"^[a-z][a-z0-9_.:-]{0,95}$")
_LOCK_GUARD = threading.Lock()
_ROOT_LOCKS: dict[str, threading.RLock] = {}

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


class BackgroundWorkError(RuntimeError):
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


def _reset_locks_after_fork() -> None:
    global _LOCK_GUARD, _ROOT_LOCKS
    _LOCK_GUARD = threading.Lock()
    _ROOT_LOCKS = {}


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_locks_after_fork)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _root(root_dir: str | Path) -> Path:
    return Path(root_dir).expanduser().resolve()


def background_work_root(root_dir: str | Path) -> Path:
    return _root(root_dir) / BACKGROUND_WORK_DIRNAME


def _jobs_root(root_dir: str | Path) -> Path:
    return background_work_root(root_dir) / BACKGROUND_WORK_JOBS_DIRNAME


def _index_path(root_dir: str | Path) -> Path:
    return background_work_root(root_dir) / BACKGROUND_WORK_INDEX_FILENAME


def _job_path(root_dir: str | Path, job_id: str) -> Path:
    _identifier(job_id, "job ID")
    return _jobs_root(root_dir) / f"{job_id}.json"


def _identifier(value: str, label: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_IDENTIFIER.fullmatch(text) or text in {".", ".."}:
        raise BackgroundWorkError(
            "background_work_invalid_identifier",
            f"{label} must be one safe opaque identifier.",
        )
    return text


def _resource_list(values: tuple[str, ...] | list[str]) -> list[str]:
    resources: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip()
        if not _SAFE_RESOURCE.fullmatch(value):
            raise BackgroundWorkError(
                "background_work_invalid_resource",
                f"Invalid scheduler resource group: {raw!r}.",
            )
        if value in seen:
            continue
        seen.add(value)
        resources.append(value)
    if not resources:
        raise BackgroundWorkError(
            "background_work_resource_required",
            "A background job requires at least one resource group.",
        )
    return sorted(resources)


def _digest(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise BackgroundWorkError(
            "background_work_invalid_fingerprint",
            f"{label} must be a lowercase SHA-256 digest.",
        )
    return text


def _thread_lock(root: Path) -> threading.RLock:
    key = str(root)
    with _LOCK_GUARD:
        return _ROOT_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _locked(root_dir: str | Path) -> Iterator[None]:
    root = background_work_root(root_dir)
    root.mkdir(parents=True, exist_ok=True)
    lock = _thread_lock(root)
    with lock:
        lock_path = root / ".lock"
        with lock_path.open("a+b") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _default_index() -> dict[str, Any]:
    return {
        "schema_version": BACKGROUND_WORK_SCHEMA_VERSION,
        "next_sequence": 1,
        "max_pending": 32,
        "last_claimed_domain": None,
        "job_ids": [],
        "updated_at": None,
    }


def _read_json(path: Path, label: str) -> Any:
    try:
        import json

        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BackgroundWorkError(
            "background_work_missing",
            f"{label} is missing.",
            details={"path": str(path)},
        ) from exc
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise BackgroundWorkError(
            "background_work_corrupt",
            f"{label} is unreadable or invalid: {exc}",
            details={"path": str(path)},
        ) from exc


def _load_index(root_dir: str | Path) -> dict[str, Any]:
    path = _index_path(root_dir)
    value = (
        _read_json(path, "Background-work index")
        if path.exists()
        else _default_index()
    )
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise BackgroundWorkError(
            "background_work_index_invalid",
            "Background-work index schema is invalid.",
        )
    job_ids = value.get("job_ids")
    if not isinstance(job_ids, list) or any(
        not isinstance(item, str) for item in job_ids
    ):
        raise BackgroundWorkError(
            "background_work_index_invalid",
            "Background-work index job IDs are invalid.",
        )
    next_sequence = value.get("next_sequence")
    max_pending = value.get("max_pending")
    if (
        not isinstance(next_sequence, int)
        or next_sequence < 1
        or not isinstance(max_pending, int)
        or max_pending < 1
    ):
        raise BackgroundWorkError(
            "background_work_index_invalid",
            "Background-work index limits are invalid.",
        )
    normalized = {
        "schema_version": 1,
        "next_sequence": next_sequence,
        "max_pending": max_pending,
        "last_claimed_domain": value.get("last_claimed_domain"),
        "job_ids": list(dict.fromkeys(job_ids)),
        "updated_at": value.get("updated_at"),
    }
    jobs_root = _jobs_root(root_dir)
    if jobs_root.is_dir():
        discovered: list[str] = []
        for job_path in jobs_root.glob("*.json"):
            try:
                discovered.append(_identifier(job_path.stem, "job ID"))
            except BackgroundWorkError as exc:
                raise BackgroundWorkError(
                    "background_work_corrupt",
                    "Background-work storage contains an invalid job filename.",
                    details={"path": str(job_path)},
                ) from exc
        normalized["job_ids"] = list(
            dict.fromkeys([*normalized["job_ids"], *sorted(discovered)])
        )
    return normalized


def _write_index(root_dir: str | Path, index: dict[str, Any]) -> None:
    value = copy.deepcopy(index)
    value["updated_at"] = _utc_now()
    atomic_json_write(value, _index_path(root_dir))


def _validate_job(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise BackgroundWorkError(
            "background_work_job_invalid",
            "Background-work job schema is invalid.",
        )
    job_id = _identifier(value.get("job_id"), "job ID")
    _identifier(value.get("domain"), "domain")
    _identifier(value.get("operation"), "operation")
    state = str(value.get("state") or "")
    if state not in ALL_STATES:
        raise BackgroundWorkError(
            "background_work_job_invalid",
            f"Background-work job {job_id} has invalid state {state!r}.",
        )
    _digest(value.get("request_fingerprint"), "Request fingerprint")
    _digest(value.get("dependency_fingerprint"), "Dependency fingerprint")
    record_fingerprint = _digest(
        value.get("record_fingerprint"),
        "Record fingerprint",
    )
    unsigned = copy.deepcopy(value)
    unsigned.pop("record_fingerprint", None)
    if fingerprint_value(unsigned) != record_fingerprint:
        raise BackgroundWorkError(
            "background_work_corrupt",
            f"Background-work job {job_id} failed its record fingerprint.",
        )
    for field in ("sequence", "priority", "attempt_count", "recovery_count"):
        item = value.get(field)
        if not isinstance(item, int) or item < 0:
            raise BackgroundWorkError(
                "background_work_job_invalid",
                f"Background-work job {job_id} has invalid {field}.",
            )
    if int(value["sequence"]) < 1 or int(value["priority"]) > 1000:
        raise BackgroundWorkError(
            "background_work_job_invalid",
            f"Background-work job {job_id} has invalid ordering fields.",
        )
    resources = value.get("resources")
    if not isinstance(resources, list):
        raise BackgroundWorkError(
            "background_work_job_invalid",
            f"Background-work job {job_id} has invalid resources.",
        )
    _resource_list(resources)
    progress = value.get("progress")
    if not isinstance(progress, dict):
        raise BackgroundWorkError(
            "background_work_job_invalid",
            f"Background-work job {job_id} has invalid progress.",
        )
    completed = progress.get("completed")
    total = progress.get("total")
    if (
        not isinstance(completed, int)
        or not isinstance(total, int)
        or completed < 0
        or total < 0
        or completed > total
    ):
        raise BackgroundWorkError(
            "background_work_job_invalid",
            f"Background-work job {job_id} has invalid progress counts.",
        )
    if state in {"running", "cancelling"} and (
        not isinstance(value.get("owner_token"), str)
        or not value.get("owner_token")
        or not isinstance(value.get("publication_token"), str)
        or not value.get("publication_token")
    ):
        raise BackgroundWorkError(
            "background_work_job_invalid",
            f"Background-work job {job_id} has no active lease tokens.",
        )
    return copy.deepcopy(value)


def _load_job(root_dir: str | Path, job_id: str) -> dict[str, Any]:
    return _validate_job(_read_json(_job_path(root_dir, job_id), "Background-work job"))


def _write_job(root_dir: str | Path, job: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(job)
    value.pop("record_fingerprint", None)
    value["updated_at"] = _utc_now()
    value["record_fingerprint"] = fingerprint_value(value)
    atomic_json_write(value, _job_path(root_dir, value["job_id"]))
    return value


def _all_jobs(root_dir: str | Path, index: dict[str, Any]) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for job_id in index["job_ids"]:
        jobs.append(_load_job(root_dir, job_id))
    return jobs


def configure_scheduler(root_dir: str | Path, *, max_pending: int) -> dict[str, Any]:
    if not isinstance(max_pending, int) or max_pending < 1 or max_pending > 1000:
        raise BackgroundWorkError(
            "background_work_invalid_limit",
            "max_pending must be between 1 and 1000.",
        )
    with _locked(root_dir):
        index = _load_index(root_dir)
        index["max_pending"] = max_pending
        _write_index(root_dir, index)
        return copy.deepcopy(index)


def submit_job(
    root_dir: str | Path,
    *,
    domain: str,
    operation: str,
    resources: tuple[str, ...] | list[str],
    request: dict[str, Any],
    dependency_fingerprint: str | None,
    resumable: bool,
    priority: int = 100,
    external_ref: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    allow_retry: bool = False,
    at_utc: str | None = None,
) -> dict[str, Any]:
    domain_value = _identifier(domain, "domain")
    operation_value = _identifier(operation, "operation")
    resource_values = _resource_list(resources)
    dependency = _digest(dependency_fingerprint, "Dependency fingerprint")
    if not isinstance(request, dict):
        raise BackgroundWorkError(
            "background_work_request_invalid",
            "Background-work request must be an object.",
        )
    if not isinstance(priority, int) or priority < 0 or priority > 1000:
        raise BackgroundWorkError(
            "background_work_priority_invalid",
            "Background-work priority must be between 0 and 1000.",
        )
    now = at_utc or _utc_now()
    request_fingerprint = fingerprint_value(
        {
            "domain": domain_value,
            "operation": operation_value,
            "resources": resource_values,
            "resumable": bool(resumable),
            "request": request,
        }
    )
    with _locked(root_dir):
        index = _load_index(root_dir)
        jobs = _all_jobs(root_dir, index)
        if jobs:
            index["next_sequence"] = max(
                int(index["next_sequence"]),
                max(int(item["sequence"]) for item in jobs) + 1,
            )
        duplicate = next(
            (
                item
                for item in jobs
                if item["request_fingerprint"] == request_fingerprint
                and item.get("dependency_fingerprint") == dependency
                and item["state"] in ACTIVE_STATES
            ),
            None,
        )
        if duplicate is not None:
            return {"job": duplicate, "duplicate": True}
        if not allow_retry:
            duplicate = next(
                (
                    item
                    for item in jobs
                    if item["request_fingerprint"] == request_fingerprint
                    and item.get("dependency_fingerprint") == dependency
                    and item["state"] == "succeeded"
                ),
                None,
            )
            if duplicate is not None:
                return {"job": duplicate, "duplicate": True}
        active = [item for item in jobs if item["state"] in ACTIVE_STATES]
        if len(active) >= index["max_pending"]:
            raise BackgroundWorkError(
                "background_work_backpressure",
                "The bounded background-work queue is full.",
                details={
                    "max_pending": index["max_pending"],
                    "active_count": len(active),
                },
            )
        sequence = index["next_sequence"]
        seed = {
            "request_fingerprint": request_fingerprint,
            "dependency_fingerprint": dependency,
            "sequence": sequence if allow_retry else None,
        }
        job_id = "work_" + fingerprint_value(seed)[:24]
        while job_id in index["job_ids"]:
            sequence += 1
            seed["sequence"] = sequence
            job_id = "work_" + fingerprint_value(seed)[:24]
        job = {
            "schema_version": 1,
            "job_id": job_id,
            "domain": domain_value,
            "operation": operation_value,
            "state": "queued",
            "priority": priority,
            "sequence": sequence,
            "resources": resource_values,
            "request_fingerprint": request_fingerprint,
            "dependency_fingerprint": dependency,
            "request": copy.deepcopy(request),
            "external_ref": copy.deepcopy(external_ref),
            "metadata": copy.deepcopy(metadata or {}),
            "resumable": bool(resumable),
            "attempt_count": 0,
            "recovery_count": 0,
            "cancel_requested": False,
            "cancel_reason": None,
            "owner_token": None,
            "owner_process_id": None,
            "publication_token": None,
            "publication_authorized": False,
            "progress": {
                "completed": 0,
                "total": 0,
                "message": "Queued",
            },
            "result": None,
            "error": None,
            "terminal_reason": None,
            "terminal_receipt_fingerprint": None,
            "created_at": now,
            "queued_at": now,
            "started_at": None,
            "finished_at": None,
        }
        written = _write_job(root_dir, job)
        index["job_ids"].append(job_id)
        index["next_sequence"] = sequence + 1
        _write_index(root_dir, index)
        return {"job": written, "duplicate": False}


def get_job(root_dir: str | Path, job_id: str) -> dict[str, Any]:
    with _locked(root_dir):
        return _load_job(root_dir, job_id)


def list_jobs(root_dir: str | Path) -> list[dict[str, Any]]:
    if not background_work_root(root_dir).exists():
        return []
    with _locked(root_dir):
        index = _load_index(root_dir)
        return sorted(
            _all_jobs(root_dir, index),
            key=lambda item: (int(item["sequence"]), item["job_id"]),
        )


def _running_resource_conflicts(
    jobs: list[dict[str, Any]],
    candidate: dict[str, Any],
) -> list[dict[str, Any]]:
    wanted = set(candidate["resources"])
    return [
        item
        for item in jobs
        if item["job_id"] != candidate["job_id"]
        and item["state"] in {"running", "cancelling"}
        and wanted.intersection(item["resources"])
    ]


def _next_claimable_job(
    index: dict[str, Any],
    jobs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    queued = [item for item in jobs if item["state"] == "queued"]
    last_domain = index.get("last_claimed_domain")
    for priority in sorted({int(item["priority"]) for item in queued}):
        pool = sorted(
            [item for item in queued if int(item["priority"]) == priority],
            key=lambda item: (int(item["sequence"]), item["job_id"]),
        )
        ordered = [item for item in pool if item["domain"] != last_domain] + [
            item for item in pool if item["domain"] == last_domain
        ]
        for candidate in ordered:
            if not _running_resource_conflicts(jobs, candidate):
                return candidate
    return None


def _claim_locked(
    root_dir: str | Path,
    index: dict[str, Any],
    jobs: list[dict[str, Any]],
    job: dict[str, Any],
    *,
    owner_process_id: int | None,
    at_utc: str | None,
) -> dict[str, Any]:
    if job["state"] != "queued":
        raise BackgroundWorkError(
            "background_work_not_queued",
            f"Background job {job['job_id']} is not queued.",
            details={"state": job["state"]},
        )
    conflicts = _running_resource_conflicts(jobs, job)
    if conflicts:
        raise BackgroundWorkError(
            "background_work_resource_busy",
            "Required scheduler resources are already leased.",
            details={
                "job_id": job["job_id"],
                "conflicts": [item["job_id"] for item in conflicts],
                "resources": sorted(
                    set(job["resources"]).intersection(
                        resource
                        for item in conflicts
                        for resource in item["resources"]
                    )
                ),
            },
        )
    now = at_utc or _utc_now()
    attempt = int(job.get("attempt_count") or 0) + 1
    owner_token = "owner_" + secrets.token_hex(16)
    publication_token = "publish_" + fingerprint_value(
        {
            "job_id": job["job_id"],
            "attempt": attempt,
            "dependency_fingerprint": job.get("dependency_fingerprint"),
            "owner_token": owner_token,
        }
    )[:32]
    job.update(
        {
            "state": "running",
            "attempt_count": attempt,
            "owner_token": owner_token,
            "owner_process_id": int(owner_process_id or os.getpid()),
            "publication_token": publication_token,
            "publication_authorized": False,
            "started_at": now,
            "finished_at": None,
            "terminal_reason": None,
            "terminal_receipt_fingerprint": None,
            "error": None,
        }
    )
    job["progress"] = {
        "completed": 0,
        "total": int((job.get("progress") or {}).get("total") or 0),
        "message": "Running",
    }
    written = _write_job(root_dir, job)
    index["last_claimed_domain"] = job["domain"]
    _write_index(root_dir, index)
    return written


def claim_job(
    root_dir: str | Path,
    job_id: str,
    *,
    owner_process_id: int | None = None,
    at_utc: str | None = None,
) -> dict[str, Any]:
    with _locked(root_dir):
        index = _load_index(root_dir)
        jobs = _all_jobs(root_dir, index)
        job = next((item for item in jobs if item["job_id"] == job_id), None)
        if job is None:
            raise BackgroundWorkError(
                "background_work_job_missing",
                f"Background job {job_id!r} does not exist.",
            )
        if job["state"] == "queued" and not _running_resource_conflicts(jobs, job):
            next_job = _next_claimable_job(index, jobs)
            if next_job is not None and next_job["job_id"] != job["job_id"]:
                raise BackgroundWorkError(
                    "background_work_not_turn",
                    "Another queued background job has the next scheduler lease.",
                    details={
                        "job_id": job["job_id"],
                        "next_job_id": next_job["job_id"],
                    },
                )
        return _claim_locked(
            root_dir,
            index,
            jobs,
            job,
            owner_process_id=owner_process_id,
            at_utc=at_utc,
        )


def claim_next_job(
    root_dir: str | Path,
    *,
    owner_process_id: int | None = None,
    at_utc: str | None = None,
) -> dict[str, Any]:
    with _locked(root_dir):
        index = _load_index(root_dir)
        jobs = _all_jobs(root_dir, index)
        queued = [item for item in jobs if item["state"] == "queued"]
        if not queued:
            raise BackgroundWorkError(
                "background_work_queue_empty",
                "No background job is queued.",
            )
        candidate = _next_claimable_job(index, jobs)
        if candidate is not None:
            return _claim_locked(
                root_dir,
                index,
                jobs,
                candidate,
                owner_process_id=owner_process_id,
                at_utc=at_utc,
            )
        raise BackgroundWorkError(
            "background_work_resource_busy",
            "All queued background jobs are waiting for leased resources.",
        )


def _require_owner(job: dict[str, Any], owner_token: str) -> None:
    if job["state"] not in {"running", "cancelling"}:
        raise BackgroundWorkError(
            "background_work_not_running",
            f"Background job {job['job_id']} is not running.",
        )
    if not secrets.compare_digest(str(job.get("owner_token") or ""), owner_token):
        raise BackgroundWorkError(
            "background_work_owner_mismatch",
            "Background job owner token does not match the current lease.",
        )


def request_cancel(
    root_dir: str | Path,
    job_id: str,
    *,
    reason: str = "cancel_requested",
    at_utc: str | None = None,
) -> dict[str, Any]:
    with _locked(root_dir):
        job = _load_job(root_dir, job_id)
        if job["state"] in TERMINAL_STATES:
            return job
        now = at_utc or _utc_now()
        job["cancel_requested"] = True
        job["cancel_reason"] = str(reason or "cancel_requested")[:500]
        if job["state"] == "queued":
            job.update(
                {
                    "state": "cancelled",
                    "terminal_reason": "cancelled_before_start",
                    "finished_at": now,
                    "owner_token": None,
                    "owner_process_id": None,
                    "publication_token": None,
                    "publication_authorized": False,
                    "result": None,
                }
            )
            job["progress"] = {
                "completed": 0,
                "total": int((job.get("progress") or {}).get("total") or 0),
                "message": "Cancelled before start",
            }
            job["terminal_receipt_fingerprint"] = fingerprint_value(
                {
                    "job_id": job["job_id"],
                    "state": job["state"],
                    "reason": job["terminal_reason"],
                    "finished_at": now,
                }
            )
        else:
            job["state"] = "cancelling"
            job["progress"]["message"] = "Cancellation requested"
        return _write_job(root_dir, job)


def should_cancel(root_dir: str | Path, job_id: str, owner_token: str) -> bool:
    with _locked(root_dir):
        job = _load_job(root_dir, job_id)
        _require_owner(job, owner_token)
        return bool(job.get("cancel_requested") or job["state"] == "cancelling")


def update_progress(
    root_dir: str | Path,
    job_id: str,
    *,
    owner_token: str,
    completed: int,
    total: int,
    message: str,
) -> dict[str, Any]:
    if completed < 0 or total < 0 or completed > total:
        raise BackgroundWorkError(
            "background_work_progress_invalid",
            "Background-work progress counts are invalid.",
        )
    with _locked(root_dir):
        job = _load_job(root_dir, job_id)
        _require_owner(job, owner_token)
        job["progress"] = {
            "completed": int(completed),
            "total": int(total),
            "message": str(message or "")[:500],
        }
        return _write_job(root_dir, job)


def finish_job(
    root_dir: str | Path,
    job_id: str,
    *,
    owner_token: str,
    publication_token: str,
    current_dependency_fingerprint: str | None,
    result: dict[str, Any] | None,
    publisher: Callable[[], Any] | None = None,
    at_utc: str | None = None,
) -> dict[str, Any]:
    dependency = _digest(
        current_dependency_fingerprint,
        "Current dependency fingerprint",
    )
    with _locked(root_dir):
        job = _load_job(root_dir, job_id)
        _require_owner(job, owner_token)
        if not secrets.compare_digest(
            str(job.get("publication_token") or ""),
            str(publication_token or ""),
        ):
            raise BackgroundWorkError(
                "background_work_stale_publication",
                "Publication token does not belong to the current job attempt.",
            )
        now = at_utc or _utc_now()
        if job.get("cancel_requested") or job["state"] == "cancelling":
            state = "cancelled"
            reason = "cancelled_before_publication"
            stored_result = None
            publication_authorized = False
        elif job.get("dependency_fingerprint") != dependency:
            state = "stale"
            reason = "dependency_changed_before_publication"
            stored_result = None
            publication_authorized = False
        else:
            if publisher is not None:
                publisher()
            state = "succeeded"
            reason = "completed"
            stored_result = copy.deepcopy(result or {})
            publication_authorized = True
        job.update(
            {
                "state": state,
                "result": stored_result,
                "error": None,
                "terminal_reason": reason,
                "publication_authorized": publication_authorized,
                "finished_at": now,
                "owner_token": None,
                "owner_process_id": None,
                "publication_token": None,
            }
        )
        job["progress"]["message"] = {
            "succeeded": "Complete",
            "cancelled": "Cancelled",
            "stale": "Discarded because dependencies changed",
        }[state]
        job["terminal_receipt_fingerprint"] = fingerprint_value(
            {
                "job_id": job["job_id"],
                "attempt_count": job["attempt_count"],
                "state": state,
                "reason": reason,
                "dependency_fingerprint": job.get("dependency_fingerprint"),
                "result": stored_result,
                "finished_at": now,
            }
        )
        return _write_job(root_dir, job)


def fail_job(
    root_dir: str | Path,
    job_id: str,
    *,
    owner_token: str,
    error: str,
    reason: str = "operation_failed",
    at_utc: str | None = None,
) -> dict[str, Any]:
    with _locked(root_dir):
        job = _load_job(root_dir, job_id)
        _require_owner(job, owner_token)
        now = at_utc or _utc_now()
        if job.get("cancel_requested") or job["state"] == "cancelling":
            state = "cancelled"
            terminal_reason = "cancelled_during_failure"
            stored_error = None
        else:
            state = "failed"
            terminal_reason = str(reason or "operation_failed")[:200]
            stored_error = str(error or "Unknown error")[:2000]
        job.update(
            {
                "state": state,
                "error": stored_error,
                "result": None,
                "terminal_reason": terminal_reason,
                "publication_authorized": False,
                "finished_at": now,
                "owner_token": None,
                "owner_process_id": None,
                "publication_token": None,
            }
        )
        job["progress"]["message"] = "Cancelled" if state == "cancelled" else "Failed"
        job["terminal_receipt_fingerprint"] = fingerprint_value(
            {
                "job_id": job["job_id"],
                "attempt_count": job["attempt_count"],
                "state": state,
                "reason": terminal_reason,
                "error": stored_error,
                "finished_at": now,
            }
        )
        return _write_job(root_dir, job)


def reconcile_interrupted_jobs(
    root_dir: str | Path,
    *,
    at_utc: str | None = None,
) -> dict[str, Any]:
    now = at_utc or _utc_now()
    requeued: list[str] = []
    failed: list[str] = []
    cancelled: list[str] = []
    with _locked(root_dir):
        index = _load_index(root_dir)
        for job in _all_jobs(root_dir, index):
            if job["state"] not in {"running", "cancelling"}:
                continue
            if job.get("cancel_requested") or job["state"] == "cancelling":
                job.update(
                    {
                        "state": "cancelled",
                        "terminal_reason": "cancelled_during_restart",
                        "finished_at": now,
                        "publication_authorized": False,
                    }
                )
                cancelled.append(job["job_id"])
                job["progress"]["message"] = "Cancelled"
            elif job.get("resumable"):
                job.update(
                    {
                        "state": "queued",
                        "queued_at": now,
                        "started_at": None,
                        "finished_at": None,
                        "terminal_reason": None,
                        "recovery_count": int(job.get("recovery_count") or 0) + 1,
                        "publication_authorized": False,
                        "cancel_requested": False,
                    }
                )
                job["progress"]["message"] = "Recovered after restart"
                requeued.append(job["job_id"])
            else:
                job.update(
                    {
                        "state": "failed",
                        "terminal_reason": "interrupted_nonresumable",
                        "error": "The operation was interrupted before completion.",
                        "finished_at": now,
                        "publication_authorized": False,
                    }
                )
                failed.append(job["job_id"])
                job["progress"]["message"] = "Failed"
            job["owner_token"] = None
            job["owner_process_id"] = None
            job["publication_token"] = None
            if job["state"] in TERMINAL_STATES:
                job["terminal_receipt_fingerprint"] = fingerprint_value(
                    {
                        "job_id": job["job_id"],
                        "state": job["state"],
                        "reason": job["terminal_reason"],
                        "finished_at": now,
                    }
                )
            _write_job(root_dir, job)
        return {
            "schema_version": 1,
            "requeued": sorted(requeued),
            "failed": sorted(failed),
            "cancelled": sorted(cancelled),
            "at_utc": now,
        }


def scheduler_status(
    root_dir: str | Path,
    *,
    history_limit: int = 20,
) -> dict[str, Any]:
    if history_limit < 0 or history_limit > 200:
        raise BackgroundWorkError(
            "background_work_history_limit_invalid",
            "History limit must be between 0 and 200.",
        )
    if not background_work_root(root_dir).exists():
        index = _default_index()
        counts = {state: 0 for state in sorted(ALL_STATES)}
        return {
            "schema_version": 1,
            "max_pending": index["max_pending"],
            "active_count": 0,
            "counts": counts,
            "jobs": [],
            "active": [],
            "history": [],
            "updated_at": None,
        }
    with _locked(root_dir):
        index = _load_index(root_dir)
        jobs = _all_jobs(root_dir, index)
        counts = {state: 0 for state in sorted(ALL_STATES)}
        for job in jobs:
            counts[job["state"]] += 1
        active = sorted(
            [item for item in jobs if item["state"] in ACTIVE_STATES],
            key=lambda item: (int(item["priority"]), int(item["sequence"])),
        )
        history = sorted(
            [item for item in jobs if item["state"] in TERMINAL_STATES],
            key=lambda item: (str(item.get("finished_at") or ""), int(item["sequence"])),
            reverse=True,
        )[:history_limit]
        updated_values = [
            str(item.get("updated_at") or "")
            for item in jobs
            if item.get("updated_at")
        ]
        if index.get("updated_at"):
            updated_values.append(str(index["updated_at"]))
        return {
            "schema_version": 1,
            "max_pending": index["max_pending"],
            "active_count": len(active),
            "counts": counts,
            "jobs": copy.deepcopy(sorted(jobs, key=lambda item: int(item["sequence"]))),
            "active": copy.deepcopy(active),
            "history": copy.deepcopy(history),
            "updated_at": max(updated_values) if updated_values else None,
        }
