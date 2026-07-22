from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "app" / "static" / "index.html"
HARNESS_PATH = ROOT / "tests" / "phase18d_visual_ui_harness.js"
REPORT_PREFIX = "PHASE18D_VISUAL_UI_REPORT="


class PersonaVisualUIContractTests(unittest.TestCase):
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
                "Phase 18D visual UI harness failed.\n"
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
                "Phase 18D visual UI harness produced no report.\n"
                f"STDOUT:\n{result.stdout}"
            )
        cls.report = json.loads(
            report_line[len(REPORT_PREFIX):]
        )

    def assert_check(self, name: str) -> None:
        self.assertTrue(
            self.report["checks"][name]["ok"],
            self.report["checks"][name],
        )

    def test_visual_workspace_controls_exist_once(self) -> None:
        for element_id in (
            "character-visual-panel",
            "character-visual-status-badge",
            "character-visual-error",
            "character-visual-enabled",
            "character-visual-summary",
            "character-visual-progress",
            "character-visual-search",
            "character-visual-selection-count",
            "btn-select-all-character-visuals",
            "btn-clear-character-visuals",
            "btn-discover-character-visuals",
            "btn-cancel-character-visuals",
            "btn-discard-character-visual-progress",
            "character-visual-list",
            "character-visual-empty",
            "character-visual-detail",
        ):
            self.assertEqual(
                self.html.count(f'id="{element_id}"'),
                1,
                element_id,
            )
        self.assert_check(
            "feature_checkbox_is_unchecked_in_markup"
        )

    def test_visual_workspace_follows_the_unified_character_list(self) -> None:
        workspace_start = self.html.index(
            'id="character-workspace"'
        )
        visual_start = self.html.index(
            '<section id="character-visual-panel"',
            workspace_start,
        )
        characters_tab_start = self.html.index(
            'id="characters-tab"'
        )
        designer_tab_start = self.html.index(
            'id="designer-tab"',
            characters_tab_start,
        )
        self.assertLess(characters_tab_start, workspace_start)
        self.assertLess(workspace_start, visual_start)
        self.assertLess(visual_start, designer_tab_start)
        self.assertIn(
            'class="workspace-section visual-workspace"',
            self.html[visual_start:visual_start + 220],
        )
        character_block = self.html[workspace_start:visual_start]
        self.assertEqual(character_block.count('id="voice-projects-list"'), 1)

    def test_visual_endpoints_are_used(self) -> None:
        for endpoint in (
            "/api/character_visuals/status",
            "/api/character_visuals/discover",
            "/api/character_visuals/cancel",
            "/api/character_visuals/discard-progress",
            "/api/character_visuals/",
        ):
            self.assertIn(endpoint, self.html)

    def test_upload_and_roster_approval_refresh_visual_status(self) -> None:
        upload_start = self.html.index(
            "document.getElementById('file-upload').addEventListener"
        )
        generation_start = self.html.index(
            "document.getElementById('btn-gen-script').addEventListener",
            upload_start,
        )
        upload_block = self.html[upload_start:generation_start]
        self.assertIn(
            "await refreshCharacterVisualStatus();",
            upload_block,
        )
        approval_start = self.html.index(
            "document.getElementById('btn-approve-character-roster')"
        )
        visual_start = self.html.index(
            "let characterVisualStatusTimer",
            approval_start,
        )
        approval_block = self.html[approval_start:visual_start]
        self.assertIn(
            "await refreshCharactersWorkspace({ selectedId: voiceTrainingSelectedId });",
            approval_block,
        )

    def test_approved_only_disabled_running_and_search_states_execute(self) -> None:
        self.assert_check("panel_is_approved_roster_only")
        self.assert_check(
            "idle_visual_collection_is_disabled_and_safe"
        )
        self.assert_check(
            "explicit_enable_and_selection_unlock_action"
        )
        self.assert_check(
            "running_state_guards_actions_and_shows_progress"
        )
        self.assert_check(
            "master_list_search_filters_without_destroying_rows"
        )

    def test_information_design_avoids_id_and_counter_soup(self) -> None:
        self.assert_check(
            "master_list_avoids_raw_id_and_counter_presentation"
        )
        start = self.html.index(
            "function characterVisualEntryHtml"
        )
        end = self.html.index(
            "function renderCharacterVisualStatus",
            start,
        )
        entry_renderer = self.html[start:end]
        self.assertNotIn("<code", entry_renderer)
        self.assertNotIn("Observations:", entry_renderer)
        self.assertNotIn("Unknowns:", entry_renderer)
        self.assertNotIn("badge", entry_renderer)

    def test_source_and_derived_visual_text_is_escaped(self) -> None:
        self.assert_check(
            "complete_status_escapes_derived_summary"
        )
        self.assert_check(
            "dossier_detail_escapes_all_source_and_derived_text"
        )
        self.assert_check(
            "status_errors_use_safe_text_and_clear_timer"
        )

    def test_dossier_uses_progressive_disclosure(self) -> None:
        self.assert_check("dossier_uses_progressive_disclosure")
        self.assertIn(
            'data-visual-panel-target="profile"',
            self.html,
        )
        self.assertIn(
            '<summary>Technical details</summary>',
            self.html,
        )

    def test_status_refresh_never_starts_collection(self) -> None:
        self.assert_check(
            "polling_starts_and_stops_without_posting"
        )

    def test_visual_block_does_not_render_runtime_secrets(self) -> None:
        start = self.html.index("let characterVisualStatusTimer")
        end = self.html.index("// --- Script Tab ---", start)
        block = self.html[start:end]
        for forbidden in (
            "api_key",
            "base_url",
            "system_prompt",
            "user_prompt",
            "raw telemetry",
        ):
            self.assertNotIn(forbidden, block)

    def test_workspace_has_narrow_and_reduced_motion_rules(self) -> None:
        self.assertIn("@media (max-width: 900px)", self.html)
        self.assertIn(
            "@media (prefers-reduced-motion: reduce)",
            self.html,
        )
        self.assertIn(":focus-visible", self.html)


if __name__ == "__main__":
    unittest.main()
