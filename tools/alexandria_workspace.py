#!/usr/bin/env python3
"""Inspect and maintain Alexandria's canonical local workspace topology.

The normal Pinokio checkout remains the Git source and launcher authority.  This
tool reads a machine-specific manifest from the canonical checkout's ignored
``.omo/state`` directory and provides read-only inventory/verification plus a
guarded worktree cleanup path.

No command performs broad deletion.  Cleanup requires an exact, fingerprinted
plan and revalidates every worktree immediately before removal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
DEFAULT_MANIFEST = ".omo/state/alexandria-canonical-locations.json"


class WorkspaceError(RuntimeError):
    """Raised when the canonical workspace contract is violated."""


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    result = _run(["git", "-C", str(cwd), *args], check=check)
    return result.stdout


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _expand(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


def _link_path(value: str) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(value)))
    return path if path.is_absolute() else (Path.cwd() / path).absolute()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkspaceError(f"Canonical location manifest is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise WorkspaceError(f"Canonical location manifest is invalid JSON: {path}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise WorkspaceError(
            f"Canonical location manifest must use schema_version {SCHEMA_VERSION}."
        )
    return value


def _manifest_path(source_root: Path, explicit: str | None) -> Path:
    return _expand(explicit) if explicit else source_root / DEFAULT_MANIFEST


def _branch_name(raw: str | None) -> str | None:
    if not raw:
        return None
    return raw.removeprefix("refs/heads/")


def _parse_worktrees(source_root: Path) -> list[dict[str, Any]]:
    raw = _git(source_root, "worktree", "list", "--porcelain")
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in [*raw.splitlines(), ""]:
        if not line:
            if current:
                current["branch"] = _branch_name(current.get("branch"))
                rows.append(current)
                current = {}
            continue
        key, *rest = line.split(" ", 1)
        current[key] = rest[0] if rest else True
    return rows


def _status_paths(path: Path) -> list[str]:
    return [
        row
        for row in _git(
            path,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ).splitlines()
        if row
    ]


def _ignored_paths(path: Path) -> list[str]:
    rows = _git(
        path,
        "status",
        "--porcelain=v1",
        "--ignored",
        "--untracked-files=all",
    ).splitlines()
    return [row[3:] for row in rows if row.startswith("!! ")]


def _path_size(path: Path) -> int:
    if path.is_symlink() or not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for candidate in path.rglob("*"):
        try:
            if candidate.is_file() and not candidate.is_symlink():
                total += candidate.stat().st_size
        except FileNotFoundError:
            continue
    return total


def _ignored_summary(path: Path) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for relative in _ignored_paths(path):
        top = relative.split("/", 1)[0]
        counts[top] = counts.get(top, 0) + 1
    roots = []
    for name in sorted(counts):
        roots.append(
            {
                "name": name,
                "entry_count": counts[name],
                "size_bytes": _path_size(path / name),
            }
        )
    return {
        "entry_count": sum(counts.values()),
        "size_bytes": sum(row["size_bytes"] for row in roots),
        "roots": roots,
    }


def _is_ancestor(source_root: Path, ancestor: str, descendant: str) -> bool:
    return (
        _run(
            ["git", "-C", str(source_root), "merge-base", "--is-ancestor", ancestor, descendant],
            check=False,
        ).returncode
        == 0
    )


def _head_is_referenced(source_root: Path, head: str) -> bool:
    branches = _git(
        source_root,
        "for-each-ref",
        "--contains",
        head,
        "--format=%(refname)",
        "refs/heads",
        "refs/tags",
    ).splitlines()
    return bool(branches)


@dataclass(frozen=True)
class CanonicalPaths:
    human_root: Path
    source_root: Path
    runtime_root: Path
    control_root: Path
    projects_root: Path
    archive_root: Path
    evidence_archive: Path
    sources_root: Path
    voice_sources: Path
    workspace_link: Path
    worktree_root: Path
    compatibility_links: dict[Path, Path]

    @classmethod
    def from_manifest(cls, manifest: dict[str, Any]) -> "CanonicalPaths":
        compatibility = {
            _link_path(source): _expand(target)
            for source, target in dict(manifest.get("compatibility_links") or {}).items()
        }
        return cls(
            human_root=_expand(str(manifest["canonical_human_root"])),
            source_root=_expand(str(manifest["canonical_source_root"])),
            runtime_root=_expand(str(manifest["canonical_runtime_root"])),
            control_root=_expand(str(manifest["canonical_control_root"])),
            projects_root=_expand(str(manifest["canonical_projects_root"])),
            archive_root=_expand(str(manifest["canonical_archive_root"])),
            evidence_archive=_expand(str(manifest["evidence_archive_git"])),
            sources_root=_expand(str(manifest["canonical_sources_root"])),
            voice_sources=_expand(str(manifest["voice_sources_root"])),
            workspace_link=_link_path(str(manifest["workspace_link"])),
            worktree_root=_expand(str(manifest["worktree_root"])),
            compatibility_links=compatibility,
        )


def _worktree_inventory(
    source_root: Path,
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    active = set(manifest.get("active_worktree_branches") or [])
    retired = set(manifest.get("retired_worktree_branches") or [])
    canonical_head = _git(source_root, "rev-parse", "HEAD").strip()
    records = []
    for row in _parse_worktrees(source_root):
        path = _expand(str(row["worktree"]))
        head = str(row.get("HEAD") or "")
        branch = row.get("branch")
        dirty = _status_paths(path)
        ignored = _ignored_summary(path)
        if path == source_root:
            disposition = "canonical"
        elif branch in active:
            disposition = "active"
        elif dirty:
            disposition = "dirty_quarantine"
        elif branch in retired:
            disposition = "retired_clean"
        elif head and _is_ancestor(source_root, head, canonical_head):
            disposition = "integrated_clean"
        else:
            disposition = "historical_clean"
        records.append(
            {
                "path": str(path),
                "branch": branch,
                "head": head,
                "dirty_paths": dirty,
                "ignored": ignored,
                "head_referenced": bool(head and _head_is_referenced(source_root, head)),
                "merged_into_canonical": bool(
                    head and _is_ancestor(source_root, head, canonical_head)
                ),
                "disposition": disposition,
            }
        )
    return records


def build_inventory(manifest: dict[str, Any]) -> dict[str, Any]:
    paths = CanonicalPaths.from_manifest(manifest)
    worktrees = _worktree_inventory(paths.source_root, manifest)
    return {
        "schema_version": SCHEMA_VERSION,
        "manifest_fingerprint": _sha256_json(manifest),
        "canonical": {
            "human_root": str(paths.human_root),
            "source_root": str(paths.source_root),
            "runtime_root": str(paths.runtime_root),
            "control_root": str(paths.control_root),
            "projects_root": str(paths.projects_root),
            "archive_root": str(paths.archive_root),
            "evidence_archive_git": str(paths.evidence_archive),
            "sources_root": str(paths.sources_root),
            "voice_sources_root": str(paths.voice_sources),
            "workspace_link": str(paths.workspace_link),
            "worktree_root": str(paths.worktree_root),
        },
        "git": {
            "branch": _git(paths.source_root, "branch", "--show-current").strip(),
            "head": _git(paths.source_root, "rev-parse", "HEAD").strip(),
            "status": _status_paths(paths.source_root),
            "worktree_count": len(worktrees),
        },
        "worktrees": worktrees,
    }


def cleanup_plan(inventory: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    source_root = _expand(str(manifest["canonical_source_root"]))
    active = set(manifest.get("active_worktree_branches") or [])
    candidates = []
    preserved = []
    for row in inventory["worktrees"]:
        path = _expand(row["path"])
        reasons = []
        if path == source_root:
            reasons.append("canonical_source")
        if row.get("branch") in active:
            reasons.append("active_branch")
        if row.get("dirty_paths"):
            reasons.append("dirty_worktree")
        if not row.get("head_referenced"):
            reasons.append("unreferenced_head")
        if row.get("disposition") not in {"integrated_clean", "retired_clean"}:
            reasons.append(f"disposition:{row.get('disposition')}")
        if reasons:
            preserved.append({"path": row["path"], "reasons": sorted(set(reasons))})
            continue
        candidates.append(
            {
                "path": row["path"],
                "branch": row.get("branch"),
                "head": row.get("head"),
                "ignored": row.get("ignored"),
                "disposition": row.get("disposition"),
            }
        )
    plan = {
        "schema_version": SCHEMA_VERSION,
        "source_root": str(source_root),
        "source_head": inventory["git"]["head"],
        "manifest_fingerprint": inventory["manifest_fingerprint"],
        "candidates": candidates,
        "preserved": preserved,
    }
    plan["plan_fingerprint"] = _sha256_json(plan)
    return plan


def _validate_link(path: Path, target: Path) -> str | None:
    if not path.is_symlink():
        return f"Compatibility path is not a symlink: {path}"
    if path.resolve() != target:
        return f"Compatibility path resolves to {path.resolve()}, expected {target}: {path}"
    return None


def verify(manifest: dict[str, Any]) -> dict[str, Any]:
    paths = CanonicalPaths.from_manifest(manifest)
    errors: list[str] = []
    for required in (
        paths.human_root,
        paths.source_root,
        paths.runtime_root,
        paths.control_root,
        paths.projects_root,
        paths.archive_root,
        paths.sources_root,
        paths.voice_sources,
        paths.worktree_root,
    ):
        if not required.exists():
            errors.append(f"Required canonical path is missing: {required}")
    for launcher_file in ("pinokio.js", "start.js", "app"):
        if not (paths.source_root / launcher_file).exists():
            errors.append(f"Canonical source is missing {launcher_file}: {paths.source_root}")
    workspace_error = _validate_link(paths.workspace_link, paths.source_root)
    if workspace_error:
        errors.append(workspace_error)
    for link, target in paths.compatibility_links.items():
        error = _validate_link(link, target)
        if error:
            errors.append(error)
    if not paths.evidence_archive.exists():
        errors.append(f"Evidence archive is missing: {paths.evidence_archive}")
    else:
        bare = _run(
            ["git", f"--git-dir={paths.evidence_archive}", "rev-parse", "--is-bare-repository"],
            check=False,
        )
        if bare.returncode or bare.stdout.strip() != "true":
            errors.append(f"Evidence archive is not a valid bare Git repository: {paths.evidence_archive}")
        fsck = _run(
            ["git", f"--git-dir={paths.evidence_archive}", "fsck", "--full", "--no-progress"],
            check=False,
        )
        if fsck.returncode:
            errors.append(f"Evidence archive fsck failed: {fsck.stderr.strip()}")
    inventory = build_inventory(manifest)
    retired_mounted = [
        row
        for row in inventory["worktrees"]
        if row["disposition"] in {"integrated_clean", "retired_clean"}
    ]
    if retired_mounted:
        errors.append(
            "Clean retired worktrees remain mounted: "
            + ", ".join(row["path"] for row in retired_mounted)
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "inventory": inventory,
    }


def apply_cleanup(
    manifest: dict[str, Any],
    plan_path: Path,
    expected_fingerprint: str,
    receipt_path: Path,
) -> dict[str, Any]:
    plan = _load_json(plan_path)
    actual_fingerprint = plan.get("plan_fingerprint")
    if actual_fingerprint != expected_fingerprint:
        raise WorkspaceError(
            f"Cleanup plan fingerprint mismatch: {actual_fingerprint} != {expected_fingerprint}"
        )
    if _sha256_json({key: value for key, value in plan.items() if key != "plan_fingerprint"}) != actual_fingerprint:
        raise WorkspaceError("Cleanup plan content no longer matches its fingerprint.")
    current_inventory = build_inventory(manifest)
    current_plan = cleanup_plan(current_inventory, manifest)
    if current_plan["plan_fingerprint"] != actual_fingerprint:
        raise WorkspaceError("Workspace topology changed after cleanup review; generate a new plan.")
    removed = []
    for candidate in plan["candidates"]:
        path = _expand(candidate["path"])
        if _status_paths(path):
            raise WorkspaceError(f"Worktree became dirty before cleanup: {path}")
        _git(_expand(plan["source_root"]), "worktree", "remove", str(path))
        removed.append(candidate)
    _git(_expand(plan["source_root"]), "worktree", "prune")
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "operation": "alexandria_worktree_cleanup",
        "plan_fingerprint": actual_fingerprint,
        "source_head": plan["source_head"],
        "removed": removed,
        "preserved": plan["preserved"],
        "post_inventory": build_inventory(manifest),
    }
    receipt["receipt_fingerprint"] = _sha256_json(receipt)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def _write_json(value: Any, path: Path | None) -> None:
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root",
        default=str(Path(__file__).resolve().parents[1]),
        help="Canonical Pinokio checkout root.",
    )
    parser.add_argument("--manifest", help="Override the ignored local manifest path.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("inventory", "verify", "cleanup-plan"):
        child = subparsers.add_parser(command)
        child.add_argument("--output", type=Path)
    apply_parser = subparsers.add_parser("apply-cleanup")
    apply_parser.add_argument("--plan", type=Path, required=True)
    apply_parser.add_argument("--expected-fingerprint", required=True)
    apply_parser.add_argument("--receipt", type=Path, required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    source_root = _expand(args.source_root)
    manifest_path = _manifest_path(source_root, args.manifest)
    manifest = _load_json(manifest_path)
    if _expand(str(manifest["canonical_source_root"])) != source_root:
        raise WorkspaceError(
            f"Manifest source root does not match --source-root: {manifest_path}"
        )
    if args.command == "inventory":
        _write_json(build_inventory(manifest), args.output)
        return 0
    if args.command == "cleanup-plan":
        _write_json(cleanup_plan(build_inventory(manifest), manifest), args.output)
        return 0
    if args.command == "verify":
        result = verify(manifest)
        _write_json(result, args.output)
        return 0 if result["status"] == "PASS" else 1
    if args.command == "apply-cleanup":
        _write_json(
            apply_cleanup(
                manifest,
                args.plan,
                args.expected_fingerprint,
                args.receipt,
            ),
            None,
        )
        return 0
    raise WorkspaceError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WorkspaceError as exc:
        sys.stderr.write(f"alexandria-workspace: {exc}\n")
        raise SystemExit(2) from exc
