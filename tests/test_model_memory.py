from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from model_memory import (
    ModelMemoryCoordinator,
    ModelMemoryError,
    admission_status,
    default_model_memory_policy,
    is_recognized_allocation_failure,
    read_model_memory_policy,
)
from model_registry import model_spec


class ModelMemoryTests(unittest.TestCase):
    def test_admission_is_conservative_and_model_specific(self) -> None:
        spec = model_spec("mlx_clone")
        required = (
            spec.estimated_loaded_memory_bytes
            + default_model_memory_policy()["minimum_headroom_bytes"]
        )
        admitted = admission_status(
            spec.key,
            snapshot={
                "total_bytes": required * 2,
                "available_bytes": required,
                "used_bytes": required,
            },
        )
        self.assertTrue(admitted["admitted"])
        denied = admission_status(
            spec.key,
            snapshot={
                "total_bytes": required,
                "available_bytes": required - 1,
                "used_bytes": 1,
            },
        )
        self.assertFalse(denied["admitted"])
        self.assertIn("including headroom", denied["reason"])

    def test_policy_round_trip_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "policy.json"
            coordinator = ModelMemoryCoordinator(policy_path=path)
            updated = coordinator.update_policy(
                {
                    "schema_version": 1,
                    "minimum_headroom_bytes": 1024,
                    "idle_unload_seconds": 60,
                    "release_and_retry_on_oom": False,
                }
            )
            self.assertEqual(read_model_memory_policy(path), updated)
            with self.assertRaises(ModelMemoryError) as error:
                coordinator.update_policy(
                    {
                        "minimum_headroom_bytes": -1,
                        "idle_unload_seconds": 60,
                        "release_and_retry_on_oom": True,
                    }
                )
            self.assertEqual(error.exception.code, "model_memory_policy_invalid")

    def test_active_jobs_block_manual_and_idle_release(self) -> None:
        coordinator = ModelMemoryCoordinator()
        released = []
        with coordinator.job():
            self.assertEqual(coordinator.active_jobs, 1)
            with self.assertRaises(ModelMemoryError) as error:
                coordinator.release(lambda: released.append(True), reason="manual")
            self.assertEqual(error.exception.code, "model_memory_active_jobs")
            idle = coordinator.release_if_idle(lambda: released.append(True), now=10**9)
            self.assertFalse(idle["released"])
        self.assertEqual(released, [])
        result = coordinator.release(lambda: released.append(True) or True, reason="manual")
        self.assertTrue(result["released"])
        self.assertEqual(released, [True])

    def test_concurrent_job_accounting_is_exact(self) -> None:
        coordinator = ModelMemoryCoordinator()
        entered = threading.Barrier(3)
        exit_barrier = threading.Barrier(3)

        def worker() -> None:
            with coordinator.job():
                entered.wait()
                exit_barrier.wait()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        entered.wait()
        self.assertEqual(coordinator.active_jobs, 2)
        exit_barrier.wait()
        for thread in threads:
            thread.join()
        self.assertEqual(coordinator.active_jobs, 0)

    def test_oom_retry_releases_once_and_never_loops(self) -> None:
        coordinator = ModelMemoryCoordinator()
        calls = []
        releases = []

        def operation():
            calls.append(True)
            if len(calls) == 1:
                raise RuntimeError("MPS backend out of memory")
            return "ok"

        with patch(
            "model_memory.memory_snapshot",
            return_value={
                "total_bytes": 128 * 1024**3,
                "available_bytes": 128 * 1024**3,
                "used_bytes": 0,
            },
        ):
            result = coordinator.run_with_oom_retry(
                "mlx_clone",
                operation,
                lambda: releases.append(True) or True,
            )
        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 2)
        self.assertEqual(releases, [True])

        calls.clear()
        releases.clear()
        with patch(
            "model_memory.memory_snapshot",
            return_value={
                "total_bytes": 128 * 1024**3,
                "available_bytes": 128 * 1024**3,
                "used_bytes": 0,
            },
        ):
            with self.assertRaises(ModelMemoryError) as error:
                coordinator.run_with_oom_retry(
                    "mlx_clone",
                    lambda: (_ for _ in ()).throw(MemoryError("out of memory")),
                    lambda: releases.append(True) or True,
                )
        self.assertEqual(error.exception.code, "model_memory_retry_exhausted")
        self.assertEqual(releases, [True])

    def test_non_allocation_failures_are_not_retried(self) -> None:
        self.assertTrue(is_recognized_allocation_failure(MemoryError()))
        self.assertFalse(is_recognized_allocation_failure(ValueError("bad input")))
        coordinator = ModelMemoryCoordinator()
        releases = []
        with patch(
            "model_memory.memory_snapshot",
            return_value={
                "total_bytes": 128 * 1024**3,
                "available_bytes": 128 * 1024**3,
                "used_bytes": 0,
            },
        ):
            with self.assertRaisesRegex(ValueError, "bad input"):
                coordinator.run_with_oom_retry(
                    "mlx_clone",
                    lambda: (_ for _ in ()).throw(ValueError("bad input")),
                    lambda: releases.append(True),
                )
        self.assertEqual(releases, [])


if __name__ == "__main__":
    unittest.main()
