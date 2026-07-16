from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import llm_adapter
import llm_config


class FakeResult:
    def __init__(
        self,
        data,
        *,
        backend="ollama-native",
        validation_mode="direct",
    ):
        self.data = data
        self.backend = backend
        self.validation_mode = (
            validation_mode
        )

        self.metrics = {
            "done_reason": "stop",
            "prompt_tokens": 100,
            "output_tokens": 25,
            "prompt_tokens_per_second": 200,
            "output_tokens_per_second": 50,
        }


class FakeRuntime:
    def __init__(
        self,
        data=None,
        *,
        backend="ollama-native",
    ):
        self.data = (
            data
            if data is not None
            else []
        )

        self.backend = backend
        self.base_url = (
            "http://localhost:11434/v1"
        )
        self.api_key = "local"
        self.calls = []
        self.preload_calls = 0

    def complete_json(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)

        return FakeResult(
            self.data,
            backend=self.backend,
        )

    def preload(self):
        self.preload_calls += 1

        return True, "Preloaded"


class ConfigParsingTests(unittest.TestCase):
    def test_config_bool(self):
        truthy = [
            True,
            1,
            "1",
            "true",
            "YES",
            "on",
            "enabled",
        ]

        falsy = [
            False,
            0,
            "0",
            "false",
            "NO",
            "off",
            "disabled",
            "",
        ]

        for value in truthy:
            with self.subTest(value=value):
                self.assertTrue(
                    llm_config.config_bool(
                        value
                    )
                )

        for value in falsy:
            with self.subTest(value=value):
                self.assertFalse(
                    llm_config.config_bool(
                        value,
                        True,
                    )
                )

        self.assertTrue(
            llm_config.config_bool(
                "unknown",
                True,
            )
        )

    def test_config_int(self):
        self.assertEqual(
            llm_config.config_int(
                "40960",
                10,
            ),
            40960,
        )

        self.assertEqual(
            llm_config.config_int(
                "invalid",
                10,
            ),
            10,
        )


class RuntimeSettingsTests(unittest.TestCase):
    def test_defaults(self):
        settings = (
            llm_config
            .runtime_settings_from_config(
                {}
            )
        )

        self.assertEqual(
            settings.base_url,
            (
                "http://localhost:"
                "11434/v1"
            ),
        )

        self.assertEqual(
            settings.model_name,
            llm_config.DEFAULT_MODEL_NAME,
        )

        self.assertEqual(
            settings.context_length,
            40960,
        )

        self.assertFalse(
            settings.thinking
        )

        self.assertTrue(
            settings.structured_output
        )

        self.assertTrue(
            settings.corrective_retry
        )

    def test_overrides(self):
        settings = (
            llm_config
            .runtime_settings_from_config(
                {
                    "llm": {
                        "base_url": (
                            "https://example.test/v1"
                        ),
                        "api_key": "secret",
                        "model_name": "model-x",
                        "backend": "openai",
                        "context_length": "8192",
                        "keep_alive": "10m",
                        "thinking": "yes",
                        "structured_output": "no",
                        "corrective_retry": 0,
                        "timeout": "90",
                    }
                }
            )
        )

        self.assertEqual(
            settings.model_name,
            "model-x",
        )

        self.assertEqual(
            settings.backend,
            "openai",
        )

        self.assertEqual(
            settings.context_length,
            8192,
        )

        self.assertEqual(
            settings.keep_alive,
            "10m",
        )

        self.assertTrue(
            settings.thinking
        )

        self.assertFalse(
            settings.structured_output
        )

        self.assertFalse(
            settings.corrective_retry
        )

        self.assertEqual(
            settings.timeout,
            90,
        )

    def test_build_runtime_client_kwargs(self):
        captured = {}

        class FakeClient:
            def __init__(
                self,
                **kwargs,
            ):
                captured.update(kwargs)

        with patch(
            "llm_config.LLMClient",
            FakeClient,
        ):
            result = (
                llm_config
                .build_runtime_client(
                    {
                        "llm": {
                            "model_name": (
                                "qwen3.5:35b-mlx"
                            ),
                            "thinking": False,
                        }
                    }
                )
            )

        self.assertIsInstance(
            result,
            FakeClient,
        )

        self.assertEqual(
            captured["model_name"],
            "qwen3.5:35b-mlx",
        )

        self.assertFalse(
            captured["thinking"]
        )

        self.assertEqual(
            set(captured),
            {
                "base_url",
                "api_key",
                "model_name",
                "backend",
                "context_length",
                "keep_alive",
                "thinking",
                "structured_output",
                "corrective_retry",
                "timeout",
            },
        )


class PersonaAdapterTests(unittest.TestCase):
    def test_contract_selection(self):
        cases = [
            (
                "Speakers to analyze:",
                "alias",
                {
                    "DOCTOR": "THE DOCTOR",
                },
            ),
            (
                (
                    "Allowed speaker labels:\n"
                    "Script batch:"
                ),
                "advanced_discovery",
                {
                    "speakers": {},
                    "global_observations": [],
                    "unresolved_aliases": [],
                },
            ),
            (
                "Character lines:",
                "persona",
                {
                    "description": "Voice.",
                    "ref_text": "Sample.",
                },
            ),
        ]

        for prompt, contract, data in cases:
            with self.subTest(
                contract=contract
            ):
                runtime = FakeRuntime(data)

                adapter = (
                    llm_adapter
                    .PersonaOpenAIAdapter(
                        runtime
                    )
                )

                response = (
                    adapter.chat
                    .completions
                    .create(
                        model="ignored",
                        messages=[
                            {
                                "role": "user",
                                "content": prompt,
                            }
                        ],
                    )
                )

                self.assertEqual(
                    runtime.calls[0][
                        "contract"
                    ],
                    contract,
                )

                self.assertEqual(
                    json.loads(
                        response.choices[
                            0
                        ].message.content
                    ),
                    data,
                )


class ScriptAdapterTests(unittest.TestCase):
    def test_native_options_and_metrics(self):
        runtime = FakeRuntime(
            [
                {
                    "speaker": "NARRATOR",
                    "text": "Text.",
                    "instruct": "Neutral.",
                }
            ]
        )

        adapter = (
            llm_adapter
            .ScriptOpenAIAdapter(
                runtime
            )
        )

        output = io.StringIO()

        with redirect_stdout(output):
            response = (
                adapter.chat
                .completions
                .create(
                    model="ignored",
                    messages=[],
                    temperature=0.2,
                    top_p=0.7,
                    max_tokens=1000,
                    extra_body={
                        "top_k": 20,
                        "min_p": 0.1,
                    },
                )
            )

        call = runtime.calls[0]

        self.assertEqual(
            call["contract"],
            "script",
        )

        self.assertEqual(
            call["top_k"],
            20,
        )

        self.assertEqual(
            call["min_p"],
            0.1,
        )

        self.assertIn(
            (
                "Structured response: "
                "backend=ollama-native"
            ),
            output.getvalue(),
        )

        self.assertEqual(
            response.choices[
                0
            ].finish_reason,
            "stop",
        )

    def test_banned_tokens_warning_once(self):
        runtime = FakeRuntime([])

        adapter = (
            llm_adapter
            .ScriptOpenAIAdapter(
                runtime
            )
        )

        output = io.StringIO()

        with redirect_stdout(output):
            for _ in range(2):
                (
                    adapter.chat
                    .completions
                    .create(
                        model="ignored",
                        messages=[],
                        extra_body={
                            "banned_tokens": [
                                "x"
                            ],
                        },
                    )
                )

        self.assertEqual(
            output.getvalue().count(
                (
                    "Native Ollama does not "
                    "expose Alexandria's "
                    "banned_tokens option"
                )
            ),
            1,
        )

    def test_legacy_delegation(self):
        sentinel = object()
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return sentinel

        legacy = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=create,
                )
            )
        )

        adapter = (
            llm_adapter
            .ScriptOpenAIAdapter(
                FakeRuntime(),
                legacy_client=legacy,
            )
        )

        result = (
            adapter.chat
            .completions
            .create(
                model="remote",
                messages=[],
                extra_body={
                    "top_k": 10,
                },
            )
        )

        self.assertIs(
            result,
            sentinel,
        )

        self.assertEqual(
            calls[0]["model"],
            "remote",
        )

        self.assertEqual(
            calls[0]["extra_body"],
            {
                "top_k": 10,
            },
        )


class BuilderTests(unittest.TestCase):
    def test_persona_builder(self):
        runtime = FakeRuntime()

        with patch(
            "llm_adapter.build_runtime_client",
            return_value=runtime,
        ):
            built_runtime, adapter = (
                llm_adapter
                .build_persona_client(
                    {}
                )
            )

        self.assertIs(
            built_runtime,
            runtime,
        )

        self.assertIsInstance(
            adapter,
            llm_adapter.PersonaOpenAIAdapter,
        )

    def test_script_builder_native(self):
        runtime = FakeRuntime(
            backend="ollama-native"
        )

        with (
            patch(
                "llm_adapter.build_runtime_client",
                return_value=runtime,
            ),
            patch(
                (
                    "llm_adapter."
                    "create_legacy_openai_client"
                )
            ) as legacy,
        ):
            built_runtime, adapter = (
                llm_adapter
                .build_script_client(
                    {}
                )
            )

        self.assertIs(
            built_runtime,
            runtime,
        )

        self.assertIsNone(
            adapter.legacy_client
        )

        legacy.assert_not_called()

    def test_script_builder_remote(self):
        runtime = FakeRuntime(
            backend="openai-compatible"
        )

        legacy = object()

        with (
            patch(
                "llm_adapter.build_runtime_client",
                return_value=runtime,
            ),
            patch(
                (
                    "llm_adapter."
                    "create_legacy_openai_client"
                ),
                return_value=legacy,
            ) as legacy_factory,
        ):
            built_runtime, adapter = (
                llm_adapter
                .build_script_client(
                    {}
                )
            )

        self.assertIs(
            built_runtime,
            runtime,
        )

        self.assertIs(
            adapter.legacy_client,
            legacy,
        )

        legacy_factory.assert_called_once_with(
            runtime
        )

    def test_roster_builder_uses_shared_runtime_without_preload(self):
        runtime = FakeRuntime(backend="ollama-native")

        with patch(
            "llm_adapter.build_runtime_client",
            return_value=runtime,
        ) as builder:
            result = llm_adapter.build_roster_client(
                {"llm": {"model_name": "qwen3.5:35b-mlx"}}
            )

        self.assertIs(result, runtime)
        self.assertEqual(runtime.preload_calls, 0)
        builder.assert_called_once()

    def test_review_builder_preloads_native(self):
        runtime = FakeRuntime(
            backend="ollama-native"
        )

        with patch(
            "llm_adapter.build_runtime_client",
            return_value=runtime,
        ):
            adapter, built_runtime = (
                llm_adapter
                .build_review_client(
                    (
                        "http://localhost:"
                        "11434/v1"
                    ),
                    "local",
                    "qwen3.5:35b-mlx",
                    {
                        "thinking": False,
                    },
                )
            )

        self.assertIs(
            built_runtime,
            runtime,
        )

        self.assertIsInstance(
            adapter,
            llm_adapter.ScriptOpenAIAdapter,
        )

        self.assertEqual(
            runtime.preload_calls,
            1,
        )


if __name__ == "__main__":
    unittest.main()
