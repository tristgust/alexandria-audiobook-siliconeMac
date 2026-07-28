from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from export_aggregate import (
    ExportAggregateError,
    build_export_plan,
    execute_export_build,
    inspect_export_project,
)


class CoverCapturingProjectManager:
    def __init__(self) -> None:
        self.cover_bytes: bytes | None = None
        self.cover_path: Path | None = None

    def merge_m4b(
        self,
        per_chunk_chapters: bool = False,
        metadata: dict[str, str] | None = None,
        output_path: str | Path | None = None,
    ) -> tuple[bool, str]:
        metadata_value = metadata or {}
        cover_text = metadata_value.get("cover_path", "")
        if cover_text:
            self.cover_path = Path(cover_text)
            self.cover_bytes = self.cover_path.read_bytes()
        target = Path(output_path or "audiobook.m4b")
        target.write_bytes(b"m4b-output")
        return True, str(target)


class ExportMetadataHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cover_bytes = b"\xff\xd8\xff\xe0embedded-source-cover"
        source = self.root / "sources" / "human-nature.epub"
        source.parent.mkdir()
        self._write_epub(source, self.cover_bytes)
        (self.root / "alexandria-project.json").write_text(
            json.dumps(
                {
                    "project_id": "project-human-nature",
                    "name": "Human Nature workspace",
                    "source": {
                        "title": "Human Nature",
                        "author": "Paul Cornell",
                        "type": "epub",
                        "original_relative_path": "sources/human-nature.epub",
                    },
                }
            ),
            encoding="utf-8",
        )
        (self.root / "state.json").write_text(
            json.dumps(
                {
                    "book_title": "State fallback title",
                    "author": "State fallback author",
                }
            ),
            encoding="utf-8",
        )
        self.config = {
            "tts": {
                "pause_between_speakers_ms": 500,
                "pause_same_speaker_ms": 250,
            }
        }
        self.produce = {
            "summary": {"complete": True},
            "chunks": [
                {
                    "chunk_id": "chunk:0",
                    "speaker": "NARRATOR",
                    "text": "Chapter One",
                    "duration_ms": 1000,
                }
            ],
            "fingerprints": {"aggregate": "produce-current"},
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _write_epub(path: Path, cover: bytes) -> None:
        container = (
            '<?xml version="1.0"?>'
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles>'
            '</container>'
        )
        package = (
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf">'
            '<metadata><meta name="cover" content="cover"/></metadata>'
            '<manifest>'
            '<item id="cover" href="cover.jpg" media-type="image/jpeg"/>'
            '</manifest>'
            '</package>'
        )
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("META-INF/container.xml", container)
            archive.writestr("OEBPS/content.opf", package)
            archive.writestr("OEBPS/cover.jpg", cover)

    @staticmethod
    def _audio_validator(
        path: str | Path,
        *,
        format_hint: str | None = None,
    ) -> dict[str, int | str]:
        content = Path(path).read_bytes()
        return {
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "duration_ms": 1000,
        }

    def test_first_export_prefills_manifest_metadata_and_source_cover(self) -> None:
        status = inspect_export_project(
            root_dir=self.root,
            produce=self.produce,
            config=self.config,
        )

        self.assertEqual(
            status["metadata"],
            {
                "title": "Human Nature",
                "author": "Paul Cornell",
                "narrator": "",
                "year": "",
                "description": "",
            },
        )
        self.assertTrue(status["cover"]["exists"])
        self.assertEqual(status["cover"]["kind"], "source_epub")
        self.assertEqual(
            status["cover"]["sha256"],
            hashlib.sha256(self.cover_bytes).hexdigest(),
        )
        self.assertNotIn(
            "export_metadata_missing",
            {item["code"] for item in status["blockers"]},
        )

    def test_receipt_nonempty_values_override_project_defaults(self) -> None:
        (self.root / "export_build.json").write_text(
            json.dumps(
                {
                    "metadata": {
                        "title": "Human Nature: Author's Edition",
                        "author": "  ",
                        "narrator": "Recorded Narrator",
                        "year": "2026",
                    },
                    "formats": ["mp3"],
                    "chapter_mode": "smart",
                }
            ),
            encoding="utf-8",
        )

        status = inspect_export_project(
            root_dir=self.root,
            produce=self.produce,
            config=self.config,
        )

        self.assertEqual(status["metadata"]["title"], "Human Nature: Author's Edition")
        self.assertEqual(status["metadata"]["author"], "Paul Cornell")
        self.assertEqual(status["metadata"]["narrator"], "Recorded Narrator")
        self.assertEqual(status["metadata"]["year"], "2026")

    def test_source_epub_cover_is_materialized_only_for_m4b_build(self) -> None:
        source = self.root / "sources" / "human-nature.epub"
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        status = inspect_export_project(
            root_dir=self.root,
            produce=self.produce,
            config=self.config,
        )
        plan = build_export_plan(
            produce=self.produce,
            metadata=status["metadata"],
            formats=["m4b"],
            chapter_mode="smart",
            config=self.config,
            cover_sha256=status["cover"]["sha256"],
        )
        manager = CoverCapturingProjectManager()

        execute_export_build(
            root_dir=self.root,
            project_manager=manager,
            plan=plan,
            audio_validator=self._audio_validator,
        )

        self.assertEqual(manager.cover_bytes, self.cover_bytes)
        self.assertIsNotNone(manager.cover_path)
        self.assertFalse(manager.cover_path.exists())
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
        self.assertFalse((self.root / "m4b_cover.jpg").exists())

    def test_uploaded_cover_precedes_source_epub_cover(self) -> None:
        uploaded = b"\xff\xd8\xff\xe0user-selected-cover"
        (self.root / "m4b_cover.jpg").write_bytes(uploaded)

        status = inspect_export_project(
            root_dir=self.root,
            produce=self.produce,
            config=self.config,
        )

        self.assertEqual(status["cover"]["kind"], "uploaded")
        self.assertTrue(status["cover"]["user_provided"])
        self.assertEqual(
            status["cover"]["sha256"],
            hashlib.sha256(uploaded).hexdigest(),
        )

    def test_cover_change_after_plan_fails_before_build_or_history(self) -> None:
        status = inspect_export_project(
            root_dir=self.root,
            produce=self.produce,
            config=self.config,
        )
        plan = build_export_plan(
            produce=self.produce,
            metadata=status["metadata"],
            formats=["m4b"],
            chapter_mode="smart",
            config=self.config,
            cover_sha256=status["cover"]["sha256"],
        )
        (self.root / "m4b_cover.jpg").write_bytes(
            b"\xff\xd8\xff\xe0changed-after-review"
        )
        manager = CoverCapturingProjectManager()

        with self.assertRaises(ExportAggregateError) as raised:
            execute_export_build(
                root_dir=self.root,
                project_manager=manager,
                plan=plan,
                audio_validator=self._audio_validator,
            )

        self.assertEqual(raised.exception.code, "export_dependencies_changed")
        self.assertIsNone(manager.cover_path)
        self.assertFalse((self.root / "export_build_history").exists())


if __name__ == "__main__":
    unittest.main()
