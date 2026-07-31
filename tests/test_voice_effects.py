from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from voice_effects import (
    VoiceEffectError,
    apply_voice_effect_chain,
    validate_voice_effect_chain,
)


class VoiceEffectsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.audio = self.root / "voice.wav"
        rate = 24000
        time_axis = np.arange(rate, dtype=np.float32) / float(rate)
        source = 0.25 * np.sin(2.0 * np.pi * 220.0 * time_axis)
        sf.write(str(self.audio), source, rate, subtype="PCM_16")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_known_chain_is_deterministic_and_replaces_audio(self) -> None:
        first = self.root / "first.wav"
        second = self.root / "second.wav"
        first.write_bytes(self.audio.read_bytes())
        second.write_bytes(self.audio.read_bytes())
        first_receipt = apply_voice_effect_chain(
            first,
            "under_sergeant_intercom_v1",
        )
        second_receipt = apply_voice_effect_chain(
            second,
            "under_sergeant_intercom_v1",
        )
        self.assertEqual(
            first_receipt["output_sha256"],
            second_receipt["output_sha256"],
        )
        self.assertNotEqual(
            first_receipt["source_sha256"],
            first_receipt["output_sha256"],
        )

    def test_none_is_a_noop_and_unknown_chain_fails(self) -> None:
        self.assertIsNone(apply_voice_effect_chain(self.audio, None))
        with self.assertRaises(VoiceEffectError):
            validate_voice_effect_chain("invented_effect")


if __name__ == "__main__":
    unittest.main()
