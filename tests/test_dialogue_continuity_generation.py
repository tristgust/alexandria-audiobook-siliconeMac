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
        handle.writeframes(b"\x00\x00" * frames)


class CapturingEngine:
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
                "speaker": speaker,
                "fish_instruction": fish_instruction,
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


class DialogueContinuityGenerationTests(unittest.TestCase):
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
                    "BERNICE": {"type": "custom", "voice": "Vivian"},
                    "NARRATOR": {"type": "custom", "voice": "Ryan"},
                }
            ),
            encoding="utf-8",
        )
        self.chunks = [
            {
                "id": 3,
                "speaker": "BERNICE",
                "text": "Not if I can help it,",
                "instruct": "Wry and controlled.",
                "status": "pending",
                "audio_path": None,
            },
            {
                "id": 4,
                "speaker": "NARRATOR",
                "text": "Bernice said, but she knew that it was too late.",
                "instruct": "Tense, urgent narration.",
                "status": "pending",
                "audio_path": None,
            },
        ]
        self.write_chunks()
        self.manager = ProjectManager(str(self.root))
        self.engine = CapturingEngine()
        self.manager.engine = self.engine

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_chunks(self) -> None:
        (self.root / "chunks.json").write_text(
            json.dumps(self.chunks),
            encoding="utf-8",
        )

    def saved_chunks(self) -> list[dict]:
        return json.loads((self.root / "chunks.json").read_text(encoding="utf-8"))

    def assert_attached_call(self, call: dict) -> None:
        self.assertEqual(
            call["text"],
            ", bernice said, but she knew that it was too late.",
        )
        instruction = call["instruct"]
        cue = "Spoken continuity: begin mid-sentence as an attached dialogue tag"
        self.assertIn(cue, instruction)
        self.assertEqual(instruction.count(cue), 1)
        self.assertEqual(call["fish_instruction"].count(cue), 1)

    def test_single_generation_persists_continuation_text_receipt_in_take(self) -> None:
        success, message = self.manager.generate_chunk_audio(1)
        self.assertTrue(success, message)
        self.assert_attached_call(self.engine.single_calls[0])

        saved = self.saved_chunks()[1]
        self.assertEqual(
            saved["text"],
            "Bernice said, but she knew that it was too late.",
        )
        self.assertEqual(
            saved["spoken_continuity_synthesis_mode"],
            "comma_continuation",
        )
        self.assertEqual(len(saved["spoken_continuity_synthesis_text_sha256"]), 64)

        registry = json.loads((self.root / "audio_takes.json").read_text(encoding="utf-8"))
        current_id = registry["chunks"]["chunk:4"]["current_take_id"]
        fields = registry["takes"][current_id]["generation"]["chunk_audio_fields"]
        self.assertEqual(
            fields["spoken_continuity_synthesis_mode"],
            "comma_continuation",
        )
        self.assertEqual(
            fields["spoken_continuity_synthesis_text_sha256"],
            saved["spoken_continuity_synthesis_text_sha256"],
        )

    def test_parallel_generation_uses_same_continuation_contract(self) -> None:
        result = self.manager.generate_chunks_parallel(
            [0, 1],
            max_workers=1,
        )
        self.assertEqual(sorted(result["completed"]), [0, 1])
        self.assertEqual(self.engine.single_calls[0]["text"], "Not if I can help it,")
        self.assert_attached_call(self.engine.single_calls[1])

    def test_batch_generation_uses_same_continuation_contract(self) -> None:
        result = self.manager.generate_chunks_batch([0, 1], batch_size=2)
        self.assertEqual(result["completed"], [0, 1])
        request = self.engine.batch_calls[0]
        self.assertEqual(request[0]["text"], "Not if I can help it,")
        self.assert_attached_call(request[1])


if __name__ == "__main__":
    unittest.main()
