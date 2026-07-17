from __future__ import annotations

import copy
import json
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from annotated_script_import import (
    AnnotatedScriptImportConflictError,
    AnnotatedScriptImportValidationError,
    build_annotated_script_import_plan,
    create_annotated_script_bundle,
    inspect_annotated_script_import,
)
from generation_state import fingerprint_text, fingerprint_value


class AnnotatedScriptImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = 'The room was quiet. "Run," said the Doctor.'
        self.entries = [
            {
                "speaker": "NARRATOR",
                "text": "The room was quiet.",
                "instruct": "Neutral narration.",
            },
            {
                "speaker": "DOCTOR",
                "text": "Run,",
                "instruct": "Urgent command.",
            },
            {
                "speaker": "NARRATOR",
                "text": "said the Doctor.",
                "instruct": "Neutral narration.",
            },
        ]
        self.script_fingerprint = fingerprint_value(self.entries)
        self.metadata = {
            "schema_version": 1,
            "generated_at_utc": "2026-07-17T20:00:00Z",
            "source": {
                "basename": "book.txt",
                "fingerprint": fingerprint_text(self.source),
                "character_count": len(self.source),
                "chunk_count": 1,
            },
            "generation": {
                "fingerprint": fingerprint_value({"model": "test"}),
                "effective_identity": {"model": "test"},
            },
            "result": {
                "script_fingerprint": self.script_fingerprint,
                "entry_count": len(self.entries),
                "speaker_labels": ["DOCTOR", "NARRATOR"],
            },
            "resume": {
                "resumed": False,
                "previously_completed_chunks": 0,
            },
        }
        self.voice_config = {
            "NARRATOR": {"type": "custom", "voice": "narrator"},
            "DOCTOR": {"type": "design", "description": "Crisp and urgent."},
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_json(self, name: str, value) -> Path:
        path = self.root / name
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def create_bundle(self, **overrides) -> dict:
        arguments = {
            "output_dir": self.root,
            "entries": self.entries,
            "metadata": self.metadata,
            "voice_config": self.voice_config,
            "application_version": "alexandria-test",
            "source_fingerprint": fingerprint_text(self.source),
            "bundle_name": "script-bundle.zip",
            "created_at_utc": "2026-07-17T20:00:00Z",
        }
        arguments.update(overrides)
        return create_annotated_script_bundle(**arguments)

    def test_direct_json_without_source_is_loadable_but_unverified(self) -> None:
        path = self.write_json("import.json", self.entries)
        candidate = inspect_annotated_script_import(
            import_path=path,
            current_script_fingerprint=fingerprint_value([]),
            checkpoint_status="none",
            generated_audio_count=4,
        )
        self.assertEqual(candidate["format"], "json")
        self.assertEqual(candidate["summary"]["entry_count"], 3)
        self.assertEqual(candidate["summary"]["speaker_labels"], ["DOCTOR", "NARRATOR"])
        self.assertEqual(candidate["provenance"]["status"], "unverified")
        self.assertEqual(
            candidate["provenance"]["label"],
            "Imported — source fidelity not verified",
        )
        self.assertTrue(candidate["consequences"]["mark_generated_audio_stale"])
        plan = build_annotated_script_import_plan(
            candidate=candidate,
            current_script_fingerprint=fingerprint_value([]),
            checkpoint_status="none",
        )
        self.assertEqual(plan["status"], "ready")
        actions = {action["action"]: action for action in plan["actions"]}
        self.assertIn("remove_metadata", actions)
        self.assertIn("preserve_voice_config", actions)
        self.assertTrue(actions["rebuild_chunks"]["mark_prior_audio_stale"])
        self.assertEqual(actions["checkpoint"]["decision"], "keep")

    def test_selected_source_runs_the_real_fidelity_auditor(self) -> None:
        path = self.write_json("verified.json", self.entries)
        candidate = inspect_annotated_script_import(
            import_path=path,
            source_text=self.source,
        )
        self.assertEqual(candidate["provenance"]["status"], "verified")
        self.assertTrue(candidate["provenance"]["audit"]["passed"])
        self.assertEqual(
            candidate["provenance"]["source_fingerprint"],
            fingerprint_text(self.source),
        )

        changed = copy.deepcopy(self.entries)
        changed[1]["text"] = "Walk,"
        changed_path = self.write_json("changed.json", changed)
        with self.assertRaisesRegex(
            AnnotatedScriptImportValidationError,
            "source-fidelity",
        ) as caught:
            inspect_annotated_script_import(
                import_path=changed_path,
                source_text=self.source,
            )
        self.assertEqual(caught.exception.code, "source_fidelity_failed")
        self.assertFalse(caught.exception.details["audit"]["passed"])

    def test_script_contract_speaker_labels_and_silent_normalization_are_rejected(self) -> None:
        extra = copy.deepcopy(self.entries)
        extra[0]["extra"] = True
        with self.assertRaisesRegex(
            AnnotatedScriptImportValidationError,
            "script contract",
        ):
            inspect_annotated_script_import(
                import_path=self.write_json("extra.json", extra),
            )

        lowercase = copy.deepcopy(self.entries)
        lowercase[1]["speaker"] = "Doctor"
        with self.assertRaisesRegex(
            AnnotatedScriptImportValidationError,
            "uppercase",
        ):
            inspect_annotated_script_import(
                import_path=self.write_json("lowercase.json", lowercase),
            )

        padded = copy.deepcopy(self.entries)
        padded[0]["text"] = " The room was quiet. "
        with self.assertRaisesRegex(
            AnnotatedScriptImportValidationError,
            "silent normalization",
        ):
            inspect_annotated_script_import(
                import_path=self.write_json("padded.json", padded),
            )

    def test_malformed_empty_and_unsupported_files_are_rejected(self) -> None:
        malformed = self.root / "malformed.json"
        malformed.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(
            AnnotatedScriptImportValidationError,
            "valid JSON",
        ):
            inspect_annotated_script_import(import_path=malformed)
        with self.assertRaisesRegex(
            AnnotatedScriptImportValidationError,
            "at least one entry",
        ):
            inspect_annotated_script_import(
                import_path=self.write_json("empty.json", []),
            )
        unsupported = self.root / "script.txt"
        unsupported.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(
            AnnotatedScriptImportValidationError,
            "JSON array or Alexandria ZIP",
        ):
            inspect_annotated_script_import(import_path=unsupported)

    def test_versioned_bundle_round_trip_validates_companions_and_provenance(self) -> None:
        record = self.create_bundle()
        candidate = inspect_annotated_script_import(
            import_path=record["path"],
            source_text=self.source,
            current_script_fingerprint=fingerprint_value([]),
        )
        self.assertEqual(candidate["format"], "zip")
        self.assertEqual(candidate["entries"], self.entries)
        self.assertEqual(candidate["metadata"], self.metadata)
        self.assertEqual(candidate["voice_config"], self.voice_config)
        self.assertEqual(candidate["provenance"]["status"], "verified")
        plan = build_annotated_script_import_plan(
            candidate=candidate,
            current_script_fingerprint=fingerprint_value([]),
            checkpoint_status="none",
        )
        action_names = [action["action"] for action in plan["actions"]]
        self.assertIn("replace_metadata", action_names)
        self.assertIn("replace_voice_config", action_names)

    def test_metadata_and_bundle_source_mismatches_are_rejected(self) -> None:
        bad_metadata = copy.deepcopy(self.metadata)
        bad_metadata["result"]["script_fingerprint"] = fingerprint_value([])
        with self.assertRaisesRegex(
            AnnotatedScriptImportValidationError,
            "Metadata script fingerprint",
        ):
            self.create_bundle(
                metadata=bad_metadata,
                bundle_name="bad-meta.zip",
            )

        valid_record = self.create_bundle(bundle_name="valid-for-meta-tamper.zip")
        forged = self.root / "forged-meta-mismatch.zip"
        with zipfile.ZipFile(valid_record["path"]) as source:
            payloads = {
                name: source.read(name)
                for name in source.namelist()
            }
        manifest = json.loads(payloads["manifest.json"])
        metadata = json.loads(payloads["annotated_script.meta.json"])
        metadata["result"]["script_fingerprint"] = fingerprint_value([])
        payloads["annotated_script.meta.json"] = json.dumps(metadata).encode("utf-8")
        manifest["metadata_fingerprint"] = fingerprint_value(metadata)
        manifest_seed = {
            key: value
            for key, value in manifest.items()
            if key != "bundle_id"
        }
        manifest["bundle_id"] = (
            "script_bundle_"
            + fingerprint_value(manifest_seed)[:24]
        )
        payloads["manifest.json"] = json.dumps(manifest).encode("utf-8")
        with zipfile.ZipFile(forged, "w") as archive:
            for name, payload in payloads.items():
                archive.writestr(name, payload)
        with self.assertRaisesRegex(
            AnnotatedScriptImportValidationError,
            "Metadata script fingerprint",
        ):
            inspect_annotated_script_import(import_path=forged)

        with self.assertRaisesRegex(
            AnnotatedScriptImportValidationError,
            "does not match its metadata",
        ):
            self.create_bundle(
                source_fingerprint=fingerprint_text("Different source."),
                bundle_name="internally-bad-source.zip",
            )

        record = self.create_bundle(
            metadata=None,
            source_fingerprint=fingerprint_text("Different source."),
            bundle_name="bad-source.zip",
        )
        with self.assertRaisesRegex(
            AnnotatedScriptImportValidationError,
            "bundle source fingerprint",
        ):
            inspect_annotated_script_import(
                import_path=record["path"],
                source_text=self.source,
            )

    def test_tampered_traversal_extra_and_symlink_archives_are_rejected(self) -> None:
        record = self.create_bundle()
        original = Path(record["path"])
        tampered = self.root / "tampered.zip"
        with zipfile.ZipFile(original) as source, zipfile.ZipFile(tampered, "w") as target:
            for name in source.namelist():
                payload = source.read(name)
                if name == "annotated_script.json":
                    payload = json.dumps(self.entries[:1]).encode("utf-8")
                target.writestr(name, payload)
        with self.assertRaisesRegex(
            AnnotatedScriptImportValidationError,
            "script_fingerprint",
        ):
            inspect_annotated_script_import(import_path=tampered)

        traversal = self.root / "traversal.zip"
        with zipfile.ZipFile(traversal, "w") as archive:
            archive.writestr("../manifest.json", "{}")
        with self.assertRaisesRegex(
            AnnotatedScriptImportValidationError,
            "Unsafe archive member",
        ):
            inspect_annotated_script_import(import_path=traversal)

        extra = self.root / "extra.zip"
        with zipfile.ZipFile(original) as source, zipfile.ZipFile(extra, "w") as target:
            for name in source.namelist():
                target.writestr(name, source.read(name))
            target.writestr("notes.txt", "unexpected")
        with self.assertRaisesRegex(
            AnnotatedScriptImportValidationError,
            "unexpected members",
        ):
            inspect_annotated_script_import(import_path=extra)

        symlink = self.root / "symlink.zip"
        with zipfile.ZipFile(symlink, "w") as archive:
            info = zipfile.ZipInfo("manifest.json")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "annotated_script.json")
        with self.assertRaisesRegex(
            AnnotatedScriptImportValidationError,
            "symbolic link",
        ):
            inspect_annotated_script_import(import_path=symlink)

    def test_checkpoint_choice_is_explicit_and_optimistic_state_is_enforced(self) -> None:
        current = fingerprint_value([])
        candidate = inspect_annotated_script_import(
            import_path=self.write_json("checkpoint.json", self.entries),
            current_script_fingerprint=current,
            checkpoint_status="resumable",
        )
        with self.assertRaisesRegex(
            AnnotatedScriptImportConflictError,
            "Choose whether",
        ):
            build_annotated_script_import_plan(
                candidate=candidate,
                current_script_fingerprint=current,
                checkpoint_status="resumable",
            )
        cancelled = build_annotated_script_import_plan(
            candidate=candidate,
            current_script_fingerprint=current,
            checkpoint_status="resumable",
            checkpoint_decision="cancel",
        )
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["actions"], [])

        discarded = build_annotated_script_import_plan(
            candidate=candidate,
            current_script_fingerprint=current,
            checkpoint_status="resumable",
            checkpoint_decision="discard",
        )
        checkpoint_action = next(
            action
            for action in discarded["actions"]
            if action["action"] == "checkpoint"
        )
        self.assertEqual(checkpoint_action["decision"], "discard")

        kept = build_annotated_script_import_plan(
            candidate=candidate,
            current_script_fingerprint=current,
            checkpoint_status="resumable",
            checkpoint_decision="keep",
        )
        self.assertTrue(any("may be incompatible" in warning for warning in kept["warnings"]))

        with self.assertRaisesRegex(
            AnnotatedScriptImportConflictError,
            "current annotated script changed",
        ):
            build_annotated_script_import_plan(
                candidate=candidate,
                current_script_fingerprint=fingerprint_value([{"changed": True}]),
                checkpoint_status="resumable",
                checkpoint_decision="discard",
            )
        with self.assertRaisesRegex(
            AnnotatedScriptImportConflictError,
            "checkpoint changed",
        ):
            build_annotated_script_import_plan(
                candidate=candidate,
                current_script_fingerprint=current,
                checkpoint_status="finalization_only",
                checkpoint_decision="discard",
            )

    def test_candidate_mutation_is_detected_before_planning(self) -> None:
        candidate = inspect_annotated_script_import(
            import_path=self.write_json("candidate.json", self.entries),
            checkpoint_status="none",
        )
        candidate["entries"][0]["text"] = "Changed after review."
        with self.assertRaisesRegex(
            AnnotatedScriptImportValidationError,
            "changed after inspection",
        ):
            build_annotated_script_import_plan(
                candidate=candidate,
                current_script_fingerprint=None,
                checkpoint_status="none",
            )

        forged = inspect_annotated_script_import(
            import_path=self.write_json("forged-candidate.json", self.entries),
            checkpoint_status="none",
        )
        forged["provenance"] = {
            "status": "verified",
            "label": "Imported — source fidelity verified",
            "source_fingerprint": fingerprint_text(self.source),
            "bundle_source_claim": None,
            "audit": {"passed": False},
        }
        protected = {
            key: value
            for key, value in forged.items()
            if key != "import_fingerprint"
        }
        forged["import_fingerprint"] = fingerprint_value(protected)
        with self.assertRaisesRegex(
            AnnotatedScriptImportValidationError,
            "passing fidelity audit",
        ):
            build_annotated_script_import_plan(
                candidate=forged,
                current_script_fingerprint=None,
                checkpoint_status="none",
            )

    def test_nonfinite_companion_values_are_rejected(self) -> None:
        with self.assertRaisesRegex(
            AnnotatedScriptImportValidationError,
            "non-finite number",
        ):
            self.create_bundle(
                voice_config={
                    "DOCTOR": {
                        "temperature": float("nan"),
                    }
                },
                bundle_name="nonfinite.zip",
            )


if __name__ == "__main__":
    unittest.main()
