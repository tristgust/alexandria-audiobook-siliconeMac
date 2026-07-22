from __future__ import annotations

import copy
import unittest

from generation_state import fingerprint_text
from instruction_dataset import (
    InstructionDatasetError,
    build_instruction_checkpoint_contract,
    build_instruction_dataset_manifest,
    build_instruction_training_receipt,
    normalize_delivery_label,
    validate_instruction_checkpoint_contract,
    validate_instruction_dataset_manifest,
    validate_instruction_record,
    validate_instruction_training_receipt,
)


class InstructionDatasetContractTests(unittest.TestCase):
    def record(
        self,
        record_id: str,
        *,
        split: str,
        audio_sha256: str,
        label: str = "Neutral",
        source_kind: str = "existing_recordings",
        license_scope: str = "owned",
        project_id: str = "project_1",
        character_id: str = "character_1",
    ) -> dict:
        transcript = f"Exact transcript for {record_id}."
        instruction = (
            "Natural, clear, conversational delivery."
            if label.casefold() in {"neutral", "natural"}
            else "Urgent but controlled, with focused breath and clear momentum."
        )
        return {
            "schema_version": 1,
            "record_id": record_id,
            "audio_path": f"clips/{record_id}.wav",
            "transcript": transcript,
            "instruction": instruction,
            "delivery_labels": [label],
            "split": split,
            "duration_ms": 3200,
            "sample_rate": 24000,
            "channels": 1,
            "provenance": {
                "source_kind": source_kind,
                "project_id": project_id,
                "character_id": character_id,
                "clip_id": f"clip_{record_id}",
                "audio_sha256": audio_sha256,
                "transcript_sha256": fingerprint_text(transcript),
                "instruction_sha256": fingerprint_text(instruction),
                "source_manifest_sha256": "a" * 64,
                "reviewed_source_fingerprint": "b" * 64,
                "license_scope": license_scope,
                "same_speaker_asserted": True,
            },
            "review": {
                "status": "approved",
                "reviewer_id": "reviewer_1",
                "reviewed_at_utc": "2026-07-21T12:00:00Z",
                "transcript_exact": True,
                "identity_retained": True,
                "delivery_labels_verified": True,
                "audio_quality_approved": True,
                "notes": "Reviewed against the source.",
            },
        }

    def manifest(self) -> dict:
        return build_instruction_dataset_manifest(
            dataset_id="bernice_instruction_v1",
            records=[
                self.record("train_1", split="train", audio_sha256="1" * 64),
                self.record(
                    "validation_1",
                    split="validation",
                    audio_sha256="2" * 64,
                    label="Urgency",
                ),
                self.record(
                    "test_1",
                    split="test",
                    audio_sha256="3" * 64,
                    label="dry sarcasm",
                ),
            ],
            base_model_key="pytorch_qwen_base",
            created_at_utc="2026-07-21T12:30:00Z",
            split_policy={
                "name": "speaker_locked_hash_grouped",
                "seed": 314159,
                "group_by_audio_sha256": True,
            },
        )

    def checkpoint(self, manifest: dict) -> dict:
        return build_instruction_checkpoint_contract(
            manifest=manifest,
            checkpoint_id="checkpoint_000100",
            training_kind="lora",
            created_at_utc="2026-07-21T13:00:00Z",
            step=100,
            hyperparameters={
                "learning_rate": 0.0001,
                "batch_size": 1,
                "gradient_accumulation_steps": 8,
                "seed": 314159,
            },
        )

    def test_delivery_labels_normalize_to_one_vocabulary(self) -> None:
        self.assertEqual(normalize_delivery_label("Natural"), "neutral")
        self.assertEqual(normalize_delivery_label("controlled anger"), "restrained_anger")
        self.assertEqual(normalize_delivery_label("whispered"), "whisper")
        self.assertEqual(normalize_delivery_label("dry sarcasm"), "sarcasm")
        with self.assertRaisesRegex(InstructionDatasetError, "Unsupported delivery"):
            normalize_delivery_label("vaguely cinematic")

    def test_record_normalizes_and_fingerprints_reviewed_instruction(self) -> None:
        value = self.record("train_1", split="train", audio_sha256="1" * 64)
        value["delivery_labels"] = ["Natural", "neutral"]
        normalized = validate_instruction_record(value)
        self.assertEqual(normalized["delivery_labels"], ["neutral"])
        self.assertRegex(normalized["record_fingerprint"], r"^[0-9a-f]{64}$")
        value["record_fingerprint"] = normalized["record_fingerprint"]
        self.assertEqual(
            validate_instruction_record(value)["record_fingerprint"],
            normalized["record_fingerprint"],
        )

    def test_record_rejects_unsafe_path_and_unapproved_review(self) -> None:
        record = self.record("train_1", split="train", audio_sha256="1" * 64)
        record["audio_path"] = "../outside.wav"
        with self.assertRaisesRegex(InstructionDatasetError, "confined relative"):
            validate_instruction_record(record)
        record = self.record("train_1", split="train", audio_sha256="1" * 64)
        record["review"]["status"] = "pending"
        with self.assertRaisesRegex(InstructionDatasetError, "Only approved"):
            validate_instruction_record(record)

    def test_record_rejects_tampered_transcript_instruction_and_audio_provenance(self) -> None:
        record = self.record("train_1", split="train", audio_sha256="1" * 64)
        record["transcript"] = "Changed after review."
        with self.assertRaisesRegex(InstructionDatasetError, "Transcript provenance"):
            validate_instruction_record(record)
        record = self.record("train_1", split="train", audio_sha256="1" * 64)
        record["instruction"] = "Changed after review."
        with self.assertRaisesRegex(InstructionDatasetError, "Instruction provenance"):
            validate_instruction_record(record)
        record = self.record("train_1", split="train", audio_sha256="not-a-hash")
        with self.assertRaisesRegex(InstructionDatasetError, "lowercase SHA-256"):
            validate_instruction_record(record)

    def test_record_rejects_license_and_same_speaker_claim_drift(self) -> None:
        synthetic = self.record(
            "train_1",
            split="train",
            audio_sha256="1" * 64,
            source_kind="synthetic",
            license_scope="owned",
        )
        with self.assertRaisesRegex(InstructionDatasetError, "Synthetic records"):
            validate_instruction_record(synthetic)
        recording = self.record("train_1", split="train", audio_sha256="1" * 64)
        recording["provenance"]["same_speaker_asserted"] = False
        with self.assertRaisesRegex(InstructionDatasetError, "must be true"):
            validate_instruction_record(recording)

    def test_manifest_is_deterministic_and_preserves_instruction_field(self) -> None:
        manifest = self.manifest()
        reordered = build_instruction_dataset_manifest(
            dataset_id="bernice_instruction_v1",
            records=list(reversed(manifest["records"])),
            base_model_key="pytorch_qwen_base",
            created_at_utc="2026-07-21T12:30:00Z",
            split_policy=manifest["split_policy"],
        )
        self.assertEqual(
            reordered["manifest_fingerprint"],
            manifest["manifest_fingerprint"],
        )
        self.assertEqual(manifest["fields"]["instruction"], "instruction")
        self.assertEqual(manifest["delivery_label_counts"]["neutral"], 1)
        self.assertEqual(manifest["delivery_label_counts"]["urgent"], 1)
        self.assertEqual(manifest["delivery_label_counts"]["sarcasm"], 1)
        self.assertFalse(manifest["production_assignment_supported"])
        self.assertEqual(
            validate_instruction_dataset_manifest(manifest)["manifest_fingerprint"],
            manifest["manifest_fingerprint"],
        )

    def test_manifest_rejects_identity_mixing_source_mixing_and_missing_validation(self) -> None:
        records = [
            self.record("train_1", split="train", audio_sha256="1" * 64),
            self.record(
                "validation_1",
                split="validation",
                audio_sha256="2" * 64,
                character_id="character_2",
            ),
        ]
        with self.assertRaisesRegex(InstructionDatasetError, "one stable project and character"):
            build_instruction_dataset_manifest(
                dataset_id="mixed_identity",
                records=records,
                base_model_key="pytorch_qwen_base",
                created_at_utc="2026-07-21T12:30:00Z",
                split_policy={
                    "name": "hash_grouped",
                    "seed": 1,
                    "group_by_audio_sha256": True,
                },
            )
        records[1] = self.record(
            "validation_1",
            split="validation",
            audio_sha256="2" * 64,
            source_kind="synthetic",
            license_scope="synthetic",
        )
        with self.assertRaisesRegex(InstructionDatasetError, "cannot mix"):
            build_instruction_dataset_manifest(
                dataset_id="mixed_source",
                records=records,
                base_model_key="pytorch_qwen_base",
                created_at_utc="2026-07-21T12:30:00Z",
                split_policy={
                    "name": "hash_grouped",
                    "seed": 1,
                    "group_by_audio_sha256": True,
                },
            )
        with self.assertRaisesRegex(InstructionDatasetError, "validation splits"):
            build_instruction_dataset_manifest(
                dataset_id="train_only",
                records=[records[0]],
                base_model_key="pytorch_qwen_base",
                created_at_utc="2026-07-21T12:30:00Z",
                split_policy={
                    "name": "hash_grouped",
                    "seed": 1,
                    "group_by_audio_sha256": True,
                },
            )

    def test_manifest_rejects_cross_split_audio_leakage(self) -> None:
        records = [
            self.record("train_1", split="train", audio_sha256="1" * 64),
            self.record("validation_1", split="validation", audio_sha256="1" * 64),
        ]
        with self.assertRaisesRegex(InstructionDatasetError, "more than one split"):
            build_instruction_dataset_manifest(
                dataset_id="split_leakage",
                records=records,
                base_model_key="pytorch_qwen_base",
                created_at_utc="2026-07-21T12:30:00Z",
                split_policy={
                    "name": "hash_grouped",
                    "seed": 1,
                    "group_by_audio_sha256": True,
                },
            )

    def test_manifest_tampering_is_rejected(self) -> None:
        manifest = self.manifest()
        tampered = copy.deepcopy(manifest)
        tampered["records"][0]["instruction"] = "Tampered."
        with self.assertRaises(InstructionDatasetError):
            validate_instruction_dataset_manifest(tampered)
        tampered = copy.deepcopy(manifest)
        tampered["production_assignment_supported"] = True
        with self.assertRaisesRegex(InstructionDatasetError, "production assignment"):
            validate_instruction_dataset_manifest(tampered)

    def test_checkpoint_binds_dataset_model_instruction_and_hyperparameters(self) -> None:
        manifest = self.manifest()
        checkpoint = self.checkpoint(manifest)
        self.assertEqual(
            checkpoint["dataset_manifest_fingerprint"],
            manifest["manifest_fingerprint"],
        )
        self.assertEqual(checkpoint["instruction_field"], "instruction")
        self.assertEqual(
            checkpoint["base_model"]["revision"],
            manifest["base_model"]["revision"],
        )
        self.assertFalse(checkpoint["production_assignment_supported"])
        self.assertEqual(
            validate_instruction_checkpoint_contract(
                checkpoint,
                manifest=manifest,
            )["checkpoint_fingerprint"],
            checkpoint["checkpoint_fingerprint"],
        )
        tampered = copy.deepcopy(checkpoint)
        tampered["hyperparameters"]["learning_rate"] = 0.9
        with self.assertRaisesRegex(InstructionDatasetError, "fingerprint"):
            validate_instruction_checkpoint_contract(tampered, manifest=manifest)

    def test_receipt_binds_checkpoint_and_cannot_claim_listening_or_production(self) -> None:
        manifest = self.manifest()
        checkpoint = self.checkpoint(manifest)
        receipt = build_instruction_training_receipt(
            manifest=manifest,
            checkpoint=checkpoint,
            run_id="run_0001",
            status="completed",
            started_at_utc="2026-07-21T13:00:00Z",
            finished_at_utc="2026-07-21T13:30:00Z",
            metrics={"loss": 1.25, "steps": 100},
            output_artifact_fingerprint="c" * 64,
        )
        self.assertEqual(
            receipt["checkpoint_fingerprint"],
            checkpoint["checkpoint_fingerprint"],
        )
        self.assertEqual(receipt["manual_audio_review_status"], "pending")
        self.assertFalse(receipt["production_assignment_supported"])
        self.assertEqual(
            validate_instruction_training_receipt(
                receipt,
                manifest=manifest,
                checkpoint=checkpoint,
            )["receipt_fingerprint"],
            receipt["receipt_fingerprint"],
        )
        tampered = copy.deepcopy(receipt)
        tampered["manual_audio_review_status"] = "approved"
        with self.assertRaisesRegex(InstructionDatasetError, "completed human listening"):
            validate_instruction_training_receipt(
                tampered,
                manifest=manifest,
                checkpoint=checkpoint,
            )

    def test_receipt_rejects_checkpoint_from_another_dataset(self) -> None:
        manifest = self.manifest()
        checkpoint = self.checkpoint(manifest)
        other = copy.deepcopy(manifest)
        other["dataset_id"] = "another_dataset"
        other["manifest_fingerprint"] = "f" * 64
        with self.assertRaises(InstructionDatasetError):
            build_instruction_training_receipt(
                manifest=other,
                checkpoint=checkpoint,
                run_id="run_0001",
                status="completed",
                started_at_utc="2026-07-21T13:00:00Z",
                finished_at_utc="2026-07-21T13:30:00Z",
                metrics={},
            )


if __name__ == "__main__":
    unittest.main()
