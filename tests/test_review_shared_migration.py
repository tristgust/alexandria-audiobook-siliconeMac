from __future__ import annotations

import ast
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import llm_adapter
import review_script


class ReviewSharedMigrationTests(
    unittest.TestCase
):
    def test_compatibility_adapter_is_shared(
        self,
    ):
        self.assertIs(
            review_script
            ._ScriptOpenAIAdapter,
            llm_adapter
            .ScriptOpenAIAdapter,
        )

    def test_compatibility_builder_delegates(
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
            result = (
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

        self.assertEqual(
            result,
            (
                client,
                runtime,
            ),
        )

        builder.assert_called_once()

    def test_main_retains_builder_call(
        self,
    ):
        tree = ast.parse(
            inspect.getsource(
                review_script.main
            )
        )

        calls = [
            node
            for node in ast.walk(tree)
            if (
                isinstance(node, ast.Call)
                and isinstance(
                    node.func,
                    ast.Name,
                )
                and node.func.id
                == "_create_review_client"
            )
        ]

        self.assertEqual(
            len(calls),
            1,
        )

    def test_duplicate_runtime_helpers_absent(
        self,
    ):
        source = inspect.getsource(
            review_script
        )

        forbidden = [
            "def _is_local_ollama_base_url(",
            "def _construct_native_review_runtime(",
            "def _wrap_native_review_runtime(",
            "import inspect",
            "from urllib.parse import urlparse",
            "from openai import OpenAI",
            "from llm_client import LLMClient",
        ]

        for fragment in forbidden:
            with self.subTest(
                fragment=fragment
            ):
                self.assertNotIn(
                    fragment,
                    source,
                )


if __name__ == "__main__":
    unittest.main()
