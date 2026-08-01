from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from audio_text_integrity import (
    assess_transcription,
    expects_terminal_sibilant,
    terminal_acoustic_features,
    unexpected_repetitions,
)


def write_audio(path: Path, *, with_terminal_noise: bool) -> None:
    sample_rate = 24000
    samples = np.arange(sample_rate, dtype=np.float64)
    audio = np.sin(2 * np.pi * 180 * samples / sample_rate) * 0.15
    if with_terminal_noise:
        start = int(sample_rate * 0.82)
        end = int(sample_rate * 0.94)
        generator = np.random.default_rng(7)
        audio[start:end] += generator.normal(0.0, 0.11, end - start)
    pcm = np.asarray(np.clip(audio, -1.0, 1.0) * 32767, dtype="<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


class AudioTextIntegrityTests(unittest.TestCase):
    def test_unexpected_repetition_excludes_authored_repeat(self) -> None:
        self.assertEqual(
            unexpected_repetitions("No no, stop.", "No no, stop."),
            (),
        )
        self.assertEqual(
            unexpected_repetitions("No, stop.", "No no, stop."),
            ("no",),
        )

    def test_terminal_sibilant_detection(self) -> None:
        self.assertTrue(expects_terminal_sibilant("one place"))
        self.assertTrue(expects_terminal_sibilant("the boys"))
        self.assertFalse(expects_terminal_sibilant("one play"))
        self.assertFalse(expects_terminal_sibilant("it was"))

    def test_terminal_acoustics_detect_weak_release(self) -> None:
        words = [{"word": " place", "start": 0.70, "end": 0.94}]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            weak = root / "weak.wav"
            clear = root / "clear.wav"
            write_audio(weak, with_terminal_noise=False)
            write_audio(clear, with_terminal_noise=True)
            weak_result = terminal_acoustic_features(
                weak,
                expected_text="one place",
                transcript_words=words,
            )
            clear_result = terminal_acoustic_features(
                clear,
                expected_text="one place",
                transcript_words=words,
            )
        self.assertIsNotNone(weak_result)
        self.assertIsNotNone(clear_result)
        self.assertTrue(weak_result.weak_sibilant_release)
        self.assertFalse(clear_result.weak_sibilant_release)

    def test_assessment_flags_missing_terminal_word(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "line.wav"
            write_audio(audio, with_terminal_noise=False)
            assessment = assess_transcription(
                expected_text="This must end with place.",
                transcript_result={
                    "text": "This must end with play.",
                    "segments": [
                        {
                            "words": [
                                {"word": " play", "start": 0.70, "end": 0.94}
                            ]
                        }
                    ],
                },
                audio_path=audio,
            )
        self.assertTrue(assessment.needs_review)
        self.assertIn("terminal_text_mismatch", assessment.reasons())


if __name__ == "__main__":
    unittest.main()
