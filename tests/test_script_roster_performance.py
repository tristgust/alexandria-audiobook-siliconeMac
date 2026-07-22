from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "script_roster_performance_harness.py"
REPORT_PREFIX = "SCRIPT_ROSTER_PERFORMANCE_REPORT="


class ScriptRosterPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        result = subprocess.run(
            [
                str(ROOT / "app" / "env" / "bin" / "python"),
                str(HARNESS),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if result.returncode != 0:
            raise AssertionError(
                "Script/roster performance harness failed.\n"
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
                "Script/roster performance harness did not emit exactly one report.\n"
                f"STDOUT:\n{result.stdout}"
            )
        cls.report: dict[str, Any] = json.loads(
            reports[0][len(REPORT_PREFIX):]
        )

    def test_representative_workloads_are_large_enough(self):
        self.assertGreaterEqual(self.report["script_entry_count"], 6_000)
        self.assertGreaterEqual(self.report["roster_target_item_count"], 1_500)
        self.assertGreaterEqual(self.report["roster_actual_item_count"], 1_500)
        self.assertGreater(self.report["roster_items_per_payload"], 0)
        self.assertGreater(self.report["roster_payload_count"], 0)
        self.assertEqual(
            self.report["roster_actual_item_count"],
            self.report["roster_items_per_payload"]
            * self.report["roster_payload_count"],
        )
        self.assertGreater(self.report["script_chunk_count"], 0)

    def test_all_measured_boundaries_stay_under_regression_limits(self):
        self.assertTrue(self.report["passed"], self.report["failures"])
        self.assertEqual(self.report["failures"], {})
        self.assertEqual(
            set(self.report["metrics_ms"]),
            set(self.report["limits_ms"]),
        )

    def test_measurements_are_finite_and_nonnegative(self):
        for name, value in self.report["metrics_ms"].items():
            with self.subTest(name=name):
                self.assertIsInstance(value, (int, float))
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, self.report["limits_ms"][name])


if __name__ == "__main__":
    unittest.main()
