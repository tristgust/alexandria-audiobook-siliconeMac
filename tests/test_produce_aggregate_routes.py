from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app as app_module
from audio_artifacts import audio_binding_fingerprint
from project import ProjectManager


class ProduceAggregateRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "app").mkdir()
        self.config_path = self.root / "app" / "config.json"
        self.config = {
            "tts": {"language": "English", "parallel_workers": 2}
        }
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        self.voice_config = {
            "NARRATOR": {"type": "custom", "voice": "Ryan"}
        }
        (self.root / "voice_config.json").write_text(
            json.dumps(self.voice_config), encoding="utf-8"
        )
        self.chunks = [
            self._chunk(0),
            self._chunk(1, stale=True),
            self._chunk(2, failed=True),
        ]
        self._write_chunks()
        self._write_cast_files()
        self.manager = ProjectManager(str(self.root))
        self.original_audio_state = copy.deepcopy(
            app_module.process_state["audio"]
        )
        app_module.process_state["audio"] = {
            "running": False,
            "logs": [],
            "cancel": False,
            "operation_id": None,
            "mode": None,
            "plan_fingerprint": None,
            "chunks_fingerprint": None,
            "queued_chunk_ids": [],
            "total_count": 0,
            "completed_count": 0,
            "failed_count": 0,
            "cancelled_count": 0,
            "worker_limit": None,
            "started_at": None,
            "finished_at": None,
            "last_error": None,
        }
        self.patchers = [
            patch.object(app_module, "ROOT_DIR", str(self.root)),
            patch.object(app_module, "CONFIG_PATH", str(self.config_path)),
            patch.object(app_module, "CHUNKS_PATH", str(self.root / "chunks.json")),
            patch.object(
                app_module,
                "VOICE_CONFIG_PATH",
                str(self.root / "voice_config.json"),
            ),
            patch.object(app_module, "project_manager", self.manager),
            patch.object(
                app_module,
                "STAGE_LOG_SPECS",
                {
                    **app_module.STAGE_LOG_SPECS,
                    "audio": (
                        "audio",
                        str(self.root / "logs" / "stages" / "audio.json"),
                    ),
                },
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        app_module.process_state["audio"] = self.original_audio_state
        self.temporary.cleanup()

    @staticmethod
    def _chunk(
        index: int,
        *,
        stale: bool = False,
        failed: bool = False,
    ) -> dict:
        value = {
            "id": index,
            "speaker": "NARRATOR",
            "text": f"Line {index}.",
            "instruct": "Calm and clear.",
            "status": "pending",
            "audio_path": None,
        }
        if stale:
            value["audio_state"] = "stale"
            value["stale_audio_path"] = "voicelines/old.mp3"
        if failed:
            value["status"] = "error"
            value["audio_state"] = "failed"
        return value

    def _write_chunks(self) -> None:
        (self.root / "chunks.json").write_text(
            json.dumps(self.chunks), encoding="utf-8"
        )

    def _read_chunks(self) -> list[dict]:
        return json.loads(
            (self.root / "chunks.json").read_text(encoding="utf-8")
        )

    def _write_cast_files(self) -> None:
        (self.root / "annotated_script.json").write_text(
            json.dumps(self.chunks), encoding="utf-8"
        )
        (self.root / "character_roster.json").write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "id": "character_narrator",
                            "canonical_name": "Narrator",
                            "display_name": "Narrator",
                            "speaking_status": "narrator",
                            "resolution_status": "resolved",
                            "aliases": ["NARRATOR"],
                            "titles": [],
                            "nicknames": [],
                            "sample_lines": ["Line 0."],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    def _protected_hashes(self) -> dict[str, str]:
        result = {}
        for name in (
            "chunks.json",
            "voice_config.json",
            "character_roster.json",
            "annotated_script.json",
        ):
            path = self.root / name
            result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    def _plan(self, mode: str = "missing_stale") -> dict:
        response = self.client.post(
            "/api/produce/plan",
            json={"mode": mode, "selected_chunk_ids": []},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_routes_are_registered_once(self) -> None:
        paths = [route.path for route in app_module.app.routes]
        for path in (
            "/api/produce",
            "/api/produce/chunks/{chunk_id}",
            "/api/produce/plan",
            "/api/produce/invalidate-selected",
            "/api/produce/rebind-selected",
            "/api/produce/generate",
            "/api/produce/retry-failed",
            "/api/produce/cancel",
        ):
            self.assertEqual(paths.count(path), 1)

    def test_status_and_plan_are_read_only_model_free_and_exact(self) -> None:
        before = self._protected_hashes()
        with (
            patch.object(
                self.manager,
                "get_engine",
                side_effect=AssertionError("status must not load TTS"),
            ),
            patch.object(
                app_module,
                "download_or_repair_model",
                side_effect=AssertionError("status must not download models"),
            ),
            patch.object(
                app_module,
                "build_runtime_client",
                side_effect=AssertionError("status must not connect to LLM"),
            ),
        ):
            status_response = self.client.get("/api/produce")
            plan = self._plan()
        self.assertEqual(status_response.status_code, 200, status_response.text)
        status = status_response.json()
        self.assertEqual(status["summary"]["needs_generation_count"], 2)
        self.assertEqual(status["summary"]["failed_count"], 1)
        self.assertEqual(plan["indices"], [0, 1])
        self.assertEqual(plan["preserved_current_count"], 0)
        self.assertEqual(before, self._protected_hashes())

    def test_selected_invalidation_requires_current_fingerprint(self) -> None:
        status = self.client.get("/api/produce").json()
        chunks_fingerprint = status["fingerprints"]["chunks"]
        before = self._read_chunks()

        stale = self.client.post(
            "/api/produce/invalidate-selected",
            json={
                "selected_chunk_ids": ["chunk:1"],
                "chunks_fingerprint": "0" * 64,
                "reason": "reference boundary defect",
            },
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(before, self._read_chunks())

        response = self.client.post(
            "/api/produce/invalidate-selected",
            json={
                "selected_chunk_ids": ["chunk:1"],
                "chunks_fingerprint": chunks_fingerprint,
                "reason": "reference boundary defect",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["invalidated_count"], 1)
        self.assertEqual(payload["chunk_ids"], ["chunk:1"])
        updated = self._read_chunks()
        self.assertEqual(updated[0], before[0])
        self.assertEqual(updated[1]["status"], "pending")
        self.assertEqual(updated[1]["audio_state"], "stale")
        self.assertEqual(
            updated[1]["audio_invalidation_reason"],
            "reference boundary defect",
        )

    def test_status_supports_bounded_pagination_without_changing_counts(self) -> None:
        response = self.client.get("/api/produce?offset=1&limit=2")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(len(payload["chunks"]), 2)
        self.assertEqual(payload["returned_chunk_count"], 2)
        self.assertEqual(payload["page"]["offset"], 1)
        self.assertEqual(payload["page"]["limit"], 2)
        self.assertGreaterEqual(payload["page"]["filtered_chunk_count"], 3)
        self.assertEqual(payload["summary"]["needs_generation_count"], 2)
        self.assertEqual(payload["summary"]["failed_count"], 1)

    def test_chunk_deep_link_restores_exact_selected_inspector(self) -> None:
        response = self.client.get("/api/produce/chunks/1")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["chunk_id"], "chunk:1")
        self.assertEqual(response.json()["state"], "stale")
        missing = self.client.get("/api/produce/chunks/missing")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(
            missing.json()["detail"]["code"],
            "produce_chunk_not_found",
        )

    def test_execute_dispatches_only_planned_indices(self) -> None:
        plan = self._plan()
        with patch.object(
            app_module,
            "generate_batch_endpoint",
            new=AsyncMock(return_value={"status": "started", "operation_id": "audio_test"}),
        ) as generate:
            response = self.client.post(
                "/api/produce/generate",
                json={
                    "mode": "missing_stale",
                    "selected_chunk_ids": [],
                    "plan_fingerprint": plan["plan_fingerprint"],
                    "chunks_fingerprint": plan["chunks_fingerprint"],
                    "confirm_regenerate_all": False,
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        request = generate.await_args.args[0]
        self.assertEqual(request.indices, [0, 1])
        self.assertEqual(request.operation_mode, "missing_stale")
        self.assertEqual(
            request.plan_fingerprint,
            plan["plan_fingerprint"],
        )

    def test_ready_only_route_dispatches_only_ready_indices(self) -> None:
        plan = self._plan("ready_only")
        with patch.object(
            app_module,
            "generate_batch_endpoint",
            new=AsyncMock(return_value={"status": "started", "operation_id": "audio_ready"}),
        ) as generate:
            response = self.client.post(
                "/api/produce/generate",
                json={
                    "mode": "ready_only",
                    "selected_chunk_ids": [],
                    "plan_fingerprint": plan["plan_fingerprint"],
                    "chunks_fingerprint": plan["chunks_fingerprint"],
                    "confirm_regenerate_all": False,
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        request = generate.await_args.args[0]
        self.assertEqual(request.indices, [0])
        self.assertEqual(request.operation_mode, "ready_only")

    def test_stale_plan_and_destructive_confirmation_fail_closed(self) -> None:
        plan = self._plan()
        stale = self.client.post(
            "/api/produce/generate",
            json={
                "mode": "missing_stale",
                "selected_chunk_ids": [],
                "plan_fingerprint": "stale",
                "chunks_fingerprint": plan["chunks_fingerprint"],
            },
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(
            stale.json()["detail"]["code"],
            "produce_plan_stale",
        )
        regenerate = self._plan("regenerate_all")
        unconfirmed = self.client.post(
            "/api/produce/generate",
            json={
                "mode": "regenerate_all",
                "selected_chunk_ids": [],
                "plan_fingerprint": regenerate["plan_fingerprint"],
                "chunks_fingerprint": regenerate["chunks_fingerprint"],
                "confirm_regenerate_all": False,
            },
        )
        self.assertEqual(unconfirmed.status_code, 422)
        self.assertEqual(
            unconfirmed.json()["detail"]["code"],
            "produce_regenerate_all_confirmation_required",
        )

    def test_retry_route_requires_retry_plan(self) -> None:
        retry = self._plan("retry_failed")
        wrong = self.client.post(
            "/api/produce/retry-failed",
            json={
                "mode": "missing_stale",
                "selected_chunk_ids": [],
                "plan_fingerprint": retry["plan_fingerprint"],
                "chunks_fingerprint": retry["chunks_fingerprint"],
            },
        )
        self.assertEqual(wrong.status_code, 422)
        self.assertEqual(
            wrong.json()["detail"]["code"],
            "produce_retry_mode_required",
        )
        with patch.object(
            app_module,
            "generate_batch_endpoint",
            new=AsyncMock(return_value={"status": "started"}),
        ) as generate:
            response = self.client.post(
                "/api/produce/retry-failed",
                json={
                    "mode": "retry_failed",
                    "selected_chunk_ids": [],
                    "plan_fingerprint": retry["plan_fingerprint"],
                    "chunks_fingerprint": retry["chunks_fingerprint"],
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(generate.await_args.args[0].indices, [2])

    def test_batch_queue_records_partial_failure_without_second_worker_path(self) -> None:
        def fake_generate(indices, workers, callback, cancel_check=None):
            callback(1, 1, len(indices))
            return {
                "completed": [indices[0]],
                "failed": [(indices[1], "synthetic failure")],
                "cancelled": 0,
            }

        with patch.object(
            self.manager,
            "generate_chunks_parallel",
            side_effect=fake_generate,
        ):
            response = self.client.post(
                "/api/generate_batch",
                json={
                    "indices": [0, 1],
                    "operation_mode": "missing_stale",
                    "plan_fingerprint": "plan",
                    "chunks_fingerprint": "chunks",
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        state = app_module.process_state["audio"]
        self.assertFalse(state["running"])
        self.assertEqual(state["completed_count"], 1)
        self.assertEqual(state["failed_count"], 1)
        self.assertEqual(state["cancelled_count"], 0)
        self.assertEqual(state["mode"], "missing_stale")
        self.assertEqual(state["queued_chunk_ids"], ["chunk:0", "chunk:1"])
        self.assertIsNotNone(state["started_at"])
        self.assertIsNotNone(state["finished_at"])

    def test_cancel_resets_interrupted_chunk_to_pending_or_stale(self) -> None:
        self.chunks[0].update(
            {
                "status": "generating",
                "audio_state": "generating",
                "stale_audio_path": None,
            }
        )
        self.chunks[1].update(
            {
                "status": "generating",
                "audio_state": "stale",
                "stale_audio_path": "voicelines/old.mp3",
            }
        )
        self._write_chunks()
        response = self.client.post("/api/produce/cancel")
        self.assertEqual(response.status_code, 200, response.text)
        chunks = self._read_chunks()
        self.assertEqual(chunks[0]["status"], "pending")
        self.assertEqual(chunks[0]["audio_state"], "pending")
        self.assertEqual(chunks[1]["status"], "pending")
        self.assertEqual(chunks[1]["audio_state"], "stale")
        self.assertEqual(response.json()["result"]["reset_chunks"], 2)

    def test_running_cancel_sets_flag_without_rewriting_chunks(self) -> None:
        before = self._protected_hashes()
        app_module.process_state["audio"]["running"] = True
        response = self.client.post("/api/produce/cancel")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "cancelling")
        self.assertTrue(app_module.process_state["audio"]["cancel"])
        self.assertEqual(before, self._protected_hashes())


if __name__ == "__main__":
    unittest.main()
