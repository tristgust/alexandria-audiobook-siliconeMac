from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from community_qwen_packs import install_qvoice_pack
import voice_library
from tests.test_qwen_voice_packs import qvoice_bytes
from voice_library import (
    VoiceLibraryError,
    build_voice_library,
    resolve_voice_library_assignment,
    resolve_voice_library_preview,
)


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

    def test_library_includes_all_six_methods_and_assigns_only_through_cast(self) -> None:
        payload = self.build()
        methods = {item["method"] for item in payload["methods"]}
        self.assertEqual(methods, set(voice_library.METHOD_ORDER))
        self.assertTrue(payload["cast_is_authoritative"])
        self.assertTrue(payload["assignment_mutation_supported"])
        built_ins = self.by_method(payload, "built_in")
        self.assertTrue(all(item["assignment_mutation_supported"] for item in built_ins))
        self.assertTrue(all(item["assignment"]["kind"] == "built_in" for item in built_ins))
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

    def test_reusable_saved_clone_is_assignable_without_exposing_transcript(self) -> None:
        reusable = self.root / "reusable"
        reference = reusable / "clone_voices" / "benny.wav"
        reference.parent.mkdir(parents=True)
        reference.write_bytes(b"saved-benny-audio")
        (reusable / "voice_config.json").write_text(
            json.dumps(
                {
                    "BERNICE": {
                        "type": "clone",
                        "voice": "Ryan",
                        "clone_backend": "qwen3_instruction_controlled",
                        "ref_audio": "clone_voices/benny.wav",
                        "ref_text": "Exact saved transcript.",
                        "controlled_clone_configuration_fingerprint": "b" * 64,
                    }
                }
            ),
            encoding="utf-8",
        )
        with (
            patch.object(voice_library, "inspect_cast_project", return_value=self.cast),
            patch.object(voice_library, "inspect_library_inventory", return_value=self.inventory),
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
            payload = build_voice_library(
                root_dir=self.root,
                project_id="project_1",
                reusable_root_dir=reusable,
            )
        saved = next(
            item
            for item in payload["voices"]
            if item["technical_details"].get("scope") == "reusable"
        )
        self.assertEqual(saved["name"], "Benny / Bernice")
        self.assertTrue(saved["assignment_mutation_supported"])
        self.assertTrue(saved["preview"]["available"])
        self.assertNotIn("Exact saved transcript", json.dumps(saved))
        assignment = resolve_voice_library_assignment(
            voice_id=saved["voice_id"],
            reusable_root_dir=reusable,
        )
        self.assertEqual(assignment["kind"], "reusable_clone")
        self.assertEqual(
            assignment["configuration"]["library_voice_id"],
            saved["voice_id"],
        )
        self.assertEqual(
            assignment["assets"][0]["relative_path"],
            "clone_voices/benny.wav",
        )

    def test_active_project_controlled_voice_is_assignable_as_alias(self) -> None:
        reference = self.root / "clone_voices" / "doctor.wav"
        reference.parent.mkdir(parents=True, exist_ok=True)
        reference.write_bytes(b"doctor-audio")
        config = json.loads((self.root / "voice_config.json").read_text(encoding="utf-8"))
        config["DOCTOR"]["controlled_clone_configuration_fingerprint"] = "d" * 64
        (self.root / "voice_config.json").write_text(
            json.dumps(config),
            encoding="utf-8",
        )
        with (
            patch.object(voice_library, "inspect_cast_project", return_value=self.cast),
            patch.object(voice_library, "inspect_library_inventory", return_value=self.inventory),
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
            payload = build_voice_library(
                root_dir=self.root,
                project_id="project_1",
            )
        project_voice = next(
            item
            for item in payload["voices"]
            if item["technical_details"].get("scope") == "project_configuration"
            and item["key"] == "DOCTOR"
        )
        self.assertTrue(project_voice["assignment_mutation_supported"])
        self.assertEqual(project_voice["assignment"]["kind"], "project_voice_alias")
        assignment = resolve_voice_library_assignment(
            voice_id=project_voice["voice_id"],
            reusable_root_dir=None,
            project_root_dir=self.root,
        )
        self.assertEqual(assignment["kind"], "project_voice_alias")
        self.assertEqual(assignment["target_configuration_key"], "DOCTOR")
        self.assertEqual(assignment["configuration"]["alias_of"], "DOCTOR")

    def test_community_qvoice_requires_review_then_becomes_cast_assignable(self) -> None:
        reusable = self.root / "reusable-packs"
        reusable.mkdir()
        source = self.root / "reader.qvoice"
        source.write_bytes(
            qvoice_bytes(reference_text=b"", reference_frames=0, flags=0b101)
        )
        installed = install_qvoice_pack(
            source_path=source,
            reusable_root=reusable,
        )

        patches = (
            patch.object(voice_library, "inspect_cast_project", return_value=self.cast),
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
        )
        with patches[0], patches[1], patches[2], patches[3]:
            pending = build_voice_library(
                root_dir=self.root,
                project_id="project_1",
                reusable_root_dir=reusable,
            )
        resource = next(
            item
            for item in pending["voices"]
            if item["technical_details"].get("community_pack_id")
            == installed["pack_id"]
        )
        self.assertEqual(resource["state"], "review_required")
        self.assertFalse(resource["assignment_mutation_supported"])

        from community_qwen_pack_store import read_manifest, write_manifest

        packs = read_manifest(reusable)
        packs[installed["pack_id"]].update(
            {
                "state": "approved",
                "production_supported": True,
                "approval_fingerprint": "a" * 64,
                "preview_fingerprint": "a" * 64,
                "persistent_description": "An older English storyteller.",
                "preview": (
                    f"community_qwen_packs/{installed['pack_id']}/preview.wav"
                ),
            }
        )
        preview = reusable / packs[installed["pack_id"]]["preview"]
        preview.write_bytes(b"RIFF-preview")
        packs[installed["pack_id"]]["preview_sha256"] = hashlib.sha256(
            preview.read_bytes()
        ).hexdigest()
        write_manifest(reusable, packs)

        patches = (
            patch.object(voice_library, "inspect_cast_project", return_value=self.cast),
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
        )
        with patches[0], patches[1], patches[2], patches[3]:
            approved = build_voice_library(
                root_dir=self.root,
                project_id="project_1",
                reusable_root_dir=reusable,
            )
        resource = next(
            item
            for item in approved["voices"]
            if item["technical_details"].get("community_pack_id")
            == installed["pack_id"]
        )
        self.assertEqual(resource["state"], "approved")
        self.assertTrue(resource["assignment_mutation_supported"])

        with patches[0], patches[1], patches[2], patches[3]:
            same_root = build_voice_library(
                root_dir=reusable,
                project_id="project_1",
                reusable_root_dir=reusable,
            )
        same_root_resource = next(
            (
                item
                for item in same_root["voices"]
                if item["technical_details"].get("community_pack_id")
                == installed["pack_id"]
            ),
            None,
        )
        self.assertIsNotNone(same_root_resource)
        self.assertTrue(same_root_resource["assignment_mutation_supported"])

        assignment = resolve_voice_library_assignment(
            voice_id=resource["voice_id"],
            reusable_root_dir=reusable,
        )
        self.assertEqual(assignment["kind"], "community_qvoice")
        self.assertEqual(assignment["configuration"]["type"], "community_qvoice")
        self.assertEqual(
            assignment["configuration"]["community_pack_id"],
            installed["pack_id"],
        )
        self.assertNotIn("license", assignment["configuration"])

        self.assertEqual(
            resolve_voice_library_preview(
                voice_id=resource["voice_id"],
                reusable_root_dir=reusable,
            ),
            preview.resolve(),
        )
        preview.write_bytes(b"tampered-preview")
        with self.assertRaises(VoiceLibraryError) as caught:
            resolve_voice_library_preview(
                voice_id=resource["voice_id"],
                reusable_root_dir=reusable,
            )
        self.assertEqual(caught.exception.code, "qwen_pack_preview_changed")

    def test_reusable_designed_voice_is_scoped_previewable_and_assignable(self) -> None:
        reusable = self.root / "reusable"
        designed = reusable / "designed_voices"
        designed.mkdir(parents=True)
        (reusable / "voice_config.json").write_text("{}", encoding="utf-8")
        (designed / "storyteller.wav").write_bytes(b"designed-audio")
        (designed / "manifest.json").write_text(json.dumps([{
            "id": "storyteller",
            "name": "Storyteller",
            "description": "Warm, precise, and lightly weathered.",
            "sample_text": "A reusable audition.",
            "filename": "storyteller.wav",
        }]), encoding="utf-8")
        with (
            patch.object(voice_library, "inspect_cast_project", return_value=self.cast),
            patch.object(voice_library, "inspect_library_inventory", return_value=self.inventory),
            patch.object(voice_library, "build_voice_backend_capabilities", return_value=self.capabilities),
            patch.object(voice_library, "validate_voice_aliases", return_value=self.aliases),
        ):
            payload = build_voice_library(
                root_dir=self.root,
                project_id="project_1",
                reusable_root_dir=reusable,
            )
        saved = next(item for item in payload["voices"] if item["key"] == "reusable:storyteller")
        self.assertEqual(saved["technical_details"]["scope"], "reusable")
        self.assertTrue(saved["preview"]["available"])
        self.assertTrue(saved["assignment_mutation_supported"])
        assignment = resolve_voice_library_assignment(
            voice_id=saved["voice_id"], reusable_root_dir=reusable,
        )
        self.assertEqual(assignment["kind"], "reusable_designed")
        self.assertEqual(assignment["configuration"]["type"], "design")

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
