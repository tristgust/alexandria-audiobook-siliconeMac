from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from application_settings import (
    MAX_CONFIG_BYTES,
    ApplicationSettingsError,
    get_application_settings,
    update_application_settings,
)


class ApplicationSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config_path = self.root / "config.json"
        self.config = {
            "llm": {
                "backend": "auto",
                "base_url": "http://localhost:11434/v1",
                "api_key": "secret-key",
                "model_name": "qwen3.5:35b-mlx",
                "context_length": 40960,
                "keep_alive": -1,
                "timeout": 1800,
                "thinking": False,
                "structured_output": True,
                "corrective_retry": True,
                "profiles": {
                    "script": {
                        "schema_version": 1,
                        "enabled": True,
                        "overrides": {"timeout": 2200},
                        "evidence": None,
                        "notes": [],
                    }
                },
                "unknown_provider_key": "preserve",
            },
            "tts": {
                "mode": "local",
                "url": "http://127.0.0.1:7860",
                "device": "auto",
                "language": "Auto",
                "parallel_workers": 2,
                "pause_between_speakers_ms": 500,
                "pause_same_speaker_ms": 250,
                "batch_seed": 123,
                "unknown_tts_key": "preserve",
            },
            "generation": {
                "chunk_size": 3000,
                "temperature": 0.6,
            },
            "prompts": {
                "system_prompt": "Protected prompt text.",
            },
            "unknown_root": {"preserve": True},
        }
        self.config_path.write_text(
            json.dumps(self.config, indent=2) + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def status(self) -> dict:
        return get_application_settings(config_path=self.config_path)

    def editable(self) -> dict:
        return self.status()["settings"]

    def update(self, settings: dict, *, fingerprint: str | None = None) -> dict:
        status = self.status()
        return update_application_settings(
            config_path=self.config_path,
            expected_config_fingerprint=(
                fingerprint or status["config_fingerprint"]
            ),
            settings=settings,
        )

    def test_status_is_file_pure_model_free_and_redacts_api_key(self) -> None:
        before = self.config_path.read_bytes()
        first = self.status()
        second = self.status()
        self.assertEqual(first, second)
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertTrue(first["config_exists"])
        provider = first["settings"]["provider"]
        self.assertTrue(provider["api_key_configured"])
        self.assertEqual(provider["api_key_mode"], "preserve")
        self.assertEqual(provider["api_key"], "")
        rendered = json.dumps(first)
        self.assertNotIn("secret-key", rendered)
        self.assertNotIn("Protected prompt text", rendered)
        self.assertFalse(first["diagnostics_in_normal_settings"])
        self.assertFalse(first["repair_actions_in_normal_settings"])

    def test_missing_config_uses_defaults_without_writing(self) -> None:
        self.config_path.unlink()
        status = self.status()
        self.assertFalse(status["config_exists"])
        self.assertFalse(self.config_path.exists())
        self.assertEqual(
            status["settings"]["preferences"]["default_source_language"],
            "English",
        )
        self.assertEqual(
            status["settings"]["accessibility"]["motion"],
            "system",
        )
        self.assertEqual(
            status["settings"]["storage"]["cleanup_mode"],
            "manual_only",
        )
        self.assertEqual(
            status["settings"]["storage"]["enforcement_status"],
            "policy_saved_not_enforced",
        )

    def test_update_preserves_prompts_profiles_generation_and_unknown_fields(self) -> None:
        settings = self.editable()
        settings["preferences"].update(
            {
                "default_source_language": "English",
                "default_output_language": "Swedish",
                "confirm_before_destructive": False,
                "remember_last_project": False,
            }
        )
        settings["provider"].update(
            {
                "backend": "ollama",
                "base_url": "http://127.0.0.1:11434/v1",
                "model_name": "qwen3.5:35b-mlx",
                "context_length": 65536,
                "keep_alive": "10m",
                "timeout": 2400,
                "thinking": True,
                "structured_output": True,
                "corrective_retry": False,
                "api_key_mode": "preserve",
                "api_key": "",
            }
        )
        settings["speech"].update(
            {
                "language": "Swedish",
                "parallel_workers": 4,
                "pause_between_speakers_ms": 600,
                "pause_same_speaker_ms": 300,
            }
        )
        settings["accessibility"].update(
            {
                "motion": "reduced",
                "contrast": "more",
                "density": "compact",
                "status_announcements": False,
            }
        )
        settings["storage"].update(
            {
                "rollback_retention_days": 45,
                "intermediate_retention_days": 10,
                "maximum_backup_gib": 25,
            }
        )
        result = self.update(settings)
        self.assertEqual(
            result["settings"]["preferences"]["default_output_language"],
            "Swedish",
        )
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["llm"]["api_key"], "secret-key")
        self.assertEqual(saved["llm"]["profiles"], self.config["llm"]["profiles"])
        self.assertEqual(
            saved["llm"]["unknown_provider_key"],
            "preserve",
        )
        self.assertEqual(saved["tts"]["batch_seed"], 123)
        self.assertEqual(saved["tts"]["unknown_tts_key"], "preserve")
        self.assertEqual(saved["generation"], self.config["generation"])
        self.assertEqual(saved["prompts"], self.config["prompts"])
        self.assertEqual(saved["unknown_root"], self.config["unknown_root"])
        self.assertEqual(saved["application"]["storage"]["cleanup_mode"], "manual_only")
        self.assertEqual(saved["application"]["accessibility"]["density"], "compact")

    def test_api_key_replace_clear_and_preserve_are_explicit(self) -> None:
        settings = self.editable()
        settings["provider"]["api_key_mode"] = "replace"
        settings["provider"]["api_key"] = "replacement-secret"
        first = self.update(settings)
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["llm"]["api_key"], "replacement-secret")
        self.assertNotIn("replacement-secret", json.dumps(first))

        settings = first["settings"]
        settings["provider"]["backend"] = "ollama"
        settings["provider"]["base_url"] = "http://localhost:11434/v1"
        settings["provider"]["api_key_mode"] = "clear"
        second = self.update(settings)
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["llm"]["api_key"], "")
        self.assertFalse(second["settings"]["provider"]["api_key_configured"])

        settings = second["settings"]
        settings["provider"]["api_key_mode"] = "preserve"
        settings["provider"]["api_key"] = "should-not-be-used"
        self.update(settings)
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["llm"]["api_key"], "")

    def test_openai_provider_requires_api_key_when_clearing(self) -> None:
        settings = self.editable()
        settings["provider"].update(
            {
                "backend": "openai",
                "base_url": "https://provider.example/v1",
                "api_key_mode": "clear",
            }
        )
        before = self.config_path.read_bytes()
        with self.assertRaises(ApplicationSettingsError) as caught:
            self.update(settings)
        self.assertEqual(caught.exception.code, "settings_api_key_required")
        self.assertEqual(self.config_path.read_bytes(), before)

    def test_native_ollama_requires_local_url_and_no_embedded_credentials(self) -> None:
        settings = self.editable()
        settings["provider"].update(
            {
                "backend": "ollama",
                "base_url": "https://remote.example/v1",
            }
        )
        with self.assertRaises(ApplicationSettingsError) as remote:
            self.update(settings)
        self.assertEqual(remote.exception.code, "settings_ollama_url_not_local")

        settings = self.editable()
        settings["provider"]["base_url"] = "https://user:secret@provider.example/v1"
        with self.assertRaises(ApplicationSettingsError) as credentials:
            self.update(settings)
        self.assertEqual(
            credentials.exception.code,
            "settings_url_credentials_forbidden",
        )

    def test_structured_output_is_required_and_invalid_input_is_retained_on_disk(self) -> None:
        before = self.config_path.read_bytes()
        settings = self.editable()
        settings["provider"]["structured_output"] = False
        with self.assertRaises(ApplicationSettingsError) as caught:
            self.update(settings)
        self.assertEqual(
            caught.exception.code,
            "settings_structured_output_required",
        )
        self.assertEqual(self.config_path.read_bytes(), before)

        settings = self.editable()
        settings["speech"]["parallel_workers"] = 100
        with self.assertRaises(ApplicationSettingsError) as range_error:
            self.update(settings)
        self.assertEqual(range_error.exception.code, "settings_field_out_of_range")
        self.assertEqual(self.config_path.read_bytes(), before)

    def test_external_speech_requires_valid_server_url(self) -> None:
        settings = self.editable()
        settings["speech"].update({"mode": "external", "url": ""})
        with self.assertRaises(ApplicationSettingsError) as missing:
            self.update(settings)
        self.assertIn(
            missing.exception.code,
            {"settings_field_required", "settings_tts_url_required"},
        )
        settings = self.editable()
        settings["speech"].update(
            {"mode": "external", "url": "ftp://example.test"}
        )
        with self.assertRaises(ApplicationSettingsError) as invalid:
            self.update(settings)
        self.assertEqual(invalid.exception.code, "settings_url_invalid")

    def test_unknown_or_missing_sections_are_rejected_without_mutation(self) -> None:
        before = self.config_path.read_bytes()
        settings = self.editable()
        settings.pop("storage")
        with self.assertRaises(ApplicationSettingsError) as missing:
            self.update(settings)
        self.assertEqual(missing.exception.code, "settings_payload_invalid")
        settings = self.editable()
        settings["diagnostics"] = {"repair": True}
        with self.assertRaises(ApplicationSettingsError) as unexpected:
            self.update(settings)
        self.assertEqual(unexpected.exception.code, "settings_payload_invalid")
        self.assertEqual(self.config_path.read_bytes(), before)

    def test_stale_fingerprint_rejects_overwrite(self) -> None:
        original = self.status()
        settings = original["settings"]
        settings["preferences"]["default_output_language"] = "German"
        self.update(settings)
        settings = self.status()["settings"]
        settings["preferences"]["default_output_language"] = "French"
        with self.assertRaises(ApplicationSettingsError) as stale:
            self.update(
                settings,
                fingerprint=original["config_fingerprint"],
            )
        self.assertEqual(stale.exception.code, "settings_config_conflict")
        self.assertEqual(
            self.status()["settings"]["preferences"]["default_output_language"],
            "German",
        )

    def test_config_symlink_and_oversized_file_fail_closed(self) -> None:
        outside = self.root / "outside.json"
        outside.write_text(json.dumps(self.config), encoding="utf-8")
        self.config_path.unlink()
        self.config_path.symlink_to(outside)
        with self.assertRaises(ApplicationSettingsError) as unsafe:
            self.status()
        self.assertEqual(unsafe.exception.code, "settings_config_unsafe")
        self.config_path.unlink()
        self.config_path.write_bytes(b" " * (MAX_CONFIG_BYTES + 1))
        with self.assertRaises(ApplicationSettingsError) as oversized:
            self.status()
        self.assertEqual(oversized.exception.code, "settings_config_too_large")

    def test_advanced_destinations_are_explicit_and_non_destructive(self) -> None:
        destinations = self.status()["advanced_destinations"]
        self.assertEqual(
            set(destinations),
            {
                "stage_profiles",
                "runtime_diagnostics",
                "model_cache",
                "advanced_generation",
            },
        )
        self.assertEqual(
            destinations["runtime_diagnostics"]["context"]["tool"],
            "maintenance",
        )
        self.assertEqual(
            destinations["model_cache"]["context"]["tool"],
            "model-cache",
        )
        self.assertTrue(
            all(
                item["context"]["return"] == "#/settings"
                for item in destinations.values()
            )
        )


if __name__ == "__main__":
    unittest.main()
