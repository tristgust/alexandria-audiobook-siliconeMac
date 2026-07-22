from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from project_flow import ProjectFlowError


PROTECTED_FILES = (
    "state.json",
    "annotated_script.json",
    "annotated_script.meta.json",
    "chunks.json",
    "voice_config.json",
    "character_roster.json",
)


def digest(path: Path) -> str:
    if not path.exists():
        return "<absent>"
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ProjectFlowRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app_module.app)

    def test_route_is_registered_once(self) -> None:
        routes = [
            route
            for route in app_module.app.routes
            if getattr(route, "path", None) == "/api/project_flow/status"
            and "GET" in getattr(route, "methods", set())
        ]
        self.assertEqual(len(routes), 1)

    def test_route_returns_versioned_public_contract(self) -> None:
        payload = {
            "schema_version": 1,
            "summary_state": "current",
            "project": {"id": "project_123", "name": "Book"},
            "source": {"filename": "book.txt"},
            "recommended_stage": "script",
            "safe_next_action": None,
            "stages": [],
            "stage_map": {},
            "blocker_count": 0,
            "completion_state": "requires_work",
            "resumable_operation": None,
            "running_operation": None,
            "compatibility": {"state": "current"},
        }
        with patch.object(
            app_module,
            "_current_project_flow_status",
            return_value=payload,
        ):
            response = self.client.get("/api/project_flow/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), payload)

    def test_domain_validation_failure_is_machine_readable(self) -> None:
        with patch.object(
            app_module,
            "_current_project_flow_status",
            side_effect=ProjectFlowError("Unsupported project-flow state"),
        ):
            response = self.client.get("/api/project_flow/status")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "project_flow_invalid")
        self.assertIn("Unsupported", response.json()["detail"]["message"])

    def test_real_collector_is_model_free_file_pure_and_hides_paths_from_ordinary_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app_dir = root / "app"
            app_dir.mkdir()
            source_path = root / "uploads" / "book.txt"
            source_path.parent.mkdir()
            source_path.write_text("The room was quiet.", encoding="utf-8")
            state_path = root / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "input_file_path": str(source_path),
                        "project_name": "Quiet Book",
                        "source_language": "English",
                        "output_language": "English",
                    }
                ),
                encoding="utf-8",
            )
            script_path = root / "annotated_script.json"
            script_path.write_text(
                json.dumps(
                    [
                        {
                            "speaker": "NARRATOR",
                            "text": "The room was quiet.",
                            "instruct": "Quiet narration.",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            metadata_path = root / "annotated_script.meta.json"
            metadata_path.write_text("{}", encoding="utf-8")
            chunks_path = root / "chunks.json"
            chunks_path.write_text("[]", encoding="utf-8")
            voice_path = root / "voice_config.json"
            voice_path.write_text(
                json.dumps({"NARRATOR": {"type": "custom", "voice": "Ryan"}}),
                encoding="utf-8",
            )
            roster_path = root / "character_roster.json"
            roster_path.write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "id": "character_narrator",
                                "canonical_name": "Narrator",
                                "display_name": "Narrator",
                                "aliases": ["NARRATOR"],
                                "titles": [],
                                "nicknames": [],
                                "sample_lines": ["The room was quiet."],
                                "speaking_status": "narrator",
                                "resolution_status": "resolved",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            config_path = app_dir / "config.json"
            config_path.write_text(
                json.dumps({"tts": {"language": "English"}}),
                encoding="utf-8",
            )
            protected = {
                name: digest(root / name)
                for name in PROTECTED_FILES
            }
            source_status = {
                "state_file_exists": True,
                "persisted": True,
                "path": str(source_path),
                "basename": source_path.name,
                "exists": True,
                "readable": True,
                "error": None,
            }
            generation_status = {
                "process": {"running": False, "logs": []},
                "checkpoint": {"status": "none", "resumable": False},
                "result": {
                    "status": "metadata_invalid",
                    "script_exists": True,
                    "script_status": "valid",
                    "script_fingerprint": "script-fingerprint",
                    "metadata": None,
                    "errors": ["metadata invalid"],
                },
            }
            roster_status = {
                "source": {"fingerprint": "source-fingerprint"},
                "process": {"running": False},
                "progress": {"status": "complete"},
                "approved": {
                    "status": "approved",
                    "compatible_source": True,
                    "fingerprint": "roster-fingerprint",
                },
                "draft": {"status": "missing"},
            }

            with (
                patch.object(app_module, "ROOT_DIR", str(root)),
                patch.object(app_module, "CONFIG_PATH", str(config_path)),
                patch.object(app_module, "SCRIPT_PATH", str(script_path)),
                patch.object(app_module, "SCRIPT_METADATA_PATH", str(metadata_path)),
                patch.object(
                    app_module,
                    "SCRIPT_LIFECYCLE_PATH",
                    str(root / "script_lifecycle.json"),
                ),
                patch.object(app_module, "CHUNKS_PATH", str(chunks_path)),
                patch.object(app_module, "VOICE_CONFIG_PATH", str(voice_path)),
                patch.object(app_module, "CHARACTER_ROSTER_PATH", str(roster_path)),
                patch.object(app_module, "AUDIOBOOK_PATH", str(root / "cloned_audiobook.mp3")),
                patch.object(app_module, "M4B_PATH", str(root / "audiobook.m4b")),
                patch.object(
                    app_module,
                    "_selected_source_recovery_status",
                    return_value=source_status,
                ),
                patch.object(
                    app_module,
                    "_current_script_generation_status",
                    return_value=generation_status,
                ),
                patch.object(
                    app_module,
                    "_current_character_roster_source_context",
                    return_value={
                        "source_text": "The room was quiet.",
                        "source_fingerprint": "source-fingerprint",
                        "source": {
                            "path": str(source_path),
                            "basename": source_path.name,
                        },
                    },
                ),
                patch.object(
                    app_module,
                    "_current_character_roster_status",
                    return_value=roster_status,
                ),
                patch.object(
                    app_module,
                    "_current_process_status",
                    return_value={"running": False, "logs": []},
                ),
                patch.object(
                    app_module,
                    "get_migration_status_payload",
                    return_value={
                        "migration_required": False,
                        "migration_blocked": False,
                    },
                ),
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
                response = self.client.get("/api/project_flow/status")

            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(payload["project"]["name"], "Quiet Book")
            self.assertNotIn("path", payload["project"])
            self.assertNotIn("path", payload["source"])
            self.assertEqual(
                payload["project"]["technical_details"]["project_path"],
                str(root.resolve()),
            )
            self.assertEqual(payload["source"]["fingerprint"], "source-fingerprint")
            self.assertEqual(
                tuple(stage["key"] for stage in payload["stages"]),
                ("script", "cast", "produce", "export"),
            )
            self.assertEqual(payload["recommended_stage"], "script")
            self.assertEqual(
                {name: digest(root / name) for name in PROTECTED_FILES},
                protected,
            )


if __name__ == "__main__":
    unittest.main()
