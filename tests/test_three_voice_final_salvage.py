from __future__ import annotations

import importlib.util
import json
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPLY_SCRIPT = ROOT / "benchmarks" / "apply_three_voice_source_repair_review.py"
SALVAGE_SCRIPT = ROOT / "benchmarks" / "prepare_three_voice_final_salvage.py"
ASSET_ROOT = ROOT / "benchmarks" / "three_voice_final_salvage_assets"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ThreeVoiceFinalSalvageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.apply = load_module(APPLY_SCRIPT, "apply_three_voice_source_repair_review")
        cls.salvage = load_module(SALVAGE_SCRIPT, "prepare_three_voice_final_salvage")

    def test_repair_review_decisions_have_strict_dispositions(self) -> None:
        self.assertEqual(self.apply.classify("approve_repaired"), "approved_after_repair")
        self.assertEqual(self.apply.classify("cleanup_still_bad"), "source_separation_required")
        self.assertEqual(self.apply.classify("boundary_still_wrong"), "boundary_repair_required")
        self.assertEqual(self.apply.classify("mine_nearby"), "mine_nearby_required")
        self.assertEqual(self.apply.classify("reject"), "rejected_after_repair")
        self.assertEqual(self.apply.classify(None), "invalid_or_missing_decision")

    def test_review_notes_flag_mixed_audio_and_boundaries(self) -> None:
        self.assertEqual(
            self.apply.issue_flags({"notes": "music and sound effects remain; boundary cuts off"}),
            ["mixed_background_contamination", "boundary_problem"],
        )
        self.assertEqual(self.apply.issue_flags({"notes": "clean"}), [])

    def test_final_salvage_scope_is_deliberately_small(self) -> None:
        self.assertEqual(len(self.salvage.SEPARATION_CARDS), 4)
        self.assertEqual(len(self.salvage.BOUNDARY_SPECS), 5)
        self.assertEqual(set(self.salvage.SEPARATION_MODELS), {"bs317", "fv4", "mdx"})
        self.assertEqual(
            Counter(row.split("_", 1)[0] for row in self.salvage.SEPARATION_CARDS),
            Counter({"benny": 1, "doctor": 2, "narrator": 1}),
        )

    def test_boundary_specs_have_complete_source_timestamp_windows(self) -> None:
        for clip_id, row in self.salvage.BOUNDARY_SPECS.items():
            with self.subTest(clip_id=clip_id):
                self.assertGreater(row["end"], row["start"])
                self.assertGreater(row["end"] - row["start"], 2.0)
                self.assertTrue(row["transcript"].strip())
                self.assertTrue(row["reason"].strip())
        self.assertEqual(
            self.salvage.BOUNDARY_SPECS["narrator_ud_warm_reconciliation"]["start"],
            14280.02,
        )
        self.assertEqual(
            self.salvage.BOUNDARY_SPECS["narrator_ud_contemptuous_disbelief"]["end"],
            7527.16,
        )

    def test_candidate_blinding_is_stable_and_complete(self) -> None:
        for clip_id in self.salvage.SEPARATION_CARDS:
            order = self.salvage.blind_order(clip_id)
            self.assertEqual(order, self.salvage.blind_order(clip_id))
            self.assertEqual(set(order), set(self.salvage.SEPARATION_MODELS))
            self.assertEqual(len(order), 3)

    def test_review_assets_blind_models_and_keep_lazy_audio_bounded(self) -> None:
        html = (ASSET_ROOT / "index.html").read_text(encoding="utf-8")
        app = (ASSET_ROOT / "app.js").read_text(encoding="utf-8")
        self.assertEqual(html.lower().count("<audio"), 4)
        self.assertIn("Candidate A", html)
        self.assertIn("Candidate B", html)
        self.assertIn("Candidate C", html)
        self.assertIn("None are usable", html)
        self.assertIn("Use final boundary", html)
        self.assertIn("alexandria_three_voice_final_salvage_review.json", app)
        self.assertIn("advanceAfterDecision", app)
        for leak in ("BS-RoFormer", "MelBand", "MDX-Net", "model_bs_roformer", "gabox", "Voc_FT"):
            self.assertNotIn(leak, html)
            self.assertNotIn(leak, app)

    def test_generated_ledgers_match_review_when_present(self) -> None:
        applied_path = ROOT / ".omo" / "evidence" / "b17-t55-three-voice-source-repair-review-applied" / "applied-repair-review-ledger.json"
        salvage_path = ROOT / ".omo" / "evidence" / "b17-t56-three-voice-final-salvage" / "salvage-manifest.json"
        if not applied_path.is_file() or not salvage_path.is_file():
            self.skipTest("Generated local salvage evidence is not present.")
        applied = json.loads(applied_path.read_text(encoding="utf-8"))
        salvage = json.loads(salvage_path.read_text(encoding="utf-8"))
        self.assertEqual(applied["validated_reference_count"], 12)
        self.assertEqual(applied["approved_after_repair_count"], 2)
        self.assertEqual(len(applied["source_separation_queue"]), 14)
        self.assertEqual(len(applied["boundary_repair_queue"]), 5)
        self.assertEqual(applied["invalid_or_missing_decisions"], [])
        self.assertEqual(salvage["card_count"], 9)
        self.assertEqual(salvage["card_type_counts"], {"boundary_final": 5, "source_separation": 4})
        self.assertFalse(salvage["automatic_production_assignment"])
        self.assertFalse(salvage["production_promotion_allowed"])
        for row in salvage["rows"]:
            self.assertFalse(row["production_promotion_allowed"])
            if row["card_type"] == "source_separation":
                self.assertEqual(len(row["candidates"]), 3)
                self.assertTrue(all(candidate["technical_pass"] for candidate in row["candidates"]))
            else:
                self.assertTrue(row["final"]["technical_pass"])


if __name__ == "__main__":
    unittest.main()
