from __future__ import annotations

import ast
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import app as app_module
from generation_state import fingerprint_text, fingerprint_value


class ScriptLifecycleRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source_path = self.root / "book.txt"
        self.source_text = "The room was quiet."
        self.source_path.write_text(self.source_text, encoding="utf-8")
        self.source_fingerprint = fingerprint_text(self.source_text)
        self.script_path = self.root / "annotated_script.json"
        self.metadata_path = self.root / "annotated_script.meta.json"
        self.lifecycle_path = self.root / "script_lifecycle.json"
        self.chunks_path = self.root / "chunks.json"
        self.validity_path = self.root / "audio_validity.json"
        self.entries = [
            {
                "speaker": "NARRATOR",
                "text": self.source_text,
                "instruct": "Quiet narration.",
            }
        ]
        self.metadata = self._metadata(self.entries)
        self.script_path.write_text(
            json.dumps(self.entries, indent=2) + "\n",
            encoding="utf-8",
        )
        self.metadata_path.write_text(
            json.dumps(self.metadata, indent=2) + "\n",
            encoding="utf-8",
        )
        self.chunks_path.write_text("[]\n", encoding="utf-8")
        self.generation_status = {
            "process": {"running": False, "logs": []},
            "checkpoint": {"status": "none", "resumable": False},
            "result": {
                "status": "complete",
                "script_exists": True,
                "script_status": "valid",
                "script_fingerprint": fingerprint_value(self.entries),
                "entry_count": len(self.entries),
                "metadata": self.metadata,
                "errors": [],
            },
        }
        self.roster_status = {
            "source": {"fingerprint": self.source_fingerprint},
            "process": {"running": False, "logs": []},
            "progress": {"status": "missing", "resumable": False},
            "approved": {"status": "missing", "compatible_source": None},
            "draft": {"status": "missing"},
        }
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _metadata(self, entries: list[dict]) -> dict:
        identity = {
            "mode": "native",
            "backend": "ollama",
            "model_name": "qwen3.5:9b",
        }
        return {
            "schema_version": 1,
            "generated_at_utc": "2026-07-20T12:00:00Z",
            "source": {
                "verification_status": "verified",
                "fingerprint": self.source_fingerprint,
                "character_count": len(self.source_text),
                "chunk_count": 1,
            },
            "generation": {
                "fingerprint": fingerprint_value(identity),
                "effective_identity": identity,
            },
            "result": {
                "script_fingerprint": fingerprint_value(entries),
                "entry_count": len(entries),
                "speaker_labels": ["NARRATOR"],
            },
            "resume": {"resumed": False, "previously_completed_chunks": 0},
        }

    def _runtime_patches(self):
        return patch.multiple(
            app_module,
            ROOT_DIR=str(self.root),
            SCRIPT_PATH=str(self.script_path),
            SCRIPT_METADATA_PATH=str(self.metadata_path),
            SCRIPT_LIFECYCLE_PATH=str(self.lifecycle_path),
            CHUNKS_PATH=str(self.chunks_path),
            AUDIO_VALIDITY_PATH=str(self.validity_path),
            _current_script_generation_status=lambda: self.generation_status,
            _current_character_roster_source_context=lambda: {
                "source_text": self.source_text,
                "source_fingerprint": self.source_fingerprint,
                "source": {
                    "path": str(self.source_path),
                    "basename": self.source_path.name,
                },
            },
            _selected_source_recovery_status=lambda: {
                "state_file_exists": True,
                "persisted": True,
                "path": str(self.source_path),
                "basename": self.source_path.name,
                "exists": True,
                "readable": True,
                "error": None,
            },
            _current_character_roster_status=lambda: self.roster_status,
        )

    def _status(self) -> dict:
        response = self.client.get("/api/script_lifecycle/status")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_discovery_start_is_only_reachable_from_acceptance_handoff(self) -> None:
        tree = ast.parse(inspect.getsource(app_module))
        callers: list[str] = []
        stack: list[str] = []

        class Visitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()

            def visit_Call(self, node: ast.Call) -> None:
                if (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "_start_automatic_roster_after_script"
                ):
                    callers.append(stack[-1] if stack else "<module>")
                self.generic_visit(node)

        Visitor().visit(tree)
        self.assertEqual(callers, ["_mark_accepted_script_handoff"])

    def test_routes_are_registered_once(self) -> None:
        expected = {
            ("GET", "/api/script_lifecycle/status"),
            ("POST", "/api/script_lifecycle/accept"),
            ("POST", "/api/script_lifecycle/reject"),
            ("POST", "/api/script_lifecycle/discovery-handoff"),
            ("GET", "/api/script_lifecycle/versions"),
            ("POST", "/api/script_lifecycle/versions/{version_id}/rollback"),
            ("POST", "/api/script_lifecycle/candidates/{candidate_id}/accept"),
        }
        actual = []
        for route in app_module.app.routes:
            route_path = getattr(route, "path", None)
            methods = getattr(route, "methods", set())
            for method in methods:
                pair = (method, route_path)
                if pair in expected:
                    actual.append(pair)
        self.assertEqual(set(actual), expected)
        self.assertEqual(len(actual), len(expected))

    def test_status_is_read_only_and_model_free(self) -> None:
        script_before = self.script_path.read_bytes()
        metadata_before = self.metadata_path.read_bytes()
        with (
            self._runtime_patches(),
            patch.object(
                app_module,
                "download_or_repair_model",
                side_effect=AssertionError("status must not download models"),
            ),
            patch.object(
                app_module.project_manager,
                "get_engine",
                side_effect=AssertionError("status must not load TTS"),
            ),
            patch.object(
                app_module,
                "build_runtime_client",
                side_effect=AssertionError("status must not connect to an LLM"),
            ),
        ):
            status = self._status()
        self.assertEqual(status["state"], "review_required")
        self.assertEqual(status["generation_method"], "local")
        self.assertFalse(status["accepted"])
        self.assertEqual(status["primary_action"]["id"], "review_script")
        self.assertFalse(self.lifecycle_path.exists())
        self.assertEqual(self.script_path.read_bytes(), script_before)
        self.assertEqual(self.metadata_path.read_bytes(), metadata_before)

    def test_accept_commits_receipt_before_starting_discovery(self) -> None:
        script_before = self.script_path.read_bytes()
        metadata_before = self.metadata_path.read_bytes()
        with (
            self._runtime_patches(),
            patch.object(
                app_module,
                "_start_automatic_roster_after_script",
                return_value=True,
            ) as start_discovery,
        ):
            status = self._status()
            response = self.client.post(
                "/api/script_lifecycle/accept",
                json={
                    "expected_script_fingerprint": status["fingerprints"]["script"],
                    "expected_metadata_fingerprint": status["fingerprints"]["metadata"],
                    "expected_source_fingerprint": status["fingerprints"]["source"],
                    "expected_state_fingerprint": status["state_fingerprint"],
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            accepted_status = self._status()

        payload = response.json()
        self.assertEqual(payload["status"], "accepted")
        self.assertEqual(payload["discovery_handoff"]["status"], "running")
        start_discovery.assert_called_once_with()
        self.assertTrue(self.lifecycle_path.is_file())
        receipt = (
            self.root
            / "script_versions"
            / payload["version"]["version_id"]
            / "receipt.json"
        )
        self.assertTrue(receipt.is_file())
        self.assertEqual(accepted_status["state"], "accepted")
        self.assertTrue(accepted_status["accepted"])
        self.assertEqual(accepted_status["primary_action"]["id"], "open_cast")
        self.assertEqual(self.script_path.read_bytes(), script_before)
        self.assertEqual(self.metadata_path.read_bytes(), metadata_before)

    def test_acceptance_validation_failure_does_not_start_discovery(self) -> None:
        bad_entries = [
            {
                "speaker": "THE DOCTOR",
                "text": "Different text.",
                "instruct": "Neutral.",
            }
        ]
        bad_metadata = self._metadata(bad_entries)
        self.script_path.write_text(json.dumps(bad_entries), encoding="utf-8")
        self.metadata_path.write_text(json.dumps(bad_metadata), encoding="utf-8")
        with (
            self._runtime_patches(),
            patch.object(
                app_module,
                "_start_automatic_roster_after_script",
                return_value=True,
            ) as start_discovery,
        ):
            status = self._status()
            response = self.client.post(
                "/api/script_lifecycle/accept",
                json={
                    "expected_script_fingerprint": status["fingerprints"]["script"],
                    "expected_metadata_fingerprint": status["fingerprints"]["metadata"],
                    "expected_source_fingerprint": status["fingerprints"]["source"],
                    "expected_state_fingerprint": status["state_fingerprint"],
                },
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"]["code"],
            "script_acceptance_blocked",
        )
        start_discovery.assert_not_called()
        self.assertFalse(self.lifecycle_path.exists())
        self.assertFalse((self.root / "script_versions").exists())

    def test_reject_removes_authority_without_deleting_artifacts(self) -> None:
        with (
            self._runtime_patches(),
            patch.object(
                app_module,
                "_start_automatic_roster_after_script",
                return_value=True,
            ),
        ):
            status = self._status()
            accepted = self.client.post(
                "/api/script_lifecycle/accept",
                json={
                    "expected_script_fingerprint": status["fingerprints"]["script"],
                    "expected_metadata_fingerprint": status["fingerprints"]["metadata"],
                    "expected_source_fingerprint": status["fingerprints"]["source"],
                    "expected_state_fingerprint": status["state_fingerprint"],
                },
            ).json()
            script_before = self.script_path.read_bytes()
            metadata_before = self.metadata_path.read_bytes()
            response = self.client.post(
                "/api/script_lifecycle/reject",
                json={
                    "expected_script_fingerprint": accepted["version"]["script_fingerprint"],
                    "expected_state_fingerprint": accepted["state_fingerprint"],
                    "reason": "Speaker attribution still needs correction.",
                },
            )
            current = self._status()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "rejected")
        self.assertFalse(current["accepted"])
        self.assertEqual(current["state"], "review_required")
        self.assertEqual(self.script_path.read_bytes(), script_before)
        self.assertEqual(self.metadata_path.read_bytes(), metadata_before)
        self.assertEqual(len(current["versions"]), 1)

    def test_retry_handoff_requires_current_accepted_script(self) -> None:
        with self._runtime_patches():
            status = self._status()
            response = self.client.post(
                "/api/script_lifecycle/discovery-handoff",
                json={"expected_state_fingerprint": status["state_fingerprint"]},
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "script_not_accepted")

    def test_version_rollback_route_restores_snapshot_and_invalidates_audio(self) -> None:
        self.source_text = "Hello. Goodbye."
        self.source_path.write_text(self.source_text, encoding="utf-8")
        self.source_fingerprint = fingerprint_text(self.source_text)
        first_entries = [
            {
                "speaker": "NARRATOR",
                "text": self.source_text,
                "instruct": "Neutral narration.",
            }
        ]
        first_metadata = self._metadata(first_entries)
        self.script_path.write_text(
            json.dumps(first_entries, indent=2) + "\n",
            encoding="utf-8",
        )
        self.metadata_path.write_text(
            json.dumps(first_metadata, indent=2) + "\n",
            encoding="utf-8",
        )
        self.generation_status["result"].update(
            {
                "script_fingerprint": fingerprint_value(first_entries),
                "entry_count": len(first_entries),
                "metadata": first_metadata,
            }
        )
        first_script_bytes = self.script_path.read_bytes()
        first_metadata_bytes = self.metadata_path.read_bytes()

        with (
            self._runtime_patches(),
            patch.object(
                app_module,
                "_start_automatic_roster_after_script",
                return_value=True,
            ),
        ):
            first_status = self._status()
            first_response = self.client.post(
                "/api/script_lifecycle/accept",
                json={
                    "expected_script_fingerprint": first_status["fingerprints"]["script"],
                    "expected_metadata_fingerprint": first_status["fingerprints"]["metadata"],
                    "expected_source_fingerprint": first_status["fingerprints"]["source"],
                    "expected_state_fingerprint": first_status["state_fingerprint"],
                },
            )
            self.assertEqual(first_response.status_code, 200, first_response.text)
            first = first_response.json()

            second_entries = [
                {
                    "speaker": "NARRATOR",
                    "text": "Hello.",
                    "instruct": "Warm narration.",
                },
                {
                    "speaker": "NARRATOR",
                    "text": "Goodbye.",
                    "instruct": "Quiet narration.",
                },
            ]
            second_metadata = self._metadata(second_entries)
            self.script_path.write_text(
                json.dumps(second_entries, indent=2) + "\n",
                encoding="utf-8",
            )
            self.metadata_path.write_text(
                json.dumps(second_metadata, indent=2) + "\n",
                encoding="utf-8",
            )
            self.generation_status["result"].update(
                {
                    "script_fingerprint": fingerprint_value(second_entries),
                    "entry_count": len(second_entries),
                    "metadata": second_metadata,
                }
            )
            second_status = self._status()
            second_response = self.client.post(
                "/api/script_lifecycle/accept",
                json={
                    "expected_script_fingerprint": second_status["fingerprints"]["script"],
                    "expected_metadata_fingerprint": second_status["fingerprints"]["metadata"],
                    "expected_source_fingerprint": second_status["fingerprints"]["source"],
                    "expected_state_fingerprint": second_status["state_fingerprint"],
                },
            )
            self.assertEqual(second_response.status_code, 200, second_response.text)
            second = second_response.json()

            audio = self.root / "voicelines" / "line.wav"
            audio.parent.mkdir()
            audio.write_bytes(b"current-audio")
            self.chunks_path.write_text(
                json.dumps(
                    [
                        {
                            "id": "chunk-current",
                            "speaker": "NARRATOR",
                            "text": self.source_text,
                            "instruct": "Current.",
                            "status": "done",
                            "audio_path": "voicelines/line.wav",
                            "audio_state": "current",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            rollback = self.client.post(
                f"/api/script_lifecycle/versions/{first['version']['version_id']}/rollback",
                json={
                    "expected_current_script_fingerprint": second["version"]["script_fingerprint"],
                    "expected_source_fingerprint": self.source_fingerprint,
                    "expected_state_fingerprint": second["state_fingerprint"],
                },
            )
            self.assertEqual(rollback.status_code, 200, rollback.text)
            current = self._status()

        payload = rollback.json()
        self.assertEqual(payload["status"], "rolled_back")
        self.assertEqual(payload["invalidated_audio_count"], 1)
        self.assertEqual(self.script_path.read_bytes(), first_script_bytes)
        self.assertEqual(self.metadata_path.read_bytes(), first_metadata_bytes)
        self.assertFalse(audio.exists())
        self.assertEqual(
            current["accepted_version_id"],
            first["version"]["version_id"],
        )
        self.assertEqual(current["state"], "accepted")
        self.assertEqual(current["discovery_handoff"]["status"], "running")

    def test_candidate_acceptance_failure_invokes_exact_existing_rollback(self) -> None:
        applied = {
            "operation_id": "script_import_12345678",
            "script_fingerprint": "new-script",
            "metadata_fingerprint": "new-metadata",
            "voice_config_fingerprint": "new-voice",
            "chunks_fingerprint": "new-chunks",
        }
        acceptance_error = HTTPException(
            status_code=409,
            detail={
                "code": "script_acceptance_blocked",
                "message": "Audit failed.",
            },
        )
        with (
            patch.object(
                app_module,
                "apply_annotated_script_candidate",
                return_value=applied,
            ) as apply_candidate,
            patch.object(
                app_module,
                "_accept_current_script_request",
                side_effect=acceptance_error,
            ),
            patch.object(
                app_module,
                "rollback_annotated_script_import",
                return_value={"status": "rolled_back"},
            ) as rollback_candidate,
        ):
            response = self.client.post(
                "/api/script_lifecycle/candidates/candidate_12345678/accept",
                json={
                    "expected_current_script_fingerprint": "old-script",
                    "expected_current_metadata_fingerprint": "old-metadata",
                    "expected_current_voice_config_fingerprint": "old-voice",
                    "expected_current_chunks_fingerprint": "old-chunks",
                    "expected_source_fingerprint": "source-fingerprint",
                    "expected_lifecycle_state_fingerprint": None,
                },
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"]["code"],
            "script_acceptance_blocked",
        )
        apply_candidate.assert_called_once()
        rollback_candidate.assert_called_once_with(
            root_dir=app_module.ROOT_DIR,
            operation_id="script_import_12345678",
            expected_current_script_fingerprint="new-script",
            expected_current_metadata_fingerprint="new-metadata",
            expected_current_voice_config_fingerprint="new-voice",
            expected_current_chunks_fingerprint="new-chunks",
        )


if __name__ == "__main__":
    unittest.main()
