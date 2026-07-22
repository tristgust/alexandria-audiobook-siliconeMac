from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from character_roster import (
    compute_draft_fingerprint,
    compute_roster_fingerprint,
    save_character_roster,
)
from tests.test_voice_training_projects import VoiceTrainingProjectFixture
from voice_training_api import (
    VoiceTrainingApiError,
    apply_voice_training_action_payload,
    create_voice_training_candidate_payload,
    get_voice_training_project_payload,
    get_voice_training_status_payload,
)
from voice_training_projects import (
    compute_voice_training_project_fingerprint,
    voice_training_project_path,
)


class VoiceTrainingApiTests(
    unittest.TestCase,
    VoiceTrainingProjectFixture,
):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_path = self.root / "book.txt"
        self.source_path.write_text(self.SOURCE_TEXT, encoding="utf-8")
        self.roster = self.approved_roster(self.source_path)
        self.roster_path = self.root / "character_roster.json"
        save_character_roster(
            self.roster,
            self.roster_path,
            source_text=self.SOURCE_TEXT,
            expected_status="approved",
        )
        self.projects_root = self.root / "voice_training_projects"
        self.doctor = next(
            item
            for item in self.roster["entries"]
            if item["canonical_name"] == "THE DOCTOR"
        )
        self.tardis = next(
            item
            for item in self.roster["entries"]
            if item["canonical_name"] == "THE TARDIS"
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create_candidate(self) -> dict:
        return create_voice_training_candidate_payload(
            approved_roster_path=self.roster_path,
            projects_root=self.projects_root,
            character_id=self.doctor["id"],
            priority="primary",
            desired_description="Draft description.",
            desired_ref_text="Draft reference.",
            source_text=self.SOURCE_TEXT,
            created_at_utc=self.TIME,
        )

    def test_missing_roster_status_is_available_false_and_file_pure(self) -> None:
        missing_roster = self.root / "missing-roster.json"
        status = get_voice_training_status_payload(
            approved_roster_path=missing_roster,
            projects_root=self.projects_root,
        )
        self.assertFalse(status["available"])
        self.assertEqual(status["candidate_count"], 0)
        self.assertFalse(missing_roster.exists())
        self.assertFalse(self.projects_root.exists())

    def test_invalid_roster_blocks_voice_profiles_instead_of_using_script_ids(self) -> None:
        self.roster_path.write_text("{}", encoding="utf-8")
        (self.root / "annotated_script.json").write_text(
            json.dumps(
                [
                    {
                        "speaker": "THE DOCTOR",
                        "text": "Run.",
                        "instruct": "Urgent.",
                    }
                ]
            ),
            encoding="utf-8",
        )
        status = get_voice_training_status_payload(
            approved_roster_path=self.roster_path,
            projects_root=self.projects_root,
        )
        self.assertFalse(status["available"])
        self.assertEqual(status["identity_source"], "none")
        self.assertFalse(status["roster_enriched"])
        self.assertEqual(status["entries"], [])
        self.assertIn("approved roster is unavailable", status["context_error"])

    def test_status_maps_character_to_exact_script_voice_label(self) -> None:
        script_path = self.root / "annotated_script.json"
        script_path.write_text(
            json.dumps(
                [
                    {
                        "speaker": "THE DOCTOR",
                        "text": "The Doctor greeted Roz.",
                        "instruct": "Alert and precise.",
                    },
                    {
                        "speaker": "ROZ",
                        "text": "Roz smiled.",
                        "instruct": "Warmly.",
                    },
                ]
            ),
            encoding="utf-8",
        )
        status = get_voice_training_status_payload(
            approved_roster_path=self.roster_path,
            projects_root=self.projects_root,
            source_text=self.SOURCE_TEXT,
            script_path=script_path,
        )
        doctor = next(
            item
            for item in status["entries"]
            if item["character_id"] == self.doctor["id"]
        )
        self.assertEqual(doctor["script_voice_name"], "THE DOCTOR")
        self.assertEqual(doctor["script_voice_mapping"], "identity_name")
        self.assertEqual(doctor["script_line_count"], 1)

    def test_status_uses_sample_lines_when_roster_name_is_longer_than_script_label(self) -> None:
        roster = copy.deepcopy(self.roster)
        doctor = next(
            item
            for item in roster["entries"]
            if item["id"] == self.doctor["id"]
        )
        doctor["canonical_name"] = "Bernice Summerfield"
        doctor["display_name"] = "Bernice Summerfield"
        doctor["aliases"] = []
        doctor["nicknames"] = []
        doctor["titles"] = []
        doctor["sample_lines"] = ["The Doctor greeted Roz."]
        roster["approved_draft_fingerprint"] = compute_draft_fingerprint(roster)
        roster["roster_fingerprint"] = compute_roster_fingerprint(roster)
        save_character_roster(
            roster,
            self.roster_path,
            source_text=self.SOURCE_TEXT,
            expected_status="approved",
        )
        script_path = self.root / "annotated_script.json"
        script_path.write_text(
            json.dumps(
                [
                    {
                        "speaker": "BERNICE",
                        "text": "The Doctor greeted Roz.",
                        "instruct": "Dry and observant.",
                    }
                ]
            ),
            encoding="utf-8",
        )
        status = get_voice_training_status_payload(
            approved_roster_path=self.roster_path,
            projects_root=self.projects_root,
            source_text=self.SOURCE_TEXT,
            script_path=script_path,
        )
        bernice = next(
            item
            for item in status["entries"]
            if item["character_id"] == self.doctor["id"]
        )
        self.assertEqual(bernice["script_voice_name"], "BERNICE")
        self.assertEqual(bernice["script_voice_mapping"], "sample_lines")
        self.assertEqual(bernice["script_line_count"], 1)

    def test_candidate_creation_is_explicit_and_returns_project(self) -> None:
        before = get_voice_training_status_payload(
            approved_roster_path=self.roster_path,
            projects_root=self.projects_root,
            source_text=self.SOURCE_TEXT,
        )
        self.assertEqual(before["candidate_count"], 0)
        created = self.create_candidate()
        self.assertEqual(created["character"]["id"], self.doctor["id"])
        self.assertEqual(created["priority"], "primary")
        self.assertEqual(
            created["desired_base_persona"]["approval_status"],
            "draft",
        )
        path = voice_training_project_path(
            self.projects_root,
            self.doctor["id"],
        )
        self.assertTrue(path.exists())
        after = get_voice_training_status_payload(
            approved_roster_path=self.roster_path,
            projects_root=self.projects_root,
            source_text=self.SOURCE_TEXT,
        )
        self.assertEqual(after["candidate_count"], 1)

    def test_duplicate_candidate_creation_returns_conflict(self) -> None:
        self.create_candidate()
        with self.assertRaises(VoiceTrainingApiError) as caught:
            self.create_candidate()
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(
            caught.exception.code,
            "voice_training_project_exists",
        )

    def test_ineligible_non_speaker_is_rejected_before_file_creation(self) -> None:
        with self.assertRaises(VoiceTrainingApiError) as caught:
            create_voice_training_candidate_payload(
                approved_roster_path=self.roster_path,
                projects_root=self.projects_root,
                character_id=self.tardis["id"],
                priority="secondary",
                source_text=self.SOURCE_TEXT,
                created_at_utc=self.TIME,
            )
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.code, "character_ineligible")
        self.assertFalse(self.projects_root.exists())

    def test_unknown_character_returns_not_found(self) -> None:
        with self.assertRaises(VoiceTrainingApiError) as caught:
            create_voice_training_candidate_payload(
                approved_roster_path=self.roster_path,
                projects_root=self.projects_root,
                character_id="character_0123456789abcdef0123",
                priority="primary",
                source_text=self.SOURCE_TEXT,
                created_at_utc=self.TIME,
            )
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(caught.exception.code, "character_not_found")

    def test_project_read_returns_full_validated_project(self) -> None:
        created = self.create_candidate()
        loaded = get_voice_training_project_payload(
            approved_roster_path=self.roster_path,
            projects_root=self.projects_root,
            character_id=self.doctor["id"],
            source_text=self.SOURCE_TEXT,
        )
        self.assertEqual(loaded, created)

    def test_missing_project_read_and_action_return_not_found(self) -> None:
        with self.assertRaises(VoiceTrainingApiError) as read_caught:
            get_voice_training_project_payload(
                approved_roster_path=self.roster_path,
                projects_root=self.projects_root,
                character_id=self.doctor["id"],
                source_text=self.SOURCE_TEXT,
            )
        self.assertEqual(read_caught.exception.status_code, 404)
        self.assertEqual(
            read_caught.exception.code,
            "voice_training_project_not_found",
        )
        with self.assertRaises(VoiceTrainingApiError) as action_caught:
            apply_voice_training_action_payload(
                approved_roster_path=self.roster_path,
                projects_root=self.projects_root,
                character_id=self.doctor["id"],
                expected_fingerprint="0" * 64,
                action="refresh_readiness",
                source_text=self.SOURCE_TEXT,
                at_utc=self.TIME,
            )
        self.assertEqual(action_caught.exception.status_code, 404)

    def test_action_payload_applies_persona_approval(self) -> None:
        created = self.create_candidate()
        updated = apply_voice_training_action_payload(
            approved_roster_path=self.roster_path,
            projects_root=self.projects_root,
            character_id=self.doctor["id"],
            expected_fingerprint=created["project_fingerprint"],
            action="approve_persona",
            payload={
                "description": "A precise, alert older traveler.",
                "ref_text": "Tell me what happened.",
            },
            source_text=self.SOURCE_TEXT,
            at_utc=self.TIME,
        )
        self.assertEqual(
            updated["desired_base_persona"]["approval_status"],
            "approved",
        )
        self.assertNotEqual(
            updated["project_fingerprint"],
            created["project_fingerprint"],
        )

    def test_stale_action_returns_machine_readable_conflict(self) -> None:
        created = self.create_candidate()
        apply_voice_training_action_payload(
            approved_roster_path=self.roster_path,
            projects_root=self.projects_root,
            character_id=self.doctor["id"],
            expected_fingerprint=created["project_fingerprint"],
            action="update_persona",
            payload={
                "description": "Changed.",
                "ref_text": "Changed.",
            },
            source_text=self.SOURCE_TEXT,
            at_utc=self.TIME,
        )
        with self.assertRaises(VoiceTrainingApiError) as caught:
            apply_voice_training_action_payload(
                approved_roster_path=self.roster_path,
                projects_root=self.projects_root,
                character_id=self.doctor["id"],
                expected_fingerprint=created["project_fingerprint"],
                action="refresh_readiness",
                source_text=self.SOURCE_TEXT,
                at_utc=self.TIME,
            )
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(
            caught.exception.code,
            "stale_voice_training_project",
        )

    def test_rejected_action_returns_unprocessable_error(self) -> None:
        created = self.create_candidate()
        with self.assertRaises(VoiceTrainingApiError) as caught:
            apply_voice_training_action_payload(
                approved_roster_path=self.roster_path,
                projects_root=self.projects_root,
                character_id=self.doctor["id"],
                expected_fingerprint=created["project_fingerprint"],
                action="train_adapter_now",
                source_text=self.SOURCE_TEXT,
                at_utc=self.TIME,
            )
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(
            caught.exception.code,
            "voice_training_action_rejected",
        )

    def test_changed_roster_fingerprint_blocks_existing_project(self) -> None:
        self.create_candidate()
        path = voice_training_project_path(
            self.projects_root,
            self.doctor["id"],
        )
        stale_project = json.loads(path.read_text(encoding="utf-8"))
        stale_project["character"]["roster_fingerprint"] = "f" * 64
        stale_project["project_fingerprint"] = (
            compute_voice_training_project_fingerprint(stale_project)
        )
        path.write_text(json.dumps(stale_project), encoding="utf-8")
        with self.assertRaises(VoiceTrainingApiError) as caught:
            get_voice_training_project_payload(
                approved_roster_path=self.roster_path,
                projects_root=self.projects_root,
                character_id=self.doctor["id"],
                source_text=self.SOURCE_TEXT,
            )
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.code, "incompatible_roster")

    def test_corrupt_and_invalid_project_errors_are_distinct(self) -> None:
        path = voice_training_project_path(
            self.projects_root,
            self.doctor["id"],
        )
        path.parent.mkdir(parents=True)
        path.write_text("not json", encoding="utf-8")
        with self.assertRaises(VoiceTrainingApiError) as corrupt_caught:
            get_voice_training_project_payload(
                approved_roster_path=self.roster_path,
                projects_root=self.projects_root,
                character_id=self.doctor["id"],
                source_text=self.SOURCE_TEXT,
            )
        self.assertEqual(
            corrupt_caught.exception.code,
            "voice_training_project_corrupt",
        )
        project = self.build_voice_training_project_fixture()
        project["priority"] = "unsupported"
        project["project_fingerprint"] = (
            compute_voice_training_project_fingerprint(project)
        )
        path.write_text(json.dumps(project), encoding="utf-8")
        with self.assertRaises(VoiceTrainingApiError) as invalid_caught:
            get_voice_training_project_payload(
                approved_roster_path=self.roster_path,
                projects_root=self.projects_root,
                character_id=self.doctor["id"],
                source_text=self.SOURCE_TEXT,
            )
        self.assertEqual(
            invalid_caught.exception.code,
            "voice_training_project_invalid",
        )

    def build_voice_training_project_fixture(self) -> dict:
        from voice_training_projects import build_voice_training_project

        return build_voice_training_project(
            approved_roster=self.roster,
            character_id=self.doctor["id"],
            priority="primary",
            created_at_utc=self.TIME,
        )


if __name__ == "__main__":
    unittest.main()
