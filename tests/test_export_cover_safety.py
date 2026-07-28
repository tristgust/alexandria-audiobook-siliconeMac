from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from export_aggregate import build_export_plan, execute_export_build, inspect_export_project
from export_publication import resolve_export_cover


class MutatingCoverManager:
    def __init__(self, original: Path, replacement: bytes) -> None:
        self.original = original
        self.replacement = replacement
        self.embedded: bytes | None = None
        self.materialized: Path | None = None

    def merge_m4b(self, *, metadata, output_path, per_chunk_chapters=False):
        self.original.write_bytes(self.replacement)
        self.materialized = Path(metadata["cover_path"])
        self.embedded = self.materialized.read_bytes()
        Path(output_path).write_bytes(b"m4b")
        return True, str(output_path)


class ExportCoverSafetyTests(unittest.TestCase):
    @staticmethod
    def _produce() -> dict:
        return {
            "summary": {"complete": True},
            "chunks": [
                {
                    "chunk_id": "chunk:0",
                    "speaker": "NARRATOR",
                    "text": "Chapter One",
                    "duration_ms": 1000,
                }
            ],
            "fingerprints": {"aggregate": "current"},
        }

    @staticmethod
    def _validator(path, *, format_hint=None):
        data = Path(path).read_bytes()
        return {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
            "duration_ms": 1000,
        }

    @staticmethod
    def _write_epub(path: Path) -> None:
        container = (
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="content.opf"/></rootfiles></container>'
        )
        package = (
            '<package xmlns="http://www.idpf.org/2007/opf"><metadata>'
            '<meta name="cover" content="cover"/></metadata><manifest>'
            '<item id="cover" href="cover.jpg" media-type="image/jpeg"/>'
            '</manifest></package>'
        )
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("META-INF/container.xml", container)
            archive.writestr("content.opf", package)
            archive.writestr("cover.jpg", b"\xff\xd8\xff\xe0source")

    def test_build_embeds_reviewed_snapshot_when_cover_changes_during_merge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = b"\xff\xd8\xff\xe0reviewed-cover"
            replacement = b"\xff\xd8\xff\xe0racing-cover"
            cover_path = root / "m4b_cover.jpg"
            cover_path.write_bytes(original)
            (root / "alexandria-project.json").write_text(
                json.dumps({"source": {"title": "Human Nature", "author": "Paul Cornell"}}),
                encoding="utf-8",
            )
            produce = self._produce()
            status = inspect_export_project(root_dir=root, produce=produce)
            plan = build_export_plan(
                produce=produce,
                metadata=status["metadata"],
                formats=["m4b"],
                chapter_mode="smart",
                cover_sha256=status["cover"]["sha256"],
            )
            manager = MutatingCoverManager(cover_path, replacement)

            result = execute_export_build(
                root_dir=root,
                project_manager=manager,
                plan=plan,
                audio_validator=self._validator,
            )

            self.assertEqual(manager.embedded, original)
            self.assertEqual(cover_path.read_bytes(), replacement)
            self.assertEqual(result["receipt"]["cover_sha256"], hashlib.sha256(original).hexdigest())
            self.assertIsNotNone(manager.materialized)
            self.assertFalse(manager.materialized.exists())

    def test_manifest_source_symlink_is_rejected_before_epub_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "real.epub"
            self._write_epub(target)
            (root / "source-link.epub").symlink_to(target)
            (root / "alexandria-project.json").write_text(
                json.dumps({"source": {"original_relative_path": "source-link.epub"}}),
                encoding="utf-8",
            )

            self.assertIsNone(resolve_export_cover(root))


if __name__ == "__main__":
    unittest.main()
