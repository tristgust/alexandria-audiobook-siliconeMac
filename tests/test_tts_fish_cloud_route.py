from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

from fish_cloud_tts import FishCloudError
from fish_inline_cues import text_sha256
from tts import TTSEngine


def write_wav(path: Path, *, frames: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\x01\x00" * frames)


class Selected:
    prompt_key = "full_alexandria_tag"
    word_error_rate = 0.0
    identity_score = 0.94
    delivery_score = 0.91


class FishResult:
    style = "grief"
    selected = Selected()
    candidates = (Selected(), Selected())

    def metadata(self):
        return {
            "cloud_provider": "fish_s21_cloud",
            "cloud_model": "s2.1-pro-free",
            "cloud_style_route": self.style,
            "cloud_prompt_variant": self.selected.prompt_key,
            "cloud_candidate_count": 2,
            "cloud_text_validation_passed": True,
            "cloud_word_error_rate": 0.0,
            "cloud_identity_score": 0.94,
            "cloud_delivery_score": 0.91,
            "cloud_auto_selected": True,
            "cloud_manual_review_required": False,
        }


class FakeFishBackend:
    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        write_wav(Path(kwargs["output_path"]))
        return FishResult()


class FailingFishBackend:
    def generate(self, **kwargs):
        raise FishCloudError("fish_key_missing", "Fish API key is unavailable.")


class FakeMLXBackend:
    def __init__(self):
        self.calls = []

    def generate_instruction_controlled_clone(self, **kwargs):
        self.calls.append(kwargs)
        write_wav(Path(kwargs["output_path"]))
        return True


class TTSFishCloudRouteTests(unittest.TestCase):
    def test_fish_clone_routes_to_auto_selection_and_records_metadata(self):
        engine = TTSEngine(
            {
                "tts": {
                    "mode": "local",
                    "fish_cloud_enabled": True,
                    "fish_model": "s2.1-pro-free",
                    "fish_candidate_count": 2,
                    "fish_difficult_candidate_count": 4,
                }
            }
        )
        backend = FakeFishBackend()
        engine._fish_backend = backend
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            output = root / "output.wav"
            write_wav(reference)
            voice_config = {
                "DOCTOR": {
                    "type": "clone",
                    "clone_backend": "fish_s21_cloud",
                    "ref_audio": str(reference),
                    "ref_text": "The portal remains open.",
                }
            }
            success = engine.generate_voice(
                "There was no goodbye.",
                "Deep grief, close to breaking.",
                "DOCTOR",
                voice_config,
                str(output),
            )
            metadata = engine.pop_generation_metadata(output)
        self.assertTrue(success)
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(backend.calls[0]["speaker"], "DOCTOR")
        self.assertEqual(metadata["cloud_provider"], "fish_s21_cloud")
        self.assertEqual(metadata["cloud_prompt_variant"], "full_alexandria_tag")
        self.assertTrue(metadata["cloud_auto_selected"])
        self.assertFalse(metadata["cloud_manual_review_required"])

    def test_fish_clone_passes_midline_render_plan_and_records_provenance(self):
        engine = TTSEngine(
            {"tts": {"mode": "local", "fish_cloud_enabled": True}}
        )
        backend = FakeFishBackend()
        engine._fish_backend = backend
        text = "I thought I was ready. I wasn't."
        render_plan = {
            "schema_version": 1,
            "text_sha256": text_sha256(text),
            "cues": [
                {
                    "anchor": "before_phrase",
                    "phrase": "I wasn't",
                    "occurrence": 1,
                    "tag": "voice breaking",
                    "kind": "delivery",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            output = root / "output.wav"
            write_wav(reference)
            success = engine.generate_voice(
                text,
                "Restrained at first, then emotionally breaking.",
                "DOCTOR",
                {
                    "DOCTOR": {
                        "type": "clone",
                        "clone_backend": "fish_s21_cloud",
                        "ref_audio": str(reference),
                        "ref_text": "Reference words.",
                    }
                },
                str(output),
                fish_render_plan=render_plan,
            )
            metadata = engine.pop_generation_metadata(output)
        self.assertTrue(success)
        self.assertEqual(backend.calls[0]["render_plan"], render_plan)
        self.assertEqual(metadata["cloud_inline_cue_count"], 1)
        self.assertRegex(metadata["cloud_render_plan_fingerprint"], r"^[0-9a-f]{64}$")

    def test_hybrid_grief_routes_to_fish_and_records_actual_provenance(self):
        engine = TTSEngine(
            {"tts": {"mode": "local", "fish_cloud_enabled": True}}
        )
        backend = FakeFishBackend()
        engine._fish_backend = backend
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            output = root / "output.wav"
            write_wav(reference)
            voice = {
                "type": "clone",
                "clone_backend": "qwen3_instruction_controlled",
                "ref_audio": str(reference),
                "ref_text": "Reference words.",
                "fish_hybrid_enabled": True,
                "fish_hybrid_styles": ["grief"],
            }
            success = engine.generate_voice(
                "There was no goodbye.",
                "Deep grief, close to breaking.",
                "NARRATOR",
                {"NARRATOR": voice},
                str(output),
            )
            metadata = engine.pop_generation_metadata(output)
        self.assertTrue(success)
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(metadata["fish_route_mode"], "hybrid")
        self.assertFalse(metadata["fish_hybrid_fallback_used"])
        self.assertEqual(
            metadata["generation_provenance"]["voice_method"],
            "fish_s21_cloud",
        )

    def test_backend_specific_directions_route_to_the_correct_generator(self):
        engine = TTSEngine(
            {"tts": {"mode": "local", "fish_cloud_enabled": True}}
        )
        fish = FakeFishBackend()
        engine._fish_backend = fish
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            output = root / "output.wav"
            write_wav(reference)
            success = engine.generate_voice(
                "I thought I was ready. I wasn't.",
                "Qwen: begin controlled, then fracture with disbelief.",
                "NARRATOR",
                {
                    "NARRATOR": {
                        "type": "clone",
                        "clone_backend": "fish_s21_cloud",
                        "ref_audio": str(reference),
                        "ref_text": "Reference words.",
                    }
                },
                str(output),
                fish_instruction="Fish: controlled, then voice breaking.",
            )
        self.assertTrue(success)
        self.assertEqual(
            fish.calls[0]["instruction"],
            "Fish: controlled, then voice breaking.",
        )

        fallback = TTSEngine(
            {"tts": {"mode": "local", "fish_cloud_enabled": True}}
        )
        fallback._fish_backend = FailingFishBackend()
        mlx = FakeMLXBackend()
        fallback._mlx_backend = mlx
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            output = root / "output.wav"
            write_wav(reference)
            success = fallback.generate_voice(
                "I thought I was ready. I wasn't.",
                "Qwen: begin controlled, then fracture with disbelief.",
                "NARRATOR",
                {
                    "NARRATOR": {
                        "type": "clone",
                        "clone_backend": "qwen3_instruction_controlled",
                        "ref_audio": str(reference),
                        "ref_text": "Reference words.",
                        "fish_hybrid_enabled": True,
                        "fish_hybrid_styles": ["expressive"],
                    }
                },
                str(output),
                fish_instruction="Fish: controlled, then voice breaking.",
            )
        self.assertTrue(success)
        self.assertIn(
            "Qwen: begin controlled, then fracture with disbelief.",
            mlx.calls[0]["instruct"],
        )
        self.assertNotIn("Fish:", mlx.calls[0]["instruct"])

    def test_hybrid_neutral_line_stays_local(self):
        engine = TTSEngine(
            {"tts": {"mode": "local", "fish_cloud_enabled": True}}
        )
        fish = FakeFishBackend()
        mlx = FakeMLXBackend()
        engine._fish_backend = fish
        engine._mlx_backend = mlx
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            output = root / "output.wav"
            write_wav(reference)
            success = engine.generate_voice(
                "The door opened.",
                "Neutral, natural clear delivery.",
                "NARRATOR",
                {
                    "NARRATOR": {
                        "type": "clone",
                        "clone_backend": "qwen3_instruction_controlled",
                        "ref_audio": str(reference),
                        "ref_text": "Reference words.",
                        "fish_hybrid_enabled": True,
                    }
                },
                str(output),
            )
        self.assertTrue(success)
        self.assertEqual(fish.calls, [])
        self.assertEqual(len(mlx.calls), 1)

    def test_hybrid_fish_failure_falls_back_to_local(self):
        engine = TTSEngine(
            {"tts": {"mode": "local", "fish_cloud_enabled": True}}
        )
        engine._fish_backend = FailingFishBackend()
        mlx = FakeMLXBackend()
        engine._mlx_backend = mlx
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            output = root / "output.wav"
            write_wav(reference)
            success = engine.generate_voice(
                "There was no goodbye.",
                "Deep grief, close to breaking.",
                "NARRATOR",
                {
                    "NARRATOR": {
                        "type": "clone",
                        "clone_backend": "qwen3_instruction_controlled",
                        "ref_audio": str(reference),
                        "ref_text": "Reference words.",
                        "fish_hybrid_enabled": True,
                    }
                },
                str(output),
            )
            metadata = engine.pop_generation_metadata(output)
        self.assertTrue(success)
        self.assertEqual(len(mlx.calls), 1)
        self.assertTrue(metadata["fish_hybrid_attempted"])
        self.assertTrue(metadata["fish_hybrid_fallback_used"])
        self.assertEqual(
            metadata["generation_provenance"]["voice_method"],
            "qwen3_instruction_controlled",
        )

    def test_fish_clone_is_not_reported_as_seed_deterministic(self):
        engine = TTSEngine({"tts": {"fish_cloud_enabled": True}})
        self.assertFalse(
            engine.supports_generation_seed(
                {"type": "clone", "clone_backend": "fish_s21_cloud"}
            )
        )

    def test_disabled_fish_fails_before_network_use(self):
        engine = TTSEngine({"tts": {"fish_cloud_enabled": False}})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            write_wav(reference)
            with self.assertRaisesRegex(Exception, "disabled"):
                engine.generate_voice(
                    "Exact words.",
                    "Fearful.",
                    "NARRATOR",
                    {
                        "NARRATOR": {
                            "type": "clone",
                            "clone_backend": "fish_s21_cloud",
                            "ref_audio": str(reference),
                            "ref_text": "Reference words.",
                        }
                    },
                    str(root / "output.wav"),
                )


if __name__ == "__main__":
    unittest.main()
