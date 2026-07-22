from __future__ import annotations

import math
import unittest

from interface_phase24d_performance import (
    PHASE24D_RENDER_STATES,
    RenderValue,
    phase24d_render_failures,
)


class ScriptRosterBrowserPerformanceContractTests(unittest.TestCase):
    def reports(
        self,
        render_ms: float = 25.0,
    ) -> dict[str, dict[str, RenderValue]]:
        return {
            state: {"renderMs": render_ms}
            for state in PHASE24D_RENDER_STATES
        }

    def test_all_three_phase24d_reports_accept_valid_boundary_timings(self) -> None:
        for render_ms in (0.0, 250.0):
            with self.subTest(render_ms=render_ms):
                self.assertEqual(
                    phase24d_render_failures(self.reports(render_ms)),
                    [],
                )

    def test_each_phase24d_report_rejects_missing_or_invalid_timing(self) -> None:
        invalid_values = (
            None,
            True,
            False,
            "25",
            math.nan,
            math.inf,
            -math.inf,
            -0.001,
        )
        for state in PHASE24D_RENDER_STATES:
            for render_ms in invalid_values:
                with self.subTest(state=state, render_ms=render_ms):
                    reports = self.reports()
                    reports[state]["renderMs"] = render_ms
                    failures = phase24d_render_failures(reports)
                    self.assertEqual(len(failures), 1, failures)
                    self.assertIn(state, failures[0])
                    self.assertIn("not recorded", failures[0])

    def test_each_phase24d_report_rejects_missing_timing_field(self) -> None:
        for state in PHASE24D_RENDER_STATES:
            with self.subTest(state=state):
                reports = self.reports()
                reports[state] = {}
                failures = phase24d_render_failures(reports)
                self.assertEqual(len(failures), 1, failures)
                self.assertIn(state, failures[0])

    def test_each_phase24d_report_rejects_over_budget_timing(self) -> None:
        for state in PHASE24D_RENDER_STATES:
            with self.subTest(state=state):
                reports = self.reports()
                reports[state]["renderMs"] = 250.001
                failures = phase24d_render_failures(reports)
                self.assertEqual(len(failures), 1, failures)
                self.assertIn(state, failures[0])
                self.assertIn("render took 250.001 ms", failures[0])


if __name__ == "__main__":
    unittest.main()
