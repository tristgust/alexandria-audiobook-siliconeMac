from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module


class VoiceAliasRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.voice_config = self.root / "voice_config.json"
        self.script = self.root / "annotated_script.json"
        self.script.write_text(
            json.dumps(
                [
                    {
                        "speaker": "THE DOCTOR",
                        "text": "Hello.",
                        "instruct": "Neutral.",
                    },
                    {
                        "speaker": "DOCTOR",
                        "text": "Goodbye.",
                        "instruct": "Neutral.",
                    },
                    {
                        "speaker": "SEVENTH DOCTOR",
                        "text": "Perhaps.",
                        "instruct": "Wryly.",
                    },
                ]
            ),
            encoding="utf-8",
        )
        self.patches = [
            patch.object(
                app_module,
                "VOICE_CONFIG_PATH",
                str(self.voice_config),
            ),
            patch.object(
                app_module,
                "SCRIPT_PATH",
                str(self.script),
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

    def write_config(self, config: dict) -> bytes:
        self.voice_config.write_text(
            json.dumps(config, indent=2),
            encoding="utf-8",
        )
        return self.voice_config.read_bytes()

    def read_config(self) -> dict:
        return json.loads(self.voice_config.read_text(encoding="utf-8"))

    def test_save_alias_preserves_dormant_configuration(self) -> None:
        self.write_config(
            {
                "THE DOCTOR": {
                    "type": "custom",
                    "voice": "Ryan",
                },
                "DOCTOR": {
                    "type": "clone",
                    "ref_audio": "clone_voices/dormant.wav",
                    "ref_text": "Dormant transcript.",
                    "unknown": 7,
                },
            }
        )

        response = self.client.post(
            "/api/save_voice_config",
            json={"DOCTOR": {"alias_of": "THE DOCTOR"}},
        )

        self.assertEqual(response.status_code, 200, response.text)
        saved = self.read_config()
        self.assertEqual(saved["DOCTOR"]["alias_of"], "THE DOCTOR")
        self.assertEqual(saved["DOCTOR"]["type"], "clone")
        self.assertEqual(
            saved["DOCTOR"]["ref_audio"],
            "clone_voices/dormant.wav",
        )
        self.assertEqual(saved["DOCTOR"]["unknown"], 7)
        self.assertEqual(
            response.json()["aliases"]["DOCTOR"]["resolved_target"],
            "THE DOCTOR",
        )

    def test_clearing_alias_restores_dormant_configuration(self) -> None:
        self.write_config(
            {
                "THE DOCTOR": {
                    "type": "custom",
                    "voice": "Ryan",
                },
                "DOCTOR": {
                    "type": "clone",
                    "ref_audio": "clone_voices/dormant.wav",
                    "ref_text": "Dormant transcript.",
                    "alias_of": "THE DOCTOR",
                },
            }
        )

        response = self.client.post(
            "/api/save_voice_config",
            json={"DOCTOR": {"alias_of": None}},
        )

        self.assertEqual(response.status_code, 200, response.text)
        saved = self.read_config()
        self.assertNotIn("alias_of", saved["DOCTOR"])
        self.assertEqual(saved["DOCTOR"]["type"], "clone")
        self.assertEqual(
            saved["DOCTOR"]["ref_audio"],
            "clone_voices/dormant.wav",
        )
        self.assertFalse(response.json()["aliases"]["DOCTOR"]["is_alias"])

    def test_missing_target_is_rejected_without_write(self) -> None:
        before = self.write_config(
            {
                "DOCTOR": {
                    "type": "custom",
                    "voice": "Ryan",
                }
            }
        )

        response = self.client.post(
            "/api/save_voice_config",
            json={"DOCTOR": {"alias_of": "MISSING"}},
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["code"], "alias_target_missing")
        self.assertEqual(self.voice_config.read_bytes(), before)

    def test_cycle_is_rejected_without_write(self) -> None:
        before = self.write_config(
            {
                "DOCTOR": {
                    "type": "custom",
                    "voice": "Ryan",
                },
                "SEVENTH DOCTOR": {
                    "type": "custom",
                    "voice": "Aiden",
                },
            }
        )

        response = self.client.post(
            "/api/save_voice_config",
            json={
                "DOCTOR": {"alias_of": "SEVENTH DOCTOR"},
                "SEVENTH DOCTOR": {"alias_of": "DOCTOR"},
            },
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["code"], "alias_cycle")
        self.assertEqual(self.voice_config.read_bytes(), before)

    def test_self_alias_is_rejected_without_write(self) -> None:
        before = self.write_config(
            {
                "DOCTOR": {
                    "type": "custom",
                    "voice": "Ryan",
                }
            }
        )

        response = self.client.post(
            "/api/save_voice_config",
            json={"DOCTOR": {"alias_of": "DOCTOR"}},
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "alias_self_reference",
        )
        self.assertEqual(self.voice_config.read_bytes(), before)

    def test_voices_response_exposes_resolved_chain_type_and_source(self) -> None:
        self.write_config(
            {
                "THE DOCTOR": {
                    "type": "clone",
                    "ref_audio": "clone_voices/doctor.wav",
                },
                "DOCTOR": {"alias_of": "THE DOCTOR"},
                "SEVENTH DOCTOR": {"alias_of": "DOCTOR"},
            }
        )

        response = self.client.get("/api/voices")

        self.assertEqual(response.status_code, 200, response.text)
        voices = {entry["name"]: entry for entry in response.json()}
        alias = voices["SEVENTH DOCTOR"]["alias_resolution"]
        self.assertTrue(alias["is_alias"])
        self.assertEqual(
            alias["chain"],
            ["SEVENTH DOCTOR", "DOCTOR", "THE DOCTOR"],
        )
        self.assertEqual(alias["resolved_target"], "THE DOCTOR")
        self.assertEqual(alias["resolved_type"], "clone")
        self.assertEqual(alias["resolved_source"], "doctor.wav")

    def test_off_script_alias_target_remains_available_for_editing(self) -> None:
        self.script.write_text(
            json.dumps(
                [
                    {
                        "speaker": "DOCTOR",
                        "text": "Hello.",
                        "instruct": "Neutral.",
                    }
                ]
            ),
            encoding="utf-8",
        )
        self.write_config(
            {
                "DOCTOR": {"alias_of": "THE DOCTOR"},
                "THE DOCTOR": {
                    "type": "custom",
                    "voice": "Ryan",
                },
                "UNRELATED OLD SPEAKER": {
                    "type": "custom",
                    "voice": "Aiden",
                },
            }
        )

        response = self.client.get("/api/voices")

        self.assertEqual(response.status_code, 200, response.text)
        voices = {entry["name"]: entry for entry in response.json()}
        self.assertEqual(set(voices), {"DOCTOR", "THE DOCTOR"})
        self.assertEqual(
            voices["DOCTOR"]["alias_resolution"]["resolved_target"],
            "THE DOCTOR",
        )
        self.assertFalse(voices["THE DOCTOR"]["persona_pending"])

    def test_invalid_legacy_alias_is_reported_without_breaking_voices(self) -> None:
        self.write_config(
            {
                "THE DOCTOR": {
                    "type": "custom",
                    "voice": "Ryan",
                },
                "DOCTOR": {"alias_of": "MISSING"},
                "SEVENTH DOCTOR": {
                    "type": "custom",
                    "voice": "Aiden",
                },
            }
        )

        response = self.client.get("/api/voices")

        self.assertEqual(response.status_code, 200, response.text)
        voices = {entry["name"]: entry for entry in response.json()}
        self.assertEqual(
            voices["DOCTOR"]["alias_resolution"]["error"]["code"],
            "alias_target_missing",
        )
        self.assertEqual(
            voices["SEVENTH DOCTOR"]["alias_resolution"]["resolved_target"],
            "SEVENTH DOCTOR",
        )

    def test_designed_voice_normalizes_to_definition_without_assigned_name(self) -> None:
        response = self.client.post(
            "/api/save_voice_config",
            json={
                "DOCTOR": {
                    "type": "designed_voice",
                    "voice": "Aiden",
                    "description": "A precise, wiry tenor with restrained warmth.",
                }
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        saved = self.read_config()["DOCTOR"]
        self.assertEqual(saved["type"], "design")
        self.assertIsNone(saved["voice"])
        self.assertEqual(
            saved["description"],
            "A precise, wiry tenor with restrained warmth.",
        )

    def test_designed_voice_without_definition_is_rejected_without_write(self) -> None:
        before = self.write_config(
            {
                "DOCTOR": {
                    "type": "custom",
                    "voice": "Aiden",
                }
            }
        )

        response = self.client.post(
            "/api/save_voice_config",
            json={
                "DOCTOR": {
                    "type": "designed_voice",
                    "voice": "Aiden",
                    "description": "",
                }
            },
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "designed_voice_definition_required",
        )
        self.assertEqual(self.voice_config.read_bytes(), before)

    def test_invalid_existing_json_is_not_overwritten(self) -> None:
        self.voice_config.write_text("{not-json", encoding="utf-8")
        before = self.voice_config.read_bytes()

        response = self.client.post(
            "/api/save_voice_config",
            json={
                "THE DOCTOR": {
                    "type": "custom",
                    "voice": "Ryan",
                }
            },
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "voice_config_invalid")
        self.assertEqual(self.voice_config.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
