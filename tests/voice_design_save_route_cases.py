from __future__ import annotations

import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import app as app_module


class VoiceDesignSaveRouteCases:
    def test_designed_voice_save_defaults_to_project_and_requires_explicit_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_designed = root / "project" / "designed_voices"
            reusable_root = root / "reusable"
            previews = project_designed / "previews"
            previews.mkdir(parents=True)
            (previews / "audition.wav").write_bytes(b"audition-audio")
            escaped_preview = root / "project" / "secret.wav"
            escaped_preview.write_bytes(b"not-a-preview")
            with (
                patch.object(app_module, "DESIGNED_VOICES_DIR", str(project_designed)),
                patch.object(app_module, "DESIGNED_VOICES_MANIFEST", str(project_designed / "manifest.json")),
                patch.object(app_module, "LEGACY_ROOT_DIR", str(reusable_root)),
            ):
                project_response = self.client.post(
                    "/api/voice_design/save",
                    json={
                        "name": "Project Narrator",
                        "description": "Warm and precise.",
                        "sample_text": "A project audition.",
                        "preview_file": "audition.wav",
                    },
                )
                reusable_response = self.client.post(
                    "/api/voice_design/save",
                    json={
                        "name": "Reusable Narrator",
                        "description": "Warm and precise.",
                        "sample_text": "A reusable audition.",
                        "preview_file": "audition.wav",
                        "scope": "reusable",
                    },
                )
                unsafe_response = self.client.post(
                    "/api/voice_design/save",
                    json={
                        "name": "Unsafe Narrator",
                        "description": "Must not escape previews.",
                        "sample_text": "Unsafe.",
                        "preview_file": "../../secret.wav",
                    },
                )

            self.assertEqual(project_response.status_code, 200, project_response.text)
            self.assertEqual(project_response.json()["scope"], "project")
            project_filename = f"{project_response.json()['voice_id']}.wav"
            self.assertTrue((project_designed / project_filename).is_file())
            self.assertFalse((reusable_root / "designed_voices" / project_filename).exists())
            self.assertEqual(reusable_response.status_code, 200, reusable_response.text)
            self.assertEqual(reusable_response.json()["scope"], "reusable")
            reusable_filename = f"{reusable_response.json()['voice_id']}.wav"
            self.assertTrue((reusable_root / "designed_voices" / reusable_filename).is_file())
            project_manifest = json.loads((project_designed / "manifest.json").read_text(encoding="utf-8"))
            reusable_manifest = json.loads((reusable_root / "designed_voices" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual([item["name"] for item in project_manifest], ["Project Narrator"])
            self.assertEqual([item["name"] for item in reusable_manifest], ["Reusable Narrator"])
            self.assertEqual(unsafe_response.status_code, 400, unsafe_response.text)
            self.assertTrue(escaped_preview.is_file())

    def test_designed_voice_same_name_saves_are_collision_safe_and_serialized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_designed = Path(directory) / "designed_voices"
            previews = project_designed / "previews"
            previews.mkdir(parents=True)
            (previews / "audition.wav").write_bytes(b"collision-proof-audio")
            payload = {
                "name": "Same Narrator",
                "description": "Warm and precise.",
                "sample_text": "Keep every audition.",
                "preview_file": "audition.wav",
            }
            with (
                patch.object(app_module, "DESIGNED_VOICES_DIR", str(project_designed)),
                patch.object(app_module, "DESIGNED_VOICES_MANIFEST", str(project_designed / "manifest.json")),
                patch.object(app_module.time, "time_ns", return_value=1_234_567_890),
            ):
                with ThreadPoolExecutor(max_workers=4) as pool:
                    responses = list(pool.map(
                        lambda _index: self.client.post("/api/voice_design/save", json=payload),
                        range(6),
                    ))

            self.assertTrue(all(response.status_code == 200 for response in responses), responses)
            voice_ids = [response.json()["voice_id"] for response in responses]
            self.assertEqual(len(set(voice_ids)), 6)
            manifest = json.loads((project_designed / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest), 6)
            self.assertEqual({item["id"] for item in manifest}, set(voice_ids))
            for voice_id in voice_ids:
                self.assertEqual(
                    (project_designed / f"{voice_id}.wav").read_bytes(),
                    b"collision-proof-audio",
                )
