from __future__ import annotations

import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException

import app as app_module


class ContextualReviewRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.script_path = self.root / "annotated_script.json"
        self.config_path = self.root / "config.json"
        self.script_path.write_text(
            json.dumps(
                [
                    {"speaker": "NARRATOR", "text": "One.", "instruct": "Plain."},
                    {"speaker": "DOCTOR", "text": "Two.", "instruct": "Direct."},
                    {"speaker": "NARRATOR", "text": "Three.", "instruct": "Plain."},
                ]
            ),
            encoding="utf-8",
        )
        self.config_path.write_text(
            json.dumps({"generation": {"review_batch_size": 2}}),
            encoding="utf-8",
        )
        self.saved_state = copy.deepcopy(app_module.process_state["review"])
        app_module.process_state["review"].update(
            {
                "running": False,
                "logs": [],
                "process": None,
                "return_code": None,
                "finished_at": None,
            }
        )
        self.patchers = [
            patch.object(app_module, "ROOT_DIR", str(self.root)),
            patch.object(app_module, "SCRIPT_PATH", str(self.script_path)),
            patch.object(app_module, "CONFIG_PATH", str(self.config_path)),
            patch.object(
                app_module,
                "_external_source_context",
                return_value=(
                    {"fingerprint": "f" * 64},
                    "One. “Two.” Three.",
                    None,
                ),
            ),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        app_module.process_state["review"].clear()
        app_module.process_state["review"].update(self.saved_state)
        self.temp.cleanup()

    def test_workload_uses_active_script_and_config(self) -> None:
        self.assertEqual(
            app_module._review_workload(),
            {
                "total_entries": 3,
                "batch_size": 2,
                "estimated_calls": 2,
            },
        )

    def test_text_fidelity_blocks_review_before_any_llm_calls(self) -> None:
        self.script_path.write_text(
            json.dumps(
                [
                    {"speaker": "NARRATOR", "text": "One.", "instruct": "Plain."},
                    {"speaker": "DOCTOR", "text": "Tw", "instruct": "Direct."},
                    {"speaker": "NARRATOR", "text": "o. Three.", "instruct": "Plain."},
                ]
            ),
            encoding="utf-8",
        )
        with self.assertRaises(HTTPException) as caught:
            app_module._review_workload()
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(
            caught.exception.detail["code"],
            "script_text_fidelity_failed",
        )

    def test_contextual_review_reserves_before_background_execution(self) -> None:
        background = BackgroundTasks()
        result = asyncio.run(
            app_module.review_script_contextual(
                app_module.ContextualReviewRequest(window_size=4),
                background,
            )
        )

        self.assertEqual(result["status"], "started")
        self.assertTrue(app_module.process_state["review"]["running"])
        self.assertEqual(len(background.tasks), 1)
        command = background.tasks[0].args[0]
        self.assertIn("--project-root", command)
        self.assertEqual(command[command.index("--project-root") + 1], str(self.root))
        self.assertIn("--config-path", command)
        self.assertEqual(command[command.index("--config-path") + 1], str(self.config_path))
        self.assertEqual(command[-2:], ["--context-window", "4"])

        with self.assertRaises(HTTPException) as caught:
            asyncio.run(
                app_module.review_script_contextual(
                    app_module.ContextualReviewRequest(window_size=4),
                    BackgroundTasks(),
                )
            )
        self.assertEqual(caught.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
