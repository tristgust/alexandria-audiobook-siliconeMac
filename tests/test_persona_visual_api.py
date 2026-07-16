from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException

import app as app_module
from character_roster import (
    build_draft_roster,
    build_source_snapshot,
    save_character_roster,
    stable_entry_id,
)
from character_roster_actions import build_approved_roster
from character_visuals import (
    PROFILE_BUCKETS,
    build_visual_dossier,
    persona_reference_targets,
    write_visual_dossier,
)
from roster_discovery import build_discovery_passages
from visual_discovery import (
    build_visual_identity,
    prepare_visual_discovery_state,
)
from tests.visual_discovery_support import DynamicVisualRuntime


class PersonaVisualAPITests(unittest.TestCase):
    SOURCE_TEXT = (
        "The Khepri had four translucent wings. "
        "Roz watched the alien carefully."
    )

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.refs_dir = self.root / "persona_refs"
        self.refs_dir.mkdir()
        self.source_path = self.root / "book.txt"
        self.source_path.write_text(
            self.SOURCE_TEXT,
            encoding="utf-8",
        )
        self.source, _ = build_source_snapshot(self.source_path)
        self.entry = self.make_entry(
            name="THE KHEPRI",
            display="The Khepri",
            quote="The Khepri",
        )
        self.other_entry = self.make_entry(
            name="ROZ",
            display="Roz",
            quote="Roz",
        )
        draft = build_draft_roster(
            source=self.source,
            discovery={
                "created_at_utc": "2026-07-16T23:00:00Z",
                "model_name": "qwen3.5:35b-mlx",
                "backend": "ollama-native",
                "generation_fingerprint": "visual-api-fixture",
                "batch_count": 1,
                "completed_batches": 1,
            },
            entries=[self.entry, self.other_entry],
            source_text=self.SOURCE_TEXT,
        )
        self.approved = build_approved_roster(
            draft,
            expected_fingerprint=draft["draft_fingerprint"],
            source_fingerprint=self.source["fingerprint"],
            source_text=self.SOURCE_TEXT,
            acknowledged_unresolved=False,
            approved_at_utc="2026-07-16T23:05:00Z",
        )
        self.approved_path = self.root / "character_roster.json"
        save_character_roster(
            self.approved,
            self.approved_path,
            source_text=self.SOURCE_TEXT,
            expected_status="approved",
        )
        (self.root / "state.json").write_text(
            json.dumps({"input_file_path": str(self.source_path)}),
            encoding="utf-8",
        )
        self.visual_state_path = self.root / "persona_visual_state.json"
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
        self.patchers = [
            patch.object(app_module, "ROOT_DIR", str(self.root)),
            patch.object(
                app_module,
                "PERSONA_REFS_DIR",
                str(self.refs_dir),
            ),
            patch.object(
                app_module,
                "CHARACTER_ROSTER_PATH",
                str(self.approved_path),
            ),
            patch.object(
                app_module,
                "PERSONA_VISUAL_STATE_PATH",
                str(self.visual_state_path),
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
        app_module.process_state["roster"] = {
            "running": False,
            "logs": [],
            "cancel": False,
            "process": None,
        }
        app_module.process_state["visual"] = {
            "running": False,
            "logs": [],
            "cancel": False,
            "process": None,
        }

    def tearDown(self) -> None:
        for key in ("roster", "visual"):
            app_module.process_state[key] = {
                "running": False,
                "logs": [],
                "cancel": False,
                "process": None,
            }
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    @staticmethod
    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def make_entry(
        self,
        *,
        name: str,
        display: str,
        quote: str,
    ) -> dict:
        start = self.SOURCE_TEXT.index(quote)
        end = start + len(quote)
        return {
            "id": stable_entry_id(f"visual-api:{start}:{name}"),
            "canonical_name": name,
            "display_name": display,
            "entity_kind": "character",
            "speaking_status": "speaker",
            "titles": [],
            "aliases": [],
            "nicknames": [],
            "pronouns": [],
            "species": [],
            "relationships": [],
            "first_evidence_location": f"characters {start}-{end}",
            "additional_evidence_locations": [],
            "confidence": 0.95,
            "resolution_status": "resolved",
            "possible_duplicate_ids": [],
            "mistaken_merge_risk": False,
            "unresolved_questions": [],
            "evidence": [
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
            ],
            "voice_clues": [],
            "sample_lines": [],
        }

    def request(self, **kwargs):
        return app_module.CharacterVisualDiscoverRequest(
            enabled=kwargs.pop("enabled", True),
            entry_ids=kwargs.pop(
                "entry_ids",
                [self.entry["id"]],
            ),
            passage_size=kwargs.pop("passage_size", 120),
            overlap_chars=kwargs.pop("overlap_chars", 20),
            **kwargs,
        )

    def protected_hashes(self) -> dict[str, str]:
        return {
            path.name: self.digest(path)
            for path in self.protected
        }

    def visual_target(self, entry: dict) -> Path:
        ownership = [
            {
                "entry_id": item["id"],
                "character_name": (
                    item["canonical_name"]
                    or item["display_name"]
                ),
            }
            for item in self.approved["entries"]
        ]
        return persona_reference_targets(
            persona_refs_dir=self.refs_dir,
            selected_entries=ownership,
            all_entries=ownership,
        )[entry["id"]]

    def write_visual(self) -> Path:
        quote = "four translucent wings"
        start = self.SOURCE_TEXT.index(quote)
        observation_id = "visual_wings"
        profile = {
            bucket: []
            for bucket in PROFILE_BUCKETS
        }
        profile["nonhuman_anatomy"] = [
            {
                "detail": "four translucent wings",
                "certainty": 0.95,
                "observation_ids": [observation_id],
            }
        ]
        dossier = build_visual_dossier(
            observations=[
                {
                    "observation_id": observation_id,
                    "category": "nonhuman_anatomy",
                    "detail": "four translucent wings",
                    "scope": "stable",
                    "certainty": 0.95,
                    "basis": "explicit",
                    "source_location": (
                        f"characters {start}-{start + len(quote)}"
                    ),
                    "start_char": start,
                    "end_char": start + len(quote),
                    "passage_index": 1,
                    "quote": quote,
                }
            ],
            profile=profile,
            source_text=self.SOURCE_TEXT,
        )
        target = self.visual_target(self.entry)
        write_visual_dossier(
            persona_ref_path=target,
            visual=dossier,
            character_name=self.entry["canonical_name"],
            source_text=self.SOURCE_TEXT,
            entry_id=self.entry["id"],
            source_fingerprint=self.source["fingerprint"],
            roster_fingerprint=self.approved[
                "roster_fingerprint"
            ],
        )
        return target

    def prepare_progress(self, entry_ids: list[str]) -> None:
        passages = build_discovery_passages(
            self.SOURCE_TEXT,
            passage_size=120,
            overlap=20,
        )
        prepare_visual_discovery_state(
            path=self.visual_state_path,
            source=self.source,
            roster_fingerprint=self.approved[
                "roster_fingerprint"
            ],
            character_ids=entry_ids,
            generation_identity=build_visual_identity(
                DynamicVisualRuntime(),
                passage_size=120,
                overlap_chars=20,
                temperature=0.1,
                max_tokens=5000,
                seed=42,
            ),
            passages=passages,
        )

    def test_status_is_model_free_file_pure_and_lists_approved_entries(self) -> None:
        before = self.protected_hashes()
        status = asyncio.run(
            app_module.get_character_visual_status()
        )
        after = self.protected_hashes()
        self.assertEqual(before, after)
        self.assertFalse(status["enabled_by_default"])
        self.assertTrue(status["approved_roster_available"])
        self.assertEqual(status["absent_count"], 2)
        self.assertEqual(status["complete_count"], 0)
        self.assertTrue(
            all(
                item["status"] == "absent"
                for item in status["entries"]
            )
        )
        self.assertEqual(status["progress"]["status"], "none")

    def test_disabled_discovery_is_true_api_no_op(self) -> None:
        background = BackgroundTasks()
        before = self.protected_hashes()
        result = asyncio.run(
            app_module.discover_character_visuals(
                background,
                self.request(enabled=False),
            )
        )
        after = self.protected_hashes()
        self.assertEqual(before, after)
        self.assertEqual(
            result,
            {"status": "disabled", "started": False},
        )
        self.assertEqual(background.tasks, [])
        self.assertFalse(self.visual_state_path.exists())

    def test_discovery_schedules_canonical_runner(self) -> None:
        background = BackgroundTasks()
        result = asyncio.run(
            app_module.discover_character_visuals(
                background,
                self.request(),
            )
        )
        self.assertTrue(result["started"])
        self.assertEqual(result["mode"], "new")
        self.assertEqual(len(background.tasks), 1)
        task = background.tasks[0]
        self.assertIs(task.func, app_module.run_process)
        command = task.args[0]
        self.assertIn("discover_persona_visuals.py", command)
        self.assertIn("--enabled", command)
        self.assertIn(self.entry["id"], command)
        self.assertEqual(task.args[1], "visual")

    def test_invalid_or_duplicate_selection_is_blocked_before_start(self) -> None:
        for entry_ids in (
            [],
            [self.entry["id"], self.entry["id"]],
            ["character_ffffffffffffffffffff"],
        ):
            background = BackgroundTasks()
            with self.assertRaises(HTTPException) as error:
                asyncio.run(
                    app_module.discover_character_visuals(
                        background,
                        self.request(entry_ids=entry_ids),
                    )
                )
            self.assertEqual(error.exception.status_code, 400)
            self.assertEqual(background.tasks, [])

    def test_saved_progress_locks_character_selection(self) -> None:
        self.prepare_progress([self.entry["id"]])
        background = BackgroundTasks()
        with self.assertRaises(HTTPException) as error:
            asyncio.run(
                app_module.discover_character_visuals(
                    background,
                    self.request(
                        entry_ids=[self.other_entry["id"]]
                    ),
                )
            )
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(
            error.exception.detail["code"],
            "visual_selection_changed",
        )
        self.assertEqual(background.tasks, [])

    def test_running_guards_cancel_and_discard(self) -> None:
        self.prepare_progress([self.entry["id"]])
        app_module.process_state["visual"]["running"] = True
        background = BackgroundTasks()
        with self.assertRaises(HTTPException):
            asyncio.run(
                app_module.discover_character_visuals(
                    background,
                    self.request(),
                )
            )
        with self.assertRaises(HTTPException) as error:
            asyncio.run(
                app_module.discard_character_visual_progress()
            )
        self.assertEqual(error.exception.status_code, 409)
        app_module.process_state["visual"]["running"] = False
        self.assertEqual(
            asyncio.run(app_module.cancel_character_visuals()),
            {"status": "not_running"},
        )
        self.assertEqual(
            asyncio.run(
                app_module.discard_character_visual_progress()
            ),
            {"status": "discarded"},
        )
        self.assertFalse(self.visual_state_path.exists())
        self.assertEqual(
            asyncio.run(
                app_module.discard_character_visual_progress()
            ),
            {"status": "absent"},
        )

    def test_visual_get_route_returns_only_valid_dossier(self) -> None:
        self.write_visual()
        result = asyncio.run(
            app_module.get_character_visual(self.entry["id"])
        )
        self.assertEqual(result["entry_id"], self.entry["id"])
        facts = result["visual"]["profile"][
            "nonhuman_anatomy"
        ]
        self.assertEqual(facts[0]["detail"], "four translucent wings")
        self.assertEqual(
            result["visual"]["observations"][0][
                "observation_id"
            ],
            "visual_wings",
        )
        with self.assertRaises(HTTPException) as error:
            asyncio.run(
                app_module.get_character_visual(
                    self.other_entry["id"]
                )
            )
        self.assertEqual(error.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
