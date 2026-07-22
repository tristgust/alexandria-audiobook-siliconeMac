from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from voice_library import VoiceLibraryError


class VoiceLibraryRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app_module.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

    def payload(self) -> dict:
        return {
            "schema_version": 1,
            "project_id": "project_1",
            "summary": {
                "voice_count": 2,
                "assigned_voice_count": 1,
                "assignment_count": 1,
                "invalid_voice_count": 0,
                "method_counts": {
                    "built_in": 1,
                    "designed": 0,
                    "supplied_recording": 1,
                    "instruction_controlled": 0,
                    "adapter": 0,
                    "alias": 0,
                },
                "cast_character_count": 1,
                "cast_blocker_count": 0,
            },
            "methods": [],
            "filters": {
                "methods": ["built_in", "supplied_recording"],
                "states": ["available"],
            },
            "voices": [],
            "assignment_mutation_supported": False,
            "cast_is_authoritative": True,
            "fingerprint": "a" * 64,
        }

    def test_route_is_registered_once_and_only_for_get(self) -> None:
        matching = [
            route
            for route in app_module.app.routes
            if getattr(route, "path", None) == "/api/voice-library"
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].methods, {"GET"})
        for method in ("post", "put", "patch", "delete"):
            response = getattr(self.client, method)("/api/voice-library")
            self.assertEqual(response.status_code, 405, response.text)

    def test_route_preserves_project_and_return_context(self) -> None:
        expected = self.payload()
        with patch.object(
            app_module,
            "build_voice_library",
            return_value=expected,
        ) as builder:
            response = self.client.get(
                "/api/voice-library",
                params={
                    "project_id": "project_1",
                    "return_route": "#/voices?method=supplied_recording",
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), expected)
        builder.assert_called_once_with(
            root_dir=app_module.ROOT_DIR,
            project_id="project_1",
            return_route="#/voices?method=supplied_recording",
        )

    def test_route_uses_active_project_identity_when_not_supplied(self) -> None:
        expected = self.payload()
        with (
            patch.object(app_module, "ACTIVE_PROJECT_ID", "active_project"),
            patch.object(
                app_module,
                "build_voice_library",
                return_value=expected,
            ) as builder,
        ):
            response = self.client.get("/api/voice-library")
        self.assertEqual(response.status_code, 200, response.text)
        builder.assert_called_once_with(
            root_dir=app_module.ROOT_DIR,
            project_id="active_project",
            return_route="#/voices",
        )

    def test_voice_library_error_remains_machine_readable(self) -> None:
        with patch.object(
            app_module,
            "build_voice_library",
            side_effect=VoiceLibraryError(
                "voice_library_config_invalid",
                "Voice configuration is invalid.",
            ),
        ):
            response = self.client.get("/api/voice-library")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"],
            {
                "code": "voice_library_config_invalid",
                "message": "Voice configuration is invalid.",
            },
        )

    def test_route_contract_cannot_assign_a_voice(self) -> None:
        source = app_module.__file__
        self.assertIsNotNone(source)
        text = open(source, encoding="utf-8").read()
        route_start = text.index('@app.get("/api/voice-library")')
        route_end = text.index('@app.get("/api/library")', route_start)
        route_source = text[route_start:route_end]
        self.assertNotIn("save_voice", route_source)
        self.assertNotIn("write_text", route_source)
        self.assertNotIn("voice_config", route_source)
        self.assertNotIn("assign", route_source.casefold())


if __name__ == "__main__":
    unittest.main()
