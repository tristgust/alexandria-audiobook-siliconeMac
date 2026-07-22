from __future__ import annotations

import unittest
from pathlib import Path


class ModelCacheInterfaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "static"
            / "index.html"
        ).read_text(encoding="utf-8")

    def test_setup_contains_one_progressively_disclosed_inventory(self) -> None:
        for element_id in (
            "setup-model-cache",
            "model-cache-panel",
            "model-cache-badge",
            "btn-model-cache-refresh",
            "btn-model-cache-download-required",
            "model-cache-error",
            "model-cache-summary",
            "model-cache-location",
            "model-cache-progress",
            "model-cache-progress-label",
            "model-cache-progress-count",
            "model-cache-progress-bar",
            "model-cache-list",
        ):
            self.assertEqual(
                self.html.count(f'id="{element_id}"'),
                1,
                element_id,
            )
        self.assertIn("Local model cache", self.html)
        self.assertIn("Installed models", self.html)
        self.assertIn("Required and optional pinned snapshots", self.html)

    def test_inventory_renders_required_fields_and_actionable_states(self) -> None:
        start = self.html.index("function modelCachePresentation")
        end = self.html.index("const LLM_PROFILE_STAGE_LABELS", start)
        block = self.html[start:end]
        for phrase in (
            "Cached",
            "Missing",
            "Repair needed",
            "Required",
            "Optional",
            "Pinned revision",
            "Snapshot location",
            "Installed",
            "Estimated",
            "Files inspected",
            "Validation",
            "Download",
            "Repair",
            "model.purpose",
            "model.runtime",
            "model.revision",
            "item.snapshot_path",
            "item.missing_required_paths",
            "item.broken_symlinks",
        ):
            self.assertIn(phrase, block)

    def test_downloads_and_repairs_are_explicit_actions_only(self) -> None:
        for endpoint in (
            "/api/model_registry/status",
            "/api/model_registry/action",
        ):
            self.assertIn(endpoint, self.html)
        self.assertIn("function runModelCacheAction", self.html)
        self.assertIn("data-model-cache-action", self.html)
        self.assertIn("download_required", self.html)
        setup_activation = self.html[
            self.html.index("if (activeTab === 'setup')"):
            self.html.index("} else if (activeTab === 'editor')")
        ]
        self.assertIn("loadModelRegistryStatus({ silent: true })", setup_activation)
        self.assertNotIn("runModelCacheAction", setup_activation)
        self.assertNotIn("/api/model_registry/action", setup_activation)

    def test_background_progress_and_failure_are_visible(self) -> None:
        for phrase in (
            "operation.current_operation",
            "operation.current_model_key",
            "operation.completed_count",
            "operation.total_count",
            "operation.error",
            "startModelCachePolling",
            "stopModelCachePolling",
            "aria-valuenow",
        ):
            self.assertIn(phrase, self.html)

    def test_setup_navigation_stops_cache_polling_outside_setup(self) -> None:
        activation = self.html[
            self.html.index("function activateWorkspaceTab"):
            self.html.index("async function refreshCharactersWorkspace")
        ]
        self.assertIn("stopModelCachePolling();", activation)
        self.assertIn("loadModelRegistryStatus({ silent: true });", activation)


if __name__ == "__main__":
    unittest.main()
