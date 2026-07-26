from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module


class GenerationSeedRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        app_module.process_state["audio"].update(
            {
                "running": False,
                "cancel": False,
                "logs": [],
            }
        )
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.client.close()
        app_module.process_state["audio"]["running"] = False
        app_module.process_state["audio"]["cancel"] = False

    def test_single_chunk_route_accepts_optional_explicit_seed(self) -> None:
        chunks = [{"id": 0, "text": "Hello.", "speaker": "DOCTOR"}]
        with (
            patch.object(
                app_module.project_manager,
                "load_chunks",
                return_value=chunks,
            ),
            patch.object(
                app_module.project_manager,
                "generate_chunk_audio",
                return_value=(True, "voicelines/test.wav"),
            ) as generate,
        ):
            response = self.client.post(
                "/api/chunks/0/generate",
                json={"generation_seed": 4242},
            )
        self.assertEqual(response.status_code, 200, response.text)
        generate.assert_called_once_with(0, generation_seed=4242)

    def test_single_chunk_route_remains_backward_compatible_without_body(self) -> None:
        chunks = [{"id": 0, "text": "Hello.", "speaker": "DOCTOR"}]
        with (
            patch.object(
                app_module.project_manager,
                "load_chunks",
                return_value=chunks,
            ),
            patch.object(
                app_module.project_manager,
                "generate_chunk_audio",
                return_value=(True, "voicelines/test.wav"),
            ) as generate,
        ):
            response = self.client.post("/api/chunks/0/generate")
        self.assertEqual(response.status_code, 200, response.text)
        generate.assert_called_once_with(0, generation_seed=None)

    def test_parallel_queue_forwards_seed_and_records_operation_state(self) -> None:
        chunks = [
            {"id": 0, "text": "One.", "speaker": "DOCTOR"},
            {"id": 1, "text": "Two.", "speaker": "DOCTOR"},
        ]
        with (
            patch.object(
                app_module.project_manager,
                "load_chunks",
                return_value=chunks,
            ),
            patch.object(
                app_module.project_manager,
                "generate_chunks_parallel",
                return_value={"completed": [0, 1], "failed": [], "cancelled": 0},
            ) as generate,
        ):
            response = self.client.post(
                "/api/generate_batch",
                json={"indices": [0, 1], "generation_seed": 77},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(app_module.process_state["audio"]["generation_seed"], 77)
        args, kwargs = generate.call_args
        self.assertEqual(args[:2], ([0, 1], 2))
        self.assertEqual(kwargs["generation_seed"], 77)

    def test_fast_queue_explicit_seed_overrides_configured_batch_seed(self) -> None:
        chunks = [{"id": 0, "text": "One.", "speaker": "DOCTOR"}]
        with (
            patch.object(
                app_module.project_manager,
                "load_chunks",
                return_value=chunks,
            ),
            patch.object(
                app_module.project_manager,
                "generate_chunks_batch",
                return_value={"completed": [0], "failed": [], "cancelled": 0},
            ) as generate,
            patch.object(app_module.os.path, "exists", return_value=False),
        ):
            response = self.client.post(
                "/api/generate_batch_fast",
                json={"indices": [0], "generation_seed": 88},
            )
        self.assertEqual(response.status_code, 200, response.text)
        args, kwargs = generate.call_args
        self.assertEqual(args[0], [0])
        self.assertEqual(args[1], 88)
        self.assertEqual(response.json()["batch_seed"], 88)

    def test_negative_generation_seed_is_rejected(self) -> None:
        response = self.client.post(
            "/api/generate_batch",
            json={"indices": [0], "generation_seed": -2},
        )
        self.assertEqual(response.status_code, 422, response.text)


if __name__ == "__main__":
    unittest.main()
