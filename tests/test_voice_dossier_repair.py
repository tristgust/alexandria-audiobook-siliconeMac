from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from generation_state import atomic_json_write, fingerprint_value
from voice_dossier_repair import (
    VoiceDossierRepairError,
    apply_voice_dossier_repair,
    inspect_voice_dossier_repair,
    load_voice_dossier_repair_manifest,
    rollback_voice_dossier_repair,
    sha256_file,
)


def _description(label: str) -> str:
    return (
        f"{label} uses a deliberately specific medium register with forward resonance. "
        f"The timbre for {label} is dry, clear, and individually recognizable. "
        "Phrases follow source-defined pressure changes rather than a reusable generic cadence. "
        "Energy and emotional movement remain bounded to this exact dramatic function."
    )


def _spec(character_id: str, speaker: str) -> dict[str, object]:
    return {
        "character_id": character_id,
        "speaker": speaker,
        "persona_summary": f"{speaker} has a distinct source-defined dramatic function.",
        "designed_voice_description": _description(speaker),
        "vocal_age_impression": "Adult source-defined impression.",
        "pitch": f"Specific mid register selected for {speaker}.",
        "weight_and_resonance": f"Focused resonance unique to {speaker}.",
        "texture_and_timbre": f"Dry individual timbre for {speaker}.",
        "accent_and_language": "No unsupported regional accent is imposed.",
        "cadence_and_rhythm": f"Source-specific phrase timing for {speaker}.",
        "energy_range": f"Bounded dramatic energy for {speaker}.",
        "emotional_range": f"Source-supported emotional movement for {speaker}.",
        "casting_guidance": f"Keep {speaker} distinct from every adjacent role.",
        "uncertainties": [],
    }


class VoiceDossierRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "fixture-project"
        self.root.mkdir()
        self.source_fingerprint = "source-fingerprint"
        self.roster_fingerprint = "roster-fingerprint"
        self.dossier = {
            "schema_version": 1,
            "source_fingerprint": self.source_fingerprint,
            "roster_fingerprint": self.roster_fingerprint,
            "parent_candidate_id": "candidate",
            "voices": [
                {
                    "character_id": "character_alpha",
                    "speaker": "ALPHA",
                    "persona_summary": "Generic alpha.",
                    "designed_voice_description": "Generic alpha description.",
                    "ref_text": "Alpha line.",
                },
                {
                    "character_id": "character_beta",
                    "speaker": "BETA",
                    "persona_summary": "Generic beta.",
                    "designed_voice_description": "Generic beta description.",
                    "ref_text": "Beta line.",
                },
                {
                    "character_id": "character_saved",
                    "speaker": "SAVED",
                    "persona_summary": "Existing saved identity.",
                    "designed_voice_description": "Existing saved description.",
                    "ref_text": "Saved line.",
                },
            ],
            "warnings": [],
            "document_fingerprint": None,
        }
        self.dossier["document_fingerprint"] = fingerprint_value(
            {
                key: value
                for key, value in self.dossier.items()
                if key != "document_fingerprint"
            }
        )
        self.voice_config = {
            "SAVED": {
                "type": "design",
                "description": "Existing production Voice.",
            }
        }
        atomic_json_write(self.dossier, self.root / "cast_voice_dossiers.json")
        atomic_json_write(self.voice_config, self.root / "voice_config.json")
        self.before_bytes = (self.root / "cast_voice_dossiers.json").read_bytes()
        self.voice_config_sha = sha256_file(self.root / "voice_config.json")
        self.manifest_path = self.root / "manifest.json"
        self.manifest = {
            "schema_version": 1,
            "project": self.root.name,
            "expected_cast_voice_dossiers_sha256": sha256_file(
                self.root / "cast_voice_dossiers.json"
            ),
            "expected_voice_config_sha256": self.voice_config_sha,
            "expected_source_fingerprint": self.source_fingerprint,
            "expected_roster_fingerprint": self.roster_fingerprint,
            "generic_markers": ["retain natural flexibility"],
            "voices": [
                _spec("character_alpha", "ALPHA"),
                _spec("character_beta", "BETA"),
            ],
        }
        atomic_json_write(self.manifest, self.manifest_path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_apply_is_exact_idempotent_and_rollback_restores_bytes(self) -> None:
        saved_before = copy.deepcopy(self.dossier["voices"][2])
        receipt = apply_voice_dossier_repair(
            project_root=self.root,
            manifest_path=self.manifest_path,
            confirm_repair=True,
            applied_at_utc="2026-08-04T00:00:00Z",
        )
        self.assertEqual(receipt["status"], "installed")
        self.assertEqual(receipt["target_count"], 2)
        self.assertTrue(receipt["saved_voice_config_unchanged"])
        self.assertEqual(sha256_file(self.root / "voice_config.json"), self.voice_config_sha)
        updated = json.loads(
            (self.root / "cast_voice_dossiers.json").read_text(encoding="utf-8")
        )
        self.assertEqual(updated["voices"][2], saved_before)
        self.assertEqual(
            updated["voices"][0]["designed_voice_description"],
            self.manifest["voices"][0]["designed_voice_description"],
        )
        self.assertEqual(
            updated["voices"][0]["pitch"],
            {
                "value": self.manifest["voices"][0]["pitch"],
                "basis": "casting_recommendation",
                "evidence_quotes": [],
            },
        )
        self.assertEqual(
            updated["document_fingerprint"],
            fingerprint_value(
                {
                    key: value
                    for key, value in updated.items()
                    if key != "document_fingerprint"
                }
            ),
        )
        inspection = inspect_voice_dossier_repair(
            project_root=self.root,
            manifest_path=self.manifest_path,
        )
        self.assertTrue(inspection["ready"])
        repeated = apply_voice_dossier_repair(
            project_root=self.root,
            manifest_path=self.manifest_path,
            confirm_repair=True,
        )
        self.assertEqual(repeated["status"], "already_applied")
        rolled_back = rollback_voice_dossier_repair(
            project_root=self.root,
            operation_id=receipt["operation_id"],
            confirm_rollback=True,
            rolled_back_at_utc="2026-08-04T01:00:00Z",
        )
        self.assertEqual(rolled_back["status"], "rolled_back")
        self.assertEqual(
            (self.root / "cast_voice_dossiers.json").read_bytes(),
            self.before_bytes,
        )

    def test_requires_explicit_confirmation(self) -> None:
        with self.assertRaisesRegex(VoiceDossierRepairError, "explicit confirmation"):
            apply_voice_dossier_repair(
                project_root=self.root,
                manifest_path=self.manifest_path,
                confirm_repair=False,
            )

    def test_rejects_target_that_has_become_a_saved_production_voice(self) -> None:
        self.voice_config["ALPHA"] = {"type": "design", "description": "Saved alpha."}
        atomic_json_write(self.voice_config, self.root / "voice_config.json")
        self.manifest["expected_voice_config_sha256"] = sha256_file(
            self.root / "voice_config.json"
        )
        atomic_json_write(self.manifest, self.manifest_path)
        with self.assertRaisesRegex(VoiceDossierRepairError, "saved production Voice"):
            apply_voice_dossier_repair(
                project_root=self.root,
                manifest_path=self.manifest_path,
                confirm_repair=True,
            )

    def test_rejects_voice_config_drift(self) -> None:
        self.voice_config["SAVED"]["description"] = "Changed after manifest."
        atomic_json_write(self.voice_config, self.root / "voice_config.json")
        with self.assertRaisesRegex(VoiceDossierRepairError, "Voice configuration changed"):
            apply_voice_dossier_repair(
                project_root=self.root,
                manifest_path=self.manifest_path,
                confirm_repair=True,
            )

    def test_rejects_duplicate_or_generic_descriptions(self) -> None:
        invalid = copy.deepcopy(self.manifest)
        invalid["voices"][1]["designed_voice_description"] = invalid["voices"][0][
            "designed_voice_description"
        ]
        atomic_json_write(invalid, self.manifest_path)
        with self.assertRaisesRegex(VoiceDossierRepairError, "repeats a description"):
            load_voice_dossier_repair_manifest(self.manifest_path)

        invalid = copy.deepcopy(self.manifest)
        invalid["voices"][0]["casting_guidance"] = "Retain natural flexibility."
        atomic_json_write(invalid, self.manifest_path)
        with self.assertRaisesRegex(VoiceDossierRepairError, "generic template language"):
            load_voice_dossier_repair_manifest(self.manifest_path)


if __name__ == "__main__":
    unittest.main()
