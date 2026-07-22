from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module


class ApplicationSettingsRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app_module.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config_path = self.root / "config.json"
        self.config = {
            "llm": {
                "backend": "auto",
                "base_url": "http://localhost:11434/v1",
                "api_key": "route-secret",
                "model_name": "qwen3.5:35b-mlx",
                "context_length": 40960,
                "keep_alive": -1,
                "timeout": 1800,
                "thinking": False,
                "structured_output": True,
                "corrective_retry": True,
                "profiles": {},
            },
            "tts": {
                "mode": "local",
                "url": "http://127.0.0.1:7860",
                "language": "Auto",
                "parallel_workers": 2,
                "pause_between_speakers_ms": 500,
                "pause_same_speaker_ms": 250,
            },
            "prompts": {"system_prompt": "Route protected prompt."},
            "unknown": {"preserve": True},
        }
        self.config_path.write_text(
            json.dumps(self.config, indent=2) + "\n",
            encoding="utf-8",
        )
        self.config_patch = patch.object(
            app_module,
            "CONFIG_PATH",
            str(self.config_path),
        )
        self.data_patch = patch.object(
            app_module,
            "PROJECTS_DATA_ROOT",
            self.root,
        )
        self.config_patch.start()
        self.data_patch.start()

    def tearDown(self) -> None:
        self.data_patch.stop()
        self.config_patch.stop()
        self.temporary.cleanup()

    def get_settings(self) -> dict:
        response = self.client.get("/api/settings")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_get_redacts_secret_and_includes_default_template(self) -> None:
        before = self.config_path.read_bytes()
        payload = self.get_settings()
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertTrue(
            payload["settings"]["provider"]["api_key_configured"]
        )
        self.assertEqual(payload["settings"]["provider"]["api_key"], "")
        self.assertNotIn("route-secret", json.dumps(payload))
        self.assertNotIn("Route protected prompt", json.dumps(payload))
        default = payload["generation_defaults"]["default_template"]
        self.assertEqual(default["id"], "builtin_standard")
        self.assertEqual(default["name"], "Standard")
        self.assertEqual(
            payload["generation_defaults"]["manage_route"]["destination"],
            "templates",
        )
        self.assertFalse(payload["diagnostics_in_normal_settings"])
        self.assertFalse(payload["repair_actions_in_normal_settings"])

    def test_put_round_trip_preserves_unknown_config_and_resets_tts_engine(self) -> None:
        initial = self.get_settings()
        settings = initial["settings"]
        settings["preferences"]["default_output_language"] = "Swedish"
        settings["provider"].update(
            {
                "backend": "ollama",
                "base_url": "http://127.0.0.1:11434/v1",
                "context_length": 65536,
                "keep_alive": "10m",
                "timeout": 2400,
                "api_key_mode": "preserve",
                "api_key": "",
            }
        )
        settings["speech"]["language"] = "Swedish"
        settings["accessibility"]["motion"] = "reduced"
        settings["storage"]["maximum_backup_gib"] = 24
        app_module.project_manager.engine = object()
        response = self.client.put(
            "/api/settings",
            json={
                "expected_config_fingerprint": initial[
                    "config_fingerprint"
                ],
                "settings": settings,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()
        self.assertEqual(
            updated["settings"]["preferences"]["default_output_language"],
            "Swedish",
        )
        self.assertIsNone(app_module.project_manager.engine)
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["llm"]["api_key"], "route-secret")
        self.assertEqual(saved["prompts"], self.config["prompts"])
        self.assertEqual(saved["unknown"], self.config["unknown"])
        self.assertEqual(saved["application"]["accessibility"]["motion"], "reduced")

    def test_stale_and_invalid_updates_are_machine_readable_and_non_mutating(self) -> None:
        initial = self.get_settings()
        first_settings = initial["settings"]
        first_settings["preferences"]["default_output_language"] = "German"
        first = self.client.put(
            "/api/settings",
            json={
                "expected_config_fingerprint": initial[
                    "config_fingerprint"
                ],
                "settings": first_settings,
            },
        )
        self.assertEqual(first.status_code, 200, first.text)
        after_first = self.config_path.read_bytes()

        stale_settings = first.json()["settings"]
        stale_settings["preferences"]["default_output_language"] = "French"
        stale = self.client.put(
            "/api/settings",
            json={
                "expected_config_fingerprint": initial[
                    "config_fingerprint"
                ],
                "settings": stale_settings,
            },
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(
            stale.json()["detail"]["code"],
            "settings_config_conflict",
        )
        self.assertEqual(self.config_path.read_bytes(), after_first)

        invalid_settings = first.json()["settings"]
        invalid_settings["provider"]["structured_output"] = False
        invalid = self.client.put(
            "/api/settings",
            json={
                "expected_config_fingerprint": first.json()[
                    "config_fingerprint"
                ],
                "settings": invalid_settings,
            },
        )
        self.assertEqual(invalid.status_code, 422, invalid.text)
        self.assertEqual(
            invalid.json()["detail"]["code"],
            "settings_structured_output_required",
        )
        self.assertEqual(self.config_path.read_bytes(), after_first)

    def test_route_surface_is_get_and_put_only(self) -> None:
        registrations = [
            (route.path, set(getattr(route, "methods", set())))
            for route in app_module.app.routes
            if getattr(route, "path", None) == "/api/settings"
        ]
        self.assertEqual(len(registrations), 2)
        self.assertEqual(
            {next(iter(methods)) for _, methods in registrations},
            {"GET", "PUT"},
        )
        for method in ("post", "patch", "delete"):
            response = getattr(self.client, method)("/api/settings")
            self.assertEqual(response.status_code, 405, response.text)

    def test_corrupt_config_and_template_catalog_fail_closed(self) -> None:
        self.config_path.write_text("not json", encoding="utf-8")
        response = self.client.get("/api/settings")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "settings_config_unreadable",
        )
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        (self.root / "templates.json").write_text("[]", encoding="utf-8")
        response = self.client.get("/api/settings")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "settings_template_catalog_unavailable",
        )


if __name__ == "__main__":
    unittest.main()
