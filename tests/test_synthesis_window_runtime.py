from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from audio_artifacts import audio_binding_fingerprint
from audio_artifacts import AudioArtifactError
from project import ProjectManager
from synthesis_windows import (
    SynthesisWindow,
    SynthesisWindowError,
    one_segment_receipt,
    synthesis_receipt_chunk_fields,
)
from tts import TTSEngine


def speech_wave(text: str, sample_rate: int = 1000) -> np.ndarray:
    seconds = max(0.8, len(text) * 0.05)
    count = max(1, round(seconds * sample_rate))
    timeline = np.arange(count, dtype=np.float32) / sample_rate
    return 0.12 * np.sin(2.0 * np.pi * 7.0 * timeline)


def write_speech(path: Path, text: str, sample_rate: int = 1000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, speech_wave(text, sample_rate), sample_rate, subtype="FLOAT")


class SynthesisWindowRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = TTSEngine({"tts": {"mode": "local"}})
        self.voice_config = {
            "NARRATOR": {
                "type": "custom",
                "voice": "Ryan",
            }
        }

    def test_long_request_generates_every_segment_and_publishes_one_atomic_receipt(self) -> None:
        text = (
            "First sentence establishes the setting and keeps its punctuation. "
            "Second sentence is long enough to force another internal request. "
            "Third sentence closes the exact source without losing a character."
        )
        calls = []

        def generate(segment_text, _instruct, _speaker, _config, output_path, **_kwargs):
            calls.append(segment_text)
            write_speech(Path(output_path), segment_text)
            self.engine._record_generation_metadata(
                output_path,
                {
                    "generation_provenance": {
                        "runtime": "fixture-runtime",
                        "voice_method": "fixture-custom",
                    },
                    "fixture_provider": "same-provider",
                },
            )
            return True

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "joined.wav"
            with patch.object(
                self.engine,
                "_generate_voice_unsegmented",
                side_effect=generate,
            ):
                success = self.engine.generate_voice(
                    text,
                    "Calm.",
                    "NARRATOR",
                    self.voice_config,
                    str(output),
                )
            metadata = self.engine.pop_generation_metadata(output)
            info = sf.info(output)
            leftovers = list(Path(temporary).glob(".*.segment_*.tmp.wav"))

        self.assertTrue(success)
        self.assertGreater(len(calls), 1)
        self.assertEqual("".join(calls).replace(" ", ""), text.replace(" ", ""))
        self.assertEqual(metadata["synthesis_segment_count"], len(calls))
        self.assertEqual(
            metadata["generation_provenance"]["runtime"],
            "fixture-runtime",
        )
        self.assertEqual(metadata["fixture_provider"], "same-provider")
        self.assertEqual(
            metadata["synthesis_final_sample_count"],
            int(info.frames),
        )
        self.assertEqual(leftovers, [])

    def test_one_failed_segment_leaves_no_final_or_temporary_output(self) -> None:
        text = "One long sentence " + "with additional words " * 12 + "at the end."
        calls = []

        def generate(segment_text, _instruct, _speaker, _config, output_path, **_kwargs):
            calls.append(segment_text)
            if len(calls) == 2:
                return False
            write_speech(Path(output_path), segment_text)
            return True

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "joined.wav"
            with patch.object(
                self.engine,
                "_generate_voice_unsegmented",
                side_effect=generate,
            ):
                success = self.engine.generate_voice(
                    text,
                    "Calm.",
                    "NARRATOR",
                    self.voice_config,
                    str(output),
                )
            leftovers = list(root.iterdir())

        self.assertFalse(success)
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(leftovers, [])

    def test_incompatible_segment_sample_rates_fail_without_final_output(self) -> None:
        text = "One long sentence " + "with additional words " * 12 + "at the end."
        calls = []

        def generate(segment_text, _instruct, _speaker, _config, output_path, **_kwargs):
            rate = 1000 if not calls else 1200
            calls.append(segment_text)
            write_speech(Path(output_path), segment_text, sample_rate=rate)
            return True

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "joined.wav"
            with (
                patch.object(
                    self.engine,
                    "_generate_voice_unsegmented",
                    side_effect=generate,
                ),
                self.assertRaisesRegex(
                    SynthesisWindowError,
                    "different sample rates",
                ),
            ):
                self.engine.generate_voice(
                    text,
                    "Calm.",
                    "NARRATOR",
                    self.voice_config,
                    str(output),
                )
            leftovers = list(root.iterdir())

        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(leftovers, [])

    def test_multisegment_fish_request_bypasses_phrase_bound_inline_plan(self) -> None:
        fish_config = {
            "DOCTOR": {
                "type": "clone",
                "clone_backend": "fish_s21_cloud",
                "ref_audio": "reference.wav",
                "ref_text": "Reference words.",
            }
        }
        text = "Fish text. " + ("This long request needs safe segmentation. " * 16)
        observed_plans = []

        def generate(segment_text, _instruct, _speaker, _config, output_path, **kwargs):
            observed_plans.append(kwargs.get("fish_render_plan"))
            write_speech(Path(output_path), segment_text)
            return True

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "joined.wav"
            with patch.object(
                self.engine,
                "_generate_voice_unsegmented",
                side_effect=generate,
            ):
                success = self.engine.generate_voice(
                    text,
                    "Controlled.",
                    "DOCTOR",
                    fish_config,
                    str(output),
                    fish_render_plan={
                        "schema_version": 1,
                        "text_sha256": "a" * 64,
                        "cues": [],
                    },
                )
            metadata = self.engine.pop_generation_metadata(output)

        self.assertTrue(success)
        self.assertGreater(len(observed_plans), 1)
        self.assertTrue(all(plan is None for plan in observed_plans))
        self.assertEqual(
            metadata["synthesis_fish_inline_plan_bypassed_reason"],
            "internal_segmentation_changed_plan_text",
        )

    def test_long_batch_item_uses_same_segmented_path_and_records_metadata(self) -> None:
        text = "Batch text. " + ("This sentence must be segmented safely. " * 8)
        calls = []

        def generate(segment_text, _instruct, _speaker, _config, output_path, **_kwargs):
            calls.append(segment_text)
            write_speech(Path(output_path), segment_text)
            return True

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(
                self.engine,
                "_generate_voice_unsegmented",
                side_effect=generate,
            ):
                result = self.engine.generate_batch(
                    [
                        {
                            "index": 7,
                            "speaker": "NARRATOR",
                            "text": text,
                            "instruct": "Calm.",
                        }
                    ],
                    self.voice_config,
                    str(root),
                )
            output = root / "temp_batch_7.wav"
            metadata = self.engine.pop_generation_metadata(output)

        self.assertEqual(result["completed"], [7])
        self.assertEqual(result["failed"], [])
        self.assertGreater(len(calls), 1)
        self.assertGreater(metadata["synthesis_segment_count"], 1)

    def test_one_window_native_batch_output_receives_one_segment_receipt(self) -> None:
        text = "Short native batch line."

        def native(chunks, _config, output_dir, _seed):
            idx = chunks[0]["index"]
            write_speech(Path(output_dir) / f"temp_batch_{idx}.wav", text)
            return {"completed": [idx], "failed": []}

        external = TTSEngine({"tts": {"mode": "external"}})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(external, "_sequential_custom", side_effect=native):
                result = external.generate_batch(
                    [
                        {
                            "index": 3,
                            "speaker": "NARRATOR",
                            "text": text,
                            "instruct": "Calm.",
                        }
                    ],
                    self.voice_config,
                    str(root),
                )
            metadata = external.pop_generation_metadata(root / "temp_batch_3.wav")

        self.assertEqual(result["completed"], [3])
        self.assertEqual(metadata["synthesis_segment_count"], 1)
        self.assertEqual(metadata["synthesis_window_backend"], "external_generic")

    def test_declaration_drift_changes_audio_binding_without_touching_chunk(self) -> None:
        text = "Stable line."
        waveform = speech_wave(text)
        receipt = one_segment_receipt(
            text=text,
            backend_id="qwen3_custom",
            audio=waveform,
            sample_rate=1000,
        )
        chunk = {
            "speaker": "NARRATOR",
            "text": text,
            "instruct": "Calm.",
            **synthesis_receipt_chunk_fields(receipt),
        }
        original = copy.deepcopy(chunk)
        before = audio_binding_fingerprint(
            chunk=chunk,
            resolved_speaker="NARRATOR",
            voice_config=self.voice_config,
            synthesis_config={},
        )
        replacement = SynthesisWindow(
            backend_id="qwen3_custom",
            family="qwen3",
            max_chars=88,
            max_words=None,
            minimum_words=2,
            seam_mode="silence_gap",
            seam_ms=100,
            split_priority=("paragraph", "sentence", "word", "character"),
        )
        with patch.dict(
            "synthesis_windows._WINDOWS",
            {"qwen3_custom": replacement},
            clear=False,
        ):
            after = audio_binding_fingerprint(
                chunk=chunk,
                resolved_speaker="NARRATOR",
                voice_config=self.voice_config,
                synthesis_config={},
            )

        self.assertNotEqual(before, after)
        self.assertEqual(chunk, original)

    def test_project_manager_persists_receipt_and_declaration_drift_blocks_current_audio(self) -> None:
        text = (
            "The first long sentence establishes exact source spans and punctuation. "
            "The second long sentence forces another internal synthesis window."
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "app").mkdir()
            (root / "app" / "config.json").write_text(
                json.dumps({"tts": {"mode": "local", "language": "English"}}),
                encoding="utf-8",
            )
            (root / "voice_config.json").write_text(
                json.dumps(self.voice_config),
                encoding="utf-8",
            )
            (root / "chunks.json").write_text(
                json.dumps(
                    [
                        {
                            "id": 0,
                            "speaker": "NARRATOR",
                            "text": text,
                            "instruct": "Calm.",
                            "status": "pending",
                            "audio_path": None,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            manager = ProjectManager(str(root))
            engine = TTSEngine({"tts": {"mode": "local"}})
            manager.engine = engine

            def generate(segment_text, _instruct, _speaker, _config, output_path, **_kwargs):
                write_speech(Path(output_path), segment_text)
                return True

            with patch.object(
                engine,
                "_generate_voice_unsegmented",
                side_effect=generate,
            ):
                success, _path = manager.generate_chunk_audio(0)
            self.assertTrue(success)
            saved = json.loads((root / "chunks.json").read_text(encoding="utf-8"))[0]
            self.assertEqual(saved["audio_state"], "current")
            self.assertGreater(saved["synthesis_segment_count"], 1)
            self.assertEqual(
                saved["synthesis_seam_receipt"]["segment_count"],
                saved["synthesis_segment_count"],
            )
            self.assertEqual(len(manager._load_chunks_with_audio()), 1)
            canonical = root / saved["audio_path"]
            before_bytes = canonical.read_bytes()

            replacement = SynthesisWindow(
                backend_id="qwen3_custom",
                family="qwen3",
                max_chars=88,
                max_words=None,
                minimum_words=2,
                seam_mode="silence_gap",
                seam_ms=100,
                split_priority=("paragraph", "sentence", "word", "character"),
            )
            with (
                patch.dict(
                    "synthesis_windows._WINDOWS",
                    {"qwen3_custom": replacement},
                    clear=False,
                ),
                self.assertRaises(AudioArtifactError),
            ):
                manager._load_chunks_with_audio()
            self.assertEqual(canonical.read_bytes(), before_bytes)

    def test_segment_failure_preserves_previous_canonical_audio_as_stale_evidence(self) -> None:
        text = "Long replacement. " + ("More source words remain exact. " * 8)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "app").mkdir()
            (root / "app" / "config.json").write_text(
                json.dumps({"tts": {"mode": "local", "language": "English"}}),
                encoding="utf-8",
            )
            (root / "voice_config.json").write_text(
                json.dumps(self.voice_config),
                encoding="utf-8",
            )
            previous = root / "voicelines" / "previous.wav"
            write_speech(previous, text)
            previous_bytes = previous.read_bytes()
            (root / "chunks.json").write_text(
                json.dumps(
                    [
                        {
                            "id": 0,
                            "speaker": "NARRATOR",
                            "text": text,
                            "instruct": "Calm.",
                            "status": "done",
                            "audio_state": "current",
                            "audio_path": "voicelines/previous.wav",
                            "audio_sha256": "fixture",
                            "audio_fingerprint": "fixture",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            manager = ProjectManager(str(root))
            engine = TTSEngine({"tts": {"mode": "local"}})
            manager.engine = engine
            calls = []

            def generate(segment_text, _instruct, _speaker, _config, output_path, **_kwargs):
                calls.append(segment_text)
                if len(calls) == 2:
                    return False
                write_speech(Path(output_path), segment_text)
                return True

            with patch.object(
                engine,
                "_generate_voice_unsegmented",
                side_effect=generate,
            ):
                success, _message = manager.generate_chunk_audio(0)
            self.assertFalse(success)
            saved = json.loads((root / "chunks.json").read_text(encoding="utf-8"))[0]
            self.assertEqual(saved["audio_state"], "failed")
            self.assertEqual(saved["stale_audio_path"], "voicelines/previous.wav")
            self.assertEqual(previous.read_bytes(), previous_bytes)
            self.assertIsNone(saved.get("synthesis_seam_receipt_fingerprint"))


if __name__ == "__main__":
    unittest.main()
