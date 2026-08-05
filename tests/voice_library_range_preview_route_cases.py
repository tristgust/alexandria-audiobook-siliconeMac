from __future__ import annotations

import json
import tempfile
import wave
from pathlib import Path
from unittest.mock import patch

import app as app_module
from voice_library import _stable_id


class VoiceLibraryRangePreviewRouteCases:
    def test_built_in_range_preview_keeps_description_for_every_delivery(self) -> None:
        calls = []

        class PreviewEngine:
            def generate_voice(
                self,
                text,
                instruction,
                speaker,
                voice_config,
                output_path,
            ):
                calls.append(
                    {
                        "text": text,
                        "instruction": instruction,
                        "configuration": dict(voice_config[speaker]),
                    }
                )
                with wave.open(output_path, "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(24000)
                    handle.writeframes(b"\x00\x00" * 240)
                return True

        with tempfile.TemporaryDirectory() as directory:
            preview_root = Path(directory)
            with (
                patch.object(app_module, "DESIGNED_VOICES_DIR", str(preview_root)),
                patch.object(
                    app_module.project_manager,
                    "get_engine",
                    return_value=PreviewEngine(),
                ),
            ):
                response = self.client.post(
                    "/api/voice-library/built-in-range-preview",
                    json={
                        "voice": "Ryan",
                        "persistent_description": (
                            "Warm, weathered contralto with deliberate pacing."
                        ),
                    },
                )

            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(
                [item["id"] for item in payload["sequence"]],
                ["baseline", "happy", "sad", "angry"],
            )
            self.assertEqual(len(calls), 4)
            self.assertEqual(
                {call["configuration"]["character_style"] for call in calls},
                {"Warm, weathered contralto with deliberate pacing."},
            )
            self.assertEqual(len({call["instruction"] for call in calls}), 4)
            self.assertTrue(
                Path(preview_root, "previews", Path(payload["audio_url"]).name).is_file()
            )

    def test_built_in_range_preview_rejects_invalid_inputs(self) -> None:
        invalid_voice = self.client.post(
            "/api/voice-library/built-in-range-preview",
            json={"voice": "Not a real voice", "persistent_description": "Warm."},
        )
        missing_description = self.client.post(
            "/api/voice-library/built-in-range-preview",
            json={"voice": "Ryan", "persistent_description": "   "},
        )
        self.assertEqual(invalid_voice.status_code, 422)
        self.assertEqual(missing_description.status_code, 422)

    def test_supplied_range_preview_uses_saved_reference_without_voice_design(self) -> None:
        calls = []

        class PreviewEngine:
            def generate_voice(
                self,
                text,
                instruction,
                speaker,
                voice_config,
                output_path,
            ):
                calls.append(
                    {
                        "text": text,
                        "instruction": instruction,
                        "speaker": speaker,
                        "configuration": dict(voice_config[speaker]),
                    }
                )
                with wave.open(output_path, "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(24000)
                    handle.writeframes(b"\x00\x00" * 240)
                return True

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "voices" / "clara.wav"
            reference.parent.mkdir()
            with wave.open(str(reference), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(24000)
                handle.writeframes(b"\x01\x00" * 240)
            voice_config = root / "voice_config.json"
            voice_config.write_text(
                """{
  "CLARA": {
    "type": "clone",
    "voice": null,
    "ref_audio": "voices/clara.wav",
    "ref_text": "The exact supplied reference transcript.",
    "clone_backend": "qwen3_base"
  }
}\n""",
                encoding="utf-8",
            )
            with (
                patch.object(app_module, "ROOT_DIR", str(root)),
                patch.object(app_module, "VOICE_CONFIG_PATH", str(voice_config)),
                patch.object(app_module, "DESIGNED_VOICES_DIR", str(root / "designed_voices")),
                patch.object(
                    app_module,
                    "inspect_cast_project",
                    return_value={
                        "selected_character": {
                            "character_id": "character_clara",
                            "display_name": "Clara",
                            "script_connection": {
                                "resolved_script_voice_label": "CLARA",
                            },
                        }
                    },
                ),
                patch.object(
                    app_module.project_manager,
                    "get_engine",
                    return_value=PreviewEngine(),
                ),
            ):
                response = self.client.post(
                    "/api/voice-library/supplied-range-preview",
                    json={
                        "character_id": "character_clara",
                        "voice_overlay": {
                            "direction": "slightly brighter and more clipped",
                        },
                    },
                )

            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(
                [item["id"] for item in payload["sequence"]],
                ["baseline", "happy", "sad", "angry"],
            )
            self.assertEqual(len(calls), 4)
            self.assertEqual({call["speaker"] for call in calls}, {"CLARA"})
            self.assertEqual(
                {call["configuration"]["ref_text"] for call in calls},
                {"The exact supplied reference transcript."},
            )
            self.assertEqual(
                {call["configuration"]["ref_audio"] for call in calls},
                {str(reference.resolve())},
            )
            self.assertTrue(
                all(
                    "Character-specific Voice direction: slightly brighter and more clipped"
                    in call["instruction"]
                    for call in calls
                )
            )
            self.assertEqual(
                payload["voice_overlay"]["direction"],
                "slightly brighter and more clipped",
            )
            self.assertTrue(
                Path(root, "designed_voices", "previews", Path(payload["audio_url"]).name)
                .is_file()
            )

    def test_supplied_range_preview_requires_exactly_one_target(self) -> None:
        missing = self.client.post(
            "/api/voice-library/supplied-range-preview",
            json={},
        )
        ambiguous = self.client.post(
            "/api/voice-library/supplied-range-preview",
            json={"character_id": "character_clara", "voice_id": "voice_clara"},
        )
        self.assertEqual(missing.status_code, 422)
        self.assertEqual(ambiguous.status_code, 422)

    def test_supplied_range_preview_projects_responsive_voice_to_direct_identity(self) -> None:
        calls = []

        class PreviewEngine:
            def generate_voice(
                self,
                text,
                instruction,
                speaker,
                voice_config,
                output_path,
            ):
                calls.append(dict(voice_config[speaker]))
                with wave.open(output_path, "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(24000)
                    handle.writeframes(b"\x00\x00" * 240)
                return True

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "clone_voices" / "computer" / "identity.wav"
            reference.parent.mkdir(parents=True)
            with wave.open(str(reference), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(24000)
                handle.writeframes(b"\x01\x00" * 240)
            voice_config = root / "voice_config.json"
            voice_config.write_text(
                json.dumps(
                    {
                        "COMPUTER": {
                            "type": "clone",
                            "ref_audio": reference.relative_to(root).as_posix(),
                            "ref_text": "Identity line.",
                            "clone_backend": "alexandria_responsive_router",
                            "responsive_backend_routing": {
                                "schema_version": 1,
                                "enabled": True,
                                "default_route": "neutral",
                                "fallback_backend": "qwen3_instruction_controlled",
                                "evidence_round_id": "fixture",
                                "production_promotion_allowed": True,
                                "routes": {
                                    "neutral": {
                                        "backend": "qwen3_instruction_controlled",
                                        "instruction_keywords": [],
                                        "identity_audio": reference.relative_to(root).as_posix(),
                                        "identity_audio_sha256": "a" * 64,
                                        "identity_text": "Identity line.",
                                        "performance_audio": None,
                                        "performance_audio_sha256": None,
                                        "performance_text": None,
                                        "control": {},
                                        "effect_chain": None,
                                        "approval_tier": "strict",
                                        "production_promotion_allowed": True,
                                    }
                                },
                            },
                            "responsive_backend_configuration_fingerprint": "b" * 64,
                        }
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(app_module, "ROOT_DIR", str(root)),
                patch.object(app_module, "VOICE_CONFIG_PATH", str(voice_config)),
                patch.object(
                    app_module,
                    "DESIGNED_VOICES_DIR",
                    str(root / "designed_voices"),
                ),
                patch.object(
                    app_module,
                    "inspect_cast_project",
                    return_value={
                        "selected_character": {
                            "character_id": "character_computer",
                            "display_name": "Computer",
                            "script_connection": {
                                "resolved_script_voice_label": "COMPUTER",
                            },
                        }
                    },
                ),
                patch.object(
                    app_module.project_manager,
                    "get_engine",
                    return_value=PreviewEngine(),
                ),
            ):
                response = self.client.post(
                    "/api/voice-library/supplied-range-preview",
                    json={"character_id": "character_computer"},
                )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(calls), 4)
        self.assertEqual(
            {call["clone_backend"] for call in calls},
            {"qwen3_instruction_controlled"},
        )
        self.assertEqual(
            {call["ref_audio"] for call in calls},
            {str(reference.resolve())},
        )
        self.assertTrue(all("responsive_backend_routing" not in call for call in calls))

    def test_existing_designed_identity_generates_range_from_saved_wav(self) -> None:
        calls = []

        class PreviewEngine:
            def generate_voice(
                self,
                text,
                instruction,
                speaker,
                voice_config,
                output_path,
            ):
                calls.append(
                    {
                        "speaker": speaker,
                        "configuration": dict(voice_config[speaker]),
                    }
                )
                with wave.open(output_path, "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(24000)
                    handle.writeframes(b"\x00\x00" * 240)
                return True

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            designed = root / "designed_voices"
            designed.mkdir()
            identity = designed / "heddolli.wav"
            with wave.open(str(identity), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(24000)
                handle.writeframes(b"\x01\x00" * 240)
            (designed / "manifest.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "heddolli",
                            "name": "Heddolli",
                            "description": "Older, orotund ceremonial Voice.",
                            "sample_text": "The ceremony will now commence.",
                            "filename": identity.name,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            voice_config = root / "voice_config.json"
            voice_config.write_text("{}", encoding="utf-8")
            voice_id = _stable_id(
                "voice",
                "instruction_controlled",
                "project-designed:heddolli",
            )
            with (
                patch.object(app_module, "ROOT_DIR", str(root)),
                patch.object(app_module, "VOICE_CONFIG_PATH", str(voice_config)),
                patch.object(
                    app_module,
                    "DESIGNED_VOICES_DIR",
                    str(designed),
                ),
                patch.object(app_module, "LEGACY_ROOT_DIR", str(root)),
                patch.object(
                    app_module.project_manager,
                    "get_engine",
                    return_value=PreviewEngine(),
                ),
            ):
                response = self.client.post(
                    "/api/voice-library/supplied-range-preview",
                    json={"voice_id": voice_id},
                )

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["source_kind"], "project_designed_identity")
            self.assertEqual(len(calls), 4)
            self.assertEqual(
                {call["configuration"]["ref_audio"] for call in calls},
                {str(identity.resolve())},
            )
            self.assertEqual(
                {call["configuration"]["ref_text"] for call in calls},
                {"The ceremony will now commence."},
            )

    def test_approved_community_voice_stages_pack_for_range_audition(self) -> None:
        calls = []

        class PreviewEngine:
            def generate_voice(
                self,
                text,
                instruction,
                speaker,
                voice_config,
                output_path,
            ):
                configuration = dict(voice_config[speaker])
                staged = Path(output_path).parent / configuration["community_pack_path"]
                calls.append(
                    {
                        "speaker": speaker,
                        "configuration": configuration,
                        "staged_exists": staged.is_file(),
                        "staged_bytes": staged.read_bytes() if staged.is_file() else b"",
                    }
                )
                with wave.open(output_path, "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(24000)
                    handle.writeframes(b"\x00\x00" * 240)
                return True

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack = root / "reusable" / "community" / "reader.qvoice"
            pack.parent.mkdir(parents=True)
            pack.write_bytes(b"approved-community-pack")
            voice_config = root / "voice_config.json"
            voice_config.write_text("{}", encoding="utf-8")
            assignment = {
                "voice_id": "voice_community",
                "kind": "community_qvoice",
                "name": "Community Reader",
                "configuration": {
                    "type": "community_qvoice",
                    "voice": "Community Reader",
                    "community_pack_path": "community/reader.qvoice",
                    "community_pack_family": "qvoice_graft",
                    "community_pack_sha256": "a" * 64,
                    "community_pack_approval_fingerprint": "b" * 64,
                },
                "assets": [
                    {
                        "relative_path": "community/reader.qvoice",
                        "source_path": pack,
                    }
                ],
            }
            with (
                patch.object(app_module, "ROOT_DIR", str(root)),
                patch.object(app_module, "VOICE_CONFIG_PATH", str(voice_config)),
                patch.object(
                    app_module,
                    "DESIGNED_VOICES_DIR",
                    str(root / "designed_voices"),
                ),
                patch.object(
                    app_module,
                    "resolve_voice_library_assignment",
                    return_value=assignment,
                ),
                patch.object(
                    app_module.project_manager,
                    "get_engine",
                    return_value=PreviewEngine(),
                ),
            ):
                response = self.client.post(
                    "/api/voice-library/supplied-range-preview",
                    json={"voice_id": "voice_community"},
                )

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["source_kind"], "community_qvoice")
            self.assertEqual(len(calls), 4)
            self.assertTrue(all(call["staged_exists"] for call in calls))
            self.assertEqual(
                {call["staged_bytes"] for call in calls},
                {b"approved-community-pack"},
            )
