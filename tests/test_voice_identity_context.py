from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from expressive_reference_bank_api import get_reference_bank_status_payload
from generation_state import fingerprint_text
from voice_identity_context import load_voice_identity_context
from voice_training_api import (
    VoiceTrainingApiError,
    create_voice_training_candidate_payload,
    get_voice_training_status_payload,
)


class VoiceIdentityContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source_text = 'The hall was dark. "Run," said the Doctor.'
        self.source_path = self.root / "book.txt"
        self.source_path.write_text(self.source_text, encoding="utf-8")
        (self.root / "state.json").write_text(
            json.dumps({"input_file_path": str(self.source_path)}),
            encoding="utf-8",
        )
        self.script = [
            {
                "speaker": "NARRATOR",
                "text": "The hall was dark.",
                "instruct": "Low, measured narration.",
            },
            {
                "speaker": "THE DOCTOR",
                "text": "Run,",
                "instruct": "Urgent and clipped.",
            },
            {
                "speaker": "NARRATOR",
                "text": "said the Doctor.",
                "instruct": "Neutral narration.",
            },
        ]
        (self.root / "annotated_script.json").write_text(
            json.dumps(self.script),
            encoding="utf-8",
        )
        self.source_fingerprint = fingerprint_text(self.source_text)
        self.roster_path = self.root / "character_roster.json"
        self.projects_root = self.root / "voice_training_projects"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def status(self) -> dict:
        return get_voice_training_status_payload(
            approved_roster_path=self.roster_path,
            projects_root=self.projects_root,
            source_text=self.source_text,
            current_source_fingerprint=self.source_fingerprint,
        )

    def approve_script_roster(self) -> dict:
        roster, _ = load_voice_identity_context(
            approved_roster_path=self.roster_path,
            source_text=self.source_text,
            current_source_fingerprint=self.source_fingerprint,
        )
        assert roster is not None
        self.roster_path.write_text(json.dumps(roster), encoding="utf-8")
        return roster

    def test_voice_profiles_require_approved_roster(self) -> None:
        status = self.status()
        self.assertFalse(status["available"])
        self.assertEqual(status["identity_source"], "none")
        self.assertFalse(status["roster_enriched"])
        self.assertEqual(status["entries"], [])
        self.assertIn("Approve the Character roster", status["reason"])

    def test_script_speaker_project_creation_is_roster_gated(self) -> None:
        fallback, _ = load_voice_identity_context(
            approved_roster_path=self.roster_path,
            source_text=self.source_text,
            current_source_fingerprint=self.source_fingerprint,
        )
        assert fallback is not None
        doctor = next(
            entry
            for entry in fallback["entries"]
            if entry["canonical_name"] == "THE DOCTOR"
        )
        with self.assertRaises(VoiceTrainingApiError) as caught:
            create_voice_training_candidate_payload(
                approved_roster_path=self.roster_path,
                projects_root=self.projects_root,
                character_id=doctor["id"],
                priority="primary",
                desired_description="A quick, incisive older voice.",
                desired_ref_text="Run,",
                source_text=self.source_text,
                current_source_fingerprint=self.source_fingerprint,
                created_at_utc="2026-07-17T20:00:00Z",
            )
        self.assertEqual(caught.exception.code, "approved_roster_required")
        self.assertFalse(self.projects_root.exists())

    def test_narrator_is_a_valid_expressive_voice_target(self) -> None:
        self.approve_script_roster()
        status = self.status()
        narrator = next(
            entry
            for entry in status["entries"]
            if entry["canonical_name"] == "NARRATOR"
        )
        project = create_voice_training_candidate_payload(
            approved_roster_path=self.roster_path,
            projects_root=self.projects_root,
            character_id=narrator["character_id"],
            priority="primary",
            source_text=self.source_text,
            current_source_fingerprint=self.source_fingerprint,
            created_at_utc="2026-07-17T20:00:00Z",
        )
        self.assertEqual(project["character"]["entity_kind"], "narrator_role")
        self.assertEqual(project["character"]["speaking_status"], "narrator")

    def test_valid_approved_roster_enriches_the_same_workflow(self) -> None:
        self.approve_script_roster()
        status = self.status()
        self.assertEqual(status["identity_source"], "approved_roster")
        self.assertTrue(status["roster_enriched"])

    def test_invalid_roster_blocks_voice_profiles_with_warning(self) -> None:
        self.roster_path.write_text("{}", encoding="utf-8")
        status = self.status()
        self.assertFalse(status["available"])
        self.assertEqual(status["identity_source"], "none")
        self.assertEqual(status["entries"], [])
        self.assertIn("approved roster is unavailable", status["context_error"])

    def test_reference_bank_status_uses_script_speakers_without_roster(self) -> None:
        status = get_reference_bank_status_payload(
            approved_roster_path=self.roster_path,
            projects_root=self.projects_root,
            source_text=self.source_text,
            current_source_fingerprint=self.source_fingerprint,
        )
        self.assertTrue(status["available"])
        self.assertEqual(status["identity_source"], "script")
        self.assertEqual(
            {entry["canonical_name"] for entry in status["entries"]},
            {"NARRATOR", "THE DOCTOR"},
        )

    def test_missing_script_and_roster_remain_unavailable(self) -> None:
        (self.root / "annotated_script.json").unlink()
        status = self.status()
        self.assertFalse(status["available"])
        self.assertEqual(status["identity_source"], "none")
        self.assertEqual(status["entries"], [])

    def test_script_catalog_is_deterministic_and_file_pure(self) -> None:
        before = sorted(path.relative_to(self.root) for path in self.root.rglob("*"))
        first, first_context = load_voice_identity_context(
            approved_roster_path=self.roster_path,
            source_text=self.source_text,
            current_source_fingerprint=self.source_fingerprint,
        )
        second, second_context = load_voice_identity_context(
            approved_roster_path=self.roster_path,
            source_text=self.source_text,
            current_source_fingerprint=self.source_fingerprint,
        )
        after = sorted(path.relative_to(self.root) for path in self.root.rglob("*"))
        self.assertEqual(first, second)
        self.assertEqual(first_context, second_context)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
