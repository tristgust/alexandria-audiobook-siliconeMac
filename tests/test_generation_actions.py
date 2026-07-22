from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import generate_script
from generation_actions import (
    GenerationActionBlockedError,
    choose_generation_action,
    discard_generation_checkpoint,
)
from generation_state import (
    GenerationStateMismatchError,
    atomic_json_write,
    checkpoint_completed_chunk,
    fingerprint_text,
    fingerprint_value,
    new_generation_state,
)


class GenerationActionTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.roster_patcher = patch.object(
            generate_script,
            "load_approved_roster_for_source",
            return_value=None,
        )
        self.roster_patcher.start()

    def tearDown(self) -> None:
        self.roster_patcher.stop()

    def status(
        self,
        checkpoint_status,
        *,
        completed=0,
        running=False,
        reason_codes=None,
    ):
        return {
            "process": {
                "running": running,
                "logs": [],
            },
            "checkpoint": {
                "status": checkpoint_status,
                "completed_chunks": completed,
                "reason_codes": list(
                    reason_codes or []
                ),
                "explanation": (
                    "Checkpoint cannot be used."
                ),
            },
            "result": {
                "status": "missing"
            },
        }

    def identity(self):
        return {
            "base_url": (
                "http://localhost:11434/v1"
            ),
            "model_name": (
                "qwen3.5:35b-mlx"
            ),
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

    def test_new_action_without_checkpoint(self):
        self.assertEqual(
            choose_generation_action(
                self.status("none")
            ),
            "new",
        )

    def test_resume_action_for_partial_checkpoint(
        self,
    ):
        self.assertEqual(
            choose_generation_action(
                self.status(
                    "compatible",
                    completed=4,
                )
            ),
            "resume",
        )

    def test_empty_compatible_checkpoint_is_new(
        self,
    ):
        self.assertEqual(
            choose_generation_action(
                self.status(
                    "compatible",
                    completed=0,
                )
            ),
            "new",
        )

    def test_finalization_action(self):
        self.assertEqual(
            choose_generation_action(
                self.status(
                    "finalization_pending",
                    completed=5,
                )
            ),
            "finalize",
        )

    def test_incompatible_action_is_blocked(
        self,
    ):
        with self.assertRaises(
            GenerationActionBlockedError
        ) as captured:
            choose_generation_action(
                self.status(
                    "incompatible",
                    reason_codes=[
                        "source_changed",
                        "model_changed",
                    ],
                )
            )

        self.assertEqual(
            captured.exception.checkpoint_status,
            "incompatible",
        )
        self.assertEqual(
            captured.exception.reason_codes,
            [
                "source_changed",
                "model_changed",
            ],
        )

    def test_corrupt_and_unknown_are_blocked(
        self,
    ):
        for checkpoint_status in (
            "corrupt",
            "invalid",
            "unknown",
        ):
            with self.subTest(
                checkpoint_status=(
                    checkpoint_status
                )
            ):
                with self.assertRaises(
                    GenerationActionBlockedError
                ):
                    choose_generation_action(
                        self.status(
                            checkpoint_status
                        )
                    )

    def test_running_action_is_blocked(self):
        with self.assertRaises(
            GenerationActionBlockedError
        ) as captured:
            choose_generation_action(
                self.status(
                    "none",
                    running=True,
                )
            )

        self.assertIn(
            "generation_already_running",
            captured.exception.reason_codes,
        )

    def test_blocked_action_does_not_delete_state(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            path = (
                Path(tmp)
                / "generation_state.json"
            )
            path.write_text(
                '{"saved": true}\n',
                encoding="utf-8",
            )
            before = path.read_bytes()

            with self.assertRaises(
                GenerationActionBlockedError
            ):
                choose_generation_action(
                    self.status(
                        "incompatible"
                    )
                )

            self.assertEqual(
                path.read_bytes(),
                before,
            )

    def test_discard_removes_only_checkpoint(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = (
                root
                / "generation_state.json"
            )
            script_path = (
                root
                / "annotated_script.json"
            )
            metadata_path = (
                root
                / "annotated_script.meta.json"
            )

            state_path.write_text(
                '{"saved": true}\n',
                encoding="utf-8",
            )
            script_path.write_text(
                "[]\n",
                encoding="utf-8",
            )
            metadata_path.write_text(
                '{"schema_version": 1}\n',
                encoding="utf-8",
            )

            removed = (
                discard_generation_checkpoint(
                    state_path
                )
            )

            self.assertTrue(removed)
            self.assertFalse(
                state_path.exists()
            )
            self.assertTrue(
                script_path.exists()
            )
            self.assertTrue(
                metadata_path.exists()
            )

            self.assertFalse(
                discard_generation_checkpoint(
                    state_path
                )
            )

    def _write_complete_checkpoint(
        self,
        *,
        root: Path,
        source_path: Path,
        config_path: Path,
        runtime,
    ):
        with (
            patch.object(
                generate_script,
                "_build_script_llm_client",
                return_value=(
                    runtime,
                    object(),
                ),
            ),
            patch.object(
                generate_script,
                "load_approved_roster_for_source",
                return_value=None,
            ),
        ):
            snapshot = (
                generate_script
                .build_script_generation_snapshot(
                    source_path,
                    config_path=config_path,
                )
            )

        state = new_generation_state(
            source_fingerprint=(
                snapshot[
                    "source_fingerprint"
                ]
            ),
            generation_fingerprint=(
                snapshot[
                    "generation_fingerprint"
                ]
            ),
            chunk_fingerprints=(
                snapshot[
                    "chunk_fingerprints"
                ]
            ),
            generation_identity=(
                snapshot[
                    "generation_identity"
                ]
            ),
            source={
                "basename": (
                    snapshot[
                        "source_basename"
                    ]
                ),
                "character_count": (
                    snapshot[
                        "source_character_count"
                    ]
                ),
            },
            auditor_contract_version=(
                snapshot[
                    "auditor_contract_version"
                ]
            ),
        )

        state_path = (
            root
            / "generation_state.json"
        )
        atomic_json_write(
            state,
            state_path,
        )

        for index, chunk_fingerprint in enumerate(
            snapshot[
                "chunk_fingerprints"
            ],
            start=1,
        ):
            state = checkpoint_completed_chunk(
                state=state,
                path=state_path,
                index=index,
                chunk_fingerprint=(
                    chunk_fingerprint
                ),
                entries=[
                    {
                        "speaker": "NARRATOR",
                        "text": (
                            f"Completed chunk "
                            f"{index}."
                        ),
                        "instruct": "Neutral.",
                    }
                ],
            )

        return snapshot

    def test_finalization_retry_skips_generation_and_preload(
        self,
    ):
        class FakeRuntime:
            model_name = "qwen3.5:35b-mlx"
            backend = "auto"
            thinking = False
            structured_output = True
            corrective_retry = True

            def preload(self):
                raise AssertionError(
                    "Finalization retry must "
                    "not preload the model."
                )

            def status(self):
                raise AssertionError(
                    "Finalization retry must "
                    "not query model status."
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "book.txt"
            config_path = root / "config.json"
            chunks_path = root / "chunks.json"

            source_path.write_text(
                "A short source passage.",
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
                            "chunk_size": 3000,
                            "max_tokens": 4096,
                            "temperature": 0.6,
                            "top_p": 0.8,
                        },
                        "prompts": {
                            "system_prompt": "system",
                            "user_prompt": "user",
                        },
                    }
                ),
                encoding="utf-8",
            )
            chunks_path.write_text(
                '{"stale": true}\n',
                encoding="utf-8",
            )

            runtime = FakeRuntime()
            snapshot = (
                self._write_complete_checkpoint(
                    root=root,
                    source_path=source_path,
                    config_path=config_path,
                    runtime=runtime,
                )
            )

            with (
                patch.object(
                    generate_script,
                    "_build_script_llm_client",
                    return_value=(
                        runtime,
                        object(),
                    ),
                ),
                patch.object(
                    generate_script,
                    "process_chunk",
                    side_effect=AssertionError(
                        "Finalization retry must "
                        "not generate a chunk."
                    ),
                ),
            ):
                result = (
                    generate_script
                    .finalize_completed_generation_checkpoint(
                        source_path,
                        root_dir=root,
                        config_path=config_path,
                    )
                )

            self.assertEqual(
                result["entry_count"],
                snapshot["total_chunks"],
            )
            self.assertFalse(
                (
                    root
                    / "generation_state.json"
                ).exists()
            )
            self.assertTrue(
                (
                    root
                    / "annotated_script.json"
                ).exists()
            )
            self.assertTrue(
                (
                    root
                    / "annotated_script.meta.json"
                ).exists()
            )
            self.assertFalse(
                chunks_path.exists()
            )

            metadata = json.loads(
                (
                    root
                    / "annotated_script.meta.json"
                ).read_text(
                    encoding="utf-8"
                )
            )

            self.assertTrue(
                metadata["resume"]["resumed"]
            )
            self.assertEqual(
                metadata["resume"][
                    "previously_completed_chunks"
                ],
                snapshot["total_chunks"],
            )

    def test_finalization_mismatch_preserves_state(
        self,
    ):
        class FakeRuntime:
            model_name = "qwen3.5:35b-mlx"
            backend = "auto"
            thinking = False
            structured_output = True
            corrective_retry = True

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "book.txt"
            config_path = root / "config.json"

            source_path.write_text(
                "Original source.",
                encoding="utf-8",
            )
            config_path.write_text(
                json.dumps(
                    {
                        "prompts": {
                            "system_prompt": "system",
                            "user_prompt": "user",
                        }
                    }
                ),
                encoding="utf-8",
            )

            runtime = FakeRuntime()

            self._write_complete_checkpoint(
                root=root,
                source_path=source_path,
                config_path=config_path,
                runtime=runtime,
            )

            state_path = (
                root
                / "generation_state.json"
            )
            before = state_path.read_bytes()

            source_path.write_text(
                "Changed source.",
                encoding="utf-8",
            )

            with patch.object(
                generate_script,
                "_build_script_llm_client",
                return_value=(
                    runtime,
                    object(),
                ),
            ):
                with self.assertRaises(
                    GenerationStateMismatchError
                ):
                    generate_script.finalize_completed_generation_checkpoint(
                        source_path,
                        root_dir=root,
                        config_path=config_path,
                    )

            self.assertEqual(
                state_path.read_bytes(),
                before,
            )
            self.assertFalse(
                (
                    root
                    / "annotated_script.json"
                ).exists()
            )
            self.assertFalse(
                (
                    root
                    / "annotated_script.meta.json"
                ).exists()
            )

    def test_finalization_write_failure_preserves_state(
        self,
    ):
        class FakeRuntime:
            model_name = "qwen3.5:35b-mlx"
            backend = "auto"
            thinking = False
            structured_output = True
            corrective_retry = True

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "book.txt"
            config_path = root / "config.json"

            source_path.write_text(
                "Source.",
                encoding="utf-8",
            )
            config_path.write_text(
                json.dumps(
                    {
                        "prompts": {
                            "system_prompt": "system",
                            "user_prompt": "user",
                        }
                    }
                ),
                encoding="utf-8",
            )

            runtime = FakeRuntime()

            self._write_complete_checkpoint(
                root=root,
                source_path=source_path,
                config_path=config_path,
                runtime=runtime,
            )

            state_path = (
                root
                / "generation_state.json"
            )

            with (
                patch.object(
                    generate_script,
                    "_build_script_llm_client",
                    return_value=(
                        runtime,
                        object(),
                    ),
                ),
                patch.object(
                    generate_script,
                    "finalize_generation_outputs",
                    side_effect=OSError(
                        "simulated finalization failure"
                    ),
                ),
            ):
                with self.assertRaises(OSError):
                    generate_script.finalize_completed_generation_checkpoint(
                        source_path,
                        root_dir=root,
                        config_path=config_path,
                    )

            self.assertTrue(
                state_path.exists()
            )


if __name__ == "__main__":
    unittest.main()
