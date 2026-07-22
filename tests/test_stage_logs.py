from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from stage_logs import (
    StageLogError,
    append_stage_log,
    read_stage_log,
    reset_stage_log,
)


class StageLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "logs" / "stages" / "roster.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_read_missing_log_is_file_pure(self) -> None:
        result = read_stage_log(self.path, stage="roster")
        self.assertFalse(result["exists"])
        self.assertEqual(result["entries"], [])
        self.assertEqual(result["lines"], [])
        self.assertFalse(self.path.exists())

    def test_reset_and_append_create_valid_persisted_log(self) -> None:
        reset_stage_log(self.path, stage="roster")
        append_stage_log(
            self.path,
            stage="roster",
            message="Started discovery.",
            timestamp="2026-07-17T12:00:00Z",
        )
        append_stage_log(
            self.path,
            stage="roster",
            message="Passage 1 complete.",
            level="progress",
            timestamp="2026-07-17T12:01:00Z",
        )

        result = read_stage_log(self.path, stage="roster")

        self.assertTrue(result["exists"])
        self.assertEqual(
            result["lines"],
            ["Started discovery.", "Passage 1 complete."],
        )
        self.assertEqual(result["entries"][1]["level"], "progress")
        self.assertEqual(result["line_count"], 2)
        self.assertEqual(result["updated_at"], "2026-07-17T12:01:00Z")
        self.assertIsNone(result["error"])

    def test_append_caps_persisted_entries(self) -> None:
        reset_stage_log(self.path, stage="roster")
        for index in range(5):
            append_stage_log(
                self.path,
                stage="roster",
                message=f"line-{index}",
                timestamp=f"2026-07-17T12:0{index}:00Z",
                max_entries=3,
            )

        document = json.loads(self.path.read_text(encoding="utf-8"))
        result = read_stage_log(self.path, stage="roster", limit=2)

        self.assertEqual(
            [entry["message"] for entry in document["entries"]],
            ["line-2", "line-3", "line-4"],
        )
        self.assertEqual(result["lines"], ["line-3", "line-4"])
        self.assertEqual(result["line_count"], 3)
        self.assertTrue(result["truncated"])

    def test_corrupt_log_is_reported_without_rewrite(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not-json", encoding="utf-8")
        before = self.path.read_bytes()

        result = read_stage_log(self.path, stage="roster")

        self.assertTrue(result["exists"])
        self.assertIn("Could not read stage log", result["error"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_wrong_stage_is_reported_without_rewrite(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "stage": "script",
                    "entries": [],
                    "updated_at": None,
                }
            ),
            encoding="utf-8",
        )
        before = self.path.read_bytes()

        result = read_stage_log(self.path, stage="roster")

        self.assertIn("belongs to another stage", result["error"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_stage_name_is_rejected(self) -> None:
        with self.assertRaises(StageLogError):
            read_stage_log(self.path, stage="../roster")


if __name__ == "__main__":
    unittest.main()
