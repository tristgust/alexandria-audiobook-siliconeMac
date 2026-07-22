from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
SHELL = (ROOT / "app" / "static" / "canonical_interface.js").read_text(encoding="utf-8")
CSS = (ROOT / "app" / "static" / "canonical_pages.css").read_text(encoding="utf-8")
APP = (ROOT / "app" / "app.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "app" / "application_settings.py").read_text(encoding="utf-8")


class SettingsInterfaceContractTests(unittest.TestCase):
    def canonical_settings_html(self) -> str:
        start = HTML.index('id="canonical-settings-workspace"')
        end = HTML.index('data-tab-panel="project-recovery"', start)
        return HTML[start:end]

    def canonical_settings_shell(self) -> str:
        start = SHELL.index("function setCanonicalSettingsStatus")
        end = SHELL.index("function appendHelpInlineText", start)
        return SHELL[start:end]

    def test_settings_has_one_canonical_surface_and_hidden_legacy_maintenance_surface(self) -> None:
        for identifier in (
            "canonical-settings-workspace",
            "canonical-settings-form",
            "settings-loading",
            "settings-load-error",
            "canonical-maintenance-workspace",
            "legacy-settings-workspace",
            "settings-surface-title",
            "settings-surface-description",
        ):
            self.assertEqual(HTML.count(f'id="{identifier}"'), 1)
        self.assertIn('id="legacy-settings-workspace" hidden', HTML)
        self.assertIn('id="recovery-center" hidden', HTML)
        self.assertIn("canonicalSettings.hidden = !settingsDestination", SHELL)
        self.assertIn("canonicalMaintenance.hidden = !maintenance || legacyMaintenance", SHELL)
        self.assertIn("legacySettings.hidden = !legacyMaintenance", SHELL)
        self.assertIn("recovery.hidden = true", SHELL)

    def test_normal_settings_contains_preferences_provider_speech_accessibility_and_storage(self) -> None:
        normal = self.canonical_settings_html()
        required = (
            "Project preferences",
            "Default source language",
            "Default output language",
            "Default template",
            "Confirm destructive actions",
            "Remember the last project",
            "Language model",
            "Provider",
            "Base URL",
            "API key",
            "Runtime behavior",
            "Structured output required",
            "Speech engine",
            "Accessibility and density",
            "Retention limits",
            "Diagnostics and specialist configuration",
            "Save Settings",
        )
        for phrase in required:
            self.assertIn(phrase, normal)
        for identifier in (
            "settings-source-language",
            "settings-output-language",
            "settings-provider-backend",
            "settings-provider-model",
            "settings-provider-url",
            "settings-api-key-action",
            "settings-api-key",
            "settings-context-length",
            "settings-speech-mode",
            "settings-motion",
            "settings-contrast",
            "settings-density",
            "settings-rollback-days",
            "settings-intermediate-days",
            "settings-backup-gib",
        ):
            self.assertEqual(normal.count(f'id="{identifier}"'), 1)
        self.assertIn('type="password"', normal)
        self.assertIn('id="settings-structured-output" checked disabled', normal)

    def test_diagnostics_repair_cache_and_prompt_editors_are_not_in_normal_settings(self) -> None:
        normal = self.canonical_settings_html()
        for forbidden in (
            "llm-runtime-panel",
            "model-cache-panel",
            "recovery-center",
            "btn-model-cache-download-required",
            "btn-llm-preload",
            "btn-llm-unload",
            "system-prompt",
            "review-system-prompt",
            "persona-advanced-prompt",
            "migration",
        ):
            self.assertNotIn(forbidden, normal)
        for route_key in (
            "stage_profiles",
            "runtime_diagnostics",
            "model_cache",
            "advanced_generation",
        ):
            self.assertIn(f'data-settings-destination="{route_key}"', normal)
        self.assertIn("openSettingsDestination", SHELL)
        self.assertIn("openMaintenanceMode", SHELL)

    def test_settings_uses_optimistic_secret_safe_api_and_retains_invalid_edits(self) -> None:
        settings_shell = self.canonical_settings_shell()
        for phrase in (
            "fetchJson('/api/settings')",
            "fetchJson('/api/settings',",
            "method: 'PUT'",
            "expected_config_fingerprint",
            "api_key_mode",
            "settings_config_conflict",
            "setCanonicalSettingsStatus(error.message",
            "setCanonicalSettingsSaveState('Not saved'",
        ):
            self.assertIn(phrase, settings_shell + SERVICE)
        self.assertNotIn("loadSettings({ force: true })", settings_shell[settings_shell.index("async function saveSettings"):settings_shell.index("function openSettingsDestination")])
        self.assertNotIn("setValue('settings-api-key', provider.api_key", settings_shell)
        self.assertIn("setValue('settings-api-key', '')", settings_shell)
        self.assertIn("provider.api_key_configured", settings_shell)
        self.assertIn('@app.get("/api/settings")', APP)
        self.assertIn('@app.put("/api/settings")', APP)
        self.assertIn('"ALEXANDRIA_CONFIG_PATH"', APP)
        self.assertNotIn('@app.post("/api/settings")', APP)
        self.assertNotIn('@app.delete("/api/settings")', APP)

    def test_accessibility_preferences_apply_immediately_and_keyboard_save_is_available(self) -> None:
        for phrase in (
            "applyAccessibilityPreferences",
            "dataset.settingsMotion",
            "accessibility.status_announcements === false",
            "event.metaKey || event.ctrlKey",
            "event.key.toLocaleLowerCase() !== 's'",
            "saveSettings();",
        ):
            self.assertIn(phrase, SHELL)
        for selector in (
            'body[data-settings-motion="reduced"]',
            'body[data-settings-contrast="more"]',
            'body[data-settings-density="compact"]',
            "@media (max-width: 1080px)",
            "@media (max-width: 760px)",
        ):
            self.assertIn(selector, CSS)

    def test_settings_has_no_project_voice_audio_or_export_mutation_path(self) -> None:
        settings_shell = self.canonical_settings_shell()
        settings_service = SERVICE.casefold()
        for forbidden in (
            "voice_config",
            "annotated_script",
            "chunks.json",
            "audio_validity",
            "character_roster",
            "cloned_audiobook",
            "audiobook.m4b",
        ):
            self.assertNotIn(forbidden, settings_service)
            self.assertNotIn(forbidden, settings_shell.casefold())
        route_start = APP.index('@app.get("/api/settings")')
        route_end = APP.index('@app.get("/api/config")', route_start)
        settings_routes = APP[route_start:route_end].casefold()
        for forbidden in (
            "voice_config_path",
            "script_path",
            "chunks_path",
            "audio_validity_path",
            "roster_path",
        ):
            self.assertNotIn(forbidden, settings_routes)

    def test_storage_policy_is_truthful_about_deferred_cleanup_enforcement(self) -> None:
        normal = self.canonical_settings_html()
        self.assertIn("Save policy now", normal)
        self.assertIn("Guarded cleanup remains a separate Maintenance action", normal)
        for phrase in (
            '"cleanup_mode": "manual_only"',
            '"enforcement_status": "policy_saved_not_enforced"',
            "Retention values are saved now",
            "implemented separately in Maintenance and audio-safety work",
        ):
            self.assertIn(phrase, SERVICE)
        self.assertNotIn("delete_backup", self.canonical_settings_shell())
        self.assertNotIn("cleanup_files", self.canonical_settings_shell())

    def test_javascript_is_valid(self) -> None:
        subprocess.run(
            ["node", "--check", str(ROOT / "app/static/canonical_interface.js")],
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
