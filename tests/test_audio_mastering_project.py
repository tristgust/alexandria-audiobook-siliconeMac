from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path

from audio_mastering import AudioMasteringError
from project import ProjectManager


def write_wav(path: Path, *, frames: int = 72000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        samples = bytearray()
        for index in range(frames):
            value = 2400 if (index // 96) % 2 == 0 else -2400
            samples.extend(int(value).to_bytes(2, "little", signed=True))
        handle.writeframes(bytes(samples))


class FakeEngine:
    mode = "local"
    _use_mlx = False

    def generate_voice(self, text, instruct, speaker, voice_config, output_path):
        write_wav(Path(output_path))
        return True


def settings() -> dict:
    return {
        "schema_version": 1,
        "gain_db": 0,
        "high_pass_hz": 70,
        "low_pass_hz": 10000,
        "compression": {
            "enabled": True,
            "threshold_dbfs": -22,
            "ratio": 2,
            "attack_ms": 8,
            "release_ms": 120,
        },
        "normalization": {
            "enabled": True,
            "target_loudness_dbfs": -20,
            "maximum_gain_db": 8,
        },
        "limiter_ceiling_dbfs": -1,
    }


class AudioMasteringProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "app").mkdir()
        (self.root / "app/config.json").write_text(
            json.dumps({"tts": {"language": "English"}}),
            encoding="utf-8",
        )
        (self.root / "voice_config.json").write_text(
            json.dumps({"NARRATOR": {"type": "custom", "voice": "Ryan"}}),
            encoding="utf-8",
        )
        chunks = [
            {
                "id": 0,
                "speaker": "NARRATOR",
                "text": "The mastering fixture remains exact.",
                "instruct": "Calm and clear.",
                "status": "pending",
                "audio_path": None,
            }
        ]
        (self.root / "chunks.json").write_text(json.dumps(chunks), encoding="utf-8")
        self.manager = ProjectManager(str(self.root))
        self.manager.engine = FakeEngine()
        self.assertTrue(self.manager.generate_chunk_audio(0)[0])

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def pin(self) -> tuple[dict, dict]:
        status = self.manager.audio_take_status(0)
        take = next(item for item in status["takes"] if item["current"])
        pinned = self.manager.set_final_listen_pin(
            0,
            take_id=take["take_id"],
            pinned=True,
            expected_registry_fingerprint=status["registry_fingerprint"],
            expected_record_fingerprint=take["record_fingerprint"],
            expected_source_order_fingerprint=(
                self.manager._final_listen_source_order(self.manager.load_chunks())
            ),
        )
        refreshed = self.manager.audio_take_status(0)
        current = next(item for item in refreshed["takes"] if item["current"])
        return pinned, current

    def test_plan_requires_current_final_listen_pin(self) -> None:
        status = self.manager.audio_take_status(0)
        take = status["takes"][0]
        with self.assertRaisesRegex(AudioMasteringError, "Pin the current Take"):
            self.manager.build_publication_mastering_plan(
                0,
                source_take_id=take["take_id"],
                expected_source_sha256=take["audio"]["sha256"],
                expected_registry_fingerprint=status["registry_fingerprint"],
                expected_source_record_fingerprint=take["record_fingerprint"],
                expected_source_order_fingerprint=(
                    self.manager._final_listen_source_order(self.manager.load_chunks())
                ),
                settings=settings(),
            )

    def test_candidate_publication_creates_mastered_child_and_exact_undo(self) -> None:
        _pinned, take = self.pin()
        inventory = self.manager.audio_take_status(0)
        source_path = self.root / take["audio"]["relative_path"]
        source_bytes = source_path.read_bytes()
        source_order = self.manager._final_listen_source_order(
            self.manager.load_chunks()
        )
        plan = self.manager.build_publication_mastering_plan(
            0,
            source_take_id=take["take_id"],
            expected_source_sha256=take["audio"]["sha256"],
            expected_registry_fingerprint=inventory["registry_fingerprint"],
            expected_source_record_fingerprint=take["record_fingerprint"],
            expected_source_order_fingerprint=source_order,
            settings=settings(),
        )
        candidate = self.root / "candidate.wav"
        progress = []
        processing = self.manager.prepare_publication_mastering_candidate(
            0,
            plan=plan,
            output_path=candidate,
            progress_callback=lambda completed, total, message: progress.append(
                (completed, total, message)
            ),
        )
        self.assertTrue(candidate.is_file())
        self.assertEqual(progress[-1][0], 7)
        result = self.manager.publish_publication_mastering_candidate(
            0,
            plan=plan,
            candidate_path=candidate,
            processing=processing,
            mastering_job_id="work_mastering_fixture",
        )
        child = result["take"]
        self.assertEqual(child["kind"], "rendition")
        self.assertEqual(child["source_take_id"], take["take_id"])
        self.assertEqual(child["processing"]["operation"], "publication_mastering")
        self.assertEqual(
            child["processing"]["mastering_job_id"],
            "work_mastering_fixture",
        )
        self.assertEqual(child["review"]["state"], "needs_listening")
        self.assertFalse(child["final_listen_pinned"])
        self.assertEqual(source_path.read_bytes(), source_bytes)
        undone = self.manager.undo_audio_take_operation(
            operation_id=result["operation_id"],
            expected_registry_fingerprint=result["registry_fingerprint"],
        )
        self.assertEqual(undone["status"], "undone")
        restored = self.manager.audio_take_status(0)
        self.assertEqual(restored["take_count"], 1)
        self.assertEqual(restored["current_take_id"], take["take_id"])
        self.assertEqual(source_path.read_bytes(), source_bytes)

    def test_dependency_changes_when_registry_or_order_changes(self) -> None:
        _pinned, take = self.pin()
        first = self.manager.publication_mastering_dependency(
            0,
            source_take_id=take["take_id"],
            settings=settings(),
        )
        chunks = self.manager.load_chunks()
        chunks[0]["text"] = "Changed after plan."
        (self.root / "chunks.json").write_text(json.dumps(chunks), encoding="utf-8")
        changed = self.manager.publication_mastering_dependency(
            0,
            source_take_id=take["take_id"],
            settings=settings(),
        )
        self.assertNotEqual(first, changed)


if __name__ == "__main__":
    unittest.main()
