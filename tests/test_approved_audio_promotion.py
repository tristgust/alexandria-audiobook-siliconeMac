from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pydub import AudioSegment

from approved_audio import (
    active_approved_audio_lock,
    approved_audio_binding_fingerprint,
    approved_audio_lock_fields,
)
from approved_audio_promotion import (
    ApprovedAudioPromotionError,
    promote_approved_adaptation_audio,
    rollback_approved_adaptation_audio,
)
from audio_artifacts import audio_binding_fingerprint, sha256_file
from audio_invalidation import apply_project_audio_invalidation
from produce_aggregate import (
    build_produce_aggregate,
    build_produce_generation_plan,
)
from project import ProjectManager


class ApprovedAudioPromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "app").mkdir()
        (self.root / "clone_voices").mkdir()
        self.source = self.root / "reviewed.wav"
        with self.source.open("wb") as handle:
            AudioSegment.silent(duration=1400).export(handle, format="wav")
        self.source_sha = sha256_file(self.source)
        self.base_reference = self.root / "clone_voices" / "base.wav"
        with self.base_reference.open("wb") as handle:
            AudioSegment.silent(duration=1800).export(handle, format="wav")
        self.chunks = [
            {
                "id": 7,
                "speaker": "BERNICE",
                "text": "What is it, Mardi Gras?",
                "instruct": "Dry and curious.",
                "status": "pending",
                "audio_path": None,
            },
            {
                "id": 8,
                "speaker": "BERNICE",
                "text": "A new line still needs generation.",
                "instruct": "Calm.",
                "status": "pending",
                "audio_path": None,
            },
        ]
        self.voice_config = {
            "BERNICE": {
                "type": "clone",
                "voice": "Ryan",
                "character_style": "Dry, curious archaeologist.",
                "default_style": "",
                "seed": "130363",
                "ref_audio": "clone_voices/base.wav",
                "ref_text": "This is the original identity line.",
                "clone_backend": "qwen3_instruction_controlled",
                "instruction_clone_temperature": 0.75,
                "instruction_clone_top_k": 50,
                "instruction_clone_top_p": 0.95,
                "instruction_clone_repetition_penalty": 1.5,
                "instruction_clone_max_tokens": 2000,
            }
        }
        (self.root / "chunks.json").write_text(
            json.dumps(self.chunks, indent=2),
            encoding="utf-8",
        )
        (self.root / "voice_config.json").write_text(
            json.dumps(self.voice_config, indent=2),
            encoding="utf-8",
        )
        (self.root / "app" / "config.json").write_text(
            json.dumps({"tts": {"mode": "local", "language": "English"}}),
            encoding="utf-8",
        )
        self.manifest_path = self.root / "complete-manifest.json"
        manifest = {
            "schema_version": 1,
            "promotion_id": "test_complete_overlap_v2",
            "strict_overlap_expansion_status": "completed_and_fully_dispositioned",
            "direct_substitutions": [
                {
                    "candidate_id": "candidate_direct_7",
                    "source_round_id": "test_round",
                    "character": "Bernice",
                    "book_speaker": "BERNICE",
                    "chunk_id": 7,
                    "transcript": "What is it, Mardi Gras?",
                    "audio_path": str(self.source),
                    "audio_sha256": self.source_sha,
                    "proxy_path": None,
                    "proxy_sha256": None,
                    "direct_placement_tier": "strict_clean",
                    "reference_bank_eligible": True,
                }
            ],
            "restricted_direct_substitutions": [],
            "identity_anchors": [
                {
                    "candidate_id": "candidate_identity_benny",
                    "source_round_id": "identity_round",
                    "character": "Bernice Summerfield",
                    "book_speaker": "BERNICE",
                    "chunk_id": None,
                    "transcript": "My name is Bernice. What's yours?",
                    "audio_path": str(self.source),
                    "audio_sha256": self.source_sha,
                }
            ],
            "adaptation_performance_references": [],
            "reference_bank_evidence": [
                {
                    "candidate_id": "candidate_reference_benny",
                    "source_round_id": "reference_round",
                    "character": "Bernice",
                    "book_speaker": "BERNICE",
                    "chunk_id": 1939,
                    "transcript": "You make it sound so easy,",
                    "audio_path": str(self.source),
                    "audio_sha256": self.source_sha,
                    "delivery_tags": ["bernice_general_expressive_delivery"],
                    "reference_bank_eligible": True,
                }
            ],
        }
        self.manifest_path.write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _cast() -> dict:
        return {
            "characters": [
                {
                    "character_id": "character_bernice000000000000",
                    "display_name": "Bernice",
                    "required_for_completion": True,
                    "script_connection": {
                        "resolved_script_voice_label": "BERNICE",
                    },
                    "voice": {
                        "configuration_key": "BERNICE",
                        "selected_production_method": "clone",
                        "valid": True,
                        "blockers": [],
                    },
                }
            ]
        }

    def test_lock_binding_ignores_voice_changes_but_not_authored_changes(self) -> None:
        chunk = dict(self.chunks[0])
        fields = approved_audio_lock_fields(
            chunk=chunk,
            promotion_id="promotion",
            candidate_id="candidate",
            source_round_id="round",
            direct_placement_tier="strict_clean",
            source_audio_path=str(self.source),
            source_audio_sha256=self.source_sha,
            manifest_path="history/manifest.json",
            installed_at_utc="2026-07-31T12:00:00Z",
            reference_bank_eligible=False,
        )
        chunk.update(fields)
        expected = approved_audio_binding_fingerprint(chunk)
        self.assertIsNotNone(active_approved_audio_lock(chunk))
        self.assertEqual(expected, approved_audio_binding_fingerprint(chunk))
        chunk["text"] = "Changed authored text."
        self.assertIsNone(active_approved_audio_lock(chunk))

    def test_promotion_locks_only_imported_chunk_and_rollback_restores_bytes(self) -> None:
        existing_audio = self.root / "voicelines" / "existing-bernice.wav"
        existing_audio.parent.mkdir()
        existing_audio.write_bytes(self.base_reference.read_bytes())
        existing_chunks = json.loads((self.root / "chunks.json").read_text())
        existing_chunks[1].update(
            {
                "status": "done",
                "audio_state": "current",
                "audio_path": existing_audio.relative_to(self.root).as_posix(),
                "audio_fingerprint": audio_binding_fingerprint(
                    chunk=existing_chunks[1],
                    resolved_speaker="BERNICE",
                    voice_config=self.voice_config,
                    synthesis_config={"mode": "local", "language": "English"},
                ),
                "audio_sha256": sha256_file(existing_audio),
                "audio_size_bytes": existing_audio.stat().st_size,
                "audio_duration_ms": 1800,
                "audio_format": "wav",
                "stale_audio_path": None,
            }
        )
        (self.root / "chunks.json").write_text(
            json.dumps(existing_chunks, indent=2),
            encoding="utf-8",
        )
        before_chunks = (self.root / "chunks.json").read_bytes()
        before_voice = (self.root / "voice_config.json").read_bytes()
        existing_audio_bytes = existing_audio.read_bytes()
        receipt = promote_approved_adaptation_audio(
            project_root=self.root,
            manifest_path=self.manifest_path,
            confirm_installation=True,
            include_restricted=True,
            promote_voice_evidence=True,
            installed_at_utc="2026-07-31T12:00:00Z",
        )
        self.assertEqual(receipt["installed_chunk_count"], 1)
        promoted = json.loads((self.root / "chunks.json").read_text())
        locked, ordinary = promoted
        self.assertEqual(locked["status"], "done")
        self.assertEqual(locked["audio_state"], "current")
        self.assertEqual(locked["audio_sha256"], self.source_sha)
        self.assertIsNotNone(active_approved_audio_lock(locked))
        self.assertEqual(
            locked["generation_provenance"]["source"],
            "approved_adaptation_import",
        )
        self.assertIsNone(active_approved_audio_lock(ordinary))
        installed = self.root / locked["audio_path"]
        self.assertEqual(installed.read_bytes(), self.source.read_bytes())

        promoted_voice = json.loads(
            (self.root / "voice_config.json").read_text()
        )["BERNICE"]
        self.assertIn("clone_voices/approved_adaptation", promoted_voice["ref_audio"])
        routes = promoted_voice["experimental_prompt_routing"]["routes"]
        self.assertIn(
            "approved_adaptation_candidate_reference_benny",
            routes,
        )
        profile = self.root / "production_prompt_routes" / "approved_adaptation" / "profile.json"
        self.assertTrue(profile.is_file())

        aggregate = build_produce_aggregate(
            root_dir=self.root,
            chunks=promoted,
            voice_config=json.loads((self.root / "voice_config.json").read_text()),
            config={"tts": {"mode": "local", "language": "English"}},
            cast=self._cast(),
        )
        by_id = {item["chunk_id"]: item for item in aggregate["chunks"]}
        self.assertEqual(by_id["chunk:7"]["state"], "current")
        self.assertIsNone(by_id["chunk:7"]["regenerate_action"])
        self.assertTrue(by_id["chunk:7"]["regeneration_lock"]["locked"])
        self.assertIsNotNone(by_id["chunk:8"]["regenerate_action"])
        plan = build_produce_generation_plan(
            aggregate,
            mode="regenerate_all",
        )
        self.assertEqual(plan["indices"], [1])
        self.assertEqual(plan["preserved_locked_count"], 1)
        self.assertFalse(existing_audio.exists())

        receipt_path = (
            self.root
            / "approved_audio_promotion_history"
            / receipt["operation_id"]
            / "receipt.json"
        )
        rollback_approved_adaptation_audio(
            project_root=self.root,
            receipt_path=receipt_path,
            confirm_rollback=True,
        )
        self.assertEqual((self.root / "chunks.json").read_bytes(), before_chunks)
        self.assertEqual((self.root / "voice_config.json").read_bytes(), before_voice)
        self.assertFalse(installed.exists())
        self.assertEqual(existing_audio.read_bytes(), existing_audio_bytes)

    def test_missing_voices_are_created_from_best_approved_evidence(self) -> None:
        training_root = self.root / "voice_training_projects"
        for character_id, canonical_name, description in (
            (
                "character_hater000000000000",
                "HATER OF HUMANS",
                "Dense alien resonance with ceremonial phrasing that tightens under threat.",
            ),
            (
                "character_karvellis00000000",
                "KARVELLIS",
                "Penetrating, amplified projection with clipped decisive commands.",
            ),
        ):
            directory = training_root / character_id
            directory.mkdir(parents=True)
            (directory / "project.json").write_text(
                json.dumps(
                    {
                        "character": {
                            "canonical_name": canonical_name,
                        },
                        "desired_base_persona": {
                            "description": description,
                            "approval_status": "draft",
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        chunks = json.loads((self.root / "chunks.json").read_text())
        chunks.extend(
            [
                {
                    "id": 9,
                    "speaker": "HATER OF HUMANS",
                    "text": "By whatever means necessary.",
                    "instruct": "Coldly.",
                    "status": "pending",
                    "audio_path": None,
                },
                {
                    "id": 10,
                    "speaker": "KARVELLIS",
                    "text": "Get up slowly, hands behind your head.",
                    "instruct": "Firm command.",
                    "status": "pending",
                    "audio_path": None,
                },
            ]
        )
        (self.root / "chunks.json").write_text(
            json.dumps(chunks, indent=2),
            encoding="utf-8",
        )
        manifest = json.loads(self.manifest_path.read_text())
        manifest["direct_substitutions"].extend(
            [
                {
                    "candidate_id": "candidate_hater_direct",
                    "source_round_id": "direct_round",
                    "character": "Hater of Humans",
                    "book_speaker": "HATER OF HUMANS",
                    "chunk_id": 9,
                    "transcript": "By whatever means necessary.",
                    "audio_path": str(self.source),
                    "audio_sha256": self.source_sha,
                    "proxy_path": None,
                    "proxy_sha256": None,
                    "direct_placement_tier": "strict_clean",
                    "reference_bank_eligible": False,
                },
                {
                    "candidate_id": "candidate_karvellis_direct",
                    "source_round_id": "direct_round",
                    "character": "Karvellis",
                    "book_speaker": "KARVELLIS",
                    "chunk_id": 10,
                    "transcript": "Get up slowly, hands behind your head.",
                    "audio_path": str(self.source),
                    "audio_sha256": self.source_sha,
                    "proxy_path": None,
                    "proxy_sha256": None,
                    "direct_placement_tier": "strict_clean",
                    "reference_bank_eligible": False,
                },
            ]
        )
        manifest["adaptation_performance_references"] = [
            {
                "candidate_id": "candidate_hater_identity",
                "source_round_id": "identity_round",
                "character": "Hater of Humans",
                "book_speaker": "HATER OF HUMANS",
                "chunk_id": None,
                "transcript": "By whatever means necessary.",
                "audio_path": str(self.source),
                "audio_sha256": self.source_sha,
            }
        ]
        self.manifest_path.write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )

        receipt = promote_approved_adaptation_audio(
            project_root=self.root,
            manifest_path=self.manifest_path,
            confirm_installation=True,
            installed_at_utc="2026-07-31T12:00:00Z",
        )
        config = json.loads((self.root / "voice_config.json").read_text())
        self.assertEqual(config["HATER OF HUMANS"]["type"], "clone")
        self.assertIn(
            "candidate_hater_identity",
            config["HATER OF HUMANS"]["ref_audio"],
        )
        self.assertIn(
            "ceremonial phrasing",
            config["HATER OF HUMANS"]["character_style"],
        )
        self.assertEqual(
            config["HATER OF HUMANS"][
                "approved_adaptation_style_approval_status"
            ],
            "draft",
        )
        self.assertEqual(config["KARVELLIS"]["type"], "clone")
        self.assertIn(
            "candidate_karvellis_direct",
            config["KARVELLIS"]["ref_audio"],
        )
        self.assertIn(
            "clipped decisive commands",
            config["KARVELLIS"]["character_style"],
        )
        profile = receipt["voice_evidence_profile"]
        bases = {
            item["voice_key"]: item["identity_basis"]
            for item in profile["identity_anchors"]
        }
        self.assertEqual(
            bases["HATER OF HUMANS"],
            "approved_adaptation_performance_reference",
        )
        self.assertEqual(
            bases["KARVELLIS"],
            "strict_clean_direct_performance_fallback",
        )
        guidance = {
            item["voice_key"]: item
            for item in profile["voice_style_guidance"]
        }
        self.assertEqual(guidance["HATER OF HUMANS"]["approval_status"], "draft")
        self.assertIn("KARVELLIS", guidance)

    def test_synthesis_edit_clears_lock(self) -> None:
        promote_approved_adaptation_audio(
            project_root=self.root,
            manifest_path=self.manifest_path,
            confirm_installation=True,
            installed_at_utc="2026-07-31T12:00:00Z",
        )
        manager = ProjectManager(
            str(self.root),
            config_path=str(self.root / "app" / "config.json"),
        )
        changed = manager.update_chunk(0, {"instruct": "Now whisper."})
        self.assertIsNone(active_approved_audio_lock(changed))
        self.assertEqual(changed["status"], "pending")
        self.assertEqual(changed["audio_state"], "stale")

    def test_rollback_refuses_newer_project_changes(self) -> None:
        receipt = promote_approved_adaptation_audio(
            project_root=self.root,
            manifest_path=self.manifest_path,
            confirm_installation=True,
            installed_at_utc="2026-07-31T12:00:00Z",
        )
        receipt_path = (
            self.root
            / "approved_audio_promotion_history"
            / receipt["operation_id"]
            / "receipt.json"
        )
        chunks = json.loads((self.root / "chunks.json").read_text())
        chunks[1]["text"] = "Newer authored work."
        (self.root / "chunks.json").write_text(
            json.dumps(chunks, indent=2),
            encoding="utf-8",
        )
        changed_bytes = (self.root / "chunks.json").read_bytes()

        with self.assertRaises(ApprovedAudioPromotionError) as raised:
            rollback_approved_adaptation_audio(
                project_root=self.root,
                receipt_path=receipt_path,
                confirm_rollback=True,
            )
        self.assertEqual(raised.exception.code, "approved_audio_rollback_conflict")
        self.assertEqual(
            (self.root / "chunks.json").read_bytes(),
            changed_bytes,
        )

    def test_voice_invalidation_preserves_locked_approved_audio(self) -> None:
        promote_approved_adaptation_audio(
            project_root=self.root,
            manifest_path=self.manifest_path,
            confirm_installation=True,
            promote_voice_evidence=False,
            installed_at_utc="2026-07-31T12:00:00Z",
        )
        chunks = json.loads((self.root / "chunks.json").read_text())
        locked_path = chunks[0]["audio_path"]
        locked_sha = chunks[0]["audio_sha256"]
        ordinary_audio = self.root / "voicelines" / "ordinary.wav"
        ordinary_audio.write_bytes(self.base_reference.read_bytes())
        chunks[1].update(
            {
                "status": "done",
                "audio_state": "current",
                "audio_path": ordinary_audio.relative_to(self.root).as_posix(),
                "audio_fingerprint": "f" * 64,
                "audio_sha256": sha256_file(ordinary_audio),
                "audio_size_bytes": ordinary_audio.stat().st_size,
                "audio_duration_ms": 1800,
                "audio_format": "wav",
                "stale_audio_path": None,
            }
        )
        (self.root / "chunks.json").write_text(
            json.dumps(chunks, indent=2),
            encoding="utf-8",
        )
        voice_path = self.root / "voice_config.json"
        record = apply_project_audio_invalidation(
            project_root=self.root,
            operation_id="voice_change_after_approved_import",
            operation="test_voice_change",
            at_utc="2026-07-31T12:10:00Z",
            speakers={"BERNICE"},
            reason="Voice changed.",
            dependency_before={voice_path: voice_path.read_bytes()},
        )
        after = json.loads((self.root / "chunks.json").read_text())
        self.assertEqual(record["affected_chunk_ids"], [8])
        self.assertEqual(after[0]["status"], "done")
        self.assertEqual(after[0]["audio_path"], locked_path)
        self.assertEqual(after[0]["audio_sha256"], locked_sha)
        self.assertEqual(after[1]["status"], "pending")
        self.assertEqual(after[1]["audio_state"], "stale")


if __name__ == "__main__":
    unittest.main()
