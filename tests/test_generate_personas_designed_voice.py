from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from audio_invalidation import undo_project_audio_invalidation
from generate_personas import _commit_voice_config, _save_generated_preview


class _Engine:
    def __init__(self, source: Path, *, fail: bool = False):
        self.source = source
        self.fail = fail

    def generate_voice_design(self, *, description: str, sample_text: str):
        if self.fail:
            raise RuntimeError("fixture preview failure")
        self.source.write_bytes(b"RIFFfixture")
        return str(self.source), 24000


class GeneratedDesignedVoiceTests(unittest.TestCase):
    def test_generated_voice_commit_invalidates_and_undoes_existing_audio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            voice_path = root / "voice_config.json"
            before = {"DOCTOR": {"type": "custom", "voice": "Ryan"}}
            after = {
                "DOCTOR": {
                    "type": "clone",
                    "ref_audio": "designed_voices/doctor.wav",
                    "ref_text": "Tell me the truth.",
                    "clone_backend": "qwen3_base",
                }
            }
            voice_path.write_text(json.dumps(before), encoding="utf-8")
            audio = root / "voicelines" / "doctor.wav"
            audio.parent.mkdir(parents=True)
            audio.write_bytes(b"old-audio")
            chunks = [
                {
                    "id": 1,
                    "speaker": "DOCTOR",
                    "text": "Tell me the truth.",
                    "status": "done",
                    "audio_state": "current",
                    "audio_path": "voicelines/doctor.wav",
                }
            ]
            (root / "chunks.json").write_text(
                json.dumps(chunks),
                encoding="utf-8",
            )

            record = _commit_voice_config(
                root=root,
                voice_config_path=voice_path,
                before=before,
                after=after,
                operation="persona_voice_generation",
            )

            self.assertIsNotNone(record)
            self.assertEqual(record["affected_speakers"], ["DOCTOR"])
            self.assertFalse(audio.exists())
            self.assertEqual(
                json.loads(voice_path.read_text(encoding="utf-8")),
                after,
            )

            undo_project_audio_invalidation(
                project_root=root,
                operation_id=record["operation_id"],
                undone_at_utc="2026-08-01T02:00:00Z",
            )
            self.assertEqual(
                json.loads(voice_path.read_text(encoding="utf-8")),
                before,
            )
            self.assertEqual(
                json.loads((root / "chunks.json").read_text(encoding="utf-8")),
                chunks,
            )
            self.assertEqual(audio.read_bytes(), b"old-audio")

    def test_successful_preview_becomes_a_fish_backed_clone_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "preview.wav"
            config = {
                "DOCTOR": {
                    "type": "custom",
                    "voice": "Aiden",
                    "ref_audio": "old.wav",
                    "clone_backend": "qwen3_base",
                }
            }
            success = _save_generated_preview(
                str(root),
                _Engine(source),
                config,
                "DOCTOR",
                "A precise, wiry tenor.",
                "Tell me the truth.",
            )
            self.assertTrue(success)
            voice = config["DOCTOR"]
            self.assertEqual(voice["type"], "clone")
            self.assertIsNone(voice["voice"])
            self.assertEqual(voice["description"], "A precise, wiry tenor.")
            self.assertEqual(voice["designed_voice_state"], "identity_seed_ready")
            self.assertEqual(voice["preview_status"], "generated")
            self.assertTrue((root / voice["preview_audio"]).is_file())
            self.assertEqual(voice["ref_audio"], voice["preview_audio"])
            self.assertEqual(voice["ref_text"], "Tell me the truth.")
            self.assertEqual(voice["clone_backend"], "qwen3_base")
            self.assertTrue(voice["fish_hybrid_enabled"])
            self.assertEqual(
                voice["fish_hybrid_styles"],
                ["fear", "grief", "sarcasm", "expressive"],
            )

    def test_failed_preview_keeps_the_definition_without_fake_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {}
            success = _save_generated_preview(
                str(root),
                _Engine(root / "preview.wav", fail=True),
                config,
                "DOCTOR",
                "A precise, wiry tenor.",
                "Tell me the truth.",
            )
            self.assertFalse(success)
            self.assertEqual(config["DOCTOR"]["type"], "design")
            self.assertIsNone(config["DOCTOR"]["voice"])
            self.assertEqual(
                config["DOCTOR"]["designed_voice_state"],
                "definition_ready",
            )
            self.assertEqual(config["DOCTOR"]["preview_status"], "failed")


if __name__ == "__main__":
    unittest.main()
