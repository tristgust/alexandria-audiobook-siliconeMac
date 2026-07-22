from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audio_artifacts import (
    AudioArtifactError,
    atomic_export_audio_segment,
    audio_backup_map,
    audio_binding_fingerprint,
    backup_operation_audio,
    confined_audio_path,
    consume_operation_audio_backups,
    install_generated_audio,
    inspect_chunk_audio,
    is_operation_audio_backup_path,
    remove_restored_operation_audio,
    require_current_project_audio,
    restore_operation_audio,
    sha256_file,
)


class FakeSegment:
    def __init__(self, duration_ms: int = 1200, *, fail: bool = False):
        self.duration_ms = duration_ms
        self.fail = fail

    def __len__(self) -> int:
        return self.duration_ms

    def export(self, path, *, format: str):
        if self.fail:
            raise RuntimeError("synthetic export failure")
        payload = b"M" * 2048 if format == "mp3" else b"W" * 512
        if hasattr(path, "write"):
            path.write(payload)
            path.flush()
        else:
            Path(path).write_bytes(payload)
        return path


def fake_decoder(path, format=None):
    target = Path(getattr(path, "name", path))
    if not target.is_file() or target.stat().st_size <= 0:
        raise RuntimeError("missing audio")
    return FakeSegment()


class AudioArtifactTests(unittest.TestCase):
    def test_operation_backup_moves_unique_paths_and_deduplicates_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "voicelines" / "first.wav"
            second = root / "voicelines" / "second.wav"
            first.parent.mkdir()
            payload = b"same-audio"
            first.write_bytes(payload)
            second.write_bytes(payload)
            records = backup_operation_audio(
                root_dir=root,
                operation_dir=root / "history" / "operation_1",
                relative_paths=[
                    "voicelines/first.wav",
                    "voicelines/first.wav",
                    "voicelines/second.wav",
                ],
            )

            self.assertEqual(len(records), 2)
            self.assertFalse(first.exists())
            self.assertFalse(second.exists())
            self.assertEqual(
                len({record["backup_path"] for record in records}),
                1,
            )
            mapping = audio_backup_map(records)
            self.assertEqual(mapping["voicelines/first.wav"]["sha256"], sha256_file(root / records[0]["backup_path"]))

            restored = restore_operation_audio(
                root_dir=root,
                records=records,
                consume_backups=False,
            )
            self.assertEqual(
                restored,
                ["voicelines/first.wav", "voicelines/second.wav"],
            )
            self.assertEqual(first.read_bytes(), payload)
            self.assertEqual(second.read_bytes(), payload)

            remove_restored_operation_audio(root_dir=root, records=records)
            self.assertFalse(first.exists())
            self.assertFalse(second.exists())

            cleanup = consume_operation_audio_backups(
                root_dir=root,
                records=records,
            )
            self.assertEqual(cleanup["status"], "complete")
            self.assertEqual(
                cleanup["removed_paths"],
                [records[0]["backup_path"]],
            )
            self.assertEqual(cleanup["failed_paths"], [])
            self.assertFalse((root / records[0]["backup_path"]).exists())

    def test_operation_restore_blocks_newer_canonical_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "voicelines" / "line.wav"
            original.parent.mkdir()
            original.write_bytes(b"old-audio")
            records = backup_operation_audio(
                root_dir=root,
                operation_dir=root / "history" / "operation_2",
                relative_paths=["voicelines/line.wav"],
            )
            original.write_bytes(b"newer-audio")
            with self.assertRaises(AudioArtifactError) as caught:
                restore_operation_audio(
                    root_dir=root,
                    records=records,
                )
            self.assertEqual(caught.exception.code, "audio_rollback_conflict")
            self.assertEqual(original.read_bytes(), b"newer-audio")

    def test_operation_backup_failure_restores_already_removed_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "voicelines" / "first.wav"
            second = root / "voicelines" / "second.wav"
            first.parent.mkdir()
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            from audio_artifacts import _atomic_copy

            copy_count = 0

            def failing_copy(source, target):
                nonlocal copy_count
                copy_count += 1
                if copy_count == 2:
                    raise OSError("synthetic backup failure")
                return _atomic_copy(source, target)

            with patch("audio_artifacts._atomic_copy", side_effect=failing_copy):
                with self.assertRaisesRegex(OSError, "synthetic"):
                    backup_operation_audio(
                        root_dir=root,
                        operation_dir=root / "history" / "operation_3",
                        relative_paths=[
                            "voicelines/first.wav",
                            "voicelines/second.wav",
                        ],
                    )
            self.assertEqual(first.read_bytes(), b"first")
            self.assertEqual(second.read_bytes(), b"second")

    def test_operation_backup_path_classification_is_narrow(self) -> None:
        self.assertTrue(
            is_operation_audio_backup_path(
                "external_workflows/import_history/import_123/audio/" + "a" * 64 + ".bin"
            )
        )
        self.assertTrue(
            is_operation_audio_backup_path(
                "speaker_management_history/speaker_123/audio/" + "b" * 64 + ".bin"
            )
        )
        for value in (
            "voicelines/current.wav",
            "external_workflows/import_history/import_123/audio/current.wav",
            "external_workflows/import_history/import_123/not-audio/" + "a" * 64 + ".bin",
            None,
        ):
            with self.subTest(value=value):
                self.assertFalse(is_operation_audio_backup_path(value))

    def test_partial_backup_cleanup_is_reported_without_hiding_successes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "voicelines" / "first.wav"
            second = root / "voicelines" / "second.wav"
            first.parent.mkdir()
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            records = backup_operation_audio(
                root_dir=root,
                operation_dir=root / "external_workflows" / "import_history" / "operation_4",
                relative_paths=[
                    "voicelines/first.wav",
                    "voicelines/second.wav",
                ],
            )
            failing_path = root / records[1]["backup_path"]
            failing_path.unlink()
            failing_path.mkdir()

            cleanup = consume_operation_audio_backups(
                root_dir=root,
                records=records,
            )

            self.assertEqual(cleanup["status"], "partial")
            self.assertEqual(len(cleanup["removed_paths"]), 1)
            self.assertEqual(
                [item["backup_path"] for item in cleanup["failed_paths"]],
                [records[1]["backup_path"]],
            )
            self.assertRegex(
                cleanup["failed_paths"][0]["error"],
                r"^(IsADirectoryError|PermissionError):",
            )
            self.assertTrue(failing_path.exists())

    def test_binding_fingerprint_is_stable_and_sensitive(self) -> None:
        chunk = {
            "speaker": "THE DOCTOR",
            "text": "Run.",
            "instruct": "Urgent.",
        }
        voice = {"THE DOCTOR": {"type": "clone", "ref_text": "Hello."}}
        first = audio_binding_fingerprint(
            chunk=chunk,
            resolved_speaker="THE DOCTOR",
            voice_config=voice,
            synthesis_config={"language": "English"},
        )
        second = audio_binding_fingerprint(
            chunk=dict(chunk),
            resolved_speaker="THE DOCTOR",
            voice_config=dict(voice),
            synthesis_config={"language": "English"},
        )
        changed = audio_binding_fingerprint(
            chunk={**chunk, "instruct": "Whispered."},
            resolved_speaker="THE DOCTOR",
            voice_config=voice,
            synthesis_config={"language": "English"},
        )
        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)

    def test_confined_path_rejects_traversal_and_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(
                confined_audio_path(root, "voicelines/a.wav"),
                root.resolve() / "voicelines" / "a.wav",
            )
            for unsafe in ("../outside.wav", "/tmp/outside.wav", ""):
                with self.subTest(unsafe=unsafe):
                    with self.assertRaises(AudioArtifactError):
                        confined_audio_path(root, unsafe)

    def test_atomic_output_export_replaces_only_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "final.mp3"
            target.write_bytes(b"old")
            result = atomic_export_audio_segment(
                segment=FakeSegment(),
                target_path=target,
                audio_format="mp3",
                decoder=fake_decoder,
            )
            self.assertEqual(target.read_bytes(), b"M" * 2048)
            self.assertEqual(result["sha256"], sha256_file(target))
            self.assertFalse(any(path.name.startswith(".final.") for path in target.parent.iterdir()))

    def test_atomic_output_failure_preserves_existing_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "final.mp3"
            target.write_bytes(b"old")
            with self.assertRaises(AudioArtifactError) as caught:
                atomic_export_audio_segment(
                    segment=FakeSegment(fail=True),
                    target_path=target,
                    audio_format="mp3",
                    decoder=fake_decoder,
                )
            self.assertEqual(caught.exception.code, "audio_output_export_failed")
            self.assertEqual(target.read_bytes(), b"old")
            self.assertFalse(any(path.name.startswith(".final.") for path in target.parent.iterdir()))

    def test_install_atomically_replaces_canonical_and_removes_obsolete_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            voicelines = root / "voicelines"
            voicelines.mkdir()
            source = root / "source.wav"
            source.write_bytes(b"source")
            old = voicelines / "old.wav"
            old.write_bytes(b"old")
            obsolete = voicelines / "line.wav"
            obsolete.write_bytes(b"obsolete")

            result = install_generated_audio(
                root_dir=root,
                voicelines_dir=voicelines,
                source_audio_path=source,
                filename_base="line",
                binding_fingerprint="f" * 64,
                previous_audio_path="voicelines/old.wav",
                decoder=fake_decoder,
            )

            canonical = root / result["audio_path"]
            self.assertEqual(result["audio_path"], "voicelines/line.mp3")
            self.assertEqual(canonical.read_bytes(), b"M" * 2048)
            self.assertFalse(old.exists())
            self.assertFalse(obsolete.exists())
            self.assertEqual(result["audio_state"], "current")
            self.assertEqual(result["audio_fingerprint"], "f" * 64)
            self.assertEqual(result["audio_sha256"], sha256_file(canonical))
            self.assertFalse(any(path.name.endswith(".tmp") for path in voicelines.iterdir()))

    def test_regeneration_preserves_operation_backup_until_terminal_undo(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            voicelines = root / "voicelines"
            voicelines.mkdir()
            source = root / "source.wav"
            source.write_bytes(b"source")
            backup_relative = (
                "external_workflows/import_history/import_123/audio/"
                + "a" * 64
                + ".bin"
            )
            backup = root / backup_relative
            backup.parent.mkdir(parents=True)
            backup.write_bytes(b"operation-backup")

            result = install_generated_audio(
                root_dir=root,
                voicelines_dir=voicelines,
                source_audio_path=source,
                filename_base="line",
                binding_fingerprint="f" * 64,
                previous_audio_path=backup_relative,
                decoder=fake_decoder,
            )

            self.assertTrue((root / result["audio_path"]).is_file())
            self.assertEqual(backup.read_bytes(), b"operation-backup")

    def test_failed_install_preserves_existing_canonical_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            voicelines = root / "voicelines"
            voicelines.mkdir()
            source = root / "source.wav"
            source.write_bytes(b"source")
            canonical = voicelines / "line.mp3"
            canonical.write_bytes(b"old-current")

            def failing_decoder(path, format=None):
                target = Path(getattr(path, "name", path))
                if target == source:
                    return FakeSegment(fail=True)
                raise RuntimeError("invalid")

            with self.assertRaises(AudioArtifactError):
                install_generated_audio(
                    root_dir=root,
                    voicelines_dir=voicelines,
                    source_audio_path=source,
                    filename_base="line",
                    binding_fingerprint="f" * 64,
                    previous_audio_path="voicelines/line.mp3",
                    decoder=failing_decoder,
                )

            self.assertEqual(canonical.read_bytes(), b"old-current")
            self.assertFalse(any(path.name.endswith(".tmp") for path in voicelines.iterdir()))

    def test_inspection_requires_current_binding_and_file_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "voicelines" / "line.wav"
            path.parent.mkdir()
            path.write_bytes(b"audio")
            fingerprint = "a" * 64
            chunk = {
                "status": "done",
                "audio_path": "voicelines/line.wav",
                "audio_state": "current",
                "audio_fingerprint": fingerprint,
                "audio_sha256": sha256_file(path),
            }
            current = inspect_chunk_audio(
                root_dir=root,
                chunk=chunk,
                expected_fingerprint=fingerprint,
                decoder=fake_decoder,
            )
            self.assertTrue(current["ready"])
            self.assertEqual(current["state"], "current")

            stale = inspect_chunk_audio(
                root_dir=root,
                chunk=chunk,
                expected_fingerprint="b" * 64,
                decoder=fake_decoder,
            )
            self.assertEqual(stale["state"], "stale")
            self.assertEqual(stale["reason"], "audio_fingerprint_mismatch")

            path.write_bytes(b"tampered")
            tampered = inspect_chunk_audio(
                root_dir=root,
                chunk=chunk,
                expected_fingerprint=fingerprint,
                decoder=fake_decoder,
            )
            self.assertEqual(tampered["state"], "stale")
            self.assertEqual(tampered["reason"], "audio_hash_mismatch")

    def test_legacy_done_audio_is_stale_until_regenerated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "voicelines" / "legacy.wav"
            path.parent.mkdir()
            path.write_bytes(b"audio")
            inspection = inspect_chunk_audio(
                root_dir=root,
                chunk={
                    "status": "done",
                    "audio_path": "voicelines/legacy.wav",
                },
                expected_fingerprint="a" * 64,
                decoder=fake_decoder,
            )
            self.assertFalse(inspection["ready"])
            self.assertEqual(inspection["state"], "stale")
            self.assertEqual(inspection["reason"], "audio_not_current")

    def test_final_readiness_reports_every_blocking_chunk(self) -> None:
        chunks = [
            {"speaker": "A", "text": "One.", "status": "pending"},
            {"speaker": "B", "text": "Two.", "status": "error", "audio_state": "failed"},
            {"speaker": "C", "text": ""},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(AudioArtifactError) as caught:
                require_current_project_audio(
                    root_dir=temporary,
                    chunks=chunks,
                    expected_fingerprint=lambda chunk: "a" * 64,
                    decoder=fake_decoder,
                )
        self.assertEqual(caught.exception.code, "project_audio_not_ready")
        self.assertEqual(
            [item["state"] for item in caught.exception.details],
            ["pending", "failed"],
        )


if __name__ == "__main__":
    unittest.main()
