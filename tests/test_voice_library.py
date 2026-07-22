from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import voice_library
from voice_library import VoiceLibraryError, build_voice_library


class VoiceLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "voice_config.json").write_text(
            json.dumps(
                {
                    "BERNICE": {
                        "type": "clone",
                        "clone_backend": "qwen3_base",
                        "ref_audio": "clone_voices/bernice.wav",
                        "ref_text": "Exact Bernice transcript.",
                    },
                    "DOCTOR": {
                        "type": "clone",
                        "clone_backend": "qwen3_instruction_controlled",
                        "ref_audio": "clone_voices/doctor.wav",
                        "ref_text": "Exact Doctor transcript.",
                        "character_style": "Dry, controlled, Scottish identity.",
                    },
                    "JOHN SMITH": {
                        "type": "alias",
                        "alias_of": "DOCTOR",
                    },
                    "NARRATOR": {
                        "type": "custom",
                        "voice": "Ryan",
                    },
                    "ADAPTER ROLE": {
                        "type": "lora",
                        "lora_adapter": "adapter_1",
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        self.cast = {
            "summary": {"blocker_count": 1},
            "characters": [
                self.character(
                    "character_bernice",
                    "Bernice Summerfield",
                    "BERNICE",
                    method="clone",
                    backend="qwen3_base",
                    selected_voice="clone_voices/bernice.wav",
                ),
                self.character(
                    "character_doctor",
                    "The Doctor",
                    "DOCTOR",
                    method="clone",
                    backend="qwen3_instruction_controlled",
                    selected_voice="clone_voices/doctor.wav",
                    preview_status="approved",
                ),
                self.character(
                    "character_alias",
                    "Dr John Smith",
                    "JOHN SMITH",
                    method="clone",
                    backend="qwen3_instruction_controlled",
                    selected_voice="clone_voices/doctor.wav",
                    alias_target="DOCTOR",
                ),
                self.character(
                    "character_narrator",
                    "Narrator",
                    "NARRATOR",
                    method="custom",
                    backend="qwen3_base",
                    selected_voice="Ryan",
                ),
                self.character(
                    "character_adapter",
                    "Adapter Role",
                    "ADAPTER ROLE",
                    method="lora",
                    backend="qwen3_base",
                    selected_voice="adapter_1",
                    adapter_id="adapter_1",
                ),
            ],
        }
        self.inventory = {
            "artifacts": [
                self.artifact(
                    "clone_bernice",
                    "clone_reference",
                    "bernice.wav",
                    "Bernice reference",
                    "clone_voices/bernice.wav",
                ),
                self.artifact(
                    "clone_doctor",
                    "clone_reference",
                    "doctor.wav",
                    "Doctor reference",
                    "clone_voices/doctor.wav",
                ),
                self.artifact(
                    "designed_1",
                    "designed_voice",
                    "designed_1",
                    "Editorial alto",
                    "designed_voices/designed_1.wav",
                ),
                self.artifact(
                    "adapter_1",
                    "lora_adapter",
                    "adapter_1",
                    "Bernice adapter",
                    "lora_models/adapter_1/adapter.safetensors",
                ),
            ]
        }
        self.capabilities = {
            "environment": {
                "mlx_models_cached": {
                    "custom_voice": True,
                    "voice_design": True,
                    "clone": True,
                    "controlled_clone": True,
                }
            },
            "expressive_clone": {
                "supported": False,
                "experimental_preview_available": True,
                "status": "experimental_unaccepted",
                "backend": "qwen3_instruction_controlled",
                "legacy_backend": "voxcpm2_controlled",
                "legacy_backend_supported": False,
                "per_line_instruction_supported": False,
                "instruction_channel_present": True,
                "production_default": False,
                "preview_and_manual_review_required": True,
                "warning": "Preview-only until listening passes.",
            },
        }
        self.aliases = {
            "BERNICE": {
                "is_alias": False,
                "alias_of": None,
                "chain": ["BERNICE"],
                "resolved_target": "BERNICE",
                "resolved_type": "clone",
                "resolved_source": "clone_voices/bernice.wav",
            },
            "DOCTOR": {
                "is_alias": False,
                "alias_of": None,
                "chain": ["DOCTOR"],
                "resolved_target": "DOCTOR",
                "resolved_type": "clone",
                "resolved_source": "clone_voices/doctor.wav",
            },
            "JOHN SMITH": {
                "is_alias": True,
                "alias_of": "DOCTOR",
                "chain": ["JOHN SMITH", "DOCTOR"],
                "resolved_target": "DOCTOR",
                "resolved_type": "clone",
                "resolved_source": "clone_voices/doctor.wav",
            },
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def character(
        self,
        character_id: str,
        name: str,
        key: str,
        *,
        method: str,
        backend: str,
        selected_voice: str,
        preview_status: str = "not_generated",
        adapter_id: str | None = None,
        alias_target: str | None = None,
    ) -> dict:
        return {
            "character_id": character_id,
            "display_name": name,
            "script_connection": {"resolved_script_voice_label": key},
            "voice": {
                "configuration_key": key,
                "selected_production_method": method,
                "selected_backend": backend,
                "selected_voice": selected_voice,
                "valid": True,
                "preview": {"status": preview_status},
                "adapter": {"id": adapter_id},
                "alias": {"target": alias_target},
            },
        }

    def artifact(
        self,
        artifact_id: str,
        kind: str,
        key: str,
        name: str,
        relative_path: str,
    ) -> dict:
        return {
            "artifact_id": artifact_id,
            "kind": kind,
            "key": key,
            "name": name,
            "state": "available",
            "size_bytes": 100,
            "file_count": 1,
            "modified_at_utc": "2026-07-21T12:00:00Z",
            "provenance": {"status": "reviewed"},
            "metadata_error": None,
            "native_route": {
                "destination": "more",
                "context": {"tool": "voice-designer", "source": artifact_id},
                "hash": f"#/more?tool=voice-designer&source={artifact_id}",
            },
            "technical_details": {
                "relative_path": relative_path,
                "identity_aliases": [key, Path(relative_path).name],
            },
        }

    def build(self) -> dict:
        with (
            patch.object(
                voice_library,
                "inspect_cast_project",
                return_value=self.cast,
            ),
            patch.object(
                voice_library,
                "inspect_library_inventory",
                return_value=self.inventory,
            ),
            patch.object(
                voice_library,
                "build_voice_backend_capabilities",
                return_value=self.capabilities,
            ),
            patch.object(
                voice_library,
                "validate_voice_aliases",
                return_value=self.aliases,
            ),
        ):
            return build_voice_library(
                root_dir=self.root,
                project_id="project_1",
                return_route="#/voices?method=clone",
            )

    def by_method(self, payload: dict, method: str) -> list[dict]:
        return [item for item in payload["voices"] if item["method"] == method]

    def test_library_includes_all_six_methods_without_second_assignment_store(self) -> None:
        payload = self.build()
        methods = {item["method"] for item in payload["methods"]}
        self.assertEqual(methods, set(voice_library.METHOD_ORDER))
        self.assertTrue(payload["cast_is_authoritative"])
        self.assertFalse(payload["assignment_mutation_supported"])
        self.assertTrue(all(not item["assignment_mutation_supported"] for item in payload["voices"]))
        self.assertEqual(payload["summary"]["method_counts"]["built_in"], 9)
        self.assertEqual(payload["summary"]["method_counts"]["designed"], 1)
        self.assertEqual(payload["summary"]["method_counts"]["supplied_recording"], 2)
        self.assertEqual(payload["summary"]["method_counts"]["instruction_controlled"], 1)
        self.assertEqual(payload["summary"]["method_counts"]["adapter"], 1)
        self.assertEqual(payload["summary"]["method_counts"]["alias"], 1)

    def test_standard_and_controlled_clone_capabilities_are_truthful(self) -> None:
        payload = self.build()
        methods = {item["method"]: item for item in payload["methods"]}
        standard = methods["supplied_recording"]
        self.assertTrue(standard["production_supported"])
        self.assertFalse(standard["instruction_supported"])
        self.assertIn("not sent", standard["message"])
        controlled = methods["instruction_controlled"]
        self.assertFalse(controlled["production_supported"])
        self.assertTrue(controlled["preview_supported"])
        self.assertTrue(controlled["instruction_supported"])
        self.assertEqual(controlled["state"], "experimental_unaccepted")
        resource = self.by_method(payload, "instruction_controlled")[0]
        self.assertEqual(resource["technical_details"]["backend"], "qwen3_instruction_controlled")
        self.assertFalse(resource["capability"]["production_supported"])

    def test_usage_links_return_to_cast_without_silent_assignment(self) -> None:
        payload = self.build()
        ryan = next(
            item
            for item in self.by_method(payload, "built_in")
            if item["key"] == "Ryan"
        )
        self.assertEqual(ryan["usage_count"], 1)
        self.assertEqual(ryan["usage"][0]["character_id"], "character_narrator")
        self.assertEqual(ryan["assignment_route"]["destination"], "cast")
        self.assertIn("character_narrator", ryan["assignment_route"]["hash"])
        self.assertIn("return=%23%2Fvoices", ryan["assignment_route"]["hash"])

    def test_physical_voice_assets_expose_preview_or_native_entry_points(self) -> None:
        payload = self.build()
        supplied = self.by_method(payload, "supplied_recording")
        self.assertTrue(all(item["preview"]["available"] for item in supplied))
        self.assertEqual(
            {item["preview"]["url"] for item in supplied},
            {"/clone_voices/bernice.wav", "/clone_voices/doctor.wav"},
        )
        designed = self.by_method(payload, "designed")[0]
        self.assertTrue(designed["preview"]["available"])
        self.assertEqual(designed["native_route"]["destination"], "more")
        adapter = self.by_method(payload, "adapter")[0]
        self.assertFalse(adapter["preview"]["available"])
        self.assertEqual(adapter["native_route"]["destination"], "more")

    def test_alias_is_a_resolution_view_not_a_duplicate_voice_configuration(self) -> None:
        payload = self.build()
        alias = self.by_method(payload, "alias")[0]
        self.assertEqual(alias["key"], "JOHN SMITH")
        self.assertEqual(alias["technical_details"]["target"], "DOCTOR")
        self.assertEqual(
            alias["technical_details"]["resolution_chain"],
            ["JOHN SMITH", "DOCTOR"],
        )
        self.assertIn("without duplicating", alias["description"])
        self.assertEqual(alias["native_route"]["destination"], "cast")

    def test_output_is_deterministic_and_contains_no_raw_voice_config(self) -> None:
        first = self.build()
        second = self.build()
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        rendered = json.dumps(first)
        self.assertNotIn("Exact Bernice transcript", rendered)
        self.assertNotIn("Exact Doctor transcript", rendered)
        self.assertNotIn("Dry, controlled, Scottish identity", rendered)
        self.assertNotIn("ref_text", rendered)
        self.assertNotIn("character_style", rendered)

    def test_invalid_root_and_config_fail_closed(self) -> None:
        with self.assertRaisesRegex(VoiceLibraryError, "unavailable or unsafe"):
            build_voice_library(root_dir=self.root / "missing")
        (self.root / "voice_config.json").write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(VoiceLibraryError, "must contain an object"):
            build_voice_library(root_dir=self.root)


if __name__ == "__main__":
    unittest.main()
