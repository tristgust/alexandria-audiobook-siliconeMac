from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module


class SoundEffectRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.voice_config = self.root / "voice_config.json"
        self.voice_config.write_text(
            json.dumps(
                {
                    "WOLSEY": {
                        "type": "clone",
                        "voice": "Ryan",
                        "clone_backend": "qwen3_base",
                        "ref_audio": "clone_voices/wolsey.wav",
                        "ref_text": "Meow.",
                        "unknown_legacy_field": 7,
                    }
                }
            ),
            encoding="utf-8",
        )
        self.patches = [
            patch.object(app_module, "ROOT_DIR", str(self.root)),
            patch.object(app_module, "VOICE_CONFIG_PATH", str(self.voice_config)),
            patch.object(
                app_module,
                "_apply_voice_config_dependency_change",
                side_effect=self.write_dependency_change,
            ),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def read_config(self) -> dict:
        return json.loads(self.voice_config.read_text(encoding="utf-8"))

    def write_dependency_change(self, **kwargs):
        self.voice_config.write_text(
            json.dumps(kwargs["after"], indent=2),
            encoding="utf-8",
        )
        return None

    def test_status_truthfully_reports_no_backend(self) -> None:
        with patch.object(
            app_module,
            "sound_effect_backend_status",
            return_value={
                "available": False,
                "backend_id": "stable_audio_open_small",
                "state": "dependencies_missing",
                "message": "Stable Audio runtime is missing.",
            },
        ):
            response = self.client.get("/api/sound-effects/status")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertFalse(payload["available"])
        self.assertEqual(payload["backend_id"], "stable_audio_open_small")
        self.assertIn("runtime is missing", payload["message"])

    def test_save_sound_effect_replaces_prior_speech_configuration_cleanly(self) -> None:
        definition = (
            "Domestic cat; natural close-mic meows, purrs, hisses, chirps, "
            "and small movement sounds; no human speech."
        )
        response = self.client.post(
            "/api/save_voice_config",
            json={
                "WOLSEY": {
                    "type": "sound_effect",
                    "voice": None,
                    "sound_effect_definition": definition,
                    "sound_effect_backend": None,
                    "description": definition,
                    "character_style": "",
                    "ref_audio": None,
                    "ref_text": None,
                }
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        saved = self.read_config()["WOLSEY"]
        self.assertEqual(
            saved,
            {
                "type": "sound_effect",
                "voice": None,
                "sound_effect_schema_version": 2,
                "sound_effect_definition": definition,
                "sound_effect_backend": "stable_audio_open_small",
                "sound_effect_duration_seconds": 3.5,
                "sound_effect_steps": 8,
                "sound_effect_cfg_scale": 1.0,
                "description": definition,
                "character_style": "",
            },
        )

        response = self.client.post(
            "/api/save_voice_config",
            json={"WOLSEY": {"type": "custom", "voice": "Ryan"}},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            self.read_config()["WOLSEY"],
            {"type": "custom", "voice": "Ryan"},
        )

    def test_save_requires_non_empty_sound_definition(self) -> None:
        before = self.voice_config.read_bytes()
        response = self.client.post(
            "/api/save_voice_config",
            json={
                "WOLSEY": {
                    "type": "sound_effect",
                    "voice": None,
                    "sound_effect_definition": "",
                }
            },
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "sound_effect_definition_required",
        )
        self.assertEqual(self.voice_config.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
