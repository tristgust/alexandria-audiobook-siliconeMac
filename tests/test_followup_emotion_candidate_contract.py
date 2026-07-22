from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_RUNNER = ROOT / "benchmarks" / "run_indextts2_emotion_probe.py"
COSY_RUNNER = ROOT / "benchmarks" / "run_cosyvoice3_instruct_probe.py"
INDEX_MATRIX = ROOT / "benchmarks" / "run_indextts2_finalist_matrix.py"
INDEX_POOL = ROOT / "benchmarks" / "run_indextts2_parallel_pool_probe.py"
INDEX_MLX_PROBE = ROOT / "benchmarks" / "probe_indextts2_mlx_gpt2_block.py"
EVALUATOR = ROOT / "benchmarks" / "evaluate_emotional_clone_outputs.py"
TRANSCRIPTION_EVALUATOR = ROOT / "benchmarks" / "transcription_evaluator.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FollowupEmotionCandidateContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = load_module("index_emotion_probe_contract", INDEX_RUNNER)
        cls.cosy = load_module("cosy_emotion_probe_contract", COSY_RUNNER)
        cls.index_matrix = load_module("index_finalist_matrix_contract", INDEX_MATRIX)

    def test_indextts2_controls_cover_vectors_and_text_instruction(self) -> None:
        self.assertEqual(
            set(self.index.EMOTION_CONTROLS),
            {
                "calm",
                "angry",
                "sad",
                "afraid",
                "melancholic",
                "happy",
                "surprised",
                "text_frightened_whisper",
            },
        )
        self.assertEqual(
            self.index.EMOTION_CONTROLS["angry"]["emo_vector"],
            [0, 0.8, 0, 0, 0, 0, 0, 0],
        )
        self.assertTrue(
            self.index.EMOTION_CONTROLS["text_frightened_whisper"][
                "use_emo_text"
            ]
        )

    def test_cosyvoice_controls_are_text_only(self) -> None:
        self.assertEqual(
            set(self.cosy.DIRECTIONS),
            {
                "neutral",
                "urgent",
                "controlled_anger",
                "fear",
                "grief",
                "excited",
            },
        )
        self.assertIn("terrified whisper", self.cosy.DIRECTIONS["fear"])
        self.assertIn("controlled anger", self.cosy.DIRECTIONS["controlled_anger"])

    def test_runners_are_fail_closed_and_do_not_download(self) -> None:
        for path in (INDEX_RUNNER, COSY_RUNNER):
            source = path.read_text(encoding="utf-8")
            self.assertIn('"production_promotion_allowed": False', source)
            self.assertIn('"expected_text": args.text', source)
            self.assertIn('"automatic_transcription_status": "unavailable"', source)
            self.assertNotIn("snapshot_download", source)
            self.assertNotIn("hf_hub_download", source)
            self.assertNotIn("modelscope", source)

    def test_indextts2_is_one_direction_per_process(self) -> None:
        source = INDEX_RUNNER.read_text(encoding="utf-8")
        self.assertIn('parser.add_argument("--direction", required=True', source)
        self.assertNotIn("for direction in", source)
        self.assertIn('output_dir / f"{args.direction}_{args.seed}.wav"', source)

    def test_followup_evaluator_exposes_transcript_and_expected_line(self) -> None:
        source = EVALUATOR.read_text(encoding="utf-8")
        self.assertIn('"expected_text": expected_text', source)
        self.assertIn('"automatic_transcript": automatic.get("transcript")', source)
        self.assertIn('"word_error_rate": automatic.get("word_error_rate")', source)
        self.assertIn('"production_promotion_allowed": False', source)
        self.assertIn('output_dir / "review.html"', source)
        self.assertIn('output_dir / "answer_key.json"', source)
        self.assertIn("review_key = hashlib.sha256", source)
        self.assertIn("alexandria-emotional-clone-review-{review_key}", source)
        self.assertIn('item.get("expected_text") or default_expected_text', source)
        self.assertIn('"expected_text_sha256_by_sample"', source)
        self.assertNotIn("snapshot_download", source)
        self.assertNotIn("hf_hub_download", source)

    def test_transcription_evaluator_supports_per_output_expected_text(self) -> None:
        source = TRANSCRIPTION_EVALUATOR.read_text(encoding="utf-8")
        self.assertIn('item.get("text") or default_expected_text', source)
        self.assertIn("Expected text is missing for sample", source)
        self.assertIn("word_error_rate(expected_text, transcript)", source)

    def test_indextts2_finalist_matrix_supports_identity_and_strength_scaling(self) -> None:
        identity = {
            "direction": "identity",
            "emotion_strength": None,
        }
        self.assertEqual(self.index_matrix.build_control(identity), {})

        sad = {
            "direction": "sad",
            "emotion_strength": 0.5,
        }
        control = self.index_matrix.build_control(sad)
        self.assertEqual(control["emo_vector"], [0, 0, 0.8, 0, 0, 0, 0, 0])
        self.assertEqual(control["emo_alpha"], 0.5)

    def test_indextts2_finalist_matrix_supports_emotion_audio_and_runtime_overrides(self) -> None:
        sample = {
            "emotion_audio_prompt": Path("/tmp/emotion.wav"),
            "emotion_strength": 0.65,
            "custom_control": None,
            "direction": "terrified",
        }
        self.assertEqual(
            self.index_matrix.build_control(sample),
            {
                "emo_audio_prompt": "/tmp/emotion.wav",
                "emo_alpha": 0.65,
            },
        )

        class FakeCFM:
            def __init__(self) -> None:
                self.calls = []

            def inference(
                self,
                mu,
                x_lens,
                prompt,
                style,
                f0,
                n_timesteps,
                temperature=1.0,
                inference_cfg_rate=0.5,
            ):
                self.calls.append((n_timesteps, inference_cfg_rate))
                return "ok"

        class FakeModel:
            def __init__(self) -> None:
                self.s2mel = type("S2Mel", (), {"models": {"cfm": FakeCFM()}})()

        model = FakeModel()
        applied = self.index_matrix.install_cfm_overrides(
            model,
            diffusion_steps=12,
            inference_cfg_rate=0.4,
        )
        result = model.s2mel.models["cfm"].inference(
            None, None, None, None, None, 25
        )
        self.assertEqual(result, "ok")
        self.assertEqual(model.s2mel.models["cfm"].calls, [(12, 0.4)])
        self.assertEqual(applied["diffusion_steps_override"], 12)
        self.assertEqual(applied["inference_cfg_rate_override"], 0.4)

    def test_indextts2_finalist_matrix_supports_greedy_generation(self) -> None:
        class FakeGPT:
            def __init__(self) -> None:
                self.calls = []

            def inference_speech(self, *args, **kwargs):
                self.calls.append(kwargs)
                return "ok"

        class FakeModel:
            def __init__(self) -> None:
                self.gpt = FakeGPT()

        model = FakeModel()
        applied = self.index_matrix.install_gpt_generation_overrides(
            model,
            greedy=True,
        )
        result = model.gpt.inference_speech(do_sample=True, num_beams=3)
        self.assertEqual(result, "ok")
        self.assertEqual(model.gpt.calls, [{"do_sample": False, "num_beams": 1}])
        self.assertTrue(applied["greedy_generation"])

    def test_indextts2_finalist_matrix_supports_custom_text_and_vector_controls(self) -> None:
        text_control = {
            "direction": "text_grief",
            "emotion_strength": 0.6,
            "custom_control": {
                "use_emo_text": True,
                "emo_text": "Speak with restrained grief.",
                "emo_alpha": 0.5,
            },
        }
        resolved_text = self.index_matrix.build_control(text_control)
        self.assertTrue(resolved_text["use_emo_text"])
        self.assertEqual(resolved_text["emo_text"], "Speak with restrained grief.")
        self.assertEqual(resolved_text["emo_alpha"], 0.6)

        vector_control = {
            "direction": "grief_blend",
            "emotion_strength": None,
            "custom_control": {
                "emo_vector": [0, 0, 0.45, 0, 0, 0.35, 0, 0],
                "emo_alpha": 1.0,
            },
        }
        resolved_vector = self.index_matrix.build_control(vector_control)
        self.assertEqual(
            resolved_vector["emo_vector"],
            [0, 0, 0.45, 0, 0, 0.35, 0, 0],
        )

    def test_indextts2_finalist_matrix_parses_stage_bottlenecks(self) -> None:
        timings = self.index_matrix.parse_stage_timings(
            ">> gpt_gen_time: 12.00 seconds\n"
            ">> gpt_forward_time: 2.00 seconds\n"
            ">> s2mel_time: 4.00 seconds\n"
            ">> bigvgan_time: 2.00 seconds\n"
            ">> Total inference time: 21.00 seconds\n"
            ">> RTF: 4.2000\n"
        )
        self.assertEqual(timings["measured_stage_sum_seconds"], 20.0)
        self.assertEqual(timings["gpt_share_of_measured_stages"], 0.7)
        self.assertEqual(timings["model_reported_rtf"], 4.2)

    def test_indextts2_finalist_matrix_is_local_and_non_production(self) -> None:
        source = INDEX_MATRIX.read_text(encoding="utf-8")
        self.assertIn('"production_promotion_allowed": False', source)
        self.assertIn('"manual_listening_required": True', source)
        self.assertIn("shared_model_load_seconds", source)
        self.assertIn("redirect_stdout", source)
        self.assertIn("emotion_audio_prompt", source)
        self.assertIn("install_cfm_overrides", source)
        self.assertIn("torch.inference_mode", source)
        self.assertNotIn("snapshot_download", source)
        self.assertNotIn("hf_hub_download", source)
        self.assertNotIn("modelscope", source)

    def test_indextts2_parallel_pool_is_warm_local_and_non_production(self) -> None:
        source = INDEX_POOL.read_text(encoding="utf-8")
        self.assertIn("start_event", source)
        self.assertIn("ready_queue", source)
        self.assertIn("aggregate_throughput_rtf", source)
        self.assertIn("emotion_audio_prompt", source)
        self.assertIn("greedy_generation", source)
        self.assertIn('"production_promotion_allowed": False', source)
        self.assertNotIn("snapshot_download", source)
        self.assertNotIn("hf_hub_download", source)
        self.assertNotIn("modelscope", source)

    def test_indextts2_mlx_probe_maps_trained_block_without_downloads(self) -> None:
        source = INDEX_MLX_PROBE.read_text(encoding="utf-8")
        self.assertIn("BLOCK_PREFIX = \"gpt.h.0.\"", source)
        self.assertIn("weight_count", source)
        self.assertIn("cosine_similarity", source)
        self.assertIn("parity_passed", source)
        self.assertIn("mx.fast.scaled_dot_product_attention", source)
        self.assertIn('"production_promotion_allowed": False', source)
        self.assertNotIn("snapshot_download", source)
        self.assertNotIn("hf_hub_download", source)
        self.assertNotIn("modelscope", source)

    def test_cosyvoice_device_routing_is_evaluation_only(self) -> None:
        source = COSY_RUNNER.read_text(encoding="utf-8")
        self.assertIn("install_device_routing", source)
        self.assertIn('choices=("cpu", "mps")', source)
        self.assertIn('choices=("cpu", "coreml")', source)
        self.assertIn('choices=("instruct", "zero_shot")', source)
        self.assertIn("--persistent-description", source)
        self.assertIn("--reference-text", source)
        self.assertIn("inference_zero_shot", source)
        self.assertIn("inference_instruct2", source)
        self.assertIn("--reference-text is required", source)
        self.assertIn("reference_prompt_terminator_applied", source)
        self.assertIn("load_trt=False", source)
        self.assertIn("load_vllm=False", source)
        self.assertIn('sys.modules["wetext"] = None', source)
        self.assertIn('"<|endofprompt|>"', source)
        self.assertIn("mps_cpu_istft", source)
        self.assertIn('text_frontend=False', source)
        self.assertNotIn("inference_vc", source)


if __name__ == "__main__":
    unittest.main()
