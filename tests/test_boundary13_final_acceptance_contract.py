from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
SHELL = (ROOT / "app" / "static" / "canonical_interface.js").read_text(
    encoding="utf-8"
)
BROWSER = (ROOT / "tests" / "interface_browser_audit.py").read_text(
    encoding="utf-8"
)
CDP = (ROOT / "tests" / "interface_cdp_audit.js").read_text(encoding="utf-8")
FINAL = (ROOT / "tests" / "boundary13_final_acceptance.js").read_text(
    encoding="utf-8"
)


class Boundary13FinalAcceptanceContractTests(unittest.TestCase):
    def test_more_navigation_exposes_current_page_semantics(self) -> None:
        self.assertIn("toolsToggle.setAttribute('aria-current', 'page')", HTML)
        self.assertIn("toolsToggle.removeAttribute('aria-current')", HTML)

    def test_voice_lab_test_controls_have_explicit_labels(self) -> None:
        for identifier in (
            "lora-test-adapter",
            "lora-test-text",
            "lora-test-instruct",
        ):
            self.assertIn(f'for="{identifier}"', HTML)
            self.assertEqual(HTML.count(f'id="{identifier}"'), 1)

    def test_library_and_template_listboxes_use_roving_focus(self) -> None:
        for phrase in (
            'id="library-artifact-${escapeHtml(artifact.artifact_id)}"',
            'tabindex="${selected ? \'0\' : \'-1\'}" data-library-artifact',
            "list.setAttribute(\n                    'aria-activedescendant',\n                    `library-artifact-${state.library.selectedId}`",
            "element('library-artifact-list')?.addEventListener('keydown'",
            "const keys = ['ArrowDown', 'ArrowUp', 'Home', 'End']",
            'id="template-row-${escapeHtml(template.id)}"',
            'tabindex="${selected ? \'0\' : \'-1\'}" data-template-id',
            "list.setAttribute(\n                    'aria-activedescendant',\n                    `template-row-${state.templates.selectedId}`",
            "element('template-list')?.addEventListener('keydown'",
        ):
            self.assertIn(phrase, SHELL)

    def test_supporting_pages_do_not_compete_with_shell_primary_actions(self) -> None:
        self.assertNotIn(
            'class="btn btn-primary project-open-action"',
            SHELL,
        )
        self.assertNotIn(
            'class="btn btn-primary" data-template-use',
            SHELL,
        )
        self.assertIn(
            'class="btn btn-outline-secondary project-open-action"',
            SHELL,
        )
        self.assertIn(
            'class="btn btn-outline-secondary" data-template-use',
            SHELL,
        )

    def test_final_browser_mode_is_read_only_and_cross_surface(self) -> None:
        for phrase in (
            'choices=("legacy", "shell", "boundary12", "boundary13", "boundary13-final")',
            'if mode == "boundary13-final":',
            '"startup_and_read_unchanged"',
            '"browser_unchanged"',
            '"api_unchanged"',
            'read_only_post_paths',
            'inspectBoundary13FinalAcceptance',
            'Accessibility.getFullAXTree',
            'boundary13-final-localization-compact.png',
            'boundary13-final-supporting-wide.png',
            "{ alias: '#project-recovery'",
            "{ alias: '#models'",
            "{ alias: '#help'",
            "{ alias: '#training'",
        ):
            self.assertIn(phrase, BROWSER + CDP + FINAL)

    def test_final_browser_modules_have_valid_syntax(self) -> None:
        for relative in (
            "app/static/canonical_interface.js",
            "tests/interface_cdp_audit.js",
            "tests/boundary13_final_acceptance.js",
        ):
            subprocess.run(
                ["node", "--check", str(ROOT / relative)],
                check=True,
                capture_output=True,
                text=True,
            )


if __name__ == "__main__":
    unittest.main()
