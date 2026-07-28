from __future__ import annotations

import gc
import tempfile
import unittest
import wave
import warnings
from pathlib import Path
from unittest.mock import patch

from audio_artifacts import validate_audio_file


class StubSegment:
    def __len__(self) -> int:
        return 100


def write_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x00\x00" * 1_600)


class AudioArtifactDecoderPathTests(unittest.TestCase):
    def test_default_probed_decoder_receives_filesystem_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / "generated.mp3"
            audio_path.write_bytes(b"synthetic-audio")

            with patch(
                "audio_artifacts.AudioSegment.from_file",
                return_value=StubSegment(),
            ) as decoder:
                validate_audio_file(audio_path)

            source = decoder.call_args.args[0]
            self.assertIsInstance(source, Path)
            self.assertEqual(source, audio_path.resolve())

    def test_default_wav_decoder_receives_managed_stream_and_explicit_format(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / "generated.wav"
            write_wav(audio_path)

            with patch(
                "audio_artifacts.AudioSegment.from_file",
                return_value=StubSegment(),
            ) as decoder:
                validate_audio_file(audio_path)

            source = decoder.call_args.args[0]
            self.assertTrue(hasattr(source, "read"))
            self.assertTrue(source.closed)
            self.assertEqual(decoder.call_args.kwargs["format"], "wav")

    def test_default_wav_validation_releases_every_file_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / "generated.wav"
            write_wav(audio_path)

            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ResourceWarning)
                for _ in range(64):
                    validate_audio_file(audio_path)
                gc.collect()

            self.assertFalse(
                any(item.category is ResourceWarning for item in caught),
                [str(item.message) for item in caught],
            )

    def test_injected_decoder_retains_open_stream_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / "generated.wav"
            audio_path.write_bytes(b"synthetic-audio")
            received = []

            def decoder(source, *, format=None):
                received.append(source)
                return StubSegment()

            validate_audio_file(audio_path, decoder=decoder)

            self.assertEqual(len(received), 1)
            self.assertTrue(hasattr(received[0], "read"))
            self.assertTrue(received[0].closed)


if __name__ == "__main__":
    unittest.main()
