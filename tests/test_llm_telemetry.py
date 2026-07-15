from __future__ import annotations

import asyncio
import importlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import llm_client
import llm_telemetry


def make_client():
    return llm_client.LLMClient(
        base_url="http://localhost:11434/v1",
        api_key="local",
        model_name="qwen3.5:35b-mlx",
        backend="auto",
        context_length=40960,
        keep_alive=-1,
        thinking=False,
        structured_output=True,
        corrective_retry=True,
        timeout=1800,
    )


class TelemetryStorageTests(unittest.TestCase):
    def test_success_snapshot_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "runtime.json"

            success = (
                llm_telemetry
                .record_llm_request(
                    model_name="qwen3.5:35b-mlx",
                    contract="script",
                    backend="ollama-native",
                    validation_mode=(
                        "corrective_retry"
                    ),
                    metrics={
                        "prompt_tokens": 100,
                        "prompt_tokens_per_second": 200.0,
                        "output_tokens": 25,
                        "output_tokens_per_second": 72.0,
                        "initial_validation_error": (
                            "Missing required field"
                        ),
                    },
                    request_elapsed_seconds=2.5,
                    thinking=False,
                    structured_output=True,
                    corrective_retry=True,
                    path=path,
                )
            )

            snapshot = (
                llm_telemetry
                .read_llm_telemetry(
                    path=path
                )
            )

        self.assertTrue(success)

        latest = snapshot["latest_request"]

        self.assertEqual(
            latest["status"],
            "success",
        )
        self.assertEqual(
            latest["contract"],
            "script",
        )
        self.assertEqual(
            latest["validation_mode"],
            "corrective_retry",
        )
        self.assertTrue(
            latest["corrective_retry_used"]
        )
        self.assertEqual(
            latest["retry_reason"],
            "Missing required field",
        )
        self.assertEqual(
            latest["metrics"]["output_tokens"],
            25,
        )

    def test_failure_snapshot_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "runtime.json"

            llm_telemetry.record_llm_failure(
                model_name="model",
                contract="persona",
                backend="ollama-native",
                request_elapsed_seconds=1.25,
                error="Request timed out",
                thinking=False,
                structured_output=True,
                corrective_retry=True,
                path=path,
            )

            snapshot = (
                llm_telemetry
                .read_llm_telemetry(
                    path=path
                )
            )

        latest = snapshot["latest_request"]

        self.assertEqual(
            latest["status"],
            "error",
        )
        self.assertEqual(
            latest["retry_reason"],
            "Request timed out",
        )
        self.assertEqual(
            latest["request_elapsed_seconds"],
            1.25,
        )

    def test_missing_file_returns_empty_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "missing.json"

            snapshot = (
                llm_telemetry
                .read_llm_telemetry(
                    path=path
                )
            )

        self.assertIsNone(
            snapshot["latest_request"]
        )
        self.assertIsNone(
            snapshot["telemetry_error"]
        )


class LLMClientTelemetryTests(unittest.TestCase):
    def test_successful_completion_records_request(self):
        client = make_client()

        result = SimpleNamespace(
            backend="ollama-native",
            validation_mode="direct",
            metrics={
                "prompt_tokens": 20,
                "output_tokens": 10,
            },
        )

        with (
            patch.object(
                client,
                "_complete_native",
                return_value=result,
            ),
            patch(
                "llm_client.record_llm_request"
            ) as recorder,
        ):
            actual = client.complete_json(
                messages=[],
                contract="persona",
                temperature=0.3,
                max_tokens=400,
            )

        self.assertIs(actual, result)
        recorder.assert_called_once()

        kwargs = recorder.call_args.kwargs

        self.assertEqual(
            kwargs["model_name"],
            "qwen3.5:35b-mlx",
        )
        self.assertEqual(
            kwargs["contract"],
            "persona",
        )
        self.assertEqual(
            kwargs["validation_mode"],
            "direct",
        )
        self.assertGreaterEqual(
            kwargs["request_elapsed_seconds"],
            0,
        )

    def test_failed_completion_records_failure(self):
        client = make_client()

        with (
            patch.object(
                client,
                "_complete_native",
                side_effect=RuntimeError(
                    "offline"
                ),
            ),
            patch(
                "llm_client.record_llm_failure"
            ) as recorder,
        ):
            with self.assertRaises(
                RuntimeError
            ):
                client.complete_json(
                    messages=[],
                    contract="persona",
                    temperature=0.3,
                    max_tokens=400,
                )

        recorder.assert_called_once()

        self.assertEqual(
            recorder.call_args.kwargs["error"],
            "offline",
        )


class TelemetryStatusAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_module = importlib.import_module(
            "app"
        )

    def test_status_includes_process_shared_telemetry(
        self,
    ):
        runtime = SimpleNamespace(
            status=lambda: {
                "model_name": "model",
                "backend": "ollama-native",
            }
        )

        telemetry = {
            "schema_version": 1,
            "latest_request": {
                "contract": "script",
            },
            "telemetry_error": None,
        }

        with (
            patch.object(
                self.app_module,
                "_configured_llm_runtime",
                return_value=runtime,
            ),
            patch.object(
                self.app_module,
                "read_llm_telemetry",
                return_value=telemetry,
            ) as reader,
        ):
            result = asyncio.run(
                self.app_module.get_llm_status()
            )

        self.assertEqual(
            result["telemetry"],
            telemetry,
        )
        reader.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
