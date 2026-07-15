from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

import generate_personas


class FakeRuntimeClient:
    def __init__(self):
        self.thinking = False
        self.calls = []

    def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        contract = kwargs["contract"]

        if contract == "alias":
            data = {
                "DOCTOR": "THE DOCTOR",
            }

        elif contract == "advanced_discovery":
            data = {
                "BERNICE": {
                    "aliases": ["BENNY"],
                    "features": [],
                    "personality": ["Dry wit"],
                    "voice_clues": [
                        "Adult British woman"
                    ],
                    "relationships": [],
                    "evidence": [],
                    "sample_lines": [
                        "That is not reassuring."
                    ],
                }
            }

        else:
            data = {
                "description": (
                    "A mature British narrator."
                ),
                "ref_text": (
                    "The matter remains unresolved."
                ),
            }

        return SimpleNamespace(
            data=data,
            backend="ollama-native",
            validation_mode="direct",
            metrics={
                "done_reason": "stop",
                "prompt_tokens": 100,
                "prompt_tokens_per_second": 200.0,
                "output_tokens": 25,
                "output_tokens_per_second": 72.0,
            },
        )


class PersonaAdapterTests(unittest.TestCase):
    def setUp(self):
        self.runtime = FakeRuntimeClient()
        self.adapter = (
            generate_personas._PersonaOpenAIAdapter(
                self.runtime
            )
        )

    def test_persona_contract(self):
        response = self.adapter.chat.completions.create(
            model="ignored",
            messages=[
                {
                    "role": "user",
                    "content": "Create a persona.",
                }
            ],
            temperature=0.3,
            max_tokens=400,
        )

        self.assertEqual(
            self.runtime.calls[0]["contract"],
            "persona",
        )

        parsed = json.loads(
            response.choices[0].message.content
        )

        self.assertEqual(
            set(parsed),
            {"description", "ref_text"},
        )

    def test_alias_contract(self):
        self.adapter.chat.completions.create(
            model="ignored",
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Speakers to analyze:\n"
                        "DOCTOR"
                    ),
                }
            ],
            temperature=0.1,
            max_tokens=1500,
        )

        self.assertEqual(
            self.runtime.calls[0]["contract"],
            "alias",
        )

    def test_advanced_discovery_contract(self):
        self.adapter.chat.completions.create(
            model="ignored",
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Allowed speaker labels:\n"
                        "BERNICE\n\n"
                        "Script batch:\n"
                        "BERNICE: Hello."
                    ),
                }
            ],
            temperature=0.2,
            max_tokens=4000,
        )

        self.assertEqual(
            self.runtime.calls[0]["contract"],
            "advanced_discovery",
        )


class PersonaConfigurationTests(unittest.TestCase):
    def test_native_client_configuration(self):
        runtime, adapter = (
            generate_personas._build_persona_llm_client(
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


class PersonaMetricsTests(unittest.TestCase):
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
            generate_personas._print_persona_llm_metrics(
                "Persona",
                result,
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
