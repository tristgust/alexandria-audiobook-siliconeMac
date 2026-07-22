from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import mlx.core as mx
import torch

from instruction_propagation import (
    INSTRUCTION_PLACEMENT,
    InstructionPropagationError,
    build_instruction_propagation_contract,
    format_instruction_prompt,
    instruction_identity,
    normalize_instruction,
    validate_instruction_propagation_contract,
)
from mlx_backend import MLXBackend
from training_sidecar.qwen_training import (
    SidecarTrainingError,
    _metadata_entries,
    build_teacher_forcing_input,
    build_training_contract,
    dataset_fingerprint,
    enable_pytorch_icl_instruction,
    prepare_dataset,
    split_prepared_samples,
    tokenize_instruction_ids,
)


class TorchEmbedding:
    def __call__(self, ids: torch.Tensor) -> torch.Tensor:
        values = ids.to(torch.float32).unsqueeze(-1)
        return values.repeat(1, 1, 4)


class FakeCodePredictor:
    def __init__(self) -> None:
        self._embeddings = [TorchEmbedding()]

    def get_input_embeddings(self):
        return self._embeddings


class FakeTrainingTalker:
    def __init__(self) -> None:
        self.code_predictor = FakeCodePredictor()

    def text_projection(self, values: torch.Tensor) -> torch.Tensor:
        return values

    def get_text_embeddings(self):
        return TorchEmbedding()

    def get_input_embeddings(self):
        return TorchEmbedding()


class FakeTrainingModel:
    def __init__(self) -> None:
        talker_config = SimpleNamespace(
            num_code_groups=2,
            codec_language_id={},
            codec_nothink_id=11,
            codec_think_bos_id=12,
            codec_think_eos_id=13,
            codec_think_id=14,
            codec_pad_id=15,
            codec_bos_id=16,
        )
        self.talker = FakeTrainingTalker()
        self.config = SimpleNamespace(
            tts_bos_token_id=21,
            tts_eos_token_id=22,
            tts_pad_token_id=23,
            talker_config=talker_config,
        )


class RecordingProcessor:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def __call__(self, *, text: str, return_tensors: str, padding: bool):
        self.texts.append(text)
        values = [ord(character) % 97 + 1 for character in text]
        return {"input_ids": torch.tensor([values], dtype=torch.long)}


class RecordingPytorchModel:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return "generated"


class RecordingMLXTokenizer:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def encode(self, text: str) -> list[int]:
        self.texts.append(text)
        return [len(text), 2, 3]


class MLXEmbeddings:
    def __call__(self, ids):
        return mx.ones((ids.shape[0], ids.shape[1], 2))


class MLXTalker:
    def text_projection(self, values):
        return values

    def get_text_embeddings(self):
        return MLXEmbeddings()


class RecordingMLXModel:
    def __init__(self) -> None:
        self.tokenizer = RecordingMLXTokenizer()
        self.talker = MLXTalker()

    def _prepare_icl_generation_inputs(self, *_args, **_kwargs):
        return (
            mx.zeros((1, 2, 2)),
            mx.zeros((1, 1, 2)),
            mx.zeros((1, 1, 2)),
            mx.zeros((1, 1, 1)),
        )


class InstructionPropagationTests(unittest.TestCase):
    def sample(self, instruction_ids=None) -> dict:
        return {
            "source_index": 4,
            "codec_ids": torch.tensor([[31, 41], [32, 42]], dtype=torch.long),
            "spk_embedding": torch.tensor([0.1, 0.2, 0.3, 0.4]),
            "text_ids": torch.tensor(
                [[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]],
                dtype=torch.long,
            ),
            "instruction_ids": instruction_ids,
            "instruction": (
                "Urgent but controlled warning."
                if instruction_ids is not None
                else ""
            ),
            "text": "Run now.",
            "audio_sha256": "a" * 64,
            "duration": 1.25,
            "review_status": "approved",
        }

    def test_formatter_normalization_and_contract_fail_closed(self) -> None:
        normalized = normalize_instruction(
            "  Urgent\n but   controlled warning.  "
        )
        self.assertEqual(normalized, "Urgent but controlled warning.")
        self.assertEqual(
            format_instruction_prompt(normalized),
            "<|im_start|>user\nUrgent but controlled warning.<|im_end|>\n",
        )
        sample = {
            **self.sample(torch.tensor([[7, 8, 9]], dtype=torch.long)),
            **instruction_identity(
                normalized,
                token_ids=torch.tensor([[7, 8, 9]], dtype=torch.long),
            ),
        }
        contract = build_instruction_propagation_contract(
            mode="per_record",
            samples=[sample],
        )
        self.assertEqual(contract["placement"], INSTRUCTION_PLACEMENT)
        self.assertTrue(contract["instruction_required_at_inference"])
        self.assertEqual(validate_instruction_propagation_contract(contract), contract)
        tampered = copy.deepcopy(contract)
        tampered["records"][0]["instruction_sha256"] = "0" * 64
        with self.assertRaises(InstructionPropagationError):
            validate_instruction_propagation_contract(tampered)
        unexpected = copy.deepcopy(contract)
        unexpected["hidden_prompt"] = "not allowed"
        with self.assertRaisesRegex(
            InstructionPropagationError,
            "unexpected fields",
        ):
            validate_instruction_propagation_contract(unexpected)
        unexpected_record = copy.deepcopy(contract)
        unexpected_record["records"][0]["hidden_label"] = "not allowed"
        with self.assertRaisesRegex(
            InstructionPropagationError,
            "record has unexpected fields",
        ):
            validate_instruction_propagation_contract(unexpected_record)
        with self.assertRaises(InstructionPropagationError):
            build_instruction_propagation_contract(
                mode="per_record",
                samples=[self.sample(None)],
            )

    def test_teacher_forcing_prepends_instruction_and_preserves_original_icl(self) -> None:
        model = FakeTrainingModel()
        identity_sample = self.sample(None)
        conditioned_sample = self.sample(
            torch.tensor([[71, 72, 73]], dtype=torch.long)
        )
        identity_input, identity_labels, _, identity_prefill = (
            build_teacher_forcing_input(
                identity_sample,
                model,
                "cpu",
                torch.float32,
            )
        )
        conditioned_input, conditioned_labels, _, conditioned_prefill = (
            build_teacher_forcing_input(
                conditioned_sample,
                model,
                "cpu",
                torch.float32,
            )
        )
        self.assertEqual(conditioned_prefill, identity_prefill + 3)
        self.assertTrue(
            torch.equal(
                conditioned_input[:, 3:conditioned_prefill],
                identity_input[:, :identity_prefill],
            )
        )
        self.assertTrue(
            torch.equal(
                conditioned_input[:, conditioned_prefill:],
                identity_input[:, identity_prefill:],
            )
        )
        self.assertTrue(
            torch.equal(
                conditioned_labels[:, 3:],
                identity_labels,
            )
        )
        self.assertTrue(
            torch.all(conditioned_labels[:, :3] == -100).item()
        )
        self.assertEqual(
            conditioned_sample["instruction_prefill_token_count"],
            3,
        )
        self.assertEqual(
            conditioned_sample["instruction_placement"],
            INSTRUCTION_PLACEMENT,
        )

    def test_pytorch_and_mlx_use_the_same_formatted_instruction(self) -> None:
        instruction = "Measured grief without melodrama."
        processor = RecordingProcessor()
        pytorch_ids = tokenize_instruction_ids(
            processor=processor,
            instruction=instruction,
            device="cpu",
        )
        pytorch_model = RecordingPytorchModel()
        enable_pytorch_icl_instruction(pytorch_model)
        pytorch_model._alexandria_icl_instruction_ids = pytorch_ids
        result = pytorch_model.generate(
            input_ids=[torch.tensor([[1, 2]], dtype=torch.long)]
        )
        self.assertEqual(result, "generated")
        self.assertEqual(
            pytorch_model.calls[-1]["kwargs"]["instruct_ids"],
            [pytorch_ids],
        )
        pytorch_model._alexandria_icl_instruction_ids = None
        pytorch_model.generate(input_ids=[torch.tensor([[1, 2]])])
        self.assertNotIn(
            "instruct_ids",
            pytorch_model.calls[-1]["kwargs"],
        )

        mlx_model = RecordingMLXModel()
        MLXBackend._enable_qwen_icl_instruction(mlx_model)
        mlx_model._alexandria_icl_instruction = instruction
        prepared = mlx_model._prepare_icl_generation_inputs("text")[0]
        self.assertEqual(prepared.shape[1], 5)
        expected = format_instruction_prompt(instruction)
        self.assertEqual(processor.texts[-1], expected)
        self.assertEqual(mlx_model.tokenizer.texts[-1], expected)

    def test_instruction_dataset_aliases_and_reviewed_splits_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "metadata.jsonl").write_text(
                json.dumps(
                    {
                        "record_id": "train_1",
                        "audio_path": "clips/train_1.wav",
                        "transcript": "Exact reviewed transcript.",
                        "instruction": "Urgent but controlled.",
                        "review": {"status": "approved"},
                        "split": "train",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            entries = _metadata_entries(root)
            self.assertEqual(entries[0]["text"], "Exact reviewed transcript.")
            self.assertEqual(entries[0]["audio_filepath"], "clips/train_1.wav")
            self.assertEqual(entries[0]["review_status"], "approved")
            self.assertEqual(entries[0]["split"], "train")

            (root / "metadata.jsonl").write_text(
                json.dumps(
                    {
                        "text": "First wording.",
                        "transcript": "Different wording.",
                        "audio_filepath": "clips/train_1.wav",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                SidecarTrainingError,
                "conflicting text and transcript",
            ):
                _metadata_entries(root)

    def test_per_record_missing_instruction_fails_before_audio_or_model_work(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "metadata.jsonl").write_text(
                json.dumps(
                    {
                        "audio_path": "missing.wav",
                        "transcript": "Exact reviewed transcript.",
                        "instruction": "",
                        "review": {"status": "approved"},
                        "split": "train",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                SidecarTrainingError,
                "requires a reviewed instruction",
            ):
                prepare_dataset(
                    data_dir=root,
                    hf_model=None,
                    processor=None,
                    device="cpu",
                    dtype=torch.float32,
                    instruction_mode="per_record",
                )

    def test_explicit_reviewed_splits_override_fractional_resplit(self) -> None:
        samples = [
            {"source_index": 0, "split": "train"},
            {"source_index": 1, "split": "validation"},
            {"source_index": 2, "split": "test"},
        ]
        train, validation, metrics = split_prepared_samples(
            samples,
            validation_fraction=0.49,
            seed=999,
        )
        self.assertEqual([item["source_index"] for item in train], [0])
        self.assertEqual([item["source_index"] for item in validation], [1])
        self.assertEqual(metrics["strategy"], "reviewed_explicit")
        self.assertEqual(metrics["test_source_indices"], [2])
        partial = [
            {"source_index": 0, "split": "train"},
            {"source_index": 1, "split": None},
        ]
        with self.assertRaisesRegex(
            SidecarTrainingError,
            "present on every sample",
        ):
            split_prepared_samples(partial)

    def test_dataset_and_resume_fingerprints_bind_instruction_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            reference = Path(temporary) / "ref.wav"
            reference.write_bytes(b"reference")
            first_instruction = instruction_identity("Calm delivery.")
            second_instruction = instruction_identity("Urgent delivery.")
            first = [{**self.sample(None), **first_instruction}]
            second = [{**self.sample(None), **second_instruction}]
            identity_first = dataset_fingerprint(
                first,
                reference_audio_path=reference,
                instruction_mode="identity_only",
            )
            identity_second = dataset_fingerprint(
                second,
                reference_audio_path=reference,
                instruction_mode="identity_only",
            )
            self.assertEqual(identity_first, identity_second)
            conditioned_first = dataset_fingerprint(
                first,
                reference_audio_path=reference,
                instruction_mode="per_record",
            )
            conditioned_second = dataset_fingerprint(
                second,
                reference_audio_path=reference,
                instruction_mode="per_record",
            )
            self.assertNotEqual(conditioned_first, conditioned_second)
            self.assertNotEqual(identity_first, conditioned_first)

            propagation = build_instruction_propagation_contract(
                mode="per_record",
                samples=[
                    {
                        "source_index": 0,
                        "instruction": "Calm delivery.",
                    }
                ],
            )
            base = dict(
                mode="lora",
                model_name="Qwen/Qwen3-TTS-12Hz-1.7B-Base",
                model_revision="a" * 40,
                dataset_fingerprint_value=conditioned_first,
                target_profile="attention",
                lora_rank=8,
                lora_alpha=16,
                learning_rate=2e-5,
                gradient_accumulation_steps=2,
                language="english",
                max_audio_seconds=30.0,
                max_samples=20,
                validation_fraction=0.2,
                seed=1337,
            )
            conditioned_contract = build_training_contract(
                **base,
                instruction_propagation=propagation,
            )
            identity_contract = build_training_contract(**base)
            self.assertNotEqual(
                conditioned_contract["fingerprint"],
                identity_contract["fingerprint"],
            )
            self.assertEqual(
                conditioned_contract["instruction_propagation"]
                ["propagation_fingerprint"],
                propagation["propagation_fingerprint"],
            )


if __name__ == "__main__":
    unittest.main()
