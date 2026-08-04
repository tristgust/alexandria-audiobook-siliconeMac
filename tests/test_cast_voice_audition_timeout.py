from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "app" / "static" / "pages" / "cast_voice_audition.js"


class CastVoiceAuditionTimeoutTests(unittest.TestCase):
    def test_range_auditions_use_a_generation_appropriate_timeout(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("const AUDITION_TIMEOUT_MS = 300000;", source)
        self.assertEqual(source.count("timeout: AUDITION_TIMEOUT_MS"), 3)
        self.assertIn("/api/voice_design/range-preview", source)
        self.assertIn("/api/voice-library/built-in-range-preview", source)

    def test_designed_audition_supports_single_lane_regeneration(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("/api/voice_design/range-preview/regenerate", source)
        self.assertIn("Regenerate ${label.toLowerCase()}", source)
        self.assertIn("Regenerate full audition", source)
        self.assertIn("force_regenerate: regenerateFull", source)
        self.assertIn("replaying the complete baseline, happy, sad, and angry audition", source)
        self.assertIn("other three lanes unchanged", source)


if __name__ == "__main__":
    unittest.main()
