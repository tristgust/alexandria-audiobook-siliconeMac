from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from audio_artifacts import AudioArtifactError, sha256_file
from project import ProjectManager


def write_wav(path: Path, *, frames: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\x00\x00" * frames)


class FakeEngine:
    def __init__(self, *, succeed: bool = True):
        self.succeed = succeed
        self.calls = []

    def generate_voice(self, text, instruct, speaker, voice_config, output_path):
        self.calls.append((text, instruct, speaker, output_path))
        if self.succeed:
            write_wav(Path(output_path))
        return self.succeed


class FakeBatchEngine:
    def generate_batch(self, chunks, voice_config, output_dir, seed):
        completed = []
        for item in chunks:
            write_wav(Path(output_dir) / f"temp_batch_{item['index']}.wav")
            completed.append(item["index"])
        return {"completed": completed, "failed": []}


class ShortAudioEngine(FakeEngine):
    def generate_voice(self, text, instruct, speaker, voice_config, output_path):
        self.calls.append((text, instruct, speaker, output_path))
        write_wav(Path(output_path), frames=2400)
        return True


class ShortBatchEngine:
    def generate_batch(self, chunks, voice_config, output_dir, seed):
        for item in chunks:
            write_wav(
                Path(output_dir) / f"temp_batch_{item['index']}.wav",
                frames=2400,
            )
        return {"completed": [item["index"] for item in chunks], "failed": []}


class ProjectAudioSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "app").mkdir()
        (self.root / "app" / "config.json").write_text(
            json.dumps({"tts": {"language": "English"}}),
            encoding="utf-8",
        )
        (self.root / "voice_config.json").write_text(
            json.dumps({"NARRATOR": {"type": "custom", "voice": "Ryan"}}),
            encoding="utf-8",
        )
        self.manager = ProjectManager(str(self.root))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_chunks(self, chunks) -> None:
        (self.root / "chunks.json").write_text(
            json.dumps(chunks),
            encoding="utf-8",
        )

    def read_chunks(self):
        return json.loads((self.root / "chunks.json").read_text(encoding="utf-8"))

    @staticmethod
    def _pending_chunk(index: int, *, text: str) -> dict:
        return {
            "id": index,
            "speaker": "NARRATOR",
            "text": text,
            "instruct": "Calm.",
            "status": "pending",
            "audio_path": None,
        }

    def install_current_chunk(self, *, text: str = "Current."):
        chunk = {
            "id": 0,
            "speaker": "NARRATOR",
            "text": text,
            "instruct": "Calm.",
            "status": "done",
        }
        voice_config = json.loads(
            (self.root / "voice_config.json").read_text(encoding="utf-8")
        )
        path = self.root / "voicelines" / "current.wav"
        write_wav(path, frames=48000)
        chunk.update(
            {
                "audio_path": "voicelines/current.wav",
                "audio_state": "current",
                "audio_fingerprint": self.manager._audio_binding(
                    chunk,
                    voice_config,
                    "NARRATOR",
                ),
                "audio_sha256": sha256_file(path),
                "audio_size_bytes": path.stat().st_size,
                "audio_duration_ms": 2000,
                "audio_format": "wav",
                "stale_audio_path": None,
            }
        )
        self.write_chunks([chunk])
        return chunk, path

    def test_synthesis_edit_immediately_removes_prior_audio_from_eligibility(self) -> None:
        old = self.root / "voicelines" / "old.wav"
        write_wav(old)
        self.write_chunks(
            [
                {
                    "id": 0,
                    "speaker": "NARRATOR",
                    "text": "Old text.",
                    "instruct": "Calm.",
                    "status": "done",
                    "audio_path": "voicelines/old.wav",
                    "audio_state": "current",
                    "audio_fingerprint": "f" * 64,
                    "audio_sha256": sha256_file(old),
                }
            ]
        )

        changed = self.manager.update_chunk(0, {"text": "New text."})

        self.assertEqual(changed["status"], "pending")
        self.assertEqual(changed["audio_state"], "stale")
        self.assertIsNone(changed["audio_path"])
        self.assertEqual(changed["stale_audio_path"], "voicelines/old.wav")
        self.assertIsNone(changed["audio_fingerprint"])
        self.assertIsNone(changed["audio_sha256"])
        self.assertTrue(old.is_file())

    def test_single_generation_marks_prior_audio_stale_then_installs_current(self) -> None:
        old = self.root / "voicelines" / "old.wav"
        write_wav(old)
        self.write_chunks(
            [
                {
                    "id": 0,
                    "speaker": "NARRATOR",
                    "text": "Hello.",
                    "instruct": "Calm.",
                    "status": "done",
                    "audio_path": "voicelines/old.wav",
                }
            ]
        )
        self.manager.engine = FakeEngine()

        success, audio_path = self.manager.generate_chunk_audio(0)

        self.assertTrue(success)
        chunk = self.read_chunks()[0]
        self.assertEqual(chunk["status"], "done")
        self.assertEqual(chunk["audio_state"], "current")
        self.assertEqual(chunk["audio_path"], audio_path)
        self.assertEqual(len(chunk["audio_fingerprint"]), 64)
        self.assertEqual(len(chunk["audio_sha256"]), 64)
        self.assertIsNone(chunk["stale_audio_path"])
        self.assertTrue((self.root / audio_path).is_file())
        self.assertFalse(old.exists())

    def test_generation_failure_keeps_old_file_but_removes_it_from_eligibility(self) -> None:
        old = self.root / "voicelines" / "old.wav"
        write_wav(old)
        self.write_chunks(
            [
                {
                    "id": 0,
                    "speaker": "NARRATOR",
                    "text": "Hello.",
                    "instruct": "Calm.",
                    "status": "done",
                    "audio_path": "voicelines/old.wav",
                }
            ]
        )
        self.manager.engine = FakeEngine(succeed=False)

        success, message = self.manager.generate_chunk_audio(0)

        self.assertFalse(success)
        self.assertEqual(message, "Generation failed")
        chunk = self.read_chunks()[0]
        self.assertEqual(chunk["status"], "error")
        self.assertEqual(chunk["audio_state"], "failed")
        self.assertIsNone(chunk["audio_path"])
        self.assertEqual(chunk["stale_audio_path"], "voicelines/old.wav")
        self.assertTrue(old.is_file())

    def test_rejected_single_generation_removes_large_temporary_source(self) -> None:
        self.write_chunks([self._pending_chunk(0, text="A much longer authored line.")])
        self.manager.engine = ShortAudioEngine()

        success, message = self.manager.generate_chunk_audio(0)

        self.assertFalse(success)
        self.assertIn("too short", message)
        self.assertFalse((self.root / "temp_chunk_0.wav").exists())

    def test_rejected_batch_generation_removes_temporary_sources(self) -> None:
        self.write_chunks([self._pending_chunk(0, text="A much longer authored line.")])
        self.manager.engine = ShortBatchEngine()

        result = self.manager.generate_chunks_batch([0], batch_size=1)

        self.assertEqual(result["completed"], [])
        self.assertEqual(len(result["failed"]), 1)
        self.assertFalse((self.root / "temp_batch_0.wav").exists())

    def test_batch_generation_uses_same_atomic_install_contract(self) -> None:
        self.write_chunks(
            [
                {
                    "id": 0,
                    "speaker": "NARRATOR",
                    "text": "One.",
                    "instruct": "Calm.",
                    "status": "pending",
                    "audio_path": None,
                },
                {
                    "id": 1,
                    "speaker": "NARRATOR",
                    "text": "Two.",
                    "instruct": "Firm.",
                    "status": "pending",
                    "audio_path": None,
                },
            ]
        )
        self.manager.engine = FakeBatchEngine()

        result = self.manager.generate_chunks_batch([0, 1], batch_size=2)

        self.assertEqual(result["completed"], [0, 1])
        for chunk in self.read_chunks():
            self.assertEqual(chunk["status"], "done")
            self.assertEqual(chunk["audio_state"], "current")
            self.assertTrue((self.root / chunk["audio_path"]).is_file())
            self.assertEqual(chunk["audio_sha256"], sha256_file(self.root / chunk["audio_path"]))

    def test_all_final_outputs_block_legacy_or_stale_audio_before_writing(self) -> None:
        audio = self.root / "voicelines" / "legacy.wav"
        write_wav(audio)
        self.write_chunks(
            [
                {
                    "id": 0,
                    "speaker": "NARRATOR",
                    "text": "Legacy.",
                    "instruct": "Calm.",
                    "status": "done",
                    "audio_path": "voicelines/legacy.wav",
                }
            ]
        )
        existing_mp3 = self.root / "cloned_audiobook.mp3"
        existing_mp3.write_bytes(b"existing")
        existing_m4b = self.root / "audiobook.m4b"
        existing_m4b.write_bytes(b"existing")
        existing_zip = self.root / "audacity_export.zip"
        existing_zip.write_bytes(b"existing")

        merge = self.manager.merge_audio()
        audacity = self.manager.export_audacity()
        m4b = self.manager.merge_m4b()

        for result in (merge, audacity, m4b):
            self.assertFalse(result[0])
            self.assertIn("Final audio export is blocked", result[1])
        self.assertEqual(existing_mp3.read_bytes(), b"existing")
        self.assertEqual(existing_m4b.read_bytes(), b"existing")
        self.assertEqual(existing_zip.read_bytes(), b"existing")

    def test_current_audio_load_rechecks_voice_binding_and_hash(self) -> None:
        self.install_current_chunk()
        voice_config = json.loads(
            (self.root / "voice_config.json").read_text(encoding="utf-8")
        )

        loaded = self.manager._load_chunks_with_audio()
        self.assertEqual(len(loaded), 1)

        voice_config["NARRATOR"]["voice"] = "Aiden"
        (self.root / "voice_config.json").write_text(
            json.dumps(voice_config),
            encoding="utf-8",
        )
        success, message = self.manager.merge_audio()
        self.assertFalse(success)
        self.assertIn("stale", message)

    def test_failed_final_mp3_export_preserves_existing_output(self) -> None:
        self.install_current_chunk()
        output = self.root / "cloned_audiobook.mp3"
        output.write_bytes(b"existing")
        with patch(
            "project.atomic_export_audio_segment",
            side_effect=AudioArtifactError(
                "audio_output_export_failed",
                "synthetic final export failure",
            ),
        ):
            success, message = self.manager.merge_audio()
        self.assertFalse(success)
        self.assertIn("synthetic final export failure", message)
        self.assertEqual(output.read_bytes(), b"existing")

    def test_failed_audacity_archive_preserves_existing_output(self) -> None:
        self.install_current_chunk()
        output = self.root / "audacity_export.zip"
        output.write_bytes(b"existing")
        with patch("project.zipfile.ZipFile", side_effect=RuntimeError("zip failed")):
            success, message = self.manager.export_audacity()
        self.assertFalse(success)
        self.assertIn("zip failed", message)
        self.assertEqual(output.read_bytes(), b"existing")

    def test_failed_m4b_encode_preserves_existing_output(self) -> None:
        self.install_current_chunk(text="Chapter One")
        output = self.root / "audiobook.m4b"
        output.write_bytes(b"existing")
        failed = type("Failed", (), {"returncode": 1, "stderr": "synthetic"})()
        with patch("project.subprocess.run", return_value=failed):
            success, message = self.manager.merge_m4b()
        self.assertFalse(success)
        self.assertIn("FFmpeg failed", message)
        self.assertEqual(output.read_bytes(), b"existing")


if __name__ == "__main__":
    unittest.main()
