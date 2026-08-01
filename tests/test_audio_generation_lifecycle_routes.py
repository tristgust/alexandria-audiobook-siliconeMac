from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

import app as app_module
from project import ProjectManager
from tts import TTSEngine


class ManifestEngine:
    mode = "local"
    _use_mlx = False


class AudioGenerationLifecycleRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "app").mkdir()
        (self.root / "app" / "config.json").write_text(
            json.dumps({"tts": {"mode": "local", "language": "English"}}),
            encoding="utf-8",
        )
        self.voice_config_path = self.root / "voice_config.json"
        self.voice_config_path.write_text(
            json.dumps({"NARRATOR": {"type": "custom", "voice": "Ryan"}}),
            encoding="utf-8",
        )
        (self.root / "chunks.json").write_text(
            json.dumps(
                [
                    {
                        "id": 0,
                        "speaker": "NARRATOR",
                        "text": "A stable lifecycle fixture line.",
                        "instruct": "Calm.",
                        "status": "pending",
                        "audio_path": None,
                    }
                ]
            ),
            encoding="utf-8",
        )
        self.manager = ProjectManager(str(self.root))
        self.manager.engine = ManifestEngine()
        self.original_audio_state = copy.deepcopy(app_module.process_state["audio"])
        app_module.process_state["audio"].update(
            {
                "running": False,
                "logs": [],
                "progress": {"completed": 0, "failed": 0, "total": 0},
                "cancel": False,
                "request_id": None,
                "request_fingerprint": None,
                "owner_token": None,
                "replacement_request_id": None,
            }
        )
        self.patches = [
            patch.object(app_module, "ROOT_DIR", str(self.root)),
            patch.object(
                app_module,
                "VOICE_CONFIG_PATH",
                str(self.voice_config_path),
            ),
            patch.object(app_module, "project_manager", self.manager),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        app_module.process_state["audio"].clear()
        app_module.process_state["audio"].update(self.original_audio_state)
        self.temporary.cleanup()

    def post_parallel(self, *, seed: int, replace_active: bool = False):
        return self.client.post(
            "/api/generate_batch",
            json={
                "indices": [0],
                "worker_count": 1,
                "generation_seed": seed,
                "replace_active": replace_active,
            },
        )

    def test_parallel_route_returns_persistent_request_and_suppresses_duplicate_dispatch(self) -> None:
        with patch.object(app_module, "_run_audio_request_controller") as controller:
            first = self.post_parallel(seed=11)
            second = self.post_parallel(seed=11)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        first_payload = first.json()
        second_payload = second.json()
        self.assertTrue(first_payload["dispatched"])
        self.assertFalse(second_payload["dispatched"])
        self.assertEqual(
            first_payload["request"]["request_id"],
            second_payload["request"]["request_id"],
        )
        controller.assert_called_once_with(
            first_payload["request"]["request_id"]
        )

    def test_replacement_is_queued_and_marks_predecessor_cancelling(self) -> None:
        with patch.object(app_module, "_run_audio_request_controller") as controller:
            first = self.post_parallel(seed=11)
            first_request = first.json()["request"]
            app_module.claim_audio_generation_request(
                str(self.root),
                first_request["request_id"],
                expected_request_fingerprint=first_request[
                    "request_fingerprint"
                ],
            )
            replacement = self.post_parallel(seed=12, replace_active=True)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(replacement.status_code, 200, replacement.text)
        replacement_request = replacement.json()["request"]
        self.assertEqual(replacement_request["state"], "queued_replacement")
        self.assertFalse(replacement.json()["dispatched"])
        predecessor = self.client.get(
            f"/api/audio-generation/requests/{first_request['request_id']}"
        )
        self.assertEqual(predecessor.status_code, 200, predecessor.text)
        self.assertEqual(predecessor.json()["state"], "cancelling")
        self.assertEqual(
            predecessor.json()["replacement_request_id"],
            replacement_request["request_id"],
        )
        controller.assert_called_once()

    def test_replacement_dispatches_when_predecessor_was_never_claimed(self) -> None:
        with patch.object(app_module, "_run_audio_request_controller") as controller:
            first = self.post_parallel(seed=21)
            replacement = self.post_parallel(seed=22, replace_active=True)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(replacement.status_code, 200, replacement.text)
        first_request = first.json()["request"]
        replacement_request = replacement.json()["request"]
        predecessor = self.client.get(
            f"/api/audio-generation/requests/{first_request['request_id']}"
        ).json()
        self.assertEqual(predecessor["state"], "replaced")
        self.assertIsNotNone(predecessor["terminal_receipt_fingerprint"])
        self.assertEqual(replacement_request["state"], "prepared")
        self.assertTrue(replacement.json()["dispatched"])
        self.assertEqual(controller.call_count, 2)

    def test_cancel_route_updates_persistent_request_and_status_route_reads_it(self) -> None:
        with patch.object(app_module, "_run_audio_request_controller"):
            started = self.post_parallel(seed=13)
        request_id = started.json()["request"]["request_id"]
        cancelled = self.client.post("/api/cancel_generation")
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["request"]["request_id"], request_id)
        self.assertIn(
            cancelled.json()["request"]["state"],
            {"cancelling", "cancelled"},
        )
        status = self.client.get(
            f"/api/audio-generation/requests/{request_id}"
        )
        self.assertEqual(status.status_code, 200, status.text)
        self.assertTrue(status.json()["cancel_requested"])

    def test_request_inventory_survives_process_state_reset(self) -> None:
        with patch.object(app_module, "_run_audio_request_controller"):
            started = self.post_parallel(seed=14)
        request_id = started.json()["request"]["request_id"]
        app_module.process_state["audio"].update(
            {
                "running": False,
                "request_id": None,
                "owner_token": None,
                "cancel": False,
            }
        )
        inventory = self.client.get("/api/audio-generation/requests")
        self.assertEqual(inventory.status_code, 200, inventory.text)
        ids = [item["request_id"] for item in inventory.json()["requests"]]
        self.assertIn(request_id, ids)

    def test_fast_route_returns_same_lifecycle_contract(self) -> None:
        with patch.object(app_module, "_run_audio_request_controller") as controller:
            response = self.client.post(
                "/api/generate_fast_batch",
                json={
                    "indices": [0],
                    "batch_size": 1,
                    "group_by_type": False,
                    "generation_seed": 15,
                    "replace_active": False,
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["request"]["mode"], "fast")
        self.assertTrue(payload["dispatched"])
        controller.assert_called_once_with(payload["request"]["request_id"])

    def test_disconnect_before_acceptance_cancels_without_dispatch(self) -> None:
        with (
            patch(
                "starlette.requests.Request.is_disconnected",
                new=AsyncMock(return_value=True),
            ),
            patch.object(app_module, "_run_audio_request_controller") as controller,
        ):
            response = self.post_parallel(seed=16)
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "cancelled")
        self.assertTrue(payload["client_disconnected"])
        self.assertFalse(payload["dispatched"])
        self.assertEqual(payload["request"]["state"], "cancelled")
        self.assertEqual(
            payload["request"]["terminal_reason"],
            "client_disconnected_before_acceptance",
        )
        controller.assert_not_called()

    def test_controller_publishes_one_terminal_success_receipt(self) -> None:
        engine = TTSEngine({"tts": {"mode": "local"}})
        self.manager.engine = engine

        def generate(segment_text, _instruct, _speaker, _config, output_path, **_kwargs):
            sample_rate = 24000
            duration = max(0.8, len(segment_text) * 0.05)
            count = max(1, round(sample_rate * duration))
            timeline = np.arange(count, dtype=np.float32) / sample_rate
            audio = 0.1 * np.sin(2.0 * np.pi * 7.0 * timeline)
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            sf.write(output_path, audio, sample_rate, subtype="FLOAT")
            return True

        with patch.object(
            engine,
            "_generate_voice_unsegmented",
            side_effect=generate,
        ):
            record, dispatch, _prepared = app_module._prepare_audio_queue_request(
                app_module.BatchGenerateRequest(
                    indices=[0],
                    generation_seed=None,
                    operation_mode="missing_stale",
                    worker_count=1,
                ),
                mode="parallel",
                execution={"worker_count": 1},
            )
            self.assertTrue(dispatch)
            app_module._run_audio_request_controller(record["request_id"])

        terminal = app_module.load_audio_generation_request(
            str(self.root),
            record["request_id"],
        )
        self.assertEqual(terminal["state"], "succeeded")
        self.assertEqual(terminal["terminal_summary"]["completed"], 1)
        self.assertIsNotNone(terminal["terminal_receipt_fingerprint"])
        self.assertFalse(app_module.process_state["audio"]["running"])
        chunk = json.loads(
            (self.root / "chunks.json").read_text(encoding="utf-8")
        )[0]
        self.assertEqual(chunk["status"], "done")
        self.assertTrue((self.root / chunk["audio_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
