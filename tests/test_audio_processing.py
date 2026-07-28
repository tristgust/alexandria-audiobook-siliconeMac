from __future__ import annotations

import builtins
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from audio_processing import (
    AudioProcessingError,
    decode_audio_mono,
    prepare_generated_speech_audio,
    temporary_mono_wav,
    voice_design_max_tokens,
)
from mlx_backend import MLXBackend


class AudioProcessingTests(unittest.TestCase):
    @staticmethod
    def _write_stereo(path: Path, *, sample_rate: int) -> None:
        timeline = np.arange(sample_rate, dtype=np.float32) / sample_rate
        left = 0.2 * np.sin(2.0 * np.pi * 220.0 * timeline)
        right = 0.2 * np.sin(2.0 * np.pi * 330.0 * timeline)
        sf.write(path, np.column_stack((left, right)), sample_rate)

    def test_decode_resamples_without_importing_scipy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "stereo-48k.wav"
            self._write_stereo(source, sample_rate=48000)
            real_import = builtins.__import__

            def guarded_import(name, *args, **kwargs):
                if name == "scipy" or name.startswith("scipy."):
                    raise AssertionError("SciPy import is forbidden on this path")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=guarded_import):
                audio, sample_rate = decode_audio_mono(
                    source,
                    sample_rate=16000,
                )

            self.assertEqual(sample_rate, 16000)
            self.assertEqual(audio.ndim, 1)
            self.assertEqual(len(audio), 16000)
            self.assertGreater(float(np.max(np.abs(audio))), 0.01)

    def test_temporary_mono_wav_normalizes_and_cleans_up(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "stereo-24k.wav"
            self._write_stereo(source, sample_rate=24000)
            with temporary_mono_wav(source, sample_rate=16000) as prepared:
                self.assertNotEqual(prepared, source)
                self.assertTrue(prepared.is_file())
                info = sf.info(prepared)
                self.assertEqual(info.samplerate, 16000)
                self.assertEqual(info.channels, 1)
                prepared_path = prepared
            self.assertFalse(prepared_path.exists())

    def test_voice_design_token_budget_is_text_bounded(self) -> None:
        short_budget = voice_design_max_tokens("A short line.")
        long_budget = voice_design_max_tokens("word " * 2000)

        self.assertGreaterEqual(short_budget, 128)
        self.assertLess(short_budget, 4096)
        self.assertLessEqual(long_budget, 768)

    def test_generated_speech_trims_bounded_edge_silence(self) -> None:
        sample_rate = 24000
        tone = 0.1 * np.sin(
            2.0 * np.pi * 180.0 * np.arange(sample_rate * 2) / sample_rate
        )
        audio = np.concatenate(
            (
                np.zeros(sample_rate, dtype=np.float32),
                tone,
                np.zeros(sample_rate * 5, dtype=np.float32),
            )
        ).astype(np.float32)

        prepared = prepare_generated_speech_audio(
            audio,
            sample_rate,
            "A short generated sentence.",
        )

        self.assertGreater(len(prepared), sample_rate * 2)
        self.assertLess(len(prepared), sample_rate * 4)


class VoiceDesignGenerationSafetyTests(unittest.TestCase):
    def test_design_preview_passes_a_text_bounded_token_budget(self) -> None:
        captured: dict[str, object] = {}

        class FakeDesignModel:
            def generate(self, text, **kwargs):
                captured["text"] = text
                captured["kwargs"] = dict(kwargs)
                return [object()]

        sample_text = "A short line for a designed voice."
        sample_rate = 24000
        timeline = np.arange(sample_rate * 2, dtype=np.float32) / sample_rate
        audio = 0.1 * np.sin(2.0 * np.pi * 180.0 * timeline)
        backend = MLXBackend()
        with (
            patch.object(backend, "_model", return_value=FakeDesignModel()),
            patch.object(
                backend,
                "_collect_audio",
                return_value=(audio.astype(np.float32), sample_rate),
            ),
            patch.object(backend, "_save"),
        ):
            backend._generate_design_preview_locked(
                "A steady middle-aged voice.",
                sample_text,
            )

        self.assertEqual(captured["text"], sample_text)
        self.assertEqual(
            captured["kwargs"]["max_tokens"],
            voice_design_max_tokens(sample_text),
        )

    def test_design_preview_retries_one_rejected_sample(self) -> None:
        sample_rate = 24000
        tone = 0.1 * np.sin(
            2.0
            * np.pi
            * 180.0
            * np.arange(sample_rate * 2, dtype=np.float32)
            / sample_rate
        ).astype(np.float32)
        pathological = np.concatenate(
            (tone, np.zeros(sample_rate * 10, dtype=np.float32), tone)
        )

        class FakeDesignModel:
            def __init__(self) -> None:
                self.calls = 0

            def generate(self, _text, **_kwargs):
                self.calls += 1
                return [object()]

        backend = MLXBackend()
        model = FakeDesignModel()
        with (
            patch.object(backend, "_model", return_value=model),
            patch.object(
                backend,
                "_collect_audio",
                side_effect=[
                    (pathological, sample_rate),
                    (tone, sample_rate),
                ],
            ),
            patch.object(backend, "_save"),
        ):
            backend._generate_design_preview_locked(
                "A steady middle-aged voice.",
                "A short line for a designed voice.",
                seed=42,
            )

        self.assertEqual(model.calls, 2)


class TransformersTokenizerCompatibilityTests(unittest.TestCase):
    def test_tokenizer_import_skips_unused_sklearn_when_scipy_is_unavailable(self) -> None:
        root = Path(__file__).resolve().parents[1]
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(root / "app")
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; sys.modules['scipy'] = None; "
                    "from mlx_backend import MLXBackend; "
                    "MLXBackend._disable_unused_transformers_sklearn(); "
                    "from transformers import AutoTokenizer; "
                    "print(AutoTokenizer.__name__)"
                ),
            ],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "AutoTokenizer")


class QwenInstructionControlledCloneTests(unittest.TestCase):
    @staticmethod
    def _write_reference(path: Path) -> None:
        timeline = np.arange(24000, dtype=np.float32) / 24000
        sf.write(
            path,
            0.1 * np.sin(2.0 * np.pi * 180.0 * timeline),
            24000,
        )

    def test_instruction_is_injected_once_and_cleared_after_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            output = root / "controlled.wav"
            self._write_reference(reference)
            captured: dict[str, object] = {}

            class Tokenizer:
                def encode(self, _text):
                    return [1, 2, 3]

            class Embeddings:
                def __call__(self, ids):
                    import mlx.core as mx

                    return mx.ones((ids.shape[0], ids.shape[1], 2))

            class Talker:
                def text_projection(self, values):
                    return values

                def get_text_embeddings(self):
                    return Embeddings()

            class FakeQwen:
                sample_rate = 24000

                def __init__(self) -> None:
                    self.tokenizer = Tokenizer()
                    self.talker = Talker()

                def _prepare_icl_generation_inputs(self, *_args, **_kwargs):
                    import mlx.core as mx

                    return (
                        mx.zeros((1, 2, 2)),
                        mx.zeros((1, 1, 2)),
                        mx.zeros((1, 1, 2)),
                        mx.zeros((1, 1, 1)),
                    )

                def generate(self, text, **kwargs):
                    captured["text"] = text
                    captured["kwargs"] = dict(kwargs)
                    captured["instruction"] = self._alexandria_icl_instruction
                    captured["prefill_shape"] = tuple(
                        self._prepare_icl_generation_inputs()[0].shape
                    )
                    return [object()]

            backend = MLXBackend()
            model = FakeQwen()
            with (
                patch.object(backend, "_model", return_value=model),
                patch.object(
                    backend,
                    "_collect_audio",
                    return_value=(np.ones(24000, dtype=np.float32) * 0.1, 24000),
                ),
                patch("mlx_backend.mx.random.seed"),
            ):
                result = backend.generate_instruction_controlled_clone(
                    text="Move now.",
                    ref_audio=str(reference),
                    ref_text="Exact reference transcript.",
                    instruct="Urgent, clipped, and forceful.",
                    output_path=str(output),
                    seed=42,
                )

            self.assertTrue(result)
            self.assertTrue(output.is_file())
            self.assertEqual(
                captured["instruction"],
                "Urgent, clipped, and forceful.",
            )
            self.assertEqual(captured["prefill_shape"], (1, 5, 2))
            self.assertNotIn("instruct", captured["kwargs"])
            self.assertEqual(captured["kwargs"]["max_tokens"], 101)
            self.assertIsNone(model._alexandria_icl_instruction)


class ControlledCloneReferencePreparationTests(unittest.TestCase):
    @staticmethod
    def _write_reference(path: Path, *, sample_rate: int) -> None:
        timeline = np.arange(sample_rate, dtype=np.float32) / sample_rate
        waveform = 0.2 * np.sin(2.0 * np.pi * 180.0 * timeline)
        sf.write(path, waveform, sample_rate)

    def test_controlled_clone_passes_exact_encoder_rate_without_scipy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "reference-24k.wav"
            output = root / "preview.wav"
            self._write_reference(source, sample_rate=24000)
            captured: dict[str, object] = {}

            class FakeVoxCPM2:
                _encode_sample_rate = 16000
                sample_rate = 48000

                def generate(self, **kwargs):
                    reference = Path(kwargs["ref_audio"])
                    info = sf.info(reference)
                    captured.update(
                        {
                            "path": reference,
                            "sample_rate": info.samplerate,
                            "channels": info.channels,
                            "max_tokens": kwargs["max_tokens"],
                            "instruct": kwargs["instruct"],
                        }
                    )
                    return [object()]

            backend = MLXBackend()
            with (
                patch.object(backend, "_model", return_value=FakeVoxCPM2()),
                patch.object(
                    backend,
                    "_collect_audio",
                    return_value=(np.zeros(4800, dtype=np.float32), 48000),
                ),
            ):
                result = backend.generate_expressive_clone(
                    text="Move now.",
                    ref_audio=str(source),
                    ref_text="Exact reference transcript.",
                    instruct="Urgent but controlled.",
                    output_path=str(output),
                )

            self.assertTrue(result)
            self.assertTrue(output.is_file())
            self.assertEqual(captured["sample_rate"], 16000)
            self.assertEqual(captured["channels"], 1)
            self.assertEqual(captured["instruct"], "Urgent but controlled.")
            self.assertFalse(Path(captured["path"]).exists())

    def test_standard_clone_normalizes_to_model_rate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "reference-48k.wav"
            output = root / "clone.wav"
            self._write_reference(source, sample_rate=48000)
            captured: dict[str, object] = {}

            class FakeQwenClone:
                sample_rate = 24000

                def generate(self, text, **kwargs):
                    reference = Path(kwargs["ref_audio"])
                    info = sf.info(reference)
                    captured.update(
                        {
                            "path": reference,
                            "sample_rate": info.samplerate,
                            "channels": info.channels,
                            "max_tokens": kwargs["max_tokens"],
                        }
                    )
                    return [object()]

            backend = MLXBackend()
            with (
                patch.object(backend, "_model", return_value=FakeQwenClone()),
                patch.object(
                    backend,
                    "_resolve_accent_clone_reference",
                    return_value=(str(source), "Exact reference transcript.", None),
                ),
                patch.object(
                    backend,
                    "_collect_audio",
                    return_value=(np.ones(24000, dtype=np.float32) * 0.1, 24000),
                ),
            ):
                result = backend.generate_clone(
                    text="Hello.",
                    ref_audio=str(source),
                    ref_text="Exact reference transcript.",
                    output_path=str(output),
                )

            self.assertTrue(result)
            self.assertEqual(captured["sample_rate"], 24000)
            self.assertEqual(captured["channels"], 1)
            self.assertEqual(captured["max_tokens"], 101)
            self.assertFalse(Path(captured["path"]).exists())


if __name__ == "__main__":
    unittest.main()
