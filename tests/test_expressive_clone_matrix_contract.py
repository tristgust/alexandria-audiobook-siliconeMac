from __future__ import annotations

import importlib.util
import json
import math
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "benchmarks" / "run_expressive_clone_matrix.py"


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "alexandria_expressive_clone_matrix",
        RUNNER,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ExpressiveCloneMatrixContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = load_runner()
        cls.evaluator = sys.modules["transcription_evaluator"]

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.reference = self.root / "reference.wav"
        sample_rate = 24000
        t = np.arange(sample_rate, dtype=np.float32) / sample_rate
        audio = 0.1 * np.sin(2 * math.pi * 220.0 * t)
        sf.write(self.reference, audio, sample_rate)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def direction(self, key: str) -> dict:
        return next(
            item
            for item in self.runner.DEFAULT_DIRECTIONS
            if item["key"] == key
        )

    def test_default_matrix_has_required_directions_and_multiple_seeds(self) -> None:
        self.assertEqual(
            [item["key"] for item in self.runner.DEFAULT_DIRECTIONS],
            [
                "neutral",
                "urgent",
                "restrained_anger",
                "panic",
                "grief",
                "whisper",
                "sarcasm",
            ],
        )
        self.assertGreaterEqual(len(self.runner.DEFAULT_SEEDS), 2)
        self.assertTrue(all(seed >= 0 for seed in self.runner.DEFAULT_SEEDS))

    def test_fish_uses_inline_freeform_control_without_changing_reference(self) -> None:
        prepared = self.runner.prepare_control(
            "fish_s2_pro",
            self.direction("urgent"),
            text="We need to leave now.",
            primary_reference_audio=str(self.reference),
            primary_reference_text="This is the reference transcript.",
        )
        self.assertTrue(prepared.supported)
        self.assertEqual(prepared.text, "[urgent] We need to leave now.")
        self.assertIsNone(prepared.instruction)
        self.assertEqual(prepared.reference_audio, str(self.reference))
        self.assertEqual(
            prepared.summary["translation"],
            "inline_freeform_tag",
        )

    def test_turbo_does_not_pretend_to_support_arbitrary_direction(self) -> None:
        unsupported = self.runner.prepare_control(
            "chatterbox_turbo",
            self.direction("urgent"),
            text="We need to leave now.",
            primary_reference_audio=str(self.reference),
            primary_reference_text="Reference transcript.",
        )
        self.assertFalse(unsupported.supported)
        self.assertEqual(
            unsupported.summary["translation"],
            "unsupported_direction",
        )

        event = self.runner.prepare_control(
            "chatterbox_turbo",
            self.direction("sarcasm"),
            text="That was an excellent plan.",
            primary_reference_audio=str(self.reference),
            primary_reference_text="Reference transcript.",
        )
        self.assertTrue(event.supported)
        self.assertEqual(
            event.text,
            "That was an excellent plan. [chuckle]",
        )
        self.assertFalse(event.summary["semantic_control_claimed"])

    def test_reference_style_candidate_requires_direction_reference(self) -> None:
        missing = self.runner.prepare_control(
            "tada_1b",
            self.direction("grief"),
            text="I thought there would be more time.",
            primary_reference_audio=str(self.reference),
            primary_reference_text="Reference transcript.",
        )
        self.assertFalse(missing.supported)
        self.assertIn("No approved grief reference", missing.skip_reason)

        grief_reference = self.root / "grief.wav"
        grief_reference.write_bytes(self.reference.read_bytes())
        prepared = self.runner.prepare_control(
            "tada_1b",
            self.direction("grief"),
            text="I thought there would be more time.",
            primary_reference_audio=str(self.reference),
            primary_reference_text="Reference transcript.",
            reference_map={
                "grief": {
                    "audio": str(grief_reference),
                    "text": "A grief reference transcript.",
                }
            },
        )
        self.assertTrue(prepared.supported)
        self.assertEqual(prepared.reference_audio, str(grief_reference))
        self.assertEqual(
            prepared.summary["translation"],
            "direction_specific_reference",
        )

    def test_qwen_patch_is_recorded_as_untrained_comparison(self) -> None:
        prepared = self.runner.prepare_control(
            "qwen_icl_patch_baseline",
            self.direction("restrained_anger"),
            text="Do not touch that switch.",
            primary_reference_audio=str(self.reference),
            primary_reference_text="Reference transcript.",
        )
        self.assertTrue(prepared.supported)
        self.assertEqual(prepared.summary["translation"], "instruction_field")
        self.assertFalse(prepared.summary["semantic_control_claimed"])

    def test_objective_audio_metrics_are_model_free(self) -> None:
        metrics = self.runner.objective_audio_metrics(
            self.reference,
            word_count=4,
        )
        self.assertAlmostEqual(metrics["duration_seconds"], 1.0, places=3)
        self.assertEqual(metrics["sample_rate"], 24000)
        self.assertGreater(metrics["rms_dbfs"], -30.0)
        self.assertLessEqual(metrics["peak_dbfs"], 0.0)
        self.assertAlmostEqual(metrics["words_per_second"], 4.0, places=3)
        self.assertIn("longest_silence_seconds", metrics)

    def test_review_manifest_exposes_expected_text_and_asr_status(self) -> None:
        measurement = {
            "sample_id": "sample-1",
            "output_file": "sample-1.wav",
            "candidate": "fish_s2_pro",
            "direction": "urgent",
            "seed": 1001,
        }
        files = self.runner._write_review_manifests(
            self.root,
            [measurement],
            expected_text="The door was still open.",
            transcription_measurements={},
        )
        review = json.loads(
            (self.root / files["blinded_review"]).read_text(
                encoding="utf-8"
            )
        )[0]
        self.assertEqual(review["expected_text"], "The door was still open.")
        self.assertEqual(
            review["automatic_transcription_status"],
            "unavailable",
        )
        self.assertIsNone(review["automatic_transcript"])
        self.assertIsNone(review["spoken_text_matches_expected"])
        self.assertEqual(review["missing_changed_or_extra_words"], "")
        self.assertNotIn("transcription_correct", review)

        files = self.runner._write_review_manifests(
            self.root,
            [measurement],
            expected_text="The door was still open.",
            transcription_measurements={
                "sample-1": {
                    "transcript": "The door was still open.",
                    "word_error_rate": 0.0,
                }
            },
        )
        review = json.loads(
            (self.root / files["blinded_review"]).read_text(
                encoding="utf-8"
            )
        )[0]
        self.assertEqual(review["automatic_transcription_status"], "available")
        self.assertEqual(
            review["automatic_transcript"],
            "The door was still open.",
        )
        self.assertEqual(review["automatic_word_error_rate"], 0.0)

    def test_probe_is_model_free_and_discloses_evaluator_failure(self) -> None:
        fake_catalog = {
            "candidate_count": 8,
            "candidates": [],
            "implicit_downloads_allowed": False,
            "production_promotion_allowed": False,
        }
        fake_cache = {
            "state": "missing",
            "cached": False,
            "snapshot_path": None,
            "required_paths": [],
            "missing_required_paths": [],
            "broken_symlinks": [],
            "file_count": 0,
            "size_bytes": 0,
            "cache_root": str(self.root),
            "revision": "a" * 40,
        }
        with (
            patch.object(
                self.runner,
                "expressive_clone_candidate_catalog",
                return_value=fake_catalog,
            ),
            patch.object(
                self.runner,
                "cached_snapshot_status",
                return_value=fake_cache,
            ),
            patch.object(
                self.runner,
                "_probe_transcription_runtime",
                return_value={
                    "module": "mlx_whisper",
                    "available": False,
                    "exit_code": 1,
                    "error": "ImportError",
                },
            ),
            patch.object(self.runner, "repository_head", return_value="b" * 40),
            patch.object(self.runner, "system_hardware", return_value={}),
            patch.object(self.runner, "package_version", return_value="0.4.5"),
        ):
            result = self.runner.build_probe_result(cache_dir=self.root)
        rendered = json.dumps(result)
        self.assertEqual(result["run_kind"], "candidate_probe")
        self.assertFalse(
            result["benchmark_contract"]["implicit_downloads_allowed"]
        )
        self.assertFalse(
            result["benchmark_contract"]["production_promotion_allowed"]
        )
        self.assertFalse(
            result["evaluators"]["transcription_accuracy"][
                "runtime_import"
            ]["available"]
        )
        self.assertNotIn("Reference transcript.", rendered)
        self.assertNotIn("We need to leave now.", rendered)

    def test_transcription_evaluator_identity_is_fully_pinned(self) -> None:
        identity = self.runner.evaluator_identity()
        self.assertEqual(identity["model_key"], "mlx_whisper_base")
        self.assertEqual(identity["model"], "mlx-community/whisper-base-mlx")
        self.assertEqual(
            identity["revision"],
            "1e3e249fb8d01c655324bd6841b1deadffd6d04c",
        )
        self.assertEqual(identity["runtime_version"], "0.4.3")
        self.assertEqual(
            identity["dependency_path"],
            "alexandria_scipy_free_signal_shim_v1",
        )
        self.assertTrue(identity["local_files_only"])

    def test_transcription_wer_normalizes_case_and_punctuation(self) -> None:
        self.assertEqual(
            self.runner._word_error_rate(
                "The door was still open. We need to leave now.",
                " the door was still open, we need to leave now! ",
            ),
            0.0,
        )
        self.assertEqual(
            self.runner._word_error_rate("It’s ready.", "it's ready"),
            0.0,
        )

    def test_transcription_evaluator_ignores_malformed_installed_scipy(self) -> None:
        malformed_scipy = types.ModuleType("scipy")
        malformed_signal = types.ModuleType("scipy.signal")
        imported_runtime = types.SimpleNamespace(__version__="0.4.3")
        previous = {
            name: module
            for name, module in list(sys.modules.items())
            if name == "mlx_whisper" or name.startswith("mlx_whisper.")
        }
        for name in previous:
            sys.modules.pop(name, None)
        try:
            with patch.dict(
                sys.modules,
                {
                    "scipy": malformed_scipy,
                    "scipy.signal": malformed_signal,
                },
            ):
                def import_runtime(name: str):
                    self.assertEqual(name, "mlx_whisper")
                    shim = sys.modules["scipy"]
                    self.assertIsNot(shim, malformed_scipy)
                    self.assertEqual(
                        shim.__alexandria_dependency_path__,
                        "alexandria_scipy_free_signal_shim_v1",
                    )
                    self.assertTrue(
                        callable(sys.modules["scipy.signal"].medfilt)
                    )
                    return imported_runtime

                with (
                    patch.object(
                        self.evaluator.metadata,
                        "version",
                        return_value="0.4.3",
                    ),
                    patch.object(
                        self.evaluator.importlib,
                        "import_module",
                        side_effect=import_runtime,
                    ),
                ):
                    runtime = self.evaluator.load_pinned_runtime()
                self.assertIs(runtime, imported_runtime)
                self.assertIs(sys.modules["scipy"], malformed_scipy)
                self.assertIs(sys.modules["scipy.signal"], malformed_signal)
        finally:
            self.evaluator._clear_partial_mlx_whisper_import()
            sys.modules.update(previous)

    def test_known_transcript_fixture_produces_complete_wer_result(self) -> None:
        snapshot = self.root / "whisper-base"
        snapshot.mkdir()
        audio = self.root / "known.wav"
        audio.write_bytes(b"fixture")
        runtime = types.SimpleNamespace(
            transcribe=lambda *args, **kwargs: {
                "text": " The door was still open, we need to leave now!"
            }
        )
        status = {
            "cached": True,
            "snapshot_path": str(snapshot),
            "revision": "1e3e249fb8d01c655324bd6841b1deadffd6d04c",
        }
        with patch.object(
            self.evaluator,
            "load_pinned_runtime",
            return_value=runtime,
        ):
            result = self.evaluator.evaluate_transcriptions(
                {
                    "model_status": status,
                    "text": "The door was still open. We need to leave now.",
                    "outputs": [
                        {"sample_id": "known", "path": str(audio)}
                    ],
                }
            )
        self.assertTrue(result["available"])
        self.assertTrue(result["complete"])
        self.assertEqual(result["success_count"], 1)
        self.assertEqual(
            result["measurements"]["known"]["word_error_rate"],
            0.0,
        )

    def test_required_transcription_rejects_partial_or_missing_results(self) -> None:
        with self.assertRaisesRegex(RuntimeError, r"1 sample\(s\) failed"):
            self.runner._require_complete_transcription_evaluation(
                {
                    "available": True,
                    "complete": False,
                    "failure_count": 1,
                }
            )
        with self.assertRaisesRegex(RuntimeError, "model_missing"):
            self.runner._require_complete_transcription_evaluation(
                {
                    "available": False,
                    "complete": False,
                    "reason": "model_missing",
                }
            )
        self.runner._require_complete_transcription_evaluation(
            {"available": True, "complete": True}
        )

    def test_transcription_path_is_offline_and_never_downloads(self) -> None:
        runner_source = RUNNER.read_text(encoding="utf-8")
        evaluator_source = (
            ROOT / "benchmarks" / "transcription_evaluator.py"
        ).read_text(encoding="utf-8")
        self.assertIn('environment["HF_HUB_OFFLINE"] = "1"', runner_source)
        self.assertIn(
            'environment["TRANSFORMERS_OFFLINE"] = "1"',
            runner_source,
        )
        self.assertIn('"local_files_only": True', evaluator_source)
        self.assertNotIn("snapshot_download", evaluator_source)
        self.assertNotIn("from_pretrained", evaluator_source)

    def test_chatterbox_resampler_is_scipy_free(self) -> None:
        source = np.linspace(-0.5, 0.5, 24000, dtype=np.float32)
        with patch.dict(sys.modules, {"scipy": None}):
            result = self.runner._scipy_free_resample_audio(
                source,
                24000,
                16000,
            )
        self.assertIsInstance(result, np.ndarray)
        self.assertEqual(result.dtype, np.float32)
        self.assertEqual(result.shape, (16000,))
        self.assertTrue(np.isfinite(result).all())

    def test_pinned_snapshot_router_blocks_unregistered_hub_requests(self) -> None:
        import huggingface_hub

        original = huggingface_hub.snapshot_download
        local_snapshot = self.root / "s3-tokenizer"
        local_snapshot.mkdir()
        restore = self.runner._install_pinned_snapshot_router(
            {"mlx-community/S3TokenizerV2": local_snapshot}
        )
        try:
            self.assertIs(restore, original)
            self.assertEqual(
                huggingface_hub.snapshot_download(
                    "mlx-community/S3TokenizerV2"
                ),
                str(local_snapshot),
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "refused an unpinned model request",
            ):
                huggingface_hub.snapshot_download("unregistered/model")
        finally:
            huggingface_hub.snapshot_download = restore
        self.assertIs(huggingface_hub.snapshot_download, original)

    def test_runner_source_forbids_downloads_and_post_processing(self) -> None:
        source = RUNNER.read_text(encoding="utf-8")
        self.assertIn('environment["HF_HUB_OFFLINE"] = "1"', source)
        self.assertIn('parser.add_argument(\n        "--direction"', source)
        self.assertIn("post_generation_prosody_applied", source)
        self.assertIn('getattr(model, "_encode_sample_rate", sample_rate)', source)
        self.assertIn('ref_audio=mx.array(reference_audio)', source)
        self.assertIn(
            '"Fish reference normalization produced the wrong sample rate."',
            source,
        )
        self.assertNotIn("apply_delivery_prosody", source)
        self.assertNotIn(
            "from huggingface_hub import snapshot_download",
            source,
        )
        self.assertNotIn("huggingface_hub.snapshot_download(", source)
        self.assertIn("manual_blinded_review_required", source)
        self.assertIn("speaker_cosine_to_primary_reference", source)
        self.assertIn("word_error_rate", source)

    def test_seed_parser_rejects_single_or_random_seed(self) -> None:
        self.assertEqual(
            self.runner._parse_seeds("1,2,3"),
            [1, 2, 3],
        )
        with self.assertRaisesRegex(ValueError, "At least two"):
            self.runner._parse_seeds("1")
        with self.assertRaisesRegex(ValueError, "non-negative"):
            self.runner._parse_seeds("1,-1")


if __name__ == "__main__":
    unittest.main()
