from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from project import ProjectManager


def write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\x00\x00" * 24000)


class PreviewEngine:
    def __init__(self) -> None:
        self.calls = []

    def generate_voice(
        self,
        text,
        instruct,
        speaker,
        voice_config,
        output_path,
        fish_render_plan=None,
        fish_instruction=None,
    ):
        self.calls.append(
            {
                "text": text,
                "instruct": instruct,
                "speaker": speaker,
                "fish_render_plan": fish_render_plan,
            }
        )
        write_wav(Path(output_path))
        return True


class PronunciationRegistryRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "app").mkdir()
        (self.root / "app" / "config.json").write_text(
            json.dumps({"tts": {"language": "English"}}),
            encoding="utf-8",
        )
        self.voice_config = self.root / "voice_config.json"
        self.voice_config.write_text(
            json.dumps({"NARRATOR": {"type": "custom", "voice": "Ryan"}}),
            encoding="utf-8",
        )
        self.script_path = self.root / "annotated_script.json"
        self.script_bytes = json.dumps(
            [{"speaker": "NARRATOR", "text": "Skaro fell.", "instruct": "Calm."}]
        ).encode("utf-8")
        self.script_path.write_bytes(self.script_bytes)
        self.first_audio = self.root / "voicelines" / "first.wav"
        self.second_audio = self.root / "voicelines" / "second.wav"
        write_wav(self.first_audio)
        write_wav(self.second_audio)
        self.chunks = [
            {
                "id": 0,
                "speaker": "NARRATOR",
                "text": "Skaro fell.",
                "instruct": "Calm.",
                "status": "done",
                "audio_state": "current",
                "audio_path": "voicelines/first.wav",
                "audio_fingerprint": "a" * 64,
            },
            {
                "id": 1,
                "speaker": "NARRATOR",
                "text": "Dalek advanced.",
                "instruct": "Calm.",
                "status": "done",
                "audio_state": "current",
                "audio_path": "voicelines/second.wav",
                "audio_fingerprint": "b" * 64,
            },
        ]
        (self.root / "chunks.json").write_text(
            json.dumps(self.chunks),
            encoding="utf-8",
        )
        self.manager = ProjectManager(str(self.root))
        self.patches = [
            patch.object(app_module, "ROOT_DIR", str(self.root)),
            patch.object(app_module, "VOICE_CONFIG_PATH", str(self.voice_config)),
            patch.object(app_module, "project_manager", self.manager),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    @staticmethod
    def candidate() -> dict:
        return {
            "pronunciation_id": "skaro",
            "chunk_index": 0,
            "start_char": 0,
            "end_char": 5,
            "original": "Skaro",
            "spoken_form": "SKA-roh",
            "engine_source": {"kind": "manual", "engine": "reviewed-listening"},
            "fallback": {"strategy": "bypass"},
            "review": {
                "state": "approved",
                "reviewer": "fixture-reviewer",
                "reviewed_at_utc": "2026-08-01T10:00:00Z",
            },
            "provenance": {
                "source": "operator_review",
                "created_at_utc": "2026-08-01T09:00:00Z",
            },
        }

    def test_preview_is_file_pure_and_preserves_script_text(self) -> None:
        response = self.client.post(
            "/api/pronunciation-registry/preview",
            json={"chunk_index": 0, "candidate_entry": self.candidate()},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["source_text"], "Skaro fell.")
        self.assertEqual(payload["synthesis_text"], "SKA-roh fell.")
        self.assertEqual(payload["receipt"]["applied_count"], 1)
        self.assertFalse((self.root / "pronunciation_registry.json").exists())
        self.assertEqual(self.script_path.read_bytes(), self.script_bytes)
        self.assertEqual(
            json.loads((self.root / "chunks.json").read_text(encoding="utf-8")),
            self.chunks,
        )

    def test_audio_preview_is_listenable_and_does_not_change_production_state(self) -> None:
        engine = PreviewEngine()
        self.manager.engine = engine
        response = self.client.post(
            "/api/pronunciation-registry/preview",
            json={
                "chunk_index": 0,
                "candidate_entry": self.candidate(),
                "generate_audio": True,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(engine.calls[0]["text"], "SKA-roh fell.")
        self.assertIsNone(engine.calls[0]["fish_render_plan"])
        preview = payload["audio_preview"]
        self.assertEqual(len(preview["preview_fingerprint"]), 64)
        audio = self.client.get(preview["audio_url"])
        self.assertEqual(audio.status_code, 200, audio.text)
        self.assertGreater(len(audio.content), 44)
        self.assertFalse((self.root / "pronunciation_registry.json").exists())
        self.assertEqual(self.script_path.read_bytes(), self.script_bytes)
        self.assertEqual(
            json.loads((self.root / "chunks.json").read_text(encoding="utf-8")),
            self.chunks,
        )
        self.assertTrue(self.first_audio.is_file())
        self.assertTrue(self.second_audio.is_file())

    def test_save_selectively_invalidates_and_generic_undo_restores_exact_state(self) -> None:
        initial = self.client.get("/api/pronunciation-registry")
        self.assertEqual(initial.status_code, 200, initial.text)
        fingerprint = initial.json()["registry_fingerprint"]
        saved = self.client.post(
            "/api/pronunciation-registry/entries",
            json={
                "entry": self.candidate(),
                "expected_registry_fingerprint": fingerprint,
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        payload = saved.json()
        self.assertEqual(payload["status"], "saved")
        self.assertEqual(payload["audio_invalidation"]["invalidated_count"], 1)
        operation_id = payload["audio_invalidation"]["operation_id"]
        updated = json.loads((self.root / "chunks.json").read_text(encoding="utf-8"))
        self.assertEqual(updated[0]["audio_state"], "stale")
        self.assertEqual(updated[1], self.chunks[1])
        self.assertFalse(self.first_audio.exists())
        self.assertTrue(self.second_audio.is_file())
        self.assertEqual(self.script_path.read_bytes(), self.script_bytes)

        status = self.client.get("/api/pronunciation-registry")
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["summary"]["approved_count"], 1)
        self.assertEqual(status.json()["entries"][0]["anchor_state"], "current")

        stale = self.client.post(
            "/api/pronunciation-registry/entries",
            json={
                "entry": self.candidate(),
                "expected_registry_fingerprint": fingerprint,
            },
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(
            stale.json()["detail"]["code"],
            "pronunciation_registry_changed",
        )

        undone = self.client.post(
            f"/api/audio-invalidation/{operation_id}/undo"
        )
        self.assertEqual(undone.status_code, 200, undone.text)
        self.assertFalse((self.root / "pronunciation_registry.json").exists())
        self.assertEqual(
            json.loads((self.root / "chunks.json").read_text(encoding="utf-8")),
            self.chunks,
        )
        self.assertTrue(self.first_audio.is_file())
        self.assertTrue(self.second_audio.is_file())
        self.assertEqual(self.script_path.read_bytes(), self.script_bytes)

    def test_delete_is_guarded_by_current_registry_fingerprint(self) -> None:
        initial = self.client.get("/api/pronunciation-registry").json()
        saved = self.client.post(
            "/api/pronunciation-registry/entries",
            json={
                "entry": self.candidate(),
                "expected_registry_fingerprint": initial["registry_fingerprint"],
            },
        ).json()
        current = saved["registry"]["registry_fingerprint"]
        stale = self.client.request(
            "DELETE",
            "/api/pronunciation-registry/entries/skaro",
            json={"expected_registry_fingerprint": initial["registry_fingerprint"]},
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        deleted = self.client.request(
            "DELETE",
            "/api/pronunciation-registry/entries/skaro",
            json={"expected_registry_fingerprint": current},
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json()["status"], "deleted")
        self.assertEqual(deleted.json()["registry"]["entries"], [])

    def test_identical_save_is_a_noop_without_new_history(self) -> None:
        initial = self.client.get("/api/pronunciation-registry").json()
        saved = self.client.post(
            "/api/pronunciation-registry/entries",
            json={
                "entry": self.candidate(),
                "expected_registry_fingerprint": initial["registry_fingerprint"],
            },
        ).json()
        history = self.root / "audio_invalidation_history"
        before_operations = sorted(path.name for path in history.iterdir())
        unchanged = self.client.post(
            "/api/pronunciation-registry/entries",
            json={
                "entry": saved["registry"]["entries"][0],
                "expected_registry_fingerprint": saved["registry"]["registry_fingerprint"],
            },
        )
        self.assertEqual(unchanged.status_code, 200, unchanged.text)
        self.assertEqual(unchanged.json()["status"], "unchanged")
        self.assertIsNone(unchanged.json()["audio_invalidation"])
        self.assertEqual(
            sorted(path.name for path in history.iterdir()),
            before_operations,
        )


if __name__ == "__main__":
    unittest.main()
