from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock

from project import ProjectManager
from sound_effects import SOUND_EFFECT_BACKEND_MESSAGE


def write_wav(path: Path, *, frames: int = 96000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\x01\x00" * frames)


class BatchEngine:
    mode = "local"
    _use_mlx = False

    def __init__(self) -> None:
        self.batch_calls: list[list[dict]] = []

    def generate_batch(self, chunks, voice_config, output_dir, seed):
        copied = json.loads(json.dumps(chunks))
        self.batch_calls.append(copied)
        for item in chunks:
            write_wav(Path(output_dir) / f"temp_batch_{item['index']}.wav")
        return {"completed": [item["index"] for item in chunks], "failed": []}


class SoundEffectProjectTests(unittest.TestCase):
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
                {
                    "WOLSEY": {
                        "type": "sound_effect",
                        "voice": None,
                        "sound_effect_definition": (
                            "Domestic cat meows, purrs, and hisses; no speech."
                        ),
                    },
                    "NARRATOR": {"type": "custom", "voice": "Ryan"},
                }
            ),
            encoding="utf-8",
        )
        self.chunks = [
            {
                "id": 0,
                "speaker": "WOLSEY",
                "text": "Wolsey answers from beneath the table.",
                "instruct": "A questioning meow followed by a quiet purr.",
                "status": "pending",
                "audio_path": None,
            },
            {
                "id": 1,
                "speaker": "NARRATOR",
                "text": "The room fell quiet again.",
                "instruct": "Calm narration.",
                "status": "pending",
                "audio_path": None,
            },
        ]
        self.write_chunks()
        self.manager = ProjectManager(str(self.root))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_chunks(self) -> None:
        (self.root / "chunks.json").write_text(
            json.dumps(self.chunks),
            encoding="utf-8",
        )

    def read_chunks(self) -> list[dict]:
        return json.loads((self.root / "chunks.json").read_text(encoding="utf-8"))

    def test_single_sound_effect_fails_before_speech_engine_initialization(self) -> None:
        self.manager.get_engine = Mock(
            side_effect=AssertionError("speech engine must not initialize")
        )
        success, message = self.manager.generate_chunk_audio(0)
        self.assertFalse(success)
        self.assertEqual(message, SOUND_EFFECT_BACKEND_MESSAGE)
        self.manager.get_engine.assert_not_called()
        saved = self.read_chunks()[0]
        self.assertEqual(saved["audio_state"], "failed")
        self.assertEqual(saved["error"], SOUND_EFFECT_BACKEND_MESSAGE)

    def test_mixed_batch_blocks_sound_row_and_generates_only_speech_row(self) -> None:
        engine = BatchEngine()
        self.manager.engine = engine
        result = self.manager.generate_chunks_batch([0, 1], batch_size=2)
        self.assertEqual(result["completed"], [1])
        self.assertEqual(result["failed"], [(0, SOUND_EFFECT_BACKEND_MESSAGE)])
        self.assertEqual(len(engine.batch_calls), 1)
        self.assertEqual([item["index"] for item in engine.batch_calls[0]], [1])
        self.assertEqual([item["speaker"] for item in engine.batch_calls[0]], ["NARRATOR"])
        saved = self.read_chunks()
        self.assertEqual(saved[0]["audio_state"], "failed")
        self.assertEqual(saved[1]["audio_state"], "current")


if __name__ == "__main__":
    unittest.main()
