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
from audio_mastering import AudioMasteringCancelled
from project import ProjectManager


def write_wav(path: Path, *, frames: int = 72000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        samples = bytearray()
        for index in range(frames):
            value = 2200 if (index // 96) % 2 == 0 else -2200
            samples.extend(int(value).to_bytes(2, "little", signed=True))
        handle.writeframes(bytes(samples))


class FakeEngine:
    mode = "local"
    _use_mlx = False

    def generate_voice(self, text, instruct, speaker, voice_config, output_path):
        write_wav(Path(output_path))
        return True


def settings() -> dict:
    return {
        "schema_version": 1,
        "gain_db": 0,
        "high_pass_hz": 70,
        "low_pass_hz": 10000,
        "compression": {
            "enabled": True,
            "threshold_dbfs": -22,
            "ratio": 2,
            "attack_ms": 8,
            "release_ms": 120,
        },
        "normalization": {
            "enabled": True,
            "target_loudness_dbfs": -20,
            "maximum_gain_db": 8,
        },
        "limiter_ceiling_dbfs": -1,
    }


class AudioMasteringRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "app").mkdir()
        self.config_path = self.root / "app/config.json"
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
                "text": "The publication mastering route remains exact.",
                "instruct": "Calm and clear.",
                "status": "pending",
                "audio_path": None,
            }
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
        inventory = self.manager.audio_take_status(0)
        current = inventory["takes"][0]
        self.manager.set_final_listen_pin(
            0,
            take_id=current["take_id"],
            pinned=True,
            expected_registry_fingerprint=inventory["registry_fingerprint"],
            expected_record_fingerprint=current["record_fingerprint"],
            expected_source_order_fingerprint=(
                self.manager._final_listen_source_order(self.manager.load_chunks())
            ),
        )

        self.original_audio = copy.deepcopy(app_module.process_state["audio"])
        self.original_mastering = copy.deepcopy(
            app_module.process_state["mastering"]
        )
        app_module.process_state["audio"].update(
            {"running": False, "cancel": False, "logs": [], "request_id": None}
        )
        app_module.process_state["mastering"].clear()
        app_module.process_state["mastering"].update(
            copy.deepcopy(app_module._PROJECT_PROCESS_DEFAULTS["mastering"])
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
        app_module.process_state["audio"].update(self.original_audio)
        app_module.process_state["mastering"].clear()
        app_module.process_state["mastering"].update(self.original_mastering)
        app_module._clear_produce_aggregate_cache()
        self.temporary.cleanup()

    def identity(self) -> tuple[dict, dict, str]:
        inventory = self.manager.audio_take_status(0)
        take = next(item for item in inventory["takes"] if item["current"])
        source_order = self.manager._final_listen_source_order(
            self.manager.load_chunks()
        )
        return inventory, take, source_order

    def plan(self) -> tuple[dict, dict]:
        inventory, take, source_order = self.identity()
        body = {
            "take_id": take["take_id"],
            "registry_fingerprint": inventory["registry_fingerprint"],
            "record_fingerprint": take["record_fingerprint"],
            "source_order_fingerprint": source_order,
            "source_sha256": take["audio"]["sha256"],
            "settings": settings(),
        }
        response = self.client.post(
            "/api/produce/chunks/0/mastering/plan",
            json=body,
        )
        self.assertEqual(response.status_code, 200, response.text)
        return body, response.json()

    def test_plan_and_apply_publish_one_mastered_child_and_expose_progress(self) -> None:
        body, plan = self.plan()
        source_path = self.root / self.identity()[1]["audio"]["relative_path"]
        source_bytes = source_path.read_bytes()
        response = self.client.post(
            "/api/produce/chunks/0/mastering/apply",
            json={
                **body,
                "plan_fingerprint": plan["plan_fingerprint"],
                "dependency_fingerprint": plan["dependency_fingerprint"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "accepted")
        state = app_module.process_state["mastering"]
        self.assertFalse(state["running"])
        self.assertEqual(state["status"], "succeeded")
        self.assertEqual(state["completed_count"], 7)
        self.assertEqual(state["result"]["source_take_id"], body["take_id"])
        inventory = self.manager.audio_take_status(0)
        self.assertEqual(inventory["take_count"], 2)
        child = next(item for item in inventory["takes"] if item["current"])
        self.assertEqual(child["processing"]["operation"], "publication_mastering")
        self.assertEqual(child["review"]["state"], "needs_listening")
        self.assertEqual(source_path.read_bytes(), source_bytes)
        aggregate = self.client.get(
            "/api/produce", params={"selected_chunk_id": "chunk:0"}
        )
        self.assertEqual(aggregate.status_code, 200, aggregate.text)
        self.assertEqual(
            aggregate.json()["selected_chunk"]["state"],
            "needs_listening",
        )
        self.assertFalse(aggregate.json()["summary"]["complete"])
        self.assertEqual(
            aggregate.json()["mastering_process"]["status"],
            "succeeded",
        )
        blocked_export = self.client.get("/api/export")
        self.assertEqual(blocked_export.status_code, 200, blocked_export.text)
        export_mastering = blocked_export.json()["plan"]["mastering"]
        self.assertEqual(export_mastering["current_mastered_count"], 1)
        self.assertEqual(
            export_mastering["selected"][0]["dependency_fingerprint"],
            child["processing"]["mastering_dependency_fingerprint"],
        )
        self.assertIn(
            "export_produce_incomplete",
            {item["code"] for item in blocked_export.json()["blockers"]},
        )
        child_inventory = self.manager.audio_take_status(0)
        pinned = self.client.post(
            "/api/produce/chunks/0/final-listen/pin",
            json={
                "take_id": child["take_id"],
                "registry_fingerprint": child_inventory[
                    "registry_fingerprint"
                ],
                "record_fingerprint": child["record_fingerprint"],
                "source_order_fingerprint": self.manager._final_listen_source_order(
                    self.manager.load_chunks()
                ),
                "pinned": True,
            },
        )
        self.assertEqual(pinned.status_code, 200, pinned.text)
        approved_export = self.client.get("/api/export")
        self.assertEqual(approved_export.status_code, 200, approved_export.text)
        self.assertNotIn(
            "export_produce_incomplete",
            {item["code"] for item in approved_export.json()["blockers"]},
        )
        pin_result = pinned.json()
        pin_undo = self.client.post(
            "/api/produce/takes/undo",
            json={
                "operation_id": pin_result["operation_id"],
                "registry_fingerprint": pin_result["registry_fingerprint"],
            },
        )
        self.assertEqual(pin_undo.status_code, 200, pin_undo.text)
        result = state["result"]
        undone = self.client.post(
            "/api/produce/takes/undo",
            json={
                "operation_id": result["operation_id"],
                "registry_fingerprint": pin_undo.json()[
                    "registry_fingerprint"
                ],
            },
        )
        self.assertEqual(undone.status_code, 200, undone.text)
        self.assertEqual(self.manager.audio_take_status(0)["take_count"], 1)

    def test_dependency_drift_discards_candidate_without_child(self) -> None:
        body, plan = self.plan()
        original = self.manager.publication_mastering_dependency

        def drift(*args, **kwargs):
            original(*args, **kwargs)
            return "0" * 64

        with patch.object(
            self.manager,
            "publication_mastering_dependency",
            side_effect=drift,
        ):
            response = self.client.post(
                "/api/produce/chunks/0/mastering/apply",
                json={
                    **body,
                    "plan_fingerprint": plan["plan_fingerprint"],
                    "dependency_fingerprint": plan["dependency_fingerprint"],
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(app_module.process_state["mastering"]["status"], "stale")
        self.assertEqual(self.manager.audio_take_status(0)["take_count"], 1)

    def test_cancellation_terminalizes_without_publishing(self) -> None:
        body, plan = self.plan()

        def cancel(*args, **kwargs):
            job_id = app_module.process_state["mastering"]["background_job_id"]
            app_module.cancel_background_job(str(self.root), job_id)
            raise AudioMasteringCancelled()

        with patch.object(
            self.manager,
            "prepare_publication_mastering_candidate",
            side_effect=cancel,
        ):
            response = self.client.post(
                "/api/produce/chunks/0/mastering/apply",
                json={
                    **body,
                    "plan_fingerprint": plan["plan_fingerprint"],
                    "dependency_fingerprint": plan["dependency_fingerprint"],
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            app_module.process_state["mastering"]["status"],
            "cancelled",
        )
        self.assertEqual(self.manager.audio_take_status(0)["take_count"], 1)

    def test_structural_provenance_does_not_claim_trust(self) -> None:
        inventory, take, source_order = self.identity()
        response = self.client.post(
            "/api/produce/chunks/0/mastering/plan",
            json={
                "take_id": take["take_id"],
                "registry_fingerprint": inventory["registry_fingerprint"],
                "record_fingerprint": take["record_fingerprint"],
                "source_order_fingerprint": source_order,
                "source_sha256": take["audio"]["sha256"],
                "settings": settings(),
                "provenance": {
                    "c2pa": {
                        "present": True,
                        "structural_status": "valid",
                        "signer_trust": "unverified",
                    }
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        provenance = response.json()["provenance"]
        self.assertEqual(provenance["c2pa"]["structural_status"], "valid")
        self.assertEqual(provenance["c2pa"]["signer_trust"], "unverified")
        self.assertEqual(provenance["voice_authorization"], "not_evaluated")

    def test_removing_mastered_pin_restores_listening_gate_and_blocks_export(self) -> None:
        body, plan = self.plan()
        applied = self.client.post(
            "/api/produce/chunks/0/mastering/apply",
            json={
                **body,
                "plan_fingerprint": plan["plan_fingerprint"],
                "dependency_fingerprint": plan["dependency_fingerprint"],
            },
        )
        self.assertEqual(applied.status_code, 200, applied.text)
        inventory, child, source_order = self.identity()
        pinned = self.client.post(
            "/api/produce/chunks/0/final-listen/pin",
            json={
                "take_id": child["take_id"],
                "registry_fingerprint": inventory["registry_fingerprint"],
                "record_fingerprint": child["record_fingerprint"],
                "source_order_fingerprint": source_order,
                "pinned": True,
            },
        )
        self.assertEqual(pinned.status_code, 200, pinned.text)
        inventory, child, source_order = self.identity()
        unpinned = self.client.post(
            "/api/produce/chunks/0/final-listen/pin",
            json={
                "take_id": child["take_id"],
                "registry_fingerprint": inventory["registry_fingerprint"],
                "record_fingerprint": child["record_fingerprint"],
                "source_order_fingerprint": source_order,
                "pinned": False,
            },
        )
        self.assertEqual(unpinned.status_code, 200, unpinned.text)
        current = next(
            item
            for item in self.manager.audio_take_status(0)["takes"]
            if item["current"]
        )
        self.assertEqual(current["review"]["state"], "needs_listening")
        self.assertTrue(current["review"]["listening_required"])
        export = self.client.get("/api/export")
        self.assertEqual(export.status_code, 200, export.text)
        self.assertIn(
            "export_produce_incomplete",
            {item["code"] for item in export.json()["blockers"]},
        )

    def test_existing_take_mutation_is_blocked_while_mastering_owns_project_audio(self) -> None:
        inventory, take, _source_order = self.identity()
        submitted = app_module.submit_background_job(
            str(self.root),
            domain="mastering",
            operation="publication_mastering",
            resources=("mastering", "project_audio"),
            request={"take_id": take["take_id"]},
            dependency_fingerprint="d" * 64,
            resumable=False,
        )
        response = self.client.post(
            "/api/produce/chunks/0/takes/keep",
            json={
                "take_id": take["take_id"],
                "registry_fingerprint": inventory["registry_fingerprint"],
                "record_fingerprint": take["record_fingerprint"],
                "kept": True,
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "audio_take_project_audio_busy",
        )
        app_module.cancel_background_job(
            str(self.root),
            submitted["job"]["job_id"],
        )

    def test_duplicate_apply_returns_existing_job_without_second_worker(self) -> None:
        body, plan = self.plan()
        queued = app_module.submit_background_job(
            str(self.root),
            domain="mastering",
            operation="publication_mastering",
            resources=("mastering", "project_audio"),
            request={
                "chunk_id": "chunk:0",
                "chunk_index": 0,
                "take_id": body["take_id"],
                "plan_fingerprint": plan["plan_fingerprint"],
            },
            dependency_fingerprint=plan["dependency_fingerprint"],
            resumable=False,
            priority=85,
            external_ref={
                "authority": "publication_mastering",
                "chunk_key": "chunk:0",
                "take_id": body["take_id"],
            },
            metadata={"label": "Master publication audio"},
            allow_retry=True,
        )["job"]
        with patch.object(
            self.manager,
            "prepare_publication_mastering_candidate",
            side_effect=AssertionError("duplicate dispatched a second worker"),
        ):
            response = self.client.post(
                "/api/produce/chunks/0/mastering/apply",
                json={
                    **body,
                    "plan_fingerprint": plan["plan_fingerprint"],
                    "dependency_fingerprint": plan["dependency_fingerprint"],
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "already_active")
        self.assertEqual(response.json()["job"]["job_id"], queued["job_id"])
        self.assertEqual(self.manager.audio_take_status(0)["take_count"], 1)
        self.assertEqual(
            len(app_module.list_background_jobs(str(self.root))),
            1,
        )
        app_module.cancel_background_job(str(self.root), queued["job_id"])


if __name__ == "__main__":
    unittest.main()
