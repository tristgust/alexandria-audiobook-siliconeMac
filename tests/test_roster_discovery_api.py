from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException

import app as app_module
from character_roster import (
    build_source_snapshot,
    save_character_roster,
)
from generation_state import atomic_json_write
from roster_discovery import (
    build_discovery_identity,
    build_discovery_passages,
    new_roster_discovery_state,
)
from tests.roster_discovery_support import (
    DynamicRosterRuntime,
)
from tests.test_character_roster import (
    CharacterRosterFixture,
)


class FakeProcess:
    def __init__(self):
        self.terminated = False

    def terminate(self):
        self.terminated = True


class RosterDiscoveryAPITests(
    unittest.TestCase,
    CharacterRosterFixture,
):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_path = self.root / "book.txt"
        self.source_text = "\n\n".join(
            f"Passage {index}. The Doctor greeted Roz."
            for index in range(1, 12)
        )
        self.source_path.write_text(
            self.source_text,
            encoding="utf-8",
        )
        self.source_snapshot, _ = build_source_snapshot(
            self.source_path
        )
        (self.root / "state.json").write_text(
            json.dumps(
                {
                    "input_file_path": str(
                        self.source_path
                    )
                }
            ),
            encoding="utf-8",
        )
        self.draft_path = (
            self.root / "character_roster.draft.json"
        )
        self.approved_path = (
            self.root / "character_roster.json"
        )
        self.discovery_state_path = (
            self.root / "character_roster_state.json"
        )
        self.patchers = [
            patch.object(
                app_module,
                "ROOT_DIR",
                str(self.root),
            ),
            patch.object(
                app_module,
                "CHARACTER_ROSTER_DRAFT_PATH",
                str(self.draft_path),
            ),
            patch.object(
                app_module,
                "CHARACTER_ROSTER_PATH",
                str(self.approved_path),
            ),
            patch.object(
                app_module,
                "CHARACTER_ROSTER_STATE_PATH",
                str(self.discovery_state_path),
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
        app_module.process_state["roster"] = {
            "running": False,
            "logs": [],
            "cancel": False,
            "process": None,
        }

    def tearDown(self):
        app_module.process_state["roster"] = {
            "running": False,
            "logs": [],
            "cancel": False,
            "process": None,
        }
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def request(
        self,
        *,
        replace_draft: bool = False,
        passage_size: int = 12000,
        overlap_chars: int = 1200,
    ):
        return app_module.CharacterRosterDiscoverRequest(
            replace_draft=replace_draft,
            passage_size=passage_size,
            overlap_chars=overlap_chars,
        )

    def test_start_builds_expected_subprocess_command(self):
        tasks = BackgroundTasks()
        captured = []

        def fake_run_process(command, task_name):
            captured.append((list(command), task_name))

        with patch.object(
            app_module,
            "run_process",
            side_effect=fake_run_process,
        ):
            result = asyncio.run(
                app_module.discover_character_roster(
                    tasks,
                    self.request(
                        passage_size=500,
                        overlap_chars=50,
                    ),
                )
            )
            asyncio.run(tasks())

        self.assertEqual(result["status"], "started")
        self.assertEqual(len(captured), 1)
        command, task_name = captured[0]
        self.assertEqual(task_name, "roster")
        self.assertEqual(
            command[:4],
            [
                app_module.sys.executable,
                "-u",
                "discover_character_roster.py",
                str(self.source_path),
            ],
        )
        self.assertIn("--passage-size", command)
        self.assertIn("500", command)
        self.assertNotIn("--replace-draft", command)

    def test_explicit_replacement_flag_is_forwarded(self):
        draft = self.draft(self.source_snapshot)
        save_character_roster(
            draft,
            self.draft_path,
            source_text=self.SOURCE_TEXT,
        )
        tasks = BackgroundTasks()
        captured = []

        def fake_run_process(command, task_name):
            captured.append((list(command), task_name))

        with patch.object(
            app_module,
            "run_process",
            side_effect=fake_run_process,
        ):
            asyncio.run(
                app_module.discover_character_roster(
                    tasks,
                    self.request(replace_draft=True),
                )
            )
            asyncio.run(tasks())

        self.assertIn(
            "--replace-draft",
            captured[0][0],
        )

    def test_approved_roster_blocks_discovery(self):
        self.approved_path.write_text("{}", encoding="utf-8")
        with self.assertRaises(HTTPException) as error:
            asyncio.run(
                app_module.discover_character_roster(
                    BackgroundTasks(),
                    self.request(replace_draft=True),
                )
            )
        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("approved", str(error.exception.detail))

    def test_existing_draft_blocks_implicit_replacement(self):
        self.draft_path.write_text("{}", encoding="utf-8")
        with self.assertRaises(HTTPException) as error:
            asyncio.run(
                app_module.discover_character_roster(
                    BackgroundTasks(),
                    self.request(),
                )
            )
        self.assertEqual(error.exception.status_code, 409)
        self.assertIn(
            "draft already exists",
            str(error.exception.detail),
        )

    def test_invalid_passage_settings_are_rejected(self):
        for passage_size, overlap in (
            (199, 10),
            (500, -1),
            (500, 500),
        ):
            with self.subTest(
                passage_size=passage_size,
                overlap=overlap,
            ):
                with self.assertRaises(HTTPException) as error:
                    asyncio.run(
                        app_module.discover_character_roster(
                            BackgroundTasks(),
                            self.request(
                                passage_size=passage_size,
                                overlap_chars=overlap,
                            ),
                        )
                    )
                self.assertEqual(
                    error.exception.status_code,
                    400,
                )

    def test_running_discovery_blocks_second_start_and_discard(self):
        app_module.process_state["roster"][
            "running"
        ] = True
        with self.assertRaises(HTTPException) as start_error:
            asyncio.run(
                app_module.discover_character_roster(
                    BackgroundTasks(),
                    self.request(),
                )
            )
        with self.assertRaises(HTTPException) as discard_error:
            asyncio.run(
                app_module.discard_character_roster_progress()
            )
        self.assertEqual(start_error.exception.status_code, 409)
        self.assertEqual(discard_error.exception.status_code, 409)

    def test_cancel_sets_flag_and_terminates_process(self):
        process = FakeProcess()
        app_module.process_state["roster"] = {
            "running": True,
            "logs": [],
            "cancel": False,
            "process": process,
        }
        result = asyncio.run(
            app_module.cancel_character_roster_discovery()
        )
        self.assertEqual(result["status"], "cancelling")
        self.assertTrue(
            app_module.process_state["roster"]["cancel"]
        )
        self.assertTrue(process.terminated)
        self.assertIn(
            "cancellation requested",
            app_module.process_state["roster"]["logs"][0],
        )

    def test_cancel_when_idle_is_rejected(self):
        with self.assertRaises(HTTPException) as error:
            asyncio.run(
                app_module.cancel_character_roster_discovery()
            )
        self.assertEqual(error.exception.status_code, 400)

    def test_discard_removes_only_discovery_checkpoint(self):
        self.discovery_state_path.write_text(
            '{"sentinel":"progress"}',
            encoding="utf-8",
        )
        self.draft_path.write_text(
            '{"sentinel":"draft"}',
            encoding="utf-8",
        )
        self.approved_path.write_text(
            '{"sentinel":"approved"}',
            encoding="utf-8",
        )
        draft_bytes = self.draft_path.read_bytes()
        approved_bytes = self.approved_path.read_bytes()

        first = asyncio.run(
            app_module.discard_character_roster_progress()
        )
        second = asyncio.run(
            app_module.discard_character_roster_progress()
        )

        self.assertEqual(first["status"], "discarded")
        self.assertEqual(second["status"], "absent")
        self.assertFalse(self.discovery_state_path.exists())
        self.assertEqual(
            self.draft_path.read_bytes(),
            draft_bytes,
        )
        self.assertEqual(
            self.approved_path.read_bytes(),
            approved_bytes,
        )

    def test_status_reports_partial_progress_and_process(self):
        runtime = DynamicRosterRuntime()
        passages = build_discovery_passages(
            self.source_text,
            passage_size=240,
            overlap=40,
        )
        state = new_roster_discovery_state(
            source=self.source_snapshot,
            generation_identity=build_discovery_identity(
                model_name=runtime.model_name,
                backend=runtime.backend,
                passage_size=240,
                overlap=40,
                temperature=0.1,
                max_tokens=6000,
                seed=42,
            ),
            passages=passages,
        )
        atomic_json_write(state, self.discovery_state_path)
        app_module.process_state["roster"]["logs"] = [
            "starting"
        ]
        status = asyncio.run(
            app_module.get_character_roster_status()
        )
        self.assertEqual(
            status["progress"]["status"],
            "resumable",
        )
        self.assertEqual(
            status["progress"]["total_passages"],
            len(passages),
        )
        self.assertEqual(
            status["process"]["logs"],
            ["starting"],
        )
        self.assertNotIn("process", status["process"])

    def test_action_routes_are_registered_once(self):
        expected = {
            "/api/character_roster/discover",
            "/api/character_roster/cancel",
            "/api/character_roster/discard-progress",
        }
        paths = [
            route.path
            for route in app_module.app.routes
            if getattr(route, "path", None) in expected
        ]
        self.assertEqual(sorted(paths), sorted(expected))


if __name__ == "__main__":
    unittest.main()
