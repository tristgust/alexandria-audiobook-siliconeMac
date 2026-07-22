from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from stage_logs import read_stage_log


class RecoveryProcessSeparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.roster_log = Path(self.temp.name) / "logs" / "stages" / "roster.json"
        self.original_script = copy.deepcopy(app_module.process_state["script"])
        self.original_roster = copy.deepcopy(app_module.process_state["roster"])
        app_module.process_state["script"] = {
            "running": False,
            "logs": [],
        }
        app_module.process_state["roster"] = {
            "running": False,
            "logs": [],
            "cancel": False,
            "process": None,
        }
        self.log_patch = patch.object(
            app_module,
            "ROSTER_LOG_PATH",
            str(self.roster_log),
        )
        self.log_patch.start()
        self.stage_log_specs_patch = patch.object(
            app_module,
            "STAGE_LOG_SPECS",
            {
                task_name: (
                    task_name,
                    str(
                        Path(self.temp.name)
                        / "logs"
                        / "stages"
                        / f"{task_name}.json"
                    ),
                )
                for task_name in (
                    "script",
                    "persona",
                    "roster",
                    "visual",
                    "audio",
                    "dataset_builder",
                )
            },
        )
        self.stage_log_specs_patch.start()

    def tearDown(self) -> None:
        self.stage_log_specs_patch.stop()
        self.log_patch.stop()
        app_module.process_state["script"] = self.original_script
        app_module.process_state["roster"] = self.original_roster
        self.temp.cleanup()

    def test_script_reaches_terminal_state_before_roster_handoff(self) -> None:
        observations: list[dict[str, object]] = []

        def start_roster() -> bool:
            observations.append(
                {
                    "script_running": app_module.process_state["script"]["running"],
                    "script_logs": list(app_module.process_state["script"]["logs"]),
                }
            )
            return True

        with (
            patch.object(
                app_module,
                "_stream_subprocess_to_logs",
                return_value=0,
            ),
            patch.object(
                app_module,
                "_start_automatic_roster_after_script",
                side_effect=start_roster,
            ) as handoff,
        ):
            app_module.run_process(["python", "generate_script.py"], "script")

        handoff.assert_called_once_with()
        self.assertEqual(
            observations,
            [
                {
                    "script_running": False,
                    "script_logs": ["Task script completed successfully."],
                }
            ],
        )
        self.assertFalse(app_module.process_state["script"]["running"])
        self.assertEqual(
            app_module.process_state["script"]["logs"],
            ["Task script completed successfully."],
        )
        self.assertFalse(
            any(
                "roster" in line.lower() or "characters" in line.lower()
                for line in app_module.process_state["script"]["logs"]
            )
        )

    def test_failed_script_does_not_start_roster(self) -> None:
        with (
            patch.object(
                app_module,
                "_stream_subprocess_to_logs",
                return_value=7,
            ),
            patch.object(
                app_module,
                "_start_automatic_roster_after_script",
            ) as handoff,
        ):
            app_module.run_process(["python", "generate_script.py"], "script")

        handoff.assert_not_called()
        self.assertEqual(
            app_module.process_state["script"]["logs"],
            ["Task script failed with return code 7."],
        )

    def test_roster_handoff_claims_roster_state_before_thread_start(self) -> None:
        captured: dict[str, object] = {}

        class FakeThread:
            def __init__(self, *, target, name, daemon):
                captured.update(
                    {
                        "target": target,
                        "name": name,
                        "daemon": daemon,
                    }
                )

            def start(self) -> None:
                captured["roster_running_at_start"] = (
                    app_module.process_state["roster"]["running"]
                )
                captured["script_running_at_start"] = (
                    app_module.process_state["script"]["running"]
                )

        with patch.object(app_module.threading, "Thread", FakeThread):
            started = app_module._start_automatic_roster_after_script()

        self.assertTrue(started)
        self.assertEqual(
            captured["name"],
            "alexandria-roster-after-script",
        )
        self.assertTrue(captured["daemon"])
        self.assertTrue(captured["roster_running_at_start"])
        self.assertFalse(captured["script_running_at_start"])
        self.assertEqual(
            app_module.process_state["script"]["logs"],
            [],
        )
        persisted = read_stage_log(self.roster_log, stage="roster")
        self.assertEqual(
            persisted["lines"],
            [
                "Annotated script complete. Starting character roster discovery as a separate stage."
            ],
        )

    def test_automatic_roster_stream_and_terminal_lines_never_enter_script_log(self) -> None:
        source = Path(self.temp.name) / "book.txt"
        source.write_text("Book source.", encoding="utf-8")
        app_module.process_state["script"]["logs"] = [
            "Task script completed successfully."
        ]
        app_module.process_state["roster"]["running"] = True
        app_module._reset_process_logs("roster")
        app_module._append_process_log(
            "roster",
            "Annotated script complete. Starting character roster discovery as a separate stage.",
        )

        def fake_stream(_command, _cwd, state, **kwargs):
            line = "Passage 1 of 2 complete."
            state["logs"].append(line)
            kwargs["log_sink"](line)
            return 0

        with (
            patch.object(
                app_module,
                "_selected_script_input_path",
                return_value=str(source),
            ),
            patch.object(
                app_module,
                "_current_character_roster_status",
                return_value={
                    "approved": {"exists": False, "status": "missing"},
                    "draft": {"exists": False, "status": "missing"},
                },
            ),
            patch.object(
                app_module,
                "_stream_subprocess_to_logs",
                side_effect=fake_stream,
            ),
        ):
            return_code = app_module._automatic_roster_after_script()

        self.assertEqual(return_code, 0)
        self.assertEqual(
            app_module.process_state["script"]["logs"],
            ["Task script completed successfully."],
        )
        self.assertFalse(app_module.process_state["roster"]["running"])
        persisted = read_stage_log(self.roster_log, stage="roster")
        self.assertEqual(
            persisted["lines"],
            [
                "Annotated script complete. Starting character roster discovery as a separate stage.",
                "Passage 1 of 2 complete.",
                "Character roster draft completed and is ready for review.",
            ],
        )

    def test_persisted_roster_log_survives_memory_reset(self) -> None:
        app_module._reset_process_logs("roster")
        app_module._append_process_log("roster", "Persistent roster line.")
        app_module.process_state["roster"]["logs"] = []

        status = app_module._current_roster_process_status()

        self.assertEqual(status["log_source"], "persisted")
        self.assertEqual(status["logs"], ["Persistent roster line."])
        self.assertEqual(status["log_line_count"], 1)
        self.assertIsNone(status["log_error"])

    def test_roster_cancel_message_is_persisted(self) -> None:
        app_module.process_state["roster"]["running"] = True
        app_module._reset_process_logs("roster")

        response = TestClient(app_module.app).post(
            "/api/character_roster/cancel"
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(app_module.process_state["roster"]["cancel"])
        persisted = read_stage_log(self.roster_log, stage="roster")
        self.assertEqual(
            persisted["lines"],
            ["[CANCEL] Character roster discovery cancellation requested"],
        )


if __name__ == "__main__":
    unittest.main()
