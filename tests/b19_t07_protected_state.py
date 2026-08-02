from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Iterable


PROTECTED_CLASSES: Final = (
    "projects", "scripts", "meta", "chunks", "Voice", "roster", "checkpoints", "logs",
    "audio", "finals", "receipts", "model_registry", "cache", "installed_experimental_model_inventory",
)
SENSITIVE_NAME_PARTS: Final = ("credential", "secret", "token", "password", ".env", "key")
LIVE_PROTECTED_CLASSES: Final = (
    "normal_catalog",
    "project_storage",
    "project_script_and_meta",
    "chunks_and_roster",
    "checkpoints",
    "logs",
    "audio",
    "takes",
    "voice_assets_and_config",
    "final_outputs",
    "receipts_and_history",
    "provider_configuration",
    "credential_storage",
    "model_registry",
    "model_cache",
    "installed_experimental_models",
)
TASK_BRANCH: Final = "refs/heads/alexandria/b19-t07-accessibility-release-gate-20260801"
TASK_WORKTREE: Final = "/Users/tristan/.devspace/worktrees/alexandria-b19-t07-accessibility-release-gate-20260801"
CANONICAL_REPO: Final = Path("/Users/tristan/pinokio/api/alexandria-audiobook.git")
ALEXANDRIA_DATA_ROOT: Final = Path.home() / "Library/Application Support/Alexandria"
MODEL_CACHE_ROOT: Final = Path.home() / ".cache/huggingface/hub"
AUDIO_SUFFIXES: Final = frozenset((".wav", ".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".aac"))


@dataclass(frozen=True, slots=True)
class ProtectedStateError(Exception):
    root: Path

    def __str__(self) -> str:
        return f"protected state contains a path escape: {self.root}"


@dataclass(frozen=True, slots=True)
class StateEntry:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class StateClass:
    name: str
    entries: tuple[StateEntry, ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class ProtectedStateManifest:
    classes: tuple[StateClass, ...]


@dataclass(frozen=True, slots=True)
class ProtectedStateComparison:
    changed_classes: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.changed_classes


@dataclass(frozen=True, slots=True)
class LiveProtectedStateComparison:
    changed_classes: tuple[str, ...]
    git_errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.changed_classes and not self.git_errors


@dataclass(frozen=True, slots=True)
class _StatEntry:
    logical_path: str
    size: int
    mtime_ns: int


def _sensitive(path: Path) -> bool:
    lower_name = path.name.casefold()
    return any(part in lower_name for part in SENSITIVE_NAME_PARTS)


def _entry(root: Path, path: Path) -> StateEntry:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError as error:
        raise ProtectedStateError(root) from error
    digest = "<credential-redacted>" if _sensitive(path) else hashlib.sha256(path.read_bytes()).hexdigest()
    return StateEntry(path=relative, size=path.stat().st_size, sha256=digest)


def _state_class(root: Path, name: str) -> StateClass:
    class_root = root / name
    if not class_root.exists():
        return StateClass(name=name, entries=(), sha256=hashlib.sha256(b"<absent>").hexdigest())
    entries = tuple(_entry(root, path) for path in sorted(class_root.rglob("*")) if path.is_file())
    material = "\n".join(f"{entry.path}\0{entry.size}\0{entry.sha256}" for entry in entries).encode("utf-8")
    return StateClass(name=name, entries=entries, sha256=hashlib.sha256(material).hexdigest())


def snapshot_protected_state(root: Path) -> ProtectedStateManifest:
    resolved_root = root.resolve()
    return ProtectedStateManifest(classes=tuple(_state_class(resolved_root, name) for name in PROTECTED_CLASSES))


def compare_protected_state(before: ProtectedStateManifest, after: ProtectedStateManifest) -> ProtectedStateComparison:
    before_hashes = {item.name: item.sha256 for item in before.classes}
    after_hashes = {item.name: item.sha256 for item in after.classes}
    changed = tuple(
        name for name in PROTECTED_CLASSES
        if name not in before_hashes or name not in after_hashes or before_hashes[name] != after_hashes[name]
    )
    return ProtectedStateComparison(changed_classes=changed)


def write_protected_state_manifest(path: Path, manifest: ProtectedStateManifest) -> None:
    payload = {"classes": [
        {"name": item.name, "sha256": item.sha256, "entries": [
            {"path": entry.path, "size": entry.size, "sha256": entry.sha256} for entry in item.entries
        ]} for item in manifest.classes
    ]}
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def read_protected_state_manifest(path: Path) -> ProtectedStateManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    classes = tuple(StateClass(
        name=item["name"],
        sha256=item["sha256"],
        entries=tuple(StateEntry(path=entry["path"], size=entry["size"], sha256=entry["sha256"]) for entry in item["entries"]),
    ) for item in payload["classes"])
    return ProtectedStateManifest(classes=classes)


def _hash_material(material: bytes) -> str:
    return hashlib.sha256(material).hexdigest()


def _absent_class() -> dict[str, Any]:
    return {
        "presence": False,
        "digest_kind": "stat_metadata_manifest_sha256",
        "digest": _hash_material(b"<absent>"),
        "inventory": {"files": 0, "bytes": 0},
    }


def _scan_stat_entries(root: Path, label: str) -> tuple[_StatEntry, ...]:
    if not root.exists():
        return ()
    resolved_root = root.resolve()
    entries: list[_StatEntry] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        resolved = path.resolve()
        try:
            resolved.relative_to(resolved_root)
        except ValueError as error:
            raise ProtectedStateError(resolved_root) from error
        stat = path.stat()
        entries.append(_StatEntry(
            logical_path=f"{label}/{path.relative_to(root).as_posix()}",
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        ))
    return tuple(sorted(entries, key=lambda item: item.logical_path))


def _stat_class(entries: Iterable[_StatEntry]) -> dict[str, Any]:
    selected = tuple(sorted(entries, key=lambda item: item.logical_path))
    if not selected:
        return _absent_class()
    material = "\n".join(
        f"{item.logical_path}\0{item.size}\0{item.mtime_ns}" for item in selected
    ).encode("utf-8")
    return {
        "presence": True,
        "digest_kind": "stat_metadata_manifest_sha256",
        "digest": _hash_material(material),
        "inventory": {
            "files": len(selected),
            "bytes": sum(item.size for item in selected),
        },
    }


def _content_class(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "presence": False,
            "digest_kind": "content_sha256",
            "digest": _hash_material(b"<absent>"),
            "inventory": {"files": 0, "bytes": 0},
        }
    return {
        "presence": True,
        "digest_kind": "content_sha256",
        "digest": hashlib.sha256(path.read_bytes()).hexdigest(),
        "inventory": {"files": 1, "bytes": path.stat().st_size},
    }


def _run_git(repo: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *arguments],
        text=True,
        stderr=subprocess.STDOUT,
    )


def _worktree_inventory(repo: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in _run_git(repo, "worktree", "list", "--porcelain").splitlines():
        if not line:
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if current:
        records.append(current)
    return sorted(records, key=lambda item: item.get("worktree", ""))


def _ref_inventory(repo: Path) -> dict[str, str]:
    return {
        ref: sha
        for line in _run_git(repo, "for-each-ref", "--format=%(refname) %(objectname)").splitlines()
        if line
        for ref, sha in [line.split(" ", 1)]
    }


def snapshot_live_protected_state(
    repo_root: Path,
    *,
    canonical_repo: Path = CANONICAL_REPO,
    data_root: Path = ALEXANDRIA_DATA_ROOT,
    model_cache_root: Path = MODEL_CACHE_ROOT,
) -> dict[str, Any]:
    projects_root = data_root / "Projects"
    project_entries = _scan_stat_entries(projects_root, "Projects")
    model_entries = _scan_stat_entries(model_cache_root, "huggingface-hub")

    def project_filter(predicate: Any) -> tuple[_StatEntry, ...]:
        return tuple(item for item in project_entries if predicate(item.logical_path.casefold()))

    script_names = (
        "/state.json", "/generation_state.json", "/annotated_script.json",
        "/annotated_script.meta.json", "/chunks.json", "/character_roster.json",
    )
    chunk_names = ("/chunks.json", "/character_roster.json")
    classes = {
        "normal_catalog": _content_class(data_root / "projects.json"),
        "project_storage": _stat_class(project_entries),
        "project_script_and_meta": _stat_class(project_filter(
            lambda value: value.endswith(script_names)
        )),
        "chunks_and_roster": _stat_class(project_filter(
            lambda value: value.endswith(chunk_names)
        )),
        "checkpoints": _stat_class(project_filter(
            lambda value: any("checkpoint" in part for part in value.split("/"))
        )),
        "logs": _stat_class(project_filter(
            lambda value: any(part == "logs" or part.endswith(".log") for part in value.split("/"))
        )),
        "audio": _stat_class(project_filter(
            lambda value: Path(value).suffix in AUDIO_SUFFIXES
        )),
        "takes": _stat_class(project_filter(
            lambda value: value.endswith("/audio_takes.json") or "/voicelines/takes/" in value
        )),
        "voice_assets_and_config": _stat_class(project_filter(
            lambda value: "voice" in value
        )),
        "final_outputs": _stat_class(project_filter(
            lambda value: value.endswith("/audiobook.m4b")
            or "/export_build" in value
            or "/final" in value
        )),
        "receipts_and_history": _stat_class(project_filter(
            lambda value: "receipt" in value or "/audio_take_history/" in value
        )),
        "provider_configuration": _content_class(canonical_repo / "config.json"),
        "credential_storage": _content_class(canonical_repo / "config.json"),
        "model_registry": _content_class(canonical_repo / "app/model_registry.py"),
        "model_cache": _stat_class(model_entries),
        "installed_experimental_models": _stat_class(
            item for item in (*project_entries, *model_entries)
            if any(marker in item.logical_path.casefold() for marker in ("model", "lora", "qwen"))
        ),
    }
    refs = _ref_inventory(canonical_repo)
    return {
        "schema_version": 2,
        "task": "B19-T07 live protected-state guard",
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "repo_root": str(repo_root.resolve()),
        "classes": classes,
        "git": {
            "worktrees": _worktree_inventory(canonical_repo),
            "refs": refs,
            "tags": {name: sha for name, sha in refs.items() if name.startswith("refs/tags/")},
            "remotes": sorted(_run_git(canonical_repo, "remote", "-v").splitlines()),
        },
    }


def write_live_protected_state_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_live_protected_state_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 2:
        raise ValueError(f"unsupported live protected-state schema: {payload.get('schema_version')!r}")
    return payload


def compare_live_protected_state(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    candidate_sha: str,
) -> LiveProtectedStateComparison:
    before_classes = before.get("classes", {})
    after_classes = after.get("classes", {})
    changed_classes = tuple(
        name for name in LIVE_PROTECTED_CLASSES
        if before_classes.get(name, {}).get("digest") != after_classes.get(name, {}).get("digest")
    )
    git_errors: list[str] = []
    before_git = before.get("git", {})
    after_git = after.get("git", {})

    before_worktrees = {
        item.get("worktree"): item for item in before_git.get("worktrees", [])
    }
    after_worktrees = {
        item.get("worktree"): item for item in after_git.get("worktrees", [])
    }
    if set(before_worktrees) != set(after_worktrees):
        git_errors.append("worktree_set_changed")
    for path in sorted(set(before_worktrees) & set(after_worktrees)):
        before_item = before_worktrees[path]
        after_item = after_worktrees[path]
        if path == TASK_WORKTREE:
            if after_item.get("HEAD") != candidate_sha:
                git_errors.append("task_worktree_head_mismatch")
            if before_item.get("branch") != TASK_BRANCH or after_item.get("branch") != TASK_BRANCH:
                git_errors.append("task_worktree_branch_mismatch")
            continue
        if before_item != after_item:
            git_errors.append(f"protected_worktree_changed:{path}")

    before_refs = before_git.get("refs", {})
    after_refs = after_git.get("refs", {})
    all_refs = set(before_refs) | set(after_refs)
    for ref in sorted(all_refs):
        if ref == TASK_BRANCH:
            if after_refs.get(ref) != candidate_sha:
                git_errors.append("task_branch_head_mismatch")
            continue
        if before_refs.get(ref) != after_refs.get(ref):
            git_errors.append(f"protected_ref_changed:{ref}")
    if before_git.get("tags") != after_git.get("tags"):
        git_errors.append("tags_changed")
    if before_git.get("remotes") != after_git.get("remotes"):
        git_errors.append("remotes_changed")
    return LiveProtectedStateComparison(
        changed_classes=changed_classes,
        git_errors=tuple(dict.fromkeys(git_errors)),
    )
