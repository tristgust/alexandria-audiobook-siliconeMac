from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path

from audio_generation_policy import (
    AudioGenerationPolicyError,
    apply_generation_seed_to_voice_config,
    resolve_generation_seed,
    voice_supports_deterministic_seed,
)
from project import ProjectManager
from tts import TTSEngine


def write_wav(path: Path, *, frames: int = 2400) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\x00\x00" * frames)


class SeedCapturingEngine:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.batch_chunks: list[dict] = []

    def generate_voice(self, text, instruct, speaker, voice_config, output_path):
        self.calls.append(
            {
                "text": text,
                "instruct": instruct,
                "speaker": speaker,
                "seed": voice_config[speaker].get("seed"),
            }
        )
        write_wav(Path(output_path))
        return True

    def generate_batch(self, chunks, voice_config, output_dir, seed):
        self.batch_chunks.extend(dict(item) for item in chunks)
        for item in chunks:
            write_wav(Path(output_dir) / f"temp_batch_{item['index']}.wav")
        return {
            "completed": [item["index"] for item in chunks],
            "failed": [],
        }


class AudioGenerationPolicyTests(unittest.TestCase):
    def controlled_voice(self, *, seed=-1):
        return {
            "type": "clone",
            "clone_backend": "qwen3_instruction_controlled",
            "ref_audio": "clone_voices/doctor.wav",
            "ref_text": "Exact reference transcript.",
            "seed": seed,
        }

    def test_derived_seed_is_stable_and_content_bound(self) -> None:
        voice_config = {"DOCTOR": self.controlled_voice()}
        chunk = {
            "id": 7,
            "speaker": "DOCTOR",
            "text": "Run.",
            "instruct": "Urgent warning.",
        }
        first = resolve_generation_seed(
            chunk=chunk,
            resolved_speaker="DOCTOR",
            voice_config=voice_config,
            synthesis_config={"language": "English"},
        )
        second = resolve_generation_seed(
            chunk=dict(chunk),
            resolved_speaker="DOCTOR",
            voice_config=voice_config,
            synthesis_config={"language": "English"},
        )
        changed = resolve_generation_seed(
            chunk={**chunk, "text": "Stop."},
            resolved_speaker="DOCTOR",
            voice_config=voice_config,
            synthesis_config={"language": "English"},
        )
        self.assertTrue(first["supported"])
        self.assertEqual(first["source"], "derived")
        self.assertEqual(first["seed"], second["seed"])
        self.assertNotEqual(first["seed"], changed["seed"])
        self.assertNotEqual(
            first["basis_fingerprint"],
            changed["basis_fingerprint"],
        )

    def test_seed_precedence_and_backend_capability_are_explicit(self) -> None:
        voice_config = {"DOCTOR": self.controlled_voice(seed="4242")}
        chunk = {"id": 0, "speaker": "DOCTOR", "text": "Hello.", "instruct": ""}
        configured = resolve_generation_seed(
            chunk=chunk,
            resolved_speaker="DOCTOR",
            voice_config=voice_config,
        )
        explicit = resolve_generation_seed(
            chunk=chunk,
            resolved_speaker="DOCTOR",
            voice_config=voice_config,
            explicit_seed=99,
        )
        unsupported = resolve_generation_seed(
            chunk=chunk,
            resolved_speaker="DOCTOR",
            voice_config={"DOCTOR": {"type": "custom", "voice": "Ryan"}},
        )
        self.assertEqual(configured["seed"], 4242)
        self.assertEqual(configured["source"], "voice_config")
        self.assertEqual(explicit["seed"], 99)
        self.assertEqual(explicit["source"], "explicit_request")
        self.assertFalse(unsupported["supported"])
        self.assertIsNone(unsupported["seed"])
        self.assertFalse(
            voice_supports_deterministic_seed(
                {"type": "clone", "clone_backend": "qwen3_base"}
            )
        )
        with self.assertRaisesRegex(
            AudioGenerationPolicyError,
            "does not support deterministic seeds",
        ):
            resolve_generation_seed(
                chunk=chunk,
                resolved_speaker="DOCTOR",
                voice_config={
                    "DOCTOR": {"type": "custom", "voice": "Ryan"}
                },
                explicit_seed=42,
                seed_supported=False,
            )

    def test_matching_persisted_seed_is_reused(self) -> None:
        voice_config = {"DOCTOR": self.controlled_voice()}
        chunk = {"id": 3, "speaker": "DOCTOR", "text": "Hello.", "instruct": "Dry."}
        initial = resolve_generation_seed(
            chunk=chunk,
            resolved_speaker="DOCTOR",
            voice_config=voice_config,
        )
        persisted = {
            **chunk,
            "generation_seed": initial["seed"],
            "generation_seed_basis": initial["basis_fingerprint"],
        }
        repeated = resolve_generation_seed(
            chunk=persisted,
            resolved_speaker="DOCTOR",
            voice_config=voice_config,
        )
        self.assertEqual(repeated["source"], "persisted_derived")
        self.assertEqual(repeated["seed"], initial["seed"])

    def test_runtime_capability_distinguishes_mlx_and_seeded_backends(self) -> None:
        engine = TTSEngine({"tts": {"mode": "local"}})
        engine._use_mlx = True
        self.assertFalse(
            engine.supports_generation_seed(
                {"type": "custom", "voice": "Ryan"}
            )
        )
        self.assertFalse(
            engine.supports_generation_seed(
                {"type": "clone", "clone_backend": "qwen3_base"}
            )
        )
        self.assertTrue(
            engine.supports_generation_seed(self.controlled_voice())
        )
        engine._use_mlx = False
        self.assertTrue(
            engine.supports_generation_seed(
                {"type": "custom", "voice": "Ryan"},
                batch=True,
                shared_seed=True,
            )
        )
        self.assertFalse(
            engine.supports_generation_seed(
                {"type": "custom", "voice": "Ryan"},
                batch=True,
                shared_seed=False,
            )
        )

    def test_effective_voice_config_injects_without_mutating_source(self) -> None:
        original = {"DOCTOR": self.controlled_voice()}
        effective = apply_generation_seed_to_voice_config(
            original,
            resolved_speaker="DOCTOR",
            resolution={"supported": True, "seed": 17},
        )
        self.assertEqual(effective["DOCTOR"]["seed"], 17)
        self.assertEqual(original["DOCTOR"]["seed"], -1)


class ProjectDeterministicSeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "app").mkdir()
        (self.root / "app" / "config.json").write_text(
            json.dumps(
                {
                    "tts": {
                        "language": "English",
                        "deterministic_seed_enabled": True,
                        "deterministic_seed_base": 20260726,
                    }
                }
            ),
            encoding="utf-8",
        )
        (self.root / "voice_config.json").write_text(
            json.dumps(
                {
                    "DOCTOR": {
                        "type": "clone",
                        "clone_backend": "qwen3_instruction_controlled",
                        "ref_audio": "clone_voices/doctor.wav",
                        "ref_text": "Exact transcript.",
                        "seed": "-1",
                    }
                }
            ),
            encoding="utf-8",
        )
        self.manager = ProjectManager(str(self.root))
        self.engine = SeedCapturingEngine()
        self.manager.engine = self.engine

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_chunks(self, count=1) -> None:
        chunks = [
            {
                "id": index,
                "speaker": "DOCTOR",
                "text": f"Line {index}.",
                "instruct": "Playful.",
                "status": "pending",
                "audio_path": None,
            }
            for index in range(count)
        ]
        (self.root / "chunks.json").write_text(
            json.dumps(chunks),
            encoding="utf-8",
        )

    def read_chunks(self):
        return json.loads((self.root / "chunks.json").read_text(encoding="utf-8"))

    def test_single_generation_retry_reuses_persisted_seed(self) -> None:
        self.write_chunks()
        first_ok, _ = self.manager.generate_chunk_audio(0)
        first_chunk = self.read_chunks()[0]
        second_ok, _ = self.manager.generate_chunk_audio(0)
        second_chunk = self.read_chunks()[0]
        self.assertTrue(first_ok and second_ok)
        self.assertEqual(self.engine.calls[0]["seed"], self.engine.calls[1]["seed"])
        self.assertEqual(first_chunk["generation_seed"], second_chunk["generation_seed"])
        self.assertEqual(second_chunk["generation_seed_source"], "persisted_derived")
        self.assertEqual(len(second_chunk["audio_fingerprint"]), 64)
        self.assertEqual(len(self.manager._load_chunks_with_audio()), 1)

    def test_synthesis_edit_clears_seed_and_derives_a_new_one(self) -> None:
        self.write_chunks()
        self.manager.generate_chunk_audio(0)
        first_seed = self.read_chunks()[0]["generation_seed"]
        changed = self.manager.update_chunk(0, {"instruct": "Grave warning."})
        self.assertIsNone(changed["generation_seed"])
        self.assertIsNone(changed["generation_seed_basis"])
        self.manager.generate_chunk_audio(0)
        second_seed = self.read_chunks()[0]["generation_seed"]
        self.assertNotEqual(first_seed, second_seed)

    def test_parallel_explicit_seed_is_forwarded_to_every_chunk(self) -> None:
        self.write_chunks(count=2)
        result = self.manager.generate_chunks_parallel(
            [0, 1],
            max_workers=1,
            generation_seed=77,
        )
        self.assertEqual(sorted(result["completed"]), [0, 1])
        self.assertEqual([call["seed"] for call in self.engine.calls], [77, 77])
        self.assertEqual(
            [chunk["generation_seed"] for chunk in self.read_chunks()],
            [77, 77],
        )

    def test_fast_batch_carries_per_chunk_seed_metadata(self) -> None:
        self.write_chunks(count=2)
        result = self.manager.generate_chunks_batch(
            [0, 1],
            batch_seed=88,
            batch_size=2,
        )
        self.assertEqual(result["completed"], [0, 1])
        self.assertEqual(
            [item["generation_seed"] for item in self.engine.batch_chunks],
            [88, 88],
        )
        self.assertEqual(
            [chunk["generation_seed"] for chunk in self.read_chunks()],
            [88, 88],
        )


if __name__ == "__main__":
    unittest.main()
