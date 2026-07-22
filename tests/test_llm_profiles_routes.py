from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module


class LLMProfilesRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config_path = self.root / "config.json"
        self.config = {
            "llm": {
                "base_url": "http://localhost:11434/v1",
                "api_key": "local",
                "model_name": "qwen3.5:35b-mlx",
                "backend": "ollama",
                "provider_note": "preserve",
            },
            "tts": {"mode": "local", "custom": 42},
        }
        self.config_path.write_text(
            json.dumps(self.config),
            encoding="utf-8",
        )
        self.config_patch = patch.object(
            app_module,
            "CONFIG_PATH",
            str(self.config_path),
        )
        self.config_patch.start()
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.client.close()
        self.config_patch.stop()
        self.temp.cleanup()

    @staticmethod
    def evidence(target: str) -> dict:
        return {
            "benchmark_id": "route-profile-v1",
            "compared_models": ["qwen3.5:35b-mlx", target],
            "quality_comparison_passed": True,
            "fidelity_validation_passed": True,
            "runtime_measurement_completed": True,
            "regression_tests_passed": True,
            "approved_at_utc": "2026-07-16T23:00:00Z",
            "notes": ["Route fixture."],
        }

    def status(self) -> dict:
        response = self.client.get("/api/llm_profiles")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_status_is_model_free_and_file_pure(self) -> None:
        before = self.config_path.read_bytes()
        payload = self.status()
        self.assertEqual(payload["global_model"], "qwen3.5:35b-mlx")
        self.assertEqual(len(payload["stages"]), 8)
        self.assertEqual(self.config_path.read_bytes(), before)

    def test_update_read_and_remove_profile_routes(self) -> None:
        initial = self.status()
        response = self.client.put(
            "/api/llm_profiles/script",
            json={
                "expected_profiles_fingerprint": initial[
                    "profiles_fingerprint"
                ],
                "profile": {
                    "enabled": True,
                    "overrides": {"context_length": 65536},
                    "evidence": None,
                    "notes": ["Long scripts."],
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()
        self.assertEqual(
            updated["effective_llm"]["context_length"],
            65536,
        )
        read = self.client.get("/api/llm_profiles/script")
        self.assertEqual(read.status_code, 200, read.text)
        self.assertEqual(
            read.json()["profiles_fingerprint"],
            updated["profiles_fingerprint"],
        )
        removed = self.client.post(
            "/api/llm_profiles/script/remove",
            json={
                "expected_profiles_fingerprint": updated[
                    "profiles_fingerprint"
                ]
            },
        )
        self.assertEqual(removed.status_code, 200, removed.text)
        script = next(
            item for item in removed.json()["stages"]
            if item["stage"] == "script"
        )
        self.assertFalse(script["configured"])
        self.assertTrue(script["inherits_global"])

    def test_unverified_model_change_returns_422(self) -> None:
        response = self.client.put(
            "/api/llm_profiles/review",
            json={
                "expected_profiles_fingerprint": self.status()[
                    "profiles_fingerprint"
                ],
                "profile": {
                    "enabled": True,
                    "overrides": {"model_name": "alternate-model"},
                    "evidence": None,
                    "notes": [],
                },
            },
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "llm_profile_rejected",
        )

    def test_verified_model_change_returns_effective_model(self) -> None:
        response = self.client.put(
            "/api/llm_profiles/review",
            json={
                "expected_profiles_fingerprint": self.status()[
                    "profiles_fingerprint"
                ],
                "profile": {
                    "enabled": True,
                    "overrides": {"model_name": "alternate-model"},
                    "evidence": self.evidence("alternate-model"),
                    "notes": [],
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["effective_llm"]["model_name"],
            "alternate-model",
        )

    def test_stale_profile_update_returns_409(self) -> None:
        original = self.status()["profiles_fingerprint"]
        first = self.client.put(
            "/api/llm_profiles/script",
            json={
                "expected_profiles_fingerprint": original,
                "profile": {
                    "enabled": True,
                    "overrides": {"timeout": 2200},
                    "evidence": None,
                    "notes": [],
                },
            },
        )
        self.assertEqual(first.status_code, 200, first.text)
        stale = self.client.put(
            "/api/llm_profiles/persona",
            json={
                "expected_profiles_fingerprint": original,
                "profile": {
                    "enabled": True,
                    "overrides": {"timeout": 2300},
                    "evidence": None,
                    "notes": [],
                },
            },
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(
            stale.json()["detail"]["code"],
            "stale_llm_profiles",
        )

    def test_unknown_stage_and_invalid_request_are_rejected(self) -> None:
        unknown = self.client.get("/api/llm_profiles/tts")
        self.assertEqual(unknown.status_code, 422, unknown.text)
        invalid = self.client.put(
            "/api/llm_profiles/script",
            json={"profile": {}},
        )
        self.assertEqual(invalid.status_code, 422, invalid.text)

    def test_config_unknown_fields_survive_profile_route(self) -> None:
        response = self.client.put(
            "/api/llm_profiles/persona",
            json={
                "expected_profiles_fingerprint": self.status()[
                    "profiles_fingerprint"
                ],
                "profile": {
                    "enabled": True,
                    "overrides": {"timeout": 2400},
                    "evidence": None,
                    "notes": [],
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["llm"]["provider_note"], "preserve")
        self.assertEqual(saved["tts"]["custom"], 42)

    def test_routes_are_registered_once(self) -> None:
        registrations = [
            (route.path, frozenset(getattr(route, "methods", set())))
            for route in app_module.app.routes
        ]
        expected = (
            ("/api/llm_profiles", "GET"),
            ("/api/llm_profiles/{stage}", "GET"),
            ("/api/llm_profiles/{stage}", "PUT"),
            ("/api/llm_profiles/{stage}/remove", "POST"),
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
