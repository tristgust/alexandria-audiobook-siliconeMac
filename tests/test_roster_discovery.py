from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from generation_state import fingerprint_text
from roster_discovery import (
    RosterDiscoveryCorruptError,
    RosterDiscoveryEvidenceError,
    RosterReconciliationError,
    build_discovery_identity,
    build_discovery_passages,
    build_draft_from_discovery_state,
    checkpoint_roster_passage,
    checkpoint_roster_reconciliation,
    completed_observations,
    load_roster_discovery_state,
    new_roster_discovery_state,
    normalize_passage_result,
    validate_reconciliation_partition,
)


class RosterDiscoveryFixture:
    SOURCE_TEXT = (
        "Intro. The Doctor arrived. \"No. It rarely is.\" "
        "Another Doctor watched from the doorway."
    )

    @classmethod
    def source(cls) -> dict:
        return {
            "path": "/tmp/book.txt",
            "basename": "book.txt",
            "fingerprint": fingerprint_text(cls.SOURCE_TEXT),
            "character_count": len(cls.SOURCE_TEXT),
        }

    @staticmethod
    def identity() -> dict:
        return build_discovery_identity(
            model_name="qwen3.5:35b-mlx",
            backend="ollama-native",
            passage_size=120,
            overlap=20,
            temperature=0.2,
            max_tokens=4096,
            seed=42,
        )

    @classmethod
    def passage(cls) -> dict:
        return build_discovery_passages(
            cls.SOURCE_TEXT,
            passage_size=120,
            overlap=20,
        )[0]

    @classmethod
    def discovery_result(
        cls,
        *,
        identity_seed: str = "doctor-at-first-mention",
        canonical_name: str = "THE DOCTOR",
        quote: str = " The Doctor",
        quote_start: int | None = None,
        sample_lines: list[str] | None = None,
    ) -> dict:
        passage = cls.passage()
        if quote_start is None:
            quote_start = passage["text"].index(quote)
        return {
            "entities": [
                {
                    "identity_seed": identity_seed,
                    "canonical_name": canonical_name,
                    "display_name": canonical_name.title(),
                    "entity_kind": "character",
                    "speaking_status": "speaker",
                    "titles": ["Doctor"],
                    "aliases": [],
                    "nicknames": [],
                    "pronouns": [],
                    "species": [],
                    "relationships": [],
                    "voice_clues": ["Dry delivery"],
                    "sample_lines": (
                        sample_lines
                        if sample_lines is not None
                        else ["No. It rarely is."]
                    ),
                    "confidence": 0.9,
                    "resolution_status": "resolved",
                    "unresolved_questions": [],
                    "evidence": [
                        {
                            "quote": quote,
                            "start_char": quote_start,
                            "end_char": quote_start + len(quote),
                            "category": category,
                            "confidence": 1.0,
                            "basis": "explicit",
                        }
                        for category in (
                            "name",
                            "title",
                            "voice",
                            "speaking",
                        )
                    ],
                }
            ],
            "warnings": [],
        }


class PassageTests(unittest.TestCase):
    def test_passages_cover_source_with_overlap(self) -> None:
        text = ("Paragraph one. " * 20) + "\n\n" + (
            "Paragraph two. " * 20
        )
        passages = build_discovery_passages(
            text,
            passage_size=180,
            overlap=30,
        )
        self.assertGreater(len(passages), 2)
        self.assertEqual(passages[0]["start_char"], 0)
        self.assertEqual(passages[-1]["end_char"], len(text))

        for previous, current in zip(passages, passages[1:]):
            self.assertLess(current["start_char"], previous["end_char"])
            self.assertGreater(current["start_char"], previous["start_char"])
            self.assertEqual(
                current["text"],
                text[current["start_char"]:current["end_char"]],
            )

    def test_invalid_overlap_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_discovery_passages(
                "x" * 200,
                passage_size=100,
                overlap=100,
            )


class EvidenceNormalizationTests(
    unittest.TestCase,
    RosterDiscoveryFixture,
):
    def test_relative_evidence_becomes_exact_absolute_evidence(self) -> None:
        passage = self.passage()
        observations, warnings = normalize_passage_result(
            self.discovery_result(),
            passage=passage,
            source_fingerprint=self.source()["fingerprint"],
        )
        self.assertEqual(warnings, [])
        evidence = observations[0]["evidence"][0]
        self.assertEqual(evidence["source_quote"], " The Doctor")
        self.assertEqual(
            self.SOURCE_TEXT[
                evidence["start_char"]:evidence["end_char"]
            ],
            " The Doctor",
        )
        self.assertEqual(evidence["passage_index"], 1)
        self.assertTrue(
            observations[0]["observation_id"].startswith(
                "observation_"
            )
        )

    def test_unique_exact_quote_offsets_are_repaired(self) -> None:
        result = self.discovery_result(quote_start=0)
        observations, warnings = normalize_passage_result(
            result,
            passage=self.passage(),
            source_fingerprint=self.source()["fingerprint"],
        )
        self.assertTrue(
            any("Repaired unique exact evidence offsets" in item for item in warnings)
        )
        evidence = observations[0]["evidence"][0]
        self.assertEqual(
            self.SOURCE_TEXT[evidence["start_char"]:evidence["end_char"]],
            evidence["source_quote"],
        )

    def test_non_source_quote_is_blocked(self) -> None:
        result = self.discovery_result(
            quote="Missing Doctor",
            quote_start=0,
        )
        with self.assertRaisesRegex(
            RosterDiscoveryEvidenceError,
            "not exact source text",
        ):
            normalize_passage_result(
                result,
                passage=self.passage(),
                source_fingerprint=self.source()["fingerprint"],
            )

    def test_sample_line_must_be_exact_passage_text(self) -> None:
        result = self.discovery_result(
            sample_lines=["No, it rarely is."],
        )
        with self.assertRaisesRegex(
            RosterDiscoveryEvidenceError,
            "sample line",
        ):
            normalize_passage_result(
                result,
                passage=self.passage(),
                source_fingerprint=self.source()["fingerprint"],
            )


class ReconciliationTests(
    unittest.TestCase,
    RosterDiscoveryFixture,
):
    def setUp(self) -> None:
        first, _ = normalize_passage_result(
            self.discovery_result(),
            passage=self.passage(),
            source_fingerprint=self.source()["fingerprint"],
        )
        second_result = self.discovery_result(
            identity_seed="doctor-at-doorway",
            quote=" Doctor watched",
            canonical_name="THE DOCTOR",
            sample_lines=[],
        )
        second, _ = normalize_passage_result(
            second_result,
            passage=self.passage(),
            source_fingerprint=self.source()["fingerprint"],
        )
        self.observations = [first[0], second[0]]

    def separate_reconciliation(self) -> dict:
        first, second = self.observations
        return {
            "entries": [
                {
                    "identity_seed": "doctor-one",
                    "canonical_name": "THE DOCTOR",
                    "display_name": "The Doctor",
                    "entity_kind": "character",
                    "speaking_status": "speaker",
                    "observation_ids": [first["observation_id"]],
                    "confidence": 0.7,
                    "resolution_status": "duplicate_candidate",
                    "possible_duplicate_seeds": ["doctor-two"],
                    "mistaken_merge_risk": True,
                    "unresolved_questions": [
                        "Are these two mentions the same person?"
                    ],
                },
                {
                    "identity_seed": "doctor-two",
                    "canonical_name": "THE DOCTOR",
                    "display_name": "The Doctor at the doorway",
                    "entity_kind": "character",
                    "speaking_status": "uncertain",
                    "observation_ids": [second["observation_id"]],
                    "confidence": 0.6,
                    "resolution_status": "duplicate_candidate",
                    "possible_duplicate_seeds": ["doctor-one"],
                    "mistaken_merge_risk": True,
                    "unresolved_questions": [
                        "Is this a second Doctor?"
                    ],
                },
            ],
            "duplicate_candidates": [
                {
                    "identity_seeds": ["doctor-one", "doctor-two"],
                    "reason": "Same title, separate source positions.",
                    "confidence": 0.5,
                    "observation_ids": [
                        first["observation_id"],
                        second["observation_id"],
                    ],
                }
            ],
            "excluded_observation_ids": [],
            "warnings": [],
        }

    def test_every_observation_must_be_accounted_for_once(self) -> None:
        reconciliation = self.separate_reconciliation()
        reconciliation["entries"] = reconciliation["entries"][:1]
        reconciliation["entries"][0]["possible_duplicate_seeds"] = []
        reconciliation["duplicate_candidates"] = []
        with self.assertRaisesRegex(
            RosterReconciliationError,
            "omitted observations",
        ):
            validate_reconciliation_partition(
                reconciliation,
                self.observations,
            )

    def test_observation_cannot_be_assigned_twice(self) -> None:
        reconciliation = self.separate_reconciliation()
        reconciliation["excluded_observation_ids"] = [
            self.observations[0]["observation_id"]
        ]
        with self.assertRaisesRegex(
            RosterReconciliationError,
            "exactly once",
        ):
            validate_reconciliation_partition(
                reconciliation,
                self.observations,
            )

    def test_draft_keeps_ambiguous_same_name_identities_separate(self) -> None:
        passages = [self.passage()]
        state = new_roster_discovery_state(
            source=self.source(),
            generation_identity=self.identity(),
            passages=passages,
        )
        state = {
            **state,
            "completed_passages": [
                {
                    "index": 1,
                    "passage_fingerprint": passages[0]["fingerprint"],
                    "observations": copy.deepcopy(self.observations),
                    "warnings": [],
                }
            ],
            "reconciliation": self.separate_reconciliation(),
        }
        draft = build_draft_from_discovery_state(
            state,
            source_text=self.SOURCE_TEXT,
            generated_at_utc="2026-07-16T20:00:00Z",
        )
        self.assertEqual(len(draft["entries"]), 2)
        self.assertNotEqual(
            draft["entries"][0]["id"],
            draft["entries"][1]["id"],
        )
        self.assertEqual(len(draft["duplicate_candidates"]), 1)
        self.assertEqual(
            set(draft["entries"][0]["possible_duplicate_ids"]),
            {draft["entries"][1]["id"]},
        )


class CheckpointIntegrationTests(
    unittest.TestCase,
    RosterDiscoveryFixture,
):
    def test_checkpoint_and_reconciliation_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "character_roster_state.json"
            passage = self.passage()
            state = new_roster_discovery_state(
                source=self.source(),
                generation_identity=self.identity(),
                passages=[passage],
            )
            observations, warnings = normalize_passage_result(
                self.discovery_result(),
                passage=passage,
                source_fingerprint=self.source()["fingerprint"],
            )
            state = checkpoint_roster_passage(
                state=state,
                path=state_path,
                passage=passage,
                observations=observations,
                warnings=warnings,
            )
            observation_id = observations[0]["observation_id"]
            state = checkpoint_roster_reconciliation(
                state=state,
                path=state_path,
                reconciliation={
                    "entries": [
                        {
                            "identity_seed": "doctor",
                            "canonical_name": "THE DOCTOR",
                            "display_name": "The Doctor",
                            "entity_kind": "character",
                            "speaking_status": "speaker",
                            "observation_ids": [observation_id],
                            "confidence": 0.95,
                            "resolution_status": "resolved",
                            "possible_duplicate_seeds": [],
                            "mistaken_merge_risk": False,
                            "unresolved_questions": [],
                        }
                    ],
                    "duplicate_candidates": [],
                    "excluded_observation_ids": [],
                    "warnings": [],
                },
            )
            self.assertEqual(len(completed_observations(state)), 1)
            self.assertIsNotNone(state["reconciliation"])
            self.assertTrue(state_path.exists())

    def test_tampered_checkpoint_evidence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "character_roster_state.json"
            passage = self.passage()
            state = new_roster_discovery_state(
                source=self.source(),
                generation_identity=self.identity(),
                passages=[passage],
            )
            observations, warnings = normalize_passage_result(
                self.discovery_result(),
                passage=passage,
                source_fingerprint=self.source()["fingerprint"],
            )
            state = checkpoint_roster_passage(
                state=state,
                path=state_path,
                passage=passage,
                observations=observations,
                warnings=warnings,
            )
            raw = copy.deepcopy(state)
            raw["completed_passages"][0]["observations"][0][
                "evidence"
            ][0]["source_quote"] += " altered"
            state_path.write_text(
                __import__("json").dumps(raw),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RosterDiscoveryCorruptError,
                "length does not match",
            ):
                load_roster_discovery_state(state_path)


if __name__ == "__main__":
    unittest.main()
