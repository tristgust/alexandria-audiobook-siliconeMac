from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AudioTakeDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = (ROOT / "docs" / "AUDIO_TAKES.md").read_text(
            encoding="utf-8"
        )
        cls.artifacts = (ROOT / "docs" / "AUDIO_ARTIFACTS.md").read_text(
            encoding="utf-8"
        )
        cls.help = (ROOT / "docs" / "help" / "produce.md").read_text(
            encoding="utf-8"
        )
        cls.service = (ROOT / "app" / "audio_takes.py").read_text(
            encoding="utf-8"
        )
        cls.api = (ROOT / "app" / "app.py").read_text(encoding="utf-8")
        cls.project = (ROOT / "app" / "project.py").read_text(encoding="utf-8")
        cls.produce = (ROOT / "app" / "produce_aggregate.py").read_text(
            encoding="utf-8"
        )
        cls.actions = (
            ROOT / "app" / "static" / "pages" / "produce_actions.js"
        ).read_text(encoding="utf-8")
        cls.inspector = (
            ROOT / "app" / "static" / "pages" / "produce_inspector.js"
        ).read_text(encoding="utf-8")
        cls.library = (ROOT / "app" / "library_inventory.py").read_text(
            encoding="utf-8"
        )
        cls.document_text = " ".join(cls.document.split())

    def test_document_states_immutable_lineage_and_metadata_contract(self) -> None:
        for phrase in (
            "Regeneration creates a new raw **Take**",
            "never overwrites or deletes the previous valid Take",
            "creates a child rendition linked to its source Take",
            "complete segment/source-span map and seam receipt",
            "original sample count, sample rate, channels",
            "Merely opening Produce does not rewrite project data",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.document_text)

    def test_document_states_selection_cleanup_and_undo_contract(self) -> None:
        for phrase in (
            "**Use this take**",
            "**Keep** or **Unkeep**",
            "An incompatible prior Take remains playable and retained",
            "current Take",
            "source ancestors of current or kept renditions",
            "active generation jobs and request receipts",
            "exact bytes and records",
            "B16-T06 owns startup reconciliation",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.document_text)
        self.assertIn("## Review Takes", self.help)
        self.assertIn("Clean up old takes", self.help)

    def test_code_uses_one_registry_for_generation_routes_and_invalidation(self) -> None:
        for symbol in (
            "def build_take_record(",
            "def register_take(",
            "def promote_take(",
            "def register_rendition(",
            "def delete_impact(",
            "def cleanup_impact(",
            "def undo_operation(",
            "def prepare_invalidation_registry(",
        ):
            self.assertIn(symbol, self.service)
        self.assertIn("register_take(", self.project)
        self.assertIn("register_audio_take_rendition(", self.project)
        self.assertIn('row["takes"]', self.produce)

    def test_api_and_interface_expose_reviewed_take_operations_once(self) -> None:
        routes = (
            '@app.get("/api/produce/chunks/{chunk_id}/takes")',
            '@app.post("/api/produce/chunks/{chunk_id}/takes/use")',
            '@app.post("/api/produce/chunks/{chunk_id}/takes/keep")',
            '@app.get("/api/produce/chunks/{chunk_id}/takes/{take_id}/delete-impact")',
            '@app.delete("/api/produce/chunks/{chunk_id}/takes/{take_id}")',
            '@app.post("/api/produce/takes/cleanup-impact")',
            '@app.post("/api/produce/takes/cleanup")',
            '@app.post("/api/produce/takes/undo")',
        )
        for route in routes:
            with self.subTest(route=route):
                self.assertEqual(self.api.count(route), 1)
        for phrase in ("Clean up old takes", "Undo deletion", "Undo cleanup"):
            self.assertIn(phrase, self.actions)
        self.assertIn("Use this take", self.inspector)
        for phrase in (
            "data-produce-take-play",
            "data-produce-take-use",
            "data-produce-take-keep",
            "data-produce-take-delete",
        ):
            self.assertIn(phrase, self.inspector)

    def test_library_and_help_manifest_own_take_evidence(self) -> None:
        self.assertIn('"audio_takes.json"', self.library)
        self.assertIn('"audio_take_history"', self.library)
        manifest = json.loads(
            (ROOT / "docs" / "help" / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        produce = next(item for item in manifest["topics"] if item["slug"] == "produce")
        digest = hashlib.sha256(self.help.encode("utf-8")).hexdigest()
        self.assertEqual(produce["content_sha256"], digest)
        self.assertIn("immutable Take", self.artifacts)


if __name__ == "__main__":
    unittest.main()
