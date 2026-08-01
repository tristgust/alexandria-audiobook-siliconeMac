from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path

from audio_artifacts import sha256_file
from project import ProjectManager


def write_wav(path: Path, *, frames: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\x01\x00" * frames)


class FakeEngine:
    mode = "local"
    _use_mlx = False

    def generate_voice(
        self,
        _text,
        _instruct,
        _speaker,
        _voice_config,
        output_path,
    ) -> bool:
        write_wav(Path(output_path))
        return True


class AudioTakeProjectManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "app").mkdir()
        (self.root / "app" / "config.json").write_text(
            json.dumps({"tts": {"language": "English"}}),
            encoding="utf-8",
        )
        (self.root / "voice_config.json").write_text(
            json.dumps(
                {"NARRATOR": {"type": "custom", "voice": "Ryan"}}
            ),
            encoding="utf-8",
        )
        (self.root / "chunks.json").write_text(
            json.dumps(
                [
                    {
                        "id": 0,
                        "speaker": "NARRATOR",
                        "text": "Hello.",
                        "instruct": "Calm.",
                        "status": "pending",
                        "audio_path": None,
                    }
                ]
            ),
            encoding="utf-8",
        )
        self.manager = ProjectManager(str(self.root))
        self.manager.engine = FakeEngine()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def chunks(self) -> list[dict]:
        return json.loads(
            (self.root / "chunks.json").read_text(encoding="utf-8")
        )

    def generate(self) -> dict:
        success, _path = self.manager.generate_chunk_audio(0)
        self.assertTrue(success)
        return self.manager.audio_take_status(0)

    def test_generation_history_promote_keep_and_child_rendition_round_trip(self) -> None:
        first_status = self.generate()
        first = first_status["takes"][0]
        first_path = self.root / first["audio"]["relative_path"]
        first_bytes = first_path.read_bytes()

        second_status = self.generate()
        self.assertEqual(second_status["take_count"], 2)
        newest = second_status["takes"][0]
        prior = next(
            item
            for item in second_status["takes"]
            if item["take_id"] == first["take_id"]
        )
        self.assertTrue(newest["current"])
        self.assertFalse(prior["current"])
        self.assertEqual(first_path.read_bytes(), first_bytes)

        promoted = self.manager.promote_audio_take(
            0,
            take_id=prior["take_id"],
            expected_registry_fingerprint=second_status[
                "registry_fingerprint"
            ],
            expected_record_fingerprint=prior["record_fingerprint"],
        )
        self.assertEqual(
            promoted["chunk"]["current_take_id"],
            prior["take_id"],
        )
        self.assertEqual(
            promoted["chunk"]["audio_path"],
            prior["audio"]["relative_path"],
        )

        promoted_status = self.manager.audio_take_status(0)
        selected = next(
            item
            for item in promoted_status["takes"]
            if item["take_id"] == prior["take_id"]
        )
        kept = self.manager.set_audio_take_kept(
            0,
            take_id=selected["take_id"],
            kept=True,
            expected_registry_fingerprint=promoted_status[
                "registry_fingerprint"
            ],
            expected_record_fingerprint=selected[
                "record_fingerprint"
            ],
        )
        self.assertTrue(kept["take"]["kept"])

        kept_status = self.manager.audio_take_status(0)
        source = next(
            item
            for item in kept_status["takes"]
            if item["take_id"] == selected["take_id"]
        )
        processed = self.root / "processed.wav"
        write_wav(processed, frames=23000)
        rendition = self.manager.register_audio_rendition(
            0,
            source_take_id=source["take_id"],
            source_audio_path=processed,
            expected_source_sha256=sha256_file(processed),
            expected_registry_fingerprint=kept_status[
                "registry_fingerprint"
            ],
            expected_source_record_fingerprint=source[
                "record_fingerprint"
            ],
            processing={
                "operation": "approved_gain_adjustment",
                "settings": {"gain_db": -1.0},
            },
        )
        child = rendition["take"]
        child_path = self.root / child["audio"]["relative_path"]
        self.assertEqual(child["kind"], "rendition")
        self.assertEqual(child["source_take_id"], source["take_id"])
        self.assertEqual(child["root_take_id"], source["root_take_id"])
        self.assertTrue(child["current"])
        self.assertTrue(child_path.is_file())
        self.assertTrue(first_path.is_file())

        undone = self.manager.undo_audio_take_operation(
            operation_id=rendition["operation_id"],
            expected_registry_fingerprint=rendition[
                "registry_fingerprint"
            ],
        )
        self.assertEqual(undone["status"], "undone")
        self.assertFalse(child_path.exists())
        restored = self.manager.audio_take_status(0)
        restored_source = next(
            item
            for item in restored["takes"]
            if item["take_id"] == source["take_id"]
        )
        self.assertTrue(restored_source["current"])
        self.assertTrue(restored_source["kept"])

    def test_project_manager_promote_rejects_stale_authored_text(self) -> None:
        status = self.generate()
        take = status["takes"][0]
        chunks = self.chunks()
        chunks[0]["text"] = "Changed text."
        chunks[0]["status"] = "pending"
        chunks[0]["audio_path"] = None
        (self.root / "chunks.json").write_text(
            json.dumps(chunks),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(Exception, "older text"):
            self.manager.promote_audio_take(
                0,
                take_id=take["take_id"],
                expected_registry_fingerprint=status[
                    "registry_fingerprint"
                ],
                expected_record_fingerprint=take[
                    "record_fingerprint"
                ],
            )

    def test_direct_chunk_edit_deselects_take_without_deleting_audio(self) -> None:
        status = self.generate()
        take = status["takes"][0]
        path = self.root / take["audio"]["relative_path"]
        before = path.read_bytes()

        updated = self.manager.update_chunk(0, {"text": "Changed text."})

        self.assertEqual(updated["audio_state"], "stale")
        self.assertEqual(updated["stale_audio_path"], take["audio"]["relative_path"])
        self.assertIsNone(updated["current_take_id"])
        self.assertEqual(path.read_bytes(), before)
        refreshed = self.manager.audio_take_status(0)
        retained = next(
            item for item in refreshed["takes"] if item["take_id"] == take["take_id"]
        )
        self.assertFalse(retained["current"])
        self.assertFalse(retained["promotable"] if "promotable" in retained else False)

    def test_explicit_invalidation_deselects_take_without_deleting_audio(self) -> None:
        status = self.generate()
        take = status["takes"][0]
        path = self.root / take["audio"]["relative_path"]
        before = path.read_bytes()

        changed = self.manager.invalidate_chunk_audio(
            [0],
            operation_id="take_invalidate_fixture",
            reason="Reviewed regeneration requested.",
        )

        self.assertEqual(changed, [0])
        updated = self.chunks()[0]
        self.assertIsNone(updated["current_take_id"])
        self.assertEqual(updated["stale_audio_path"], take["audio"]["relative_path"])
        self.assertEqual(path.read_bytes(), before)
        retained = self.manager.audio_take_status(0)["takes"][0]
        self.assertFalse(retained["current"])


if __name__ == "__main__":
    unittest.main()
