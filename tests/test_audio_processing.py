from __future__ import annotations

import builtins
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from audio_processing import decode_audio_mono, temporary_mono_wav


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


if __name__ == "__main__":
    unittest.main()
