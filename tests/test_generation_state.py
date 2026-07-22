from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from generation_state import (
    GenerationStateCorruptError,
    GenerationStateError,
    GenerationStateMismatchError,
    atomic_json_write,
    checkpoint_completed_chunk,
    clear_generation_state,
    completed_entries,
    fingerprint_text,
    fingerprint_value,
    load_generation_state,
    prepare_generation_state,
)


class GenerationFingerprintTests(
    unittest.TestCase
):
    def test_value_fingerprint_is_order_independent(self):
        first = fingerprint_value(
            {
                "b": 2,
                "a": 1,
            }
        )
        second = fingerprint_value(
            {
                "a": 1,
                "b": 2,
            }
        )

        self.assertEqual(
            first,
            second,
        )

    def test_text_fingerprint_changes_with_source(self):
        self.assertNotEqual(
            fingerprint_text(
                "first"
            ),
            fingerprint_text(
                "second"
            ),
        )


class GenerationStateTests(
    unittest.TestCase
):
    def prepare(
        self,
        path,
        chunks=None,
        source="source",
        config=None,
        auditor_contract_version=None,
    ):
        chunks = chunks or [
            "chunk one",
            "chunk two",
        ]
        config = config or {
            "model": "test"
        }

        return prepare_generation_state(
            path=path,
            source_fingerprint=(
                fingerprint_text(
                    source
                )
            ),
            generation_fingerprint=(
                fingerprint_value(
                    config
                )
            ),
            chunk_fingerprints=[
                fingerprint_text(chunk)
                for chunk in chunks
            ],
            auditor_contract_version=auditor_contract_version,
        )

    def test_fresh_state_is_written(self):
        with tempfile.TemporaryDirectory() as temp:
            path = (
                Path(temp)
                / "generation_state.json"
            )

            state = self.prepare(
                path
            )

            self.assertTrue(
                path.exists()
            )
            self.assertEqual(
                state["schema_version"],
                1,
            )
            self.assertEqual(
                state["total_chunks"],
                2,
            )
            self.assertEqual(
                state["completed_chunks"],
                [],
            )

    def test_checkpoint_is_contiguous_and_persistent(self):
        with tempfile.TemporaryDirectory() as temp:
            path = (
                Path(temp)
                / "generation_state.json"
            )
            chunks = [
                "one",
                "two",
            ]
            state = self.prepare(
                path,
                chunks=chunks,
            )

            state = checkpoint_completed_chunk(
                state=state,
                path=path,
                index=1,
                chunk_fingerprint=(
                    fingerprint_text(
                        chunks[0]
                    )
                ),
                entries=[
                    {
                        "speaker": "NARRATOR",
                        "text": "One.",
                        "instruct": "Neutral.",
                    }
                ],
            )

            loaded = load_generation_state(
                path
            )

            self.assertEqual(
                loaded,
                state,
            )
            self.assertEqual(
                completed_entries(
                    loaded
                )[0]["text"],
                "One.",
            )

            with self.assertRaises(
                GenerationStateError
            ):
                checkpoint_completed_chunk(
                    state=state,
                    path=path,
                    index=3,
                    chunk_fingerprint=(
                        fingerprint_text(
                            chunks[1]
                        )
                    ),
                    entries=[
                        {
                            "speaker": "NARRATOR",
                            "text": "Two.",
                            "instruct": "Neutral.",
                        }
                    ],
                )

    def test_source_mismatch_blocks_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            path = (
                Path(temp)
                / "generation_state.json"
            )
            self.prepare(
                path,
                source="original",
            )

            with self.assertRaisesRegex(
                GenerationStateMismatchError,
                "source",
            ):
                self.prepare(
                    path,
                    source="changed",
                )

    def test_config_mismatch_blocks_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            path = (
                Path(temp)
                / "generation_state.json"
            )
            self.prepare(
                path,
                config={
                    "model": "first"
                },
            )

            with self.assertRaisesRegex(
                GenerationStateMismatchError,
                "generation configuration",
            ):
                self.prepare(
                    path,
                    config={
                        "model": "second"
                    },
                )

    def test_auditor_contract_mismatch_blocks_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "generation_state.json"
            self.prepare(path, auditor_contract_version=1)

            with self.assertRaisesRegex(
                GenerationStateMismatchError,
                "auditor contract",
            ):
                self.prepare(path, auditor_contract_version=2)

            saved = load_generation_state(path)
            self.assertEqual(saved["auditor_contract_version"], 1)

    def test_missing_legacy_auditor_contract_blocks_versioned_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "generation_state.json"
            self.prepare(path)

            with self.assertRaisesRegex(
                GenerationStateMismatchError,
                "auditor contract",
            ):
                self.prepare(path, auditor_contract_version=2)

            self.assertNotIn(
                "auditor_contract_version",
                load_generation_state(path),
            )

    def test_corrupt_state_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = (
                Path(temp)
                / "generation_state.json"
            )
            path.write_text(
                "{not json",
                encoding="utf-8",
            )

            with self.assertRaises(
                GenerationStateCorruptError
            ):
                load_generation_state(
                    path
                )

    def test_atomic_failure_preserves_previous_state(self):
        with tempfile.TemporaryDirectory() as temp:
            path = (
                Path(temp)
                / "generation_state.json"
            )
            original = {
                "value": "original"
            }
            atomic_json_write(
                original,
                path,
            )

            with patch(
                "generation_state.os.replace",
                side_effect=OSError(
                    "replace failed"
                ),
            ):
                with self.assertRaises(
                    OSError
                ):
                    atomic_json_write(
                        {
                            "value": "new"
                        },
                        path,
                    )

            self.assertEqual(
                json.loads(
                    path.read_text(
                        encoding="utf-8"
                    )
                ),
                original,
            )
            self.assertFalse(
                Path(
                    str(path) + ".tmp"
                ).exists()
            )

    def test_clear_state_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            path = (
                Path(temp)
                / "generation_state.json"
            )
            path.write_text(
                "{}",
                encoding="utf-8",
            )

            clear_generation_state(
                path
            )
            clear_generation_state(
                path
            )

            self.assertFalse(
                path.exists()
            )


if __name__ == "__main__":
    unittest.main()
