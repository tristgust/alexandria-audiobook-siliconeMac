from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "app" / "static" / "pages" / "cast_voice_audition.js"


class CastVoiceAuditionTimeoutTests(unittest.TestCase):
    def test_range_auditions_use_a_generation_appropriate_timeout(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("const AUDITION_TIMEOUT_MS = 300000;", source)
        self.assertEqual(source.count("timeout: AUDITION_TIMEOUT_MS"), 4)
        self.assertIn("/api/voice_design/range-preview", source)
        self.assertIn("/api/voice-library/built-in-range-preview", source)
        self.assertIn("/api/voice-library/supplied-range-preview", source)

    def test_designed_audition_supports_single_lane_regeneration(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("/api/voice_design/range-preview/regenerate", source)
        self.assertIn("Regenerate ${label.toLowerCase()}", source)
        self.assertIn("Regenerate full audition", source)
        self.assertIn("force_regenerate: regenerateFull", source)
        self.assertIn("replaying the complete baseline, happy, sad, and angry audition", source)
        self.assertIn("other three lanes unchanged", source)

    def test_supplied_voice_auditions_stay_inline(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("Generate supplied Voice audition", source)
        self.assertIn("No Designed Voice identity will be created", source)
        self.assertNotIn("onOpenWorkflow('voice-designer', previewChoice)", source)

    def test_audition_studio_has_refresh_animation_and_single_action_save(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        styles = (ROOT / "app" / "static" / "styles" / "pages" / "cast.css").read_text(
            encoding="utf-8"
        )
        save_source = (ROOT / "app" / "static" / "pages" / "cast_voice_save.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("UI.iconButton({", source)
        self.assertIn("name: 'refresh'", source)
        self.assertIn("cast-profile__audition-wave", source)
        self.assertIn("Save audition as Production Voice", source)
        self.assertIn("onSaveAudition?.()", source)
        self.assertIn("save_audition_bundle: true", save_source)
        self.assertIn("preview_fingerprint: designedPreviewFingerprint", save_source)
        self.assertIn("@keyframes cast-audition-wave", styles)


if __name__ == "__main__":
    unittest.main()
