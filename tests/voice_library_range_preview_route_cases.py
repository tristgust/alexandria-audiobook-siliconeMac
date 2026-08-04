from __future__ import annotations

import tempfile
import wave
from pathlib import Path
from unittest.mock import patch

import app as app_module


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
                    json={"character_id": "character_clara"},
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
