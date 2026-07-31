from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "benchmarks/original_sin_chris_urgent_performance_plan_v3.json"
PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")


def norm(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", str(value).casefold().replace("’", "'")))


class ChrisUrgentPerformancePlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = json.loads(PLAN.read_text(encoding="utf-8"))

    def test_six_candidates_cover_three_backends_and_two_references(self):
        self.assertEqual(len(self.plan["adaptation_performance"]["treatments"]), 2)
        self.assertEqual(len(self.plan["routes_per_reference"]), 3)
        self.assertEqual(self.plan["candidate_count"], 6)

    def test_target_line_is_not_exactly_in_adaptation(self):
        segments = json.loads((PROJECT / "external_workflows/big_finish_overlap_reference_v1/private/transcript.json").read_text(encoding="utf-8"))["segments"]
        exact = {norm(row.get("text")) for row in segments}
        self.assertNotIn(norm(self.plan["text"]), exact)

    def test_source_is_real_urgent_adaptation_performance(self):
        self.assertEqual(self.plan["adaptation_performance"]["segment_start"], 2442)
        self.assertIn("shooting straight", self.plan["adaptation_performance"]["transcript"])

    def test_source_asr_alias_does_not_change_spoken_target(self):
        source = self.plan["adaptation_performance"]
        self.assertIn("stripping straight", source["recognizer_transcripts"][0])
        self.assertIn("shooting straight", source["transcript"])

    def test_fish_is_included(self):
        self.assertIn("fish_s2.1_pro_free", self.plan["routes_per_reference"])

    def test_no_production_changes(self):
        self.assertFalse(self.plan["production_changes"])


if __name__ == "__main__":
    unittest.main()
