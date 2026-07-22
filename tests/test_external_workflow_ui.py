from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "external_workflow_ui_harness.js"
REPORT_PREFIX = "EXTERNAL_WORKFLOW_UI_REPORT="
RUNTIME_FILES = (
    "state.json",
    "generation_state.json",
    "annotated_script.json",
    "annotated_script.meta.json",
    "chunks.json",
    "voice_config.json",
    "audio_validity.json",
    "app/config.json",
)


def _digest(path: Path) -> str:
    if not path.exists():
        return "<absent>"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _runtime_hashes() -> dict[str, str]:
    return {relative: _digest(ROOT / relative) for relative in RUNTIME_FILES}


class ExternalWorkflowUIBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.before_hashes = _runtime_hashes()
        result = subprocess.run(
            ["node", str(HARNESS), "--repo-root", str(ROOT)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        cls.after_hashes = _runtime_hashes()
        if result.returncode != 0:
            raise AssertionError(
                "Task Bundle UI harness failed.\n"
                f"STDOUT:\n{result.stdout}\n"
                f"STDERR:\n{result.stderr}"
            )
        reports = [
            line
            for line in result.stdout.splitlines()
            if line.startswith(REPORT_PREFIX)
        ]
        if len(reports) != 1:
            raise AssertionError(
                "Task Bundle UI harness did not emit exactly one report.\n"
                f"STDOUT:\n{result.stdout}"
            )
        cls.report: dict[str, Any] = json.loads(
            reports[0][len(REPORT_PREFIX):]
        )

    def assert_check(self, name: str) -> None:
        check = self.report["checks"].get(name)
        self.assertIsNotNone(check, f"Missing Task Bundle UI check: {name}")
        self.assertTrue(check["ok"], f"Task Bundle UI check failed: {name}: {check}")

    def test_actual_source_functions_are_executed(self):
        self.assert_check("actual_source_extraction")
        self.assertEqual(self.report["extractedFunctions"], 14)

    def test_registry_and_scope_drive_export(self):
        self.assert_check("registry_populates_task_chooser")
        self.assert_check("default_task_explains_native_destination")
        self.assert_check("target_scope_is_registry_driven")
        self.assert_check("export_uses_task_and_scope_without_code")
        self.assert_check("export_directs_user_to_attach_zip")

    def test_completed_task_import_routes_without_approval(self):
        self.assert_check("completed_task_import_uses_one_file_without_identifier")
        self.assert_check("persona_import_opens_native_review")
        self.assert_check("import_does_not_approve_or_assign")
        self.assert_check("script_result_uses_existing_candidate_review")

    def test_fallback_and_reconciliation_states_remain_actionable(self):
        self.assert_check("json_fallback_requests_original_zip_not_code")
        self.assert_check("blocked_result_retains_clear_review_action")
        self.assert_check("persona_catalog_shows_current_imported_comparison")
        self.assert_check("persona_catalog_applies_only_selected_replacements")

    def test_ui_harness_does_not_modify_runtime_files(self):
        self.assertEqual(
            self.before_hashes,
            self.after_hashes,
            "The Task Bundle Node VM harness modified live runtime files.",
        )


if __name__ == "__main__":
    unittest.main()
