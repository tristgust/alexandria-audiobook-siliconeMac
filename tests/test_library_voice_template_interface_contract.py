from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


class LibraryVoiceTemplateInterfaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.library = (STATIC / "pages/library.js").read_text(encoding="utf-8")
        cls.voices = (STATIC / "pages/voices.js").read_text(encoding="utf-8")
        cls.templates = (STATIC / "pages/templates.js").read_text(encoding="utf-8")
        cls.template_components = (
            STATIC / "pages/templates_components.js"
        ).read_text(encoding="utf-8")
        cls.supporting_selection = (
            STATIC / "pages/supporting_selection.js"
        ).read_text(encoding="utf-8")
        cls.flow_styles = (
            STATIC / "styles/pages/project_flow.css"
        ).read_text(encoding="utf-8")
        cls.shell_styles = (STATIC / "styles/shell.css").read_text(encoding="utf-8")

    def test_global_and_project_destinations_keep_one_header_height(self) -> None:
        self.assertRegex(
            self.shell_styles,
            r"\.app-header--global\s*\{[^}]*height:\s*var\(--header-project\)",
        )

    def test_supporting_loading_shell_reserves_final_master_detail_height(self) -> None:
        for page in ("library-page", "voices-page", "templates-page"):
            self.assertIn(f".{page} .content-state", self.flow_styles)
        self.assertIn(
            '.supporting-page .content-state[data-state="loading"]',
            self.flow_styles,
        )
        self.assertIn("grid-template-columns: var(--master-wide)", self.flow_styles)

    def test_library_filters_by_operator_group_instead_of_internal_kind(self) -> None:
        self.assertIn("label: 'Show'", self.library)
        self.assertIn("label: 'Everything'", self.library)
        self.assertIn("artifactGroup(artifact) === chosenKind", self.library)
        self.assertIn("ARTIFACT_GROUP_ORDER", self.library)

    def test_voice_detail_reports_real_capabilities_and_preview_readiness(self) -> None:
        for phrase in (
            "experimental_unaccepted",
            "Production use",
            "Preview sample",
            "Instruction control",
            "data-voice-preview",
            "Not generated",
            "firstCharacterName",
        ):
            self.assertIn(phrase, self.voices)

    def test_voice_selection_remains_visible_after_detail_render(self) -> None:
        for phrase in (
            "replaceWith(detailFor(selected))",
            "String(item === button)",
            "focus({ preventScroll: true })",
            "scrollIntoView({ block: 'nearest' })",
        ):
            self.assertIn(phrase, self.voices)

    def test_supporting_master_lists_share_one_keyboard_selection_model(self) -> None:
        combined = self.library + self.templates + self.supporting_selection
        for phrase in (
            "configureSupportingListbox",
            "role', 'listbox",
            "role', 'option",
            "aria-selected",
            "ArrowDown",
            "ArrowUp",
            "Home",
            "End",
            "restoreSupportingSelectionFocus",
        ):
            self.assertIn(phrase, combined)
        self.assertNotIn("aria-pressed', String(artifact === selected)", self.library)
        self.assertNotIn("aria-pressed', String(template === selected)", self.templates)

    def test_templates_expose_the_backend_management_lifecycle(self) -> None:
        combined = self.templates + self.template_components
        for phrase in (
            "data-template-edit",
            "data-template-duplicate",
            "data-template-default",
            "data-template-delete",
            "api.put",
            "/duplicate",
            "/default",
            "/delete-impact",
            "api.delete",
            "expected_template_fingerprint",
        ):
            self.assertIn(phrase, combined)

    def test_all_modules_are_valid_javascript(self) -> None:
        for relative in (
            "pages/library.js",
            "pages/voices.js",
            "pages/templates.js",
            "pages/templates_components.js",
            "pages/supporting_selection.js",
        ):
            with self.subTest(module=relative):
                subprocess.run(
                    ["node", "--check", str(STATIC / relative)],
                    check=True,
                    capture_output=True,
                    text=True,
                )


if __name__ == "__main__":
    unittest.main()
