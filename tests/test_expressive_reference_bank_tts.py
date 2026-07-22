from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tts import TTSEngine


class FakeMLXBackend:
    def __init__(self) -> None:
        self.clone_calls = []
        self.batch_calls = []

    def generate_clone(
        self,
        *,
        text,
        ref_audio,
        ref_text,
        output_path,
    ):
        self.clone_calls.append(
            {
                "text": text,
                "ref_audio": ref_audio,
                "ref_text": ref_text,
                "output_path": output_path,
            }
        )
        return True

    def generate_clone_batch(self, chunks, voice_config, output_dir):
        self.batch_calls.append(
            {
                "chunks": chunks,
                "voice_config": voice_config,
                "output_dir": output_dir,
            }
        )
        return {
            "completed": [chunk["index"] for chunk in chunks],
            "failed": [],
        }


class ExpressiveReferenceBankTTSTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.backend = FakeMLXBackend()
        self.engine = TTSEngine({"tts": {"mode": "local"}})
        self.engine._use_mlx = True
        self.engine._mlx_backend = self.backend

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def selection(style: str) -> dict:
        return {
            "style_key": style,
            "mapping_reason": "keyword_match",
            "reference_id": f"reference_{style:0<24}"[:34],
            "ref_audio": f"/tmp/{style}.wav",
            "ref_text": f"Exact {style} reference text.",
            "instruction": f"{style} instruction",
            "bank_fingerprint": "a" * 64,
            "character_id": "character_" + "b" * 20,
        }

    def test_clone_generation_selects_reference_from_instruction(self) -> None:
        voice_config = {
            "THE DOCTOR": {
                "type": "clone",
                "reference_bank_path": (
                    "voice_training_projects/character_aaaaaaaaaaaaaaaaaaaa/"
                    "reference_bank.json"
                ),
                "ref_audio": "fallback.wav",
                "ref_text": "Fallback text.",
            }
        }
        selected = self.selection("urgency")
        with patch(
            "expressive_reference_bank.select_reference_for_instruction",
            return_value=selected,
        ) as resolver:
            result = self.engine.generate_voice(
                "We have to leave now.",
                "Urgent, desperate warning.",
                "THE DOCTOR",
                voice_config,
                str(self.root / "out.wav"),
            )
        self.assertTrue(result)
        resolver.assert_called_once()
        self.assertEqual(
            resolver.call_args.kwargs["instruction"],
            "Urgent, desperate warning.",
        )
        self.assertEqual(self.backend.clone_calls[0]["ref_audio"], "/tmp/urgency.wav")
        self.assertEqual(
            self.backend.clone_calls[0]["ref_text"],
            "Exact urgency reference text.",
        )
        self.assertEqual(voice_config["THE DOCTOR"]["ref_audio"], "fallback.wav")

    def test_expressive_batch_processes_each_line_with_its_style(self) -> None:
        voice_config = {
            "ROZ": {
                "type": "clone",
                "reference_bank_path": (
                    "voice_training_projects/character_aaaaaaaaaaaaaaaaaaaa/"
                    "reference_bank.json"
                ),
                "ref_audio": "fallback.wav",
                "ref_text": "Fallback text.",
            }
        }
        chunks = [
            {
                "index": 1,
                "speaker": "ROZ",
                "text": "Move.",
                "instruct": "Urgent command.",
            },
            {
                "index": 2,
                "speaker": "ROZ",
                "text": "It is all right.",
                "instruct": "Warm reassurance.",
            },
        ]
        selections = [self.selection("urgency"), self.selection("warmth")]
        with (
            patch(
                "expressive_reference_bank.select_reference_for_instruction",
                side_effect=selections,
            ) as resolver,
            patch.object(self.engine, "_clear_gpu_cache"),
        ):
            result = self.engine.generate_batch(
                chunks,
                voice_config,
                str(self.root),
            )
        self.assertEqual(result["completed"], [1, 2])
        self.assertEqual(result["failed"], [])
        self.assertEqual(resolver.call_count, 2)
        self.assertEqual(
            [call["ref_audio"] for call in self.backend.clone_calls],
            ["/tmp/urgency.wav", "/tmp/warmth.wav"],
        )
        self.assertEqual(self.backend.batch_calls, [])

    def test_ordinary_clone_batch_keeps_existing_batch_path(self) -> None:
        voice_config = {
            "NARRATOR": {
                "type": "clone",
                "ref_audio": "ordinary.wav",
                "ref_text": "Ordinary exact text.",
            }
        }
        chunks = [
            {
                "index": 3,
                "speaker": "NARRATOR",
                "text": "The room was quiet.",
                "instruct": "Neutral narration.",
            }
        ]
        with patch.object(self.engine, "_clear_gpu_cache"):
            result = self.engine.generate_batch(
                chunks,
                voice_config,
                str(self.root),
            )
        self.assertEqual(result["completed"], [3])
        self.assertEqual(len(self.backend.batch_calls), 1)
        self.assertEqual(self.backend.clone_calls, [])


if __name__ == "__main__":
    unittest.main()
