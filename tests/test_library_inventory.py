from __future__ import annotations

import hashlib
import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import library_inventory

from library_inventory import (
    LibraryInventoryError,
    build_library_delete_impact,
    get_library_artifact,
    inspect_library_inventory,
    validate_library_delete_request,
)


class LibraryInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self._write_fixture()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_json(self, relative: str, value) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def _write_file(self, relative: str, content: bytes = b"artifact") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def _write_fixture(self) -> None:
        self._write_file("designed_voices/voice1.wav", b"designed")
        self._write_json(
            "designed_voices/voice1.json",
            {
                "name": "Designed Voice One",
                "description": "A measured voice.",
                "created_at_utc": "2026-07-20T12:00:00Z",
            },
        )
        self._write_json(
            "designed_voices/manifest.json",
            [
                {
                    "id": "voice1",
                    "name": "Designed Voice One",
                    "filename": "voice1.wav",
                }
            ],
        )
        self._write_file("clone_voices/clone1.mp3", b"clone")
        self._write_json(
            "clone_voices/manifest.json",
            [
                {
                    "id": "clone1",
                    "name": "Clone One",
                    "filename": "clone1.mp3",
                }
            ],
        )
        preparer = self.root / "preparer_output" / "My output.zip"
        preparer.parent.mkdir(parents=True)
        with zipfile.ZipFile(preparer, "w") as archive:
            archive.writestr("metadata.jsonl", "{}\n")
        self._write_json(
            "dataset_builder/project1/state.json",
            {
                "name": "Dataset Project One",
                "status": "draft",
                "samples": [],
            },
        )
        self._write_json(
            "lora_datasets/dataset1/manifest.json",
            {
                "name": "Dataset One",
                "dataset_id": "dataset1",
                "status": "ready",
            },
        )
        self._write_json(
            "lora_models/adapter1/training_meta.json",
            {
                "name": "Adapter One",
                "adapter_id": "adapter1",
                "dataset_id": "dataset1",
                "status": "validated",
                "production_assignment_supported": True,
            },
        )
        self._write_file("lora_models/adapter1/adapter.safetensors", b"adapter")
        self._write_json(
            "voice_training_projects/character_doctor/project.json",
            {
                "character_id": "character_doctor",
                "name": "Doctor preparation",
                "status": "ready",
                "adapter_path": "lora_models/adapter1",
            },
        )
        self._write_json(
            "voice_training_projects/character_doctor/reference_bank.json",
            {
                "character_id": "character_doctor",
                "name": "Doctor reference bank",
                "status": "approved",
                "source_audio": (
                    "voice_training_projects/character_doctor/recordings/source/doctor.wav"
                ),
            },
        )
        self._write_file(
            "voice_training_projects/character_doctor/recordings/source/doctor.wav",
            b"doctor-owned-recording",
        )
        self._write_json(
            "voice_config.json",
            {
                "NARRATOR": {
                    "type": "clone",
                    "ref_audio": "designed_voices/voice1.wav",
                },
                "DOCTOR": {
                    "type": "clone",
                    "ref_audio": "clone_voices/clone1.mp3",
                    "reference_bank_path": (
                        "voice_training_projects/character_doctor/reference_bank.json"
                    ),
                },
                "ADAPTER": {
                    "type": "lora",
                    "adapter_id": "adapter1",
                    "adapter_path": "lora_models/adapter1",
                },
            },
        )
        self._write_json(
            "external_workflows/history/operation.json",
            {
                "operation": "dataset_export",
                "dataset_id": "dataset1",
                "dataset_path": "lora_datasets/dataset1",
            },
        )

    def _write_workflow_fixture(self) -> None:
        source = self._write_file("source/book.epub", b"source-book")
        self._write_json(
            "alexandria-project.json",
            {
                "project_id": "project_1",
                "name": "Library Project",
                "created_at_utc": "2026-07-20T10:00:00Z",
                "updated_at_utc": "2026-07-21T10:00:00Z",
                "source": {
                    "title": "The Library Book",
                    "author": "Alexandria QA",
                    "original_filename": source.name,
                    "original_relative_path": "source/book.epub",
                    "type": "epub",
                    "source_language": "English",
                    "output_language": "English",
                },
            },
        )
        self._write_json(
            "annotated_script.json",
            [{"speaker": "NARRATOR", "text": "Text.", "instruct": "Neutral."}],
        )
        self._write_file("voicelines/chunk-1.wav", b"current-audio")
        self._write_json(
            "chunks.json",
            [
                {
                    "id": 1,
                    "speaker": "NARRATOR",
                    "text": "Text.",
                    "status": "current",
                    "audio_path": "voicelines/chunk-1.wav",
                },
                {
                    "id": 2,
                    "speaker": "NARRATOR",
                    "text": "More text.",
                    "status": "pending",
                    "audio_path": None,
                },
            ],
        )
        self._write_json(
            "audio_validity.json",
            {
                "schema_version": 1,
                "stale": False,
                "updated_at_utc": "2026-07-21T11:00:00Z",
            },
        )
        mp3 = self._write_file("cloned_audiobook.mp3", b"verified-mp3")
        self._write_file("audacity_export.zip", b"legacy-audacity")
        self._write_json(
            "export_build.json",
            {
                "schema_version": 1,
                "built_at_utc": "2026-07-21T12:00:00Z",
                "outputs": {
                    "mp3": {
                        "sha256": hashlib.sha256(mp3.read_bytes()).hexdigest(),
                        "size_bytes": mp3.stat().st_size,
                        "duration_ms": 90000,
                        "built_at_utc": "2026-07-21T12:00:00Z",
                    }
                },
            },
        )

    def _inventory(self, **kwargs):
        return inspect_library_inventory(
            root_dir=self.root,
            project_id="project_1",
            character_id="character_doctor",
            return_route="#/cast?project=project_1&character=character_doctor",
            **kwargs,
        )

    @staticmethod
    def _by_kind(inventory, kind):
        return [item for item in inventory["artifacts"] if item["kind"] == kind]

    def test_inventory_unifies_every_artifact_family_without_copying_files(self) -> None:
        protected = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.root.rglob("*")
            if path.is_file()
        }
        inventory = self._inventory()
        self.assertEqual(inventory["schema_version"], 1)
        self.assertEqual(inventory["summary"]["artifact_count"], 9)
        self.assertEqual(
            {item["kind"] for item in inventory["artifacts"]},
            {
                "designed_voice",
                "clone_reference",
                "preparer_output",
                "dataset_builder_project",
                "lora_dataset",
                "lora_adapter",
                "voice_preparation_project",
                "expressive_reference_bank",
                "owned_recording",
            },
        )
        self.assertEqual(
            len({item["artifact_id"] for item in inventory["artifacts"]}),
            9,
        )
        self.assertEqual(
            {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected},
            protected,
        )

    def test_workflow_inventory_includes_source_audio_and_outputs(self) -> None:
        self._write_workflow_fixture()
        protected = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.root.rglob("*")
            if path.is_file()
        }
        inventory = self._inventory()
        workflow = {
            kind: self._by_kind(inventory, kind)
            for kind in ("source_book", "production_audio", "export_output")
        }
        self.assertEqual(len(workflow["source_book"]), 1)
        self.assertEqual(len(workflow["production_audio"]), 1)
        self.assertEqual(len(workflow["export_output"]), 2)

        source = workflow["source_book"][0]
        self.assertEqual(source["name"], "The Library Book")
        self.assertEqual(source["provenance"]["author"], "Alexandria QA")
        self.assertEqual(source["native_route"]["destination"], "script")
        self.assertIn("project=project_1", source["native_route"]["hash"])
        self.assertFalse(source["delete"]["supported"])
        self.assertIn(
            "annotated_script.json",
            {item["source"] for item in source["usage"]},
        )

        audio = workflow["production_audio"][0]
        self.assertEqual(audio["state"], "available")
        self.assertEqual(audio["file_count"], 1)
        self.assertEqual(audio["provenance"]["total_chunks"], 2)
        self.assertEqual(audio["provenance"]["current_chunk_count"], 1)
        self.assertEqual(audio["provenance"]["pending_chunk_count"], 1)
        self.assertEqual(audio["native_route"]["destination"], "produce")
        self.assertIn("chunks.json", {item["source"] for item in audio["usage"]})

        outputs = {item["key"]: item for item in workflow["export_output"]}
        self.assertEqual(outputs["mp3"]["state"], "available")
        self.assertEqual(outputs["mp3"]["provenance"]["format"], "mp3")
        self.assertEqual(outputs["mp3"]["native_route"]["destination"], "export")
        self.assertEqual(outputs["audacity"]["state"], "legacy_unverified")
        self.assertTrue(all(not item["delete"]["supported"] for item in outputs.values()))
        self.assertEqual(
            {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected},
            protected,
        )

    def test_export_hash_mismatch_is_visible_and_blocked(self) -> None:
        self._write_workflow_fixture()
        (self.root / "cloned_audiobook.mp3").write_bytes(b"changed-after-build")
        inventory = self._inventory(kind="export_output")
        mp3 = next(item for item in inventory["artifacts"] if item["key"] == "mp3")
        self.assertEqual(mp3["state"], "invalid")
        self.assertIn("hash does not match", mp3["metadata_error"])
        impact = build_library_delete_impact(
            inventory=inventory,
            artifact_id=mp3["artifact_id"],
        )
        self.assertFalse(impact["safe_to_delete"])
        self.assertFalse(impact["supported"])

    def test_external_source_path_is_visible_as_invalid_not_read(self) -> None:
        outside = self.root.parent / f"{self.root.name}-outside.txt"
        outside.write_bytes(b"outside")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        self._write_json(
            "state.json",
            {"input_file_path": str(outside), "book_title": "Unsafe source"},
        )
        inventory = self._inventory(kind="source_book")
        source = inventory["artifacts"][0]
        self.assertEqual(source["state"], "invalid")
        self.assertIn("outside", source["metadata_error"])
        self.assertEqual(source["size_bytes"], (self.root / "state.json").stat().st_size)

    def test_unsafe_user_filename_is_inventoried_but_not_delete_routed(self) -> None:
        artifact = self._by_kind(self._inventory(), "preparer_output")[0]
        self.assertEqual(artifact["source_key"], "My output.zip")
        self.assertTrue(artifact["key"].startswith("artifact_"))
        self.assertFalse(artifact["delete"]["supported"])
        self.assertNotIn("My output.zip", artifact["artifact_id"])

    def test_current_and_historical_dependencies_block_authoritative_deletion(self) -> None:
        inventory = self._inventory()
        designed = self._by_kind(inventory, "designed_voice")[0]
        clone = self._by_kind(inventory, "clone_reference")[0]
        adapter = self._by_kind(inventory, "lora_adapter")[0]
        dataset = self._by_kind(inventory, "lora_dataset")[0]
        self.assertEqual(designed["usage"][0]["source"], "voice_config.json")
        self.assertEqual(clone["usage"][0]["source"], "voice_config.json")
        self.assertTrue(adapter["delete"]["blocked"])
        self.assertIn(
            "voice_config.json",
            {item["source"] for item in adapter["usage"]},
        )
        self.assertIn(
            "history",
            {item["scope"] for item in dataset["usage"]},
        )
        self.assertTrue(dataset["delete"]["blocked"])

    def test_plain_prose_does_not_create_filename_dependency(self) -> None:
        self._write_json(
            "external_workflows/history/prose.json",
            {"description": "voice1 clone1 doctor adapter1 all appear in prose"},
        )
        inventory = self._inventory()
        designed = self._by_kind(inventory, "designed_voice")[0]
        clone = self._by_kind(inventory, "clone_reference")[0]
        self.assertEqual(
            {item["source"] for item in designed["usage"]},
            {"voice_config.json"},
        )
        self.assertEqual(
            {item["source"] for item in clone["usage"]},
            {"voice_config.json"},
        )

    def test_dependency_resolution_scales_to_large_distinct_inventory(self) -> None:
        count = 1_000
        artifacts = [
            {
                "kind": "lora_adapter",
                "state": "available",
                "technical_details": {
                    "identity_aliases": [f"packs/voice-{index}.json"],
                    "relative_path": f"artifacts/voice-{index}",
                },
                "delete": {"supported": True},
            }
            for index in range(count)
        ]
        references = [
            {
                "value": f"packs/voice-{index}.json",
                "scope": "history",
                "source_relative_path": f"history/use-{index}.json",
                "character_id": None,
            }
            for index in range(count)
        ]
        started = time.perf_counter()
        with patch.object(library_inventory, "_dependency_index", return_value=references):
            library_inventory._apply_dependencies(self.root, artifacts)
        elapsed = time.perf_counter() - started

        self.assertTrue(all(item["dependency_count"] == 1 for item in artifacts))
        self.assertLess(elapsed, 1.0)

    def test_dependency_resolution_skips_embedded_payloads_too_large_to_be_paths(self) -> None:
        artifact = {
            "kind": "lora_adapter",
            "state": "available",
            "technical_details": {
                "identity_aliases": ["packs/voice.json"],
                "relative_path": "artifacts/voice",
            },
            "delete": {"supported": True},
        }
        embedded_payload = ("Wwog" * 700_000) + ("/noise" * 400)
        references = [
            {
                "value": embedded_payload,
                "scope": "history",
                "source_relative_path": "history/embedded-payload.json",
                "character_id": None,
            },
            {
                "value": "packs/voice.json",
                "scope": "current",
                "source_relative_path": "voice_config.json",
                "character_id": None,
            },
        ]
        started = time.perf_counter()
        with patch.object(library_inventory, "_dependency_index", return_value=references):
            library_inventory._apply_dependencies(self.root, [artifact])
        elapsed = time.perf_counter() - started

        self.assertEqual(artifact["dependency_count"], 1)
        self.assertEqual(artifact["usage"][0]["source"], "voice_config.json")
        self.assertLess(elapsed, 0.5)

    def test_unreferenced_dataset_builder_project_has_safe_existing_delete_route(self) -> None:
        inventory = self._inventory()
        artifact = self._by_kind(inventory, "dataset_builder_project")[0]
        self.assertTrue(artifact["delete"]["supported"])
        self.assertFalse(artifact["delete"]["blocked"])
        impact = build_library_delete_impact(
            inventory=inventory,
            artifact_id=artifact["artifact_id"],
        )
        self.assertTrue(impact["safe_to_delete"])
        self.assertEqual(
            impact["delete_endpoint"],
            "/api/dataset_builder/project1",
        )
        self.assertEqual(impact["confirm_name"], "Dataset Project One")

    def test_unsupported_artifact_never_claims_safe_delete(self) -> None:
        inventory = self._inventory()
        project = self._by_kind(inventory, "voice_preparation_project")[0]
        impact = build_library_delete_impact(
            inventory=inventory,
            artifact_id=project["artifact_id"],
        )
        self.assertFalse(impact["supported"])
        self.assertTrue(impact["blocked"])
        self.assertIsNone(impact["delete_endpoint"])

    def test_delete_validation_rechecks_inventory_artifact_confirmation_and_dependencies(self) -> None:
        inventory = self._inventory()
        artifact = self._by_kind(inventory, "dataset_builder_project")[0]
        impact = build_library_delete_impact(
            inventory=inventory,
            artifact_id=artifact["artifact_id"],
        )
        validated = validate_library_delete_request(
            inventory=inventory,
            artifact_id=artifact["artifact_id"],
            expected_inventory_fingerprint=inventory["inventory_fingerprint"],
            expected_artifact_fingerprint=impact["artifact_fingerprint"],
            confirm_name=impact["confirm_name"],
        )
        self.assertEqual(validated, impact)
        with self.assertRaises(LibraryInventoryError) as inventory_error:
            validate_library_delete_request(
                inventory=inventory,
                artifact_id=artifact["artifact_id"],
                expected_inventory_fingerprint="stale",
                expected_artifact_fingerprint=impact["artifact_fingerprint"],
                confirm_name=impact["confirm_name"],
            )
        self.assertEqual(
            inventory_error.exception.code,
            "library_inventory_changed",
        )
        with self.assertRaises(LibraryInventoryError) as artifact_error:
            validate_library_delete_request(
                inventory=inventory,
                artifact_id=artifact["artifact_id"],
                expected_inventory_fingerprint=inventory["inventory_fingerprint"],
                expected_artifact_fingerprint="stale",
                confirm_name=impact["confirm_name"],
            )
        self.assertEqual(
            artifact_error.exception.code,
            "library_artifact_changed",
        )
        with self.assertRaises(LibraryInventoryError) as confirmation_error:
            validate_library_delete_request(
                inventory=inventory,
                artifact_id=artifact["artifact_id"],
                expected_inventory_fingerprint=inventory["inventory_fingerprint"],
                expected_artifact_fingerprint=impact["artifact_fingerprint"],
                confirm_name="wrong",
            )
        self.assertEqual(
            confirmation_error.exception.code,
            "library_delete_confirmation_mismatch",
        )

        clone = self._by_kind(inventory, "clone_reference")[0]
        clone_impact = build_library_delete_impact(
            inventory=inventory,
            artifact_id=clone["artifact_id"],
        )
        with self.assertRaises(LibraryInventoryError) as dependency_error:
            validate_library_delete_request(
                inventory=inventory,
                artifact_id=clone["artifact_id"],
                expected_inventory_fingerprint=inventory["inventory_fingerprint"],
                expected_artifact_fingerprint=clone_impact["artifact_fingerprint"],
                confirm_name=clone_impact["confirm_name"],
            )
        self.assertEqual(
            dependency_error.exception.code,
            "library_delete_blocked",
        )

    def test_contextual_voice_lab_routes_preserve_project_character_and_return(self) -> None:
        inventory = self._inventory()
        for artifact in inventory["artifacts"]:
            route = artifact["voice_lab"]
            self.assertEqual(route["destination"], "more")
            self.assertEqual(route["context"]["project"], "project_1")
            self.assertEqual(
                route["context"]["character"],
                artifact.get("character_id") or "character_doctor",
            )
            self.assertEqual(
                route["context"]["return"],
                "#/cast?project=project_1&character=character_doctor",
            )
            self.assertIn("source=library_", route["hash"])

    def test_filters_search_and_missing_artifact_are_explicit(self) -> None:
        adapters = self._inventory(kind="lora_adapter")
        self.assertEqual(adapters["summary"]["visible_count"], 1)
        searched = self._inventory(search="doctor reference bank")
        self.assertEqual(searched["summary"]["visible_count"], 1)
        self.assertEqual(
            searched["artifacts"][0]["kind"],
            "expressive_reference_bank",
        )
        with self.assertRaises(LibraryInventoryError) as kind_error:
            self._inventory(kind="bogus")
        self.assertEqual(kind_error.exception.code, "library_kind_invalid")
        with self.assertRaises(LibraryInventoryError) as missing_error:
            get_library_artifact(self._inventory(), "library_missing")
        self.assertEqual(
            missing_error.exception.code,
            "library_artifact_not_found",
        )

    def test_invalid_metadata_is_visible_and_not_deletable(self) -> None:
        (self.root / "dataset_builder" / "broken").mkdir()
        (self.root / "dataset_builder" / "broken" / "state.json").write_text(
            "{not-json",
            encoding="utf-8",
        )
        inventory = self._inventory()
        broken = next(
            item
            for item in inventory["artifacts"]
            if item["kind"] == "dataset_builder_project"
            and item["source_key"] == "broken"
        )
        self.assertEqual(broken["state"], "invalid")
        self.assertTrue(broken["delete"]["blocked"])
        self.assertIn("invalid JSON", broken["metadata_error"])

    def test_manifest_entries_are_assets_not_fake_manifest_artifacts(self) -> None:
        (self.root / "designed_voices" / "voice1.json").unlink()
        self._write_json(
            "designed_voices/manifest.json",
            [
                {
                    "id": "voice1",
                    "name": "Designed Voice One",
                    "filename": "voice1.wav",
                }
            ],
        )
        self._write_json(
            "clone_voices/manifest.json",
            [
                {
                    "id": "clone1",
                    "name": "Clone One",
                    "filename": "clone1.mp3",
                }
            ],
        )
        inventory = self._inventory()
        designed = self._by_kind(inventory, "designed_voice")
        clones = self._by_kind(inventory, "clone_reference")
        self.assertEqual([item["source_key"] for item in designed], ["voice1"])
        self.assertEqual([item["source_key"] for item in clones], ["clone1"])
        self.assertNotIn(
            "manifest",
            {item["source_key"] for item in inventory["artifacts"]},
        )
        self.assertEqual(designed[0]["name"], "Designed Voice One")
        self.assertEqual(clones[0]["name"], "Clone One")
        self.assertTrue(designed[0]["delete"]["supported"])

    def test_empty_root_is_valid_read_only_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            inventory = inspect_library_inventory(root_dir=temporary)
        self.assertEqual(inventory["summary"]["artifact_count"], 0)
        self.assertEqual(inventory["artifacts"], [])
        self.assertTrue(inventory["inventory_fingerprint"])


if __name__ == "__main__":
    unittest.main()
