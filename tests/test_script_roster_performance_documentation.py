from __future__ import annotations

import unittest
from pathlib import Path

from interface_phase24d_performance import PHASE24D_RENDER_LIMIT_MS


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "SCRIPT_ROSTER_PERFORMANCE.md"
HARNESS = ROOT / "tests" / "script_roster_performance_harness.py"


class ScriptRosterPerformanceDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.doc = DOC.read_text(encoding="utf-8")
        cls.harness = HARNESS.read_text(encoding="utf-8")

    def test_document_records_representative_workloads(self):
        for phrase in (
            "6,000 valid Script entries",
            "at least 1,500 observations",
            "native Script contract validator",
            "source-fidelity audit",
            "Script speaker-run chunk grouping",
            "native roster-discovery schema",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.doc)

    def test_document_and_harness_share_regression_budgets(self):
        for value in ("2,500 ms", "6,000 ms", "4,000 ms", "250 ms"):
            with self.subTest(value=value):
                self.assertIn(value, self.doc)
        self.assertIn("SCRIPT_ENTRY_COUNT = 6_000", self.harness)
        self.assertIn("ROSTER_ITEM_COUNT = 1_500", self.harness)
        self.assertIn("math.ceil(ROSTER_ITEM_COUNT / roster_items_per_payload)", self.harness)
        self.assertIn('"script_fidelity_audit_median_ms": 6_000.0', self.harness)
        self.assertEqual(PHASE24D_RENDER_LIMIT_MS, 250.0)

    def test_document_records_runtime_timing_and_eta_policy(self):
        for phrase in (
            "logs/stages/script_metrics.json",
            "logs/stages/roster_metrics.json",
            "prompt assembly",
            "native Ollama total, load, prompt-evaluation, and generation time",
            "exact source-fidelity audit",
            "exact source-evidence validation",
            "at least three recent units",
            "15 percent buffer",
            "ETA is deliberately suppressed",
            "Missing or corrupt metrics disable timing",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.doc)

    def test_document_limits_claims_to_local_structured_work(self):
        self.assertIn("does not claim a fixed end-to-end generation time", self.doc)
        self.assertIn("do not include model inference or network transfer", self.doc)
        self.assertIn("Do not weaken source fidelity", self.doc)
        self.assertIn("no unverified model concurrency", self.doc)


if __name__ == "__main__":
    unittest.main()
