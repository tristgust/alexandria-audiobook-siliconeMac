from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "app" / "static" / "index.html"
HARNESS_PATH = ROOT / "tests" / "phase18c_ui_harness.js"
REPORT_PREFIX = "PHASE18C_UI_REPORT="


class CharacterRosterUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = HTML_PATH.read_text(encoding="utf-8")
        result = subprocess.run(
            ["node", str(HARNESS_PATH), str(ROOT)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise AssertionError(
                "Phase 18C UI harness failed.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )
        report_line = next(
            (
                line
                for line in result.stdout.splitlines()
                if line.startswith(REPORT_PREFIX)
            ),
            None,
        )
        if report_line is None:
            raise AssertionError(
                "Phase 18C UI harness produced no report.\n"
                f"STDOUT:\n{result.stdout}"
            )
        cls.report = json.loads(report_line[len(REPORT_PREFIX):])

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

    def test_safe_evidence_rendering_executes(self) -> None:
        self.assertTrue(
            self.report["checks"]["evidence_is_html_escaped"]["ok"]
        )

    def test_draft_and_approved_states_execute(self) -> None:
        self.assertTrue(
            self.report["checks"][
                "draft_renders_actions_duplicates_and_acknowledgment"
            ]["ok"]
        )
        self.assertTrue(
            self.report["checks"]["approved_roster_is_read_only"]["ok"]
        )

    def test_running_and_polling_guards_execute(self) -> None:
        self.assertTrue(
            self.report["checks"]["running_state_guards_controls"]["ok"]
        )
        self.assertTrue(
            self.report["checks"]["polling_starts_and_stops"]["ok"]
        )

    def test_real_action_payload_and_stale_refresh_execute(self) -> None:
        self.assertTrue(
            self.report["checks"][
                "action_uses_current_fingerprint_and_refreshes"
            ]["ok"]
        )
        self.assertTrue(
            self.report["checks"][
                "stale_action_refreshes_instead_of_overwriting"
            ]["ok"]
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
