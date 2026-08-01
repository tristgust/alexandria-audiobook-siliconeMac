from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from audio_generation_lifecycle import (
    AudioGenerationLifecycleError,
    claim_request,
    completed_segment_artifact,
    finalize_request,
    guard_publication,
    load_request,
    pending_replacement,
    prepare_request,
    reconcile_interrupted_requests,
    record_chunk_completed,
    record_chunk_started,
    record_segment_completed,
    record_segment_started,
    request_cancel,
    request_context,
    segment_output_path,
    should_cancel,
)


def manifest(
    *,
    dependency: str = "request-dependency",
    chunk_count: int = 1,
    segment_count: int = 2,
    mode: str = "parallel",
) -> dict:
    chunks = []
    for chunk_index in range(chunk_count):
        segments = []
        cursor = 0
        for segment_index in range(segment_count):
            length = 10 + segment_index
            segments.append(
                {
                    "segment_id": f"segment_{segment_index:04d}",
                    "segment_index": segment_index,
                    "source_start": cursor,
                    "source_end": cursor + length,
                    "generation_text_sha256": f"text-{chunk_index}-{segment_index}",
                    "dependency_fingerprint": f"segment-dependency-{chunk_index}",
                }
            )
            cursor += length
        chunks.append(
            {
                "chunk_key": f"chunk:{chunk_index}",
                "index": chunk_index,
                "chunk_id": chunk_index,
                "dependency_fingerprint": f"chunk-dependency-{chunk_index}",
                "segment_plan_fingerprint": f"plan-{chunk_index}",
                "segments": segments,
            }
        )
    return {
        "mode": mode,
        "operation_mode": "missing_stale",
        "generation_seed": 77,
        "plan_fingerprint": "produce-plan",
        "chunks_fingerprint": "chunks-fingerprint",
        "dependency_fingerprint": dependency,
        "chunks": chunks,
    }


class AudioGenerationLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def prepare_and_claim(self, value: dict | None = None):
        prepared = prepare_request(
            self.root,
            value or manifest(),
            operation_id="operation-fixture",
            at_utc="2026-08-01T10:00:00Z",
        )
        record = claim_request(
            self.root,
            prepared["record"]["request_id"],
            expected_request_fingerprint=prepared["record"]["request_fingerprint"],
            owner_process_id=123,
            at_utc="2026-08-01T10:01:00Z",
        )
        return prepared, record

    def complete_segment(
        self,
        record: dict,
        *,
        chunk_key: str = "chunk:0",
        segment_id: str = "segment_0000",
    ) -> dict:
        token = record["owner_token"]
        segment = record["progress"][chunk_key]["segments"][segment_id]
        record_segment_started(
            self.root,
            record["request_id"],
            token,
            chunk_key,
            segment_id,
            expected_dependency_fingerprint=segment["dependency_fingerprint"],
            at_utc="2026-08-01T10:02:00Z",
        )
        path = segment_output_path(
            self.root,
            record["request_id"],
            chunk_key,
            segment_id,
        )
        path.write_bytes(f"audio-{chunk_key}-{segment_id}".encode())
        return record_segment_completed(
            self.root,
            record["request_id"],
            token,
            chunk_key,
            segment_id,
            expected_dependency_fingerprint=segment["dependency_fingerprint"],
            artifact_path=path,
            sample_rate=24000,
            sample_count=2400,
            at_utc="2026-08-01T10:03:00Z",
        )

    def complete_chunk(self, record: dict, chunk_key: str = "chunk:0") -> dict:
        current = load_request(self.root, record["request_id"])
        for segment_id in current["progress"][chunk_key]["segments"]:
            current = self.complete_segment(
                current,
                chunk_key=chunk_key,
                segment_id=segment_id,
            )
        current = load_request(self.root, record["request_id"])
        guard_publication(
            self.root,
            current["request_id"],
            record["owner_token"],
            chunk_key,
            current_request_fingerprint=current["request_fingerprint"],
            current_chunk_dependency_fingerprint=current["progress"][chunk_key][
                "dependency_fingerprint"
            ],
        )
        return record_chunk_completed(
            self.root,
            current["request_id"],
            record["owner_token"],
            chunk_key,
            canonical_artifact={
                "audio_path": f"voicelines/{chunk_key}.wav",
                "audio_sha256": "a" * 64,
            },
            at_utc="2026-08-01T10:04:00Z",
        )

    def test_prepare_is_deterministic_and_duplicate_does_not_dispatch_twice(self) -> None:
        first = prepare_request(self.root, manifest(), operation_id="first")
        second = prepare_request(self.root, manifest(), operation_id="second")
        self.assertTrue(first["created"])
        self.assertFalse(first["duplicate"])
        self.assertTrue(first["dispatch_required"])
        self.assertFalse(second["created"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(
            first["record"]["request_id"],
            second["record"]["request_id"],
        )

    def test_segment_artifact_survives_interruption_and_is_reused_after_resume(self) -> None:
        _prepared, running = self.prepare_and_claim()
        running = self.complete_segment(running)
        artifact = completed_segment_artifact(
            self.root,
            running["request_id"],
            "chunk:0",
            "segment_0000",
            expected_dependency_fingerprint="segment-dependency-0",
        )
        self.assertIsNotNone(artifact)
        old_token = running["owner_token"]

        changed = reconcile_interrupted_requests(
            self.root,
            at_utc="2026-08-01T10:05:00Z",
        )
        self.assertEqual(changed[0]["state"], "resumable")
        resumed = claim_request(
            self.root,
            running["request_id"],
            expected_request_fingerprint=running["request_fingerprint"],
            owner_process_id=456,
            at_utc="2026-08-01T10:06:00Z",
        )
        self.assertNotEqual(resumed["owner_token"], old_token)
        self.assertEqual(
            resumed["progress"]["chunk:0"]["segments"]["segment_0000"]["state"],
            "completed",
        )
        self.assertEqual(
            resumed["progress"]["chunk:0"]["segments"]["segment_0001"]["state"],
            "pending",
        )
        self.assertIsNotNone(
            completed_segment_artifact(
                self.root,
                resumed["request_id"],
                "chunk:0",
                "segment_0000",
                expected_dependency_fingerprint="segment-dependency-0",
            )
        )

    def test_changed_request_or_segment_dependency_is_rejected(self) -> None:
        _prepared, running = self.prepare_and_claim()
        with self.assertRaisesRegex(
            AudioGenerationLifecycleError,
            "identity changed",
        ):
            guard_publication(
                self.root,
                running["request_id"],
                running["owner_token"],
                "chunk:0",
                current_request_fingerprint="changed-request",
                current_chunk_dependency_fingerprint="chunk-dependency-0",
            )
        with self.assertRaisesRegex(
            AudioGenerationLifecycleError,
            "dependency changed",
        ):
            record_segment_started(
                self.root,
                running["request_id"],
                running["owner_token"],
                "chunk:0",
                "segment_0000",
                expected_dependency_fingerprint="changed-segment",
            )

    def test_cancellation_is_terminal_and_late_worker_cannot_publish(self) -> None:
        _prepared, running = self.prepare_and_claim()
        cancelled = request_cancel(
            self.root,
            running["request_id"],
            at_utc="2026-08-01T10:02:00Z",
        )
        self.assertEqual(cancelled["state"], "cancelling")
        self.assertTrue(should_cancel(self.root, running["request_id"], running["owner_token"]))
        path = segment_output_path(
            self.root,
            running["request_id"],
            "chunk:0",
            "segment_0000",
        )
        path.write_bytes(b"late-audio")
        with self.assertRaisesRegex(AudioGenerationLifecycleError, "Cancelled"):
            record_segment_completed(
                self.root,
                running["request_id"],
                running["owner_token"],
                "chunk:0",
                "segment_0000",
                expected_dependency_fingerprint="segment-dependency-0",
                artifact_path=path,
                sample_rate=24000,
                sample_count=2400,
            )
        terminal = finalize_request(
            self.root,
            running["request_id"],
            running["owner_token"],
            at_utc="2026-08-01T10:03:00Z",
        )
        self.assertEqual(terminal["state"], "cancelled")
        self.assertIsNotNone(terminal["terminal_receipt_fingerprint"])

    def test_stale_owner_token_cannot_mutate_resumed_request(self) -> None:
        _prepared, running = self.prepare_and_claim()
        old_token = running["owner_token"]
        reconcile_interrupted_requests(self.root)
        resumed = claim_request(
            self.root,
            running["request_id"],
            expected_request_fingerprint=running["request_fingerprint"],
        )
        with self.assertRaisesRegex(AudioGenerationLifecycleError, "no longer owns"):
            record_chunk_started(
                self.root,
                resumed["request_id"],
                old_token,
                "chunk:0",
            )
        context = request_context(
            self.root,
            resumed["request_id"],
            resumed["owner_token"],
            "chunk:0",
        )
        self.assertEqual(context["owner_token"], resumed["owner_token"])

    def test_request_succeeds_only_when_every_planned_chunk_is_complete(self) -> None:
        _prepared, running = self.prepare_and_claim(
            manifest(chunk_count=2, segment_count=1)
        )
        self.complete_chunk(running, "chunk:0")
        partial = finalize_request(
            self.root,
            running["request_id"],
            running["owner_token"],
            at_utc="2026-08-01T10:10:00Z",
        )
        self.assertEqual(partial["state"], "failed")
        self.assertEqual(partial["terminal_reason"], "partial_completion")
        self.assertEqual(partial["terminal_summary"]["completed"], 1)
        self.assertEqual(partial["terminal_summary"]["pending"], 1)

    def test_completed_request_is_idempotent_and_never_reclaimed(self) -> None:
        _prepared, running = self.prepare_and_claim(manifest(segment_count=1))
        self.complete_chunk(running)
        terminal = finalize_request(
            self.root,
            running["request_id"],
            running["owner_token"],
        )
        self.assertEqual(terminal["state"], "succeeded")
        duplicate = prepare_request(self.root, manifest(segment_count=1))
        self.assertTrue(duplicate["duplicate"])
        self.assertTrue(duplicate["terminal"])
        self.assertFalse(duplicate["dispatch_required"])
        claimed = claim_request(
            self.root,
            terminal["request_id"],
            expected_request_fingerprint=terminal["request_fingerprint"],
        )
        self.assertEqual(claimed["state"], "succeeded")
        self.assertIsNone(claimed["owner_token"])

    def test_replacement_cancels_predecessor_and_waits_for_terminal_state(self) -> None:
        _prepared, running = self.prepare_and_claim(manifest(dependency="first"))
        replacement = prepare_request(
            self.root,
            manifest(dependency="second"),
            replace_active=True,
            max_pending=1,
            at_utc="2026-08-01T10:02:00Z",
        )
        self.assertEqual(replacement["record"]["state"], "queued_replacement")
        predecessor = load_request(self.root, running["request_id"])
        self.assertEqual(predecessor["state"], "cancelling")
        self.assertEqual(
            predecessor["replacement_request_id"],
            replacement["record"]["request_id"],
        )
        with self.assertRaisesRegex(
            AudioGenerationLifecycleError,
            "prior request is terminal",
        ):
            claim_request(
                self.root,
                replacement["record"]["request_id"],
                expected_request_fingerprint=replacement["record"][
                    "request_fingerprint"
                ],
            )
        terminal = finalize_request(
            self.root,
            running["request_id"],
            running["owner_token"],
            at_utc="2026-08-01T10:03:00Z",
        )
        self.assertEqual(terminal["state"], "replaced")
        pending = pending_replacement(self.root, terminal["request_id"])
        self.assertEqual(
            pending["request_id"],
            replacement["record"]["request_id"],
        )
        claimed = claim_request(
            self.root,
            pending["request_id"],
            expected_request_fingerprint=pending["request_fingerprint"],
        )
        self.assertEqual(claimed["state"], "running")

    def test_replacement_immediately_terminalizes_unclaimed_predecessor(self) -> None:
        predecessor = prepare_request(
            self.root,
            manifest(dependency="first"),
        )
        replacement = prepare_request(
            self.root,
            manifest(dependency="second"),
            replace_active=True,
        )
        prior = load_request(
            self.root,
            predecessor["record"]["request_id"],
        )
        self.assertEqual(prior["state"], "replaced")
        self.assertEqual(
            prior["replacement_request_id"],
            replacement["record"]["request_id"],
        )
        self.assertIsNotNone(prior["terminal_receipt_fingerprint"])
        self.assertEqual(replacement["record"]["state"], "prepared")
        self.assertTrue(replacement["dispatch_required"])

    def test_bounded_replacement_queue_rejects_second_pending_request(self) -> None:
        _prepared, _running = self.prepare_and_claim(manifest(dependency="first"))
        prepare_request(
            self.root,
            manifest(dependency="second"),
            replace_active=True,
            max_pending=1,
        )
        with self.assertRaisesRegex(AudioGenerationLifecycleError, "queue is full"):
            prepare_request(
                self.root,
                manifest(dependency="third"),
                replace_active=True,
                max_pending=1,
            )


if __name__ == "__main__":
    unittest.main()
