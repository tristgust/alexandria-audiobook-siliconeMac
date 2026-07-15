from __future__ import annotations

import ast
import inspect
import unittest
from unittest.mock import patch

import generate_personas
import llm_adapter
import llm_config


class PersonaSharedMigrationTests(
    unittest.TestCase
):
    def test_compatibility_symbols_are_shared(
        self,
    ):
        expected = {
            "_PersonaOpenAIAdapter": (
                llm_adapter
                .PersonaOpenAIAdapter
            ),
            "_persona_config_bool": (
                llm_config.config_bool
            ),
            "_persona_config_int": (
                llm_config.config_int
            ),
            "_persona_metric_rate": (
                llm_adapter.metric_rate
            ),
            "_print_persona_llm_metrics": (
                llm_adapter
                .print_llm_metrics
            ),
        }

        for name, shared in expected.items():
            with self.subTest(name=name):
                self.assertIs(
                    getattr(
                        generate_personas,
                        name,
                    ),
                    shared,
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
                "generate_personas."
                "build_persona_client"
            ),
            return_value=sentinel,
        ) as builder:
            result = (
                generate_personas
                ._build_persona_llm_client(
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
                generate_personas.main
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
                == "_build_persona_llm_client"
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
            generate_personas
        )

        forbidden = [
            "def _persona_config_bool(",
            "def _persona_config_int(",
            "def _persona_metric_rate(",
            "def _print_persona_llm_metrics(",
            "class _PersonaOpenAIAdapter",
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
