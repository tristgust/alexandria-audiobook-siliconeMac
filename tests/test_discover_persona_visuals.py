from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from character_roster import (
    build_draft_roster,
    build_source_snapshot,
    save_character_roster,
)
from character_roster_actions import build_approved_roster
from character_visuals import load_persona_reference
from discover_persona_visuals import run_persona_visual_discovery
from tests.test_visual_discovery import VisualDiscoveryFixture
from tests.visual_discovery_support import DynamicVisualRuntime


class DiscoverPersonaVisualsTests(
    unittest.TestCase,
    VisualDiscoveryFixture,
):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app_dir = self.root / "app"
        self.app_dir.mkdir()
        self.source_path = self.root / "book.txt"
        self.source_path.write_text(
            self.SOURCE_TEXT,
            encoding="utf-8",
        )
        self.source, _ = build_source_snapshot(self.source_path)
        self.roster = self.roster(self.source["fingerprint"])
        evidence_quotes = {
            "THE DOCTOR": "The Doctor",
            "ROZ FORRESTER": "Roz Forrester",
        }
        for entry in self.roster["entries"]:
            quote = evidence_quotes[entry["canonical_name"]]
            start = self.SOURCE_TEXT.index(quote)
            end = start + len(quote)
            entry["first_evidence_location"] = (
                f"characters {start}-{end}"
            )
            entry["evidence"] = [
                {
                    "source_quote": quote,
                    "source_location": f"characters {start}-{end}",
                    "start_char": start,
                    "end_char": end,
                    "passage_index": 1,
                    "entry_index": None,
                    "batch_index": 1,
                    "category": "name",
                    "confidence": 1.0,
                    "basis": "explicit",
                }
            ]
        draft = build_draft_roster(
            source=self.source,
            discovery={
                "created_at_utc": "2026-07-16T23:00:00Z",
                "model_name": "qwen3.5:35b-mlx",
                "backend": "ollama-native",
                "generation_fingerprint": "visual-cli-fixture",
                "batch_count": 1,
                "completed_batches": 1,
            },
            entries=self.roster["entries"],
            source_text=self.SOURCE_TEXT,
        )
        self.roster = build_approved_roster(
            draft,
            expected_fingerprint=draft["draft_fingerprint"],
            source_fingerprint=self.source["fingerprint"],
            source_text=self.SOURCE_TEXT,
            acknowledged_unresolved=False,
            approved_at_utc="2026-07-16T23:05:00Z",
        )
        self.roster_path = self.root / "character_roster.json"
        save_character_roster(
            self.roster,
            self.roster_path,
            source_text=self.SOURCE_TEXT,
            expected_status="approved",
        )
        self.refs_dir = self.root / "persona_refs"
        self.refs_dir.mkdir()
        self.state_path = self.root / "persona_visual_state.json"
        self.config_path = self.app_dir / "config.json"
        self.config_path.write_text(
            json.dumps(
                {
                    "visual": {
                        "passage_size": 400,
                        "passage_overlap": 40,
                        "temperature": 0.1,
                        "max_tokens": 5000,
                        "seed": 42,
                    }
                }
            ),
            encoding="utf-8",
        )
        self.character_ids = [
            entry["id"]
            for entry in self.roster["entries"]
        ]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(
        self,
        *,
        enabled: bool = True,
        runtime: DynamicVisualRuntime | None = None,
        entry_ids: list[str] | None = None,
    ) -> dict:
        return run_persona_visual_discovery(
            enabled=enabled,
            source_path=self.source_path,
            approved_roster_path=self.roster_path,
            entry_ids=(
                self.character_ids
                if entry_ids is None
                else entry_ids
            ),
            config_path=self.config_path,
            state_path=self.state_path,
            persona_refs_dir=self.refs_dir,
            runtime_client=runtime or DynamicVisualRuntime(),
        )

    def test_disabled_mode_is_a_true_no_op(self) -> None:
        runtime = DynamicVisualRuntime()
        result = run_persona_visual_discovery(
            enabled=False,
            source_path=self.root / "missing.txt",
            approved_roster_path=self.root / "missing-roster.json",
            entry_ids=[],
            config_path=self.root / "missing-config.json",
            state_path=self.state_path,
            persona_refs_dir=self.refs_dir,
            runtime_client=runtime,
        )
        self.assertEqual(
            result,
            {"status": "disabled", "written": []},
        )
        self.assertEqual(runtime.discovery_calls, 0)
        self.assertFalse(self.state_path.exists())
        self.assertEqual(list(self.refs_dir.iterdir()), [])

    def test_enabled_run_uses_canonical_engine_and_records_ownership(self) -> None:
        runtime = DynamicVisualRuntime()
        result = self.run_cli(runtime=runtime)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["entry_count"], 2)
        self.assertEqual(len(result["written"]), 2)
        self.assertFalse(self.state_path.exists())
        self.assertEqual(
            runtime.contracts.count("visual_reconciliation"),
            1,
        )
        for entry, written in zip(
            self.roster["entries"],
            result["written"],
        ):
            reference = load_persona_reference(written)
            self.assertEqual(
                reference["roster_entry_id"],
                entry["id"],
            )
            self.assertEqual(
                reference["visual_source_fingerprint"],
                self.source["fingerprint"],
            )
            self.assertEqual(
                reference["visual_roster_fingerprint"],
                self.roster["roster_fingerprint"],
            )
            self.assertIn("visual", reference)

    def test_unknown_selection_is_rejected_before_model_call(self) -> None:
        runtime = DynamicVisualRuntime()
        with self.assertRaisesRegex(
            RuntimeError,
            "not found",
        ):
            self.run_cli(
                runtime=runtime,
                entry_ids=["character_missing"],
            )
        self.assertEqual(runtime.discovery_calls, 0)
        self.assertFalse(self.state_path.exists())
        self.assertEqual(list(self.refs_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
