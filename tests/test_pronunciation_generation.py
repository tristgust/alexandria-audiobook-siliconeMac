from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path

from project import ProjectManager
from pronunciation_registry import (
    empty_pronunciation_registry,
    upsert_pronunciation_entry,
)


def write_wav(path: Path, *, frames: int = 24000) -> None:
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
        self.single_calls = []
        self.batch_calls = []

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
                "fish_render_plan": fish_render_plan,
                "fish_instruction": fish_instruction,
            }
        )
        write_wav(Path(output_path))
        return True

    def generate_batch(self, chunks, voice_config, output_dir, seed):
        self.batch_calls.append(json.loads(json.dumps(chunks)))
        for item in chunks:
            write_wav(Path(output_dir) / f"temp_batch_{item['index']}.wav")
        return {"completed": [item["index"] for item in chunks], "failed": []}


class PronunciationGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
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
        self.engine = CapturingEngine()
        self.manager.engine = self.engine

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def install_registry(self, chunks: list[dict]) -> None:
        registry = upsert_pronunciation_entry(
            empty_pronunciation_registry(),
            {
                "pronunciation_id": "skaro",
                "chunk_index": 0,
                "start_char": 0,
                "end_char": 5,
                "original": "Skaro",
                "spoken_form": "SKA-roh",
                "engine_source": {"kind": "manual", "engine": "reviewed-listening"},
                "fallback": {"strategy": "bypass"},
                "review": {
                    "state": "approved",
                    "reviewer": "fixture-reviewer",
                    "reviewed_at_utc": "2026-08-01T10:00:00Z",
                },
                "provenance": {"source": "operator_review"},
            },
            chunks=chunks,
        )
        (self.root / "pronunciation_registry.json").write_text(
            json.dumps(registry),
            encoding="utf-8",
        )

    def test_single_generation_uses_spoken_text_but_preserves_chunk_and_bypasses_stale_fish_cues(self) -> None:
        chunks = [
            {
                "id": 0,
                "speaker": "NARRATOR",
                "text": "Skaro fell.",
                "instruct": "Calm.",
                "status": "pending",
                "audio_path": None,
                "fish_render_plan": {
                    "schema_version": 1,
                    "text_sha256": "a" * 64,
                    "cues": [],
                },
            }
        ]
        (self.root / "chunks.json").write_text(json.dumps(chunks), encoding="utf-8")
        self.install_registry(chunks)

        success, _path = self.manager.generate_chunk_audio(0)
        self.assertTrue(success)
        self.assertEqual(self.engine.single_calls[0]["text"], "SKA-roh fell.")
        self.assertIsNone(self.engine.single_calls[0]["fish_render_plan"])
        saved = json.loads((self.root / "chunks.json").read_text(encoding="utf-8"))[0]
        self.assertEqual(saved["text"], "Skaro fell.")
        self.assertEqual(saved["pronunciation_applied_count"], 1)
        self.assertEqual(saved["pronunciation_decisions"][0]["original"], "Skaro")
        self.assertEqual(
            saved["pronunciation_fish_inline_plan_bypassed_reason"],
            "pronunciation_changed_plan_text",
        )
        self.assertEqual(saved["audio_state"], "current")

    def test_batch_generation_transforms_only_the_anchored_chunk(self) -> None:
        chunks = [
            {
                "id": 0,
                "speaker": "NARRATOR",
                "text": "Skaro fell.",
                "instruct": "Calm.",
                "status": "pending",
                "audio_path": None,
            },
            {
                "id": 1,
                "speaker": "NARRATOR",
                "text": "Dalek advanced.",
                "instruct": "Calm.",
                "status": "pending",
                "audio_path": None,
            },
        ]
        (self.root / "chunks.json").write_text(json.dumps(chunks), encoding="utf-8")
        self.install_registry(chunks)
        result = self.manager.generate_chunks_batch([0, 1], batch_size=2)
        self.assertEqual(result["completed"], [0, 1])
        request = self.engine.batch_calls[0]
        self.assertEqual(request[0]["text"], "SKA-roh fell.")
        self.assertEqual(request[1]["text"], "Dalek advanced.")
        saved = json.loads((self.root / "chunks.json").read_text(encoding="utf-8"))
        self.assertEqual(saved[0]["text"], "Skaro fell.")
        self.assertEqual(saved[1]["text"], "Dalek advanced.")
        self.assertEqual(saved[0]["pronunciation_applied_count"], 1)
        self.assertIsNone(saved[1].get("pronunciation_request_fingerprint"))

    def test_rebind_refuses_to_certify_audio_after_pronunciation_changed_out_of_band(self) -> None:
        chunks = [
            {
                "id": 0,
                "speaker": "NARRATOR",
                "text": "Skaro fell.",
                "instruct": "Calm.",
                "status": "pending",
                "audio_path": None,
            }
        ]
        (self.root / "chunks.json").write_text(json.dumps(chunks), encoding="utf-8")
        self.install_registry(chunks)
        success, _path = self.manager.generate_chunk_audio(0)
        self.assertTrue(success)
        current = json.loads(
            (self.root / "pronunciation_registry.json").read_text(encoding="utf-8")
        )
        changed = upsert_pronunciation_entry(
            current,
            {
                **current["entries"][0],
                "spoken_form": "SKAIR-oh",
            },
            chunks=chunks,
        )
        (self.root / "pronunciation_registry.json").write_text(
            json.dumps(changed),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "requires regeneration"):
            self.manager.rebind_chunk_audio(
                [0],
                operation_id="unsafe_rebind",
                reason="fixture",
            )


if __name__ == "__main__":
    unittest.main()
