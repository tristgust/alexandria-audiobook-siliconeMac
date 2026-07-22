from __future__ import annotations

import re
import unittest
from pathlib import Path


class RecoveryInterfaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "static"
            / "index.html"
        ).read_text(encoding="utf-8")

    def test_legacy_recovery_disclosure_stays_hidden_behind_canonical_maintenance(self) -> None:
        self.assertIn(
            '<details class="recovery-center" id="recovery-center" hidden>',
            self.source,
        )
        self.assertNotIn(
            '<details class="recovery-center" id="recovery-center" open',
            self.source,
        )
        self.assertIn('id="canonical-maintenance-workspace" hidden', self.source)
        self.assertIn('id="maintenance-health-list"', self.source)
        self.assertIn('id="maintenance-history-list"', self.source)
        self.assertIn('class="recovery-overall-light"', self.source)
        self.assertIn('id="recovery-overall-text"', self.source)
        self.assertIn('id="recovery-overall-count"', self.source)
        self.assertNotIn(
            '<section class="recovery-center"',
            self.source,
        )

    def test_recovery_summary_is_subtle_and_hides_details_until_open(self) -> None:
        for phrase in (
            '.recovery-center > summary {',
            'min-height: 48px;',
            '.recovery-center-body {',
            '.recovery-center[open] .recovery-center-chevron',
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.source)

    def test_success_status_lights_use_success_green_not_ink_blue(self) -> None:
        success_blocks = (
            r'\.stage-page-state\[data-state="complete"\]::before,[\s\S]*?background: var\(--alexandria-success\);',
            r'\.diagnostic-status\[data-state="loaded"\]::before,[\s\S]*?background: var\(--alexandria-success\);',
            r'\.workspace-state\[data-state="complete"\] \.workspace-state-dot \{[\s\S]*?background: var\(--alexandria-success\);',
            r'\.visual-character-state\[data-state="complete"\]::before \{[\s\S]*?background: var\(--alexandria-success\);',
            r'\.chunk-status\[data-state="done"\]::before \{[\s\S]*?background: var\(--alexandria-success\);',
        )
        for pattern in success_blocks:
            with self.subTest(pattern=pattern):
                self.assertRegex(self.source, re.compile(pattern))

    def test_green_success_lights_have_subtle_glow(self) -> None:
        self.assertGreaterEqual(
            self.source.count(
                'box-shadow: 0 0 0 2px rgba(70, 122, 82, 0.14)'
            ),
            4,
        )

    def test_recovery_overall_summary_uses_real_stage_priority(self) -> None:
        for phrase in (
            'function recoveryOverallPresentation(status)',
            "['blocked', 'invalid'].includes(stage.state)",
            "'resumable',",
            "'finalization_only',",
            "'restart_required'",
            "state: 'complete'",
            "state: 'warning'",
            "state: 'error'",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.source)

    def test_saved_source_targets_actual_file_picker(self) -> None:
        self.assertIn(
            "document.getElementById('file-upload')",
            self.source,
        )
        self.assertIn(
            "document.querySelector('[data-file-name]')",
            self.source,
        )
        self.assertNotIn(
            "document.getElementById('book-file')",
            self.source,
        )

    def test_recovery_startup_is_deferred_until_api_initializes(self) -> None:
        self.assertRegex(
            self.source,
            re.compile(
                r"setTimeout\(\(\) => \{\s*"
                r"loadLLMProfiles\(\{ selectedStage: llmProfilesSelectedStage \}\);"
            ),
        )
        self.assertIn(
            "refreshRecoveryStatus({ silent: true }).catch(() => {});",
            self.source,
        )

    def test_roster_logs_live_with_character_roster_not_setup_recovery(self) -> None:
        for phrase in (
            'id="character-roster-log-disclosure"',
            'id="character-roster-logs"',
            "function renderCharacterRosterLog(process)",
            "characterRosterLogFollowTail",
            "remaining <= 24",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.source)
        render_start = self.source.index("function renderRecoveryStage(stage)")
        render_end = self.source.index("function renderRecoveryStatus(status)")
        recovery_render = self.source[render_start:render_end]
        self.assertNotIn("process.lines", recovery_render)
        self.assertNotIn("recovery-stage-logs", recovery_render)

    def test_recovery_only_renders_work_that_needs_attention(self) -> None:
        for state in (
            "'running'",
            "'resumable'",
            "'finalization_only'",
            "'restart_required'",
            "'blocked'",
            "'invalid'",
        ):
            with self.subTest(state=state):
                self.assertIn(state, self.source)
        self.assertIn(
            "const stages = allStages.filter(stage => visibleStates.has(stage.state));",
            self.source,
        )
        self.assertIn(
            "Nothing is running, blocked, or waiting to resume.",
            self.source,
        )

    def test_recovery_polling_stops_outside_setup(self) -> None:
        for phrase in (
            "if (activeTab !== 'setup') {",
            "stopRecoveryPolling();",
            "stopModelCachePolling();",
            "function stopRecoveryPolling()",
            "window.clearInterval(recoveryPollTimer);",
            "window.__recoveryPollingActive = false;",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.source)

    def test_llm_profile_loads_are_last_request_wins(self) -> None:
        for phrase in (
            'var llmProfilesLoadRequest = 0;',
            'const loadRequest = ++llmProfilesLoadRequest;',
            'loadRequest !== llmProfilesLoadRequest',
            'selectLLMProfileStage(selected, { loadRequest })',
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.source)


if __name__ == "__main__":
    unittest.main()
