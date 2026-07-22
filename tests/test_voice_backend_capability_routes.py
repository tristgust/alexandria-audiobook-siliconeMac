from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import get_args, get_origin
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module


class VoiceBackendCapabilityRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        results = self.root / "benchmarks" / "results"
        results.mkdir(parents=True)
        (results / "20260717T014952Z_phase22_apple_silicon.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "stable_lora_outcome": "unsupported",
                    "tts_measurements": {
                        "voice_design": {"warm_rtf": 0.34},
                        "custom_voice": {"warm_rtf": 0.31},
                    },
                }
            ),
            encoding="utf-8",
        )
        self.models = self.root / "lora_models"
        self.models.mkdir()
        model_dir = self.models / "existing_adapter"
        model_dir.mkdir()
        (model_dir / "training_meta.json").write_text(
            json.dumps({"model_id": "existing_adapter"}),
            encoding="utf-8",
        )
        manifest_path = self.models / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                [
                    {
                        "id": "existing_adapter",
                        "name": "Existing adapter",
                        "dataset_id": "fixture_dataset",
                    }
                ]
            ),
            encoding="utf-8",
        )
        self.patches = [
            patch.object(app_module, "ROOT_DIR", str(self.root)),
            patch.object(app_module, "LORA_MODELS_DIR", str(self.models)),
            patch.object(
                app_module,
                "LORA_MODELS_MANIFEST",
                str(manifest_path),
            ),
            patch.object(
                app_module,
                "_load_builtin_lora_manifest",
                return_value=[],
            ),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    @staticmethod
    def _sample_value(name: str, annotation):
        origin = get_origin(annotation)
        args = get_args(annotation)
        if origin is not None and args:
            if origin is list:
                return []
            non_none = [item for item in args if item is not type(None)]
            if non_none:
                if origin.__name__ == "Literal":
                    return non_none[0]
                return VoiceBackendCapabilityRouteTests._sample_value(
                    name,
                    non_none[0],
                )
        if annotation is str:
            values = {
                "dataset_id": "fixture_dataset",
                "model_id": "existing_adapter",
                "text": "Tell me what happened.",
                "instruct": "Measured curiosity.",
                "device": "cpu",
            }
            return values.get(name, "fixture")
        if annotation is int:
            return 1
        if annotation is float:
            return 0.001
        if annotation is bool:
            return False
        return "fixture"

    @classmethod
    def _payload(cls, model_class):
        values = {}
        for name, field in model_class.model_fields.items():
            if field.is_required():
                values[name] = cls._sample_value(name, field.annotation)
        model = model_class(**values)
        return model.model_dump()

    def test_capability_route_is_model_free_and_reports_unsupported(self) -> None:
        before = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))
        response = self.client.get("/api/voice_backend/capabilities")
        after = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["stable_lora_outcome"], "unsupported")
        self.assertFalse(payload["lora_training_supported"])
        self.assertFalse(payload["lora_inference_supported"])
        self.assertFalse(payload["training_action_enabled"])
        self.assertEqual(
            payload["measured_inference"]["voice_design"]["warm_rtf"],
            0.34,
        )
        self.assertEqual(before, after)

    def test_model_list_preserves_list_shape_and_adds_capability_fields(self) -> None:
        response = self.client.get("/api/lora/models")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIsInstance(payload, list)
        self.assertEqual(len(payload), 1)
        model = payload[0]
        self.assertEqual(model["id"], "existing_adapter")
        self.assertFalse(model["training_supported"])
        self.assertFalse(model["inference_supported"])
        self.assertIn("unsupported", model["capability_reason"].casefold())

    def test_training_route_fails_before_process_or_directory_work(self) -> None:
        payload = self._payload(app_module.LoraTrainingRequest)
        with patch("app.subprocess.Popen") as popen:
            response = self.client.post("/api/lora/train", json=payload)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "lora_sidecar_unavailable",
        )
        popen.assert_not_called()

    def test_download_test_and_preview_fail_before_backend_actions(self) -> None:
        with patch("app.download_builtin_adapter") as download:
            response = self.client.post("/api/lora/download/builtin_fixture")
        self.assertEqual(response.status_code, 409, response.text)
        download.assert_not_called()

        test_payload = self._payload(app_module.LoraTestRequest)
        with patch.object(
            app_module.project_manager,
            "get_engine",
        ) as engine:
            response = self.client.post("/api/lora/test", json=test_payload)
        self.assertEqual(response.status_code, 409, response.text)
        engine.assert_not_called()

        with patch.object(
            app_module.project_manager,
            "get_engine",
        ) as engine:
            response = self.client.post(
                "/api/lora/preview/existing_adapter"
            )
        self.assertEqual(response.status_code, 409, response.text)
        engine.assert_not_called()

    def test_dataset_routes_remain_available_independently(self) -> None:
        response = self.client.get("/api/lora/datasets")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsInstance(response.json(), list)

    def test_explicit_adapter_delete_remains_available(self) -> None:
        response = self.client.delete("/api/lora/models/existing_adapter")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse((self.models / "existing_adapter").exists())

    def test_capability_and_lora_routes_are_registered_once(self) -> None:
        registrations = [
            (route.path, frozenset(getattr(route, "methods", set())))
            for route in app_module.app.routes
        ]
        expected = (
            ("/api/voice_backend/capabilities", "GET"),
            ("/api/lora/train", "POST"),
            ("/api/lora/models", "GET"),
            ("/api/lora/download/{adapter_id}", "POST"),
            ("/api/lora/test", "POST"),
            ("/api/lora/preview/{adapter_id}", "POST"),
        )
        for path, method in expected:
            self.assertEqual(
                sum(
                    route_path == path and method in methods
                    for route_path, methods in registrations
                ),
                1,
            )


if __name__ == "__main__":
    unittest.main()
