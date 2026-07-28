from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from character_roster import (
    build_draft_roster,
    build_source_snapshot,
    compute_draft_fingerprint,
    compute_roster_fingerprint,
    save_character_roster,
    stable_entry_id,
    validate_character_roster,
)
from character_roster_actions import build_approved_roster
from generation_state import atomic_json_write, fingerprint_value
from project import group_into_chunks
from speaker_management import (
    SpeakerManagementConflictError,
    SpeakerManagementValidationError,
    apply_speaker_operation,
    inspect_speaker_lines,
    load_speaker_operation,
    undo_speaker_operation,
)
from voice_training_projects import (
    build_voice_training_project,
    read_voice_training_project,
    save_voice_training_project,
    voice_training_project_path,
)


class SpeakerManagementFixture:
    SOURCE_TEXT = (
        "The Doctor greeted Roz. Roz smiled. "
        "The Doctor asked a question. Roz answered. "
        "The TARDIS stood behind them."
    )
    TIME = "2026-07-16T22:00:00Z"

    @classmethod
    def evidence(cls, quote: str, occurrence: int = 0) -> dict:
        start = -1
        cursor = 0
        for _ in range(occurrence + 1):
            start = cls.SOURCE_TEXT.index(quote, cursor)
            cursor = start + 1
        return {
            "source_quote": quote,
            "source_location": f"characters {start}-{start + len(quote)}",
            "start_char": start,
            "end_char": start + len(quote),
            "passage_index": 0,
            "entry_index": None,
            "batch_index": 0,
            "category": "name",
            "confidence": 1.0,
            "basis": "explicit",
        }

    @classmethod
    def entry(
        cls,
        name: str,
        quote: str,
        *,
        occurrence: int = 0,
        entity_kind: str = "character",
        speaking_status: str = "speaker",
        extra_evidence: list[dict] | None = None,
    ) -> dict:
        evidence = [cls.evidence(quote, occurrence)]
        evidence.extend(copy.deepcopy(extra_evidence or []))
        return {
            "id": stable_entry_id(
                f"speaker-management:{evidence[0]['start_char']}:{name}"
            ),
            "canonical_name": name,
            "display_name": name,
            "entity_kind": entity_kind,
            "speaking_status": speaking_status,
            "titles": [],
            "aliases": [],
            "nicknames": [],
            "pronouns": [],
            "species": [],
            "relationships": [],
            "first_evidence_location": evidence[0]["source_location"],
            "additional_evidence_locations": [
                item["source_location"] for item in evidence[1:]
            ],
            "confidence": 0.95,
            "resolution_status": "resolved",
            "possible_duplicate_ids": [],
            "mistaken_merge_risk": False,
            "unresolved_questions": [],
            "evidence": evidence,
            "voice_clues": [],
            "sample_lines": [],
        }

    @classmethod
    def approved_roster(cls, source_path: Path) -> dict:
        source, text = build_source_snapshot(source_path)
        assert text == cls.SOURCE_TEXT
        doctor = cls.entry(
            "THE DOCTOR",
            "The Doctor",
            extra_evidence=[cls.evidence("The Doctor", 1)],
        )
        roz = cls.entry("ROZ", "Roz")
        tardis = cls.entry(
            "THE TARDIS",
            "The TARDIS",
            entity_kind="named_non_speaker",
            speaking_status="non_speaker",
        )
        draft = build_draft_roster(
            source=source,
            discovery={
                "created_at_utc": "2026-07-16T21:00:00Z",
                "model_name": "qwen3.5:35b-mlx",
                "backend": "ollama-native",
                "generation_fingerprint": "speaker-management-fixture",
                "batch_count": 1,
                "completed_batches": 1,
            },
            entries=[doctor, roz, tardis],
            source_text=cls.SOURCE_TEXT,
        )
        return build_approved_roster(
            draft,
            expected_fingerprint=draft["draft_fingerprint"],
            source_fingerprint=source["fingerprint"],
            source_text=cls.SOURCE_TEXT,
            acknowledged_unresolved=False,
            approved_at_utc="2026-07-16T21:05:00Z",
        )


class SpeakerManagementTests(
    unittest.TestCase,
    SpeakerManagementFixture,
):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_path = self.root / "book.txt"
        self.source_path.write_text(self.SOURCE_TEXT, encoding="utf-8")
        (self.root / "state.json").write_text(
            json.dumps({"input_file_path": str(self.source_path)}),
            encoding="utf-8",
        )
        self.roster = self.approved_roster(self.source_path)
        save_character_roster(
            self.roster,
            self.root / "character_roster.json",
            source_text=self.SOURCE_TEXT,
            expected_status="approved",
        )
        self.doctor = next(
            item for item in self.roster["entries"]
            if item["canonical_name"] == "THE DOCTOR"
        )
        self.roz = next(
            item for item in self.roster["entries"]
            if item["canonical_name"] == "ROZ"
        )
        self.script = [
            {
                "speaker": "THE DOCTOR",
                "text": "Hello, Roz.",
                "instruct": "Warm greeting.",
            },
            {
                "speaker": "ROZ",
                "text": "Hello.",
                "instruct": "Dry reply.",
            },
            {
                "speaker": "NARRATOR",
                "text": "The Doctor asked a question.",
                "instruct": "Neutral narration.",
            },
            {
                "speaker": "THE DOCTOR",
                "text": "What happened?",
                "instruct": "Alert curiosity.",
            },
            {
                "speaker": "ROZ",
                "text": "Nothing useful.",
                "instruct": "Clipped frustration.",
            },
        ]
        atomic_json_write(self.script, self.root / "annotated_script.json")
        chunks = group_into_chunks(self.script)
        for index, chunk in enumerate(chunks):
            chunk.update(
                {
                    "id": index,
                    "status": "done",
                    "audio_path": f"voicelines/chunk-{index}.wav",
                }
            )
        atomic_json_write(chunks, self.root / "chunks.json")
        self.metadata = {
            "schema_version": 1,
            "source": {
                "fingerprint": self.roster["source"]["fingerprint"]
            },
            "result": {
                "script_fingerprint": fingerprint_value(self.script),
                "entry_count": len(self.script),
                "speaker_labels": ["NARRATOR", "ROZ", "THE DOCTOR"],
            },
            "unknown": {"preserve": True},
        }
        atomic_json_write(
            self.metadata,
            self.root / "annotated_script.meta.json",
        )
        self.voice_config = {
            "THE DOCTOR": {
                "type": "clone",
                "ref_audio": "clone_voices/doctor.wav",
                "ref_text": "What happened?",
            },
            "ROZ": {
                "type": "design",
                "description": "Low, dry, controlled.",
                "ref_text": "Nothing useful.",
            },
        }
        atomic_json_write(
            self.voice_config,
            self.root / "voice_config.json",
        )
        persona_dir = self.root / "persona_refs"
        persona_dir.mkdir()
        atomic_json_write(
            {
                "name": "THE DOCTOR",
                "aliases": ["Doctor"],
                "features": [],
                "personality": [],
                "voice_clues": [],
                "relationships": [],
                "sample_lines": ["What happened?"],
                "observations": [],
                "roster_entry_id": self.doctor["id"],
                "visual_roster_fingerprint": self.roster[
                    "roster_fingerprint"
                ],
            },
            persona_dir / "doctor.json",
        )
        atomic_json_write(
            {
                "name": "ROZ",
                "aliases": [],
                "features": [],
                "personality": ["Dry"],
                "voice_clues": [],
                "relationships": [],
                "sample_lines": ["Nothing useful."],
                "observations": [],
                "roster_entry_id": self.roz["id"],
                "visual_roster_fingerprint": self.roster[
                    "roster_fingerprint"
                ],
            },
            persona_dir / "roz.json",
        )
        self.projects_root = self.root / "voice_training_projects"
        doctor_project = build_voice_training_project(
            approved_roster=self.roster,
            character_id=self.doctor["id"],
            priority="primary",
            desired_description="Draft Doctor voice.",
            desired_ref_text="What happened?",
            created_at_utc=self.TIME,
        )
        save_voice_training_project(
            doctor_project,
            voice_training_project_path(
                self.projects_root,
                self.doctor["id"],
            ),
        )
        atomic_json_write(
            {"schema_version": 1, "checkpoint": "preserve"},
            self.root / "generation_state.json",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def use_long_roster_name_with_short_script_label(self) -> None:
        roster = copy.deepcopy(self.roster)
        doctor = next(
            entry for entry in roster["entries"]
            if entry["id"] == self.doctor["id"]
        )
        doctor["canonical_name"] = "Bernice Summerfield"
        doctor["display_name"] = "Bernice Summerfield"
        doctor["aliases"] = []
        doctor["nicknames"] = []
        doctor["titles"] = []
        doctor["sample_lines"] = ["Hello, Roz."]
        roster["approved_draft_fingerprint"] = compute_draft_fingerprint(
            roster
        )
        roster["roster_fingerprint"] = compute_roster_fingerprint(roster)
        save_character_roster(
            roster,
            self.root / "character_roster.json",
            source_text=self.SOURCE_TEXT,
            expected_status="approved",
        )
        self.roster = roster
        self.doctor = doctor
        self.script[0]["speaker"] = "BERNICE"
        atomic_json_write(self.script, self.root / "annotated_script.json")
        chunks = group_into_chunks(self.script)
        for index, chunk in enumerate(chunks):
            chunk.update(
                {
                    "id": index,
                    "status": "done",
                    "audio_path": f"voicelines/chunk-{index}.wav",
                }
            )
        atomic_json_write(chunks, self.root / "chunks.json")
        self.voice_config.pop("THE DOCTOR", None)
        self.voice_config["BERNICE"] = {
            "type": "clone",
            "ref_audio": "clone_voices/benny.wav",
            "ref_text": "Hello, Roz.",
        }
        atomic_json_write(
            self.voice_config,
            self.root / "voice_config.json",
        )

    def fingerprint(self) -> str:
        return inspect_speaker_lines(root_dir=self.root)[
            "script_fingerprint"
        ]

    def read(self, name: str):
        return json.loads((self.root / name).read_text(encoding="utf-8"))

    def snapshot_bytes(self) -> dict[str, bytes]:
        paths = [
            "annotated_script.json",
            "annotated_script.meta.json",
            "chunks.json",
            "voice_config.json",
            "character_roster.json",
            "generation_state.json",
            "persona_refs/doctor.json",
            "persona_refs/roz.json",
            (
                "voice_training_projects/"
                + self.doctor["id"]
                + "/project.json"
            ),
        ]
        return {
            path: (self.root / path).read_bytes()
            for path in paths
            if (self.root / path).exists()
        }

    def write_chunk_audio(self, speaker: str | None = None) -> dict[str, bytes]:
        written = {}
        for chunk in self.read("chunks.json"):
            if speaker is not None and chunk.get("speaker") != speaker:
                continue
            relative = chunk.get("audio_path")
            if not relative:
                continue
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = f"audio:{chunk['id']}:{chunk['speaker']}".encode("utf-8")
            path.write_bytes(payload)
            written[relative] = payload
        return written

    def apply(self, operation: str, payload: dict) -> dict:
        return apply_speaker_operation(
            root_dir=self.root,
            operation=operation,
            expected_script_fingerprint=self.fingerprint(),
            payload=payload,
            at_utc=self.TIME,
        )

    def test_line_inspection_reports_fingerprint_counts_and_lines(self) -> None:
        result = inspect_speaker_lines(
            root_dir=self.root,
            speaker="THE DOCTOR",
        )
        self.assertEqual(result["entry_count"], 5)
        self.assertEqual(result["speaker_counts"]["THE DOCTOR"], 2)
        self.assertEqual([line["index"] for line in result["lines"]], [0, 3])
        self.assertEqual(result["script_fingerprint"], fingerprint_value(self.script))

    def test_add_script_only_speaker_uses_exact_script_entry_evidence(self) -> None:
        script = self.read("annotated_script.json")
        script.append({
            "speaker": "SUPPLEMENTAL SPEAKER",
            "text": "I think the matter is perfectly clear.",
            "instruct": "Hushed and precise.",
        })
        atomic_json_write(script, self.root / "annotated_script.json")
        atomic_json_write(group_into_chunks(script), self.root / "chunks.json")
        metadata = self.read("annotated_script.meta.json")
        metadata["result"].update({
            "script_fingerprint": fingerprint_value(script),
            "entry_count": len(script),
            "speaker_labels": sorted({item["speaker"] for item in script}),
        })
        atomic_json_write(metadata, self.root / "annotated_script.meta.json")

        self.apply("add", {
            "script_speaker": "SUPPLEMENTAL SPEAKER",
            "display_name": "Supplemental Speaker",
            "expected_roster_fingerprint": self.read(
                "character_roster.json"
            )["roster_fingerprint"],
            "pronouns": ["she/her"],
            "species": ["human"],
            "relationships": ["Member of a supplemental scene."],
            "voice_clues": ["Hushed and precise."],
            "designed_voice_description": (
                "Adult voice with crisp diction, restrained volume, "
                "and controlled social precision."
            ),
        })

        roster = self.read("character_roster.json")
        added = next(
            item for item in roster["entries"]
            if item["canonical_name"] == "SUPPLEMENTAL SPEAKER"
        )
        self.assertEqual(added["display_name"], "Supplemental Speaker")
        self.assertEqual(added["sample_lines"], [script[-1]["text"]])
        self.assertEqual(added["evidence"][0]["entry_index"], 5)
        self.assertIsNone(added["evidence"][0]["passage_index"])
        self.assertEqual(added["evidence"][0]["start_char"], 0)
        self.assertEqual(
            added["evidence"][0]["end_char"], len(script[-1]["text"])
        )
        validate_character_roster(
            roster,
            source_text=self.SOURCE_TEXT,
            expected_status="approved",
        )
        config = self.read("voice_config.json")["SUPPLEMENTAL SPEAKER"]
        self.assertEqual(config["type"], "design")
        self.assertEqual(config["ref_text"], script[-1]["text"])

        with self.assertRaisesRegex(
            SpeakerManagementConflictError,
            "already has a roster identity",
        ):
            self.apply("add", {
                "script_speaker": "SUPPLEMENTAL SPEAKER",
                "expected_roster_fingerprint": roster[
                    "roster_fingerprint"
                ],
            })

    def test_stale_script_fingerprint_blocks_without_writes(self) -> None:
        before = self.snapshot_bytes()
        with self.assertRaisesRegex(
            SpeakerManagementConflictError,
            "script changed",
        ):
            apply_speaker_operation(
                root_dir=self.root,
                operation="rename",
                expected_script_fingerprint="stale",
                payload={
                    "entry_id": self.doctor["id"],
                    "new_name": "THE TRAVELER",
                },
                at_utc=self.TIME,
            )
        self.assertEqual(before, self.snapshot_bytes())

    def test_rename_propagates_and_preserves_prior_audio_as_stale(self) -> None:
        audio_before = self.write_chunk_audio("THE DOCTOR")
        record = self.apply(
            "rename",
            {
                "entry_id": self.doctor["id"],
                "new_name": "THE TRAVELER",
                "display_name": "The Traveler",
            },
        )
        script = self.read("annotated_script.json")
        self.assertEqual(script[0]["speaker"], "THE TRAVELER")
        self.assertEqual(script[3]["speaker"], "THE TRAVELER")
        self.assertEqual(script[0]["text"], self.script[0]["text"])
        config = self.read("voice_config.json")
        self.assertEqual(config["THE DOCTOR"], {"alias_of": "THE TRAVELER"})
        self.assertEqual(
            config["THE TRAVELER"]["ref_audio"],
            "clone_voices/doctor.wav",
        )
        roster = self.read("character_roster.json")
        renamed = next(
            item for item in roster["entries"]
            if item["id"] == self.doctor["id"]
        )
        self.assertEqual(renamed["canonical_name"], "THE TRAVELER")
        self.assertIn("THE DOCTOR", renamed["aliases"])
        persona = self.read("persona_refs/doctor.json")
        self.assertEqual(persona["name"], "THE TRAVELER")
        self.assertEqual(
            persona["visual_roster_fingerprint"],
            roster["roster_fingerprint"],
        )
        project = read_voice_training_project(
            voice_training_project_path(
                self.projects_root,
                self.doctor["id"],
            )
        )
        self.assertEqual(
            project["character"]["canonical_name"],
            "THE TRAVELER",
        )
        self.assertEqual(
            project["character"]["roster_fingerprint"],
            roster["roster_fingerprint"],
        )
        chunks = self.read("chunks.json")
        changed = [chunk for chunk in chunks if chunk["speaker"] == "THE TRAVELER"]
        self.assertTrue(changed)
        self.assertTrue(all(chunk["status"] == "pending" for chunk in changed))
        self.assertTrue(all(chunk["audio_path"] is None for chunk in changed))
        self.assertTrue(any(chunk.get("stale_audio_path") for chunk in changed))
        self.assertEqual(len(record["audio_backups"]), len(audio_before))
        for original, payload in audio_before.items():
            self.assertFalse((self.root / original).exists())
            backup_record = next(
                item
                for item in record["audio_backups"]
                if item["original_path"] == original
            )
            self.assertEqual(
                (self.root / backup_record["backup_path"]).read_bytes(),
                payload,
            )
        self.assertTrue(
            all(
                chunk.get("audio_state") == "stale"
                for chunk in changed
                if chunk.get("stale_audio_path")
            )
        )
        self.assertTrue(
            all(
                "speaker_management_history" in chunk.get("stale_audio_path", "")
                for chunk in changed
                if chunk.get("stale_audio_path")
            )
        )
        validity = self.read("audio_validity.json")
        self.assertTrue(validity["stale"])
        self.assertTrue(validity["invalidated_chunks"])
        self.assertTrue(
            all(
                item.get("backup_audio_path")
                for item in validity["invalidated_chunks"]
                if item.get("audio_path") in audio_before
            )
        )
        metadata = self.read("annotated_script.meta.json")
        self.assertTrue(metadata["unknown"]["preserve"])
        self.assertIn("THE TRAVELER", metadata["result"]["speaker_labels"])
        self.assertFalse((self.root / "generation_state.json").exists())
        archived = (
            self.root
            / "speaker_management_history"
            / record["operation_id"]
            / "invalidated_generation_state.json"
        )
        self.assertTrue(archived.exists())

    def test_rename_voice_conflict_requires_explicit_resolution(self) -> None:
        config = self.read("voice_config.json")
        config["THE TRAVELER"] = {"type": "design", "description": "Other"}
        atomic_json_write(config, self.root / "voice_config.json")
        with self.assertRaisesRegex(
            SpeakerManagementConflictError,
            "voice configurations",
        ):
            self.apply(
                "rename",
                {
                    "entry_id": self.doctor["id"],
                    "new_name": "THE TRAVELER",
                },
            )
        self.apply(
            "rename",
            {
                "entry_id": self.doctor["id"],
                "new_name": "THE TRAVELER",
                "voice_resolution": "old",
            },
        )
        self.assertEqual(
            self.read("voice_config.json")["THE TRAVELER"]["ref_audio"],
            "clone_voices/doctor.wav",
        )

    def test_rename_targets_mapped_script_and_voice_config_label(self) -> None:
        self.use_long_roster_name_with_short_script_label()
        record = apply_speaker_operation(
            root_dir=self.root,
            operation="rename",
            expected_script_fingerprint=fingerprint_value(self.script),
            payload={
                "entry_id": self.doctor["id"],
                "new_name": "PROFESSOR SUMMERFIELD",
                "display_name": "Professor Summerfield",
            },
            at_utc=self.TIME,
        )
        updated_script = json.loads(
            (self.root / "annotated_script.json").read_text(
                encoding="utf-8"
            )
        )
        updated_config = json.loads(
            (self.root / "voice_config.json").read_text(
                encoding="utf-8"
            )
        )
        updated_roster = json.loads(
            (self.root / "character_roster.json").read_text(
                encoding="utf-8"
            )
        )
        renamed = next(
            entry for entry in updated_roster["entries"]
            if entry["id"] == self.doctor["id"]
        )
        self.assertEqual(updated_script[0]["speaker"], "PROFESSOR SUMMERFIELD")
        self.assertIn("PROFESSOR SUMMERFIELD", updated_config)
        self.assertEqual(
            updated_config["BERNICE"],
            {"alias_of": "PROFESSOR SUMMERFIELD"},
        )
        self.assertIn("Bernice Summerfield", renamed["aliases"])
        self.assertIn("BERNICE", renamed["aliases"])
        self.assertEqual(record["changed_script_indices"], [0])

    def test_alias_uses_mapped_production_voice_as_target(self) -> None:
        self.use_long_roster_name_with_short_script_label()
        apply_speaker_operation(
            root_dir=self.root,
            operation="add_alias",
            expected_script_fingerprint=fingerprint_value(self.script),
            payload={
                "entry_id": self.doctor["id"],
                "alias": "BENNY",
            },
            at_utc=self.TIME,
        )
        updated_config = json.loads(
            (self.root / "voice_config.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(updated_config["BENNY"]["alias_of"], "BERNICE")

    def test_unresolved_identity_can_be_marked_and_resolved_later(self) -> None:
        self.apply(
            "mark_unresolved",
            {
                "entry_id": self.doctor["id"],
                "question": "Confirm whether this label is the same incarnation.",
            },
        )
        roster = self.read("character_roster.json")
        doctor = next(
            item for item in roster["entries"]
            if item["id"] == self.doctor["id"]
        )
        self.assertEqual(doctor["resolution_status"], "unresolved")
        self.assertEqual(
            doctor["unresolved_questions"],
            ["Confirm whether this label is the same incarnation."],
        )

        self.apply("resolve", {"entry_id": self.doctor["id"]})
        roster = self.read("character_roster.json")
        doctor = next(
            item for item in roster["entries"]
            if item["id"] == self.doctor["id"]
        )
        self.assertEqual(doctor["resolution_status"], "resolved")
        self.assertEqual(doctor["unresolved_questions"], [])
        self.assertEqual(self.read("annotated_script.json"), self.script)

    def test_exclude_requires_script_lines_to_be_reassigned_first(self) -> None:
        with self.assertRaisesRegex(
            SpeakerManagementValidationError,
            "still owns 2 Script line",
        ):
            self.apply(
                "exclude",
                {
                    "entry_id": self.doctor["id"],
                    "reason": "Not a separate Cast identity.",
                },
            )

        tardis = next(
            item for item in self.roster["entries"]
            if item["canonical_name"] == "THE TARDIS"
        )
        self.apply(
            "exclude",
            {
                "entry_id": tardis["id"],
                "reason": "Setting, not a speaking Cast identity.",
            },
        )
        roster = self.read("character_roster.json")
        self.assertNotIn(tardis["id"], {item["id"] for item in roster["entries"]})
        self.assertEqual(
            roster["excluded_entities"][-1]["reason"],
            "Setting, not a speaking Cast identity.",
        )

    def test_alias_add_and_remove_update_roster_and_voice_config(self) -> None:
        self.apply(
            "add_alias",
            {"entry_id": self.doctor["id"], "alias": "Doctor"},
        )
        roster = self.read("character_roster.json")
        doctor = next(item for item in roster["entries"] if item["id"] == self.doctor["id"])
        self.assertIn("Doctor", doctor["aliases"])
        self.assertEqual(
            self.read("voice_config.json")["Doctor"]["alias_of"],
            "THE DOCTOR",
        )
        self.apply(
            "remove_alias",
            {
                "entry_id": self.doctor["id"],
                "alias": "Doctor",
                "remove_voice_alias": True,
            },
        )
        roster = self.read("character_roster.json")
        doctor = next(item for item in roster["entries"] if item["id"] == self.doctor["id"])
        self.assertNotIn("Doctor", doctor["aliases"])
        self.assertNotIn("Doctor", self.read("voice_config.json"))

    def test_merge_requires_voice_resolution_and_preserves_evidence(self) -> None:
        with self.assertRaisesRegex(
            SpeakerManagementConflictError,
            "voice configurations",
        ):
            self.apply(
                "merge",
                {
                    "primary_entry_id": self.doctor["id"],
                    "secondary_entry_id": self.roz["id"],
                },
            )
        record = self.apply(
            "merge",
            {
                "primary_entry_id": self.doctor["id"],
                "secondary_entry_id": self.roz["id"],
                "voice_resolution": "old",
            },
        )
        roster = self.read("character_roster.json")
        self.assertEqual(len(roster["entries"]), 2)
        doctor = next(item for item in roster["entries"] if item["id"] == self.doctor["id"])
        self.assertIn("ROZ", doctor["aliases"])
        self.assertEqual(len(doctor["evidence"]), 3)
        self.assertNotIn(self.roz["id"], {item["id"] for item in roster["entries"]})
        script = self.read("annotated_script.json")
        self.assertNotIn("ROZ", {_speaker(entry) for entry in script})
        self.assertEqual(
            self.read("voice_config.json")["ROZ"],
            {"alias_of": "THE DOCTOR"},
        )
        retired_ref = self.read("persona_refs/roz.json")
        self.assertEqual(
            retired_ref["merged_into_roster_entry_id"],
            self.doctor["id"],
        )
        loaded_record = load_speaker_operation(
            root_dir=self.root,
            operation_id=record["operation_id"],
        )
        self.assertEqual(loaded_record["operation"], "merge")

    def test_merge_voice_projects_requires_explicit_resolution(self) -> None:
        roz_project = build_voice_training_project(
            approved_roster=self.roster,
            character_id=self.roz["id"],
            priority="secondary",
            created_at_utc=self.TIME,
        )
        save_voice_training_project(
            roz_project,
            voice_training_project_path(
                self.projects_root,
                self.roz["id"],
            ),
        )
        with self.assertRaisesRegex(
            SpeakerManagementConflictError,
            "voice-training projects",
        ):
            self.apply(
                "merge",
                {
                    "primary_entry_id": self.doctor["id"],
                    "secondary_entry_id": self.roz["id"],
                    "voice_resolution": "old",
                },
            )
        self.apply(
            "merge",
            {
                "primary_entry_id": self.doctor["id"],
                "secondary_entry_id": self.roz["id"],
                "voice_resolution": "old",
                "voice_project_resolution": "primary",
            },
        )
        self.assertFalse(
            voice_training_project_path(
                self.projects_root,
                self.roz["id"],
            ).exists()
        )
        retired = list(
            (self.projects_root / "_retired" / self.roz["id"]).rglob("project.json")
        )
        self.assertEqual(len(retired), 1)

    def test_split_moves_selected_lines_and_evidence_without_copying_voice_project(self) -> None:
        record = self.apply(
            "split",
            {
                "entry_id": self.doctor["id"],
                "new_name": "JOHN SMITH",
                "entry_indices": [3],
                "evidence_indexes": [1],
            },
        )
        roster = self.read("character_roster.json")
        new_entry = next(
            item for item in roster["entries"]
            if item["canonical_name"] == "JOHN SMITH"
        )
        original = next(
            item for item in roster["entries"]
            if item["id"] == self.doctor["id"]
        )
        self.assertEqual(len(new_entry["evidence"]), 1)
        self.assertEqual(len(original["evidence"]), 1)
        self.assertEqual(
            self.read("annotated_script.json")[3]["speaker"],
            "JOHN SMITH",
        )
        self.assertFalse(
            voice_training_project_path(
                self.projects_root,
                new_entry["id"],
            ).exists()
        )
        self.assertIn(3, record["changed_script_indices"])

    def test_split_requires_justified_evidence_and_original_retention(self) -> None:
        with self.assertRaisesRegex(
            SpeakerManagementValidationError,
            "leave supporting evidence",
        ):
            self.apply(
                "split",
                {
                    "entry_id": self.doctor["id"],
                    "new_name": "JOHN SMITH",
                    "entry_indices": [3],
                    "evidence_indexes": [0, 1],
                },
            )

    def test_selected_and_range_reassignment(self) -> None:
        self.apply(
            "reassign",
            {
                "entry_indices": [1],
                "target_entry_id": self.doctor["id"],
                "expected_speaker": "ROZ",
            },
        )
        self.assertEqual(
            self.read("annotated_script.json")[1]["speaker"],
            "THE DOCTOR",
        )
        self.apply(
            "reassign",
            {
                "start_index": 3,
                "end_index": 4,
                "target_entry_id": self.roz["id"],
            },
        )
        script = self.read("annotated_script.json")
        self.assertEqual(script[3]["speaker"], "ROZ")
        self.assertEqual(script[4]["speaker"], "ROZ")

    def test_expected_speaker_guard_blocks_wrong_reassignment(self) -> None:
        with self.assertRaisesRegex(
            SpeakerManagementConflictError,
            "not 'ROZ'",
        ):
            self.apply(
                "reassign",
                {
                    "entry_indices": [0],
                    "target_entry_id": self.roz["id"],
                    "expected_speaker": "ROZ",
                },
            )

    def test_undo_restores_exact_before_bytes_and_checkpoint(self) -> None:
        audio_before = self.write_chunk_audio("THE DOCTOR")
        before = self.snapshot_bytes()
        record = self.apply(
            "rename",
            {
                "entry_id": self.doctor["id"],
                "new_name": "THE TRAVELER",
            },
        )
        undo = undo_speaker_operation(
            root_dir=self.root,
            operation_id=record["operation_id"],
            at_utc="2026-07-16T22:10:00Z",
        )
        self.assertEqual(undo["operation"], "undo")
        self.assertEqual(
            sorted(undo["restored_audio_paths"]),
            sorted(audio_before),
        )
        for relative, content in audio_before.items():
            self.assertEqual((self.root / relative).read_bytes(), content)
        for path, content in before.items():
            self.assertEqual((self.root / path).read_bytes(), content, path)
        self.assertFalse((self.root / "audio_validity.json").exists())

    def test_undo_blocks_when_a_touched_file_changed_after_operation(self) -> None:
        record = self.apply(
            "rename",
            {
                "entry_id": self.doctor["id"],
                "new_name": "THE TRAVELER",
            },
        )
        script = self.read("annotated_script.json")
        script[0]["instruct"] = "Changed later."
        atomic_json_write(script, self.root / "annotated_script.json")
        with self.assertRaisesRegex(
            SpeakerManagementConflictError,
            "changed after the operation",
        ):
            undo_speaker_operation(
                root_dir=self.root,
                operation_id=record["operation_id"],
            )

    def test_undo_blocks_when_newer_audio_exists_at_original_path(self) -> None:
        audio_before = self.write_chunk_audio("THE DOCTOR")
        record = self.apply(
            "rename",
            {
                "entry_id": self.doctor["id"],
                "new_name": "THE TRAVELER",
            },
        )
        original = self.root / sorted(audio_before)[0]
        original.write_bytes(b"newer-generation")
        with self.assertRaisesRegex(
            SpeakerManagementConflictError,
            "newer audio file exists",
        ):
            undo_speaker_operation(
                root_dir=self.root,
                operation_id=record["operation_id"],
            )
        self.assertEqual(original.read_bytes(), b"newer-generation")
        self.assertEqual(
            self.read("annotated_script.json")[0]["speaker"],
            "THE TRAVELER",
        )

    def test_transaction_failure_rolls_back_every_written_file(self) -> None:
        audio_before = self.write_chunk_audio("THE DOCTOR")
        before = self.snapshot_bytes()
        real_write = atomic_json_write
        call_count = 0

        def failing_write(value, path):
            nonlocal call_count
            call_count += 1
            if call_count == 4:
                raise OSError("simulated write failure")
            return real_write(value, path)

        with patch("speaker_management.atomic_json_write", side_effect=failing_write):
            with self.assertRaisesRegex(OSError, "simulated"):
                self.apply(
                    "rename",
                    {
                        "entry_id": self.doctor["id"],
                        "new_name": "THE TRAVELER",
                    },
                )
        self.assertEqual(before, self.snapshot_bytes())
        for relative, content in audio_before.items():
            self.assertEqual((self.root / relative).read_bytes(), content)
        backups = list(
            (self.root / "speaker_management_history").glob("*/audio/*")
        )
        self.assertEqual(backups, [])


def _speaker(entry: dict) -> str:
    return entry.get("speaker") or entry.get("type") or ""


if __name__ == "__main__":
    unittest.main()
