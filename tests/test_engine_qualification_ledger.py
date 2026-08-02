from __future__ import annotations

import copy
import unittest

import engine_qualification as qualification


class EngineQualificationLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = qualification.build_manifest("qwen3_base")
        self.expected = qualification.make_expected_set(self.manifest)
        self.ledger = qualification.make_ledger(self.manifest, self.expected)

    def assert_ledger_code(self, code: str, ledger: dict[str, object]) -> None:
        with self.assertRaises(qualification.QualificationError) as raised:
            qualification.validate_ledger(ledger, self.manifest, self.expected)
        self.assertEqual(raised.exception.code, code)

    def test_every_expected_item_has_one_terminal_row(self) -> None:
        self.assertEqual({item["item_id"] for item in self.expected["items"]}, {row["item_id"] for row in self.ledger["rows"]})

    def test_denominator_counts_all_terminal_states(self) -> None:
        states = ["complete", "failed", "excluded", *(["complete"] * (len(self.expected["items"]) - 3))]
        rows = [qualification.terminal_row(self.manifest, item, terminal_state=state, assertion_outcome="block" if state != "complete" else "pass") for item, state in zip(self.expected["items"], states, strict=True)]
        ledger = qualification.make_ledger(self.manifest, self.expected, rows)
        counts = qualification.ledger_counts(ledger)
        self.assertEqual((counts["expected_count"], counts["terminal_count"], counts["complete_count"], counts["failed_count"], counts["excluded_count"]), (18, 18, 16, 1, 1))

    def test_missing_row_fails_closed(self) -> None:
        value = copy.deepcopy(self.ledger); value["rows"].pop()
        self.assert_ledger_code("missing_terminal_row", value)

    def test_duplicate_row_fails_closed(self) -> None:
        value = copy.deepcopy(self.ledger); value["rows"][-1] = copy.deepcopy(value["rows"][0])
        self.assert_ledger_code("duplicate_terminal_row", value)

    def test_unknown_row_fails_closed(self) -> None:
        value = copy.deepcopy(self.ledger); value["rows"][0]["item_id"] = "unknown"
        self.assert_ledger_code("missing_terminal_row", value)

    def test_unknown_state_fails_closed(self) -> None:
        value = copy.deepcopy(self.ledger); value["rows"][0]["terminal_state"] = "silent_success"
        self.assert_ledger_code("unknown_terminal_state", value)

    def test_exclusion_requires_reason(self) -> None:
        value = copy.deepcopy(self.ledger); value["rows"][0]["terminal_state"] = "excluded"; value["rows"][0]["output_hash"] = None
        self.assert_ledger_code("exclusion_without_reason", value)

    def test_cross_subject_reuse_fails_closed(self) -> None:
        value = copy.deepcopy(self.ledger); value["rows"][0]["subject_id"] = "mlx_whisper_base"
        self.assert_ledger_code("cross_subject_reuse", value)

    def test_wrong_span_fails_closed(self) -> None:
        value = copy.deepcopy(self.ledger); value["rows"][0]["source_span"]["end"] = 0
        self.assert_ledger_code("expected_item_mismatch", value)

    def test_complete_row_requires_output_hash(self) -> None:
        value = copy.deepcopy(self.ledger); value["rows"][0]["output_hash"] = None
        self.assert_ledger_code("missing_output_hash", value)

    def test_receipt_bytes_are_deterministic(self) -> None:
        one = qualification.initial_qualification("qwen3_base")["receipt"]
        two = qualification.initial_qualification("qwen3_base")["receipt"]
        self.assertEqual(qualification.canonical_bytes(one), qualification.canonical_bytes(two))

    def test_tampered_receipt_fails_closed(self) -> None:
        result = qualification.initial_qualification("qwen3_base")
        receipt = result["receipt"]
        forged = copy.deepcopy(receipt); forged["final_disposition"] = "production_accepted"; forged["receipt_hash"] = qualification.canonical_hash({key: value for key, value in forged.items() if key != "receipt_hash"})
        with self.assertRaises(qualification.QualificationError) as raised:
            qualification.prepare_publication(result["manifest"], result["expected_set"], result["ledger"], result["stage_results"], forged)
        self.assertEqual(raised.exception.code, "untrusted_disposition")

    def test_expected_item_ids_are_unique(self) -> None:
        value = copy.deepcopy(self.expected); value["items"][1]["item_id"] = value["items"][0]["item_id"]
        with self.assertRaises(qualification.QualificationError) as raised: qualification.validate_expected_set(value, self.manifest)
        self.assertEqual(raised.exception.code, "duplicate_expected_item")

    def test_parent_ledger_hash_must_be_digest(self) -> None:
        value = copy.deepcopy(self.ledger); value["parent_ledger_hash"] = "recent-file"
        self.assert_ledger_code("invalid_hash", value)


if __name__ == "__main__":
    unittest.main()
