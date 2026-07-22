from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import project_catalog
from project_catalog import (
    PROJECT_CATALOG_FILENAME,
    PROJECT_MANIFEST_FILENAME,
    ProjectCatalogError,
    application_data_root,
    create_managed_project,
    delete_project_to_trash,
    duplicate_project,
    inspect_project_source,
    list_project_summaries,
    load_project_catalog,
    managed_projects_root,
    project_catalog_path,
    select_project,
    set_project_archived,
)
from project_flow import build_project_flow_summary


class ProjectCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data_root = self.root / "application-data"
        self.legacy_root = self.root / "legacy-checkout"
        self.legacy_root.mkdir()
        self.legacy_source = self.legacy_root / "book.txt"
        self.legacy_source.write_text("The room was quiet.\n", encoding="utf-8")
        self.legacy_flow = self._flow(
            project_id="project_legacy",
            project_name="Legacy Book",
            source_filename="book.txt",
            source_fingerprint="legacy-source",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _flow(
        self,
        *,
        project_id: str,
        project_name: str,
        source_filename: str,
        source_fingerprint: str,
    ) -> dict:
        return build_project_flow_summary(
            project={
                "id": project_id,
                "name": project_name,
                "latest_meaningful_activity": "2026-07-20T12:00:00Z",
                "archive_state": "active",
            },
            source={
                "selected": True,
                "available": True,
                "title": Path(source_filename).stem,
                "filename": source_filename,
                "type": Path(source_filename).suffix.lstrip("."),
                "source_language": "English",
                "output_language": "English",
                "fingerprint": source_fingerprint,
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
                "fingerprints": {
                    "source": source_fingerprint,
                    "script": None,
                    "generation": None,
                },
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

    def _catalog_fingerprint(self) -> str:
        return load_project_catalog(project_catalog_path(self.data_root))[
            "catalog_fingerprint"
        ]

    def _create(self, name: str = "Managed Book") -> dict:
        source = self.root / f"{name.replace(' ', '-').lower()}.txt"
        source.write_text("A clean source file.\n", encoding="utf-8")
        return create_managed_project(
            data_root=self.data_root,
            project_name=name,
            source_path=source,
            source_language="English",
            output_language="English",
            generation_method="local",
            preset="standard",
            expected_catalog_fingerprint=self._catalog_fingerprint(),
            reserved_names=["Legacy Book"],
            at_utc="2026-07-20T13:00:00Z",
        )

    def test_default_data_root_uses_macos_application_support_and_env_override(self) -> None:
        self.assertEqual(
            application_data_root(home="/Users/test", platform_name="darwin"),
            Path("/Users/test/Library/Application Support/Alexandria"),
        )
        self.assertEqual(
            application_data_root(
                environment={"ALEXANDRIA_DATA_ROOT": str(self.root / "custom")},
                home="/Users/test",
                platform_name="darwin",
            ),
            (self.root / "custom").resolve(),
        )

    def test_missing_catalog_list_is_side_effect_free_and_includes_legacy_project(self) -> None:
        self.assertFalse(self.data_root.exists())

        payload = list_project_summaries(
            data_root=self.data_root,
            current_project_root=self.legacy_root,
            current_flow_summary=self.legacy_flow,
        )

        self.assertFalse(self.data_root.exists())
        self.assertEqual(payload["current_project_id"], "project_legacy")
        self.assertEqual(payload["last_selected_project_id"], "project_legacy")
        self.assertEqual(len(payload["projects"]), 1)
        legacy = payload["projects"][0]
        self.assertTrue(legacy["current"])
        self.assertEqual(legacy["storage_kind"], "legacy_checkout")
        self.assertEqual(legacy["availability_state"], "available")
        self.assertNotIn("path", legacy)
        self.assertEqual(
            legacy["technical_details"]["project_path"],
            str(self.legacy_root.resolve()),
        )

    def test_create_project_is_destination_transactional_and_preserves_source(self) -> None:
        source = self.root / "source.txt"
        source.write_text("Exact source bytes.\n", encoding="utf-8")
        before = source.read_bytes()

        result = create_managed_project(
            data_root=self.data_root,
            project_name="My Book",
            source_path=source,
            book_title="Confirmed Book Title",
            author="Confirmed Author",
            source_language="English",
            output_language="Swedish",
            generation_method="local",
            preset="maximum_fidelity",
            expected_catalog_fingerprint=self._catalog_fingerprint(),
            reserved_names=["Legacy Book"],
            at_utc="2026-07-20T13:00:00Z",
        )

        self.assertEqual(source.read_bytes(), before)
        project = result["project"]
        self.assertEqual(project["name"], "My Book")
        self.assertEqual(project["current_recommended_stage"], "script")
        self.assertEqual(project["activation_state"], "available")
        self.assertEqual(result["activation"]["state"], "available")
        project_root = Path(project["technical_details"]["project_path"])
        self.assertTrue(project_root.is_dir())
        self.assertTrue((project_root / PROJECT_MANIFEST_FILENAME).is_file())
        state = json.loads((project_root / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["project_id"], project["id"])
        self.assertEqual(state["project_name"], "My Book")
        self.assertEqual(state["source_language"], "English")
        self.assertEqual(state["output_language"], "Swedish")
        selected = Path(state["input_file_path"])
        self.assertTrue(selected.is_file())
        self.assertEqual(selected.read_bytes(), before)
        self.assertTrue(str(selected).startswith(str(project_root)))
        manifest = json.loads(
            (project_root / PROJECT_MANIFEST_FILENAME).read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["source"]["title"], "Confirmed Book Title")
        self.assertEqual(manifest["source"]["author"], "Confirmed Author")
        self.assertEqual(state["book_title"], "Confirmed Book Title")
        self.assertEqual(state["author"], "Confirmed Author")
        self.assertEqual(manifest["generation"]["method"], "local")
        self.assertEqual(manifest["generation"]["preset"], "maximum_fidelity")
        self.assertEqual(
            manifest["flow_snapshot"]["stage_map"]["script"]["state"],
            "not_started",
        )
        self.assertFalse(any(path.name.startswith(".") and ".pending-" in path.name for path in managed_projects_root(self.data_root).iterdir()))

    def test_epub_source_is_prepared_in_spine_reading_order(self) -> None:
        epub = self.root / "book.epub"
        with zipfile.ZipFile(epub, "w") as archive:
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
<package xmlns="http://www.idpf.org/2007/opf">
  <manifest>
    <item id="second" href="second.xhtml" media-type="application/xhtml+xml"/>
    <item id="first" href="first.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="first"/><itemref idref="second"/></spine>
</package>""",
            )
            archive.writestr("OEBPS/second.xhtml", "<html><body><p>Second chapter.</p></body></html>")
            archive.writestr("OEBPS/first.xhtml", "<html><body><p>First chapter.</p></body></html>")

        result = create_managed_project(
            data_root=self.data_root,
            project_name="EPUB Book",
            source_path=epub,
            source_language="English",
            output_language="English",
            generation_method="local",
            expected_catalog_fingerprint=self._catalog_fingerprint(),
            at_utc="2026-07-20T13:00:00Z",
        )

        project_root = Path(result["project"]["technical_details"]["project_path"])
        state = json.loads((project_root / "state.json").read_text(encoding="utf-8"))
        prepared = Path(state["input_file_path"]).read_text(encoding="utf-8")
        self.assertLess(prepared.index("First chapter."), prepared.index("Second chapter."))
        self.assertTrue((project_root / "sources" / "book.epub").is_file())

    def test_source_inspection_extracts_epub_identity_without_writing_project_state(self) -> None:
        epub = self.root / "identity.epub"
        cover_bytes = b"\x89PNG\r\n\x1a\ncover"
        with zipfile.ZipFile(epub, "w") as archive:
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
    <dc:title>The Extracted Book</dc:title>
    <dc:creator>Alex Author</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="cover" href="cover.png" media-type="image/png" properties="cover-image"/>
    <item id="one" href="one.xhtml" media-type="application/xhtml+xml"/>
    <item id="two" href="two.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="one"/><itemref idref="two"/></spine>
</package>""",
            )
            archive.writestr("OEBPS/cover.png", cover_bytes)
            archive.writestr("OEBPS/one.xhtml", "<html><body><p>One.</p></body></html>")
            archive.writestr("OEBPS/two.xhtml", "<html><body><p>Two.</p></body></html>")

        before = epub.read_bytes()
        inspected = inspect_project_source(epub, generation_method="local")

        self.assertEqual(epub.read_bytes(), before)
        self.assertFalse(self.data_root.exists())
        self.assertTrue(inspected["valid"])
        self.assertEqual(inspected["source_type"], "epub")
        self.assertEqual(inspected["title"], "The Extracted Book")
        self.assertEqual(inspected["author"], "Alex Author")
        self.assertEqual(inspected["language"], "en")
        self.assertEqual(inspected["chapter_count"], 2)
        self.assertTrue(inspected["cover_data_url"].startswith("data:image/png;base64,"))
        self.assertEqual(inspected["suggested_project_name"], "The Extracted Book")

    def test_source_inspection_validates_imported_script_without_applying_it(self) -> None:
        candidate = self.root / "candidate.json"
        candidate.write_text(
            json.dumps(
                [
                    {
                        "speaker": "NARRATOR",
                        "text": "Exact source text.",
                        "instruct": "Measured narration.",
                    }
                ]
            ),
            encoding="utf-8",
        )

        inspected = inspect_project_source(
            candidate,
            generation_method="import_existing_script",
        )

        self.assertEqual(inspected["source_type"], "alexandria_script")
        self.assertEqual(inspected["entry_count"], 1)
        self.assertFalse(self.data_root.exists())

    def test_import_existing_script_starts_in_review_required(self) -> None:
        candidate = self.root / "script.json"
        candidate.write_text(
            json.dumps(
                [
                    {
                        "speaker": "NARRATOR",
                        "text": "Exact text.",
                        "instruct": "Neutral narration.",
                    }
                ]
            ),
            encoding="utf-8",
        )

        result = create_managed_project(
            data_root=self.data_root,
            project_name="Imported Book",
            source_path=candidate,
            source_language="English",
            output_language="English",
            generation_method="import_existing_script",
            expected_catalog_fingerprint=self._catalog_fingerprint(),
            at_utc="2026-07-20T13:00:00Z",
        )

        project_root = Path(result["project"]["technical_details"]["project_path"])
        manifest = json.loads(
            (project_root / PROJECT_MANIFEST_FILENAME).read_text(encoding="utf-8")
        )
        script = manifest["flow_snapshot"]["stage_map"]["script"]
        self.assertEqual(script["state"], "review_required")
        self.assertEqual(script["safe_next_action"]["id"], "review_imported_script")
        self.assertTrue((project_root / "imports" / "script-candidate.json").is_file())
        self.assertFalse((project_root / "annotated_script.json").exists())

    def test_invalid_source_rejection_creates_no_authoritative_project_state(self) -> None:
        with self.assertRaises(ProjectCatalogError) as raised:
            create_managed_project(
                data_root=self.data_root,
                project_name="Bad Project",
                source_path=self.root / "missing.txt",
                source_language="English",
                output_language="English",
                generation_method="local",
                expected_catalog_fingerprint=self._catalog_fingerprint(),
            )
        self.assertEqual(raised.exception.code, "project_source_missing")
        self.assertFalse(self.data_root.exists())

    def test_partial_catalog_failure_rolls_back_published_project(self) -> None:
        source = self.root / "source.txt"
        source.write_text("Source.\n", encoding="utf-8")
        real_write = project_catalog._write_json_atomic

        def fail_catalog(value, path):
            if Path(path).name == PROJECT_CATALOG_FILENAME:
                raise OSError("simulated catalog failure")
            return real_write(value, path)

        with patch("project_catalog._write_json_atomic", side_effect=fail_catalog):
            with self.assertRaises(OSError):
                create_managed_project(
                    data_root=self.data_root,
                    project_name="Rollback Project",
                    source_path=source,
                    source_language="English",
                    output_language="English",
                    generation_method="local",
                    expected_catalog_fingerprint=self._catalog_fingerprint(),
                    at_utc="2026-07-20T13:00:00Z",
                )

        projects_root = managed_projects_root(self.data_root)
        self.assertTrue(projects_root.exists())
        self.assertEqual(list(projects_root.iterdir()), [])
        self.assertFalse(project_catalog_path(self.data_root).exists())

    def test_stale_catalog_fingerprint_blocks_creation(self) -> None:
        stale = self._catalog_fingerprint()
        self._create("First")
        source = self.root / "second.txt"
        source.write_text("Second.\n", encoding="utf-8")
        with self.assertRaises(ProjectCatalogError) as raised:
            create_managed_project(
                data_root=self.data_root,
                project_name="Second",
                source_path=source,
                source_language="English",
                output_language="English",
                generation_method="local",
                expected_catalog_fingerprint=stale,
            )
        self.assertEqual(raised.exception.code, "stale_project_catalog")

    def test_listing_distinguishes_unavailable_from_invalid_projects(self) -> None:
        unavailable = self._create("Unavailable")
        invalid = self._create("Invalid")
        unavailable_root = Path(
            unavailable["project"]["technical_details"]["project_path"]
        )
        invalid_root = Path(invalid["project"]["technical_details"]["project_path"])
        unavailable_root.rename(unavailable_root.with_name(unavailable_root.name + "-moved"))
        manifest_path = invalid_root / PROJECT_MANIFEST_FILENAME
        manifest_path.write_text("{not-json", encoding="utf-8")

        payload = list_project_summaries(
            data_root=self.data_root,
            current_project_root=self.legacy_root,
            current_flow_summary=self.legacy_flow,
        )
        by_name = {item["name"]: item for item in payload["projects"]}
        self.assertEqual(by_name["Unavailable"]["availability_state"], "unavailable")
        self.assertEqual(by_name["Invalid"]["availability_state"], "invalid")
        self.assertIsNotNone(by_name["Unavailable"]["error"])
        self.assertIsNotNone(by_name["Invalid"]["error"])

    def test_select_project_preserves_last_selection_without_claiming_activation(self) -> None:
        created = self._create("Selected")
        project_id = created["project"]["id"]
        result = select_project(
            data_root=self.data_root,
            project_id=project_id,
            current_project_id="project_legacy",
            expected_catalog_fingerprint=created["catalog_fingerprint"],
            at_utc="2026-07-20T14:00:00Z",
        )
        self.assertEqual(result["activation_state"], "available")
        self.assertEqual(result["safe_action"]["id"], "activate_selected_project")
        catalog = load_project_catalog(project_catalog_path(self.data_root))
        self.assertEqual(catalog["last_selected_project_id"], project_id)

        payload = list_project_summaries(
            data_root=self.data_root,
            current_project_root=self.legacy_root,
            current_flow_summary=self.legacy_flow,
        )
        selected = next(item for item in payload["projects"] if item["id"] == project_id)
        legacy = next(item for item in payload["projects"] if item["id"] == "project_legacy")
        self.assertTrue(selected["selected"])
        self.assertFalse(selected["current"])
        self.assertTrue(legacy["current"])
        self.assertFalse(legacy["selected"])

    def test_duplicate_preserves_authoritative_artifacts_but_not_active_operation_state(self) -> None:
        created = self._create("Original")
        original = created["project"]
        original_root = Path(original["technical_details"]["project_path"])
        script_bytes = b'[{"speaker":"NARRATOR","text":"A","instruct":"N"}]\n'
        (original_root / "annotated_script.json").write_bytes(script_bytes)
        (original_root / "generation_state.json").write_text(
            json.dumps({"status": "running"}), encoding="utf-8"
        )
        voicelines = original_root / "voicelines"
        voicelines.mkdir()
        (voicelines / "line.wav").write_bytes(b"audio")

        duplicated = duplicate_project(
            data_root=self.data_root,
            project_id=original["id"],
            new_name="Duplicate",
            expected_catalog_fingerprint=created["catalog_fingerprint"],
            at_utc="2026-07-20T15:00:00Z",
        )

        duplicate = duplicated["project"]
        duplicate_root = Path(duplicate["technical_details"]["project_path"])
        self.assertNotEqual(duplicate["id"], original["id"])
        self.assertEqual(
            (duplicate_root / "annotated_script.json").read_bytes(),
            script_bytes,
        )
        self.assertEqual((duplicate_root / "voicelines" / "line.wav").read_bytes(), b"audio")
        self.assertFalse((duplicate_root / "generation_state.json").exists())
        state = json.loads((duplicate_root / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["project_id"], duplicate["id"])
        self.assertEqual(state["project_name"], "Duplicate")
        self.assertTrue(str(state["input_file_path"]).startswith(str(duplicate_root)))
        self.assertFalse(duplicated["active_operations_copied"])

    def test_duplicate_legacy_project_copies_only_project_artifacts(self) -> None:
        (self.legacy_root / "state.json").write_text(
            json.dumps({"input_file_path": str(self.legacy_source)}),
            encoding="utf-8",
        )
        (self.legacy_root / "annotated_script.json").write_text("[]", encoding="utf-8")
        (self.legacy_root / "application-code.py").write_text("do not copy", encoding="utf-8")

        duplicate = duplicate_project(
            data_root=self.data_root,
            project_id="project_legacy",
            new_name="Legacy Copy",
            expected_catalog_fingerprint=self._catalog_fingerprint(),
            source_project_root=self.legacy_root,
            source_flow_summary=self.legacy_flow,
            at_utc="2026-07-20T15:00:00Z",
        )

        root = Path(duplicate["project"]["technical_details"]["project_path"])
        self.assertTrue((root / "annotated_script.json").is_file())
        self.assertFalse((root / "application-code.py").exists())
        self.assertTrue(Path(json.loads((root / "state.json").read_text())["input_file_path"]).is_file())

    def test_archive_uses_manifest_fingerprint_and_changes_selection_safely(self) -> None:
        created = self._create("Archive Me")
        project = created["project"]
        selected = select_project(
            data_root=self.data_root,
            project_id=project["id"],
            current_project_id="project_legacy",
            expected_catalog_fingerprint=created["catalog_fingerprint"],
            at_utc="2026-07-20T14:00:00Z",
        )
        with self.assertRaises(ProjectCatalogError) as stale:
            set_project_archived(
                data_root=self.data_root,
                project_id=project["id"],
                archived=True,
                expected_catalog_fingerprint=selected["catalog_fingerprint"],
                expected_project_fingerprint="stale",
                current_project_id="project_legacy",
            )
        self.assertEqual(stale.exception.code, "stale_project_manifest")

        archived = set_project_archived(
            data_root=self.data_root,
            project_id=project["id"],
            archived=True,
            expected_catalog_fingerprint=selected["catalog_fingerprint"],
            expected_project_fingerprint=project["technical_details"]["manifest_fingerprint"],
            current_project_id="project_legacy",
            at_utc="2026-07-20T16:00:00Z",
        )
        self.assertEqual(archived["archive_state"], "archived")
        catalog = load_project_catalog(project_catalog_path(self.data_root))
        self.assertEqual(catalog["last_selected_project_id"], "project_legacy")
        entry = next(item for item in catalog["projects"] if item["id"] == project["id"])
        self.assertEqual(entry["archive_state"], "archived")

    def test_delete_requires_archive_exact_confirmation_and_dependency_acknowledgment(self) -> None:
        created = self._create("Delete Me")
        project = created["project"]
        with self.assertRaises(ProjectCatalogError) as not_archived:
            delete_project_to_trash(
                data_root=self.data_root,
                project_id=project["id"],
                confirm_project_id=project["id"],
                expected_catalog_fingerprint=created["catalog_fingerprint"],
                expected_project_fingerprint=project["technical_details"]["manifest_fingerprint"],
                current_project_id="project_legacy",
                confirm_dependencies=True,
            )
        self.assertEqual(not_archived.exception.code, "project_delete_requires_archive")

        archived = set_project_archived(
            data_root=self.data_root,
            project_id=project["id"],
            archived=True,
            expected_catalog_fingerprint=created["catalog_fingerprint"],
            expected_project_fingerprint=project["technical_details"]["manifest_fingerprint"],
            current_project_id="project_legacy",
            at_utc="2026-07-20T16:00:00Z",
        )
        with self.assertRaises(ProjectCatalogError) as mismatch:
            delete_project_to_trash(
                data_root=self.data_root,
                project_id=project["id"],
                confirm_project_id="wrong",
                expected_catalog_fingerprint=archived["catalog_fingerprint"],
                expected_project_fingerprint=archived["project_fingerprint"],
                current_project_id="project_legacy",
                confirm_dependencies=True,
            )
        self.assertEqual(mismatch.exception.code, "project_delete_confirmation_mismatch")

        with self.assertRaises(ProjectCatalogError) as dependencies:
            delete_project_to_trash(
                data_root=self.data_root,
                project_id=project["id"],
                confirm_project_id=project["id"],
                expected_catalog_fingerprint=archived["catalog_fingerprint"],
                expected_project_fingerprint=archived["project_fingerprint"],
                current_project_id="project_legacy",
                confirm_dependencies=False,
            )
        self.assertEqual(
            dependencies.exception.code,
            "project_delete_dependencies_unconfirmed",
        )
        self.assertGreater(
            dependencies.exception.context["dependencies"]["file_count"],
            0,
        )

        deleted = delete_project_to_trash(
            data_root=self.data_root,
            project_id=project["id"],
            confirm_project_id=project["id"],
            expected_catalog_fingerprint=archived["catalog_fingerprint"],
            expected_project_fingerprint=archived["project_fingerprint"],
            current_project_id="project_legacy",
            confirm_dependencies=True,
            at_utc="2026-07-20T17:00:00Z",
        )
        self.assertTrue(deleted["deleted"])
        self.assertTrue(deleted["recoverable"])
        original_root = Path(project["technical_details"]["project_path"])
        trash_root = Path(deleted["technical_details"]["trash_path"])
        self.assertFalse(original_root.exists())
        self.assertTrue(trash_root.is_dir())
        catalog = load_project_catalog(project_catalog_path(self.data_root))
        self.assertFalse(any(item["id"] == project["id"] for item in catalog["projects"]))
        self.assertTrue(any(item["id"] == project["id"] for item in catalog["trash"]))

    def test_active_legacy_project_cannot_be_archived_or_deleted(self) -> None:
        with self.assertRaises(ProjectCatalogError) as archived:
            set_project_archived(
                data_root=self.data_root,
                project_id="project_legacy",
                archived=True,
                expected_catalog_fingerprint=self._catalog_fingerprint(),
                expected_project_fingerprint="legacy",
                current_project_id="project_legacy",
            )
        self.assertEqual(archived.exception.code, "current_project_archive_blocked")

        with self.assertRaises(ProjectCatalogError) as deleted:
            delete_project_to_trash(
                data_root=self.data_root,
                project_id="project_legacy",
                confirm_project_id="project_legacy",
                expected_catalog_fingerprint=self._catalog_fingerprint(),
                expected_project_fingerprint="legacy",
                current_project_id="project_legacy",
                confirm_dependencies=True,
            )
        self.assertEqual(deleted.exception.code, "current_project_delete_blocked")


if __name__ == "__main__":
    unittest.main()
