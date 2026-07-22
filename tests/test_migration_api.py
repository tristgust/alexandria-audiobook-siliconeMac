from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from generation_state import atomic_json_write
from migration_api import (
    MigrationApiError,
    apply_migration_payload,
    get_migration_history_payload,
    get_migration_operation_payload,
    get_migration_status_payload,
    rollback_migration_payload,
)


class MigrationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config_path = self.root / "app" / "config.json"
        self.config_path.parent.mkdir(parents=True)
        atomic_json_write(
            {
                "llm": {
                    "model_name": "qwen3.5:35b-mlx",
                    "custom": "preserve",
                },
                "tts": {"mode": "local"},
                "unknown_root": {"keep": True},
            },
            self.config_path,
        )
        atomic_json_write(
            [
                {
                    "speaker": "NARRATOR",
                    "text": "The room was quiet.",
                    "instruct": "Neutral narration.",
                }
            ],
            self.root / "annotated_script.json",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def status(self):
        return get_migration_status_payload(
            root_dir=self.root,
            config_path=self.config_path,
        )

    def test_status_is_file_pure_and_reports_additive_action(self) -> None:
        before = {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }
        status = self.status()
        after = {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)
        self.assertTrue(status["migration_required"])
        self.assertFalse(status["migration_blocked"])
        self.assertIsNone(status["last_migration"])
        self.assertEqual(
            status["actions"][0]["action"],
            "add_empty_llm_profiles",
        )
        self.assertFalse(status["text_rewrite_planned"])
        self.assertFalse(status["automatic_artifact_deletion_planned"])

    def test_history_payload_is_empty_and_file_pure_before_migration(self) -> None:
        before = {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }
        history = get_migration_history_payload(root_dir=self.root)
        after = {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(history["operations"], [])
        self.assertEqual(history["invalid_records"], [])
        self.assertTrue(history["history_fingerprint"])
        self.assertEqual(before, after)

    def test_history_payload_summarizes_apply_and_rollback(self) -> None:
        status = self.status()
        applied = apply_migration_payload(
            root_dir=self.root,
            config_path=self.config_path,
            expected_plan_fingerprint=status["plan_fingerprint"],
            confirm=True,
            at_utc="2026-07-17T03:00:00Z",
        )
        operation_id = applied["operation"]["operation_id"]
        rollback_migration_payload(
            root_dir=self.root,
            config_path=self.config_path,
            operation_id=operation_id,
            at_utc="2026-07-17T03:10:00Z",
        )
        history = get_migration_history_payload(root_dir=self.root)
        self.assertEqual(
            [item["operation"] for item in history["operations"]],
            ["rollback", "migration"],
        )
        migration = history["operations"][1]
        self.assertEqual(migration["state"], "rolled_back")
        self.assertFalse(migration["rollback_available"])
        self.assertNotIn("files", migration)

    def test_apply_returns_operation_and_current_status(self) -> None:
        status = self.status()
        result = apply_migration_payload(
            root_dir=self.root,
            config_path=self.config_path,
            expected_plan_fingerprint=status["plan_fingerprint"],
            confirm=True,
            at_utc="2026-07-17T03:00:00Z",
        )
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["llm"]["profiles"], {})
        self.assertEqual(saved["llm"]["custom"], "preserve")
        self.assertEqual(saved["unknown_root"], {"keep": True})
        self.assertEqual(result["status"]["actions"], [])
        self.assertFalse(result["operation"]["text_rewritten"])
        self.assertFalse(result["operation"]["artifacts_deleted"])

    def test_stale_and_unconfirmed_apply_errors_are_machine_readable(self) -> None:
        status = self.status()
        with self.assertRaises(MigrationApiError) as stale:
            apply_migration_payload(
                root_dir=self.root,
                config_path=self.config_path,
                expected_plan_fingerprint="stale",
                confirm=True,
            )
        self.assertEqual(stale.exception.status_code, 409)
        self.assertEqual(stale.exception.code, "stale_migration_plan")

        with self.assertRaises(MigrationApiError) as unconfirmed:
            apply_migration_payload(
                root_dir=self.root,
                config_path=self.config_path,
                expected_plan_fingerprint=status["plan_fingerprint"],
                confirm=False,
            )
        self.assertEqual(unconfirmed.exception.status_code, 422)
        self.assertEqual(unconfirmed.exception.code, "migration_rejected")

    def test_blocked_plan_is_visible_and_apply_is_rejected(self) -> None:
        self.config_path.write_text("[]", encoding="utf-8")
        status = self.status()
        self.assertTrue(status["migration_blocked"])
        self.assertTrue(status["blockers"])
        with self.assertRaises(MigrationApiError) as rejected:
            apply_migration_payload(
                root_dir=self.root,
                config_path=self.config_path,
                expected_plan_fingerprint=status["plan_fingerprint"],
                confirm=True,
            )
        self.assertEqual(rejected.exception.status_code, 422)

    def test_operation_read_and_missing_error(self) -> None:
        status = self.status()
        result = apply_migration_payload(
            root_dir=self.root,
            config_path=self.config_path,
            expected_plan_fingerprint=status["plan_fingerprint"],
            confirm=True,
        )
        operation_id = result["operation"]["operation_id"]
        loaded = get_migration_operation_payload(
            root_dir=self.root,
            operation_id=operation_id,
        )
        self.assertEqual(loaded, result["operation"])
        with self.assertRaises(MigrationApiError) as missing:
            get_migration_operation_payload(
                root_dir=self.root,
                operation_id="migration_000000000000000000000000",
            )
        self.assertEqual(missing.exception.status_code, 404)
        self.assertEqual(
            missing.exception.code,
            "migration_operation_not_found",
        )

    def test_rollback_restores_exact_config_and_refreshes_status(self) -> None:
        before = self.config_path.read_bytes()
        status = self.status()
        result = apply_migration_payload(
            root_dir=self.root,
            config_path=self.config_path,
            expected_plan_fingerprint=status["plan_fingerprint"],
            confirm=True,
            at_utc="2026-07-17T03:00:00Z",
        )
        rolled_back = rollback_migration_payload(
            root_dir=self.root,
            config_path=self.config_path,
            operation_id=result["operation"]["operation_id"],
            at_utc="2026-07-17T03:05:00Z",
        )
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertEqual(
            rolled_back["rollback"]["operation"],
            "rollback",
        )
        self.assertTrue(rolled_back["status"]["migration_required"])
        self.assertIsNone(rolled_back["status"]["last_migration"])

    def test_rollback_conflict_is_machine_readable(self) -> None:
        status = self.status()
        result = apply_migration_payload(
            root_dir=self.root,
            config_path=self.config_path,
            expected_plan_fingerprint=status["plan_fingerprint"],
            confirm=True,
        )
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        saved["llm"]["timeout"] = 999
        atomic_json_write(saved, self.config_path)
        with self.assertRaises(MigrationApiError) as conflict:
            rollback_migration_payload(
                root_dir=self.root,
                config_path=self.config_path,
                operation_id=result["operation"]["operation_id"],
            )
        self.assertEqual(conflict.exception.status_code, 409)
        self.assertEqual(
            conflict.exception.code,
            "migration_rollback_conflict",
        )

    def test_corrupt_migration_state_is_explicit(self) -> None:
        (self.root / "migration_state.json").write_text(
            "not json",
            encoding="utf-8",
        )
        with self.assertRaises(MigrationApiError) as unreadable:
            self.status()
        self.assertEqual(unreadable.exception.status_code, 409)
        self.assertEqual(
            unreadable.exception.code,
            "migration_state_unreadable",
        )


if __name__ == "__main__":
    unittest.main()
