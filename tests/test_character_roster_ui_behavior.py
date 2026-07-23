from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "b19_t06_cast_profile.js"
REPORT_PREFIX = "B19_T06_CAST="


class StandaloneCastBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with tempfile.TemporaryDirectory(prefix="b19-t06-cast-source-") as artifacts:
            result = subprocess.run(
                [
                    "node",
                    str(HARNESS),
                    "--repo-root",
                    str(ROOT),
                    "--artifacts",
                    artifacts,
                    "--source-only",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            if result.returncode != 0:
                raise AssertionError(
                    "Cast source harness failed.\n"
                    f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
                )
            report_line = next(
                (
                    line
                    for line in result.stdout.splitlines()
                    if line.startswith(REPORT_PREFIX)
                ),
                None,
            )
            if report_line is None:
                raise AssertionError(f"Cast harness emitted no report:\n{result.stdout}")
            cls.report = json.loads(report_line[len(REPORT_PREFIX) :])

    def assert_check(self, name: str) -> None:
        check = self.report["checks"].get(name)
        self.assertIsNotNone(check, name)
        self.assertTrue(check["ok"], check)

    def test_structure_and_order_contract(self) -> None:
        for name in (
            "one_roster_one_profile",
            "profile_order",
            "shell_lifecycle",
            "real_api_paths",
        ):
            self.assert_check(name)

    def test_state_and_accessibility_contract(self) -> None:
        for name in (
            "loading_empty_error_retry",
            "dirty_save_retry",
            "keyboard_listbox",
            "safe_dom",
            "return_context",
        ):
            self.assert_check(name)

    def test_persona_contract_is_embedded_not_global(self) -> None:
        for name in (
            "persona_states",
            "persona_opt_in",
            "persona_cleanup",
            "no_legacy_workspace",
        ):
            self.assert_check(name)


if __name__ == "__main__":
    unittest.main()
