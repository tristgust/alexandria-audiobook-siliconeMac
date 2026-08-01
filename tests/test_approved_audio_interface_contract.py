from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ApprovedAudioInterfaceContractTests(unittest.TestCase):
    def test_produce_row_renders_approved_lock_instead_of_regenerate(self) -> None:
        source = (
            ROOT / "app" / "static" / "pages" / "produce_audio_row.js"
        ).read_text(encoding="utf-8")
        self.assertIn("chunk.regeneration_lock?.locked", source)
        self.assertIn("label: 'Approved audio'", source)
        self.assertIn("approved adaptation audio, regeneration locked", source)

    def test_produce_inspector_explains_chunk_specific_lock(self) -> None:
        source = (
            ROOT / "app" / "static" / "pages" / "produce_inspector.js"
        ).read_text(encoding="utf-8")
        self.assertIn("Approved adaptation performance", source)
        self.assertIn("Locked for this approved chunk", source)
        self.assertIn("'Approved audio'", source)
        self.assertIn("'Regeneration locked'", source)
        self.assertIn("produce-inspector-lock", source)
        self.assertNotIn("Approved audio - regeneration locked", source)


if __name__ == "__main__":
    unittest.main()
