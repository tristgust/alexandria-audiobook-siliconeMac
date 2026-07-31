from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "benchmarks/original_sin_chris_urgent_clean_identity_plan_v4.json"
PROJECT = Path("/Users/tristan/Library/Application Support/Alexandria/Projects/original-sin--e6286665")


def norm(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", str(value).casefold().replace("’", "'")))


class ChrisCleanIdentityPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = json.loads(PLAN.read_text())

    def test_uses_longer_unseen_command(self):
        self.assertEqual(self.plan["chunk_id"], 5146)
        segments = json.loads((PROJECT / "external_workflows/big_finish_overlap_reference_v1/private/transcript.json").read_text())["segments"]
        adaptation = norm(" ".join(str(row.get("text") or "") for row in segments))
        self.assertNotIn(norm(self.plan["text"]), adaptation)

    def test_qwen_and_fish_have_normal_and_fast_variants(self):
        self.assertEqual(set(self.plan["routes"]), {"qwen_clean_identity", "qwen_clean_identity_fast", "fish_clean_identity", "fish_clean_identity_fast"})

    def test_no_production_changes(self):
        self.assertFalse(self.plan["production_changes"])


if __name__ == "__main__":
    unittest.main()
