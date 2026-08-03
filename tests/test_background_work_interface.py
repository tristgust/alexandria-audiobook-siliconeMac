from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from more_tools import MORE_TOOL_DEFINITIONS


ROOT = Path(__file__).resolve().parents[1]


class BackgroundWorkInterfaceTests(unittest.TestCase):
    def test_more_directory_and_router_expose_one_background_work_destination(self) -> None:
        definitions = [
            item for item in MORE_TOOL_DEFINITIONS if item["tool"] == "background-work"
        ]
        self.assertEqual(len(definitions), 1)
        self.assertEqual(definitions[0]["title"], "Background Work")
        self.assertFalse(definitions[0]["mutates_project"])
        for relative, required in (
            ("app/static/app_shell.js", "'/static/specialists/background_work.js'"),
            ("app/static/navigation_routes.js", "'more/background-work'"),
            ("app/static/pages/more.js", "'background-work': 'more/background-work'"),
        ):
            with self.subTest(relative=relative):
                self.assertIn(required, (ROOT / relative).read_text(encoding="utf-8"))

    def test_background_work_surface_is_truthful_accessible_and_cancellable(self) -> None:
        source = (ROOT / "app/static/specialists/background_work.js").read_text(
            encoding="utf-8"
        )
        for required in (
            "Queued, running, recovering, cancelling",
            "/api/background-work?history_limit=20",
            "/api/background-work/${encodeURIComponent(job.job_id)}/cancel",
            "data-background-work-cancel",
            "aria-live",
            "Current work",
            "Recent history",
            "The bounded queue accepts up to",
            "next safe cancellation boundary",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)
        for forbidden in ("owner_token", "publication_token", "secret_input"):
            self.assertNotIn(forbidden, source)

    def test_javascript_modules_parse(self) -> None:
        for relative in (
            "app/static/specialists/background_work.js",
            "app/static/app_shell.js",
            "app/static/navigation_routes.js",
            "app/static/pages/more.js",
        ):
            with self.subTest(relative=relative):
                result = subprocess.run(
                    ["node", "--check", str(ROOT / relative)],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
