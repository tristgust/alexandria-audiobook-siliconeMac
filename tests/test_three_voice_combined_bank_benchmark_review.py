from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "apply_three_voice_combined_bank_benchmark_review.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ThreeVoiceCombinedBankBenchmarkReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module(SCRIPT, "apply_three_voice_combined_bank_benchmark_review")

    def test_wrong_seventh_doctor_note_is_detected(self) -> None:
        self.assertTrue(
            self.module.wrong_seventh_doctor_note(
                "the authentic performance reference is NOT the 7th doctor..."
            )
        )
        self.assertTrue(
            self.module.wrong_seventh_doctor_note(
                "This is not the Seventh Doctor."
            )
        )
        self.assertFalse(self.module.wrong_seventh_doctor_note("Correct speaker."))

    def test_quality_status_separates_preference_from_usable_quality(self) -> None:
        reviewed = {
            "candidate_A_issues": [],
            "candidate_B_issues": ["weak_delivery"],
            "notes": "I don't love either as they both have huge flaws, but B sounds more accurate.",
        }
        self.assertEqual(
            self.module.quality_status(reviewed, "B"),
            "preference_only_quality_blocked",
        )
        self.assertEqual(
            self.module.quality_status(
                {"candidate_A_issues": [], "candidate_B_issues": [], "notes": None},
                "A",
            ),
            "clean_preference",
        )
        self.assertEqual(self.module.quality_status({}, None), "neither_usable")

    def test_applied_ledger_is_conservative_when_present(self) -> None:
        path = (
            ROOT
            / ".omo"
            / "evidence"
            / "b17-t64-three-voice-bank-benchmark-review-applied"
            / "applied-benchmark-review-ledger.json"
        )
        if not path.is_file():
            self.skipTest("Generated local applied benchmark review is not present.")
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = self.module.validate_applied(payload)
        self.assertEqual(result["route_count"], 6)
        self.assertEqual(result["valid_route_count"], 2)
        self.assertEqual(result["invalid_route_count"], 4)
        self.assertEqual(result["valid_combined_bank_win_count"], 2)
        self.assertEqual(result["clean_valid_combined_bank_win_count"], 1)
        self.assertFalse(result["broad_bank_improvement_claim_supported"])
        rejected = {row["clip_id"]: row for row in payload["rejected_bank_clips"]}
        self.assertEqual(
            rejected["doctor_acf_emergency_command"]["disposition"],
            "rejected_wrong_speaker",
        )
        outcomes = {row["route_id"]: row for row in payload["outcomes"]}
        self.assertTrue(outcomes["narrator_anger"]["benchmark_valid"])
        self.assertEqual(outcomes["narrator_anger"]["quality_status"], "clean_preference")
        self.assertTrue(outcomes["narrator_joy"]["benchmark_valid"])
        self.assertEqual(
            outcomes["narrator_joy"]["quality_status"],
            "preference_only_quality_blocked",
        )
        self.assertFalse(outcomes["doctor_urgency"]["benchmark_valid"])
        self.assertIn(
            "authentic_reference_wrong_speaker",
            outcomes["doctor_urgency"]["invalid_reasons"],
        )

    def test_importer_never_promotes_or_assigns_production(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('"automatic_production_assignment": False', source)
        self.assertIn('"production_promotion_allowed": False', source)
        self.assertIn('"broad_bank_improvement_claim_supported": False', source)


if __name__ == "__main__":
    unittest.main()
