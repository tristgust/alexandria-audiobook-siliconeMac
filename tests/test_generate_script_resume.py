from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import generate_script
from generation_state import (
    GenerationStateError,
    fingerprint_text,
    fingerprint_value,
    load_generation_state,
)


class GenerateScriptResumeTests(
    unittest.TestCase
):
    def run_generation(
        self,
        *,
        path,
        chunks,
        side_effect,
    ):
        with patch.object(
            generate_script,
            "process_chunk",
            side_effect=side_effect,
        ) as process_chunk:
            result = (
                generate_script
                ._generate_chunks_with_resume(
                    client=object(),
                    model_name="test-model",
                    chunks=chunks,
                    state_path=path,
                    source_fingerprint=(
                        fingerprint_text(
                            "".join(chunks)
                        )
                    ),
                    generation_fingerprint=(
                        fingerprint_value(
                            {
                                "model": (
                                    "test-model"
                                )
                            }
                        )
                    ),
                    process_kwargs={},
                )
            )

        return result, process_chunk

    def test_fresh_run_checkpoints_every_chunk(self):
        with tempfile.TemporaryDirectory() as temp:
            path = (
                Path(temp)
                / "generation_state.json"
            )
            chunks = [
                "One.",
                "Two.",
            ]
            outputs = [
                [
                    {
                        "speaker": "NARRATOR",
                        "text": "One.",
                        "instruct": "Neutral.",
                    }
                ],
                [
                    {
                        "speaker": "NARRATOR",
                        "text": "Two.",
                        "instruct": "Neutral.",
                    }
                ],
            ]

            result, process_chunk = (
                self.run_generation(
                    path=path,
                    chunks=chunks,
                    side_effect=outputs,
                )
            )

            self.assertEqual(
                result,
                outputs[0] + outputs[1],
            )
            self.assertEqual(
                process_chunk.call_count,
                2,
            )

            state = load_generation_state(
                path
            )
            self.assertEqual(
                len(
                    state[
                        "completed_chunks"
                    ]
                ),
                2,
            )

    def test_interrupted_run_resumes_without_old_requests(self):
        with tempfile.TemporaryDirectory() as temp:
            path = (
                Path(temp)
                / "generation_state.json"
            )
            chunks = [
                "One.",
                "Two.",
                "Three.",
            ]
            first_entry = [
                {
                    "speaker": "NARRATOR",
                    "text": "One.",
                    "instruct": "Neutral.",
                }
            ]

            with self.assertRaises(
                GenerationStateError
            ):
                self.run_generation(
                    path=path,
                    chunks=chunks,
                    side_effect=[
                        first_entry,
                        [],
                    ],
                )

            state = load_generation_state(
                path
            )
            self.assertEqual(
                len(
                    state[
                        "completed_chunks"
                    ]
                ),
                1,
            )

            second_entry = [
                {
                    "speaker": "NARRATOR",
                    "text": "Two.",
                    "instruct": "Neutral.",
                }
            ]
            third_entry = [
                {
                    "speaker": "NARRATOR",
                    "text": "Three.",
                    "instruct": "Neutral.",
                }
            ]

            result, process_chunk = (
                self.run_generation(
                    path=path,
                    chunks=chunks,
                    side_effect=[
                        second_entry,
                        third_entry,
                    ],
                )
            )

            self.assertEqual(
                process_chunk.call_count,
                2,
            )
            self.assertEqual(
                [
                    call.args[3]
                    for call
                    in process_chunk.call_args_list
                ],
                [
                    2,
                    3,
                ],
            )
            self.assertEqual(
                result,
                (
                    first_entry
                    + second_entry
                    + third_entry
                ),
            )

            previous_entries = (
                process_chunk
                .call_args_list[0]
                .kwargs[
                    "previous_entries"
                ]
            )
            self.assertEqual(
                previous_entries,
                first_entry,
            )

    def test_failed_chunk_does_not_checkpoint_empty_output(self):
        with tempfile.TemporaryDirectory() as temp:
            path = (
                Path(temp)
                / "generation_state.json"
            )

            with self.assertRaises(
                GenerationStateError
            ):
                self.run_generation(
                    path=path,
                    chunks=[
                        "One."
                    ],
                    side_effect=[
                        []
                    ],
                )

            state = load_generation_state(
                path
            )
            self.assertEqual(
                state["completed_chunks"],
                [],
            )

    def test_final_write_format_and_state_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state_path = (
                root
                / "generation_state.json"
            )
            output_path = (
                root
                / "annotated_script.json"
            )
            entries = [
                {
                    "speaker": "NARRATOR",
                    "text": "One.",
                    "instruct": "Neutral.",
                }
            ]

            result, _ = self.run_generation(
                path=state_path,
                chunks=[
                    "One."
                ],
                side_effect=[
                    entries
                ],
            )

            generate_script.atomic_json_write(
                result,
                output_path,
            )
            generate_script.clear_generation_state(
                state_path
            )

            self.assertEqual(
                json.loads(
                    output_path.read_text(
                        encoding="utf-8"
                    )
                ),
                entries,
            )
            self.assertFalse(
                state_path.exists()
            )


if __name__ == "__main__":
    unittest.main()
