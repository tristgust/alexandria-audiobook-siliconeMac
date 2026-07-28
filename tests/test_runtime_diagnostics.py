from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
PAGE_STYLES = (
    "project_flow.css",
    "cast.css",
    "settings_more.css",
    "produce_export.css",
)


class RuntimeDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app_module.app)

    def test_runtime_status_reports_current_source_and_request_id(self) -> None:
        response = self.client.get("/api/runtime_status")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["restart_required"])
        self.assertEqual(payload["changed_sources"], [])
        self.assertEqual(payload["process_id"], app_module.os.getpid())
        self.assertRegex(payload["started_at"], r"Z$")
        self.assertRegex(
            response.headers["X-Alexandria-Request-Id"],
            r"^[0-9a-f]{12}$",
        )

    def test_runtime_status_identifies_source_changed_after_start(self) -> None:
        app_path = Path(app_module.__file__).resolve()
        stale_snapshot = {app_path: 0}
        with patch.object(
            app_module,
            "RUNTIME_SOURCE_SNAPSHOT",
            stale_snapshot,
        ):
            response = self.client.get("/api/runtime_status")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["restart_required"])
        self.assertIn("app/app.py", payload["changed_sources"])

    def test_runtime_source_graph_covers_canonical_static_assets_deterministically(
        self,
    ) -> None:
        expected = tuple(
            sorted(
                (
                    STATIC / "index.html",
                    STATIC / "navigation_routes.js",
                    STATIC / "api_client.js",
                    STATIC / "shell_chrome.js",
                    STATIC / "app_shell.js",
                    *STATIC.glob("components/*.js"),
                    *STATIC.glob("pages/*.js"),
                    *STATIC.glob("specialists/*.js"),
                    *STATIC.glob("styles/**/*.css"),
                ),
                key=lambda path: path.as_posix(),
            )
        )
        observed = tuple(
            path
            for path in app_module.RUNTIME_SOURCE_PATHS
            if path.is_relative_to(STATIC)
        )
        self.assertEqual(observed, expected)
        self.assertNotIn(STATIC / "canonical_interface.js", observed)
        self.assertNotIn(STATIC / "canonical_pages.css", observed)
        self.assertNotIn(STATIC / "primitive_showcase.html", observed)
        self.assertNotIn(STATIC / "reference_bank_ui.js", observed)

    def test_index_preloads_page_styles_in_reference_order(self) -> None:
        source = (STATIC / "index.html").read_text(encoding="utf-8")
        observed = tuple(
            re.findall(
                r'<link rel="stylesheet" href="/static/styles/pages/([^"]+)"',
                source,
            )
        )
        self.assertEqual(observed, PAGE_STYLES)
        self.assertIn('data-page-style="cast"', source)
        self.assertIn('data-page-style="produce-export"', source)

    def test_api_error_is_logged_with_request_metadata(self) -> None:
        with patch.object(app_module.logger, "warning") as warning:
            response = self.client.get("/api/projects/definitely-missing")
        self.assertEqual(response.status_code, 404)
        self.assertIn("X-Alexandria-Request-Id", response.headers)
        warning.assert_called()
        rendered = " ".join(str(item) for item in warning.call_args.args)
        self.assertIn("api_request", rendered)
        self.assertIn("/api/projects/definitely-missing", rendered)
        self.assertIn('"status": 404', rendered)


if __name__ == "__main__":
    unittest.main()
