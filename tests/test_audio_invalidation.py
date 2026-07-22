from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from audio_invalidation import (
    AUDIO_INVALIDATION_SCHEMA_VERSION,
    apply_project_audio_invalidation,
    attach_audio_backup_evidence,
    build_audio_validity_record,
    normalize_audio_invalidation,
    undo_project_audio_invalidation,
)


class AudioInvalidationTests(unittest.TestCase):
    def test_normalized_shape_is_deterministic_and_versioned(self) -> None:
        source = {
            "chunk_id": 3,
            "speaker": "DOCTOR",
            "audio_path": "audio/chunk_3.wav",
            "reason": "voice changed",
        }
        first = normalize_audio_invalidation(
            source,
            operation_id="operation_fixture",
            operation="voice_save",
            default_reason="dependency changed",
        )
        second = normalize_audio_invalidation(
            source,
            operation_id="operation_fixture",
            operation="voice_save",
            default_reason="dependency changed",
        )
        self.assertEqual(first, second)
        self.assertEqual(first["schema_version"], AUDIO_INVALIDATION_SCHEMA_VERSION)
        self.assertEqual(first["canonical_audio_path"], "audio/chunk_3.wav")
        self.assertFalse(first["undo_available"])
        self.assertTrue(first["invalidation_id"].startswith("audio_invalid_"))

    def test_validity_record_has_one_canonical_shape(self) -> None:
        record = build_audio_validity_record(
            operation_id="operation_fixture",
            operation="annotated_script_import",
            at_utc="2026-07-22T00:00:00Z",
            invalidations=[
                {
                    "chunk_id": 4,
                    "speaker": "NARRATOR",
                    "audio_path": "audio/chunk_4.wav",
                }
            ],
            note="Prior audio is stale.",
            default_reason="annotated_script_replaced",
        )
        self.assertTrue(record["stale"])
        self.assertEqual(record["schema_version"], AUDIO_INVALIDATION_SCHEMA_VERSION)
        self.assertEqual(len(record["invalidation_fingerprint"]), 64)
        self.assertFalse(record["undo_available"])
        self.assertEqual(
            record["invalidated_chunks"][0]["reason"],
            "annotated_script_replaced",
        )

    def test_project_dependency_change_invalidates_matching_audio_and_undoes_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio = root / "voicelines" / "chunk_1.wav"
            audio.parent.mkdir(parents=True)
            audio.write_bytes(b"audio-fixture")
            chunks = [
                {
                    "id": 1,
                    "speaker": "DOCTOR",
                    "text": "Run.",
                    "status": "done",
                    "audio_path": "voicelines/chunk_1.wav",
                    "audio_state": "current",
                    "audio_fingerprint": "f" * 64,
                },
                {
                    "id": 2,
                    "speaker": "NARRATOR",
                    "text": "He ran.",
                    "status": "pending",
                    "audio_path": None,
                },
            ]
            chunks_path = root / "chunks.json"
            chunks_path.write_text(json.dumps(chunks), encoding="utf-8")
            config_path = root / "voice_config.json"
            before_config = b'{"DOCTOR":{"voice":"old"}}'
            config_path.write_bytes(before_config)
            config_path.write_bytes(b'{"DOCTOR":{"voice":"new"}}')

            record = apply_project_audio_invalidation(
                project_root=root,
                operation_id="operation_dependency_fixture",
                operation="voice_save",
                at_utc="2026-07-22T00:00:00Z",
                speakers={"DOCTOR"},
                reason="production voice changed",
                dependency_before={config_path: before_config},
            )
            updated = json.loads(chunks_path.read_text(encoding="utf-8"))
            self.assertEqual(updated[0]["audio_state"], "stale")
            self.assertIsNone(updated[0]["audio_path"])
            self.assertFalse(audio.exists())
            self.assertEqual(len(record["audio_backups"]), 1)

            undone = undo_project_audio_invalidation(
                project_root=root,
                operation_id="operation_dependency_fixture",
                undone_at_utc="2026-07-22T01:00:00Z",
            )
            self.assertEqual(undone["status"], "undone")
            self.assertEqual(config_path.read_bytes(), before_config)
            self.assertEqual(
                json.loads(chunks_path.read_text(encoding="utf-8")),
                chunks,
            )
            self.assertEqual(audio.read_bytes(), b"audio-fixture")

    def test_backup_evidence_enables_exact_undo(self) -> None:
        record = build_audio_validity_record(
            operation_id="operation_fixture",
            operation="speaker_rename",
            at_utc="2026-07-22T00:00:00Z",
            invalidations=[
                {
                    "old_chunk_id": 1,
                    "new_chunk_id": 1,
                    "audio_path": "audio/chunk_1.wav",
                }
            ],
            note="Audio moved to backup.",
            default_reason="speaker changed",
        )
        updated = attach_audio_backup_evidence(
            record,
            {
                "audio/chunk_1.wav": {
                    "original_path": "audio/chunk_1.wav",
                    "backup_path": "speaker_management_history/op/audio/chunk_1.wav",
                    "sha256": "a" * 64,
                    "size_bytes": 1234,
                }
            },
        )
        item = updated["invalidated_chunks"][0]
        self.assertTrue(item["undo_available"])
        self.assertTrue(updated["undo_available"])
        self.assertEqual(item["audio_sha256"], "a" * 64)
        self.assertEqual(item["audio_size_bytes"], 1234)


if __name__ == "__main__":
    unittest.main()
