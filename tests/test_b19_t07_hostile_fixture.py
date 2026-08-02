from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from b19_t07_hostile_fixture import FIXTURE_COUNTS, HOSTILE_FRAGMENT, FixtureRootError, build_hostile_fixture


class B19T07HostileFixtureTests(unittest.TestCase):
    def test_builds_the_exact_deterministic_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as second_temporary:
            root = Path(temporary)
            manifest = build_hostile_fixture(root)
            second_manifest = build_hostile_fixture(Path(second_temporary))
            project = self._read(root / "catalog" / "project.json")
            chapters = self._read(root / "catalog" / "chapters.json")
            produce_rows = self._read(root / "catalog" / "produce_rows.json")
            characters = self._read(root / "catalog" / "characters.json")
            issues = self._read(root / "catalog" / "validation_issues.json")
        self.assertEqual(manifest.deterministic_sha256, second_manifest.deterministic_sha256)
        self.assertEqual(manifest.aggregate_chunk_count, 5328)
        self.assertEqual(project["cover"], None)
        self.assertEqual(project["portrait"], None)
        self.assertEqual(project["optional_metadata"], None)
        self.assertEqual(len(project["title"]), 512)
        self.assertEqual(len(project["author"]), 512)
        self.assertEqual(len(chapters[0]["title"]), 512)
        self.assertEqual(len(produce_rows[0]["label"]), 512)
        self.assertEqual(len(characters[0]["name"]), 512)
        self.assertEqual(len(characters[0]["voice"]), 512)
        self.assertEqual(len(issues[0]["message"]), 512)
        self.assertIn(HOSTILE_FRAGMENT, project["title"])
        self.assertEqual(len(project["source_path"]), 1024)
        self.assertIn("u" * 256, project["source_path"])
        self.assertTrue(all(len(label) > len("Save") for label in project["swedish_labels"]))

    def test_writes_only_fixture_audio_and_the_exact_dense_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = build_hostile_fixture(root)
            counts = {
                "chapters": len(self._read(root / "catalog" / "chapters.json")),
                "script_rows": len(self._read(root / "catalog" / "script_rows.json")),
                "visible_produce_rows": len(self._read(root / "catalog" / "produce_rows.json")),
                "aggregate_chunks": len(self._read(root / "catalog" / "chunks.json")),
                "characters": len(self._read(root / "catalog" / "characters.json")),
                "selected_chunk_takes": len(self._read(root / "catalog" / "selected_chunk_takes.json")),
                "validation_issues": len(self._read(root / "catalog" / "validation_issues.json")),
            }
            audio_files = list(root.rglob("*.wav"))
        self.assertEqual(counts, FIXTURE_COUNTS)
        self.assertEqual([path.resolve() for path in audio_files], [manifest.fixture_audio])

    def test_rejects_a_nonempty_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "existing.txt").write_text("owned", encoding="utf-8")
            with self.assertRaises(FixtureRootError):
                build_hostile_fixture(root)

    def test_temporary_fixture_root_is_removed_after_cleanup(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        build_hostile_fixture(root)
        temporary.cleanup()
        self.assertFalse(root.exists())

    @staticmethod
    def _read(path: Path):
        return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
