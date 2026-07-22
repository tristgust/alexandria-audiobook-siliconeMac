from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from more_tools import MORE_TOOL_DEFINITIONS


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
SHELL = (ROOT / "app" / "static" / "canonical_interface.js").read_text(encoding="utf-8")
ROUTES = (ROOT / "app" / "static" / "navigation_routes.js").read_text(encoding="utf-8")
CSS = (ROOT / "app" / "static" / "canonical_pages.css").read_text(encoding="utf-8")
APP = (ROOT / "app" / "app.py").read_text(encoding="utf-8")


class MoreInterfaceContractTests(unittest.TestCase):
    def test_more_is_one_dynamic_quiet_directory(self) -> None:
        for identifier in (
            "more-workspace",
            "more-context-banner",
            "more-context-label",
            "more-context-copy",
            "more-return-action",
            "more-search",
            "more-result-count",
            "more-loading",
            "more-content",
            "more-tool-groups",
        ):
            self.assertEqual(HTML.count(f'id="{identifier}"'), 1)
        more_start = HTML.index('id="more-workspace"')
        more_end = HTML.index('id="help-center-workspace"', more_start)
        more_html = HTML[more_start:more_end]
        self.assertNotIn('data-more-tool="', more_html)
        self.assertNotIn("dashboard", more_html.casefold())
        self.assertIn("without turning it into another project stage", more_html)
        self.assertIn("duplicating its authoritative state", more_html)

    def test_more_uses_get_only_registry_and_server_issued_routes(self) -> None:
        for phrase in (
            '@app.get("/api/more")',
            "fetchJson(`/api/more?${params.toString()}`)",
            "state.more.payload?.tools",
            "const route = tool?.route",
            "window.AlexandriaNavigation?.navigate(route.destination, route.context || {})",
            "landing_mutation_supported",
        ):
            self.assertIn(phrase, APP + SHELL + (ROOT / "app" / "more_tools.py").read_text(encoding="utf-8"))
        self.assertNotIn('@app.post("/api/more")', APP)
        self.assertNotIn('@app.put("/api/more")', APP)
        self.assertNotIn('@app.delete("/api/more")', APP)
        self.assertNotIn("tool: button.dataset.moreTool", SHELL)

    def test_registry_tools_match_header_copy_and_semantic_navigation(self) -> None:
        tools = {item["tool"] for item in MORE_TOOL_DEFINITIONS}
        self.assertEqual(len(tools), 8)
        for tool in tools:
            self.assertIn(f"'{tool}':", SHELL)
            self.assertIn(f"'{tool}':", ROUTES)
            self.assertIn(tool, (ROOT / "docs" / "NAVIGATION_ROUTES.md").read_text(encoding="utf-8"))
        for definition in MORE_TOOL_DEFINITIONS:
            self.assertIn(definition["legacy_tab"], ROUTES)
        self.assertIn("const TOOL_TO_TAB = Object.freeze", ROUTES)
        self.assertIn("function routeForLegacyTab", ROUTES)
        self.assertIn("function parseHash", ROUTES)

    def test_more_preserves_project_character_source_and_exact_return_context(self) -> None:
        for phrase in (
            "project_id: state.route.context.project || state.flow?.project?.id || null",
            "character_id: state.route.context.character || null",
            "source: state.route.context.source || null",
            "return_route: state.route.context.return || '#/more'",
            "params.set('project_id', context.project_id)",
            "params.set('character_id', context.character_id)",
            "params.set('source', context.source)",
            "params.set('return_route', context.return_route)",
            "routeApi.parseHash(hash)",
        ):
            self.assertIn(phrase, SHELL)
        self.assertIn("Character context requires a project context", (ROOT / "app" / "more_tools.py").read_text(encoding="utf-8"))

    def test_search_and_context_are_url_backed_and_accessibly_rendered(self) -> None:
        for phrase in (
            "applyMoreRouteContext",
            "syncMoreRouteContext",
            "historyMode: 'replace'",
            "state.more.query",
            "renderMoreTools",
            "aria-labelledby=\"more-group-",
            "role=\"list\"",
            "role=\"listitem\"",
            "No specialist tool matches",
        ):
            self.assertIn(phrase, SHELL)
        self.assertIn("Tool routes will preserve this context", HTML)
        self.assertIn("Return to character", SHELL)
        self.assertIn("Return to project", SHELL)

    def test_landing_has_no_mutating_or_backend_internal_controls(self) -> None:
        more_start = HTML.index('id="more-workspace"')
        more_end = HTML.index('id="help-center-workspace"', more_start)
        more_html = HTML[more_start:more_end].casefold()
        for forbidden in (
            "delete",
            "repair",
            "download",
            "migrate",
            "api key",
            "model name",
            "prompt",
            "context length",
            "fingerprint",
            "voice_config",
        ):
            self.assertNotIn(forbidden, more_html)
        self.assertNotIn("data-more-delete", SHELL)
        self.assertNotIn("saveMore", SHELL)
        self.assertNotIn("assignMore", SHELL)

    def test_more_layout_is_flat_and_responsive(self) -> None:
        for selector in (
            ".more-toolbar",
            ".more-context-banner",
            ".more-tool-groups",
            ".more-tool-group-heading",
            ".more-tool-state",
            "@media (max-width: 560px)",
        ):
            self.assertIn(selector, CSS)
        self.assertIn("border-bottom: 1px solid var(--alexandria-line)", CSS)
        self.assertNotIn(".more-tool-row { box-shadow", CSS)

    def test_javascript_and_navigation_harness_are_valid(self) -> None:
        subprocess.run(
            ["node", "--check", str(ROOT / "app/static/canonical_interface.js")],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["node", "--check", str(ROOT / "app/static/navigation_routes.js")],
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
