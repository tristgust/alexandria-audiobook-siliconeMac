from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import mlx.core as mx
import numpy as np

from instruction_propagation import build_instruction_propagation_contract
from mlx_backend import MLXBackend
from tts import TTSEngine


class FakeMLXLoraBackend:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate_merged_lora_clone(self, **kwargs):
        self.calls.append(dict(kwargs))
        Path(kwargs["output_path"]).write_bytes(b"generated")
        return True


class MLXLoraRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.model = self.root / "lora_models" / "doctor" / "mlx_model"
        self.model.mkdir(parents=True)
        (self.model / "model.safetensors").write_bytes(b"mlx")
        self.reference = self.root / "reference.wav"
        self.reference.write_bytes(b"reference")
        self.backend = FakeMLXLoraBackend()
        self.engine = TTSEngine({"tts": {"mode": "local"}})
        self.engine._use_mlx = True
        self.engine._mlx_backend = self.backend

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def per_record_contract() -> dict:
        return build_instruction_propagation_contract(
            mode="per_record",
            samples=[
                {
                    "source_index": 0,
                    "instruction": "Controlled anger.",
                }
            ],
        )

    def voice_data(self) -> dict:
        return {
            "type": "lora",
            "adapter_path": str(self.model.parent),
            "mlx_model_path": str(self.model),
            "ref_audio": str(self.reference),
            "ref_text": "Exact supplied transcript.",
            "character_style": "Preserve the supplied identity.",
            "lora_mlx_temperature": 0.8,
            "lora_mlx_top_k": 40,
            "lora_mlx_top_p": 0.95,
            "lora_mlx_repetition_penalty": 1.6,
            "lora_mlx_max_tokens": 1400,
        }

    def test_exported_lora_routes_to_mlx_with_instruction(self) -> None:
        output = self.root / "out.wav"
        result = self.engine.generate_lora_voice(
            "Tell me the truth.",
            "Controlled anger.",
            self.voice_data(),
            str(output),
        )
        self.assertTrue(result)
        self.assertTrue(output.is_file())
        call = self.backend.calls[0]
        self.assertEqual(call["model_path"], str(self.model))
        self.assertEqual(call["ref_audio"], str(self.reference))
        self.assertEqual(call["ref_text"], "Exact supplied transcript.")
        self.assertIn("Controlled anger", call["instruct"])
        self.assertIn("supplied identity", call["instruct"])
        self.assertEqual(call["temperature"], 0.8)
        self.assertEqual(call["top_k"], 40)
        self.assertEqual(call["top_p"], 0.95)
        self.assertEqual(call["repetition_penalty"], 1.6)
        self.assertEqual(call["max_tokens"], 1400)

    def test_per_record_mlx_voice_requires_instruction(self) -> None:
        voice_data = {
            **self.voice_data(),
            "character_style": "",
            "instruction_propagation": self.per_record_contract(),
        }
        result = self.engine.generate_lora_voice(
            "Tell me the truth.",
            "",
            voice_data,
            str(self.root / "missing-instruction.wav"),
        )
        self.assertFalse(result)
        self.assertEqual(self.backend.calls, [])

        result = self.engine.generate_lora_voice(
            "Tell me the truth.",
            "  Controlled\n anger.  ",
            voice_data,
            str(self.root / "with-instruction.wav"),
        )
        self.assertTrue(result)
        self.assertEqual(
            self.backend.calls[-1]["instruct"],
            "Controlled anger.",
        )

    def test_per_record_contract_can_be_loaded_from_training_metadata(self) -> None:
        (self.model.parent / "training_meta.json").write_text(
            json.dumps(
                {
                    "instruction_propagation": self.per_record_contract(),
                }
            ),
            encoding="utf-8",
        )
        voice_data = {
            **self.voice_data(),
            "character_style": "",
        }
        result = self.engine.generate_lora_voice(
            "Tell me the truth.",
            "",
            voice_data,
            str(self.root / "metadata-missing-instruction.wav"),
        )
        self.assertFalse(result)
        self.assertEqual(self.backend.calls, [])

    def test_exported_model_can_supply_reference_files(self) -> None:
        ref = self.model / "ref_sample.wav"
        text = self.model / "ref_sample.txt"
        ref.write_bytes(b"reference")
        text.write_text("Artifact transcript.", encoding="utf-8")
        voice_data = {
            "type": "lora",
            "mlx_model_path": str(self.model),
        }
        result = self.engine.generate_lora_voice(
            "Hello.",
            "Softly.",
            voice_data,
            str(self.root / "out.wav"),
        )
        self.assertTrue(result)
        call = self.backend.calls[0]
        self.assertEqual(call["ref_audio"], str(ref))
        self.assertEqual(call["ref_text"], "Artifact transcript.")

    def test_missing_exported_model_fails_without_pytorch_import(self) -> None:
        voice_data = {
            "type": "lora",
            "adapter_path": str(self.root / "missing"),
        }
        with patch.dict("sys.modules", {"qwen_tts": None}):
            result = self.engine.generate_lora_voice(
                "Hello.",
                "Neutral.",
                voice_data,
                str(self.root / "out.wav"),
            )
        self.assertFalse(result)
        self.assertEqual(self.backend.calls, [])

    def test_mlx_lora_batch_preserves_each_instruction(self) -> None:
        config = {"DOCTOR": self.voice_data()}
        chunks = [
            {
                "index": 1,
                "speaker": "DOCTOR",
                "text": "Run.",
                "instruct": "Urgent warning.",
            },
            {
                "index": 2,
                "speaker": "DOCTOR",
                "text": "It is all right.",
                "instruct": "Soft reassurance.",
            },
        ]
        with patch.object(self.engine, "_clear_gpu_cache"):
            result = self.engine.generate_batch(
                chunks,
                config,
                str(self.root),
            )
        self.assertEqual(result["completed"], [1, 2])
        self.assertEqual(result["failed"], [])
        self.assertEqual(
            [call["instruct"] for call in self.backend.calls],
            [
                "Urgent warning. Preserve the supplied identity.",
                "Soft reassurance. Preserve the supplied identity.",
            ],
        )


class FakeTokenizer:
    def encode(self, _text):
        return [1, 2, 3]


class FakeEmbeddings:
    def __call__(self, ids):
        return mx.ones((ids.shape[0], ids.shape[1], 2))


class FakeTalker:
    def text_projection(self, values):
        return values * 2

    def get_text_embeddings(self):
        return FakeEmbeddings()


class FakeQwenModel:
    def __init__(self) -> None:
        self.tokenizer = FakeTokenizer()
        self.talker = FakeTalker()

    def _prepare_icl_generation_inputs(self, *_args, **_kwargs):
        return (
            mx.zeros((1, 2, 2)),
            mx.zeros((1, 1, 2)),
            mx.zeros((1, 1, 2)),
            mx.zeros((1, 1, 1)),
        )


class FakePytorchLoraModel:
    def __init__(self) -> None:
        self.prompt_calls: list[dict] = []
        self.tokenized_texts: list[list[str]] = []
        self.generation_calls: list[dict] = []

    def create_voice_clone_prompt(self, **kwargs):
        self.prompt_calls.append(dict(kwargs))
        return {"prompt": "fixture"}

    def _tokenize_texts(self, texts):
        self.tokenized_texts.append(list(texts))
        return ["fixture-instruction-ids"]

    def generate_voice_clone(self, **kwargs):
        self.generation_calls.append(dict(kwargs))
        return [np.zeros(240, dtype=np.float32)], 24000


class PytorchLoraInstructionRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.adapter = self.root / "lora_models" / "doctor"
        self.adapter.mkdir(parents=True)
        (self.adapter / "ref_sample.wav").write_bytes(b"fixture-reference")
        (self.adapter / "training_meta.json").write_text(
            json.dumps(
                {
                    "ref_sample_text": "Exact supplied transcript.",
                    "instruction_propagation": (
                        build_instruction_propagation_contract(
                            mode="per_record",
                            samples=[
                                {
                                    "source_index": 0,
                                    "instruction": "Controlled anger.",
                                }
                            ],
                        )
                    ),
                }
            ),
            encoding="utf-8",
        )
        self.engine = TTSEngine({"tts": {"mode": "local"}})
        self.engine._use_mlx = False
        self.model = FakePytorchLoraModel()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def voice_data(self) -> dict:
        return {
            "type": "lora",
            "adapter_path": str(self.adapter),
            "character_style": "",
        }

    def test_per_record_pytorch_voice_requires_instruction(self) -> None:
        with (
            patch.object(self.engine, "_init_local_lora", return_value=self.model),
            patch("tts.sf.read", return_value=(np.zeros(2400), 24000)),
            patch.object(self.engine, "_save_wav"),
        ):
            result = self.engine.generate_lora_voice(
                "Tell me the truth.",
                "",
                self.voice_data(),
                str(self.root / "missing.wav"),
            )
        self.assertFalse(result)
        self.assertEqual(self.model.generation_calls, [])

    def test_per_record_pytorch_voice_uses_shared_formatter(self) -> None:
        with (
            patch.object(self.engine, "_init_local_lora", return_value=self.model),
            patch("tts.sf.read", return_value=(np.zeros(2400), 24000)),
            patch.object(self.engine, "_save_wav") as save,
        ):
            result = self.engine.generate_lora_voice(
                "Tell me the truth.",
                "  Controlled\n anger.  ",
                self.voice_data(),
                str(self.root / "conditioned.wav"),
            )
        self.assertTrue(result)
        self.assertEqual(
            self.model.tokenized_texts[-1],
            ["<|im_start|>user\nControlled anger.<|im_end|>\n"],
        )
        self.assertEqual(
            self.model.generation_calls[-1]["instruct_ids"],
            ["fixture-instruction-ids"],
        )
        save.assert_called_once()


class MLXInstructionPatchTests(unittest.TestCase):
    def test_patch_prepends_instruction_embedding_only_when_set(self) -> None:
        model = FakeQwenModel()
        MLXBackend._enable_qwen_icl_instruction(model)

        no_instruction = model._prepare_icl_generation_inputs("text")[0]
        self.assertEqual(no_instruction.shape, (1, 2, 2))

        model._alexandria_icl_instruction = "Controlled anger."
        instructed = model._prepare_icl_generation_inputs("text")[0]
        self.assertEqual(instructed.shape, (1, 5, 2))
        self.assertTrue(
            mx.array_equal(
                instructed[:, :3, :],
                mx.full((1, 3, 2), 2.0),
            ).item()
        )

    def test_patch_is_idempotent(self) -> None:
        model = FakeQwenModel()
        MLXBackend._enable_qwen_icl_instruction(model)
        first = model._prepare_icl_generation_inputs
        MLXBackend._enable_qwen_icl_instruction(model)
        self.assertIs(model._prepare_icl_generation_inputs, first)


if __name__ == "__main__":
    unittest.main()
