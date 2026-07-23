from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from mlx_backend import MLXBackend


class MergedLoraSeedTests(unittest.TestCase):
    def test_fixed_seed_is_applied_before_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            output = root / "generated.wav"
            sf.write(reference, np.zeros(2400, dtype=np.float32), 24000)
            captured: dict[str, object] = {}

            class FakeModel:
                sample_rate = 24000
                _alexandria_icl_instruction = None

                def generate(self, **kwargs):
                    captured["kwargs"] = dict(kwargs)
                    captured["instruction"] = self._alexandria_icl_instruction
                    return [object()]

            backend = MLXBackend()
            model = FakeModel()
            with (
                patch.object(backend, "_external_qwen_model", return_value=model),
                patch.object(
                    backend,
                    "_collect_audio",
                    return_value=(np.zeros(2400, dtype=np.float32), 24000),
                ),
                patch("mlx_backend.mx.random.seed") as seed_mock,
            ):
                result = backend.generate_merged_lora_clone(
                    text="Tell me the truth.",
                    ref_audio=str(reference),
                    ref_text="Exact reference transcript.",
                    instruct="Controlled anger.",
                    model_path=str(root / "model"),
                    output_path=str(output),
                    seed=20260723,
                )

            self.assertTrue(result)
            self.assertTrue(output.is_file())
            seed_mock.assert_called_once_with(20260723)
            self.assertEqual(captured["instruction"], "Controlled anger.")
            self.assertEqual(captured["kwargs"]["temperature"], 0.9)
            self.assertIsNone(model._alexandria_icl_instruction)


if __name__ == "__main__":
    unittest.main()
