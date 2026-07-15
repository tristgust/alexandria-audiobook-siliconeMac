from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import review_script
import llm_adapter


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
    def test_native_builder_delegates(
        self,
    ):
        client = object()
        runtime = SimpleNamespace(
            backend="ollama-native",
        )

        with patch(
            "review_script.build_review_client",
            return_value=(
                client,
                runtime,
            ),
        ) as builder:
            actual_client, actual_runtime = (
                review_script
                ._create_review_client(
                    "http://localhost:11434/v1",
                    "local",
                    "qwen3.5:35b-mlx",
                    {
                        "thinking": False,
                    },
                )
            )

        self.assertIs(
            actual_client,
            client,
        )

        self.assertIs(
            actual_runtime,
            runtime,
        )

        builder.assert_called_once_with(
            "http://localhost:11434/v1",
            "local",
            "qwen3.5:35b-mlx",
            {
                "thinking": False,
            },
        )

    def test_remote_builder_preserves_marker(
        self,
    ):
        client = object()
        runtime = SimpleNamespace(
            backend="openai-compatible",
        )

        with patch(
            "review_script.build_review_client",
            return_value=(
                client,
                runtime,
            ),
        ):
            actual_client, native_runtime = (
                review_script
                ._create_review_client(
                    "https://example.test/v1",
                    "secret",
                    "remote-model",
                    {},
                )
            )

        self.assertIs(
            actual_client,
            client,
        )

        self.assertIsNone(
            native_runtime
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
