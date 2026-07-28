from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "app" / "static" / "index.html"
SHELL_JS_PATH = ROOT / "app" / "static" / "canonical_interface.js"


class CanonicalShellInterfaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = HTML_PATH.read_text(encoding="utf-8")
        cls.shell_js = SHELL_JS_PATH.read_text(encoding="utf-8")

    def test_approved_soft_editorial_tokens_are_the_final_shell_contract(self) -> None:
        required = (
            "--alexandria-canvas: #f6f3ec",
            "--alexandria-surface: #faf8f2",
            "--alexandria-surface-muted: #ece7df",
            "--alexandria-ink: #23211e",
            "--alexandria-accent: #3f6e6a",
            "--alexandria-terracotta: #c4553d",
            "--alexandria-rail-width: 224px",
            'font-family: "Source Serif 4"',
            'font-family: "IBM Plex Sans"',
        )
        for snippet in required:
            self.assertIn(snippet, self.html)

    def test_shell_geometry_matches_wide_and_compact_references(self) -> None:
        required = (
            ".canonical-global-header",
            "height: 88px",
            ".canonical-project-header",
            "height: 104px",
            "--alexandria-player-height: 80px",
            "@media (max-width: 1199px) and (min-width: 761px)",
            "--alexandria-rail-width: 184px",
            "padding-right: 24px",
            "padding-left: 24px",
        )
        for snippet in required:
            self.assertIn(snippet, self.html)

    def test_global_and_project_navigation_are_distinct(self) -> None:
        self.assertIn('class="alexandria-rail"', self.html)
        self.assertIn('id="home-navigation" aria-label="Project"', self.html)
        self.assertIn('<span>Voices</span>', self.html)
        self.assertIn('<span>Templates</span>', self.html)
        self.assertIn('data-route="projects"', self.html)
        self.assertIn('<span>Home</span>', self.html)
        self.assertIn('data-route="library"', self.html)
        self.assertIn('id="project-stage-navigation"', self.html)
        self.assertIn('aria-label="Project stages" hidden', self.html)
        self.assertIn("homeNavigation.hidden = projectMode", self.shell_js)
        self.assertIn("document.body.classList.toggle(\n            'home-has-project-stages'", self.shell_js)
        for route, label, icon in (
            ("script", "Script", "fa-file-lines"),
            ("cast", "Cast", "fa-user-group"),
            ("produce", "Produce", "fa-wave-square"),
            ("export", "Export", "fa-arrow-up-from-bracket"),
        ):
            self.assertIn(f'data-route="{route}"', self.html)
            self.assertIn(f'<span>{label}</span>', self.html)
            self.assertIn(icon, self.html)

    def test_shared_shell_uses_project_context_and_page_title_layers(self) -> None:
        for identifier in (
            "canonical-global-header",
            "shell-global-title",
            "canonical-project-header",
            "shell-project-title",
            "canonical-page-title-region",
            "shell-page-title",
            "shell-page-subtitle",
            "shell-primary-action",
            "shell-stage-tracker",
        ):
            self.assertEqual(self.html.count(f'id="{identifier}"'), 1)
        self.assertIn("renderStageTracker(route, state.flow)", self.shell_js)
        self.assertIn("mountPrimaryAction(route)", self.shell_js)
        self.assertIn("document.body.dataset.shellMode", self.shell_js)

    def test_projects_settings_and_maintenance_share_one_shell_without_leaking_surfaces(self) -> None:
        self.assertIn('class="alexandria-preboot"', self.html)
        self.assertIn("html.alexandria-preboot body", self.html)
        self.assertIn("document.documentElement.classList.remove('alexandria-preboot')", self.shell_js)
        self.assertIn('id="project-home-workspace"', self.html)
        self.assertIn('class="workflow-surface setup-surface" hidden', self.html)
        self.assertIn('id="canonical-settings-workspace" hidden', self.html)
        self.assertIn('id="canonical-maintenance-workspace" hidden', self.html)
        self.assertIn('id="legacy-settings-workspace" hidden inert aria-hidden="true"', self.html)
        self.assertIn("projectHome.hidden = destination !== 'projects'", self.shell_js)
        self.assertIn("setupSurface.hidden = !settingsDestination && !maintenance", self.shell_js)
        self.assertIn("canonicalSettings.hidden = !settingsDestination", self.shell_js)
        self.assertIn("canonicalMaintenance.hidden = !maintenance", self.shell_js)
        self.assertIn("legacySettings.hidden = true", self.shell_js)
        self.assertIn("legacySettings.setAttribute('inert', '')", self.shell_js)
        self.assertNotIn("legacySettings.hidden = !legacyMaintenance", self.shell_js)
        self.assertIn("recovery.hidden = true", self.shell_js)
        self.assertEqual(self.html.count('data-tab-panel="setup"'), 1)

    def test_persistent_player_owns_the_only_promoted_transport(self) -> None:
        for identifier in (
            "persistent-player-host",
            "persistent-player-play",
            "persistent-player-timeline",
            "persistent-player-volume",
            "persistent-player-speed",
        ):
            self.assertEqual(self.html.count(f'id="{identifier}"'), 1)
        self.assertEqual(self.html.count('id="main-audio"'), 1)
        self.assertIn("slot.appendChild(audio)", self.shell_js)
        self.assertIn("audio.removeAttribute('controls')", self.shell_js)

    def test_route_activation_notifies_the_shared_shell(self) -> None:
        self.assertIn("new CustomEvent('alexandria:routechange'", self.html)
        self.assertIn("window.addEventListener('alexandria:routechange'", self.shell_js)
        self.assertIn("routeApi.normalizeRoute", self.shell_js)

    def test_cast_voice_editor_stays_inside_the_approved_cast_surface(self) -> None:
        for identifier in (
            "cast-voice-editor",
            "cast-voice-editor-slot",
            "cast-cancel-voice",
            "cast-save-voice",
        ):
            self.assertEqual(self.html.count(f'id="{identifier}"'), 1)
        self.assertNotIn("cast-legacy-mode", self.html)
        self.assertNotIn("cast-legacy-mode", self.shell_js)
        self.assertIn("beginCastVoiceEdit", self.shell_js)
        self.assertIn("window.AlexandriaVoiceCardBridge?.mountCast?.(voiceName)", self.shell_js)
        self.assertIn("save.hidden = !cast.editing || !cast.dirty", self.shell_js)
        self.assertIn("state.cast.dirty = true", self.shell_js)
        self.assertIn("Voice configuration saved.", self.shell_js)

    def test_cast_selected_character_sections_follow_the_approved_order(self) -> None:
        voice = self.html.index('class="cast-voice-section"')
        reference = self.html.index('id="cast-reference-heading"')
        preview = self.html.index('id="cast-preview-heading"')
        character = self.html.index('id="cast-character-summary-heading"')
        appearance = self.html.index('id="cast-appearance-summary-heading"')
        advanced = self.html.index('id="cast-advanced-disclosure"')
        self.assertLess(voice, reference)
        self.assertLess(reference, preview)
        self.assertLess(preview, character)
        self.assertLess(character, appearance)
        self.assertLess(appearance, advanced)
        self.assertIn('class="cast-summary-grid"', self.html)
        self.assertNotIn('id="cast-character-summary-disclosure"', self.html)
        self.assertNotIn('id="cast-appearance-summary-disclosure"', self.html)
        self.assertIn('id="character-workspace" hidden inert aria-hidden="true"', self.html)

    def test_script_review_surface_matches_the_approved_issue_workflow(self) -> None:
        for identifier in (
            "script-review-workspace",
            "script-review-filters",
            "script-entry-list",
            "script-issue-previous",
            "script-issue-position",
            "script-issue-next",
            "script-blocker-summary",
            "script-review-inspector",
            "script-inspector-issue-type",
            "script-inspector-source-section",
            "script-inspector-actions",
            "script-approval-reason",
        ):
            self.assertEqual(self.html.count(f'id="{identifier}"'), 1)
        for label in (
            "Issue filter:",
            "Uncertain speaker",
            "Delivery direction",
            "Source mismatch",
            "Previous issue",
            "Next issue",
            "Source versus Script",
            "Recommended resolution",
            "Generation options",
            "Provenance and versions",
        ):
            self.assertIn(label, self.html)
        self.assertIn("grid-template-columns: minmax(0, 1fr) 360px", self.html)
        self.assertIn("width: 360px", self.html)
        self.assertIn("outline: 2px solid var(--alexandria-accent)", self.html)
        self.assertIn("border-bottom: 1px solid var(--alexandria-line)", self.html)

    def test_script_approval_uses_the_authoritative_lifecycle_audit(self) -> None:
        for snippet in (
            "async function approveCurrentScript()",
            "fetchJson('/api/script_lifecycle/accept'",
            "expected_script_fingerprint",
            "expected_metadata_fingerprint",
            "expected_source_fingerprint",
            "expected_state_fingerprint",
            "script_acceptance_blocked",
            "context.blocking_issues",
            "auditFingerprint: null",
            "review.auditFingerprint = lifecycle.fingerprints?.script || null",
            "review.auditFingerprint !== currentScriptFingerprint",
            "Resolve ${issues.length} blocking issue",
            "action.setAttribute('aria-describedby', 'script-approval-reason')",
        ):
            self.assertIn(snippet, self.shell_js)
        self.assertNotIn(
            "review.lifecycle = null;\n            review.auditIssues = [];",
            self.shell_js,
        )
        self.assertNotIn("/api/annotated_script/edit", self.shell_js)
        self.assertNotIn("/api/annotated_script/patch", self.shell_js)

    def test_script_issue_categories_are_user_facing_and_contextual(self) -> None:
        for snippet in (
            "return 'delivery_direction'",
            "return 'uncertain_speaker'",
            "return 'source_mismatch'",
            "Review speaker correction",
            "Review delivery correction",
            "Replace mismatched Script",
            "openLegacyScriptTool(button.dataset.scriptLegacyAction)",
        ):
            self.assertIn(snippet, self.shell_js)

    def test_produce_and_export_use_the_existing_transaction_contracts(self) -> None:
        for identifier in (
            "produce-workspace",
            "produce-chunk-list",
            "produce-inspector",
            "produce-regenerate-selected",
            "produce-regenerate-all",
            "export-workspace",
            "export-format-group",
            "export-validation-list",
            "export-waveform",
            "export-filename-behavior",
            "export-built-confirmation",
        ):
            self.assertEqual(self.html.count(f'id="{identifier}"'), 1)
        self.assertIn(
            'class="btn btn-outline-secondary" id="produce-regenerate-selected"',
            self.html,
        )
        for snippet in (
            "function produceReasonText(chunk)",
            "function groupProduceChunks(chunks)",
            "fetchJson('/api/produce/plan'",
            "'/api/produce/retry-failed'",
            "'/api/produce/generate'",
            "fetchJson('/api/produce/cancel'",
            "confirm_regenerate_all: confirmed",
            "function setPersistentAudioFromExport(aggregate)",
            "fetchJson('/api/export/plan'",
            "fetchJson('/api/export/build'",
            "fetchJson('/api/export/cancel'",
            "plan_fingerprint: plan.plan_fingerprint",
            "dependency_fingerprint: plan.dependency_fingerprint",
            "The previous valid output was preserved.",
            "async function buildExport()",
            "function setupExport()",
            "action.dataset.action = 'export-primary'",
        ):
            self.assertIn(snippet, self.shell_js)
        for label in (
            "M4B audiobook",
            "MP3 audio file",
            "Audacity project package",
            "Separate chapter files",
        ):
            self.assertIn(label, self.html)

    def test_confirmation_actions_wait_until_the_modal_is_interactive(self) -> None:
        for snippet in (
            "okBtn.disabled = true",
            "cancelBtn.disabled = true",
            "function onShown()",
            "modalElement.addEventListener('shown.bs.modal', onShown)",
        ):
            self.assertIn(snippet, self.html)

    def test_shell_surfaces_stale_runtime_instead_of_silently_using_old_code(self) -> None:
        self.assertEqual(self.html.count('id="runtime-restart-banner"'), 1)
        self.assertEqual(self.html.count('id="runtime-restart-copy"'), 1)
        self.assertIn("async function loadRuntimeStatus()", self.shell_js)
        self.assertIn("fetchJson('/api/runtime_status')", self.shell_js)
        self.assertIn("Restart Alexandria from Pinokio", self.shell_js)
        self.assertIn("window.setInterval(loadRuntimeStatus, 30000)", self.shell_js)

    def test_shell_module_has_valid_javascript_syntax(self) -> None:
        result = subprocess.run(
            ["node", "--check", str(SHELL_JS_PATH)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
