from __future__ import annotations

import copy
import unittest

import engine_qualification as qualification


class EngineQualificationStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = qualification.build_manifest("qwen3_base")
        self.expected = qualification.make_expected_set(self.manifest)
        self.ledger = qualification.make_ledger(self.manifest, self.expected)
        self.results = [qualification.aggregate_stage(self.manifest, self.expected, self.ledger, stage) for stage in qualification.STAGE_IDS]

    def test_all_eighteen_stage_results_preserve_order(self) -> None:
        self.assertEqual([result["stage_id"] for result in self.results], list(qualification.STAGE_IDS))

    def test_valid_not_applicable_has_no_denominator_rows(self) -> None:
        manifest = qualification.build_manifest("mlx_whisper_base"); expected = qualification.make_expected_set(manifest); ledger = qualification.make_ledger(manifest, expected)
        result = qualification.aggregate_stage(manifest, expected, ledger, "blinded_human_listening")
        self.assertEqual((result["state"], result["counts"]["expected_count"]), ("not_applicable", 0))

    def test_missing_applicable_evidence_fails_closed(self) -> None:
        expected = copy.deepcopy(self.expected); expected["items"] = [item for item in expected["items"] if item["stage_id"] != qualification.STAGE_IDS[0]]
        with self.assertRaises(qualification.QualificationError) as raised: qualification.validate_expected_set(expected, self.manifest)
        self.assertEqual(raised.exception.code, "expected_set_definition_mismatch")

    def test_failed_row_fails_stage(self) -> None:
        item = self.expected["items"][0]; rows = copy.deepcopy(self.ledger["rows"]); rows[0] = qualification.terminal_row(self.manifest, item, terminal_state="failed", assertion_outcome="fail")
        result = qualification.aggregate_stage(self.manifest, self.expected, qualification.make_ledger(self.manifest, self.expected, rows), qualification.STAGE_IDS[0])
        self.assertEqual(result["state"], "failed")

    def test_cancelled_row_blocks_stage(self) -> None:
        item = self.expected["items"][0]; rows = copy.deepcopy(self.ledger["rows"]); rows[0] = qualification.terminal_row(self.manifest, item, terminal_state="cancelled", assertion_outcome="block")
        result = qualification.aggregate_stage(self.manifest, self.expected, qualification.make_ledger(self.manifest, self.expected, rows), qualification.STAGE_IDS[0])
        self.assertEqual(result["state"], "blocked")

    def test_unverified_reject_cannot_mask_a_hard_block(self) -> None:
        with self.assertRaises(qualification.QualificationError) as raised:
            qualification.derive_disposition(self.manifest, self.results, trusted_decision="reject", decision_attestation_hash="a" * 64)
        self.assertEqual(raised.exception.code, "untrusted_decision")
        with self.assertRaises(qualification.QualificationError) as raised:
            qualification.build_receipt(self.manifest, self.expected, self.ledger, self.results, "rejected", decision_attestation_hash="a" * 64)
        self.assertEqual(raised.exception.code, "untrusted_decision")

    def test_license_and_acquisition_blocks_precede_metrics(self) -> None:
        manifest = copy.deepcopy(self.manifest); manifest["license_disposition"] = "blocked"
        self.assertEqual(qualification.derive_disposition(manifest, self.results), "blocked_by_license")
        manifest = copy.deepcopy(self.manifest); manifest["acquisition_disposition"] = "blocked"
        self.assertEqual(qualification.derive_disposition(manifest, self.results), "blocked_by_acquisition")

    def test_platform_block_precedes_metrics(self) -> None:
        manifest = copy.deepcopy(self.manifest); manifest["platform_target"] = qualification.identity("platform", "blocked")
        self.assertEqual(qualification.derive_disposition(manifest, self.results), "blocked_by_platform")

    def test_unsigned_decision_never_crosses_trust_boundary(self) -> None:
        with self.assertRaises(qualification.QualificationError) as raised:
            qualification.derive_disposition(self.manifest, self.results, trusted_decision="approve")
        self.assertEqual(raised.exception.code, "untrusted_decision")

    def test_missing_human_decision_never_promotes(self) -> None:
        self.assertEqual(qualification.derive_disposition(self.manifest, self.results), "deferred")

    def test_small_sample_wilson_interval_is_none(self) -> None:
        self.assertIsNone(qualification.wilson_interval(1, 1))

    def test_metric_requires_canonical_non_boolean_count(self) -> None:
        metric = {"id": "wer", "formula_version": "wer_v1", "unit": "ratio", "value_decimal": "0", "sample_count": True, "confidence_level_decimal": None, "confidence_interval_decimal_pair": None, "limitation_codes": ["insufficient_sample_size"]}
        with self.assertRaises(qualification.QualificationError) as raised: qualification.validate_metric(metric)
        self.assertEqual(raised.exception.code, "invalid_metric_count")


if __name__ == "__main__":
    unittest.main()
