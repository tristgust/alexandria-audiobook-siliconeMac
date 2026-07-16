from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import generate_script
from generation_metadata import (
    GenerationMetadataError,
    build_generation_metadata,
    finalize_generation_outputs,
)
from generation_state import (
    atomic_json_write,
    checkpoint_completed_chunk,
    fingerprint_text,
    fingerprint_value,
    prepare_generation_state,
)


class GenerationMetadataTests(
    unittest.TestCase
):
    def setUp(self):
        self.entries = [
            {
                "speaker": "NARRATOR",
                "text": "The room was quiet.",
                "instruct": "Neutral narration.",
            },
            {
                "speaker": "MARCUS",
                "text": "We should leave.",
                "instruct": "Quiet urgency.",
            },
        ]

        self.identity = {
            "model": "qwen3.5:35b-mlx",
            "backend": "auto",
            "generation": {
                "temperature": 0.6,
                "top_p": 0.8,
            },
        }

    def build_metadata(
        self,
        **overrides,
    ):
        values = {
            "source_path": (
                "/books/example.txt"
            ),
            "source_fingerprint": (
                fingerprint_text(
                    "source text"
                )
            ),
            "source_character_count": 11,
            "source_chunk_count": 2,
            "generation_fingerprint": (
                fingerprint_value(
                    self.identity
                )
            ),
            "generation_identity": (
                self.identity
            ),
            "entries": self.entries,
            "resumed": True,
            "previously_completed_chunks": 1,
            "generated_at_utc": (
                "2026-07-16T15:00:00Z"
            ),
        }

        values.update(overrides)

        return build_generation_metadata(
            **values
        )

    def test_deterministic_metadata_fields(self):
        metadata = self.build_metadata()

        self.assertEqual(
            metadata,
            {
                "schema_version": 1,
                "generated_at_utc": (
                    "2026-07-16T15:00:00Z"
                ),
                "source": {
                    "basename": "example.txt",
                    "fingerprint": (
                        fingerprint_text(
                            "source text"
                        )
                    ),
                    "character_count": 11,
                    "chunk_count": 2,
                },
                "generation": {
                    "fingerprint": (
                        fingerprint_value(
                            self.identity
                        )
                    ),
                    "effective_identity": (
                        self.identity
                    ),
                },
                "result": {
                    "script_fingerprint": (
                        fingerprint_value(
                            self.entries
                        )
                    ),
                    "entry_count": 2,
                    "speaker_labels": [
                        "MARCUS",
                        "NARRATOR",
                    ],
                },
                "resume": {
                    "resumed": True,
                    "previously_completed_chunks": 1,
                },
            },
        )

        self.assertEqual(
            metadata,
            self.build_metadata(),
        )

    def test_script_fingerprint_is_stable(self):
        reordered_keys = [
            {
                "instruct": (
                    "Neutral narration."
                ),
                "text": (
                    "The room was quiet."
                ),
                "speaker": "NARRATOR",
            },
            {
                "text": "We should leave.",
                "speaker": "MARCUS",
                "instruct": "Quiet urgency.",
            },
        ]

        first = self.build_metadata()
        second = self.build_metadata(
            entries=reordered_keys
        )

        self.assertEqual(
            first["result"][
                "script_fingerprint"
            ],
            second["result"][
                "script_fingerprint"
            ],
        )

    def test_finalize_writes_script_then_metadata(
        self,
    ):
        metadata = self.build_metadata()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script_path = (
                root
                / "annotated_script.json"
            )
            metadata_path = (
                root
                / "annotated_script.meta.json"
            )
            state_path = (
                root
                / "generation_state.json"
            )

            atomic_json_write(
                {"state": True},
                state_path,
            )

            calls = []

            def recording_writer(
                value,
                path,
            ):
                calls.append(
                    Path(path).name
                )
                atomic_json_write(
                    value,
                    path,
                )

            with patch(
                "generation_metadata."
                "atomic_json_write",
                side_effect=recording_writer,
            ):
                finalize_generation_outputs(
                    entries=self.entries,
                    metadata=metadata,
                    script_path=script_path,
                    metadata_path=(
                        metadata_path
                    ),
                    state_path=state_path,
                )

            self.assertEqual(
                calls,
                [
                    "annotated_script.json",
                    "annotated_script.meta.json",
                ],
            )
            self.assertTrue(
                script_path.exists()
            )
            self.assertTrue(
                metadata_path.exists()
            )
            self.assertFalse(
                state_path.exists()
            )

    def test_metadata_failure_preserves_checkpoint(
        self,
    ):
        metadata = self.build_metadata()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script_path = (
                root
                / "annotated_script.json"
            )
            metadata_path = (
                root
                / "annotated_script.meta.json"
            )
            state_path = (
                root
                / "generation_state.json"
            )

            atomic_json_write(
                {"old": "metadata"},
                metadata_path,
            )
            atomic_json_write(
                {"state": True},
                state_path,
            )

            call_count = 0

            def failing_writer(
                value,
                path,
            ):
                nonlocal call_count
                call_count += 1

                if call_count == 2:
                    raise OSError(
                        "simulated metadata failure"
                    )

                atomic_json_write(
                    value,
                    path,
                )

            with patch(
                "generation_metadata."
                "atomic_json_write",
                side_effect=failing_writer,
            ):
                with self.assertRaises(
                    OSError
                ):
                    finalize_generation_outputs(
                        entries=self.entries,
                        metadata=metadata,
                        script_path=script_path,
                        metadata_path=(
                            metadata_path
                        ),
                        state_path=state_path,
                    )

            self.assertTrue(
                script_path.exists()
            )
            self.assertFalse(
                metadata_path.exists()
            )
            self.assertTrue(
                state_path.exists()
            )

    def test_checkpoint_not_cleared_without_metadata(
        self,
    ):
        metadata = self.build_metadata()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script_path = (
                root
                / "annotated_script.json"
            )
            metadata_path = (
                root
                / "annotated_script.meta.json"
            )
            state_path = (
                root
                / "generation_state.json"
            )

            atomic_json_write(
                {"state": True},
                state_path,
            )

            call_count = 0

            def incomplete_writer(
                value,
                path,
            ):
                nonlocal call_count
                call_count += 1

                if call_count == 1:
                    atomic_json_write(
                        value,
                        path,
                    )

            with patch(
                "generation_metadata."
                "atomic_json_write",
                side_effect=incomplete_writer,
            ):
                with self.assertRaises(
                    GenerationMetadataError
                ):
                    finalize_generation_outputs(
                        entries=self.entries,
                        metadata=metadata,
                        script_path=script_path,
                        metadata_path=(
                            metadata_path
                        ),
                        state_path=state_path,
                    )

            self.assertTrue(
                state_path.exists()
            )
            self.assertFalse(
                metadata_path.exists()
            )

    def test_resume_information_reports_prior_chunks(
        self,
    ):
        chunks = [
            "first chunk",
            "second chunk",
        ]
        source_fingerprint = (
            fingerprint_text(
                "".join(chunks)
            )
        )
        generation_fingerprint = (
            fingerprint_value(
                {"model": "test"}
            )
        )

        with tempfile.TemporaryDirectory() as tmp:
            state_path = (
                Path(tmp)
                / "generation_state.json"
            )
            chunk_fingerprints = [
                fingerprint_text(chunk)
                for chunk in chunks
            ]

            state = prepare_generation_state(
                path=state_path,
                source_fingerprint=(
                    source_fingerprint
                ),
                generation_fingerprint=(
                    generation_fingerprint
                ),
                chunk_fingerprints=(
                    chunk_fingerprints
                ),
            )

            checkpoint_completed_chunk(
                state=state,
                path=state_path,
                index=1,
                chunk_fingerprint=(
                    chunk_fingerprints[0]
                ),
                entries=[
                    {
                        "speaker": "NARRATOR",
                        "text": "First.",
                        "instruct": "Neutral.",
                    }
                ],
            )

            resume_info = {}

            with patch.object(
                generate_script,
                "process_chunk",
                return_value=[
                    {
                        "speaker": "NARRATOR",
                        "text": "Second.",
                        "instruct": "Neutral.",
                    }
                ],
            ):
                result = (
                    generate_script
                    ._generate_chunks_with_resume(
                        client=object(),
                        model_name="test",
                        chunks=chunks,
                        state_path=state_path,
                        source_fingerprint=(
                            source_fingerprint
                        ),
                        generation_fingerprint=(
                            generation_fingerprint
                        ),
                        process_kwargs={},
                        resume_info=resume_info,
                    )
                )

            self.assertEqual(
                len(result),
                2,
            )
            self.assertEqual(
                resume_info,
                {
                    "resumed": True,
                    "previously_completed_chunks": 1,
                },
            )


if __name__ == "__main__":
    unittest.main()
