from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from character_roster import (
    compute_draft_fingerprint,
    compute_roster_fingerprint,
    save_character_roster,
)
from generation_state import atomic_json_write
from project import group_into_chunks
from tests.excluded_speaker_recovery_audit_binding_cases import (
    ExcludedSpeakerRecoveryAuditBindingCases,
)
from tests.excluded_speaker_recovery_safety_cases import (
    ExcludedSpeakerRecoverySafetyCases,
)
from tests.test_speaker_management import SpeakerManagementFixture


class ExcludedSpeakerRecoveryTests(
    ExcludedSpeakerRecoveryAuditBindingCases,
    ExcludedSpeakerRecoverySafetyCases,
    unittest.TestCase,
    SpeakerManagementFixture,
):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source_path = self.root / "book.txt"
        self.source_path.write_text(self.SOURCE_TEXT, encoding="utf-8")
        (self.root / "state.json").write_text(
            json.dumps({"input_file_path": str(self.source_path)}),
            encoding="utf-8",
        )
        roster = self.approved_roster(self.source_path)
        self.excluded = {
            "name": "Vicar",
            "reason": "Excluded during imported roster review as incidental.",
            "evidence": [self.evidence("The TARDIS")],
        }
        roster["excluded_entities"] = [copy.deepcopy(self.excluded)]
        roster["approval_summary"]["excluded_count"] = 1
        roster["approved_draft_fingerprint"] = compute_draft_fingerprint(roster)
        roster["roster_fingerprint"] = compute_roster_fingerprint(roster)
        save_character_roster(
            roster,
            self.root / "character_roster.json",
            source_text=self.SOURCE_TEXT,
            expected_status="approved",
        )
        self.script = [
            {
                "speaker": "THE DOCTOR",
                "text": "Hello, Roz.",
                "instruct": "Warm greeting.",
            },
            {
                "speaker": "VICAR",
                "text": "You are welcome in this parish.",
                "instruct": "Formal but kind.",
            },
            {
                "speaker": "VICAR",
                "text": "The school is just beyond the green.",
                "instruct": "Matter-of-fact.",
            },
        ]
        atomic_json_write(self.script, self.root / "annotated_script.json")
        atomic_json_write(
            group_into_chunks(self.script),
            self.root / "chunks.json",
        )
        atomic_json_write(
            {"THE DOCTOR": {"type": "design", "description": "Doctor"}},
            self.root / "voice_config.json",
        )
        self.root_patch = patch.object(app_module, "ROOT_DIR", str(self.root))
        self.root_patch.start()
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.client.close()
        self.root_patch.stop()
        self.temporary.cleanup()

    def status(self) -> dict[str, Any]:
        response = self.client.get(
            "/api/speaker_management/status",
            params={"speaker": "VICAR"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def add(
        self,
        fingerprint: str,
        roster_fingerprint: str,
        display_name: str = "Vicar",
    ):
        return self.client.post(
            "/api/speaker_management/action",
            json={
                "operation": "add",
                "expected_script_fingerprint": fingerprint,
                "payload": {
                    "script_speaker": "VICAR",
                    "display_name": display_name,
                    "expected_roster_fingerprint": roster_fingerprint,
                    "require_exclusion_audit": True,
                },
            },
        )

    def read_roster(self) -> dict[str, Any]:
        return json.loads(
            (self.root / "character_roster.json").read_text(encoding="utf-8")
        )

    def test_status_exposes_excluded_speaking_recovery_evidence(self) -> None:
        recovery = self.status()["speaker_recovery"]

        self.assertEqual(recovery["script_speaker"], "VICAR")
        self.assertEqual(recovery["display_name"], "Vicar")
        self.assertEqual(recovery["line_count"], 2)
        self.assertEqual(
            [line["text"] for line in recovery["sample_lines"]],
            [self.script[1]["text"], self.script[2]["text"]],
        )
        self.assertTrue(recovery["eligible"])
        self.assertIsNone(recovery["active_character_id"])
        self.assertEqual(recovery["excluded_audit"], [self.excluded])

    def test_add_reintroduces_identity_without_assigning_voice_and_keeps_exclusion_audit(
        self,
    ) -> None:
        before_voice = (self.root / "voice_config.json").read_bytes()
        excluded_before = copy.deepcopy(self.read_roster()["excluded_entities"])

        reviewed = self.status()
        response = self.add(
            reviewed["script_fingerprint"],
            reviewed["roster_fingerprint"],
        )

        self.assertEqual(response.status_code, 200, response.text)
        roster = self.read_roster()
        recovered = next(
            entry for entry in roster["entries"]
            if entry["canonical_name"] == "VICAR"
        )
        self.assertEqual(recovered["sample_lines"], [
            self.script[1]["text"],
            self.script[2]["text"],
        ])
        self.assertEqual(roster["excluded_entities"], excluded_before)
        self.assertEqual(
            (self.root / "voice_config.json").read_bytes(),
            before_voice,
        )
        recovery = self.status()["speaker_recovery"]
        self.assertFalse(recovery["eligible"])
        self.assertEqual(recovery["active_character_id"], recovered["id"])

    def test_undo_removes_recovered_identity_while_exclusion_audit_remains(
        self,
    ) -> None:
        excluded_before = copy.deepcopy(self.read_roster()["excluded_entities"])
        reviewed = self.status()
        added = self.add(
            reviewed["script_fingerprint"],
            reviewed["roster_fingerprint"],
        )
        self.assertEqual(added.status_code, 200, added.text)
        operation_id = added.json()["operation"]["operation_id"]

        history = self.client.get(
            f"/api/speaker_management/history/{operation_id}"
        )
        undone = self.client.post(
            "/api/speaker_management/undo",
            json={"operation_id": operation_id},
        )

        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(history.json()["operation"], "add")
        self.assertEqual(undone.status_code, 200, undone.text)
        roster = self.read_roster()
        self.assertFalse(any(
            entry["canonical_name"] == "VICAR" for entry in roster["entries"]
        ))
        self.assertEqual(roster["excluded_entities"], excluded_before)
        status = self.status()
        recovery = status["speaker_recovery"]
        self.assertTrue(recovery["eligible"])
        self.assertIsNone(recovery["active_character_id"])
        self.assertEqual(
            {item["operation"] for item in status["history"]},
            {"add", "undo"},
        )
        add_record = next(
            item for item in status["history"] if item["operation"] == "add"
        )
        self.assertTrue(add_record["undone"])
        self.assertFalse(add_record["undoable"])

    def test_active_identity_disables_duplicate_recovery(self) -> None:
        reviewed = self.status()
        added = self.add(
            reviewed["script_fingerprint"],
            reviewed["roster_fingerprint"],
        )
        self.assertEqual(added.status_code, 200, added.text)

        recovery = self.status()["speaker_recovery"]
        duplicate_status = self.status()
        duplicate = self.add(
            duplicate_status["script_fingerprint"],
            duplicate_status["roster_fingerprint"],
        )

        self.assertFalse(recovery["eligible"])
        self.assertIsNotNone(recovery["active_character_id"])
        self.assertEqual(duplicate.status_code, 409, duplicate.text)

    def test_stale_fingerprint_rejects_recovery_without_writes(self) -> None:
        reviewed = self.status()
        roster_before = (self.root / "character_roster.json").read_bytes()
        voice_before = (self.root / "voice_config.json").read_bytes()
        atomic_json_write(
            [*self.script, {
                "speaker": "THE DOCTOR",
                "text": "The Script changed after review.",
                "instruct": "Plainly.",
            }],
            self.root / "annotated_script.json",
        )

        response = self.add(
            reviewed["script_fingerprint"],
            reviewed["roster_fingerprint"],
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "stale_speaker_management",
        )
        self.assertEqual(
            (self.root / "character_roster.json").read_bytes(),
            roster_before,
        )
        self.assertEqual(
            (self.root / "voice_config.json").read_bytes(),
            voice_before,
        )

if __name__ == "__main__":
    unittest.main()
