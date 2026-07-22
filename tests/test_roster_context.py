from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from character_roster import (
    build_draft_roster,
    build_source_snapshot,
    compute_roster_fingerprint,
    save_character_roster,
    stable_entry_id,
    validate_character_roster,
)
from character_roster_actions import build_approved_roster
from roster_context import (
    RosterContextInvalidError,
    RosterContextSourceMismatchError,
    build_roster_prompt_context,
    build_speaker_alias_index,
    canonical_speaker_name,
    canonicalize_script_entries,
    load_approved_roster_for_source,
    roster_generation_identity,
)


class RosterContextFixture:
    SOURCE_TEXT = (
        "The Doctor greeted Roz. 'Hello,' said the Doctor. "
        "Roz answered. The TARDIS stood behind them."
    )

    @classmethod
    def evidence(cls, quote: str) -> dict:
        start = cls.SOURCE_TEXT.index(quote)
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
        display_name: str | None = None,
        aliases: list[str] | None = None,
        titles: list[str] | None = None,
        entity_kind: str = "character",
        speaking_status: str = "speaker",
        resolution_status: str = "resolved",
        questions: list[str] | None = None,
    ) -> dict:
        return {
            "id": stable_entry_id(
                f"roster-context:{cls.SOURCE_TEXT.index(quote)}:{name}"
            ),
            "canonical_name": name,
            "display_name": display_name or name,
            "entity_kind": entity_kind,
            "speaking_status": speaking_status,
            "titles": titles or [],
            "aliases": aliases or [],
            "nicknames": [],
            "pronouns": [],
            "species": [],
            "relationships": [],
            "first_evidence_location": "fixture",
            "additional_evidence_locations": [],
            "confidence": 0.95,
            "resolution_status": resolution_status,
            "possible_duplicate_ids": [],
            "mistaken_merge_risk": False,
            "unresolved_questions": questions or [],
            "evidence": [cls.evidence(quote)],
            "voice_clues": [],
            "sample_lines": [],
        }

    @classmethod
    def approved_roster(cls, source_path: Path) -> dict:
        source, text = build_source_snapshot(source_path)
        assert text == cls.SOURCE_TEXT
        draft = build_draft_roster(
            source=source,
            discovery={
                "created_at_utc": "2026-07-16T21:00:00Z",
                "model_name": "qwen3.5:35b-mlx",
                "backend": "ollama-native",
                "generation_fingerprint": "roster-context-fixture",
                "batch_count": 1,
                "completed_batches": 1,
            },
            entries=[
                cls.entry(
                    "THE DOCTOR",
                    "The Doctor",
                    display_name="The Doctor",
                    aliases=["Doctor"],
                    titles=["the Doctor"],
                ),
                cls.entry(
                    "ROZ",
                    "Roz",
                    display_name="Roz",
                    aliases=["Roslyn"],
                ),
                cls.entry(
                    "THE TARDIS",
                    "The TARDIS",
                    display_name="The TARDIS",
                    entity_kind="named_non_speaker",
                    speaking_status="non_speaker",
                ),
            ],
            source_text=cls.SOURCE_TEXT,
        )
        approved = {
            key: copy.deepcopy(value)
            for key, value in draft.items()
            if key not in {"status", "draft_fingerprint"}
        }
        approved.update(
            {
                "status": "approved",
                "approved_at_utc": "2026-07-16T21:05:00Z",
                "approved_draft_fingerprint": draft["draft_fingerprint"],
                "approval_summary": {
                    "resolved_count": 3,
                    "unresolved_count": 0,
                    "merged_count": 0,
                    "excluded_count": 0,
                    "acknowledged_unresolved": False,
                },
            }
        )
        approved["roster_fingerprint"] = compute_roster_fingerprint(approved)
        return validate_character_roster(
            approved,
            source_text=cls.SOURCE_TEXT,
            expected_status="approved",
        )


class RosterContextTests(unittest.TestCase, RosterContextFixture):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_path = self.root / "book.txt"
        self.source_path.write_text(self.SOURCE_TEXT, encoding="utf-8")
        self.roster = self.approved_roster(self.source_path)
        self.roster_path = self.root / "character_roster.json"
        save_character_roster(
            self.roster,
            self.roster_path,
            source_text=self.SOURCE_TEXT,
            expected_status="approved",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def approved_from_entries(
        self,
        entries: list[dict],
        *,
        acknowledged_unresolved: bool = False,
    ) -> dict:
        draft = build_draft_roster(
            source=self.roster["source"],
            discovery=self.roster["discovery"],
            entries=entries,
            source_text=self.SOURCE_TEXT,
        )
        return build_approved_roster(
            draft,
            expected_fingerprint=draft["draft_fingerprint"],
            source_fingerprint=self.roster["source"]["fingerprint"],
            source_text=self.SOURCE_TEXT,
            acknowledged_unresolved=acknowledged_unresolved,
            approved_at_utc="2026-07-16T21:10:00Z",
        )

    def test_missing_roster_is_backward_compatible(self) -> None:
        self.roster_path.unlink()
        loaded = load_approved_roster_for_source(
            root_dir=self.root,
            source_text=self.SOURCE_TEXT,
        )
        self.assertIsNone(loaded)

    def test_valid_roster_loads_only_for_exact_source(self) -> None:
        loaded = load_approved_roster_for_source(
            root_dir=self.root,
            source_text=self.SOURCE_TEXT,
        )
        self.assertEqual(loaded, self.roster)
        with self.assertRaises(RosterContextSourceMismatchError):
            load_approved_roster_for_source(
                root_dir=self.root,
                source_text=self.SOURCE_TEXT + " changed",
            )

    def test_invalid_existing_roster_blocks_instead_of_disappearing(self) -> None:
        self.roster_path.write_text("{}", encoding="utf-8")
        with self.assertRaises(RosterContextInvalidError):
            load_approved_roster_for_source(
                root_dir=self.root,
                source_text=self.SOURCE_TEXT,
            )

    def test_prompt_context_is_deterministic_and_stage_specific(self) -> None:
        first = build_roster_prompt_context(
            self.roster,
            stage="script",
        )
        second = build_roster_prompt_context(
            self.roster,
            stage="script",
        )
        self.assertEqual(first, second)
        self.assertIn("Stage: script", first)
        self.assertIn("THE DOCTOR", first)
        self.assertIn("aliases: Doctor", first)
        self.assertIn("ROZ", first)
        self.assertNotIn("THE TARDIS\n", first)
        self.assertIn("never authorizes wording", first)

    def test_prompt_preserves_unresolved_identity_as_separate(self) -> None:
        unresolved = self.entry(
            "SHORT MAN",
            "The Doctor",
            display_name="Short man near the doorway",
            resolution_status="unresolved",
            questions=["May or may not be the Doctor."],
        )
        validated = self.approved_from_entries(
            [*copy.deepcopy(self.roster["entries"]), unresolved],
            acknowledged_unresolved=True,
        )
        prompt = build_roster_prompt_context(validated, stage="review")
        self.assertIn("Unresolved identities", prompt)
        self.assertIn("May or may not be the Doctor", prompt)
        self.assertIn("do not merge", prompt.casefold())

    def test_alias_index_maps_only_unique_resolved_speakers(self) -> None:
        index = build_speaker_alias_index(self.roster)
        self.assertEqual(index["doctor"], "THE DOCTOR")
        self.assertEqual(index["roslyn"], "ROZ")
        self.assertNotIn("the tardis", index)

        entries = copy.deepcopy(self.roster["entries"])
        entries[1]["aliases"].append("Doctor")
        validated = self.approved_from_entries(entries)
        ambiguous = build_speaker_alias_index(validated)
        self.assertIsNone(ambiguous["doctor"])

    def test_canonicalization_changes_speaker_only(self) -> None:
        entries = [
            {
                "speaker": "Doctor",
                "text": "Tell me the truth.",
                "instruct": "Quiet urgency.",
            },
            {
                "speaker": "narrator",
                "text": "Roz looked away.",
                "instruct": "Neutral narration.",
            },
            {
                "speaker": "Unknown Guard",
                "text": "Stop.",
                "instruct": "Sharp command.",
            },
        ]
        normalized = canonicalize_script_entries(entries, self.roster)
        self.assertEqual(normalized[0]["speaker"], "THE DOCTOR")
        self.assertEqual(normalized[1]["speaker"], "NARRATOR")
        self.assertEqual(normalized[2]["speaker"], "Unknown Guard")
        self.assertEqual(
            [item["text"] for item in normalized],
            [item["text"] for item in entries],
        )
        self.assertEqual(
            [item["instruct"] for item in normalized],
            [item["instruct"] for item in entries],
        )
        self.assertEqual(entries[0]["speaker"], "Doctor")

    def test_canonical_speaker_leaves_ambiguous_alias_unchanged(self) -> None:
        entries = copy.deepcopy(self.roster["entries"])
        entries[1]["aliases"].append("Doctor")
        validated = self.approved_from_entries(entries)
        self.assertEqual(
            canonical_speaker_name("Doctor", validated),
            "Doctor",
        )

    def test_generation_identity_uses_roster_fingerprint(self) -> None:
        identity = roster_generation_identity(self.roster)
        self.assertEqual(identity["context_version"], 1)
        self.assertEqual(
            identity["roster_fingerprint"],
            self.roster["roster_fingerprint"],
        )
        self.assertEqual(
            identity["source_fingerprint"],
            self.roster["source"]["fingerprint"],
        )
        self.assertIsNone(roster_generation_identity(None))

    def test_prompt_truncation_is_bounded_and_explicit(self) -> None:
        prompt = build_roster_prompt_context(
            self.roster,
            stage="persona",
            max_chars=420,
        )
        self.assertLessEqual(len(prompt), 420)
        self.assertIn("truncated", prompt)


if __name__ == "__main__":
    unittest.main()
