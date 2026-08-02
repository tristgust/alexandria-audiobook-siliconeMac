from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from b19_t07_protected_state import (
    compare_live_protected_state,
    compare_protected_state,
    read_live_protected_state_manifest,
    read_protected_state_manifest,
    snapshot_live_protected_state,
    write_live_protected_state_manifest,
)


DEFAULT_BASELINE = Path(
    ".omo/evidence/b19-t07-accessibility-release-gate-20260801/"
    "task-09-integration/protected-before-post-b16.json"
)


def _head(repo_root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True
    ).strip()


def _live_result(comparison: object, *, status: str) -> dict[str, object]:
    return {
        "changed_classes": list(comparison.changed_classes),
        "git_errors": list(comparison.git_errors),
        "error": None if comparison.ok else "protected_state_changed",
        "ok": comparison.ok,
        "status": status if comparison.ok else "BLOCKED",
    }


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifests", nargs="*")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--capture-live", type=Path)
    parser.add_argument("--compare-live", nargs=2, metavar=("BEFORE", "AFTER"), type=Path)
    parser.add_argument("--candidate-sha")
    parser.add_argument("--mode", choices=("candidate",))
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    values = parser.parse_args(arguments)
    repo_root = values.repo_root.resolve()
    if values.capture_live:
        manifest = snapshot_live_protected_state(repo_root)
        write_live_protected_state_manifest(values.capture_live, manifest)
        print(json.dumps({
            "ok": True,
            "path": str(values.capture_live),
            "status": "CAPTURED",
        }, sort_keys=True))
        return 0
    if values.compare_live:
        before_path, after_path = values.compare_live
        candidate_sha = values.candidate_sha or _head(repo_root)
        comparison = compare_live_protected_state(
            read_live_protected_state_manifest(before_path),
            read_live_protected_state_manifest(after_path),
            candidate_sha=candidate_sha,
        )
        print(json.dumps(_live_result(comparison, status="PASS"), sort_keys=True))
        return 0 if comparison.ok else 1
    if values.mode == "candidate":
        candidate_sha = values.candidate_sha or _head(repo_root)
        comparison = compare_live_protected_state(
            read_live_protected_state_manifest(values.baseline),
            snapshot_live_protected_state(repo_root),
            candidate_sha=candidate_sha,
        )
        clean = not subprocess.check_output(
            ["git", "status", "--porcelain=v1"], cwd=repo_root, text=True
        ).strip()
        result = _live_result(comparison, status="READY_TO_CLOSE")
        result["clean_worktree"] = clean
        if not clean:
            result.update({"ok": False, "status": "BLOCKED", "error": "worktree_not_clean"})
        print(json.dumps(result, sort_keys=True))
        return 0 if result["ok"] else 1
    if len(values.manifests) != 2:
        parser.error("provide BEFORE AFTER, --capture-live, --compare-live, or --mode candidate")
    comparison = compare_protected_state(
        read_protected_state_manifest(Path(values.manifests[0])),
        read_protected_state_manifest(Path(values.manifests[1])),
    )
    print(json.dumps({
        "changed_classes": list(comparison.changed_classes),
        "error": None if comparison.ok else "protected_state_changed",
        "ok": comparison.ok,
    }, sort_keys=True))
    return 0 if comparison.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
