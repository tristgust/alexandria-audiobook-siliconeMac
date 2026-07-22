from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from tests.test_voice_training_projects import VoiceTrainingProjectFixture
from voice_training_actions import (
    VoiceTrainingActionError,
    VoiceTrainingConflictError,
    apply_voice_training_action,
    calculate_training_readiness,
    create_voice_training_project_file,
    mutate_voice_training_project_file,
)
from voice_training_projects import (
    build_voice_training_project,
    read_voice_training_project,
    voice_training_project_path,
)


class VoiceTrainingActionTests(
    unittest.TestCase,
    VoiceTrainingProjectFixture,
):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_path = self.root / "book.txt"
        self.source_path.write_text(self.SOURCE_TEXT, encoding="utf-8")
        self.roster = self.approved_roster(self.source_path)
        self.doctor = next(
            item
            for item in self.roster["entries"]
            if item["canonical_name"] == "THE DOCTOR"
        )
        self.project = build_voice_training_project(
            approved_roster=self.roster,
            character_id=self.doctor["id"],
            priority="primary",
            desired_description="Draft description.",
            desired_ref_text="Draft reference.",
            created_at_utc=self.TIME,
        )
        self.projects_root = self.root / "voice_training_projects"
        self.project_path = voice_training_project_path(
            self.projects_root,
            self.doctor["id"],
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def apply(
        self,
        action: str,
        payload: dict | None = None,
        *,
        expected_fingerprint: str | None = None,
    ) -> dict:
        self.project = apply_voice_training_action(
            self.project,
            expected_fingerprint=(
                expected_fingerprint
                if expected_fingerprint is not None
                else self.project["project_fingerprint"]
            ),
            action=action,
            payload=payload,
            expected_character_id=self.doctor["id"],
            expected_source_fingerprint=self.roster["source"]["fingerprint"],
            expected_roster_fingerprint=self.roster["roster_fingerprint"],
            at_utc=self.TIME,
        )
        return self.project

    def approve_persona(self) -> None:
        self.apply(
            "approve_persona",
            {
                "description": (
                    "An alert older traveler with a warm, weathered tenor, "
                    "precise diction, and elastic emotional timing."
                ),
                "ref_text": (
                    "There are worlds out there where the sky is burning."
                ),
            },
        )

    def create_synthetic(self) -> None:
        self.approve_persona()
        self.apply(
            "create_synthetic_project",
            {
                "seed_supported": True,
                "global_seed": 42,
                "sample_target": 24,
            },
        )

    def add_accepted_synthetic_sample(self) -> dict:
        sample = self.synthetic_sample()
        sample["review_status"] = "pending"
        sample["review_notes"] = ""
        self.apply("add_synthetic_sample", {"sample": sample})
        self.apply(
            "review_synthetic_sample",
            {
                "clip_id": sample["clip_id"],
                "review_status": "accepted",
                "review_notes": "Identity remains stable.",
                "drift_flags": [],
            },
        )
        return sample

    def approve_synthetic_dataset(self) -> dict:
        sample = self.add_accepted_synthetic_sample()
        self.apply(
            "approve_dataset",
            {
                "source_kind": "synthetic",
                "clip_ids": [sample["clip_id"]],
                "metadata_path": "synthetic/metadata.jsonl",
            },
        )
        return sample

    def make_ready(self) -> dict:
        self.create_synthetic()
        sample = self.approve_synthetic_dataset()
        self.apply("select_reference", {"clip_id": sample["clip_id"]})
        return sample

    def test_stale_fingerprint_is_rejected(self) -> None:
        stale = self.project["project_fingerprint"]
        self.apply(
            "update_persona",
            {
                "description": "Updated description.",
                "ref_text": "Updated reference.",
            },
        )
        with self.assertRaisesRegex(
            VoiceTrainingConflictError,
            "changed after this action was loaded",
        ):
            self.apply(
                "approve_persona",
                expected_fingerprint=stale,
            )

    def test_character_source_and_roster_ownership_are_checked(self) -> None:
        with self.assertRaisesRegex(
            VoiceTrainingConflictError,
            "another approved roster",
        ):
            apply_voice_training_action(
                self.project,
                expected_fingerprint=self.project["project_fingerprint"],
                action="refresh_readiness",
                expected_character_id=self.doctor["id"],
                expected_source_fingerprint=self.roster["source"]["fingerprint"],
                expected_roster_fingerprint="f" * 64,
                at_utc=self.TIME,
            )

    def test_persona_update_returns_to_draft_and_approval_is_explicit(self) -> None:
        self.apply(
            "update_persona",
            {
                "description": "A precise, alert older traveler.",
                "ref_text": "Tell me what happened.",
            },
        )
        self.assertEqual(
            self.project["desired_base_persona"]["approval_status"],
            "draft",
        )
        self.apply("approve_persona")
        persona = self.project["desired_base_persona"]
        self.assertEqual(persona["approval_status"], "approved")
        self.assertIsNotNone(persona["approved_fingerprint"])

    def test_synthetic_project_requires_approved_persona(self) -> None:
        with self.assertRaisesRegex(
            VoiceTrainingActionError,
            "Approve the desired base persona",
        ):
            self.apply(
                "create_synthetic_project",
                {
                    "seed_supported": True,
                    "global_seed": 42,
                    "sample_target": 24,
                },
            )

    def test_synthetic_sample_review_preserves_exact_generation_provenance(self) -> None:
        self.create_synthetic()
        sample = self.synthetic_sample()
        sample["review_status"] = "pending"
        sample["review_notes"] = ""
        self.apply("add_synthetic_sample", {"sample": sample})
        self.apply(
            "review_synthetic_sample",
            {
                "clip_id": sample["clip_id"],
                "review_status": "regenerate",
                "review_notes": "Accent drifted.",
                "drift_flags": ["accent"],
            },
        )
        saved = self.project["designed_voice_project"]["samples"][0]
        self.assertEqual(saved["text"], sample["text"])
        self.assertEqual(saved["instruction"], sample["instruction"])
        self.assertEqual(saved["seed"], 42)
        self.assertEqual(saved["review_status"], "regenerate")
        self.assertEqual(saved["drift_flags"], ["accent"])

    def test_dataset_approval_rejects_unaccepted_synthetic_samples(self) -> None:
        self.create_synthetic()
        sample = self.synthetic_sample()
        sample["review_status"] = "pending"
        sample["review_notes"] = ""
        self.apply("add_synthetic_sample", {"sample": sample})
        with self.assertRaisesRegex(
            VoiceTrainingActionError,
            "is not accepted",
        ):
            self.apply(
                "approve_dataset",
                {
                    "source_kind": "synthetic",
                    "clip_ids": [sample["clip_id"]],
                    "metadata_path": "synthetic/metadata.jsonl",
                },
            )

    def test_dataset_and_reference_are_separate_approval_boundaries(self) -> None:
        self.create_synthetic()
        sample = self.approve_synthetic_dataset()
        self.assertEqual(
            self.project["training_readiness"]["status"],
            "not_ready",
        )
        self.assertTrue(
            any(
                "Select an approved dataset clip" in blocker
                for blocker in self.project["training_readiness"]["blockers"]
            )
        )
        self.apply("select_reference", {"clip_id": sample["clip_id"]})
        self.assertEqual(
            self.project["training_readiness"]["status"],
            "ready_for_feasibility_review",
        )
        self.assertIsNone(self.project["adapter_provenance"])
        self.assertIsNone(self.project["adapter_assignment"])

    def test_record_dataset_export_keeps_one_manifest_and_zip(self) -> None:
        self.create_synthetic()
        self.approve_synthetic_dataset()
        self.apply(
            "record_dataset_export",
            {
                "dataset_path": "synthetic/export",
                "metadata_path": "synthetic/export/metadata.jsonl",
                "zip_path": "synthetic/export/doctor-dataset.zip",
            },
        )
        dataset = self.project["dataset_project"]
        source = self.project["designed_voice_project"]
        self.assertEqual(dataset["status"], "exported")
        self.assertEqual(
            dataset["metadata_path"],
            "synthetic/export/metadata.jsonl",
        )
        self.assertEqual(
            dataset["zip_path"],
            "synthetic/export/doctor-dataset.zip",
        )
        self.assertEqual(
            source["export"]["dataset_fingerprint"],
            dataset["dataset_fingerprint"],
        )

    def test_source_clip_mutation_is_blocked_after_dataset_approval(self) -> None:
        self.create_synthetic()
        sample = self.approve_synthetic_dataset()
        with self.assertRaisesRegex(
            VoiceTrainingActionError,
            "blocked after dataset approval",
        ):
            self.apply(
                "review_synthetic_sample",
                {
                    "clip_id": sample["clip_id"],
                    "review_status": "rejected",
                    "review_notes": "Changed mind.",
                    "drift_flags": [],
                },
            )

    def test_existing_recording_path_requires_same_speaker_declaration(self) -> None:
        with self.assertRaisesRegex(
            VoiceTrainingActionError,
            "same-speaker declaration",
        ):
            self.apply(
                "create_recording_project",
                {
                    "same_speaker_declared": False,
                    "speaker_declaration": "THE DOCTOR",
                },
            )
        self.apply(
            "create_recording_project",
            {
                "same_speaker_declared": True,
                "speaker_declaration": "THE DOCTOR",
            },
        )
        self.assertTrue(
            self.project["existing_recordings"]["same_speaker_declared"]
        )

    def test_existing_recording_actions_preserve_file_and_clip_provenance(self) -> None:
        fixture = self.existing_recording_project()
        self.apply(
            "create_recording_project",
            {
                "same_speaker_declared": True,
                "speaker_declaration": fixture["speaker_declaration"],
            },
        )
        self.apply(
            "add_recording_file",
            {"file": fixture["files"][0]},
        )
        clip = copy.deepcopy(fixture["clips"][0])
        clip["transcript_corrected"] = False
        clip["duplicate_status"] = "unchecked"
        clip["contamination_status"] = "unchecked"
        clip["inclusion_decision"] = "pending"
        self.apply("add_recording_clip", {"clip": clip})
        self.apply(
            "review_recording_clip",
            {
                "clip_id": clip["clip_id"],
                "transcript": "Tell me what happened here.",
                "transcript_confidence": 0.95,
                "transcript_corrected": True,
                "audio_quality_score": 0.9,
                "duplicate_status": "unique",
                "contamination_status": "clean",
                "inclusion_decision": "included",
                "style_label": "measured curiosity",
            },
        )
        saved = self.project["existing_recordings"]
        self.assertEqual(
            saved["files"][0]["permission_basis"],
            "owned",
        )
        self.assertEqual(
            saved["clips"][0]["source_file_id"],
            saved["files"][0]["file_id"],
        )
        self.assertTrue(saved["clips"][0]["transcript_corrected"])
        self.assertEqual(saved["clips"][0]["duplicate_status"], "unique")
        self.assertEqual(saved["clips"][0]["contamination_status"], "clean")

    def test_readiness_calculation_does_not_claim_training_support(self) -> None:
        readiness = calculate_training_readiness(self.project)
        self.assertEqual(readiness["status"], "not_ready")
        self.make_ready()
        readiness = calculate_training_readiness(self.project)
        self.assertEqual(
            readiness["status"],
            "ready_for_feasibility_review",
        )
        self.assertIn("Phase 22", readiness["warnings"][0])

    def test_adapter_provenance_requires_ready_project(self) -> None:
        with self.assertRaisesRegex(
            VoiceTrainingActionError,
            "not ready",
        ):
            self.apply(
                "record_adapter_provenance",
                {
                    "provenance": {
                        "training_backend": "external-test-backend",
                        "base_model": "Qwen3-TTS-Base",
                        "dataset_fingerprint": "a" * 64,
                        "training_settings": {"epochs": 1},
                        "adapter_path": "adapters/doctor-v1",
                        "created_at_utc": self.TIME,
                        "validation_samples": [],
                        "comparison_results": {},
                        "user_approved": False,
                    }
                },
            )

    def test_adapter_assignment_requires_validation_and_explicit_approval(self) -> None:
        self.make_ready()
        dataset_fingerprint = self.project["dataset_project"][
            "dataset_fingerprint"
        ]
        self.apply(
            "record_adapter_provenance",
            {
                "provenance": {
                    "training_backend": "external-test-backend",
                    "base_model": "Qwen3-TTS-Base",
                    "dataset_fingerprint": dataset_fingerprint,
                    "training_settings": {"epochs": 1},
                    "adapter_path": "adapters/doctor-v1",
                    "created_at_utc": self.TIME,
                    "validation_samples": [],
                    "comparison_results": {},
                    "user_approved": False,
                }
            },
        )
        with self.assertRaisesRegex(
            VoiceTrainingActionError,
            "validated adapter",
        ):
            self.apply(
                "assign_adapter",
                {
                    "adapter_id": "doctor-v1",
                    "user_approved": True,
                },
            )
        self.apply(
            "record_adapter_validation",
            {
                "status": "validated",
                "notes": ["Identity and instruction adherence passed."],
            },
        )
        with self.assertRaisesRegex(
            VoiceTrainingActionError,
            "explicit user approval",
        ):
            self.apply(
                "assign_adapter",
                {
                    "adapter_id": "doctor-v1",
                    "user_approved": False,
                },
            )
        self.apply(
            "assign_adapter",
            {
                "adapter_id": "doctor-v1",
                "user_approved": True,
            },
        )
        self.assertEqual(
            self.project["adapter_assignment"]["status"],
            "assigned",
        )
        self.assertFalse((self.root / "voice_config.json").exists())

    def test_file_actions_are_atomic_fingerprint_gated_and_verified(self) -> None:
        created = create_voice_training_project_file(
            project=self.project,
            project_path=self.project_path,
        )
        self.assertEqual(read_voice_training_project(self.project_path), created)
        with self.assertRaisesRegex(
            VoiceTrainingConflictError,
            "already exists",
        ):
            create_voice_training_project_file(
                project=self.project,
                project_path=self.project_path,
            )
        updated = mutate_voice_training_project_file(
            project_path=self.project_path,
            expected_fingerprint=created["project_fingerprint"],
            action="approve_persona",
            payload={
                "description": "A precise, alert older traveler.",
                "ref_text": "Tell me what happened.",
            },
            expected_character_id=self.doctor["id"],
            expected_source_fingerprint=self.roster["source"]["fingerprint"],
            expected_roster_fingerprint=self.roster["roster_fingerprint"],
            at_utc=self.TIME,
        )
        self.assertEqual(
            updated["desired_base_persona"]["approval_status"],
            "approved",
        )
        with self.assertRaises(VoiceTrainingConflictError):
            mutate_voice_training_project_file(
                project_path=self.project_path,
                expected_fingerprint=created["project_fingerprint"],
                action="refresh_readiness",
                expected_character_id=self.doctor["id"],
                expected_source_fingerprint=self.roster["source"]["fingerprint"],
                expected_roster_fingerprint=self.roster["roster_fingerprint"],
                at_utc=self.TIME,
            )

    def test_unsupported_action_is_rejected_without_mutation(self) -> None:
        original = copy.deepcopy(self.project)
        with self.assertRaisesRegex(
            VoiceTrainingActionError,
            "Unsupported voice-training action",
        ):
            self.apply("train_adapter_now")
        self.assertEqual(self.project, original)


if __name__ == "__main__":
    unittest.main()
