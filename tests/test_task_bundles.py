from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from chatgpt_handoff import HandoffConflictError, HandoffValidationError
import task_bundles as task_bundle_module
from task_bundles import (
    COMPLETION_PATH,
    DEFAULT_RESULT_PATH,
    GUIDANCE_MANIFEST_PATH,
    NONHUMAN_GUIDANCE_PATH,
    TASK_CHECKSUMS_PATH,
    TASK_GUIDANCE_PATH,
    TASK_INPUT_PATH,
    TASK_INSTRUCTIONS_PATH,
    TASK_MANIFEST_PATH,
    TASK_REGISTRY,
    TASK_SCHEMA_PATH,
    create_completed_task_bundle,
    create_result_envelope,
    create_task_bundle,
    get_task_transfer_contract,
    inspect_completed_task_bundle,
    inspect_result_envelope,
    inspect_task_bundle,
    list_task_definitions,
    task_definition_contract,
)


class TaskBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create_persona_task(self, **overrides):
        values = {
            "output_dir": self.root,
            "task_type": "persona_generation",
            "input_payload": {
                "speaker": "THE DOCTOR",
                "sample_lines": ["Good evening."],
                "narrator_context": "The Doctor smiled.",
            },
            "application_version": "test",
            "target": {"kind": "speaker", "value": "THE DOCTOR"},
            "source_fingerprint": "a" * 64,
            "artifact_fingerprints": {"annotated_script": "b" * 64},
            "created_at_utc": "2026-07-19T12:00:00Z",
        }
        values.update(overrides)
        return create_task_bundle(**values)

    def test_registry_covers_required_task_families(self) -> None:
        expected = {
            "script_generation",
            "script_review",
            "complete_cast_dossier",
            "roster_discovery",
            "roster_reconciliation",
            "persona_catalog_generation",
            "persona_generation",
            "persona_refinement",
            "persona_reconciliation",
            "persona_audit",
            "visual_discovery",
            "visual_reconciliation",
            "persistent_voice_description_generation",
            "persistent_voice_description_refinement",
            "persistent_voice_description_audit",
            "line_direction_generation",
            "line_direction_audit",
            "backend_render_plan_generation",
            "pronunciation_guidance",
        }
        self.assertEqual(set(TASK_REGISTRY), expected)
        listed = {item["task_type"]: item for item in list_task_definitions()}
        self.assertEqual(set(listed), expected)
        self.assertEqual(
            listed["persona_generation"]["native_destination"],
            "expressive_voices",
        )
        self.assertEqual(
            listed["line_direction_generation"]["native_destination"],
            "editor",
        )

    def test_registry_contract_is_complete_and_self_consistent(self) -> None:
        legacy_v1 = {
            "script_generation",
            "script_review",
            "roster_discovery",
            "roster_reconciliation",
            "persona_generation",
            "visual_discovery",
        }
        handlers = {
            "script_candidate",
            "line_direction_review",
            "cast_dossier_package",
            "roster_discovery",
            "roster_reconciliation",
            "persona_catalog",
            "persona_single",
            "visual_discovery",
            "visual_reconciliation",
            "backend_render_plan",
            "pronunciation_guidance",
        }
        for task_type, definition in TASK_REGISTRY.items():
            with self.subTest(task_type=task_type):
                contract = task_definition_contract(definition)
                self.assertEqual(contract["task_type"], task_type)
                self.assertTrue(contract["label"])
                self.assertTrue(contract["stage"])
                self.assertTrue(contract["schema"]["contract"])
                self.assertEqual(len(contract["schema"]["fingerprint"]), 64)
                self.assertTrue(contract["minimized_input"]["builder"])
                self.assertTrue(
                    set(contract["minimized_input"]["required"])
                    <= set(contract["minimized_input"]["allowed"])
                )
                self.assertIn(
                    contract["dependencies"]["source"],
                    {"none", "tracked_if_present", "required"},
                )
                self.assertEqual(contract["validator"]["kind"], "native_contract")
                self.assertIn(contract["transfer_handler"], handlers)
                self.assertEqual(contract["stale_result"]["behavior"], "reject")
                self.assertEqual(
                    contract["legacy_v1_supported"],
                    task_type in legacy_v1,
                )
                self.assertEqual(
                    contract["native_transfer"],
                    get_task_transfer_contract(task_type),
                )

    def test_registry_consumers_do_not_define_parallel_task_maps(self) -> None:
        app_root = Path(__file__).resolve().parents[1] / "app"
        legacy_source = (app_root / "chatgpt_handoff.py").read_text(
            encoding="utf-8"
        )
        workflow_source = (app_root / "external_workflows.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("_TASK_INPUT_CONTRACTS", legacy_source)
        self.assertNotIn("_TASK_CONTRACTS", legacy_source)
        self.assertNotIn("def _structured_transfer_contract", workflow_source)
        self.assertIn("get_task_transfer_contract", workflow_source)

    def test_export_is_byte_reproducible_with_deterministic_zip_metadata(self) -> None:
        first = self.create_persona_task(
            output_dir=self.root / "first",
            bundle_name="persona.alexandria-task.zip",
        )
        second = self.create_persona_task(
            output_dir=self.root / "second",
            bundle_name="persona.alexandria-task.zip",
        )
        self.assertEqual(
            Path(first["path"]).read_bytes(),
            Path(second["path"]).read_bytes(),
        )
        with zipfile.ZipFile(first["path"]) as archive:
            infos = archive.infolist()
        self.assertEqual([info.filename for info in infos], sorted(info.filename for info in infos))
        for info in infos:
            with self.subTest(member=info.filename):
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)
                self.assertEqual((info.external_attr >> 16) & 0o777, 0o600)

    def test_export_filename_and_size_limits_fail_closed(self) -> None:
        with self.assertRaises(HandoffValidationError) as unsafe:
            self.create_persona_task(bundle_name="../persona.zip")
        self.assertEqual(unsafe.exception.code, "unsafe_bundle_name")

        with mock.patch("task_bundles.MAX_MEMBER_BYTES", 32):
            with self.assertRaises(HandoffValidationError) as oversized:
                self.create_persona_task()
        self.assertEqual(oversized.exception.code, "task_bundle_too_large")

    def test_persona_bundle_is_self_contained_and_versioned(self) -> None:
        created = self.create_persona_task()
        self.assertTrue(created["filename"].endswith(".alexandria-task.zip"))
        inspected = inspect_task_bundle(created["path"])
        manifest = inspected["manifest"]
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["task_type"], "persona_generation")
        self.assertEqual(manifest["task_label"], "Create one Voice profile")
        self.assertEqual(manifest["native_destination"], "expressive_voices")
        self.assertEqual(manifest["transfer_policy"], "persona_draft")
        self.assertEqual(manifest["guidance"]["profile"], "persona")
        with zipfile.ZipFile(created["path"]) as archive:
            names = set(archive.namelist())
        self.assertTrue(
            {
                TASK_MANIFEST_PATH,
                TASK_INSTRUCTIONS_PATH,
                TASK_INPUT_PATH,
                TASK_SCHEMA_PATH,
                TASK_CHECKSUMS_PATH,
                TASK_GUIDANCE_PATH,
                GUIDANCE_MANIFEST_PATH,
                NONHUMAN_GUIDANCE_PATH,
            }.issubset(names)
        )

    def test_exported_instructions_define_completion_contract_without_repo_access(self) -> None:
        created = self.create_persona_task()
        with zipfile.ZipFile(created["path"]) as archive:
            original_members = {
                name: archive.read(name)
                for name in archive.namelist()
            }
        instructions = original_members[TASK_INSTRUCTIONS_PATH].decode("utf-8")
        for phrase in (
            "preserve every original ZIP member byte-for-byte",
            "lowercase SHA-256 hexadecimal digests of the exact member bytes",
            '"schema_version": 2',
            '"task_id"',
            '"manifest_fingerprint"',
            '"result_path": "result/result.json"',
            '"result_size_bytes"',
            '"result_sha256"',
            '"completed_at_utc"',
            "RFC 3339 UTC timestamp",
            "SHA-256 of the exact original manifest.json bytes",
            "SHA-256 of the exact result/result.json bytes",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, instructions)

        result = {
            "description": "Tenor, clear and lightly nasal.",
            "ref_text": "Good evening.",
        }
        result_payload = (
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        manifest_payload = original_members[TASK_MANIFEST_PATH]
        manifest = json.loads(manifest_payload.decode("utf-8"))
        completion = {
            "schema_version": 2,
            "task_id": manifest["task_id"],
            "manifest_fingerprint": hashlib.sha256(
                manifest_payload
            ).hexdigest(),
            "result_path": DEFAULT_RESULT_PATH,
            "result_size_bytes": len(result_payload),
            "result_sha256": hashlib.sha256(result_payload).hexdigest(),
            "completed_at_utc": "2026-07-19T21:00:00Z",
        }
        completed_path = self.root / "manual-completed.zip"
        with zipfile.ZipFile(completed_path, "w") as archive:
            for name, payload in original_members.items():
                archive.writestr(name, payload)
            archive.writestr(DEFAULT_RESULT_PATH, result_payload)
            archive.writestr(
                COMPLETION_PATH,
                (
                    json.dumps(
                        completion,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8"),
            )
        inspected = inspect_completed_task_bundle(
            path=completed_path,
            current_source_fingerprint="a" * 64,
            current_artifact_fingerprints={"annotated_script": "b" * 64},
        )
        self.assertEqual(inspected["result"], result)

    def test_voice_reference_snapshot_is_hash_pinned_and_task_bound(self) -> None:
        guidance_root = Path(task_bundle_module.GUIDANCE_ROOT)
        manifest = json.loads(
            (guidance_root / "voice-reference.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["alexandria_task_bundle_schema_version"], 2)
        self.assertEqual(manifest["alexandria_guidance_schema_version"], 1)
        self.assertTrue(manifest["source"]["url"].startswith("https://"))
        self.assertEqual(len(manifest["source"]["upstream_commit"]), 40)
        self.assertIn("reviewed_at_utc", manifest["source"])
        for filename, expected in manifest["content_sha256"].items():
            actual = hashlib.sha256((guidance_root / filename).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, filename)
        for profile, tasks in manifest["task_bindings"].items():
            for task_type in tasks:
                self.assertEqual(TASK_REGISTRY[task_type].guidance_profile, profile)

        copied = self.root / "guidance"
        shutil.copytree(guidance_root, copied)
        (copied / "persona.md").write_text("tampered\n", encoding="utf-8")
        with mock.patch.object(task_bundle_module, "GUIDANCE_ROOT", copied):
            with self.assertRaises(HandoffValidationError) as error:
                self.create_persona_task(output_dir=self.root / "tampered")
        self.assertEqual(error.exception.code, "guidance_content_mismatch")

    def test_voice_guidance_separates_identity_from_delivery(self) -> None:
        created = self.create_persona_task()
        with zipfile.ZipFile(created["path"]) as archive:
            persona = archive.read(TASK_GUIDANCE_PATH).decode("utf-8")
            nonhuman = archive.read(NONHUMAN_GUIDANCE_PATH).decode("utf-8")
        self.assertIn("stable and acoustic", persona)
        self.assertIn("Do not put current emotion", persona)
        self.assertIn("never fabricate a quotation", persona)
        self.assertIn("vocal apparatus", nonhuman)
        self.assertIn("Do not reduce every creature", nonhuman)

    def test_line_direction_guidance_does_not_restate_identity(self) -> None:
        created = create_task_bundle(
            output_dir=self.root,
            task_type="line_direction_generation",
            input_payload={
                "entries": [
                    {
                        "speaker": "THE DOCTOR",
                        "text": "Run.",
                        "instruct": "",
                    }
                ]
            },
            application_version="test",
        )
        with zipfile.ZipFile(created["path"]) as archive:
            guidance = archive.read(TASK_GUIDANCE_PATH).decode("utf-8")
        self.assertIn("Edit only the line's `instruct` field", guidance)
        self.assertIn("Do not restate", guidance)

    def test_every_registered_task_can_export_minimum_safe_input(self) -> None:
        inputs = {
            "script_generation": {"source_text": "Text."},
            "complete_cast_dossier": {
                "requested_sections": {
                    "roster_and_relationships": True,
                    "voice_personas_and_designs": True,
                    "visual_dossiers": True,
                },
                "source_text": "Text.",
                "source_context": {"fingerprint": "a" * 64},
                "script_speakers": [
                    {"speaker": "A", "sample_lines": ["Text."]}
                ],
            },
            "script_review": {
                "entries": [
                    {"speaker": "NARRATOR", "text": "Text.", "instruct": "Even."}
                ]
            },
            "roster_discovery": {"source_passage": "Text."},
            "roster_reconciliation": {"observations": [{"id": "one"}]},
            "persona_catalog_generation": {
                "speakers": [
                    {"speaker": "A", "sample_lines": ["Text."]}
                ]
            },
            "persona_generation": {"speaker": "A", "sample_lines": ["Text."]},
            "persona_refinement": {
                "speaker": "A",
                "sample_lines": ["Text."],
                "existing_persona": {"description": "Tenor.", "ref_text": "Text."},
            },
            "persona_reconciliation": {
                "speaker": "A",
                "sample_lines": ["Text."],
                "roster_entry": {"id": "a"},
            },
            "persona_audit": {
                "speaker": "A",
                "sample_lines": ["Text."],
                "existing_persona": {"description": "Tenor.", "ref_text": "Text."},
            },
            "visual_discovery": {
                "roster_entry": {"id": "a"},
                "source_passage": "Text.",
            },
            "visual_reconciliation": {
                "observations": [{"id": "one"}],
                "approved_roster": {"entries": [{"id": "a"}]},
            },
            "persistent_voice_description_generation": {
                "speaker": "A",
                "sample_lines": ["Text."],
            },
            "persistent_voice_description_refinement": {
                "speaker": "A",
                "sample_lines": ["Text."],
                "existing_persona": {"description": "Tenor.", "ref_text": "Text."},
            },
            "persistent_voice_description_audit": {
                "speaker": "A",
                "sample_lines": ["Text."],
                "existing_persona": {"description": "Tenor.", "ref_text": "Text."},
            },
            "line_direction_generation": {
                "entries": [
                    {"speaker": "A", "text": "Text.", "instruct": ""}
                ]
            },
            "line_direction_audit": {
                "entries": [
                    {"speaker": "A", "text": "Text.", "instruct": "Even."}
                ]
            },
            "pronunciation_guidance": {
                "schema_version": 1,
                "script_fingerprint": "a" * 64,
                "chunks_fingerprint": "b" * 64,
                "registry_fingerprint": "c" * 64,
                "chunks": [
                    {
                        "chunk_index": 0,
                        "chunk_id": "chunk:0",
                        "speaker": "NARRATOR",
                        "text": "Skaro was silent.",
                        "text_sha256": "d" * 64,
                    }
                ],
                "existing_entries": [],
            },
        }
        for task_type, input_payload in inputs.items():
            definition = TASK_REGISTRY[task_type]
            with self.subTest(task_type=task_type):
                created = create_task_bundle(
                    output_dir=self.root / task_type,
                    task_type=task_type,
                    input_payload=input_payload,
                    application_version="test",
                    target=(
                        {"kind": definition.target_kind, "value": "A"}
                        if definition.target_kind
                        else None
                    ),
                )
                self.assertEqual(
                    inspect_task_bundle(created["path"])["manifest"]["task_type"],
                    task_type,
                )

    def test_completed_zip_round_trip(self) -> None:
        created = self.create_persona_task()
        completed_path = self.root / "completed.alexandria-completed-task.zip"
        create_completed_task_bundle(
            task_bundle_path=created["path"],
            result={
                "description": "Tenor, clear and lightly nasal.",
                "ref_text": "Good evening.",
            },
            output_path=completed_path,
            completed_at_utc="2026-07-19T13:00:00Z",
        )
        inspected = inspect_completed_task_bundle(
            path=completed_path,
            current_source_fingerprint="a" * 64,
            current_artifact_fingerprints={"annotated_script": "b" * 64},
        )
        self.assertEqual(inspected["container"], "completed_zip")
        self.assertEqual(inspected["task_type"], "persona_generation")
        self.assertEqual(inspected["native_destination"], "expressive_voices")
        self.assertEqual(inspected["result"]["ref_text"], "Good evening.")
        with zipfile.ZipFile(completed_path) as archive:
            self.assertIn(DEFAULT_RESULT_PATH, archive.namelist())
            self.assertIn(COMPLETION_PATH, archive.namelist())

    def test_result_envelope_round_trip(self) -> None:
        created = self.create_persona_task()
        envelope = create_result_envelope(
            task_bundle_path=created["path"],
            result={
                "description": "Tenor, clear and lightly nasal.",
                "ref_text": "Good evening.",
            },
        )
        path = self.root / "result.json"
        path.write_text(json.dumps(envelope), encoding="utf-8")
        inspected = inspect_result_envelope(
            envelope_path=path,
            task_bundle_path=created["path"],
            current_source_fingerprint="a" * 64,
            current_artifact_fingerprints={"annotated_script": "b" * 64},
        )
        self.assertEqual(inspected["container"], "result_envelope")
        self.assertEqual(inspected["task_label"], "Create one Voice profile")

    def test_stale_source_and_artifact_fail_closed(self) -> None:
        created = self.create_persona_task()
        completed_path = self.root / "completed.zip"
        create_completed_task_bundle(
            task_bundle_path=created["path"],
            result={"description": "Tenor, clear.", "ref_text": "Good evening."},
            output_path=completed_path,
        )
        with self.assertRaises(HandoffConflictError) as source_error:
            inspect_completed_task_bundle(
                path=completed_path,
                current_source_fingerprint="c" * 64,
                current_artifact_fingerprints={"annotated_script": "b" * 64},
            )
        self.assertEqual(source_error.exception.code, "stale_source")
        with self.assertRaises(HandoffConflictError) as artifact_error:
            inspect_completed_task_bundle(
                path=completed_path,
                current_source_fingerprint="a" * 64,
                current_artifact_fingerprints={"annotated_script": "c" * 64},
            )
        self.assertEqual(artifact_error.exception.code, "stale_artifact")

    def test_tampered_original_member_is_rejected(self) -> None:
        created = self.create_persona_task()
        target = self.root / "tampered.zip"
        with zipfile.ZipFile(created["path"]) as source, zipfile.ZipFile(
            target, "w"
        ) as destination:
            for info in source.infolist():
                payload = source.read(info.filename)
                if info.filename == TASK_INPUT_PATH:
                    payload = b'{"speaker":"OTHER","sample_lines":["Text."]}\n'
                destination.writestr(info, payload)
        with self.assertRaises(HandoffValidationError) as error:
            inspect_task_bundle(target)
        self.assertEqual(error.exception.code, "bundle_fingerprint_mismatch")

    def test_completed_zip_rejects_completion_mismatch(self) -> None:
        created = self.create_persona_task()
        completed = self.root / "completed.zip"
        create_completed_task_bundle(
            task_bundle_path=created["path"],
            result={"description": "Tenor, clear.", "ref_text": "Good evening."},
            output_path=completed,
        )
        target = self.root / "wrong-completion.zip"
        with zipfile.ZipFile(completed) as source, zipfile.ZipFile(
            target, "w"
        ) as destination:
            for info in source.infolist():
                payload = source.read(info.filename)
                if info.filename == COMPLETION_PATH:
                    value = json.loads(payload)
                    value["task_id"] = "task_" + "0" * 32
                    payload = json.dumps(value).encode("utf-8")
                destination.writestr(info, payload)
        with self.assertRaises(HandoffValidationError) as error:
            inspect_completed_task_bundle(
                path=target,
                current_source_fingerprint="a" * 64,
                current_artifact_fingerprints={"annotated_script": "b" * 64},
            )
        self.assertEqual(error.exception.code, "completion_fingerprint_mismatch")

    def test_result_envelope_rejects_wrong_task(self) -> None:
        created = self.create_persona_task()
        envelope = create_result_envelope(
            task_bundle_path=created["path"],
            result={"description": "Tenor, clear.", "ref_text": "Good evening."},
        )
        envelope["alexandria_task"]["task_id"] = "task_" + "0" * 32
        path = self.root / "wrong.json"
        path.write_text(json.dumps(envelope), encoding="utf-8")
        with self.assertRaises(HandoffValidationError) as error:
            inspect_result_envelope(
                envelope_path=path,
                task_bundle_path=created["path"],
            )
        self.assertEqual(error.exception.code, "task_id_mismatch")

    def test_secret_fields_are_rejected(self) -> None:
        with self.assertRaises(HandoffValidationError) as error:
            create_task_bundle(
                output_dir=self.root,
                task_type="script_generation",
                input_payload={
                    "source_text": "Text.",
                    "generation_constraints": {"api_key": "secret"},
                },
                application_version="test",
            )
        self.assertEqual(error.exception.code, "sensitive_field")

    def test_target_is_required_only_for_targeted_tasks(self) -> None:
        with self.assertRaises(HandoffValidationError) as error:
            create_task_bundle(
                output_dir=self.root,
                task_type="persona_generation",
                input_payload={"speaker": "A", "sample_lines": ["Text."]},
                application_version="test",
            )
        self.assertEqual(error.exception.code, "target_required")
        with self.assertRaises(HandoffValidationError) as other:
            create_task_bundle(
                output_dir=self.root,
                task_type="script_generation",
                input_payload={"source_text": "Text."},
                application_version="test",
                target={"kind": "speaker", "value": "A"},
            )
        self.assertEqual(other.exception.code, "unexpected_target")


if __name__ == "__main__":
    unittest.main()
