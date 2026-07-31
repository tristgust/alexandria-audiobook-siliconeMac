from __future__ import annotations

import json
import unittest
from pathlib import Path

from benchmarks.original_sin_direct_overlap_timing import safe_segment_bounds


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / (
    "benchmarks/original_sin_direct_overlap_expansion_batch_001_"
    "timing_repair_plan.json"
)
PROJECT = Path(
    "/Users/tristan/Library/Application Support/Alexandria/Projects/"
    "original-sin--e6286665"
)


class TimingRepairPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = json.loads(PLAN.read_text(encoding="utf-8"))
        cls.segments = json.loads(
            (
                PROJECT
                / "external_workflows/big_finish_overlap_reference_v1/private/transcript.json"
            ).read_text(encoding="utf-8")
        )["segments"]

    def test_contains_only_nine_boundary_repairs(self) -> None:
        self.assertEqual(
            set(self.plan["selected_chunk_ids"]),
            {5351, 696, 1261, 2741, 2745, 90, 2090, 4764, 3285},
        )

    def test_segment_tail_not_word_timestamp_is_required(self) -> None:
        contract = self.plan["timing_contract"]
        self.assertFalse(contract["word_timestamp_alone_is_sufficient"])
        self.assertTrue(contract["transcript_segment_tail_required"])
        self.assertTrue(contract["deterministic_post_silence_required"])

    def test_tight_doctor_gap_uses_all_safe_tail(self) -> None:
        timing = safe_segment_bounds(
            segments=self.segments,
            segment_start=1372,
            segment_end=1372,
            adjacent_guard_seconds=self.plan["adjacent_speaker_guard_seconds"],
            requested_segment_tail_seconds=self.plan["requested_segment_tail_seconds"],
        )
        self.assertGreater(timing["required_segment_tail_seconds"], 0.08)
        self.assertLess(timing["required_segment_tail_seconds"], 0.11)

    def test_roomy_line_gets_full_requested_tail(self) -> None:
        timing = safe_segment_bounds(
            segments=self.segments,
            segment_start=479,
            segment_end=479,
            adjacent_guard_seconds=self.plan["adjacent_speaker_guard_seconds"],
            requested_segment_tail_seconds=self.plan["requested_segment_tail_seconds"],
        )
        self.assertAlmostEqual(
            timing["required_segment_tail_seconds"],
            self.plan["requested_segment_tail_seconds"],
            places=6,
        )

    def test_no_production_changes(self) -> None:
        self.assertFalse(self.plan["production_changes"])


if __name__ == "__main__":
    unittest.main()
