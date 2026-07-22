from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from more_tools import MoreToolsError


class MoreToolsRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app_module.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

    def payload(self) -> dict:
        return {
            "schema_version": 1,
            "context": {
                "project_id": "project_1",
                "character_id": "character_2",
                "source": "cast:character:character_2",
                "return_route": "#/cast?project=project_1",
                "label": "Selected character",
            },
            "summary": {
                "tool_count": 8,
                "read_only_count": 1,
                "guarded_count": 3,
                "experimental_count": 1,
            },
            "categories": [],
            "tools": [],
            "landing_mutation_supported": False,
            "fingerprint": "a" * 64,
        }

    def test_route_is_get_only(self) -> None:
        registrations = [
            route
            for route in app_module.app.routes
            if getattr(route, "path", None) == "/api/more"
        ]
        self.assertEqual(len(registrations), 1)
        self.assertEqual(registrations[0].methods, {"GET"})
        for method in ("post", "put", "patch", "delete"):
            response = getattr(self.client, method)("/api/more")
            self.assertEqual(response.status_code, 405, response.text)

    def test_route_preserves_explicit_context(self) -> None:
        expected = self.payload()
        with patch.object(
            app_module,
            "inspect_more_tools",
            return_value=expected,
        ) as inspector:
            response = self.client.get(
                "/api/more",
                params={
                    "project_id": "project_1",
                    "character_id": "character_2",
                    "source": "cast:character:character_2",
                    "return_route": "#/cast?project=project_1",
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), expected)
        inspector.assert_called_once_with(
            project_id="project_1",
            character_id="character_2",
            source="cast:character:character_2",
            return_route="#/cast?project=project_1",
        )

    def test_route_uses_active_project_when_not_supplied(self) -> None:
        expected = self.payload()
        with (
            patch.object(app_module, "ACTIVE_PROJECT_ID", "active_project"),
            patch.object(
                app_module,
                "inspect_more_tools",
                return_value=expected,
            ) as inspector,
        ):
            response = self.client.get("/api/more")
        self.assertEqual(response.status_code, 200, response.text)
        inspector.assert_called_once_with(
            project_id="active_project",
            character_id=None,
            source=None,
            return_route="#/more",
        )

    def test_error_is_machine_readable(self) -> None:
        with patch.object(
            app_module,
            "inspect_more_tools",
            side_effect=MoreToolsError(
                "more_context_invalid",
                "return_route contains unsupported characters or is too long.",
                context={"field": "return_route"},
            ),
        ):
            response = self.client.get("/api/more")
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            response.json()["detail"],
            {
                "code": "more_context_invalid",
                "message": "return_route contains unsupported characters or is too long.",
                "context": {"field": "return_route"},
            },
        )

    def test_route_source_contains_no_write_side_effect(self) -> None:
        source = open(app_module.__file__, encoding="utf-8").read()
        start = source.index('@app.get("/api/more")')
        end = source.index('def _settings_with_template_default', start)
        route_source = source[start:end].casefold()
        for forbidden in (
            "write_text",
            "atomic_json",
            "voice_config",
            "script_path",
            "delete",
            "update_",
        ):
            self.assertNotIn(forbidden, route_source)


if __name__ == "__main__":
    unittest.main()
