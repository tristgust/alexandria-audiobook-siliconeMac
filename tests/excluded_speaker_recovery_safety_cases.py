from __future__ import annotations

import copy
import threading
from pathlib import Path
from unittest.mock import patch

import character_roster_actions
import speaker_management
import speaker_management_api
from character_roster import (
    build_draft_roster,
    compute_draft_fingerprint,
    compute_roster_fingerprint,
    save_character_roster,
)


class ExcludedSpeakerRecoverySafetyCases:
    def recovery_snapshot(self) -> dict[str, bytes]:
        return {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def test_missing_exclusion_audit_blocks_recovery_without_writes(
        self,
    ) -> None:
        roster = self.read_roster()
        roster["excluded_entities"] = []
        roster["approval_summary"]["excluded_count"] = 0
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

        recovery = reviewed["speaker_recovery"]
        response = self.add(
            reviewed["script_fingerprint"],
            reviewed["roster_fingerprint"],
        )

        self.assertFalse(recovery["eligible"])
        self.assertEqual(recovery["state"], "blocked_no_audit")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "speaker_management_conflict",
        )
        self.assertEqual(self.recovery_snapshot(), before)

    def test_changed_exclusion_audit_rejects_reviewed_recovery_without_writes(
        self,
    ) -> None:
        reviewed = self.status()
        roster = self.read_roster()
        roster["excluded_entities"][0]["reason"] = (
            "Exclusion review changed after the recovery screen loaded."
        )
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
        before = self.recovery_snapshot()

        response = self.add(
            reviewed["script_fingerprint"],
            reviewed["roster_fingerprint"],
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "stale_speaker_management",
        )
        self.assertEqual(self.recovery_snapshot(), before)

    def test_history_marks_superseded_recovery_as_not_undoable(self) -> None:
        reviewed = self.status()
        added = self.add(
            reviewed["script_fingerprint"],
            reviewed["roster_fingerprint"],
        )
        self.assertEqual(added.status_code, 200, added.text)
        operation_id = added.json()["operation"]["operation_id"]
        doctor = next(
            entry for entry in self.read_roster()["entries"]
            if entry["canonical_name"] == "THE DOCTOR"
        )
        later = self.client.post(
            "/api/speaker_management/action",
            json={
                "operation": "add_alias",
                "expected_script_fingerprint": self.status()[
                    "script_fingerprint"
                ],
                "payload": {
                    "entry_id": doctor["id"],
                    "alias": "The Time Lord",
                },
            },
        )
        self.assertEqual(later.status_code, 200, later.text)

        add_record = next(
            item for item in self.status()["history"]
            if item["operation_id"] == operation_id
        )

        self.assertFalse(add_record["undoable"])
        self.assertIn("changed", add_record["undo_blocked_reason"])

    def test_concurrent_roster_replacement_wins_without_recovery_overwrite(
        self,
    ) -> None:
        reviewed = self.status()
        current = self.read_roster()
        replacement_audit = copy.deepcopy(current["excluded_entities"])
        replacement_audit[0]["reason"] = (
            "A newer roster review retained this exclusion."
        )
        draft = build_draft_roster(
            source=copy.deepcopy(current["source"]),
            discovery=copy.deepcopy(current["discovery"]),
            entries=copy.deepcopy(current["entries"]),
            unresolved=copy.deepcopy(current["unresolved"]),
            duplicate_candidates=copy.deepcopy(
                current["duplicate_candidates"]
            ),
            excluded_entities=replacement_audit,
            warnings=copy.deepcopy(current["warnings"]),
            source_text=self.SOURCE_TEXT,
        )
        draft_path = self.root / "character_roster.draft.json"
        save_character_roster(
            draft,
            draft_path,
            source_text=self.SOURCE_TEXT,
            expected_status="draft",
        )
        approved_path = self.root / "character_roster.json"
        writer_loaded = threading.Event()
        release_writer = threading.Event()
        recovery_attempted = threading.Event()
        writer_finished = threading.Event()
        writer_result = {}
        recovery_result = {}
        original_read = character_roster_actions.read_character_roster
        original_apply = speaker_management_api.apply_speaker_operation

        def paused_read(path, *args, **kwargs):
            value = original_read(path, *args, **kwargs)
            if (
                Path(path) == approved_path
                and kwargs.get("expected_status") == "approved"
                and not writer_loaded.is_set()
            ):
                writer_loaded.set()
                release_writer.wait(5)
            return value

        def observed_apply(*args, **kwargs):
            recovery_attempted.set()
            return original_apply(*args, **kwargs)

        def replace_roster() -> None:
            try:
                writer_result["value"] = (
                    character_roster_actions
                    .replace_approved_character_roster_file(
                        draft_path=draft_path,
                        approved_path=approved_path,
                        history_root=self.root / "character_roster_history",
                        source_text=self.SOURCE_TEXT,
                        source_fingerprint=current["source"]["fingerprint"],
                        expected_draft_fingerprint=draft[
                            "draft_fingerprint"
                        ],
                        expected_approved_fingerprint=current[
                            "roster_fingerprint"
                        ],
                        acknowledged_unresolved=True,
                        approved_at_utc="2026-07-28T10:30:00Z",
                    )
                )
            except (OSError, RuntimeError) as exc:
                writer_result["error"] = exc
            finally:
                writer_finished.set()

        def recover_speaker() -> None:
            recovery_result["response"] = self.add(
                reviewed["script_fingerprint"],
                reviewed["roster_fingerprint"],
            )

        writer = threading.Thread(target=replace_roster)
        recovery = threading.Thread(target=recover_speaker)
        with (
            patch.object(
                character_roster_actions,
                "read_character_roster",
                side_effect=paused_read,
            ),
            patch.object(
                speaker_management_api,
                "apply_speaker_operation",
                side_effect=observed_apply,
            ),
        ):
            try:
                self.assertIs(
                    speaker_management._MANAGEMENT_LOCK,
                    character_roster_actions._ROSTER_ACTION_LOCK,
                )
                writer.start()
                self.assertTrue(writer_loaded.wait(2))
                recovery.start()
                self.assertTrue(recovery_attempted.wait(2))
                self.assertTrue(recovery.is_alive())
                self.assertNotIn("response", recovery_result)
                release_writer.set()
                self.assertTrue(writer_finished.wait(5))
                after_replacement = self.recovery_snapshot()
                writer.join(5)
                recovery.join(5)
            finally:
                release_writer.set()
                writer.join(5) if writer.ident is not None else None
                recovery.join(5) if recovery.ident is not None else None

        self.assertNotIn("error", writer_result)
        response = recovery_result["response"]
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "stale_speaker_management",
        )
        self.assertEqual(self.recovery_snapshot(), after_replacement)
        self.assertEqual(
            self.read_roster()["excluded_entities"][0]["reason"],
            replacement_audit[0]["reason"],
        )
