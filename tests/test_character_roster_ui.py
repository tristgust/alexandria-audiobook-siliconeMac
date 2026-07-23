from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CAST = ROOT / "app" / "static" / "pages" / "cast.js"
PERSONA = ROOT / "app" / "static" / "components" / "persona_visual.js"
STYLE = ROOT / "app" / "static" / "styles" / "pages" / "cast.css"


class StandaloneCastContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cast = CAST.read_text(encoding="utf-8")
        cls.persona = PERSONA.read_text(encoding="utf-8")
        cls.style = STYLE.read_text(encoding="utf-8")

    def test_mounts_as_one_shell_owned_destination(self) -> None:
        self.assertIn("export async function mount", self.cast)
        self.assertIn("data-cast-page", self.cast)
        self.assertIn("data-cast-roster", self.cast)
        self.assertIn("data-cast-profile", self.cast)
        self.assertNotIn("character-workspace", self.cast)
        self.assertNotIn("character-visual-panel", self.cast)
        self.assertNotIn("innerHTML", self.cast)

    def test_profile_section_order_is_explicit(self) -> None:
        markers = [
            'section("voice"',
            'section("reference"',
            'section("preview"',
            'section("character"',
            'section("appearance"',
            'section("advanced"',
        ]
        positions = [self.cast.index(marker) for marker in markers]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("data-cast-identity", self.cast)

    def test_real_cast_and_save_endpoints_are_used(self) -> None:
        for endpoint in (
            "/api/cast",
            "/api/cast/characters/",
            "/api/save_voice_config",
        ):
            self.assertIn(endpoint, self.cast)
        self.assertIn("signal", self.cast)
        self.assertIn("AbortController", self.cast)

    def test_selection_is_not_presented_as_a_status(self) -> None:
        self.assertIn("aria-selected", self.cast)
        self.assertNotIn('label: "Selected"', self.cast)
        for label in (
            "Voice assigned",
            "Missing voice",
            "Identity review",
            "Preview recommended",
            "Non-speaking",
        ):
            self.assertIn(label, self.cast)

    def test_cast_style_is_token_driven_and_responsive(self) -> None:
        self.assertIn("var(--master-wide)", self.style)
        self.assertIn("var(--master-compact)", self.style)
        self.assertIn('[data-layout="narrow"]', self.style)
        self.assertIn(":focus-visible", self.style)
        self.assertIn("prefers-reduced-motion", self.style)
        self.assertNotRegex(self.style, r"#[0-9a-fA-F]{3,8}")
        self.assertNotIn("linear-gradient", self.style)
        self.assertNotIn("backdrop-filter", self.style)

    def test_no_runtime_secret_or_raw_identifier_presentation(self) -> None:
        for forbidden in (
            "api_key",
            "base_url",
            "system_prompt",
            "user_prompt",
            "configuration_key",
        ):
            self.assertNotIn(forbidden, self.cast)
        self.assertIsNone(re.search(r"<code|\\.innerHTML|insertAdjacentHTML", self.cast))


if __name__ == "__main__":
    unittest.main()
