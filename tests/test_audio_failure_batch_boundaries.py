from __future__ import annotations

import unittest

from tests.audio_failure_support import AudioFailureProjectMixin, MalformedBatchEngine


GENERIC_FAILURE = (
    "Audio generation failed. Retry this line after reviewing the operation log."
)


class AudioFailureBatchBoundaryTests(AudioFailureProjectMixin, unittest.TestCase):
    def assert_malformed_result(self, provider_result: object) -> None:
        self.write_chunks([self.chunk(0), self.chunk(1)])
        self.manager.engine = MalformedBatchEngine(provider_result)

        result = self.manager.generate_chunks_batch([0, 1], batch_size=2)

        self.assertEqual(result["failed"], [(0, GENERIC_FAILURE), (1, GENERIC_FAILURE)])
        for chunk in self.read_chunks():
            self.assertEqual(chunk["status"], "error")
            self.assertEqual(chunk["audio_state"], "failed")
            self.assertEqual(chunk["error"], GENERIC_FAILURE)
            self.assertEqual(chunk["error_code"], "audio_generation_failed")

    def test_batch_result_non_mapping_is_terminal_failure(self) -> None:
        self.assert_malformed_result([])

    def test_batch_result_completed_container_type_is_rejected(self) -> None:
        self.assert_malformed_result({"completed": 0, "failed": []})

    def test_batch_result_failed_container_type_is_rejected(self) -> None:
        self.assert_malformed_result({"completed": [0, 1], "failed": {}})

    def test_batch_result_failed_entry_shape_is_rejected(self) -> None:
        self.assert_malformed_result({"completed": [0, 1], "failed": [1]})

    def test_batch_result_duplicate_completed_is_rejected(self) -> None:
        self.assert_malformed_result(
            {"completed": [0, 0], "failed": [(1, "provider")]}
        )

    def test_batch_result_duplicate_failed_is_rejected(self) -> None:
        self.assert_malformed_result(
            {"completed": [0], "failed": [(1, "first"), (1, "second")]}
        )

    def test_batch_result_completed_failed_overlap_is_rejected(self) -> None:
        self.assert_malformed_result(
            {"completed": [0], "failed": [(0, "overlap"), (1, "provider")]}
        )

    def test_batch_result_incomplete_coverage_is_rejected(self) -> None:
        self.assert_malformed_result({"completed": [0], "failed": []})

    def test_batch_result_foreign_index_is_rejected(self) -> None:
        self.assert_malformed_result({"completed": [0, 7], "failed": []})

    def test_batch_result_boolean_index_is_rejected(self) -> None:
        self.assert_malformed_result({"completed": [True], "failed": [(1, "provider")]})

    def test_single_config_read_failure_replaces_stale_cause(self) -> None:
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
        config_path = self.root / "voice_config.json"
        config_path.unlink()
        config_path.mkdir()

        success, returned = self.manager.generate_chunk_audio(0)

        self.assertFalse(success)
        self.assertEqual(returned, GENERIC_FAILURE)
        persisted = self.read_chunks()[0]
        self.assertEqual(persisted["status"], "error")
        self.assertEqual(persisted["audio_state"], "failed")
        self.assertEqual(persisted["error"], GENERIC_FAILURE)
        self.assertEqual(persisted["error_code"], "audio_generation_failed")

    def test_batch_config_read_failure_fails_every_selected_row(self) -> None:
        self.write_chunks([self.chunk(0), self.chunk(1)])
        config_path = self.root / "voice_config.json"
        config_path.unlink()
        config_path.mkdir()

        result = self.manager.generate_chunks_batch([0, 1], batch_size=2)

        self.assertEqual(result["failed"], [(0, GENERIC_FAILURE), (1, GENERIC_FAILURE)])
        for chunk in self.read_chunks():
            self.assertEqual(chunk["status"], "error")
            self.assertEqual(chunk["audio_state"], "failed")
            self.assertEqual(chunk["error"], GENERIC_FAILURE)
            self.assertEqual(chunk["error_code"], "audio_generation_failed")


if __name__ == "__main__":
    unittest.main()
