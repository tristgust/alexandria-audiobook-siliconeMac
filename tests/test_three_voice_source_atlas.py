from __future__ import annotations

import importlib.util
import json
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "prepare_three_voice_source_atlas.py"
PACKAGE_SCRIPT = ROOT / "benchmarks" / "package_three_voice_source_atlas_review.py"
ASSET_ROOT = ROOT / "benchmarks" / "three_voice_source_atlas_assets"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ThreeVoiceSourceAtlasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.atlas = load_module(SCRIPT, "prepare_three_voice_source_atlas")
        cls.package = load_module(PACKAGE_SCRIPT, "package_three_voice_source_atlas_review")

    def test_candidate_counts_are_explicit_and_balanced(self) -> None:
        self.assertEqual(len(self.atlas.NARRATOR_SPECS), 23)
        self.assertEqual(len(self.atlas.BENNY_SPECS), 10)
        self.assertEqual(len(self.atlas.DOCTOR_SPECS), 12)
        self.assertEqual(len(self.atlas.ALL_SPECS), 45)
        self.assertEqual(
            Counter(row["target"] for row in self.atlas.ALL_SPECS),
            Counter({"narrator": 23, "benny": 10, "doctor": 12}),
        )
        self.assertEqual(self.package.EXPECTED_COUNTS, {"narrator": 23, "benny": 10, "doctor": 12})

    def test_every_candidate_has_reviewable_provenance_and_gap_metadata(self) -> None:
        seen = set()
        for row in self.atlas.ALL_SPECS:
            with self.subTest(clip_id=row["clip_id"]):
                self.assertNotIn(row["clip_id"], seen)
                seen.add(row["clip_id"])
                self.assertIn(row["source"], self.atlas.SOURCES)
                self.assertEqual(self.atlas.SOURCES[row["source"]]["target"], row["target"])
                self.assertTrue(row["expected_text"].strip())
                self.assertTrue(row["primary_emotion"].strip())
                self.assertTrue(row["secondary_emotion"].strip())
                self.assertTrue(row["dramatic_function"].strip())
                self.assertTrue(row["selection_reason"].strip())
                self.assertTrue(row["coverage_gap"].strip())
                self.assertTrue(row["speaker_role"].strip())
                self.assertIn(row["speaker_certainty"], {"high", "medium", "low"})
                self.assertIn(row["intensity_1_to_5"], range(1, 6))
                self.assertGreater(row["window"][1], row["window"][0])

    def test_uncertain_speaker_candidates_carry_warnings(self) -> None:
        uncertain = [row for row in self.atlas.ALL_SPECS if row["speaker_certainty"] != "high"]
        self.assertGreaterEqual(len(uncertain), 4)
        for row in uncertain:
            with self.subTest(clip_id=row["clip_id"]):
                self.assertTrue(row["source_role_warning"].strip())

    def test_review_assets_encode_fast_safe_workflow(self) -> None:
        html = (ASSET_ROOT / "index.html").read_text(encoding="utf-8")
        app = (ASSET_ROOT / "app.js").read_text(encoding="utf-8")
        self.assertEqual(html.lower().count("<audio"), 2)
        self.assertIn("Approve as labeled", html)
        self.assertIn("Approve after cleanup", html)
        self.assertIn("Mine a better nearby line", html)
        self.assertIn("target-filters", html)
        self.assertIn("status-filters", html)
        self.assertIn("production automatically", html)
        self.assertIn("alexandria_three_voice_source_atlas_review.json", app)
        self.assertIn('reference_decision: "approve"', app)
        self.assertIn('reference_decision: "mine_nearby"', app)
        self.assertIn('reference_decision: "reject"', app)

    def test_generated_atlas_remains_non_promotable_when_present(self) -> None:
        path = ROOT / ".omo" / "evidence" / "b17-t50-three-voice-source-atlas" / "three-voice-source-atlas.json"
        if not path.is_file():
            self.skipTest("Generated local atlas is not present in this checkout.")
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertFalse(payload["production_promotion_allowed"])
        self.assertFalse(payload["selection_policy"]["production_promotion_allowed"])
        self.assertTrue(payload["selection_policy"]["speaker_identity_requires_user_confirmation"])
        self.assertEqual(payload["candidate_count"], 45)
        self.assertEqual(payload["failure_count"], 0)
        self.assertEqual(payload["missing_clip_ids"], [])


if __name__ == "__main__":
    unittest.main()
