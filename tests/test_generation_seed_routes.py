from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from approved_audio import approved_audio_lock_fields
from project import ProjectManager


class _ManifestEngine:
    mode = "local"
    _use_mlx = False


class GenerationSeedRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "app").mkdir()
        self.config_path = self.root / "app" / "config.json"
        self.config_path.write_text(
            json.dumps(
                {
                    "tts": {
                        "mode": "local",
                        "language": "English",
                        "parallel_workers": 2,
                    }
                }
            ),
            encoding="utf-8",
        )
        (self.root / "voice_config.json").write_text(
            json.dumps(
                {
                    "DOCTOR": {
                        "type": "custom",
                        "voice": "Ryan",
                    }
                }
            ),
            encoding="utf-8",
        )
        (self.root / "chunks.json").write_text(
            json.dumps(
                [
                    {"id": 0, "text": "One.", "speaker": "DOCTOR"},
                    {"id": 1, "text": "Two.", "speaker": "DOCTOR"},
                ]
            ),
            encoding="utf-8",
        )
        self.manager = ProjectManager(str(self.root))
        self.manager.engine = _ManifestEngine()
        self.patchers = [
            patch.object(app_module, "ROOT_DIR", str(self.root)),
            patch.object(app_module, "CONFIG_PATH", str(self.config_path)),
            patch.object(app_module, "project_manager", self.manager),
        ]
        for item in self.patchers:
            item.start()
        app_module.process_state["audio"].update(
            {
                "running": False,
                "cancel": False,
                "logs": [],
                "request_id": None,
                "owner_token": None,
            }
        )
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.client.close()
        for item in reversed(self.patchers):
            item.stop()
        app_module.process_state["audio"]["running"] = False
        app_module.process_state["audio"]["cancel"] = False
        self.temporary.cleanup()

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

    @staticmethod
    def _locked_chunk() -> dict:
        chunk = {
            "id": 9,
            "text": "Approved adaptation line.",
            "speaker": "DOCTOR",
            "instruct": "Firmly.",
            "status": "done",
        }
        chunk.update(
            approved_audio_lock_fields(
                chunk=chunk,
                promotion_id="promotion-v2",
                candidate_id="candidate-9",
                source_round_id="round-v9",
                direct_placement_tier="strict_clean",
                source_audio_path="/tmp/candidate.wav",
                source_audio_sha256="a" * 64,
                manifest_path="approved_audio_promotion_history/manifest.json",
                installed_at_utc="2026-07-31T12:00:00Z",
                reference_bank_eligible=False,
            )
        )
        return chunk

    def test_single_chunk_route_rejects_approved_audio_regeneration(self) -> None:
        with patch.object(
            app_module.project_manager,
            "load_chunks",
            return_value=[self._locked_chunk()],
        ):
            response = self.client.post("/api/chunks/0/generate")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "approved_audio_regeneration_locked",
        )

    def test_batch_routes_reject_approved_audio_indices(self) -> None:
        chunk = self._locked_chunk()
        with patch.object(
            app_module.project_manager,
            "load_chunks",
            return_value=[chunk],
        ):
            parallel = self.client.post(
                "/api/generate_batch",
                json={"indices": [0]},
            )
            fast = self.client.post(
                "/api/generate_batch_fast",
                json={"indices": [0]},
            )
        self.assertEqual(parallel.status_code, 409, parallel.text)
        self.assertEqual(fast.status_code, 409, fast.text)
        self.assertEqual(
            parallel.json()["detail"]["code"],
            "approved_audio_batch_contains_locked_chunks",
        )


if __name__ == "__main__":
    unittest.main()
