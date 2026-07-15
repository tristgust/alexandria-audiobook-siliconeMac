from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import review_script


class FakeRuntime:
    def __init__(
        self,
        base_url,
        api_key,
        model_name,
        timeout=0,
        context_length=0,
        keep_alive=0,
        think=True,
        corrective_retry=False,
    ):
        self.kwargs = {
            "base_url": base_url,
            "api_key": api_key,
            "model_name": model_name,
            "timeout": timeout,
            "context_length": context_length,
            "keep_alive": keep_alive,
            "think": think,
            "corrective_retry": corrective_retry,
        }

        self.preloaded = False

    def preload(self):
        self.preloaded = True


class FakeAdapter:
    def __init__(
        self,
        runtime_client,
    ):
        self.runtime_client = (
            runtime_client
        )


class ReviewClientSelectionTests(
    unittest.TestCase
):
    def test_local_ollama_uses_native_runtime(self):
        config = {
            "timeout": 321,
            "context_length": 16384,
            "keep_alive": -1,
            "thinking": False,
            "corrective_retry": True,
        }

        with (
            patch(
                "review_script.LLMClient",
                FakeRuntime,
            ),
            patch(
                "review_script._ScriptOpenAIAdapter",
                FakeAdapter,
            ),
            patch(
                "review_script.OpenAI",
            ) as legacy,
        ):
            client, runtime = (
                review_script
                ._create_review_client(
                    "http://localhost:11434/v1",
                    "local",
                    "qwen3.5:35b-mlx",
                    config,
                )
            )

        self.assertIsInstance(
            client,
            FakeAdapter,
        )

        self.assertIsInstance(
            runtime,
            FakeRuntime,
        )

        self.assertTrue(
            runtime.preloaded
        )

        self.assertEqual(
            runtime.kwargs[
                "model_name"
            ],
            "qwen3.5:35b-mlx",
        )

        self.assertEqual(
            runtime.kwargs[
                "context_length"
            ],
            16384,
        )

        self.assertFalse(
            runtime.kwargs["think"]
        )

        legacy.assert_not_called()

    def test_remote_endpoint_keeps_openai_client(
        self,
    ):
        sentinel = object()

        with patch(
            "review_script.OpenAI",
            return_value=sentinel,
        ) as legacy:
            client, runtime = (
                review_script
                ._create_review_client(
                    "https://example.test/v1",
                    "secret",
                    "remote-model",
                    {},
                )
            )

        self.assertIs(
            client,
            sentinel,
        )

        self.assertIsNone(runtime)

        legacy.assert_called_once_with(
            base_url=(
                "https://example.test/v1"
            ),
            api_key="secret",
        )


class FakeCompletionClient:
    def __init__(self, entries):
        self.entries = entries
        self.calls = []

        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=self._create,
            )
        )

    def _create(self, **kwargs):
        self.calls.append(kwargs)

        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            self.entries,
                            ensure_ascii=False,
                        )
                    ),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=50,
            ),
        )


class ReviewBatchCompatibilityTests(
    unittest.TestCase
):
    def test_review_batch_accepts_structured_adapter(
        self,
    ):
        entries = [
            {
                "speaker": "NARRATOR",
                "text": "The door opened.",
                "instruct": (
                    "Neutral, even narration."
                ),
            },
            {
                "speaker": "MARCUS",
                "text": "Wait.",
                "instruct": (
                    "Firm, restrained urgency."
                ),
            },
        ]

        client = FakeCompletionClient(
            entries
        )

        with (
            patch(
                "review_script.os.makedirs",
            ),
            patch(
                "builtins.open",
            ),
        ):
            result = review_script.review_batch(
                client,
                "qwen3.5:35b-mlx",
                entries,
                1,
                1,
                previous_tail=[
                    {
                        "speaker": "NARRATOR",
                        "text": "Earlier.",
                        "instruct": (
                            "Neutral narration."
                        ),
                    }
                ],
                source_context=(
                    "Context-only neighboring entries."
                ),
                max_retries=0,
            )

        self.assertEqual(
            result,
            entries,
        )

        self.assertEqual(
            len(client.calls),
            1,
        )

        messages = client.calls[
            0
        ]["messages"]

        self.assertIn(
            "Previous batch ended with:",
            messages[1]["content"],
        )

        self.assertIn(
            "ADDITIONAL REVIEW CONTEXT:",
            messages[1]["content"],
        )

        self.assertEqual(
            client.calls[0]["model"],
            "qwen3.5:35b-mlx",
        )


if __name__ == "__main__":
    unittest.main()
