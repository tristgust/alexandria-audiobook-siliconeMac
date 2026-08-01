from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from generation_state import atomic_json_write, fingerprint_value


ORPHAN_RECEIPT_DIRNAME: Final = "audio_orphan_reconciliation"
_AUDIO_SUFFIXES: Final = frozenset(
    {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".bin", ".tmp"}
)
_PATH_FIELDS: Final = frozenset(
    {
        "audio_path",
        "stale_audio_path",
        "relative_path",
        "path",
        "backup_path",
        "backup_relative_path",
    }
)


class OrphanReconciliationError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def _sha256(path: Path) -> str | None:
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return None
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except (FileNotFoundError, OSError):
        return None


def _json(path: Path) -> tuple[str, Any]:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "unreadable", None
    if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):
        return "unreadable", None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return "unreadable", None
            return "ok", json.loads(handle.read())
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "unreadable", None


def _relative_reference(root: Path, value: str) -> str | None:
    raw = Path(value).expanduser()
    target = raw if raw.is_absolute() else root / raw
    try:
        return target.resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        return None


def _collect_nested_references(
    root: Path,
    value: Any,
    references: dict[str, str | None],
) -> None:
    if isinstance(value, list):
        for item in value:
            _collect_nested_references(root, item, references)
        return
    if not isinstance(value, dict):
        return
    expected = value.get("sha256") or value.get("audio_sha256")
    expected_hash = (
        expected if isinstance(expected, str) and len(expected) == 64 else None
    )
    for key, item in value.items():
        if key in _PATH_FIELDS and isinstance(item, str) and item.strip():
            relative = _relative_reference(root, item)
            if relative is None:
                references.setdefault(f"@unsafe:{item}", expected_hash)
            elif relative not in references:
                references[relative] = expected_hash
            elif references[relative] not in {None, expected_hash, "@conflict"}:
                references[relative] = "@conflict"
            elif references[relative] is None and expected_hash is not None:
                references[relative] = expected_hash
        _collect_nested_references(root, item, references)


def _metadata_paths(root: Path) -> list[Path]:
    paths = [root / "chunks.json", root / "audio_takes.json"]
    for dirname in ("audio_generation_requests", "audio_take_history"):
        directory = root / dirname
        if directory.is_dir() and not directory.is_symlink():
            paths.extend(directory.rglob("*.json"))
    return sorted(set(paths), key=lambda path: path.as_posix())


def _references(root: Path) -> tuple[dict[str, str | None], list[str]]:
    references: dict[str, str | None] = {}
    unreadable: list[str] = []
    for path in _metadata_paths(root):
        state, value = _json(path)
        if state == "missing":
            continue
        if state != "ok":
            unreadable.append(path.relative_to(root).as_posix())
            continue
        _collect_nested_references(root, value, references)
    return references, unreadable


def _candidate_paths(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for dirname in ("voicelines", "audio_generation_requests", "audio_take_history"):
        directory = root / dirname
        if not directory.exists() or directory.is_symlink():
            continue
        for current, directories, filenames in os.walk(directory, followlinks=False):
            current_path = Path(current)
            for name in sorted((*directories, *filenames)):
                path = current_path / name
                try:
                    mode = path.lstat().st_mode
                except OSError:
                    continue
                if stat.S_ISLNK(mode) or (
                    stat.S_ISREG(mode)
                    and (
                        path.suffix.casefold() in _AUDIO_SUFFIXES
                        or path.name.endswith(".audio-reconcile.tmp")
                    )
                ):
                    candidates.append(path)
            directories[:] = [
                name
                for name in directories
                if not (current_path / name).is_symlink()
            ]
    return sorted(set(candidates), key=lambda path: path.as_posix())


def _category(relative: str) -> str:
    path = Path(relative)
    if path.name.endswith(".tmp") or path.name.endswith(".audio-reconcile.tmp"):
        return "temporary_file"
    if "segments" in path.parts:
        return "internal_segment_file"
    if "audio" in path.parts and (
        path.suffix.casefold() == ".bin" or "audio_take_history" in path.parts
    ):
        return "backup_file"
    if len(path.parts) >= 2 and path.parts[:2] == ("voicelines", "takes"):
        return "artifact_only"
    return "canonical_file"


def _issue(
    *,
    category: str,
    relative: str,
    state: str,
    expected: str | None,
    actual: str | None,
    removable: bool,
) -> dict[str, Any]:
    seed = {
        "category": category,
        "relative_path": relative,
        "state": state,
        "expected_sha256": expected,
        "actual_sha256": actual,
    }
    fingerprint = fingerprint_value(seed)
    actions = [{"kind": "retain_evidence", "expected_issue_fingerprint": fingerprint}]
    if removable:
        actions.append({"kind": "remove_orphan", "expected_issue_fingerprint": fingerprint})
    return {
        **seed,
        "issue_id": fingerprint[:32],
        "issue_fingerprint": fingerprint,
        "ambiguous": True,
        "actions": actions,
    }


def inspect_audio_orphans(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    references, unreadable_metadata = _references(root)
    issues: list[dict[str, Any]] = [
        _issue(
            category="metadata_only",
            relative=relative,
            state="metadata_unreadable",
            expected=None,
            actual=_sha256(root / relative),
            removable=False,
        )
        for relative in unreadable_metadata
    ]
    metadata_uncertain = bool(unreadable_metadata)
    observed: set[str] = set()
    for path in _candidate_paths(root):
        relative = path.relative_to(root).as_posix()
        observed.add(relative)
        if path.is_symlink():
            issues.append(
                _issue(
                    category=_category(relative),
                    relative=relative,
                    state="unsafe_symlink",
                    expected=references.get(relative),
                    actual=None,
                    removable=False,
                )
            )
            continue
        actual = _sha256(path)
        if actual is None:
            issues.append(
                _issue(
                    category=_category(relative),
                    relative=relative,
                    state="artifact_unreadable",
                    expected=references.get(relative),
                    actual=None,
                    removable=False,
                )
            )
            continue
        if relative not in references:
            issues.append(
                _issue(
                    category=_category(relative),
                    relative=relative,
                    state="metadata_uncertain" if metadata_uncertain else "unreferenced",
                    expected=None,
                    actual=actual,
                    removable=not metadata_uncertain,
                )
            )
        elif references[relative] == "@conflict":
            issues.append(
                _issue(
                    category="canonical_file",
                    relative=relative,
                    state="cross_reference_mismatch",
                    expected=None,
                    actual=actual,
                    removable=False,
                )
            )
        elif references[relative] is None:
            issues.append(
                _issue(
                    category="canonical_file",
                    relative=relative,
                    state="hash_unverified",
                    expected=None,
                    actual=actual,
                    removable=False,
                )
            )
        elif references[relative] != actual:
            issues.append(
                _issue(
                    category="canonical_file",
                    relative=relative,
                    state="hash_mismatch",
                    expected=references[relative],
                    actual=actual,
                    removable=False,
                )
            )
    for relative, expected in sorted(references.items()):
        if relative.startswith("@unsafe:"):
            issues.append(
                _issue(
                    category="metadata_only",
                    relative=relative.removeprefix("@unsafe:"),
                    state="unsafe_path",
                    expected=expected,
                    actual=None,
                    removable=False,
                )
            )
            continue
        if relative in observed:
            continue
        target = root / relative
        if not target.exists() and not target.is_symlink():
            issues.append(
                _issue(
                    category="metadata_only",
                    relative=relative,
                    state="missing_artifact",
                    expected=expected,
                    actual=None,
                    removable=False,
                )
            )
    issues.sort(key=lambda item: (item["category"], item["relative_path"], item["issue_id"]))
    return {
        "schema_version": 1,
        "issue_count": len(issues),
        "ambiguous_count": len(issues),
        "issues": issues,
        "status_fingerprint": fingerprint_value(issues),
    }


def reconcile_audio_orphans(project_root: str | Path) -> dict[str, Any]:
    """Return the deterministic startup reconciliation report.

    Ambiguous evidence is deliberately retained until a guarded operator action.
    """
    return inspect_audio_orphans(project_root)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _receipt_relative(issue_id: str, action: str) -> str:
    return f"{ORPHAN_RECEIPT_DIRNAME}/{issue_id}/{action}.json"


def _receipt_fingerprint(receipt: dict[str, Any]) -> str:
    return fingerprint_value(
        {key: value for key, value in receipt.items() if key != "record_fingerprint"}
    )


def _receipt_issue_fingerprint(receipt: dict[str, Any]) -> str:
    return fingerprint_value(
        {
            "category": receipt["category"],
            "relative_path": receipt["relative_path"],
            "state": receipt["issue_state"],
            "expected_sha256": receipt["expected_sha256"],
            "actual_sha256": receipt["before_sha256"],
        }
    )


def _validate_existing_receipt(
    root: Path,
    receipt: Any,
    *,
    issue_id: str,
    action: str,
    expected_issue_fingerprint: str,
) -> dict[str, Any]:
    expected_keys = {
        "schema_version",
        "issue_id",
        "issue_fingerprint",
        "category",
        "relative_path",
        "issue_state",
        "expected_sha256",
        "action",
        "before_sha256",
        "result",
        "transaction_id",
        "recorded_at_utc",
        "receipt_path",
        "record_fingerprint",
    }
    transaction_id = f"orphan-{issue_id}-{action}"
    receipt_relative = _receipt_relative(issue_id, action)
    try:
        valid = isinstance(receipt, dict) and set(receipt) == expected_keys
        expected_result = "orphan_removed" if action == "remove_orphan" else "evidence_retained"
        before_sha256 = receipt["before_sha256"]
        expected_sha256 = receipt["expected_sha256"]
        valid = valid and (
            receipt["schema_version"] == 2
            and receipt["issue_id"] == issue_id
            and receipt["issue_id"] == receipt["issue_fingerprint"][:32]
            and receipt["issue_fingerprint"] == expected_issue_fingerprint
            and receipt["action"] == action
            and receipt["result"] == expected_result
            and receipt["transaction_id"] == transaction_id
            and receipt["receipt_path"] == receipt_relative
            and isinstance(receipt["recorded_at_utc"], str)
            and bool(receipt["recorded_at_utc"])
            and isinstance(receipt["relative_path"], str)
            and isinstance(receipt["category"], str)
            and isinstance(receipt["issue_state"], str)
            and (
                expected_sha256 is None
                or isinstance(expected_sha256, str)
                and len(expected_sha256) == 64
            )
            and (
                before_sha256 is None
                or isinstance(before_sha256, str)
                and len(before_sha256) == 64
            )
            and receipt["issue_fingerprint"] == _receipt_issue_fingerprint(receipt)
            and receipt["record_fingerprint"] == _receipt_fingerprint(receipt)
        )
        relative = Path(receipt["relative_path"])
        if action == "remove_orphan":
            valid = valid and not relative.is_absolute() and ".." not in relative.parts
            artifact = root / relative
            valid = valid and before_sha256 is not None
            valid = valid and not artifact.exists() and not artifact.is_symlink()
        elif before_sha256 is not None:
            valid = valid and not relative.is_absolute() and ".." not in relative.parts
            artifact = root / relative
            valid = valid and _sha256(artifact) == before_sha256
        elif receipt["issue_state"] not in {"unsafe_path", "missing_artifact"}:
            valid = valid and not relative.is_absolute() and ".." not in relative.parts
            artifact = root / relative
            valid = valid and (artifact.exists() or artifact.is_symlink())
        if valid and action == "remove_orphan":
            journal_state, journal = _json(
                root / "audio_transition_journal" / transaction_id / "transition.json"
            )
            writes = journal.get("writes") if isinstance(journal, dict) else None
            receipt_write = writes.get(receipt_relative) if isinstance(writes, dict) else None
            artifact_write = writes.get(receipt["relative_path"]) if isinstance(writes, dict) else None
            receipt_after = receipt_write.get("after") if isinstance(receipt_write, dict) else None
            artifact_after = artifact_write.get("after") if isinstance(artifact_write, dict) else None
            journal_fingerprint = (
                fingerprint_value(
                    {key: value for key, value in journal.items() if key != "record_fingerprint"}
                )
                if isinstance(journal, dict)
                else None
            )
            valid = (
                journal_state == "ok"
                and journal.get("operation_id") == transaction_id
                and journal.get("transition") == "invalidation"
                and journal.get("status") == "committed"
                and journal.get("record_fingerprint") == journal_fingerprint
                and isinstance(writes, dict)
                and set(writes) == {receipt_relative, receipt["relative_path"]}
                and isinstance(artifact_after, dict)
                and artifact_after.get("exists") is False
                and isinstance(receipt_after, dict)
                and receipt_after.get("sha256") == _sha256(root / receipt_relative)
            )
    except (AttributeError, KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise OrphanReconciliationError(
            "audio_orphan_receipt_invalid",
            "The existing orphan action receipt is incomplete, inconsistent, or forged.",
        )
    return receipt


def apply_audio_orphan_action(
    project_root: str | Path,
    *,
    issue_id: str,
    action: str,
    expected_issue_fingerprint: str,
) -> dict[str, Any]:
    from audio_crash_reconciliation import apply_audio_transition, audio_project_lock

    root = Path(project_root).expanduser().resolve()
    if not re.fullmatch(r"[0-9a-f]{32}", issue_id) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_issue_fingerprint
    ):
        raise OrphanReconciliationError(
            "audio_orphan_action_stale",
            "Orphan evidence identity is invalid; refresh status before acting.",
        )
    if action not in {"retain_evidence", "remove_orphan"}:
        raise OrphanReconciliationError(
            "audio_orphan_action_unavailable",
            "The requested action is not available for orphan evidence.",
        )
    with audio_project_lock(root):
        receipt_relative = _receipt_relative(issue_id, action)
        receipt_path = root / receipt_relative
        receipt_state, existing = _json(receipt_path)
        if receipt_state == "ok":
            return _validate_existing_receipt(
                root,
                existing,
                issue_id=issue_id,
                action=action,
                expected_issue_fingerprint=expected_issue_fingerprint,
            )
        if receipt_state != "missing":
            raise OrphanReconciliationError(
                "audio_orphan_receipt_invalid",
                "The existing orphan action receipt is unreadable.",
            )
        issue = next(
            (
                item
                for item in inspect_audio_orphans(root)["issues"]
                if item["issue_id"] == issue_id
            ),
            None,
        )
        if issue is None or issue["issue_fingerprint"] != expected_issue_fingerprint:
            raise OrphanReconciliationError(
                "audio_orphan_action_stale",
                "Orphan evidence changed; refresh status before acting.",
            )
        if action not in {item["kind"] for item in issue["actions"]}:
            raise OrphanReconciliationError(
                "audio_orphan_action_unavailable",
                "The requested action is not available for this evidence.",
            )
        transaction_id = f"orphan-{issue_id}-{action}"
        receipt = {
            "schema_version": 2,
            "issue_id": issue_id,
            "issue_fingerprint": expected_issue_fingerprint,
            "category": issue["category"],
            "relative_path": issue["relative_path"],
            "issue_state": issue["state"],
            "expected_sha256": issue["expected_sha256"],
            "action": action,
            "before_sha256": issue["actual_sha256"],
            "result": "orphan_removed" if action == "remove_orphan" else "evidence_retained",
            "transaction_id": transaction_id,
            "recorded_at_utc": _utc_now(),
            "receipt_path": receipt_relative,
            "record_fingerprint": None,
        }
        receipt["record_fingerprint"] = _receipt_fingerprint(receipt)
        if action == "remove_orphan":
            apply_audio_transition(
                root,
                transition="invalidation",
                operation_id=transaction_id,
                json_writes={receipt_relative: receipt},
                deletes=[issue["relative_path"]],
            )
        else:
            atomic_json_write(receipt, receipt_path)
        return _validate_existing_receipt(
            root,
            receipt,
            issue_id=issue_id,
            action=action,
            expected_issue_fingerprint=expected_issue_fingerprint,
        )
