from __future__ import annotations

import tempfile
import unittest
import wave
import hashlib
from pathlib import Path
from types import SimpleNamespace

from tts import TTSEngine


def write_wav(path: Path, *, value: bytes = b"\x01\x00") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24_000)
        handle.writeframes(value * 12_000)


def fish_result(instruction: str):
    normalized = instruction.casefold()
    if "happy" in normalized or "delighted" in normalized:
        instruction_key, style = "excited", "joy"
    elif "sad" in normalized or "vulnerable" in normalized:
        instruction_key, style = "sad", "grief"
    elif "angry" in normalized or "anger" in normalized or "accusatory" in normalized:
        instruction_key, style = "furious", "anger"
    else:
        instruction_key, style = "neutral", "neutral"
    features = {
        "neutral": dict(
            duration_seconds=2.0,
            words_per_second=2.5,
            rms_mean=0.08,
            rms_cv=0.4,
            pitch_cv=0.3,
            silence_ratio=0.08,
        ),
        "excited": dict(
            duration_seconds=1.8,
            words_per_second=3.0,
            rms_mean=0.09,
            rms_cv=0.45,
            pitch_cv=0.42,
            silence_ratio=0.05,
        ),
        "sad": dict(
            duration_seconds=2.8,
            words_per_second=2.0,
            rms_mean=0.065,
            rms_cv=0.55,
            pitch_cv=0.35,
            silence_ratio=0.16,
        ),
        "furious": dict(
            duration_seconds=2.0,
            words_per_second=2.7,
            rms_mean=0.09,
            rms_cv=0.50,
            pitch_cv=0.45,
            silence_ratio=0.04,
        ),
    }[instruction_key]
    return SimpleNamespace(
        style=style,
        selected=SimpleNamespace(
            prompt_key="simple_emotion_tag",
            delivery_score=0.8,
            instruction_delivery_score=0.8,
            identity_score=0.98,
            features=SimpleNamespace(**features),
        ),
    )


def flat_happy_result():
    return SimpleNamespace(
        style="joy",
        selected=SimpleNamespace(
            prompt_key="simple_emotion_tag",
            delivery_score=0.62,
            instruction_delivery_score=0.30,
            identity_score=0.99,
            features=SimpleNamespace(
                duration_seconds=2.0,
                words_per_second=2.5,
                rms_mean=0.00001,
                rms_cv=0.4,
                pitch_cv=0.3,
                silence_ratio=0.08,
            ),
        ),
    )


class VoiceDesignRangePreviewTests(unittest.TestCase):
    def test_physical_identity_isolated_from_persona_then_seeds_four_deliveries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = object.__new__(TTSEngine)
            design_calls = []
            fish_calls = []

            def generate_voice_design(**kwargs):
                design_calls.append(kwargs)
                generated = root / "generated" / f"identity-{len(design_calls)}.wav"
                write_wav(generated, value=bytes([len(design_calls), 0]))
                return str(generated), 24_000

            def generate_with_fish(**kwargs):
                fish_calls.append(kwargs)
                write_wav(Path(kwargs["output_path"]), value=b"\x02\x00")
                return fish_result(kwargs["instruction"])

            engine.generate_voice_design = generate_voice_design
            engine._generate_with_fish = generate_with_fish
            result = engine.generate_voice_design_range_preview(
                description="A compact, precise alto.",
                persona_context="Dry, guarded, and intellectually agile.",
                sample_text="I knew the letter would arrive before dusk.",
                output_dir=root / "previews",
                language="English",
            )

            self.assertEqual(len(design_calls), 1)
            self.assertEqual(
                design_calls[0]["description"],
                "A compact, precise alto.",
            )
            self.assertNotIn(
                "Dry, guarded, and intellectually agile.",
                design_calls[0]["description"],
            )
            self.assertEqual(len(fish_calls), 3)
            self.assertEqual(
                [call["instruction"] for call in fish_calls],
                ["Happy.", "Sad.", "Angry."],
            )
            self.assertTrue(all(
                "Dry, guarded, and intellectually agile."
                not in call["instruction"]
                for call in fish_calls
            ))
            self.assertEqual(
                [call["route_reason"] for call in fish_calls],
                [
                    "audition:happy",
                    "audition:sad",
                    "audition:angry",
                ],
            )
            self.assertTrue(all(call["allow_text_mismatch"] for call in fish_calls))
            self.assertTrue(all(call["max_candidates"] == 1 for call in fish_calls))
            identity_paths = {call["ref_audio"] for call in fish_calls}
            identity_texts = {call["ref_text"] for call in fish_calls}
            self.assertEqual(len(identity_paths), 1)
            self.assertEqual(
                identity_texts,
                {"I knew the letter would arrive before dusk."},
            )
            self.assertTrue(Path(result["identity_seed_path"]).is_file())
            self.assertTrue(Path(result["audio_path"]).is_file())
            self.assertEqual(len(result["preview_fingerprint"]), 64)
            session = (
                root
                / "previews"
                / "voice_design_range_sessions"
                / result["preview_fingerprint"][:20]
            )
            self.assertEqual(
                hashlib.sha256((session / "segment_baseline.wav").read_bytes()).hexdigest(),
                hashlib.sha256(Path(result["identity_seed_path"]).read_bytes()).hexdigest(),
            )
            self.assertEqual(
                result["delivery_backend"],
                "voice_design_identity_plus_fish_s21_cloud",
            )
            self.assertEqual(
                [item["id"] for item in result["sequence"]],
                ["baseline", "happy", "sad", "angry"],
            )
            self.assertEqual(
                [item["style"] for item in result["sequence"]],
                ["neutral", "joy", "grief", "anger"],
            )
            self.assertTrue(all(
                "Dry, guarded, and intellectually agile." in item["instruction"]
                for item in result["sequence"]
            ))
            self.assertEqual(
                {item["reference_identity_mode"] for item in result["sequence"]},
                {"shared_neutral_identity"},
            )
            self.assertEqual(
                [item["text"] for item in result["sequence"]],
                [
                    "I knew the letter would arrive before dusk.",
                    "You're here!",
                    "It still hurts.",
                    "You betrayed me!",
                ],
            )

    def test_audition_uses_short_listen_first_fish_requests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = object.__new__(TTSEngine)
            calls = []
            design_calls = []

            def generate_voice_design(**kwargs):
                design_calls.append(kwargs)
                generated = root / "generated" / f"identity-{len(design_calls)}.wav"
                write_wav(generated, value=bytes([len(design_calls), 0]))
                return str(generated), 24_000

            engine.generate_voice_design = generate_voice_design
            def generate_with_fish(**kwargs):
                calls.append(kwargs)
                write_wav(Path(kwargs["output_path"]), value=b"\x03\x00")
                return fish_result(kwargs["instruction"])

            engine._generate_with_fish = generate_with_fish
            result = engine.generate_voice_design_range_preview(
                description="A compact, precise alto.",
                persona_context="Dry and guarded.",
                sample_text="I knew the letter would arrive before dusk.",
                output_dir=root / "previews",
                language="English",
            )

            self.assertEqual(
                [call["text"] for call in calls],
                ["You're here!", "It still hurts.", "You betrayed me!"],
            )
            self.assertTrue(all(call["allow_text_mismatch"] for call in calls))
            self.assertTrue(all(call["max_candidates"] == 1 for call in calls))
            self.assertEqual(result["sequence"][0]["selected_prompt"], "voice_design_identity")

    def test_flat_emotional_lane_returns_listenable_warning_without_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = object.__new__(TTSEngine)
            calls = []
            design_calls = []

            def generate_voice_design(**kwargs):
                design_calls.append(kwargs)
                generated = root / "generated" / f"identity-{len(design_calls)}.wav"
                write_wav(generated, value=bytes([len(design_calls), 0]))
                return str(generated), 24_000

            def generate_with_fish(**kwargs):
                calls.append(kwargs)
                write_wav(Path(kwargs["output_path"]), value=b"\x04\x00")
                if kwargs["route_reason"] == "audition:happy":
                    return flat_happy_result()
                return fish_result(kwargs["instruction"])

            engine.generate_voice_design = generate_voice_design
            engine._generate_with_fish = generate_with_fish
            result = engine.generate_voice_design_range_preview(
                description="A compact, precise alto.",
                persona_context="Dry and guarded.",
                sample_text="I knew the letter would arrive before dusk.",
                output_dir=root / "previews",
                language="English",
            )

            self.assertEqual(len(calls), 3)
            happy = next(item for item in result["sequence"] if item["id"] == "happy")
            self.assertEqual(happy["variance_status"], "subtle")
            self.assertLess(happy["variance_evidence_count"], 2)
            self.assertFalse(result["all_lanes_distinct"])
            self.assertIn(
                "happy",
                [warning["lane"] for warning in result["warnings"]],
            )
            self.assertTrue(Path(result["audio_path"]).is_file())

    def test_single_lane_regeneration_reuses_identity_and_rebuilds_full_montage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previews = root / "previews"
            engine = object.__new__(TTSEngine)
            design_calls = []
            fish_calls = []

            def generate_voice_design(**kwargs):
                design_calls.append(kwargs)
                generated = root / "generated" / f"identity-{len(design_calls)}.wav"
                write_wav(generated, value=bytes([len(design_calls), 0]))
                return str(generated), 24_000

            def generate_with_fish(**kwargs):
                fish_calls.append(kwargs)
                value = b"\x05\x00" if "manual_regeneration" in kwargs["route_reason"] else b"\x02\x00"
                write_wav(Path(kwargs["output_path"]), value=value)
                return fish_result(kwargs["instruction"])

            engine.generate_voice_design = generate_voice_design
            engine._generate_with_fish = generate_with_fish
            initial = engine.generate_voice_design_range_preview(
                description="A compact, precise alto.",
                persona_context="Dry and guarded.",
                sample_text="I knew the letter would arrive before dusk.",
                output_dir=previews,
                language="English",
            )
            session = previews / "voice_design_range_sessions" / initial["preview_fingerprint"][:20]
            hashes_before = {
                lane: hashlib.sha256((session / f"segment_{lane}.wav").read_bytes()).hexdigest()
                for lane in ("baseline", "happy", "sad", "angry")
            }
            identity_hash = hashlib.sha256(
                Path(initial["identity_seed_path"]).read_bytes()
            ).hexdigest()

            updated = engine.regenerate_voice_design_range_lane(
                preview_fingerprint=initial["preview_fingerprint"],
                lane="angry",
                output_dir=previews,
            )
            hashes_after = {
                lane: hashlib.sha256((session / f"segment_{lane}.wav").read_bytes()).hexdigest()
                for lane in ("baseline", "happy", "sad", "angry")
            }

            self.assertEqual(len(design_calls), 1)
            self.assertEqual(len(fish_calls), 4)
            self.assertEqual(
                fish_calls[-1]["route_reason"],
                "audition:angry:manual_regeneration",
            )
            self.assertEqual(hashes_before["baseline"], hashes_after["baseline"])
            self.assertEqual(hashes_before["happy"], hashes_after["happy"])
            self.assertEqual(hashes_before["sad"], hashes_after["sad"])
            self.assertNotEqual(hashes_before["angry"], hashes_after["angry"])
            self.assertEqual(
                identity_hash,
                hashlib.sha256(Path(updated["identity_seed_path"]).read_bytes()).hexdigest(),
            )
            self.assertEqual(updated["regenerated_lane"], "angry")
            self.assertEqual(updated["revision"], 1)
            self.assertEqual(
                [item["id"] for item in updated["sequence"]],
                ["baseline", "happy", "sad", "angry"],
            )
            self.assertTrue(Path(updated["audio_path"]).is_file())

    def test_full_regeneration_rebuilds_identity_and_all_four_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previews = root / "previews"
            engine = object.__new__(TTSEngine)
            design_calls = []
            fish_calls = []

            def generate_voice_design(**kwargs):
                design_calls.append(kwargs)
                generated = root / "generated" / f"identity-{len(design_calls)}.wav"
                write_wav(generated, value=bytes([len(design_calls), 0]))
                return str(generated), 24_000

            def generate_with_fish(**kwargs):
                fish_calls.append(kwargs)
                write_wav(
                    Path(kwargs["output_path"]),
                    value=bytes([len(fish_calls) + 10, 0]),
                )
                return fish_result(kwargs["instruction"])

            engine.generate_voice_design = generate_voice_design
            engine._generate_with_fish = generate_with_fish

            initial = engine.generate_voice_design_range_preview(
                description="A compact, precise alto.",
                persona_context="Dry and guarded.",
                sample_text="I knew the letter would arrive before dusk.",
                output_dir=previews,
                language="English",
            )
            session = previews / "voice_design_range_sessions" / initial["preview_fingerprint"][:20]
            before = {
                "identity": hashlib.sha256(Path(initial["identity_seed_path"]).read_bytes()).hexdigest(),
                **{
                    lane: hashlib.sha256((session / f"segment_{lane}.wav").read_bytes()).hexdigest()
                    for lane in ("baseline", "happy", "sad", "angry")
                },
            }

            regenerated = engine.generate_voice_design_range_preview(
                description="A compact, precise alto.",
                persona_context="Dry and guarded.",
                sample_text="I knew the letter would arrive before dusk.",
                output_dir=previews,
                language="English",
                force_regenerate=True,
            )
            after = {
                "identity": hashlib.sha256(Path(regenerated["identity_seed_path"]).read_bytes()).hexdigest(),
                **{
                    lane: hashlib.sha256((session / f"segment_{lane}.wav").read_bytes()).hexdigest()
                    for lane in ("baseline", "happy", "sad", "angry")
                },
            }

            self.assertEqual(len(design_calls), 2)
            self.assertEqual(len(fish_calls), 6)
            self.assertEqual(regenerated["status"], "regenerated_all")
            self.assertTrue(regenerated["full_regeneration"])
            self.assertEqual(regenerated["revision"], 1)
            self.assertTrue(all(before[key] != after[key] for key in before))
            self.assertEqual(
                len({call["ref_audio"] for call in fish_calls[3:]}),
                1,
            )


if __name__ == "__main__":
    unittest.main()
