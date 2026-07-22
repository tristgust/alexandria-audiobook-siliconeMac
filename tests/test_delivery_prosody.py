from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from delivery_prosody import (
    apply_delivery_prosody,
    build_delivery_prosody_profile,
)


class DeliveryProsodyProfileTests(unittest.TestCase):
    def test_contrasting_explicit_cues_produce_contrasting_profiles(self) -> None:
        grief = build_delivery_prosody_profile(
            "Very slow, soft, grieving delivery with a long pause after choice."
        )
        anger = build_delivery_prosody_profile(
            "Fast, clipped, forceful anger with no hesitation."
        )
        self.assertLess(grief.tempo, 1.0)
        self.assertLess(grief.volume, 1.0)
        self.assertEqual(grief.pause_anchor, "choice")
        self.assertGreaterEqual(grief.pause_ms, 400)
        self.assertGreater(anger.tempo, 1.0)
        self.assertGreater(anger.volume, 1.0)
        self.assertEqual(anger.pause_ms, 0)

    def test_neutral_instruction_does_not_invent_prosody(self) -> None:
        profile = build_delivery_prosody_profile(
            "Natural conversational delivery with restrained emotion."
        )
        self.assertFalse(profile.active)
        self.assertEqual(profile.tempo, 1.0)
        self.assertEqual(profile.volume, 1.0)


class DeliveryProsodyAudioTests(unittest.TestCase):
    @staticmethod
    def write_tone(path: Path) -> None:
        sample_rate = 24000
        timeline = np.arange(sample_rate * 2, dtype=np.float32) / sample_rate
        waveform = 0.08 * np.sin(2.0 * np.pi * 180.0 * timeline)
        sf.write(path, waveform, sample_rate)

    def test_slow_soft_pause_enforcement_changes_duration_and_level(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "line.wav"
            self.write_tone(path)
            before_audio, before_rate = sf.read(path, dtype="float32")
            before_duration = len(before_audio) / before_rate
            before_rms = float(np.sqrt(np.mean(before_audio**2)))

            result = apply_delivery_prosody(
                audio_path=path,
                text="I did not have a choice, but I came back.",
                instruction=(
                    "Very slow, soft, grieving delivery with a long pause "
                    "after choice."
                ),
            )
            after_audio, after_rate = sf.read(path, dtype="float32")
            after_duration = len(after_audio) / after_rate
            after_rms = float(np.sqrt(np.mean(after_audio**2)))

            self.assertTrue(result["applied"])
            self.assertGreater(after_duration, before_duration + 0.7)
            self.assertLess(after_rms, before_rms)
            self.assertEqual(result["profile"]["pause_anchor"], "choice")

    def test_fast_forceful_enforcement_shortens_and_raises_level(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "line.wav"
            self.write_tone(path)
            before_audio, before_rate = sf.read(path, dtype="float32")
            before_duration = len(before_audio) / before_rate
            before_rms = float(np.sqrt(np.mean(before_audio**2)))

            result = apply_delivery_prosody(
                audio_path=path,
                text="Move now and do not look back.",
                instruction="Fast, clipped, forceful anger.",
            )
            after_audio, after_rate = sf.read(path, dtype="float32")
            after_duration = len(after_audio) / after_rate
            after_rms = float(np.sqrt(np.mean(after_audio**2)))

            self.assertTrue(result["applied"])
            self.assertLess(after_duration, before_duration)
            self.assertGreater(after_rms, before_rms)


if __name__ == "__main__":
    unittest.main()
