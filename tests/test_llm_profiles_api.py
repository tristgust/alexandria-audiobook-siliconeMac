from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from llm_profiles_api import (
    LLMProfilesApiError,
    get_llm_profiles_payload,
    get_llm_stage_profile_payload,
    remove_llm_stage_profile_payload,
    update_llm_stage_profile_payload,
)


class LLMProfilesApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config_path = self.root / "config.json"
        self.config = {
            "llm": {
                "base_url": "http://localhost:11434/v1",
                "api_key": "local",
                "model_name": "qwen3.5:35b-mlx",
                "backend": "ollama",
                "provider_note": "preserve",
            },
            "tts": {"mode": "local", "custom": 42},
            "custom_section": {"keep": True},
        }
        self.config_path.write_text(
            json.dumps(self.config),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def evidence(target: str) -> dict:
        return {
            "benchmark_id": "profile-api-v1",
            "compared_models": ["qwen3.5:35b-mlx", target],
            "quality_comparison_passed": True,
            "fidelity_validation_passed": True,
            "runtime_measurement_completed": True,
            "regression_tests_passed": True,
            "approved_at_utc": "2026-07-16T23:00:00Z",
            "notes": ["Verified locally."],
        }

    def status(self) -> dict:
        return get_llm_profiles_payload(config_path=self.config_path)

    def test_status_is_model_free_and_does_not_modify_config(self) -> None:
        before = self.config_path.read_bytes()
        status = self.status()
        self.assertEqual(status["global_model"], "qwen3.5:35b-mlx")
        self.assertEqual(len(status["stages"]), 8)
        self.assertTrue(status["config_exists"])
        self.assertEqual(self.config_path.read_bytes(), before)

    def test_missing_config_status_uses_defaults_without_creating_file(self) -> None:
        missing = self.root / "missing.json"
        status = get_llm_profiles_payload(config_path=missing)
        self.assertEqual(status["global_model"], "qwen3.5:35b-mlx")
        self.assertFalse(status["config_exists"])
        self.assertFalse(missing.exists())

    def test_update_profile_preserves_unknown_config(self) -> None:
        status = self.status()
        updated = update_llm_stage_profile_payload(
            config_path=self.config_path,
            stage="script",
            expected_profiles_fingerprint=status["profiles_fingerprint"],
            profile={
                "enabled": True,
                "overrides": {"context_length": 65536},
                "evidence": None,
                "notes": ["Long source context."],
                "custom_profile_note": "preserve",
            },
        )
        self.assertEqual(updated["stage"]["stage"], "script")
        self.assertEqual(
            updated["effective_llm"]["context_length"],
            65536,
        )
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["llm"]["provider_note"], "preserve")
        self.assertEqual(saved["tts"]["custom"], 42)
        self.assertEqual(saved["custom_section"], {"keep": True})
        self.assertEqual(
            saved["llm"]["profiles"]["script"]["custom_profile_note"],
            "preserve",
        )

    def test_model_change_without_evidence_returns_422(self) -> None:
        with self.assertRaises(LLMProfilesApiError) as caught:
            update_llm_stage_profile_payload(
                config_path=self.config_path,
                stage="review",
                expected_profiles_fingerprint=self.status()[
                    "profiles_fingerprint"
                ],
                profile={
                    "enabled": True,
                    "overrides": {"model_name": "alternate-model"},
                    "evidence": None,
                    "notes": [],
                },
            )
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(caught.exception.code, "llm_profile_rejected")

    def test_valid_model_change_returns_effective_profile(self) -> None:
        updated = update_llm_stage_profile_payload(
            config_path=self.config_path,
            stage="review",
            expected_profiles_fingerprint=self.status()[
                "profiles_fingerprint"
            ],
            profile={
                "enabled": True,
                "overrides": {"model_name": "alternate-model"},
                "evidence": self.evidence("alternate-model"),
                "notes": [],
            },
        )
        self.assertEqual(
            updated["effective_llm"]["model_name"],
            "alternate-model",
        )
        self.assertTrue(updated["stage"]["evidence_complete"])

    def test_stale_update_returns_409(self) -> None:
        original = self.status()["profiles_fingerprint"]
        update_llm_stage_profile_payload(
            config_path=self.config_path,
            stage="script",
            expected_profiles_fingerprint=original,
            profile={
                "enabled": True,
                "overrides": {"timeout": 2400},
                "evidence": None,
                "notes": [],
            },
        )
        with self.assertRaises(LLMProfilesApiError) as caught:
            update_llm_stage_profile_payload(
                config_path=self.config_path,
                stage="persona",
                expected_profiles_fingerprint=original,
                profile={
                    "enabled": True,
                    "overrides": {"timeout": 2200},
                    "evidence": None,
                    "notes": [],
                },
            )
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.code, "stale_llm_profiles")
        self.assertEqual(
            caught.exception.as_detail()["code"],
            "stale_llm_profiles",
        )

    def test_remove_profile_returns_updated_status(self) -> None:
        first = update_llm_stage_profile_payload(
            config_path=self.config_path,
            stage="persona",
            expected_profiles_fingerprint=self.status()[
                "profiles_fingerprint"
            ],
            profile={
                "enabled": True,
                "overrides": {"timeout": 2400},
                "evidence": None,
                "notes": [],
            },
        )
        removed = remove_llm_stage_profile_payload(
            config_path=self.config_path,
            stage="persona",
            expected_profiles_fingerprint=first["profiles_fingerprint"],
        )
        persona = next(
            item for item in removed["stages"]
            if item["stage"] == "persona"
        )
        self.assertFalse(persona["configured"])
        self.assertTrue(persona["inherits_global"])

    def test_corrupt_and_invalid_config_errors_are_distinct(self) -> None:
        self.config_path.write_text("not json", encoding="utf-8")
        with self.assertRaises(LLMProfilesApiError) as corrupt:
            self.status()
        self.assertEqual(
            corrupt.exception.code,
            "llm_profiles_config_unreadable",
        )
        self.config_path.write_text("[]", encoding="utf-8")
        with self.assertRaises(LLMProfilesApiError) as invalid:
            self.status()
        self.assertEqual(
            invalid.exception.code,
            "llm_profiles_config_invalid",
        )

    def test_unknown_stage_is_rejected(self) -> None:
        with self.assertRaises(LLMProfilesApiError) as caught:
            get_llm_stage_profile_payload(
                config_path=self.config_path,
                stage="tts",
            )
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(caught.exception.code, "llm_profile_rejected")


if __name__ == "__main__":
    unittest.main()
