from __future__ import annotations

import json
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

from project import ProjectManager
from audio_generation_lifecycle import (
    claim_request,
    finalize_request,
    load_request,
    normalize_request_manifest,
    prepare_request,
    request_context,
)


def write_wav(path: Path, *, frames: int = 96000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\x01\x00" * frames)


def write_stereo_sound(path: Path, *, seconds: float = 3.5) -> dict:
    sample_rate = 44100
    frames = round(sample_rate * seconds)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x01\x00\x02\x00" * frames)
    return {
        "schema_version": 1,
        "backend_id": "stable_audio_open_small",
        "model_revision": "d" * 40,
        "request_fingerprint": "r" * 64,
        "prompt_sha256": "p" * 64,
        "device": "mps",
        "duration_requested_seconds": seconds,
        "sample_rate": sample_rate,
        "sample_count": frames,
        "channels": 2,
        "generation_seconds": 1.25,
        "seed": 130363,
        "steps": 8,
        "cfg_scale": 1.0,
        "sampler": "pingpong",
    }


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

    def test_single_sound_effect_generates_without_speech_engine_initialization(self) -> None:
        self.manager.get_engine = Mock(
            side_effect=AssertionError("speech engine must not initialize")
        )
        with patch(
            "project.generate_sound_effect_audio",
            side_effect=lambda **kwargs: write_stereo_sound(
                Path(kwargs["output_path"])
            ),
        ) as generate:
            success, message = self.manager.generate_chunk_audio(0)
        self.assertTrue(success, message)
        self.manager.get_engine.assert_not_called()
        self.assertEqual(generate.call_count, 1)
        request = generate.call_args.kwargs["request"]
        self.assertIn("Domestic cat meows", request["prompt"])
        self.assertIn("questioning meow", request["prompt"])
        saved = self.read_chunks()[0]
        self.assertEqual(saved["audio_state"], "current")
        self.assertEqual(saved["audio_content_kind"], "sound_effect")
        self.assertEqual(saved["sound_effect_backend"], "stable_audio_open_small")
        self.assertTrue((self.root / saved["audio_path"]).is_file())
        registry = json.loads(
            (self.root / "audio_takes.json").read_text(encoding="utf-8")
        )
        take = registry["takes"][saved["current_take_id"]]
        self.assertEqual(
            take["generation"]["chunk_audio_fields"]["audio_content_kind"],
            "sound_effect",
        )
        self.assertEqual(take["artifact"]["sample_rate"], 44100)
        self.assertEqual(take["artifact"]["channels"], 2)

    def test_mixed_batch_generates_sound_and_speech_through_separate_backends(self) -> None:
        engine = BatchEngine()
        self.manager.engine = engine
        with patch(
            "project.generate_sound_effect_audio",
            side_effect=lambda **kwargs: write_stereo_sound(
                Path(kwargs["output_path"])
            ),
        ):
            result = self.manager.generate_chunks_batch([0, 1], batch_size=2)
        self.assertEqual(result["completed"], [0, 1])
        self.assertEqual(result["failed"], [])
        self.assertEqual(len(engine.batch_calls), 1)
        self.assertEqual([item["index"] for item in engine.batch_calls[0]], [1])
        self.assertEqual([item["speaker"] for item in engine.batch_calls[0]], ["NARRATOR"])
        saved = self.read_chunks()
        self.assertEqual(saved[0]["audio_state"], "current")
        self.assertEqual(saved[0]["audio_content_kind"], "sound_effect")
        self.assertEqual(saved[1]["audio_state"], "current")

    def test_exact_once_sound_effect_completes_internal_unit_and_take(self) -> None:
        self.manager.get_engine = Mock(
            side_effect=AssertionError("sound-only manifest must not initialize TTS")
        )
        arguments = {
            "indices": [0],
            "mode": "parallel",
            "operation_mode": "selected",
            "generation_seed": None,
            "plan_fingerprint": "1" * 64,
            "chunks_fingerprint": "2" * 64,
            "execution": {"worker_count": 1},
        }
        manifest = self.manager.build_audio_generation_manifest(
            arguments["indices"],
            mode=arguments["mode"],
            operation_mode=arguments["operation_mode"],
            generation_seed=arguments["generation_seed"],
            plan_fingerprint=arguments["plan_fingerprint"],
            chunks_fingerprint=arguments["chunks_fingerprint"],
        )
        manifest["execution"] = arguments["execution"]
        prepared = prepare_request(
            self.root,
            normalize_request_manifest(manifest),
        )
        record = prepared["record"]
        claimed = claim_request(
            self.root,
            record["request_id"],
            expected_request_fingerprint=record["request_fingerprint"],
            owner_process_id=os.getpid(),
        )
        context = request_context(
            self.root,
            record["request_id"],
            claimed["owner_token"],
            "chunk:0",
        )
        context["manifest_request"] = arguments
        with patch(
            "project.generate_sound_effect_audio",
            side_effect=lambda **kwargs: write_stereo_sound(
                Path(kwargs["output_path"])
            ),
        ):
            success, message = self.manager.generate_chunk_audio(
                0,
                generation_context=context,
            )
        self.assertTrue(success, message)
        terminal = finalize_request(
            self.root,
            record["request_id"],
            claimed["owner_token"],
        )
        self.assertEqual(terminal["state"], "succeeded")
        durable = load_request(self.root, record["request_id"])
        progress = durable["progress"]["chunk:0"]
        self.assertEqual(progress["state"], "completed")
        segment = next(iter(progress["segments"].values()))
        self.assertEqual(segment["state"], "completed")
        self.assertTrue(Path(segment["artifact"]["path"]).is_file())
        saved = self.read_chunks()[0]
        self.assertEqual(saved["audio_content_kind"], "sound_effect")
        self.assertIsNotNone(saved["current_take_id"])
        self.manager.get_engine.assert_not_called()


if __name__ == "__main__":
    unittest.main()
