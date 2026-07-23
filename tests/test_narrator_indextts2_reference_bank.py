from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "benchmarks"
    / "prepare_narrator_indextts2_reference_bank.py"
)
SPEC = importlib.util.spec_from_file_location("narrator_indextts2_reference_bank", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class NarratorIndexTTS2ReferenceBankTests(unittest.TestCase):
    @staticmethod
    def _tone(path: Path, frequency: float, *, seconds: float = 1.2) -> None:
        sample_rate = 24000
        timeline = np.arange(int(sample_rate * seconds), dtype=np.float32) / sample_rate
        waveform = (0.18 * np.sin(2 * np.pi * frequency * timeline)).astype(np.float32)
        sf.write(path, waveform, sample_rate)

    def test_matrix_contains_three_strengths_for_six_styles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = root / "identity.wav"
            self._tone(identity, 110)
            emotion_paths = {}
            for index, style in enumerate(MODULE.STYLE_SPECS):
                path = root / f"{style}.wav"
                self._tone(path, 120 + index * 5)
                emotion_paths[style] = path

            matrix_path = MODULE.write_matrix(
                output_root=root,
                identity_audio=identity,
                emotion_paths=emotion_paths,
            )
            matrix = MODULE.json.loads(matrix_path.read_text(encoding="utf-8"))

            self.assertEqual(len(matrix["styles"]), 6)
            self.assertEqual(len(matrix["samples"]), 18)
            self.assertEqual(
                {sample["alpha"] for sample in matrix["samples"]},
                set(MODULE.ALPHAS),
            )
            for style in MODULE.STYLE_SPECS:
                self.assertEqual(
                    sum(sample["style"] == style for sample in matrix["samples"]),
                    3,
                )

    def test_pitch_trajectory_flags_large_upward_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rising.wav"
            sample_rate = 24000
            thirds = []
            for frequency in (100.0, 170.0, 330.0):
                timeline = np.arange(sample_rate, dtype=np.float32) / sample_rate
                thirds.append(0.18 * np.sin(2 * np.pi * frequency * timeline))
            sf.write(path, np.concatenate(thirds).astype(np.float32), sample_rate)

            metrics = MODULE.acoustic_metrics(path, 8)

            self.assertTrue(metrics["pitch_trajectory_anomaly"])
            self.assertGreater(metrics["pitch_end_start_ratio"], 2.5)


if __name__ == "__main__":
    unittest.main()
