from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
HTML_PATH = STATIC / "index.html"
SHELL_PATH = STATIC / "app_shell.js"
CHROME_PATH = STATIC / "shell_chrome.js"
API_PATH = STATIC / "api_client.js"
ROUTES_PATH = STATIC / "navigation_routes.js"
RUNTIME_STATE_PATH = STATIC / "shell_runtime_state.js"


class CanonicalShellInterfaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = HTML_PATH.read_text(encoding="utf-8")
        cls.shell = SHELL_PATH.read_text(encoding="utf-8") if SHELL_PATH.exists() else ""

    def test_document_composes_the_canonical_shell_during_parse(self) -> None:
        for stylesheet in (
            "/static/styles/tokens.css",
            "/static/styles/shell.css",
            "/static/styles/components.css",
        ):
            self.assertEqual(self.html.count(stylesheet), 1)
        for factory in (
            "UI.appShell(",
            "UI.navRail(",
            "UI.globalHeader(",
            "UI.projectHeader(",
            "UI.persistentPlayer(",
            "UI.shellInspector(",
        ):
            self.assertEqual(self.html.count(factory), 1, factory)
        self.assertIn('class="visually-hidden skip-link"', self.html)
        self.assertNotIn('class="app-shell"', self.html)
        self.assertNotIn("<nav", self.html)
        self.assertNotIn("<header", self.html)
        self.assertLess(len(self.html.splitlines()), 220)

    def test_document_contains_no_legacy_workspace_or_controller(self) -> None:
        prohibited = (
            "data-tab-panel",
            "setup-tab",
            "characters-tab",
            "editor-tab",
            "audio-tab",
            "speaker-management-tab",
            "legacy-tab-store",
            "activateWorkspaceTab",
            "VoiceCardBridge",
            "canonical_interface.js",
            "canonical_pages.css",
        )
        for marker in prohibited:
            self.assertNotIn(marker, self.html)
            self.assertNotIn(marker, self.shell)

    def test_document_has_no_embedded_destination_pages(self) -> None:
        self.assertNotIn('data-route-owner=', self.html)
        self.assertNotIn('data-page=', self.html)
        for marker in (
            "project-home-workspace",
            "script-review-workspace",
            "cast-voice-editor",
            "produce-workspace",
            "export-workspace",
            "canonical-settings-workspace",
            "canonical-maintenance-workspace",
        ):
            self.assertNotIn(marker, self.html)

    def test_factory_input_declares_the_canonical_navigation_contract(self) -> None:
        for path, label in (
            ("projects", "Home"),
            ("script", "Script"),
            ("cast", "Cast"),
            ("produce", "Produce"),
            ("export", "Export"),
            ("library", "Library"),
            ("voices", "Voices"),
            ("templates", "Templates"),
            ("settings", "Settings"),
            ("more", "More"),
        ):
            self.assertIn(f"{{ label: '{label}', href: '#/{path}'", self.html)

    def test_shell_has_one_persistent_transport_and_one_overlay_layer(self) -> None:
        self.assertEqual(self.html.count("UI.persistentPlayer("), 1)
        self.assertEqual(self.html.count("overlay.id = 'canonical-overlay-root'"), 1)
        self.assertNotIn('id="main-audio"', self.html)
        self.assertNotIn('<audio', self.html)

    def test_failed_module_bootstrap_has_a_static_truthful_fallback(self) -> None:
        self.assertIn("data-bootstrap-error", self.html)
        self.assertIn("globalThis.AlexandriaBootstrap", self.html)
        self.assertIn('onerror="AlexandriaBootstrap.fail()"', self.html)
        self.assertIn("import('/static/app_shell.js')", self.html)
        self.assertIn('.catch((error) => AlexandriaBootstrap.fail(error))', self.html)
        self.assertRegex(self.html, r'<main[^>]+data-bootstrap-error[^>]+hidden')

    def test_retired_hybrid_assets_are_absent(self) -> None:
        self.assertFalse((STATIC / "canonical_interface.js").exists())
        self.assertFalse((STATIC / "canonical_pages.css").exists())

    def test_owned_browser_sources_have_valid_syntax_and_bounded_size(self) -> None:
        for path in (
            SHELL_PATH,
            CHROME_PATH,
            API_PATH,
            ROUTES_PATH,
            RUNTIME_STATE_PATH,
        ):
            self.assertTrue(path.exists(), path)
            result = subprocess.run(
                ["node", "--check", str(path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            pure_lines = [
                line for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("//")
            ]
            self.assertLessEqual(len(pure_lines), 250, path)


if __name__ == "__main__":
    unittest.main()
