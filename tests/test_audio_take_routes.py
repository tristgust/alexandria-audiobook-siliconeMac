from __future__ import annotations

import copy
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from project import ProjectManager


def write_wav(path: Path, *, frames: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        samples = bytearray()
        for index in range(frames):
            value = 1200 if (index // 120) % 2 == 0 else -1200
            samples.extend(int(value).to_bytes(2, "little", signed=True))
        handle.writeframes(bytes(samples))


class FakeEngine:
    mode = "local"
    _use_mlx = False

    def __init__(self) -> None:
        self.frames = 72000

    def generate_voice(self, text, instruct, speaker, voice_config, output_path):
        write_wav(Path(output_path), frames=self.frames)
        self.frames += 2400
        return True


class AudioTakeRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "app").mkdir()
        self.config_path = self.root / "app" / "config.json"
        self.config_path.write_text(
            json.dumps({"tts": {"mode": "local", "language": "English"}}),
            encoding="utf-8",
        )
        (self.root / "voice_config.json").write_text(
            json.dumps({"NARRATOR": {"type": "custom", "voice": "Ryan"}}),
            encoding="utf-8",
        )
        chunks = [
            {
                "id": 0,
                "speaker": "NARRATOR",
                "text": "A stable Take route fixture line.",
                "instruct": "Calm and clear.",
                "status": "pending",
                "audio_path": None,
            }
        ]
        (self.root / "chunks.json").write_text(
            json.dumps(chunks),
            encoding="utf-8",
        )
        (self.root / "annotated_script.json").write_text(
            json.dumps(chunks),
            encoding="utf-8",
        )
        (self.root / "character_roster.json").write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "id": "character_narrator",
                            "canonical_name": "Narrator",
                            "display_name": "Narrator",
                            "speaking_status": "narrator",
                            "resolution_status": "resolved",
                            "aliases": ["NARRATOR"],
                            "titles": [],
                            "nicknames": [],
                            "sample_lines": [chunks[0]["text"]],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.manager = ProjectManager(str(self.root))
        self.manager.engine = FakeEngine()
        self.assertTrue(self.manager.generate_chunk_audio(0)[0])
        self.assertTrue(self.manager.generate_chunk_audio(0)[0])

        self.original_audio_state = copy.deepcopy(app_module.process_state["audio"])
        app_module.process_state["audio"].update(
            {"running": False, "cancel": False, "logs": [], "request_id": None}
        )
        self.patchers = [
            patch.object(app_module, "ROOT_DIR", str(self.root)),
            patch.object(app_module, "CONFIG_PATH", str(self.config_path)),
            patch.object(app_module, "CHUNKS_PATH", str(self.root / "chunks.json")),
            patch.object(
                app_module,
                "VOICE_CONFIG_PATH",
                str(self.root / "voice_config.json"),
            ),
            patch.object(app_module, "project_manager", self.manager),
            patch.object(
                app_module,
                "list_audio_generation_requests",
                return_value=[],
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
        app_module._clear_produce_aggregate_cache()
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.client.close()
        for patcher in reversed(self.patchers):
            patcher.stop()
        app_module.process_state["audio"].clear()
        app_module.process_state["audio"].update(self.original_audio_state)
        app_module._clear_produce_aggregate_cache()
        self.temporary.cleanup()

    def inventory(self) -> dict:
        response = self.client.get("/api/produce/chunks/0/takes")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_inventory_keep_and_use_prior_take(self) -> None:
        inventory = self.inventory()
        self.assertEqual(inventory["take_count"], 2)
        self.assertTrue(inventory["takes"][0]["current"])
        prior = inventory["takes"][1]

        kept = self.client.post(
            "/api/produce/chunks/0/takes/keep",
            json={
                "take_id": prior["take_id"],
                "registry_fingerprint": inventory["registry_fingerprint"],
                "record_fingerprint": prior["record_fingerprint"],
                "kept": True,
            },
        )
        self.assertEqual(kept.status_code, 200, kept.text)
        self.assertTrue(kept.json()["take"]["kept"])

        refreshed = self.inventory()
        prior = next(
            item for item in refreshed["takes"] if item["take_id"] == prior["take_id"]
        )
        used = self.client.post(
            "/api/produce/chunks/0/takes/use",
            json={
                "take_id": prior["take_id"],
                "registry_fingerprint": refreshed["registry_fingerprint"],
                "record_fingerprint": prior["record_fingerprint"],
            },
        )
        self.assertEqual(used.status_code, 200, used.text)
        selected = json.loads(
            (self.root / "chunks.json").read_text(encoding="utf-8")
        )[0]
        self.assertEqual(selected["current_take_id"], prior["take_id"])
        self.assertEqual(selected["audio_path"], prior["audio"]["relative_path"])

    def test_delete_impact_delete_and_undo_restore_exact_take(self) -> None:
        self.assertTrue(self.manager.generate_chunk_audio(0)[0])
        inventory = self.inventory()
        candidate = inventory["takes"][-1]
        impact_response = self.client.get(
            f"/api/produce/chunks/0/takes/{candidate['take_id']}/delete-impact"
        )
        self.assertEqual(impact_response.status_code, 200, impact_response.text)
        impact = impact_response.json()
        self.assertTrue(impact["safe_to_delete"], impact)
        original_path = self.root / candidate["audio"]["relative_path"]
        original_bytes = original_path.read_bytes()

        deleted = self.client.request(
            "DELETE",
            f"/api/produce/chunks/0/takes/{candidate['take_id']}",
            json={
                "take_id": candidate["take_id"],
                "impact_fingerprint": impact["impact_fingerprint"],
            },
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        result = deleted.json()
        self.assertFalse(original_path.exists())

        undone = self.client.post(
            "/api/produce/takes/undo",
            json={
                "operation_id": result["operation_id"],
                "registry_fingerprint": result["registry_fingerprint"],
            },
        )
        self.assertEqual(undone.status_code, 200, undone.text)
        self.assertEqual(original_path.read_bytes(), original_bytes)

    def test_cleanup_impact_and_noop_apply_are_reviewed(self) -> None:
        impact_response = self.client.post(
            "/api/produce/takes/cleanup-impact",
            json={"older_than_days": 36500, "reclaim_at_least_bytes": 0},
        )
        self.assertEqual(impact_response.status_code, 200, impact_response.text)
        impact = impact_response.json()
        self.assertEqual(impact["candidate_count"], 0)
        applied = self.client.post(
            "/api/produce/takes/cleanup",
            json={
                "older_than_days": 36500,
                "reclaim_at_least_bytes": 0,
                "impact_fingerprint": impact["impact_fingerprint"],
            },
        )
        self.assertEqual(applied.status_code, 409, applied.text)
        self.assertEqual(
            applied.json()["detail"]["code"],
            "audio_take_cleanup_empty",
        )

    def test_mutation_is_blocked_by_persistent_active_request(self) -> None:
        inventory = self.inventory()
        prior = inventory["takes"][1]
        with patch.object(
            app_module,
            "list_audio_generation_requests",
            return_value=[
                {
                    "request_id": "audio_request_active",
                    "state": "resumable",
                }
            ],
        ):
            response = self.client.post(
                "/api/produce/chunks/0/takes/keep",
                json={
                    "take_id": prior["take_id"],
                    "registry_fingerprint": inventory["registry_fingerprint"],
                    "record_fingerprint": prior["record_fingerprint"],
                    "kept": True,
                },
            )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "audio_take_generation_active",
        )


if __name__ == "__main__":
    unittest.main()
