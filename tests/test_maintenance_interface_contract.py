from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
SHELL = (ROOT / "app" / "static" / "canonical_interface.js").read_text(encoding="utf-8")
CSS = (ROOT / "app" / "static" / "canonical_pages.css").read_text(encoding="utf-8")
APP = (ROOT / "app" / "app.py").read_text(encoding="utf-8")


class MaintenanceInterfaceContractTests(unittest.TestCase):
    def maintenance_html(self) -> str:
        start = HTML.index('id="canonical-maintenance-workspace"')
        end = HTML.index('<details class="recovery-center"', start)
        return HTML[start:end]

    def maintenance_shell(self) -> str:
        start = SHELL.index("function maintenanceStateIcon")
        end = SHELL.index("function applyMoreRouteContext", start)
        return SHELL[start:end]

    def test_canonical_surface_is_read_only_first_and_separate_from_settings(self) -> None:
        maintenance = self.maintenance_html()
        for identifier in (
            "canonical-maintenance-workspace",
            "maintenance-refresh",
            "maintenance-loading",
            "maintenance-load-error",
            "maintenance-health-list",
            "maintenance-model-list",
            "maintenance-memory-controls",
            "maintenance-memory-summary",
            "maintenance-memory-headroom",
            "maintenance-memory-idle",
            "maintenance-memory-retry",
            "maintenance-memory-save",
            "maintenance-memory-release",
            "maintenance-library-list",
            "maintenance-project-list",
            "maintenance-history-list",
            "maintenance-migration-summary",
            "maintenance-impact-dialog",
        ):
            self.assertEqual(HTML.count(f'id="{identifier}"'), 1)
        self.assertIn('class="maintenance-summary-strip"', maintenance)
        self.assertIn("Read-only health and dependency inspection comes first", maintenance)
        self.assertIn("Downloads, repair, migration, and deletion always require a separate explicit action", maintenance)
        self.assertIn("Impact before action", maintenance)
        self.assertIn("Dry run only", maintenance)
        self.assertIn("canonicalMaintenance.hidden = !maintenance || legacyMaintenance", SHELL)
        self.assertIn("legacySettings.hidden = !legacyMaintenance", SHELL)

    def test_status_load_composes_existing_authoritative_apis(self) -> None:
        maintenance = self.maintenance_shell()
        for endpoint in (
            "/api/recovery/status",
            "/api/model_registry/status",
            "/api/model_registry/memory",
            "/api/library?",
            "/api/projects",
            "/api/migration/status",
            "/api/migration/history",
        ):
            self.assertIn(endpoint, maintenance)
        self.assertIn("Promise.allSettled", maintenance)
        self.assertIn("state.maintenance.errors", maintenance)
        self.assertNotIn("localStorage", maintenance)
        self.assertNotIn("sessionStorage", maintenance)

    def test_guarded_actions_delegate_to_existing_fingerprinted_endpoints(self) -> None:
        maintenance = self.maintenance_shell()
        for endpoint in (
            "/api/library/artifacts/${encodeURIComponent(impact.artifact_id)}",
            "/api/projects/${encodeURIComponent(impact.project_id)}/delete",
            "/api/migration/apply",
            "/api/migration/rollback",
            "/api/model_registry/action",
            "/api/model_registry/action/cancel",
            "/api/model_registry/memory/policy",
            "/api/model_registry/memory/release",
        ):
            self.assertIn(endpoint, maintenance)
        for field in (
            "expected_inventory_fingerprint",
            "expected_artifact_fingerprint",
            "expected_catalog_fingerprint",
            "expected_project_fingerprint",
            "plan_fingerprint",
            "confirm_dependencies: true",
        ):
            self.assertIn(field, maintenance)
        for confirmation in (
            "impact.confirm_name",
            "impact.project_id",
            "'APPLY MIGRATION'",
            "'ROLL BACK'",
        ):
            self.assertIn(confirmation, maintenance)
        self.assertIn("state.maintenance.impactTrigger?.focus?.({ preventScroll: true })", maintenance)
        self.assertIn("dialog.showModal()", maintenance)
        self.assertIn("const libraryImpactId = trigger?.dataset?.maintenanceLibraryImpact", maintenance)
        self.assertIn("restored?.focus?.({ preventScroll: true })", maintenance)
        self.assertIn("}, 50)", maintenance)

    def test_dependency_and_project_actions_link_to_native_destinations(self) -> None:
        maintenance = self.maintenance_shell()
        for phrase in (
            "artifact.native_route",
            "data-maintenance-artifact-open",
            "data-maintenance-project-open",
            "maintenanceStageRoute(stage)",
            "window.AlexandriaNavigation?.navigate",
            "return: state.route.hash",
        ):
            self.assertIn(phrase, maintenance)

    def test_normal_surface_does_not_render_raw_paths_or_fingerprints(self) -> None:
        maintenance = self.maintenance_shell()
        for forbidden in (
            "snapshot_path",
            "cache_dir",
            "root_dir",
            "config_path",
            "technical_details",
            "content_base64",
        ):
            self.assertNotIn(forbidden, maintenance)
        self.assertIn("missing_required_paths", maintenance)
        self.assertIn("required_by_default", maintenance)

    def test_migration_history_route_precedes_parameterized_operation_route(self) -> None:
        inventory = APP.index('@app.get("/api/migration/history")')
        operation = APP.index('@app.get("/api/migration/history/{operation_id}")')
        self.assertLess(inventory, operation)
        self.assertIn("get_migration_history_payload", APP)

    def test_compact_layout_and_native_dialog_are_defined(self) -> None:
        for phrase in (
            ".canonical-maintenance-workspace",
            ".maintenance-summary-strip",
            ".maintenance-dependency-layout",
            ".maintenance-impact-dialog",
            "@media (max-width: 860px)",
            "@media (max-width: 560px)",
        ):
            self.assertIn(phrase, CSS)

    def test_javascript_is_valid(self) -> None:
        subprocess.run(
            ["node", "--check", str(ROOT / "app/static/canonical_interface.js")],
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
