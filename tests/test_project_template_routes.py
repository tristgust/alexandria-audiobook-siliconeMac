from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from project_templates import template_catalog_path


class ProjectTemplateRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app_module.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data_root_patch = patch.object(
            app_module,
            "PROJECTS_DATA_ROOT",
            self.root,
        )
        self.data_root_patch.start()

    def tearDown(self) -> None:
        self.data_root_patch.stop()
        self.temporary.cleanup()

    def template_fields(self, **updates) -> dict:
        value = {
            "name": "Swedish production",
            "description": "Reviewed Swedish output.",
            "generation_method": "local",
            "preset": "maximum_fidelity",
            "source_language": "English",
            "output_language": "Swedish",
            "intent": "High-fidelity Swedish production",
        }
        value.update(updates)
        return value

    def inventory(self) -> dict:
        response = self.client.get("/api/templates")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def create(self, **updates) -> dict:
        status = self.inventory()
        response = self.client.post(
            "/api/templates",
            json={
                "expected_catalog_fingerprint": status["catalog_fingerprint"],
                "template": self.template_fields(**updates),
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_get_is_file_pure_and_exposes_only_named_project_intent(self) -> None:
        payload = self.inventory()
        self.assertFalse(template_catalog_path(self.root).exists())
        self.assertEqual(payload["summary"]["built_in_count"], 6)
        self.assertEqual(payload["summary"]["custom_count"], 0)
        rendered = str(payload).casefold()
        for forbidden in (
            "model_name",
            "api_key",
            "prompt_template",
            "context_length_bytes",
            "cache_path",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_create_edit_duplicate_default_and_delete_round_trip(self) -> None:
        created = self.create()
        template = created["template"]
        response = self.client.put(
            f"/api/templates/{template['id']}",
            json={
                "expected_catalog_fingerprint": created["catalog_fingerprint"],
                "expected_template_fingerprint": template["fingerprint"],
                "template": self.template_fields(
                    name="Swedish publication",
                    preset="standard",
                    intent="Balanced Swedish publication",
                ),
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()
        template = updated["template"]
        self.assertEqual(template["name"], "Swedish publication")

        response = self.client.post(
            f"/api/templates/{template['id']}/duplicate",
            json={
                "expected_catalog_fingerprint": updated["catalog_fingerprint"],
                "name": "Swedish publication copy",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        duplicated = response.json()
        copy = duplicated["template"]
        self.assertEqual(duplicated["duplicated_from"], template["id"])

        response = self.client.post(
            f"/api/templates/{copy['id']}/default",
            json={
                "expected_catalog_fingerprint": duplicated["catalog_fingerprint"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        defaulted = response.json()
        self.assertEqual(defaulted["default_template_id"], copy["id"])

        impact = self.client.get(
            f"/api/templates/{template['id']}/delete-impact"
        )
        self.assertEqual(impact.status_code, 200, impact.text)
        impact_value = impact.json()
        response = self.client.request(
            "DELETE",
            f"/api/templates/{template['id']}",
            json={
                "expected_catalog_fingerprint": impact_value["catalog_fingerprint"],
                "expected_template_fingerprint": impact_value["template"]["fingerprint"],
                "confirmation_text": impact_value["confirmation_text"],
                "acknowledge_usage": False,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["deleted_template_id"], template["id"])
        self.assertEqual(response.json()["summary"]["custom_count"], 1)

    def test_builtin_edit_and_delete_fail_closed(self) -> None:
        status = self.inventory()
        builtin = next(
            item
            for item in status["templates"]
            if item["id"] == "builtin_standard"
        )
        response = self.client.put(
            "/api/templates/builtin_standard",
            json={
                "expected_catalog_fingerprint": status["catalog_fingerprint"],
                "expected_template_fingerprint": builtin["fingerprint"],
                "template": self.template_fields(name="Changed Standard"),
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "template_builtin_immutable",
        )
        impact = self.client.get(
            "/api/templates/builtin_standard/delete-impact"
        )
        self.assertEqual(impact.status_code, 409, impact.text)
        self.assertEqual(
            impact.json()["detail"]["code"],
            "template_builtin_immutable",
        )
        self.assertFalse(template_catalog_path(self.root).exists())

    def test_stale_catalog_and_invalid_fields_are_machine_readable(self) -> None:
        before = self.inventory()
        created = self.create()
        response = self.client.post(
            "/api/templates",
            json={
                "expected_catalog_fingerprint": before["catalog_fingerprint"],
                "template": self.template_fields(name="Stale template"),
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "template_catalog_conflict",
        )
        response = self.client.post(
            "/api/templates",
            json={
                "expected_catalog_fingerprint": created["catalog_fingerprint"],
                "template": self.template_fields(
                    name="Bad import",
                    generation_method="import_existing_script",
                    preset="maximum_fidelity",
                ),
            },
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "template_import_preset_invalid",
        )

    def test_template_application_requires_exact_method_preset_and_languages(self) -> None:
        identifier = app_module._validate_project_template_application(
            template_id="builtin_maximum_fidelity",
            generation_method="local",
            preset="maximum_fidelity",
            source_language="English",
            output_language="English",
        )
        self.assertEqual(identifier, "builtin_maximum_fidelity")
        with self.assertRaisesRegex(Exception, "no longer match") as mismatch:
            app_module._validate_project_template_application(
                template_id="builtin_maximum_fidelity",
                generation_method="local",
                preset="standard",
                source_language="English",
                output_language="English",
            )
        self.assertEqual(
            mismatch.exception.code,
            "template_application_mismatch",
        )
        self.assertIsNone(
            app_module._validate_project_template_application(
                template_id=None,
                generation_method="local",
                preset="standard",
                source_language="English",
                output_language="German",
            )
        )

    def test_route_surface_has_no_unscoped_bulk_or_hidden_runtime_mutation(self) -> None:
        route_paths = {
            route.path: set(getattr(route, "methods", set()))
            for route in app_module.app.routes
            if str(getattr(route, "path", "")).startswith("/api/templates")
        }
        self.assertEqual(route_paths["/api/templates"], {"POST"})
        get_routes = [
            route
            for route in app_module.app.routes
            if getattr(route, "path", None) == "/api/templates"
            and "GET" in getattr(route, "methods", set())
        ]
        self.assertEqual(len(get_routes), 1)
        self.assertNotIn("/api/templates/bulk", route_paths)
        self.assertNotIn("/api/templates/runtime", route_paths)


if __name__ == "__main__":
    unittest.main()
