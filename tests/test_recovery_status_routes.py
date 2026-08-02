from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app as app_module


PROTECTED_RUNTIME_FILES = (
    "state.json",
    "generation_state.json",
    "annotated_script.json",
    "annotated_script.meta.json",
    "chunks.json",
    "voice_config.json",
)


def digest(path: Path) -> str:
    if not path.exists():
        return "<absent>"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protected_hashes(root: Path) -> dict[str, str]:
    return {
        relative: digest(root / relative)
        for relative in PROTECTED_RUNTIME_FILES
    }


class RecoveryCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_selected_source_status_restores_persisted_source(self) -> None:
        source = self.root / "uploads" / "book.txt"
        source.parent.mkdir(parents=True)
        source.write_text("A complete source book.", encoding="utf-8")
        (self.root / "state.json").write_text(
            json.dumps({"input_file_path": str(source)}),
            encoding="utf-8",
        )

        with patch.object(app_module, "ROOT_DIR", str(self.root)):
            status = app_module._selected_source_recovery_status()

        self.assertTrue(status["state_file_exists"])
        self.assertTrue(status["persisted"])
        self.assertEqual(status["path"], str(source))
        self.assertEqual(status["basename"], "book.txt")
        self.assertTrue(status["exists"])
        self.assertTrue(status["readable"])
        self.assertIsNone(status["error"])

    def test_selected_source_status_reports_missing_saved_file(self) -> None:
        missing = self.root / "uploads" / "missing.txt"
        (self.root / "state.json").write_text(
            json.dumps({"input_file_path": str(missing)}),
            encoding="utf-8",
        )

        with patch.object(app_module, "ROOT_DIR", str(self.root)):
            status = app_module._selected_source_recovery_status()

        self.assertTrue(status["persisted"])
        self.assertFalse(status["exists"])
        self.assertFalse(status["readable"])
        self.assertEqual(
            status["error"],
            "The saved source book no longer exists.",
        )

    def test_selected_source_status_reports_corrupt_state(self) -> None:
        (self.root / "state.json").write_text("{not-json", encoding="utf-8")

        with patch.object(app_module, "ROOT_DIR", str(self.root)):
            status = app_module._selected_source_recovery_status()

        self.assertTrue(status["state_file_exists"])
        self.assertFalse(status["persisted"])
        self.assertIn("Could not read the saved source selection", status["error"])

    def test_persona_collector_counts_configured_script_speakers(self) -> None:
        script_path = self.root / "annotated_script.json"
        voice_path = self.root / "voice_config.json"
        script_path.write_text(
            json.dumps(
                [
                    {"speaker": "NARRATOR", "text": "One."},
                    {"speaker": "DOCTOR", "text": "Two."},
                    {"speaker": "DOCTOR", "text": "Three."},
                ]
            ),
            encoding="utf-8",
        )
        voice_path.write_text(
            json.dumps(
                {
                    "NARRATOR": {"type": "custom", "voice": "Ryan"},
                    "UNUSED": {"type": "custom", "voice": "Aiden"},
                }
            ),
            encoding="utf-8",
        )

        with (
            patch.object(app_module, "SCRIPT_PATH", str(script_path)),
            patch.object(app_module, "VOICE_CONFIG_PATH", str(voice_path)),
        ):
            result = app_module._current_persona_recovery_inputs()

        self.assertTrue(result["script_available"])
        self.assertEqual(result["total_speakers"], 2)
        self.assertEqual(result["configured_speakers"], 1)
        self.assertIsNone(result["error"])

    def test_persona_collector_reports_invalid_script_without_rewrite(self) -> None:
        script_path = self.root / "annotated_script.json"
        voice_path = self.root / "voice_config.json"
        script_path.write_text("{not-json", encoding="utf-8")
        before = script_path.read_bytes()

        with (
            patch.object(app_module, "SCRIPT_PATH", str(script_path)),
            patch.object(app_module, "VOICE_CONFIG_PATH", str(voice_path)),
        ):
            result = app_module._current_persona_recovery_inputs()

        self.assertFalse(result["script_available"])
        self.assertIn("Could not inspect the annotated script", result["error"])
        self.assertEqual(script_path.read_bytes(), before)

    def test_dataset_collector_selects_latest_persisted_project(self) -> None:
        dataset_root = self.root / "dataset_builder"
        older = dataset_root / "older" / "state.json"
        newer = dataset_root / "newer" / "state.json"
        older.parent.mkdir(parents=True)
        newer.parent.mkdir(parents=True)
        older.write_text(
            json.dumps(
                {
                    "samples": [
                        {"status": "done"},
                        {"status": "pending"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        newer.write_text(
            json.dumps(
                {
                    "samples": [
                        {"status": "done"},
                        {"status": "done"},
                        {"status": "pending"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        older.touch()
        newer.touch()
        older_mtime = older.stat().st_mtime - 100
        older.chmod(0o644)
        import os

        os.utime(older, (older_mtime, older_mtime))

        with patch.object(app_module, "DATASET_BUILDER_DIR", str(dataset_root)):
            result, newest = app_module._current_dataset_recovery_inputs()

        self.assertEqual(result["selected_project"], "newer")
        self.assertEqual(newest, str(newer))
        projects = {item["name"]: item for item in result["projects"]}
        self.assertEqual(projects["older"]["sample_count"], 2)
        self.assertEqual(projects["older"]["done_count"], 1)
        self.assertEqual(projects["newer"]["sample_count"], 3)
        self.assertEqual(projects["newer"]["done_count"], 2)
        self.assertIsNone(result["error"])

    def test_dataset_collector_reports_corrupt_project_without_rewrite(self) -> None:
        dataset_root = self.root / "dataset_builder"
        corrupt = dataset_root / "broken" / "state.json"
        corrupt.parent.mkdir(parents=True)
        corrupt.write_text("{not-json", encoding="utf-8")
        before = corrupt.read_bytes()

        with patch.object(app_module, "DATASET_BUILDER_DIR", str(dataset_root)):
            result, newest = app_module._current_dataset_recovery_inputs()

        self.assertEqual(result["projects"], [])
        self.assertIsNone(newest)
        self.assertIn("Invalid Dataset builder state", result["error"])
        self.assertEqual(corrupt.read_bytes(), before)

    def test_audio_collector_reports_invalid_chunks_without_rewrite(self) -> None:
        chunks = self.root / "chunks.json"
        chunks.write_text("{not-json", encoding="utf-8")
        before = chunks.read_bytes()

        with patch.object(app_module, "CHUNKS_PATH", str(chunks)):
            result = app_module._current_audio_recovery_inputs()

        self.assertEqual(result["chunks"], [])
        self.assertIn("Could not inspect audio chunks", result["error"])
        self.assertEqual(chunks.read_bytes(), before)

    def test_audio_collector_exposes_orphan_reconciliation_status(self) -> None:
        chunks = self.root / "chunks.json"
        chunks.write_text("[]", encoding="utf-8")
        temporary = self.root / "voicelines" / ".render.wav.tmp"
        temporary.parent.mkdir()
        temporary.write_bytes(b"orphan-status-fixture")

        with (
            patch.object(app_module, "ROOT_DIR", str(self.root)),
            patch.object(app_module, "CHUNKS_PATH", str(chunks)),
        ):
            result = app_module._current_audio_recovery_inputs()

        self.assertEqual(result["orphan_reconciliation"]["issue_count"], 1)
        self.assertEqual(
            result["orphan_reconciliation"]["issues"][0]["category"],
            "temporary_file",
        )


class RecoveryStatusRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.client = TestClient(app_module.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

    def test_route_is_registered_once(self) -> None:
        registrations = [
            (route.path, frozenset(getattr(route, "methods", set())))
            for route in app_module.app.routes
        ]
        self.assertEqual(
            sum(
                path == "/api/recovery/status" and "GET" in methods
                for path, methods in registrations
            ),
            1,
        )

    def test_audio_orphan_status_and_operator_action_routes_are_registered(self) -> None:
        registrations = {
            (route.path, method)
            for route in app_module.app.routes
            for method in getattr(route, "methods", set())
        }
        self.assertIn(("/api/audio/orphans", "GET"), registrations)
        self.assertIn(("/api/audio/orphans/action", "POST"), registrations)

    def test_audio_orphan_routes_return_status_and_durable_operator_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            orphan = root / "voicelines" / ".route.wav.tmp"
            orphan.parent.mkdir(parents=True)
            orphan.write_bytes(b"route-orphan")
            with patch.object(app_module, "ROOT_DIR", str(root)):
                status = self.client.get("/api/audio/orphans")
                self.assertEqual(status.status_code, 200, status.text)
                issue = status.json()["issues"][0]
                action = self.client.post(
                    "/api/audio/orphans/action",
                    json={
                        "issue_id": issue["issue_id"],
                        "action": "retain_evidence",
                        "expected_issue_fingerprint": issue["issue_fingerprint"],
                    },
                )

            self.assertEqual(action.status_code, 200, action.text)
            receipt = action.json()
            self.assertEqual(receipt["action"], "retain_evidence")
            self.assertTrue(orphan.exists())
            self.assertTrue((root / receipt["receipt_path"]).is_file())

    def test_audio_orphan_action_rejects_stale_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            orphan = root / "voicelines" / ".stale.wav.tmp"
            orphan.parent.mkdir(parents=True)
            orphan.write_bytes(b"before")
            with patch.object(app_module, "ROOT_DIR", str(root)):
                issue = self.client.get("/api/audio/orphans").json()["issues"][0]
                orphan.write_bytes(b"after")
                response = self.client.post(
                    "/api/audio/orphans/action",
                    json={
                        "issue_id": issue["issue_id"],
                        "action": "remove_orphan",
                        "expected_issue_fingerprint": issue["issue_fingerprint"],
                    },
                )

            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(
                response.json()["detail"]["code"],
                "audio_orphan_action_stale",
            )
            self.assertTrue(orphan.exists())

    def test_route_returns_public_stage_contract(self) -> None:
        expected = {
            "schema_version": 1,
            "model_free": True,
            "file_pure": True,
            "source": {"persisted": False},
            "stages": [
                {
                    "id": "script",
                    "label": "Script",
                    "state": "new",
                }
            ],
            "summary": {"actionable": 1},
        }
        with patch.object(
            app_module,
            "_current_recovery_status",
            return_value=expected,
        ) as builder:
            response = self.client.get("/api/recovery/status")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), expected)
        builder.assert_called_once_with()

    def test_action_rejects_stale_button_against_live_state(self) -> None:
        recovery = {
            "stages": [
                {
                    "id": "script",
                    "state": "blocked",
                    "primary_action": None,
                    "discard_action": {
                        "kind": "discard_script_checkpoint",
                        "label": "Discard script checkpoint",
                    },
                }
            ]
        }
        with patch.object(
            app_module,
            "_current_recovery_status",
            return_value=recovery,
        ):
            response = self.client.post(
                "/api/recovery/action",
                json={"stage_id": "script", "action": "resume_script"},
            )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "recovery_action_stale",
        )
        self.assertEqual(
            response.json()["detail"]["stage_state"],
            "blocked",
        )

    def test_script_resume_dispatches_only_advertised_action(self) -> None:
        recovery = {
            "stages": [
                {
                    "id": "script",
                    "state": "resumable",
                    "primary_action": {
                        "kind": "resume_script",
                        "label": "Resume script from chunk 18",
                        "endpoint": "/api/generate_script",
                    },
                    "discard_action": None,
                }
            ]
        }
        generate = AsyncMock(return_value={"status": "started"})
        with (
            patch.object(
                app_module,
                "_current_recovery_status",
                return_value=recovery,
            ),
            patch.object(app_module, "generate_script", generate),
        ):
            response = self.client.post(
                "/api/recovery/action",
                json={"stage_id": "script", "action": "resume_script"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "accepted")
        self.assertEqual(response.json()["action"], "resume_script")
        self.assertEqual(response.json()["result"], {"status": "started"})
        self.assertEqual(generate.await_count, 1)

    def test_visual_resume_passes_persisted_character_selection(self) -> None:
        recovery = {
            "stages": [
                {
                    "id": "visual",
                    "state": "resumable",
                    "primary_action": {
                        "kind": "resume_visuals",
                        "label": "Resume visual dossiers",
                        "endpoint": "/api/character_visuals/discover",
                        "payload": {
                            "enabled": True,
                            "entry_ids": ["doctor", "ace"],
                        },
                    },
                    "discard_action": None,
                }
            ]
        }
        discover = AsyncMock(return_value={"status": "started"})
        with (
            patch.object(
                app_module,
                "_current_recovery_status",
                return_value=recovery,
            ),
            patch.object(
                app_module,
                "discover_character_visuals",
                discover,
            ),
        ):
            response = self.client.post(
                "/api/recovery/action",
                json={"stage_id": "visual", "action": "resume_visuals"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(discover.await_count, 1)
        visual_request = discover.await_args.args[1]
        self.assertTrue(visual_request.enabled)
        self.assertEqual(visual_request.entry_ids, ["doctor", "ace"])

    def test_stage_specific_discard_does_not_touch_other_checkpoints(self) -> None:
        recovery = {
            "stages": [
                {
                    "id": "roster",
                    "state": "blocked",
                    "primary_action": None,
                    "discard_action": {
                        "kind": "discard_roster_checkpoint",
                        "label": "Discard roster checkpoint",
                        "endpoint": "/api/character_roster/discard-progress",
                    },
                }
            ]
        }
        discard_roster = AsyncMock(return_value={"status": "discarded"})
        discard_script = AsyncMock(return_value={"status": "discarded"})
        discard_visual = AsyncMock(return_value={"status": "discarded"})
        with (
            patch.object(
                app_module,
                "_current_recovery_status",
                return_value=recovery,
            ),
            patch.object(
                app_module,
                "discard_character_roster_progress",
                discard_roster,
            ),
            patch.object(
                app_module,
                "discard_script_generation_state",
                discard_script,
            ),
            patch.object(
                app_module,
                "discard_character_visual_progress",
                discard_visual,
            ),
        ):
            response = self.client.post(
                "/api/recovery/action",
                json={
                    "stage_id": "roster",
                    "action": "discard_roster_checkpoint",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(discard_roster.await_count, 1)
        self.assertEqual(discard_script.await_count, 0)
        self.assertEqual(discard_visual.await_count, 0)

    def test_audio_resume_dispatches_all_unfinished_chunks(self) -> None:
        recovery = {
            "stages": [
                {
                    "id": "audio",
                    "state": "resumable",
                    "primary_action": {
                        "kind": "resume_audio",
                        "label": "Resume audio from chunk 2",
                        "endpoint": "/api/recovery/action",
                    },
                    "discard_action": None,
                }
            ]
        }
        generate = AsyncMock(return_value={"status": "started"})
        with (
            patch.object(
                app_module,
                "_current_recovery_status",
                return_value=recovery,
            ),
            patch.object(
                app_module,
                "_current_audio_recovery_inputs",
                return_value={
                    "chunks": [
                        {"status": "done", "audio_path": "audio/0.mp3"},
                        {"status": "pending", "audio_path": None},
                        {"status": "error", "audio_path": None},
                        {"status": "done", "audio_path": "audio/3.mp3"},
                    ],
                    "error": None,
                },
            ),
            patch.object(app_module, "generate_batch_endpoint", generate),
        ):
            response = self.client.post(
                "/api/recovery/action",
                json={"stage_id": "audio", "action": "resume_audio"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(generate.await_count, 1)
        request = generate.await_args.args[0]
        self.assertEqual(request.indices, [1, 2])

    def test_navigation_action_returns_exact_tab_without_side_effect(self) -> None:
        recovery = {
            "stages": [
                {
                    "id": "dataset_builder",
                    "state": "resumable",
                    "primary_action": {
                        "kind": "resume_dataset",
                        "label": "Continue dataset doctor from sample 4",
                        "tab": "dataset-builder",
                    },
                    "discard_action": None,
                }
            ]
        }
        with patch.object(
            app_module,
            "_current_recovery_status",
            return_value=recovery,
        ):
            response = self.client.post(
                "/api/recovery/action",
                json={
                    "stage_id": "dataset_builder",
                    "action": "resume_dataset",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {
                "status": "navigation_required",
                "stage_id": "dataset_builder",
                "stage_state": "resumable",
                "action": "resume_dataset",
                "tab": "dataset-builder",
            },
        )

    def test_real_status_is_model_free_and_project_file_pure(self) -> None:
        before = protected_hashes(self.root)
        engine_before = app_module.project_manager.engine
        with patch.object(
            app_module.project_manager,
            "get_engine",
            side_effect=AssertionError("Recovery status initialized TTS"),
        ):
            response = self.client.get("/api/recovery/status")
        after = protected_hashes(self.root)

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["schema_version"], 1)
        self.assertTrue(payload["model_free"])
        self.assertTrue(payload["file_pure"])
        self.assertEqual(
            [stage["id"] for stage in payload["stages"]],
            [
                "script",
                "roster",
                "visual",
                "persona",
                "dataset_builder",
                "audio",
                "experimental_training",
            ],
        )
        for stage in payload["stages"]:
            self.assertIn("state", stage)
            self.assertIn("process", stage)
            self.assertIn("identity", stage)
            self.assertIn("details", stage)
        self.assertEqual(after, before)
        self.assertIs(app_module.project_manager.engine, engine_before)


if __name__ == "__main__":
    unittest.main()
