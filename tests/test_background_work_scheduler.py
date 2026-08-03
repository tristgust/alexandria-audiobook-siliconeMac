from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from background_work import (
    BackgroundWorkError,
    claim_job,
    claim_next_job,
    configure_scheduler,
    finish_job,
    get_job,
    list_jobs,
    reconcile_interrupted_jobs,
    request_cancel,
    scheduler_status,
    should_cancel,
    submit_job,
    update_progress,
)


ROOT = Path(__file__).resolve().parents[1]


class BackgroundWorkSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        configure_scheduler(self.root, max_pending=4)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def submit(
        self,
        domain: str,
        *,
        operation: str = "run",
        resources: tuple[str, ...] = ("project_write",),
        dependency: str | None = "a" * 64,
        request: dict | None = None,
        resumable: bool = True,
        priority: int = 100,
        external_ref: dict | None = None,
        allow_retry: bool = False,
    ) -> dict:
        return submit_job(
            self.root,
            domain=domain,
            operation=operation,
            resources=resources,
            dependency_fingerprint=dependency,
            request=request or {"domain": domain, "operation": operation},
            resumable=resumable,
            priority=priority,
            external_ref=external_ref,
            allow_retry=allow_retry,
        )

    def test_duplicate_submission_returns_one_durable_job(self) -> None:
        first = self.submit("audio_generation")
        duplicate = self.submit("audio_generation")
        self.assertFalse(first["duplicate"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(first["job"]["job_id"], duplicate["job"]["job_id"])
        self.assertEqual(len(list_jobs(self.root)), 1)

        claimed = claim_job(self.root, first["job"]["job_id"])
        completed = finish_job(
            self.root,
            claimed["job_id"],
            owner_token=claimed["owner_token"],
            publication_token=claimed["publication_token"],
            current_dependency_fingerprint="a" * 64,
            result={"request_id": "audio_request_1"},
        )
        self.assertEqual(completed["state"], "succeeded")
        self.assertIsNone(completed["owner_token"])
        self.assertIsNone(completed["publication_token"])
        after = self.submit("audio_generation")
        self.assertTrue(after["duplicate"])
        self.assertEqual(after["job"]["state"], "succeeded")

    def test_orphan_job_file_is_recovered_and_sequence_does_not_reuse(self) -> None:
        first = self.submit("audio_generation", request={"id": 1})["job"]
        index_path = self.root / "background_work" / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        index["job_ids"] = []
        index["next_sequence"] = 1
        index_path.write_text(json.dumps(index), encoding="utf-8")

        recovered = list_jobs(self.root)
        self.assertEqual([item["job_id"] for item in recovered], [first["job_id"]])
        second = self.submit(
            "export",
            request={"id": 2},
            resources=("project_export",),
            allow_retry=True,
        )["job"]
        self.assertGreater(second["sequence"], first["sequence"])
        self.assertEqual(len(list_jobs(self.root)), 2)

    def test_record_fingerprint_detects_job_tampering(self) -> None:
        job = self.submit("audio_generation", request={"id": 1})["job"]
        path = self.root / "background_work" / "jobs" / f"{job['job_id']}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["request"]["id"] = 99
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(BackgroundWorkError) as corrupt:
            get_job(self.root, job["job_id"])
        self.assertEqual(corrupt.exception.code, "background_work_corrupt")

    def test_retry_permission_never_duplicates_active_work(self) -> None:
        first = self.submit("export", resources=("project_export",))
        duplicate = self.submit(
            "export",
            resources=("project_export",),
            allow_retry=True,
        )
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(first["job"]["job_id"], duplicate["job"]["job_id"])
        self.assertEqual(len(list_jobs(self.root)), 1)

    def test_bounded_queue_rejects_pressure_without_partial_record(self) -> None:
        configure_scheduler(self.root, max_pending=2)
        self.submit("audio_generation", request={"id": 1})
        self.submit("export", request={"id": 2}, resources=("project_export",))
        with self.assertRaises(BackgroundWorkError) as error:
            self.submit("model_cache", request={"id": 3}, resources=("model_cache",))
        self.assertEqual(error.exception.code, "background_work_backpressure")
        self.assertEqual(len(list_jobs(self.root)), 2)

    def test_resource_serialization_and_fair_domain_order(self) -> None:
        audio_one = self.submit(
            "audio_generation",
            request={"id": 1},
            resources=("model_runtime", "project_audio"),
        )["job"]
        audio_two = self.submit(
            "audio_generation",
            request={"id": 2},
            resources=("model_runtime", "project_audio"),
        )["job"]
        export = self.submit(
            "export",
            request={"id": 3},
            resources=("project_export",),
        )["job"]

        claimed_audio = claim_next_job(self.root)
        self.assertEqual(claimed_audio["job_id"], audio_one["job_id"])
        claimed_export = claim_next_job(self.root)
        self.assertEqual(claimed_export["job_id"], export["job_id"])
        with self.assertRaises(BackgroundWorkError) as blocked:
            claim_job(self.root, audio_two["job_id"])
        self.assertEqual(blocked.exception.code, "background_work_resource_busy")

    def test_explicit_worker_claim_cannot_bypass_scheduler_order(self) -> None:
        first = self.submit(
            "delivery_plan",
            request={"id": 1},
            resources=("project_plan",),
        )["job"]
        second = self.submit(
            "export",
            request={"id": 2},
            resources=("project_export",),
        )["job"]
        with self.assertRaises(BackgroundWorkError) as out_of_turn:
            claim_job(self.root, second["job_id"])
        self.assertEqual(out_of_turn.exception.code, "background_work_not_turn")
        self.assertEqual(
            out_of_turn.exception.details["next_job_id"],
            first["job_id"],
        )
        self.assertEqual(claim_job(self.root, first["job_id"])["state"], "running")
        self.assertEqual(claim_job(self.root, second["job_id"])["state"], "running")

    def test_blocked_high_priority_job_does_not_idle_free_resources(self) -> None:
        blocker = self.submit(
            "audio_generation",
            request={"id": 1},
            resources=("model_runtime",),
            priority=0,
        )["job"]
        claim_job(self.root, blocker["job_id"])
        high_blocked = self.submit(
            "voice_preparation",
            request={"id": 2},
            resources=("model_runtime",),
            priority=10,
        )["job"]
        low_runnable = self.submit(
            "export",
            request={"id": 3},
            resources=("project_export",),
            priority=100,
        )["job"]
        claimed = claim_next_job(self.root)
        self.assertEqual(claimed["job_id"], low_runnable["job_id"])
        self.assertEqual(get_job(self.root, high_blocked["job_id"])["state"], "queued")

    def test_cancel_has_precedence_over_final_success(self) -> None:
        job = self.submit("export", resources=("project_export",))["job"]
        claimed = claim_job(self.root, job["job_id"])
        cancelling = request_cancel(self.root, job["job_id"], reason="user_cancelled")
        self.assertEqual(cancelling["state"], "cancelling")
        self.assertTrue(should_cancel(self.root, job["job_id"], claimed["owner_token"]))
        terminal = finish_job(
            self.root,
            job["job_id"],
            owner_token=claimed["owner_token"],
            publication_token=claimed["publication_token"],
            current_dependency_fingerprint="a" * 64,
            result={"would_have_published": True},
        )
        self.assertEqual(terminal["state"], "cancelled")
        self.assertIsNone(terminal["result"])
        self.assertFalse(terminal["publication_authorized"])

    def test_publication_callback_is_joined_with_cancel_gate(self) -> None:
        job = self.submit("export", resources=("project_export",))["job"]
        claimed = claim_job(self.root, job["job_id"])
        published: list[str] = []
        completed = finish_job(
            self.root,
            claimed["job_id"],
            owner_token=claimed["owner_token"],
            publication_token=claimed["publication_token"],
            current_dependency_fingerprint="a" * 64,
            result={"status": "complete"},
            publisher=lambda: published.append("committed"),
        )
        self.assertEqual(completed["state"], "succeeded")
        self.assertEqual(published, ["committed"])

        cancelled_job = self.submit(
            "voice_preparation",
            request={"id": "cancelled"},
            resources=("voice_preparation",),
        )["job"]
        cancelled_claim = claim_job(self.root, cancelled_job["job_id"])
        request_cancel(self.root, cancelled_job["job_id"])
        terminal = finish_job(
            self.root,
            cancelled_claim["job_id"],
            owner_token=cancelled_claim["owner_token"],
            publication_token=cancelled_claim["publication_token"],
            current_dependency_fingerprint="a" * 64,
            result={"status": "complete"},
            publisher=lambda: published.append("must-not-run"),
        )
        self.assertEqual(terminal["state"], "cancelled")
        self.assertEqual(published, ["committed"])

    def test_stale_dependency_and_old_worker_cannot_publish(self) -> None:
        job = self.submit("delivery_plan", resources=("project_plan",))["job"]
        claimed = claim_job(self.root, job["job_id"])
        stale = finish_job(
            self.root,
            job["job_id"],
            owner_token=claimed["owner_token"],
            publication_token=claimed["publication_token"],
            current_dependency_fingerprint="b" * 64,
            result={"plan": "stale"},
        )
        self.assertEqual(stale["state"], "stale")
        self.assertIsNone(stale["result"])

        retry = self.submit(
            "delivery_plan",
            resources=("project_plan",),
            request={"retry": 1},
            dependency="b" * 64,
        )["job"]
        retried = claim_job(self.root, retry["job_id"])
        with self.assertRaises(BackgroundWorkError) as old_worker:
            finish_job(
                self.root,
                retry["job_id"],
                owner_token=retried["owner_token"],
                publication_token=claimed["publication_token"],
                current_dependency_fingerprint="b" * 64,
                result={"plan": "wrong worker"},
            )
        self.assertEqual(old_worker.exception.code, "background_work_stale_publication")
        self.assertEqual(get_job(self.root, retry["job_id"])["state"], "running")

    def test_restart_requeues_resumable_and_fails_nonresumable(self) -> None:
        resumable = self.submit(
            "audio_generation",
            request={"id": 1},
            resources=("project_audio",),
            resumable=True,
        )["job"]
        nonresumable = self.submit(
            "export",
            request={"id": 2},
            resources=("project_export",),
            resumable=False,
        )["job"]
        claim_job(self.root, resumable["job_id"])
        claim_job(self.root, nonresumable["job_id"])

        receipt = reconcile_interrupted_jobs(self.root, at_utc="2026-08-02T23:59:00Z")
        self.assertEqual(receipt["requeued"], [resumable["job_id"]])
        self.assertEqual(receipt["failed"], [nonresumable["job_id"]])
        recovered = get_job(self.root, resumable["job_id"])
        failed = get_job(self.root, nonresumable["job_id"])
        self.assertEqual(recovered["state"], "queued")
        self.assertEqual(recovered["recovery_count"], 1)
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(failed["terminal_reason"], "interrupted_nonresumable")

    def test_progress_and_external_reference_survive_fresh_process(self) -> None:
        job = self.submit(
            "audio_generation",
            resources=("project_audio", "model_runtime"),
            external_ref={
                "authority": "audio_generation_request",
                "request_id": "audio_request_abc",
            },
        )["job"]
        claimed = claim_job(self.root, job["job_id"])
        update_progress(
            self.root,
            job["job_id"],
            owner_token=claimed["owner_token"],
            completed=3,
            total=10,
            message="Three lines complete",
        )
        code = (
            "import json,sys; "
            "from background_work import scheduler_status; "
            "print(json.dumps(scheduler_status(sys.argv[1]), sort_keys=True))"
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "app")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        process = subprocess.run(
            [sys.executable, "-c", code, str(self.root)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        status = json.loads(process.stdout)
        restored = next(item for item in status["jobs"] if item["job_id"] == job["job_id"])
        self.assertEqual(restored["progress"]["completed"], 3)
        self.assertEqual(
            restored["external_ref"]["request_id"],
            "audio_request_abc",
        )

    def test_scheduler_status_is_truthful_and_bounded(self) -> None:
        running = self.submit("export", resources=("project_export",))["job"]
        claim_job(self.root, running["job_id"])
        queued = self.submit("voice_preparation", resources=("model_runtime",))["job"]
        request_cancel(self.root, queued["job_id"])
        status = scheduler_status(self.root, history_limit=1)
        self.assertEqual(status["counts"]["running"], 1)
        self.assertEqual(status["counts"]["cancelled"], 1)
        self.assertEqual(status["active_count"], 1)
        self.assertEqual(len(status["history"]), 1)

    def test_empty_status_read_is_filesystem_pure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            status = scheduler_status(root)
            self.assertEqual(status["active_count"], 0)
            self.assertEqual(status["jobs"], [])
            self.assertFalse((root / "background_work").exists())


if __name__ == "__main__":
    unittest.main()
