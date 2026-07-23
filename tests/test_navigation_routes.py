from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "navigation_routes_harness.js"
ROUTES = ROOT / "app" / "static" / "navigation_routes.js"
MANIFEST = ROOT / "tests" / "b19_t06_routes.json"


class NavigationRouteTests(unittest.TestCase):
    def test_pure_route_harness_covers_the_acceptance_manifest(self) -> None:
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

    def test_manifest_and_route_module_have_exact_canonical_counts(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["routes"]), 18)
        self.assertEqual(len(manifest["aliases"]), 18)
        source = ROUTES.read_text(encoding="utf-8")
        self.assertIn("const ROUTES", source)
        self.assertIn("const ALIASES", source)

    def test_aliases_are_translations_not_legacy_runtime_state(self) -> None:
        source = ROUTES.read_text(encoding="utf-8")
        for marker in (
            "legacyTab",
            "TAB_TO_TOOL",
            "TOOL_TO_TAB",
            "routeForLegacyTab",
            "data-tab-panel",
            "activateWorkspaceTab",
        ):
            self.assertNotIn(marker, source)

    def test_only_allowlisted_bounded_context_is_serialized(self) -> None:
        source = ROUTES.read_text(encoding="utf-8")
        for key in (
            "project",
            "character",
            "chunk",
            "chapter",
            "issue",
            "source",
            "mode",
            "filter",
            "search",
            "help",
            "topic",
            "return",
        ):
            self.assertIn(f"'{key}'", source)
        self.assertIn("MAX_CONTEXT_LENGTH", source)
        self.assertIn("CONTROL_CHARACTERS", source)


if __name__ == "__main__":
    unittest.main()
