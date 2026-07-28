from __future__ import annotations

import unittest

from tests.audio_failure_support import (
    AudioFailureProjectMixin,
    MalformedBatchEngine,
    PreflightFailure,
    RaisingBatchEngine,
    SuccessfulEngine,
)


GENERIC_FAILURE = (
    "Audio generation failed. Retry this line after reviewing the operation log."
)


class AudioFailureLifecycleTests(AudioFailureProjectMixin, unittest.TestCase):
    def test_engine_unavailable_replaces_stale_cause(self) -> None:
        self.write_chunks(
            [
                {
                    **self.chunk(0),
                    "status": "error",
                    "audio_state": "failed",
                    "error": "old provider path /tmp/old.wav",
                    "error_code": "old_code",
                }
            ]
        )
        def unavailable_engine():
            return None

        self.manager.get_engine = unavailable_engine

        success, returned = self.manager.generate_chunk_audio(0)

        self.assertFalse(success)
        self.assertEqual(returned, "TTS engine is not initialized.")
        persisted = self.read_chunks()[0]
        self.assertEqual(persisted["error"], "TTS engine is not initialized.")
        self.assertEqual(persisted["error_code"], "audio_engine_unavailable")

    def test_single_malformed_voice_config_replaces_stale_cause(self) -> None:
        self.write_chunks(
            [
                {
                    **self.chunk(0),
                    "status": "error",
                    "audio_state": "failed",
                    "error": "old",
                    "error_code": "old_code",
                }
            ]
        )
        (self.root / "voice_config.json").write_text("{not valid json", encoding="utf-8")

        success, returned = self.manager.generate_chunk_audio(0)

        self.assertFalse(success)
        self.assertEqual(returned, GENERIC_FAILURE)
        persisted = self.read_chunks()[0]
        self.assertEqual(persisted["status"], "error")
        self.assertEqual(persisted["audio_state"], "failed")
        self.assertEqual(persisted["error"], GENERIC_FAILURE)
        self.assertEqual(persisted["error_code"], "audio_generation_failed")

    def test_batch_malformed_voice_config_fails_every_selected_row(self) -> None:
        self.write_chunks(
            [
                {**self.chunk(0), "error": "old"},
                {**self.chunk(1), "error": "old"},
            ]
        )
        (self.root / "voice_config.json").write_text("{not valid json", encoding="utf-8")

        result = self.manager.generate_chunks_batch([0, 1], batch_size=2)

        self.assertEqual(result["failed"], [(0, GENERIC_FAILURE), (1, GENERIC_FAILURE)])
        for chunk in self.read_chunks():
            self.assertEqual(chunk["status"], "error")
            self.assertEqual(chunk["audio_state"], "failed")
            self.assertEqual(chunk["error"], GENERIC_FAILURE)
            self.assertEqual(chunk["error_code"], "audio_generation_failed")

    def test_batch_engine_initialization_exception_fails_every_selected_row(self) -> None:
        self.write_chunks([self.chunk(0), self.chunk(1)])

        def raise_engine_bootstrap():
            raise RuntimeError("engine bootstrap failed at /private/model-cache")

        self.manager.get_engine = raise_engine_bootstrap

        result = self.manager.generate_chunks_batch([0, 1], batch_size=2)

        self.assertEqual(result["failed"], [(0, GENERIC_FAILURE), (1, GENERIC_FAILURE)])
        for chunk in self.read_chunks():
            self.assertEqual(chunk["status"], "error")
            self.assertEqual(chunk["audio_state"], "failed")
            self.assertEqual(chunk["error"], GENERIC_FAILURE)
            self.assertEqual(chunk["error_code"], "audio_generation_failed")

    def test_malformed_batch_result_fails_every_row_without_generating_state(self) -> None:
        self.write_chunks([self.chunk(0), self.chunk(1)])
        self.manager.engine = MalformedBatchEngine({"completed": [0]})

        result = self.manager.generate_chunks_batch([0, 1], batch_size=2)

        self.assertEqual(result["failed"], [(0, GENERIC_FAILURE), (1, GENERIC_FAILURE)])
        for chunk in self.read_chunks():
            self.assertNotEqual(chunk["status"], "generating")
            self.assertNotEqual(chunk["audio_state"], "generating")
            self.assertEqual(chunk["status"], "error")
            self.assertEqual(chunk["audio_state"], "failed")
            self.assertEqual(chunk["error"], GENERIC_FAILURE)
            self.assertEqual(chunk["error_code"], "audio_generation_failed")

    def test_raised_batch_failure_does_not_leave_rows_generating(self) -> None:
        self.write_chunks(
            [
                {**self.chunk(0), "error": "old"},
                {**self.chunk(1), "error": "old"},
            ]
        )
        self.manager.engine = RaisingBatchEngine()

        result = self.manager.generate_chunks_batch([0, 1], batch_size=2)

        self.assertEqual(result["failed"], [(0, GENERIC_FAILURE), (1, GENERIC_FAILURE)])
        for chunk in self.read_chunks():
            self.assertEqual(chunk["status"], "error")
            self.assertEqual(chunk["audio_state"], "failed")
            self.assertEqual(chunk["error"], GENERIC_FAILURE)
            self.assertEqual(chunk["error_code"], "audio_generation_failed")

    def test_preflight_exception_is_persisted_without_provider_details(self) -> None:
        self.write_chunks(
            [
                {
                    **self.chunk(0),
                    "status": "error",
                    "audio_state": "failed",
                    "error": "stale",
                    "error_code": "stale_code",
                }
            ]
        )
        self.manager.engine = object()

        def raise_preflight(*args, **kwargs):
            raise PreflightFailure("provider rejected /Users/private/request-44")

        self.manager._generation_seed_resolution = raise_preflight

        success, returned = self.manager.generate_chunk_audio(0)

        self.assertFalse(success)
        self.assertEqual(returned, GENERIC_FAILURE)
        persisted = self.read_chunks()[0]
        self.assertEqual(persisted["status"], "error")
        self.assertEqual(persisted["audio_state"], "failed")
        self.assertEqual(persisted["error"], GENERIC_FAILURE)
        self.assertEqual(persisted["error_code"], "audio_generation_failed")

    def test_retry_success_clears_failure_fields(self) -> None:
        self.write_chunks(
            [
                {
                    **self.chunk(0),
                    "status": "error",
                    "audio_state": "failed",
                    "error": "old",
                    "error_code": "old_code",
                }
            ]
        )
        self.manager.engine = SuccessfulEngine()

        success, _ = self.manager.generate_chunk_audio(0)

        self.assertTrue(success)
        persisted = self.read_chunks()[0]
        self.assertEqual(persisted["status"], "done")
        self.assertIsNone(persisted["error"])
        self.assertIsNone(persisted["error_code"])

    def test_edit_clears_failure_fields(self) -> None:
        self.write_chunks(
            [
                {
                    **self.chunk(0),
                    "status": "error",
                    "audio_state": "failed",
                    "error": "old",
                    "error_code": "old_code",
                }
            ]
        )

        updated = self.manager.update_chunk(0, {"text": "Edited text."})

        self.assertIsNone(updated["error"])
        self.assertIsNone(updated["error_code"])

    def test_failure_fields_survive_delete_restore_history(self) -> None:
        failed = {
            **self.chunk(0),
            "status": "error",
            "audio_state": "failed",
            "error": "Generation failed",
            "error_code": "audio_generation_failed",
        }
        self.write_chunks([failed, self.chunk(1)])

        deleted, _ = self.manager.delete_chunk(0)
        self.manager.restore_chunk(0, deleted)

        restored = self.read_chunks()[0]
        self.assertEqual(restored["error"], "Generation failed")
        self.assertEqual(restored["error_code"], "audio_generation_failed")


if __name__ == "__main__":
    unittest.main()
