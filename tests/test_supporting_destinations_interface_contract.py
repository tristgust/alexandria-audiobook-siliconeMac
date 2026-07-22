from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
SHELL = (ROOT / "app" / "static" / "canonical_interface.js").read_text(encoding="utf-8")
ROUTES = (ROOT / "app" / "static" / "navigation_routes.js").read_text(encoding="utf-8")
CSS = (ROOT / "app" / "static" / "canonical_pages.css").read_text(encoding="utf-8")
APP = (ROOT / "app" / "app.py").read_text(encoding="utf-8")


class SupportingDestinationsInterfaceContractTests(unittest.TestCase):
    def test_library_voices_templates_and_more_are_canonical_destinations(self) -> None:
        for route in ("library", "voices", "templates"):
            self.assertIn(f'data-route="{route}"', HTML)
            self.assertIn(f"{route}: Object.freeze", ROUTES)
        self.assertIn("'voice-casting': Object.freeze({ destination: 'cast' })", ROUTES)
        self.assertNotIn("voices: Object.freeze({ destination: 'cast' })", ROUTES)
        self.assertIn("route.destination === 'library'", SHELL)
        self.assertIn("route.destination === 'voices'", SHELL)
        self.assertIn("await loadLibrary()", SHELL)
        self.assertIn("await loadVoices()", SHELL)
        self.assertIn("route.destination === 'templates'", SHELL)
        self.assertIn("id=\"more-workspace\"", HTML)
        self.assertIn("id=\"help-center-workspace\"", HTML)

    def test_library_uses_authoritative_inventory_and_guarded_delete_routes(self) -> None:
        for identifier in (
            "library-workspace",
            "library-search",
            "library-kind-filter",
            "library-state-filter",
            "library-artifact-list",
            "library-artifact-detail",
        ):
            self.assertEqual(HTML.count(f'id="{identifier}"'), 1)
        for snippet in (
            "fetchJson(`/api/library?${params.toString()}`)",
            "delete-impact",
            "expected_inventory_fingerprint: impact.inventory_fingerprint",
            "expected_artifact_fingerprint: impact.artifact_fingerprint",
            "confirm_name: impact.confirm_name",
            "artifact?.native_route",
            "libraryNativeActionLabel",
            "applyLibraryRouteContext",
            "syncLibraryRouteContext",
            "source_book: { label: 'Source book'",
            "production_audio: { label: 'Production audio'",
            "export_output: { label: 'Finished output'",
            "VOICE_LIBRARY_KINDS",
        ):
            self.assertIn(snippet, SHELL)
        self.assertNotIn("copyLibraryArtifact", SHELL)
        self.assertNotIn("assignLibraryArtifact", SHELL)
        self.assertIn("No Library material yet", SHELL)
        self.assertIn("Open Script", SHELL)
        self.assertIn("Open Produce", SHELL)
        self.assertIn("Open Export", SHELL)

    def test_templates_use_authoritative_crud_and_new_project_fields(self) -> None:
        for identifier in (
            "templates-workspace",
            "template-search",
            "template-scope-filter",
            "template-list",
            "template-detail",
            "templateEditorModal",
            "template-editor-form",
            "new-project-template-context",
        ):
            self.assertEqual(HTML.count(f'id="{identifier}"'), 1)
        for preset in ("standard", "maximum_fidelity", "faster_draft", "custom"):
            self.assertIn(f'value="{preset}"', HTML)
        for phrase in (
            "/api/templates",
            "loadTemplates",
            "renderTemplates",
            "openTemplateEditor",
            "duplicateTemplate",
            "setDefaultTemplate",
            "deleteTemplate",
            "applyNewProjectTemplate",
            "template_id",
            "expected_catalog_fingerprint",
            "expected_template_fingerprint",
            "confirmation_text",
            "acknowledge_usage",
        ):
            self.assertIn(phrase, SHELL)
        for hidden_internal in (
            "template-editor-model",
            "template-editor-prompt",
            "template-editor-context-length",
            "template-editor-cache",
        ):
            self.assertNotIn(hidden_internal, HTML)
        self.assertIn('@app.get("/api/templates")', APP)
        self.assertIn('@app.post("/api/templates")', APP)
        self.assertIn('@app.put("/api/templates/{template_id}")', APP)
        self.assertIn('@app.delete("/api/templates/{template_id}")', APP)

    def test_help_center_is_manifest_backed_contextual_and_rendered_without_html_execution(self) -> None:
        self.assertIn('@app.get("/api/help")', APP)
        self.assertIn('@app.get("/api/help/context/{context_id}")', APP)
        self.assertIn('@app.get("/api/help/{slug}")', APP)
        self.assertLess(
            APP.index('@app.get("/api/help/context/{context_id}")'),
            APP.index('@app.get("/api/help/{slug}")'),
        )
        for identifier in (
            "global-help-action",
            "project-help-action",
            "help-search",
            "help-topic-list",
            "help-topic-detail",
            "help-return-more",
        ):
            self.assertEqual(HTML.count(f'id="{identifier}"'), 1)
        for snippet in (
            "renderHelpMarkdown(article, topic.markdown, topic.title)",
            "document.createElement",
            "document.createTextNode",
            "textContent = topic.title",
            "textContent = related.summary",
            "helpContextIdForRoute",
            "openContextualHelp",
            "help: safeContext",
            "topic: slug",
            "applyHelpRouteContext",
            "syncHelpRouteContext",
            "scheduleHelpSearch",
            "Promise",
            "new URLSearchParams",
            "event.key === 'ArrowDown'",
            "event.key === 'ArrowUp'",
            "event.key === 'Home'",
            "event.key === 'End'",
            "aria-activedescendant",
            "returnFromHelpCenter",
            "helpDestinationContext",
            "delete context.help",
            "delete context.topic",
        ):
            self.assertIn(snippet, SHELL)
        self.assertIn("'help'", ROUTES)
        self.assertIn("'topic'", ROUTES)
        help_renderer = SHELL[
            SHELL.index("function renderHelpMarkdown") : SHELL.index("function filteredHelpTopics")
        ]
        self.assertNotIn("innerHTML", help_renderer)
        self.assertNotIn("insertAdjacentHTML", help_renderer)
        self.assertNotIn("marked.parse", help_renderer)

    def test_supporting_surfaces_are_flat_and_responsive(self) -> None:
        for snippet in (
            ".supporting-master-detail",
            ".supporting-list-row[aria-selected=\"true\"]",
            ".template-master-detail",
            ".template-editor-grid",
            ".more-tool-list",
            "@media (max-width: 860px)",
        ):
            self.assertIn(snippet, CSS)
        self.assertNotIn("box-shadow: 0 12px 40px", CSS)

    def test_javascript_is_valid(self) -> None:
        for relative in (
            "app/static/navigation_routes.js",
            "app/static/canonical_interface.js",
        ):
            subprocess.run(
                ["node", "--check", str(ROOT / relative)],
                check=True,
                capture_output=True,
                text=True,
            )


    def test_voices_uses_read_only_aggregate_and_cast_authority(self) -> None:
        source = SHELL
        html = HTML
        for phrase in (
            "/api/voice-library",
            "voiceLibraryAsInventory",
            "renderVoiceLibraryDetail",
            "loadVoices",
            "setPersistentVoicePreview",
            "assignment_mutation_supported",
            "cast_is_authoritative",
            "Built-in Voice",
            "Supplied recording",
            "Instruction-controlled",
            "Voice adapter",
            "Voice alias",
            "Assignment happens only in Cast",
            "Voices is read-only",
        ):
            self.assertIn(phrase, source)
        self.assertIn('id="library-loading-copy"', html)
        self.assertIn("data-voice-preview", source)
        self.assertIn("data-voice-cast-character", source)
        self.assertNotIn("assignVoiceFromLibrary", source)
        self.assertNotIn("saveVoiceFromLibrary", source)



if __name__ == "__main__":
    unittest.main()
