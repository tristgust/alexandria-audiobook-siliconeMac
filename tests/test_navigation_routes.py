from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "navigation_routes_harness.js"
SHELL_HARNESS = ROOT / "tests" / "navigation_shell_harness.js"
HTML = ROOT / "app" / "static" / "index.html"
ROUTES = ROOT / "app" / "static" / "navigation_routes.js"


class NavigationRouteTests(unittest.TestCase):
    def test_pure_route_harness(self) -> None:
        completed = subprocess.run(
            ["node", str(HARNESS)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertTrue(all(payload["results"].values()))

    def test_shipped_shell_history_functions_use_canonical_routes(self) -> None:
        completed = subprocess.run(
            ["node", str(SHELL_HARNESS)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["calls"][0]["method"], "replace")
        self.assertEqual(payload["calls"][1]["method"], "push")
        self.assertEqual(payload["finalRoute"]["context"]["character"], "character_2")

    def test_route_module_is_loaded_before_application_script(self) -> None:
        html = HTML.read_text(encoding="utf-8")
        route_src = '<script src="/static/navigation_routes.js"></script>'
        self.assertIn(route_src, html)
        self.assertLess(html.index(route_src), html.index("var llmProfilesStatus"))

    def test_semantic_destination_contract_is_complete(self) -> None:
        source = ROUTES.read_text(encoding="utf-8")
        for destination in (
            "projects",
            "script",
            "cast",
            "produce",
            "export",
            "library",
            "settings",
            "more",
        ):
            self.assertIn(f"{destination}:", source)
        for context in (
            "project",
            "character",
            "chunk",
            "chapter",
            "issue",
            "tool",
            "mode",
            "return",
            "filter",
            "search",
        ):
            self.assertIn(f"'{context}'", source)

    def test_old_hashes_remain_present_only_as_compatibility_aliases(self) -> None:
        source = ROUTES.read_text(encoding="utf-8")
        for alias in (
            "setup",
            "characters",
            "voices",
            "voice-projects",
            "editor",
            "audio",
            "result",
            "speaker-management",
            "designer",
            "preparer",
            "dataset-builder",
            "training",
            "project-recovery",
        ):
            self.assertRegex(
                source,
                rf"(?:^|\n)\s*(?:'{re.escape(alias)}'|{re.escape(alias)}):",
            )


if __name__ == "__main__":
    unittest.main()
