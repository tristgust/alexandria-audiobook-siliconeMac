from __future__ import annotations

import asyncio
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

import llm_config


class LLMNormalizationTests(unittest.TestCase):
    def test_legacy_section_gains_supported_defaults(self):
        result = llm_config.normalized_llm_section(
            {
                "base_url": (
                    "http://localhost:11434/v1"
                ),
                "api_key": "local",
                "model_name": "custom-model",
            }
        )

        self.assertEqual(
            result["model_name"],
            "custom-model",
        )
        self.assertEqual(
            result["backend"],
            "auto",
        )
        self.assertEqual(
            result["context_length"],
            40960,
        )
        self.assertEqual(
            result["keep_alive"],
            -1,
        )
        self.assertFalse(result["thinking"])
        self.assertTrue(
            result["structured_output"]
        )
        self.assertTrue(
            result["corrective_retry"]
        )
        self.assertEqual(
            result["timeout"],
            1800,
        )

    def test_invalid_values_fall_back_safely(self):
        result = llm_config.normalized_llm_section(
            {
                "base_url": "   ",
                "api_key": "",
                "model_name": "",
                "backend": "invalid",
                "context_length": -50,
                "keep_alive": None,
                "thinking": "unknown",
                "structured_output": "unknown",
                "corrective_retry": "unknown",
                "timeout": 0,
            }
        )

        self.assertEqual(
            result["base_url"],
            llm_config.DEFAULT_BASE_URL,
        )
        self.assertEqual(
            result["api_key"],
            llm_config.DEFAULT_API_KEY,
        )
        self.assertEqual(
            result["model_name"],
            llm_config.DEFAULT_MODEL_NAME,
        )
        self.assertEqual(
            result["backend"],
            llm_config.DEFAULT_BACKEND,
        )
        self.assertEqual(
            result["context_length"],
            llm_config.DEFAULT_CONTEXT_LENGTH,
        )
        self.assertEqual(
            result["timeout"],
            llm_config.DEFAULT_TIMEOUT,
        )

    def test_unknown_keys_are_preserved(self):
        result = llm_config.normalized_llm_section(
            {
                "model_name": "custom-model",
                "provider_note": "preserve me",
            }
        )

        self.assertEqual(
            result["provider_note"],
            "preserve me",
        )


class ConfigAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_module = importlib.import_module(
            "app"
        )

    def test_llm_model_accepts_legacy_fields(self):
        model = self.app_module.LLMConfig(
            base_url="http://localhost:11434/v1",
            api_key="local",
            model_name="legacy-model",
        )

        self.assertEqual(
            model.model_name,
            "legacy-model",
        )
        self.assertEqual(model.backend, "auto")
        self.assertEqual(
            model.context_length,
            40960,
        )
        self.assertEqual(model.profiles, {})

    def test_llm_model_rejects_invalid_backend(self):
        with self.assertRaises(ValidationError):
            self.app_module.LLMConfig(
                base_url="http://localhost:11434/v1",
                api_key="local",
                model_name="model",
                backend="invalid",
            )

    def test_get_config_upgrades_legacy_llm_in_memory(self):
        legacy = {
            "llm": {
                "base_url": (
                    "http://localhost:11434/v1"
                ),
                "api_key": "local",
                "model_name": "legacy-custom-model",
            },
            "tts": {
                "mode": "local",
                "url": "http://127.0.0.1:7860",
                "device": "auto",
            },
            "prompts": {
                "system_prompt": "CUSTOM SYSTEM",
                "user_prompt": "CUSTOM USER",
            },
        }

        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "config.json"
            config_path.write_text(
                json.dumps(legacy),
                encoding="utf-8",
            )

            with patch.object(
                self.app_module,
                "CONFIG_PATH",
                str(config_path),
            ):
                result = asyncio.run(
                    self.app_module.get_config()
                )

        self.assertEqual(
            result["llm"]["model_name"],
            "legacy-custom-model",
        )
        self.assertEqual(
            result["llm"]["context_length"],
            40960,
        )
        self.assertEqual(
            result["prompts"]["system_prompt"],
            "CUSTOM SYSTEM",
        )

    def test_save_merges_without_dropping_custom_data(self):
        existing = {
            "llm": {
                "base_url": (
                    "http://localhost:11434/v1"
                ),
                "api_key": "local",
                "model_name": "custom-model",
                "provider_note": "keep",
                "profiles": {
                    "script": {
                        "enabled": True,
                        "overrides": {"timeout": 2200},
                        "evidence": None,
                        "notes": [],
                    }
                },
            },
            "tts": {
                "mode": "local",
                "url": "http://127.0.0.1:7860",
                "device": "auto",
                "custom_tts_value": 42,
            },
            "prompts": {
                "system_prompt": "CUSTOM SYSTEM",
                "user_prompt": "CUSTOM USER",
                "custom_prompt_value": "keep",
            },
            "custom_section": {
                "enabled": True,
            },
        }

        payload = self.app_module.AppConfig(
            llm=self.app_module.LLMConfig(
                base_url=(
                    "http://localhost:11434/v1"
                ),
                api_key="local",
                model_name="custom-model",
            ),
            tts=self.app_module.TTSConfig(
                mode="local",
                url="http://127.0.0.1:7860",
                device="auto",
            ),
        )

        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "config.json"
            config_path.write_text(
                json.dumps(existing),
                encoding="utf-8",
            )

            with patch.object(
                self.app_module,
                "CONFIG_PATH",
                str(config_path),
            ):
                asyncio.run(
                    self.app_module.save_config(
                        payload
                    )
                )

            saved = json.loads(
                config_path.read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(
            saved["llm"]["model_name"],
            "custom-model",
        )
        self.assertEqual(
            saved["llm"]["provider_note"],
            "keep",
        )
        self.assertEqual(
            saved["llm"]["context_length"],
            40960,
        )
        self.assertEqual(
            saved["llm"]["profiles"]["script"]["overrides"][
                "timeout"
            ],
            2200,
        )
        self.assertEqual(
            saved["prompts"]["system_prompt"],
            "CUSTOM SYSTEM",
        )
        self.assertEqual(
            saved["prompts"]["custom_prompt_value"],
            "keep",
        )
        self.assertEqual(
            saved["tts"]["custom_tts_value"],
            42,
        )
        self.assertEqual(
            saved["custom_section"],
            {
                "enabled": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
