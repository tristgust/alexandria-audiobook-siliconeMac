from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HARNESS = (
    ROOT / "tests" / "phase18c_roster_ui_harness.js"
)
REPORT_PREFIX = "PHASE18C_UI_REPORT="
RUNTIME_FILES = (
    "state.json",
    "generation_state.json",
    "annotated_script.json",
    "annotated_script.meta.json",
    "chunks.json",
    "voice_config.json",
    "character_roster.draft.json",
    "character_roster.json",
    "character_roster_state.json",
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


class CharacterRosterUiBehaviorTests(unittest.TestCase):
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
                "Phase 18C roster UI harness failed.\n"
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
                "Phase 18C roster UI harness did not emit exactly "
                f"one report line. Output:\n{result.stdout}"
            )

        cls.report: dict[str, Any] = json.loads(
            report_lines[0][len(REPORT_PREFIX):]
        )

    def assert_check(self, name: str) -> None:
        check = self.report["checks"].get(name)
        self.assertIsNotNone(
            check,
            f"Missing UI check: {name}",
        )
        self.assertTrue(
            check["ok"],
            f"UI check failed: {name}: {check}",
        )

    def test_actual_source_functions_and_handlers_execute(self):
        self.assert_check("actual_source_extraction")
        self.assert_check("initial_refresh_present")
        self.assertEqual(self.report["extractedFunctions"], 7)
        self.assertEqual(self.report["extractedHandlers"], 6)

    def test_rendering_is_safe_and_distinguishes_states(self):
        for name in (
            "empty_source_ready_state",
            "source_content_is_escaped",
            "draft_status_and_actions",
            "approved_is_read_only",
            "duplicate_comparison_actions",
            "running_status_controls",
        ):
            self.assert_check(name)

    def test_refresh_and_polling_lifecycle(self):
        for name in (
            "refresh_fetches_status_and_active_artifact",
            "polling_starts",
            "polling_stops",
            "polling_error_is_safe_and_cleans_timer",
        ):
            self.assert_check(name)

    def test_every_review_action_posts_current_fingerprint(self):
        for action in (
            "confirm",
            "rename",
            "add_alias",
            "reject_alias",
            "mark_unresolved",
            "keep_separate",
            "merge",
            "exclude",
        ):
            self.assert_check(f"action_{action}")
        self.assert_check("stale_action_refreshes")

    def test_discovery_cancel_discard_and_approval_controls(self):
        for name in (
            "rediscovery_cancel",
            "rediscovery_confirmation_and_payload",
            "cancel_control",
            "discard_cancel",
            "discard_success",
            "approval_confirmation_and_acknowledgment",
            "stale_approval_refreshes",
        ):
            self.assert_check(name)

    def test_vm_harness_does_not_modify_runtime_files(self):
        self.assertEqual(
            self.before_hashes,
            self.after_hashes,
            "The Phase 18C Node VM harness modified live runtime files.",
        )


if __name__ == "__main__":
    unittest.main()
