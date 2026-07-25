from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "apply_three_voice_selected_refinement_review.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ThreeVoiceCombinedReferenceBankTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bank = load_module(SCRIPT, "apply_three_voice_selected_refinement_review")

    def test_gap_assessment_is_conservative_and_explicit(self) -> None:
        references = [
            {"clip_id": "narrator_ud_ecstatic_bucket_affection"},
            {"clip_id": "benny_hesitation_fearful_vigilance"},
            {"clip_id": "benny_hesitation_protective_reassurance"},
            {"clip_id": "doctor_acf_emergency_command"},
            {"clip_id": "doctor_indomitable_determination"},
        ]
        assessment = self.bank.gap_assessment(references)
        narrator = {row["function"]: row for row in assessment["narrator"]["requirements"]}
        benny = {row["function"]: row for row in assessment["benny"]["requirements"]}
        doctor = {row["function"]: row for row in assessment["doctor"]["requirements"]}
        self.assertEqual(narrator["joy"]["status"], "covered")
        self.assertEqual(narrator["grief_or_regret"]["status"], "open_gap")
        self.assertEqual(benny["credible_fear"]["status"], "covered")
        self.assertEqual(benny["soft_intimacy"]["status"], "covered")
        self.assertEqual(benny["grief"]["status"], "open_gap")
        self.assertEqual(doctor["urgency"]["status"], "covered")
        self.assertEqual(doctor["authority"]["status"], "covered")
        self.assertEqual(doctor["compassion"]["status"], "open_gap")

    def test_review_decisions_are_strict_and_non_promoting(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('ALLOWED_DECISIONS = {"use_refined", "use_selected", "reject"}', source)
        self.assertIn('"reference_status": "approved_source_reference_final"', source)
        self.assertIn("historical_candidate_is_explicitly_approved", source)
        self.assertIn("historical_candidate_lacks_explicit_human_approval", source)
        self.assertIn('"automatic_production_assignment": False', source)
        self.assertIn('"production_promotion_allowed": False', source)
        self.assertIn('"ffmpeg"', source)
        self.assertIn('"24000"', source)
        self.assertIn('"pcm_s16le"', source)

    def test_historical_candidates_require_explicit_human_approval(self) -> None:
        pending = {
            "selection_status": "assistant_transcript_guided_candidate",
            "user_correction_required_before_bank_approval": True,
        }
        approved = {
            "selection_status": "user_approved",
            "user_correction_required_before_bank_approval": False,
        }
        self.assertFalse(self.bank.historical_candidate_is_explicitly_approved(pending))
        self.assertTrue(self.bank.historical_candidate_is_explicitly_approved(approved))

    def test_duplicate_clip_ids_are_rejected(self) -> None:
        with self.assertRaises(self.bank.FinalBankError):
            # rows_by_id is the first duplicate guard used by every imported decision set.
            self.bank.rows_by_id(
                [{"clip_id": "same"}, {"clip_id": "same"}],
                key="clip_id",
                label="test rows",
            )

    def test_generated_bank_matches_review_when_present(self) -> None:
        path = (
            ROOT
            / ".omo"
            / "evidence"
            / "b17-t65-three-voice-validated-core-bank"
            / "three-voice-combined-reference-bank.json"
        )
        if not path.is_file():
            self.skipTest("Generated local validated core bank is not present in this checkout.")
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = self.bank.validate_bank(payload)
        self.assertEqual(result["reference_count"], 19)
        self.assertEqual(
            result["reference_counts_by_target"],
            {"benny": 1, "doctor": 2, "narrator": 16},
        )
        self.assertEqual(result["new_source_reference_count"], 19)
        self.assertEqual(result["historical_reference_count"], 0)
        self.assertEqual(result["pending_historical_candidate_count"], 14)
        self.assertEqual(result["open_gaps"]["narrator"], ["grief_or_regret"])
        self.assertEqual(
            result["open_gaps"]["benny"],
            ["credible_fear", "grief", "explosive_anger"],
        )
        self.assertEqual(
            result["open_gaps"]["doctor"],
            ["compassion", "urgency", "weariness"],
        )
        self.assertFalse(payload["automatic_production_assignment"])
        self.assertFalse(payload["production_promotion_allowed"])
        self.assertTrue(payload["ready_for_targeted_generation_benchmark"])

    def test_all_generated_audio_is_canonical_when_present(self) -> None:
        path = (
            ROOT
            / ".omo"
            / "evidence"
            / "b17-t65-three-voice-validated-core-bank"
            / "three-voice-combined-reference-bank.json"
        )
        if not path.is_file():
            self.skipTest("Generated local validated core bank is not present in this checkout.")
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload["references"]:
            with self.subTest(clip_id=row["clip_id"]):
                audio = Path(row["audio_path"])
                self.assertTrue(audio.is_file())
                self.bank.validated_audio(
                    audio,
                    row["audio_sha256"],
                    row["clip_id"],
                    require_bank_format=True,
                )


if __name__ == "__main__":
    unittest.main()
