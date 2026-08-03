from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from model_memory import ModelMemoryCoordinator
from responsive_voice_backend import (
    IndexTTS2SidecarClient,
    ResponsiveVoiceBackend,
)


class ResponsiveModelResidencyTests(unittest.TestCase):
    def test_router_shares_one_residency_authority_with_local_specialists(self) -> None:
        coordinator = ModelMemoryCoordinator()
        backend = ResponsiveVoiceBackend(model_residency=coordinator)
        self.assertIs(backend._memory, coordinator)
        self.assertIs(backend.index._memory, coordinator)
        self.assertIs(backend.vox._memory, coordinator)

    def test_indextts_start_and_registration_are_reentrant_under_request_lock(self) -> None:
        coordinator = ModelMemoryCoordinator()
        client = IndexTTS2SidecarClient(model_residency=coordinator)
        fake_process = SimpleNamespace(poll=lambda: None)

        def start_process() -> None:
            client._process = fake_process

        finished = threading.Event()
        failures: list[BaseException] = []

        def worker() -> None:
            try:
                with client._lock:
                    client._ensure_started()
            except BaseException as exc:  # pragma: no cover - asserted below
                failures.append(exc)
            finally:
                finished.set()

        available = {
            "total_bytes": 128 * 1024**3,
            "available_bytes": 64 * 1024**3,
            "used_bytes": 64 * 1024**3,
        }
        with (
            patch.object(client, "_start_process", side_effect=start_process),
            patch("model_memory.memory_snapshot", return_value=available),
        ):
            thread = threading.Thread(target=worker)
            thread.start()
            self.assertTrue(finished.wait(2), "IndexTTS2 startup deadlocked")
            thread.join(2)

        self.assertEqual(failures, [])
        status = coordinator.status()
        self.assertEqual(status["loaded_model_keys"], ["indextts2_sidecar"])
        resident = status["residents"][0]
        self.assertEqual(resident["slot_id"], "responsive:indextts2")
        self.assertEqual(resident["revision"], client._identity()["revision"])
        coordinator.forget_resident(
            "responsive:indextts2",
            reason="fixture_cleanup",
        )


if __name__ == "__main__":
    unittest.main()
