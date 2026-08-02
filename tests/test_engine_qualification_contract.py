from __future__ import annotations

import copy
import unittest

import engine_qualification as qualification
from model_registry import component_record_payload, engine_record_fingerprint, engine_record_payload


class EngineQualificationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = qualification.build_manifest("qwen3_base")

    def assert_code(self, code: str, value: dict[str, object]) -> None:
        with self.assertRaises(qualification.QualificationError) as raised:
            qualification.validate_manifest(value)
        self.assertEqual(raised.exception.code, code)

    def test_exact_ordered_stage_catalog(self) -> None:
        self.assertEqual(len(qualification.STAGE_IDS), 18)
        self.assertEqual(len(set(qualification.STAGE_IDS)), 18)
        self.assertEqual(self.manifest["applicable_stages"], list(qualification.STAGE_IDS))

    def test_exact_final_dispositions(self) -> None:
        self.assertEqual(len(qualification.FINAL_DISPOSITIONS), 10)

    def test_manifest_uses_authoritative_engine_fingerprint(self) -> None:
        record = engine_record_payload("qwen3_base")
        self.assertEqual(self.manifest["record_fingerprint"], engine_record_fingerprint(record))

    def test_component_subject_uses_authoritative_record(self) -> None:
        manifest = qualification.build_manifest("mlx_whisper_base")
        self.assertEqual(manifest["record_fingerprint"], engine_record_fingerprint(component_record_payload("mlx_whisper_base")))

    def test_record_projections_are_exact(self) -> None:
        self.assertEqual([item["json_pointer"] for item in self.manifest["record_projections"]], ["/engine_id", "/voice_methods", "/readiness"])

    def test_stale_record_fails_closed(self) -> None:
        value = copy.deepcopy(self.manifest)
        value["record_fingerprint"] = "0" * 64
        self.assert_code("stale_record", value)

    def test_unknown_field_fails_closed(self) -> None:
        value = copy.deepcopy(self.manifest)
        value["extra"] = True
        self.assert_code("unknown_field", value)

    def test_missing_stage_fails_closed(self) -> None:
        value = copy.deepcopy(self.manifest)
        value["applicable_stages"].pop()
        self.assert_code("stage_catalog_mismatch", value)

    def test_impossible_acceptance_fails_closed(self) -> None:
        value = copy.deepcopy(self.manifest)
        value["qualification_state"] = "blocked"
        value["final_disposition"] = "production_accepted"
        self.assert_code("impossible_disposition", value)

    def test_profile_hash_is_subject_locked(self) -> None:
        value = copy.deepcopy(self.manifest)
        value["metric_profile_hash"] = "0" * 64
        self.assert_code("metric_profile_subject_mismatch", value)

    def test_projection_overflow_fails_closed(self) -> None:
        value = copy.deepcopy(self.manifest)
        value["record_projections"].append(copy.deepcopy(value["record_projections"][0]))
        self.assert_code("record_projection_overflow", value)

    def test_canonical_manifest_bytes_are_stable(self) -> None:
        self.assertEqual(qualification.canonical_bytes(self.manifest), qualification.canonical_bytes(qualification.build_manifest("qwen3_base")))


if __name__ == "__main__":
    unittest.main()
