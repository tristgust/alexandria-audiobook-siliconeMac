from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from b19_t07_protected_state import (
    LIVE_PROTECTED_CLASSES,
    PROTECTED_CLASSES,
    TASK_BRANCH,
    TASK_WORKTREE,
    ProtectedStateError,
    ProtectedStateManifest,
    compare_live_protected_state,
    compare_protected_state,
    read_protected_state_manifest,
    snapshot_protected_state,
    write_protected_state_manifest,
)


class B19T07ProtectedStateTests(unittest.TestCase):
    def test_identical_manifests_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._populate(root)
            before = snapshot_protected_state(root)
            after = snapshot_protected_state(root)
        self.assertTrue(compare_protected_state(before, after).ok)

    def test_snapshot_contains_every_runtime_purity_class(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = snapshot_protected_state(Path(temporary))
        self.assertEqual(tuple(item.name for item in manifest.classes), PROTECTED_CLASSES)

    def test_incomplete_manifests_do_not_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = snapshot_protected_state(Path(temporary))
        incomplete = ProtectedStateManifest(classes=manifest.classes[:-1])
        self.assertEqual(
            compare_protected_state(incomplete, incomplete).changed_classes,
            ("installed_experimental_model_inventory",),
        )

    def test_serialized_identical_manifests_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._populate(root)
            before_path = root / "before.json"
            after_path = root / "after.json"
            write_protected_state_manifest(before_path, snapshot_protected_state(root))
            write_protected_state_manifest(after_path, snapshot_protected_state(root))
            result = compare_protected_state(
                read_protected_state_manifest(before_path),
                read_protected_state_manifest(after_path),
            )
        self.assertTrue(result.ok)

    def test_mutated_project_inventory_is_reported(self) -> None:
        self._assert_changed_class("projects")

    def test_mutated_audio_inventory_is_reported(self) -> None:
        self._assert_changed_class("audio")

    def test_mutated_cache_inventory_is_reported(self) -> None:
        self._assert_changed_class("cache")

    def test_mutated_voice_inventory_is_reported(self) -> None:
        self._assert_changed_class("Voice")

    def test_redacts_credential_contents_from_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._populate(root)
            secret = "must-not-be-read"
            (root / "projects" / "credential-token.txt").write_text(secret, encoding="utf-8")
            manifest = snapshot_protected_state(root)
        project = manifest.classes[0]
        credential = next(entry for entry in project.entries if entry.path.endswith("credential-token.txt"))
        self.assertEqual(credential.sha256, "<credential-redacted>")

    def test_rejects_a_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as outside_temporary:
            root = Path(temporary)
            self._populate(root)
            outside = Path(outside_temporary) / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            (root / "projects" / "escape.txt").symlink_to(outside)
            with self.assertRaises(ProtectedStateError):
                snapshot_protected_state(root)

    def test_live_comparison_allows_only_the_task_branch_and_worktree_to_advance(self) -> None:
        before = self._live_manifest("base")
        after = self._live_manifest("candidate")
        comparison = compare_live_protected_state(before, after, candidate_sha="candidate")
        self.assertTrue(comparison.ok)

    def test_live_comparison_rejects_runtime_or_other_ref_changes(self) -> None:
        before = self._live_manifest("base")
        after = self._live_manifest("candidate")
        after["classes"]["audio"]["digest"] = "changed"
        after["git"]["refs"]["refs/heads/protected"] = "changed"
        comparison = compare_live_protected_state(before, after, candidate_sha="candidate")
        self.assertEqual(comparison.changed_classes, ("audio",))
        self.assertIn("protected_ref_changed:refs/heads/protected", comparison.git_errors)

    def _assert_changed_class(self, class_name: str) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._populate(root)
            before = snapshot_protected_state(root)
            (root / class_name / "inventory.txt").write_text("mutated", encoding="utf-8")
            after = snapshot_protected_state(root)
        self.assertEqual(compare_protected_state(before, after).changed_classes, (class_name,))

    @staticmethod
    def _populate(root: Path) -> None:
        for class_name in PROTECTED_CLASSES:
            path = root / class_name
            path.mkdir(parents=True, exist_ok=True)
            (path / "inventory.txt").write_text(class_name, encoding="utf-8")

    @staticmethod
    def _live_manifest(task_sha: str) -> dict:
        return {
            "schema_version": 2,
            "classes": {
                name: {"digest": f"digest-{name}"} for name in LIVE_PROTECTED_CLASSES
            },
            "git": {
                "worktrees": [
                    {"worktree": TASK_WORKTREE, "HEAD": task_sha, "branch": TASK_BRANCH},
                    {"worktree": "/protected", "HEAD": "protected", "branch": "refs/heads/protected"},
                ],
                "refs": {
                    TASK_BRANCH: task_sha,
                    "refs/heads/protected": "protected",
                    "refs/tags/v1": "tag",
                },
                "tags": {"refs/tags/v1": "tag"},
                "remotes": ["origin git@example.invalid (fetch)"],
            },
        }


if __name__ == "__main__":
    unittest.main()
