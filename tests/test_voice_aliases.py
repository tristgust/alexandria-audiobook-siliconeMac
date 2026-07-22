from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from project import ProjectManager
from voice_aliases import (
    VoiceAliasError,
    merge_voice_config_updates,
    resolve_voice_alias,
    validate_voice_aliases,
)


class VoiceAliasContractTests(unittest.TestCase):
    def test_resolves_multi_hop_alias_to_independent_voice(self) -> None:
        config = {
            "THE DOCTOR": {
                "type": "clone",
                "ref_audio": "clone_voices/doctor.wav",
            },
            "DOCTOR": {"alias_of": "THE DOCTOR"},
            "SEVENTH DOCTOR": {"alias_of": "DOCTOR"},
        }

        resolution = resolve_voice_alias("SEVENTH DOCTOR", config)

        self.assertEqual(
            resolution.chain,
            ("SEVENTH DOCTOR", "DOCTOR", "THE DOCTOR"),
        )
        self.assertEqual(resolution.resolved_target, "THE DOCTOR")
        self.assertEqual(resolution.resolved_type, "clone")
        self.assertEqual(resolution.resolved_source, "doctor.wav")

    def test_rejects_missing_target(self) -> None:
        with self.assertRaises(VoiceAliasError) as raised:
            resolve_voice_alias(
                "DOCTOR",
                {"DOCTOR": {"alias_of": "THE DOCTOR"}},
            )

        self.assertEqual(raised.exception.code, "alias_target_missing")
        self.assertEqual(raised.exception.target, "THE DOCTOR")

    def test_rejects_self_alias(self) -> None:
        with self.assertRaises(VoiceAliasError) as raised:
            validate_voice_aliases(
                {"DOCTOR": {"alias_of": "DOCTOR"}},
            )

        self.assertEqual(raised.exception.code, "alias_self_reference")
        self.assertEqual(raised.exception.chain, ("DOCTOR", "DOCTOR"))

    def test_rejects_cycle_with_complete_chain(self) -> None:
        with self.assertRaises(VoiceAliasError) as raised:
            validate_voice_aliases(
                {
                    "DOCTOR": {"alias_of": "SEVENTH DOCTOR"},
                    "SEVENTH DOCTOR": {"alias_of": "DOCTOR"},
                }
            )

        self.assertEqual(raised.exception.code, "alias_cycle")
        self.assertEqual(
            raised.exception.chain,
            ("DOCTOR", "SEVENTH DOCTOR", "DOCTOR"),
        )

    def test_alias_update_preserves_dormant_and_unknown_fields(self) -> None:
        current = {
            "THE DOCTOR": {"type": "custom", "voice": "Ryan"},
            "DOCTOR": {
                "type": "clone",
                "ref_audio": "clone_voices/dormant.wav",
                "ref_text": "Dormant transcript.",
                "unknown": {"keep": True},
            },
        }

        candidate, diagnostics = merge_voice_config_updates(
            current,
            {"DOCTOR": {"alias_of": "THE DOCTOR"}},
        )

        self.assertEqual(candidate["DOCTOR"]["type"], "clone")
        self.assertEqual(
            candidate["DOCTOR"]["ref_audio"],
            "clone_voices/dormant.wav",
        )
        self.assertEqual(candidate["DOCTOR"]["unknown"], {"keep": True})
        self.assertEqual(candidate["DOCTOR"]["alias_of"], "THE DOCTOR")
        self.assertEqual(
            diagnostics["DOCTOR"]["resolved_target"],
            "THE DOCTOR",
        )

    def test_clearing_alias_restores_dormant_configuration(self) -> None:
        current = {
            "THE DOCTOR": {"type": "custom", "voice": "Ryan"},
            "DOCTOR": {
                "type": "clone",
                "ref_audio": "clone_voices/dormant.wav",
                "ref_text": "Dormant transcript.",
                "alias_of": "THE DOCTOR",
            },
        }

        candidate, diagnostics = merge_voice_config_updates(
            current,
            {"DOCTOR": {"alias_of": None}},
        )

        self.assertNotIn("alias_of", candidate["DOCTOR"])
        self.assertEqual(candidate["DOCTOR"]["type"], "clone")
        self.assertEqual(
            candidate["DOCTOR"]["ref_audio"],
            "clone_voices/dormant.wav",
        )
        self.assertFalse(diagnostics["DOCTOR"]["is_alias"])
        self.assertEqual(diagnostics["DOCTOR"]["resolved_target"], "DOCTOR")

    def test_clearing_alias_removes_legacy_alias_field(self) -> None:
        current = {
            "THE DOCTOR": {"type": "custom", "voice": "Ryan"},
            "DOCTOR": {
                "type": "custom",
                "voice": "Aiden",
                "alias": "THE DOCTOR",
            },
        }

        candidate, _ = merge_voice_config_updates(
            current,
            {"DOCTOR": {"alias_of": ""}},
        )

        self.assertNotIn("alias", candidate["DOCTOR"])
        self.assertNotIn("alias_of", candidate["DOCTOR"])
        self.assertEqual(candidate["DOCTOR"]["voice"], "Aiden")

    def test_failed_update_does_not_mutate_input(self) -> None:
        current = {
            "DOCTOR": {"type": "custom", "voice": "Ryan"},
            "MASTER": {"type": "custom", "voice": "Aiden"},
        }
        before = copy.deepcopy(current)

        with self.assertRaises(VoiceAliasError):
            merge_voice_config_updates(
                current,
                {
                    "DOCTOR": {"alias_of": "MASTER"},
                    "MASTER": {"alias_of": "DOCTOR"},
                },
            )

        self.assertEqual(current, before)


class RecordingEngine:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate_voice(
        self,
        text: str,
        instruct: str,
        speaker: str,
        voice_config: dict,
        output_path: str,
    ) -> bool:
        self.calls.append(
            {
                "text": text,
                "instruct": instruct,
                "speaker": speaker,
                "voice_config": voice_config,
                "output_path": output_path,
            }
        )
        return False


class ProjectManagerAliasRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manager = ProjectManager(str(self.root))
        self.engine = RecordingEngine()
        self.manager.engine = self.engine
        (self.root / "chunks.json").write_text(
            json.dumps(
                [
                    {
                        "id": 0,
                        "speaker": "SEVENTH DOCTOR",
                        "text": "Hello.",
                        "instruct": "Quietly.",
                        "status": "pending",
                        "audio_path": None,
                    }
                ]
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_chunk_generation_routes_only_to_resolved_target(self) -> None:
        (self.root / "voice_config.json").write_text(
            json.dumps(
                {
                    "THE DOCTOR": {
                        "type": "custom",
                        "voice": "Ryan",
                    },
                    "DOCTOR": {
                        "type": "clone",
                        "ref_audio": "dormant.wav",
                        "alias_of": "THE DOCTOR",
                    },
                    "SEVENTH DOCTOR": {
                        "type": "design",
                        "description": "Dormant design.",
                        "alias_of": "DOCTOR",
                    },
                }
            ),
            encoding="utf-8",
        )

        success, message = self.manager.generate_chunk_audio(0)

        self.assertFalse(success)
        self.assertEqual(message, "Generation failed")
        self.assertEqual(len(self.engine.calls), 1)
        self.assertEqual(self.engine.calls[0]["speaker"], "THE DOCTOR")

    def test_invalid_alias_blocks_synthesis_before_engine_call(self) -> None:
        (self.root / "voice_config.json").write_text(
            json.dumps(
                {
                    "SEVENTH DOCTOR": {
                        "alias_of": "MISSING DOCTOR",
                    }
                }
            ),
            encoding="utf-8",
        )

        before = (self.root / "chunks.json").read_bytes()
        success, message = self.manager.generate_chunk_audio(0)

        self.assertFalse(success)
        self.assertIn("does not exist", message)
        self.assertEqual(self.engine.calls, [])
        self.assertEqual((self.root / "chunks.json").read_bytes(), before)

    def test_invalid_alias_blocks_batch_before_chunk_state_changes(self) -> None:
        (self.root / "voice_config.json").write_text(
            json.dumps(
                {
                    "SEVENTH DOCTOR": {
                        "alias_of": "MISSING DOCTOR",
                    }
                }
            ),
            encoding="utf-8",
        )
        before = (self.root / "chunks.json").read_bytes()

        result = self.manager.generate_chunks_batch([0])

        self.assertEqual(result["completed"], [])
        self.assertEqual(result["cancelled"], 0)
        self.assertEqual(len(result["failed"]), 1)
        self.assertIn("does not exist", result["failed"][0][1])
        self.assertEqual(self.engine.calls, [])
        self.assertEqual((self.root / "chunks.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
