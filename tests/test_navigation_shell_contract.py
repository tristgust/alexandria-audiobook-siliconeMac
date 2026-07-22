from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "app" / "static" / "index.html"
CANONICAL_INTERFACE_PATH = ROOT / "app" / "static" / "canonical_interface.js"


class NavigationShellContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = HTML_PATH.read_text(encoding="utf-8")
        cls.canonical_interface = CANONICAL_INTERFACE_PATH.read_text(
            encoding="utf-8"
        )

    def test_primary_navigation_uses_approved_semantic_labels(self) -> None:
        expected = (
            ("setup", "projects", "Home"),
            ("script", "script", "Script"),
            ("characters", "cast", "Cast"),
            ("editor", "produce", "Produce"),
            ("audio", "export", "Export"),
            ("designer", "library", "Library"),
            ("setup", "settings", "Settings"),
        )
        for legacy_tab, destination, label in expected:
            pattern = re.compile(
                rf'class="[^"]*app-tab-link[^"]*"[^>]*'
                rf'data-tab="{re.escape(legacy_tab)}"[^>]*'
                rf'data-route="{re.escape(destination)}"[^>]*>.*?'
                rf'<span[^>]*>{re.escape(label)}</span>',
                re.S,
            )
            self.assertRegex(self.html, pattern)
        self.assertIn(
            '<span class="alexandria-rail-label project-stage-label">Project</span>',
            self.html,
        )
        self.assertNotIn('class="nav-step"', self.html)

    def test_more_menu_maps_every_legacy_tool_to_one_semantic_route(self) -> None:
        mappings = {
            "speaker-management": "advanced-character-operations",
            "designer": "voice-designer",
            "preparer": "audio-preparer",
            "dataset-builder": "dataset-builder",
            "training": "voice-training",
            "project-recovery": "maintenance",
        }
        self.assertIn('id="app-tools-toggle"', self.html)
        self.assertIn('aria-label="More tools"', self.html)
        for tab, tool in mappings.items():
            self.assertRegex(
                self.html,
                re.compile(
                    rf'data-tab="{re.escape(tab)}"[^>]*'
                    rf'data-route="more"[^>]*'
                    rf'data-route-tool="{re.escape(tool)}"'
                ),
            )

    def test_all_legacy_panels_remain_addressable(self) -> None:
        for tab in (
            "setup",
            "script",
            "characters",
            "editor",
            "audio",
            "speaker-management",
            "designer",
            "preparer",
            "dataset-builder",
            "training",
            "project-recovery",
        ):
            self.assertEqual(
                self.html.count(f'data-tab-panel="{tab}"'),
                1,
                tab,
            )

    def test_explicit_navigation_pushes_and_initialization_replaces(self) -> None:
        self.assertIn("window.history.pushState(state, '', hash)", self.html)
        self.assertIn("window.history.replaceState(state, '', hash)", self.html)
        self.assertIn("historyMode: 'push'", self.html)
        self.assertIn("setWorkspaceHash(currentWorkspaceRoute, 'replace')", self.html)
        self.assertIn("workspaceRouteApi.parseHash(window.location.hash)", self.html)

    def test_back_forward_and_manual_hash_changes_restore_without_rewriting(self) -> None:
        self.assertIn("window.addEventListener('popstate', activateLocationRoute)", self.html)
        self.assertIn("window.addEventListener('hashchange', activateLocationRoute)", self.html)
        self.assertRegex(
            self.html,
            re.compile(
                r"activateWorkspaceTab\(route\.legacyTab, \{\s*"
                r"route,\s*updateHash: false,\s*initial: true,\s*"
                r"focusMain: false",
                re.S,
            ),
        )
        self.assertIn("workspaceRouteApi.sameRoute(route, currentWorkspaceRoute)", self.html)

    def test_stale_async_activation_cannot_overwrite_a_newer_route(self) -> None:
        self.assertIn("let workspaceActivationSequence = 0", self.html)
        self.assertRegex(
            self.html,
            re.compile(
                r"async function activateWorkspaceTab\(tabName, options = \{\}\) \{\s*"
                r"const activationSequence = \+\+workspaceActivationSequence;",
                re.S,
            ),
        )
        self.assertRegex(
            self.html,
            re.compile(
                r"if \(activationSequence !== workspaceActivationSequence\) \{\s*"
                r"return false;\s*\}\s*"
                r"restoreWorkspaceRouteContext\(route\);",
                re.S,
            ),
        )

    def test_entity_context_updates_use_stable_ids(self) -> None:
        required = (
            "[data-project-id]",
            "[data-entry-id], [data-character-id]",
            "#chunks-body tr[data-id]",
            "[data-issue-id]",
            "[data-chapter-id]",
            "{ chunk: `chunk:${chunkTarget.dataset.id}` }",
            "context.search !== undefined",
            "context.filter !== undefined",
            "remove: value ? [] : ['search']",
            "remove: value ? [] : ['filter']",
        )
        for snippet in required:
            self.assertIn(snippet, self.html)
        self.assertIn("restoreWorkspaceRouteContext", self.html)
        self.assertIn("voiceTrainingSelectedId = currentWorkspaceRoute.context.character", self.html)

    def test_contextual_tool_handoff_preserves_exact_return_route(self) -> None:
        self.assertIn("currentWorkspaceRoute?.destination === 'cast'", self.html)
        self.assertIn("workspaceRouteApi.TAB_TO_TOOL", self.html)
        self.assertIn("const source = `cast:character:${entry.character_id}`", self.html)
        self.assertIn("const mode = CHARACTER_TOOL_MODES[tab] || null", self.html)
        self.assertIn("source,\n                return: returnRoute", self.html)
        self.assertIn("workspaceRouteApi.parseHash(exactReturnRoute)", self.html)
        self.assertIn("historyMode: 'push'", self.html)

    def test_canonical_cast_restores_route_owned_search_filter_and_character(self) -> None:
        self.assertIn("const usesCanonicalCastControls", self.html)
        self.assertIn("function applyCastRouteContext(route)", self.canonical_interface)
        self.assertIn("state.cast.search = String(context.search || '')", self.canonical_interface)
        self.assertIn("allowedFilters.has(context.filter)", self.canonical_interface)
        self.assertIn("if (context.character) state.cast.selectedId = context.character", self.canonical_interface)
        self.assertIn("applyCastRouteContext(route);\n            await loadCast();", self.canonical_interface)
        self.assertIn("{ filter: state.cast.filter }", self.canonical_interface)

    def test_existing_activation_function_remains_compatible(self) -> None:
        self.assertIn(
            "async function activateWorkspaceTab(tabName, options = {})",
            self.html,
        )
        self.assertIn("workspaceRouteForActivation(tabName, options)", self.html)
        self.assertIn("options.updateHash !== false", self.html)
        self.assertIn("options.historyMode || 'replace'", self.html)
        self.assertIn("window.AlexandriaNavigation = Object.freeze", self.html)

    def test_routing_changes_do_not_add_a_second_page_system(self) -> None:
        self.assertNotIn('data-tab-panel="projects"', self.html)
        self.assertNotIn('data-tab-panel="cast"', self.html)
        self.assertNotIn('data-tab-panel="produce"', self.html)
        self.assertNotIn('data-tab-panel="export"', self.html)
        self.assertEqual(self.html.count('/static/navigation_routes.js'), 1)


if __name__ == "__main__":
    unittest.main()
