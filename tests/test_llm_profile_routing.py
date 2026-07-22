from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from llm_adapter import (
    ScriptOpenAIAdapter,
    build_persona_client,
    build_review_client,
    build_roster_client,
    build_script_client,
)
from llm_config import runtime_settings_from_config
from visual_discovery import build_visual_identity


class LLMProfileRoutingTests(unittest.TestCase):
    @staticmethod
    def evidence(target: str) -> dict:
        return {
            "benchmark_id": "routing-evidence-v1",
            "compared_models": ["qwen3.5:35b-mlx", target],
            "quality_comparison_passed": True,
            "fidelity_validation_passed": True,
            "runtime_measurement_completed": True,
            "regression_tests_passed": True,
            "approved_at_utc": "2026-07-16T23:00:00Z",
            "notes": ["Routing fixture."],
        }

    def config(self) -> dict:
        return {
            "llm": {
                "base_url": "http://localhost:11434/v1",
                "api_key": "local",
                "model_name": "qwen3.5:35b-mlx",
                "backend": "ollama",
                "context_length": 40960,
                "keep_alive": -1,
                "thinking": False,
                "structured_output": True,
                "corrective_retry": True,
                "timeout": 1800,
                "profiles": {
                    "script": {
                        "enabled": True,
                        "overrides": {"context_length": 65536},
                        "evidence": None,
                        "notes": [],
                    },
                    "review": {
                        "enabled": True,
                        "overrides": {"model_name": "review-model"},
                        "evidence": self.evidence("review-model"),
                        "notes": [],
                    },
                    "persona": {
                        "enabled": True,
                        "overrides": {"timeout": 2400},
                        "evidence": None,
                        "notes": [],
                    },
                    "roster": {
                        "enabled": True,
                        "overrides": {"context_length": 32768},
                        "evidence": None,
                        "notes": [],
                    },
                    "visual_discovery": {
                        "enabled": True,
                        "overrides": {"model_name": "visual-discovery-model"},
                        "evidence": self.evidence("visual-discovery-model"),
                        "notes": [],
                    },
                    "visual_compilation": {
                        "enabled": True,
                        "overrides": {"model_name": "visual-compile-model"},
                        "evidence": self.evidence("visual-compile-model"),
                        "notes": [],
                    },
                },
            }
        }

    def test_runtime_settings_apply_only_requested_stage(self) -> None:
        config = self.config()
        global_settings = runtime_settings_from_config(config)
        script = runtime_settings_from_config(config, stage="script")
        review = runtime_settings_from_config(config, stage="review")
        self.assertEqual(global_settings.context_length, 40960)
        self.assertEqual(script.context_length, 65536)
        self.assertEqual(script.model_name, "qwen3.5:35b-mlx")
        self.assertEqual(review.model_name, "review-model")
        self.assertEqual(review.context_length, 40960)

    def test_builders_route_to_their_named_profiles(self) -> None:
        config = self.config()
        script_runtime, _ = build_script_client(config)
        persona_runtime, _ = build_persona_client(config)
        roster_runtime = build_roster_client(config)
        visual_runtime = build_roster_client(
            config,
            stage="visual_discovery",
        )
        compilation_runtime = build_roster_client(
            config,
            stage="visual_compilation",
        )
        self.assertEqual(script_runtime.context_length, 65536)
        self.assertEqual(persona_runtime.timeout, 2400)
        self.assertEqual(roster_runtime.context_length, 32768)
        self.assertEqual(
            visual_runtime.model_name,
            "visual-discovery-model",
        )
        self.assertEqual(
            compilation_runtime.model_name,
            "visual-compile-model",
        )

    def test_review_builder_uses_review_profile_model(self) -> None:
        config = self.config()
        llm = config["llm"]
        adapter, runtime = build_review_client(
            llm["base_url"],
            llm["api_key"],
            llm["model_name"],
            llm,
        )
        self.assertEqual(runtime.model_name, "review-model")
        self.assertEqual(adapter.runtime_client.model_name, "review-model")

    def test_openai_adapter_uses_effective_runtime_model_not_call_argument(self) -> None:
        response = SimpleNamespace(
            choices=[],
            usage=None,
        )
        legacy_create = Mock(return_value=response)
        legacy = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=legacy_create)
            )
        )
        runtime = SimpleNamespace(model_name="profile-model")
        adapter = ScriptOpenAIAdapter(
            runtime,
            legacy_client=legacy,
        )
        returned = adapter.chat.completions.create(
            model="global-model",
            messages=[{"role": "user", "content": "x"}],
        )
        self.assertIs(returned, response)
        self.assertEqual(
            legacy_create.call_args.kwargs["model"],
            "profile-model",
        )

    def test_visual_identity_is_legacy_compatible_when_profiles_match(self) -> None:
        runtime = SimpleNamespace(
            model_name="same",
            backend="ollama-native",
            thinking=False,
            structured_output=True,
            corrective_retry=True,
            context_length=40960,
        )
        identity = build_visual_identity(
            runtime,
            compilation_runtime_client=SimpleNamespace(**runtime.__dict__),
            passage_size=12000,
            overlap_chars=1200,
            temperature=0.1,
            max_tokens=5000,
            seed=42,
        )
        self.assertNotIn("visual_compilation_runtime", identity)

    def test_visual_identity_records_distinct_compilation_profile(self) -> None:
        discovery = SimpleNamespace(
            model_name="discovery",
            backend="ollama-native",
            thinking=False,
            structured_output=True,
            corrective_retry=True,
            context_length=40960,
        )
        compilation = SimpleNamespace(
            model_name="compilation",
            backend="ollama-native",
            thinking=False,
            structured_output=True,
            corrective_retry=True,
            context_length=32768,
        )
        identity = build_visual_identity(
            discovery,
            compilation_runtime_client=compilation,
            passage_size=12000,
            overlap_chars=1200,
            temperature=0.1,
            max_tokens=5000,
            seed=42,
        )
        self.assertEqual(identity["model_name"], "discovery")
        self.assertEqual(
            identity["visual_compilation_runtime"]["model_name"],
            "compilation",
        )


if __name__ == "__main__":
    unittest.main()
