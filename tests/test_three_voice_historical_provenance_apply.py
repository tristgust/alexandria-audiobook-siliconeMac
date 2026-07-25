from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "apply_three_voice_historical_provenance_review.py"
EVIDENCE = ROOT / ".omo" / "evidence" / "b17-t68-three-voice-validated-bank-after-provenance"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ThreeVoiceHistoricalProvenanceApplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module(SCRIPT, "apply_three_voice_historical_provenance_review")

    def test_decision_contract_is_strict(self) -> None:
        self.assertEqual(
            self.module.EXPECTED_DECISION_COUNTS,
            {
                "approve_usable": 10,
                "correct_speaker_unusable": 2,
                "wrong_or_uncertain_speaker": 0,
                "wrong_boundary": 1,
                "locked_rejected_wrong_speaker": 1,
            },
        )
        self.assertEqual(
            set(self.module.BOUNDARY_REPAIR_SPECS),
            {"benny_hesitation_fatalistic_dread"},
        )

    def test_generated_bank_when_present(self) -> None:
        bank_path = EVIDENCE / "three-voice-validated-reference-bank.json"
        ledger_path = EVIDENCE / "applied-provenance-review-ledger.json"
        if not bank_path.is_file() or not ledger_path.is_file():
            self.skipTest("Generated provenance evidence is not present in this checkout.")
        bank = json.loads(bank_path.read_text(encoding="utf-8"))
        result = self.module.validate_bank(bank)
        self.assertEqual(result["reference_count"], 29)
        self.assertEqual(
            result["reference_counts_by_target"],
            {"benny": 9, "doctor": 4, "narrator": 16},
        )
        self.assertEqual(result["newly_human_validated_reference_count"], 10)
        self.assertEqual(result["follow_up_count"], 3)
        ids = {row["clip_id"] for row in bank["references"]}
        self.assertNotIn("benny_hesitation_fatalistic_dread", ids)
        self.assertNotIn("benny_hesitation_protective_reassurance", ids)
        self.assertNotIn("doctor_acf_dismissive_contempt", ids)
        self.assertNotIn("doctor_acf_emergency_command", ids)
        self.assertIn("benny_hesitation_fearful_vigilance", ids)
        self.assertIn("doctor_acf_playful_introduction", ids)
        self.assertEqual(bank["source_upload"]["source_upload_sha256"], "41f2b5d5ca27c829b0d1a602df19a468cd6155f9dff8cef7d064daada425a82a")
        self.assertFalse(bank["automatic_production_assignment"])
        self.assertFalse(bank["production_promotion_allowed"])

    def test_follow_up_queue_is_bounded_when_present(self) -> None:
        bank_path = EVIDENCE / "three-voice-validated-reference-bank.json"
        if not bank_path.is_file():
            self.skipTest("Generated provenance evidence is not present in this checkout.")
        bank = json.loads(bank_path.read_text(encoding="utf-8"))
        queue = {row["clip_id"]: row for row in bank["follow_up_queue"]}
        self.assertEqual(
            {clip_id: row["follow_up_type"] for clip_id, row in queue.items()},
            {
                "benny_hesitation_fatalistic_dread": "boundary_recut",
                "benny_hesitation_protective_reassurance": "replacement_source_required",
                "doctor_acf_dismissive_contempt": "source_cleanup",
            },
        )
        self.assertEqual(
            queue["benny_hesitation_protective_reassurance"]["disposition"],
            "exclude_role_contaminated_clip",
        )
        self.assertEqual(
            queue["doctor_acf_dismissive_contempt"]["disposition"],
            "one_bounded_cleanup_attempt_then_stop",
        )


if __name__ == "__main__":
    unittest.main()
