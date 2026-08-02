from __future__ import annotations

import copy
import json
import random
import unittest

import model_registry


class EngineCapabilityRecordTests(unittest.TestCase):
    def test_qwen_and_voxcpm_records_are_authoritative_and_migrated(self) -> None:
        self.assertTrue(
            hasattr(model_registry, "ENGINE_COMPONENT_RECORD_SCHEMA_VERSION")
        )
        qwen = model_registry.engine_record_payload("qwen3_base")
        voxcpm = model_registry.engine_record_payload("voxcpm2_controlled")

        self.assertEqual(qwen["migration_state"], "migrated")
        self.assertEqual(voxcpm["migration_state"], "migrated")
        self.assertRegex(qwen["engine_revision"], r"^[0-9a-f]{40}$")
        self.assertRegex(voxcpm["engine_revision"], r"^[0-9a-f]{40}$")
        self.assertEqual(qwen["synthesis_window"]["backend_id"], "qwen3_base")
        self.assertEqual(
            voxcpm["synthesis_window"]["seam_mode"],
            "discard_overlap",
        )

    def test_model_specs_are_record_derived_compatibility_views(self) -> None:
        model_component_ids = {
            component["component_id"]
            for component in model_registry.engine_component_record_payload()[
                "components"
            ]
            if component["role"] == "model"
        }
        self.assertEqual(
            {spec.key for spec in model_registry.registered_models()},
            model_component_ids,
        )
        components = model_registry.engine_component_record_payload()["components"]
        for spec in model_registry.registered_models():
            with self.subTest(spec=spec.key):
                component = model_registry.component_record_payload(spec.key)
                self.assertEqual(spec.repo_id, component["source_id"])
                self.assertEqual(spec.revision, component["revision"])
                recorded_paths = {
                    path
                    for declaration in components
                    if declaration["source_id"] == spec.repo_id
                    and declaration["revision"] == spec.revision
                    for path in declaration["required_paths"]
                }
                self.assertEqual(set(spec.required_paths), recorded_paths)

    def test_qwen_and_voxcpm_supporting_components_have_exact_identity(self) -> None:
        qwen = model_registry.engine_record_payload("qwen3_base")
        voxcpm = model_registry.engine_record_payload("voxcpm2_controlled")
        qwen_roles = {component["role"] for component in qwen["components"]}
        voxcpm_roles = {component["role"] for component in voxcpm["components"]}
        self.assertEqual(qwen_roles, {"model", "tokenizer", "codec", "auxiliary"})
        self.assertEqual(voxcpm_roles, {"model", "tokenizer"})
        for engine in (qwen, voxcpm):
            for component in engine["components"]:
                with self.subTest(component=component["component_id"]):
                    self.assertRegex(component["revision"], r"^[0-9a-f]{40}$")
                    self.assertRegex(component["build_id"], r"^[0-9a-f]{64}$")
                    self.assertTrue(component["source_id"])
                    self.assertTrue(component["required_paths"])

    def test_duplicate_ids_fail_closed(self) -> None:
        payload = model_registry.engine_component_record_payload()
        duplicate_engine = copy.deepcopy(payload)
        duplicate_engine["engines"].append(copy.deepcopy(payload["engines"][0]))
        with self.assertRaises(model_registry.EngineRecordValidationError) as caught:
            model_registry.validate_engine_component_record(duplicate_engine)
        self.assertEqual(caught.exception.code, "duplicate_engine_id")

        duplicate_component = copy.deepcopy(payload)
        duplicate_component["components"].append(
            copy.deepcopy(payload["components"][0])
        )
        with self.assertRaises(model_registry.EngineRecordValidationError) as caught:
            model_registry.validate_engine_component_record(duplicate_component)
        self.assertEqual(caught.exception.code, "duplicate_component_id")

    def test_unknown_fields_fail_closed(self) -> None:
        payload = model_registry.engine_record_payload("qwen3_base")
        payload["untrusted_extra"] = True
        with self.assertRaises(model_registry.EngineRecordValidationError) as caught:
            model_registry.migrate_legacy_engine_record(payload)
        self.assertEqual(caught.exception.code, "unknown_field")

    def test_migration_is_idempotent(self) -> None:
        payload = model_registry.engine_record_payload("qwen3_base")
        first = model_registry.migrate_legacy_engine_record(payload)
        second = model_registry.migrate_legacy_engine_record(first)
        canonical = lambda value: json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertEqual(canonical(first), canonical(second))
        self.assertEqual(
            model_registry.engine_record_fingerprint(first),
            model_registry.engine_record_fingerprint(second),
        )

    def test_actual_legacy_engine_and_component_shapes_migrate_deterministically(self) -> None:
        legacy_engine = {
            "schema_version": 1,
            "backend_id": "qwen3_base",
            "family": "qwen3",
            "max_chars": 180,
            "max_words": 14,
            "minimum_words": 2,
            "seam_mode": "silence_gap",
            "seam_ms": 100,
            "split_priority": ["paragraph", "sentence", "clause", "word", "character"],
        }
        expected_engine = model_registry.engine_record_payload("qwen3_base")
        legacy_component = model_registry.model_spec("mlx_clone").as_dict()
        expected_component = model_registry.component_record_payload("mlx_clone")

        self.assertEqual(
            model_registry.migrate_legacy_engine_record(legacy_engine),
            expected_engine,
        )
        self.assertEqual(
            model_registry.migrate_legacy_component_record(legacy_component),
            expected_component,
        )
        shuffled = list(legacy_engine.items())
        random.Random(20260801).shuffle(shuffled)
        self.assertEqual(
            model_registry.migrate_legacy_engine_record(dict(shuffled)),
            expected_engine,
        )
        self.assertEqual(
            model_registry.migrate_legacy_engine_record(expected_engine),
            expected_engine,
        )

    def test_legacy_migration_rejects_unknown_or_unsupported_shapes(self) -> None:
        legacy = {
            "schema_version": 1,
            "backend_id": "unknown",
            "family": "qwen3",
            "max_chars": 180,
            "max_words": 14,
            "minimum_words": 2,
            "seam_mode": "silence_gap",
            "seam_ms": 100,
            "split_priority": ["paragraph", "sentence", "clause", "word", "character"],
        }
        with self.assertRaises(model_registry.EngineRecordValidationError) as caught:
            model_registry.migrate_legacy_engine_record(legacy)
        self.assertEqual(caught.exception.code, "unknown_engine")
        legacy["backend_id"] = "qwen3_base"
        legacy["unexpected"] = True
        with self.assertRaises(model_registry.EngineRecordValidationError) as caught:
            model_registry.migrate_legacy_engine_record(legacy)
        self.assertEqual(caught.exception.code, "unknown_field")

    def test_unsupported_ready_fails_closed(self) -> None:
        payload = model_registry.engine_record_payload("external_generic")
        payload["readiness"] = "ready"
        with self.assertRaises(model_registry.EngineRecordValidationError) as caught:
            model_registry.migrate_legacy_engine_record(payload)
        self.assertEqual(caught.exception.code, "unsupported_ready")

    def test_synthesis_instruction_offline_mismatch_fails_closed(self) -> None:
        cases = (
            ("synthesis_window", {"backend_id": "wrong"}, "synthesis_mismatch"),
            ("instruction", {"mode": "per_record", "supported": False}, "instruction_mismatch"),
            ("offline", {"local_only": False}, "offline_mismatch"),
        )
        for field, mutation, code in cases:
            with self.subTest(code=code):
                payload = model_registry.engine_record_payload("qwen3_base")
                payload[field].update(mutation)
                with self.assertRaises(
                    model_registry.EngineRecordValidationError
                ) as caught:
                    model_registry.migrate_legacy_engine_record(payload)
                self.assertEqual(caught.exception.code, code)

    def test_closure_guards_reject_dirty_zero_test_and_timeout(self) -> None:
        def closes(porcelain: str, output: str, timed_out: bool) -> bool:
            return not porcelain and "Ran 0 tests" not in output and not timed_out

        self.assertFalse(closes("1 .M N... app/model_registry.py", "OK", False))
        self.assertFalse(closes("", "Ran 0 tests\nOK", False))
        self.assertFalse(closes("", "Ran 1 test\nOK", True))


if __name__ == "__main__":
    unittest.main()
