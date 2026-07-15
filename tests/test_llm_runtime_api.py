from __future__ import annotations

import asyncio
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import llm_client


def make_native_client():
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


class LLMClientLifecycleTests(unittest.TestCase):
    @patch("llm_client.unload_model")
    def test_unload_calls_native_runtime(
        self,
        unload_mock,
    ):
        unload_mock.return_value = {
            "done": True,
            "done_reason": "unload",
        }

        client = make_native_client()
        success, message = client.unload()

        self.assertTrue(success)
        self.assertIn("Unloaded", message)
        self.assertEqual(
            client.last_unload_result[
                "done_reason"
            ],
            "unload",
        )

        unload_mock.assert_called_once_with(
            native_root="http://localhost:11434",
            model="qwen3.5:35b-mlx",
            timeout=300,
        )

    @patch("llm_client.unload_model")
    def test_unload_failure_is_reported(
        self,
        unload_mock,
    ):
        unload_mock.side_effect = RuntimeError(
            "offline"
        )

        client = make_native_client()
        success, message = client.unload()

        self.assertFalse(success)
        self.assertIn("failed", message.lower())
        self.assertIsNone(
            client.last_unload_result
        )

    def test_remote_runtime_does_not_support_lifecycle(
        self,
    ):
        client = llm_client.LLMClient(
            base_url="https://example.test/v1",
            api_key="secret",
            model_name="remote-model",
            backend="openai",
        )

        success, _ = client.unload()
        status = client.status()

        self.assertFalse(success)
        self.assertFalse(
            status["supports_lifecycle"]
        )
        self.assertIsNone(status["loaded"])

    @patch("llm_client.get_running_models")
    def test_status_matches_loaded_model(
        self,
        running_mock,
    ):
        running_mock.return_value = [
            {
                "name": "qwen3.5:35b-mlx",
                "model": "qwen3.5:35b-mlx",
                "size": 40_000,
                "size_vram": 40_000,
                "context_length": 40960,
            }
        ]

        status = make_native_client().status()

        self.assertTrue(status["loaded"])
        self.assertTrue(status["warm"])
        self.assertEqual(
            status["processor_placement"],
            "gpu",
        )
        self.assertEqual(
            status["active_model"]["context_length"],
            40960,
        )

    @patch("llm_client.get_running_models")
    def test_status_reports_mixed_placement(
        self,
        running_mock,
    ):
        running_mock.return_value = [
            {
                "name": "qwen3.5:35b-mlx",
                "size": 100_000,
                "size_vram": 50_000,
            }
        ]

        status = make_native_client().status()

        self.assertEqual(
            status["processor_placement"],
            "mixed",
        )

    @patch("llm_client.get_running_models")
    def test_latest_suffix_matches_model(
        self,
        running_mock,
    ):
        running_mock.return_value = [
            {
                "name": "qwen3.5:35b-mlx:latest",
                "size": 10,
                "size_vram": 10,
            }
        ]

        status = make_native_client().status()

        self.assertTrue(status["loaded"])


class FakeRuntime:
    def __init__(
        self,
        *,
        native=True,
        preload_success=True,
        unload_success=True,
    ):
        self.native_root = (
            "http://localhost:11434"
            if native
            else None
        )
        self.preload_success = preload_success
        self.unload_success = unload_success
        self.last_preload_result = None
        self.last_unload_result = None

    def status(self):
        return {
            "model_name": "qwen3.5:35b-mlx",
            "backend": (
                "ollama-native"
                if self.native_root
                else "openai-compatible"
            ),
            "native_ollama": (
                self.native_root is not None
            ),
            "loaded": True,
            "warm": True,
        }

    def preload(self):
        if not self.preload_success:
            return False, "Ollama preload failed"

        self.last_preload_result = {
            "done": True,
            "done_reason": "load",
            "total_duration": 2_000_000_000,
            "load_duration": 1_500_000_000,
        }

        return (
            True,
            "Preloaded qwen3.5:35b-mlx",
        )

    def unload(self):
        if not self.unload_success:
            return False, "Ollama unload failed"

        self.last_unload_result = {
            "done": True,
            "done_reason": "unload",
            "total_duration": 500_000_000,
        }

        return (
            True,
            "Unloaded qwen3.5:35b-mlx",
        )


class LLMRuntimeAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_module = importlib.import_module(
            "app"
        )

    def setUp(self):
        self.app_module._llm_runtime_activity.update(
            {
                "last_action": None,
                "last_action_success": None,
                "last_action_message": None,
                "last_action_at": None,
                "last_action_elapsed_seconds": None,
                "last_action_metrics": {},
            }
        )

    def test_configured_runtime_reads_llm_section(self):
        config = {
            "llm": {
                "model_name": "configured-model",
                "backend": "auto",
            },
            "prompts": {
                "system_prompt": "preserve",
            },
        }

        sentinel = object()

        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "config.json"
            config_path.write_text(
                json.dumps(config),
                encoding="utf-8",
            )

            with (
                patch.object(
                    self.app_module,
                    "CONFIG_PATH",
                    str(config_path),
                ),
                patch.object(
                    self.app_module,
                    "build_runtime_client",
                    return_value=sentinel,
                ) as builder,
            ):
                result = (
                    self.app_module
                    ._configured_llm_runtime()
                )

        self.assertIs(result, sentinel)

        passed_config = builder.call_args.args[0]

        self.assertEqual(
            passed_config["llm"]["model_name"],
            "configured-model",
        )
        self.assertEqual(
            passed_config["llm"]["context_length"],
            40960,
        )
        self.assertNotIn(
            "prompts",
            passed_config,
        )

    def test_status_endpoint_includes_lifecycle(self):
        runtime = FakeRuntime()

        with patch.object(
            self.app_module,
            "_configured_llm_runtime",
            return_value=runtime,
        ):
            result = asyncio.run(
                self.app_module.get_llm_status()
            )

        self.assertEqual(
            result["backend"],
            "ollama-native",
        )
        self.assertIn("lifecycle", result)

    def test_preload_endpoint_records_metrics(self):
        runtime = FakeRuntime()

        with patch.object(
            self.app_module,
            "_configured_llm_runtime",
            return_value=runtime,
        ):
            result = asyncio.run(
                self.app_module.preload_llm()
            )

        self.assertEqual(
            result["status"],
            "preloaded",
        )
        self.assertEqual(
            result["lifecycle"][
                "last_action"
            ],
            "preload",
        )
        self.assertEqual(
            result["lifecycle"][
                "last_action_metrics"
            ]["load_duration_seconds"],
            1.5,
        )

    def test_unload_endpoint_records_action(self):
        runtime = FakeRuntime()

        with patch.object(
            self.app_module,
            "_configured_llm_runtime",
            return_value=runtime,
        ):
            result = asyncio.run(
                self.app_module.unload_llm()
            )

        self.assertEqual(
            result["status"],
            "unloaded",
        )
        self.assertEqual(
            result["lifecycle"][
                "last_action"
            ],
            "unload",
        )

    def test_remote_preload_is_rejected(self):
        runtime = FakeRuntime(native=False)

        with patch.object(
            self.app_module,
            "_configured_llm_runtime",
            return_value=runtime,
        ):
            with self.assertRaises(
                HTTPException
            ) as context:
                asyncio.run(
                    self.app_module.preload_llm()
                )

        self.assertEqual(
            context.exception.status_code,
            400,
        )

    def test_failed_preload_returns_gateway_error(self):
        runtime = FakeRuntime(
            preload_success=False
        )

        with patch.object(
            self.app_module,
            "_configured_llm_runtime",
            return_value=runtime,
        ):
            with self.assertRaises(
                HTTPException
            ) as context:
                asyncio.run(
                    self.app_module.preload_llm()
                )

        self.assertEqual(
            context.exception.status_code,
            502,
        )
        self.assertEqual(
            self.app_module
            ._llm_runtime_activity[
                "last_action_success"
            ],
            False,
        )


if __name__ == "__main__":
    unittest.main()
