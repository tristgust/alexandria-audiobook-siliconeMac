from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
SHELL_JS = (ROOT / "app" / "static" / "canonical_interface.js").read_text(
    encoding="utf-8"
)
APP_PY = (ROOT / "app" / "app.py").read_text(encoding="utf-8")


class ProjectHomeInterfaceContractTests(unittest.TestCase):
    def test_project_home_is_distinct_from_technical_settings(self) -> None:
        self.assertEqual(HTML.count('id="project-home-workspace"'), 1)
        self.assertEqual(HTML.count('id="project-list"'), 1)
        self.assertIn('id="project-search"', HTML)
        self.assertIn('id="project-sort"', HTML)
        self.assertIn('id="project-filter"', HTML)
        self.assertIn('id="project-continuation"', HTML)
        self.assertIn("projectHome.hidden = destination !== 'projects'", SHELL_JS)
        self.assertIn("setupSurface.hidden = !settingsDestination && !maintenance", SHELL_JS)
        self.assertIn("canonicalSettings.hidden = !settingsDestination", SHELL_JS)
        self.assertIn("canonicalMaintenance.hidden = !maintenance || legacyMaintenance", SHELL_JS)
        self.assertIn("legacySettings.hidden = !legacyMaintenance", SHELL_JS)

    def test_new_project_is_one_scrollable_form_without_a_faux_stepper(self) -> None:
        modal_start = HTML.index('id="newProjectModal"')
        modal_end = HTML.index('id="templateEditorModal"', modal_start)
        modal_html = HTML[modal_start:modal_end]
        self.assertEqual(HTML.count('id="new-project-form"'), 1)
        self.assertEqual(modal_html.count('class="new-project-section"'), 5)
        self.assertEqual(modal_html.count('role="radiogroup"'), 2)
        self.assertNotIn("new-project-section-number", modal_html)
        self.assertNotIn("new-project-stepper", modal_html)
        self.assertIn("overflow-y: auto", HTML)
        self.assertIn("position: sticky", HTML)
        self.assertIn('id="new-project-submit" disabled', modal_html)

    def test_new_project_contains_only_the_approved_normal_flow(self) -> None:
        required = (
            'id="new-project-source"',
            'id="new-project-name"',
            'id="new-project-title"',
            'id="new-project-author"',
            'id="new-project-source-language"',
            'id="new-project-output-language"',
            'value="local"',
            'value="chatgpt_task_bundle"',
            'value="import_existing_script"',
            'value="standard"',
            'value="maximum_fidelity"',
            'value="faster_draft"',
            'value="custom"',
        )
        for snippet in required:
            self.assertIn(snippet, HTML)
        modal_start = HTML.index('id="newProjectModal"')
        modal_end = HTML.index('id="templateEditorModal"', modal_start)
        modal_html = HTML[modal_start:modal_end]
        for prohibited in (
            "model name",
            "cache location",
            "context length",
            "prompt template",
        ):
            self.assertNotIn(prohibited, modal_html.casefold())

    def test_source_inspection_and_creation_are_separate_transactions(self) -> None:
        self.assertIn("/api/projects/inspect-source", SHELL_JS)
        self.assertIn("/api/projects'", SHELL_JS)
        self.assertIn('formData.append(\'book_title\'', SHELL_JS)
        self.assertIn('formData.append(\'author\'', SHELL_JS)
        self.assertIn("@app.post(\"/api/projects/inspect-source\")", APP_PY)
        self.assertIn("@app.post(\"/api/projects\")", APP_PY)

    def test_invalid_source_replacement_preserves_the_previous_valid_source(self) -> None:
        required = (
            "const previousFile = state.newProject.sourceFile",
            "const previousInspection = state.newProject.inspection",
            "state.newProject.sourceFile = previousFile",
            "state.newProject.inspection = previousInspection",
            "The previously validated source is still attached.",
        )
        for snippet in required:
            self.assertIn(snippet, SHELL_JS)

    def test_extracted_epub_identity_populates_the_editable_fields(self) -> None:
        required = (
            "renderNewProjectInspection(inspection)",
            "projectName.value",
            "bookTitle.value",
            "authorInput.value",
            "sourceLanguageInput.value",
            "outputLanguageInput.value",
            "inspection.cover_data_url",
            "inspection.chapter_count",
        )
        for snippet in required:
            self.assertIn(snippet, SHELL_JS)

    def test_completion_requires_immediate_runtime_activation(self) -> None:
        self.assertIn("if (activation.state === 'current')", SHELL_JS)
        self.assertNotIn("requires an Alexandria restart", SHELL_JS)
        self.assertIn("did not activate", SHELL_JS)
        self.assertIn("project.completed = true", SHELL_JS)
        self.assertIn("submit.textContent = project.completed", SHELL_JS)
        self.assertIn("_activate_runtime_project", APP_PY)
        self.assertIn('"project_switching": "dynamic"', APP_PY)

    def test_compact_modal_retains_a_visible_action_footer(self) -> None:
        compact_start = HTML.index("@media (max-width: 760px)")
        compact_css = HTML[compact_start:]
        self.assertIn(".new-project-footer", compact_css)
        self.assertIn("flex-direction: column", compact_css)
        self.assertIn("max-height: calc(100vh - 16px)", compact_css)


if __name__ == "__main__":
    unittest.main()
