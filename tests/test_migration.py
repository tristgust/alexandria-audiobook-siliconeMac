from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from generation_state import atomic_json_write
from migration import (
    MigrationConflictError,
    MigrationValidationError,
    apply_migration_plan,
    build_migration_plan,
    list_migration_operations,
    load_migration_operation,
    rollback_migration,
)


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config_path = self.root / "app" / "config.json"
        self.config_path.parent.mkdir(parents=True)
        self.config = {
            "llm": {
                "model_name": "qwen3.5:35b-mlx",
                "base_url": "http://localhost:11434/v1",
                "custom_llm_key": {"preserve": True},
            },
            "tts": {
                "mode": "local",
                "custom_tts_key": 42,
            },
            "unknown_root": ["keep", "exactly"],
        }
        self.config_path.write_text(
            json.dumps(self.config, separators=(",", ":")),
            encoding="utf-8",
        )
        self.script = [
            {
                "speaker": "NARRATOR",
                "text": "The room was quiet.",
                "instruct": "Neutral narration.",
                "unknown_entry_key": "preserve",
            }
        ]
        atomic_json_write(self.script, self.root / "annotated_script.json")
        atomic_json_write(
            {
                "NARRATOR": {
                    "type": "design",
                    "description": "Neutral narrator.",
                    "unknown_voice_key": True,
                },
                "ANNOUNCER": {
                    "type": "clone",
                    "ref_audio": "clone_voices/dormant.wav",
                    "unknown_dormant_key": {"keep": True},
                    "alias_of": "NARRATOR",
                },
            },
            self.root / "voice_config.json",
        )
        persona_dir = self.root / "persona_refs"
        persona_dir.mkdir()
        atomic_json_write(
            {
                "name": "NARRATOR",
                "aliases": [],
                "features": [],
                "personality": [],
                "voice_clues": [],
                "relationships": [],
                "sample_lines": [],
                "observations": [],
                "legacy_unknown": {"keep": True},
            },
            persona_dir / "narrator.json",
        )
        (self.root / "scripts").mkdir()
        atomic_json_write(
            self.script,
            self.root / "scripts" / "legacy_saved.json",
        )
        for directory, filename, content in (
            ("lora_datasets/dataset_one", "metadata.jsonl", "{}\n"),
            ("lora_models/adapter_one", "adapter_config.json", "{}"),
            ("designed_voices", "voice.wav", b"RIFFfixture"),
            ("clone_voices", "reference.wav", b"RIFFfixture"),
            ("voicelines", "chunk_0.wav", b"RIFFfixture"),
        ):
            target = self.root / directory
            target.mkdir(parents=True, exist_ok=True)
            path = target / filename
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")
        atomic_json_write(
            {"accents": {"Scottish": "reference.wav"}},
            self.root / "designed_voices" / "accent_registry.json",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def plan(self):
        return build_migration_plan(
            root_dir=self.root,
            config_path=self.config_path,
        )

    def all_user_bytes(self) -> dict[str, bytes]:
        excluded = {"migration_state.json"}
        result = {}
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(self.root).as_posix()
            if relative in excluded or relative.startswith("migration_backups/"):
                continue
            result[relative] = path.read_bytes()
        return result

    def test_dry_run_is_file_pure_and_recognizes_legacy_states(self) -> None:
        before = self.all_user_bytes()
        plan = self.plan()
        after = self.all_user_bytes()
        self.assertEqual(before, after)
        self.assertEqual(plan["blockers"], [])
        self.assertEqual(len(plan["actions"]), 1)
        self.assertEqual(
            plan["actions"][0]["action"],
            "add_empty_llm_profiles",
        )
        self.assertFalse(plan["text_rewrite_planned"])
        self.assertFalse(plan["automatic_artifact_deletion_planned"])
        self.assertEqual(plan["destructive_action_count"], 0)
        self.assertTrue(
            plan["compatibility"]["script_metadata"][
                "legacy_without_metadata_supported"
            ]
        )
        self.assertEqual(
            plan["compatibility"]["persona_references"][
                "without_visual_count"
            ],
            1,
        )
        self.assertTrue(
            plan["compatibility"]["approved_roster"][
                "rosterless_installation_supported"
            ]
        )
        self.assertEqual(plan["inventory"]["saved_scripts"], 1)
        self.assertGreater(plan["inventory"]["lora_datasets"], 0)
        self.assertGreater(plan["inventory"]["lora_models"], 0)
        self.assertGreater(plan["inventory"]["generated_audio"], 0)

    def test_apply_requires_confirmation_and_current_plan(self) -> None:
        plan = self.plan()
        with self.assertRaisesRegex(
            MigrationValidationError,
            "explicit confirmation",
        ):
            apply_migration_plan(
                root_dir=self.root,
                config_path=self.config_path,
                expected_plan_fingerprint=plan["plan_fingerprint"],
                confirm=False,
            )
        with self.assertRaisesRegex(
            MigrationConflictError,
            "plan changed",
        ):
            apply_migration_plan(
                root_dir=self.root,
                config_path=self.config_path,
                expected_plan_fingerprint="stale",
                confirm=True,
            )

    def test_apply_adds_only_empty_profiles_and_preserves_user_artifacts(self) -> None:
        before = self.all_user_bytes()
        config_before = before.pop("app/config.json")
        plan = self.plan()
        result = apply_migration_plan(
            root_dir=self.root,
            config_path=self.config_path,
            expected_plan_fingerprint=plan["plan_fingerprint"],
            confirm=True,
            at_utc="2026-07-17T02:00:00Z",
        )
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["llm"]["profiles"], {})
        self.assertEqual(
            saved["llm"]["custom_llm_key"],
            {"preserve": True},
        )
        self.assertEqual(saved["tts"]["custom_tts_key"], 42)
        self.assertEqual(saved["unknown_root"], ["keep", "exactly"])
        after = self.all_user_bytes()
        after.pop("app/config.json")
        self.assertEqual(before, after)
        self.assertNotEqual(self.config_path.read_bytes(), config_before)
        operation = result["operation"]
        self.assertFalse(operation["text_rewritten"])
        self.assertFalse(operation["artifacts_deleted"])
        self.assertTrue((self.root / "migration_state.json").exists())
        loaded = load_migration_operation(
            root_dir=self.root,
            operation_id=operation["operation_id"],
        )
        self.assertEqual(loaded, operation)
        self.assertEqual(result["status"]["actions"], [])

    def test_rollback_restores_exact_config_bytes_and_prior_state(self) -> None:
        config_before = self.config_path.read_bytes()
        plan = self.plan()
        result = apply_migration_plan(
            root_dir=self.root,
            config_path=self.config_path,
            expected_plan_fingerprint=plan["plan_fingerprint"],
            confirm=True,
            at_utc="2026-07-17T02:00:00Z",
        )
        rollback = rollback_migration(
            root_dir=self.root,
            operation_id=result["operation"]["operation_id"],
            at_utc="2026-07-17T02:10:00Z",
        )
        self.assertEqual(rollback["operation"], "rollback")
        self.assertEqual(self.config_path.read_bytes(), config_before)
        self.assertFalse((self.root / "migration_state.json").exists())

    def test_rollback_blocks_when_config_changed_after_migration(self) -> None:
        plan = self.plan()
        result = apply_migration_plan(
            root_dir=self.root,
            config_path=self.config_path,
            expected_plan_fingerprint=plan["plan_fingerprint"],
            confirm=True,
        )
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        saved["llm"]["timeout"] = 999
        atomic_json_write(saved, self.config_path)
        with self.assertRaisesRegex(
            MigrationConflictError,
            "changed after migration",
        ):
            rollback_migration(
                root_dir=self.root,
                operation_id=result["operation"]["operation_id"],
            )

    def test_existing_profiles_make_migration_idempotent(self) -> None:
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        config["llm"]["profiles"] = {
            "script": {
                "enabled": False,
                "overrides": {},
                "evidence": None,
                "notes": [],
                "unknown_profile_key": "preserve",
            }
        }
        atomic_json_write(config, self.config_path)
        plan = self.plan()
        self.assertEqual(plan["actions"], [])
        result = apply_migration_plan(
            root_dir=self.root,
            config_path=self.config_path,
            expected_plan_fingerprint=plan["plan_fingerprint"],
            confirm=True,
        )
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(
            saved["llm"]["profiles"]["script"]["unknown_profile_key"],
            "preserve",
        )
        self.assertEqual(result["operation"]["actions"], [])

    def test_legacy_config_without_llm_section_is_preserved(self) -> None:
        legacy = {
            "model_name": "legacy-model",
            "base_url": "http://legacy",
            "unknown": {"keep": True},
        }
        self.config_path.write_text(
            json.dumps(legacy, separators=(",", ":")),
            encoding="utf-8",
        )
        before = self.config_path.read_bytes()
        plan = self.plan()
        self.assertEqual(plan["actions"], [])
        result = apply_migration_plan(
            root_dir=self.root,
            config_path=self.config_path,
            expected_plan_fingerprint=plan["plan_fingerprint"],
            confirm=True,
        )
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertEqual(result["operation"]["actions"], [])

    def test_invalid_config_script_and_persona_block_without_writes(self) -> None:
        before = self.all_user_bytes()
        self.config_path.write_text("[]", encoding="utf-8")
        (self.root / "annotated_script.json").write_text("{}", encoding="utf-8")
        (self.root / "persona_refs" / "broken.json").write_text(
            "not json",
            encoding="utf-8",
        )
        plan = self.plan()
        self.assertGreaterEqual(len(plan["blockers"]), 3)
        with self.assertRaisesRegex(
            MigrationValidationError,
            "Migration is blocked",
        ):
            apply_migration_plan(
                root_dir=self.root,
                config_path=self.config_path,
                expected_plan_fingerprint=plan["plan_fingerprint"],
                confirm=True,
            )
        self.assertFalse((self.root / "migration_state.json").exists())
        self.assertFalse((self.root / "migration_backups").exists())
        self.assertNotEqual(before, self.all_user_bytes())

    def test_invalid_profiles_type_blocks(self) -> None:
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        config["llm"]["profiles"] = []
        atomic_json_write(config, self.config_path)
        plan = self.plan()
        self.assertTrue(
            any("llm.profiles" in blocker for blocker in plan["blockers"])
        )

    def test_missing_config_is_supported_without_creation(self) -> None:
        self.config_path.unlink()
        plan = self.plan()
        self.assertEqual(plan["actions"], [])
        result = apply_migration_plan(
            root_dir=self.root,
            config_path=self.config_path,
            expected_plan_fingerprint=plan["plan_fingerprint"],
            confirm=True,
        )
        self.assertFalse(self.config_path.exists())
        self.assertEqual(result["operation"]["actions"], [])

    def test_transaction_failure_restores_exact_config_and_cleans_partial_backup(self) -> None:
        before = self.config_path.read_bytes()
        plan = self.plan()
        real_write = atomic_json_write
        calls = 0

        def fail_on_state(value, path):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("simulated migration failure")
            return real_write(value, path)

        with patch("migration.atomic_json_write", side_effect=fail_on_state):
            with self.assertRaisesRegex(OSError, "simulated"):
                apply_migration_plan(
                    root_dir=self.root,
                    config_path=self.config_path,
                    expected_plan_fingerprint=plan["plan_fingerprint"],
                    confirm=True,
                    at_utc="2026-07-17T02:00:00Z",
                )
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertFalse((self.root / "migration_state.json").exists())
        operation_files = list(
            (self.root / "migration_backups").glob("*/operation.json")
        ) if (self.root / "migration_backups").exists() else []
        self.assertEqual(operation_files, [])

    def test_config_path_must_remain_inside_project_root(self) -> None:
        outside = self.root.parent / "outside-config.json"
        with self.assertRaisesRegex(
            MigrationValidationError,
            "inside the project root",
        ):
            build_migration_plan(
                root_dir=self.root,
                config_path=outside,
            )

    def test_operation_id_rejects_path_traversal(self) -> None:
        with self.assertRaisesRegex(
            MigrationValidationError,
            "valid migration operation ID",
        ):
            load_migration_operation(
                root_dir=self.root,
                operation_id="../operation",
            )

    def test_empty_history_is_file_pure_and_fingerprinted(self) -> None:
        before = self.all_user_bytes()
        history = list_migration_operations(root_dir=self.root)
        self.assertEqual(history["operations"], [])
        self.assertEqual(history["invalid_records"], [])
        self.assertTrue(history["history_fingerprint"])
        self.assertEqual(self.all_user_bytes(), before)

    def test_history_summarizes_apply_and_rollback_without_snapshots(self) -> None:
        plan = self.plan()
        applied = apply_migration_plan(
            root_dir=self.root,
            config_path=self.config_path,
            expected_plan_fingerprint=plan["plan_fingerprint"],
            confirm=True,
            at_utc="2026-07-17T02:00:00Z",
        )
        migration_id = applied["operation"]["operation_id"]
        rollback = rollback_migration(
            root_dir=self.root,
            operation_id=migration_id,
            at_utc="2026-07-17T02:10:00Z",
        )
        history = list_migration_operations(root_dir=self.root)
        self.assertEqual(
            [item["operation"] for item in history["operations"]],
            ["rollback", "migration"],
        )
        migration = next(
            item
            for item in history["operations"]
            if item["operation_id"] == migration_id
        )
        self.assertEqual(migration["state"], "rolled_back")
        self.assertFalse(migration["rollback_available"])
        rolled_back = history["operations"][0]
        self.assertEqual(
            rolled_back["operation_id"],
            rollback["operation_id"],
        )
        self.assertEqual(
            rolled_back["rolls_back_operation_id"],
            migration_id,
        )
        for item in history["operations"]:
            self.assertNotIn("files", item)
            self.assertNotIn("previous_state", item)
            self.assertNotIn("content_base64", json.dumps(item))

    def test_history_reports_invalid_records_without_hiding_valid_operations(self) -> None:
        plan = self.plan()
        applied = apply_migration_plan(
            root_dir=self.root,
            config_path=self.config_path,
            expected_plan_fingerprint=plan["plan_fingerprint"],
            confirm=True,
            at_utc="2026-07-17T02:00:00Z",
        )
        invalid = self.root / "migration_backups" / "not-an-operation"
        invalid.mkdir(parents=True)
        (invalid / "operation.json").write_text("{}", encoding="utf-8")
        history = list_migration_operations(root_dir=self.root)
        self.assertEqual(len(history["operations"]), 1)
        self.assertEqual(
            history["operations"][0]["operation_id"],
            applied["operation"]["operation_id"],
        )
        self.assertEqual(
            history["invalid_records"][0]["operation_id"],
            "not-an-operation",
        )
        self.assertIn(
            "invalid operation ID",
            history["invalid_records"][0]["message"],
        )

    def test_tampered_operation_cannot_restore_absolute_path(self) -> None:
        plan = self.plan()
        result = apply_migration_plan(
            root_dir=self.root,
            config_path=self.config_path,
            expected_plan_fingerprint=plan["plan_fingerprint"],
            confirm=True,
            at_utc="2026-07-17T02:00:00Z",
        )
        operation_id = result["operation"]["operation_id"]
        operation_path = (
            self.root
            / "migration_backups"
            / operation_id
            / "operation.json"
        )
        record = json.loads(operation_path.read_text(encoding="utf-8"))
        file_state = next(iter(record["files"].values()))
        record["files"] = {"/tmp/escape.json": file_state}
        atomic_json_write(record, operation_path)
        with self.assertRaisesRegex(
            MigrationValidationError,
            "project-relative",
        ):
            load_migration_operation(
                root_dir=self.root,
                operation_id=operation_id,
            )


if __name__ == "__main__":
    unittest.main()
