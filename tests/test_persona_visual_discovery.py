from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from roster_discovery import build_discovery_passages
from persona_visual_discovery import (
    build_visual_generation_identity,
    clear_persona_visual_state,
    inspect_persona_visual_state,
    load_persona_visual_state,
    prepare_persona_visual_state,
    selected_entry_records,
)
from tests.test_visual_discovery import VisualDiscoveryFixture
from tests.visual_discovery_support import DynamicVisualRuntime


class PersonaVisualDiscoveryCompatibilityTests(
    unittest.TestCase,
    VisualDiscoveryFixture,
):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state_path = self.root / "persona_visual_state.json"
        self.source = {
            "path": str(self.root / "book.txt"),
            "basename": "book.txt",
            "fingerprint": "source-fingerprint",
            "character_count": len(self.SOURCE_TEXT),
        }
        self.roster = self.roster(self.source["fingerprint"])
        self.character_ids = [
            entry["id"]
            for entry in self.roster["entries"]
        ]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_selected_entry_records_preserves_requested_order(self) -> None:
        requested = list(reversed(self.character_ids))
        selected = selected_entry_records(self.roster, requested)
        self.assertEqual(
            [entry["id"] for entry in selected],
            requested,
        )

    def test_status_adapter_uses_canonical_passage_state(self) -> None:
        runtime = DynamicVisualRuntime()
        passages = build_discovery_passages(
            self.SOURCE_TEXT,
            passage_size=400,
            overlap=40,
        )
        identity = build_visual_generation_identity(
            runtime,
            passage_size=400,
            overlap_chars=40,
            temperature=0.1,
            max_tokens=5000,
            seed=42,
        )
        prepare_persona_visual_state(
            path=self.state_path,
            source=self.source,
            roster_fingerprint=self.roster[
                "roster_fingerprint"
            ],
            character_ids=self.character_ids,
            generation_identity=identity,
            passages=passages,
        )
        status = inspect_persona_visual_state(
            self.state_path,
            source_fingerprint=self.source["fingerprint"],
            roster_fingerprint=self.roster[
                "roster_fingerprint"
            ],
        )
        self.assertEqual(status["status"], "resumable")
        self.assertEqual(status["completed_units"], 0)
        self.assertEqual(status["total_units"], len(passages))
        self.assertEqual(
            status["selected_entry_ids"],
            self.character_ids,
        )
        loaded = load_persona_visual_state(self.state_path)
        self.assertEqual(loaded["character_ids"], self.character_ids)

    def test_clear_reports_whether_progress_existed(self) -> None:
        self.assertFalse(clear_persona_visual_state(self.state_path))
        self.state_path.write_text("{}", encoding="utf-8")
        self.assertTrue(clear_persona_visual_state(self.state_path))
        self.assertFalse(self.state_path.exists())


if __name__ == "__main__":
    unittest.main()
