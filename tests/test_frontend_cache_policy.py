from __future__ import annotations

import re
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import app as app_module


ROOT = Path(__file__).resolve().parents[1]
SHELL_PATH = ROOT / "app" / "static" / "app_shell.js"


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
        self.assertTrue(
            all(f"?v={app_module.STATIC_ASSET_VERSION}" in url for url in static_urls),
            static_urls,
        )
        self.assertIn(
            f"/static/app_shell.js?v={app_module.STATIC_ASSET_VERSION}",
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

    def test_lazy_page_modules_inherit_the_shell_asset_version(self) -> None:
        source = SHELL_PATH.read_text(encoding="utf-8")
        self.assertIn("new URL(import.meta.url).searchParams.get('v')", source)
        self.assertIn("versionedModule(PAGE_MODULES[effectiveRoute.path])", source)


if __name__ == "__main__":
    unittest.main()
