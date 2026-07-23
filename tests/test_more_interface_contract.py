from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from more_tools import MORE_TOOL_DEFINITIONS


ROOT = Path(__file__).resolve().parents[1]
MORE_PATH = ROOT / "app/static/pages/more.js"
ROUTES = (ROOT / "app/static/navigation_routes.js").read_text(encoding="utf-8")
APP = (ROOT / "app/app.py").read_text(encoding="utf-8")


class MoreInterfaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = MORE_PATH.read_text(encoding="utf-8")

    def test_more_is_one_direct_get_only_directory(self) -> None:
        for phrase in (
            "export async function mount",
            "dataRouteOwner",
            'api.get(`/api/more?',
            "landing_mutation_supported",
            "No specialist tool matches",
        ):
            self.assertIn(phrase, self.source)
        self.assertIn('@app.get("/api/more")', APP)
        for method in ("post", "put", "delete"):
            self.assertNotIn(f"api.{method}(", self.source)

    def test_all_server_registry_tools_dispatch_to_direct_routes(self) -> None:
        tools = {item["tool"] for item in MORE_TOOL_DEFINITIONS}
        self.assertEqual(len(tools), 8)
        for tool in tools:
            self.assertIn(tool, self.source)
            self.assertIn(f"'more/{tool}'", ROUTES)
        self.assertIn("shell.navigate", self.source)

    def test_context_search_and_return_are_url_backed(self) -> None:
        for phrase in (
            "project_id",
            "character_id",
            "return_route",
            "route.context.search",
            "historyMode: 'replace'",
            "data-support-return",
        ):
            self.assertIn(phrase, self.source)

    def test_directory_has_no_assignment_or_mutation_authority(self) -> None:
        for forbidden in (
            "saveVoice",
            "assignVoice",
            "deleteProject",
            "innerHTML",
            "legacy-tab-store",
            "data-tab-panel",
        ):
            self.assertNotIn(forbidden, self.source)

    def test_javascript_and_navigation_harness_are_valid(self) -> None:
        subprocess.run(
            ["node", "--check", str(MORE_PATH)],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["node", str(ROOT / "tests/navigation_routes_harness.js")],
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
