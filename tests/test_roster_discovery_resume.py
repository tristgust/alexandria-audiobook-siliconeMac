from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from generation_state import fingerprint_text
from roster_discovery import (
    RosterDiscoveryCorruptError,
    RosterDiscoveryError,
    RosterDiscoveryMismatchError,
    build_discovery_identity,
    build_discovery_passages,
    checkpoint_roster_passage,
    checkpoint_roster_reconciliation,
    clear_roster_discovery_state,
    inspect_roster_discovery_state,
    load_roster_discovery_state,
    prepare_roster_discovery_state,
)


class RosterDiscoveryResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "character_roster_state.json"
        self.text = (
            ("Alpha spoke. " * 30)
            + "\n\n"
            + ("Beta answered. " * 30)
        )
        self.source = {
            "path": str(self.root / "book.txt"),
            "basename": "book.txt",
            "fingerprint": fingerprint_text(self.text),
            "character_count": len(self.text),
        }
        self.passages = build_discovery_passages(
            self.text,
            passage_size=180,
            overlap=30,
        )
        self.identity = build_discovery_identity(
            model_name="qwen3.5:35b-mlx",
            backend="ollama-native",
            passage_size=180,
            overlap=30,
            temperature=0.2,
            max_tokens=4096,
            seed=42,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def prepare(self):
        return prepare_roster_discovery_state(
            path=self.path,
            source=self.source,
            generation_identity=self.identity,
            passages=self.passages,
        )

    def test_fresh_state_is_atomic_and_resumable(self) -> None:
        state = self.prepare()
        self.assertTrue(self.path.exists())
        self.assertFalse(self.path.with_name(self.path.name + ".tmp").exists())
        self.assertEqual(state["completed_passages"], [])

        state = checkpoint_roster_passage(
            state=state,
            path=self.path,
            passage=self.passages[0],
            observations=[],
            warnings=["No entities in this passage."],
        )
        resumed = self.prepare()
        self.assertEqual(len(resumed["completed_passages"]), 1)
        self.assertEqual(
            resumed["completed_passages"][0]["warnings"],
            ["No entities in this passage."],
        )

    def test_mismatched_source_requires_explicit_discard(self) -> None:
        self.prepare()
        changed = copy.deepcopy(self.source)
        changed["fingerprint"] = fingerprint_text(self.text + "changed")

        with self.assertRaisesRegex(
            RosterDiscoveryMismatchError,
            "source",
        ):
            prepare_roster_discovery_state(
                path=self.path,
                source=changed,
                generation_identity=self.identity,
                passages=self.passages,
            )
        self.assertTrue(self.path.exists())

    def test_mismatched_generation_and_layout_are_blocked(self) -> None:
        self.prepare()
        changed_identity = {
            **self.identity,
            "temperature": 0.4,
        }
        with self.assertRaisesRegex(
            RosterDiscoveryMismatchError,
            "generation configuration",
        ):
            prepare_roster_discovery_state(
                path=self.path,
                source=self.source,
                generation_identity=changed_identity,
                passages=self.passages,
            )

        changed_passages = copy.deepcopy(self.passages)
        changed_passages[0]["fingerprint"] = "different"
        with self.assertRaisesRegex(
            RosterDiscoveryMismatchError,
            "passage layout",
        ):
            prepare_roster_discovery_state(
                path=self.path,
                source=self.source,
                generation_identity=self.identity,
                passages=changed_passages,
            )

    def test_checkpoint_must_be_contiguous(self) -> None:
        state = self.prepare()
        with self.assertRaisesRegex(
            RosterDiscoveryError,
            "next contiguous",
        ):
            checkpoint_roster_passage(
                state=state,
                path=self.path,
                passage=self.passages[1],
                observations=[],
                warnings=[],
            )

    def test_reconciliation_waits_for_every_passage(self) -> None:
        state = self.prepare()
        state = checkpoint_roster_passage(
            state=state,
            path=self.path,
            passage=self.passages[0],
            observations=[],
            warnings=[],
        )
        with self.assertRaisesRegex(
            RosterDiscoveryError,
            "All roster discovery passages",
        ):
            checkpoint_roster_reconciliation(
                state=state,
                path=self.path,
                reconciliation={
                    "entries": [],
                    "duplicate_candidates": [],
                    "excluded_observation_ids": [],
                    "warnings": [],
                },
            )

    def test_corrupt_state_is_reported_without_replacement(self) -> None:
        self.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(RosterDiscoveryCorruptError):
            load_roster_discovery_state(self.path)
        inspection = inspect_roster_discovery_state(
            self.path,
            current_source=self.source,
        )
        self.assertEqual(inspection["status"], "corrupt")
        self.assertTrue(self.path.exists())

    def test_status_transitions_and_discard_are_explicit(self) -> None:
        missing = inspect_roster_discovery_state(
            self.path,
            current_source=self.source,
        )
        self.assertEqual(missing["status"], "missing")

        state = self.prepare()
        resumable = inspect_roster_discovery_state(
            self.path,
            current_source=self.source,
        )
        self.assertEqual(resumable["status"], "resumable")
        self.assertEqual(resumable["next_passage"], 1)

        for passage in self.passages:
            state = checkpoint_roster_passage(
                state=state,
                path=self.path,
                passage=passage,
                observations=[],
                warnings=[],
            )

        awaiting = inspect_roster_discovery_state(
            self.path,
            current_source=self.source,
        )
        self.assertEqual(
            awaiting["status"],
            "awaiting_reconciliation",
        )

        state = checkpoint_roster_reconciliation(
            state=state,
            path=self.path,
            reconciliation={
                "entries": [],
                "duplicate_candidates": [],
                "excluded_observation_ids": [],
                "warnings": [],
            },
        )
        ready = inspect_roster_discovery_state(
            self.path,
            current_source=self.source,
        )
        self.assertEqual(ready["status"], "ready_to_finalize")
        self.assertTrue(clear_roster_discovery_state(self.path))
        self.assertFalse(clear_roster_discovery_state(self.path))

    def test_unknown_state_fields_are_rejected(self) -> None:
        state = self.prepare()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        raw["invented"] = True
        self.path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(
            RosterDiscoveryCorruptError,
            "unexpected invented",
        ):
            load_roster_discovery_state(self.path)
        self.assertEqual(state["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
