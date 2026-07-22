from __future__ import annotations

import unittest

from recovery_status import (
    RECOVERY_STATES,
    RecoveryStatusError,
    build_audio_stage,
    build_dataset_stage,
    build_persona_stage,
    build_recovery_summary,
    build_roster_stage,
    build_script_stage,
    build_training_stage,
    build_visual_stage,
    capped_logs,
    stage,
)


SOURCE = {
    "persisted": True,
    "path": "/project/uploads/book.txt",
    "basename": "book.txt",
    "exists": True,
    "readable": True,
    "error": None,
}


def script_status(checkpoint: dict, result: dict | None = None, *, running=False):
    return {
        "process": {"running": running, "logs": ["line"] if running else []},
        "checkpoint": checkpoint,
        "result": result
        or {
            "script_exists": False,
            "script_status": "missing",
            "metadata_status": "missing",
        },
    }


def roster_status(progress: dict, *, running=False, draft=None, approved=None):
    return {
        "source": {"available": True},
        "active": "none",
        "draft": draft
        or {"exists": False, "status": "missing", "fingerprint": None},
        "approved": approved
        or {"exists": False, "status": "missing", "fingerprint": None},
        "process": {"running": running, "logs": []},
        "progress": progress,
    }


class RecoveryStatusContractTests(unittest.TestCase):
    def test_stage_rejects_unsupported_state(self) -> None:
        with self.assertRaises(RecoveryStatusError):
            stage("x", "X", "paused", summary="No.")

    def test_contract_state_set_is_exact(self) -> None:
        self.assertEqual(
            RECOVERY_STATES,
            {
                "new",
                "running",
                "resumable",
                "finalization_only",
                "restart_required",
                "complete",
                "blocked",
                "invalid",
                "unavailable",
            },
        )

    def test_logs_are_capped_without_mutating_input(self) -> None:
        process = {"running": True, "logs": [f"line-{i}" for i in range(6)]}
        value = capped_logs(process, limit=3)
        self.assertEqual(value["lines"], ["line-3", "line-4", "line-5"])
        self.assertTrue(value["truncated"])
        self.assertEqual(len(process["logs"]), 6)


class ScriptRecoveryTests(unittest.TestCase):
    def test_compatible_checkpoint_is_resumable(self) -> None:
        value = build_script_stage(
            script_status(
                {
                    "status": "compatible",
                    "completed_chunks": 17,
                    "total_chunks": 155,
                    "next_chunk": 18,
                }
            ),
            SOURCE,
            checkpoint_at="2026-07-17T12:00:00Z",
        )
        self.assertEqual(value["state"], "resumable")
        self.assertEqual(value["primary_action"]["label"], "Resume script from chunk 18")
        self.assertEqual(value["discard_action"]["endpoint"], "/api/script_generation/discard")
        self.assertEqual(value["progress"]["last_checkpoint_at"], "2026-07-17T12:00:00Z")

    def test_finalization_checkpoint_has_exact_action(self) -> None:
        value = build_script_stage(
            script_status(
                {
                    "status": "finalization_pending",
                    "completed_chunks": 155,
                    "total_chunks": 155,
                    "next_chunk": None,
                }
            ),
            SOURCE,
        )
        self.assertEqual(value["state"], "finalization_only")
        self.assertEqual(value["primary_action"]["label"], "Retry script finalization")

    def test_incompatible_checkpoint_is_blocked_not_restartable(self) -> None:
        value = build_script_stage(
            script_status(
                {
                    "status": "incompatible",
                    "completed_chunks": 17,
                    "total_chunks": 155,
                    "next_chunk": 18,
                    "reason_codes": ["chunk_layout_changed"],
                    "explanation": "Generation cannot resume: Chunk layout changed.",
                },
                {
                    "script_exists": True,
                    "script_status": "valid",
                    "metadata_status": "legacy",
                },
            ),
            SOURCE,
        )
        self.assertEqual(value["state"], "blocked")
        self.assertIsNone(value["primary_action"])
        self.assertEqual(value["discard_action"]["kind"], "discard_script_checkpoint")

    def test_corrupt_checkpoint_is_invalid(self) -> None:
        value = build_script_stage(
            script_status(
                {
                    "status": "corrupt",
                    "explanation": "Unreadable JSON.",
                }
            ),
            SOURCE,
        )
        self.assertEqual(value["state"], "invalid")
        self.assertEqual(value["reason"], "Unreadable JSON.")

    def test_valid_script_without_checkpoint_is_complete(self) -> None:
        value = build_script_stage(
            script_status(
                {"status": "none"},
                {
                    "script_exists": True,
                    "script_status": "valid",
                    "metadata_status": "valid",
                },
            ),
            SOURCE,
        )
        self.assertEqual(value["state"], "complete")

    def test_missing_source_blocks_new_script(self) -> None:
        value = build_script_stage(
            script_status({"status": "none"}),
            {**SOURCE, "exists": False, "error": "Missing source."},
        )
        self.assertEqual(value["state"], "blocked")
        self.assertEqual(value["reason"], "Missing source.")


class RosterRecoveryTests(unittest.TestCase):
    def test_roster_resumes_from_passage(self) -> None:
        value = build_roster_stage(
            roster_status(
                {
                    "exists": True,
                    "status": "resumable",
                    "completed_passages": 6,
                    "total_passages": 42,
                    "next_passage": 7,
                }
            ),
            SOURCE,
        )
        self.assertEqual(value["state"], "resumable")
        self.assertEqual(value["primary_action"]["label"], "Resume roster from passage 7")
        self.assertEqual(value["discard_action"]["endpoint"], "/api/character_roster/discard-progress")

    def test_roster_reconciliation_is_finalization_only(self) -> None:
        value = build_roster_stage(
            roster_status(
                {
                    "exists": True,
                    "status": "awaiting_reconciliation",
                    "completed_passages": 42,
                    "total_passages": 42,
                    "next_passage": None,
                }
            ),
            SOURCE,
        )
        self.assertEqual(value["state"], "finalization_only")
        self.assertEqual(value["primary_action"]["label"], "Run roster reconciliation")

    def test_approved_roster_is_complete_even_with_old_checkpoint(self) -> None:
        value = build_roster_stage(
            roster_status(
                {
                    "exists": True,
                    "status": "resumable",
                    "completed_passages": 1,
                    "total_passages": 3,
                    "next_passage": 2,
                },
                approved={
                    "exists": True,
                    "status": "approved",
                    "fingerprint": "approved-fingerprint",
                },
            ),
            SOURCE,
        )
        self.assertEqual(value["state"], "complete")
        self.assertIsNotNone(value["discard_action"])


class VisualPersonaDatasetAudioTrainingTests(unittest.TestCase):
    def test_visuals_are_unavailable_without_approved_roster(self) -> None:
        value = build_visual_stage(
            {
                "approved_roster_available": False,
                "context_error": "Approve a roster.",
                "process": {"running": False, "logs": []},
                "progress": {"exists": False, "status": "none"},
            },
            SOURCE,
        )
        self.assertEqual(value["state"], "unavailable")
        self.assertEqual(value["reason"], "Approve a roster.")

    def test_visual_reconciliation_is_finalization_only(self) -> None:
        value = build_visual_stage(
            {
                "approved_roster_available": True,
                "process": {"running": False, "logs": []},
                "progress": {
                    "exists": True,
                    "status": "complete_pending_reconciliation",
                    "completed_passages": 10,
                    "total_passages": 10,
                },
            },
            SOURCE,
        )
        self.assertEqual(value["state"], "finalization_only")
        self.assertEqual(value["primary_action"]["label"], "Finish visual dossiers")

    def test_partial_personas_require_restart_not_fake_resume(self) -> None:
        value = build_persona_stage(
            process={"running": False, "logs": []},
            configured_speakers=3,
            total_speakers=10,
            script_available=True,
        )
        self.assertEqual(value["state"], "restart_required")
        self.assertEqual(value["primary_action"]["label"], "Restart persona generation")
        self.assertFalse(value["details"]["durably_resumable"])

    def test_partial_dataset_is_resumable_at_sample_boundary(self) -> None:
        value = build_dataset_stage(
            projects=[{"name": "doctor", "sample_count": 8, "done_count": 3}],
            process={"running": False, "logs": []},
        )
        self.assertEqual(value["state"], "resumable")
        self.assertEqual(value["primary_action"]["label"], "Continue dataset doctor from sample 4")

    def test_partial_audio_is_resumable_at_chunk_boundary(self) -> None:
        value = build_audio_stage(
            chunks=[
                {"status": "done", "audio_path": "audio/0.mp3"},
                {"status": "pending", "audio_path": None},
            ],
            process={"running": False, "logs": []},
        )
        self.assertEqual(value["state"], "resumable")
        self.assertEqual(value["primary_action"]["label"], "Resume audio from chunk 2")

    def test_failed_training_job_requires_restart(self) -> None:
        value = build_training_stage(
            {
                "environment_exists": True,
                "experimental": True,
                "jobs": [
                    {
                        "job_id": "job-1",
                        "action": "train_lora",
                        "status": "failed",
                        "error": "Out of memory.",
                    }
                ],
            }
        )
        self.assertEqual(value["state"], "restart_required")
        self.assertEqual(value["reason"], "Out of memory.")


class AggregateRecoveryTests(unittest.TestCase):
    def test_summary_has_every_required_stage_and_is_model_free(self) -> None:
        value = build_recovery_summary(
            source=SOURCE,
            script_status=script_status({"status": "none"}),
            roster_status=roster_status(
                {
                    "exists": False,
                    "status": "missing",
                    "completed_passages": 0,
                    "total_passages": 0,
                }
            ),
            visual_status={
                "approved_roster_available": False,
                "context_error": "Approve a roster.",
                "process": {"running": False, "logs": []},
                "progress": {"exists": False, "status": "none"},
            },
            persona={
                "process": {"running": False, "logs": []},
                "configured_speakers": 0,
                "total_speakers": 0,
                "script_available": False,
            },
            dataset={"projects": [], "process": {"running": False, "logs": []}},
            audio={"chunks": [], "process": {"running": False, "logs": []}},
            training_status={"environment_exists": False, "jobs": []},
        )
        self.assertTrue(value["model_free"])
        self.assertTrue(value["file_pure"])
        self.assertEqual(
            [item["id"] for item in value["stages"]],
            [
                "script",
                "roster",
                "visual",
                "persona",
                "dataset_builder",
                "audio",
                "experimental_training",
            ],
        )
        self.assertEqual(len(value["stages"]), 7)


if __name__ == "__main__":
    unittest.main()
