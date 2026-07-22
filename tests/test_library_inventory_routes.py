from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module


class LibraryInventoryRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.dataset_builder_root = self.root / "dataset_builder"
        self.project_dir = self.dataset_builder_root / "safe_project"
        self.project_dir.mkdir(parents=True)
        (self.project_dir / "state.json").write_text(
            json.dumps(
                {
                    "name": "Safe Project",
                    "status": "draft",
                    "samples": [],
                }
            ),
            encoding="utf-8",
        )
        designed_dir = self.root / "designed_voices"
        designed_dir.mkdir()
        (designed_dir / "voice1.wav").write_bytes(b"designed")
        (designed_dir / "voice1.json").write_text(
            json.dumps({"name": "Designed One"}),
            encoding="utf-8",
        )
        (designed_dir / "manifest.json").write_text(
            json.dumps(
                [
                    {
                        "id": "voice1",
                        "name": "Designed One",
                        "filename": "voice1.wav",
                    }
                ]
            ),
            encoding="utf-8",
        )
        clone_dir = self.root / "clone_voices"
        clone_dir.mkdir()
        (clone_dir / "clone1.mp3").write_bytes(b"clone")
        (clone_dir / "manifest.json").write_text(
            json.dumps(
                [
                    {
                        "id": "clone1",
                        "name": "Clone One",
                        "filename": "clone1.mp3",
                    }
                ]
            ),
            encoding="utf-8",
        )
        (self.root / "voice_config.json").write_text(
            json.dumps(
                {
                    "NARRATOR": {
                        "type": "clone",
                        "ref_audio": "clone_voices/clone1.mp3",
                    }
                }
            ),
            encoding="utf-8",
        )
        self.process_backup = {
            key: copy.deepcopy(app_module.process_state.get(key, {}))
            for key in (
                "audio",
                "dataset_gen",
                "dataset_builder",
                "preparer",
                "batch_preparer",
                "lora_training",
            )
        }
        for key in self.process_backup:
            app_module.process_state.setdefault(key, {})["running"] = False
        self.patchers = [
            patch.object(app_module, "ROOT_DIR", str(self.root)),
            patch.object(
                app_module,
                "DATASET_BUILDER_DIR",
                str(self.dataset_builder_root),
            ),
            patch.object(
                app_module,
                "DESIGNED_VOICES_DIR",
                str(self.root / "designed_voices"),
            ),
            patch.object(
                app_module,
                "DESIGNED_VOICES_MANIFEST",
                str(self.root / "designed_voices" / "manifest.json"),
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        for key, value in self.process_backup.items():
            app_module.process_state[key] = value
        self.temporary.cleanup()

    def _write_workflow_fixture(self) -> None:
        source = self.root / "source" / "route-book.epub"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"route-source")
        (self.root / "alexandria-project.json").write_text(
            json.dumps(
                {
                    "project_id": "project_1",
                    "name": "Route Library Project",
                    "source": {
                        "title": "Route Book",
                        "author": "Alexandria QA",
                        "original_filename": source.name,
                        "original_relative_path": "source/route-book.epub",
                        "type": "epub",
                    },
                }
            ),
            encoding="utf-8",
        )
        (self.root / "annotated_script.json").write_text(
            json.dumps(
                [{"speaker": "NARRATOR", "text": "Text.", "instruct": "Neutral."}]
            ),
            encoding="utf-8",
        )
        voicelines = self.root / "voicelines"
        voicelines.mkdir()
        (voicelines / "chunk.wav").write_bytes(b"route-audio")
        (self.root / "chunks.json").write_text(
            json.dumps(
                [
                    {
                        "id": 1,
                        "speaker": "NARRATOR",
                        "text": "Text.",
                        "status": "current",
                        "audio_path": "voicelines/chunk.wav",
                    }
                ]
            ),
            encoding="utf-8",
        )
        (self.root / "audio_validity.json").write_text(
            json.dumps({"schema_version": 1, "stale": False}),
            encoding="utf-8",
        )
        output = self.root / "cloned_audiobook.mp3"
        output.write_bytes(b"route-output")
        (self.root / "export_build.json").write_text(
            json.dumps(
                {
                    "outputs": {
                        "mp3": {
                            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                            "size_bytes": output.stat().st_size,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

    def _context(self) -> dict:
        return {
            "project_id": "project_1",
            "character_id": "character_1",
            "return_route": "#/cast?project=project_1&character=character_1",
        }

    def _inventory(self) -> dict:
        response = self.client.get(
            "/api/library",
            params=self._context(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @staticmethod
    def _artifact(inventory: dict, kind: str) -> dict:
        return next(item for item in inventory["artifacts"] if item["kind"] == kind)

    def _impact(self, artifact_id: str) -> dict:
        response = self.client.post(
            f"/api/library/artifacts/{artifact_id}/delete-impact",
            json=self._context(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _protected_hashes(self) -> dict[str, str]:
        result = {}
        for relative in (
            "dataset_builder/safe_project/state.json",
            "designed_voices/voice1.wav",
            "designed_voices/voice1.json",
            "designed_voices/manifest.json",
            "clone_voices/clone1.mp3",
            "clone_voices/manifest.json",
            "voice_config.json",
        ):
            path = self.root / relative
            result[relative] = (
                hashlib.sha256(path.read_bytes()).hexdigest()
                if path.is_file()
                else "<absent>"
            )
        return result

    def test_routes_are_registered_once(self) -> None:
        expected = {
            ("/api/library", "GET"),
            ("/api/library/artifacts/{artifact_id}", "GET"),
            (
                "/api/library/artifacts/{artifact_id}/delete-impact",
                "POST",
            ),
            ("/api/library/artifacts/{artifact_id}", "DELETE"),
        }
        registered = []
        for route in app_module.app.routes:
            methods = getattr(route, "methods", set())
            for method in methods:
                pair = (route.path, method)
                if pair in expected:
                    registered.append(pair)
        self.assertEqual(set(registered), expected)
        self.assertEqual(len(registered), len(expected))

    def test_inventory_and_artifact_reads_are_model_free_and_file_pure(self) -> None:
        before = self._protected_hashes()
        with (
            patch.object(
                app_module.project_manager,
                "get_engine",
                side_effect=AssertionError("Library read must not load TTS"),
            ),
            patch.object(
                app_module,
                "download_or_repair_model",
                side_effect=AssertionError("Library read must not download models"),
            ),
            patch.object(
                app_module,
                "build_runtime_client",
                side_effect=AssertionError("Library read must not connect to LLM"),
            ),
        ):
            inventory = self._inventory()
            project = self._artifact(inventory, "dataset_builder_project")
            response = self.client.get(
                f"/api/library/artifacts/{project['artifact_id']}",
                params=self._context(),
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["artifact_id"], project["artifact_id"])
        self.assertEqual(
            response.json()["voice_lab"]["context"]["return"],
            self._context()["return_route"],
        )
        self.assertEqual(before, self._protected_hashes())

    def test_workflow_artifacts_expose_native_stage_routes_without_delete(self) -> None:
        self._write_workflow_fixture()
        before = self._protected_hashes()
        inventory = self._inventory()
        expected = {
            "source_book": "script",
            "production_audio": "produce",
            "export_output": "export",
        }
        for kind, destination in expected.items():
            artifact = self._artifact(inventory, kind)
            response = self.client.get(
                f"/api/library/artifacts/{artifact['artifact_id']}",
                params=self._context(),
            )
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["native_route"]["destination"], destination)
            self.assertEqual(
                payload["native_route"]["context"]["return"],
                self._context()["return_route"],
            )
            self.assertFalse(payload["delete"]["supported"])
            impact = self._impact(artifact["artifact_id"])
            self.assertFalse(impact["safe_to_delete"])
            self.assertFalse(impact["supported"])
        self.assertEqual(before, self._protected_hashes())

    def test_delete_impact_exposes_safe_existing_authoritative_route(self) -> None:
        inventory = self._inventory()
        project = self._artifact(inventory, "dataset_builder_project")
        impact = self._impact(project["artifact_id"])
        self.assertTrue(impact["safe_to_delete"])
        self.assertEqual(
            impact["delete_endpoint"],
            "/api/dataset_builder/safe_project",
        )
        self.assertEqual(impact["confirm_name"], "Safe Project")

    def test_guarded_delete_delegates_to_existing_handler_and_rechecks_absence(self) -> None:
        inventory = self._inventory()
        project = self._artifact(inventory, "dataset_builder_project")
        impact = self._impact(project["artifact_id"])
        response = self.client.request(
            "DELETE",
            f"/api/library/artifacts/{project['artifact_id']}",
            json={
                **self._context(),
                "expected_inventory_fingerprint": inventory[
                    "inventory_fingerprint"
                ],
                "expected_artifact_fingerprint": impact[
                    "artifact_fingerprint"
                ],
                "confirm_name": impact["confirm_name"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "deleted")
        self.assertEqual(response.json()["result"]["name"], "safe_project")
        self.assertFalse(self.project_dir.exists())
        self.assertTrue((self.root / "clone_voices" / "clone1.mp3").is_file())
        missing = self.client.get(
            f"/api/library/artifacts/{project['artifact_id']}"
        )
        self.assertEqual(missing.status_code, 404)

    def test_manifest_delete_ignores_unsupported_sidecar_remnant(self) -> None:
        inventory = self._inventory()
        designed = self._artifact(inventory, "designed_voice")
        impact = self._impact(designed["artifact_id"])
        self.assertTrue(impact["safe_to_delete"])
        response = self.client.request(
            "DELETE",
            f"/api/library/artifacts/{designed['artifact_id']}",
            json={
                **self._context(),
                "expected_inventory_fingerprint": inventory[
                    "inventory_fingerprint"
                ],
                "expected_artifact_fingerprint": impact[
                    "artifact_fingerprint"
                ],
                "confirm_name": impact["confirm_name"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse((self.root / "designed_voices" / "voice1.wav").exists())
        self.assertTrue((self.root / "designed_voices" / "voice1.json").is_file())
        updated = self._inventory()
        remnants = [
            item
            for item in updated["artifacts"]
            if item["kind"] == "designed_voice"
        ]
        self.assertEqual(len(remnants), 1)
        self.assertTrue(remnants[0]["source_key"].startswith("orphan-"))
        self.assertFalse(remnants[0]["delete"]["supported"])

    def test_stale_fingerprints_confirmation_and_dependencies_fail_closed(self) -> None:
        inventory = self._inventory()
        project = self._artifact(inventory, "dataset_builder_project")
        impact = self._impact(project["artifact_id"])
        base = {
            **self._context(),
            "expected_inventory_fingerprint": inventory["inventory_fingerprint"],
            "expected_artifact_fingerprint": impact["artifact_fingerprint"],
            "confirm_name": impact["confirm_name"],
        }
        stale_inventory = self.client.request(
            "DELETE",
            f"/api/library/artifacts/{project['artifact_id']}",
            json={**base, "expected_inventory_fingerprint": "stale"},
        )
        self.assertEqual(stale_inventory.status_code, 409)
        self.assertEqual(
            stale_inventory.json()["detail"]["code"],
            "library_inventory_changed",
        )
        stale_artifact = self.client.request(
            "DELETE",
            f"/api/library/artifacts/{project['artifact_id']}",
            json={**base, "expected_artifact_fingerprint": "stale"},
        )
        self.assertEqual(stale_artifact.status_code, 409)
        self.assertEqual(
            stale_artifact.json()["detail"]["code"],
            "library_artifact_changed",
        )
        confirmation = self.client.request(
            "DELETE",
            f"/api/library/artifacts/{project['artifact_id']}",
            json={**base, "confirm_name": "wrong"},
        )
        self.assertEqual(confirmation.status_code, 422)
        self.assertEqual(
            confirmation.json()["detail"]["code"],
            "library_delete_confirmation_mismatch",
        )

        clone = self._artifact(inventory, "clone_reference")
        clone_impact = self._impact(clone["artifact_id"])
        blocked = self.client.request(
            "DELETE",
            f"/api/library/artifacts/{clone['artifact_id']}",
            json={
                **self._context(),
                "expected_inventory_fingerprint": inventory[
                    "inventory_fingerprint"
                ],
                "expected_artifact_fingerprint": clone_impact[
                    "artifact_fingerprint"
                ],
                "confirm_name": clone_impact["confirm_name"],
            },
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(
            blocked.json()["detail"]["code"],
            "library_delete_blocked",
        )
        self.assertTrue((self.root / "clone_voices" / "clone1.mp3").is_file())
        self.assertTrue(self.project_dir.is_dir())

    def test_running_operation_blocks_delete_before_dispatch(self) -> None:
        inventory = self._inventory()
        project = self._artifact(inventory, "dataset_builder_project")
        impact = self._impact(project["artifact_id"])
        app_module.process_state["audio"]["running"] = True
        with patch.object(
            app_module,
            "dataset_builder_delete",
            side_effect=AssertionError("delete must not dispatch"),
        ):
            response = self.client.request(
                "DELETE",
                f"/api/library/artifacts/{project['artifact_id']}",
                json={
                    **self._context(),
                    "expected_inventory_fingerprint": inventory[
                        "inventory_fingerprint"
                    ],
                    "expected_artifact_fingerprint": impact[
                        "artifact_fingerprint"
                    ],
                    "confirm_name": impact["confirm_name"],
                },
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"]["code"],
            "library_operation_running",
        )
        self.assertTrue(self.project_dir.is_dir())

    def test_invalid_kind_and_missing_artifact_are_machine_readable(self) -> None:
        invalid = self.client.get("/api/library", params={"kind": "bogus"})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(
            invalid.json()["detail"]["code"],
            "library_kind_invalid",
        )
        missing = self.client.get("/api/library/artifacts/library_missing")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(
            missing.json()["detail"]["code"],
            "library_artifact_not_found",
        )


if __name__ == "__main__":
    unittest.main()
