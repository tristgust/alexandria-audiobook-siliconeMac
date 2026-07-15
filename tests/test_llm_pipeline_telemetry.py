from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import generate_script
import llm_telemetry
import review_script


class PipelineTelemetryStorageTests(
    unittest.TestCase
):
    def test_pipeline_result_merges_into_request(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "runtime.json"

            llm_telemetry.record_llm_request(
                model_name="qwen3.5:35b-mlx",
                contract="script",
                backend="ollama-native",
                validation_mode="direct",
                metrics={
                    "prompt_tokens": 100,
                    "output_tokens": 25,
                },
                request_elapsed_seconds=2.0,
                thinking=False,
                structured_output=True,
                corrective_retry=True,
                path=path,
            )

            updated = (
                llm_telemetry
                .record_llm_pipeline_result(
                    stage="script",
                    unit_kind="chunk",
                    unit_index=2,
                    unit_total=5,
                    outer_attempt=2,
                    unit_elapsed_seconds=4.5,
                    audit_kind=(
                        "script_fidelity"
                    ),
                    audit_result={
                        "passed": False,
                        "blocking_count": 1,
                        "warning_count": 0,
                        "metrics": {},
                        "issues": [
                            {
                                "code": "missing_text",
                                "severity": "blocking",
                                "message": (
                                    "Source text was omitted"
                                ),
                            }
                        ],
                    },
                    expected_contract="script",
                    path=path,
                )
            )

            snapshot = (
                llm_telemetry
                .read_llm_telemetry(
                    path=path
                )
            )

        self.assertTrue(updated)

        latest = snapshot["latest_request"]
        pipeline = latest["pipeline"]

        self.assertEqual(
            latest["metrics"]["prompt_tokens"],
            100,
        )
        self.assertEqual(
            pipeline["stage"],
            "script",
        )
        self.assertEqual(
            pipeline["unit_kind"],
            "chunk",
        )
        self.assertEqual(
            pipeline["outer_attempt"],
            2,
        )
        self.assertTrue(
            pipeline["outer_retry_used"]
        )
        self.assertFalse(
            pipeline["audit_passed"]
        )
        self.assertEqual(
            pipeline["outcome"],
            "blocked",
        )
        self.assertIn(
            "missing_text",
            pipeline["retry_reason"],
        )
        self.assertEqual(
            pipeline["unit_elapsed_seconds"],
            4.5,
        )

    def test_pipeline_update_requires_request(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "missing.json"

            updated = (
                llm_telemetry
                .record_llm_pipeline_result(
                    stage="review",
                    unit_kind="batch",
                    unit_index=1,
                    unit_total=1,
                    outer_attempt=1,
                    unit_elapsed_seconds=1.0,
                    audit_kind="review_text",
                    audit_result={
                        "passed": True,
                    },
                    expected_contract="script",
                    path=path,
                )
            )

        self.assertFalse(updated)

    def test_contract_mismatch_is_not_updated(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "runtime.json"

            llm_telemetry.record_llm_request(
                model_name="model",
                contract="persona",
                backend="ollama-native",
                validation_mode="direct",
                metrics={},
                request_elapsed_seconds=1.0,
                thinking=False,
                structured_output=True,
                corrective_retry=True,
                path=path,
            )

            updated = (
                llm_telemetry
                .record_llm_pipeline_result(
                    stage="script",
                    unit_kind="chunk",
                    unit_index=1,
                    unit_total=1,
                    outer_attempt=1,
                    unit_elapsed_seconds=1.0,
                    audit_kind=(
                        "script_fidelity"
                    ),
                    audit_result={
                        "passed": True,
                    },
                    expected_contract="script",
                    path=path,
                )
            )

        self.assertFalse(updated)


class PipelineAuditIntegrationTests(
    unittest.TestCase
):
    def test_script_audit_records_pipeline_result(self):
        entries = [
            {
                "speaker": "NARRATOR",
                "text": "The door opened.",
                "instruct": (
                    "Neutral, even narration."
                ),
            }
        ]

        with (
            patch.object(
                generate_script,
                "_record_fidelity_audit",
            ),
            patch.object(
                generate_script,
                "record_llm_pipeline_result",
            ) as recorder,
        ):
            result = (
                generate_script
                ._audit_candidate(
                    "The door opened.",
                    entries,
                    2,
                    4,
                    0,
                    time.perf_counter(),
                )
            )

        self.assertTrue(result.passed)
        recorder.assert_called_once()

        kwargs = recorder.call_args.kwargs

        self.assertEqual(
            kwargs["stage"],
            "script",
        )
        self.assertEqual(
            kwargs["unit_kind"],
            "chunk",
        )
        self.assertEqual(
            kwargs["unit_index"],
            2,
        )
        self.assertEqual(
            kwargs["audit_kind"],
            "script_fidelity",
        )
        self.assertTrue(
            kwargs["audit_result"]["passed"]
        )

    def test_review_audit_records_pipeline_result(self):
        entries = [
            {
                "speaker": "NARRATOR",
                "text": "The door opened.",
                "instruct": (
                    "Neutral, even narration."
                ),
            }
        ]

        with (
            patch.object(
                review_script,
                "_record_review_text_audit",
            ),
            patch.object(
                review_script,
                "record_llm_pipeline_result",
            ) as recorder,
        ):
            result = (
                review_script
                ._audit_review_candidate(
                    entries,
                    entries,
                    1,
                    3,
                    1,
                    time.perf_counter(),
                )
            )

        self.assertTrue(result.passed)
        recorder.assert_called_once()

        kwargs = recorder.call_args.kwargs

        self.assertEqual(
            kwargs["stage"],
            "review",
        )
        self.assertEqual(
            kwargs["unit_kind"],
            "batch",
        )
        self.assertEqual(
            kwargs["outer_attempt"],
            2,
        )
        self.assertEqual(
            kwargs["audit_kind"],
            "review_text",
        )
        self.assertTrue(
            kwargs["audit_result"]["passed"]
        )


if __name__ == "__main__":
    unittest.main()
