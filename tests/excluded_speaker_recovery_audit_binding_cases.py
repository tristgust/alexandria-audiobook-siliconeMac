from __future__ import annotations

from character_roster import (
    compute_draft_fingerprint,
    compute_roster_fingerprint,
    save_character_roster,
)


class ExcludedSpeakerRecoveryAuditBindingCases:
    def _assert_non_text_add_field_rejected(
        self,
        field: str,
        value: dict[str, str] | list[str],
    ) -> None:
        reviewed = self.status()
        payload = {
            "script_speaker": "VICAR",
            "display_name": "Vicar",
            "expected_roster_fingerprint": reviewed["roster_fingerprint"],
            "require_exclusion_audit": True,
            field: value,
        }
        before = self.recovery_snapshot()

        response = self.client.post(
            "/api/speaker_management/action",
            json={
                "operation": "add",
                "expected_script_fingerprint": reviewed[
                    "script_fingerprint"
                ],
                "payload": payload,
            },
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "speaker_management_rejected",
        )
        self.assertEqual(self.recovery_snapshot(), before)

    def test_display_name_rejects_non_text_json(self) -> None:
        self._assert_non_text_add_field_rejected(
            "display_name", {"borrowed": "Vicar"}
        )

    def test_designed_voice_description_rejects_non_text_json(self) -> None:
        self._assert_non_text_add_field_rejected(
            "designed_voice_description", ["not", "text"]
        )

    def test_display_name_cannot_borrow_unrelated_exclusion_audit(
        self,
    ) -> None:
        roster = self.read_roster()
        roster["excluded_entities"][0]["name"] = "UNRELATED PERSON"
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
        reviewed = self.status()
        before = self.recovery_snapshot()

        response = self.add(
            reviewed["script_fingerprint"],
            reviewed["roster_fingerprint"],
            display_name="UNRELATED PERSON",
        )

        self.assertEqual(
            reviewed["speaker_recovery"]["state"],
            "blocked_no_audit",
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "speaker_management_conflict",
        )
        self.assertEqual(self.recovery_snapshot(), before)
