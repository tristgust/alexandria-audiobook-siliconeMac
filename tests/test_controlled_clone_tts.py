from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

import app as app_module
from controlled_clone_approval import (
    clear_controlled_clone_approvals,
    confirm_controlled_clone_preview,
    register_controlled_clone_preview,
)
from controlled_clone_preview import (
    build_controlled_clone_configuration_fingerprint,
)
from recurring_voice_routing import (
    ROUTED_CLONE_BACKEND,
    routing_fingerprint,
    validate_recurring_voice_routing,
)
from tts import TTSEngine


class FakeMLXBackend:
    def __init__(self) -> None:
        self.qwen_calls: list[dict] = []
        self.controlled_calls: list[dict] = []
        self.batch_calls: list[dict] = []

    @staticmethod
    def _write_output(path: str, text: str) -> None:
        sample_rate = 24000
        duration = max(0.8, len(text) * 0.05)
        count = max(1, round(sample_rate * duration))
        timeline = np.arange(count, dtype=np.float32) / sample_rate
        audio = 0.1 * np.sin(2.0 * np.pi * 7.0 * timeline)
        sf.write(path, audio, sample_rate, subtype="FLOAT")

    def generate_clone(self, **kwargs):
        self.qwen_calls.append(dict(kwargs))
        self._write_output(kwargs["output_path"], kwargs["text"])
        return True

    def generate_instruction_controlled_clone(self, **kwargs):
        self.controlled_calls.append(dict(kwargs))
        self._write_output(kwargs["output_path"], kwargs["text"])
        return True

    def generate_clone_batch(self, chunks, voice_config, output_dir):
        self.batch_calls.append(
            {
                "chunks": chunks,
                "voice_config": voice_config,
                "output_dir": output_dir,
            }
        )
        for chunk in chunks:
            self._write_output(
                str(Path(output_dir) / f"temp_batch_{chunk['index']}.wav"),
                chunk["text"],
            )
        return {
            "completed": [chunk["index"] for chunk in chunks],
            "failed": [],
        }


class ControlledCloneTTSTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.backend = FakeMLXBackend()
        self.engine = TTSEngine({"tts": {"mode": "local"}})
        self.engine._use_mlx = True
        self.engine._mlx_backend = self.backend

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_supplied_clip_routes_to_controlled_clone_with_instruction(self) -> None:
        config = {
            "DOCTOR": {
                "type": "clone",
                "clone_backend": "qwen3_instruction_controlled",
                "ref_audio": "/tmp/doctor-reference.wav",
                "ref_text": "Exact reference transcript.",
                "character_style": "Keep the supplied Scottish identity.",
                "instruction_clone_temperature": 0.8,
                "instruction_clone_top_k": 42,
                "instruction_clone_top_p": 0.9,
                "instruction_clone_repetition_penalty": 1.6,
                "instruction_clone_max_tokens": 1500,
                "seed": "4242",
            }
        }
        result = self.engine.generate_voice(
            "Tell me the truth.",
            "Controlled anger with restrained intensity.",
            "DOCTOR",
            config,
            str(self.root / "out.wav"),
        )
        self.assertTrue(result)
        self.assertEqual(self.backend.qwen_calls, [])
        call = self.backend.controlled_calls[0]
        self.assertEqual(call["ref_audio"], "/tmp/doctor-reference.wav")
        self.assertEqual(call["ref_text"], "Exact reference transcript.")
        self.assertIn("Controlled anger", call["instruct"])
        self.assertIn("Scottish identity", call["instruct"])
        self.assertEqual(call["temperature"], 0.8)
        self.assertEqual(call["top_k"], 42)
        self.assertEqual(call["top_p"], 0.9)
        self.assertEqual(call["repetition_penalty"], 1.6)
        self.assertEqual(call["max_tokens"], 1500)
        self.assertEqual(call["seed"], 4242)
        self.assertEqual(call["request_label"], "DOCTOR")

    def test_ordinary_supplied_clip_remains_on_qwen_clone(self) -> None:
        config = {
            "DOCTOR": {
                "type": "clone",
                "ref_audio": "/tmp/doctor-reference.wav",
                "ref_text": "Exact reference transcript.",
            }
        }
        result = self.engine.generate_voice(
            "Tell me the truth.",
            "Controlled anger.",
            "DOCTOR",
            config,
            str(self.root / "out.wav"),
        )
        self.assertTrue(result)
        self.assertEqual(len(self.backend.qwen_calls), 1)
        self.assertEqual(self.backend.controlled_calls, [])

    def test_controlled_clone_batch_preserves_each_line_instruction(self) -> None:
        config = {
            "DOCTOR": {
                "type": "clone",
                "clone_backend": "qwen3_instruction_controlled",
                "ref_audio": "/tmp/doctor-reference.wav",
                "ref_text": "Exact reference transcript.",
            }
        }
        chunks = [
            {
                "index": 1,
                "speaker": "DOCTOR",
                "text": "Run.",
                "instruct": "Urgent warning.",
            },
            {
                "index": 2,
                "speaker": "DOCTOR",
                "text": "It is all right.",
                "instruct": "Soft reassurance.",
            },
        ]
        with patch.object(self.engine, "_clear_gpu_cache"):
            result = self.engine.generate_batch(chunks, config, str(self.root))
        self.assertEqual(result["completed"], [1, 2])
        self.assertEqual(result["failed"], [])
        self.assertEqual(
            [call["instruct"] for call in self.backend.controlled_calls],
            ["Urgent warning.", "Soft reassurance."],
        )
        self.assertEqual(self.backend.batch_calls, [])

    def test_unknown_clone_backend_fails_explicitly(self) -> None:
        config = {
            "DOCTOR": {
                "type": "clone",
                "clone_backend": "invented",
                "ref_audio": "/tmp/doctor-reference.wav",
                "ref_text": "Exact reference transcript.",
            }
        }
        with self.assertRaisesRegex(ValueError, "Unsupported clone backend"):
            self.engine.generate_voice(
                "Hello.",
                "Neutral.",
                "DOCTOR",
                config,
                str(self.root / "out.wav"),
            )


class ControlledCloneVoiceConfigRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_controlled_clone_approvals()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.voice_config = self.root / "voice_config.json"
        self.reference = self.root / "clone_voices" / "doctor.wav"
        self.reference.parent.mkdir(parents=True)
        sf.write(
            self.reference,
            np.zeros(24000, dtype=np.float32),
            24000,
        )
        self.patches = [
            patch.object(
                app_module,
                "VOICE_CONFIG_PATH",
                str(self.voice_config),
            ),
            patch.object(app_module, "ROOT_DIR", str(self.root)),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        clear_controlled_clone_approvals()
        self.temp.cleanup()

    def controlled_voice(self, **overrides) -> dict:
        voice = {
            "type": "clone",
            "ref_audio": "clone_voices/doctor.wav",
            "ref_text": "Exact supplied transcript.",
            "character_style": "Measured and dry.",
            "clone_backend": "qwen3_instruction_controlled",
            "instruction_clone_temperature": 0.75,
            "instruction_clone_top_k": 50,
            "instruction_clone_top_p": 0.95,
            "instruction_clone_repetition_penalty": 1.5,
            "instruction_clone_max_tokens": 1800,
            "reference_bank_path": (
                "voice_training_projects/character_aaaaaaaaaaaaaaaaaaaa/"
                "reference_bank.json"
            ),
            "reference_bank_character_id": (
                "character_aaaaaaaaaaaaaaaaaaaa"
            ),
            "reference_bank_fingerprint": "a" * 64,
        }
        voice.update(overrides)
        return voice

    def responsive_voice(self) -> dict:
        identity = self.root / "clone_voices" / "chris.wav"
        performance = self.root / "production_prompt_routes" / "chris-neutral.wav"
        identity.parent.mkdir(parents=True, exist_ok=True)
        performance.parent.mkdir(parents=True, exist_ok=True)
        sf.write(identity, np.ones(24000, dtype=np.float32) * 0.02, 24000)
        sf.write(performance, np.ones(24000, dtype=np.float32) * 0.03, 24000)
        import hashlib

        def digest(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        policy = {
            "schema_version": 1,
            "enabled": True,
            "default_route": "neutral",
            "fallback_backend": "qwen3_instruction_controlled",
            "evidence_round_id": "reviewed_recurring_voice_round",
            "production_promotion_allowed": True,
            "routes": {
                "neutral": {
                    "backend": "indextts2_matched_control",
                    "instruction_keywords": ["neutral", "analytical"],
                    "identity_audio": "clone_voices/chris.wav",
                    "identity_audio_sha256": digest(identity),
                    "identity_text": "Exact Chris identity transcript.",
                    "performance_audio": "production_prompt_routes/chris-neutral.wav",
                    "performance_audio_sha256": digest(performance),
                    "performance_text": "Exact Chris performance transcript.",
                    "control": {
                        "emotion_strength": 0.0,
                        "diffusion_steps": 8,
                        "num_beams": 1,
                        "greedy": True,
                        "max_mel_tokens": 600,
                    },
                    "production_promotion_allowed": True,
                }
            },
        }
        normalized = validate_recurring_voice_routing(
            policy,
            project_root=self.root,
            verify_audio=True,
        )
        return {
            "type": "clone",
            "voice": "Ryan",
            "ref_audio": "clone_voices/chris.wav",
            "ref_text": "Exact Chris identity transcript.",
            "clone_backend": ROUTED_CLONE_BACKEND,
            "seed": "130363",
            "responsive_backend_routing": normalized,
            "responsive_backend_configuration_fingerprint": routing_fingerprint(
                normalized
            ),
        }

    def approval_for(self, voice: dict, *, preview: str = "p" * 64) -> tuple[str, str]:
        configuration_fingerprint = (
            build_controlled_clone_configuration_fingerprint(
                root_dir=self.root,
                ref_audio=voice["ref_audio"],
                ref_text=voice["ref_text"],
                character_style=voice.get("character_style", ""),
                temperature=voice["instruction_clone_temperature"],
                top_k=voice["instruction_clone_top_k"],
                top_p=voice["instruction_clone_top_p"],
                repetition_penalty=voice[
                    "instruction_clone_repetition_penalty"
                ],
                max_tokens=voice["instruction_clone_max_tokens"],
                seed=voice.get("seed", -1),
            )
        )
        register_controlled_clone_preview(
            speaker="DOCTOR",
            preview_fingerprint=preview,
            configuration_fingerprint=configuration_fingerprint,
        )
        confirmation = confirm_controlled_clone_preview(
            speaker="DOCTOR",
            preview_fingerprint=preview,
            configuration_fingerprint=configuration_fingerprint,
        )
        return confirmation["approval_token"], configuration_fingerprint

    def save(self, voice: dict):
        return self.client.post(
            "/api/save_voice_config",
            json={"DOCTOR": voice},
        )

    def test_save_route_requires_matching_server_listen_receipt(self) -> None:
        response = self.save(self.controlled_voice())
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "controlled_clone_approval_required",
        )
        self.assertFalse(self.voice_config.exists())

    def test_save_route_preserves_controlled_clone_and_bank_fields(self) -> None:
        voice = self.controlled_voice()
        token, configuration_fingerprint = self.approval_for(voice)
        response = self.save(
            {
                **voice,
                "controlled_clone_approval_token": token,
                "controlled_clone_configuration_fingerprint": "forged",
            }
        )
        self.assertEqual(response.status_code, 200, response.text)
        saved = json.loads(self.voice_config.read_text(encoding="utf-8"))
        doctor = saved["DOCTOR"]
        self.assertEqual(
            doctor["clone_backend"],
            "qwen3_instruction_controlled",
        )
        self.assertEqual(doctor["instruction_clone_temperature"], 0.75)
        self.assertEqual(doctor["instruction_clone_top_k"], 50)
        self.assertEqual(doctor["instruction_clone_top_p"], 0.95)
        self.assertEqual(
            doctor["instruction_clone_repetition_penalty"],
            1.5,
        )
        self.assertEqual(doctor["instruction_clone_max_tokens"], 1800)
        self.assertEqual(
            doctor["reference_bank_character_id"],
            "character_aaaaaaaaaaaaaaaaaaaa",
        )
        self.assertEqual(doctor["reference_bank_fingerprint"], "a" * 64)
        self.assertEqual(
            doctor["controlled_clone_configuration_fingerprint"],
            configuration_fingerprint,
        )
        self.assertNotIn("controlled_clone_approval_token", doctor)

    def test_receipt_is_bound_to_settings_and_consumed_once(self) -> None:
        voice = self.controlled_voice()
        token, _ = self.approval_for(voice)
        changed = {
            **voice,
            "instruction_clone_temperature": 0.9,
            "controlled_clone_approval_token": token,
        }
        response = self.save(changed)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "controlled_clone_approval_mismatch",
        )
        self.assertFalse(self.voice_config.exists())

        response = self.save(
            {
                **voice,
                "controlled_clone_approval_token": token,
            }
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.voice_config.unlink()
        response = self.save(
            {
                **voice,
                "controlled_clone_approval_token": token,
            }
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "controlled_clone_approval_required",
        )

    def test_changing_seed_requires_a_new_preview_receipt(self) -> None:
        voice = self.controlled_voice(seed="101")
        token, _ = self.approval_for(voice)
        response = self.save(
            {
                **voice,
                "seed": "202",
                "controlled_clone_approval_token": token,
            }
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "controlled_clone_approval_mismatch",
        )
        self.assertFalse(self.voice_config.exists())

    def test_unchanged_saved_controlled_clone_needs_no_new_receipt(self) -> None:
        voice = self.controlled_voice()
        token, _ = self.approval_for(voice)
        first = self.save(
            {
                **voice,
                "controlled_clone_approval_token": token,
            }
        )
        self.assertEqual(first.status_code, 200, first.text)
        second = self.save(voice)
        self.assertEqual(second.status_code, 200, second.text)

    def test_reference_audio_content_change_requires_new_receipt(self) -> None:
        voice = self.controlled_voice()
        token, _ = self.approval_for(voice)
        first = self.save(
            {
                **voice,
                "controlled_clone_approval_token": token,
            }
        )
        self.assertEqual(first.status_code, 200, first.text)
        sf.write(
            self.reference,
            np.ones(24000, dtype=np.float32) * 0.05,
            24000,
        )
        second = self.save(voice)
        self.assertEqual(second.status_code, 409, second.text)
        self.assertEqual(
            second.json()["detail"]["code"],
            "controlled_clone_approval_required",
        )

    def test_legacy_voxcpm2_configuration_is_not_approved(self) -> None:
        legacy = self.controlled_voice(
            clone_backend="voxcpm2_controlled",
        )
        response = self.save(legacy)
        self.assertEqual(response.status_code, 200, response.text)
        saved = json.loads(self.voice_config.read_text(encoding="utf-8"))
        self.assertEqual(
            saved["DOCTOR"]["clone_backend"],
            "voxcpm2_controlled",
        )
        self.assertNotIn(
            "controlled_clone_configuration_fingerprint",
            saved["DOCTOR"],
        )

    def test_standard_clone_does_not_require_receipt(self) -> None:
        response = self.save(
            {
                "type": "clone",
                "ref_audio": "clone_voices/doctor.wav",
                "ref_text": "Exact supplied transcript.",
                "clone_backend": "qwen3_base",
            }
        )
        self.assertEqual(response.status_code, 200, response.text)
        saved = json.loads(self.voice_config.read_text(encoding="utf-8"))
        self.assertNotIn(
            "controlled_clone_configuration_fingerprint",
            saved["DOCTOR"],
        )

    def test_ordinary_save_cannot_create_responsive_routing(self) -> None:
        response = self.save(self.responsive_voice())
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "responsive_voice_review_required",
        )
        self.assertFalse(self.voice_config.exists())

    def test_unchanged_responsive_voice_can_be_saved_without_new_review(self) -> None:
        voice = self.responsive_voice()
        self.voice_config.write_text(json.dumps({"DOCTOR": voice}), encoding="utf-8")
        response = self.save(voice)
        self.assertEqual(response.status_code, 200, response.text)
        saved = json.loads(self.voice_config.read_text(encoding="utf-8"))["DOCTOR"]
        self.assertEqual(saved["clone_backend"], ROUTED_CLONE_BACKEND)
        self.assertEqual(
            saved["responsive_backend_configuration_fingerprint"],
            voice["responsive_backend_configuration_fingerprint"],
        )

    def test_responsive_voice_cannot_be_edited_in_place(self) -> None:
        voice = self.responsive_voice()
        self.voice_config.write_text(json.dumps({"DOCTOR": voice}), encoding="utf-8")
        response = self.save({**voice, "seed": "77"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "responsive_voice_review_required",
        )
        saved = json.loads(self.voice_config.read_text(encoding="utf-8"))["DOCTOR"]
        self.assertEqual(saved["seed"], "130363")

    def test_responsive_voice_can_be_replaced_through_normal_voice_choice(self) -> None:
        voice = self.responsive_voice()
        self.voice_config.write_text(json.dumps({"DOCTOR": voice}), encoding="utf-8")
        response = self.save(
            {
                "type": "clone",
                "ref_audio": "clone_voices/doctor.wav",
                "ref_text": "Exact supplied transcript.",
                "clone_backend": "qwen3_base",
            }
        )
        self.assertEqual(response.status_code, 200, response.text)
        saved = json.loads(self.voice_config.read_text(encoding="utf-8"))["DOCTOR"]
        self.assertEqual(saved["clone_backend"], "qwen3_base")
        self.assertNotIn("responsive_backend_routing", saved)
        self.assertNotIn("responsive_backend_configuration_fingerprint", saved)

    def test_save_route_rejects_unknown_clone_backend(self) -> None:
        response = self.client.post(
            "/api/save_voice_config",
            json={
                "DOCTOR": {
                    "type": "clone",
                    "clone_backend": "not-real",
                }
            },
        )
        self.assertEqual(response.status_code, 422, response.text)


if __name__ == "__main__":
    unittest.main()
