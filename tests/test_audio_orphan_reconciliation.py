from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import audio_crash_reconciliation as reconciliation


class AudioOrphanReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "chunks.json").write_text("[]", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _categories(self) -> set[str]:
        return {
            item["category"]
            for item in reconciliation.inspect_audio_orphans(self.root)["issues"]
        }

    def test_detects_orphaned_canonical_file(self) -> None:
        path = self.root / "voicelines" / "orphan.wav"
        path.parent.mkdir()
        path.write_bytes(b"unknown-canonical")
        self.assertIn("canonical_file", self._categories())

    def test_detects_orphaned_temporary_file(self) -> None:
        path = self.root / "voicelines" / ".render.wav.tmp"
        path.parent.mkdir()
        path.write_bytes(b"unknown-temporary")
        self.assertIn("temporary_file", self._categories())

    def test_detects_orphaned_backup_file(self) -> None:
        path = self.root / "audio_take_history" / "op" / "audio" / "deadbeef.bin"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"unknown-backup")
        self.assertIn("backup_file", self._categories())

    def test_detects_orphaned_internal_segment_file(self) -> None:
        path = (
            self.root
            / "audio_generation_requests"
            / "audio_request_fixture"
            / "segments"
            / "chunk"
            / "segment.wav"
        )
        path.parent.mkdir(parents=True)
        path.write_bytes(b"unknown-segment")
        self.assertIn("internal_segment_file", self._categories())

    def test_detects_metadata_only_record(self) -> None:
        (self.root / "chunks.json").write_text(
            json.dumps(
                [
                    {
                        "id": 0,
                        "audio_path": "voicelines/missing.wav",
                        "audio_sha256": hashlib.sha256(b"missing").hexdigest(),
                    }
                ]
            ),
            encoding="utf-8",
        )
        self.assertIn("metadata_only", self._categories())

    def test_detects_artifact_only_take_file(self) -> None:
        path = self.root / "voicelines" / "takes" / "chunk_0" / "take.wav"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"unknown-take")
        self.assertIn("artifact_only", self._categories())

    def test_valid_cross_referenced_take_is_not_orphaned(self) -> None:
        path = self.root / "voicelines" / "takes" / "chunk_0" / "take.wav"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"known-take")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        relative = path.relative_to(self.root).as_posix()
        (self.root / "chunks.json").write_text(
            json.dumps([{"id": 0, "audio_path": relative, "audio_sha256": digest}]),
            encoding="utf-8",
        )
        (self.root / "audio_takes.json").write_text(
            json.dumps(
                {
                    "chunks": {"chunk:0": {"current_take_id": "take_0"}},
                    "takes": {
                        "take_0": {
                            "artifact": {"relative_path": relative, "sha256": digest}
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(reconciliation.inspect_audio_orphans(self.root)["issues"], [])

    def test_hash_mismatched_canonical_bytes_are_retained_and_never_current(self) -> None:
        path = self.root / "voicelines" / "current.wav"
        path.parent.mkdir()
        path.write_bytes(b"unknown-replacement")
        (self.root / "chunks.json").write_text(
            json.dumps(
                [
                    {
                        "id": 0,
                        "audio_path": "voicelines/current.wav",
                        "audio_sha256": hashlib.sha256(b"expected").hexdigest(),
                    }
                ]
            ),
            encoding="utf-8",
        )
        issue = reconciliation.inspect_audio_orphans(self.root)["issues"][0]
        self.assertEqual(issue["category"], "canonical_file")
        self.assertEqual(issue["state"], "hash_mismatch")
        self.assertEqual([item["kind"] for item in issue["actions"]], ["retain_evidence"])
        self.assertEqual(path.read_bytes(), b"unknown-replacement")

    def test_hashless_metadata_never_makes_unknown_bytes_current(self) -> None:
        path = self.root / "voicelines" / "legacy.wav"
        path.parent.mkdir()
        path.write_bytes(b"unverified-legacy-bytes")
        (self.root / "chunks.json").write_text(
            json.dumps([{"id": 0, "audio_path": "voicelines/legacy.wav"}]),
            encoding="utf-8",
        )
        issue = reconciliation.inspect_audio_orphans(self.root)["issues"][0]
        self.assertEqual(issue["state"], "hash_unverified")
        self.assertEqual([item["kind"] for item in issue["actions"]], ["retain_evidence"])

    def test_conflicting_cross_references_remain_ambiguous(self) -> None:
        path = self.root / "voicelines" / "takes" / "chunk_0" / "take.wav"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"cross-referenced")
        relative = path.relative_to(self.root).as_posix()
        (self.root / "chunks.json").write_text(
            json.dumps([{"id": 0, "audio_path": relative, "audio_sha256": "a" * 64}]),
            encoding="utf-8",
        )
        (self.root / "audio_takes.json").write_text(
            json.dumps({"takes": {"take_0": {"artifact": {"relative_path": relative, "sha256": "b" * 64}}}}),
            encoding="utf-8",
        )
        issue = reconciliation.inspect_audio_orphans(self.root)["issues"][0]
        self.assertEqual(issue["state"], "cross_reference_mismatch")

    def test_escaping_metadata_path_is_visible_and_never_read(self) -> None:
        outside = self.root.parent / f"{self.root.name}-outside.wav"
        outside.write_bytes(b"outside")
        try:
            (self.root / "chunks.json").write_text(
                json.dumps([{"id": 0, "audio_path": "../" + outside.name}]),
                encoding="utf-8",
            )
            issue = reconciliation.inspect_audio_orphans(self.root)["issues"][0]
            self.assertEqual(issue["state"], "unsafe_path")
            self.assertEqual(outside.read_bytes(), b"outside")
        finally:
            outside.unlink(missing_ok=True)

    def test_symlinked_artifact_is_reported_without_reading_external_bytes(self) -> None:
        outside = self.root.parent / f"{self.root.name}-external.wav"
        outside.write_bytes(b"external-evidence")
        link = self.root / "voicelines" / "takes" / "chunk_0" / "take.wav"
        link.parent.mkdir(parents=True)
        link.symlink_to(outside)
        try:
            issue = reconciliation.inspect_audio_orphans(self.root)["issues"][0]
            self.assertEqual(issue["state"], "unsafe_symlink")
            self.assertEqual(outside.read_bytes(), b"external-evidence")
        finally:
            outside.unlink(missing_ok=True)

    def test_corrupt_metadata_fails_closed_without_advertising_removal(self) -> None:
        artifact = self.root / "voicelines" / "unknown.wav"
        artifact.parent.mkdir()
        artifact.write_bytes(b"must-retain")
        metadata_files = (
            self.root / "chunks.json",
            self.root / "audio_takes.json",
            self.root / "audio_generation_requests" / "request" / "request.json",
            self.root / "audio_take_history" / "operation" / "history.json",
        )
        for metadata in metadata_files:
            with self.subTest(metadata=metadata.relative_to(self.root).as_posix()):
                metadata.parent.mkdir(parents=True, exist_ok=True)
                metadata.write_text("{not-json", encoding="utf-8")
                report = reconciliation.inspect_audio_orphans(self.root)
                self.assertTrue(
                    any(
                        issue["relative_path"] == metadata.relative_to(self.root).as_posix()
                        and issue["state"] == "metadata_unreadable"
                        for issue in report["issues"]
                    )
                )
                self.assertFalse(
                    any(
                        action["kind"] == "remove_orphan"
                        for issue in report["issues"]
                        for action in issue["actions"]
                    )
                )
                self.assertTrue(artifact.is_file())
                metadata.unlink()
                (self.root / "chunks.json").write_text("[]", encoding="utf-8")

    def test_unreadable_metadata_bytes_fail_closed(self) -> None:
        artifact = self.root / "voicelines" / "unknown.wav"
        artifact.parent.mkdir()
        artifact.write_bytes(b"must-retain")
        (self.root / "chunks.json").write_bytes(b"\xff")
        report = reconciliation.inspect_audio_orphans(self.root)
        self.assertEqual(
            [action["kind"] for issue in report["issues"] for action in issue["actions"]],
            ["retain_evidence", "retain_evidence"],
        )
        issue = next(item for item in report["issues"] if item["state"] == "metadata_unreadable")
        receipt = reconciliation.apply_audio_orphan_action(
            self.root,
            issue_id=issue["issue_id"],
            action="retain_evidence",
            expected_issue_fingerprint=issue["issue_fingerprint"],
        )
        self.assertEqual(receipt["result"], "evidence_retained")
        self.assertTrue((self.root / "chunks.json").is_file())

    def test_artifact_swap_to_symlink_during_hashing_fails_closed(self) -> None:
        outside = self.root.parent / f"{self.root.name}-external-race.wav"
        outside.write_bytes(b"external")
        artifact = self.root / "voicelines" / "race.wav"
        artifact.parent.mkdir()
        artifact.write_bytes(b"inside")
        artifact = artifact.resolve()
        real_open = os.open

        def swap_before_open(path: object, flags: int, *args: object) -> int:
            if Path(path) == artifact:
                artifact.unlink()
                artifact.symlink_to(outside)
            return real_open(path, flags, *args)

        try:
            with mock.patch("audio_orphan_reconciliation.os.open", side_effect=swap_before_open):
                report = reconciliation.inspect_audio_orphans(self.root)
            issue = next(item for item in report["issues"] if item["relative_path"] == "voicelines/race.wav")
            self.assertEqual(issue["state"], "artifact_unreadable")
            self.assertEqual([item["kind"] for item in issue["actions"]], ["retain_evidence"])
            self.assertEqual(outside.read_bytes(), b"external")
        finally:
            outside.unlink(missing_ok=True)

    def test_repeated_status_is_deterministic_and_retain_receipt_is_idempotent(self) -> None:
        path = self.root / "voicelines" / ".render.wav.tmp"
        path.parent.mkdir()
        path.write_bytes(b"temporary-evidence")
        first = reconciliation.inspect_audio_orphans(self.root)
        second = reconciliation.inspect_audio_orphans(self.root)
        self.assertEqual(first, second)
        issue = first["issues"][0]
        receipt_one = reconciliation.apply_audio_orphan_action(
            self.root,
            issue_id=issue["issue_id"],
            action="retain_evidence",
            expected_issue_fingerprint=issue["issue_fingerprint"],
        )
        receipt_two = reconciliation.apply_audio_orphan_action(
            self.root,
            issue_id=issue["issue_id"],
            action="retain_evidence",
            expected_issue_fingerprint=issue["issue_fingerprint"],
        )
        self.assertEqual(receipt_one, receipt_two)
        self.assertTrue(path.is_file())

    def test_explicit_remove_has_stale_guard_and_durable_receipt(self) -> None:
        path = self.root / "voicelines" / ".render.wav.tmp"
        path.parent.mkdir()
        path.write_bytes(b"temporary-evidence")
        issue = reconciliation.inspect_audio_orphans(self.root)["issues"][0]
        receipt = reconciliation.apply_audio_orphan_action(
            self.root,
            issue_id=issue["issue_id"],
            action="remove_orphan",
            expected_issue_fingerprint=issue["issue_fingerprint"],
        )
        self.assertFalse(path.exists())
        self.assertEqual(receipt["before_sha256"], hashlib.sha256(b"temporary-evidence").hexdigest())
        self.assertTrue((self.root / receipt["receipt_path"]).is_file())
        repeated = reconciliation.apply_audio_orphan_action(
            self.root,
            issue_id=issue["issue_id"],
            action="remove_orphan",
            expected_issue_fingerprint=issue["issue_fingerprint"],
        )
        self.assertEqual(receipt, repeated)

    def test_partial_or_forged_receipt_is_rejected(self) -> None:
        path = self.root / "voicelines" / ".render.wav.tmp"
        path.parent.mkdir()
        path.write_bytes(b"temporary-evidence")
        issue = reconciliation.inspect_audio_orphans(self.root)["issues"][0]
        receipt_path = (
            self.root
            / reconciliation.ORPHAN_RECEIPT_DIRNAME
            / issue["issue_id"]
            / "retain_evidence.json"
        )
        receipt_path.parent.mkdir(parents=True)
        receipt_path.write_text(
            json.dumps({"issue_fingerprint": issue["issue_fingerprint"]}),
            encoding="utf-8",
        )
        with self.assertRaises(reconciliation.OrphanReconciliationError) as caught:
            reconciliation.apply_audio_orphan_action(
                self.root,
                issue_id=issue["issue_id"],
                action="retain_evidence",
                expected_issue_fingerprint=issue["issue_fingerprint"],
            )
        self.assertEqual(caught.exception.code, "audio_orphan_receipt_invalid")
        self.assertTrue(path.is_file())

    def test_complete_receipt_with_forged_result_is_rejected(self) -> None:
        path = self.root / "voicelines" / ".render.wav.tmp"
        path.parent.mkdir()
        path.write_bytes(b"temporary-evidence")
        issue = reconciliation.inspect_audio_orphans(self.root)["issues"][0]
        receipt = reconciliation.apply_audio_orphan_action(
            self.root,
            issue_id=issue["issue_id"],
            action="retain_evidence",
            expected_issue_fingerprint=issue["issue_fingerprint"],
        )
        receipt["result"] = "orphan_removed"
        receipt["record_fingerprint"] = reconciliation.fingerprint_value(
            {key: value for key, value in receipt.items() if key != "record_fingerprint"}
        )
        (self.root / receipt["receipt_path"]).write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaises(reconciliation.OrphanReconciliationError) as caught:
            reconciliation.apply_audio_orphan_action(
                self.root,
                issue_id=issue["issue_id"],
                action="retain_evidence",
                expected_issue_fingerprint=issue["issue_fingerprint"],
            )
        self.assertEqual(caught.exception.code, "audio_orphan_receipt_invalid")

    def test_remove_crash_restarts_to_one_truthful_receipt_and_delete(self) -> None:
        path = self.root / "voicelines" / ".render.wav.tmp"
        path.parent.mkdir()
        path.write_bytes(b"temporary-evidence")
        issue = reconciliation.inspect_audio_orphans(self.root)["issues"][0]
        with mock.patch.dict(
            os.environ,
            {
                "ALEXANDRIA_TEST_AUDIO_CRASH_INJECTION": "1",
                "ALEXANDRIA_AUDIO_CRASH_POINT": "invalidation:after",
            },
            clear=False,
        ):
            with self.assertRaises(reconciliation.InjectedAudioCrash):
                reconciliation.apply_audio_orphan_action(
                    self.root,
                    issue_id=issue["issue_id"],
                    action="remove_orphan",
                    expected_issue_fingerprint=issue["issue_fingerprint"],
                )
        repaired = reconciliation.reconcile_audio_transitions(self.root)
        self.assertEqual(repaired["repaired_count"], 1)
        self.assertFalse(path.exists())
        receipt = reconciliation.apply_audio_orphan_action(
            self.root,
            issue_id=issue["issue_id"],
            action="remove_orphan",
            expected_issue_fingerprint=issue["issue_fingerprint"],
        )
        self.assertEqual(receipt["result"], "orphan_removed")
        self.assertEqual(receipt["transaction_id"], f"orphan-{issue['issue_id']}-remove_orphan")

    def test_concurrent_remove_calls_converge_on_one_transaction_receipt(self) -> None:
        path = self.root / "voicelines" / ".render.wav.tmp"
        path.parent.mkdir()
        path.write_bytes(b"temporary-evidence")
        issue = reconciliation.inspect_audio_orphans(self.root)["issues"][0]

        def remove() -> dict[str, object]:
            return reconciliation.apply_audio_orphan_action(
                self.root,
                issue_id=issue["issue_id"],
                action="remove_orphan",
                expected_issue_fingerprint=issue["issue_fingerprint"],
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            receipts = list(executor.map(lambda _: remove(), range(2)))
        self.assertEqual(receipts[0], receipts[1])
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
