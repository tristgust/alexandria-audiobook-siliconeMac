from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from character_roster import (
    build_draft_roster,
    build_source_snapshot,
    compute_roster_fingerprint,
    stable_entry_id,
    validate_character_roster,
)
from voice_training_projects import (
    VoiceTrainingProjectCompatibilityError,
    VoiceTrainingProjectError,
    VoiceTrainingProjectValidationError,
    build_voice_training_project,
    build_voice_training_status,
    compute_dataset_fingerprint,
    compute_persona_fingerprint,
    compute_voice_training_project_fingerprint,
    inspect_voice_training_project,
    read_voice_training_project,
    save_voice_training_project,
    validate_voice_training_project,
    voice_training_project_path,
)


class VoiceTrainingProjectFixture:
    SOURCE_TEXT = (
        "The Doctor greeted Roz. Roz smiled. "
        "The TARDIS stood behind them."
    )
    TIME = "2026-07-16T20:00:00Z"

    @classmethod
    def evidence(cls, quote: str) -> dict:
        start = cls.SOURCE_TEXT.index(quote)
        return {
            "source_quote": quote,
            "source_location": "characters 0-72",
            "start_char": start,
            "end_char": start + len(quote),
            "passage_index": 0,
            "entry_index": None,
            "batch_index": 0,
            "category": "name",
            "confidence": 1.0,
            "basis": "explicit",
        }

    @classmethod
    def roster_entry(
        cls,
        name: str,
        quote: str,
        *,
        entity_kind: str = "character",
        speaking_status: str = "speaker",
        resolution_status: str = "resolved",
    ) -> dict:
        return {
            "id": stable_entry_id(
                f"voice-project-fixture:{cls.SOURCE_TEXT.index(quote)}:{name}"
            ),
            "canonical_name": name,
            "display_name": name,
            "entity_kind": entity_kind,
            "speaking_status": speaking_status,
            "titles": [],
            "aliases": [],
            "nicknames": [],
            "pronouns": [],
            "species": [],
            "relationships": [],
            "first_evidence_location": "characters 0-72",
            "additional_evidence_locations": [],
            "confidence": 0.95,
            "resolution_status": resolution_status,
            "possible_duplicate_ids": [],
            "mistaken_merge_risk": False,
            "unresolved_questions": [],
            "evidence": [cls.evidence(quote)],
            "voice_clues": [],
            "sample_lines": [],
        }

    @classmethod
    def approved_roster(cls, source_path: Path) -> dict:
        source, text = build_source_snapshot(source_path)
        assert text == cls.SOURCE_TEXT
        draft = build_draft_roster(
            source=source,
            discovery={
                "created_at_utc": "2026-07-16T18:00:00Z",
                "model_name": "qwen3.5:35b-mlx",
                "backend": "ollama-native",
                "generation_fingerprint": "voice-project-generation",
                "batch_count": 1,
                "completed_batches": 1,
            },
            entries=[
                cls.roster_entry("THE DOCTOR", "The Doctor"),
                cls.roster_entry("ROZ", "Roz"),
                cls.roster_entry(
                    "THE TARDIS",
                    "The TARDIS",
                    entity_kind="named_non_speaker",
                    speaking_status="non_speaker",
                ),
            ],
            source_text=cls.SOURCE_TEXT,
        )
        approved = {
            key: copy.deepcopy(value)
            for key, value in draft.items()
            if key not in {"status", "draft_fingerprint"}
        }
        approved.update(
            {
                "status": "approved",
                "approved_at_utc": "2026-07-16T19:00:00Z",
                "approved_draft_fingerprint": draft["draft_fingerprint"],
                "approval_summary": {
                    "resolved_count": 3,
                    "unresolved_count": 0,
                    "merged_count": 0,
                    "excluded_count": 0,
                    "acknowledged_unresolved": False,
                },
            }
        )
        approved["roster_fingerprint"] = compute_roster_fingerprint(approved)
        return validate_character_roster(
            approved,
            source_text=cls.SOURCE_TEXT,
            expected_status="approved",
        )

    @staticmethod
    def refingerprint(project: dict) -> dict:
        project["project_fingerprint"] = (
            compute_voice_training_project_fingerprint(project)
        )
        return project

    @classmethod
    def approve_persona(cls, project: dict) -> dict:
        persona = project["desired_base_persona"]
        persona.update(
            {
                "description": (
                    "An alert older traveler with a warm, weathered tenor, "
                    "precise diction, and elastic emotional timing."
                ),
                "ref_text": (
                    "There are worlds out there where the sky is burning."
                ),
                "approval_status": "approved",
                "approved_at_utc": cls.TIME,
            }
        )
        persona["approved_fingerprint"] = compute_persona_fingerprint(
            description=persona["description"],
            ref_text=persona["ref_text"],
        )
        return cls.refingerprint(project)

    @classmethod
    def synthetic_sample(cls, clip_id: str = "clip_sample_01") -> dict:
        return {
            "clip_id": clip_id,
            "text": "Tell me what happened here.",
            "instruction": "Low urgency, alert curiosity, measured pace.",
            "seed": 42,
            "audio_path": f"synthetic/clips/{clip_id}.wav",
            "audio_sha256": "a" * 64,
            "generation_backend": "mlx-audio",
            "model": "Qwen3-TTS-VoiceDesign",
            "generated_at_utc": cls.TIME,
            "review_status": "accepted",
            "review_notes": "Identity and pronunciation are stable.",
            "drift_flags": [],
        }

    @classmethod
    def add_synthetic_project(cls, project: dict) -> dict:
        sample = cls.synthetic_sample()
        project["designed_voice_project"] = {
            "status": "review",
            "root_description": project["desired_base_persona"]["description"],
            "global_seed": 42,
            "seed_supported": True,
            "sample_target": 24,
            "samples": [sample],
            "export": None,
        }
        return cls.refingerprint(project)

    @classmethod
    def approve_synthetic_dataset(cls, project: dict) -> dict:
        sample = project["designed_voice_project"]["samples"][0]
        clip = {"source_kind": "synthetic", **sample}
        dataset_fingerprint = compute_dataset_fingerprint(
            source_kind="synthetic",
            clips=[clip],
        )
        project["dataset_project"] = {
            "source_kind": "synthetic",
            "status": "approved",
            "clip_ids": [sample["clip_id"]],
            "metadata_path": "synthetic/metadata.jsonl",
            "zip_path": None,
            "dataset_fingerprint": dataset_fingerprint,
            "approved_at_utc": cls.TIME,
            "exported_at_utc": None,
        }
        project["training_readiness"]["dataset_fingerprint"] = dataset_fingerprint
        return cls.refingerprint(project)

    @classmethod
    def select_reference(cls, project: dict) -> dict:
        sample = project["designed_voice_project"]["samples"][0]
        project["selected_reference_sample"] = {
            "clip_id": sample["clip_id"],
            "source_kind": "synthetic",
            "audio_path": sample["audio_path"],
            "audio_sha256": sample["audio_sha256"],
            "selected_at_utc": cls.TIME,
        }
        return cls.refingerprint(project)

    @classmethod
    def configure_owned_recording_project(
        cls,
        project: dict,
        project_dir: Path,
        *,
        clip_id: str = "clip_recording_01",
        style_label: str = "neutral",
        transcript: str = "Tell me what happened here.",
    ) -> dict:
        source_path = project_dir / "recordings/source/doctor-session.wav"
        clip_path = project_dir / f"recordings/clips/{clip_id}.wav"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        clip_path.parent.mkdir(parents=True, exist_ok=True)
        source_bytes = b"owned source recording fixture"
        clip_bytes = (clip_id + " owned clip fixture").encode("utf-8")
        source_path.write_bytes(source_bytes)
        clip_path.write_bytes(clip_bytes)
        source_sha = hashlib.sha256(source_bytes).hexdigest()
        clip_sha = hashlib.sha256(clip_bytes).hexdigest()

        recordings = cls.existing_recording_project()
        recordings["status"] = "approved"
        recordings["files"][0].update(
            {
                "stored_path": source_path.relative_to(project_dir).as_posix(),
                "sha256": source_sha,
            }
        )
        clip = recordings["clips"][0]
        clip.update(
            {
                "clip_id": clip_id,
                "transcript": transcript,
                "style_label": style_label,
                "audio_path": clip_path.relative_to(project_dir).as_posix(),
                "audio_sha256": clip_sha,
            }
        )
        project["existing_recordings"] = recordings
        dataset_clip = {
            "source_kind": "existing_recordings",
            **copy.deepcopy(clip),
        }
        dataset_fingerprint = compute_dataset_fingerprint(
            source_kind="existing_recordings",
            clips=[dataset_clip],
        )
        project["dataset_project"] = {
            "source_kind": "existing_recordings",
            "status": "approved",
            "clip_ids": [clip_id],
            "metadata_path": None,
            "zip_path": None,
            "dataset_fingerprint": dataset_fingerprint,
            "approved_at_utc": cls.TIME,
            "exported_at_utc": None,
        }
        project["selected_reference_sample"] = {
            "clip_id": clip_id,
            "source_kind": "existing_recordings",
            "audio_path": clip["audio_path"],
            "audio_sha256": clip_sha,
            "selected_at_utc": cls.TIME,
        }
        project["training_readiness"]["dataset_fingerprint"] = (
            dataset_fingerprint
        )
        return cls.refingerprint(project)

    @classmethod
    def existing_recording_project(cls) -> dict:
        return {
            "status": "review",
            "same_speaker_declared": True,
            "speaker_declaration": "THE DOCTOR",
            "files": [
                {
                    "file_id": "file_recording_01",
                    "original_filename": "doctor-session.wav",
                    "stored_path": "recordings/source/doctor-session.wav",
                    "sha256": "b" * 64,
                    "permission_basis": "owned",
                    "imported_at_utc": cls.TIME,
                }
            ],
            "clips": [
                {
                    "clip_id": "clip_recording_01",
                    "source_file_id": "file_recording_01",
                    "start_seconds": 1.25,
                    "end_seconds": 6.75,
                    "speaker_declaration": "THE DOCTOR",
                    "transcript": "Tell me what happened here.",
                    "transcript_confidence": 0.91,
                    "transcript_corrected": True,
                    "audio_quality_score": 0.88,
                    "normalization": {
                        "sample_rate_hz": 24000,
                        "channels": 1,
                        "format": "wav",
                    },
                    "duplicate_status": "unique",
                    "contamination_status": "clean",
                    "inclusion_decision": "included",
                    "style_label": "measured curiosity",
                    "audio_path": "recordings/clips/clip_recording_01.wav",
                    "audio_sha256": "c" * 64,
                }
            ],
            "export": None,
        }


class VoiceTrainingProjectContractTests(
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
            item for item in self.roster["entries"]
            if item["canonical_name"] == "THE DOCTOR"
        )
        self.tardis = next(
            item for item in self.roster["entries"]
            if item["canonical_name"] == "THE TARDIS"
        )
        self.projects_root = self.root / "voice_training_projects"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def new_project(self) -> dict:
        return build_voice_training_project(
            approved_roster=self.roster,
            character_id=self.doctor["id"],
            priority="primary",
            desired_description="Draft persona description.",
            desired_ref_text="Draft reference text.",
            created_at_utc=self.TIME,
        )

    def test_status_read_is_file_pure_until_candidate_is_created(self) -> None:
        status = build_voice_training_status(
            approved_roster=self.roster,
            projects_root=self.projects_root,
        )
        self.assertFalse(self.projects_root.exists())
        self.assertTrue(status["available"])
        self.assertEqual(status["candidate_count"], 0)
        doctor = next(
            item for item in status["entries"]
            if item["character_id"] == self.doctor["id"]
        )
        tardis = next(
            item for item in status["entries"]
            if item["character_id"] == self.tardis["id"]
        )
        self.assertTrue(doctor["eligible"])
        self.assertEqual(doctor["status"], "absent")
        self.assertFalse(tardis["eligible"])
        self.assertEqual(tardis["status"], "ineligible")

    def test_build_creates_candidate_contract_without_side_effects(self) -> None:
        project = self.new_project()
        self.assertTrue(project["voice_training_candidate"])
        self.assertEqual(project["priority"], "primary")
        self.assertEqual(
            project["desired_base_persona"]["approval_status"],
            "draft",
        )
        self.assertIsNone(project["designed_voice_project"])
        self.assertIsNone(project["existing_recordings"])
        self.assertIsNone(project["dataset_project"])
        self.assertIsNone(project["selected_reference_sample"])
        self.assertIsNone(project["adapter_assignment"])
        self.assertIsNone(project["adapter_provenance"])
        self.assertEqual(
            project["training_readiness"]["status"],
            "not_ready",
        )
        self.assertFalse(self.projects_root.exists())

    def test_project_path_rejects_traversal_and_unknown_ids(self) -> None:
        with self.assertRaises(VoiceTrainingProjectValidationError):
            voice_training_project_path(self.projects_root, "../../doctor")
        expected = (
            self.projects_root / self.doctor["id"] / "project.json"
        )
        self.assertEqual(
            voice_training_project_path(
                self.projects_root,
                self.doctor["id"],
            ),
            expected,
        )

    def test_only_resolved_script_speakers_are_eligible(self) -> None:
        with self.assertRaisesRegex(
            VoiceTrainingProjectCompatibilityError,
            "speakers in the current script",
        ):
            build_voice_training_project(
                approved_roster=self.roster,
                character_id=self.tardis["id"],
                priority="secondary",
                created_at_utc=self.TIME,
            )

    def test_save_read_and_no_implicit_overwrite(self) -> None:
        project = self.new_project()
        path = voice_training_project_path(
            self.projects_root,
            self.doctor["id"],
        )
        saved = save_voice_training_project(project, path)
        self.assertEqual(read_voice_training_project(path), saved)
        with self.assertRaisesRegex(
            VoiceTrainingProjectError,
            "already exists",
        ):
            save_voice_training_project(project, path)

    def test_project_fingerprint_tampering_is_rejected(self) -> None:
        project = self.new_project()
        project["priority"] = "experimental"
        with self.assertRaisesRegex(
            VoiceTrainingProjectValidationError,
            "fingerprint",
        ):
            validate_voice_training_project(project)

    def test_approved_persona_requires_matching_approval_fingerprint(self) -> None:
        project = self.new_project()
        self.approve_persona(project)
        project["desired_base_persona"]["approved_fingerprint"] = "d" * 64
        self.refingerprint(project)
        with self.assertRaisesRegex(
            VoiceTrainingProjectValidationError,
            "persona metadata",
        ):
            validate_voice_training_project(project)

    def test_synthetic_path_requires_approved_root_persona(self) -> None:
        project = self.new_project()
        project["designed_voice_project"] = {
            "status": "draft",
            "root_description": "Draft persona description.",
            "global_seed": 42,
            "seed_supported": True,
            "sample_target": 24,
            "samples": [],
            "export": None,
        }
        self.refingerprint(project)
        with self.assertRaisesRegex(
            VoiceTrainingProjectValidationError,
            "approved base persona",
        ):
            validate_voice_training_project(project)

    def test_synthetic_contract_preserves_text_instruction_seed_and_review(self) -> None:
        project = self.new_project()
        self.approve_persona(project)
        self.add_synthetic_project(project)
        normalized = validate_voice_training_project(project)
        sample = normalized["designed_voice_project"]["samples"][0]
        self.assertEqual(sample["text"], "Tell me what happened here.")
        self.assertEqual(
            sample["instruction"],
            "Low urgency, alert curiosity, measured pace.",
        )
        self.assertEqual(sample["seed"], 42)
        self.assertEqual(sample["review_status"], "accepted")
        self.assertEqual(
            normalized["designed_voice_project"]["sample_target"],
            24,
        )

    def test_existing_recordings_require_same_speaker_and_owned_provenance(self) -> None:
        project = self.new_project()
        project["existing_recordings"] = self.existing_recording_project()
        self.refingerprint(project)
        normalized = validate_voice_training_project(project)
        clip = normalized["existing_recordings"]["clips"][0]
        self.assertTrue(clip["transcript_corrected"])
        self.assertEqual(clip["duplicate_status"], "unique")
        self.assertEqual(clip["contamination_status"], "clean")
        project["existing_recordings"]["same_speaker_declared"] = False
        self.refingerprint(project)
        with self.assertRaisesRegex(
            VoiceTrainingProjectValidationError,
            "same-speaker declaration",
        ):
            validate_voice_training_project(project)

    def test_existing_recording_dataset_cannot_include_unreviewed_clip(self) -> None:
        project = self.new_project()
        project["existing_recordings"] = self.existing_recording_project()
        clip = project["existing_recordings"]["clips"][0]
        clip["transcript_corrected"] = False
        self.refingerprint(project)
        with self.assertRaisesRegex(
            VoiceTrainingProjectValidationError,
            "reviewed transcript",
        ):
            validate_voice_training_project(project)

    def test_approved_dataset_requires_accepted_clips_and_exact_fingerprint(self) -> None:
        project = self.new_project()
        self.approve_persona(project)
        self.add_synthetic_project(project)
        self.approve_synthetic_dataset(project)
        normalized = validate_voice_training_project(project)
        self.assertEqual(
            normalized["dataset_project"]["status"],
            "approved",
        )
        project["designed_voice_project"]["samples"][0][
            "review_status"
        ] = "rejected"
        self.refingerprint(project)
        with self.assertRaisesRegex(
            VoiceTrainingProjectValidationError,
            "must be accepted",
        ):
            validate_voice_training_project(project)

    def test_reference_selection_is_explicit_and_bound_to_dataset_clip(self) -> None:
        project = self.new_project()
        self.approve_persona(project)
        self.add_synthetic_project(project)
        self.approve_synthetic_dataset(project)
        self.select_reference(project)
        normalized = validate_voice_training_project(project)
        self.assertEqual(
            normalized["selected_reference_sample"]["clip_id"],
            "clip_sample_01",
        )
        project["selected_reference_sample"]["audio_sha256"] = "e" * 64
        self.refingerprint(project)
        with self.assertRaisesRegex(
            VoiceTrainingProjectValidationError,
            "accepted clip artifact",
        ):
            validate_voice_training_project(project)

    def test_ready_status_means_feasibility_review_not_training_support(self) -> None:
        project = self.new_project()
        self.approve_persona(project)
        self.add_synthetic_project(project)
        self.approve_synthetic_dataset(project)
        self.select_reference(project)
        project["training_readiness"] = {
            "status": "ready_for_feasibility_review",
            "blockers": [],
            "warnings": [
                "Phase 22 must still measure Apple Silicon training feasibility."
            ],
            "dataset_fingerprint": project["dataset_project"][
                "dataset_fingerprint"
            ],
        }
        self.refingerprint(project)
        normalized = validate_voice_training_project(project)
        self.assertEqual(
            normalized["training_readiness"]["status"],
            "ready_for_feasibility_review",
        )
        self.assertIsNone(normalized["adapter_assignment"])
        self.assertIsNone(normalized["adapter_provenance"])

    def test_adapter_assignment_requires_provenance_and_explicit_approval(self) -> None:
        project = self.new_project()
        self.approve_persona(project)
        self.add_synthetic_project(project)
        self.approve_synthetic_dataset(project)
        dataset_fingerprint = project["dataset_project"]["dataset_fingerprint"]
        project["adapter_provenance"] = {
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
        project["adapter_assignment"] = {
            "status": "assigned",
            "adapter_id": "doctor-v1",
            "adapter_path": "adapters/doctor-v1",
            "assigned_at_utc": self.TIME,
            "user_approved": False,
        }
        self.refingerprint(project)
        with self.assertRaisesRegex(
            VoiceTrainingProjectValidationError,
            "explicitly user approved",
        ):
            validate_voice_training_project(project)
        project["adapter_assignment"]["user_approved"] = True
        self.refingerprint(project)
        normalized = validate_voice_training_project(project)
        self.assertEqual(
            normalized["adapter_assignment"]["status"],
            "assigned",
        )

    def test_inspection_reports_corrupt_invalid_and_roster_mismatch(self) -> None:
        path = voice_training_project_path(
            self.projects_root,
            self.doctor["id"],
        )
        path.parent.mkdir(parents=True)
        path.write_text("not json", encoding="utf-8")
        self.assertEqual(
            inspect_voice_training_project(path=path)["status"],
            "corrupt",
        )
        project = self.new_project()
        path.write_text(json.dumps(project), encoding="utf-8")
        inspection = inspect_voice_training_project(
            path=path,
            expected_character_id=self.doctor["id"],
            expected_source_fingerprint=self.roster["source"]["fingerprint"],
            expected_roster_fingerprint="f" * 64,
        )
        self.assertEqual(inspection["status"], "incompatible_roster")
        project["priority"] = "unknown"
        self.refingerprint(project)
        path.write_text(json.dumps(project), encoding="utf-8")
        self.assertEqual(
            inspect_voice_training_project(path=path)["status"],
            "invalid",
        )


if __name__ == "__main__":
    unittest.main()
