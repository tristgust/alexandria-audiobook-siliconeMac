from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

from voice_overlay import (
    VoiceOverlayError,
    apply_voice_overlay_audio,
    apply_voice_overlay_instruction,
    normalize_voice_overlay,
    voice_overlay_fingerprint,
)


class VoiceOverlayTests(unittest.TestCase):
    def test_normalizes_and_bounds_controls(self) -> None:
        value = normalize_voice_overlay(
            {
                "direction": "  more clipped   and formal ",
                "pitch_semitones": -3,
                "pace_percent": 112.5,
                "level_db": -2,
            }
        )
        self.assertEqual(value["direction"], "more clipped and formal")
        self.assertEqual(value["pitch_semitones"], -3)
        self.assertEqual(value["pace_percent"], 112.5)
        self.assertEqual(value["level_db"], -2)
        self.assertEqual(len(voice_overlay_fingerprint(value)), 64)
        with self.assertRaises(VoiceOverlayError):
            normalize_voice_overlay({"pitch_semitones": 13})

    def test_direction_is_appended_without_replacing_line_instruction(self) -> None:
        self.assertEqual(
            apply_voice_overlay_instruction(
                "Controlled anger.",
                {"direction": "slightly higher, faster, and more synthetic"},
            ),
            "Controlled anger. Character-specific Voice direction: slightly higher, faster, and more synthetic",
        )

    def test_audio_overlay_changes_duration_and_keeps_readable_wav(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "voice.wav"
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(24000)
                handle.writeframes(b"\x01\x00" * 48000)
            before = path.stat().st_size
            result = apply_voice_overlay_audio(
                path,
                {
                    "pitch_semitones": 2,
                    "pace_percent": 125,
                    "level_db": -1,
                },
            )
            self.assertTrue(result["voice_overlay_applied"])
            self.assertNotEqual(path.stat().st_size, before)
            with wave.open(str(path), "rb") as handle:
                self.assertEqual(handle.getframerate(), 24000)
                self.assertGreater(handle.getnframes(), 1000)


if __name__ == "__main__":
    unittest.main()
