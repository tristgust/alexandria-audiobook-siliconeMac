from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from character_roster import save_character_roster
from generation_state import atomic_json_write, fingerprint_value
from project import group_into_chunks
from tests.test_speaker_management import SpeakerManagementFixture


class SpeakerManagementRouteTests(
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
        self.root_patch = patch.object(
            app_module,
            "ROOT_DIR",
            str(self.root),
        )
        self.root_patch.start()
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.client.close()
        self.root_patch.stop()
        self.temp.cleanup()

    def fingerprint(self) -> str:
        return fingerprint_value(
            json.loads(
                (self.root / "annotated_script.json").read_text(
                    encoding="utf-8"
                )
            )
        )

    def test_status_and_filtered_line_inspection(self) -> None:
        response = self.client.get("/api/speaker_management/status")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["available"])
        self.assertEqual(payload["speaker_counts"]["THE DOCTOR"], 1)
        response = self.client.get(
            "/api/speaker_management/status",
            params={"speaker": "ROZ"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["lines"]), 1)
        self.assertEqual(response.json()["lines"][0]["speaker"], "ROZ")

    def test_action_history_and_undo_routes(self) -> None:
        before = (self.root / "annotated_script.json").read_bytes()
        response = self.client.post(
            "/api/speaker_management/action",
            json={
                "operation": "rename",
                "expected_script_fingerprint": self.fingerprint(),
                "payload": {
                    "entry_id": self.doctor["id"],
                    "new_name": "THE TRAVELER",
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        operation_id = response.json()["operation"]["operation_id"]
        self.assertEqual(
            response.json()["status"]["speaker_counts"]["THE TRAVELER"],
            1,
        )
        history = self.client.get(
            f"/api/speaker_management/history/{operation_id}"
        )
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(history.json()["operation"], "rename")
        undo = self.client.post(
            "/api/speaker_management/undo",
            json={"operation_id": operation_id},
        )
        self.assertEqual(undo.status_code, 200, undo.text)
        self.assertEqual(
            (self.root / "annotated_script.json").read_bytes(),
            before,
        )

    def test_stale_action_returns_409_machine_readable_detail(self) -> None:
        response = self.client.post(
            "/api/speaker_management/action",
            json={
                "operation": "rename",
                "expected_script_fingerprint": "stale",
                "payload": {
                    "entry_id": self.doctor["id"],
                    "new_name": "THE TRAVELER",
                },
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "stale_speaker_management",
        )

    def test_invalid_operation_is_rejected_by_request_schema(self) -> None:
        response = self.client.post(
            "/api/speaker_management/action",
            json={
                "operation": "delete_everything",
                "expected_script_fingerprint": self.fingerprint(),
                "payload": {},
            },
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_missing_history_returns_404(self) -> None:
        response = self.client.get(
            "/api/speaker_management/history/speaker_missing"
        )
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "speaker_management_operation_not_found",
        )

    def test_routes_are_registered_once(self) -> None:
        paths = [route.path for route in app_module.app.routes]
        for path in (
            "/api/speaker_management/status",
            "/api/speaker_management/history/{operation_id}",
            "/api/speaker_management/action",
            "/api/speaker_management/undo",
        ):
            self.assertEqual(paths.count(path), 1)


if __name__ == "__main__":
    unittest.main()
