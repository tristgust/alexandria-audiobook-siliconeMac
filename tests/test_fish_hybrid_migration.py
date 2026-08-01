from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from audio_artifacts import audio_binding_fingerprint
from fish_hybrid_migration import migrate_fish_hybrid_policy


class FishHybridMigrationTests(unittest.TestCase):
    @staticmethod
    def _write_config(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "NARRATOR": {
                        "type": "clone",
                        "clone_backend": "qwen3_instruction_controlled",
                        "ref_audio": "voice.wav",
                        "ref_text": "Exact reference words.",
                    },
                    "ALIAS": {"alias_of": "NARRATOR"},
                    "BUILTIN": {"type": "custom", "voice": "Ryan"},
                }
            ),
            encoding="utf-8",
        )

    def test_migration_updates_reusable_and_managed_configs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reusable = root / "reusable"
            projects = root / "Projects"
            active = projects / "book-a"
            other = projects / "book-b"
            self._write_config(reusable / "voice_config.json")
            self._write_config(active / "voice_config.json")
            self._write_config(other / "voice_config.json")

            result = migrate_fish_hybrid_policy(
                reusable_root=reusable,
                managed_projects_root=projects,
                active_project_root=active,
                enabled=True,
            )

            self.assertEqual(result["files_changed"], 3)
            self.assertEqual(result["changed_voice_count"], 3)
            for path in (
                reusable / "voice_config.json",
                active / "voice_config.json",
                other / "voice_config.json",
            ):
                config = json.loads(path.read_text(encoding="utf-8"))
                self.assertTrue(config["NARRATOR"]["fish_hybrid_enabled"])
                self.assertEqual(
                    config["NARRATOR"]["clone_backend"],
                    "qwen3_instruction_controlled",
                )
                self.assertNotIn("fish_hybrid_enabled", config["ALIAS"])

    def test_dry_run_reports_changes_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reusable = root / "reusable"
            projects = root / "Projects"
            active = projects / "book-a"
            target = reusable / "voice_config.json"
            self._write_config(target)
            before = target.read_bytes()

            result = migrate_fish_hybrid_policy(
                reusable_root=reusable,
                managed_projects_root=projects,
                active_project_root=active,
                enabled=True,
                dry_run=True,
            )

            self.assertTrue(result["dry_run"])
            self.assertEqual(result["eligible_voice_count"], 1)
            self.assertEqual(result["changed_voice_count"], 1)
            self.assertEqual(target.read_bytes(), before)

    def test_policy_fields_do_not_change_existing_local_audio_binding(self) -> None:
        chunk = {
            "id": 0,
            "speaker": "NARRATOR",
            "text": "A line.",
            "instruct": "Neutral delivery.",
        }
        base_voice = {
            "type": "clone",
            "clone_backend": "qwen3_instruction_controlled",
            "ref_audio": "voice.wav",
            "ref_text": "Exact reference words.",
        }
        hybrid_voice = {
            **base_voice,
            "fish_hybrid_enabled": True,
            "fish_hybrid_styles": ["fear", "grief", "sarcasm"],
            "fish_hybrid_use_approved_routes": True,
            "fish_hybrid_fallback_to_local": True,
        }
        baseline = audio_binding_fingerprint(
            chunk=chunk,
            resolved_speaker="NARRATOR",
            voice_config={"NARRATOR": base_voice},
            synthesis_config={"mode": "local"},
        )
        migrated = audio_binding_fingerprint(
            chunk=chunk,
            resolved_speaker="NARRATOR",
            voice_config={"NARRATOR": hybrid_voice},
            synthesis_config={"mode": "local"},
        )
        self.assertEqual(baseline, migrated)

    def test_installed_fish_metadata_changes_binding(self) -> None:
        voice = {
            "type": "clone",
            "clone_backend": "qwen3_instruction_controlled",
            "ref_audio": "voice.wav",
            "ref_text": "Exact reference words.",
            "fish_hybrid_enabled": True,
        }
        local_chunk = {
            "speaker": "NARRATOR",
            "text": "A line.",
            "instruct": "Grief.",
        }
        fish_chunk = {
            **local_chunk,
            "cloud_provider": "fish_s21_cloud",
            "cloud_model": "s2.1-pro-free",
            "cloud_style_route": "grief",
            "cloud_reference_fingerprint": "a" * 64,
        }
        local = audio_binding_fingerprint(
            chunk=local_chunk,
            resolved_speaker="NARRATOR",
            voice_config={"NARRATOR": voice},
        )
        fish = audio_binding_fingerprint(
            chunk=fish_chunk,
            resolved_speaker="NARRATOR",
            voice_config={"NARRATOR": voice},
        )
        self.assertNotEqual(local, fish)


if __name__ == "__main__":
    unittest.main()
