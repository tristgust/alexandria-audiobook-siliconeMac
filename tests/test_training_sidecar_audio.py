from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from training_sidecar.qwen_training import (
    SidecarTrainingError,
    write_canonical_reference_wav,
)


class TrainingSidecarReferenceAudioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_mislabeled_compressed_input_becomes_real_pcm_wav(self) -> None:
        source = self.root / "supplied_clip.mp3"
        audio = np.sin(
            np.linspace(0.0, 30.0, 22050, dtype=np.float32)
        ) * 0.1
        sf.write(source, audio, 22050, format="FLAC")
        self.assertEqual(source.read_bytes()[:4], b"fLaC")

        target = self.root / "ref_sample.wav"
        result = write_canonical_reference_wav(source, target)

        self.assertEqual(target.read_bytes()[:4], b"RIFF")
        info = sf.info(target)
        self.assertEqual(info.format, "WAV")
        self.assertEqual(info.samplerate, 24000)
        self.assertEqual(info.channels, 1)
        self.assertGreater(info.frames, 0)
        self.assertEqual(result["sample_rate"], 24000)
        self.assertEqual(result["channels"], 1)
        self.assertRegex(result["sha256"], r"^[0-9a-f]{64}$")

    def test_missing_or_empty_reference_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            SidecarTrainingError,
            "does not exist",
        ):
            write_canonical_reference_wav(
                self.root / "missing.mp3",
                self.root / "ref.wav",
            )
        empty = self.root / "empty.wav"
        empty.write_bytes(b"")
        with self.assertRaisesRegex(
            SidecarTrainingError,
            "empty",
        ):
            write_canonical_reference_wav(
                empty,
                self.root / "ref.wav",
            )


if __name__ == "__main__":
    unittest.main()
