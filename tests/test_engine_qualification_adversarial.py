from __future__ import annotations

import copy
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from typing import Callable
from unittest.mock import patch

import engine_qualification as qualification
import engine_qualification_review as review


class EngineQualificationAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        result = qualification.initial_qualification("qwen3_base")
        self.manifest = result["manifest"]
        self.expected = result["expected_set"]
        self.ledger = result["ledger"]
        self.stage_results = result["stage_results"]
        self.receipt = result["receipt"]
        self.publication = qualification.prepare_publication(self.manifest, self.expected, self.ledger, self.stage_results, self.receipt)

    def assert_code(self, code: str, callback: Callable[[], object]) -> None:
        with self.assertRaises(qualification.QualificationError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)

    def test_strict_json_rejects_duplicate_keys_and_invalid_encoding(self) -> None:
        self.assert_code("duplicate_key", lambda: qualification.strict_json_loads('{"a":1,"a":2}'))
        self.assert_code("bom_forbidden", lambda: qualification.strict_json_loads(b"\xef\xbb\xbf{}"))
        self.assert_code("invalid_utf8", lambda: qualification.strict_json_loads(b"\xff"))
        self.assert_code("invalid_test_receipt", lambda: qualification._validate_test_log("Ran 0 tests in 0.000s\nOK\n", 87))
        self.assert_code("invalid_test_receipt", lambda: qualification._validate_test_log("TimeoutExpired\n", 87))

    def test_float_values_are_rejected_from_json_and_python(self) -> None:
        self.assert_code("float_forbidden", lambda: qualification.strict_json_loads('{"a":1.5}'))
        self.assert_code("float_forbidden", lambda: qualification.canonical_bytes({"a": 1.0}))

    def test_json_nan_rejected(self) -> None:
        self.assert_code("non_finite", lambda: qualification.strict_json_loads('{"a":NaN}'))

    def test_json_trailing_data_rejected(self) -> None:
        self.assert_code("invalid_json", lambda: qualification.strict_json_loads('{}{}'))
        guard = qualification.OfflineCallGuard()
        with guard:
            self.assert_code("offline_call_blocked", guard.provider_call)
            self.assert_code("offline_call_blocked", guard.model_call)
            self.assert_code("offline_call_blocked", guard.model_load)
            self.assert_code("offline_call_blocked", guard.download)
            self.assert_code("offline_call_blocked", lambda: __import__("socket").socket())
        self.assertEqual(guard.counts(), {"network": 1, "provider": 1, "model": 1, "model_load": 1, "download": 1, "unstubbed_calls": 5})

    def test_module_tables_cannot_authorize_forged_material(self) -> None:
        self.assert_code("non_canonical_text", lambda: qualification.canonical_bytes("e\u0301"))
        unsupported = copy.deepcopy(self.stage_results); unsupported[0]["state"] = "passed"
        self.assert_code("unsupported_stage_pass", lambda: qualification.prepare_publication(self.manifest, self.expected, self.ledger, unsupported, self.receipt))
        forbidden_tables = ("_VERIFIED_IMPORTED_EVIDENCE", "_VERIFIED_DECISIONS", "_VALIDATED_PUBLICATIONS")
        self.assertTrue(all(name not in vars(qualification) for name in forbidden_tables))
        fixture_root = Path(__file__).parent / "fixtures" / "engine_qualification"
        imported = qualification.verify_imported_evidence(fixture_root)
        parent = "8f2e98bde6376caa7b3690c0f50f78ee592a1197"
        forged_import = qualification.ImportedEvidenceMaterial(
            imported.fixture_root, parent, imported.source_hashes, "0" * 64, imported.supported_stage_ids,
        )
        unsigned_decision = {
            "schema_version": 1, "decision_kind": "reject", "subject_id": self.manifest["subject_id"],
            "record_fingerprint": self.manifest["record_fingerprint"],
            "record_projection_hash": qualification.canonical_hash(self.manifest["record_projections"]),
            "profile_hash": self.manifest["metric_profile_hash"], "package_hash": None, "result_hash": None,
            "reviewer_id": "forged", "nonce": "forged", "issued_ns": 0,
        }
        forged_decision = qualification.TrustedDecisionMaterial(
            qualification.canonical_bytes(unsigned_decision), "missing-signature", "missing-signers", "forged", "missing-nonce-ledger", "authoritative_existing",
        )
        forged_receipt = copy.deepcopy(self.receipt)
        forged_receipt["final_disposition"] = "production_accepted"
        forged_receipt["receipt_hash"] = qualification.canonical_hash({key: item for key, item in forged_receipt.items() if key != "receipt_hash"})
        forged_publication = qualification.PublicationBundle(
            self.publication.manifest_bytes, self.publication.expected_set_bytes, self.publication.ledger_bytes,
            self.publication.stage_results_bytes, qualification.canonical_bytes(forged_receipt), None, None,
        )
        try:
            for name, forged in zip(forbidden_tables, (forged_import, forged_decision, forged_publication), strict=True):
                setattr(qualification, name, {id(forged): forged})
            with self.subTest("imported evidence table poisoning"):
                self.assert_code("imported_evidence_drift", lambda: qualification.build_manifest("qwen3_base", verified_import=forged_import))
            with self.subTest("decision table poisoning"):
                self.assert_code("unsafe_trust_path", lambda: qualification.derive_disposition(self.manifest, self.stage_results, trusted_decision=forged_decision))
            with self.subTest("publication table poisoning"):
                with tempfile.TemporaryDirectory() as temporary:
                    self.assert_code("untrusted_disposition", lambda: qualification.publish_receipt(temporary, forged_publication, recovery_token="forged"))
                    self.assertFalse((Path(temporary) / "HEAD").exists())
        finally:
            for name in forbidden_tables:
                vars(qualification).pop(name, None)
        for _ in range(8):
            qualification.verify_imported_evidence(fixture_root)
            qualification.prepare_publication(self.manifest, self.expected, self.ledger, self.stage_results, self.receipt)
        self.assertTrue(all(name not in vars(qualification) for name in forbidden_tables))

    def test_unregistered_subject_rejected(self) -> None:
        self.assert_code("unregistered_subject", lambda: qualification.build_manifest("not_registered"))

    def test_unknown_expected_stage_rejected(self) -> None:
        value = copy.deepcopy(self.expected); value["items"][0]["stage_id"] = "unknown"
        self.assert_code("unknown_stage", lambda: qualification.validate_expected_set(value, self.manifest))
        missing = copy.deepcopy(self.expected); missing["items"].pop()
        altered = copy.deepcopy(self.expected); altered["items"][0]["fixture_id"] = "fixture:cross-subject"
        reordered = copy.deepcopy(self.expected); reordered["items"][:2] = reversed(reordered["items"][:2])
        for candidate in (missing, altered, reordered):
            self.assert_code("expected_set_definition_mismatch", lambda candidate=candidate: qualification.validate_expected_set(candidate, self.manifest))

    def test_boolean_source_span_rejected(self) -> None:
        value = copy.deepcopy(self.expected); value["items"][0]["source_span"]["start"] = True
        self.assert_code("invalid_source_span", lambda: qualification.validate_expected_set(value, self.manifest))

    def test_path_traversal_rejected(self) -> None:
        self.assert_code("unsafe_path", lambda: qualification._safe_relative("../receipt.json"))

    def test_receipt_unknown_field_rejected(self) -> None:
        value = copy.deepcopy(self.receipt); value["extra"] = True
        self.assert_code("unknown_field", lambda: qualification.validate_receipt(value))

    def test_receipt_tamper_rejected(self) -> None:
        value = copy.deepcopy(self.receipt); value["subject_id"] = "mlx_whisper_base"
        self.assert_code("tampered_receipt", lambda: qualification.validate_receipt(value))
        forged = copy.deepcopy(self.receipt); forged["final_disposition"] = "production_accepted"; forged["receipt_hash"] = qualification.canonical_hash({key: item for key, item in forged.items() if key != "receipt_hash"})
        self.assertEqual(qualification.validate_receipt(forged), forged)
        forged_bundle = qualification.PublicationBundle(
            self.publication.manifest_bytes, self.publication.expected_set_bytes, self.publication.ledger_bytes,
            self.publication.stage_results_bytes, qualification.canonical_bytes(forged), None, None,
        )
        with tempfile.TemporaryDirectory() as temporary:
            self.assert_code("unverified_publication", lambda: qualification.publish_receipt(temporary, forged, recovery_token="forged"))
            self.assert_code("untrusted_disposition", lambda: qualification.publish_receipt(temporary, forged_bundle, recovery_token="forged"))

    def test_receipt_publication_succeeds_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = qualification.publish_receipt(temporary, self.publication, recovery_token="owned")
            self.assertEqual((result.status, result.publication_count), ("published", 1))
            owner = qualification.canonical_bytes({
                "qualification_id": self.receipt["qualification_id"],
                "parent_hash": self.receipt["parent_receipt_hash"],
                "receipt_hash": self.receipt["receipt_hash"],
                "recovery_token_hash": hashlib.sha256(b"owned").hexdigest(),
            })
            (Path(temporary) / ".publish.lock").write_bytes(owner)
            recovered = qualification.publish_receipt(temporary, self.publication, recovery_token="owned")
            self.assertEqual((recovered.status, recovered.publication_count), ("idempotent", 1))

    def test_receipt_publication_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            qualification.publish_receipt(temporary, self.publication, recovery_token="owned")
            result = qualification.publish_receipt(temporary, self.publication, recovery_token="owned")
            self.assertEqual((result.status, result.publication_count), ("idempotent", 1))

    def test_cancel_before_rename_publishes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self.assert_code("cancelled_before_rename", lambda: qualification.publish_receipt(temporary, self.publication, recovery_token="owned", interrupt_at="before_rename"))
            self.assertFalse((Path(temporary) / "HEAD").exists())

    def test_interruption_after_rename_recovers_owned_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self.assert_code("interrupted_after_rename", lambda: qualification.publish_receipt(temporary, self.publication, recovery_token="owned", interrupt_at="after_rename"))
            result = qualification.publish_receipt(temporary, self.publication, recovery_token="owned")
            self.assertEqual((result.status, result.publication_count), ("idempotent", 1))

    def test_cancel_after_head_is_already_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = qualification.publish_receipt(temporary, self.publication, recovery_token="owned", interrupt_at="after_head")
            self.assertEqual((result.status, result.publication_count), ("already_terminal", 1))

    def test_parent_receipt_fork_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            qualification.publish_receipt(temporary, self.publication, recovery_token="owned")
            child = qualification.build_receipt(
                self.manifest, self.expected, self.ledger, self.stage_results, "deferred",
                parent_receipt_hash=self.receipt["receipt_hash"],
            )
            child_publication = qualification.prepare_publication(self.manifest, self.expected, self.ledger, self.stage_results, child)
            appended = qualification.publish_receipt(temporary, child_publication, recovery_token="owned-2")
            self.assertEqual((appended.status, appended.publication_count), ("published", 2))
            other = qualification.build_receipt(self.manifest, self.expected, self.ledger, self.stage_results, "deferred", parent_receipt_hash="0" * 64)
            other_publication = qualification.prepare_publication(self.manifest, self.expected, self.ledger, self.stage_results, other)
            self.assert_code("receipt_parent_fork", lambda: qualification.publish_receipt(temporary, other_publication, recovery_token="owned-3"))

    def test_foreign_lock_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            (Path(temporary) / ".publish.lock").write_bytes(qualification.canonical_bytes({"owner": "foreign"}))
            self.assert_code("foreign_lock", lambda: qualification.publish_receipt(temporary, self.publication, recovery_token="owned"))

    def test_foreign_pending_tree_is_never_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pending = Path(temporary) / f".pending-{self.receipt['qualification_id'].replace(':', '_')}-{self.receipt['receipt_hash']}"
            pending.mkdir()
            (pending / "owner.json").write_bytes(qualification.canonical_bytes({"owner": "foreign"}))
            self.assert_code("foreign_lock", lambda: qualification.publish_receipt(temporary, self.publication, recovery_token="owned"))
            self.assertTrue((pending / "owner.json").is_file())

    def test_synthetic_manifest_cannot_build_persistent_receipt(self) -> None:
        manifest = qualification.build_manifest("qwen3_base", evidence_origin="synthetic_validation")
        expected = qualification.make_expected_set(manifest)
        ledger = qualification.make_ledger(manifest, expected)
        stages = [qualification.aggregate_stage(manifest, expected, ledger, stage) for stage in qualification.STAGE_IDS]
        self.assert_code("synthetic_publication_forbidden", lambda: qualification.build_receipt(manifest, expected, ledger, stages, "deferred"))

    def test_symlink_publication_root_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "target"; target.mkdir(); link = Path(temporary) / "link"; os.symlink(target, link)
            self.assert_code("unsafe_path", lambda: qualification.publish_receipt(link, self.publication, recovery_token="owned"))

    def test_existing_receipt_collision_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            qualification.publish_receipt(temporary, self.publication, recovery_token="owned")
            (Path(temporary) / self.receipt["receipt_hash"] / "receipt.json").write_text("{}", encoding="utf-8")
            self.assert_code("receipt_collision", lambda: qualification.publish_receipt(temporary, self.publication, recovery_token="owned"))

    def test_infinite_metric_json_rejected(self) -> None:
        self.assert_code("non_finite", lambda: qualification.strict_json_loads('{"value":Infinity}'))

    def test_noncanonical_metric_decimal_rejected(self) -> None:
        metric = {"id": "wer", "formula_version": "wer_v1", "unit": "ratio", "value_decimal": "1.0", "sample_count": 1, "confidence_level_decimal": None, "confidence_interval_decimal_pair": None, "limitation_codes": []}
        self.assert_code("invalid_decimal", lambda: qualification.validate_metric(metric))

    def test_duplicate_metric_limitations_rejected(self) -> None:
        metric = {"id": "wer", "formula_version": "wer_v1", "unit": "ratio", "value_decimal": "0", "sample_count": 1, "confidence_level_decimal": None, "confidence_interval_decimal_pair": None, "limitation_codes": ["single_fixture", "single_fixture"]}
        self.assert_code("invalid_limitations", lambda: qualification.validate_metric(metric))

    def test_expected_item_unknown_field_rejected(self) -> None:
        value = copy.deepcopy(self.expected); value["items"][0]["extra"] = True
        self.assert_code("unknown_field", lambda: qualification.validate_expected_set(value, self.manifest))

    def test_terminal_row_unknown_field_rejected(self) -> None:
        value = copy.deepcopy(self.ledger); value["rows"][0]["extra"] = True
        self.assert_code("unknown_field", lambda: qualification.validate_ledger(value, self.manifest, self.expected))

    def test_synthetic_decision_cannot_cross_user_boundary(self) -> None:
        decision = {
            "schema_version": 1, "decision_kind": "approve", "subject_id": self.manifest["subject_id"],
            "record_fingerprint": self.manifest["record_fingerprint"],
            "record_projection_hash": qualification.canonical_hash(self.manifest["record_projections"]),
            "profile_hash": self.manifest["metric_profile_hash"], "package_hash": None, "result_hash": None,
            "reviewer_id": "fixture_reviewer", "nonce": "fixture", "issued_ns": 0,
        }
        self.assert_code("synthetic_attestation_forbidden", lambda: qualification.verify_trusted_decision(
            decision, manifest=self.manifest, signature_path="missing", allowed_signers_path="missing",
            signer_identity="fixture", nonce_ledger_root="missing", package_hash=None, result_hash=None,
            evidence_origin="synthetic_validation",
        ))

    def test_trusted_decision_verifier_ignores_path_shadow(self) -> None:
        decision = {
            "schema_version": 1,
            "decision_kind": "approve",
            "subject_id": self.manifest["subject_id"],
            "record_fingerprint": self.manifest["record_fingerprint"],
            "record_projection_hash": qualification.canonical_hash(self.manifest["record_projections"]),
            "profile_hash": self.manifest["metric_profile_hash"],
            "package_hash": None,
            "result_hash": None,
            "reviewer_id": "fixture_reviewer",
            "nonce": "path-shadow-fixture",
            "issued_ns": 0,
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            signature = root / "decision.sig"
            signers = root / "allowed_signers"
            nonce_ledger = root / "nonces"
            shadow_bin = root / "shadow-bin"
            marker = root / "shadow-executed"
            signature.write_text("not a valid signature\n", encoding="utf-8")
            signers.write_text("fixture_reviewer ssh-ed25519 AAAAinvalid\n", encoding="utf-8")
            nonce_ledger.mkdir()
            shadow_bin.mkdir()
            shadow = shadow_bin / "ssh-keygen"
            shadow.write_text(
                "#!/bin/sh\nprintf shadow > " + repr(str(marker)) + "\nexit 0\n",
                encoding="utf-8",
            )
            shadow.chmod(0o755)
            with patch.dict(os.environ, {"PATH": str(shadow_bin)}):
                self.assert_code(
                    "signature_verification_failed",
                    lambda: qualification.verify_trusted_decision(
                        decision,
                        manifest=self.manifest,
                        signature_path=signature,
                        allowed_signers_path=signers,
                        signer_identity="fixture_reviewer",
                        nonce_ledger_root=nonce_ledger,
                        package_hash=None,
                        result_hash=None,
                    ),
                )
            self.assertFalse(marker.exists())

    def test_closure_commit_uses_git_sha_not_artifact_digest(self) -> None:
        git_sha = "a" * 40
        self.assertEqual(qualification._git_commit(git_sha), git_sha)
        self.assert_code("invalid_commit", lambda: qualification._git_commit("a" * 64))

    def test_public_review_identity_leak_rejected(self) -> None:
        package = review.build_review_package([{
            "expected_item_id": "private", "subject_id": "qwen3_base", "record_fingerprint": "a" * 64,
            "profile_hash": "b" * 64, "audio_identity": "audio", "required_playback": True, "restriction_options": [],
        }], "seed")
        package["public"]["items"][0]["subject_id"] = "qwen3_base"
        package["answer_key"]["package_hash"] = qualification.canonical_hash(package["public"])
        self.assert_code("public_identity_leak", lambda: review.validate_review_package(package["public"], package["answer_key"]))
        package = review.build_review_package([{
            "expected_item_id": "private", "subject_id": "qwen3_base", "record_fingerprint": "a" * 64,
            "profile_hash": "b" * 64, "audio_identity": "/private/qwen3_base.wav", "required_playback": True, "restriction_options": [],
        }], "seed")
        package["public"]["items"][0]["audio_identity"] = "/private/qwen3_base.wav"
        package["answer_key"]["package_hash"] = qualification.canonical_hash(package["public"])
        self.assert_code("public_identity_leak", lambda: review.validate_review_package(package["public"], package["answer_key"]))


if __name__ == "__main__":
    unittest.main()
