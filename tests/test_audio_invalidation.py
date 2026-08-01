from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path

from approved_audio import approved_audio_lock_fields
from audio_artifacts import sha256_file, validate_audio_file
from audio_invalidation import (
    AUDIO_INVALIDATION_SCHEMA_VERSION,
    affected_voice_dependency_speakers,
    apply_project_audio_invalidation,
    apply_speaker_audio_dependency_change,
    attach_audio_backup_evidence,
    build_audio_validity_record,
    normalize_audio_invalidation,
    undo_project_audio_invalidation,
)
from audio_takes import build_take_record, load_registry, register_take


def write_wav(path: Path, *, frames: int = 48000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        samples = bytearray()
        for index in range(frames):
            value = 1200 if (index // 120) % 2 == 0 else -1200
            samples.extend(int(value).to_bytes(2, "little", signed=True))
        handle.writeframes(bytes(samples))


def register_current_take(root: Path, chunk: dict, *, take_id: str) -> dict:
    relative = f"voicelines/takes/chunk_{chunk['id']}/{take_id}.wav"
    path = root / relative
    write_wav(path)
    validation = validate_audio_file(path)
    record = build_take_record(
        take_id=take_id,
        chunk_key_value=f"chunk:{chunk['id']}",
        chunk_index=0,
        kind="raw",
        source_take_id=None,
        root_take_id=None,
        artifact={
            "relative_path": relative,
            "sha256": validation["sha256"],
            "size_bytes": validation["size_bytes"],
            "duration_ms": validation["duration_ms"],
            "format": "wav",
            "sample_rate": 24000,
            "sample_count": 48000,
            "channels": 1,
        },
        authored={
            "text": chunk["text"],
            "speaker": chunk["speaker"],
            "direction": chunk.get("instruct", ""),
        },
        voice={"resolved_speaker": chunk["speaker"]},
        generation={"audio_fingerprint": chunk["audio_fingerprint"]},
        synthesis={},
    )
    take, registry = register_take(root, chunks=[chunk], record=record)
    chunk.update(
        {
            "current_take_id": take["take_id"],
            "take_record_fingerprint": take["record_fingerprint"],
            "take_registry_fingerprint": registry["registry_fingerprint"],
        }
    )
    return take


class AudioInvalidationTests(unittest.TestCase):
    def test_voice_dependency_change_propagates_through_aliases(self) -> None:
        before = {
            "THE DOCTOR": {"type": "custom", "voice": "Ryan"},
            "DOCTOR": {"alias_of": "THE DOCTOR"},
            "NARRATOR": {"type": "custom", "voice": "Aiden"},
        }
        after = {
            **before,
            "THE DOCTOR": {"type": "custom", "voice": "Aiden"},
        }

        self.assertEqual(
            affected_voice_dependency_speakers(before, after),
            ["DOCTOR", "THE DOCTOR"],
        )

    def test_speaker_dependency_change_preserves_approved_audio_and_undoes_raw_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ordinary_audio = root / "voicelines" / "ordinary.wav"
            locked_audio = root / "voicelines" / "locked.wav"
            ordinary_audio.parent.mkdir(parents=True)
            ordinary_audio.write_bytes(b"ordinary-audio")
            locked_audio.write_bytes(b"approved-audio")
            ordinary = {
                "id": 1,
                "speaker": "DOCTOR",
                "text": "Run.",
                "instruct": "Urgently.",
                "status": "done",
                "audio_state": "current",
                "audio_path": "voicelines/ordinary.wav",
            }
            locked = {
                "id": 2,
                "speaker": "DOCTOR",
                "text": "Stay.",
                "instruct": "Quietly.",
                "status": "done",
                "audio_state": "current",
                "audio_path": "voicelines/locked.wav",
            }
            locked.update(
                approved_audio_lock_fields(
                    chunk=locked,
                    promotion_id="promotion-fixture",
                    candidate_id="candidate-fixture",
                    source_round_id="round-fixture",
                    direct_placement_tier="strict_clean",
                    source_audio_path="source.wav",
                    source_audio_sha256="a" * 64,
                    manifest_path="manifest.json",
                    installed_at_utc="2026-08-01T00:00:00Z",
                    reference_bank_eligible=True,
                )
            )
            chunks_path = root / "chunks.json"
            chunks_path.write_text(
                json.dumps([ordinary, locked]),
                encoding="utf-8",
            )
            voice_path = root / "voice_config.json"
            voice_path.write_text(
                json.dumps({"DOCTOR": {"voice": "Ryan"}}),
                encoding="utf-8",
            )
            imported = root / "clone_voices" / "doctor.wav"

            record = apply_speaker_audio_dependency_change(
                project_root=root,
                operation_id="voice_dependency_fixture",
                operation="voice_library_assign",
                at_utc="2026-08-01T00:00:00Z",
                speakers={"DOCTOR"},
                reason="Voice changed.",
                changes={
                    voice_path: {"DOCTOR": {"voice": "Aiden"}},
                },
                byte_changes={imported: b"reference-audio"},
                dependency_kind="production_voice",
            )

            updated = json.loads(chunks_path.read_text(encoding="utf-8"))
            self.assertEqual(
                record["audio_invalidation"]["affected_chunk_ids"],
                [1],
            )
            self.assertEqual(updated[0]["audio_state"], "stale")
            self.assertEqual(updated[1], locked)
            self.assertFalse(ordinary_audio.exists())
            self.assertEqual(locked_audio.read_bytes(), b"approved-audio")
            self.assertEqual(imported.read_bytes(), b"reference-audio")

            undo_project_audio_invalidation(
                project_root=root,
                operation_id="voice_dependency_fixture",
                undone_at_utc="2026-08-01T01:00:00Z",
            )
            self.assertEqual(
                json.loads(chunks_path.read_text(encoding="utf-8")),
                [ordinary, locked],
            )
            self.assertEqual(ordinary_audio.read_bytes(), b"ordinary-audio")
            self.assertEqual(locked_audio.read_bytes(), b"approved-audio")
            self.assertFalse(imported.exists())

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

    def test_take_managed_project_invalidation_retains_audio_and_restores_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chunk = {
                "id": 1,
                "speaker": "DOCTOR",
                "text": "Run.",
                "instruct": "Urgently.",
                "status": "done",
                "audio_state": "current",
                "audio_fingerprint": "f" * 64,
                "audio_sha256": None,
                "audio_size_bytes": None,
                "audio_duration_ms": None,
                "audio_format": "wav",
            }
            take = register_current_take(
                root,
                chunk,
                take_id="take_project_invalidation",
            )
            path = root / take["artifact"]["relative_path"]
            chunk.update(
                {
                    "audio_path": take["artifact"]["relative_path"],
                    "audio_sha256": take["artifact"]["sha256"],
                    "audio_size_bytes": take["artifact"]["size_bytes"],
                    "audio_duration_ms": take["artifact"]["duration_ms"],
                }
            )
            chunks_path = root / "chunks.json"
            chunks_path.write_text(json.dumps([chunk]), encoding="utf-8")
            before_chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
            before_registry = load_registry(root)
            before_bytes = path.read_bytes()
            config_path = root / "voice_config.json"
            before_config = b'{"DOCTOR":{"voice":"old"}}'
            config_path.write_bytes(b'{"DOCTOR":{"voice":"new"}}')

            record = apply_project_audio_invalidation(
                project_root=root,
                operation_id="take_project_dependency_fixture",
                operation="voice_save",
                at_utc="2026-08-01T10:00:00Z",
                speakers={"DOCTOR"},
                reason="production voice changed",
                dependency_before={config_path: before_config},
            )
            updated = json.loads(chunks_path.read_text(encoding="utf-8"))[0]
            registry = load_registry(root)
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), before_bytes)
            self.assertEqual(record["audio_backups"], [])
            self.assertEqual(updated["stale_audio_path"], take["artifact"]["relative_path"])
            self.assertIsNone(updated["current_take_id"])
            self.assertIsNone(registry["chunks"]["chunk:1"]["current_take_id"])
            self.assertFalse(registry["takes"][take["take_id"]]["current"])
            invalidation = record["audio_invalidation"]["invalidated_chunks"][0]
            self.assertEqual(invalidation["preserved_take_id"], take["take_id"])
            self.assertTrue(invalidation["preserved_immutable_take"])

            undo_project_audio_invalidation(
                project_root=root,
                operation_id="take_project_dependency_fixture",
                undone_at_utc="2026-08-01T11:00:00Z",
            )
            self.assertEqual(
                json.loads(chunks_path.read_text(encoding="utf-8")),
                before_chunks,
            )
            self.assertEqual(load_registry(root), before_registry)
            self.assertEqual(path.read_bytes(), before_bytes)

    def test_take_managed_generic_invalidation_retains_audio_and_undoes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chunk = {
                "id": 1,
                "speaker": "DOCTOR",
                "text": "Run.",
                "instruct": "Urgently.",
                "status": "done",
                "audio_state": "current",
                "audio_fingerprint": "f" * 64,
                "audio_format": "wav",
            }
            take = register_current_take(
                root,
                chunk,
                take_id="take_generic_invalidation",
            )
            path = root / take["artifact"]["relative_path"]
            chunk.update(
                {
                    "audio_path": take["artifact"]["relative_path"],
                    "audio_sha256": take["artifact"]["sha256"],
                    "audio_size_bytes": take["artifact"]["size_bytes"],
                    "audio_duration_ms": take["artifact"]["duration_ms"],
                }
            )
            chunks_path = root / "chunks.json"
            chunks_path.write_text(json.dumps([chunk]), encoding="utf-8")
            before_chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
            before_registry = load_registry(root)
            before_bytes = path.read_bytes()
            voice_path = root / "voice_config.json"
            voice_path.write_text(json.dumps({"DOCTOR": {"voice": "Ryan"}}))

            record = apply_speaker_audio_dependency_change(
                project_root=root,
                operation_id="take_generic_dependency_fixture",
                operation="voice_library_assign",
                at_utc="2026-08-01T10:00:00Z",
                speakers={"DOCTOR"},
                reason="Voice changed.",
                changes={voice_path: {"DOCTOR": {"voice": "Aiden"}}},
                dependency_kind="production_voice",
            )
            updated = json.loads(chunks_path.read_text(encoding="utf-8"))[0]
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), before_bytes)
            self.assertEqual(record["audio_backups"], [])
            self.assertIsNone(updated["current_take_id"])
            self.assertEqual(updated["stale_audio_path"], take["artifact"]["relative_path"])

            undo_project_audio_invalidation(
                project_root=root,
                operation_id="take_generic_dependency_fixture",
                undone_at_utc="2026-08-01T11:00:00Z",
            )
            self.assertEqual(
                json.loads(chunks_path.read_text(encoding="utf-8")),
                before_chunks,
            )
            self.assertEqual(load_registry(root), before_registry)
            self.assertEqual(path.read_bytes(), before_bytes)

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
