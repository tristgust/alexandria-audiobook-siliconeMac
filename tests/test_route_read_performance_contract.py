from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as app_module


class RouteReadPerformanceContractTests(unittest.TestCase):
    def test_heavy_read_routes_use_fastapi_worker_pool(self) -> None:
        routes = {
            route.path: route.endpoint
            for route in app_module.app.routes
            if (
                getattr(route, "path", None)
                and hasattr(route, "endpoint")
                and "GET" in (getattr(route, "methods", None) or set())
            )
        }
        for path in (
            "/api/projects",
            "/api/project_flow/status",
            "/api/cast",
            "/api/produce",
            "/api/export",
            "/api/library",
            "/api/voice-library",
        ):
            with self.subTest(path=path):
                endpoint = routes[path]
                self.assertFalse(inspect.iscoroutinefunction(endpoint))

        for path in (
            "/api/projects",
            "/api/project_flow/status",
            "/api/cast",
            "/api/produce",
            "/api/export",
            "/api/library",
        ):
            with self.subTest(runtime_lock=path):
                self.assertIn("_read_runtime_project", inspect.getsource(routes[path]))

    def test_voice_library_releases_runtime_lock_before_scanning(self) -> None:
        source = inspect.getsource(app_module.get_voice_library)
        lock_end = source.index("return build_voice_library")
        self.assertIn("with _RUNTIME_PROJECT_LOCK", source[:lock_end])
        self.assertNotIn("_read_runtime_project", source)

    def test_runtime_read_helper_serializes_project_binding_access(self) -> None:
        source = inspect.getsource(app_module._read_runtime_project)
        self.assertIn("with _RUNTIME_PROJECT_LOCK", source)
        self.assertIn("return reader(*args, **kwargs)", source)

    def test_produce_snapshot_reuses_unchanged_inputs_and_detects_audio_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            config.write_text("{}", encoding="utf-8")
            (root / "chunks.json").write_text("[]", encoding="utf-8")
            (root / "voice_config.json").write_text("{}", encoding="utf-8")
            voicelines = root / "voicelines"
            voicelines.mkdir()
            audio = voicelines / "line.mp3"
            audio.write_bytes(b"first")
            aggregate = {"schema_version": 1, "chunks": [], "summary": {}}
            app_module._clear_produce_aggregate_cache()
            try:
                with (
                    patch.object(app_module, "ROOT_DIR", str(root)),
                    patch.object(app_module, "CONFIG_PATH", str(config)),
                    patch.object(
                        app_module,
                        "_current_process_status",
                        return_value={"running": False, "status": "idle"},
                    ),
                    patch.object(
                        app_module,
                        "inspect_produce_project",
                        return_value=aggregate,
                    ) as inspect_produce,
                ):
                    self.assertIs(app_module._current_produce_status(), aggregate)
                    self.assertIs(app_module._current_produce_status(), aggregate)
                    self.assertEqual(inspect_produce.call_count, 1)
                    audio.write_bytes(b"changed-audio")
                    self.assertIs(app_module._current_produce_status(), aggregate)
                    self.assertEqual(inspect_produce.call_count, 2)
            finally:
                app_module._clear_produce_aggregate_cache()

    def test_large_json_response_preserves_payload_without_recursive_reencoding(self) -> None:
        response = app_module._json_payload_response(
            {"title": "Human Nature", "chunks": [{"id": 1, "text": "Hello"}]}
        )
        self.assertEqual(response.media_type, "application/json")
        self.assertEqual(json.loads(response.body), {
            "title": "Human Nature",
            "chunks": [{"id": 1, "text": "Hello"}],
        })
        self.assertNotIn(b"\n", response.body)


if __name__ == "__main__":
    unittest.main()
