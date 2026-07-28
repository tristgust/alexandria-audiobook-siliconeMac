from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from audio_artifacts import audio_binding_fingerprint
from project_flow import (
    PROJECT_FLOW_SCHEMA_VERSION,
    PROJECT_FLOW_STAGE_KEYS,
    PROJECT_FLOW_STAGE_STATES,
    build_project_flow_summary,
    cast_aggregate_to_flow_evidence,
    export_aggregate_to_flow_evidence,
    inspect_cast_evidence,
    inspect_compatibility_evidence,
    inspect_produce_evidence,
    inspect_project_flow,
    produce_aggregate_to_flow_evidence,
    inspect_script_evidence,
)


class ProjectFlowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = {
            "id": "project_123",
            "name": "Test project",
            "latest_meaningful_activity": "2026-07-20T12:00:00Z",
            "archive_state": "active",
        }
        self.source = {
            "selected": True,
            "available": True,
            "title": "Book",
            "filename": "book.txt",
            "type": "txt",
            "source_language": "English",
            "output_language": "English",
            "fingerprint": "source-fingerprint",
            "error": None,
        }
        self.script = {
            "source_available": True,
            "process": {"running": False},
            "resumable": False,
            "failed": False,
            "artifact_exists": True,
            "structure_valid": True,
            "attribution_valid": True,
            "fidelity_valid": True,
            "artifact_current": True,
            "provenance_recorded": True,
            "finalization_complete": True,
            "review_required": False,
            "accepted": True,
            "fingerprints": {
                "source": "source-fingerprint",
                "script": "script-fingerprint",
                "generation": "generation-fingerprint",
            },
        }
        self.cast = {
            "process": {"running": False},
            "resumable": False,
            "failed": False,
            "roster_exists": True,
            "review_required": False,
            "roster_approved": True,
            "roster_current": True,
            "required_speaking_characters": 2,
            "valid_production_voices": 2,
            "unresolved_identity_ids": [],
            "ambiguous_mapping_ids": [],
            "missing_voice_ids": [],
            "invalid_voice_ids": [],
            "invalid_clone_ids": [],
            "controlled_clone_approval_missing_ids": [],
            "invalid_adapter_ids": [],
            "stale_voice_ids": [],
            "fingerprints": {
                "script": "script-fingerprint",
                "roster": "roster-fingerprint",
                "voice_config": "voice-fingerprint",
            },
        }
        self.produce = {
            "process": {"running": False},
            "resumable": False,
            "required_chunks": 2,
            "current_chunks": 2,
            "missing_chunk_ids": [],
            "stale_chunk_ids": [],
            "failed_chunk_ids": [],
            "hash_invalid_chunk_ids": [],
            "review_chunk_ids": [],
            "listening_chunk_ids": [],
            "fingerprints": {
                "chunks": "chunks-fingerprint",
                "voice_config": "voice-fingerprint",
                "synthesis": "synthesis-fingerprint",
            },
        }
        self.export = {
            "process": {"running": False},
            "failed": False,
            "missing_metadata_fields": [],
            "invalid_chapter_ids": [],
            "unavailable_formats": [],
            "output_exists": True,
            "output_current": True,
            "output_valid": True,
            "fingerprints": {
                "build_dependencies": "build-fingerprint",
                "output": "output-fingerprint",
            },
        }

    def summary(self, **overrides):
        values = {
            "project": self.project,
            "source": self.source,
            "script": self.script,
            "cast": self.cast,
            "produce": self.produce,
            "export": self.export,
            "compatibility": {"state": "current"},
            "generated_at_utc": "2026-07-20T12:00:00Z",
        }
        values.update(overrides)
        return build_project_flow_summary(**values)

    def test_versioned_summary_has_exact_stage_order_and_vocabulary(self) -> None:
        summary = self.summary()

        self.assertEqual(summary["schema_version"], PROJECT_FLOW_SCHEMA_VERSION)
        self.assertEqual(
            tuple(stage["key"] for stage in summary["stages"]),
            PROJECT_FLOW_STAGE_KEYS,
        )
        for stage in summary["stages"]:
            self.assertIn(stage["state"], PROJECT_FLOW_STAGE_STATES)
            self.assertEqual(stage, summary["stage_map"][stage["key"]])
        self.assertEqual(summary["completion_state"], "complete")
        self.assertEqual(summary["recommended_stage"], "export")
        self.assertEqual(summary["blocker_count"], 0)

    def test_recommended_stage_is_first_incomplete_stage(self) -> None:
        cast = copy.deepcopy(self.cast)
        cast["missing_voice_ids"] = ["character_doctor"]
        cast["valid_production_voices"] = 1

        summary = self.summary(cast=cast)

        self.assertEqual(summary["stage_map"]["script"]["state"], "complete")
        self.assertEqual(summary["stage_map"]["cast"]["state"], "blocked")
        self.assertEqual(summary["stage_map"]["produce"]["state"], "blocked")
        self.assertEqual(summary["recommended_stage"], "cast")
        self.assertEqual(
            summary["safe_next_action"]["native_destination"],
            "cast",
        )

    def test_script_completion_is_impossible_when_any_authoritative_gate_fails(self) -> None:
        cases = {
            "structure_valid": "script_structure_invalid",
            "attribution_valid": "script_attribution_invalid",
            "fidelity_valid": "script_source_fidelity_failed",
            "artifact_current": "script_artifact_stale",
            "provenance_recorded": "script_provenance_missing",
            "finalization_complete": "script_finalization_incomplete",
        }
        for field, expected_code in cases.items():
            with self.subTest(field=field):
                script = copy.deepcopy(self.script)
                script[field] = False
                stage = self.summary(script=script)["stage_map"]["script"]
                self.assertNotEqual(stage["state"], "complete")
                self.assertIn(expected_code, {item["code"] for item in stage["blockers"]})

    def test_unverified_or_unaccepted_script_requires_review(self) -> None:
        script = copy.deepcopy(self.script)
        script["fidelity_valid"] = None
        script["accepted"] = False
        script["review_required"] = True

        stage = self.summary(script=script)["stage_map"]["script"]

        self.assertEqual(stage["state"], "review_required")
        self.assertEqual(stage["safe_next_action"]["id"], "review_script")

    def test_resumable_script_exposes_one_resume_action(self) -> None:
        script = copy.deepcopy(self.script)
        script.update(
            {
                "artifact_exists": False,
                "resumable": True,
                "accepted": False,
            }
        )
        stage = self.summary(script=script)["stage_map"]["script"]
        self.assertEqual(stage["state"], "resumable")
        self.assertEqual(stage["safe_next_action"]["id"], "resume_script_generation")

    def test_cast_completion_is_impossible_for_every_required_identity_or_voice_gate(self) -> None:
        cases = {
            "unresolved_identity_ids": "cast_identity_unresolved",
            "ambiguous_mapping_ids": "cast_script_label_ambiguous",
            "missing_voice_ids": "cast_voice_missing",
            "invalid_voice_ids": "cast_voice_invalid",
            "invalid_clone_ids": "cast_clone_reference_invalid",
            "controlled_clone_approval_missing_ids": "cast_controlled_clone_approval_invalid",
            "invalid_adapter_ids": "cast_adapter_invalid",
            "stale_voice_ids": "cast_voice_stale",
        }
        for field, expected_code in cases.items():
            with self.subTest(field=field):
                cast = copy.deepcopy(self.cast)
                cast[field] = ["character_doctor"]
                cast["valid_production_voices"] = 1
                stage = self.summary(cast=cast)["stage_map"]["cast"]
                self.assertNotEqual(stage["state"], "complete")
                self.assertIn(expected_code, {item["code"] for item in stage["blockers"]})
                blocker = next(
                    item for item in stage["blockers"] if item["code"] == expected_code
                )
                self.assertEqual(blocker["native_destination"], "cast")
                self.assertEqual(blocker["target_id"], "character_doctor")

    def test_cast_does_not_require_optional_visual_or_training_artifacts(self) -> None:
        cast = copy.deepcopy(self.cast)
        cast.update(
            {
                "visual_dossier_exists": False,
                "expressive_reference_bank_exists": False,
                "dataset_exists": False,
                "training_project_exists": False,
            }
        )
        stage = self.summary(cast=cast)["stage_map"]["cast"]
        self.assertEqual(stage["state"], "complete")

    def test_produce_completion_is_impossible_for_audio_and_review_failures(self) -> None:
        cases = {
            "missing_chunk_ids": ("produce_audio_missing", "ready"),
            "stale_chunk_ids": ("produce_audio_stale", "stale"),
            "failed_chunk_ids": ("produce_audio_failed", "failed"),
            "hash_invalid_chunk_ids": ("produce_audio_hash_invalid", "failed"),
            "review_chunk_ids": ("produce_review_required", "review_required"),
            "listening_chunk_ids": ("produce_listening_required", "review_required"),
        }
        for field, (expected_code, expected_state) in cases.items():
            with self.subTest(field=field):
                produce = copy.deepcopy(self.produce)
                produce[field] = ["chunk:7"]
                produce["current_chunks"] = 1
                stage = self.summary(produce=produce)["stage_map"]["produce"]
                self.assertEqual(stage["state"], expected_state)
                self.assertIn(expected_code, {item["code"] for item in stage["blockers"]})
                blocker = next(
                    item for item in stage["blockers"] if item["code"] == expected_code
                )
                self.assertEqual(blocker["native_destination"], "produce")
                self.assertEqual(blocker["target_id"], "chunk:7")

    def test_export_is_hard_blocked_by_produce_and_exact_export_fields(self) -> None:
        produce = copy.deepcopy(self.produce)
        produce["stale_chunk_ids"] = ["chunk:2"]
        produce["current_chunks"] = 1
        blocked = self.summary(produce=produce)["stage_map"]["export"]
        self.assertEqual(blocked["state"], "blocked")
        self.assertIn(
            "export_produce_dependency_incomplete",
            {item["code"] for item in blocked["blockers"]},
        )
        self.assertEqual(blocked["blockers"][-1]["native_destination"], "produce")

        export = copy.deepcopy(self.export)
        export.update(
            {
                "output_exists": False,
                "output_current": False,
                "output_valid": False,
                "missing_metadata_fields": ["title"],
                "invalid_chapter_ids": ["chapter:3"],
            }
        )
        stage = self.summary(export=export)["stage_map"]["export"]
        codes = {item["code"] for item in stage["blockers"]}
        self.assertEqual(stage["state"], "blocked")
        self.assertIn("export_metadata_incomplete", codes)
        self.assertIn("export_chapter_metadata_incomplete", codes)
        title = next(item for item in stage["blockers"] if item["code"] == "export_metadata_incomplete")
        chapter = next(item for item in stage["blockers"] if item["code"] == "export_chapter_metadata_incomplete")
        self.assertEqual(title["target_id"], "metadata:title")
        self.assertEqual(chapter["target_id"], "chapter:3")

    def test_outdated_build_is_stale_not_complete(self) -> None:
        export = copy.deepcopy(self.export)
        export["output_current"] = False
        stage = self.summary(export=export)["stage_map"]["export"]
        self.assertEqual(stage["state"], "stale")
        self.assertIn(
            "export_output_stale",
            {item["code"] for item in stage["blockers"]},
        )

    def test_migration_or_compatibility_blocker_prevents_every_stage_completion(self) -> None:
        summary = self.summary(
            compatibility={
                "state": "migration_required",
                "code": "project_migration_required",
                "title": "Migration required",
                "explanation": "Apply the plan.",
            }
        )
        self.assertEqual(summary["recommended_stage"], "script")
        self.assertEqual(summary["completion_state"], "requires_work")
        self.assertTrue(all(stage["state"] != "complete" for stage in summary["stages"]))
        for stage in summary["stages"]:
            self.assertIn(
                "project_migration_required",
                {item["code"] for item in stage["blockers"]},
            )

    def test_native_roster_compatibility_blocker_starts_at_cast(self) -> None:
        summary = self.summary(
            compatibility={
                "state": "incompatible",
                "code": "project_migration_blocked",
                "title": "Project migration is blocked",
                "explanation": "Approved roster is incompatible.",
                "plan_fingerprint": "plan-fingerprint",
                "native_blockers": [
                    {
                        "code": "project_approved_roster_incompatible",
                        "title": "Approved Cast roster is incompatible",
                        "explanation": "Roster evidence no longer matches the selected source.",
                        "native_destination": "cast",
                        "target_id": "cast:review",
                        "safe_action_id": "review_cast",
                        "affected_stages": ["cast", "produce", "export"],
                    }
                ],
            }
        )
        self.assertEqual(summary["stage_map"]["script"]["state"], "complete")
        self.assertEqual(summary["recommended_stage"], "cast")
        cast = summary["stage_map"]["cast"]
        blocker = next(
            item
            for item in cast["blockers"]
            if item["code"] == "project_approved_roster_incompatible"
        )
        self.assertEqual(blocker["stage"], "cast")
        self.assertEqual(blocker["native_destination"], "cast")
        self.assertEqual(blocker["target_id"], "cast:review")
        self.assertEqual(blocker["dependency_fingerprint"], "plan-fingerprint")

    def test_blockers_have_stable_machine_destinations_and_ids(self) -> None:
        cast = copy.deepcopy(self.cast)
        cast["missing_voice_ids"] = ["character_doctor"]
        one = self.summary(cast=cast)
        two = self.summary(cast=cast)
        first = one["stage_map"]["cast"]["blockers"][0]
        second = two["stage_map"]["cast"]["blockers"][0]
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["code"], "cast_voice_missing")
        self.assertEqual(first["native_destination"], "cast")
        self.assertEqual(first["target_id"], "character_doctor")
        self.assertTrue(first["technical_detail_available"])


class ProjectFlowArtifactInspectionTests(unittest.TestCase):
    SOURCE_TEXT = "The room was quiet.\n\"Hello,\" said the Doctor."

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app_dir = self.root / "app"
        self.app_dir.mkdir()
        self.source_path = self.root / "book.txt"
        self.source_path.write_text(self.SOURCE_TEXT, encoding="utf-8")
        self.script_path = self.root / "annotated_script.json"
        self.script = [
            {
                "speaker": "NARRATOR",
                "text": "The room was quiet.",
                "instruct": "Quiet narration.",
            },
            {
                "speaker": "THE DOCTOR",
                "text": "Hello,",
                "instruct": "A measured greeting.",
            },
        ]
        self.script_path.write_text(json.dumps(self.script), encoding="utf-8")
        self.voice_path = self.root / "voice_config.json"
        self.voice_config = {
            "NARRATOR": {"type": "custom", "voice": "Ryan"},
            "THE DOCTOR": {"type": "custom", "voice": "Aiden"},
        }
        self.voice_path.write_text(json.dumps(self.voice_config), encoding="utf-8")
        self.config_path = self.app_dir / "config.json"
        self.config_path.write_text(json.dumps({"tts": {"language": "English"}}), encoding="utf-8")
        self.roster_path = self.root / "character_roster.json"
        self.roster = {
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
                },
                {
                    "id": "character_doctor",
                    "canonical_name": "The Doctor",
                    "display_name": "The Doctor",
                    "aliases": ["THE DOCTOR"],
                    "titles": [],
                    "nicknames": [],
                    "sample_lines": ["Hello,"],
                    "speaking_status": "speaker",
                    "resolution_status": "resolved",
                },
            ]
        }
        self.roster_path.write_text(json.dumps(self.roster), encoding="utf-8")
        self.roster_status = {
            "process": {"running": False},
            "progress": {"status": "complete"},
            "approved": {
                "status": "approved",
                "compatible_source": True,
                "fingerprint": "roster-fingerprint",
            },
            "draft": {"status": "missing"},
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_cast_aggregate_is_project_flow_cast_authority(self) -> None:
        native = {
            "process": {"running": False},
            "resumable": False,
            "failed": False,
            "roster_exists": True,
            "review_required": False,
            "roster_approved": True,
            "roster_current": True,
            "required_speaking_characters": 2,
            "valid_production_voices": 2,
            "unresolved_identity_ids": [],
            "ambiguous_mapping_ids": [],
            "missing_voice_ids": [],
            "invalid_voice_ids": [],
            "invalid_clone_ids": [],
            "controlled_clone_approval_missing_ids": [],
            "invalid_adapter_ids": [],
            "stale_voice_ids": [],
            "fingerprints": {"native": "native-fingerprint"},
        }
        aggregate = {
            "schema_version": 1,
            "summary": {
                "state": "blocked",
                "character_count": 2,
                "required_speaking_count": 2,
                "ready_required_count": 1,
                "blocker_count": 1,
                "complete": False,
            },
            "compatibility": {
                "state": "current",
                "roster_source": "approved",
            },
            "fingerprints": {
                "script": "script-fingerprint",
                "roster": "roster-fingerprint",
                "voice_config": "voice-fingerprint",
            },
            "characters": [
                {
                    "character_id": "character_ready",
                    "required_for_completion": True,
                    "readiness_state": "ready",
                    "voice": {"valid": True, "configuration_key": "READY"},
                    "blockers": [],
                },
                {
                    "character_id": "character_missing",
                    "required_for_completion": True,
                    "readiness_state": "needs_voice",
                    "voice": {"valid": False, "configuration_key": None},
                    "blockers": [
                        {
                            "code": "cast_voice_selection_missing",
                            "blocking": True,
                        }
                    ],
                },
            ],
        }
        evidence = cast_aggregate_to_flow_evidence(
            aggregate,
            native_evidence=native,
        )
        self.assertEqual(evidence["aggregate_schema_version"], 1)
        self.assertEqual(evidence["required_speaking_characters"], 2)
        self.assertEqual(evidence["valid_production_voices"], 1)
        self.assertEqual(
            evidence["missing_voice_ids"],
            ["character_missing"],
        )
        self.assertEqual(evidence["fingerprints"]["script"], "script-fingerprint")
        self.assertEqual(evidence["fingerprints"]["native"], "native-fingerprint")

    def test_managed_import_candidate_drives_script_review_state(self) -> None:
        missing_script = self.root / "pending-import-script.json"
        source_status = {
            "persisted": True,
            "exists": True,
            "readable": True,
            "fingerprint": "source-fingerprint",
            "error": None,
        }
        evidence = inspect_script_evidence(
            source_status=source_status,
            generation_status={
                "process": {"running": False},
                "checkpoint": {"status": "none", "resumable": False},
                "result": {},
            },
            script_path=missing_script,
            lifecycle_status={
                "state": "review_required",
                "accepted": False,
                "primary_action": {
                    "id": "review_imported_script",
                    "label": "Review imported Script",
                },
                "fingerprints": {"accepted_receipt": None},
            },
        )
        self.assertFalse(evidence["artifact_exists"])
        self.assertTrue(evidence["import_candidate_exists"])
        self.assertTrue(evidence["review_required"])

        summary = build_project_flow_summary(
            project={"id": "project_import", "name": "Imported Script"},
            source={
                "selected": True,
                "available": True,
                "title": "Imported Script",
                "fingerprint": "source-fingerprint",
            },
            script=evidence,
            cast={},
            produce={},
            export={},
            compatibility={"state": "current"},
            generated_at_utc="2026-07-20T12:00:00Z",
        )
        stage = summary["stage_map"]["script"]
        self.assertEqual(stage["state"], "review_required")
        self.assertEqual(
            stage["safe_next_action"]["id"],
            "review_imported_script",
        )
        self.assertEqual(summary["recommended_stage"], "script")

    def test_project_flow_requires_explicit_script_lifecycle_acceptance(self) -> None:
        metadata = {
            "source": {
                "verification_status": "verified",
                "fingerprint": "source-fingerprint",
            },
            "generation": {
                "fingerprint": "generation-fingerprint",
                "effective_identity": {
                    "mode": "native",
                    "backend": "ollama",
                    "model_name": "qwen3.5:9b",
                },
            },
            "result": {
                "script_fingerprint": "script-fingerprint",
                "entry_count": len(self.script),
            },
        }
        generation_status = {
            "process": {"running": False},
            "checkpoint": {"status": "none", "resumable": False},
            "result": {
                "status": "complete",
                "script_exists": True,
                "script_status": "valid",
                "script_fingerprint": "script-fingerprint",
                "metadata": metadata,
                "errors": [],
            },
        }
        source_status = {
            "persisted": True,
            "exists": True,
            "readable": True,
            "fingerprint": "source-fingerprint",
        }

        review = inspect_script_evidence(
            source_status=source_status,
            generation_status=generation_status,
            script_path=self.script_path,
            lifecycle_status={
                "state": "review_required",
                "accepted": False,
                "generation_method": "local",
                "provenance": {"method": "local"},
                "fingerprints": {"accepted_receipt": None},
            },
        )
        self.assertFalse(review["accepted"])
        self.assertTrue(review["review_required"])

        accepted = inspect_script_evidence(
            source_status=source_status,
            generation_status=generation_status,
            script_path=self.script_path,
            lifecycle_status={
                "state": "accepted",
                "accepted": True,
                "generation_method": "local",
                "provenance": {"method": "local"},
                "fingerprints": {"accepted_receipt": "receipt-fingerprint"},
            },
        )
        self.assertTrue(accepted["accepted"])
        self.assertFalse(accepted["review_required"])
        self.assertEqual(
            accepted["fingerprints"]["accepted_receipt"],
            "receipt-fingerprint",
        )

    def test_deterministic_long_name_to_short_script_label_mapping_remains_authoritative(self) -> None:
        script = [
            {"speaker": "BERNICE", "text": "One.", "instruct": "Neutral."},
            {"speaker": "NARRATOR (BENNY)", "text": "Two.", "instruct": "Neutral."},
            {"speaker": "ALTON", "text": "Three.", "instruct": "Neutral."},
            {"speaker": "AUBERTIDES", "text": "Four.", "instruct": "Neutral."},
        ]
        self.script_path.write_text(json.dumps(script), encoding="utf-8")
        roster = {
            "entries": [
                {
                    "id": "bernice",
                    "canonical_name": "Bernice Summerfield",
                    "display_name": "Bernice Summerfield",
                    "aliases": ["BERNICE"],
                    "titles": [],
                    "nicknames": ["Benny"],
                    "sample_lines": ["One."],
                    "speaking_status": "speaker",
                    "resolution_status": "resolved",
                },
                {
                    "id": "benny_narrator",
                    "canonical_name": "Benny first-person narrator",
                    "display_name": "Narrator (Benny)",
                    "aliases": ["NARRATOR (BENNY)"],
                    "titles": [],
                    "nicknames": [],
                    "sample_lines": ["Two."],
                    "speaking_status": "narrator",
                    "resolution_status": "resolved",
                },
                {
                    "id": "alton",
                    "canonical_name": "Clive Alton",
                    "display_name": "Clive Alton",
                    "aliases": ["ALTON"],
                    "titles": [],
                    "nicknames": [],
                    "sample_lines": ["Three."],
                    "speaking_status": "speaker",
                    "resolution_status": "resolved",
                },
                {
                    "id": "aubertides",
                    "canonical_name": "The Aubertides",
                    "display_name": "The Aubertides",
                    "aliases": ["AUBERTIDES"],
                    "titles": [],
                    "nicknames": [],
                    "sample_lines": ["Four."],
                    "speaking_status": "speaker",
                    "resolution_status": "resolved",
                },
            ]
        }
        self.roster_path.write_text(json.dumps(roster), encoding="utf-8")
        self.voice_path.write_text(
            json.dumps(
                {
                    label: {"type": "custom", "voice": "Ryan"}
                    for label in (
                        "BERNICE",
                        "NARRATOR (BENNY)",
                        "ALTON",
                        "AUBERTIDES",
                    )
                }
            ),
            encoding="utf-8",
        )

        evidence = inspect_cast_evidence(
            root_dir=self.root,
            roster_status=self.roster_status,
            approved_roster_path=self.roster_path,
            script_path=self.script_path,
            voice_config_path=self.voice_path,
        )

        self.assertEqual(evidence["required_speaking_characters"], 4)
        self.assertEqual(evidence["valid_production_voices"], 4)
        self.assertEqual(evidence["ambiguous_mapping_ids"], [])
        self.assertEqual(evidence["missing_voice_ids"], [])

    def test_clone_requires_existing_audio_and_exact_transcript(self) -> None:
        self.voice_config["THE DOCTOR"] = {
            "type": "clone",
            "ref_audio": "clone_voices/doctor.wav",
            "ref_text": "",
            "clone_backend": "qwen3_base",
        }
        self.voice_path.write_text(json.dumps(self.voice_config), encoding="utf-8")
        evidence = inspect_cast_evidence(
            root_dir=self.root,
            roster_status=self.roster_status,
            approved_roster_path=self.roster_path,
            script_path=self.script_path,
            voice_config_path=self.voice_path,
        )
        self.assertIn("character_doctor", evidence["invalid_clone_ids"])

        audio = self.root / "clone_voices" / "doctor.wav"
        audio.parent.mkdir()
        audio.write_bytes(b"reference-audio")
        self.voice_config["THE DOCTOR"]["ref_text"] = "Exact reference transcript."
        self.voice_path.write_text(json.dumps(self.voice_config), encoding="utf-8")
        evidence = inspect_cast_evidence(
            root_dir=self.root,
            roster_status=self.roster_status,
            approved_roster_path=self.roster_path,
            script_path=self.script_path,
            voice_config_path=self.voice_path,
        )
        self.assertNotIn("character_doctor", evidence["invalid_clone_ids"])

    def test_approved_community_qvoice_is_a_valid_production_voice(self) -> None:
        pack = self.root / "community_qwen_packs" / "ohenry" / "ohenry.qvoice"
        pack.parent.mkdir(parents=True)
        pack.write_bytes(b"approved-community-pack")
        digest = hashlib.sha256(pack.read_bytes()).hexdigest()
        self.voice_config["THE DOCTOR"] = {
            "type": "community_qvoice",
            "voice": "O. Henry reader",
            "description": "An older English storyteller.",
            "community_pack_id": "ohenry",
            "community_pack_path": "community_qwen_packs/ohenry/ohenry.qvoice",
            "community_pack_sha256": digest,
            "community_pack_approval_fingerprint": "a" * 64,
        }
        self.voice_path.write_text(json.dumps(self.voice_config), encoding="utf-8")

        evidence = inspect_cast_evidence(
            root_dir=self.root,
            roster_status=self.roster_status,
            approved_roster_path=self.roster_path,
            script_path=self.script_path,
            voice_config_path=self.voice_path,
        )

        self.assertEqual(evidence["valid_production_voices"], 2)
        self.assertEqual(evidence["invalid_voice_ids"], [])

    def test_community_qvoice_blocks_missing_approval_or_tampered_pack(self) -> None:
        pack = self.root / "community_qwen_packs" / "ohenry" / "ohenry.qvoice"
        pack.parent.mkdir(parents=True)
        pack.write_bytes(b"approved-community-pack")
        self.voice_config["THE DOCTOR"] = {
            "type": "community_qvoice",
            "voice": "O. Henry reader",
            "description": "An older English storyteller.",
            "community_pack_id": "ohenry",
            "community_pack_path": "community_qwen_packs/ohenry/ohenry.qvoice",
            "community_pack_sha256": hashlib.sha256(pack.read_bytes()).hexdigest(),
        }
        self.voice_path.write_text(json.dumps(self.voice_config), encoding="utf-8")
        missing_approval = inspect_cast_evidence(
            root_dir=self.root,
            roster_status=self.roster_status,
            approved_roster_path=self.roster_path,
            script_path=self.script_path,
            voice_config_path=self.voice_path,
        )
        self.assertIn("character_doctor", missing_approval["invalid_voice_ids"])

        self.voice_config["THE DOCTOR"]["community_pack_approval_fingerprint"] = "b" * 64
        pack.write_bytes(b"tampered-community-pack")
        self.voice_path.write_text(json.dumps(self.voice_config), encoding="utf-8")
        tampered = inspect_cast_evidence(
            root_dir=self.root,
            roster_status=self.roster_status,
            approved_roster_path=self.roster_path,
            script_path=self.script_path,
            voice_config_path=self.voice_path,
        )
        self.assertIn("character_doctor", tampered["invalid_voice_ids"])

    def test_controlled_clone_requires_server_saved_configuration_fingerprint(self) -> None:
        audio = self.root / "clone_voices" / "doctor.wav"
        audio.parent.mkdir()
        audio.write_bytes(b"reference-audio")
        self.voice_config["THE DOCTOR"] = {
            "type": "clone",
            "ref_audio": "clone_voices/doctor.wav",
            "ref_text": "Exact transcript.",
            "clone_backend": "qwen3_instruction_controlled",
        }
        self.voice_path.write_text(json.dumps(self.voice_config), encoding="utf-8")
        evidence = inspect_cast_evidence(
            root_dir=self.root,
            roster_status=self.roster_status,
            approved_roster_path=self.roster_path,
            script_path=self.script_path,
            voice_config_path=self.voice_path,
        )
        self.assertIn(
            "character_doctor",
            evidence["controlled_clone_approval_missing_ids"],
        )

        self.voice_config["THE DOCTOR"][
            "controlled_clone_configuration_fingerprint"
        ] = "approved-fingerprint"
        self.voice_path.write_text(json.dumps(self.voice_config), encoding="utf-8")
        evidence = inspect_cast_evidence(
            root_dir=self.root,
            roster_status=self.roster_status,
            approved_roster_path=self.roster_path,
            script_path=self.script_path,
            voice_config_path=self.voice_path,
        )
        self.assertEqual(evidence["controlled_clone_approval_missing_ids"], [])

    def test_legacy_voxcpm2_clone_remains_blocked_even_with_old_receipt(self) -> None:
        audio = self.root / "clone_voices" / "doctor.wav"
        audio.parent.mkdir(exist_ok=True)
        audio.write_bytes(b"reference-audio")
        self.voice_config["THE DOCTOR"] = {
            "type": "clone",
            "ref_audio": "clone_voices/doctor.wav",
            "ref_text": "Exact transcript.",
            "clone_backend": "voxcpm2_controlled",
            "controlled_clone_configuration_fingerprint": "legacy-receipt",
        }
        self.voice_path.write_text(json.dumps(self.voice_config), encoding="utf-8")
        evidence = inspect_cast_evidence(
            root_dir=self.root,
            roster_status=self.roster_status,
            approved_roster_path=self.roster_path,
            script_path=self.script_path,
            voice_config_path=self.voice_path,
        )
        self.assertIn(
            "character_doctor",
            evidence["controlled_clone_approval_missing_ids"],
        )

    def test_experimental_unreviewed_adapter_is_not_a_valid_production_voice(self) -> None:
        adapter = self.root / "lora_models" / "doctor" / "mlx_model"
        adapter.mkdir(parents=True)
        (adapter / "mlx_export_manifest.json").write_text(
            json.dumps(
                {
                    "production_assignment_supported": False,
                    "validation": {"manual_audio_review_status": "pending"},
                }
            ),
            encoding="utf-8",
        )
        self.voice_config["THE DOCTOR"] = {
            "type": "lora",
            "adapter_id": "doctor",
            "adapter_path": "lora_models/doctor",
        }
        self.voice_path.write_text(json.dumps(self.voice_config), encoding="utf-8")
        evidence = inspect_cast_evidence(
            root_dir=self.root,
            roster_status=self.roster_status,
            approved_roster_path=self.roster_path,
            script_path=self.script_path,
            voice_config_path=self.voice_path,
        )
        self.assertIn("character_doctor", evidence["invalid_adapter_ids"])

    def test_produce_inspection_checks_binding_file_and_recorded_hash(self) -> None:
        audio = self.root / "voicelines" / "line.wav"
        audio.parent.mkdir()
        audio.write_bytes(b"current-audio")
        chunk = {
            "id": "line-1",
            "speaker": "THE DOCTOR",
            "text": "Hello,",
            "instruct": "A measured greeting.",
            "status": "done",
            "audio_state": "current",
            "audio_path": "voicelines/line.wav",
        }
        expected = audio_binding_fingerprint(
            chunk=chunk,
            resolved_speaker="THE DOCTOR",
            voice_config=self.voice_config,
            synthesis_config={"language": "English"},
        )
        chunk["audio_fingerprint"] = expected
        chunk["audio_sha256"] = hashlib.sha256(audio.read_bytes()).hexdigest()
        chunks_path = self.root / "chunks.json"
        chunks_path.write_text(json.dumps([chunk]), encoding="utf-8")

        evidence = inspect_produce_evidence(
            root_dir=self.root,
            chunks_path=chunks_path,
            voice_config_path=self.voice_path,
            config_path=self.config_path,
        )
        self.assertEqual(evidence["required_chunks"], 1)
        self.assertEqual(evidence["current_chunks"], 1)
        self.assertEqual(evidence["hash_invalid_chunk_ids"], [])

        audio.write_bytes(b"changed-audio")
        evidence = inspect_produce_evidence(
            root_dir=self.root,
            chunks_path=chunks_path,
            voice_config_path=self.voice_path,
            config_path=self.config_path,
        )
        self.assertEqual(evidence["current_chunks"], 0)
        self.assertEqual(evidence["hash_invalid_chunk_ids"], ["chunk:line-1"])

    def test_migration_actions_hide_raw_paths_in_public_summary(self) -> None:
        evidence = inspect_compatibility_evidence(
            {
                "migration_required": True,
                "migration_blocked": False,
                "plan_fingerprint": "plan-fingerprint",
                "actions": [
                    {
                        "action": "add_empty_llm_profiles",
                        "path": "/private/project/app/config.json",
                        "description": "Add the missing profiles object.",
                        "destructive": False,
                    }
                ],
            }
        )
        action = evidence["native_actions"][0]
        self.assertNotIn("path", action)
        self.assertEqual(action["native_destination"], "maintenance")
        self.assertEqual(
            action["target_id"],
            "maintenance:migration:add_empty_llm_profiles",
        )
        self.assertTrue(action["technical_detail_available"])

    def test_produce_aggregate_adapter_preserves_stricter_native_blockers(self) -> None:
        native = {
            "process": {"running": False},
            "resumable": False,
            "required_chunks": 2,
            "current_chunks": 1,
            "missing_chunk_ids": [],
            "stale_chunk_ids": ["chunk:1"],
            "failed_chunk_ids": [],
            "hash_invalid_chunk_ids": [],
            "review_chunk_ids": [],
            "listening_chunk_ids": [],
            "fingerprints": {"chunks": "native-chunks"},
            "collector_error": None,
        }
        aggregate = {
            "schema_version": 1,
            "summary": {
                "required_chunk_count": 2,
                "current_count": 1,
            },
            "chunks": [
                {"chunk_id": "chunk:0", "state": "current", "reason": None},
                {"chunk_id": "chunk:1", "state": "current", "reason": None},
            ],
            "process": {
                "running": False,
                "cancelled_count": 1,
                "mode": "missing_stale",
            },
            "fingerprints": {
                "chunks": "aggregate-chunks",
                "aggregate": "aggregate",
            },
        }
        evidence = produce_aggregate_to_flow_evidence(
            aggregate,
            native_evidence=native,
        )
        self.assertEqual(evidence["required_chunks"], 2)
        self.assertEqual(evidence["current_chunks"], 1)
        self.assertEqual(evidence["stale_chunk_ids"], ["chunk:1"])
        self.assertTrue(evidence["resumable"])
        self.assertEqual(
            evidence["fingerprints"]["chunks"],
            "aggregate-chunks",
        )

    def test_export_aggregate_adapter_replaces_legacy_metadata_but_preserves_native_invalidity(self) -> None:
        native = {
            "process": {"running": False},
            "failed": False,
            "failure_reason": None,
            "missing_metadata_fields": ["title", "author"],
            "invalid_chapter_ids": [],
            "unavailable_formats": [],
            "output_exists": True,
            "output_current": True,
            "output_valid": False,
            "fingerprints": {
                "build_dependencies": "native-dependencies",
                "output": None,
            },
            "technical": {"selected_formats": ["mp3"]},
        }
        aggregate = {
            "schema_version": 1,
            "state": "complete",
            "metadata": {"title": "Book", "author": "Author"},
            "formats": ["mp3"],
            "selected_outputs": [
                {
                    "format": "mp3",
                    "exists": True,
                    "state": "current",
                    "sha256": "a" * 64,
                    "technical_details": {
                        "relative_path": "cloned_audiobook.mp3"
                    },
                }
            ],
            "blockers": [],
            "process": {"running": False},
            "fingerprints": {
                "dependencies": "aggregate-dependencies",
                "plan": "aggregate-plan",
            },
        }
        evidence = export_aggregate_to_flow_evidence(
            aggregate,
            native_evidence=native,
        )
        self.assertEqual(evidence["missing_metadata_fields"], [])
        self.assertTrue(evidence["output_exists"])
        self.assertTrue(evidence["output_current"])
        self.assertFalse(evidence["output_valid"])
        self.assertEqual(
            evidence["fingerprints"]["build_dependencies"],
            "aggregate-dependencies",
        )

        stale = json.loads(json.dumps(aggregate))
        stale["selected_outputs"][0]["state"] = "stale"
        stale_evidence = export_aggregate_to_flow_evidence(
            stale,
            native_evidence={**native, "output_valid": True},
        )
        self.assertFalse(stale_evidence["output_current"])

    def test_project_flow_status_read_is_file_pure(self) -> None:
        state_path = self.root / "state.json"
        state_path.write_text(
            json.dumps(
                {
                    "input_file_path": str(self.source_path),
                    "project_name": "Book project",
                }
            ),
            encoding="utf-8",
        )
        metadata_path = self.root / "annotated_script.meta.json"
        metadata_path.write_text("{}", encoding="utf-8")
        chunks_path = self.root / "chunks.json"
        chunks_path.write_text("[]", encoding="utf-8")
        protected = [
            state_path,
            self.script_path,
            metadata_path,
            chunks_path,
            self.voice_path,
            self.roster_path,
        ]
        before = {path: path.read_bytes() for path in protected}
        source_status = {
            "persisted": True,
            "path": str(self.source_path),
            "basename": self.source_path.name,
            "exists": True,
            "readable": True,
            "fingerprint": "source-fingerprint",
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

        summary = inspect_project_flow(
            root_dir=self.root,
            config_path=self.config_path,
            script_path=self.script_path,
            script_metadata_path=metadata_path,
            chunks_path=chunks_path,
            voice_config_path=self.voice_path,
            roster_path=self.roster_path,
            state_path=state_path,
            audiobook_path=self.root / "cloned_audiobook.mp3",
            m4b_path=self.root / "audiobook.m4b",
            source_status=source_status,
            generation_status=generation_status,
            script_lifecycle_status={
                "state": "review_required",
                "accepted": False,
                "generation_method": "import_existing_script",
                "provenance": {
                    "method": "import_existing_script",
                    "provenance_status": "unverified",
                },
                "fingerprints": {"accepted_receipt": None},
            },
            roster_status=self.roster_status,
            migration_status={
                "migration_required": False,
                "migration_blocked": False,
            },
            generated_at_utc="2026-07-20T12:00:00Z",
        )

        self.assertEqual(summary["schema_version"], 1)
        self.assertEqual(summary["project"]["id"].split("_")[0], "project")
        self.assertEqual(summary["project"]["name"], "Book project")
        self.assertEqual(summary["recommended_stage"], "script")
        self.assertEqual(
            {path: path.read_bytes() for path in protected},
            before,
        )


if __name__ == "__main__":
    unittest.main()
