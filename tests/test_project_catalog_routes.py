from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from project_catalog import load_project_catalog, project_catalog_path
from project_flow import build_project_flow_summary


class ProjectCatalogRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data_root = self.root / "application-data"
        self.legacy_root = self.root / "legacy"
        self.legacy_root.mkdir()
        self.legacy_source = self.legacy_root / "book.txt"
        self.legacy_source.write_text("Legacy source.\n", encoding="utf-8")
        (self.legacy_root / "state.json").write_text(
            json.dumps({"input_file_path": str(self.legacy_source)}),
            encoding="utf-8",
        )
        self.flow = self._flow()
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _flow(self) -> dict:
        return build_project_flow_summary(
            project={
                "id": "project_legacy",
                "name": "Legacy Book",
                "latest_meaningful_activity": "2026-07-20T12:00:00Z",
                "archive_state": "active",
            },
            source={
                "selected": True,
                "available": True,
                "title": "book",
                "filename": "book.txt",
                "type": "txt",
                "source_language": "English",
                "output_language": "English",
                "fingerprint": "source-fingerprint",
                "error": None,
            },
            script={
                "source_available": True,
                "process": {"running": False},
                "resumable": False,
                "failed": False,
                "artifact_exists": False,
                "structure_valid": None,
                "attribution_valid": None,
                "fidelity_valid": None,
                "artifact_current": None,
                "provenance_recorded": None,
                "finalization_complete": None,
                "review_required": False,
                "accepted": False,
                "fingerprints": {"source": "source-fingerprint"},
            },
            cast={
                "process": {"running": False},
                "resumable": False,
                "failed": False,
                "roster_exists": False,
                "review_required": False,
                "roster_approved": False,
                "roster_current": None,
                "required_speaking_characters": 0,
                "valid_production_voices": 0,
                "unresolved_identity_ids": [],
                "ambiguous_mapping_ids": [],
                "missing_voice_ids": [],
                "invalid_voice_ids": [],
                "invalid_clone_ids": [],
                "controlled_clone_approval_missing_ids": [],
                "invalid_adapter_ids": [],
                "stale_voice_ids": [],
                "fingerprints": {},
            },
            produce={
                "process": {"running": False},
                "resumable": False,
                "required_chunks": 0,
                "current_chunks": 0,
                "missing_chunk_ids": [],
                "stale_chunk_ids": [],
                "failed_chunk_ids": [],
                "hash_invalid_chunk_ids": [],
                "review_chunk_ids": [],
                "listening_chunk_ids": [],
                "fingerprints": {},
            },
            export={
                "process": {"running": False},
                "failed": False,
                "missing_metadata_fields": ["title", "author"],
                "invalid_chapter_ids": [],
                "unavailable_formats": [],
                "output_exists": False,
                "output_current": False,
                "output_valid": False,
                "fingerprints": {},
            },
            compatibility={"state": "current"},
            generated_at_utc="2026-07-20T12:00:00Z",
        )

    def _patch_runtime(self):
        def activate(*, root_dir, project_id, storage_kind):
            return {
                "state": "current",
                "project_id": project_id,
                "root_path": str(Path(root_dir).resolve()),
                "native_destination": "script",
            }

        return patch.multiple(
            app_module,
            PROJECTS_DATA_ROOT=self.data_root,
            ROOT_DIR=str(self.legacy_root),
            LEGACY_ROOT_DIR=str(self.legacy_root),
            ACTIVE_PROJECT_ID="project_legacy",
            ACTIVE_PROJECT_STORAGE_KIND="legacy_checkout",
            LEGACY_PROJECT_ID="project_legacy",
            LEGACY_FLOW_SNAPSHOT=self.flow,
            _current_project_flow_status=lambda: self.flow,
            _activate_runtime_project=activate,
        )

    def _catalog_fingerprint(self) -> str:
        return load_project_catalog(project_catalog_path(self.data_root))[
            "catalog_fingerprint"
        ]

    def _epub_bytes(self) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr(
                "META-INF/container.xml",
                """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles>
</container>""",
            )
            archive.writestr(
                "OEBPS/content.opf",
                """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Cover Route Book</dc:title>
    <dc:creator>Cover Author</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="cover" href="cover.png" media-type="image/png" properties="cover-image"/>
    <item id="one" href="one.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="one"/></spine>
</package>""",
            )
            archive.writestr("OEBPS/cover.png", b"\x89PNG\r\n\x1a\ncover-route")
            archive.writestr("OEBPS/one.xhtml", "<html><body><p>One.</p></body></html>")
        return output.getvalue()

    def _create_via_route(self, name: str = "Managed Book") -> dict:
        response = self.client.post(
            "/api/projects",
            data={
                "project_name": name,
                "book_title": "Confirmed Route Title",
                "author": "Route Author",
                "source_language": "English",
                "output_language": "English",
                "generation_method": "local",
                "preset": "standard",
                "expected_catalog_fingerprint": self._catalog_fingerprint(),
            },
            files={
                "source_file": (
                    "managed.txt",
                    b"Managed source.\n",
                    "text/plain",
                )
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_routes_are_registered_once(self) -> None:
        expected = {
            ("GET", "/api/projects"),
            ("GET", "/api/projects/{project_id}/cover"),
            ("POST", "/api/projects/inspect-source"),
            ("POST", "/api/projects"),
            ("POST", "/api/projects/{project_id}/open"),
            ("POST", "/api/projects/{project_id}/duplicate"),
            ("POST", "/api/projects/{project_id}/archive"),
            ("GET", "/api/projects/{project_id}/delete-impact"),
            ("POST", "/api/projects/{project_id}/delete"),
        }
        actual = []
        for route in app_module.app.routes:
            path = getattr(route, "path", None)
            methods = getattr(route, "methods", set())
            for method in methods:
                pair = (method, path)
                if pair in expected:
                    actual.append(pair)
        self.assertEqual(set(actual), expected)
        self.assertEqual(len(actual), len(expected))

    def test_project_list_is_read_only_model_free_and_path_confined(self) -> None:
        self.assertFalse(self.data_root.exists())
        with (
            self._patch_runtime(),
            patch.object(
                app_module,
                "download_or_repair_model",
                side_effect=AssertionError("Project listing must not download models"),
            ),
            patch.object(
                app_module.project_manager,
                "get_engine",
                side_effect=AssertionError("Project listing must not load TTS"),
            ),
            patch.object(
                app_module,
                "build_runtime_client",
                side_effect=AssertionError("Project listing must not connect to an LLM"),
            ),
        ):
            response = self.client.get("/api/projects")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertFalse(self.data_root.exists())
        self.assertEqual(payload["current_project_id"], "project_legacy")
        self.assertEqual(len(payload["projects"]), 1)
        project = payload["projects"][0]
        self.assertNotIn("path", project)
        self.assertEqual(
            project["technical_details"]["project_path"],
            str(self.legacy_root.resolve()),
        )
        self.assertEqual(
            set(project["stage_states"]),
            {"script", "cast", "produce", "export"},
        )

    def test_project_cover_route_serves_the_original_epub_cover(self) -> None:
        with self._patch_runtime():
            created = self.client.post(
                "/api/projects",
                data={
                    "project_name": "Cover Route",
                    "book_title": "Cover Route Book",
                    "author": "Cover Author",
                    "source_language": "English",
                    "output_language": "English",
                    "generation_method": "local",
                    "preset": "standard",
                    "expected_catalog_fingerprint": self._catalog_fingerprint(),
                },
                files={
                    "source_file": (
                        "cover-route.epub",
                        self._epub_bytes(),
                        "application/epub+zip",
                    )
                },
            )
            self.assertEqual(created.status_code, 200, created.text)
            project = created.json()["project"]
            self.assertEqual(project["source_author"], "Cover Author")
            self.assertEqual(
                project["cover_url"],
                f"/api/projects/{project['id']}/cover",
            )
            cover = self.client.get(project["cover_url"])

        self.assertEqual(cover.status_code, 200, cover.text)
        self.assertEqual(cover.headers["content-type"], "image/png")
        self.assertEqual(cover.content, b"\x89PNG\r\n\x1a\ncover-route")

    def test_source_inspection_is_read_only_and_returns_identity(self) -> None:
        self.assertFalse(self.data_root.exists())
        with self._patch_runtime():
            response = self.client.post(
                "/api/projects/inspect-source",
                data={"generation_method": "local"},
                files={
                    "source_file": (
                        "inspection.txt",
                        b"Readable source.\n",
                        "text/plain",
                    )
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["filename"], "inspection.txt")
        self.assertEqual(payload["title"], "inspection")
        self.assertEqual(payload["source_type"], "text")
        self.assertFalse(self.data_root.exists())

    def test_source_inspection_error_is_machine_readable(self) -> None:
        with self._patch_runtime():
            response = self.client.post(
                "/api/projects/inspect-source",
                data={"generation_method": "local"},
                files={
                    "source_file": (
                        "unsupported.pdf",
                        b"not an accepted source",
                        "application/pdf",
                    )
                },
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"]["code"],
            "project_source_type_unsupported",
        )
        self.assertFalse(self.data_root.exists())

    def test_create_upload_publishes_valid_managed_project_without_touching_legacy(self) -> None:
        legacy_before = {
            path.relative_to(self.legacy_root).as_posix(): path.read_bytes()
            for path in self.legacy_root.rglob("*")
            if path.is_file()
        }
        with self._patch_runtime():
            created = self._create_via_route()

        project = created["project"]
        self.assertEqual(project["activation_state"], "current")
        project_root = Path(project["technical_details"]["project_path"])
        self.assertTrue(project_root.is_dir())
        self.assertTrue((project_root / "state.json").is_file())
        state = json.loads((project_root / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["book_title"], "Confirmed Route Title")
        self.assertEqual(state["author"], "Route Author")
        self.assertEqual(
            {
                path.relative_to(self.legacy_root).as_posix(): path.read_bytes()
                for path in self.legacy_root.rglob("*")
                if path.is_file()
            },
            legacy_before,
        )

    def test_create_is_blocked_before_catalog_mutation_when_operation_runs(self) -> None:
        fingerprint = self._catalog_fingerprint()
        with self._patch_runtime():
            app_module.process_state["audio"]["running"] = True
            try:
                response = self.client.post(
                    "/api/projects",
                    data={
                        "project_name": "Blocked Create",
                        "source_language": "English",
                        "output_language": "English",
                        "generation_method": "local",
                        "preset": "standard",
                        "expected_catalog_fingerprint": fingerprint,
                    },
                    files={
                        "source_file": (
                            "blocked.txt",
                            b"Blocked source.\n",
                            "text/plain",
                        )
                    },
                )
            finally:
                app_module.process_state["audio"]["running"] = False
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "project_activation_operation_running",
        )
        catalog = load_project_catalog(project_catalog_path(self.data_root))
        self.assertEqual(catalog["projects"], [])
        self.assertIsNone(catalog["last_selected_project_id"])

    def test_open_activation_failure_restores_previous_catalog_selection(self) -> None:
        with self._patch_runtime():
            created = self._create_via_route("Rollback Open")
            project_id = created["project"]["id"]
            legacy = self.client.post(
                "/api/projects/project_legacy/open",
                json={
                    "expected_catalog_fingerprint": created[
                        "catalog_fingerprint"
                    ]
                },
            )
            self.assertEqual(legacy.status_code, 200, legacy.text)
            with patch.object(
                app_module,
                "_activate_runtime_project",
                side_effect=app_module.HTTPException(
                    status_code=409,
                    detail={
                        "code": "injected_activation_failure",
                        "message": "Injected activation failure.",
                    },
                ),
            ):
                response = self.client.post(
                    f"/api/projects/{project_id}/open",
                    json={
                        "expected_catalog_fingerprint": legacy.json()[
                            "catalog_fingerprint"
                        ]
                    },
                )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "injected_activation_failure",
        )
        catalog = load_project_catalog(project_catalog_path(self.data_root))
        self.assertEqual(catalog["last_selected_project_id"], "project_legacy")

    def test_create_activation_failure_keeps_project_but_restores_selection(self) -> None:
        fingerprint = self._catalog_fingerprint()
        with self._patch_runtime():
            with patch.object(
                app_module,
                "_activate_runtime_project",
                side_effect=RuntimeError("injected activation commit failure"),
            ):
                response = self.client.post(
                    "/api/projects",
                    data={
                        "project_name": "Created Not Activated",
                        "book_title": "Created Not Activated",
                        "source_language": "English",
                        "output_language": "English",
                        "generation_method": "local",
                        "preset": "standard",
                        "expected_catalog_fingerprint": fingerprint,
                    },
                    files={
                        "source_file": (
                            "created.txt",
                            b"Created source.\n",
                            "text/plain",
                        )
                    },
                )
        self.assertEqual(response.status_code, 500, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "project_created_activation_failed")
        self.assertTrue(detail["context"]["previous_selection_restored"])
        catalog = load_project_catalog(project_catalog_path(self.data_root))
        self.assertEqual(len(catalog["projects"]), 1)
        self.assertEqual(catalog["last_selected_project_id"], "project_legacy")
        project_root = Path(catalog["projects"][0]["root_path"])
        self.assertTrue(project_root.is_dir())

    def test_invalid_upload_error_is_machine_readable_and_creates_no_project(self) -> None:
        with self._patch_runtime():
            response = self.client.post(
                "/api/projects",
                data={
                    "project_name": "Bad",
                    "source_language": "English",
                    "output_language": "English",
                    "generation_method": "local",
                    "preset": "standard",
                    "expected_catalog_fingerprint": self._catalog_fingerprint(),
                },
                files={
                    "source_file": (
                        "bad.pdf",
                        b"not supported",
                        "application/pdf",
                    )
                },
            )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"]["code"],
            "project_source_type_unsupported",
        )
        catalog = load_project_catalog(project_catalog_path(self.data_root))
        self.assertEqual(catalog["projects"], [])

    def test_open_managed_project_persists_selection_and_activates_immediately(self) -> None:
        with self._patch_runtime():
            created = self._create_via_route("Open Me")
            response = self.client.post(
                f"/api/projects/{created['project']['id']}/open",
                json={
                    "expected_catalog_fingerprint": created[
                        "catalog_fingerprint"
                    ]
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["activation_state"], "current")
        self.assertEqual(payload["activation"]["state"], "current")
        self.assertEqual(payload["safe_action"]["id"], "activate_selected_project")
        catalog = load_project_catalog(project_catalog_path(self.data_root))
        self.assertEqual(
            catalog["last_selected_project_id"],
            created["project"]["id"],
        )

    def test_duplicate_current_legacy_uses_compatibility_adapter(self) -> None:
        (self.legacy_root / "annotated_script.json").write_text("[]", encoding="utf-8")
        (self.legacy_root / "application-code.py").write_text("do not copy", encoding="utf-8")
        with self._patch_runtime():
            response = self.client.post(
                "/api/projects/project_legacy/duplicate",
                json={
                    "name": "Legacy Copy",
                    "expected_catalog_fingerprint": self._catalog_fingerprint(),
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        root = Path(payload["project"]["technical_details"]["project_path"])
        self.assertTrue((root / "annotated_script.json").is_file())
        self.assertFalse((root / "application-code.py").exists())
        self.assertFalse(payload["active_operations_copied"])

    def test_archive_impact_and_recoverable_delete_route(self) -> None:
        with self._patch_runtime():
            created = self._create_via_route("Delete Route")
            project = created["project"]
            archive = self.client.post(
                f"/api/projects/{project['id']}/archive",
                json={
                    "archived": True,
                    "expected_catalog_fingerprint": created[
                        "catalog_fingerprint"
                    ],
                    "expected_project_fingerprint": project[
                        "technical_details"
                    ]["manifest_fingerprint"],
                },
            )
            self.assertEqual(archive.status_code, 200, archive.text)
            impact = self.client.get(
                f"/api/projects/{project['id']}/delete-impact"
            )
            self.assertEqual(impact.status_code, 200, impact.text)
            impact_payload = impact.json()
            self.assertTrue(impact_payload["deletable"])
            self.assertTrue(impact_payload["recoverable_delete"])
            delete = self.client.post(
                f"/api/projects/{project['id']}/delete",
                json={
                    "confirm_project_id": project["id"],
                    "expected_catalog_fingerprint": archive.json()[
                        "catalog_fingerprint"
                    ],
                    "expected_project_fingerprint": archive.json()[
                        "project_fingerprint"
                    ],
                    "confirm_dependencies": True,
                },
            )
        self.assertEqual(delete.status_code, 200, delete.text)
        payload = delete.json()
        self.assertTrue(payload["deleted"])
        self.assertTrue(payload["recoverable"])
        self.assertTrue(Path(payload["technical_details"]["trash_path"]).is_dir())

    def test_catalog_error_preserves_code_context_and_status(self) -> None:
        with self._patch_runtime():
            response = self.client.post(
                "/api/projects/project_legacy/archive",
                json={
                    "archived": True,
                    "expected_catalog_fingerprint": self._catalog_fingerprint(),
                    "expected_project_fingerprint": "legacy",
                },
            )
        self.assertEqual(response.status_code, 409)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "current_project_archive_blocked")
        self.assertIn("active project", detail["message"])
        self.assertEqual(detail["context"], {})


if __name__ == "__main__":
    unittest.main()
