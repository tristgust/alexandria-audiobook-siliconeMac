from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FINAL_LISTEN = ROOT / "app/static/pages/produce_final_listen.js"
ACTIONS = ROOT / "app/static/pages/produce_actions.js"
INSPECTOR = ROOT / "app/static/pages/produce_inspector.js"
STYLES = ROOT / "app/static/styles/pages/produce_export.css"
APP = ROOT / "app/app.py"
PROJECT = ROOT / "app/project.py"
EXPORT = ROOT / "app/export_aggregate.py"
BROWSER = ROOT / "tests/b20_t05_chapter_assembly_browser.js"


class ChapterAssemblyInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.final_listen = FINAL_LISTEN.read_text(encoding="utf-8")
        cls.actions = ACTIONS.read_text(encoding="utf-8")
        cls.inspector = INSPECTOR.read_text(encoding="utf-8")
        cls.styles = STYLES.read_text(encoding="utf-8")
        cls.app = APP.read_text(encoding="utf-8")
        cls.project = PROJECT.read_text(encoding="utf-8")
        cls.export = EXPORT.read_text(encoding="utf-8")

    def test_final_listen_is_inside_produce_and_uses_existing_take_actions(self) -> None:
        self.assertIn("createFinalListenSection", self.inspector)
        self.assertIn("takeList({ selected, aggregate, shell, actions })", self.inspector)
        for action in (
            "pinFinalListen",
            "updateFinalListenPause",
            "createFinalListenRendition",
            "useTake",
            "undoTakeOperation",
        ):
            self.assertIn(action, self.actions)
        self.assertNotIn("localStorage", self.final_listen)
        self.assertNotIn("sessionStorage", self.final_listen)
        self.assertNotIn("innerHTML", self.final_listen)

    def test_operator_surface_names_bounded_non_destructive_contract(self) -> None:
        for phrase in (
            "Pin current Take",
            "Pause after this line",
            "Trim edge defects",
            "Split one problematic delivery",
            "raw Take remains unchanged",
            "Script remains one chunk",
        ):
            self.assertIn(phrase, self.final_listen)
        for selector in (
            "data-final-listen-pin",
            "data-final-listen-pause-apply",
            "data-final-listen-trim-apply",
            "data-final-listen-split-apply",
            "data-final-listen-play",
        ):
            self.assertIn(selector, self.final_listen)

    def test_api_and_project_preserve_one_source_order_and_rendition_authority(self) -> None:
        for endpoint in (
            "/final-listen/pin",
            "/final-listen/pause",
            "/final-listen/rendition",
        ):
            self.assertIn(endpoint, self.app)
        self.assertIn("register_audio_rendition", self.project)
        self.assertIn("chapter_source_order_fingerprint", self.project)
        self.assertNotIn("insert_chunk(", self.final_listen)
        self.assertNotIn("delete_chunk(", self.final_listen)
        self.assertNotIn("restore_chunk(", self.final_listen)
        self.assertIn("build_chapters", self.export)

    def test_styles_use_existing_tokens_and_responsive_controls(self) -> None:
        self.assertIn(".produce-final-listen", self.styles)
        self.assertIn("var(--space-", self.styles)
        self.assertIn("var(--color-", self.styles)
        self.assertIn("@media (max-width: 640px)", self.styles)
        self.assertNotRegex(self.styles, r"#[0-9a-fA-F]{3,8}\b")

    def test_javascript_and_browser_contract_parse(self) -> None:
        for path in (FINAL_LISTEN, ACTIONS, INSPECTOR, BROWSER):
            with self.subTest(path=path.name):
                subprocess.run(
                    ["node", "--check", str(path)],
                    check=True,
                    capture_output=True,
                    text=True,
                )


if __name__ == "__main__":
    unittest.main()
