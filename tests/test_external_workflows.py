from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import external_workflows
from annotated_script_import import create_annotated_script_bundle
from external_workflows import (
    ExternalWorkflowConflictError,
    ExternalWorkflowValidationError,
    apply_annotated_script_candidate,
    create_stored_handoff,
    get_annotated_script_candidate,
    get_handoff_bundle_path,
    get_handoff_prompt,
    inspect_annotated_script_upload,
    inspect_stored_handoff_result,
    open_handoff_folder,
    rollback_annotated_script_import,
)
from generation_state import fingerprint_text, fingerprint_value
from llm_schemas import get_schema


class ExternalWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = 'The room was quiet. "Run," said the Doctor.'
        self.entries = [
            {
                "speaker": "NARRATOR",
                "text": "The room was quiet.",
                "instruct": "Neutral narration.",
            },
            {
                "speaker": "DOCTOR",
                "text": "Run,",
                "instruct": "Urgent command.",
            },
            {
                "speaker": "NARRATOR",
                "text": "said the Doctor.",
                "instruct": "Neutral narration.",
            },
        ]
        self.old_entries = [
            {
                "speaker": "NARRATOR",
                "text": "Old script.",
                "instruct": "Neutral.",
            }
        ]
        self.source_context = {
            "basename": "book.txt",
            "fingerprint": fingerprint_text(self.source),
            "character_count": len(self.source),
            "chunk_count": 1,
        }
        self.write_json("annotated_script.json", self.old_entries)
        self.write_json(
            "chunks.json",
            [
                {
                    "id": 0,
                    "speaker": "NARRATOR",
                    "text": "Old script.",
                    "instruct": "Neutral.",
                    "status": "done",
                    "audio_path": "voicelines/chunk_000000.wav",
                }
            ],
        )
        self.write_json(
            "voice_config.json",
            {"NARRATOR": {"type": "custom", "voice": "Ryan"}},
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_json(self, name: str, value) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def read_json(self, name: str):
        return json.loads((self.root / name).read_text(encoding="utf-8"))

    def write_audio(self, name: str, payload: bytes = b"old-audio") -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def inspect_direct(
        self,
        *,
        checkpoint_status: str = "none",
        source_text: str | None = None,
        created_at_utc: str = "2026-07-17T21:00:00Z",
    ) -> dict:
        path = self.write_json("incoming.json", self.entries)
        return inspect_annotated_script_upload(
            root_dir=self.root,
            import_path=path,
            source_text=source_text,
            source_context=(
                self.source_context
                if source_text is not None
                else None
            ),
            current_script_fingerprint=fingerprint_value(self.old_entries),
            checkpoint_status=checkpoint_status,
            generated_audio_count=1,
            created_at_utc=created_at_utc,
        )

    def test_stored_handoff_round_trip_and_script_result_becomes_candidate(self) -> None:
        handoff = create_stored_handoff(
            root_dir=self.root,
            task_type="script_generation",
            stage_prompt="Convert the source into Alexandria script entries.",
            input_payload={"source_text": self.source},
            output_schema=get_schema("script"),
            application_version="alexandria-test",
            source_fingerprint=fingerprint_text(self.source),
            created_at_utc="2026-07-17T21:00:00Z",
        )
        bundle_path, record = get_handoff_bundle_path(
            root_dir=self.root,
            handoff_id=handoff["handoff_id"],
        )
        self.assertTrue(bundle_path.is_file())
        self.assertEqual(record["task_type"], "script_generation")
        self.assertEqual(bundle_path.name, "handoff.zip")
        prompt = get_handoff_prompt(
            root_dir=self.root,
            handoff_id=handoff["handoff_id"],
        )
        self.assertEqual(prompt["task_type"], "script_generation")
        self.assertIn("Return only valid JSON", prompt["prompt"])

        result_path = self.write_json("result.json", self.entries)
        candidate = inspect_stored_handoff_result(
            root_dir=self.root,
            handoff_id=handoff["handoff_id"],
            result_path=result_path,
            current_source_fingerprint=fingerprint_text(self.source),
            current_artifact_fingerprints={},
            source_text=self.source,
            source_context=self.source_context,
            current_script_fingerprint=fingerprint_value(self.old_entries),
            checkpoint_status="none",
            generated_audio_count=1,
            created_at_utc="2026-07-17T21:05:00Z",
        )
        self.assertEqual(candidate["kind"], "annotated_script")
        self.assertEqual(candidate["summary"]["entry_count"], 3)
        self.assertEqual(candidate["provenance"]["status"], "verified")
        self.assertEqual(candidate["comparison"]["current"]["entry_count"], 1)
        self.assertEqual(candidate["comparison"]["imported"]["entry_count"], 3)
        self.assertEqual(candidate["comparison"]["deltas"]["entry_count"], 2)
        self.assertEqual(
            candidate["origin"]["handoff_id"],
            handoff["handoff_id"],
        )
        loaded = get_annotated_script_candidate(
            root_dir=self.root,
            candidate_id=candidate["candidate_id"],
        )
        self.assertEqual(loaded, candidate)
        self.assertNotIn("entries", loaded)

    def test_open_handoff_folder_is_confined_and_macos_only(self) -> None:
        handoff = create_stored_handoff(
            root_dir=self.root,
            task_type="script_generation",
            stage_prompt="Complete it.",
            input_payload={"source_text": self.source},
            output_schema=get_schema("script"),
            application_version="test",
            created_at_utc="2026-07-17T21:00:00Z",
        )
        with patch.object(external_workflows.sys, "platform", "darwin"):
            with patch.object(external_workflows.subprocess, "run") as run:
                run.return_value.returncode = 0
                run.return_value.stderr = ""
                result = open_handoff_folder(
                    root_dir=self.root,
                    handoff_id=handoff["handoff_id"],
                )
        self.assertTrue(result["opened"])
        command = run.call_args.args[0]
        self.assertEqual(command[0], "open")
        self.assertEqual(
            Path(command[1]),
            self.root
            / "external_workflows"
            / "handoffs"
            / handoff["handoff_id"],
        )
        self.assertNotIn("shell", run.call_args.kwargs)

        with patch.object(external_workflows.sys, "platform", "linux"):
            with self.assertRaisesRegex(
                ExternalWorkflowValidationError,
                "supported only on macOS",
            ):
                open_handoff_folder(
                    root_dir=self.root,
                    handoff_id=handoff["handoff_id"],
                )

    def test_unverified_direct_import_applies_atomically_and_preserves_voice_config(self) -> None:
        old_voice_bytes = (self.root / "voice_config.json").read_bytes()
        candidate = self.inspect_direct(source_text=None)
        result = apply_annotated_script_candidate(
            root_dir=self.root,
            candidate_id=candidate["candidate_id"],
            current_script_fingerprint=fingerprint_value(self.old_entries),
            checkpoint_status="none",
            checkpoint_decision=None,
            at_utc="2026-07-17T21:10:00Z",
        )
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.read_json("annotated_script.json"), self.entries)
        chunks = self.read_json("chunks.json")
        self.assertTrue(chunks)
        self.assertTrue(all(chunk["status"] == "pending" for chunk in chunks))
        self.assertTrue(all(chunk["audio_path"] is None for chunk in chunks))
        self.assertEqual((self.root / "voice_config.json").read_bytes(), old_voice_bytes)

        metadata = self.read_json("annotated_script.meta.json")
        self.assertIsNone(metadata["source"]["fingerprint"])
        self.assertEqual(
            metadata["source"]["verification_status"],
            "unverified",
        )
        self.assertEqual(
            metadata["generation"]["effective_identity"]["model_name"],
            "Imported annotated script",
        )
        self.assertEqual(
            metadata["result"]["script_fingerprint"],
            fingerprint_value(self.entries),
        )
        self.assertEqual(
            metadata["import"]["candidate_id"],
            candidate["candidate_id"],
        )
        validity = self.read_json("audio_validity.json")
        self.assertTrue(validity["stale"])
        self.assertEqual(len(validity["invalidated_chunks"]), 1)
        self.assertEqual(
            validity["invalidated_chunks"][0]["audio_path"],
            "voicelines/chunk_000000.wav",
        )
        loaded = get_annotated_script_candidate(
            root_dir=self.root,
            candidate_id=candidate["candidate_id"],
        )
        self.assertEqual(loaded["status"], "applied")
        self.assertEqual(
            loaded["application"]["operation_id"],
            result["operation"]["operation_id"],
        )

    def test_import_moves_audio_to_operation_backup_and_rollback_restores_it(self) -> None:
        original = self.write_audio("voicelines/chunk_000000.wav")
        original_bytes = original.read_bytes()
        candidate = self.inspect_direct(source_text=None)
        applied = apply_annotated_script_candidate(
            root_dir=self.root,
            candidate_id=candidate["candidate_id"],
            current_script_fingerprint=fingerprint_value(self.old_entries),
            checkpoint_status="none",
            checkpoint_decision=None,
            at_utc="2026-07-17T21:11:00Z",
        )

        self.assertFalse(original.exists())
        self.assertEqual(applied["operation"]["audio_backup_count"], 1)
        validity = self.read_json("audio_validity.json")
        invalidation = validity["invalidated_chunks"][0]
        self.assertEqual(
            invalidation["canonical_audio_path"],
            "voicelines/chunk_000000.wav",
        )
        backup = self.root / invalidation["backup_audio_path"]
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.read_bytes(), original_bytes)
        self.assertEqual(len(invalidation["audio_sha256"]), 64)

        rollback = rollback_annotated_script_import(
            root_dir=self.root,
            operation_id=applied["operation"]["operation_id"],
            at_utc="2026-07-17T21:12:00Z",
        )
        self.assertEqual(
            rollback["restored_audio_paths"],
            ["voicelines/chunk_000000.wav"],
        )
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assertEqual(
            self.read_json("chunks.json")[0]["audio_path"],
            "voicelines/chunk_000000.wav",
        )

    def test_import_rollback_blocks_newer_canonical_audio(self) -> None:
        original = self.write_audio("voicelines/chunk_000000.wav")
        candidate = self.inspect_direct(source_text=None)
        applied = apply_annotated_script_candidate(
            root_dir=self.root,
            candidate_id=candidate["candidate_id"],
            current_script_fingerprint=fingerprint_value(self.old_entries),
            checkpoint_status="none",
            checkpoint_decision=None,
            at_utc="2026-07-17T21:13:00Z",
        )
        original.write_bytes(b"newer-generation")

        with self.assertRaisesRegex(
            ExternalWorkflowConflictError,
            "newer audio file exists",
        ):
            rollback_annotated_script_import(
                root_dir=self.root,
                operation_id=applied["operation"]["operation_id"],
                at_utc="2026-07-17T21:14:00Z",
            )
        self.assertEqual(original.read_bytes(), b"newer-generation")
        self.assertEqual(self.read_json("annotated_script.json"), self.entries)

    def test_verified_bundle_replaces_voice_config_and_discards_checkpoint(self) -> None:
        metadata = {
            "schema_version": 1,
            "generated_at_utc": "2026-07-17T20:00:00Z",
            "source": self.source_context,
            "generation": {
                "fingerprint": fingerprint_value({"model": "external"}),
                "effective_identity": {"model": "external"},
            },
            "result": {
                "script_fingerprint": fingerprint_value(self.entries),
                "entry_count": len(self.entries),
                "speaker_labels": ["DOCTOR", "NARRATOR"],
            },
            "resume": {
                "resumed": False,
                "previously_completed_chunks": 0,
            },
        }
        imported_voices = {
            "NARRATOR": {"type": "custom", "voice": "Ryan"},
            "DOCTOR": {"type": "design", "description": "Crisp."},
        }
        bundle = create_annotated_script_bundle(
            output_dir=self.root,
            entries=self.entries,
            metadata=metadata,
            voice_config=imported_voices,
            application_version="test",
            source_fingerprint=fingerprint_text(self.source),
            bundle_name="incoming.zip",
            created_at_utc="2026-07-17T20:00:00Z",
        )
        self.write_json(
            "generation_state.json",
            {"schema_version": 4, "saved": True},
        )
        candidate = inspect_annotated_script_upload(
            root_dir=self.root,
            import_path=bundle["path"],
            source_text=self.source,
            source_context=self.source_context,
            current_script_fingerprint=fingerprint_value(self.old_entries),
            checkpoint_status="resumable",
            generated_audio_count=1,
            created_at_utc="2026-07-17T21:00:00Z",
        )
        result = apply_annotated_script_candidate(
            root_dir=self.root,
            candidate_id=candidate["candidate_id"],
            current_script_fingerprint=fingerprint_value(self.old_entries),
            checkpoint_status="resumable",
            checkpoint_decision="discard",
            at_utc="2026-07-17T21:15:00Z",
        )
        self.assertFalse((self.root / "generation_state.json").exists())
        self.assertEqual(self.read_json("voice_config.json"), imported_voices)
        self.assertEqual(
            result["operation"]["checkpoint_decision"],
            "discard",
        )
        self.assertEqual(
            self.read_json("annotated_script.meta.json")["import"]["provenance"]["status"],
            "verified",
        )

    def test_checkpoint_choice_and_optimistic_state_conflicts_fail_closed(self) -> None:
        candidate = self.inspect_direct(checkpoint_status="resumable")
        with self.assertRaisesRegex(
            ExternalWorkflowConflictError,
            "Choose whether",
        ):
            apply_annotated_script_candidate(
                root_dir=self.root,
                candidate_id=candidate["candidate_id"],
                current_script_fingerprint=fingerprint_value(self.old_entries),
                checkpoint_status="resumable",
                checkpoint_decision=None,
            )
        with self.assertRaisesRegex(
            ExternalWorkflowConflictError,
            "script changed",
        ):
            apply_annotated_script_candidate(
                root_dir=self.root,
                candidate_id=candidate["candidate_id"],
                current_script_fingerprint=fingerprint_value([{"changed": True}]),
                checkpoint_status="resumable",
                checkpoint_decision="keep",
            )
        with self.assertRaisesRegex(
            ExternalWorkflowConflictError,
            "checkpoint changed",
        ):
            apply_annotated_script_candidate(
                root_dir=self.root,
                candidate_id=candidate["candidate_id"],
                current_script_fingerprint=fingerprint_value(self.old_entries),
                checkpoint_status="finalization_only",
                checkpoint_decision="keep",
            )
        with self.assertRaisesRegex(
            ExternalWorkflowConflictError,
            "Stop Script generation",
        ):
            running_candidate = self.inspect_direct(
                checkpoint_status="running",
                created_at_utc="2026-07-17T21:01:00Z",
            )
            apply_annotated_script_candidate(
                root_dir=self.root,
                candidate_id=running_candidate["candidate_id"],
                current_script_fingerprint=fingerprint_value(self.old_entries),
                checkpoint_status="running",
                checkpoint_decision="keep",
            )
        self.assertEqual(self.read_json("annotated_script.json"), self.old_entries)

    def test_invalid_imported_voice_aliases_are_rejected_before_writes(self) -> None:
        bundle = create_annotated_script_bundle(
            output_dir=self.root,
            entries=self.entries,
            metadata=None,
            voice_config={"DOCTOR": {"alias_of": "MISSING"}},
            application_version="test",
            bundle_name="bad-alias.zip",
            created_at_utc="2026-07-17T20:00:00Z",
        )
        candidate = inspect_annotated_script_upload(
            root_dir=self.root,
            import_path=bundle["path"],
            source_text=None,
            source_context=None,
            current_script_fingerprint=fingerprint_value(self.old_entries),
            checkpoint_status="none",
            generated_audio_count=1,
        )
        before = (self.root / "annotated_script.json").read_bytes()
        with self.assertRaises(ExternalWorkflowValidationError) as caught:
            apply_annotated_script_candidate(
                root_dir=self.root,
                candidate_id=candidate["candidate_id"],
                current_script_fingerprint=fingerprint_value(self.old_entries),
                checkpoint_status="none",
                checkpoint_decision=None,
            )
        self.assertEqual(caught.exception.code, "invalid_voice_aliases")
        self.assertEqual((self.root / "annotated_script.json").read_bytes(), before)

    def test_import_rollback_restores_exact_bytes_and_detects_later_changes(self) -> None:
        protected_names = [
            "annotated_script.json",
            "chunks.json",
            "voice_config.json",
        ]
        before = {
            name: (self.root / name).read_bytes()
            for name in protected_names
        }
        candidate = self.inspect_direct(source_text=None)
        applied = apply_annotated_script_candidate(
            root_dir=self.root,
            candidate_id=candidate["candidate_id"],
            current_script_fingerprint=fingerprint_value(self.old_entries),
            checkpoint_status="none",
            checkpoint_decision=None,
            at_utc="2026-07-17T21:20:00Z",
        )
        rollback = rollback_annotated_script_import(
            root_dir=self.root,
            operation_id=applied["operation"]["operation_id"],
            at_utc="2026-07-17T21:21:00Z",
        )
        self.assertEqual(
            rollback["rolls_back_operation_id"],
            applied["operation"]["operation_id"],
        )
        for name, content in before.items():
            self.assertEqual((self.root / name).read_bytes(), content)
        self.assertFalse((self.root / "annotated_script.meta.json").exists())
        self.assertFalse((self.root / "audio_validity.json").exists())
        self.assertEqual(
            get_annotated_script_candidate(
                root_dir=self.root,
                candidate_id=candidate["candidate_id"],
            )["status"],
            "inspected",
        )

        second = self.inspect_direct(
            source_text=None,
            created_at_utc="2026-07-17T21:22:00Z",
        )
        applied_again = apply_annotated_script_candidate(
            root_dir=self.root,
            candidate_id=second["candidate_id"],
            current_script_fingerprint=fingerprint_value(self.old_entries),
            checkpoint_status="none",
            checkpoint_decision=None,
            at_utc="2026-07-17T21:23:00Z",
        )
        self.write_json("chunks.json", [{"changed": True}])
        with self.assertRaisesRegex(
            ExternalWorkflowConflictError,
            "chunks.json changed",
        ):
            rollback_annotated_script_import(
                root_dir=self.root,
                operation_id=applied_again["operation"]["operation_id"],
            )

    def test_write_failure_rolls_back_every_touched_project_file(self) -> None:
        audio = self.write_audio("voicelines/chunk_000000.wav")
        audio_bytes = audio.read_bytes()
        candidate = self.inspect_direct(source_text=None)
        protected = {
            path: path.read_bytes()
            for path in [
                self.root / "annotated_script.json",
                self.root / "chunks.json",
                self.root / "voice_config.json",
                self.root
                / "external_workflows"
                / "candidates"
                / f"{candidate['candidate_id']}.json",
            ]
        }
        original_atomic = external_workflows.atomic_json_write

        def fail_on_chunks(value, path):
            if Path(path) == self.root / "chunks.json":
                raise OSError("injected chunk write failure")
            return original_atomic(value, path)

        with patch.object(
            external_workflows,
            "atomic_json_write",
            side_effect=fail_on_chunks,
        ):
            with self.assertRaisesRegex(OSError, "injected"):
                apply_annotated_script_candidate(
                    root_dir=self.root,
                    candidate_id=candidate["candidate_id"],
                    current_script_fingerprint=fingerprint_value(self.old_entries),
                    checkpoint_status="none",
                    checkpoint_decision=None,
                    at_utc="2026-07-17T21:30:00Z",
                )
        for path, content in protected.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertFalse((self.root / "annotated_script.meta.json").exists())
        self.assertFalse((self.root / "audio_validity.json").exists())
        self.assertEqual(audio.read_bytes(), audio_bytes)
        backups = list(
            (self.root / "external_workflows" / "import_history").glob("*/audio/*")
        )
        self.assertEqual(backups, [])


if __name__ == "__main__":
    unittest.main()
