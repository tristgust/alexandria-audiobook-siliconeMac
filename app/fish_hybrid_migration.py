from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fish_hybrid_policy import apply_fish_hybrid_policy, eligible_for_fish_hybrid
from utils import atomic_json_write


class FishHybridMigrationError(RuntimeError):
    pass


def _load_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FishHybridMigrationError(
            f"Voice configuration could not be read: {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise FishHybridMigrationError(
            f"Voice configuration must contain an object: {path}"
        )
    return value


def migrate_voice_config_path(
    path: str | Path,
    *,
    enabled: bool,
    dry_run: bool = False,
) -> dict[str, Any]:
    target = Path(path).expanduser().resolve()
    config = _load_config(target)
    if not config:
        return {"path": str(target), "eligible": 0, "changed": 0, "exists": False}
    eligible = 0
    changed = 0
    updated: dict[str, Any] = {}
    for name, raw in config.items():
        if not isinstance(raw, dict):
            updated[name] = raw
            continue
        voice = dict(raw)
        if eligible_for_fish_hybrid(voice):
            eligible += 1
            replacement = apply_fish_hybrid_policy(voice, enabled=enabled)
            if replacement != voice:
                changed += 1
            voice = replacement
        updated[name] = voice
    if changed and not dry_run:
        atomic_json_write(updated, target)
    return {
        "path": str(target),
        "eligible": eligible,
        "changed": changed,
        "exists": True,
        "dry_run": dry_run,
    }


def migrate_fish_hybrid_policy(
    *,
    reusable_root: str | Path,
    managed_projects_root: str | Path,
    active_project_root: str | Path,
    enabled: bool,
    dry_run: bool = False,
) -> dict[str, Any]:
    reusable = Path(reusable_root).expanduser().resolve()
    projects = Path(managed_projects_root).expanduser().resolve()
    active = Path(active_project_root).expanduser().resolve()
    paths: list[Path] = [reusable / "voice_config.json"]
    if projects.is_dir() and not projects.is_symlink():
        for project in sorted(projects.iterdir()):
            if project.is_dir() and not project.is_symlink():
                paths.append(project / "voice_config.json")
    paths.append(active / "voice_config.json")

    seen: set[Path] = set()
    results: list[dict[str, Any]] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        results.append(
            migrate_voice_config_path(
                resolved,
                enabled=enabled,
                dry_run=dry_run,
            )
        )
    return {
        "status": "enabled" if enabled else "disabled",
        "enabled": enabled,
        "dry_run": dry_run,
        "files_considered": len(results),
        "files_changed": sum(item["changed"] > 0 for item in results),
        "eligible_voice_count": sum(item["eligible"] for item in results),
        "changed_voice_count": sum(item["changed"] for item in results),
        "results": results,
    }
