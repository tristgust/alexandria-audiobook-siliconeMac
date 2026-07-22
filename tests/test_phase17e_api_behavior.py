from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "phase17e_api_harness.py"
PYTHON = Path(sys.executable)
REPORT_PREFIX = "PHASE17E_REPORT="
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


class Phase17EApiBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.before_hashes = _runtime_hashes()
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"

        result = subprocess.run(
            [
                str(PYTHON),
                str(HARNESS),
                "--repo-root",
                str(ROOT),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )

        cls.harness_stdout = result.stdout
        cls.harness_stderr = result.stderr
        cls.after_hashes = _runtime_hashes()

        if result.returncode != 0:
            raise AssertionError(
                "Phase 17E API harness failed.\n"
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
                "Phase 17E API harness did not emit exactly one "
                f"report line. Output:\n{result.stdout}"
            )

        cls.report: dict[str, Any] = json.loads(
            report_lines[0][len(REPORT_PREFIX):]
        )

    def assert_check(self, name: str) -> None:
        check = self.report["checks"].get(name)
        self.assertIsNotNone(
            check,
            f"Missing harness check: {name}",
        )
        self.assertTrue(
            check["ok"],
            f"Harness check failed: {name}: {check}",
        )

    def test_fixture_imports_real_app_with_prompt_companions(self):
        self.assert_check("fixture_manifest")
        self.assert_check("snapshot_has_multiple_chunks")
        self.assert_check("temporary_root_confinement")
        self.assertTrue(self.report["fixture_destroyed"])

    def test_served_script_controls_and_initial_status(self):
        self.assert_check("served_script_controls")
        self.assert_check("initial_no_checkpoint_state")

    def test_new_resume_and_finalization_routes(self):
        self.assert_check("new_generation_mode")
        self.assert_check("resume_generation_mode")
        self.assert_check("finalization_generation_mode")

    def test_unsafe_checkpoint_states_block_generation(self):
        self.assert_check("incompatible_checkpoint_blocked")
        self.assert_check("corrupt_checkpoint_blocked")
        self.assert_check("invalid_checkpoint_blocked")
        self.assert_check("unknown_checkpoint_blocked")

    def test_running_and_discard_guards(self):
        self.assert_check("running_process_guards")
        self.assert_check("checkpoint_only_discard")

    def test_status_reads_are_file_pure(self):
        self.assert_check("status_read_file_purity")

    def test_upload_selects_isolated_source(self):
        self.assert_check("upload_selection")

    def test_saved_script_provenance_and_annotated_api(self):
        self.assert_check("saved_script_provenance_status")
        self.assert_check("annotated_script_plain_array")

    def test_live_runtime_files_are_unchanged(self):
        self.assertEqual(
            self.before_hashes,
            self.after_hashes,
            msg=(
                "The isolated Phase 17E API harness modified live "
                "runtime files."
            ),
        )


if __name__ == "__main__":
    unittest.main()
