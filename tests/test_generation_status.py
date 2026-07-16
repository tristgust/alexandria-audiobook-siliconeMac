from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import generate_script
from generation_metadata import (
    build_generation_metadata,
)
from generation_state import (
    atomic_json_write,
    checkpoint_completed_chunk,
    fingerprint_text,
    fingerprint_value,
    new_generation_state,
)
from generation_status import (
    build_generation_status,
    inspect_generation_checkpoint,
)


class GenerationStatusTests(unittest.TestCase):
    def identity(self):
        return {
            "base_url": "http://localhost:11434/v1",
            "model_name": "qwen3.5:35b-mlx",
            "backend": "auto",
            "thinking": False,
            "structured_output": True,
            "corrective_retry": True,
            "system_prompt": "system",
            "user_prompt_template": "user",
            "chunk_size": 3000,
            "max_tokens": 4096,
            "temperature": 0.6,
            "top_p": 0.8,
            "top_k": 0,
            "min_p": 0,
            "presence_penalty": 0.0,
            "banned_tokens": [],
        }

    def snapshot(
        self,
        *,
        identity=None,
        source_text="source",
        chunks=None,
        auditor_contract_version=1,
    ):
        identity = (
            self.identity()
            if identity is None
            else identity
        )
        chunks = (
            ["chunk one", "chunk two"]
            if chunks is None
            else chunks
        )

        return {
            "source_path": "/books/book.txt",
            "source_basename": "book.txt",
            "source_character_count": len(source_text),
            "source_fingerprint": fingerprint_text(
                source_text
            ),
            "generation_identity": identity,
            "generation_fingerprint": fingerprint_value(
                identity
            ),
            "chunk_fingerprints": [
                fingerprint_text(chunk)
                for chunk in chunks
            ],
            "total_chunks": len(chunks),
            "auditor_contract_version": (
                auditor_contract_version
            ),
        }

    def write_checkpoint(
        self,
        path: Path,
        snapshot,
        *,
        completed=0,
        include_identity=True,
    ):
        state = new_generation_state(
            source_fingerprint=(
                snapshot["source_fingerprint"]
            ),
            generation_fingerprint=(
                snapshot["generation_fingerprint"]
            ),
            chunk_fingerprints=(
                snapshot["chunk_fingerprints"]
            ),
            generation_identity=(
                snapshot["generation_identity"]
                if include_identity
                else None
            ),
            source={
                "basename": snapshot["source_basename"],
                "character_count": (
                    snapshot["source_character_count"]
                ),
            },
            auditor_contract_version=(
                snapshot["auditor_contract_version"]
            ),
        )

        atomic_json_write(state, path)

        for index in range(1, completed + 1):
            state = checkpoint_completed_chunk(
                state=state,
                path=path,
                index=index,
                chunk_fingerprint=(
                    snapshot["chunk_fingerprints"][
                        index - 1
                    ]
                ),
                entries=[
                    {
                        "speaker": "NARRATOR",
                        "text": f"Chunk {index}.",
                        "instruct": "Neutral.",
                    }
                ],
            )

        return state

    def test_no_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = inspect_generation_checkpoint(
                checkpoint_path=(
                    Path(tmp)
                    / "generation_state.json"
                ),
                current_snapshot=self.snapshot(),
            )

        self.assertEqual(result["status"], "none")
        self.assertFalse(result["resumable"])
        self.assertEqual(
            result["percent_complete"],
            0.0,
        )

    def test_partial_compatible_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "generation_state.json"
            snapshot = self.snapshot()
            self.write_checkpoint(
                path,
                snapshot,
                completed=1,
            )

            result = inspect_generation_checkpoint(
                checkpoint_path=path,
                current_snapshot=snapshot,
            )

        self.assertEqual(
            result["status"],
            "compatible",
        )
        self.assertTrue(result["resumable"])
        self.assertEqual(
            result["completed_chunks"],
            1,
        )
        self.assertEqual(result["total_chunks"], 2)
        self.assertEqual(result["next_chunk"], 2)
        self.assertEqual(
            result["percent_complete"],
            50.0,
        )
        self.assertTrue(
            result["completed_entries_present"]
        )
        self.assertTrue(
            result["source_fingerprint_match"]
        )
        self.assertTrue(
            result["generation_fingerprint_match"]
        )
        self.assertTrue(
            result["chunk_layout_match"]
        )

    def test_complete_checkpoint_awaits_finalization(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "generation_state.json"
            snapshot = self.snapshot()
            self.write_checkpoint(
                path,
                snapshot,
                completed=2,
            )

            result = inspect_generation_checkpoint(
                checkpoint_path=path,
                current_snapshot=snapshot,
            )

        self.assertEqual(
            result["status"],
            "finalization_pending",
        )
        self.assertTrue(result["resumable"])
        self.assertIsNone(result["next_chunk"])
        self.assertEqual(
            result["percent_complete"],
            100.0,
        )

    def test_corrupt_checkpoint_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "generation_state.json"
            path.write_text("{", encoding="utf-8")

            result = inspect_generation_checkpoint(
                checkpoint_path=path,
                current_snapshot=self.snapshot(),
            )

        self.assertEqual(
            result["status"],
            "corrupt",
        )
        self.assertEqual(
            result["reason_codes"],
            ["checkpoint_corrupt"],
        )

    def test_invalid_checkpoint_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "generation_state.json"
            atomic_json_write(
                {
                    "schema_version": 1,
                    "source_fingerprint": "abc",
                },
                path,
            )

            result = inspect_generation_checkpoint(
                checkpoint_path=path,
                current_snapshot=self.snapshot(),
            )

        self.assertEqual(
            result["status"],
            "invalid",
        )
        self.assertEqual(
            result["reason_codes"],
            ["checkpoint_schema_invalid"],
        )

    def test_state_schema_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "generation_state.json"
            atomic_json_write(
                {
                    "schema_version": 999,
                    "total_chunks": 4,
                    "completed_chunks": [],
                },
                path,
            )

            result = inspect_generation_checkpoint(
                checkpoint_path=path,
                current_snapshot=self.snapshot(),
            )

        self.assertEqual(
            result["status"],
            "incompatible",
        )
        self.assertEqual(
            result["reason_codes"],
            ["state_schema_changed"],
        )

    def test_source_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "generation_state.json"
            saved = self.snapshot()
            self.write_checkpoint(path, saved)

            current = self.snapshot(
                source_text="different source"
            )
            result = inspect_generation_checkpoint(
                checkpoint_path=path,
                current_snapshot=current,
            )

        self.assertEqual(
            result["status"],
            "incompatible",
        )
        self.assertIn(
            "source_changed",
            result["reason_codes"],
        )
        self.assertFalse(
            result["source_fingerprint_match"]
        )

    def test_generation_mismatches_are_specific(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "generation_state.json"
            saved = self.snapshot()
            self.write_checkpoint(path, saved)

            changed_identity = {
                **self.identity(),
                "model_name": "other-model",
                "backend": "native",
                "system_prompt": "changed prompt",
                "thinking": True,
                "temperature": 0.2,
                "chunk_size": 2000,
            }
            current = self.snapshot(
                identity=changed_identity
            )

            result = inspect_generation_checkpoint(
                checkpoint_path=path,
                current_snapshot=current,
            )

        codes = set(result["reason_codes"])

        self.assertEqual(
            result["status"],
            "incompatible",
        )
        self.assertTrue(
            {
                "model_changed",
                "backend_changed",
                "prompt_changed",
                "runtime_settings_changed",
                "sampling_changed",
                "chunk_size_changed",
            }.issubset(codes)
        )

    def test_legacy_generation_mismatch_is_generic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "generation_state.json"
            saved = self.snapshot()
            self.write_checkpoint(
                path,
                saved,
                include_identity=False,
            )

            current_identity = {
                **self.identity(),
                "model_name": "other-model",
            }
            current = self.snapshot(
                identity=current_identity
            )

            result = inspect_generation_checkpoint(
                checkpoint_path=path,
                current_snapshot=current,
            )

        self.assertIn(
            "generation_identity_changed",
            result["reason_codes"],
        )

    def test_chunk_layout_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "generation_state.json"
            saved = self.snapshot()
            self.write_checkpoint(path, saved)

            current = {
                **saved,
                "chunk_fingerprints": [
                    fingerprint_text("different"),
                    fingerprint_text("layout"),
                ],
            }

            result = inspect_generation_checkpoint(
                checkpoint_path=path,
                current_snapshot=current,
            )

        self.assertIn(
            "chunk_layout_changed",
            result["reason_codes"],
        )
        self.assertFalse(
            result["chunk_layout_match"]
        )

    def test_multiple_simultaneous_mismatches(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "generation_state.json"
            saved = self.snapshot()
            self.write_checkpoint(path, saved)

            changed_identity = {
                **self.identity(),
                "model_name": "other-model",
            }
            current = self.snapshot(
                identity=changed_identity,
                source_text="changed source",
                chunks=["different"],
                auditor_contract_version=2,
            )

            result = inspect_generation_checkpoint(
                checkpoint_path=path,
                current_snapshot=current,
            )

        self.assertTrue(
            {
                "source_changed",
                "model_changed",
                "chunk_layout_changed",
                "auditor_contract_changed",
            }.issubset(
                set(result["reason_codes"])
            )
        )

    def test_missing_script_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            result = build_generation_status(
                checkpoint_path=(
                    root / "generation_state.json"
                ),
                script_path=(
                    root / "annotated_script.json"
                ),
                metadata_path=(
                    root
                    / "annotated_script.meta.json"
                ),
                current_snapshot=None,
                current_error=None,
                process_running=False,
                process_logs=[],
            )

        self.assertEqual(
            result["result"]["status"],
            "missing",
        )
        self.assertFalse(
            result["result"]["script_exists"]
        )

    def test_valid_script_and_metadata(self):
        entries = [
            {
                "speaker": "NARRATOR",
                "text": "Text.",
                "instruct": "Neutral.",
            }
        ]
        identity = self.identity()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script_path = (
                root / "annotated_script.json"
            )
            metadata_path = (
                root
                / "annotated_script.meta.json"
            )
            atomic_json_write(entries, script_path)
            metadata = build_generation_metadata(
                source_path="/books/book.txt",
                source_fingerprint=(
                    fingerprint_text("source")
                ),
                source_character_count=6,
                source_chunk_count=1,
                generation_fingerprint=(
                    fingerprint_value(identity)
                ),
                generation_identity=identity,
                entries=entries,
                resumed=False,
                previously_completed_chunks=0,
                generated_at_utc=(
                    "2026-07-16T15:00:00Z"
                ),
            )
            atomic_json_write(
                metadata,
                metadata_path,
            )

            result = build_generation_status(
                checkpoint_path=(
                    root / "generation_state.json"
                ),
                script_path=script_path,
                metadata_path=metadata_path,
                current_snapshot=None,
                current_error=None,
                process_running=False,
                process_logs=[],
            )

        self.assertEqual(
            result["result"]["status"],
            "complete",
        )
        self.assertEqual(
            result["result"]["metadata_status"],
            "valid",
        )

    def test_legacy_script_without_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            atomic_json_write(
                [],
                root / "annotated_script.json",
            )

            result = build_generation_status(
                checkpoint_path=(
                    root / "generation_state.json"
                ),
                script_path=(
                    root / "annotated_script.json"
                ),
                metadata_path=(
                    root
                    / "annotated_script.meta.json"
                ),
                current_snapshot=None,
                current_error=None,
                process_running=False,
                process_logs=[],
            )

        self.assertEqual(
            result["result"]["status"],
            "legacy",
        )
        self.assertEqual(
            result["result"]["metadata_status"],
            "legacy",
        )

    def test_complete_checkpoint_with_missing_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = self.snapshot()
            self.write_checkpoint(
                root / "generation_state.json",
                snapshot,
                completed=2,
            )
            atomic_json_write(
                [],
                root / "annotated_script.json",
            )

            result = build_generation_status(
                checkpoint_path=(
                    root / "generation_state.json"
                ),
                script_path=(
                    root / "annotated_script.json"
                ),
                metadata_path=(
                    root
                    / "annotated_script.meta.json"
                ),
                current_snapshot=snapshot,
                current_error=None,
                process_running=False,
                process_logs=[],
            )

        self.assertEqual(
            result["checkpoint"]["status"],
            "finalization_pending",
        )
        self.assertEqual(
            result["result"]["status"],
            "finalization_pending",
        )
        self.assertEqual(
            result["result"]["metadata_status"],
            "missing",
        )

    def test_corrupt_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            atomic_json_write(
                [],
                root / "annotated_script.json",
            )
            (
                root
                / "annotated_script.meta.json"
            ).write_text(
                "{",
                encoding="utf-8",
            )

            result = build_generation_status(
                checkpoint_path=(
                    root / "generation_state.json"
                ),
                script_path=(
                    root / "annotated_script.json"
                ),
                metadata_path=(
                    root
                    / "annotated_script.meta.json"
                ),
                current_snapshot=None,
                current_error=None,
                process_running=False,
                process_logs=[],
            )

        self.assertEqual(
            result["result"]["metadata_status"],
            "corrupt",
        )
        self.assertEqual(
            result["result"]["status"],
            "metadata_corrupt",
        )

    def test_status_reads_do_not_modify_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = self.snapshot()
            state_path = (
                root / "generation_state.json"
            )
            script_path = (
                root / "annotated_script.json"
            )
            metadata_path = (
                root
                / "annotated_script.meta.json"
            )

            self.write_checkpoint(
                state_path,
                snapshot,
                completed=1,
            )
            atomic_json_write([], script_path)
            atomic_json_write(
                {
                    "schema_version": 1,
                    "generated_at_utc": (
                        "2026-07-16T15:00:00Z"
                    ),
                    "source": {
                        "basename": "book.txt",
                        "fingerprint": "abc",
                        "character_count": 1,
                        "chunk_count": 1,
                    },
                    "generation": {
                        "fingerprint": "def",
                        "effective_identity": {},
                    },
                    "result": {
                        "script_fingerprint": (
                            fingerprint_value([])
                        ),
                        "entry_count": 0,
                        "speaker_labels": [],
                    },
                    "resume": {
                        "resumed": False,
                        "previously_completed_chunks": 0,
                    },
                },
                metadata_path,
            )

            paths = [
                state_path,
                script_path,
                metadata_path,
            ]
            before = {
                path: (
                    path.read_bytes(),
                    path.stat().st_mtime_ns,
                )
                for path in paths
            }

            build_generation_status(
                checkpoint_path=state_path,
                script_path=script_path,
                metadata_path=metadata_path,
                current_snapshot=snapshot,
                current_error=None,
                process_running=False,
                process_logs=[],
            )

            after = {
                path: (
                    path.read_bytes(),
                    path.stat().st_mtime_ns,
                )
                for path in paths
            }

        self.assertEqual(before, after)

    def test_snapshot_does_not_preload_or_query_model(self):
        class FakeRuntime:
            model_name = "qwen3.5:35b-mlx"
            backend = "auto"
            thinking = False
            structured_output = True
            corrective_retry = True

            def preload(self):
                raise AssertionError(
                    "preload must not be called"
                )

            def status(self):
                raise AssertionError(
                    "status must not be called"
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "book.txt"
            config_path = root / "config.json"
            source_path.write_text(
                "A short source.",
                encoding="utf-8",
            )
            config_path.write_text(
                json.dumps(
                    {
                        "llm": {
                            "base_url": (
                                "http://localhost:11434/v1"
                            ),
                            "model_name": (
                                "qwen3.5:35b-mlx"
                            ),
                        },
                        "generation": {
                            "chunk_size": 3000
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(
                generate_script,
                "_build_script_llm_client",
                return_value=(
                    FakeRuntime(),
                    object(),
                ),
            ):
                snapshot = (
                    generate_script
                    .build_script_generation_snapshot(
                        source_path,
                        config_path=config_path,
                    )
                )

        self.assertEqual(
            snapshot["source_basename"],
            "book.txt",
        )
        self.assertEqual(
            snapshot[
                "auditor_contract_version"
            ],
            1,
        )


if __name__ == "__main__":
    unittest.main()
