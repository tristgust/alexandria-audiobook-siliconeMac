from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import app as app_module
from recurring_voice_routing import (
    ROUTED_CLONE_BACKEND,
    routing_fingerprint,
    validate_recurring_voice_routing,
)


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

    def test_assignment_route_revalidates_responsive_voice_in_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            source_root = Path(directory) / "library"
            root.mkdir()
            identity_relative = Path("clone_voices/primary/chris.wav")
            performance_relative = Path(
                "production_prompt_routes/expressive/chris/chris-dry.wav"
            )
            identity_source = source_root / identity_relative
            performance_source = source_root / performance_relative
            identity_source.parent.mkdir(parents=True)
            performance_source.parent.mkdir(parents=True)
            identity_source.write_bytes(b"reviewed-identity")
            performance_source.write_bytes(b"reviewed-performance")

            def digest(path: Path) -> str:
                return hashlib.sha256(path.read_bytes()).hexdigest()

            policy = {
                "schema_version": 1,
                "enabled": True,
                "default_route": "dry_humour",
                "fallback_backend": "qwen3_instruction_controlled",
                "evidence_round_id": "reviewed_recurring_voice_round",
                "production_promotion_allowed": True,
                "routes": {
                    "dry_humour": {
                        "backend": "indextts2_matched_control",
                        "instruction_keywords": ["dry humour", "wry"],
                        "identity_audio": identity_relative.as_posix(),
                        "identity_audio_sha256": digest(identity_source),
                        "identity_text": "Exact identity transcript.",
                        "performance_audio": performance_relative.as_posix(),
                        "performance_audio_sha256": digest(performance_source),
                        "performance_text": "Exact performance transcript.",
                        "control": {
                            "emotion_strength": 0.75,
                            "diffusion_steps": 8,
                            "num_beams": 1,
                            "greedy": True,
                            "max_mel_tokens": 600,
                        },
                        "production_promotion_allowed": True,
                    }
                },
            }
            normalized = validate_recurring_voice_routing(
                policy,
                project_root=source_root,
                verify_audio=True,
            )
            fingerprint = routing_fingerprint(normalized)
            assignment = {
                "voice_id": "voice_chris_reviewed",
                "kind": "reusable_clone",
                "name": "Chris",
                "configuration": {
                    "type": "clone",
                    "voice": "Ryan",
                    "library_voice_id": "voice_chris_reviewed",
                    "ref_audio": identity_relative.as_posix(),
                    "ref_text": "Exact identity transcript.",
                    "clone_backend": ROUTED_CLONE_BACKEND,
                    "seed": "130363",
                    "responsive_backend_routing": normalized,
                    "responsive_backend_configuration_fingerprint": fingerprint,
                },
                "assets": [
                    {
                        "relative_path": identity_relative.as_posix(),
                        "source_path": identity_source,
                    },
                    {
                        "relative_path": performance_relative.as_posix(),
                        "source_path": performance_source,
                    },
                ],
            }
            character = {
                "character_id": "character_chris",
                "canonical_name": "CHRIS",
                "display_name": "Chris",
                "script_connection": {
                    "resolved_script_voice_label": "CHRIS",
                },
            }
            config_path = root / "voice_config.json"
            with (
                patch.object(app_module, "ROOT_DIR", str(root)),
                patch.object(app_module, "VOICE_CONFIG_PATH", str(config_path)),
                patch.object(app_module, "LEGACY_ROOT_DIR", str(source_root)),
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
                        "character_id": "character_chris",
                        "voice_id": "voice_chris_reviewed",
                    },
                )
            self.assertEqual(response.status_code, 200, response.text)
            saved = json.loads(config_path.read_text(encoding="utf-8"))["CHRIS"]
            self.assertEqual(saved["clone_backend"], ROUTED_CLONE_BACKEND)
            self.assertEqual(
                saved["responsive_backend_configuration_fingerprint"],
                fingerprint,
            )
            self.assertTrue((root / identity_relative).is_file())
            self.assertTrue((root / performance_relative).is_file())

    def test_assignment_route_rejects_forged_responsive_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identity = root / "clone_voices" / "chris.wav"
            performance = root / "production_prompt_routes" / "chris.wav"
            identity.parent.mkdir(parents=True)
            performance.parent.mkdir(parents=True)
            identity.write_bytes(b"identity")
            performance.write_bytes(b"performance")

            def digest(path: Path) -> str:
                return hashlib.sha256(path.read_bytes()).hexdigest()

            policy = {
                "schema_version": 1,
                "enabled": True,
                "default_route": "neutral",
                "fallback_backend": "qwen3_instruction_controlled",
                "evidence_round_id": "reviewed_round",
                "production_promotion_allowed": True,
                "routes": {
                    "neutral": {
                        "backend": "indextts2_matched_control",
                        "instruction_keywords": ["neutral"],
                        "identity_audio": "clone_voices/chris.wav",
                        "identity_audio_sha256": digest(identity),
                        "identity_text": "Identity.",
                        "performance_audio": "production_prompt_routes/chris.wav",
                        "performance_audio_sha256": digest(performance),
                        "performance_text": "Performance.",
                        "control": {
                            "emotion_strength": 0.0,
                            "diffusion_steps": 8,
                            "num_beams": 1,
                            "greedy": True,
                            "max_mel_tokens": 600,
                        },
                        "production_promotion_allowed": True,
                    }
                },
            }
            normalized = validate_recurring_voice_routing(
                policy,
                project_root=root,
                verify_audio=True,
            )
            assignment = {
                "voice_id": "forged",
                "kind": "reusable_clone",
                "name": "Chris",
                "configuration": {
                    "type": "clone",
                    "ref_audio": "clone_voices/chris.wav",
                    "ref_text": "Identity.",
                    "clone_backend": ROUTED_CLONE_BACKEND,
                    "responsive_backend_routing": normalized,
                    "responsive_backend_configuration_fingerprint": "0" * 64,
                },
                "assets": [],
            }
            character = {
                "character_id": "character_chris",
                "canonical_name": "CHRIS",
                "script_connection": {"resolved_script_voice_label": "CHRIS"},
            }
            config_path = root / "voice_config.json"
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
                    json={"character_id": "character_chris", "voice_id": "forged"},
                )
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(
                response.json()["detail"]["code"],
                "voice_library_approval_mismatch",
            )
            self.assertFalse(config_path.exists())
