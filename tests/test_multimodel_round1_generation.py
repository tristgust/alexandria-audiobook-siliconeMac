from __future__ import annotations

import hashlib
import shutil
import sys
import unittest
import uuid
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator, Protocol


ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))

import build_multimodel_round1_manifest as manifest_builder  # noqa: E402
import run_multimodel_round1_mlx as mlx_runner  # noqa: E402


class _MossArray(Protocol):
    """Structural stand-in for an MLX array in dependency-free tests."""


_MossArgument = bool | float | int | str | Path | _MossArray


class _FakeMossModel:
    sample_rate = 48000

    def __init__(self) -> None:
        self.generate_kwargs: dict[str, _MossArgument] | None = None
        self.config = SimpleNamespace(n_vq=12)
        self.encode_calls = 0

    def encode_reference_audio(
        self,
        *_args: _MossArray,
        **_kwargs: int | str,
    ) -> _MossArray:
        self.encode_calls += 1
        return mlx_runner.mx.array([[1]], dtype=mlx_runner.mx.int32)

    def generate(self, **kwargs: _MossArgument) -> Iterator[SimpleNamespace]:
        self.generate_kwargs = kwargs
        yield SimpleNamespace(
            audio=mlx_runner.np.array([0.0], dtype=mlx_runner.np.float32),
            sample_rate=self.sample_rate,
        )


class MultimodelRound1MossContractTests(unittest.TestCase):
    def test_manifest_records_approved_chatterbox_safety_quarantine(self) -> None:
        failure_for = getattr(manifest_builder, "generation_failure_for", None)

        self.assertTrue(callable(failure_for), "generation failure lookup is missing")
        failure = failure_for("chatterbox_multilingual_v3", "narrator", "proud")
        self.assertEqual(failure["code"], "repeated_no_eos_memory_pressure_surge")
        self.assertFalse(failure["retry_allowed"])
        self.assertFalse(failure["controls_changed"])

    def test_moss_manifest_pins_official_audio_sampler_controls(self) -> None:
        style = {
            "key": "neutral",
            "instruction": "Speak naturally.",
            "target_text": "A short line.",
        }

        control = manifest_builder.control_for(
            {"key": "moss_tts_local_v15"},
            "narrator",
            style,
            {},
        )

        self.assertEqual(control["audio_temperature"], 1.7)
        self.assertEqual(control["audio_top_p"], 0.8)
        self.assertEqual(control["audio_top_k"], 25)
        self.assertEqual(control["n_vq_for_inference"], 12)
        self.assertEqual(control["max_tokens"], 768)

    def test_moss_generator_forwards_the_manifest_contract(self) -> None:
        model = _FakeMossModel()
        sample = {
            "target_text": "A short line.",
            "control": {
                "instruction": "Speak naturally.",
                "language": "English",
                "audio_temperature": 1.7,
                "audio_top_p": 0.8,
                "audio_top_k": 25,
                "n_vq_for_inference": 12,
                "max_tokens": 768,
            },
        }

        mlx_runner.generate_moss(
            model,
            sample,
            Path("reference.wav"),
            "Reference words.",
            Path("tokenizer"),
        )

        self.assertIsNotNone(model.generate_kwargs)
        assert model.generate_kwargs is not None
        self.assertEqual(model.generate_kwargs["max_tokens"], 768)
        self.assertEqual(model.generate_kwargs["audio_temperature"], 1.7)
        self.assertEqual(model.generate_kwargs["audio_top_p"], 0.8)
        self.assertEqual(model.generate_kwargs["audio_top_k"], 25)
        self.assertEqual(model.generate_kwargs["n_vq_for_inference"], 12)

    def test_moss_reference_codes_are_encoded_once_per_worker(self) -> None:
        base = (
            ROOT
            / ".omo"
            / "evidence"
            / "b17-t05-multimodel-round1"
            / "recovery"
            / "runtime-tests"
            / uuid.uuid4().hex
        )
        self.addCleanup(shutil.rmtree, base)
        reference = base / "references" / "voice.wav"
        reference.parent.mkdir(parents=True)
        with wave.open(str(reference), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(48_000)
            handle.writeframes(b"\x00\x00" * 480)
        model = _FakeMossModel()
        sample = {
            "target_text": "A short line.",
            "reference": {
                "conditioning_sha256": hashlib.sha256(
                    reference.read_bytes()
                ).hexdigest()
            },
            "control": {
                "instruction": "Speak naturally.",
                "language": "English",
                "audio_temperature": 1.7,
                "audio_top_p": 0.8,
                "audio_top_k": 25,
                "n_vq_for_inference": 12,
                "max_tokens": 768,
            },
        }
        cache: dict[str, _MossArray] = {}

        first = mlx_runner.generate_moss(
            model, sample, reference, "Words.", Path("tokenizer"), base, cache
        )
        second = mlx_runner.generate_moss(
            model, sample, reference, "Words.", Path("tokenizer"), base, cache
        )

        self.assertEqual((first[2], second[2]), ("encoded", "memory"))
        self.assertEqual(model.encode_calls, 1)
        self.assertIsNotNone(model.generate_kwargs)
        assert model.generate_kwargs is not None
        self.assertIn("prompt_audio_codes", model.generate_kwargs)
        self.assertNotIn("ref_audio", model.generate_kwargs)

    def test_mlx_releases_unused_cache_after_each_sample(self) -> None:
        calls: list[str] = []
        original = mlx_runner.mx.clear_cache
        self.addCleanup(setattr, mlx_runner.mx, "clear_cache", original)
        mlx_runner.mx.clear_cache = lambda: calls.append("clear")

        mlx_runner.release_sample_mlx_cache()

        self.assertEqual(calls, ["clear"])


if __name__ == "__main__":
    unittest.main()
