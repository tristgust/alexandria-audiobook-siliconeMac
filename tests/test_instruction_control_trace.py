from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "benchmarks" / "run_instruction_control_trace.py"


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "alexandria_instruction_control_trace",
        RUNNER,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class InstructionControlTraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = load_runner()

    def test_actual_tts_routing_distinguishes_three_clone_contracts(self) -> None:
        trace = self.runner.request_trace()
        standard = trace["standard_clone"]
        self.assertEqual(standard["backend"], "qwen3_base")
        self.assertTrue(standard["line_instruction_supplied_to_tts"])
        self.assertFalse(standard["instruction_forwarded_to_backend"])
        self.assertNotIn("instruct", standard["call_keys"])

        controlled = trace["controlled_clone"]
        self.assertEqual(controlled["backend"], "qwen3_instruction_controlled")
        self.assertEqual(controlled["request_count"], 2)
        self.assertTrue(controlled["contrasting_combined_instructions_differ"])
        for request in controlled["requests"]:
            self.assertTrue(request["contains_line_instruction"])
            self.assertTrue(request["contains_identity_constraint"])
            self.assertEqual(request["request_label"], "CONTROLLED")
            self.assertEqual(request["seed"], 314159)

        legacy = trace["legacy_clone"]
        self.assertEqual(legacy["backend"], "voxcpm2_controlled")
        self.assertTrue(legacy["production_blocked"])
        self.assertIsNotNone(legacy["error_sha256"])

    def test_embedding_trace_proves_no_neutral_and_contrasting_positions(self) -> None:
        trace = self.runner.embedding_trace()
        self.assertEqual(
            trace["ordering"],
            "instruction_embedding_then_original_icl_prefill",
        )
        self.assertTrue(trace["exactly_once"])
        self.assertTrue(trace["contrasting_embeddings_differ"])
        self.assertTrue(trace["original_icl_prefill_unchanged"])
        self.assertEqual(trace["cases"]["none"]["instruction_token_count"], 0)
        self.assertEqual(trace["cases"]["neutral"]["instruction_token_count"], 4)
        self.assertEqual(
            trace["cases"]["contrasting"]["instruction_token_count"],
            4,
        )
        self.assertNotEqual(
            trace["cases"]["neutral"]["instruction_embedding_sha256"],
            trace["cases"]["contrasting"]["instruction_embedding_sha256"],
        )

    def test_source_position_proof_matches_official_qwen_order(self) -> None:
        proof = self.runner.source_position_proof(ROOT)
        tts = proof["tts_combines_line_and_identity"]
        mlx = proof["mlx_sets_request_local_instruction"]
        installed = proof["installed_mlx_base_drops_public_instruct_for_icl"]
        official = proof[
            "official_pytorch_orders_instruction_before_tts_prompt"
        ]
        self.assertIsInstance(tts["line"], int)
        self.assertIsInstance(tts["call_line"], int)
        self.assertLess(tts["line"], tts["call_line"])
        self.assertIsInstance(mlx["set_line"], int)
        self.assertIsInstance(mlx["prepend_line"], int)
        self.assertIsInstance(mlx["clear_line"], int)
        self.assertIsInstance(mlx["post_processing_line"], int)
        self.assertIsInstance(installed["public_instruct_argument_line"], int)
        self.assertIsInstance(installed["icl_call_line"], int)
        self.assertIsInstance(official["instruction_append_line"], int)
        self.assertIsInstance(official["tts_prompt_append_line"], int)
        self.assertLess(
            official["instruction_append_line"],
            official["tts_prompt_append_line"],
        )

    def test_current_checkout_classification_is_configuration_and_policy_specific(
        self,
    ) -> None:
        trace = self.runner.build_trace(ROOT)
        classification = trace["classification"]
        self.assertTrue(classification["shared_request_path_intact"])
        self.assertFalse(
            classification["shared_request_path_drops_instruction"]
        )
        self.assertEqual(
            classification["primary_cause"],
            "configuration_and_backend_policy_specific",
        )
        self.assertEqual(classification["active_controlled_assignment_count"], 0)
        self.assertGreater(
            classification[
                "active_standard_instruction_inert_assignment_count"
            ],
            0,
        )
        self.assertGreater(
            classification["active_legacy_blocked_assignment_count"],
            0,
        )
        self.assertFalse(
            classification["reference_specific_for_historical_doctor_voice"]
        )
        self.assertFalse(classification["model_directionality_accepted"])
        self.assertTrue(
            classification[
                "production_acoustic_difference_confounded_by_post_processing"
            ]
        )
        current = trace["active_assignments"]
        self.assertEqual(
            current["backend_counts"].get("qwen3_instruction_controlled", 0),
            0,
        )
        historical = trace["evidence_comparison"]["legacy_voxcpm2"]
        self.assertTrue(historical["outputs_differ"])
        self.assertIn("THE DOCTOR", historical["matching_current_speakers"])
        self.assertFalse(historical["current_production_support"])

    def test_cli_writes_read_only_trace_without_audio_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "trace.json"
            result = self.runner.build_trace(ROOT)
            output.write_text(json.dumps(result), encoding="utf-8")
            loaded = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(
            loaded["run_kind"],
            "instruction_control_regression_trace",
        )
        self.assertFalse(
            loaded["classification"]["shared_request_path_drops_instruction"]
        )
        source = RUNNER.read_text(encoding="utf-8")
        self.assertNotIn("snapshot_download", source)
        self.assertNotIn("model.generate(", source)
        self.assertNotIn("from delivery_prosody import", source)


if __name__ == "__main__":
    unittest.main()
