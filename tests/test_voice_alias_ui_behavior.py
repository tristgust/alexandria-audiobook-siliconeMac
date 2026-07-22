from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "voice_alias_ui_harness.js"
REPORT_PREFIX = "VOICE_ALIAS_UI_REPORT="
RUNTIME_FILES = (
    "state.json",
    "generation_state.json",
    "annotated_script.json",
    "annotated_script.meta.json",
    "chunks.json",
    "voice_config.json",
    "app/config.json",
)


def digest(path: Path) -> str:
    if not path.exists():
        return "<absent>"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runtime_hashes() -> dict[str, str]:
    return {
        relative: digest(ROOT / relative)
        for relative in RUNTIME_FILES
    }


class VoiceAliasUiBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.before_hashes = runtime_hashes()
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
        cls.after_hashes = runtime_hashes()
        if result.returncode != 0:
            raise AssertionError(
                "Voice alias UI harness failed.\n"
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
                "Voice alias UI harness did not emit exactly one report line.\n"
                f"STDOUT:\n{result.stdout}"
            )
        cls.report: dict[str, Any] = json.loads(
            report_lines[0][len(REPORT_PREFIX):]
        )

    def assert_check(self, name: str) -> None:
        check = self.report["checks"].get(name)
        self.assertIsNotNone(check, f"Missing UI check: {name}")
        self.assertTrue(check["ok"], f"UI check failed: {name}: {check}")

    def test_actual_source_functions_execute(self) -> None:
        self.assert_check("actual_source_extraction")

    def test_alias_payload_does_not_copy_dormant_configuration(self) -> None:
        self.assert_check("alias_posts_only_alias_field")
        self.assert_check("alias_does_not_read_dormant_controls")

    def test_independent_payload_explicitly_clears_alias(self) -> None:
        self.assert_check("independent_update_explicitly_clears_alias")

    def test_target_diagnostics_update_live_dependents(self) -> None:
        self.assert_check("target_diagnostics_propagate_to_live_alias_summary")

    def test_vm_harness_does_not_modify_runtime_files(self) -> None:
        self.assertEqual(self.after_hashes, self.before_hashes)


if __name__ == "__main__":
    unittest.main()
