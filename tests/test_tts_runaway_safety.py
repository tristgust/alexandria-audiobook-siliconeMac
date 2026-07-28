from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from audio_artifacts import (
    AudioArtifactError,
    inspect_chunk_audio,
    install_generated_audio,
    sha256_file,
)
from audio_processing import (
    AudioProcessingError,
    production_speech_max_tokens,
    split_generated_speech,
    validate_generated_speech_duration,
)
from mlx_backend import MLXBackend


LONG_HUMAN_NATURE_TEXT = ("human nature " * 93) + "."


class _FakeSegment:
    def __init__(self, duration_ms: int) -> None:
        self._duration_ms = duration_ms

    def __len__(self) -> int:
        return self._duration_ms

    def export(self, output, *, format: str) -> None:
        del format
        output.write(b"synthetic-audio" * 128)


class _ExhaustingQwen:
    def __init__(self) -> None:
        self.requested_limits: list[int] = []

    def generate(self, _text: str | None = None, **kwargs):
        limit = int(kwargs.get("max_tokens", 4096))
        self.requested_limits.append(limit)
        return [limit]


class _LengthSensitiveQwen:
    def __init__(self) -> None:
        self.requests: list[tuple[str, int]] = []

    def generate(self, text: str, **kwargs):
        limit = int(kwargs["max_tokens"])
        self.requests.append((text, limit))
        natural_tokens = max(12, round(((len(text) / 15.0) + 0.5) * 12.5))
        return [limit if len(text) >= 137 else natural_tokens]


class _ExactTextExhaustingQwen:
    def __init__(self, exhausted_text: str) -> None:
        self.exhausted_text = exhausted_text
        self.requests: list[str] = []

    def generate(self, text: str, **kwargs):
        self.requests.append(text)
        natural_tokens = max(12, round(((len(text) / 15.0) + 0.5) * 12.5))
        return [int(kwargs["max_tokens"]) if text == self.exhausted_text else natural_tokens]


def _exhausted_waveform(_model, results) -> tuple[np.ndarray, int]:
    token_count = int(results[0])
    sample_rate = 100
    sample_count = round((token_count / 12.5) * sample_rate)
    timeline = np.arange(sample_count, dtype=np.float32) / sample_rate
    return 0.1 * np.sin(2.0 * np.pi * 4.0 * timeline), sample_rate


class ProductionTokenCeilingTests(unittest.TestCase):
    LIVE_SHORT_CONSTANCE_TEXT = "Your accent gives you away."
    LIVE_CONSTANCE_TEXT = (
        "The Cat and Mouse Act. I'm on a hunger strike in Holloway. Every now "
        "and then, they release me, let me get my strength back. Then they arrest "
        "me again, and I go on hunger strike again. I've been in and out three "
        "times now. It's getting to be a matter of routine."
    )

    def test_custom_qwen_rejects_exact_4096_token_exhaustion(self) -> None:
        model = _ExhaustingQwen()
        backend = MLXBackend()

        with (
            patch.object(backend, "_model", return_value=model),
            patch.object(backend, "_collect_audio", side_effect=_exhausted_waveform),
            patch.object(backend, "_save"),
            self.assertRaisesRegex(AudioProcessingError, "too long"),
        ):
            backend.generate_custom("Oh.", "Brief.", "Vivian", "/tmp/unused.wav")

        self.assertEqual(model.requested_limits, [101, 101])

    def test_custom_qwen_adaptively_splits_exact_short_live_exhaustion(self) -> None:
        self.assertEqual(len(self.LIVE_SHORT_CONSTANCE_TEXT), 27)
        model = _ExactTextExhaustingQwen(self.LIVE_SHORT_CONSTANCE_TEXT)
        backend = MLXBackend()

        with (
            patch.object(backend, "_model", return_value=model),
            patch.object(backend, "_collect_audio", side_effect=_exhausted_waveform),
            patch.object(backend, "_save") as save,
        ):
            result = backend.generate_custom(
                self.LIVE_SHORT_CONSTANCE_TEXT,
                "Earnest and determined.",
                "Vivian",
                "/tmp/unused.wav",
            )

        self.assertTrue(result)
        self.assertEqual(
            model.requests,
            [self.LIVE_SHORT_CONSTANCE_TEXT] * 2 + ["Your accent", "gives you away."],
        )
        save.assert_called_once()

    def test_custom_qwen_segments_live_line_instead_of_repeating_exhaustion(self) -> None:
        self.assertEqual(len(self.LIVE_CONSTANCE_TEXT), 262)
        model = _LengthSensitiveQwen()
        backend = MLXBackend()

        with (
            patch.object(backend, "_model", return_value=model),
            patch.object(backend, "_collect_audio", side_effect=_exhausted_waveform),
            patch.object(backend, "_save") as save,
        ):
            result = backend.generate_custom(
                self.LIVE_CONSTANCE_TEXT,
                "Earnest and determined; formal phrasing, measured pace.",
                "Vivian",
                "/tmp/unused.wav",
            )

        self.assertTrue(result)
        self.assertEqual([len(text) for text, _limit in model.requests], [58, 65, 59, 77])
        self.assertEqual(" ".join(text for text, _limit in model.requests), self.LIVE_CONSTANCE_TEXT)
        save.assert_called_once()

    def test_segmenter_also_bounds_one_unbroken_token(self) -> None:
        text = "x" * 97

        segments = split_generated_speech(text)

        self.assertTrue(all(0 < len(segment) <= 96 for segment in segments))
        self.assertEqual("".join(segments), text)

    def test_instruction_qwen_rejects_cap_exhaustion_before_fast_prosody(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            output = root / "output.wav"
            sf.write(reference, np.ones(100, dtype=np.float32) * 0.1, 100)
            model = _ExhaustingQwen()
            backend = MLXBackend()

            with (
                patch.object(backend, "_model", return_value=model),
                patch.object(backend, "_enable_qwen_icl_instruction"),
                patch.object(backend, "_collect_audio", side_effect=_exhausted_waveform),
                patch("mlx_backend.apply_delivery_prosody") as prosody,
                self.assertRaisesRegex(AudioProcessingError, "too long"),
            ):
                backend.generate_instruction_controlled_clone(
                    text="Oh.",
                    ref_audio=str(reference),
                    ref_text="Reference sentence.",
                    instruct="Urgent.",
                    output_path=str(output),
                    seed=17,
                )

            self.assertEqual(model.requested_limits, [101])
            prosody.assert_not_called()
            self.assertFalse(output.exists())

    def test_merged_lora_qwen_receives_text_relative_cap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            sf.write(reference, np.ones(100, dtype=np.float32) * 0.1, 100)
            captured: dict[str, int] = {}

            class Model:
                sample_rate = 100

                def generate(self, **kwargs):
                    captured["max_tokens"] = kwargs["max_tokens"]
                    return [object()]

            backend = MLXBackend()
            tone = 0.1 * np.sin(2.0 * np.pi * 4.0 * np.arange(100) / 100)
            with (
                patch.object(backend, "_external_qwen_model", return_value=Model()),
                patch.object(backend, "_collect_audio", return_value=(tone, 100)),
                patch.object(backend, "_save"),
            ):
                self.assertTrue(
                    backend.generate_merged_lora_clone(
                        text="Oh.",
                        ref_audio=str(reference),
                        ref_text="Reference sentence.",
                        instruct="Brief.",
                        model_path=str(root / "model"),
                        output_path=str(root / "output.wav"),
                    )
                )

            self.assertEqual(captured["max_tokens"], 101)

    def test_text_ceiling_preserves_configured_lower_cap_and_long_line(self) -> None:
        self.assertEqual(len(LONG_HUMAN_NATURE_TEXT), 1210)
        self.assertEqual(production_speech_max_tokens(LONG_HUMAN_NATURE_TEXT, 256), 256)
        self.assertGreater(production_speech_max_tokens(LONG_HUMAN_NATURE_TEXT), 1500)
        self.assertLess(production_speech_max_tokens(LONG_HUMAN_NATURE_TEXT), 4096)

    def test_clipped_but_complete_live_line_is_not_rejected_as_truncated(self) -> None:
        live_text = (
            "Are you coming with us, then? You've waited so long, it'd be a shame "
            "if you weren't there for the kill."
        )
        self.assertEqual(len(live_text), 103)
        validate_generated_speech_duration(4.24, live_text)


class ArtifactDurationSafetyTests(unittest.TestCase):
    @staticmethod
    def _decoder(duration_ms: int):
        def decode(_source, *, format=None):
            del format
            return _FakeSegment(duration_ms)

        return decode

    def test_install_rejects_327_second_audio_for_short_line(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "runaway.wav"
            source.write_bytes(b"synthetic-source")

            with self.assertRaises(AudioArtifactError) as raised:
                install_generated_audio(
                    root_dir=root,
                    voicelines_dir=root / "voicelines",
                    source_audio_path=source,
                    filename_base="line",
                    binding_fingerprint="f" * 64,
                    text="Oh.",
                    decoder=self._decoder(327_680),
                )

            self.assertEqual(raised.exception.code, "audio_duration_excessive")

    def test_inspection_rejects_current_runaway_but_accepts_long_narration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio = root / "voicelines" / "line.wav"
            audio.parent.mkdir()
            audio.write_bytes(b"synthetic-source")
            fingerprint = "f" * 64
            base_chunk = {
                "status": "done",
                "audio_state": "current",
                "audio_path": "voicelines/line.wav",
                "audio_fingerprint": fingerprint,
                "audio_sha256": sha256_file(audio),
            }

            runaway = inspect_chunk_audio(
                root_dir=root,
                chunk={**base_chunk, "text": "Oh."},
                expected_fingerprint=fingerprint,
                decoder=self._decoder(327_680),
            )
            legitimate = inspect_chunk_audio(
                root_dir=root,
                chunk={**base_chunk, "text": LONG_HUMAN_NATURE_TEXT},
                expected_fingerprint=fingerprint,
                decoder=self._decoder(90_000),
            )

            self.assertEqual(runaway["reason"], "audio_duration_excessive")
            self.assertFalse(runaway["ready"])
            self.assertEqual(legitimate["state"], "current")
            self.assertTrue(legitimate["ready"])


if __name__ == "__main__":
    unittest.main()
