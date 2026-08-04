from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path

from project import ProjectManager


def write_wav(path: Path, *, frames: int = 96000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\x01\x00" * frames)


class OverlayEngine:
    mode = "local"
    _use_mlx = False

    def __init__(self) -> None:
        self.single_calls: list[dict] = []
        self.batch_calls: list[list[dict]] = []

    def generate_voice(
        self,
        text,
        instruct,
        speaker,
        voice_config,
        output_path,
        fish_render_plan=None,
        fish_instruction=None,
    ):
        self.single_calls.append(
            {
                "text": text,
                "instruct": instruct,
                "fish_instruction": fish_instruction,
                "speaker": speaker,
            }
        )
        write_wav(Path(output_path))
        return True

    def generate_batch(self, chunks, voice_config, output_dir, seed):
        copied = json.loads(json.dumps(chunks))
        self.batch_calls.append(copied)
        for item in chunks:
            write_wav(Path(output_dir) / f"temp_batch_{item['index']}.wav")
        return {"completed": [item["index"] for item in chunks], "failed": []}


class VoiceOverlayProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "app").mkdir()
        (self.root / "app" / "config.json").write_text(
            json.dumps({"tts": {"language": "English"}}),
            encoding="utf-8",
        )
        self.voice_config = {
            "COMPUTER": {"type": "custom", "voice": "Ryan"},
            "PURSERBOT": {
                "type": "alias",
                "alias_of": "COMPUTER",
                "voice_overlay": {
                    "direction": "slightly higher, brisk, clipped, and synthetic",
                    "pitch_semitones": 0,
                    "pace_percent": 100,
                    "level_db": 0,
                },
            },
        }
        (self.root / "voice_config.json").write_text(
            json.dumps(self.voice_config),
            encoding="utf-8",
        )
        self.chunks = [
            {
                "id": 0,
                "speaker": "PURSERBOT",
                "text": "Your request has been logged.",
                "instruct": "Formal system response.",
                "status": "pending",
                "audio_path": None,
            },
            {
                "id": 1,
                "speaker": "PURSERBOT",
                "text": "Please remain where you are.",
                "instruct": "Calm warning.",
                "status": "pending",
                "audio_path": None,
            },
        ]
        self.write_chunks()
        self.manager = ProjectManager(str(self.root))
        self.engine = OverlayEngine()
        self.manager.engine = self.engine

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_chunks(self) -> None:
        (self.root / "chunks.json").write_text(
            json.dumps(self.chunks),
            encoding="utf-8",
        )

    def test_single_generation_appends_target_overlay_and_preserves_source(self) -> None:
        success, message = self.manager.generate_chunk_audio(0)
        self.assertTrue(success, message)
        call = self.engine.single_calls[0]
        self.assertEqual(call["speaker"], "COMPUTER")
        self.assertIn("Formal system response.", call["instruct"])
        self.assertIn(
            "Character-specific Voice direction: slightly higher, brisk, clipped, and synthetic",
            call["instruct"],
        )
        saved = json.loads((self.root / "chunks.json").read_text(encoding="utf-8"))[0]
        self.assertEqual(saved["voice_overlay"]["direction"], self.voice_config["PURSERBOT"]["voice_overlay"]["direction"])
        self.assertEqual(len(saved["voice_overlay_fingerprint"]), 64)
        persisted_config = json.loads(
            (self.root / "voice_config.json").read_text(encoding="utf-8")
        )
        self.assertEqual(persisted_config["COMPUTER"], self.voice_config["COMPUTER"])

    def test_batch_generation_uses_same_character_overlay(self) -> None:
        result = self.manager.generate_chunks_batch([0, 1], batch_size=2)
        self.assertEqual(result["completed"], [0, 1])
        request = self.engine.batch_calls[0]
        self.assertTrue(all(item["speaker"] == "COMPUTER" for item in request))
        self.assertTrue(
            all("Character-specific Voice direction:" in item["instruct"] for item in request)
        )

    def test_pitch_only_change_changes_binding_without_source_mutation(self) -> None:
        chunk, _ = self.manager._chunk_with_spoken_continuity(
            self.chunks,
            0,
            bind=True,
        )
        first = self.manager._audio_binding(chunk, self.voice_config)
        changed = json.loads(json.dumps(self.voice_config))
        changed["PURSERBOT"]["voice_overlay"]["pitch_semitones"] = 3
        second = self.manager._audio_binding(chunk, changed)
        self.assertNotEqual(first, second)
        self.assertEqual(changed["COMPUTER"], self.voice_config["COMPUTER"])

    def test_absent_overlay_does_not_add_neutral_binding_fields(self) -> None:
        chunk = {
            "speaker": "COMPUTER",
            "text": "System ready.",
            "instruct": "Neutral.",
            "effective_instruct": "Neutral.",
            "effective_fish_instruct": "Neutral.",
        }
        updated, overlay = self.manager._chunk_with_voice_overlay(
            chunk,
            {"COMPUTER": {"type": "custom", "voice": "Ryan"}},
        )
        self.assertIsNone(overlay)
        self.assertNotIn("voice_overlay", updated)
        self.assertNotIn("voice_overlay_fingerprint", updated)


if __name__ == "__main__":
    unittest.main()
