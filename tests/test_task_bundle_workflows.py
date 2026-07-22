from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from chatgpt_handoff import create_handoff_bundle
from external_workflows import (
    ExternalWorkflowConflictError,
    ExternalWorkflowValidationError,
    create_stored_task_bundle,
    get_task_bundle_path,
    inspect_completed_task_upload,
    list_task_library,
    mark_structured_result_transferred,
)
from generation_state import atomic_json_write as real_atomic_json_write
from generation_state import fingerprint_text
from llm_schemas import get_schema
from task_bundles import (
    create_completed_task_bundle,
    create_result_envelope,
)


class TaskBundleWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_text = "Good evening."
        self.source_context = {
            "path": str(self.root / "source.txt"),
            "basename": "source.txt",
            "fingerprint": fingerprint_text(self.source_text),
            "character_count": len(self.source_text),
        }
        (self.root / "source.txt").write_text(
            self.source_text,
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create_persona(self):
        return create_stored_task_bundle(
            root_dir=self.root,
            task_type="persona_generation",
            input_payload={
                "speaker": "THE DOCTOR",
                "sample_lines": [self.source_text],
            },
            application_version="test",
            source_fingerprint=self.source_context["fingerprint"],
            target={"kind": "speaker", "value": "THE DOCTOR"},
            created_at_utc="2026-07-19T12:00:00Z",
        )

    def inspect(self, completed_path, original_task_path=None):
        return inspect_completed_task_upload(
            root_dir=self.root,
            completed_path=completed_path,
            original_task_path=original_task_path,
            current_source_fingerprint=self.source_context["fingerprint"],
            current_artifact_fingerprints={},
            source_text=self.source_text,
            source_context=self.source_context,
            current_script_fingerprint=None,
            checkpoint_status="none",
            generated_audio_count=0,
            created_at_utc="2026-07-19T13:00:00Z",
        )

    def test_stored_task_round_trip_and_json_auto_match(self) -> None:
        task = self.create_persona()
        bundle_path, record = get_task_bundle_path(
            root_dir=self.root,
            task_id=task["task_id"],
        )
        self.assertEqual(record["status"], "exported")
        envelope = create_result_envelope(
            task_bundle_path=bundle_path,
            result={
                "description": "Tenor, clear and lightly nasal.",
                "ref_text": self.source_text,
            },
        )
        result_path = self.root / "completed.json"
        result_path.write_text(json.dumps(envelope), encoding="utf-8")
        candidate = self.inspect(result_path)
        self.assertEqual(candidate["kind"], "structured_result")
        self.assertEqual(candidate["task_type"], "persona_generation")
        self.assertEqual(candidate["task_label"], "Create one Voice profile")
        self.assertEqual(
            candidate["native_transfer"]["destination"],
            "expressive_voices",
        )
        self.assertFalse(candidate["duplicate"])
        _, imported_record = get_task_bundle_path(
            root_dir=self.root,
            task_id=task["task_id"],
        )
        self.assertEqual(imported_record["status"], "imported")
        self.assertEqual(
            imported_record["import"]["candidate_id"],
            candidate["candidate_id"],
        )

    def test_task_record_write_failure_rolls_back_new_candidate(self) -> None:
        task = self.create_persona()
        bundle_path, original_record = get_task_bundle_path(
            root_dir=self.root,
            task_id=task["task_id"],
        )
        envelope = create_result_envelope(
            task_bundle_path=bundle_path,
            result={
                "description": "Tenor, clear and lightly nasal.",
                "ref_text": self.source_text,
            },
        )
        result_path = self.root / "transaction-failure.json"
        result_path.write_text(json.dumps(envelope), encoding="utf-8")

        def fail_task_record_only(value, path):
            if Path(path).name == "record.json":
                raise OSError("injected task record failure")
            return real_atomic_json_write(value, path)

        with mock.patch(
            "external_workflows.atomic_json_write",
            side_effect=fail_task_record_only,
        ):
            with self.assertRaises(ExternalWorkflowValidationError) as error:
                self.inspect(result_path)
        self.assertEqual(error.exception.code, "task_import_transaction_failed")
        candidate_root = self.root / "external_workflows" / "candidates"
        self.assertEqual(list(candidate_root.glob("*.json")), [])
        _, current_record = get_task_bundle_path(
            root_dir=self.root,
            task_id=task["task_id"],
        )
        self.assertEqual(current_record, original_record)

    def test_task_library_persists_filters_and_tracks_state(self) -> None:
        task = self.create_persona()
        awaiting = list_task_library(
            root_dir=self.root,
            current_source_fingerprint=self.source_context["fingerprint"],
            current_artifact_fingerprints={},
        )
        self.assertEqual([item["status"] for item in awaiting], ["awaiting_import"])
        self.assertEqual(awaiting[0]["review_destination"], "expressive_voices")
        self.assertNotIn("handoff_id", awaiting[0])
        self.assertEqual(
            len(list_task_library(root_dir=self.root, query="doctor")),
            1,
        )
        self.assertEqual(
            list_task_library(root_dir=self.root, status="transferred"),
            [],
        )

        stale = list_task_library(
            root_dir=self.root,
            current_source_fingerprint="f" * 64,
            current_artifact_fingerprints={},
        )
        self.assertEqual(stale[0]["status"], "stale")

        bundle_path, _ = get_task_bundle_path(
            root_dir=self.root,
            task_id=task["task_id"],
        )
        envelope = create_result_envelope(
            task_bundle_path=bundle_path,
            result={
                "description": "Tenor, clear and lightly nasal.",
                "ref_text": self.source_text,
            },
        )
        result_path = self.root / "library-result.json"
        result_path.write_text(json.dumps(envelope), encoding="utf-8")
        candidate = self.inspect(result_path)
        imported = list_task_library(
            root_dir=self.root,
            current_source_fingerprint=self.source_context["fingerprint"],
            current_artifact_fingerprints={},
        )
        self.assertEqual(imported[0]["status"], "imported")

        mark_structured_result_transferred(
            root_dir=self.root,
            candidate_id=candidate["candidate_id"],
            expected_result_fingerprint=candidate["result_fingerprint"],
            application={"destination": "expressive_voices"},
        )
        transferred = list_task_library(
            root_dir=self.root,
            current_source_fingerprint=self.source_context["fingerprint"],
            current_artifact_fingerprints={},
            status="transferred",
        )
        self.assertEqual(len(transferred), 1)
        self.assertEqual(transferred[0]["status"], "transferred")

        bundle_path.unlink()
        failed = list_task_library(root_dir=self.root, status="failed")
        self.assertEqual(len(failed), 1)
        self.assertIsNotNone(failed[0]["error"])

        with self.assertRaises(ExternalWorkflowValidationError) as error:
            list_task_library(root_dir=self.root, status="unknown")
        self.assertEqual(error.exception.code, "invalid_task_library_status")

    def test_duplicate_result_returns_existing_candidate(self) -> None:
        task = self.create_persona()
        bundle_path, _ = get_task_bundle_path(
            root_dir=self.root,
            task_id=task["task_id"],
        )
        envelope = create_result_envelope(
            task_bundle_path=bundle_path,
            result={
                "description": "Tenor, clear and lightly nasal.",
                "ref_text": self.source_text,
            },
        )
        result_path = self.root / "completed.json"
        result_path.write_text(json.dumps(envelope), encoding="utf-8")
        first = self.inspect(result_path)
        second = self.inspect(result_path)
        self.assertEqual(second["candidate_id"], first["candidate_id"])
        self.assertTrue(second["duplicate"])

    def test_self_contained_completed_zip_needs_no_library_record(self) -> None:
        task = self.create_persona()
        bundle_path, _ = get_task_bundle_path(
            root_dir=self.root,
            task_id=task["task_id"],
        )
        completed_path = self.root / "completed.alexandria-completed-task.zip"
        create_completed_task_bundle(
            task_bundle_path=bundle_path,
            result={
                "description": "Tenor, clear and lightly nasal.",
                "ref_text": self.source_text,
            },
            output_path=completed_path,
        )
        other = Path(tempfile.mkdtemp())
        try:
            candidate = inspect_completed_task_upload(
                root_dir=other,
                completed_path=completed_path,
                original_task_path=None,
                current_source_fingerprint=self.source_context["fingerprint"],
                current_artifact_fingerprints={},
                source_text=self.source_text,
                source_context=self.source_context,
                current_script_fingerprint=None,
                checkpoint_status="none",
                generated_audio_count=0,
            )
            self.assertEqual(candidate["task_type"], "persona_generation")
        finally:
            import shutil

            shutil.rmtree(other)

    def test_script_result_enters_existing_script_candidate_review(self) -> None:
        task = create_stored_task_bundle(
            root_dir=self.root,
            task_type="script_generation",
            input_payload={"source_text": self.source_text},
            application_version="test",
            source_fingerprint=self.source_context["fingerprint"],
            created_at_utc="2026-07-19T12:00:00Z",
        )
        bundle_path, _ = get_task_bundle_path(
            root_dir=self.root,
            task_id=task["task_id"],
        )
        envelope = create_result_envelope(
            task_bundle_path=bundle_path,
            result=[
                {
                    "speaker": "NARRATOR",
                    "text": self.source_text,
                    "instruct": "Even narration.",
                }
            ],
        )
        result_path = self.root / "script-result.json"
        result_path.write_text(json.dumps(envelope), encoding="utf-8")
        candidate = self.inspect(result_path)
        self.assertEqual(candidate["kind"], "annotated_script")
        self.assertEqual(
            candidate["origin"]["native_destination"],
            "script_review",
        )
        self.assertEqual(candidate["summary"]["entry_count"], 1)

    def test_script_review_and_line_direction_results_enter_review_only(self) -> None:
        for task_type, input_payload in (
            ("script_review", {"entries": [{"speaker": "NARRATOR", "text": self.source_text, "instruct": "Even narration."}]}),
            ("line_direction_generation", {"entries": [{"speaker": "NARRATOR", "text": self.source_text, "instruct": ""}]}),
            ("line_direction_audit", {"entries": [{"speaker": "NARRATOR", "text": self.source_text, "instruct": "Even narration."}]}),
        ):
            with self.subTest(task_type=task_type):
                task = create_stored_task_bundle(
                    root_dir=self.root,
                    task_type=task_type,
                    input_payload=input_payload,
                    application_version="test",
                    source_fingerprint=self.source_context["fingerprint"],
                    created_at_utc="2026-07-19T12:00:00Z",
                )
                bundle_path, _ = get_task_bundle_path(
                    root_dir=self.root,
                    task_id=task["task_id"],
                )
                envelope = create_result_envelope(
                    task_bundle_path=bundle_path,
                    result=[
                        {
                            "speaker": "NARRATOR",
                            "text": self.source_text,
                            "instruct": "Measured and clear.",
                        }
                    ],
                )
                result_path = self.root / f"{task_type}.json"
                result_path.write_text(json.dumps(envelope), encoding="utf-8")
                candidate = self.inspect(result_path)
                self.assertEqual(candidate["kind"], "annotated_script")
                self.assertEqual(candidate["status"], "inspected")
                self.assertIsNone(candidate.get("application"))
                self.assertEqual(
                    candidate["origin"]["native_destination"],
                    "script_review" if task_type == "script_review" else "editor",
                )

    def test_legacy_result_requires_original_zip_instead_of_code(self) -> None:
        legacy_dir = self.root / "legacy"
        legacy = create_handoff_bundle(
            output_dir=legacy_dir,
            task_type="persona_generation",
            stage_prompt="Create a Persona.",
            input_payload={
                "speaker": "THE DOCTOR",
                "sample_lines": [self.source_text],
            },
            output_schema=get_schema("persona"),
            application_version="test",
            source_fingerprint=self.source_context["fingerprint"],
            created_at_utc="2026-07-19T12:00:00Z",
        )
        result_path = self.root / "legacy-result.json"
        result_path.write_text(
            json.dumps(
                {
                    "description": "Tenor, clear and lightly nasal.",
                    "ref_text": self.source_text,
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ExternalWorkflowConflictError) as error:
            self.inspect(result_path)
        self.assertEqual(error.exception.code, "legacy_task_bundle_required")
        candidate = self.inspect(result_path, original_task_path=legacy["path"])
        self.assertEqual(candidate["task_type"], "persona_generation")
        self.assertEqual(candidate["kind"], "structured_result")

    def test_unknown_v2_envelope_requests_original_zip(self) -> None:
        task = self.create_persona()
        bundle_path, _ = get_task_bundle_path(
            root_dir=self.root,
            task_id=task["task_id"],
        )
        envelope = create_result_envelope(
            task_bundle_path=bundle_path,
            result={
                "description": "Tenor, clear and lightly nasal.",
                "ref_text": self.source_text,
            },
        )
        result_path = self.root / "completed.json"
        result_path.write_text(json.dumps(envelope), encoding="utf-8")
        other = Path(tempfile.mkdtemp())
        try:
            with self.assertRaises(ExternalWorkflowConflictError) as error:
                inspect_completed_task_upload(
                    root_dir=other,
                    completed_path=result_path,
                    original_task_path=None,
                    current_source_fingerprint=self.source_context["fingerprint"],
                    current_artifact_fingerprints={},
                    source_text=self.source_text,
                    source_context=self.source_context,
                    current_script_fingerprint=None,
                    checkpoint_status="none",
                    generated_audio_count=0,
                )
            self.assertEqual(error.exception.code, "original_task_required")
            candidate = inspect_completed_task_upload(
                root_dir=other,
                completed_path=result_path,
                original_task_path=bundle_path,
                current_source_fingerprint=self.source_context["fingerprint"],
                current_artifact_fingerprints={},
                source_text=self.source_text,
                source_context=self.source_context,
                current_script_fingerprint=None,
                checkpoint_status="none",
                generated_audio_count=0,
            )
            self.assertEqual(candidate["task_type"], "persona_generation")
        finally:
            import shutil

            shutil.rmtree(other)


if __name__ == "__main__":
    unittest.main()
