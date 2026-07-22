from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "navigation_routes_harness.js"
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
