from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import train_lora
from voice_backend_capabilities import VoiceBackendCapabilityError


class TrainLoraCapabilityGateTests(unittest.TestCase):
    def test_train_fails_before_torch_or_qwen_import(self) -> None:
        args = SimpleNamespace()
        with patch(
            "train_lora.require_lora_training_supported",
            side_effect=VoiceBackendCapabilityError("measured unsupported"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "LoRA training is disabled.*measured unsupported",
            ):
                train_lora.train(args)

    def test_module_import_does_not_import_qwen_tts(self) -> None:
        self.assertNotIn("qwen_tts", train_lora.__dict__)
        self.assertNotIn("Qwen3TTSModel", train_lora.__dict__)


if __name__ == "__main__":
    unittest.main()
