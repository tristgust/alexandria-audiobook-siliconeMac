from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import torch

from training_sidecar.qwen_training import (
    SidecarTrainingError,
    build_training_contract,
    load_training_checkpoint,
    lora_target_suffixes,
    restore_training_runtime_state,
    save_training_checkpoint,
    split_prepared_samples,
)


class FakeTrainable:
    def save_pretrained(self, directory: str | Path) -> None:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        (target / "adapter_config.json").write_text(
            json.dumps({"r": 8, "lora_alpha": 16}),
            encoding="utf-8",
        )
        (target / "adapter_model.safetensors").write_bytes(b"adapter")


class TrainingSidecarResumeTests(unittest.TestCase):
    def test_target_profiles_are_bounded_and_validated(self) -> None:
        self.assertEqual(
            lora_target_suffixes("attention"),
            ("q_proj", "k_proj", "v_proj", "o_proj"),
        )
        self.assertIn("gate_proj", lora_target_suffixes("attention_mlp"))
        with self.assertRaisesRegex(SidecarTrainingError, "attention"):
            lora_target_suffixes("everything")

    def test_split_is_deterministic_disjoint_and_nonempty(self) -> None:
        samples = [
            {"source_index": index, "text": f"sample {index}"}
            for index in range(10)
        ]
        first = split_prepared_samples(
            samples,
            validation_fraction=0.2,
            seed=42,
        )
        second = split_prepared_samples(
            samples,
            validation_fraction=0.2,
            seed=42,
        )
        self.assertEqual(first[2], second[2])
        train, validation, metrics = first
        self.assertEqual(len(train), 8)
        self.assertEqual(len(validation), 2)
        self.assertFalse(
            set(metrics["train_source_indices"])
            & set(metrics["validation_source_indices"])
        )
        self.assertEqual(
            sorted(
                metrics["train_source_indices"]
                + metrics["validation_source_indices"]
            ),
            list(range(10)),
        )

    def test_training_contract_changes_with_target_or_dataset(self) -> None:
        base = dict(
            mode="lora",
            model_name="Qwen/Qwen3-TTS-12Hz-1.7B-Base",
            model_revision="a" * 40,
            dataset_fingerprint_value="b" * 64,
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
        first = build_training_contract(**base)
        self.assertEqual(first, build_training_contract(**base))
        changed_target = build_training_contract(
            **{**base, "target_profile": "attention_mlp"}
        )
        changed_dataset = build_training_contract(
            **{**base, "dataset_fingerprint_value": "c" * 64}
        )
        self.assertNotEqual(first["fingerprint"], changed_target["fingerprint"])
        self.assertNotEqual(first["fingerprint"], changed_dataset["fingerprint"])

    def test_checkpoint_round_trip_and_contract_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "pilot"
            parameter = torch.nn.Parameter(torch.tensor([1.0]))
            optimizer = torch.optim.AdamW([parameter], lr=1e-3)
            loss = parameter.square().sum()
            loss.backward()
            optimizer.step()
            optimizer.zero_grad()
            contract = build_training_contract(
                mode="lora",
                model_name="Qwen/Qwen3-TTS-12Hz-1.7B-Base",
                model_revision="a" * 40,
                dataset_fingerprint_value="b" * 64,
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
            saved = save_training_checkpoint(
                output_dir=output,
                epoch=1,
                global_step=8,
                optimizer_steps=4,
                trainable_talker=FakeTrainable(),
                optimizer=optimizer,
                training_contract=contract,
                step_metrics=[{"epoch": 1, "loss": 1.0}],
                validation_metrics=[{"epoch": 1, "validation": {"loss": 1.1}}],
                device="cpu",
            )
            checkpoint = output / saved["path"]
            loaded = load_training_checkpoint(
                checkpoint_dir=checkpoint,
                expected_contract=contract,
            )
            self.assertEqual(loaded["state"]["completed_epoch"], 1)
            self.assertEqual(loaded["state"]["global_step"], 8)
            self.assertEqual(loaded["state"]["optimizer_steps"], 4)

            replacement_parameter = torch.nn.Parameter(torch.tensor([2.0]))
            replacement_optimizer = torch.optim.AdamW(
                [replacement_parameter],
                lr=1e-3,
            )
            restore_training_runtime_state(
                optimizer=replacement_optimizer,
                runtime_state=loaded["runtime_state"],
                device="cpu",
            )
            self.assertTrue(replacement_optimizer.state)

            incompatible = {**contract, "fingerprint": "0" * 64}
            with self.assertRaisesRegex(
                SidecarTrainingError,
                "incompatible",
            ):
                load_training_checkpoint(
                    checkpoint_dir=checkpoint,
                    expected_contract=incompatible,
                )


if __name__ == "__main__":
    unittest.main()
