from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SynthesisWindowDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = (ROOT / "docs" / "SYNTHESIS_WINDOWS.md").read_text(
            encoding="utf-8"
        )
        cls.help = (ROOT / "docs" / "help" / "produce.md").read_text(
            encoding="utf-8"
        )
        cls.service = (ROOT / "app" / "synthesis_windows.py").read_text(
            encoding="utf-8"
        )
        cls.tts = (ROOT / "app" / "tts.py").read_text(encoding="utf-8")
        cls.mlx = (ROOT / "app" / "mlx_backend.py").read_text(encoding="utf-8")
        cls.project = (ROOT / "app" / "project.py").read_text(encoding="utf-8")
        cls.artifacts = (ROOT / "app" / "audio_artifacts.py").read_text(
            encoding="utf-8"
        )
        cls.capabilities = (
            ROOT / "app" / "voice_backend_capabilities.py"
        ).read_text(encoding="utf-8")

    def test_document_states_exact_source_span_and_expected_set_contract(self) -> None:
        for phrase in (
            "exact zero-based `source_start` and `source_end`",
            "reconstruct the complete synthesis request exactly",
            "Every planned segment must return exactly once",
            "never joins only the surviving outputs",
            "never drops punctuation, paragraph breaks, double spaces",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.document)

    def test_document_states_explicit_seam_and_exact_length_contract(self) -> None:
        for phrase in (
            "`silence_gap`",
            "`crossfade`",
            "`discard_overlap`",
            "exact expected sample count",
            "exact declared frame count",
            "atomically replaces the request output",
            "internal_segmentation_changed_plan_text",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.document)

    def test_document_states_binding_batch_and_invalidation_contract(self) -> None:
        for phrase in (
            "declaration change is an audio dependency",
            "receipt participates in the normal audio binding fingerprint",
            "Batch generation uses the same contract",
            "clears its synthesis-window and seam",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.document)
        self.assertIn("survivor-only result", self.help)

    def test_code_uses_one_planner_assembler_and_binding_contract(self) -> None:
        for symbol in (
            "def synthesis_window(",
            "def plan_synthesis_segments(",
            "def assemble_synthesis_segments(",
            "def synthesis_receipt_chunk_fields(",
            "def synthesis_binding_fields(",
        ):
            self.assertIn(symbol, self.service)
        self.assertIn("plan_synthesis_segments(", self.tts)
        self.assertIn("assemble_synthesis_segments(", self.tts)
        self.assertIn("generate_adaptive_custom_speech_with_receipt", self.mlx)
        self.assertIn("synthesis_receipt_reset_fields()", self.project)
        self.assertIn('payload["synthesis_windows"]', self.artifacts)
        self.assertIn('"synthesis_windows"', self.capabilities)


if __name__ == "__main__":
    unittest.main()
