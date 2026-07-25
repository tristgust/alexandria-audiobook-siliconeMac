from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "apply_three_voice_provenance_final_boundaries_review.py"
EVIDENCE = ROOT / ".omo" / "evidence" / "b17-t73-three-voice-validated-bank-final"
BANK = EVIDENCE / "three-voice-validated-reference-bank.json"
LEDGER = EVIDENCE / "applied-final-boundary-review-ledger.json"
REVIEW = (
    ROOT
    / ".omo"
    / "evidence"
    / "b17-t72-three-voice-provenance-final-boundaries-review-applied"
    / "normalized-review-export.json"
)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ThreeVoiceProvenanceFinalBoundariesApplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module(SCRIPT, "apply_three_voice_provenance_final_boundaries_review")

    def test_exact_final_decisions_are_required(self) -> None:
        self.assertEqual(
            self.module.EXPECTED_CARDS,
            {
                "boundary:benny_hesitation_fatalistic_dread": "approve_final",
                "separation:doctor_acf_dismissive_contempt": "candidate_C",
            },
        )

    def test_normalized_review_preserves_uploaded_hash(self) -> None:
        if not REVIEW.is_file():
            self.skipTest("Normalized uploaded review is not present in this checkout.")
        payload = json.loads(REVIEW.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["source_upload_sha256"],
            "c20c08120355438ee4eedb8ef17b36107cc6efb62cfe774e1ea6ab1a1e6f2b7f",
        )
        review = payload["review"]
        self.assertEqual(review["summary"]["complete_count"], 2)
        self.assertEqual(
            {row["card_id"]: row["decision"] for row in review["rows"]},
            self.module.EXPECTED_CARDS,
        )

    def test_generated_bank_contains_final_31_references(self) -> None:
        if not BANK.is_file():
            self.skipTest("Generated final bank is not present in this checkout.")
        payload = json.loads(BANK.read_text(encoding="utf-8"))
        result = self.module.validate_final_bank(payload)
        self.assertEqual(result["reference_count"], 31)
        self.assertEqual(
            result["reference_counts_by_target"],
            {"benny": 10, "doctor": 5, "narrator": 16},
        )
        by_id = {row["clip_id"]: row for row in payload["references"]}
        benny = by_id["benny_hesitation_fatalistic_dread"]
        doctor = by_id["doctor_acf_dismissive_contempt"]
        self.assertEqual(benny["review_decision"], "approve_final")
        self.assertEqual(benny["technical_verification"]["verification_similarity"], 1.0)
        self.assertEqual(doctor["review_decision"], "candidate_C")
        self.assertEqual(doctor["separation_model_key"], "fv4")
        self.assertEqual(doctor["technical_verification"]["verification_similarity"], 1.0)
        self.assertEqual(
            [row["clip_id"] for row in payload["follow_up_queue"]],
            ["benny_hesitation_protective_reassurance"],
        )
        self.assertFalse(payload["automatic_production_assignment"])
        self.assertFalse(payload["production_promotion_allowed"])

    def test_all_generated_audio_is_canonical(self) -> None:
        if not BANK.is_file():
            self.skipTest("Generated final bank is not present in this checkout.")
        payload = json.loads(BANK.read_text(encoding="utf-8"))
        for row in payload["references"]:
            with self.subTest(clip_id=row["clip_id"]):
                self.module.validated_audio(
                    row["audio_path"],
                    row["audio_sha256"],
                    row["clip_id"],
                    require_bank_format=True,
                )

    def test_applied_ledger_matches_bank(self) -> None:
        if not BANK.is_file() or not LEDGER.is_file():
            self.skipTest("Generated final evidence is not present in this checkout.")
        ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        self.assertEqual(ledger["round_id"], self.module.APPLIED_ROUND_ID)
        self.assertEqual(ledger["added_reference_count"], 2)
        self.assertEqual(ledger["final_bank"]["reference_count"], 31)
        self.assertEqual(ledger["final_bank"]["sha256"], self.module.sha256_file(BANK))
        self.assertEqual(ledger["remaining_follow_up_count"], 1)
        self.assertFalse(ledger["automatic_production_assignment"])
        self.assertFalse(ledger["production_promotion_allowed"])


if __name__ == "__main__":
    unittest.main()
