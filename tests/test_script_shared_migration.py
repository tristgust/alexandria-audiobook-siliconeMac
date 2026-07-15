from __future__ import annotations

import ast
import inspect
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import generate_script
import llm_adapter
import llm_config


class ScriptSharedMigrationTests(
    unittest.TestCase
):
    def test_compatibility_symbols_are_shared(
        self,
    ):
        expected = {
            "_ScriptOpenAIAdapter": (
                llm_adapter
                .ScriptOpenAIAdapter
            ),
            "_script_config_bool": (
                llm_config.config_bool
            ),
            "_script_config_int": (
                llm_config.config_int
            ),
            "_script_metric_rate": (
                llm_adapter.metric_rate
            ),
        }

        for name, shared in expected.items():
            with self.subTest(name=name):
                self.assertIs(
                    getattr(
                        generate_script,
                        name,
                    ),
                    shared,
                )

    def test_metric_wrapper_uses_shared_formatter(
        self,
    ):
        result = type(
            "Result",
            (),
            {
                "backend": "ollama-native",
                "validation_mode": "direct",
                "metrics": {
                    "prompt_tokens": 100,
                    "output_tokens": 25,
                    "prompt_tokens_per_second": 200,
                    "output_tokens_per_second": 50,
                },
            },
        )()

        output = io.StringIO()

        with redirect_stdout(output):
            generate_script._print_script_llm_metrics(
                result
            )

        self.assertIn(
            (
                "Structured response: "
                "backend=ollama-native"
            ),
            output.getvalue(),
        )

    def test_compatibility_builder_delegates(
        self,
    ):
        config = {
            "llm": {
                "model_name": (
                    "qwen3.5:35b-mlx"
                )
            }
        }

        sentinel = (
            object(),
            object(),
        )

        with patch(
            (
                "generate_script."
                "build_script_client"
            ),
            return_value=sentinel,
        ) as builder:
            result = (
                generate_script
                ._build_script_llm_client(
                    config
                )
            )

        self.assertIs(
            result,
            sentinel,
        )

        builder.assert_called_once_with(
            config
        )

    def test_main_retains_existing_builder_call(
        self,
    ):
        tree = ast.parse(
            inspect.getsource(
                generate_script.main
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
                == "_build_script_llm_client"
            )
        ]

        self.assertEqual(
            len(calls),
            1,
        )

    def test_duplicate_implementations_are_absent(
        self,
    ):
        source = inspect.getsource(
            generate_script
        )

        forbidden = [
            "def _script_config_bool(",
            "def _script_config_int(",
            "def _script_metric_rate(",
            "class _ScriptOpenAIAdapter",
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
