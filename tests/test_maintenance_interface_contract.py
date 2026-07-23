from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAINTENANCE_PATH = ROOT / "app/static/pages/maintenance.js"
STYLE_PATH = ROOT / "app/static/styles/pages/settings_more.css"
APP = (ROOT / "app/app.py").read_text(encoding="utf-8")


class MaintenanceInterfaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = MAINTENANCE_PATH.read_text(encoding="utf-8")
        cls.styles = STYLE_PATH.read_text(encoding="utf-8")

    def test_direct_surface_is_separate_and_read_only_first(self) -> None:
        for phrase in (
            "export async function mount",
            "dataRouteOwner",
            "maintenance-workspace",
            "Read-only health",
            "Promise.allSettled",
            "data-state-region",
        ):
            self.assertIn(phrase, self.source)
        self.assertNotIn("/api/settings", self.source)
        self.assertNotIn("legacy", self.source.casefold())

    def test_status_composes_existing_authoritative_reads(self) -> None:
        for endpoint in (
            "/api/recovery/status",
            "/api/model_registry/status",
            "/api/model_registry/memory",
            "/api/library",
            "/api/projects",
            "/api/migration/status",
            "/api/migration/history",
        ):
            self.assertIn(endpoint, self.source)
        self.assertNotIn("localStorage", self.source)
        self.assertNotIn("sessionStorage", self.source)

    def test_guarded_actions_require_review_and_typed_confirmation(self) -> None:
        for phrase in (
            "/api/model_registry/action",
            "/api/model_registry/memory/release",
            "/api/migration/apply",
            "/api/migration/rollback",
            "APPLY MIGRATION",
            "ROLL BACK",
            "UI.dialog",
            "Review impact",
        ):
            self.assertIn(phrase, self.source)

    def test_normal_renderer_omits_raw_internal_fields(self) -> None:
        for forbidden in (
            "snapshot_path",
            "cache_dir",
            "root_dir",
            "config_path",
            "content_base64",
            "technical_details",
        ):
            self.assertNotIn(forbidden, self.source)
        self.assertLess(
            APP.index('@app.get("/api/migration/history")'),
            APP.index('@app.get("/api/migration/history/{operation_id}")'),
        )

    def test_responsive_styles_and_javascript_are_valid(self) -> None:
        for selector in (
            ".maintenance-summary",
            ".maintenance-section-grid",
            "@media (max-width: 1199px)",
            "@media (max-width: 639px)",
        ):
            self.assertIn(selector, self.styles)
        subprocess.run(
            ["node", "--check", str(MAINTENANCE_PATH)],
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
