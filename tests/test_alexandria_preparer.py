from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np
import soundfile as sf

from alexandria_preparer import (
    AudioPreparerError,
    prepare_dataset,
    transcript_candidates,
)


class AlexandriaPreparerTests(unittest.TestCase):
    def _write_source(self, root: Path) -> Path:
        sample_rate = 24_000
        waveform = np.zeros(sample_rate * 8, dtype=np.float32)
        for start, end, frequency in (
            (0.9, 2.7, 220.0),
            (3.0, 5.7, 180.0),
        ):
            start_frame = int(start * sample_rate)
            end_frame = int(end * sample_rate)
            timeline = np.arange(end_frame - start_frame) / sample_rate
            waveform[start_frame:end_frame] = (
                0.35 * np.sin(2.0 * np.pi * frequency * timeline)
            ).astype(np.float32)
        source = root / "owned_recording.wav"
        sf.write(source, waveform, sample_rate, subtype="PCM_16")
        return source

    @staticmethod
    def _transcript(probability: float = 0.97) -> dict:
        return {
            "text": "Hello world. This is the second sentence.",
            "segments": [
                {
                    "start": 1.0,
                    "end": 2.5,
                    "text": " Hello world.",
                    "avg_logprob": -0.05,
                    "no_speech_prob": 0.01,
                    "words": [
                        {
                            "word": " Hello",
                            "start": 1.0,
                            "end": 1.45,
                            "probability": probability,
                        },
                        {
                            "word": " world.",
                            "start": 1.45,
                            "end": 2.5,
                            "probability": probability,
                        },
                    ],
                },
                {
                    "start": 3.2,
                    "end": 5.5,
                    "text": " This is the second sentence.",
                    "avg_logprob": -0.08,
                    "no_speech_prob": 0.01,
                    "words": [
                        {
                            "word": " This",
                            "start": 3.2,
                            "end": 3.65,
                            "probability": probability,
                        },
                        {
                            "word": " is",
                            "start": 3.65,
                            "end": 3.9,
                            "probability": probability,
                        },
                        {
                            "word": " the",
                            "start": 3.9,
                            "end": 4.2,
                            "probability": probability,
                        },
                        {
                            "word": " second",
                            "start": 4.2,
                            "end": 4.8,
                            "probability": probability,
                        },
                        {
                            "word": " sentence.",
                            "start": 4.8,
                            "end": 5.5,
                            "probability": probability,
                        },
                    ],
                },
            ],
        }

    def test_word_timestamps_create_reviewable_candidates(self) -> None:
        candidates = transcript_candidates(self._transcript())

        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].text, "Hello world.")
        self.assertEqual(candidates[1].text, "This is the second sentence.")
        self.assertGreater(candidates[0].confidence, 0.95)

    def test_prepare_dataset_writes_atomic_review_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._write_source(root)
            output = root / "prepared.zip"
            calls: list[dict] = []

            def fake_transcriber(path, *, language, model):
                calls.append(
                    {
                        "path": Path(path),
                        "language": language,
                        "model": model,
                    }
                )
                return self._transcript()

            result = prepare_dataset(
                audio_path=source,
                output_path=output,
                language="en",
                min_confidence=0.9,
                min_snr=10.0,
                model="fixture-whisper",
                transcriber=fake_transcriber,
            )

            self.assertEqual(result["sample_count"], 2)
            self.assertTrue(output.is_file())
            self.assertEqual(calls[0]["path"], source.resolve())
            self.assertEqual(calls[0]["language"], "en")
            self.assertEqual(calls[0]["model"], "fixture-whisper")

            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                self.assertTrue(
                    {
                        "sample_0000.wav",
                        "sample_0001.wav",
                        "metadata.jsonl",
                        "preparation_manifest.json",
                        "ref.wav",
                        "ref_text.txt",
                    }.issubset(names)
                )
                metadata = [
                    json.loads(line)
                    for line in archive.read("metadata.jsonl")
                    .decode("utf-8")
                    .splitlines()
                ]
                manifest = json.loads(
                    archive.read("preparation_manifest.json")
                )
                reference_text = archive.read("ref_text.txt").decode("utf-8")

            self.assertEqual(len(metadata), 2)
            self.assertEqual(metadata[0]["ref_audio"], "ref.wav")
            self.assertEqual(metadata[0]["review_status"], "unreviewed")
            self.assertGreaterEqual(metadata[0]["transcript_confidence"], 0.9)
            self.assertGreaterEqual(metadata[0]["snr_db"], 10.0)
            self.assertEqual(manifest["accepted_count"], 2)
            self.assertTrue(manifest["review_required"])
            self.assertTrue(manifest["same_speaker_assertion_required"])
            self.assertIn(reference_text, {item["text"] for item in metadata})

    def test_video_container_extension_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wav_source = self._write_source(root)
            video_source = root / "owned_recording.mp4"
            video_source.write_bytes(wav_source.read_bytes())
            output = root / "prepared-video.zip"

            result = prepare_dataset(
                audio_path=video_source,
                output_path=output,
                language="en",
                min_confidence=0.9,
                min_snr=10.0,
                model="fixture-whisper",
                transcriber=lambda *_args, **_kwargs: self._transcript(),
            )

            self.assertEqual(result["sample_count"], 2)
            self.assertTrue(output.is_file())

    def test_filters_fail_with_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._write_source(root)

            with self.assertRaisesRegex(
                AudioPreparerError,
                "No clips passed the current filters",
            ):
                prepare_dataset(
                    audio_path=source,
                    output_path=root / "rejected.zip",
                    min_confidence=0.95,
                    min_snr=10.0,
                    transcriber=lambda *_args, **_kwargs: self._transcript(0.5),
                )

            self.assertFalse((root / "rejected.zip").exists())
            self.assertFalse((root / "rejected.zip.tmp").exists())

    def test_existing_output_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._write_source(root)
            output = root / "prepared.zip"
            output.write_bytes(b"preserve-existing-dataset")

            with self.assertRaisesRegex(
                AudioPreparerError,
                "will not be overwritten",
            ):
                prepare_dataset(
                    audio_path=source,
                    output_path=output,
                    transcriber=lambda *_args, **_kwargs: self._transcript(),
                )

            self.assertEqual(output.read_bytes(), b"preserve-existing-dataset")
            self.assertFalse((root / "prepared.zip.tmp").exists())

    def test_output_must_be_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._write_source(root)
            with self.assertRaisesRegex(AudioPreparerError, "must end in .zip"):
                prepare_dataset(
                    audio_path=source,
                    output_path=root / "dataset.bin",
                    transcriber=lambda *_args, **_kwargs: self._transcript(),
                )


if __name__ == "__main__":
    unittest.main()
