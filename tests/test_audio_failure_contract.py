from __future__ import annotations

import unittest

from audio_processing import GeneratedSpeechTooShortError
from produce_aggregate import build_produce_aggregate

from tests.audio_failure_support import (
    AudioFailureProjectMixin,
    BoundedFailureEngine,
    FailedBatchEngine,
)


class AudioFailureContractTests(AudioFailureProjectMixin, unittest.TestCase):
    def test_single_failure_persists_and_serializes_exact_cause(self) -> None:
        message = str(
            GeneratedSpeechTooShortError(
                "Generated speech is too short for the requested text "
                "(0.32s for 5 characters)."
            )
        )
        self.write_chunks([self.chunk(0)])
        self.manager.engine = BoundedFailureEngine(message)

        success, returned = self.manager.generate_chunk_audio(0)

        self.assertFalse(success)
        self.assertEqual(returned, message)
        persisted = self.read_chunks()[0]
        self.assertEqual(persisted["status"], "error")
        self.assertEqual(persisted["audio_state"], "failed")
        self.assertEqual(persisted["error"], message)
        self.assertEqual(persisted["error_code"], "audio_duration_insufficient")

        aggregate = build_produce_aggregate(
            root_dir=self.root,
            chunks=self.read_chunks(),
            voice_config={"NARRATOR": {"type": "custom", "voice": "Ryan"}},
            config={"tts": {"language": "English"}},
            cast=self.cast(),
        )
        row = aggregate["chunks"][0]
        self.assertEqual(row["error"], message)
        self.assertEqual(row["error_code"], "audio_duration_insufficient")
        self.assertEqual(row["blockers"][0]["explanation"], message)

    def test_batch_failure_persists_each_returned_cause(self) -> None:
        message = "Generated speech is too long for the requested text (8.08s for 12 characters)."
        self.write_chunks([self.chunk(0), self.chunk(1)])
        self.manager.engine = FailedBatchEngine(message)

        result = self.manager.generate_chunks_batch([0, 1], batch_size=2)

        self.assertEqual(result["completed"], [])
        self.assertEqual(result["failed"], [(0, message), (1, message)])
        for chunk in self.read_chunks():
            self.assertEqual(chunk["status"], "error")
            self.assertEqual(chunk["audio_state"], "failed")
            self.assertEqual(chunk["error"], message)
            self.assertEqual(chunk["error_code"], "audio_duration_excessive")

    def test_legacy_and_sensitive_errors_fall_back_at_public_boundary(self) -> None:
        chunks = [
            {**self.chunk(0), "status": "error", "audio_state": "failed"},
            {
                **self.chunk(1),
                "status": "error",
                "audio_state": "failed",
                "error": "/Users/tristan/private/provider-payload.json",
                "error_code": "provider_raw_error",
            },
            {
                **self.chunk(2),
                "status": "error",
                "audio_state": "failed",
                "error": {"secret": "/Users/tristan/private"},
            },
        ]
        aggregate = build_produce_aggregate(
            root_dir=self.root,
            chunks=chunks,
            voice_config={"NARRATOR": {"type": "custom", "voice": "Ryan"}},
            config={"tts": {"language": "English"}},
            cast=self.cast(),
        )

        expected = "Retry this chunk after inspecting the generation log."
        for row in aggregate["chunks"]:
            self.assertIsNone(row["error"])
            self.assertIsNone(row["error_code"])
            self.assertEqual(row["blockers"][0]["explanation"], expected)


if __name__ == "__main__":
    unittest.main()
