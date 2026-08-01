from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from audio_artifacts import AudioArtifactError
from experimental_prompt_routing import (
    ExperimentalPromptRoutingError,
    parse_prompt_route,
    resolve_experimental_prompt_override,
    sha256_file,
    strip_prompt_route_tag,
    validate_experimental_prompt_routing,
)
from project import ProjectManager
from tts import TTSEngine


def write_wav(path: Path, *, frames: int = 2400) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\x10\x00" * frames)


class ArtifactEngine:
    def generate_voice(self, text, instruct, speaker, voice_config, output_path):
        write_wav(Path(output_path), frames=24000)
        return True


class FakeMLXBackend:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate_instruction_controlled_clone(self, **kwargs):
        self.calls.append(dict(kwargs))
        write_wav(
            Path(kwargs["output_path"]),
            frames=max(24000, len(kwargs["text"]) * 1200),
        )
        return True


class ExperimentalPromptRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.prompt = self.root / "experimental_prompts" / "doctor-playful.wav"
        write_wav(self.prompt)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def policy(self, **overrides):
        value = {
            "schema_version": 1,
            "enabled": True,
            "scope": "research_only",
            "general_routing": "disabled",
            "production_promotion_allowed": False,
            "evidence_round_id": (
                "alexandria_three_voice_paired_seed_reliability_review_applied_v1"
            ),
            "routes": {
                "ordinary_identity": {
                    "status": "research_preferred",
                    "prompt_role": "validated_bank",
                    "reference_key": "doctor_acf_playful_introduction",
                    "validated_bank_clip_id": "doctor_acf_playful_introduction",
                    "ref_audio": "experimental_prompts/doctor-playful.wav",
                    "ref_audio_sha256": sha256_file(self.prompt),
                    "ref_text": "Hello, I'm the Doctor.",
                    "production_promotion_allowed": False,
                }
            },
        }
        value.update(overrides)
        return value

    def test_route_tag_parser_and_strip_are_explicit(self) -> None:
        instruction = "Playfully. [prompt-route: ordinary_identity] Keep moving."
        self.assertEqual(parse_prompt_route(instruction), "ordinary_identity")
        self.assertEqual(
            strip_prompt_route_tag(instruction),
            "Playfully. Keep moving.",
        )
        self.assertIsNone(parse_prompt_route("Playfully."))

    def test_untagged_line_never_uses_research_override(self) -> None:
        voice = {"experimental_prompt_routing": self.policy()}
        self.assertIsNone(
            resolve_experimental_prompt_override(
                voice_data=voice,
                instruction="Playfully.",
                project_root=self.root,
            )
        )

    def production_policy(self):
        return {
            "schema_version": 2,
            "enabled": True,
            "scope": "production_opt_in",
            "general_routing": "instruction_keywords",
            "production_promotion_allowed": True,
            "evidence_round_id": (
                "alexandria_three_voice_paired_seed_reliability_review_applied_v1"
            ),
            "routes": {
                "ordinary_identity": {
                    "status": "production_opt_in",
                    "prompt_role": "validated_bank",
                    "reference_key": "doctor_acf_playful_introduction",
                    "validated_bank_clip_id": "doctor_acf_playful_introduction",
                    "ref_audio": "experimental_prompts/doctor-playful.wav",
                    "ref_audio_sha256": sha256_file(self.prompt),
                    "ref_text": "Hello, I'm the Doctor.",
                    "production_promotion_allowed": True,
                    "instruction_keywords": [
                        "playful",
                        "playfully",
                        "dryly amused",
                    ],
                    "approval_basis": "operator_approved_after_listening",
                    "operator_approved_at_utc": "2026-07-26T05:00:00Z",
                }
            },
        }

    def test_tagged_line_resolves_hash_verified_project_audio(self) -> None:
        voice = {"experimental_prompt_routing": self.policy()}
        selected = resolve_experimental_prompt_override(
            voice_data=voice,
            instruction="[prompt-route: ordinary_identity] Playfully.",
            project_root=self.root,
        )
        self.assertEqual(selected["route_key"], "ordinary_identity")
        self.assertEqual(selected["prompt_role"], "validated_bank")
        self.assertEqual(Path(selected["ref_audio"]), self.prompt.resolve())
        self.assertFalse(selected["production_promotion_allowed"])

    def test_production_route_matches_existing_instruction_and_is_export_eligible(self) -> None:
        voice = {"experimental_prompt_routing": self.production_policy()}
        selected = resolve_experimental_prompt_override(
            voice_data=voice,
            instruction="Dryly amused; conversational pace, underplay the punch line.",
            project_root=self.root,
        )
        self.assertEqual(selected["route_key"], "ordinary_identity")
        self.assertEqual(selected["mapping_reason"], "instruction_keyword_match")
        self.assertTrue(selected["production_promotion_allowed"])

    def test_automatic_routing_uses_word_boundaries_and_phrase_specificity(self) -> None:
        # Given two production routes where a short token is contained inside an
        # unrelated word and a longer phrase overlaps a generic keyword.
        policy = self.production_policy()
        template = policy["routes"].pop("ordinary_identity")

        def route(key: str, keywords: list[str]) -> dict:
            value = dict(template)
            value["reference_key"] = key
            value["validated_bank_clip_id"] = key
            value["instruction_keywords"] = keywords
            return value

        policy["routes"] = {
            "anger": route("anger", ["anger"]),
            "affection": route("affection", ["affectionate"]),
            "generic_question": route("generic_question", ["inquisitive"]),
            "dry_question": route("dry_question", ["dryly inquisitive"]),
        }
        voice = {"experimental_prompt_routing": policy}

        # When instructions contain "danger" or both a phrase and its generic
        # suffix, then the semantically exact route wins deterministically.
        affectionate = resolve_experimental_prompt_override(
            voice_data=voice,
            instruction="Affectionate, with a brief glimpse of danger.",
            project_root=self.root,
        )
        dry = resolve_experimental_prompt_override(
            voice_data=voice,
            instruction="Dryly inquisitive; conversational pace.",
            project_root=self.root,
        )

        # Then "anger" does not match inside "danger", and the longest phrase
        # outranks its generic overlapping keyword.
        self.assertEqual(affectionate["route_key"], "affection")
        self.assertEqual(dry["route_key"], "dry_question")

    def test_unknown_explicit_route_fails_instead_of_falling_back(self) -> None:
        voice = {"experimental_prompt_routing": self.policy()}
        with self.assertRaisesRegex(
            ExperimentalPromptRoutingError,
            "No approved experimental prompt route",
        ):
            resolve_experimental_prompt_override(
                voice_data=voice,
                instruction="[prompt-route: urgency] Quickly.",
                project_root=self.root,
            )

    def test_hash_mismatch_and_unsafe_path_are_rejected(self) -> None:
        bad_hash = self.policy()
        bad_hash["routes"]["ordinary_identity"]["ref_audio_sha256"] = "0" * 64
        with self.assertRaisesRegex(ExperimentalPromptRoutingError, "changed"):
            validate_experimental_prompt_routing(
                bad_hash,
                project_root=self.root,
                verify_audio=True,
            )
        unsafe = self.policy()
        unsafe["routes"]["ordinary_identity"]["ref_audio"] = "../outside.wav"
        with self.assertRaisesRegex(ExperimentalPromptRoutingError, "safe project-relative"):
            validate_experimental_prompt_routing(
                unsafe,
                project_root=self.root,
            )

    def test_general_or_production_routing_can_never_be_enabled(self) -> None:
        general = self.policy(general_routing="enabled")
        with self.assertRaisesRegex(ExperimentalPromptRoutingError, "general prompt routing"):
            validate_experimental_prompt_routing(general)
        production = self.policy(production_promotion_allowed=True)
        with self.assertRaisesRegex(ExperimentalPromptRoutingError, "production promotion"):
            validate_experimental_prompt_routing(production)

    def test_research_override_audio_is_auditionable_but_export_blocked(self) -> None:
        (self.root / "app").mkdir(exist_ok=True)
        (self.root / "app" / "config.json").write_text(
            json.dumps({"tts": {"language": "English"}}),
            encoding="utf-8",
        )
        base = self.root / "clone_voices" / "doctor.wav"
        write_wav(base)
        voice_config = {
            "DOCTOR": {
                "type": "clone",
                "clone_backend": "qwen3_instruction_controlled",
                "ref_audio": "clone_voices/doctor.wav",
                "ref_text": "Base transcript.",
                "experimental_prompt_routing": self.policy(),
            }
        }
        (self.root / "voice_config.json").write_text(
            json.dumps(voice_config),
            encoding="utf-8",
        )
        (self.root / "chunks.json").write_text(
            json.dumps(
                [
                    {
                        "id": 0,
                        "speaker": "DOCTOR",
                        "text": "Oh, wonderful.",
                        "instruct": (
                            "[prompt-route: ordinary_identity] Playfully."
                        ),
                        "status": "pending",
                        "audio_path": None,
                    }
                ]
            ),
            encoding="utf-8",
        )
        manager = ProjectManager(str(self.root))
        manager.engine = ArtifactEngine()
        success, _ = manager.generate_chunk_audio(0)
        self.assertTrue(success)
        chunk = json.loads(
            (self.root / "chunks.json").read_text(encoding="utf-8")
        )[0]
        self.assertTrue(chunk["audio_research_only"])
        self.assertEqual(
            chunk["experimental_prompt_route"],
            "ordinary_identity",
        )
        with self.assertRaises(AudioArtifactError) as raised:
            manager._load_chunks_with_audio()
        self.assertEqual(raised.exception.code, "project_audio_not_ready")
        self.assertEqual(
            raised.exception.details[0]["reason"],
            "experimental_prompt_not_production_eligible",
        )

    def test_production_override_audio_reaches_final_export_contract(self) -> None:
        (self.root / "app").mkdir(exist_ok=True)
        (self.root / "app" / "config.json").write_text(
            json.dumps({"tts": {"language": "English"}}),
            encoding="utf-8",
        )
        base = self.root / "clone_voices" / "doctor.wav"
        write_wav(base)
        voice_config = {
            "DOCTOR": {
                "type": "clone",
                "clone_backend": "qwen3_instruction_controlled",
                "ref_audio": "clone_voices/doctor.wav",
                "ref_text": "Base transcript.",
                "experimental_prompt_routing": self.production_policy(),
            }
        }
        (self.root / "voice_config.json").write_text(
            json.dumps(voice_config),
            encoding="utf-8",
        )
        (self.root / "chunks.json").write_text(
            json.dumps(
                [
                    {
                        "id": 0,
                        "speaker": "DOCTOR",
                        "text": "Oh, wonderful.",
                        "instruct": "Dryly amused; conversational pace.",
                        "status": "pending",
                        "audio_path": None,
                    }
                ]
            ),
            encoding="utf-8",
        )
        manager = ProjectManager(str(self.root))
        manager.engine = ArtifactEngine()
        success, _ = manager.generate_chunk_audio(0)
        self.assertTrue(success)
        chunk = json.loads(
            (self.root / "chunks.json").read_text(encoding="utf-8")
        )[0]
        self.assertFalse(chunk["audio_research_only"])
        self.assertTrue(chunk["audio_production_prompt_approved"])
        self.assertTrue(chunk["production_promotion_allowed"])
        self.assertEqual(chunk["experimental_prompt_route"], "ordinary_identity")
        current = manager._load_chunks_with_audio()
        self.assertEqual(len(current), 1)

    def test_tts_override_precedes_base_reference_and_strips_internal_tag(self) -> None:
        backend = FakeMLXBackend()
        engine = TTSEngine({"tts": {"mode": "local"}})
        engine._use_mlx = True
        engine._mlx_backend = backend
        voice_config = {
            "DOCTOR": {
                "type": "clone",
                "clone_backend": "qwen3_instruction_controlled",
                "ref_audio": "clone_voices/base.wav",
                "ref_text": "Base transcript.",
                "character_style": "Measured and dry.",
                "seed": "42",
                "experimental_prompt_routing": self.policy(),
            }
        }
        result = engine.generate_voice(
            "Oh, wonderful.",
            "[prompt-route: ordinary_identity] Playful eccentricity.",
            "DOCTOR",
            voice_config,
            str(self.root / "out.wav"),
        )
        self.assertTrue(result)
        call = backend.calls[0]
        self.assertEqual(Path(call["ref_audio"]), self.prompt.resolve())
        self.assertEqual(call["ref_text"], "Hello, I'm the Doctor.")
        self.assertNotIn("prompt-route", call["instruct"])
        self.assertIn("Playful eccentricity", call["instruct"])
        self.assertEqual(call["seed"], 42)


class ExperimentalPromptVoiceConfigRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.voice_config = self.root / "voice_config.json"
        self.base = self.root / "clone_voices" / "doctor.wav"
        self.route_audio = self.root / "experimental_prompts" / "doctor-playful.wav"
        write_wav(self.base)
        write_wav(self.route_audio)
        self.patchers = [
            patch.object(app_module, "ROOT_DIR", str(self.root)),
            patch.object(app_module, "VOICE_CONFIG_PATH", str(self.voice_config)),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.client.close()
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def policy(self):
        return {
            "schema_version": 1,
            "enabled": True,
            "scope": "research_only",
            "general_routing": "disabled",
            "production_promotion_allowed": False,
            "evidence_round_id": "paired_seed_applied_v1",
            "routes": {
                "ordinary_identity": {
                    "status": "research_preferred",
                    "prompt_role": "validated_bank",
                    "reference_key": "doctor_acf_playful_introduction",
                    "validated_bank_clip_id": "doctor_acf_playful_introduction",
                    "ref_audio": "experimental_prompts/doctor-playful.wav",
                    "ref_audio_sha256": sha256_file(self.route_audio),
                    "ref_text": "Hello, I'm the Doctor.",
                    "production_promotion_allowed": False,
                }
            },
        }

    def test_standard_clone_policy_is_validated_and_saved_without_auto_use(self) -> None:
        response = self.client.post(
            "/api/save_voice_config",
            json={
                "DOCTOR": {
                    "type": "clone",
                    "clone_backend": "qwen3_base",
                    "ref_audio": "clone_voices/doctor.wav",
                    "ref_text": "Base transcript.",
                    "experimental_prompt_routing": self.policy(),
                }
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        saved = json.loads(self.voice_config.read_text(encoding="utf-8"))
        policy = saved["DOCTOR"]["experimental_prompt_routing"]
        self.assertEqual(policy["scope"], "research_only")
        self.assertEqual(policy["general_routing"], "disabled")

    def test_prompt_policy_changes_controlled_clone_approval_fingerprint(self) -> None:
        voice = {
            "type": "clone",
            "clone_backend": "qwen3_instruction_controlled",
            "ref_audio": "clone_voices/doctor.wav",
            "ref_text": "Base transcript.",
            "character_style": "Measured and dry.",
            "seed": "42",
            "experimental_prompt_routing": self.policy(),
        }
        first = app_module._controlled_clone_configuration_fingerprint(voice)
        changed = json.loads(json.dumps(voice))
        changed["experimental_prompt_routing"]["routes"]["ordinary_identity"][
            "ref_text"
        ] = "Hello. I am the Doctor."
        second = app_module._controlled_clone_configuration_fingerprint(changed)
        self.assertNotEqual(first, second)

    def test_save_rejects_tampered_route_audio(self) -> None:
        policy = self.policy()
        policy["routes"]["ordinary_identity"]["ref_audio_sha256"] = "0" * 64
        response = self.client.post(
            "/api/save_voice_config",
            json={
                "DOCTOR": {
                    "type": "clone",
                    "clone_backend": "qwen3_base",
                    "ref_audio": "clone_voices/doctor.wav",
                    "ref_text": "Base transcript.",
                    "experimental_prompt_routing": policy,
                }
            },
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "experimental_prompt_routing_invalid",
        )
        self.assertFalse(self.voice_config.exists())


if __name__ == "__main__":
    unittest.main()
