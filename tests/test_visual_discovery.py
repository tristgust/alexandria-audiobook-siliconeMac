from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from character_roster import (
    build_source_snapshot,
    compute_roster_fingerprint,
    stable_entry_id,
)
from character_visuals import (
    PROFILE_BUCKETS,
    load_persona_reference,
    persona_reference_path,
)
from llm_schemas import validate_contract
from roster_discovery import build_discovery_passages
from visual_discovery import (
    VisualDiscoveryEvidenceError,
    VisualDiscoveryError,
    VisualReconciliationError,
    build_visual_dossiers_from_state,
    build_visual_identity,
    checkpoint_visual_passage,
    checkpoint_visual_reconciliation,
    new_visual_discovery_state,
    normalize_visual_passage_result,
    run_visual_discovery,
    validate_reconciliation_integrity,
)
from tests.visual_discovery_support import (
    DynamicVisualRuntime,
)


class VisualDiscoveryFixture:
    SOURCE_TEXT = "\n\n".join(
        (
            "The Doctor adjusted his battered hat beside the TARDIS.",
            "Roz Forrester pushed back her dark hair and watched him.",
            "Later Roz crossed the observatory in a red cloak.",
        )
    )

    @classmethod
    def roster(cls, source_fingerprint: str) -> dict:
        doctor_id = stable_entry_id(
            f"{source_fingerprint}:doctor"
        )
        roz_id = stable_entry_id(
            f"{source_fingerprint}:roz"
        )
        roster = {
            "schema_version": 1,
            "status": "approved",
            "source": {
                "path": "/tmp/book.txt",
                "basename": "book.txt",
                "fingerprint": source_fingerprint,
                "character_count": len(cls.SOURCE_TEXT),
            },
            "discovery": {
                "created_at_utc": "2026-07-16T20:00:00Z",
                "model_name": "qwen3.5:35b-mlx",
                "backend": "ollama-native",
                "generation_fingerprint": "generation",
                "batch_count": 1,
                "completed_batches": 1,
            },
            "entries": [
                {
                    "id": doctor_id,
                    "canonical_name": "THE DOCTOR",
                    "display_name": "The Doctor",
                    "entity_kind": "character",
                    "speaking_status": "speaker",
                    "titles": ["Doctor"],
                    "aliases": ["DOCTOR"],
                    "nicknames": [],
                    "pronouns": [],
                    "species": [],
                    "relationships": [],
                    "first_evidence_location": "characters 0-10",
                    "additional_evidence_locations": [],
                    "confidence": 1.0,
                    "resolution_status": "resolved",
                    "possible_duplicate_ids": [],
                    "mistaken_merge_risk": False,
                    "unresolved_questions": [],
                    "evidence": [],
                    "voice_clues": [],
                    "sample_lines": [],
                },
                {
                    "id": roz_id,
                    "canonical_name": "ROZ FORRESTER",
                    "display_name": "Roz Forrester",
                    "entity_kind": "character",
                    "speaking_status": "speaker",
                    "titles": [],
                    "aliases": ["ROZ"],
                    "nicknames": [],
                    "pronouns": [],
                    "species": [],
                    "relationships": [],
                    "first_evidence_location": "characters 60-73",
                    "additional_evidence_locations": [],
                    "confidence": 1.0,
                    "resolution_status": "resolved",
                    "possible_duplicate_ids": [],
                    "mistaken_merge_risk": False,
                    "unresolved_questions": [],
                    "evidence": [],
                    "voice_clues": [],
                    "sample_lines": [],
                },
            ],
            "unresolved": [],
            "duplicate_candidates": [],
            "excluded_entities": [],
            "warnings": [],
            "review_history": [],
            "approved_at_utc": "2026-07-16T21:00:00Z",
            "approved_draft_fingerprint": "draft",
            "approval_summary": {
                "resolved_count": 2,
                "unresolved_count": 0,
                "merged_count": 0,
                "excluded_count": 0,
                "acknowledged_unresolved": False,
            },
        }
        roster["roster_fingerprint"] = (
            compute_roster_fingerprint(roster)
        )
        return roster


class VisualDiscoveryTests(
    unittest.TestCase,
    VisualDiscoveryFixture,
):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_path = self.root / "book.txt"
        self.source_path.write_text(
            self.SOURCE_TEXT,
            encoding="utf-8",
        )
        self.source, normalized = build_source_snapshot(
            self.source_path
        )
        self.assertEqual(normalized, self.SOURCE_TEXT)
        self.roster = self.roster(
            self.source["fingerprint"]
        )
        self.state_path = (
            self.root / "character_visual_state.json"
        )
        self.refs = self.root / "persona_refs"
        self.refs.mkdir()
        self.character_ids = [
            entry["id"]
            for entry in self.roster["entries"]
        ]

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def digest(path: Path) -> str:
        if not path.exists():
            return "<absent>"
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_exact_offsets_are_repaired_only_for_unique_quote(self):
        passage = build_discovery_passages(
            self.SOURCE_TEXT,
            passage_size=400,
            overlap=40,
        )[0]
        result = validate_contract(
            "visual_discovery",
            {
                "observations": [
                    {
                        "character_id": self.character_ids[0],
                        "category": (
                            "accessories_weapons_equipment"
                        ),
                        "detail": "a battered hat",
                        "scope": "stable",
                        "certainty": 0.9,
                        "basis": "explicit",
                        "quote": "battered hat",
                        "start_char": 0,
                        "end_char": 12,
                    }
                ],
                "warnings": [],
            },
        )
        observations, warnings = (
            normalize_visual_passage_result(
                result,
                passage=passage,
                source_text=self.SOURCE_TEXT,
                allowed_character_ids=set(
                    self.character_ids
                ),
            )
        )
        observation = observations[0]
        self.assertEqual(
            self.SOURCE_TEXT[
                observation["start_char"]:
                observation["end_char"]
            ],
            "battered hat",
        )
        self.assertTrue(
            any("Repaired unique" in item for item in warnings)
        )

    def test_ambiguous_bad_offsets_are_blocking(self):
        source = "red cloak and another red cloak"
        passage = build_discovery_passages(
            source,
            passage_size=300,
            overlap=20,
        )[0]
        result = validate_contract(
            "visual_discovery",
            {
                "observations": [
                    {
                        "character_id": self.character_ids[1],
                        "category": "clothing",
                        "detail": "a red cloak",
                        "scope": "scene_specific",
                        "certainty": 0.8,
                        "basis": "explicit",
                        "quote": "red cloak",
                        "start_char": 1,
                        "end_char": 10,
                    }
                ],
                "warnings": [],
            },
        )
        with self.assertRaisesRegex(
            VisualDiscoveryEvidenceError,
            "multiple times",
        ):
            normalize_visual_passage_result(
                result,
                passage=passage,
                source_text=source,
                allowed_character_ids=set(
                    self.character_ids
                ),
            )

    def test_unapproved_character_id_is_blocked(self):
        passage = build_discovery_passages(
            self.SOURCE_TEXT,
            passage_size=400,
            overlap=40,
        )[0]
        start = passage["text"].index("dark hair")
        result = validate_contract(
            "visual_discovery",
            {
                "observations": [
                    {
                        "character_id": "character_unknown",
                        "category": "hair",
                        "detail": "dark hair",
                        "scope": "stable",
                        "certainty": 0.9,
                        "basis": "explicit",
                        "quote": "dark hair",
                        "start_char": start,
                        "end_char": start + len("dark hair"),
                    }
                ],
                "warnings": [],
            },
        )
        with self.assertRaisesRegex(
            VisualDiscoveryEvidenceError,
            "unapproved character ID",
        ):
            normalize_visual_passage_result(
                result,
                passage=passage,
                source_text=self.SOURCE_TEXT,
                allowed_character_ids=set(
                    self.character_ids
                ),
            )

    def test_stable_profile_cannot_promote_scene_variant(self):
        runtime = DynamicVisualRuntime()
        passages = build_discovery_passages(
            self.SOURCE_TEXT,
            passage_size=400,
            overlap=40,
        )
        identity = build_visual_identity(
            runtime,
            passage_size=400,
            overlap_chars=40,
            temperature=0.1,
            max_tokens=5000,
            seed=42,
        )
        state = new_visual_discovery_state(
            source=self.source,
            roster_fingerprint=self.roster[
                "roster_fingerprint"
            ],
            character_ids=self.character_ids,
            generation_identity=identity,
            passages=passages,
        )
        passage = passages[0]
        start = passage["text"].index("red cloak")
        observation = {
            "character_id": self.character_ids[1],
            "observation_id": "visual_cloak",
            "category": "clothing",
            "detail": "a red cloak",
            "scope": "scene_specific",
            "certainty": 0.8,
            "basis": "explicit",
            "source_location": (
                f"characters {start}-{start + 9}"
            ),
            "start_char": start,
            "end_char": start + 9,
            "passage_index": 1,
            "quote": "red cloak",
        }
        state = checkpoint_visual_passage(
            state=state,
            path=self.state_path,
            passage=passage,
            observations=[observation],
            warnings=[],
        )
        profile = {
            bucket: []
            for bucket in PROFILE_BUCKETS
        }
        profile["clothing"] = [
            {
                "detail": "a red cloak",
                "certainty": 0.8,
                "observation_ids": ["visual_cloak"],
            }
        ]
        reconciliation = validate_contract(
            "visual_reconciliation",
            {
                "characters": [
                    {
                        "character_id": self.character_ids[0],
                        "profile": {
                            bucket: []
                            for bucket in PROFILE_BUCKETS
                        },
                        "variants": [],
                        "conflicts": [],
                        "unknowns": [],
                    },
                    {
                        "character_id": self.character_ids[1],
                        "profile": profile,
                        "variants": [],
                        "conflicts": [],
                        "unknowns": [],
                    },
                ],
                "warnings": [],
            },
        )
        with self.assertRaisesRegex(
            VisualReconciliationError,
            "promotes scene-specific evidence",
        ):
            validate_reconciliation_integrity(
                state=state,
                reconciliation=reconciliation,
            )

    def test_cross_character_evidence_is_rejected(self):
        runtime = DynamicVisualRuntime()
        passages = build_discovery_passages(
            self.SOURCE_TEXT,
            passage_size=400,
            overlap=40,
        )
        identity = build_visual_identity(
            runtime,
            passage_size=400,
            overlap_chars=40,
            temperature=0.1,
            max_tokens=5000,
            seed=42,
        )
        state = new_visual_discovery_state(
            source=self.source,
            roster_fingerprint=self.roster[
                "roster_fingerprint"
            ],
            character_ids=self.character_ids,
            generation_identity=identity,
            passages=passages,
        )
        passage = passages[0]
        start = passage["text"].index("battered hat")
        state = checkpoint_visual_passage(
            state=state,
            path=self.state_path,
            passage=passage,
            observations=[
                {
                    "character_id": self.character_ids[0],
                    "observation_id": "visual_hat",
                    "category": (
                        "accessories_weapons_equipment"
                    ),
                    "detail": "a battered hat",
                    "scope": "stable",
                    "certainty": 0.9,
                    "basis": "explicit",
                    "source_location": (
                        f"characters {start}-{start + 12}"
                    ),
                    "start_char": start,
                    "end_char": start + 12,
                    "passage_index": 1,
                    "quote": "battered hat",
                }
            ],
            warnings=[],
        )
        wrong_profile = {
            bucket: []
            for bucket in PROFILE_BUCKETS
        }
        wrong_profile[
            "accessories_weapons_equipment"
        ] = [
            {
                "detail": "a battered hat",
                "certainty": 0.9,
                "observation_ids": ["visual_hat"],
            }
        ]
        reconciliation = validate_contract(
            "visual_reconciliation",
            {
                "characters": [
                    {
                        "character_id": self.character_ids[0],
                        "profile": {
                            bucket: []
                            for bucket in PROFILE_BUCKETS
                        },
                        "variants": [],
                        "conflicts": [],
                        "unknowns": [],
                    },
                    {
                        "character_id": self.character_ids[1],
                        "profile": wrong_profile,
                        "variants": [],
                        "conflicts": [],
                        "unknowns": [],
                    },
                ],
                "warnings": [],
            },
        )
        with self.assertRaisesRegex(
            VisualReconciliationError,
            "cross-character",
        ):
            validate_reconciliation_integrity(
                state=state,
                reconciliation=reconciliation,
            )

    def test_complete_run_writes_only_selected_persona_refs(self):
        script = self.root / "annotated_script.json"
        voice = self.root / "voice_config.json"
        script.write_text('{"sentinel":"script"}\n')
        voice.write_text('{"sentinel":"voice"}\n')
        before = {
            "script": self.digest(script),
            "voice": self.digest(voice),
        }
        runtime = DynamicVisualRuntime()
        result = run_visual_discovery(
            runtime_client=runtime,
            source=self.source,
            source_text=self.SOURCE_TEXT,
            approved_roster=self.roster,
            character_ids=self.character_ids,
            state_path=self.state_path,
            persona_refs_dir=self.refs,
            passage_size=400,
            overlap_chars=40,
        )
        after = {
            "script": self.digest(script),
            "voice": self.digest(voice),
        }
        self.assertEqual(before, after)
        self.assertEqual(result["status"], "complete")
        self.assertFalse(self.state_path.exists())
        doctor_ref = load_persona_reference(
            persona_reference_path(
                self.refs,
                "THE DOCTOR",
            )
        )
        roz_ref = load_persona_reference(
            persona_reference_path(
                self.refs,
                "ROZ FORRESTER",
            )
        )
        self.assertIn("visual", doctor_ref)
        self.assertIn("visual", roz_ref)
        self.assertEqual(
            doctor_ref["visual"]["profile"]
            ["accessories_weapons_equipment"][0]["detail"],
            "a battered hat",
        )
        self.assertEqual(
            roz_ref["visual"]["profile"]["hair"][0][
                "detail"
            ],
            "dark hair",
        )
        self.assertEqual(
            roz_ref["visual"]["variants"][0]["scope"],
            "scene_specific",
        )

    def test_unknown_selected_id_is_rejected_before_model_call(self):
        runtime = DynamicVisualRuntime()
        with self.assertRaisesRegex(
            VisualDiscoveryError,
            "unknown approved character IDs",
        ):
            run_visual_discovery(
                runtime_client=runtime,
                source=self.source,
                source_text=self.SOURCE_TEXT,
                approved_roster=self.roster,
                character_ids=["character_missing"],
                state_path=self.state_path,
                persona_refs_dir=self.refs,
                passage_size=400,
                overlap_chars=40,
            )
        self.assertEqual(runtime.discovery_calls, 0)
        self.assertFalse(any(self.refs.iterdir()))


if __name__ == "__main__":
    unittest.main()
