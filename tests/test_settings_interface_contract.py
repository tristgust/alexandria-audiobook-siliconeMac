from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "app/static/pages/settings.js"
SETTINGS_MODULES = (
    SETTINGS_PATH,
    ROOT / "app/static/pages/settings_model.js",
    ROOT / "app/static/pages/settings_sections.js",
    ROOT / "app/static/pages/settings_view.js",
)
STYLE_PATH = ROOT / "app/static/styles/pages/settings_more.css"
APP = (ROOT / "app/app.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "app/application_settings.py").read_text(encoding="utf-8")


class SettingsInterfaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.route = SETTINGS_PATH.read_text(encoding="utf-8")
        cls.source = "\n".join(
            path.read_text(encoding="utf-8") for path in SETTINGS_MODULES
        )
        cls.styles = STYLE_PATH.read_text(encoding="utf-8")

    def test_direct_module_owns_settings_lifecycle_when_loaded(self) -> None:
        for phrase in (
            "export async function mount",
            "dataRouteOwner",
            "settings-workspace",
            "signal",
            "return cleanup",
        ):
            self.assertIn(phrase, self.route)
        for forbidden in (
            "canonical_interface",
            "legacy-settings-workspace",
            "legacy-tab-store",
            "data-tab-panel",
            "innerHTML",
        ):
            self.assertNotIn(forbidden, self.source)

    def test_settings_uses_secret_safe_optimistic_api(self) -> None:
        for phrase in (
            "/api/settings",
            "expected_config_fingerprint",
            "api_key_mode",
            "settings_config_conflict",
            "UI.secretField",
        ):
            self.assertIn(phrase, self.source + SERVICE)
        self.assertIn('@app.get("/api/settings")', APP)
        self.assertIn('@app.put("/api/settings")', APP)
        self.assertNotIn('@app.post("/api/settings")', APP)

    def test_sections_and_specialist_links_preserve_route_context(self) -> None:
        for phrase in (
            "settings-provider-heading",
            "settings-accessibility-heading",
            "settingsSectionLink",
            "stage_profiles",
            "runtime_diagnostics",
            "model_cache",
            "advanced_generation",
            "focusSettingsSection",
            "shell.navigate",
        ):
            self.assertIn(phrase, self.source)

    def test_accessibility_preview_and_manual_storage_are_truthful(self) -> None:
        for phrase in (
            "settingsMotion",
            "settingsContrast",
            "settingsDensity",
            "status_announcements",
            "manual_only",
            "Guarded cleanup remains a separate Maintenance action",
            "event.metaKey || event.ctrlKey",
        ):
            self.assertIn(phrase, self.source + SERVICE)
        for selector in (
            'body[data-settings-motion="reduced"]',
            'body[data-settings-contrast="more"]',
            'body[data-settings-density="compact"]',
        ):
            self.assertIn(selector, self.styles)

    def test_javascript_is_valid(self) -> None:
        for path in SETTINGS_MODULES:
            subprocess.run(
                ["node", "--check", str(path)],
                check=True,
                capture_output=True,
                text=True,
            )


if __name__ == "__main__":
    unittest.main()
