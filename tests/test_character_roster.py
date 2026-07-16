from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from character_roster import (
    CharacterRosterValidationError,
    build_character_roster_status,
    build_draft_roster,
    build_source_snapshot,
    compute_draft_fingerprint,
    compute_roster_fingerprint,
    inspect_character_roster_file,
    save_character_roster,
    stable_entry_id,
    validate_character_roster,
)


class CharacterRosterFixture:
    SOURCE_TEXT = (
        "The Doctor greeted Roz. Roz smiled. "
        "The TARDIS stood behind them."
    )

    @classmethod
    def evidence(
        cls,
        quote: str,
        *,
        category: str = "name",
        basis: str = "explicit",
        passage_index: int | None = 0,
        entry_index: int | None = None,
    ) -> dict:
        start = cls.SOURCE_TEXT.index(quote)
        return {
            "source_quote": quote,
            "source_location": "characters 0-72",
            "start_char": start,
            "end_char": start + len(quote),
            "passage_index": passage_index,
            "entry_index": entry_index,
            "batch_index": 0,
            "category": category,
            "confidence": 1.0,
            "basis": basis,
        }

    @classmethod
    def entry(
        cls,
        name: str,
        quote: str,
        *,
        entity_kind: str = "character",
        speaking_status: str = "speaker",
        resolution_status: str = "resolved",
        possible_duplicate_ids: list[str] | None = None,
        mistaken_merge_risk: bool = False,
    ) -> dict:
        return {
            "id": stable_entry_id(
                f"fixture:{cls.SOURCE_TEXT.index(quote)}:{name}"
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
            "first_evidence_location": "characters 0-72",
            "additional_evidence_locations": [],
            "confidence": 0.95,
            "resolution_status": resolution_status,
            "possible_duplicate_ids": (
                possible_duplicate_ids or []
            ),
            "mistaken_merge_risk": mistaken_merge_risk,
            "unresolved_questions": [],
            "evidence": [cls.evidence(quote)],
            "voice_clues": [],
            "sample_lines": [],
        }

    @classmethod
    def source(cls, path: Path) -> dict:
        snapshot, text = build_source_snapshot(path)
        assert text == cls.SOURCE_TEXT
        return snapshot

    @classmethod
    def draft(cls, source: dict) -> dict:
        doctor = cls.entry("THE DOCTOR", "The Doctor")
        roz = cls.entry("ROZ", "Roz")
        tardis = cls.entry(
            "THE TARDIS",
            "The TARDIS",
            entity_kind="named_non_speaker",
            speaking_status="non_speaker",
        )
        return build_draft_roster(
            source=source,
            discovery={
                "created_at_utc": "2026-07-16T18:00:00Z",
                "model_name": "qwen3.5:35b-mlx",
                "backend": "ollama-native",
                "generation_fingerprint": "generation-test",
                "batch_count": 1,
                "completed_batches": 1,
            },
            entries=[doctor, roz, tardis],
            source_text=cls.SOURCE_TEXT,
        )

    @classmethod
    def approved(cls, draft: dict) -> dict:
        approved = {
            key: copy.deepcopy(value)
            for key, value in draft.items()
            if key not in {"status", "draft_fingerprint"}
        }
        approved.update(
            {
                "status": "approved",
                "approved_at_utc": "2026-07-16T19:00:00Z",
                "approved_draft_fingerprint": (
                    draft["draft_fingerprint"]
                ),
                "approval_summary": {
                    "resolved_count": 3,
                    "unresolved_count": 0,
                    "merged_count": 0,
                    "excluded_count": 0,
                    "acknowledged_unresolved": False,
                },
            }
        )
        approved["roster_fingerprint"] = (
            compute_roster_fingerprint(approved)
        )
        return validate_character_roster(
            approved,
            source_text=cls.SOURCE_TEXT,
            expected_status="approved",
        )


class CharacterRosterContractTests(
    unittest.TestCase,
    CharacterRosterFixture,
):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_path = self.root / "book.txt"
        self.source_path.write_text(
            self.SOURCE_TEXT,
            encoding="utf-8",
        )
        self.source_snapshot = self.source(
            self.source_path
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_stable_entry_id_uses_immutable_identity_seed(self):
        self.assertEqual(
            stable_entry_id("  source:10:the   doctor "),
            stable_entry_id("source:10:the doctor"),
        )
        self.assertNotEqual(
            stable_entry_id("source:10:THE DOCTOR"),
            stable_entry_id("source:80:THE DOCTOR"),
        )

    def test_entry_id_format_is_strict(self):
        draft = self.draft(self.source_snapshot)
        draft["entries"][0]["id"] = "THE DOCTOR"
        draft["draft_fingerprint"] = (
            compute_draft_fingerprint(draft)
        )

        with self.assertRaisesRegex(
            CharacterRosterValidationError,
            "opaque character ID",
        ):
            validate_character_roster(draft)

    def test_exact_evidence_preserves_boundary_whitespace(self):
        source_text = "  The Doctor arrived."
        source_path = self.root / "whitespace.txt"
        source_path.write_text(source_text, encoding="utf-8")
        source, _ = build_source_snapshot(source_path)
        entry = self.entry("THE DOCTOR", "The Doctor")
        entry["id"] = stable_entry_id(
            "whitespace-source:1:the-doctor"
        )
        entry["evidence"][0].update(
            {
                "source_quote": " The Doctor",
                "start_char": 1,
                "end_char": 12,
            }
        )
        draft = build_draft_roster(
            source=source,
            discovery={
                "created_at_utc": "2026-07-16T18:00:00Z",
                "model_name": "qwen3.5:35b-mlx",
                "backend": "ollama-native",
                "generation_fingerprint": "generation-test",
                "batch_count": 1,
                "completed_batches": 1,
            },
            entries=[entry],
            source_text=source_text,
        )
        self.assertEqual(
            draft["entries"][0]["evidence"][0][
                "source_quote"
            ],
            " The Doctor",
        )

    def test_valid_draft_round_trip(self):
        draft = self.draft(self.source_snapshot)
        path = self.root / "character_roster.draft.json"
        saved = save_character_roster(
            draft,
            path,
            source_text=self.SOURCE_TEXT,
            expected_status="draft",
        )
        self.assertEqual(saved, draft)
        self.assertTrue(path.exists())
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8")),
            draft,
        )

    def test_exact_evidence_quote_is_required(self):
        draft = self.draft(self.source_snapshot)
        draft["entries"][0]["evidence"][0][
            "source_quote"
        ] = "The Other Doctor"
        draft["draft_fingerprint"] = (
            "tampered-fingerprint"
        )

        with self.assertRaisesRegex(
            CharacterRosterValidationError,
            "quote does not match",
        ):
            validate_character_roster(
                draft,
                source_text=self.SOURCE_TEXT,
                expected_status="draft",
            )

    def test_unknown_entry_field_is_rejected(self):
        draft = self.draft(self.source_snapshot)
        draft["entries"][0]["invented"] = True

        with self.assertRaisesRegex(
            CharacterRosterValidationError,
            "unexpected invented",
        ):
            validate_character_roster(draft)

    def test_draft_fingerprint_tampering_is_rejected(self):
        draft = self.draft(self.source_snapshot)
        draft["entries"][0]["aliases"].append(
            "DOCTOR"
        )

        with self.assertRaisesRegex(
            CharacterRosterValidationError,
            "draft fingerprint",
        ):
            validate_character_roster(draft)

    def test_approved_roster_cannot_be_forged_by_status_change(self):
        draft = self.draft(self.source_snapshot)
        forged = copy.deepcopy(draft)
        forged["status"] = "approved"

        with self.assertRaises(
            CharacterRosterValidationError
        ):
            validate_character_roster(forged)

    def test_valid_approved_roster_has_two_fingerprint_gates(self):
        draft = self.draft(self.source_snapshot)
        approved = self.approved(draft)
        self.assertEqual(
            approved["approved_draft_fingerprint"],
            draft["draft_fingerprint"],
        )
        self.assertTrue(approved["roster_fingerprint"])

        tampered = copy.deepcopy(approved)
        tampered["approved_at_utc"] = (
            "2026-07-16T20:00:00Z"
        )

        with self.assertRaisesRegex(
            CharacterRosterValidationError,
            "Approved roster fingerprint",
        ):
            validate_character_roster(tampered)

    def test_approval_summary_must_match_roster_contents(self):
        draft = self.draft(self.source_snapshot)
        approved = self.approved(draft)
        approved["approval_summary"][
            "resolved_count"
        ] = 2
        approved["roster_fingerprint"] = (
            compute_roster_fingerprint(approved)
        )

        with self.assertRaisesRegex(
            CharacterRosterValidationError,
            "resolved_count does not match",
        ):
            validate_character_roster(approved)

    def test_unnamed_and_unresolved_entries_remain_explicit(self):
        entry = self.entry(
            "TEMPORARY",
            "Roz",
            resolution_status="unnamed",
        )
        entry["canonical_name"] = ""
        entry["display_name"] = "Unnamed woman near Roz"
        entry["unresolved_questions"] = [
            "Is this speaker Roz or a separate person?"
        ]
        draft = build_draft_roster(
            source=self.source_snapshot,
            discovery={
                "created_at_utc": "2026-07-16T18:00:00Z",
                "model_name": "qwen3.5:35b-mlx",
                "backend": "ollama-native",
                "generation_fingerprint": "generation-test",
                "batch_count": 1,
                "completed_batches": 1,
            },
            entries=[entry],
            unresolved=[
                {
                    "entry_id": entry["id"],
                    "question": (
                        "Is this speaker Roz or a separate person?"
                    ),
                    "confidence": 0.4,
                }
            ],
            source_text=self.SOURCE_TEXT,
        )
        self.assertEqual(
            draft["entries"][0]["resolution_status"],
            "unnamed",
        )
        self.assertEqual(len(draft["unresolved"]), 1)

        approved = {
            key: copy.deepcopy(value)
            for key, value in draft.items()
            if key not in {"status", "draft_fingerprint"}
        }
        approved.update(
            {
                "status": "approved",
                "approved_at_utc": "2026-07-16T19:00:00Z",
                "approved_draft_fingerprint": (
                    draft["draft_fingerprint"]
                ),
                "approval_summary": {
                    "resolved_count": 0,
                    "unresolved_count": 1,
                    "merged_count": 0,
                    "excluded_count": 0,
                    "acknowledged_unresolved": False,
                },
            }
        )
        approved["roster_fingerprint"] = (
            compute_roster_fingerprint(approved)
        )

        with self.assertRaisesRegex(
            CharacterRosterValidationError,
            "acknowledged_unresolved=true",
        ):
            validate_character_roster(approved)

    def test_incompatible_source_is_distinct_from_invalid(self):
        draft = self.draft(self.source_snapshot)
        path = self.root / "character_roster.draft.json"
        save_character_roster(
            draft,
            path,
            source_text=self.SOURCE_TEXT,
        )
        other_path = self.root / "other.txt"
        other_path.write_text(
            "A different book.",
            encoding="utf-8",
        )
        other_snapshot, other_text = (
            build_source_snapshot(other_path)
        )
        inspection = inspect_character_roster_file(
            path,
            expected_status="draft",
            current_source=other_snapshot,
            current_source_text=other_text,
        )
        self.assertEqual(
            inspection["status"],
            "incompatible_source",
        )
        self.assertFalse(
            inspection["compatible_source"]
        )
        self.assertIsNone(inspection["error"])

    def test_status_distinguishes_missing_corrupt_and_active(self):
        draft_path = self.root / "draft.json"
        approved_path = self.root / "approved.json"
        missing = build_character_roster_status(
            draft_path=draft_path,
            approved_path=approved_path,
            current_source=self.source_snapshot,
            current_source_text=self.SOURCE_TEXT,
            current_source_error=None,
        )
        self.assertEqual(missing["active"], "none")
        self.assertEqual(
            missing["draft"]["status"],
            "missing",
        )

        draft_path.write_text(
            "{broken",
            encoding="utf-8",
        )
        corrupt = build_character_roster_status(
            draft_path=draft_path,
            approved_path=approved_path,
            current_source=self.source_snapshot,
            current_source_text=self.SOURCE_TEXT,
            current_source_error=None,
        )
        self.assertEqual(
            corrupt["draft"]["status"],
            "corrupt",
        )

        draft = self.draft(self.source_snapshot)
        save_character_roster(
            draft,
            draft_path,
            source_text=self.SOURCE_TEXT,
        )
        active = build_character_roster_status(
            draft_path=draft_path,
            approved_path=approved_path,
            current_source=self.source_snapshot,
            current_source_text=self.SOURCE_TEXT,
            current_source_error=None,
        )
        self.assertEqual(active["active"], "draft")
        self.assertEqual(
            active["draft"]["counts"],
            {
                "entries": 3,
                "resolved": 3,
                "unresolved": 0,
                "unnamed": 0,
                "duplicate_candidates": 0,
                "excluded": 0,
                "speakers": 2,
                "named_non_speakers": 1,
            },
        )


if __name__ == "__main__":
    unittest.main()
