from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import mock_open
from unittest.mock import patch

import generate_script


class FakeRuntimeClient:
    def __init__(self):
        self.calls = []
        self.thinking = False

    def complete_json(self, **kwargs):
        self.calls.append(kwargs)

        return SimpleNamespace(
            data=[
                {
                    "speaker": "NARRATOR",
                    "text": "The door opened.",
                    "instruct": (
                        "Neutral, even narration."
                    ),
                },
                {
                    "speaker": "THE DOCTOR",
                    "text": "No,",
                    "instruct": (
                        "Quiet, reflective agreement."
                    ),
                },
                {
                    "speaker": "NARRATOR",
                    "text": "the Doctor said.",
                    "instruct": (
                        "Neutral, even narration."
                    ),
                },
                {
                    "speaker": "THE DOCTOR",
                    "text": "It rarely is.",
                    "instruct": (
                        "Quiet resignation, dry and weary."
                    ),
                },
            ],
            backend="ollama-native",
            validation_mode="direct",
            metrics={
                "done_reason": "stop",
                "prompt_tokens": 120,
                "prompt_tokens_per_second": 240.0,
                "output_tokens": 55,
                "output_tokens_per_second": 72.0,
            },
        )


class ScriptAdapterTests(unittest.TestCase):
    def setUp(self):
        self.runtime = FakeRuntimeClient()
        self.adapter = (
            generate_script._ScriptOpenAIAdapter(
                self.runtime
            )
        )

    def test_native_script_contract_and_shape(self):
        response = (
            self.adapter
            .chat
            .completions
            .create(
                model="ignored",
                messages=[
                    {
                        "role": "user",
                        "content": "Convert this passage.",
                    }
                ],
                temperature=0.6,
                top_p=0.8,
                presence_penalty=0.0,
                max_tokens=1000,
                extra_body={
                    "top_k": 40,
                    "min_p": 0.05,
                },
            )
        )

        self.assertEqual(
            self.runtime.calls[0]["contract"],
            "script",
        )

        self.assertEqual(
            self.runtime.calls[0]["top_k"],
            40,
        )

        self.assertEqual(
            self.runtime.calls[0]["min_p"],
            0.05,
        )

        parsed = json.loads(
            response.choices[0].message.content
        )

        self.assertIsInstance(parsed, list)

        self.assertEqual(
            parsed[1]["speaker"],
            "THE DOCTOR",
        )

        self.assertEqual(
            response.choices[0].finish_reason,
            "stop",
        )

    def test_banned_tokens_warning_is_once(self):
        output = io.StringIO()

        with redirect_stdout(output):
            for _ in range(2):
                (
                    self.adapter
                    .chat
                    .completions
                    .create(
                        model="ignored",
                        messages=[
                            {
                                "role": "user",
                                "content": "Test.",
                            }
                        ],
                        temperature=0.6,
                        top_p=0.8,
                        presence_penalty=0.0,
                        max_tokens=100,
                        extra_body={
                            "banned_tokens": [
                                "forbidden"
                            ],
                        },
                    )
                )

        rendered = output.getvalue()

        self.assertEqual(
            rendered.count(
                "banned_tokens option"
            ),
            1,
        )

    def test_legacy_client_delegation(self):
        legacy_client = MagicMock()

        legacy_response = SimpleNamespace(
            choices=[],
        )

        (
            legacy_client
            .chat
            .completions
            .create
            .return_value
        ) = legacy_response

        adapter = (
            generate_script._ScriptOpenAIAdapter(
                self.runtime,
                legacy_client=legacy_client,
            )
        )

        response = (
            adapter
            .chat
            .completions
            .create(
                model="legacy-model",
                messages=[
                    {
                        "role": "user",
                        "content": "Test.",
                    }
                ],
                temperature=0.4,
                top_p=0.7,
                presence_penalty=0.1,
                max_tokens=500,
                extra_body={
                    "top_k": 20,
                },
            )
        )

        self.assertIs(
            response,
            legacy_response,
        )

        (
            legacy_client
            .chat
            .completions
            .create
            .assert_called_once()
        )

        kwargs = (
            legacy_client
            .chat
            .completions
            .create
            .call_args
            .kwargs
        )

        self.assertEqual(
            kwargs["model"],
            "legacy-model",
        )

        self.assertEqual(
            kwargs["extra_body"]["top_k"],
            20,
        )

        self.assertEqual(
            len(self.runtime.calls),
            0,
        )


class ScriptConfigurationTests(unittest.TestCase):
    def test_native_runtime_configuration(self):
        runtime, adapter = (
            generate_script._build_script_llm_client(
                {
                    "llm": {
                        "base_url": (
                            "http://localhost:11434/v1"
                        ),
                        "api_key": "local",
                        "model_name": (
                            "qwen3.5:35b-mlx"
                        ),
                        "backend": "auto",
                        "context_length": 40960,
                        "keep_alive": -1,
                        "thinking": False,
                        "structured_output": True,
                        "corrective_retry": True,
                    }
                }
            )
        )

        self.assertEqual(
            runtime.backend,
            "ollama-native",
        )

        self.assertEqual(
            runtime.model_name,
            "qwen3.5:35b-mlx",
        )

        self.assertEqual(
            runtime.context_length,
            40960,
        )

        self.assertFalse(runtime.thinking)
        self.assertTrue(runtime.structured_output)
        self.assertTrue(runtime.corrective_retry)

        self.assertIs(
            adapter.runtime_client,
            runtime,
        )

        self.assertIsNone(
            adapter.legacy_client
        )

    @patch("openai.OpenAI")
    def test_remote_runtime_keeps_legacy_client(
        self,
        openai_mock,
    ):
        runtime, adapter = (
            generate_script._build_script_llm_client(
                {
                    "llm": {
                        "base_url": (
                            "https://example.invalid/v1"
                        ),
                        "api_key": "test-key",
                        "model_name": "remote-model",
                        "backend": "auto",
                    }
                }
            )
        )

        self.assertEqual(
            runtime.backend,
            "openai-compatible",
        )

        openai_mock.assert_called_once_with(
            base_url=(
                "https://example.invalid/v1"
            ),
            api_key="test-key",
        )

        self.assertIsNotNone(
            adapter.legacy_client
        )


class ProcessChunkIntegrationTests(unittest.TestCase):
    def test_existing_process_chunk_uses_adapter_and_context(
        self,
    ):
        runtime = FakeRuntimeClient()

        adapter = (
            generate_script._ScriptOpenAIAdapter(
                runtime
            )
        )

        previous_entries = [
            {
                "speaker": "THE DOCTOR",
                "text": "We should proceed.",
                "instruct": "Measured authority.",
            },
            {
                "speaker": "NARRATOR",
                "text": "He approached the doorway.",
                "instruct": (
                    "Neutral, even narration."
                ),
            },
        ]

        fake_log = mock_open()

        with (
            patch(
                "builtins.open",
                fake_log,
            ),
            patch(
                "generate_script.os.makedirs"
            ),
        ):
            entries = generate_script.process_chunk(
                adapter,
                "qwen3.5:35b-mlx",
                (
                    'The door opened. '
                    '"No," the Doctor said. '
                    '"It rarely is."'
                ),
                2,
                3,
                previous_entries=previous_entries,
                max_retries=0,
                system_prompt=(
                    "Return an audiobook script."
                ),
                user_prompt_template=(
                    "{context}\n\n"
                    "SOURCE TEXT:\n"
                    "{chunk}"
                ),
                max_tokens=1000,
                temperature=0.6,
                top_p=0.8,
                top_k=40,
                min_p=0.05,
                presence_penalty=0.0,
                banned_tokens=[],
            )

        self.assertEqual(
            len(entries),
            4,
        )

        self.assertEqual(
            [
                entry["speaker"]
                for entry in entries
            ],
            [
                "NARRATOR",
                "THE DOCTOR",
                "NARRATOR",
                "THE DOCTOR",
            ],
        )

        self.assertEqual(
            entries[1]["text"],
            "No,",
        )

        self.assertEqual(
            entries[2]["text"],
            "the Doctor said.",
        )

        self.assertEqual(
            entries[3]["text"],
            "It rarely is.",
        )

        call = runtime.calls[0]

        user_prompt = call[
            "messages"
        ][1]["content"]

        self.assertIn(
            "Characters in this book: THE DOCTOR",
            user_prompt,
        )

        self.assertIn(
            "Previous section ended with:",
            user_prompt,
        )

        self.assertIn(
            "The door opened.",
            user_prompt,
        )

        self.assertEqual(
            call["contract"],
            "script",
        )


class ScriptMetricsTests(unittest.TestCase):
    def test_metrics_output(self):
        result = SimpleNamespace(
            backend="ollama-native",
            validation_mode="direct",
            metrics={
                "prompt_tokens": 100,
                "prompt_tokens_per_second": 200.0,
                "output_tokens": 25,
                "output_tokens_per_second": 72.0,
            },
        )

        output = io.StringIO()

        with redirect_stdout(output):
            generate_script._print_script_llm_metrics(
                result
            )

        rendered = output.getvalue()

        self.assertIn(
            "backend=ollama-native",
            rendered,
        )

        self.assertIn(
            "validation=direct",
            rendered,
        )

        self.assertIn(
            "72.00 tok/s",
            rendered,
        )


if __name__ == "__main__":
    unittest.main()
