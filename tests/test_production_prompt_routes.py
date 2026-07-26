from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from experimental_prompt_routing import (
    resolve_experimental_prompt_override,
    sha256_file,
)
from production_prompt_routes import (
    ProductionPromptRouteError,
    install_primary_responsive_voices,
)


def write_wav(path: Path, *, frames: int = 2400, value: bytes = b"\x00\x00") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(value * frames)


class ProductionPromptRouteInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "app").mkdir()
        (self.root / "app" / "config.json").write_text(
            json.dumps(
                {
                    "tts": {
                        "language": "English",
                        "deterministic_seed_enabled": True,
                    }
                }
            ),
            encoding="utf-8",
        )
        self.narrator = self.root / "clone_voices" / "narrator.wav"
        self.benny_base = self.root / "clone_voices" / "benny.wav"
        self.doctor_base = self.root / "clone_voices" / "doctor.wav"
        write_wav(self.narrator, value=b"\x01\x00")
        write_wav(self.benny_base, value=b"\x02\x00")
        write_wav(self.doctor_base, value=b"\x03\x00")
        self.benny_prompt = self.root / "sources" / "benny-fear.wav"
        self.doctor_prompt = self.root / "sources" / "doctor-playful.wav"
        write_wav(self.benny_prompt, value=b"\x04\x00")
        write_wav(self.doctor_prompt, value=b"\x05\x00")
        self.voice_config = {
            "NARRATOR": {
                "type": "clone",
                "clone_backend": "qwen3_base",
                "ref_audio": "clone_voices/narrator.wav",
                "ref_text": "Narrator reference transcript.",
                "seed": "-1",
            },
            "BERNICE": {
                "type": "clone",
                "clone_backend": "qwen3_base",
                "ref_audio": "clone_voices/benny.wav",
                "ref_text": "Benny reference transcript.",
                "seed": "-1",
            },
            "THE DOCTOR": {
                "type": "clone",
                "clone_backend": "voxcpm2_controlled",
                "ref_audio": "clone_voices/doctor.wav",
                "ref_text": "Doctor reference transcript.",
                "character_style": "Measured, dry, Scottish.",
                "seed": "-1",
            },
        }
        (self.root / "voice_config.json").write_text(
            json.dumps(self.voice_config),
            encoding="utf-8",
        )
        old_audio = self.root / "voicelines" / "doctor.wav"
        write_wav(old_audio, frames=4800)
        (self.root / "chunks.json").write_text(
            json.dumps(
                [
                    {
                        "id": 0,
                        "speaker": "THE DOCTOR",
                        "text": "Oh, wonderful.",
                        "instruct": "Dryly amused; conversational pace.",
                        "status": "done",
                        "audio_state": "current",
                        "audio_path": "voicelines/doctor.wav",
                        "audio_fingerprint": "a" * 64,
                        "audio_sha256": sha256_file(old_audio),
                    }
                ]
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def install(self):
        with (
            patch(
                "production_prompt_routes.BENNY_SOURCE_SHA256",
                sha256_file(self.benny_prompt),
            ),
            patch(
                "production_prompt_routes.DOCTOR_SOURCE_SHA256",
                sha256_file(self.doctor_prompt),
            ),
        ):
            return install_primary_responsive_voices(
                project_root=self.root,
                benny_prompt_source=self.benny_prompt,
                doctor_prompt_source=self.doctor_prompt,
                confirm_production_opt_in=True,
                approved_at_utc="2026-07-26T06:00:00Z",
            )

    def test_confirmation_is_required_before_mutation(self) -> None:
        before = (self.root / "voice_config.json").read_bytes()
        with self.assertRaisesRegex(
            ProductionPromptRouteError,
            "explicit confirmation",
        ):
            install_primary_responsive_voices(
                project_root=self.root,
                benny_prompt_source=self.benny_prompt,
                doctor_prompt_source=self.doctor_prompt,
                confirm_production_opt_in=False,
            )
        self.assertEqual((self.root / "voice_config.json").read_bytes(), before)
        self.assertFalse((self.root / "production_prompt_routes").exists())

    def test_installer_upgrades_all_primary_voices_and_invalidates_old_audio(self) -> None:
        result = self.install()
        self.assertTrue(result["final_export_eligible"])
        self.assertEqual(
            result["voices"],
            ["NARRATOR", "BERNICE", "THE DOCTOR"],
        )
        config = json.loads(
            (self.root / "voice_config.json").read_text(encoding="utf-8")
        )
        for name in ("NARRATOR", "BERNICE", "THE DOCTOR"):
            self.assertEqual(
                config[name]["clone_backend"],
                "qwen3_instruction_controlled",
            )
            self.assertEqual(config[name]["seed"], "130363")
            self.assertEqual(
                len(config[name]["controlled_clone_configuration_fingerprint"]),
                64,
            )
        self.assertNotIn("experimental_prompt_routing", config["NARRATOR"])
        self.assertEqual(
            config["BERNICE"]["experimental_prompt_routing"]["scope"],
            "production_opt_in",
        )
        self.assertEqual(
            config["THE DOCTOR"]["experimental_prompt_routing"]["general_routing"],
            "instruction_keywords",
        )
        chunks = json.loads(
            (self.root / "chunks.json").read_text(encoding="utf-8")
        )
        self.assertEqual(chunks[0]["status"], "pending")
        self.assertEqual(chunks[0]["audio_state"], "stale")
        self.assertIsNone(chunks[0]["audio_path"])
        self.assertTrue(
            (self.root / "production_prompt_routes" / "benny_credible_fear.wav").is_file()
        )
        self.assertTrue(
            (self.root / "production_prompt_routes" / "doctor_playful_identity.wav").is_file()
        )

    def test_installed_routes_match_real_delivery_directions_automatically(self) -> None:
        self.install()
        config = json.loads(
            (self.root / "voice_config.json").read_text(encoding="utf-8")
        )
        doctor = resolve_experimental_prompt_override(
            voice_data=config["THE DOCTOR"],
            instruction="Dryly amused; conversational pace, underplay the punch line.",
            project_root=self.root,
        )
        self.assertEqual(doctor["route_key"], "ordinary_identity")
        self.assertTrue(doctor["production_promotion_allowed"])
        benny = resolve_experimental_prompt_override(
            voice_data=config["BERNICE"],
            instruction="Fearful and tense; let the dread arrive slowly.",
            project_root=self.root,
        )
        self.assertEqual(benny["route_key"], "credible_fear")
        self.assertTrue(benny["production_promotion_allowed"])
        neutral = resolve_experimental_prompt_override(
            voice_data=config["BERNICE"],
            instruction="Dryly inquisitive; conversational pace.",
            project_root=self.root,
        )
        self.assertIsNone(neutral)


if __name__ == "__main__":
    unittest.main()
