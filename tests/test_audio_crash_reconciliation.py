from __future__ import annotations

import json
import hashlib
import multiprocessing
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from audio_crash_reconciliation import (
    AUDIO_DURABLE_TRANSITIONS,
    InjectedAudioCrash,
    JournalSchemaError,
    _record_fingerprint,
    _validate_record,
    apply_audio_transition,
    reconcile_audio_transitions,
)
from generation_state import fingerprint_value


EXPECTED_HELPER_TRANSITIONS = (
    "internal_segment_generation",
    "segment_completion",
    "join",
    "immutable_take_installation",
    "chunks_metadata",
    "take_registry",
    "request_receipt_publication",
    "lifecycle_receipt_publication",
    "current_take_selection",
    "invalidation",
    "undo_restoration",
)


def _concurrent_transition(root: str, operation_id: str, marker: str) -> None:
    import audio_crash_reconciliation as reconciliation

    original = reconciliation.atomic_json_write

    def delayed_write(value, path):
        original(value, path)
        if Path(path).name == "transition.json" and value.get("status") == "applying":
            time.sleep(0.2)

    reconciliation.atomic_json_write = delayed_write
    reconciliation.apply_audio_transition(
        root,
        transition="chunks_metadata",
        operation_id=operation_id,
        json_writes={"chunks.json": [{"marker": marker}]},
    )


class AudioCrashReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.chunks_path = self.root / "chunks.json"
        self.registry_path = self.root / "audio_takes.json"
        self.before_chunks = [{"id": 0, "status": "pending", "current_take_id": None}]
        self.after_chunks = [{"id": 0, "status": "done", "current_take_id": "take_new"}]
        self.before_registry = {"chunks": {"chunk:0": {"current_take_id": None}}}
        self.after_registry = {"chunks": {"chunk:0": {"current_take_id": "take_new"}}}
        self.chunks_path.write_text(json.dumps(self.before_chunks), encoding="utf-8")
        self.registry_path.write_text(json.dumps(self.before_registry), encoding="utf-8")
        self.artifact = self.root / "voicelines" / "takes" / "take_new.wav"
        self.artifact.parent.mkdir(parents=True)
        self.artifact.write_bytes(b"synthetic-audio-fixture")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _apply(self, transition: str, crash_point: str) -> None:
        apply_audio_transition(
            self.root,
            transition=transition,
            operation_id=f"fixture-{transition}-{crash_point}",
            json_writes={
                "chunks.json": self.after_chunks,
                "audio_takes.json": self.after_registry,
            },
            required_artifacts={"voicelines/takes/take_new.wav": None},
            crash_point=crash_point,
        )

    def test_generic_helper_transition_taxonomy_is_explicit(self) -> None:
        self.assertEqual(AUDIO_DURABLE_TRANSITIONS, EXPECTED_HELPER_TRANSITIONS)

    def test_generic_transition_helper_repairs_pre_and_post_write_crashes(self) -> None:
        for transition in EXPECTED_HELPER_TRANSITIONS:
            for crash_point in ("before", "after"):
                with self.subTest(transition=transition, crash_point=crash_point):
                    # Given: exact prior metadata and an immutable synthetic artifact.
                    self.chunks_path.write_text(json.dumps(self.before_chunks), encoding="utf-8")
                    self.registry_path.write_text(json.dumps(self.before_registry), encoding="utf-8")

                    # When: the process stops immediately before or after the durable write.
                    with self.assertRaises(InjectedAudioCrash):
                        self._apply(transition, crash_point)
                    report = reconcile_audio_transitions(self.root)

                    # Then: startup deterministically publishes one exact selection.
                    self.assertEqual(report["unresolved_count"], 0)
                    self.assertEqual(json.loads(self.chunks_path.read_text()), self.after_chunks)
                    self.assertEqual(json.loads(self.registry_path.read_text()), self.after_registry)
                    self.assertEqual(report["repaired_count"], 1)
                    self.assertTrue(self.artifact.is_file())

    def test_reconciliation_preserves_unexpected_evidence_and_reports_ambiguity(self) -> None:
        # Given: a pending transition whose target was independently replaced.
        with self.assertRaises(InjectedAudioCrash):
            self._apply("current_take_selection", "before")
        unexpected = [{"id": 0, "status": "done", "current_take_id": "take_other"}]
        self.chunks_path.write_text(json.dumps(unexpected), encoding="utf-8")

        # When: startup reconciliation encounters state matching neither side.
        report = reconcile_audio_transitions(self.root)

        # Then: it reports ambiguity and does not overwrite or delete evidence.
        self.assertEqual(report["unresolved_count"], 1)
        self.assertEqual(json.loads(self.chunks_path.read_text()), unexpected)
        self.assertTrue(self.artifact.is_file())

    def test_corrupt_journal_is_retained_and_never_reported_as_success(self) -> None:
        # Given: malformed durable transition state.
        journal = self.root / "audio_transition_journal" / "corrupt" / "transition.json"
        journal.parent.mkdir(parents=True)
        journal.write_text("{not-json", encoding="utf-8")

        # When: startup reconciliation scans the project.
        report = reconcile_audio_transitions(self.root)

        # Then: corruption stays visible and terminal success is rejected.
        self.assertEqual(report["unresolved_count"], 1)
        self.assertEqual(report["repaired_count"], 0)
        self.assertTrue(journal.is_file())

    def test_operation_id_is_one_safe_opaque_path_component(self) -> None:
        for operation_id in ("../escaped", "nested/name", ".", ""):
            with self.subTest(operation_id=operation_id):
                with self.assertRaisesRegex(ValueError, "operation_id"):
                    apply_audio_transition(
                        self.root,
                        transition="chunks_metadata",
                        operation_id=operation_id,
                        json_writes={"chunks.json": self.after_chunks},
                    )
        self.assertFalse((self.root.parent / "escaped" / "transition.json").exists())

    def test_operation_journal_rejects_symlink_redirection(self) -> None:
        outside = self.root.parent / f"{self.root.name}-outside"
        outside.mkdir()
        journal_root = self.root / "audio_transition_journal"
        journal_root.mkdir()
        (journal_root / "redirected").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "journal"):
            apply_audio_transition(
                self.root,
                transition="chunks_metadata",
                operation_id="redirected",
                json_writes={"chunks.json": self.after_chunks},
            )
        self.assertFalse((outside / "transition.json").exists())

    def test_startup_rejects_symlinked_journal_root_without_external_io(self) -> None:
        outside = self.root.parent / f"{self.root.name}-outside-root"
        outside.mkdir()
        marker = outside / "marker"
        marker.write_bytes(b"outside-evidence")
        (self.root / "audio_transition_journal").symlink_to(
            outside,
            target_is_directory=True,
        )

        with self.assertRaisesRegex(ValueError, "journal root"):
            reconcile_audio_transitions(self.root)

        self.assertEqual(marker.read_bytes(), b"outside-evidence")
        self.assertEqual(sorted(path.name for path in outside.iterdir()), ["marker"])

    def _assert_startup_rejects_nested_symlink_without_external_read(
        self,
        *,
        symlink_transition_file: bool,
    ) -> None:
        journal_root = self.root / "audio_transition_journal"
        outside = self.root.parent / (
            f"{self.root.name}-outside-file"
            if symlink_transition_file
            else f"{self.root.name}-outside-operation"
        )
        outside.mkdir()
        external_transition = outside / "transition.json"
        external_bytes = b'{"external":"must-not-be-read"}'
        external_transition.write_bytes(external_bytes)
        external_mtime = external_transition.stat().st_mtime_ns

        if symlink_transition_file:
            operation = journal_root / "00-external-file"
            operation.mkdir(parents=True)
            (operation / "transition.json").symlink_to(external_transition)
            expected_code = "journal_file_symlink"
        else:
            journal_root.mkdir()
            (journal_root / "00-external-operation").symlink_to(
                outside,
                target_is_directory=True,
            )
            expected_code = "journal_operation_symlink"

        with self.assertRaises(InjectedAudioCrash):
            apply_audio_transition(
                self.root,
                transition="chunks_metadata",
                operation_id="zz-valid-local",
                json_writes={"chunks.json": self.after_chunks},
                required_artifacts={"voicelines/takes/take_new.wav": None},
                crash_point="before",
            )

        original_read_text = Path.read_text
        external_reads = []

        def reject_external_read(path, *args, **kwargs):
            if Path(path).resolve().is_relative_to(outside.resolve()):
                external_reads.append(Path(path))
                raise AssertionError("startup attempted an external journal read")
            return original_read_text(path, *args, **kwargs)

        with patch.object(Path, "read_text", reject_external_read):
            report = reconcile_audio_transitions(self.root)

        self.assertEqual(external_reads, [])
        self.assertEqual(external_transition.read_bytes(), external_bytes)
        self.assertEqual(external_transition.stat().st_mtime_ns, external_mtime)
        rejected = next(
            item
            for item in report["actions"]
            if item["operation_id"].startswith("00-external")
        )
        self.assertEqual(rejected["action"], "unresolved")
        self.assertEqual(rejected["error_code"], expected_code)
        self.assertEqual(report["repaired_count"], 1)
        self.assertEqual(json.loads(self.chunks_path.read_text()), self.after_chunks)

    def test_startup_rejects_symlinked_operation_directory_without_external_read(self) -> None:
        self._assert_startup_rejects_nested_symlink_without_external_read(
            symlink_transition_file=False,
        )

    def test_startup_rejects_symlinked_transition_file_without_external_read(self) -> None:
        self._assert_startup_rejects_nested_symlink_without_external_read(
            symlink_transition_file=True,
        )

    def test_corrupt_field_reports_typed_schema_error(self) -> None:
        journal = self.root / "audio_transition_journal" / "typed-corrupt" / "transition.json"
        journal.parent.mkdir(parents=True)
        journal.write_text(json.dumps({"schema_version": 1, "writes": []}), encoding="utf-8")
        report = reconcile_audio_transitions(self.root)
        action = report["actions"][0]
        self.assertEqual(action["action"], "unresolved")
        self.assertEqual(action["error_code"], "journal_schema_invalid")
        self.assertEqual(action["error_field"], "operation_id")

    def test_nested_corrupt_journal_is_retained_while_later_valid_journal_repairs(self) -> None:
        corrupt_path = (
            self.root
            / "audio_transition_journal"
            / "00-corrupt"
            / "transition.json"
        )
        corrupt_path.parent.mkdir(parents=True)
        corrupt = {
            "schema_version": 1,
            "operation_id": "00-corrupt",
            "transition": "chunks_metadata",
            "status": "applying",
            "created_at_utc": "2026-08-01T00:00:00Z",
            "writes": {"chunks.json": "not-a-snapshot-pair"},
            "required_artifacts": [],
            "record_fingerprint": None,
        }
        corrupt["record_fingerprint"] = fingerprint_value(
            {
                key: value
                for key, value in corrupt.items()
                if key != "record_fingerprint"
            }
        )
        corrupt_path.write_text(json.dumps(corrupt), encoding="utf-8")
        with self.assertRaises(InjectedAudioCrash):
            apply_audio_transition(
                self.root,
                transition="chunks_metadata",
                operation_id="zz-valid",
                json_writes={"chunks.json": self.after_chunks},
                required_artifacts={"voicelines/takes/take_new.wav": None},
                crash_point="before",
            )

        report = reconcile_audio_transitions(self.root)

        corrupt_action = next(
            item for item in report["actions"] if item["operation_id"] == "00-corrupt"
        )
        self.assertEqual(corrupt_action["action"], "unresolved")
        self.assertEqual(corrupt_action["error_code"], "journal_schema_invalid")
        self.assertEqual(corrupt_action["error_field"], "writes.chunks.json")
        self.assertTrue(corrupt_path.is_file())
        self.assertEqual(report["repaired_count"], 1)
        self.assertEqual(json.loads(self.chunks_path.read_text()), self.after_chunks)

    def test_every_nested_journal_shape_is_validated_with_a_typed_field(self) -> None:
        with self.assertRaises(InjectedAudioCrash):
            apply_audio_transition(
                self.root,
                transition="chunks_metadata",
                operation_id="schema-template",
                json_writes={"chunks.json": self.after_chunks},
                required_artifacts={"voicelines/takes/take_new.wav": None},
                crash_point="before",
            )
        template = json.loads(
            (
                self.root
                / "audio_transition_journal"
                / "schema-template"
                / "transition.json"
            ).read_text()
        )
        cases = {
            "writes.chunks.json": lambda value: value["writes"].update(
                {"chunks.json": []}
            ),
            "writes.chunks.json.before": lambda value: value["writes"][
                "chunks.json"
            ].update({"before": []}),
            "writes.chunks.json.before.exists": lambda value: value["writes"][
                "chunks.json"
            ]["before"].update({"exists": "yes"}),
            "writes.chunks.json.before.sha256": lambda value: value["writes"][
                "chunks.json"
            ]["before"].update({"sha256": "not-a-sha"}),
            "writes.chunks.json.before.content_base64": lambda value: value[
                "writes"
            ]["chunks.json"]["before"].update({"content_base64": "%%%"}),
            "required_artifacts": lambda value: value.update(
                {"required_artifacts": {}}
            ),
            "required_artifacts.0": lambda value: value.update(
                {"required_artifacts": ["artifact"]}
            ),
            "required_artifacts.0.relative_path": lambda value: value[
                "required_artifacts"
            ][0].update({"relative_path": "../escaped.wav"}),
            "required_artifacts.0.sha256": lambda value: value[
                "required_artifacts"
            ][0].update({"sha256": "not-a-sha"}),
        }
        for expected_field, mutate in cases.items():
            with self.subTest(expected_field=expected_field):
                candidate = json.loads(json.dumps(template))
                mutate(candidate)
                candidate["record_fingerprint"] = _record_fingerprint(candidate)
                with self.assertRaises(JournalSchemaError) as caught:
                    _validate_record(candidate)
                self.assertEqual(caught.exception.field, expected_field)

    def test_separate_processes_serialize_snapshot_through_commit(self) -> None:
        initial_bytes = self.chunks_path.read_bytes()
        processes = [
            multiprocessing.Process(
                target=_concurrent_transition,
                args=(str(self.root), f"process-{marker}", marker),
            )
            for marker in ("one", "two")
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(5)
            self.assertEqual(process.exitcode, 0)
        before_hashes = []
        for marker in ("one", "two"):
            journal = json.loads(
                (self.root / "audio_transition_journal" / f"process-{marker}" / "transition.json").read_text()
            )
            before_hashes.append(journal["writes"]["chunks.json"]["before"]["sha256"])
        initial_hash = hashlib.sha256(initial_bytes).hexdigest()
        self.assertEqual(before_hashes.count(initial_hash), 1)
        self.assertEqual(reconcile_audio_transitions(self.root)["actions"], [])

    def test_missing_required_artifact_rolls_metadata_back_without_deleting_journal(self) -> None:
        # Given: metadata was published but the required immutable output is missing.
        with self.assertRaises(InjectedAudioCrash):
            self._apply("immutable_take_installation", "after")
        self.artifact.unlink()

        # When: startup reconciles the incomplete installation.
        report = reconcile_audio_transitions(self.root)

        # Then: false success is rejected and exact prior metadata is restored.
        self.assertEqual(report["rolled_back_count"], 1)
        self.assertEqual(json.loads(self.chunks_path.read_text()), self.before_chunks)
        self.assertEqual(json.loads(self.registry_path.read_text()), self.before_registry)
        self.assertEqual(report["unresolved_count"], 0)

    def test_environment_crash_injection_is_explicitly_test_gated_and_reconciliation_is_idempotent(self) -> None:
        # Given: a configured interruption that is disabled without the test gate.
        with patch.dict(
            os.environ,
            {"ALEXANDRIA_AUDIO_CRASH_POINT": "chunks_metadata:after"},
            clear=False,
        ):
            applied = apply_audio_transition(
                self.root,
                transition="chunks_metadata",
                operation_id="ungated",
                json_writes={"chunks.json": self.after_chunks},
            )
        self.assertEqual(applied["status"], "committed")

        # When: the same configured interruption is deliberately test-enabled.
        self.chunks_path.write_text(json.dumps(self.before_chunks), encoding="utf-8")
        with patch.dict(
            os.environ,
            {
                "ALEXANDRIA_TEST_AUDIO_CRASH_INJECTION": "1",
                "ALEXANDRIA_AUDIO_CRASH_POINT": "chunks_metadata:after",
            },
            clear=False,
        ):
            with self.assertRaises(InjectedAudioCrash):
                apply_audio_transition(
                    self.root,
                    transition="chunks_metadata",
                    operation_id="gated",
                    json_writes={"chunks.json": self.after_chunks},
                )

        # Then: one startup repairs it and repeated startup is a no-op.
        first = reconcile_audio_transitions(self.root)
        second = reconcile_audio_transitions(self.root)
        self.assertEqual(first["repaired_count"], 1)
        self.assertEqual(second["actions"], [])
        self.assertEqual(json.loads(self.chunks_path.read_text()), self.after_chunks)


if __name__ == "__main__":
    unittest.main()
