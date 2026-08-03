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


def write_wav(path: Path, *, frames: int = 72000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        samples = bytearray()
        for index in range(frames):
            value = 1800 if (index // 96) % 2 == 0 else -1800
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


class FinalListenRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "app").mkdir()
        self.config_path = self.root / "app" / "config.json"
        self.config_path.write_text(
            json.dumps(
                {
                    "tts": {
                        "mode": "local",
                        "language": "English",
                        "pause_same_speaker_ms": 250,
                        "pause_between_speakers_ms": 500,
                    }
                }
            ),
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
                "text": "Chapter One",
                "instruct": "Measured.",
                "status": "pending",
                "audio_path": None,
            },
            {
                "id": 1,
                "speaker": "NARRATOR",
                "text": "The line remains in canonical source order.",
                "instruct": "Calm and clear.",
                "status": "pending",
                "audio_path": None,
            },
        ]
        for name in ("chunks.json", "annotated_script.json"):
            (self.root / name).write_text(json.dumps(chunks), encoding="utf-8")
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
                            "sample_lines": [item["text"] for item in chunks],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.manager = ProjectManager(str(self.root))
        self.manager.engine = FakeEngine()
        self.assertTrue(self.manager.generate_chunk_audio(0)[0])
        self.assertTrue(self.manager.generate_chunk_audio(1)[0])

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
            patch.object(
                app_module,
                "background_scheduler_status",
                return_value={"active": []},
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

    def produce(self, selected: str = "chunk:0") -> dict:
        response = self.client.get(
            "/api/produce",
            params={"selected_chunk_id": selected},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def current_take(self, chunk_id: str) -> tuple[dict, dict]:
        response = self.client.get(f"/api/produce/chunks/{chunk_id}/takes")
        self.assertEqual(response.status_code, 200, response.text)
        inventory = response.json()
        return inventory, next(
            item for item in inventory["takes"] if item["current"]
        )

    def test_pin_pause_export_consumption_and_exact_undo(self) -> None:
        produce = self.produce("chunk:0")
        source_order = produce["final_listen"]["source_order_fingerprint"]
        inventory, current = self.current_take("0")
        audio_path = self.root / current["audio"]["relative_path"]
        audio_bytes = audio_path.read_bytes()

        pinned = self.client.post(
            "/api/produce/chunks/0/final-listen/pin",
            json={
                "take_id": current["take_id"],
                "registry_fingerprint": inventory["registry_fingerprint"],
                "record_fingerprint": current["record_fingerprint"],
                "source_order_fingerprint": source_order,
                "pinned": True,
            },
        )
        self.assertEqual(pinned.status_code, 200, pinned.text)
        self.assertTrue(
            pinned.json()["produce"]["selected_chunk"]["final_listen"][
                "current_take_pinned"
            ]
        )
        self.assertEqual(audio_path.read_bytes(), audio_bytes)

        inventory, current = self.current_take("0")
        before_export = self.client.get("/api/export")
        self.assertEqual(before_export.status_code, 200, before_export.text)
        before_chapter_end = before_export.json()["chapters"][0]["end_ms"]
        paused = self.client.post(
            "/api/produce/chunks/0/final-listen/pause",
            json={
                "take_id": current["take_id"],
                "registry_fingerprint": inventory["registry_fingerprint"],
                "record_fingerprint": current["record_fingerprint"],
                "source_order_fingerprint": source_order,
                "pause_after_ms": 1400,
            },
        )
        self.assertEqual(paused.status_code, 200, paused.text)
        pause_result = paused.json()
        chapters = pause_result["produce"]["final_listen"]["chapters"]
        self.assertEqual(chapters[0]["start_ms"], 0)
        self.assertEqual(
            pause_result["produce"]["selected_chunk"]["pause_after_ms"],
            1400,
        )
        export = self.client.get("/api/export")
        self.assertEqual(export.status_code, 200, export.text)
        self.assertEqual(
            export.json()["chapters"][0]["end_ms"],
            chapters[0]["end_ms"],
        )
        self.assertEqual(
            export.json()["chapters"][0]["end_ms"],
            before_chapter_end + 1150,
        )
        self.assertEqual(audio_path.read_bytes(), audio_bytes)

        undone = self.client.post(
            "/api/produce/takes/undo",
            json={
                "operation_id": pause_result["operation_id"],
                "registry_fingerprint": pause_result["registry_fingerprint"],
            },
        )
        self.assertEqual(undone.status_code, 200, undone.text)
        restored = json.loads((self.root / "chunks.json").read_text())
        self.assertNotIn("pause_after", restored[0])
        self.assertEqual(audio_path.read_bytes(), audio_bytes)

    def test_trim_and_split_create_child_renditions_without_changing_source_order(self) -> None:
        produce = self.produce("chunk:1")
        source_order = produce["final_listen"]["source_order_fingerprint"]
        inventory, source = self.current_take("1")
        source_path = self.root / source["audio"]["relative_path"]
        source_bytes = source_path.read_bytes()
        before_order = [
            (item["id"], item["speaker"], item["text"])
            for item in json.loads((self.root / "chunks.json").read_text())
        ]

        trimmed = self.client.post(
            "/api/produce/chunks/1/final-listen/rendition",
            json={
                "take_id": source["take_id"],
                "registry_fingerprint": inventory["registry_fingerprint"],
                "record_fingerprint": source["record_fingerprint"],
                "source_order_fingerprint": source_order,
                "source_sha256": source["audio"]["sha256"],
                "operation": "trim_edges",
                "trim_start_ms": 120,
                "trim_end_ms": 180,
            },
        )
        self.assertEqual(trimmed.status_code, 200, trimmed.text)
        trim_result = trimmed.json()
        child = trim_result["take"]
        self.assertEqual(child["kind"], "rendition")
        self.assertEqual(child["source_take_id"], source["take_id"])
        self.assertEqual(child["review"]["state"], "needs_listening")
        self.assertTrue(child["review"]["listening_required"])
        self.assertEqual(source_path.read_bytes(), source_bytes)
        self.assertEqual(
            [
                (item["id"], item["speaker"], item["text"])
                for item in json.loads((self.root / "chunks.json").read_text())
            ],
            before_order,
        )

        inventory, current = self.current_take("1")
        split = self.client.post(
            "/api/produce/chunks/1/final-listen/rendition",
            json={
                "take_id": current["take_id"],
                "registry_fingerprint": inventory["registry_fingerprint"],
                "record_fingerprint": current["record_fingerprint"],
                "source_order_fingerprint": source_order,
                "source_sha256": current["audio"]["sha256"],
                "operation": "split_with_pause",
                "split_at_ms": 1100,
                "pause_ms": 360,
            },
        )
        self.assertEqual(split.status_code, 200, split.text)
        split_result = split.json()
        self.assertEqual(
            split_result["take"]["source_take_id"],
            current["take_id"],
        )
        self.assertEqual(
            split_result["processing"]["operation"],
            "final_listen_split_with_pause",
        )
        self.assertEqual(
            [
                (item["id"], item["speaker"], item["text"])
                for item in json.loads((self.root / "chunks.json").read_text())
            ],
            before_order,
        )
        self.assertEqual(source_path.read_bytes(), source_bytes)

        undone = self.client.post(
            "/api/produce/takes/undo",
            json={
                "operation_id": split_result["operation_id"],
                "registry_fingerprint": split_result["registry_fingerprint"],
            },
        )
        self.assertEqual(undone.status_code, 200, undone.text)
        restored_inventory, restored_current = self.current_take("1")
        self.assertEqual(restored_current["take_id"], current["take_id"])
        self.assertEqual(restored_inventory["take_count"], 2)

    def test_stale_order_and_scheduler_owned_audio_fail_closed(self) -> None:
        inventory, current = self.current_take("0")
        stale = self.client.post(
            "/api/produce/chunks/0/final-listen/pin",
            json={
                "take_id": current["take_id"],
                "registry_fingerprint": inventory["registry_fingerprint"],
                "record_fingerprint": current["record_fingerprint"],
                "source_order_fingerprint": "0" * 64,
                "pinned": True,
            },
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(
            stale.json()["detail"]["code"],
            "audio_take_final_listen_order_changed",
        )
        with patch.object(
            app_module,
            "background_scheduler_status",
            return_value={
                "active": [
                    {
                        "job_id": "work_export",
                        "domain": "export",
                        "operation": "build",
                        "resources": ["project_audio", "project_export"],
                    }
                ]
            },
        ):
            busy = self.client.post(
                "/api/produce/chunks/0/final-listen/pause",
                json={
                    "take_id": current["take_id"],
                    "registry_fingerprint": inventory["registry_fingerprint"],
                    "record_fingerprint": current["record_fingerprint"],
                    "source_order_fingerprint": self.produce()["final_listen"][
                        "source_order_fingerprint"
                    ],
                    "pause_after_ms": 800,
                },
            )
        self.assertEqual(busy.status_code, 409, busy.text)
        self.assertEqual(
            busy.json()["detail"]["code"],
            "final_listen_project_audio_busy",
        )


if __name__ == "__main__":
    unittest.main()
