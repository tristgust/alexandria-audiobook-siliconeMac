from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from discover_character_roster import run_roster_discovery
from roster_discovery import (
    build_discovery_passages,
    load_roster_discovery_state,
)


class FakeRuntime:
    def __init__(self, outputs, *, fail_at: int | None = None):
        self.outputs = list(outputs)
        self.fail_at = fail_at
        self.calls = []
        self.model_name = "qwen3.5:35b-mlx"
        self.backend = "ollama-native"
        self.base_url = "http://localhost:11434/v1"
        self.context_length = 40960
        self.keep_alive = -1
        self.thinking = False
        self.structured_output = True
        self.corrective_retry = True
        self.timeout = 1800

    def complete_json(self, **kwargs):
        call_index = len(self.calls)
        self.calls.append(kwargs)
        if self.fail_at is not None and call_index == self.fail_at:
            raise RuntimeError("simulated interruption")
        data = self.outputs.pop(0)
        return SimpleNamespace(
            data=data,
            backend=self.backend,
            validation_mode="direct",
            metrics={
                "prompt_tokens": 10,
                "output_tokens": 5,
                "prompt_tokens_per_second": 20.0,
                "output_tokens_per_second": 10.0,
                "done_reason": "stop",
            },
        )


class DiscoverCharacterRosterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app_dir = self.root / "app"
        self.app_dir.mkdir()
        self.source_path = self.root / "book.txt"
        self.state_path = self.root / "character_roster_state.json"
        self.draft_path = self.root / "character_roster.draft.json"
        self.approved_path = self.root / "character_roster.json"
        self.config_path = self.app_dir / "config.json"
        self.config_path.write_text(
            json.dumps(
                {
                    "roster": {
                        "passage_size": 100,
                        "passage_overlap": 10,
                        "temperature": 0.2,
                        "max_tokens": 1024,
                        "seed": 42,
                    }
                }
            ),
            encoding="utf-8",
        )
        self.protected = [
            self.root / "annotated_script.json",
            self.root / "annotated_script.meta.json",
            self.root / "generation_state.json",
            self.root / "chunks.json",
            self.root / "voice_config.json",
        ]
        for index, path in enumerate(self.protected):
            path.write_text(
                json.dumps({"protected": index}),
                encoding="utf-8",
            )

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def discovery_output(passage: dict, name: str) -> dict:
        token = name
        start = passage["text"].index(token)
        return {
            "entities": [
                {
                    "identity_seed": (
                        f"passage-{passage['index']}:"
                        f"{start}:{name.casefold()}"
                    ),
                    "canonical_name": name.upper(),
                    "display_name": name,
                    "entity_kind": "character",
                    "speaking_status": "speaker",
                    "titles": [],
                    "aliases": [],
                    "nicknames": [],
                    "pronouns": [],
                    "species": [],
                    "relationships": [],
                    "voice_clues": [],
                    "sample_lines": [],
                    "confidence": 0.9,
                    "resolution_status": "resolved",
                    "unresolved_questions": [],
                    "evidence": [
                        {
                            "quote": token,
                            "start_char": start,
                            "end_char": start + len(token),
                            "category": category,
                            "confidence": 1.0,
                            "basis": "explicit",
                        }
                        for category in ("name", "speaking")
                    ],
                }
            ],
            "warnings": [],
        }

    @staticmethod
    def reconciliation_output(observation_ids: list[str]) -> dict:
        return {
            "entries": [
                {
                    "identity_seed": "alice",
                    "canonical_name": "ALICE",
                    "display_name": "Alice",
                    "entity_kind": "character",
                    "speaking_status": "speaker",
                    "observation_ids": observation_ids,
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

    def test_complete_run_writes_only_draft_and_clears_state(self) -> None:
        text = "Alice spoke clearly. " * 4
        self.source_path.write_text(text, encoding="utf-8")
        passage = build_discovery_passages(
            text,
            passage_size=100,
            overlap=10,
        )[0]
        discovery = self.discovery_output(passage, "Alice")
        # The reconciliation observation ID is deterministic but easiest to
        # obtain by running discovery first, so use a runtime that derives it.
        runtime = FakeRuntime([discovery])

        original_complete = runtime.complete_json

        def complete_json(**kwargs):
            if kwargs["contract"] == "roster_reconciliation":
                state = load_roster_discovery_state(self.state_path)
                assert state is not None
                observation_ids = [
                    item["observation_id"]
                    for record in state["completed_passages"]
                    for item in record["observations"]
                ]
                return SimpleNamespace(
                    data=self.reconciliation_output(observation_ids),
                    backend=runtime.backend,
                    validation_mode="direct",
                    metrics={
                        "prompt_tokens": 10,
                        "output_tokens": 5,
                        "prompt_tokens_per_second": 20.0,
                        "output_tokens_per_second": 10.0,
                        "done_reason": "stop",
                    },
                )
            return original_complete(**kwargs)

        runtime.complete_json = complete_json
        before = {path.name: self.digest(path) for path in self.protected}
        draft = run_roster_discovery(
            self.source_path,
            config_path=self.config_path,
            state_path=self.state_path,
            draft_path=self.draft_path,
            approved_path=self.approved_path,
            runtime_client=runtime,
            generated_at_utc="2026-07-16T20:00:00Z",
        )
        after = {path.name: self.digest(path) for path in self.protected}

        self.assertEqual(before, after)
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(len(draft["entries"]), 1)
        self.assertTrue(self.draft_path.exists())
        self.assertFalse(self.state_path.exists())
        self.assertEqual(
            [call["contract"] for call in runtime.calls],
            ["roster_discovery"],
        )

    def test_interrupted_run_resumes_without_repeating_passages(self) -> None:
        text = (
            ("Alice spoke. " * 12)
            + "\n\n"
            + ("Alice answered. " * 12)
        )
        self.source_path.write_text(text, encoding="utf-8")
        passages = build_discovery_passages(
            text,
            passage_size=100,
            overlap=10,
        )
        outputs = [
            self.discovery_output(passage, "Alice")
            for passage in passages
        ]
        first_runtime = FakeRuntime(outputs, fail_at=1)

        with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
            run_roster_discovery(
                self.source_path,
                config_path=self.config_path,
                state_path=self.state_path,
                draft_path=self.draft_path,
                approved_path=self.approved_path,
                runtime_client=first_runtime,
            )

        state = load_roster_discovery_state(self.state_path)
        assert state is not None
        self.assertEqual(len(state["completed_passages"]), 1)
        self.assertFalse(self.draft_path.exists())

        remaining_outputs = outputs[1:]
        second_runtime = FakeRuntime(remaining_outputs)
        original_complete = second_runtime.complete_json

        def complete_json(**kwargs):
            if kwargs["contract"] == "roster_reconciliation":
                current = load_roster_discovery_state(self.state_path)
                assert current is not None
                ids = [
                    item["observation_id"]
                    for record in current["completed_passages"]
                    for item in record["observations"]
                ]
                return SimpleNamespace(
                    data=self.reconciliation_output(ids),
                    backend=second_runtime.backend,
                    validation_mode="direct",
                    metrics={
                        "prompt_tokens": 10,
                        "output_tokens": 5,
                        "prompt_tokens_per_second": 20.0,
                        "output_tokens_per_second": 10.0,
                        "done_reason": "stop",
                    },
                )
            return original_complete(**kwargs)

        second_runtime.complete_json = complete_json
        run_roster_discovery(
            self.source_path,
            config_path=self.config_path,
            state_path=self.state_path,
            draft_path=self.draft_path,
            approved_path=self.approved_path,
            runtime_client=second_runtime,
            generated_at_utc="2026-07-16T20:00:00Z",
        )
        discovery_calls = [
            call
            for call in second_runtime.calls
            if call["contract"] == "roster_discovery"
        ]
        self.assertEqual(len(discovery_calls), len(passages) - 1)
        self.assertFalse(self.state_path.exists())
        self.assertTrue(self.draft_path.exists())

    def test_direct_invalid_passage_overrides_are_rejected(self) -> None:
        self.source_path.write_text("Alice spoke.", encoding="utf-8")
        runtime = FakeRuntime([])
        with self.assertRaisesRegex(ValueError, "at least 100"):
            run_roster_discovery(
                self.source_path,
                config_path=self.config_path,
                state_path=self.state_path,
                draft_path=self.draft_path,
                approved_path=self.approved_path,
                runtime_client=runtime,
                passage_size_override=50,
            )
        self.assertEqual(runtime.calls, [])

    def test_existing_draft_requires_explicit_replacement(self) -> None:
        self.source_path.write_text("Alice spoke.", encoding="utf-8")
        self.draft_path.write_text("{}", encoding="utf-8")
        runtime = FakeRuntime([])
        with self.assertRaisesRegex(RuntimeError, "replacement intent"):
            run_roster_discovery(
                self.source_path,
                config_path=self.config_path,
                state_path=self.state_path,
                draft_path=self.draft_path,
                approved_path=self.approved_path,
                runtime_client=runtime,
            )
        self.assertEqual(runtime.calls, [])

    def test_approved_roster_is_never_overwritten(self) -> None:
        self.source_path.write_text("Alice spoke.", encoding="utf-8")
        self.approved_path.write_text("{}", encoding="utf-8")
        runtime = FakeRuntime([])
        with self.assertRaisesRegex(RuntimeError, "approved"):
            run_roster_discovery(
                self.source_path,
                config_path=self.config_path,
                state_path=self.state_path,
                draft_path=self.draft_path,
                approved_path=self.approved_path,
                runtime_client=runtime,
                replace_draft=True,
            )
        self.assertEqual(runtime.calls, [])


if __name__ == "__main__":
    unittest.main()
