from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module


class RuntimeProjectSwitchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.original_root = Path(app_module.ROOT_DIR).resolve()
        self.original_project_id = app_module.ACTIVE_PROJECT_ID or "legacy-runtime"
        self.original_storage_kind = app_module.ACTIVE_PROJECT_STORAGE_KIND
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        for state in app_module.process_state.values():
            if isinstance(state, dict):
                state["running"] = False
        app_module._activate_runtime_project(
            root_dir=self.original_root,
            project_id=self.original_project_id,
            storage_kind=self.original_storage_kind,
        )
        self.client.close()
        self.temporary.cleanup()

    def project(self, name: str, speaker: str, audio: bytes) -> Path:
        root = self.base / name
        (root / "voicelines").mkdir(parents=True)
        (root / "annotated_script.json").write_text(
            json.dumps(
                [
                    {
                        "speaker": speaker,
                        "text": f"Line for {speaker}.",
                        "instruct": "Measured delivery.",
                    }
                ]
            ),
            encoding="utf-8",
        )
        (root / "voice_config.json").write_text(
            json.dumps(
                {
                    speaker: {
                        "type": "custom",
                        "voice": "Ryan",
                        "character_style": name,
                    }
                }
            ),
            encoding="utf-8",
        )
        (root / "chunks.json").write_text("[]", encoding="utf-8")
        (root / "voicelines" / "sample.wav").write_bytes(audio)
        return root

    def test_switch_rebinds_routes_manager_and_static_assets(self) -> None:
        first = self.project("first", "FIRST SPEAKER", b"first-audio")
        second = self.project("second", "SECOND SPEAKER", b"second-audio")

        first_activation = app_module._activate_runtime_project(
            root_dir=first,
            project_id="project_first",
            storage_kind="managed",
        )
        self.assertEqual(first_activation["state"], "current")
        self.assertEqual(Path(app_module.ROOT_DIR), first.resolve())
        self.assertEqual(
            Path(app_module.project_manager.root_dir),
            first.resolve(),
        )
        first_voices = self.client.get("/api/voices")
        self.assertEqual(first_voices.status_code, 200, first_voices.text)
        self.assertEqual(
            [item["name"] for item in first_voices.json()],
            ["FIRST SPEAKER"],
        )
        first_audio = self.client.get("/voicelines/sample.wav")
        self.assertEqual(first_audio.status_code, 200, first_audio.text)
        self.assertEqual(first_audio.content, b"first-audio")

        second_activation = app_module._activate_runtime_project(
            root_dir=second,
            project_id="project_second",
            storage_kind="managed",
        )
        self.assertEqual(second_activation["state"], "current")
        self.assertEqual(Path(app_module.ROOT_DIR), second.resolve())
        self.assertEqual(
            Path(app_module.project_manager.root_dir),
            second.resolve(),
        )
        second_voices = self.client.get("/api/voices")
        self.assertEqual(second_voices.status_code, 200, second_voices.text)
        self.assertEqual(
            [item["name"] for item in second_voices.json()],
            ["SECOND SPEAKER"],
        )
        second_audio = self.client.get("/voicelines/sample.wav")
        self.assertEqual(second_audio.status_code, 200, second_audio.text)
        self.assertEqual(second_audio.content, b"second-audio")
        status = self.client.get("/api/runtime_status")
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["active_project_id"], "project_second")
        self.assertEqual(status.json()["project_switching"], "dynamic")

    def test_failed_commit_restores_previous_runtime_binding(self) -> None:
        first = self.project("rollback-first", "FIRST", b"first")
        second = self.project("rollback-second", "SECOND", b"second")
        app_module._activate_runtime_project(
            root_dir=first,
            project_id="project_first",
            storage_kind="managed",
        )
        manager_before = app_module.project_manager
        engine_before = object()
        manager_before.engine = engine_before
        app_module.process_state["audio"]["logs"] = ["sentinel-log"]
        static_before = [
            (item.directory, list(item.all_directories), item.config_checked)
            for item in app_module._RUNTIME_PROJECT_STATIC_APPS
        ]
        original_set_static = app_module._set_static_directory
        calls = 0

        def fail_during_commit(static_app, directory):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected static mount failure")
            return original_set_static(static_app, directory)

        with (
            patch.object(
                app_module,
                "_set_static_directory",
                side_effect=fail_during_commit,
            ),
            self.assertRaisesRegex(RuntimeError, "injected static mount failure"),
        ):
            app_module._activate_runtime_project(
                root_dir=second,
                project_id="project_second",
                storage_kind="managed",
            )

        self.assertEqual(Path(app_module.ROOT_DIR), first.resolve())
        self.assertEqual(app_module.ACTIVE_PROJECT_ID, "project_first")
        self.assertIs(app_module.project_manager, manager_before)
        self.assertIs(app_module.project_manager.engine, engine_before)
        self.assertEqual(
            app_module.process_state["audio"]["logs"],
            ["sentinel-log"],
        )
        self.assertEqual(
            [
                (item.directory, list(item.all_directories), item.config_checked)
                for item in app_module._RUNTIME_PROJECT_STATIC_APPS
            ],
            static_before,
        )

    def test_startup_reactivates_last_selected_managed_project(self) -> None:
        legacy = self.project("restart-legacy", "LEGACY", b"legacy")
        selected = self.project("restart-selected", "SELECTED", b"selected")
        app_module._activate_runtime_project(
            root_dir=legacy,
            project_id="project_legacy",
            storage_kind="legacy_checkout",
        )
        legacy_flow = {
            "project": {
                "id": "project_legacy",
                "technical_details": {"project_path": str(legacy)},
            }
        }
        catalog = {
            "last_selected_project_id": "project_selected",
            "projects": [
                {
                    "id": "project_selected",
                    "availability_state": "available",
                    "archive_state": "active",
                    "storage_kind": "managed",
                    "technical_details": {
                        "project_path": str(selected.resolve())
                    },
                }
            ],
        }
        app_module.ACTIVE_PROJECT_ID = None
        app_module.LEGACY_PROJECT_ID = None
        app_module.LEGACY_FLOW_SNAPSHOT = None
        reconciliation_order = []
        with (
            patch.object(
                app_module,
                "_current_project_flow_status",
                return_value=legacy_flow,
            ),
            patch.object(
                app_module,
                "_project_catalog_payload",
                return_value=catalog,
            ),
            patch.object(
                app_module,
                "reconcile_audio_transitions",
                side_effect=lambda root: reconciliation_order.append(
                    ("transitions", Path(root).resolve())
                )
                or {
                    "repaired_count": 0,
                    "rolled_back_count": 0,
                    "unresolved_count": 0,
                    "actions": [],
                },
            ),
            patch.object(
                app_module,
                "reconcile_audio_orphans",
                side_effect=lambda root: reconciliation_order.append(
                    ("orphans", Path(root).resolve())
                )
                or {"issue_count": 0, "issues": []},
            ),
            patch.object(
                app_module,
                "reconcile_interrupted_audio_requests",
                side_effect=lambda root: reconciliation_order.append(
                    ("requests", Path(root).resolve())
                )
                or [],
            ),
        ):
            asyncio.run(app_module.initialize_runtime_project())

        self.assertEqual(app_module.ACTIVE_PROJECT_ID, "project_selected")
        self.assertEqual(Path(app_module.ROOT_DIR), selected.resolve())
        self.assertEqual(
            Path(app_module.project_manager.root_dir),
            selected.resolve(),
        )
        self.assertEqual(
            reconciliation_order,
            [
                ("transitions", selected.resolve()),
                ("orphans", selected.resolve()),
                ("requests", selected.resolve()),
            ],
        )

    def test_running_project_operation_blocks_switch(self) -> None:
        first = self.project("blocked-first", "FIRST", b"first")
        second = self.project("blocked-second", "SECOND", b"second")
        app_module._activate_runtime_project(
            root_dir=first,
            project_id="project_first",
            storage_kind="managed",
        )
        app_module.process_state["audio"]["running"] = True
        with self.assertRaises(app_module.HTTPException) as caught:
            app_module._activate_runtime_project(
                root_dir=second,
                project_id="project_second",
                storage_kind="managed",
            )
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(
            caught.exception.detail["code"],
            "project_activation_operation_running",
        )
        self.assertEqual(Path(app_module.ROOT_DIR), first.resolve())
        app_module.process_state["audio"]["running"] = False


if __name__ == "__main__":
    unittest.main()
