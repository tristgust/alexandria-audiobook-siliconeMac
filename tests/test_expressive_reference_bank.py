from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from generation_state import fingerprint_value
from expressive_reference_bank import (
    REQUIRED_STYLE_KEYS,
    ExpressiveReferenceBankConflictError,
    ExpressiveReferenceBankValidationError,
    add_reference,
    approve_reference_bank,
    assign_reference_bank_to_voice_config,
    clear_reference_bank_assignment,
    build_reference_bank,
    build_reference_bank_status,
    compute_bank_fingerprint,
    create_reference_bank_file,
    map_instruction_to_style,
    read_reference_bank,
    record_comparison_outputs,
    reference_bank_path,
    review_comparison,
    review_reference,
    save_reference_bank,
    select_reference_for_instruction,
    sha256_file,
    validate_reference_bank,
)
from tests.test_voice_training_projects import VoiceTrainingProjectFixture
from voice_training_projects import (
    build_voice_training_project,
    save_voice_training_project,
    voice_training_project_path,
)


class ExpressiveReferenceBankTests(
    unittest.TestCase,
    VoiceTrainingProjectFixture,
):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.projects = self.root / "voice_training_projects"
        self.source = self.root / "book.txt"
        self.source.write_text(self.SOURCE_TEXT, encoding="utf-8")
        self.roster = self.approved_roster(self.source)
        self.character_id = self.roster["entries"][0]["id"]
        self.project = build_voice_training_project(
            approved_roster=self.roster,
            character_id=self.character_id,
            priority="primary",
            desired_description="",
            desired_ref_text="",
            created_at_utc=self.TIME,
        )
        self.project = self.approve_persona(self.project)
        self.project_path = voice_training_project_path(
            self.projects,
            self.character_id,
        )
        self.project = self.configure_owned_recording_project(
            self.project,
            self.project_path.parent,
        )
        save_voice_training_project(
            self.project,
            self.project_path,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def identity_source(self) -> dict:
        recording = self.project["existing_recordings"]
        clip = recording["clips"][0]
        source_file = recording["files"][0]
        return {
            "kind": "owned_recording",
            "source_clip_id": clip["clip_id"],
            "source_file_id": source_file["file_id"],
            "exact_transcript": clip["transcript"],
            "audio_path": clip["audio_path"],
            "audio_sha256": clip["audio_sha256"],
            "permission_basis": source_file["permission_basis"],
            "selected_reference_fingerprint": fingerprint_value(
                self.project["selected_reference_sample"]
            ),
        }

    def new_bank(
        self,
        *,
        project: dict | None = None,
        seed: int | None = 42,
        required_style_keys: list[str] | None = None,
    ) -> dict:
        return build_reference_bank(
            voice_training_project=project or self.project,
            identity_source=self.identity_source(),
            identity_seed=seed,
            required_style_keys=required_style_keys,
            created_at_utc=self.TIME,
        )

    def audio_fixture(self, style_key: str) -> tuple[str, str]:
        directory = self.project_path.parent / "reference_bank_audio"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{style_key}.wav"
        path.write_bytes((style_key + " audio").encode("utf-8"))
        return (
            path.relative_to(self.project_path.parent).as_posix(),
            sha256_file(path),
        )

    def add_and_review(
        self,
        bank: dict,
        style_key: str,
    ) -> dict:
        audio_path, audio_sha = self.audio_fixture(style_key)
        updated = add_reference(
            bank,
            expected_fingerprint=bank["bank_fingerprint"],
            style_key=style_key,
            instruction=f"Approved {style_key} delivery.",
            reference_text=f"This is the {style_key} reference line.",
            seed=None,
            audio_path=audio_path,
            audio_sha256=audio_sha,
            generation_backend="mlx-audio-voxcpm2-controlled",
            model="mlx-community/VoxCPM2-4bit",
            source_kind="controlled_clone_experimental",
            source_clip_id=bank["identity_source"]["source_clip_id"],
            generated_at_utc=self.TIME,
        )
        reference = next(
            item
            for item in updated["references"]
            if item["style_key"] == style_key
        )
        return review_reference(
            updated,
            expected_fingerprint=updated["bank_fingerprint"],
            reference_id=reference["reference_id"],
            source_identity_retention_passed=True,
            identity_drift_passed=True,
            emotion_match_passed=True,
            pronunciation_passed=True,
            pace_passed=True,
            notes="Approved fixture.",
            reviewed_at_utc=self.TIME,
        )

    def completed_bank(self) -> dict:
        bank = self.new_bank(seed=314159)
        for style_key in REQUIRED_STYLE_KEYS:
            bank = self.add_and_review(bank, style_key)
        outputs = []
        for mode in (
            "reference_bank_clone",
            "single_reference_clone",
            "direct_voice_design",
        ):
            audio_path, audio_sha = self.audio_fixture(f"comparison_{mode}")
            outputs.append(
                {
                    "mode": mode,
                    "style_key": (
                        "urgency"
                        if mode == "reference_bank_clone"
                        else None
                    ),
                    "line_index": 0,
                    "audio_path": audio_path,
                    "audio_sha256": audio_sha,
                    "identity_role": (
                        "external_experimental_comparator"
                        if mode == "direct_voice_design"
                        else "owned_identity_candidate"
                    ),
                }
            )
        bank = record_comparison_outputs(
            bank,
            expected_fingerprint=bank["bank_fingerprint"],
            test_lines=["We have to leave now."],
            outputs=outputs,
        )
        bank = review_comparison(
            bank,
            expected_fingerprint=bank["bank_fingerprint"],
            source_identity_retention_passed=True,
            identity_consistency_passed=True,
            emotion_match_passed=True,
            pronunciation_passed=True,
            pace_passed=True,
            long_form_drift_passed=True,
            notes="All fixed comparison checks passed.",
            reviewed_at_utc=self.TIME,
        )
        return approve_reference_bank(
            bank,
            expected_fingerprint=bank["bank_fingerprint"],
        )

    def test_bank_requires_approved_persona(self) -> None:
        draft_project = build_voice_training_project(
            approved_roster=self.roster,
            character_id=self.character_id,
            priority="primary",
        )
        with self.assertRaisesRegex(
            ExpressiveReferenceBankValidationError,
            "approved desired persona",
        ):
            build_reference_bank(
                voice_training_project=draft_project,
                identity_source=self.identity_source(),
                identity_seed=42,
            )

    def test_create_is_explicit_and_does_not_create_audio(self) -> None:
        bank = create_reference_bank_file(
            projects_root=self.projects,
            character_id=self.character_id,
            identity_seed=314159,
            created_at_utc=self.TIME,
        )
        self.assertEqual(bank["status"], "draft")
        self.assertEqual(len(bank["references"]), 1)
        neutral = bank["references"][0]
        self.assertEqual(neutral["style_key"], "neutral")
        self.assertEqual(neutral["source_kind"], "owned_recording")
        self.assertEqual(
            neutral["source_clip_id"],
            self.project["selected_reference_sample"]["clip_id"],
        )
        self.assertFalse(
            (self.project_path.parent / "reference_bank_audio").exists()
        )
        with self.assertRaises(ExpressiveReferenceBankConflictError):
            create_reference_bank_file(
                projects_root=self.projects,
                character_id=self.character_id,
                identity_seed=314159,
            )

    def test_reference_replacement_resets_comparison(self) -> None:
        bank = self.new_bank(required_style_keys=["neutral"])
        bank = self.add_and_review(bank, "neutral")
        audio_path, audio_sha = self.audio_fixture("comparison_neutral")
        bank = record_comparison_outputs(
            bank,
            expected_fingerprint=bank["bank_fingerprint"],
            test_lines=["Hello."],
            outputs=[
                {
                    "mode": "reference_bank_clone",
                    "style_key": "neutral",
                    "line_index": 0,
                    "audio_path": audio_path,
                    "audio_sha256": audio_sha,
                    "identity_role": "owned_identity_candidate",
                }
            ],
        )
        replacement_path, replacement_sha = self.audio_fixture("neutral_replacement")
        replaced = add_reference(
            bank,
            expected_fingerprint=bank["bank_fingerprint"],
            style_key="neutral",
            instruction="Replacement neutral delivery.",
            reference_text="Replacement reference text.",
            seed=None,
            audio_path=replacement_path,
            audio_sha256=replacement_sha,
            generation_backend="mlx-audio-voxcpm2-controlled",
            model="fixture",
            source_kind="controlled_clone_experimental",
            source_clip_id=bank["identity_source"]["source_clip_id"],
        )
        self.assertEqual(replaced["comparison"]["status"], "not_started")
        self.assertFalse(replaced["references"][0]["review"]["approved"])

    def test_approval_requires_every_style_and_comparison(self) -> None:
        bank = self.new_bank(
            required_style_keys=["neutral", "anger"]
        )
        bank = self.add_and_review(bank, "neutral")
        with self.assertRaisesRegex(
            ExpressiveReferenceBankValidationError,
            "missing approved styles",
        ):
            approve_reference_bank(
                bank,
                expected_fingerprint=bank["bank_fingerprint"],
            )
        bank = self.add_and_review(bank, "anger")
        with self.assertRaisesRegex(
            ExpressiveReferenceBankValidationError,
            "approved comparison",
        ):
            approve_reference_bank(
                bank,
                expected_fingerprint=bank["bank_fingerprint"],
            )

    def test_complete_bank_validates_and_selects_style_reference(self) -> None:
        bank = self.completed_bank()
        path = reference_bank_path(self.projects, self.character_id)
        save_reference_bank(bank, path)
        selected = select_reference_for_instruction(
            bank_path=path,
            instruction="Urgent, desperate warning. [style: urgency]",
            project_root=self.root,
        )
        self.assertEqual(selected["style_key"], "urgency")
        self.assertEqual(selected["mapping_reason"], "explicit_override")
        self.assertTrue(Path(selected["ref_audio"]).is_file())

    def test_mapping_is_deterministic_with_neutral_fallback(self) -> None:
        self.assertEqual(
            map_instruction_to_style("Firm commanding authority.")["style_key"],
            "authority",
        )
        self.assertEqual(
            map_instruction_to_style("Ordinary narration.")["style_key"],
            "neutral",
        )
        ambiguous = map_instruction_to_style("angry but terrified")
        self.assertEqual(ambiguous["style_key"], "neutral")
        self.assertEqual(ambiguous["reason"], "ambiguous_fallback")

    def test_assignment_is_explicit_and_preserves_unknown_voice_fields(self) -> None:
        bank = self.completed_bank()
        path = reference_bank_path(self.projects, self.character_id)
        save_reference_bank(bank, path)
        voice_config_path = self.root / "voice_config.json"
        voice_config_path.write_text(
            '{"THE DOCTOR":{"type":"custom","voice":"Ryan","unknown":"keep"}}',
            encoding="utf-8",
        )
        result = assign_reference_bank_to_voice_config(
            bank_path=path,
            voice_config_path=voice_config_path,
            project_root=self.root,
            expected_fingerprint=bank["bank_fingerprint"],
            voice_name="THE DOCTOR",
            assigned_at_utc=self.TIME,
        )
        voice = result["voice_config"]["THE DOCTOR"]
        self.assertEqual(voice["type"], "clone")
        self.assertEqual(voice["unknown"], "keep")
        self.assertEqual(voice["reference_bank_character_id"], self.character_id)
        self.assertTrue(voice["reference_bank_path"].endswith("reference_bank.json"))
        self.assertEqual(result["bank"]["production_assignment"]["status"], "assigned")

        cleared = clear_reference_bank_assignment(
            bank_path=path,
            voice_config_path=voice_config_path,
            project_root=self.root,
            expected_fingerprint=result["bank"]["bank_fingerprint"],
        )
        self.assertEqual(cleared["bank"]["production_assignment"]["status"], "unassigned")
        self.assertNotIn(
            "reference_bank_path",
            cleared["voice_config"]["THE DOCTOR"],
        )
        self.assertEqual(cleared["voice_config"]["THE DOCTOR"]["unknown"], "keep")

    def test_assignment_rejects_draft_bank(self) -> None:
        bank = self.new_bank(required_style_keys=["neutral"])
        path = reference_bank_path(self.projects, self.character_id)
        save_reference_bank(bank, path)
        with self.assertRaisesRegex(
            ExpressiveReferenceBankConflictError,
            "approved and compared",
        ):
            assign_reference_bank_to_voice_config(
                bank_path=path,
                voice_config_path=self.root / "voice_config.json",
                project_root=self.root,
                expected_fingerprint=bank["bank_fingerprint"],
            )

    def test_audio_hash_mismatch_blocks_runtime_selection(self) -> None:
        bank = self.completed_bank()
        path = reference_bank_path(self.projects, self.character_id)
        save_reference_bank(bank, path)
        neutral = next(
            item for item in bank["references"] if item["style_key"] == "neutral"
        )
        audio = path.parent / neutral["audio_path"]
        audio.write_bytes(b"tampered")
        with self.assertRaisesRegex(
            ExpressiveReferenceBankValidationError,
            "fingerprint",
        ):
            select_reference_for_instruction(
                bank_path=path,
                instruction="Neutral delivery.",
                project_root=self.root,
            )

    def test_stale_fingerprint_is_rejected(self) -> None:
        bank = self.new_bank()
        audio_path, audio_sha = self.audio_fixture("neutral")
        with self.assertRaisesRegex(
            ExpressiveReferenceBankConflictError,
            "changed",
        ):
            add_reference(
                bank,
                expected_fingerprint="0" * 64,
                style_key="neutral",
                instruction="Neutral.",
                reference_text="Neutral reference.",
                seed=None,
                audio_path=audio_path,
                audio_sha256=audio_sha,
                generation_backend="mlx-audio-voxcpm2-controlled",
                model="fixture",
                source_kind="controlled_clone_experimental",
                source_clip_id=bank["identity_source"]["source_clip_id"],
            )

    def test_tampering_is_rejected(self) -> None:
        bank = self.new_bank()
        tampered = copy.deepcopy(bank)
        tampered["identity_seed"] = 99
        with self.assertRaisesRegex(
            ExpressiveReferenceBankValidationError,
            "fingerprint",
        ):
            validate_reference_bank(tampered)

    def test_status_is_file_pure_and_lists_absent_bank(self) -> None:
        before = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))
        status = build_reference_bank_status(
            approved_roster=self.roster,
            projects_root=self.projects,
        )
        after = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))
        self.assertEqual(before, after)
        doctor = next(
            item for item in status["entries"] if item["character_id"] == self.character_id
        )
        self.assertEqual(doctor["status"], "absent")
        self.assertEqual(doctor["required_style_count"], len(REQUIRED_STYLE_KEYS))


if __name__ == "__main__":
    unittest.main()
