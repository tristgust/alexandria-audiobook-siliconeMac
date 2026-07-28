from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import app as app_module


class VoiceLibraryAssignmentRouteCases:
    def test_clear_route_removes_authoritative_assignment_and_copied_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "voice_config.json"
            copied = root / "clone_voices" / "benny.wav"
            copied.parent.mkdir(parents=True)
            copied.write_bytes(b"same-audio")
            config_path.write_text(
                json.dumps(
                    {
                        "BERNICE": {
                            "type": "clone",
                            "library_voice_id": "voice_benny",
                            "ref_audio": "clone_voices/benny.wav",
                            "ref_text": "Exact transcript.",
                        }
                    }
                ),
                encoding="utf-8",
            )
            source = root / "source-benny.wav"
            source.write_bytes(b"same-audio")
            character = {
                "character_id": "character_bernice",
                "canonical_name": "BERNICE",
                "display_name": "Bernice",
                "script_connection": {
                    "resolved_script_voice_label": "BERNICE",
                },
            }
            cleared = {
                **character,
                "voice": {"selected_production_method": None},
            }
            assignment = {
                "voice_id": "voice_benny",
                "kind": "reusable_clone",
                "name": "Benny / Bernice",
                "configuration": {},
                "assets": [
                    {
                        "relative_path": "clone_voices/benny.wav",
                        "source_path": source,
                    }
                ],
            }
            with (
                patch.object(app_module, "ROOT_DIR", str(root)),
                patch.object(app_module, "VOICE_CONFIG_PATH", str(config_path)),
                patch.object(app_module, "LEGACY_ROOT_DIR", str(root)),
                patch.object(
                    app_module,
                    "inspect_cast_project",
                    side_effect=[
                        {"selected_character": character},
                        {"selected_character": cleared},
                    ],
                ),
                patch.object(
                    app_module,
                    "resolve_voice_library_assignment",
                    return_value=assignment,
                ),
            ):
                response = self.client.post(
                    "/api/voice-library/clear",
                    json={"character_id": "character_bernice"},
                )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "cleared")
            self.assertFalse(config_path.exists())
            self.assertFalse(copied.exists())
            self.assertEqual(
                response.json()["removed_assets"],
                ["clone_voices/benny.wav"],
            )

    def test_assignment_route_writes_the_authoritative_cast_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "voice_config.json"
            character = {
                "character_id": "character_doctor",
                "canonical_name": "THE DOCTOR",
                "display_name": "The Doctor",
                "script_connection": {
                    "resolved_script_voice_label": "THE DOCTOR",
                },
            }
            assignment = {
                "voice_id": "voice_fixture",
                "kind": "built_in",
                "name": "Ryan",
                "configuration": {
                    "type": "custom",
                    "voice": "Ryan",
                    "library_voice_id": "voice_fixture",
                },
                "assets": [],
            }
            with (
                patch.object(app_module, "ROOT_DIR", str(root)),
                patch.object(app_module, "VOICE_CONFIG_PATH", str(config_path)),
                patch.object(
                    app_module,
                    "resolve_voice_library_assignment",
                    return_value=assignment,
                ),
                patch.object(
                    app_module,
                    "inspect_cast_project",
                    return_value={"selected_character": character},
                ),
            ):
                response = self.client.post(
                    "/api/voice-library/assign",
                    json={
                        "character_id": "character_doctor",
                        "voice_id": "voice_fixture",
                    },
                )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "assigned")
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["THE DOCTOR"]["voice"], "Ryan")
            self.assertEqual(
                saved["THE DOCTOR"]["library_voice_id"],
                "voice_fixture",
            )
