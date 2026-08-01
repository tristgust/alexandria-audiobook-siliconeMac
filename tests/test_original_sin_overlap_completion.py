from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

from approved_audio import active_approved_audio_lock, approved_audio_lock_fields
from original_sin_overlap_completion import (
    MODE_SPECS,
    SECURITYBOT_CHUNK_IDS,
    TOBIAS_ROBOT_CHUNK_IDS,
    OriginalSinOverlapCompletionError,
    _remap_bot_chunks,
)
from recurring_voice_routing import validate_recurring_voice_routing
from voice_effects import apply_voice_effect_chain


def write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\x01\x00" * 4800)


class OriginalSinOverlapCompletionTests(unittest.TestCase):
    def test_authoritative_mode_and_speaker_split_counts(self) -> None:
        self.assertEqual(len(MODE_SPECS), 20)
        self.assertEqual(len(SECURITYBOT_CHUNK_IDS), 9)
        self.assertEqual(len(TOBIAS_ROBOT_CHUNK_IDS), 7)
        self.assertFalse(set(SECURITYBOT_CHUNK_IDS) & set(TOBIAS_ROBOT_CHUNK_IDS))

    def test_bot_remap_preserves_approved_lock(self) -> None:
        chunks = [
            {
                "id": chunk_id,
                "speaker": "BOT",
                "text": f"line {chunk_id}",
                "instruct": "Even synthetic delivery.",
                "status": "pending",
                "audio_path": None,
            }
            for chunk_id in (*SECURITYBOT_CHUNK_IDS, *TOBIAS_ROBOT_CHUNK_IDS)
        ]
        locked = next(chunk for chunk in chunks if chunk["id"] == 618)
        locked.update(
            {
                "status": "done",
                "audio_path": "voicelines/securitybot.mp3",
                **approved_audio_lock_fields(
                    chunk=locked,
                    promotion_id="promotion",
                    candidate_id="candidate",
                    source_round_id="round",
                    direct_placement_tier="strict_clean",
                    source_audio_path="source.wav",
                    source_audio_sha256="a" * 64,
                    manifest_path="history/manifest.json",
                    installed_at_utc="2026-08-01T00:00:00Z",
                    reference_bank_eligible=False,
                ),
            }
        )
        records = _remap_bot_chunks(
            chunks,
            {"SECURITYBOT", "TOBIAS VAUGHN"},
        )
        self.assertEqual(len(records), 16)
        self.assertEqual(locked["speaker"], "SECURITYBOT")
        self.assertIsNotNone(active_approved_audio_lock(locked))

    def test_bot_remap_rejects_stale_chunk_set(self) -> None:
        chunks = [
            {
                "id": chunk_id,
                "speaker": "BOT",
                "text": "line",
                "instruct": "neutral",
                "status": "pending",
                "audio_path": None,
            }
            for chunk_id in SECURITYBOT_CHUNK_IDS
        ]
        with self.assertRaisesRegex(
            OriginalSinOverlapCompletionError,
            "BOT speaker split is stale",
        ):
            _remap_bot_chunks(chunks, {"SECURITYBOT"})

    def test_operator_approved_tier_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = root / "clone_voices" / "identity.wav"
            write_wav(identity)
            from experimental_prompt_routing import sha256_file

            fingerprint = sha256_file(identity)
            policy = validate_recurring_voice_routing(
                {
                    "schema_version": 1,
                    "enabled": True,
                    "default_route": "neutral",
                    "fallback_backend": "qwen3_instruction_controlled",
                    "evidence_round_id": "round",
                    "production_promotion_allowed": True,
                    "routes": {
                        "neutral": {
                            "backend": "qwen3_instruction_controlled",
                            "instruction_keywords": [],
                            "identity_audio": "clone_voices/identity.wav",
                            "identity_audio_sha256": fingerprint,
                            "identity_text": "Identity line.",
                            "performance_audio": None,
                            "performance_audio_sha256": None,
                            "performance_text": None,
                            "control": {},
                            "effect_chain": None,
                            "approval_tier": "operator_approved_scores_incomplete",
                            "production_promotion_allowed": True,
                        }
                    },
                },
                project_root=root,
                verify_audio=True,
            )
            self.assertEqual(
                policy["routes"]["neutral"]["approval_tier"],
                "operator_approved_scores_incomplete",
            )

    def test_reviewed_effect_chains_execute(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for chain in ("securitybot_synthetic_v2", "computer_terminal_v3"):
                audio = root / f"{chain}.wav"
                write_wav(audio)
                receipt = apply_voice_effect_chain(audio, chain)
                self.assertEqual(receipt["chain"], chain)
                self.assertTrue(receipt["output_sha256"])


if __name__ == "__main__":
    unittest.main()
