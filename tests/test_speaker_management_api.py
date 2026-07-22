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
from generation_state import atomic_json_write, fingerprint_value
from project import group_into_chunks
from speaker_management_api import (
    SpeakerManagementApiError,
    apply_speaker_operation_payload,
    get_speaker_management_status_payload,
    get_speaker_operation_payload,
    undo_speaker_operation_payload,
)
from tests.test_speaker_management import SpeakerManagementFixture


class SpeakerManagementApiTests(
    unittest.TestCase,
    SpeakerManagementFixture,
):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_path = self.root / "book.txt"
        self.source_path.write_text(self.SOURCE_TEXT, encoding="utf-8")
        (self.root / "state.json").write_text(
            json.dumps({"input_file_path": str(self.source_path)}),
            encoding="utf-8",
        )
        self.roster = self.approved_roster(self.source_path)
        save_character_roster(
            self.roster,
            self.root / "character_roster.json",
            source_text=self.SOURCE_TEXT,
            expected_status="approved",
        )
        self.doctor = next(
            entry for entry in self.roster["entries"]
            if entry["canonical_name"] == "THE DOCTOR"
        )
        self.roz = next(
            entry for entry in self.roster["entries"]
            if entry["canonical_name"] == "ROZ"
        )
        self.script = [
            {
                "speaker": "THE DOCTOR",
                "text": "Hello, Roz.",
                "instruct": "Warm greeting.",
            },
            {
                "speaker": "ROZ",
                "text": "Hello.",
                "instruct": "Dry reply.",
            },
        ]
        atomic_json_write(self.script, self.root / "annotated_script.json")
        chunks = group_into_chunks(self.script)
        for index, chunk in enumerate(chunks):
            chunk.update(
                {
                    "id": index,
                    "status": "done",
                    "audio_path": f"voicelines/{index}.wav",
                }
            )
        atomic_json_write(chunks, self.root / "chunks.json")
        atomic_json_write(
            {
                "THE DOCTOR": {"type": "design", "description": "Doctor"},
                "ROZ": {"type": "design", "description": "Roz"},
            },
            self.root / "voice_config.json",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def use_long_roster_name_with_short_script_label(self) -> None:
        roster = copy.deepcopy(self.roster)
        doctor = next(
            entry for entry in roster["entries"]
            if entry["id"] == self.doctor["id"]
        )
        doctor["canonical_name"] = "Bernice Summerfield"
        doctor["display_name"] = "Bernice Summerfield"
        doctor["aliases"] = []
        doctor["nicknames"] = []
        doctor["titles"] = []
        doctor["sample_lines"] = ["Hello, Roz."]
        roster["approved_draft_fingerprint"] = compute_draft_fingerprint(
            roster
        )
        roster["roster_fingerprint"] = compute_roster_fingerprint(roster)
        save_character_roster(
            roster,
            self.root / "character_roster.json",
            source_text=self.SOURCE_TEXT,
            expected_status="approved",
        )
        self.roster = roster
        self.doctor = doctor
        self.script[0]["speaker"] = "BERNICE"
        atomic_json_write(self.script, self.root / "annotated_script.json")
        atomic_json_write(
            {
                "BERNICE": {
                    "type": "clone",
                    "ref_audio": "clone_voices/benny.wav",
                    "ref_text": "Hello, Roz.",
                },
                "ROZ": {"type": "design", "description": "Roz"},
            },
            self.root / "voice_config.json",
        )
        chunks = group_into_chunks(self.script)
        for index, chunk in enumerate(chunks):
            chunk.update(
                {
                    "id": index,
                    "status": "done",
                    "audio_path": f"voicelines/{index}.wav",
                }
            )
        atomic_json_write(chunks, self.root / "chunks.json")

    def fingerprint(self) -> str:
        return fingerprint_value(
            json.loads(
                (self.root / "annotated_script.json").read_text(
                    encoding="utf-8"
                )
            )
        )

    def test_status_is_model_free_and_lists_roster_lines_history(self) -> None:
        status = get_speaker_management_status_payload(
            root_dir=self.root,
        )
        self.assertTrue(status["available"])
        self.assertEqual(status["entry_count"], 2)
        self.assertEqual(status["speaker_counts"]["THE DOCTOR"], 1)
        doctor = next(
            entry for entry in status["entries"]
            if entry["character_id"] == self.doctor["id"]
        )
        self.assertEqual(doctor["line_count"], 1)
        self.assertEqual(status["history"], [])

    def test_status_maps_long_roster_identity_to_short_script_voice(self) -> None:
        self.use_long_roster_name_with_short_script_label()
        status = get_speaker_management_status_payload(
            root_dir=self.root,
            speaker="Bernice Summerfield",
        )
        bernice = next(
            entry for entry in status["entries"]
            if entry["character_id"] == self.doctor["id"]
        )
        self.assertEqual(bernice["script_voice_name"], "BERNICE")
        self.assertEqual(bernice["script_voice_mapping"], "sample_lines")
        self.assertEqual(bernice["line_count"], 1)
        self.assertEqual(status["selected_script_voice"], "BERNICE")
        self.assertEqual(len(status["lines"]), 1)
        self.assertEqual(status["lines"][0]["speaker"], "BERNICE")

    def test_status_can_filter_line_inspection(self) -> None:
        status = get_speaker_management_status_payload(
            root_dir=self.root,
            speaker="ROZ",
        )
        self.assertEqual(len(status["lines"]), 1)
        self.assertEqual(status["lines"][0]["speaker"], "ROZ")
        self.assertEqual(status["speaker_counts"]["THE DOCTOR"], 1)

    def test_missing_roster_returns_unavailable_status(self) -> None:
        (self.root / "character_roster.json").unlink()
        status = get_speaker_management_status_payload(
            root_dir=self.root,
        )
        self.assertFalse(status["available"])
        self.assertIn("No approved", status["reason"])

    def test_action_returns_operation_and_refreshed_status(self) -> None:
        result = apply_speaker_operation_payload(
            root_dir=self.root,
            operation="rename",
            expected_script_fingerprint=self.fingerprint(),
            payload={
                "entry_id": self.doctor["id"],
                "new_name": "THE TRAVELER",
            },
            at_utc=self.TIME,
        )
        self.assertEqual(result["operation"]["operation"], "rename")
        self.assertEqual(
            result["status"]["speaker_counts"]["THE TRAVELER"],
            1,
        )
        self.assertTrue(result["status"]["history"])

    def test_stale_action_returns_machine_readable_conflict(self) -> None:
        with self.assertRaises(SpeakerManagementApiError) as caught:
            apply_speaker_operation_payload(
                root_dir=self.root,
                operation="rename",
                expected_script_fingerprint="stale",
                payload={
                    "entry_id": self.doctor["id"],
                    "new_name": "THE TRAVELER",
                },
                at_utc=self.TIME,
            )
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.code, "stale_speaker_management")
        self.assertEqual(
            caught.exception.as_detail()["code"],
            "stale_speaker_management",
        )

    def test_rejected_operation_returns_422(self) -> None:
        with self.assertRaises(SpeakerManagementApiError) as caught:
            apply_speaker_operation_payload(
                root_dir=self.root,
                operation="delete_everything",
                expected_script_fingerprint=self.fingerprint(),
                payload={},
                at_utc=self.TIME,
            )
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(caught.exception.code, "speaker_management_rejected")

    def test_operation_read_and_undo_payload(self) -> None:
        result = apply_speaker_operation_payload(
            root_dir=self.root,
            operation="add_alias",
            expected_script_fingerprint=self.fingerprint(),
            payload={
                "entry_id": self.doctor["id"],
                "alias": "Doctor",
            },
            at_utc=self.TIME,
        )
        operation_id = result["operation"]["operation_id"]
        record = get_speaker_operation_payload(
            root_dir=self.root,
            operation_id=operation_id,
        )
        self.assertEqual(record["operation"], "add_alias")
        undone = undo_speaker_operation_payload(
            root_dir=self.root,
            operation_id=operation_id,
            at_utc="2026-07-16T22:10:00Z",
        )
        self.assertEqual(undone["operation"]["operation"], "undo")
        roster = json.loads(
            (self.root / "character_roster.json").read_text(encoding="utf-8")
        )
        doctor = next(
            entry for entry in roster["entries"]
            if entry["id"] == self.doctor["id"]
        )
        self.assertNotIn("Doctor", doctor["aliases"])

    def test_missing_operation_returns_404(self) -> None:
        with self.assertRaises(SpeakerManagementApiError) as caught:
            get_speaker_operation_payload(
                root_dir=self.root,
                operation_id="speaker_missing",
            )
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(
            caught.exception.code,
            "speaker_management_operation_not_found",
        )


if __name__ == "__main__":
    unittest.main()
