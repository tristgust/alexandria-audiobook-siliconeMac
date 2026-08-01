from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "alexandria_workspace.py"
SPEC = importlib.util.spec_from_file_location("alexandria_workspace", MODULE_PATH)
assert SPEC and SPEC.loader
workspace = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = workspace
SPEC.loader.exec_module(workspace)


class AlexandriaWorkspaceTests(unittest.TestCase):
    def test_cleanup_plan_only_selects_integrated_or_retired_clean_worktrees(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = Path(temporary) / "source"
            source.mkdir()
            manifest = {
                "schema_version": 1,
                "canonical_human_root": str(base / "human"),
                "canonical_source_root": str(source),
                "active_worktree_branches": ["research/active"],
            }
            inventory = {
                "manifest_fingerprint": "m" * 64,
                "git": {"head": "h" * 40},
                "worktrees": [
                    {
                        "path": str(source),
                        "branch": "main",
                        "head": "a" * 40,
                        "dirty_paths": [],
                        "head_referenced": True,
                        "disposition": "canonical",
                        "ignored": {},
                    },
                    {
                        "path": str(Path(temporary) / "integrated"),
                        "branch": "feature/done",
                        "head": "b" * 40,
                        "dirty_paths": [],
                        "head_referenced": True,
                        "disposition": "integrated_clean",
                        "ignored": {"entry_count": 2},
                    },
                    {
                        "path": str(Path(temporary) / "dirty"),
                        "branch": "feature/dirty",
                        "head": "c" * 40,
                        "dirty_paths": [" M app.py"],
                        "head_referenced": True,
                        "disposition": "dirty_quarantine",
                        "ignored": {},
                    },
                    {
                        "path": str(Path(temporary) / "active"),
                        "branch": "research/active",
                        "head": "d" * 40,
                        "dirty_paths": [],
                        "head_referenced": True,
                        "disposition": "active",
                        "ignored": {},
                    },
                ],
            }

            plan = workspace.cleanup_plan(inventory, manifest)

            self.assertEqual(
                [Path(row["path"]).name for row in plan["candidates"]],
                ["integrated"],
            )
            preserved = {Path(row["path"]).name: row["reasons"] for row in plan["preserved"]}
            self.assertIn("canonical_source", preserved["source"])
            self.assertIn("dirty_worktree", preserved["dirty"])
            self.assertIn("active_branch", preserved["active"])
            self.assertEqual(len(plan["plan_fingerprint"]), 64)

    def test_canonical_paths_expand_and_resolve_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "source"
            runtime = base / "runtime"
            projects = runtime / "Projects"
            archives = runtime / "Archives"
            evidence = archives / "worktree-evidence.git"
            sources = runtime / "Sources"
            voice_sources = sources / "Voice Sources"
            worktrees = base / "worktrees"
            for path in (source, projects, evidence, voice_sources, worktrees):
                path.mkdir(parents=True, exist_ok=True)
            workspace_link = runtime / "Workspace"
            workspace_link.symlink_to(source, target_is_directory=True)
            legacy = base / "legacy-evidence.git"
            legacy.symlink_to(evidence, target_is_directory=True)
            manifest = {
                "schema_version": 1,
                "canonical_human_root": str(base / "human"),
                "canonical_source_root": str(source),
                "canonical_runtime_root": str(runtime),
                "canonical_control_root": str(source / ".omo"),
                "canonical_projects_root": str(projects),
                "canonical_archive_root": str(archives),
                "evidence_archive_git": str(evidence),
                "canonical_sources_root": str(sources),
                "voice_sources_root": str(voice_sources),
                "workspace_link": str(workspace_link),
                "worktree_root": str(worktrees),
                "compatibility_links": {str(legacy): str(evidence)},
            }

            paths = workspace.CanonicalPaths.from_manifest(manifest)

            self.assertEqual(paths.human_root, (base / "human").resolve())
            self.assertEqual(paths.source_root, source.resolve())
            self.assertIsNone(workspace._validate_link(paths.workspace_link, paths.source_root))
            self.assertIsNone(workspace._validate_link(legacy, evidence.resolve()))

    def test_plan_fingerprint_changes_when_candidate_changes(self) -> None:
        first = {"schema_version": 1, "candidates": [{"path": "a"}]}
        second = json.loads(json.dumps(first))
        second["candidates"][0]["path"] = "b"
        self.assertNotEqual(workspace._sha256_json(first), workspace._sha256_json(second))


if __name__ == "__main__":
    unittest.main()
