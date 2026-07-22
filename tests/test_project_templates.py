from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project_catalog import create_managed_project, project_catalog_path
from project_templates import (
    BUILT_IN_TEMPLATES,
    MAX_TEMPLATE_CATALOG_BYTES,
    ProjectTemplateError,
    create_project_template,
    delete_project_template,
    duplicate_project_template,
    list_project_templates,
    project_template_delete_impact,
    resolve_project_template,
    set_default_project_template,
    template_catalog_path,
    update_project_template,
)


class ProjectTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def status(self) -> dict:
        return list_project_templates(self.root)

    def fields(self, **updates) -> dict:
        value = {
            "name": "Swedish production",
            "description": "Use the normal reviewed workflow for a Swedish-language output.",
            "generation_method": "local",
            "preset": "maximum_fidelity",
            "source_language": "English",
            "output_language": "Swedish",
            "intent": "High-fidelity Swedish production",
        }
        value.update(updates)
        return value

    def create(self, **updates) -> dict:
        before = self.status()
        return create_project_template(
            data_root=self.root,
            fields=self.fields(**updates),
            expected_catalog_fingerprint=before["catalog_fingerprint"],
        )

    def test_builtin_inventory_is_file_pure_and_covers_guided_methods(self) -> None:
        first = self.status()
        second = self.status()
        self.assertEqual(first, second)
        self.assertFalse(template_catalog_path(self.root).exists())
        self.assertEqual(first["summary"]["built_in_count"], len(BUILT_IN_TEMPLATES))
        self.assertEqual(first["summary"]["custom_count"], 0)
        self.assertEqual(first["default_template_id"], "builtin_standard")
        by_id = {item["id"]: item for item in first["templates"]}
        for identifier in (
            "builtin_standard",
            "builtin_maximum_fidelity",
            "builtin_faster_draft",
            "builtin_custom",
            "builtin_chatgpt_bundle",
            "builtin_import_script",
        ):
            self.assertIn(identifier, by_id)
            self.assertTrue(by_id[identifier]["built_in"])
            self.assertFalse(by_id[identifier]["editable"])
            self.assertFalse(by_id[identifier]["deletable"])
        self.assertEqual(
            by_id["builtin_import_script"]["generation_method"],
            "import_existing_script",
        )
        self.assertEqual(by_id["builtin_custom"]["preset"], "custom")

    def test_create_round_trip_is_normalized_atomic_and_optimistic(self) -> None:
        before = self.status()
        created = create_project_template(
            data_root=self.root,
            fields=self.fields(name="  Swedish   production  "),
            expected_catalog_fingerprint=before["catalog_fingerprint"],
        )
        template = created["template"]
        self.assertRegex(template["id"], r"^template_[0-9a-f]{20}$")
        self.assertEqual(template["name"], "Swedish production")
        self.assertFalse(template["built_in"])
        self.assertTrue(template["editable"])
        self.assertTrue(template["deletable"])
        self.assertTrue(template["fingerprint"])
        persisted = json.loads(template_catalog_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(len(persisted["custom_templates"]), 1)
        self.assertFalse(template_catalog_path(self.root).with_suffix(".json.tmp").exists())
        with self.assertRaisesRegex(ProjectTemplateError, "changed") as raised:
            create_project_template(
                data_root=self.root,
                fields=self.fields(name="Another template"),
                expected_catalog_fingerprint=before["catalog_fingerprint"],
            )
        self.assertEqual(raised.exception.code, "template_catalog_conflict")
        self.assertEqual(self.status()["summary"]["custom_count"], 1)

    def test_custom_update_is_fingerprint_gated_and_builtins_are_immutable(self) -> None:
        created = self.create()
        template = created["template"]
        updated = update_project_template(
            data_root=self.root,
            template_id=template["id"],
            fields=self.fields(
                name="Swedish publication",
                preset="standard",
                intent="Balanced Swedish publication",
            ),
            expected_catalog_fingerprint=created["catalog_fingerprint"],
            expected_template_fingerprint=template["fingerprint"],
        )
        self.assertEqual(updated["template"]["name"], "Swedish publication")
        self.assertEqual(updated["template"]["preset"], "standard")
        with self.assertRaises(ProjectTemplateError) as stale:
            update_project_template(
                data_root=self.root,
                template_id=template["id"],
                fields=self.fields(name="Stale edit"),
                expected_catalog_fingerprint=updated["catalog_fingerprint"],
                expected_template_fingerprint=template["fingerprint"],
            )
        self.assertEqual(stale.exception.code, "template_conflict")
        with self.assertRaises(ProjectTemplateError) as builtin:
            update_project_template(
                data_root=self.root,
                template_id="builtin_standard",
                fields=self.fields(name="Rewrite built-in"),
                expected_catalog_fingerprint=updated["catalog_fingerprint"],
                expected_template_fingerprint=resolve_project_template(
                    data_root=self.root,
                    template_id="builtin_standard",
                )["fingerprint"],
            )
        self.assertEqual(builtin.exception.code, "template_builtin_immutable")

    def test_duplicate_supports_builtins_and_custom_templates(self) -> None:
        status = self.status()
        first = duplicate_project_template(
            data_root=self.root,
            template_id="builtin_maximum_fidelity",
            name="Maximum fidelity — Swedish",
            expected_catalog_fingerprint=status["catalog_fingerprint"],
        )
        copy = first["template"]
        self.assertEqual(copy["preset"], "maximum_fidelity")
        self.assertEqual(copy["output_language"], "English")
        second = duplicate_project_template(
            data_root=self.root,
            template_id=copy["id"],
            name="Maximum fidelity — Swedish copy",
            expected_catalog_fingerprint=first["catalog_fingerprint"],
        )
        self.assertEqual(second["duplicated_from"], copy["id"])
        self.assertEqual(second["summary"]["custom_count"], 2)

    def test_default_template_blocks_deletion_until_another_default_is_selected(self) -> None:
        created = self.create()
        template = created["template"]
        defaulted = set_default_project_template(
            data_root=self.root,
            template_id=template["id"],
            expected_catalog_fingerprint=created["catalog_fingerprint"],
        )
        self.assertEqual(defaulted["default_template_id"], template["id"])
        impact = project_template_delete_impact(
            data_root=self.root,
            template_id=template["id"],
        )
        self.assertFalse(impact["safe_to_delete"])
        self.assertEqual(impact["blocking_reasons"][0]["code"], "template_is_default")
        with self.assertRaises(ProjectTemplateError) as blocked:
            delete_project_template(
                data_root=self.root,
                template_id=template["id"],
                expected_catalog_fingerprint=impact["catalog_fingerprint"],
                expected_template_fingerprint=template["fingerprint"],
                confirmation_text=template["name"],
                acknowledge_usage=False,
            )
        self.assertEqual(blocked.exception.code, "template_delete_blocked")
        restored = set_default_project_template(
            data_root=self.root,
            template_id="builtin_standard",
            expected_catalog_fingerprint=impact["catalog_fingerprint"],
        )
        deleted = delete_project_template(
            data_root=self.root,
            template_id=template["id"],
            expected_catalog_fingerprint=restored["catalog_fingerprint"],
            expected_template_fingerprint=template["fingerprint"],
            confirmation_text=template["name"],
            acknowledge_usage=False,
        )
        self.assertEqual(deleted["deleted_template_id"], template["id"])
        self.assertEqual(deleted["summary"]["custom_count"], 0)

    def test_delete_impact_reports_historical_usage_without_rewriting_project(self) -> None:
        created = self.create()
        template = created["template"]
        project_dir = self.root / "Projects" / "fixture-project"
        project_dir.mkdir(parents=True)
        manifest = {
            "schema_version": 1,
            "project_id": "project_fixture",
            "name": "Fixture Project",
            "book": {"title": "Fixture Book"},
            "creation": {"template_id": template["id"]},
        }
        manifest_path = project_dir / "alexandria-project.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        before = manifest_path.read_bytes()
        impact = project_template_delete_impact(
            data_root=self.root,
            template_id=template["id"],
        )
        self.assertTrue(impact["safe_to_delete"])
        self.assertTrue(impact["requires_usage_acknowledgement"])
        self.assertEqual(impact["usage_count"], 1)
        self.assertFalse(impact["usage"][0]["blocking"])
        with self.assertRaises(ProjectTemplateError) as acknowledgement:
            delete_project_template(
                data_root=self.root,
                template_id=template["id"],
                expected_catalog_fingerprint=impact["catalog_fingerprint"],
                expected_template_fingerprint=template["fingerprint"],
                confirmation_text=template["name"],
                acknowledge_usage=False,
            )
        self.assertEqual(
            acknowledgement.exception.code,
            "template_delete_usage_acknowledgement_required",
        )
        deleted = delete_project_template(
            data_root=self.root,
            template_id=template["id"],
            expected_catalog_fingerprint=impact["catalog_fingerprint"],
            expected_template_fingerprint=template["fingerprint"],
            confirmation_text=template["name"],
            acknowledge_usage=True,
        )
        self.assertEqual(deleted["summary"]["custom_count"], 0)
        self.assertEqual(manifest_path.read_bytes(), before)

    def test_delete_requires_exact_name_and_current_fingerprints(self) -> None:
        created = self.create()
        template = created["template"]
        impact = project_template_delete_impact(
            data_root=self.root,
            template_id=template["id"],
        )
        with self.assertRaises(ProjectTemplateError) as confirmation:
            delete_project_template(
                data_root=self.root,
                template_id=template["id"],
                expected_catalog_fingerprint=impact["catalog_fingerprint"],
                expected_template_fingerprint=template["fingerprint"],
                confirmation_text="swedish production",
                acknowledge_usage=False,
            )
        self.assertEqual(
            confirmation.exception.code,
            "template_delete_confirmation_invalid",
        )
        self.assertEqual(self.status()["summary"]["custom_count"], 1)

    def test_validation_rejects_duplicate_names_and_invalid_import_presets(self) -> None:
        created = self.create()
        with self.assertRaises(ProjectTemplateError) as duplicate:
            create_project_template(
                data_root=self.root,
                fields=self.fields(name="SWEDISH PRODUCTION"),
                expected_catalog_fingerprint=created["catalog_fingerprint"],
            )
        self.assertEqual(duplicate.exception.code, "template_name_conflict")
        with self.assertRaises(ProjectTemplateError) as imported:
            create_project_template(
                data_root=self.root,
                fields=self.fields(
                    name="Import fixture",
                    generation_method="import_existing_script",
                    preset="maximum_fidelity",
                ),
                expected_catalog_fingerprint=created["catalog_fingerprint"],
            )
        self.assertEqual(imported.exception.code, "template_import_preset_invalid")
        self.assertEqual(self.status()["summary"]["custom_count"], 1)

    def test_catalog_rejects_symbolic_links_and_oversized_files(self) -> None:
        outside = self.root / "outside.json"
        outside.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "updated_at_utc": None,
                    "default_template_id": "builtin_standard",
                    "custom_templates": [],
                }
            ),
            encoding="utf-8",
        )
        catalog_path = template_catalog_path(self.root)
        catalog_path.symlink_to(outside)
        with self.assertRaises(ProjectTemplateError) as unsafe:
            self.status()
        self.assertEqual(unsafe.exception.code, "template_catalog_unsafe")
        catalog_path.unlink()
        catalog_path.write_bytes(b" " * (MAX_TEMPLATE_CATALOG_BYTES + 1))
        with self.assertRaises(ProjectTemplateError) as oversized:
            self.status()
        self.assertEqual(oversized.exception.code, "template_catalog_too_large")

    def test_public_contract_hides_runtime_models_prompts_and_cache_locations(self) -> None:
        rendered = json.dumps(self.status()).casefold()
        for forbidden in (
            "model_name",
            "prompt_template",
            "context_length_bytes",
            "huggingface_cache",
            "ollama_host",
            "api_key",
        ):
            self.assertNotIn(forbidden, rendered)
        self.assertIn("hidden_from_normal_ui", rendered)

    def test_managed_project_records_exact_template_provenance(self) -> None:
        template = resolve_project_template(
            data_root=self.root,
            template_id="builtin_maximum_fidelity",
        )
        source = self.root / "book.txt"
        source.write_text("One short source passage.\n", encoding="utf-8")
        result = create_managed_project(
            data_root=self.root,
            project_name="Template Project",
            source_path=source,
            book_title="Template Book",
            author="Fixture Author",
            source_language=template["source_language"],
            output_language=template["output_language"],
            generation_method=template["generation_method"],
            preset=template["preset"],
            template_id=template["id"],
            expected_catalog_fingerprint=None,
        )
        project = result["project"]
        self.assertEqual(project["template_id"], template["id"])
        project_root = Path(project["technical_details"]["project_path"])
        manifest = json.loads(
            (project_root / "alexandria-project.json").read_text(encoding="utf-8")
        )
        state = json.loads((project_root / "state.json").read_text(encoding="utf-8"))
        catalog = json.loads(project_catalog_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(manifest["creation"]["template_id"], template["id"])
        self.assertEqual(state["template_id"], template["id"])
        self.assertEqual(catalog["projects"][0]["template_id"], template["id"])

    def test_invalid_template_id_is_rejected_before_project_creation(self) -> None:
        source = self.root / "book.txt"
        source.write_text("Source.\n", encoding="utf-8")
        with self.assertRaises(ProjectTemplateError):
            resolve_project_template(
                data_root=self.root,
                template_id="../../unsafe",
            )
        with self.assertRaisesRegex(Exception, "Template ID is invalid"):
            create_managed_project(
                data_root=self.root,
                project_name="Unsafe Template",
                source_path=source,
                source_language="English",
                output_language="English",
                generation_method="local",
                preset="standard",
                template_id="../../unsafe",
            )
        self.assertFalse(project_catalog_path(self.root).exists())


if __name__ == "__main__":
    unittest.main()
