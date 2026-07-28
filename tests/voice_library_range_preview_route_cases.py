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
