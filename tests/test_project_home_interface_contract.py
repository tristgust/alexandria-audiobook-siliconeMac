from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
PAGES = STATIC / "pages"
PAGE_NAMES = (
    "projects",
    "new_project",
    "script",
    "library",
    "voices",
    "templates",
)
SOURCES = {
    name: (PAGES / f"{name}.js").read_text(encoding="utf-8")
    if (PAGES / f"{name}.js").exists()
    else ""
    for name in PAGE_NAMES
}
SHELL = (STATIC / "app_shell.js").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CSS_PATH = STATIC / "styles" / "pages" / "project_flow.css"
CSS = CSS_PATH.read_text(encoding="utf-8") if CSS_PATH.exists() else ""
SCRIPT_REVIEW_CONTROLLER = (PAGES / "script_review_controller.js").read_text(
    encoding="utf-8"
)
TEMPLATE_OWNERSHIP = "\n".join(
    (PAGES / name).read_text(encoding="utf-8")
    for name in (
        "templates.js",
        "template_actions.js",
        "template_editor.js",
    )
)
APP = (ROOT / "app" / "app.py").read_text(encoding="utf-8")


class ProjectHomeInterfaceContractTests(unittest.TestCase):
    def test_shell_mounts_direct_owned_modules_without_embedded_pages(self) -> None:
        for name in ("projects", "script", "library", "voices", "templates"):
            self.assertIn(f"{name}: '/static/pages/{name}.js'", SHELL)
            self.assertIn("export async function mount", SOURCES[name])
        self.assertIn(
            "export function createNewProjectController",
            SOURCES["new_project"],
        )
        self.assertNotIn("project-home-workspace", HTML)
        self.assertNotIn("script-review-workspace", HTML)

    def test_projects_is_global_and_new_project_stays_within_projects(self) -> None:
        source = SOURCES["projects"] + SOURCES["new_project"]
        for marker in (
            "/api/projects",
            "/api/projects/inspect-source",
            "dataProjectOpen",
            "dataNewProjectOpen",
            "dataNewProject",
            "Project Home",
            "New Project",
        ):
            self.assertIn(marker, source)
        self.assertIn("createNewProjectController", SOURCES["projects"])
        self.assertNotIn("shell.header.set", SOURCES["projects"])
        self.assertNotIn("new-project-stepper", source)
        self.assertNotIn("new-project-section-number", source)

    def test_new_project_preserves_valid_source_and_uses_two_transactions(self) -> None:
        source = SOURCES["new_project"]
        for marker in (
            "const previousFile",
            "const previousInspection",
            "state.sourceFile = previousFile",
            "state.inspection = previousInspection",
            "previously validated source is still attached",
            "new FormData()",
            "formData.append('book_title'",
            "formData.append('author'",
            "activation.state === 'current'",
        ):
            self.assertIn(marker, source)
        for value in (
            "local",
            "chatgpt_task_bundle",
            "import_existing_script",
            "standard",
            "maximum_fidelity",
            "faster_draft",
            "custom",
        ):
            self.assertIn(value, source)
        self.assertIn('@app.post("/api/projects/inspect-source")', APP)
        self.assertIn('@app.post("/api/projects")', APP)

    def test_script_lifecycle_keeps_cast_as_the_only_next_stage(self) -> None:
        source = SOURCES["script"]
        for marker in (
            "/api/project_flow/status",
            "/api/script_lifecycle/status",
            "/api/annotated_script",
            "/api/script_lifecycle/accept",
            "dataScriptContinue",
            "Approve Script",
            "Review required",
        ):
            self.assertIn(marker, source)
        self.assertIn("./script_review_controller.js", source)
        self.assertIn("No Script entries", SCRIPT_REVIEW_CONTROLLER)
        self.assertIn("shell.routes.routeForPath('cast'", source)
        self.assertNotIn("shell.routes.routeForPath('produce'", source)
        self.assertNotIn("shell.routes.routeForPath('export'", source)

    def test_supporting_pages_use_existing_authoritative_boundaries(self) -> None:
        contracts = {
            "library": ("/api/library", "Open Script", "Open Produce", "Open Export"),
            "voices": (
                "/api/voice-library",
                "Voices is read-only",
                "Assignment happens only in Cast",
            ),
            "templates": ("/api/templates", "Use Template", "New Template"),
        }
        for name, markers in contracts.items():
            source = TEMPLATE_OWNERSHIP if name == "templates" else SOURCES[name]
            with self.subTest(page=name):
                for marker in markers:
                    self.assertIn(marker, source)
        voices = SOURCES["voices"]
        for prohibited in (
            "assignVoice",
            "saveVoice",
            "voice_config",
            "assignment_mutation",
        ):
            self.assertNotIn(prohibited, voices)

    def test_all_modules_own_loading_empty_error_success_and_dense_states(self) -> None:
        for name in ("projects", "script", "library", "voices", "templates"):
            source = SOURCES[name]
            with self.subTest(page=name):
                for state in ("loading", "empty", "error", "success", "dense"):
                    self.assertIn(f"'{state}'", source)

    def test_modules_use_safe_dom_abort_and_idempotent_cleanup(self) -> None:
        for name, source in SOURCES.items():
            with self.subTest(page=name):
                self.assertIn("textContent", source)
                self.assertIn("signal", source)
                for prohibited in (
                    "innerHTML",
                    "insertAdjacentHTML",
                    "document.getElementById",
                    "activateWorkspaceTab",
                    "VoiceCardBridge",
                    "canonical_interface",
                    "canonical_pages",
                    "data-tab-panel",
                ):
                    self.assertNotIn(prohibited, source)

    def test_page_css_uses_foundation_tokens_and_no_private_visual_system(self) -> None:
        self.assertTrue(CSS_PATH.exists())
        for selector in (
            ".project-flow",
            ".project-list",
            ".new-project",
            ".script-review",
            ".supporting-page",
        ):
            self.assertIn(selector, CSS)
        for prohibited in ("#", "rgb(", "linear-gradient", "radial-gradient"):
            self.assertNotIn(prohibited, CSS)
        self.assertIn("var(--color-", CSS)
        self.assertIn('var(--space-', CSS)

    def test_owned_sources_are_valid_and_bounded(self) -> None:
        for name in PAGE_NAMES:
            path = PAGES / f"{name}.js"
            completed = subprocess.run(
                ["node", "--check", str(path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            pure_lines = [
                line
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("//")
            ]
            self.assertLessEqual(len(pure_lines), 250, path)


if __name__ == "__main__":
    unittest.main()
