from __future__ import annotations

import math
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from discover_character_roster import _metric_seconds
from generate_script import (
    _accumulate_script_response_timing,
    _new_script_unit_timing,
)
from stage_metrics import (
    StageMetricsError,
    prepare_stage_metrics,
    read_stage_metrics,
    record_stage_event,
    record_stage_unit,
    summarize_stage_metrics,
)


class StageMetricsAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "script_metrics.json"

    def prepare(
        self,
        *,
        baseline: int = 0,
        total_units: int = 3,
        started_at: str = "2026-07-18T12:00:00Z",
    ) -> None:
        prepare_stage_metrics(
            self.path,
            stage="script",
            run_id="run-one",
            total_units=total_units,
            baseline_completed_units=baseline,
            started_at=started_at,
        )

    def record(
        self,
        index: int,
        *,
        completed_at: str | None = None,
        max_units: int = 1000,
        phases_seconds: dict[str, float] | None = None,
    ) -> None:
        record_stage_unit(
            self.path,
            stage="script",
            index=index,
            input_characters=100,
            output_items=1,
            attempts=1,
            corrective_retries=0,
            prompt_tokens=10,
            output_tokens=5,
            validation_mode="direct",
            phases_seconds=phases_seconds or {"unit_wall": 1.0},
            completed_at=(
                completed_at or f"2026-07-18T12:00:0{index}Z"
            ),
            max_units=max_units,
        )

    def test_unit_record_is_rejected_when_prior_index_is_missing(self) -> None:
        # Given
        self.prepare()

        # When / Then
        with self.assertRaisesRegex(StageMetricsError, "contiguous"):
            self.record(2)

        result = read_stage_metrics(self.path, stage="script")
        self.assertEqual(result["document"]["units"], [])

    def test_resume_rebaselines_after_unrecorded_checkpoint_timing(self) -> None:
        # Given
        self.prepare()
        self.record(1)

        # When
        resumed = prepare_stage_metrics(
            self.path,
            stage="script",
            run_id="run-one",
            total_units=3,
            baseline_completed_units=2,
        )

        # Then
        self.assertEqual(resumed["baseline_completed_units"], 2)
        self.assertEqual(resumed["units"], [])

    def test_incomplete_metrics_cannot_report_complete(self) -> None:
        # Given
        self.prepare()
        self.record(1)

        # When / Then
        with self.assertRaisesRegex(StageMetricsError, "(?i)complete"):
            record_stage_event(
                self.path,
                stage="script",
                event="finalization",
                phases_seconds={"finalization": 0.1},
                mark_complete=True,
            )

        result = read_stage_metrics(self.path, stage="script")
        self.assertEqual(result["summary"]["status"], "running")
        self.assertEqual(result["summary"]["remaining_units"], 2)

    def test_nonfinite_adapter_metrics_fall_back_to_measured_wall(self) -> None:
        # Given
        timing = _new_script_unit_timing()
        response = SimpleNamespace(
            alexandria_metrics={
                "request_wall_seconds": math.inf,
                "total_seconds": math.nan,
                "generation_seconds": math.inf,
            },
            alexandria_validation_mode="direct",
        )

        # When
        _accumulate_script_response_timing(
            timing,
            response,
            measured_request_seconds=0.25,
            outer_retry=False,
        )

        # Then
        phases = timing["phases_seconds"]
        self.assertEqual(phases["request_wall"], 0.25)
        self.assertTrue(all(math.isfinite(value) for value in phases.values()))

    def test_nonfinite_roster_metrics_are_ignored(self) -> None:
        # Given / When / Then
        self.assertEqual(_metric_seconds(math.inf), 0.0)
        self.assertEqual(_metric_seconds(math.nan), 0.0)

    def test_stale_running_metrics_cannot_publish_a_reliable_eta(self) -> None:
        # Given
        self.prepare(
            total_units=5,
            started_at="2020-01-01T00:00:00Z",
        )
        for index in range(1, 4):
            self.record(
                index,
                completed_at=f"2020-01-01T00:00:0{index}Z",
            )
        document = read_stage_metrics(self.path, stage="script")["document"]

        # When
        summary = summarize_stage_metrics(
            document,
            now=datetime(2026, 7, 18, 12, 1, tzinfo=timezone.utc),
        )

        # Then
        self.assertFalse(summary["eta_reliable"], summary)
        self.assertIsNone(summary["eta_seconds"])
        self.assertEqual(summary["eta_reason"], "stale_running_state")

    def test_malformed_or_future_update_cannot_publish_reliable_eta(self) -> None:
        # Given
        self.prepare(total_units=5)
        for index in range(1, 4):
            self.record(index)
        document = read_stage_metrics(self.path, stage="script")["document"]
        now = datetime(2026, 7, 18, 12, 1, tzinfo=timezone.utc)

        # When / Then
        for updated_at in ("not-a-date", "2027-01-01T00:00:00Z"):
            with self.subTest(updated_at=updated_at):
                candidate = dict(document)
                candidate["updated_at"] = updated_at
                summary = summarize_stage_metrics(candidate, now=now)
                self.assertFalse(summary["eta_reliable"], summary)
                self.assertIsNone(summary["eta_seconds"])
                self.assertEqual(
                    summary["eta_reason"],
                    "stale_running_state",
                )

    def test_unit_gap_is_rejected_before_history_capping(self) -> None:
        # Given
        self.prepare(total_units=4)
        self.record(1, max_units=1)
        before = self.path.read_bytes()

        # When / Then
        with self.assertRaisesRegex(StageMetricsError, "contiguous"):
            self.record(3, max_units=1)
        self.assertEqual(self.path.read_bytes(), before)

    def test_unit_without_wall_timing_is_rejected(self) -> None:
        # Given
        self.prepare()

        # When / Then
        with self.assertRaisesRegex(StageMetricsError, "unit_wall"):
            self.record(1, phases_seconds={"request_wall": 1.0})

    def test_prepare_rejects_malformed_timestamp_before_write(self) -> None:
        # When / Then
        with self.assertRaisesRegex(StageMetricsError, "timestamp"):
            self.prepare(started_at="not-a-date")
        self.assertFalse(self.path.exists())

    def test_record_rejects_malformed_timestamp_without_mutation(self) -> None:
        # Given
        self.prepare()
        before = self.path.read_bytes()

        # When / Then
        with self.assertRaisesRegex(StageMetricsError, "timestamp"):
            self.record(1, completed_at="not-a-date")
        self.assertEqual(self.path.read_bytes(), before)

    def test_legacy_units_without_wall_time_do_not_inflate_rates_or_eta(self) -> None:
        # Given
        self.prepare(total_units=6)
        for index in range(1, 6):
            self.record(index)
        document = read_stage_metrics(self.path, stage="script")["document"]
        self.assertIsNotNone(document)
        for unit in document["units"][:2]:
            unit["phases_seconds"].pop("unit_wall")

        # When
        summary = summarize_stage_metrics(
            document,
            now=datetime(2026, 7, 18, 12, 1, tzinfo=timezone.utc),
        )

        # Then
        self.assertEqual(summary["units_per_minute"], 60.0)
        self.assertEqual(summary["characters_per_second"], 100.0)
        self.assertFalse(summary["eta_reliable"])
        self.assertEqual(summary["eta_reason"], "incomplete_timing_samples")


if __name__ == "__main__":
    unittest.main()
