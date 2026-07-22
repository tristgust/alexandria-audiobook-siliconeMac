from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from character_roster_actions import approve_character_roster_file
from character_visuals import PROFILE_BUCKETS
from external_stage_transfers import (
    ExternalStageTransferConflictError,
    transfer_structured_result_candidate,
)
from external_workflows import (
    get_structured_result_candidate,
    store_structured_result_candidate,
)
from generation_state import fingerprint_text, fingerprint_value
from roster_discovery import (
    completed_observations,
    load_roster_discovery_state,
)
from visual_discovery import (
    completed_visual_observations,
    load_visual_discovery_state,
)
from voice_training_projects import (
    read_voice_training_project,
    voice_training_project_path,
)


class ExternalStageTransferTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = (
            'The Doctor, a short man in a battered hat, opened the door. '
            '"No. It rarely is," said the Doctor.'
        )
        self.source_path = self.root / "book.txt"
        self.source_path.write_text(self.source, encoding="utf-8")
        self.source_snapshot = {
            "path": str(self.source_path),
            "basename": self.source_path.name,
            "fingerprint": fingerprint_text(self.source),
            "character_count": len(self.source),
        }
        self.state_path = self.root / "character_roster_state.json"
        self.draft_path = self.root / "character_roster.draft.json"
        self.approved_path = self.root / "character_roster.json"
        self.projects_root = self.root / "voice_training_projects"
        self.visual_state_path = self.root / "persona_visual_state.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_json(self, name: str, value) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def discovery_result(self) -> dict:
        name_quote = "The Doctor"
        name_start = self.source.index(name_quote)
        speaking_quote = "No. It rarely is,"
        speaking_start = self.source.index(speaking_quote)
        return {
            "entities": [
                {
                    "identity_seed": "doctor-at-first-mention",
                    "canonical_name": "THE DOCTOR",
                    "display_name": "The Doctor",
                    "entity_kind": "character",
                    "speaking_status": "speaker",
                    "titles": ["Doctor"],
                    "aliases": [],
                    "nicknames": [],
                    "pronouns": [],
                    "species": [],
                    "relationships": [],
                    "voice_clues": ["Measured and dry."],
                    "sample_lines": [speaking_quote],
                    "confidence": 0.98,
                    "resolution_status": "resolved",
                    "unresolved_questions": [],
                    "evidence": [
                        {
                            "quote": name_quote,
                            "start_char": name_start,
                            "end_char": name_start + len(name_quote),
                            "category": "name",
                            "confidence": 1.0,
                            "basis": "explicit",
                        },
                        {
                            "quote": name_quote,
                            "start_char": name_start,
                            "end_char": name_start + len(name_quote),
                            "category": "title",
                            "confidence": 1.0,
                            "basis": "explicit",
                        },
                        {
                            "quote": speaking_quote,
                            "start_char": speaking_start,
                            "end_char": speaking_start + len(speaking_quote),
                            "category": "speaking",
                            "confidence": 1.0,
                            "basis": "explicit",
                        },
                    ],
                }
            ],
            "warnings": [],
        }

    def store_candidate(
        self,
        *,
        task_type: str,
        result: dict,
        artifact_fingerprints: dict[str, str] | None = None,
        input_payload: dict | None = None,
        suffix: str = "one",
    ) -> dict:
        return store_structured_result_candidate(
            root_dir=self.root,
            validated={
                "handoff_id": f"handoff_{suffix:0<24}"[:32],
                "task_type": task_type,
                "result_fingerprint": fingerprint_value(result),
                "review": {
                    "root_type": "object",
                    "item_count": len(result),
                    "source_fingerprint_verified": True,
                    "artifact_fingerprints_verified": sorted(
                        artifact_fingerprints or {}
                    ),
                },
                "result": result,
            },
            handoff={
                "manifest": {
                    "source_fingerprint": self.source_snapshot[
                        "fingerprint"
                    ],
                    "artifact_fingerprints": artifact_fingerprints or {},
                },
                "input": input_payload or {},
            },
            created_at_utc=f"2026-07-19T09:0{len(suffix)}:00Z",
        )

    def transfer(self, candidate: dict, **overrides) -> dict:
        arguments = {
            "root_dir": self.root,
            "candidate_id": candidate["candidate_id"],
            "expected_result_fingerprint": candidate[
                "result_fingerprint"
            ],
            "source_snapshot": self.source_snapshot,
            "source_text": self.source,
            "roster_state_path": self.state_path,
            "roster_draft_path": self.draft_path,
            "approved_roster_path": self.approved_path,
            "voice_training_projects_root": self.projects_root,
            "visual_state_path": self.visual_state_path,
            "replace_persona_draft": False,
            "at_utc": "2026-07-19T09:30:00Z",
        }
        arguments.update(overrides)
        return transfer_structured_result_candidate(**arguments)

    def prepare_approved_roster(self) -> dict:
        discovery = self.store_candidate(
            task_type="roster_discovery",
            result=self.discovery_result(),
            suffix="approved-discovery",
        )
        self.transfer(discovery)
        state = load_roster_discovery_state(self.state_path)
        assert state is not None
        observation_id = completed_observations(state)[0]["observation_id"]
        reconciliation = {
            "entries": [
                {
                    "identity_seed": "doctor",
                    "canonical_name": "THE DOCTOR",
                    "display_name": "The Doctor",
                    "entity_kind": "character",
                    "speaking_status": "speaker",
                    "observation_ids": [observation_id],
                    "confidence": 0.98,
                    "resolution_status": "resolved",
                    "possible_duplicate_seeds": [],
                    "mistaken_merge_risk": False,
                    "unresolved_questions": [],
                }
            ],
            "duplicate_candidates": [],
            "excluded_observation_ids": [],
            "warnings": [],
        }
        state_fingerprint = fingerprint_value(
            json.loads(self.state_path.read_text(encoding="utf-8"))
        )
        candidate = self.store_candidate(
            task_type="roster_reconciliation",
            result=reconciliation,
            artifact_fingerprints={
                "roster_discovery_state": state_fingerprint,
            },
            suffix="approved-reconciliation",
        )
        self.transfer(candidate)
        draft = json.loads(self.draft_path.read_text(encoding="utf-8"))
        return approve_character_roster_file(
            draft_path=self.draft_path,
            approved_path=self.approved_path,
            source_text=self.source,
            source_fingerprint=self.source_snapshot["fingerprint"],
            expected_fingerprint=draft["draft_fingerprint"],
            acknowledged_unresolved=False,
            approved_at_utc="2026-07-19T09:45:00Z",
        )

    def test_roster_discovery_enters_native_state_without_approval(self) -> None:
        candidate = self.store_candidate(
            task_type="roster_discovery",
            result=self.discovery_result(),
        )

        transferred = self.transfer(candidate)

        self.assertEqual(transferred["status"], "transferred")
        self.assertEqual(
            transferred["application"]["destination"],
            "character_roster",
        )
        self.assertEqual(
            transferred["application"]["observation_count"],
            1,
        )
        state = load_roster_discovery_state(self.state_path)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(len(completed_observations(state)), 1)
        self.assertIsNone(state["reconciliation"])
        self.assertFalse(self.draft_path.exists())
        self.assertFalse(self.approved_path.exists())
        self.assertEqual(
            get_structured_result_candidate(
                root_dir=self.root,
                candidate_id=candidate["candidate_id"],
            )["status"],
            "transferred",
        )
        with self.assertRaisesRegex(
            ExternalStageTransferConflictError,
            "already entered",
        ):
            self.transfer(candidate)

    def test_roster_reconciliation_creates_reviewable_draft_only(self) -> None:
        discovery = self.store_candidate(
            task_type="roster_discovery",
            result=self.discovery_result(),
        )
        self.transfer(discovery)
        state = load_roster_discovery_state(self.state_path)
        assert state is not None
        observation_id = completed_observations(state)[0]["observation_id"]
        reconciliation = {
            "entries": [
                {
                    "identity_seed": "doctor",
                    "canonical_name": "THE DOCTOR",
                    "display_name": "The Doctor",
                    "entity_kind": "character",
                    "speaking_status": "speaker",
                    "observation_ids": [observation_id],
                    "confidence": 0.98,
                    "resolution_status": "resolved",
                    "possible_duplicate_seeds": [],
                    "mistaken_merge_risk": False,
                    "unresolved_questions": [],
                }
            ],
            "duplicate_candidates": [],
            "excluded_observation_ids": [],
            "warnings": [],
        }
        state_fingerprint = fingerprint_value(
            json.loads(self.state_path.read_text(encoding="utf-8"))
        )
        candidate = self.store_candidate(
            task_type="roster_reconciliation",
            result=reconciliation,
            artifact_fingerprints={
                "roster_discovery_state": state_fingerprint,
            },
            suffix="reconciliation",
        )

        transferred = self.transfer(candidate)

        self.assertEqual(transferred["status"], "transferred")
        self.assertTrue(self.draft_path.exists())
        draft = json.loads(self.draft_path.read_text(encoding="utf-8"))
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["entries"][0]["canonical_name"], "THE DOCTOR")
        self.assertFalse(self.approved_path.exists())
        updated = load_roster_discovery_state(self.state_path)
        assert updated is not None
        self.assertIsNotNone(updated["reconciliation"])

    def test_persona_enters_expressive_voice_as_unapproved_draft(self) -> None:
        self.prepare_approved_roster()
        script = [
            {
                "speaker": "NARRATOR",
                "text": "The Doctor opened the door.",
                "instruct": "Neutral narration.",
            },
            {
                "speaker": "THE DOCTOR",
                "text": "No. It rarely is,",
                "instruct": "Dry and measured.",
            },
        ]
        script_path = self.write_json("annotated_script.json", script)
        script_fingerprint = fingerprint_value(script)
        result = {
            "description": "A measured, dry voice with clipped phrasing.",
            "ref_text": "No. It rarely is,",
        }
        candidate = self.store_candidate(
            task_type="persona_generation",
            result=result,
            artifact_fingerprints={
                "annotated_script": script_fingerprint,
            },
            input_payload={"speaker": "THE DOCTOR"},
            suffix="persona",
        )

        transferred = self.transfer(candidate)

        self.assertEqual(
            transferred["application"]["destination"],
            "expressive_voices",
        )
        character_id = transferred["application"]["character_id"]
        project_path = voice_training_project_path(
            self.projects_root,
            character_id,
        )
        project = read_voice_training_project(project_path)
        persona = project["desired_base_persona"]
        self.assertEqual(persona["description"], result["description"])
        self.assertEqual(persona["ref_text"], result["ref_text"])
        self.assertEqual(persona["approval_status"], "draft")
        self.assertIsNone(persona["approved_fingerprint"])
        self.assertTrue(script_path.exists())

        replacement_result = {
            "description": "A sharper draft with harder pauses.",
            "ref_text": "No. It rarely is,",
        }
        replacement = self.store_candidate(
            task_type="persona_generation",
            result=replacement_result,
            artifact_fingerprints={
                "annotated_script": script_fingerprint,
            },
            input_payload={"speaker": "THE DOCTOR"},
            suffix="replacement",
        )
        with self.assertRaisesRegex(
            ExternalStageTransferConflictError,
            "Confirm replacement",
        ):
            self.transfer(replacement)

        replaced = self.transfer(
            replacement,
            replace_persona_draft=True,
        )
        project = read_voice_training_project(
            voice_training_project_path(
                self.projects_root,
                replaced["application"]["character_id"],
            )
        )
        self.assertEqual(
            project["desired_base_persona"]["description"],
            replacement_result["description"],
        )
        self.assertEqual(
            project["desired_base_persona"]["approval_status"],
            "draft",
        )

    def test_persistent_voice_description_enters_persona_draft_review(self) -> None:
        approved = self.prepare_approved_roster()
        script = [
            {
                "speaker": "NARRATOR",
                "text": "The Doctor opened the door.",
                "instruct": "Neutral narration.",
            },
            {
                "speaker": "THE DOCTOR",
                "text": "No. It rarely is,",
                "instruct": "Dry and measured.",
            },
        ]
        self.write_json("annotated_script.json", script)
        result = {
            "description": "Tenor, dry and lightly nasal with compact resonance.",
            "ref_text": "No. It rarely is,",
        }
        candidate = self.store_candidate(
            task_type="persistent_voice_description_audit",
            result=result,
            artifact_fingerprints={
                "annotated_script": fingerprint_value(script),
                "character_roster": fingerprint_value(approved),
            },
            input_payload={"speaker": "THE DOCTOR"},
            suffix="voice-description",
        )

        transferred = self.transfer(candidate)

        self.assertEqual(transferred["status"], "transferred")
        self.assertEqual(
            transferred["application"]["destination"],
            "expressive_voices",
        )
        self.assertEqual(
            transferred["application"]["stage"],
            "persistent_voice_description_audit",
        )
        project = read_voice_training_project(
            voice_training_project_path(
                self.projects_root,
                transferred["application"]["character_id"],
            )
        )
        persona = project["desired_base_persona"]
        self.assertEqual(persona["description"], result["description"])
        self.assertEqual(persona["ref_text"], result["ref_text"])
        self.assertEqual(persona["approval_status"], "draft")
        self.assertIsNone(persona["approved_fingerprint"])

    def test_visual_discovery_and_reconciliation_enter_native_review_state(self) -> None:
        approved = self.prepare_approved_roster()
        entry = approved["entries"][0]
        roster_fingerprint = fingerprint_value(approved)
        detail_quote = "short man"
        detail_start = self.source.index(detail_quote)
        discovery_result = {
            "observations": [
                {
                    "character_id": entry["id"],
                    "category": "height_and_build",
                    "detail": "a short man",
                    "scope": "stable",
                    "certainty": 0.95,
                    "basis": "explicit",
                    "start_char": detail_start,
                    "end_char": detail_start + len(detail_quote),
                    "quote": detail_quote,
                }
            ],
            "warnings": [],
        }
        discovery = self.store_candidate(
            task_type="visual_discovery",
            result=discovery_result,
            artifact_fingerprints={
                "character_roster": roster_fingerprint,
            },
            input_payload={"roster_entry": {"id": entry["id"]}},
            suffix="visual-discovery",
        )

        transferred = self.transfer(discovery)

        self.assertEqual(transferred["status"], "transferred")
        self.assertEqual(
            transferred["application"]["destination"],
            "visual_dossiers",
        )
        self.assertEqual(
            transferred["application"]["stage"],
            "visual_discovery",
        )
        state = load_visual_discovery_state(self.visual_state_path)
        self.assertIsNotNone(state)
        assert state is not None
        observations = completed_visual_observations(state)
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["character_id"], entry["id"])
        self.assertIsNone(state["reconciliation"])

        profile = {bucket: [] for bucket in PROFILE_BUCKETS}
        profile["height_and_build"] = [
            {
                "detail": "a short man",
                "certainty": 0.95,
                "observation_ids": [observations[0]["observation_id"]],
            }
        ]
        reconciliation_result = {
            "characters": [
                {
                    "character_id": entry["id"],
                    "profile": profile,
                    "variants": [],
                    "conflicts": [],
                    "unknowns": [],
                }
            ],
            "warnings": [],
        }
        visual_state_fingerprint = fingerprint_value(
            json.loads(self.visual_state_path.read_text(encoding="utf-8"))
        )
        reconciliation = self.store_candidate(
            task_type="visual_reconciliation",
            result=reconciliation_result,
            artifact_fingerprints={
                "character_roster": roster_fingerprint,
                "visual_discovery_state": visual_state_fingerprint,
            },
            suffix="visual-reconciliation",
        )

        reconciled = self.transfer(reconciliation)

        self.assertEqual(reconciled["status"], "transferred")
        self.assertEqual(
            reconciled["application"]["stage"],
            "visual_reconciliation",
        )
        updated = load_visual_discovery_state(self.visual_state_path)
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertIsNotNone(updated["reconciliation"])
        self.assertNotIn("approved_at_utc", updated)
        self.assertNotIn("production_assignment", updated)

    def test_stale_artifact_blocks_transfer_without_native_writes(self) -> None:
        script = [
            {
                "speaker": "THE DOCTOR",
                "text": "No. It rarely is,",
                "instruct": "Dry and measured.",
            }
        ]
        self.write_json("annotated_script.json", script)
        candidate = self.store_candidate(
            task_type="persona_generation",
            result={
                "description": "Measured and dry.",
                "ref_text": "No. It rarely is,",
            },
            artifact_fingerprints={
                "annotated_script": fingerprint_value(script),
            },
            input_payload={"speaker": "THE DOCTOR"},
            suffix="stale",
        )
        self.write_json(
            "annotated_script.json",
            [
                {
                    "speaker": "THE DOCTOR",
                    "text": "Changed after export.",
                    "instruct": "Different.",
                }
            ],
        )

        with self.assertRaisesRegex(
            ExternalStageTransferConflictError,
            "changed after this handoff",
        ):
            self.transfer(candidate)

        self.assertFalse(self.projects_root.exists())
        self.assertEqual(
            get_structured_result_candidate(
                root_dir=self.root,
                candidate_id=candidate["candidate_id"],
            )["status"],
            "inspected",
        )


if __name__ == "__main__":
    unittest.main()
