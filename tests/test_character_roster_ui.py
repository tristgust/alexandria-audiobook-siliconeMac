from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "app" / "static" / "index.html"


class CharacterRosterUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = HTML_PATH.read_text(encoding="utf-8")

    def test_roster_controls_exist_once(self) -> None:
        for element_id in (
            "character-roster-card",
            "character-roster-status-badge",
            "btn-refresh-character-roster",
            "btn-discover-character-roster",
            "btn-cancel-character-roster",
            "btn-discard-character-roster-progress",
            "character-roster-summary",
            "character-roster-progress",
            "character-roster-source",
            "character-roster-counts",
            "character-roster-content",
            "character-roster-approval",
            "character-roster-unresolved-ack",
            "btn-approve-character-roster",
        ):
            self.assertEqual(
                self.html.count(f'id="{element_id}"'),
                1,
                element_id,
            )

    def test_all_action_and_status_endpoints_are_used(self) -> None:
        for endpoint in (
            "/api/character_roster/status",
            "/api/character_roster/draft",
            "/api/character_roster/draft/action",
            "/api/character_roster/approve",
            "/api/character_roster/discover",
            "/api/character_roster/cancel",
            "/api/character_roster/discard-progress",
        ):
            self.assertIn(endpoint, self.html)

    def test_upload_refreshes_roster_compatibility(self) -> None:
        upload_start = self.html.index(
            "document.getElementById('file-upload').addEventListener"
        )
        generation_start = self.html.index(
            "document.getElementById('btn-gen-script').addEventListener",
            upload_start,
        )
        upload_block = self.html[upload_start:generation_start]
        self.assertIn(
            "await refreshCharacterRosterStatus();",
            upload_block,
        )

    def test_sensitive_runtime_fields_are_not_rendered(self) -> None:
        roster_block_start = self.html.index(
            "let characterRosterStatusTimer"
        )
        roster_block_end = self.html.index(
            "// --- Script Tab ---",
            roster_block_start,
        )
        block = self.html[roster_block_start:roster_block_end]
        for forbidden in (
            "base_url",
            "api_key",
            "system_prompt",
            "user_prompt",
            "temperature",
            "max_tokens",
            "raw telemetry",
        ):
            self.assertNotIn(forbidden, block)


if __name__ == "__main__":
    unittest.main()
