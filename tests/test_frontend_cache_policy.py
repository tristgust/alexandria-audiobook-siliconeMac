from __future__ import annotations

import re
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import app as app_module


ROOT = Path(__file__).resolve().parents[1]
SHELL_PATH = ROOT / "app" / "static" / "app_shell.js"
SHELL_RUNTIME_STATE_PATH = ROOT / "app" / "static" / "shell_runtime_state.js"


class FrontendCachePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app_module.app)

    def test_index_versions_every_local_static_asset(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("cache-control"),
            "no-cache, no-store, must-revalidate",
        )
        self.assertEqual(response.headers.get("pragma"), "no-cache")
        self.assertEqual(response.headers.get("expires"), "0")
        static_urls = re.findall(r"[\"'](/static/[^\"']+)[\"']", response.text)
        self.assertGreater(len(static_urls), 10)
        current_version = app_module._current_static_asset_version()
        self.assertTrue(
            all(f"?v={current_version}" in url for url in static_urls),
            static_urls,
        )
        self.assertIn(
            f"/static/app_shell.js?v={current_version}",
            response.text,
        )

    def test_static_javascript_is_never_reused_without_revalidation(self) -> None:
        response = self.client.get("/static/pages/settings.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("cache-control"),
            "no-cache, no-store, must-revalidate",
        )
        self.assertEqual(response.headers.get("pragma"), "no-cache")
        self.assertEqual(response.headers.get("expires"), "0")

    def test_runtime_status_exposes_current_and_loaded_asset_versions(self) -> None:
        response = self.client.get("/api/runtime_status")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload["static_asset_version"],
            app_module._current_static_asset_version(),
        )
        self.assertEqual(
            payload["loaded_static_asset_version"],
            app_module.STATIC_ASSET_VERSION,
        )

    def test_lazy_page_modules_inherit_and_enforce_the_shell_asset_version(self) -> None:
        shell_source = SHELL_PATH.read_text(encoding="utf-8")
        runtime_source = SHELL_RUNTIME_STATE_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "new URL(import.meta.url).searchParams.get('v')",
            shell_source,
        )
        self.assertIn(
            "versionedModule(PAGE_MODULES[effectiveRoute.path])",
            shell_source,
        )
        self.assertIn("createShellRuntimeState", shell_source)
        self.assertIn("ensureCurrentAssets", shell_source)
        self.assertIn("current !== assetVersion", runtime_source)
        self.assertIn("location.reload()", runtime_source)


if __name__ == "__main__":
    unittest.main()
