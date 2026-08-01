from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import wave
from unittest.mock import patch

from audio_artifacts import sha256_file, validate_audio_file
from audio_crash_reconciliation import InjectedAudioCrash, reconcile_audio_transitions
from audio_takes import (
    AudioTakeError,
    apply_cleanup,
    apply_delete,
    build_take_record,
    cleanup_impact,
    delete_impact,
    load_registry,
    materialize_registry,
    new_take_id,
    promote_take,
    public_chunk_takes,
    register_take,
    registry_path,
    registry_view,
    set_take_kept,
    take_chunk_audio_fields,
    undo_operation,
)


def write_tone(path: Path, *, frames: int = 24000, rate: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = bytearray()
    for index in range(frames):
        value = int(2500 * ((index % 60) / 30.0 - 1.0))
        samples.extend(int(value).to_bytes(2, byteorder="little", signed=True))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(samples))


class AudioTakeRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.voicelines = self.root / "voicelines"
        self.current = self.voicelines / "legacy-current.wav"
        self.stale = self.voicelines / "legacy-stale.wav"
        write_tone(self.current, frames=24000)
        write_tone(self.stale, frames=18000)
        current_info = validate_audio_file(self.current)
        self.chunks = [
            {
                "id": 7,
                "speaker": "NARRATOR",
                "text": "The archived line remains exact.",
                "instruct": "Calm and measured.",
                "status": "done",
                "audio_state": "current",
                "audio_path": "voicelines/legacy-current.wav",
                "stale_audio_path": "voicelines/legacy-stale.wav",
                "audio_sha256": current_info["sha256"],
                "audio_size_bytes": current_info["size_bytes"],
                "audio_duration_ms": current_info["duration_ms"],
                "audio_format": "wav",
                "audio_fingerprint": "f" * 64,
                "generated_at_utc": "2026-07-01T12:00:00Z",
                "generation_provenance": {
                    "model_id": "fixture-model",
                    "voice_method": "custom",
                },
            }
        ]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def artifact(self, relative_path: str) -> dict:
        path = self.root / relative_path
        info = validate_audio_file(path)
        return {
            "relative_path": relative_path,
            "sha256": info["sha256"],
            "size_bytes": info["size_bytes"],
            "duration_ms": info["duration_ms"],
            "format": path.suffix.lstrip("."),
            "sample_rate": 24000,
            "sample_count": round(info["duration_ms"] * 24),
            "channels": 1,
        }

    def record(
        self,
        relative_path: str,
        *,
        take_id: str | None = None,
        kind: str = "raw",
        source_take_id: str | None = None,
        root_take_id: str | None = None,
        created_at_utc: str | None = None,
        processing: dict | None = None,
    ) -> dict:
        return build_take_record(
            take_id=take_id or new_take_id(kind=kind),
            chunk_key_value="chunk:7",
            chunk_index=0,
            kind=kind,
            source_take_id=source_take_id,
            root_take_id=root_take_id,
            artifact=self.artifact(relative_path),
            authored={
                "text": self.chunks[0]["text"],
                "text_sha256": "a" * 64,
                "speaker": "NARRATOR",
                "direction": "Calm and measured.",
            },
            voice={
                "resolved_speaker": "NARRATOR",
                "configuration": {"type": "custom", "voice": "Ryan"},
            },
            generation={
                "audio_fingerprint": "b" * 64,
                "request_id": "audio_request_fixture",
                "request_fingerprint": "c" * 64,
                "seed": 42,
                "provenance": {"model_id": "fixture-model"},
                "chunk_audio_fields": {
                    "generation_provenance": {"model_id": "fixture-model"},
                    "generated_at_utc": created_at_utc or "2026-08-01T10:00:00Z",
                    "synthesis_seam_receipt_fingerprint": "d" * 64,
                },
            },
            synthesis={
                "seam_receipt": {
                    "receipt_fingerprint": "d" * 64,
                    "segment_results": [
                        {
                            "segment_id": "segment_0000",
                            "source_start": 0,
                            "source_end": len(self.chunks[0]["text"]),
                        }
                    ],
                }
            },
            processing=processing,
            created_at_utc=created_at_utc,
        )

    def add_audio(self, name: str, *, frames: int = 22000) -> str:
        relative = f"voicelines/takes/chunk_7/{name}.wav"
        write_tone(self.root / relative, frames=frames)
        return relative

    def test_read_only_view_represents_legacy_current_and_stale_without_writing(self) -> None:
        view = registry_view(self.root, self.chunks)
        self.assertFalse(registry_path(self.root).exists())
        entry = view["chunks"]["chunk:7"]
        self.assertEqual(len(entry["take_ids"]), 2)
        current = view["takes"][entry["current_take_id"]]
        self.assertTrue(current["current"])
        self.assertTrue(current["legacy"])
        self.assertEqual(current["artifact"]["relative_path"], self.chunks[0]["audio_path"])
        public = public_chunk_takes(self.root, self.chunks, index=0)
        self.assertEqual(public["take_count"], 2)
        self.assertEqual(public["takes"][0]["audio"]["url"], "/voicelines/legacy-current.wav")

    def test_materialization_preserves_view_fingerprint(self) -> None:
        before = registry_view(self.root, self.chunks)
        written = materialize_registry(self.root, self.chunks)
        self.assertEqual(before["registry_fingerprint"], written["registry_fingerprint"])
        self.assertTrue(registry_path(self.root).is_file())

    def test_register_raw_take_preserves_prior_files_and_makes_newest_current(self) -> None:
        relative = self.add_audio("new-raw")
        record = self.record(relative)
        take, registry = register_take(
            self.root,
            chunks=self.chunks,
            record=record,
        )
        self.assertTrue(self.current.is_file())
        self.assertTrue(self.stale.is_file())
        self.assertTrue((self.root / relative).is_file())
        self.assertEqual(registry["chunks"]["chunk:7"]["current_take_id"], take["take_id"])
        self.assertEqual(registry["chunks"]["chunk:7"]["take_ids"][0], take["take_id"])
        legacy = [value for value in registry["takes"].values() if value["legacy"]]
        self.assertEqual(len(legacy), 2)
        self.assertFalse(any(value["current"] for value in legacy))
        fields = take_chunk_audio_fields(take)
        self.assertEqual(fields["audio_path"], relative)
        self.assertEqual(fields["current_take_id"], take["take_id"])
        self.assertEqual(fields["audio_fingerprint"], "b" * 64)

    def test_child_rendition_links_to_raw_root_and_becomes_current(self) -> None:
        raw_relative = self.add_audio("raw")
        raw, _ = register_take(
            self.root,
            chunks=self.chunks,
            record=self.record(raw_relative),
        )
        child_relative = self.add_audio("processed", frames=21000)
        child_record = self.record(
            child_relative,
            kind="rendition",
            source_take_id=raw["take_id"],
            root_take_id=raw["take_id"],
            processing={
                "operation": "approved_gain_adjustment",
                "settings": {"gain_db": -1.0},
            },
        )
        child, registry = register_take(
            self.root,
            chunks=self.chunks,
            record=child_record,
        )
        self.assertEqual(child["source_take_id"], raw["take_id"])
        self.assertEqual(child["root_take_id"], raw["take_id"])
        self.assertTrue(child["current"])
        self.assertFalse(registry["takes"][raw["take_id"]]["current"])

    def test_promote_prior_take_updates_chunk_and_exact_undo_restores_selection(self) -> None:
        chunks_path = self.root / "chunks.json"
        chunks_path.write_text(json.dumps(self.chunks), encoding="utf-8")
        first_relative = self.add_audio("promote-first")
        first, _ = register_take(
            self.root,
            chunks=self.chunks,
            record=self.record(first_relative),
        )
        second_relative = self.add_audio("promote-second", frames=23000)
        second, registry = register_take(
            self.root,
            chunks=self.chunks,
            record=self.record(second_relative),
        )
        result = promote_take(
            self.root,
            chunks=self.chunks,
            chunks_path=chunks_path,
            index=0,
            take_id=first["take_id"],
            expected_registry_fingerprint=registry["registry_fingerprint"],
            expected_record_fingerprint=registry["takes"][first["take_id"]][
                "record_fingerprint"
            ],
            expected_audio_fingerprint="b" * 64,
        )
        selected = json.loads(chunks_path.read_text(encoding="utf-8"))[0]
        self.assertEqual(selected["current_take_id"], first["take_id"])
        self.assertEqual(selected["audio_path"], first_relative)
        current_registry = load_registry(self.root)
        self.assertTrue(current_registry["takes"][first["take_id"]]["current"])
        self.assertFalse(current_registry["takes"][second["take_id"]]["current"])
        undone = undo_operation(
            self.root,
            operation_id=result["operation_id"],
            expected_registry_fingerprint=result["registry_fingerprint"],
        )
        restored = json.loads(chunks_path.read_text(encoding="utf-8"))[0]
        self.assertEqual(restored["audio_path"], self.chunks[0]["audio_path"])
        restored_registry = load_registry(self.root)
        self.assertTrue(restored_registry["takes"][second["take_id"]]["current"])
        self.assertEqual(undone["status"], "undone")

    def test_promote_real_surface_is_one_crash_reconciled_selection(self) -> None:
        chunks_path = self.root / "chunks.json"
        chunks_path.write_text(json.dumps(self.chunks), encoding="utf-8")
        first_relative = self.add_audio("crash-promote-first")
        first, _ = register_take(self.root, chunks=self.chunks, record=self.record(first_relative))
        second_relative = self.add_audio("crash-promote-second", frames=23000)
        _second, registry = register_take(self.root, chunks=self.chunks, record=self.record(second_relative))
        with patch.dict(os.environ, {
            "ALEXANDRIA_TEST_AUDIO_CRASH_INJECTION": "1",
            "ALEXANDRIA_AUDIO_CRASH_POINT": "current_take_selection:after",
        }, clear=False):
            with self.assertRaises(InjectedAudioCrash):
                promote_take(
                    self.root,
                    chunks=self.chunks,
                    chunks_path=chunks_path,
                    index=0,
                    take_id=first["take_id"],
                    expected_registry_fingerprint=registry["registry_fingerprint"],
                    expected_record_fingerprint=registry["takes"][first["take_id"]]["record_fingerprint"],
                    expected_audio_fingerprint="b" * 64,
                )
        repaired = reconcile_audio_transitions(self.root)
        self.assertEqual(repaired["repaired_count"], 1)
        promoted_registry = load_registry(self.root)
        operation_id = repaired["actions"][0]["operation_id"]
        with patch.dict(os.environ, {
            "ALEXANDRIA_TEST_AUDIO_CRASH_INJECTION": "1",
            "ALEXANDRIA_AUDIO_CRASH_POINT": "undo_restoration:after",
        }, clear=False):
            with self.assertRaises(InjectedAudioCrash):
                undo_operation(
                    self.root,
                    operation_id=operation_id,
                    expected_registry_fingerprint=promoted_registry["registry_fingerprint"],
                )
        self.assertEqual(reconcile_audio_transitions(self.root)["repaired_count"], 1)
        self.assertEqual(json.loads(chunks_path.read_text()), self.chunks)
        self.assertEqual(reconcile_audio_transitions(self.root)["actions"], [])

    def test_promote_rejects_take_from_changed_dependency(self) -> None:
        chunks_path = self.root / "chunks.json"
        chunks_path.write_text(json.dumps(self.chunks), encoding="utf-8")
        relative = self.add_audio("dependency-mismatch")
        take, registry = register_take(
            self.root,
            chunks=self.chunks,
            record=self.record(relative),
        )
        with self.assertRaisesRegex(AudioTakeError, "older text"):
            promote_take(
                self.root,
                chunks=self.chunks,
                chunks_path=chunks_path,
                index=0,
                take_id=take["take_id"],
                expected_registry_fingerprint=registry["registry_fingerprint"],
                expected_record_fingerprint=take["record_fingerprint"],
                expected_audio_fingerprint="e" * 64,
            )

    def test_keep_is_optimistic_and_blocks_delete(self) -> None:
        relative = self.add_audio("keep")
        take, registry = register_take(
            self.root,
            chunks=self.chunks,
            record=self.record(relative),
        )
        updated, written = set_take_kept(
            self.root,
            chunks=self.chunks,
            chunk_key_value="chunk:7",
            take_id=take["take_id"],
            kept=True,
            expected_registry_fingerprint=registry["registry_fingerprint"],
            expected_record_fingerprint=take["record_fingerprint"],
        )
        self.assertTrue(updated["kept"])
        impact = delete_impact(
            self.root,
            chunks=self.chunks,
            chunk_key_value="chunk:7",
            take_id=take["take_id"],
        )
        self.assertFalse(impact["safe_to_delete"])
        self.assertIn("current_take", {item["code"] for item in impact["blockers"]})
        self.assertIn("kept_take", {item["code"] for item in impact["blockers"]})
        with self.assertRaisesRegex(AudioTakeError, "changed"):
            set_take_kept(
                self.root,
                chunks=self.chunks,
                chunk_key_value="chunk:7",
                take_id=take["take_id"],
                kept=False,
                expected_registry_fingerprint=registry["registry_fingerprint"],
                expected_record_fingerprint=take["record_fingerprint"],
            )
        self.assertNotEqual(written["registry_fingerprint"], registry["registry_fingerprint"])

    def test_delete_moves_eligible_take_to_backup_and_undo_restores_exact_bytes(self) -> None:
        first_relative = self.add_audio("first")
        first, _ = register_take(
            self.root,
            chunks=self.chunks,
            record=self.record(first_relative),
        )
        second_relative = self.add_audio("second", frames=23000)
        _second, _ = register_take(
            self.root,
            chunks=self.chunks,
            record=self.record(second_relative),
        )
        before_bytes = (self.root / first_relative).read_bytes()
        impact = delete_impact(
            self.root,
            chunks=self.chunks,
            chunk_key_value="chunk:7",
            take_id=first["take_id"],
        )
        self.assertTrue(impact["safe_to_delete"], impact)
        result = apply_delete(
            self.root,
            chunks=self.chunks,
            impact=impact,
            expected_impact_fingerprint=impact["impact_fingerprint"],
        )
        self.assertFalse((self.root / first_relative).exists())
        self.assertNotIn(first["take_id"], load_registry(self.root)["takes"])
        undone = undo_operation(
            self.root,
            operation_id=result["operation_id"],
            expected_registry_fingerprint=result["registry_fingerprint"],
        )
        self.assertEqual((self.root / first_relative).read_bytes(), before_bytes)
        self.assertIn(first["take_id"], load_registry(self.root)["takes"])
        self.assertEqual(undone["restored_take_ids"], [first["take_id"]])

    def test_cleanup_uses_age_and_size_while_protecting_current_kept_and_ancestors(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat().replace("+00:00", "Z")
        raw_relative = self.add_audio("old-raw")
        raw, _ = register_take(
            self.root,
            chunks=self.chunks,
            record=self.record(raw_relative, created_at_utc=old),
        )
        child_relative = self.add_audio("old-child")
        child, registry = register_take(
            self.root,
            chunks=self.chunks,
            record=self.record(
                child_relative,
                kind="rendition",
                source_take_id=raw["take_id"],
                created_at_utc=old,
            ),
        )
        child, registry = set_take_kept(
            self.root,
            chunks=self.chunks,
            chunk_key_value="chunk:7",
            take_id=child["take_id"],
            kept=True,
            expected_registry_fingerprint=registry["registry_fingerprint"],
            expected_record_fingerprint=child["record_fingerprint"],
        )
        eligible_relative = self.add_audio("old-eligible")
        eligible, _ = register_take(
            self.root,
            chunks=self.chunks,
            record=self.record(eligible_relative, created_at_utc=old),
        )
        newest_relative = self.add_audio("new-current")
        _newest, _ = register_take(
            self.root,
            chunks=self.chunks,
            record=self.record(newest_relative),
        )
        impact = cleanup_impact(
            self.root,
            chunks=self.chunks,
            older_than_days=30,
            reclaim_at_least_bytes=1,
        )
        candidate_ids = {item["take_id"] for item in impact["candidates"]}
        self.assertIn(eligible["take_id"], candidate_ids)
        self.assertNotIn(raw["take_id"], candidate_ids)
        self.assertNotIn(child["take_id"], candidate_ids)
        result = apply_cleanup(
            self.root,
            chunks=self.chunks,
            impact=impact,
            expected_impact_fingerprint=impact["impact_fingerprint"],
        )
        self.assertEqual(result["deleted_take_ids"], [eligible["take_id"]])
        self.assertFalse((self.root / eligible_relative).exists())
        self.assertTrue((self.root / raw_relative).exists())
        self.assertTrue((self.root / child_relative).exists())

    def test_external_receipt_reference_blocks_deletion(self) -> None:
        first_relative = self.add_audio("referenced")
        first, _ = register_take(
            self.root,
            chunks=self.chunks,
            record=self.record(first_relative),
        )
        second_relative = self.add_audio("current-after-reference")
        register_take(
            self.root,
            chunks=self.chunks,
            record=self.record(second_relative),
        )
        receipt = self.root / "audio_generation_requests" / "fixture" / "request.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text(
            json.dumps(
                {
                    "take_id": first["take_id"],
                    "audio_path": first_relative,
                }
            ),
            encoding="utf-8",
        )
        impact = delete_impact(
            self.root,
            chunks=self.chunks,
            chunk_key_value="chunk:7",
            take_id=first["take_id"],
        )
        self.assertFalse(impact["safe_to_delete"])
        blocker = next(
            item for item in impact["blockers"] if item["code"] == "referenced_by_evidence"
        )
        self.assertIn("audio_generation_requests/fixture/request.json", blocker["paths"])

    def test_corrupt_registry_fails_closed(self) -> None:
        registry_path(self.root).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "chunks": {"chunk:7": {"take_ids": ["missing"]}},
                    "takes": {},
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(AudioTakeError):
            load_registry(self.root)


if __name__ == "__main__":
    unittest.main()
