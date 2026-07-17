from __future__ import annotations

import json
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from chatgpt_handoff import (
    EXPECTED_MEMBERS,
    HandoffConflictError,
    HandoffValidationError,
    create_handoff_bundle,
    inspect_handoff_bundle,
    validate_handoff_result,
)
from generation_state import fingerprint_text, fingerprint_value
from llm_schemas import get_schema


class ChatGPTHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source_fingerprint = fingerprint_text("Source text.")
        self.artifact_fingerprint = fingerprint_value(
            [{"speaker": "NARRATOR", "text": "Source text.", "instruct": "Neutral."}]
        )
        self.schema = get_schema("review")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create_review_bundle(self) -> dict:
        return create_handoff_bundle(
            output_dir=self.root,
            task_type="script_review",
            stage_prompt="Review speaker boundaries without changing any text.",
            input_payload={
                "entries": [
                    {
                        "speaker": "NARRATOR",
                        "text": "Source text.",
                        "instruct": "Neutral.",
                    }
                ],
                "context_before": "",
                "context_after": "",
            },
            output_schema=self.schema,
            application_version="alexandria-test",
            source_fingerprint=self.source_fingerprint,
            artifact_fingerprints={
                "annotated_script": self.artifact_fingerprint,
            },
            created_at_utc="2026-07-17T20:00:00Z",
            bundle_name="review-handoff.zip",
        )

    def test_round_trip_bundle_has_exact_confined_members_and_fingerprints(self) -> None:
        record = self.create_review_bundle()
        path = Path(record["path"])
        self.assertEqual(path.parent, self.root)
        self.assertEqual(path.name, "review-handoff.zip")
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(set(archive.namelist()), set(EXPECTED_MEMBERS))
            self.assertFalse(any("/" in name for name in archive.namelist()))

        inspected = inspect_handoff_bundle(path)
        manifest = inspected["manifest"]
        self.assertEqual(manifest["task_type"], "script_review")
        self.assertEqual(manifest["source_fingerprint"], self.source_fingerprint)
        self.assertEqual(
            manifest["artifact_fingerprints"],
            {"annotated_script": self.artifact_fingerprint},
        )
        self.assertEqual(manifest["input_fingerprint"], fingerprint_value(inspected["input"]))
        self.assertEqual(manifest["schema_fingerprint"], fingerprint_value(inspected["schema"]))
        self.assertIn("Return only valid JSON", inspected["prompt"])
        self.assertIn("result.json", inspected["prompt"])

        result_path = self.root / "result.json"
        result = inspected["input"]["entries"]
        result_path.write_text(json.dumps(result), encoding="utf-8")
        validated = validate_handoff_result(
            bundle_path=path,
            result_path=result_path,
            current_source_fingerprint=self.source_fingerprint,
            current_artifact_fingerprints={
                "annotated_script": self.artifact_fingerprint,
            },
        )
        self.assertEqual(validated["result"], result)
        self.assertEqual(validated["task_type"], "script_review")
        self.assertTrue(validated["review"]["source_fingerprint_verified"])
        self.assertEqual(
            validated["review"]["artifact_fingerprints_verified"],
            ["annotated_script"],
        )

    def test_all_supported_stage_contracts_accept_minimum_confined_input(self) -> None:
        cases = {
            "script_generation": {"source_text": "Text."},
            "script_review": {"entries": [{"speaker": "NARRATOR"}]},
            "roster_discovery": {"source_passage": "Text."},
            "roster_reconciliation": {"observations": [{"name": "Doctor"}]},
            "persona_generation": {"speaker": "DOCTOR", "sample_lines": ["Run."]},
            "visual_discovery": {
                "roster_entry": {"canonical_name": "DOCTOR"},
                "source_passage": "A short figure crossed the room.",
            },
        }
        contracts = {
            "script_generation": "script",
            "script_review": "review",
            "roster_discovery": "roster_discovery",
            "roster_reconciliation": "roster_reconciliation",
            "persona_generation": "persona",
            "visual_discovery": "visual_discovery",
        }
        for task_type, payload in cases.items():
            with self.subTest(task_type=task_type):
                record = create_handoff_bundle(
                    output_dir=self.root,
                    task_type=task_type,
                    stage_prompt="Complete the structured task.",
                    input_payload=payload,
                    output_schema=get_schema(contracts[task_type]),
                    application_version="test",
                    bundle_name=f"{task_type}.zip",
                    created_at_utc="2026-07-17T20:00:00Z",
                )
                self.assertEqual(
                    inspect_handoff_bundle(record["path"])["manifest"]["task_type"],
                    task_type,
                )

    def test_unknown_unexpected_sensitive_and_non_json_inputs_are_rejected(self) -> None:
        base = {
            "output_dir": self.root,
            "stage_prompt": "Complete it.",
            "output_schema": get_schema("script"),
            "application_version": "test",
            "created_at_utc": "2026-07-17T20:00:00Z",
        }
        with self.assertRaisesRegex(HandoffValidationError, "Unsupported"):
            create_handoff_bundle(
                **base,
                task_type="unknown_task",
                input_payload={"source_text": "Text."},
            )
        with self.assertRaisesRegex(HandoffValidationError, "Unexpected"):
            create_handoff_bundle(
                **base,
                task_type="script_generation",
                input_payload={"source_text": "Text.", "whole_project": {}},
            )
        with self.assertRaisesRegex(HandoffValidationError, "not permitted"):
            create_handoff_bundle(
                **base,
                task_type="script_generation",
                input_payload={
                    "source_text": "Text.",
                    "generation_constraints": {"api_key": "do-not-export"},
                },
            )
        with self.assertRaisesRegex(HandoffValidationError, "cannot be represented"):
            create_handoff_bundle(
                **base,
                task_type="script_generation",
                input_payload={"source_text": Path("book.txt")},
            )

    def test_export_schema_must_match_the_native_stage_contract(self) -> None:
        with self.assertRaisesRegex(HandoffValidationError, "stage contract"):
            create_handoff_bundle(
                output_dir=self.root,
                task_type="script_generation",
                stage_prompt="Complete it.",
                input_payload={"source_text": "Text."},
                output_schema={"type": "array", "items": {"type": "string"}},
                application_version="test",
            )

    def test_bundle_and_result_filenames_must_be_confined(self) -> None:
        with self.assertRaisesRegex(HandoffValidationError, "confined filename"):
            create_handoff_bundle(
                output_dir=self.root,
                task_type="script_generation",
                stage_prompt="Complete it.",
                input_payload={"source_text": "Text."},
                output_schema=get_schema("script"),
                application_version="test",
                bundle_name="../escape.zip",
            )
        with self.assertRaisesRegex(HandoffValidationError, "confined JSON filename"):
            create_handoff_bundle(
                output_dir=self.root,
                task_type="script_generation",
                stage_prompt="Complete it.",
                input_payload={"source_text": "Text."},
                output_schema=get_schema("script"),
                application_version="test",
                expected_output_filename="../result.json",
            )

    def test_tampered_payload_is_rejected_by_manifest_fingerprint(self) -> None:
        record = self.create_review_bundle()
        original = Path(record["path"])
        tampered = self.root / "tampered.zip"
        with zipfile.ZipFile(original) as source, zipfile.ZipFile(tampered, "w") as target:
            for name in source.namelist():
                payload = source.read(name)
                if name == "input.json":
                    payload = (
                        b'{"entries": [{"speaker": "NARRATOR", '
                        b'"text": "Changed.", "instruct": "Neutral."}]}\n'
                    )
                target.writestr(name, payload)
        with self.assertRaisesRegex(HandoffValidationError, "input_fingerprint"):
            inspect_handoff_bundle(tampered)

    def test_archive_traversal_extra_members_and_symlinks_are_rejected(self) -> None:
        traversal = self.root / "traversal.zip"
        with zipfile.ZipFile(traversal, "w") as archive:
            archive.writestr("../manifest.json", b"{}")
        with self.assertRaisesRegex(HandoffValidationError, "Unsafe archive member"):
            inspect_handoff_bundle(traversal)

        extra = self.root / "extra.zip"
        with zipfile.ZipFile(extra, "w") as archive:
            for name in EXPECTED_MEMBERS:
                archive.writestr(name, b"{}" if name.endswith(".json") else b"Prompt")
            archive.writestr("notes.txt", b"unexpected")
        with self.assertRaisesRegex(HandoffValidationError, "unexpected"):
            inspect_handoff_bundle(extra)

        symlink = self.root / "symlink.zip"
        with zipfile.ZipFile(symlink, "w") as archive:
            info = zipfile.ZipInfo("manifest.json")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "input.json")
        with self.assertRaisesRegex(HandoffValidationError, "symbolic link"):
            inspect_handoff_bundle(symlink)

    def test_stale_source_and_artifact_results_are_rejected(self) -> None:
        record = self.create_review_bundle()
        result_path = self.root / "result.json"
        result_path.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(HandoffConflictError, "source changed"):
            validate_handoff_result(
                bundle_path=record["path"],
                result_path=result_path,
                current_source_fingerprint=fingerprint_text("Different source."),
                current_artifact_fingerprints={
                    "annotated_script": self.artifact_fingerprint,
                },
            )
        with self.assertRaisesRegex(HandoffConflictError, "changed"):
            validate_handoff_result(
                bundle_path=record["path"],
                result_path=result_path,
                current_source_fingerprint=self.source_fingerprint,
                current_artifact_fingerprints={
                    "annotated_script": fingerprint_value([]),
                },
            )

    def test_malformed_and_wrong_root_type_results_are_rejected(self) -> None:
        record = self.create_review_bundle()
        malformed = self.root / "malformed.json"
        malformed.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(HandoffValidationError, "not valid JSON"):
            validate_handoff_result(
                bundle_path=record["path"],
                result_path=malformed,
                current_source_fingerprint=self.source_fingerprint,
                current_artifact_fingerprints={
                    "annotated_script": self.artifact_fingerprint,
                },
            )
        wrong_root = self.root / "wrong-root.json"
        wrong_root.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(HandoffValidationError, "root type"):
            validate_handoff_result(
                bundle_path=record["path"],
                result_path=wrong_root,
                current_source_fingerprint=self.source_fingerprint,
                current_artifact_fingerprints={
                    "annotated_script": self.artifact_fingerprint,
                },
            )

        invalid_entry = self.root / "invalid-entry.json"
        invalid_entry.write_text(
            json.dumps(
                [
                    {
                        "speaker": "NARRATOR",
                        "text": "Source text.",
                        "instruct": "Neutral.",
                        "unexpected": True,
                    }
                ]
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(HandoffValidationError, "stage contract"):
            validate_handoff_result(
                bundle_path=record["path"],
                result_path=invalid_entry,
                current_source_fingerprint=self.source_fingerprint,
                current_artifact_fingerprints={
                    "annotated_script": self.artifact_fingerprint,
                },
            )


if __name__ == "__main__":
    unittest.main()
