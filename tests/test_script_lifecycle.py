from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from generation_state import fingerprint_text, fingerprint_value
from script_lifecycle import (
    ScriptLifecycleError,
    accept_current_script,
    inspect_script_lifecycle,
    load_script_lifecycle,
    mark_discovery_handoff,
    reject_current_script,
    rollback_script_version,
)


class ScriptLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.script_path = self.root / "annotated_script.json"
        self.metadata_path = self.root / "annotated_script.meta.json"
        self.lifecycle_path = self.root / "script_lifecycle.json"
        self.chunks_path = self.root / "chunks.json"
        self.validity_path = self.root / "audio_validity.json"
        self.source_text = "The room was quiet."
        self.source_fingerprint = fingerprint_text(self.source_text)
        self.entries = [
            {
                "speaker": "NARRATOR",
                "text": self.source_text,
                "instruct": "Quiet narration.",
            }
        ]
        self.metadata = self._metadata(self.entries)
        self._write_current(self.entries, self.metadata)
        self.generation_status = {
            "process": {"running": False, "logs": []},
            "checkpoint": {"status": "none", "resumable": False},
            "result": {
                "status": "complete",
                "entry_count": len(self.entries),
                "script_fingerprint": fingerprint_value(self.entries),
            },
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _metadata(
        self,
        entries,
        *,
        method: str = "local",
        source_fingerprint: str | None = None,
    ):
        if source_fingerprint is None and method == "local":
            source_fingerprint = self.source_fingerprint
        if method == "local":
            identity = {
                "mode": "native",
                "backend": "ollama",
                "model_name": "qwen3.5:9b",
            }
            import_value = None
        elif method == "chatgpt_task_bundle":
            identity = {
                "mode": "external_import",
                "backend": "external",
                "model_name": "Ordinary ChatGPT handoff",
                "origin_type": "chatgpt_handoff_result",
                "provenance_status": "verified",
            }
            import_value = {
                "operation_id": "script_import_12345678",
                "candidate_id": "candidate_12345678",
                "provenance": {"status": "verified"},
                "origin": {"type": "chatgpt_handoff_result"},
            }
        else:
            identity = {
                "mode": "external_import",
                "backend": "external",
                "model_name": "Imported annotated script",
                "origin_type": "direct_upload",
                "provenance_status": "unverified",
            }
            import_value = {
                "operation_id": "script_import_12345678",
                "candidate_id": "candidate_12345678",
                "provenance": {"status": "unverified"},
                "origin": {"type": "direct_upload"},
            }
        metadata = {
            "schema_version": 1,
            "generated_at_utc": "2026-07-20T12:00:00Z",
            "source": {
                "verification_status": "verified" if source_fingerprint else "unverified",
                "fingerprint": source_fingerprint,
                "character_count": len(self.source_text),
                "chunk_count": 1,
            },
            "generation": {
                "fingerprint": fingerprint_value(identity),
                "effective_identity": identity,
            },
            "result": {
                "script_fingerprint": fingerprint_value(entries),
                "entry_count": len(entries),
                "speaker_labels": sorted({entry["speaker"] for entry in entries}),
            },
            "resume": {"resumed": False, "previously_completed_chunks": 0},
        }
        if import_value:
            metadata["import"] = import_value
        return metadata

    def _write_current(self, entries, metadata):
        self.script_path.write_text(
            json.dumps(entries, indent=2) + "\n",
            encoding="utf-8",
        )
        self.metadata_path.write_text(
            json.dumps(metadata, indent=2) + "\n",
            encoding="utf-8",
        )

    def _status(self, **overrides):
        values = {
            "root_dir": self.root,
            "script_path": self.script_path,
            "metadata_path": self.metadata_path,
            "lifecycle_path": self.lifecycle_path,
            "generation_status": self.generation_status,
            "source_fingerprint": self.source_fingerprint,
            "source_available": True,
            "import_candidate_count": 0,
        }
        values.update(overrides)
        return inspect_script_lifecycle(**values)

    def _accept(self, **overrides):
        status = self._status()
        values = {
            "root_dir": self.root,
            "script_path": self.script_path,
            "metadata_path": self.metadata_path,
            "lifecycle_path": self.lifecycle_path,
            "source_text": self.source_text,
            "source_fingerprint": self.source_fingerprint,
            "expected_script_fingerprint": status["fingerprints"]["script"],
            "expected_metadata_fingerprint": status["fingerprints"]["metadata"],
            "expected_source_fingerprint": status["fingerprints"]["source"],
            "expected_state_fingerprint": status["state_fingerprint"],
            "at_utc": "2026-07-20T13:00:00Z",
        }
        values.update(overrides)
        return accept_current_script(**values)

    def test_no_script_has_one_generate_action_and_candidate_has_review_action(self) -> None:
        self.script_path.unlink()
        self.metadata_path.unlink()
        missing = self._status()
        self.assertEqual(missing["state"], "not_started")
        self.assertEqual(missing["primary_action"]["id"], "generate_script")

        candidate = self._status(import_candidate_count=1)
        self.assertEqual(candidate["state"], "review_required")
        self.assertEqual(
            candidate["primary_action"]["id"],
            "review_imported_script",
        )

    def test_running_and_resumable_states_override_review(self) -> None:
        running = self._status(
            generation_status={
                "process": {"running": True},
                "checkpoint": {"status": "none", "resumable": False},
                "result": {},
            }
        )
        self.assertEqual(running["state"], "running")
        self.assertIsNone(running["primary_action"])

        resumable = self._status(
            generation_status={
                "process": {"running": False},
                "checkpoint": {"status": "compatible", "resumable": True},
                "result": {},
            }
        )
        self.assertEqual(resumable["state"], "resumable")
        self.assertEqual(
            resumable["primary_action"]["id"],
            "resume_script_generation",
        )

    def test_local_script_requires_explicit_acceptance_then_has_version_receipt(self) -> None:
        before = self._status()
        self.assertEqual(before["state"], "review_required")
        self.assertFalse(before["accepted"])
        self.assertEqual(before["generation_method"], "local")

        accepted = self._accept()

        self.assertEqual(accepted["status"], "accepted")
        self.assertFalse(accepted["idempotent"])
        self.assertEqual(
            accepted["version"]["generation_method"],
            "local",
        )
        self.assertEqual(
            accepted["version"]["provenance_status"],
            "verified_at_acceptance",
        )
        self.assertEqual(
            accepted["discovery_handoff"]["status"],
            "pending",
        )
        version_id = accepted["version"]["version_id"]
        version_dir = self.root / "script_versions" / version_id
        self.assertEqual(
            (version_dir / "annotated_script.json").read_bytes(),
            self.script_path.read_bytes(),
        )
        self.assertEqual(
            (version_dir / "annotated_script.meta.json").read_bytes(),
            self.metadata_path.read_bytes(),
        )
        current = self._status()
        self.assertEqual(current["state"], "accepted")
        self.assertTrue(current["accepted"])
        self.assertEqual(current["accepted_version_id"], version_id)
        self.assertTrue(current["character_discovery_eligible"])
        self.assertEqual(current["primary_action"]["id"], "open_cast")

    def test_chatgpt_and_direct_import_converge_on_same_accepted_contract(self) -> None:
        for method, expected in (
            ("chatgpt_task_bundle", "chatgpt_task_bundle"),
            ("import_existing_script", "import_existing_script"),
        ):
            with self.subTest(method=method):
                self.lifecycle_path.unlink(missing_ok=True)
                metadata = self._metadata(
                    self.entries,
                    method=method,
                    source_fingerprint=(
                        self.source_fingerprint
                        if method == "chatgpt_task_bundle"
                        else None
                    ),
                )
                self._write_current(self.entries, metadata)
                accepted = self._accept(
                    origin={"candidate_id": "candidate_12345678"}
                )
                self.assertEqual(
                    accepted["version"]["generation_method"],
                    expected,
                )
                self.assertEqual(
                    accepted["version"]["provenance_status"],
                    "verified_at_acceptance",
                )
                self.assertEqual(self._status()["state"], "accepted")

    def test_source_fidelity_or_attribution_failure_cannot_create_acceptance(self) -> None:
        bad_entries = [
            {
                "speaker": "THE DOCTOR",
                "text": "Different words.",
                "instruct": "Neutral.",
            }
        ]
        self._write_current(bad_entries, self._metadata(bad_entries))
        status = self._status()
        with self.assertRaises(ScriptLifecycleError) as raised:
            self._accept(
                expected_script_fingerprint=status["fingerprints"]["script"],
                expected_metadata_fingerprint=status["fingerprints"]["metadata"],
                expected_state_fingerprint=status["state_fingerprint"],
            )
        self.assertEqual(raised.exception.code, "script_acceptance_blocked")
        self.assertGreater(raised.exception.context["blocking_count"], 0)
        self.assertFalse(self.lifecycle_path.exists())
        self.assertFalse((self.root / "script_versions").exists())

    def test_stale_source_script_metadata_and_state_conflicts_are_rejected(self) -> None:
        status = self._status()
        cases = (
            (
                "expected_source_fingerprint",
                "stale",
                "stale_script_source",
            ),
            (
                "expected_script_fingerprint",
                "stale",
                "stale_script_review",
            ),
            (
                "expected_metadata_fingerprint",
                "stale",
                "stale_script_metadata",
            ),
            (
                "expected_state_fingerprint",
                "stale",
                "stale_script_lifecycle",
            ),
        )
        for field, value, code in cases:
            with self.subTest(field=field):
                with self.assertRaises(ScriptLifecycleError) as raised:
                    self._accept(**{field: value})
                self.assertEqual(raised.exception.code, code)
        self.assertEqual(self._status()["state"], "review_required")
        self.assertEqual(status["state"], "review_required")

    def test_idempotent_acceptance_preserves_completed_handoff(self) -> None:
        accepted = self._accept()
        completed = mark_discovery_handoff(
            lifecycle_path=self.lifecycle_path,
            accepted_version_id=accepted["version"]["version_id"],
            status="complete",
            expected_state_fingerprint=accepted["state_fingerprint"],
            at_utc="2026-07-20T13:05:00Z",
        )
        again = self._accept(
            expected_state_fingerprint=completed["state_fingerprint"],
            at_utc="2026-07-20T13:10:00Z",
        )
        self.assertTrue(again["idempotent"])
        self.assertEqual(again["discovery_handoff"]["status"], "complete")

    def test_reject_removes_authority_without_deleting_script_or_versions(self) -> None:
        accepted = self._accept()
        script_before = self.script_path.read_bytes()
        rejected = reject_current_script(
            lifecycle_path=self.lifecycle_path,
            current_script_fingerprint=accepted["version"]["script_fingerprint"],
            reason="Attribution still needs human correction.",
            expected_state_fingerprint=accepted["state_fingerprint"],
            at_utc="2026-07-20T13:10:00Z",
        )
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(self.script_path.read_bytes(), script_before)
        state = load_script_lifecycle(self.lifecycle_path)
        self.assertIsNone(state["accepted_version_id"])
        self.assertEqual(state["review"]["status"], "rejected")
        self.assertEqual(len(state["versions"]), 1)
        status = self._status()
        self.assertFalse(status["accepted"])
        self.assertEqual(status["state"], "review_required")

    def test_discovery_handoff_is_bound_to_exact_accepted_version(self) -> None:
        accepted = self._accept()
        version_id = accepted["version"]["version_id"]
        running = mark_discovery_handoff(
            lifecycle_path=self.lifecycle_path,
            accepted_version_id=version_id,
            status="running",
            expected_state_fingerprint=accepted["state_fingerprint"],
            at_utc="2026-07-20T13:05:00Z",
        )
        self.assertEqual(running["discovery_handoff"]["attempt_count"], 1)
        failed = mark_discovery_handoff(
            lifecycle_path=self.lifecycle_path,
            accepted_version_id=version_id,
            status="failed",
            error="Discovery process failed to launch.",
            expected_state_fingerprint=running["state_fingerprint"],
            at_utc="2026-07-20T13:06:00Z",
        )
        self.assertEqual(failed["discovery_handoff"]["attempt_count"], 2)
        self.assertEqual(
            failed["discovery_handoff"]["last_error"],
            "Discovery process failed to launch.",
        )
        with self.assertRaises(ScriptLifecycleError) as stale:
            mark_discovery_handoff(
                lifecycle_path=self.lifecycle_path,
                accepted_version_id="script_version_00000000",
                status="running",
                expected_state_fingerprint=failed["state_fingerprint"],
            )
        self.assertEqual(stale.exception.code, "stale_script_discovery_handoff")

    def test_changed_script_or_source_makes_acceptance_stale(self) -> None:
        self._accept()
        changed = [
            {
                "speaker": "NARRATOR",
                "text": self.source_text,
                "instruct": "More restrained narration.",
            }
        ]
        self._write_current(changed, self._metadata(changed))
        status = self._status()
        self.assertEqual(status["state"], "stale")
        self.assertFalse(status["accepted"])
        self.assertIn(
            "script_acceptance_stale",
            {item["code"] for item in status["blockers"]},
        )

        source_changed = self._status(source_fingerprint="different-source")
        self.assertEqual(source_changed["state"], "stale")
        self.assertFalse(source_changed["accepted"])

    def test_tampered_version_snapshot_blocks_accepted_state(self) -> None:
        accepted = self._accept()
        version_dir = (
            self.root
            / "script_versions"
            / accepted["version"]["version_id"]
        )
        (version_dir / "annotated_script.json").write_text("[]", encoding="utf-8")
        status = self._status()
        self.assertFalse(status["accepted"])
        self.assertNotEqual(status["state"], "accepted")
        self.assertIn(
            "script_version_snapshot_invalid",
            {item["code"] for item in status["blockers"]},
        )
        with self.assertRaises(ScriptLifecycleError) as raised:
            self._accept(expected_state_fingerprint=status["state_fingerprint"])
        self.assertEqual(
            raised.exception.code,
            "script_version_snapshot_invalid",
        )

    def test_version_rollback_restores_exact_bytes_and_invalidates_audio(self) -> None:
        source_text = "Hello. Goodbye."
        self.source_text = source_text
        self.source_fingerprint = fingerprint_text(source_text)
        first_entries = [
            {
                "speaker": "NARRATOR",
                "text": source_text,
                "instruct": "Neutral narration.",
            }
        ]
        first_metadata = self._metadata(first_entries)
        self._write_current(first_entries, first_metadata)
        first_script_bytes = self.script_path.read_bytes()
        first_metadata_bytes = self.metadata_path.read_bytes()
        first = self._accept(at_utc="2026-07-20T13:00:00Z")

        second_entries = [
            {
                "speaker": "NARRATOR",
                "text": "Hello.",
                "instruct": "Warm narration.",
            },
            {
                "speaker": "NARRATOR",
                "text": "Goodbye.",
                "instruct": "Quiet narration.",
            },
        ]
        second_metadata = self._metadata(second_entries)
        self._write_current(second_entries, second_metadata)
        second_status = self._status()
        second = self._accept(
            expected_script_fingerprint=second_status["fingerprints"]["script"],
            expected_metadata_fingerprint=second_status["fingerprints"]["metadata"],
            expected_source_fingerprint=second_status["fingerprints"]["source"],
            expected_state_fingerprint=second_status["state_fingerprint"],
            at_utc="2026-07-20T14:00:00Z",
        )
        audio = self.root / "voicelines" / "line.wav"
        audio.parent.mkdir()
        audio.write_bytes(b"current-audio")
        self.chunks_path.write_text(
            json.dumps(
                [
                    {
                        "id": "chunk-current",
                        "speaker": "NARRATOR",
                        "text": source_text,
                        "instruct": "Current.",
                        "status": "done",
                        "audio_path": "voicelines/line.wav",
                        "audio_state": "current",
                    }
                ]
            ),
            encoding="utf-8",
        )

        result = rollback_script_version(
            root_dir=self.root,
            script_path=self.script_path,
            metadata_path=self.metadata_path,
            chunks_path=self.chunks_path,
            audio_validity_path=self.validity_path,
            lifecycle_path=self.lifecycle_path,
            version_id=first["version"]["version_id"],
            current_source_fingerprint=self.source_fingerprint,
            expected_current_script_fingerprint=second["version"][
                "script_fingerprint"
            ],
            expected_state_fingerprint=second["state_fingerprint"],
            at_utc="2026-07-20T15:00:00Z",
        )

        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual(result["invalidated_audio_count"], 1)
        self.assertEqual(result["audio_backup_count"], 1)
        self.assertEqual(self.script_path.read_bytes(), first_script_bytes)
        self.assertEqual(self.metadata_path.read_bytes(), first_metadata_bytes)
        self.assertFalse(audio.exists())
        chunks = json.loads(self.chunks_path.read_text(encoding="utf-8"))
        self.assertTrue(chunks)
        self.assertTrue(all(chunk["status"] == "pending" for chunk in chunks))
        self.assertTrue(all(chunk["audio_state"] == "missing" for chunk in chunks))
        validity = json.loads(self.validity_path.read_text(encoding="utf-8"))
        self.assertTrue(validity["stale"])
        self.assertEqual(
            validity["invalidated_chunks"][0]["reason"],
            "script_version_rollback",
        )
        state = load_script_lifecycle(self.lifecycle_path)
        self.assertEqual(
            state["accepted_version_id"],
            first["version"]["version_id"],
        )
        self.assertEqual(state["discovery_handoff"]["status"], "pending")
        self.assertTrue(
            (
                self.root
                / "script_lifecycle_history"
                / result["operation_id"]
                / "operation.json"
            ).is_file()
        )


if __name__ == "__main__":
    unittest.main()
