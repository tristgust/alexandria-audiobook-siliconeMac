from __future__ import annotations

import ast
import copy
from dataclasses import replace
import tempfile
import unittest
from pathlib import Path

import cast_aggregate
import instruction_propagation
import recurring_voice_routing
import synthesis_windows

from capability_truth import (
    CapabilityTruthError,
    audit_capability_truth,
    audit_engine_record_truth,
)
from engine_artifact_admission import ArtifactAdmissionError, admit_engine_artifacts
from model_registry import (
    EngineRecordValidationError,
    engine_component_record_payload,
    engine_record_fingerprint,
    engine_record_payload,
    migrate_legacy_engine_record,
    registered_models,
    validate_engine_component_record,
)


ROOT = Path(__file__).resolve().parents[1]
ENGINE_CONSUMER_CLOSURE_PATHS = (
    "app/noncore_quasi_emotive_voices.py",
    "app/production_prompt_routes.py",
    "app/chris_roz_recurring_voices.py",
    "app/original_sin_overlap_completion.py",
    "app/approved_audio_promotion.py",
    "app/audio_generation_policy.py",
    "app/project_flow.py",
    "app/responsive_voice_backend.py",
    "app/recurring_voice_routing.py",
    "app/app.py",
    "app/synthesis_windows.py",
)


class CapabilityTruthTests(unittest.TestCase):
    def statuses(self) -> list[dict]:
        return [
            {
                "model": spec.as_dict(),
                "cached": spec.key in {
                    "mlx_clone",
                    "mlx_custom_voice",
                    "mlx_voice_design",
                    "mlx_controlled_clone",
                    "pytorch_qwen_base",
                },
                "state": "cached" if spec.key in {
                    "mlx_clone",
                    "mlx_custom_voice",
                    "mlx_voice_design",
                    "mlx_controlled_clone",
                    "pytorch_qwen_base",
                } else "missing",
            }
            for spec in registered_models()
        ]

    def capabilities(self) -> dict:
        return {
            "lora_training_supported": False,
            "training_action_enabled": False,
            "lora_inference_supported": False,
            "expressive_clone": {
                "supported": False,
                "model_cached": True,
                "per_line_instruction_supported": False,
                "acceptance": {"manual_audio_review_status": "pending"},
            },
            "experimental_lora_sidecar": {
                "merged_mlx_inference_technically_validated": False,
                "installed_artifact_count": 0,
            },
            "environment": {
                "pytorch_base_model_cached": True,
                "mlx_models_cached": {
                    "clone": True,
                    "custom_voice": True,
                    "voice_design": True,
                    "controlled_clone_voxcpm2": True,
                },
            },
        }

    def test_repository_capability_truth_passes(self) -> None:
        result = audit_capability_truth(
            repository_root=ROOT,
            capabilities=self.capabilities(),
            model_statuses=self.statuses(),
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["registry_count"], len(registered_models()))

    def test_commission_and_phantom_claims_fail(self) -> None:
        capabilities = self.capabilities()
        capabilities["environment"]["mlx_models_cached"]["clone"] = False
        statuses = self.statuses() + [
            {"model": {"key": "phantom_model"}, "cached": True, "state": "cached"}
        ]
        with self.assertRaises(CapabilityTruthError) as caught:
            audit_capability_truth(
                repository_root=ROOT,
                capabilities=capabilities,
                model_statuses=statuses,
            )
        kinds = {item["kind"] for item in caught.exception.issues}
        self.assertIn("commission", kinds)
        self.assertIn("phantom", kinds)

    def test_omission_and_unsupported_ready_fail(self) -> None:
        capabilities = self.capabilities()
        del capabilities["environment"]["mlx_models_cached"]["voice_design"]
        capabilities["training_action_enabled"] = True
        capabilities["expressive_clone"].update(
            {
                "supported": True,
                "model_cached": False,
                "per_line_instruction_supported": False,
            }
        )
        with self.assertRaises(CapabilityTruthError) as caught:
            audit_capability_truth(
                repository_root=ROOT,
                capabilities=capabilities,
                model_statuses=self.statuses(),
            )
        kinds = {item["kind"] for item in caught.exception.issues}
        self.assertIn("omission", kinds)
        self.assertIn("unsupported_ready", kinds)

    def test_orphan_runtime_binding_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "app/static").mkdir(parents=True)
            (root / "app/static/canonical_interface.js").write_text(
                "required_by_default missing_required_paths data-maintenance-model-action",
                encoding="utf-8",
            )
            with self.assertRaises(CapabilityTruthError) as caught:
                audit_capability_truth(
                    repository_root=root,
                    capabilities=self.capabilities(),
                    model_statuses=self.statuses(),
                )
        self.assertIn(
            "orphan",
            {item["kind"] for item in caught.exception.issues},
        )

    def test_authoritative_record_has_no_consumer_drift(self) -> None:
        first = audit_engine_record_truth()
        second = audit_engine_record_truth()
        self.assertTrue(first["passed"])
        self.assertEqual(first["record_fingerprint"], second["record_fingerprint"])

    def test_in_scope_consumers_do_not_copy_engine_selection_ids(self) -> None:
        selection_ids = {
            item["engine_id"]
            for item in engine_component_record_payload()["engines"]
        } | {recurring_voice_routing.ROUTED_CLONE_BACKEND}
        duplicates = []
        for relative_path in ENGINE_CONSUMER_CLOSURE_PATHS:
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(source, filename=relative_path)):
                if isinstance(node, ast.Constant) and node.value in selection_ids:
                    duplicates.append(
                        f"{relative_path}:{node.lineno}:{node.value}"
                    )
        self.assertEqual([], duplicates)

    def test_responsive_route_protocol_ids_are_not_engine_capability_ids(self) -> None:
        engine_ids = {
            item["engine_id"]
            for item in engine_component_record_payload()["engines"]
        }
        route_protocol_ids = {
            recurring_voice_routing.FISH_ROUTE_BACKEND_ID,
            recurring_voice_routing.INDEXTTS2_ROUTE_BACKEND_ID,
            recurring_voice_routing.VOXCPM2_ROUTE_BACKEND_ID,
        }
        self.assertTrue(route_protocol_ids.isdisjoint(engine_ids))

    def test_altered_live_synthesis_declaration_is_detected(self) -> None:
        original = synthesis_windows._WINDOWS["qwen3_base"]
        synthesis_windows._WINDOWS["qwen3_base"] = replace(
            original,
            max_chars=original.max_chars + 1,
        )
        try:
            with self.assertRaises(CapabilityTruthError) as caught:
                audit_engine_record_truth()
        finally:
            synthesis_windows._WINDOWS["qwen3_base"] = original
        self.assertIn(
            "synthesis_mismatch",
            {item["kind"] for item in caught.exception.issues},
        )

    def test_altered_cast_consumer_binding_is_detected(self) -> None:
        original = cast_aggregate.CONTROLLED_CLONE_BACKENDS
        cast_aggregate.CONTROLLED_CLONE_BACKENDS = frozenset()
        try:
            with self.assertRaises(CapabilityTruthError) as caught:
                audit_engine_record_truth()
        finally:
            cast_aggregate.CONTROLLED_CLONE_BACKENDS = original
        self.assertIn(
            "registry_consumer_missing",
            {item["kind"] for item in caught.exception.issues},
        )

    def test_unknown_cast_consumer_binding_is_detected(self) -> None:
        original = cast_aggregate.CONTROLLED_CLONE_BACKENDS
        cast_aggregate.CONTROLLED_CLONE_BACKENDS = original | {
            "bogus_extra_engine"
        }
        try:
            with self.assertRaises(CapabilityTruthError) as caught:
                audit_engine_record_truth()
        finally:
            cast_aggregate.CONTROLLED_CLONE_BACKENDS = original
        self.assertIn(
            "registry_consumer_extra",
            {item["kind"] for item in caught.exception.issues},
        )

    def test_unknown_live_synthesis_engine_is_structured_failure(self) -> None:
        synthesis_windows._WINDOWS["bogus_extra_engine"] = replace(
            synthesis_windows._WINDOWS["qwen3_base"],
            backend_id="bogus_extra_engine",
        )
        try:
            with self.assertRaises(CapabilityTruthError) as caught:
                audit_engine_record_truth()
        finally:
            del synthesis_windows._WINDOWS["bogus_extra_engine"]
        self.assertIn(
            "registry_consumer_extra",
            {item["kind"] for item in caught.exception.issues},
        )

    def test_altered_instruction_projection_is_detected(self) -> None:
        original = instruction_propagation.INSTRUCTION_FORMATTER
        instruction_propagation.INSTRUCTION_FORMATTER = "wrong_formatter"
        try:
            with self.assertRaises(CapabilityTruthError) as caught:
                audit_engine_record_truth()
        finally:
            instruction_propagation.INSTRUCTION_FORMATTER = original
        self.assertIn(
            "instruction_mismatch",
            {item["kind"] for item in caught.exception.issues},
        )

    def test_authoritative_record_artifact_drift_matrix(self) -> None:
        catalog = engine_component_record_payload()
        ledger = []

        duplicate = copy.deepcopy(catalog)
        duplicate["engines"].append(copy.deepcopy(duplicate["engines"][0]))
        with self.assertRaises(EngineRecordValidationError):
            validate_engine_component_record(duplicate)
        ledger.append("duplicate_id")
        unknown = engine_record_payload("qwen3_base")
        unknown["unknown"] = True
        with self.assertRaises(EngineRecordValidationError):
            migrate_legacy_engine_record(unknown)
        ledger.append("unknown_field")

        engine = engine_record_payload("voxcpm2_controlled")
        artifacts = []
        for component in engine["components"]:
            for declaration in component["artifacts"]:
                artifacts.append(
                    {
                        "artifact_id": f'{component["component_id"]}:{declaration["path"]}',
                        "component_id": component["component_id"],
                        "component_revision": component["revision"],
                        "component_build_id": component["build_id"],
                        "source_id": component["source_id"],
                        "role": declaration["role"],
                        "path": declaration["path"],
                        "size": 0,
                        "sha256": "0" * 64,
                        "runtime": component["runtime"],
                        "loader": component["loader"],
                        "serialization": declaration["serialization"],
                    }
                )
        manifest = {
            "schema_version": 1,
            "engine_id": engine["engine_id"],
            "engine_revision": engine["engine_revision"],
            "record_fingerprint": engine_record_fingerprint(engine),
            "artifacts": artifacts,
        }
        for case in (
            "stale_artifact",
            "incompatible_tokenizer",
            "incompatible_codec",
            "incompatible_adapter",
            "incompatible_loader",
            "unsafe_serialization",
        ):
            changed_manifest = copy.deepcopy(manifest)
            if case == "stale_artifact":
                changed_manifest["record_fingerprint"] = "0" * 64
            elif case == "unsafe_serialization":
                changed_manifest["artifacts"][0]["serialization"] = "pickle"
            else:
                changed_manifest["artifacts"][0]["loader"] = case
            with self.assertRaises(ArtifactAdmissionError):
                admit_engine_artifacts(changed_manifest, "missing", "unused")
            ledger.append(case)

        migrated = migrate_legacy_engine_record(engine_record_payload("qwen3_base"))
        self.assertEqual(migrated, migrate_legacy_engine_record(migrated))
        ledger.append("migration_idempotency")
        print(" ".join(ledger))


if __name__ == "__main__":
    unittest.main()
