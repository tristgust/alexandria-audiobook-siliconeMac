from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PronunciationDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = (ROOT / "docs" / "PRONUNCIATION.md").read_text(
            encoding="utf-8"
        )
        cls.help = (ROOT / "docs" / "help" / "produce.md").read_text(
            encoding="utf-8"
        )
        cls.registry = (ROOT / "app" / "pronunciation_registry.py").read_text(
            encoding="utf-8"
        )
        cls.project = (ROOT / "app" / "project.py").read_text(encoding="utf-8")
        cls.artifacts = (ROOT / "app" / "audio_artifacts.py").read_text(
            encoding="utf-8"
        )
        cls.api = (ROOT / "app" / "app.py").read_text(encoding="utf-8")

    def test_document_states_source_integrity_and_exact_occurrence_contract(self) -> None:
        for phrase in (
            "never rewrites the imported source, accepted Script",
            "one exact occurrence",
            "zero-based start and end character offsets",
            "does not perform a global find-and-replace",
            "accepted_script_chunk",
            "Approved entries may not overlap",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.document)

    def test_document_states_review_fallback_limits_and_receipt_contract(self) -> None:
        for phrase in (
            "spoken_form",
            "phonetic_hint",
            "G2P source",
            "fallback strategy",
            "language, character-label, production-Voice, and engine limits",
            "every applied and bypassed decision",
            "chunk-local pronunciation-entry fingerprint",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.document)

    def test_document_states_preview_selective_invalidation_and_undo(self) -> None:
        for phrase in (
            "generate_audio: true",
            "does not modify the registry",
            "does not make chunk 10 stale",
            "invalidates only audio for the chunk indices",
            "POST /api/audio-invalidation/{operation_id}/undo",
            "rebind repair",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.document)
        self.assertIn("Reviewed pronunciation guidance", self.help)

    def test_code_uses_one_registry_and_audio_dependency_contract(self) -> None:
        for symbol in (
            "def normalize_pronunciation_entry(",
            "def resolve_pronunciation_request(",
            "def apply_pronunciation_registry_change(",
            "apply_audio_invalidation_transaction(",
        ):
            self.assertIn(symbol, self.registry)
        self.assertNotIn("annotated_script.json", self.registry)
        self.assertIn("pronunciation_chunk_fields(", self.project)
        self.assertIn('["synthesis_text"]', self.project)
        self.assertIn('payload["pronunciation"]', self.artifacts)

    def test_api_routes_are_explicit_and_registered_once(self) -> None:
        routes = (
            '@app.get("/api/pronunciation-registry")',
            '@app.post("/api/pronunciation-registry/preview")',
            '@app.post("/api/pronunciation-registry/entries")',
            '@app.delete("/api/pronunciation-registry/entries/{pronunciation_id}")',
            '@app.get("/api/pronunciation-registry/previews/{preview_fingerprint}")',
        )
        for route in routes:
            with self.subTest(route=route):
                self.assertEqual(self.api.count(route), 1)


if __name__ == "__main__":
    unittest.main()
