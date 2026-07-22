from __future__ import annotations

import copy
import unittest

from llm_profiles import (
    LLMProfileConflictError,
    LLMProfileValidationError,
    PROFILE_STAGES,
    build_profiles_status,
    config_for_llm_stage,
    profiles_fingerprint,
    remove_stage_profile,
    update_stage_profile,
    validate_stage_profile,
)


class LLMProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
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
                "provider_note": "preserve",
            },
            "custom_section": {"keep": True},
        }

    @staticmethod
    def evidence(
        target_model: str = "alternate-model",
        *,
        quality: bool = True,
        fidelity: bool = True,
        runtime: bool = True,
        regression: bool = True,
    ) -> dict:
        return {
            "benchmark_id": "phase21-comparison-v1",
            "compared_models": [
                "qwen3.5:35b-mlx",
                target_model,
            ],
            "quality_comparison_passed": quality,
            "fidelity_validation_passed": fidelity,
            "runtime_measurement_completed": runtime,
            "regression_tests_passed": regression,
            "approved_at_utc": "2026-07-16T23:00:00Z",
            "notes": ["Reproducible local comparison."],
        }

    def test_every_stage_inherits_global_by_default(self) -> None:
        status = build_profiles_status(self.config)
        self.assertEqual(
            [item["stage"] for item in status["stages"]],
            list(PROFILE_STAGES),
        )
        self.assertTrue(
            all(item["inherits_global"] for item in status["stages"])
        )
        self.assertTrue(
            all(
                item["effective_model"] == "qwen3.5:35b-mlx"
                for item in status["stages"]
            )
        )
        self.assertEqual(
            config_for_llm_stage(
                self.config,
                stage="script",
            ),
            self.config,
        )

    def test_same_model_supported_runtime_overrides_are_applied(self) -> None:
        updated = update_stage_profile(
            self.config,
            stage="script",
            expected_profiles_fingerprint=profiles_fingerprint(self.config),
            profile={
                "enabled": True,
                "overrides": {
                    "context_length": 65536,
                    "timeout": 2400,
                },
                "evidence": None,
                "notes": ["Long-book script profile."],
                "custom_profile_note": "preserve",
            },
        )
        effective = config_for_llm_stage(updated, stage="script")
        self.assertEqual(effective["llm"]["context_length"], 65536)
        self.assertEqual(effective["llm"]["timeout"], 2400)
        self.assertEqual(
            effective["llm"]["model_name"],
            "qwen3.5:35b-mlx",
        )
        self.assertEqual(effective["llm"]["provider_note"], "preserve")
        self.assertEqual(updated["custom_section"], {"keep": True})
        self.assertEqual(
            updated["llm"]["profiles"]["script"]["custom_profile_note"],
            "preserve",
        )

    def test_unsupported_runtime_override_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            LLMProfileValidationError,
            "unsupported overrides",
        ):
            update_stage_profile(
                self.config,
                stage="script",
                expected_profiles_fingerprint=profiles_fingerprint(self.config),
                profile={
                    "enabled": True,
                    "overrides": {"temperature": 0.2},
                    "evidence": None,
                    "notes": [],
                },
            )

    def test_model_change_requires_complete_evidence(self) -> None:
        with self.assertRaisesRegex(
            LLMProfileValidationError,
            "requires evidence",
        ):
            update_stage_profile(
                self.config,
                stage="review",
                expected_profiles_fingerprint=profiles_fingerprint(self.config),
                profile={
                    "enabled": True,
                    "overrides": {"model_name": "alternate-model"},
                    "evidence": None,
                    "notes": [],
                },
            )

    def test_failed_evidence_gate_blocks_model_change(self) -> None:
        with self.assertRaisesRegex(
            LLMProfileValidationError,
            "gates must all pass",
        ):
            update_stage_profile(
                self.config,
                stage="review",
                expected_profiles_fingerprint=profiles_fingerprint(self.config),
                profile={
                    "enabled": True,
                    "overrides": {"model_name": "alternate-model"},
                    "evidence": self.evidence(fidelity=False),
                    "notes": [],
                },
            )

    def test_evidence_must_compare_inherited_and_target_models(self) -> None:
        evidence = self.evidence()
        evidence["compared_models"] = ["alternate-model", "other"]
        with self.assertRaisesRegex(
            LLMProfileValidationError,
            "inherited and target",
        ):
            validate_stage_profile(
                {
                    "enabled": True,
                    "overrides": {"model_name": "alternate-model"},
                    "evidence": evidence,
                    "notes": [],
                },
                stage="review",
                base_model="qwen3.5:35b-mlx",
            )

    def test_valid_evidence_allows_model_change(self) -> None:
        updated = update_stage_profile(
            self.config,
            stage="review",
            expected_profiles_fingerprint=profiles_fingerprint(self.config),
            profile={
                "enabled": True,
                "overrides": {
                    "model_name": "alternate-model",
                    "context_length": 32768,
                },
                "evidence": self.evidence(),
                "notes": ["Review-specific candidate."],
            },
        )
        effective = config_for_llm_stage(updated, stage="review")
        self.assertEqual(
            effective["llm"]["model_name"],
            "alternate-model",
        )
        status = build_profiles_status(updated)
        review = next(
            item for item in status["stages"]
            if item["stage"] == "review"
        )
        self.assertTrue(review["model_changed"])
        self.assertTrue(review["evidence_complete"])
        self.assertFalse(review["inherits_global"])

    def test_disabled_profile_is_preserved_but_inherits_global(self) -> None:
        updated = update_stage_profile(
            self.config,
            stage="persona",
            expected_profiles_fingerprint=profiles_fingerprint(self.config),
            profile={
                "enabled": False,
                "overrides": {"context_length": 65536},
                "evidence": None,
                "notes": ["Saved for later."],
            },
        )
        effective = config_for_llm_stage(updated, stage="persona")
        self.assertEqual(effective["llm"]["context_length"], 40960)
        status = build_profiles_status(updated)
        persona = next(
            item for item in status["stages"]
            if item["stage"] == "persona"
        )
        self.assertTrue(persona["configured"])
        self.assertFalse(persona["enabled"])
        self.assertTrue(persona["inherits_global"])

    def test_stale_profile_fingerprint_is_rejected(self) -> None:
        updated = update_stage_profile(
            self.config,
            stage="script",
            expected_profiles_fingerprint=profiles_fingerprint(self.config),
            profile={
                "enabled": True,
                "overrides": {"timeout": 2200},
                "evidence": None,
                "notes": [],
            },
        )
        with self.assertRaisesRegex(
            LLMProfileConflictError,
            "changed after this edit",
        ):
            update_stage_profile(
                updated,
                stage="review",
                expected_profiles_fingerprint=profiles_fingerprint(self.config),
                profile={
                    "enabled": True,
                    "overrides": {"timeout": 2300},
                    "evidence": None,
                    "notes": [],
                },
            )

    def test_remove_profile_preserves_other_profiles_and_unknown_config(self) -> None:
        first = update_stage_profile(
            self.config,
            stage="script",
            expected_profiles_fingerprint=profiles_fingerprint(self.config),
            profile={
                "enabled": True,
                "overrides": {"timeout": 2200},
                "evidence": None,
                "notes": [],
            },
        )
        second = update_stage_profile(
            first,
            stage="review",
            expected_profiles_fingerprint=profiles_fingerprint(first),
            profile={
                "enabled": True,
                "overrides": {"context_length": 32768},
                "evidence": None,
                "notes": [],
            },
        )
        removed = remove_stage_profile(
            second,
            stage="script",
            expected_profiles_fingerprint=profiles_fingerprint(second),
        )
        self.assertNotIn("script", removed["llm"]["profiles"])
        self.assertIn("review", removed["llm"]["profiles"])
        self.assertEqual(removed["llm"]["provider_note"], "preserve")
        self.assertEqual(removed["custom_section"], {"keep": True})

    def test_invalid_stage_and_profile_collection_are_rejected(self) -> None:
        with self.assertRaisesRegex(
            LLMProfileValidationError,
            "Unsupported LLM profile stage",
        ):
            config_for_llm_stage(self.config, stage="tts")
        invalid = copy.deepcopy(self.config)
        invalid["llm"]["profiles"] = []
        with self.assertRaisesRegex(
            LLMProfileValidationError,
            "llm.profiles",
        ):
            build_profiles_status(invalid)


if __name__ == "__main__":
    unittest.main()
