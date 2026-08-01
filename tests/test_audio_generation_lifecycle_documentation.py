from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AudioGenerationLifecycleDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = (
            ROOT / "docs" / "AUDIO_GENERATION_LIFECYCLE.md"
        ).read_text(encoding="utf-8")
        cls.document_text = " ".join(cls.document.split())
        cls.help = (ROOT / "docs" / "help" / "produce.md").read_text(
            encoding="utf-8"
        )
        cls.help_text = " ".join(cls.help.split())
        cls.service = (
            ROOT / "app" / "audio_generation_lifecycle.py"
        ).read_text(encoding="utf-8")
        cls.project = (ROOT / "app" / "project.py").read_text(
            encoding="utf-8"
        )
        cls.tts = (ROOT / "app" / "tts.py").read_text(encoding="utf-8")
        cls.api = (ROOT / "app" / "app.py").read_text(encoding="utf-8")

    def test_document_states_identity_ownership_and_terminal_contract(self) -> None:
        for phrase in (
            "does not introduce a second scheduler",
            "one `request_fingerprint`",
            "Submitting an identical non-terminal request is a duplicate",
            "Only one worker may claim a request",
            "terminal request records a terminal reason",
            "cannot become `succeeded` unless every planned chunk is complete",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.document_text)

    def test_document_states_restart_cancel_and_replacement_contract(self) -> None:
        for phrase in (
            "does not silently run audio",
            "generates only missing or failed segments",
            "stale buffered output",
            "late worker may not install canonical audio",
            "one `queued_replacement` does not run concurrently",
            "client_disconnected_before_acceptance",
            "does not depend on the browser connection remaining open",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.document_text)
        self.assertIn("persistent request ID", self.help_text)
        self.assertIn("Only one pending replacement", self.help_text)

    def test_document_retains_b16_t06_cross_file_crash_boundary(self) -> None:
        self.assertIn("Boundary retained for B16-T06", self.document)
        self.assertIn("half-committed canonical artifacts", self.document)

    def test_code_uses_one_persistent_lifecycle_and_publication_gate(self) -> None:
        for symbol in (
            "def prepare_request(",
            "def claim_request(",
            "def request_context(",
            "def record_segment_completed(",
            "def publish_chunk(",
            "def finalize_request(",
            "def reconcile_interrupted_requests(",
        ):
            self.assertIn(symbol, self.service)
        self.assertIn("build_audio_generation_manifest(", self.project)
        self.assertIn("publish_generation_chunk(", self.project)
        self.assertIn("completed_segment_artifact(", self.tts)
        self.assertIn("segment_output_path(", self.tts)

    def test_api_routes_are_registered_once(self) -> None:
        for route in (
            '@app.get("/api/audio-generation/requests")',
            '@app.get("/api/audio-generation/requests/{request_id}")',
            '@app.post("/api/generate_batch")',
            '@app.post("/api/generate_batch_fast")',
            '@app.post("/api/generate_fast_batch")',
            '@app.post("/api/cancel_generation")',
        ):
            with self.subTest(route=route):
                self.assertEqual(self.api.count(route), 1)
        self.assertIn("client_disconnected_before_acceptance", self.api)
        self.assertIn("reconcile_interrupted_audio_requests(ROOT_DIR)", self.api)


if __name__ == "__main__":
    unittest.main()
