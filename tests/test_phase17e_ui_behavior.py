from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "phase17e_ui_harness.js"
REPORT_PREFIX = "PHASE17E_UI_REPORT="
RUNTIME_FILES = (
    "state.json",
    "generation_state.json",
    "annotated_script.json",
    "annotated_script.meta.json",
    "chunks.json",
    "voice_config.json",
    "app/config.json",
)


def _digest(path: Path) -> str:
    if not path.exists():
        return "<absent>"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _runtime_hashes() -> dict[str, str]:
    return {
        relative: _digest(ROOT / relative)
        for relative in RUNTIME_FILES
    }


class Phase17EUiBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.before_hashes = _runtime_hashes()
        result = subprocess.run(
            [
                "node",
                str(HARNESS),
                "--repo-root",
                str(ROOT),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        cls.after_hashes = _runtime_hashes()
        cls.stdout = result.stdout
        cls.stderr = result.stderr

        if result.returncode != 0:
            raise AssertionError(
                "Phase 17E UI harness failed.\n"
                f"STDOUT:\n{result.stdout}\n"
                f"STDERR:\n{result.stderr}"
            )

        report_lines = [
            line
            for line in result.stdout.splitlines()
            if line.startswith(REPORT_PREFIX)
        ]
        if len(report_lines) != 1:
            raise AssertionError(
                "Phase 17E UI harness did not emit exactly one "
                f"report line. Output:\n{result.stdout}"
            )

        cls.report: dict[str, Any] = json.loads(
            report_lines[0][len(REPORT_PREFIX):]
        )

    def assert_check(self, name: str) -> None:
        check = self.report["checks"].get(name)
        self.assertIsNotNone(check, f"Missing UI check: {name}")
        self.assertTrue(
            check["ok"],
            f"UI check failed: {name}: {check}",
        )

    def test_actual_source_functions_and_handlers_are_executed(self):
        self.assert_check("actual_source_extraction")
        self.assert_check("initial_status_fetch_present")
        self.assertEqual(self.report["extractedFunctions"], 9)
        self.assertEqual(self.report["extractedHandlers"], 3)

    def test_checkpoint_and_running_states_render_correctly(self):
        for name in (
            "render_no_checkpoint",
            "render_resume_checkpoint",
            "render_finalization_checkpoint",
            "render_incompatible_blocked",
            "render_unknown_blocked",
            "render_corrupt_blocked",
            "render_invalid_blocked",
            "render_running_state",
        ):
            self.assert_check(name)

    def test_provenance_states_are_safe_and_complete(self):
        self.assert_check("valid_provenance_rendering")
        self.assert_check("imported_provenance_persists_after_reload")
        self.assert_check("invalid_metadata_not_trusted")
        self.assert_check("all_result_presentations")

    def test_polling_starts_updates_stops_and_cleans_up(self):
        self.assert_check("initial_refresh_behavior")
        self.assert_check("polling_start")
        self.assert_check("polling_update")
        self.assert_check("polling_stop")
        self.assert_check(
            "status_error_safe_output_and_timer_cleanup"
        )

    def test_discard_confirmation_success_and_failure(self):
        self.assert_check("discard_cancel")
        self.assert_check("discard_success")
        self.assert_check("discard_failure")

    def test_upload_and_generate_handlers_refresh_safely(self):
        for name in (
            "upload_refresh_success",
            "upload_error_escaped",
            "generate_requires_source",
            "generate_new_feedback",
            "generate_resume_feedback",
            "generate_finalize_feedback",
            "generate_error_escaped_and_refreshed",
        ):
            self.assert_check(name)

    def test_saved_script_load_refreshes_provenance(self):
        self.assert_check("saved_script_refresh")

    def test_ui_harness_does_not_modify_runtime_files(self):
        self.assertEqual(
            self.before_hashes,
            self.after_hashes,
            "The Node VM UI harness modified live runtime files.",
        )


if __name__ == "__main__":
    unittest.main()
