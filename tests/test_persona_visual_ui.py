from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PERSONA_PATH = ROOT / "app" / "static" / "components" / "persona_visual.js"


class PersonaVisualProfileContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = PERSONA_PATH.read_text(encoding="utf-8")

    def test_component_has_exclusive_progressive_states(self) -> None:
        for state in ("disabled", "idle", "running", "error", "completed"):
            self.assertIn(f'"{state}"', self.source)
        self.assertIn("data-persona-state", self.source)

    def test_status_read_does_not_begin_optional_collection(self) -> None:
        status = self.source.index('"/api/character_visuals/status"')
        discover = self.source.index('"/api/character_visuals/discover"')
        self.assertLess(status, discover)
        self.assertIn("enabled: true", self.source)
        self.assertIn("entry_ids:", self.source)
        self.assertIn("Collect appearance details", self.source)

    def test_visual_api_lifecycle_and_cleanup_are_present(self) -> None:
        for endpoint in (
            "/api/character_visuals/status",
            "/api/character_visuals/discover",
            "/api/character_visuals/cancel",
            "/api/character_visuals/",
        ):
            self.assertIn(endpoint, self.source)
        self.assertIn("clearTimeout", self.source)
        self.assertIn("signal.addEventListener", self.source)
        self.assertIn("cleanup", self.source)

    def test_source_text_is_rendered_without_html_injection(self) -> None:
        self.assertIn("textContent", self.source)
        self.assertNotIn("innerHTML", self.source)
        self.assertNotIn("insertAdjacentHTML", self.source)
        for forbidden in ("api_key", "base_url", "system_prompt", "user_prompt"):
            self.assertNotIn(forbidden, self.source)

    def test_persona_is_a_profile_component_not_a_second_workspace(self) -> None:
        self.assertIn("export function createPersonaVisual", self.source)
        self.assertNotIn('role="listbox"', self.source)
        self.assertNotIn("character-visual-list", self.source)
        self.assertNotIn("master-detail", self.source)


if __name__ == "__main__":
    unittest.main()
