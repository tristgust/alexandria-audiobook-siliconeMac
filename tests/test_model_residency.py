import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from model_memory import (
    ModelMemoryCoordinator,
    ModelMemoryError,
    write_model_memory_policy,
)


def identity(component_id: str, *, estimate: int = 100) -> dict:
    return {
        "component_id": component_id,
        "source_id": f"fixture/{component_id}",
        "revision": "a" * 40,
        "build_id": (component_id.encode("utf-8").hex() + "0" * 64)[:64],
        "runtime": "fixture-runtime",
        "estimated_loaded_memory_bytes": estimate,
    }


class ModelResidencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.policy_path = self.root / "model-memory.json"
        write_model_memory_policy(
            self.policy_path,
            {
                "schema_version": 1,
                "minimum_headroom_bytes": 0,
                "idle_unload_seconds": 10,
                "release_and_retry_on_oom": True,
            },
        )
        self.coordinator = ModelMemoryCoordinator(policy_path=self.policy_path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def register(
        self,
        slot_id: str,
        component_id: str,
        *,
        events: list[str] | None = None,
        release_error: Exception | None = None,
    ) -> dict:
        events = events if events is not None else []

        def synchronize() -> None:
            events.append(f"sync:{slot_id}")

        def release() -> bool:
            events.append(f"release:{slot_id}")
            if release_error is not None:
                raise release_error
            return True

        return self.coordinator.register_resident(
            slot_id=slot_id,
            component_id=component_id,
            release_callback=release,
            synchronize_callback=synchronize,
            engine_id=f"engine:{component_id}",
            device="mps",
            identity=identity(component_id),
            estimated_loaded_memory_bytes=100,
        )

    def test_status_reports_exact_identity_owner_and_lease(self) -> None:
        resident = self.register("mlx:clone", "fixture_clone")
        self.assertEqual(resident["revision"], "a" * 40)
        self.assertEqual(resident["device"], "mps")

        started = threading.Event()
        release = threading.Event()

        def worker() -> None:
            with self.coordinator.job(("fixture_clone",), label="synthesis"):
                started.set()
                release.wait(2)

        with self.coordinator.operation(
            {
                "job_id": "work_123",
                "domain": "audio_generation",
                "operation": "parallel",
            }
        ):
            thread = threading.Thread(target=worker)
            thread.start()
            self.assertTrue(started.wait(2))
            status = self.coordinator.status()
            self.assertEqual(status["current_owner"]["job_id"], "work_123")
            self.assertEqual(status["active_jobs"], 1)
            self.assertEqual(status["residents"][0]["active_lease_count"], 1)
            self.assertEqual(status["residents"][0]["owners"][0]["job_id"], "work_123")
            self.assertEqual(status["loaded_model_keys"], ["fixture_clone"])
            release.set()
            thread.join(2)
        self.assertEqual(self.coordinator.status()["active_jobs"], 0)

    def test_active_lease_blocks_release_and_generic_lease_blocks_everything(self) -> None:
        self.register("mlx:clone", "fixture_clone")
        self.register("mlx:design", "fixture_design")
        with self.coordinator.job(("fixture_clone",)):
            with self.assertRaises(ModelMemoryError) as blocked:
                self.coordinator.release_residents(
                    reason="manual",
                    slot_ids=["mlx:clone"],
                )
            self.assertEqual(blocked.exception.code, "model_residency_active_lease")
            released = self.coordinator.release_residents(
                reason="manual",
                slot_ids=["mlx:design"],
            )
            self.assertEqual(released["released_slots"], ["mlx:design"])

        self.register("mlx:design", "fixture_design")
        with self.coordinator.job():
            with self.assertRaises(ModelMemoryError) as generic:
                self.coordinator.release_residents(reason="manual")
            self.assertEqual(generic.exception.code, "model_residency_active_lease")

    def test_manual_and_idle_release_block_during_scheduler_operation(self) -> None:
        self.register("mlx:clone", "fixture_clone")
        with self.coordinator.operation(
            {
                "job_id": "work_audio",
                "domain": "audio_generation",
                "operation": "generate_audio",
            }
        ):
            with self.assertRaises(ModelMemoryError) as manual:
                self.coordinator.release_residents(reason="manual")
            self.assertEqual(
                manual.exception.code,
                "model_residency_active_operation",
            )
            self.coordinator._last_activity_monotonic = -100.0
            with self.assertRaises(ModelMemoryError) as idle:
                self.coordinator.release_residents_if_idle(now=100.0)
            self.assertEqual(
                idle.exception.code,
                "model_residency_active_operation",
            )

    def test_low_memory_evicts_only_idle_resident_before_load(self) -> None:
        events: list[str] = []
        self.register("mlx:old", "fixture_old", events=events)
        installed: dict[str, object] = {}

        def load() -> object:
            events.append("load:new")
            return object()

        def install(value: object) -> None:
            installed["model"] = value
            events.append("install:new")

        def release_new() -> bool:
            installed.pop("model", None)
            events.append("release:new")
            return True

        snapshots = [
            {"total_bytes": 1000, "available_bytes": 20, "used_bytes": 980},
            {"total_bytes": 1000, "available_bytes": 20, "used_bytes": 980},
            {"total_bytes": 1000, "available_bytes": 220, "used_bytes": 780},
            {"total_bytes": 1000, "available_bytes": 220, "used_bytes": 780},
        ]
        with patch("model_memory.memory_snapshot", side_effect=snapshots):
            loaded = self.coordinator.load_resident(
                slot_id="mlx:new",
                component_id="fixture_new",
                load_callback=load,
                install_callback=install,
                release_callback=release_new,
                engine_id="engine:new",
                device="mps",
                identity=identity("fixture_new"),
                estimated_loaded_memory_bytes=100,
            )

        self.assertIs(loaded, installed["model"])
        self.assertEqual(
            events,
            ["sync:mlx:old", "release:mlx:old", "load:new", "install:new"],
        )
        status = self.coordinator.status()
        self.assertEqual(status["loaded_model_keys"], ["fixture_new"])
        self.assertEqual(status["planned_eviction"]["status"], "completed")
        self.assertEqual(
            status["last_release"]["measured_available_bytes_recovered"],
            200,
        )

    def test_prepared_job_closes_load_to_lease_transition_gap(self) -> None:
        release_started = threading.Event()
        release_finished = threading.Event()
        release_errors: list[str] = []

        def prepare() -> object:
            self.register("mlx:new", "fixture_new")

            def release_worker() -> None:
                release_started.set()
                try:
                    self.coordinator.release_residents(
                        reason="memory_pressure",
                        slot_ids=["mlx:new"],
                    )
                except ModelMemoryError as exc:
                    release_errors.append(exc.code)
                finally:
                    release_finished.set()

            threading.Thread(target=release_worker).start()
            self.assertTrue(release_started.wait(2))
            return object()

        with self.coordinator.prepared_job(
            ("fixture_new",),
            prepare,
            label="atomic fixture generation",
        ):
            self.assertTrue(release_finished.wait(2))
            self.assertEqual(release_errors, ["model_residency_active_lease"])
            self.assertEqual(
                self.coordinator.status()["residents"][0]["state"],
                "resident",
            )

    def test_pressure_cannot_evict_leased_resident(self) -> None:
        events: list[str] = []
        self.register("mlx:old", "fixture_old", events=events)
        low = {"total_bytes": 1000, "available_bytes": 20, "used_bytes": 980}
        with self.coordinator.job(("fixture_old",)):
            with (
                patch("model_memory.memory_snapshot", return_value=low),
                self.assertRaises(ModelMemoryError) as blocked,
            ):
                self.coordinator.load_resident(
                    slot_id="mlx:new",
                    component_id="fixture_new",
                    load_callback=lambda: object(),
                    install_callback=lambda _value: None,
                    release_callback=lambda: True,
                    identity=identity("fixture_new"),
                    estimated_loaded_memory_bytes=100,
                )
        self.assertEqual(blocked.exception.code, "model_residency_admission_blocked")
        self.assertEqual(events, [])
        self.assertEqual(
            self.coordinator.status()["planned_eviction"]["blocked_residents"][0][
                "component_id"
            ],
            "fixture_old",
        )

    def test_release_failure_remains_truthful_and_retryable(self) -> None:
        attempts = {"count": 0}

        def release() -> bool:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("synthetic release failure")
            return True

        self.coordinator.register_resident(
            slot_id="mlx:broken",
            component_id="fixture_broken",
            release_callback=release,
            synchronize_callback=lambda: None,
            engine_id="engine:fixture_broken",
            device="mps",
            identity=identity("fixture_broken"),
            estimated_loaded_memory_bytes=100,
        )
        with self.assertRaises(ModelMemoryError) as failed:
            self.coordinator.release_residents(reason="manual")
        self.assertEqual(failed.exception.code, "model_residency_release_failed")
        status = self.coordinator.status()
        self.assertEqual(status["residents"][0]["state"], "release_failed")
        self.assertIn("synthetic release failure", status["residents"][0]["last_error"])
        self.assertEqual(status["blockers"][0]["reason"], "release_failed")

        recovered = self.coordinator.release_residents(reason="manual_retry")
        self.assertEqual(recovered["released_slots"], ["mlx:broken"])
        self.assertEqual(recovered["failures"], [])
        self.assertEqual(self.coordinator.status()["residents"], [])

    def test_idle_release_is_measured_and_respects_activity(self) -> None:
        self.register("mlx:clone", "fixture_clone")
        time_value = 0.0
        not_idle = self.coordinator.release_residents_if_idle(now=time_value)
        self.assertFalse(not_idle["released"])
        self.assertEqual(not_idle["reason"], "not_idle")
        self.coordinator._last_activity_monotonic = time_value - 20.0
        snapshots = [
            {"total_bytes": 1000, "available_bytes": 100, "used_bytes": 900},
            {"total_bytes": 1000, "available_bytes": 350, "used_bytes": 650},
        ]
        with patch("model_memory.memory_snapshot", side_effect=snapshots):
            released = self.coordinator.release_residents_if_idle(now=time_value)
        self.assertTrue(released["released"])
        self.assertEqual(released["reason"], "idle_policy")
        self.assertEqual(released["measured_available_bytes_recovered"], 250)


if __name__ == "__main__":
    unittest.main()
