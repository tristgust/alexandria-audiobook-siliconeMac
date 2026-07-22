from __future__ import annotations

import json
import math
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from stage_metrics import (
    StageMetricsError,
    prepare_stage_metrics,
    read_stage_metrics,
    record_stage_event,
    record_stage_unit,
    summarize_stage_metrics,
)
from stage_metric_types import StageMetricDocument


class StageMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "script_metrics.json"

    def prepare(
        self,
        *,
        run_id: str = "run-one",
        total_units: int = 5,
        baseline_completed_units: int = 0,
    ) -> StageMetricDocument:
        return prepare_stage_metrics(
            self.path,
            stage="script",
            run_id=run_id,
            total_units=total_units,
            baseline_completed_units=baseline_completed_units,
            started_at="2026-07-17T12:00:00Z",
        )

    def record(
        self,
        index: int,
        *,
        retries: int = 0,
        unit_wall: float = 10.0,
        max_units: int = 1000,
    ) -> StageMetricDocument:
        return record_stage_unit(
            self.path,
            stage="script",
            index=index,
            input_characters=3000,
            output_items=20,
            attempts=1 + retries,
            corrective_retries=retries,
            prompt_tokens=500,
            output_tokens=200,
            validation_mode=(
                "corrective_retry" if retries else "direct"
            ),
            phases_seconds={
                "prompt_assembly": 0.2,
                "request_wall": 8.0,
                "model_generation": 5.0,
                "schema_validation": 0.1,
                "checkpoint_write": 0.1,
                "unit_wall": unit_wall,
            },
            completed_at=f"2026-07-17T12:00:0{index}Z",
            max_units=max_units,
        )

    def test_missing_read_is_file_pure(self):
        result = read_stage_metrics(
            self.path,
            stage="script",
        )

        self.assertFalse(result["exists"])
        self.assertIsNone(result["summary"])
        self.assertIsNone(result["error"])
        self.assertFalse(self.path.exists())

    def test_three_stable_units_enable_conservative_eta(self):
        self.prepare()
        self.record(1)
        self.record(2)
        document = self.record(3)

        summary = summarize_stage_metrics(
            document,
            now=datetime(2026, 7, 17, 12, 0, 10, tzinfo=timezone.utc),
        )

        self.assertEqual(summary["completed_units"], 3)
        self.assertEqual(summary["remaining_units"], 2)
        self.assertEqual(summary["measured_units"], 3)
        self.assertEqual(summary["measured_wall_seconds"], 30.0)
        self.assertEqual(summary["units_per_minute"], 6.0)
        self.assertEqual(summary["characters_per_second"], 300.0)
        self.assertEqual(
            summary["model_output_tokens_per_second"],
            40.0,
        )
        self.assertTrue(summary["eta_reliable"])
        self.assertEqual(summary["eta_reason"], "rolling_conservative")
        self.assertEqual(summary["eta_seconds"], 23.0)

    def test_recent_retry_suppresses_eta(self):
        self.prepare()
        self.record(1)
        self.record(2)
        document = self.record(3, retries=1)

        summary = summarize_stage_metrics(document)

        self.assertFalse(summary["eta_reliable"])
        self.assertIsNone(summary["eta_seconds"])
        self.assertEqual(summary["eta_reason"], "recent_retries")

    def test_same_run_preserves_units_and_new_run_replaces_them(self):
        self.prepare()
        self.record(1)

        resumed = self.prepare(
            baseline_completed_units=1,
        )
        replaced = self.prepare(
            run_id="run-two",
            total_units=2,
        )

        self.assertEqual(len(resumed["units"]), 1)
        self.assertEqual(resumed["units"][0]["index"], 1)
        self.assertEqual(replaced["run_id"], "run-two")
        self.assertEqual(replaced["units"], [])
        self.assertEqual(replaced["total_units"], 2)

    def test_completed_run_is_replaced_by_fresh_run(self):
        self.prepare(total_units=1)
        self.record(1)
        record_stage_event(
            self.path,
            stage="script",
            event="finalization",
            phases_seconds={"finalization": 0.2},
            mark_complete=True,
        )

        fresh = self.prepare(total_units=1)

        self.assertEqual(fresh["status"], "running")
        self.assertEqual(fresh["units"], [])
        self.assertIsNone(fresh["finalization"])
        self.assertEqual(fresh["baseline_completed_units"], 0)

    def test_reconciliation_and_finalization_are_separate_events(self):
        self.prepare(total_units=1)
        self.record(1)
        after_reconciliation = record_stage_event(
            self.path,
            stage="script",
            event="reconciliation",
            phases_seconds={
                "request_wall": 3.0,
                "reconciliation_validation": 0.2,
            },
            completed_at="2026-07-17T12:01:00Z",
        )
        completed = record_stage_event(
            self.path,
            stage="script",
            event="finalization",
            phases_seconds={
                "artifact_write": 0.3,
                "finalization": 0.4,
            },
            completed_at="2026-07-17T12:02:00Z",
            mark_complete=True,
        )

        self.assertEqual(after_reconciliation["status"], "running")
        self.assertEqual(
            after_reconciliation["reconciliation"]["phases_seconds"]
            ["reconciliation_validation"],
            0.2,
        )
        self.assertEqual(completed["status"], "complete")
        self.assertEqual(
            completed["finalization"]["phases_seconds"]["artifact_write"],
            0.3,
        )
        self.assertEqual(
            summarize_stage_metrics(completed)["eta_reason"],
            "complete",
        )

    def test_unit_cap_preserves_completed_baseline(self):
        self.prepare(total_units=5)
        self.record(1, max_units=2)
        self.record(2, max_units=2)
        document = self.record(3, max_units=2)

        summary = summarize_stage_metrics(document)

        self.assertEqual(
            [unit["index"] for unit in document["units"]],
            [2, 3],
        )
        self.assertEqual(document["baseline_completed_units"], 1)
        self.assertEqual(summary["completed_units"], 3)
        self.assertEqual(summary["measured_units"], 2)
        self.assertEqual(summary["unmeasured_completed_units"], 1)

    def test_corrupt_read_reports_error_without_rewriting(self):
        raw = "{not-json"
        self.path.write_text(raw, encoding="utf-8")

        result = read_stage_metrics(
            self.path,
            stage="script",
        )

        self.assertTrue(result["exists"])
        self.assertIsNotNone(result["error"])
        self.assertEqual(self.path.read_text(encoding="utf-8"), raw)

    def test_invalid_measurements_are_rejected(self):
        self.prepare()

        with self.assertRaises(StageMetricsError):
            record_stage_unit(
                self.path,
                stage="script",
                index=1,
                input_characters=1,
                output_items=1,
                attempts=1,
                corrective_retries=0,
                prompt_tokens=None,
                output_tokens=None,
                validation_mode="direct",
                phases_seconds={"unit_wall": math.inf},
            )

        with self.assertRaises(StageMetricsError):
            record_stage_unit(
                self.path,
                stage="script",
                index=1,
                input_characters=1,
                output_items=1,
                attempts=1,
                corrective_retries=0,
                prompt_tokens=None,
                output_tokens=None,
                validation_mode="direct",
                phases_seconds={"unknown_phase": 1.0},
            )

        self.record(1)
        before = json.loads(self.path.read_text(encoding="utf-8"))

        with self.assertRaises(StageMetricsError):
            self.record(1)

        after = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
