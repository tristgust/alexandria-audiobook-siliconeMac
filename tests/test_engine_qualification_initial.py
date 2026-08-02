from __future__ import annotations

import copy
import unittest
from pathlib import Path

import engine_qualification as qualification
from model_registry import component_record_payload, engine_record_fingerprint, engine_record_payload


class EngineQualificationInitialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verified_import = qualification.verify_imported_evidence(Path(__file__).parent / "fixtures" / "engine_qualification")
        cls.results = {subject: qualification.initial_qualification(subject, verified_import=cls.verified_import) for subject in ("qwen3_base", "qwen3_instruction_controlled", "mlx_whisper_base")}

    def test_exact_three_subjects(self) -> None:
        self.assertEqual(set(self.results), {"qwen3_base", "qwen3_instruction_controlled", "mlx_whisper_base"})

    def test_expected_denominators_are_eighteen_eighteen_thirteen(self) -> None:
        self.assertEqual([len(self.results[subject]["expected_set"]["items"]) for subject in self.results], [18, 18, 13])

    def test_all_forty_nine_items_are_terminal(self) -> None:
        self.assertEqual(sum(len(result["ledger"]["rows"]) for result in self.results.values()), 49)

    def test_all_fifty_four_stage_results_exist(self) -> None:
        self.assertEqual(sum(len(result["stage_results"]) for result in self.results.values()), 54)
        self.assertTrue(all(result["manifest"]["imported_evidence_hash"] == self.verified_import.bundle_hash for result in self.results.values()))
        self.assertTrue(all(stage["state"] != "passed" for result in self.results.values() for stage in result["stage_results"][:-1]))

    def test_all_new_dispositions_are_deferred(self) -> None:
        self.assertEqual({result["receipt"]["final_disposition"] for result in self.results.values()}, {"deferred"})

    def test_qwen_base_preserves_inherited_authority(self) -> None:
        result = self.results["qwen3_base"]
        self.assertEqual(result["manifest"]["prior_authority"], "current_production_baseline")
        self.assertIn("no_new_production_authority", result["limitations"])

    def test_controlled_and_whisper_bind_authoritative_records(self) -> None:
        controlled = self.results["qwen3_instruction_controlled"]["manifest"]
        whisper = self.results["mlx_whisper_base"]["manifest"]
        self.assertEqual(controlled["record_fingerprint"], engine_record_fingerprint(engine_record_payload("qwen3_instruction_controlled")))
        self.assertEqual(whisper["record_fingerprint"], engine_record_fingerprint(component_record_payload("mlx_whisper_base")))

    def test_receipt_tamper_is_not_silently_accepted(self) -> None:
        receipt = copy.deepcopy(self.results["mlx_whisper_base"]["receipt"]); receipt["final_disposition"] = "supporting_component_accepted"
        with self.assertRaises(qualification.QualificationError) as raised: qualification.validate_receipt(receipt)
        self.assertEqual(raised.exception.code, "tampered_receipt")


if __name__ == "__main__":
    unittest.main()
