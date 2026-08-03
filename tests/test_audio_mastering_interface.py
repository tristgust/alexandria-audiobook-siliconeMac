from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MASTERING = ROOT / "app/static/pages/produce_mastering.js"
FINAL_LISTEN = ROOT / "app/static/pages/produce_final_listen.js"
ACTIONS = ROOT / "app/static/pages/produce_actions.js"
ROUTE = ROOT / "app/static/pages/produce_route.js"
STYLES = ROOT / "app/static/styles/pages/produce_export.css"
APP = ROOT / "app/app.py"
PROJECT = ROOT / "app/project.py"
ENGINE = ROOT / "app/audio_mastering.py"
BROWSER = ROOT / "tests/b20_t06_publication_mastering_browser.js"


class AudioMasteringInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mastering = MASTERING.read_text(encoding="utf-8")
        cls.final_listen = FINAL_LISTEN.read_text(encoding="utf-8")
        cls.actions = ACTIONS.read_text(encoding="utf-8")
        cls.route = ROUTE.read_text(encoding="utf-8")
        cls.styles = STYLES.read_text(encoding="utf-8")
        cls.app = APP.read_text(encoding="utf-8")
        cls.project = PROJECT.read_text(encoding="utf-8")
        cls.engine = ENGINE.read_text(encoding="utf-8")

    def test_mastering_is_nested_in_final_listen_and_reuses_take_lineage(self) -> None:
        self.assertIn("createMasteringSection", self.final_listen)
        self.assertIn("useTake(selected, source)", self.mastering)
        self.assertIn("undoTakeOperation", self.mastering)
        self.assertIn("register_audio_rendition", self.project)
        self.assertNotIn("localStorage", self.mastering)
        self.assertNotIn("sessionStorage", self.mastering)
        self.assertNotIn("innerHTML", self.mastering)

    def test_operator_copy_rejects_generic_effects_and_truthfully_scopes_provenance(self) -> None:
        for phrase in (
            "Pitch shifting, chorus, dramatic reverb, voice transformation",
            "source Take remains immutable",
            "Structural provenance does not establish Voice authorization or human approval",
            "Bypass to source Take",
            "Final Listen pin required",
        ):
            self.assertIn(phrase, self.mastering)
        self.assertNotIn("trusted signer", self.mastering.casefold())
        self.assertNotIn("voice authorized", self.mastering.casefold())

    def test_scheduler_joined_publication_and_exact_routes_are_declared(self) -> None:
        for endpoint in (
            "/mastering/plan",
            "/mastering/apply",
            "/api/background-work/",
        ):
            self.assertIn(endpoint, self.app + self.actions)
        self.assertIn('domain="mastering"', self.app)
        self.assertIn('resources=("mastering", "project_audio")', self.app)
        self.assertIn("publisher=publish", self.app)
        self.assertIn("publication_mastering_dependency", self.project)
        self.assertIn("temporary_output", self.engine)
        self.assertIn("audio_mastering_effect_rejected", self.engine)

    def test_progress_polling_and_responsive_styles_use_existing_system(self) -> None:
        self.assertIn("mastering_process?.running", self.route)
        self.assertIn("data-mastering-cancel", self.mastering)
        self.assertIn("data-mastering-review", self.mastering)
        self.assertIn("maximumLowPass", self.mastering)
        self.assertIn(".produce-mastering", self.styles)
        self.assertIn("@media (max-width: 640px)", self.styles)
        self.assertIn("var(--space-", self.styles)
        self.assertNotRegex(self.styles, r"#[0-9a-fA-F]{3,8}\b")

    def test_javascript_and_browser_contract_parse(self) -> None:
        for path in (MASTERING, FINAL_LISTEN, ACTIONS, ROUTE, BROWSER):
            with self.subTest(path=path.name):
                subprocess.run(
                    ["node", "--check", str(path)],
                    check=True,
                    capture_output=True,
                    text=True,
                )


if __name__ == "__main__":
    unittest.main()
